"""TikTok shop-authorization orchestration (Lane E — the "onboarding"
half of the OAuth lifecycle).

Owns the pieces that :mod:`tts_erp_v2.proxy.token_service` deliberately
does NOT carry (its docstring: the CSRF ``register_state`` /
``pop_state`` machinery "lives in the OAuth callback HTTP layer, built
on top of FastAPI in Lane E"):

* :func:`register_state` — mint a single-use CSRF ``state`` token, store
  only its sha256, hand the raw token back for the authorize-link URL.
* :func:`pop_state` — atomically consume a state on callback
  (single-use; reports ``ok`` / ``unknown`` / ``reused`` / ``expired``).
* :func:`complete_tiktok_authorization` — the end-to-end callback path:
  validate state → exchange ``auth_code`` for tokens (via
  :func:`tts_erp_v2.proxy.tiktok_auth.exchange_auth_code`) → upsert the
  ``integration.credentials`` row (keyed by the upstream 19-digit
  ``shop_id``, which the sync worker fans out over) → upsert the
  ``commerce.shops`` channel-account row linked to it.

Flow reference: ``tts-partner-api-docs/Authorization overview.md`` +
``Seller authorization guide.md``.
"""

from __future__ import annotations

import hashlib
import logging
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from tts_erp_v2.db.models.commerce import ChannelAccount
from tts_erp_v2.db.models.integration import OAuthState
from tts_erp_v2.proxy.tiktok_auth import exchange_auth_code
from tts_erp_v2.proxy.token_service import upsert_credentials

log = logging.getLogger("tts_erp_v2.proxy.tiktok_oauth")

#: How long a registered state stays valid. Matches the upstream
#: auth_code validity (30 minutes per the Authorization overview doc)
#: plus headroom so a slightly-slow seller doesn't hit a dead state
#: while their code is still live.
STATE_TTL = timedelta(minutes=45)

#: user_type values we can actually run data jobs for. From the
#: Authorization overview "user_type enumeration" table: 0 = Seller,
#: 4/5 = Global Selling seller (production shop is a CROSS_BORDER VN
#: seller). Creator (1) / partner (2/3) grants are rejected.
ALLOWED_USER_TYPES = frozenset({0, 4, 5})


class OAuthFlowError(ValueError):
    """A hard, user-presentable failure in the authorization flow.

    ``kind`` is a stable machine-readable tag the HTTP layer maps to a
    message + status:
    ``state_invalid`` / ``state_reused`` / ``user_type`` /
    ``missing_shop_id`` / ``missing_shop_cipher``.
    """

    def __init__(self, kind: str, message: str) -> None:
        super().__init__(message)
        self.kind = kind
        self.message = message


def _state_hash(raw_state: str) -> str:
    """sha256 hex of a raw state token (what the DB stores)."""
    return hashlib.sha256(raw_state.encode("utf-8")).hexdigest()


def register_state(
    session: Session,
    *,
    provider: str = "tiktok",
    ttl: timedelta = STATE_TTL,
) -> tuple[str, datetime]:
    """Mint + persist a CSRF state for an authorization flow.

    Returns ``(raw_state, expires_at)``. The raw token is returned
    exactly once (embed in the authorize-link URL); only its sha256 is
    persisted, so a DB leak does not hand out usable states.
    """
    raw = secrets.token_urlsafe(32)
    expires_at = datetime.now(UTC) + ttl
    session.add(
        OAuthState(
            provider=provider,
            state_hash=_state_hash(raw),
            expires_at=expires_at,
        )
    )
    session.commit()
    return raw, expires_at


def pop_state(session: Session, raw_state: str | None) -> tuple[str, int | None]:
    """Atomically consume a state token. Returns ``(status, oauth_state_id)``.

    Status is one of:

    * ``"ok"`` — valid, unconsumed, unexpired; now consumed (single use).
    * ``"unknown"`` — never registered (CSRF gate fails closed).
    * ``"reused"`` — already consumed by an earlier callback.
    * ``"expired"`` — registered but past ``expires_at``.

    Consumption is a single conditional UPDATE ... RETURNING so two
    racing callbacks cannot both win.
    """
    if not raw_state:
        return "unknown", None
    state_hash = _state_hash(raw_state)
    now = datetime.now(UTC)

    consumed = session.execute(
        update(OAuthState)
        .where(
            OAuthState.state_hash == state_hash,
            OAuthState.consumed_at.is_(None),
            OAuthState.expires_at > now,
        )
        .values(consumed_at=now)
        .returning(OAuthState.id)
    ).first()
    if consumed is not None:
        session.commit()
        return "ok", int(consumed[0])

    row = session.execute(
        select(OAuthState).where(OAuthState.state_hash == state_hash)
    ).scalar_one_or_none()
    if row is None:
        return "unknown", None
    if row.consumed_at is not None:
        return "reused", None
    return "expired", None


def complete_tiktok_authorization(
    session: Session,
    *,
    code: str,
    state: str,
) -> dict[str, Any]:
    """Run the callback end-to-end for a TikTok seller grant.

    1. Validate + consume the CSRF state (fails closed on unknown /
       expired / reused state — no upstream call is made).
    2. Exchange ``auth_code`` for tokens (upstream errors propagate as
       :class:`~tts_erp_v2.proxy.errors.UpstreamHttpError`; the state is
       already spent, so the operator starts a fresh link).
    3. Verify the grant is a shop we can sync (user_type in
       :data:`ALLOWED_USER_TYPES`) and carries a ``shop_id``.
    4. Upsert ``integration.credentials`` (key = upstream ``shop_id``)
       and ``commerce.shops`` linked to it, then commit.

    Idempotent per shop: re-authorizing an existing shop updates both
    rows in place (this is also the renewal path).

    Returns a summary dict with ``shop_id`` / ``credential_id`` /
    ``account_id`` / ``account_name`` / ``region`` / ``seller_type`` /
    ``granted_scopes`` / ``expires_at``.

    Raises:
        OAuthFlowError: kind ``state_invalid`` (unknown/expired state),
            ``state_reused``, ``user_type`` (creator/partner grant),
            ``missing_shop_id``.
    """
    status, _sid = pop_state(session, state)
    if status == "reused":
        raise OAuthFlowError(
            "state_reused",
            "this authorization link was already used — start a fresh one",
        )
    if status != "ok":
        raise OAuthFlowError(
            "state_invalid",
            "unknown or expired authorization state — restart the flow "
            "from /v2/oauth/tiktok/authorize",
        )

    grant = exchange_auth_code(auth_code=code)

    user_type = grant.get("user_type")
    if user_type not in ALLOWED_USER_TYPES:
        raise OAuthFlowError(
            "user_type",
            f"authorized user_type={user_type!r} is not a TikTok Shop "
            "seller (allowed: seller 0 / global-selling 4, 5) — a "
            "creator or partner grant cannot be synced by this system",
        )

    shop_id = grant.get("shop_id")
    if not shop_id:
        raise OAuthFlowError(
            "missing_shop_id",
            "token response carried no shop_id — cannot key a credentials "
            "row; check the Partner Center app scopes",
        )
    shop_id = str(shop_id)

    # Cross-border routing + HMAC signing require shop_cipher on EVERY
    # shop-scoped data call (proxy/tts_shop: products_api raises on an
    # empty cipher). Fail loudly here instead of writing a credential
    # row whose first sync job dies with CredentialsMissing.
    #
    # ⚠ contract note: the Authorization overview doc's token/get data
    # field table does NOT list shop_cipher / shop_id — the original
    # Lane-E unit mocks assumed both. If the real upstream response
    # omits them, this flow surfaces immediately with kind
    # ``missing_shop_cipher`` (never a silent half-broken row); the
    # fix is then to source them via TikTok's "Get Authorized Shop"
    # call after token/get, not to weaken this check.
    shop_cipher = grant.get("shop_cipher")
    if not shop_cipher:
        raise OAuthFlowError(
            "missing_shop_cipher",
            "token response carried no shop_cipher — every tiktok data job "
            "signs with shop_cipher and cross-border routing requires it; "
            "verify the real token/get response with a test account (the "
            "Authorization overview data table does not document it), or "
            "extend this flow to fetch it via Get Authorized Shop",
        )

    account_name = grant.get("account_name")
    region = grant.get("region")
    seller_type = grant.get("seller_type")
    granted_scopes = grant.get("granted_scopes")
    now = datetime.now(UTC)

    cred_row = upsert_credentials(
        session,
        provider="tiktok",
        external_account_id=shop_id,
        plaintext_access_token=grant["access_token"],
        plaintext_refresh_token=grant.get("refresh_token"),
        plaintext_shop_cipher=shop_cipher,
        account_label=account_name,
        expires_at=grant.get("expires_at"),
        granted_scopes=granted_scopes,
    )
    credential_id = cred_row.id

    acct_vals: dict[str, Any] = {
        "platform": "tiktok",
        "shop_id": shop_id,
        "account_name": account_name,
        "region": region,
        "seller_type": seller_type,
        "status": "active",
        "credential_id": credential_id,
        "source_updated_at": now,
        "synced_at": now,
        "updated_at": now,
    }
    insert_stmt = pg_insert(ChannelAccount).values(**acct_vals)
    upsert_stmt = insert_stmt.on_conflict_do_update(
        index_elements=["platform", "shop_id"],
        set_={
            "account_name": acct_vals["account_name"],
            "region": acct_vals["region"],
            "seller_type": acct_vals["seller_type"],
            "status": "active",
            "credential_id": acct_vals["credential_id"],
            "source_updated_at": acct_vals["source_updated_at"],
            "synced_at": acct_vals["synced_at"],
            "updated_at": acct_vals["updated_at"],
        },
    )
    session.execute(upsert_stmt)
    acct_row = session.execute(
        select(ChannelAccount).where(
            ChannelAccount.platform == "tiktok",
            ChannelAccount.shop_id == shop_id,
        )
    ).scalar_one()
    session.commit()
    session.expire_all()

    log.info(
        "tiktok oauth: shop=%s authorized (user_type=%s region=%s scopes=%s)",
        shop_id,
        user_type,
        region,
        granted_scopes,
    )
    return {
        "shop_id": shop_id,
        "credential_id": credential_id,
        "account_id": acct_row.id,
        "account_name": account_name,
        "region": region,
        "seller_type": seller_type,
        "granted_scopes": granted_scopes,
        "expires_at": grant.get("expires_at"),
    }


__all__ = [
    "ALLOWED_USER_TYPES",
    "STATE_TTL",
    "OAuthFlowError",
    "complete_tiktok_authorization",
    "pop_state",
    "register_state",
]
