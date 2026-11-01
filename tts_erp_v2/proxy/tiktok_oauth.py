"""Single-use OAuth state and authorization for one existing target shop.

App configuration belongs to that shop. A callback must return exactly its
shop ID; no other shop is created or updated by the authorization flow.
"""

from __future__ import annotations

import hashlib
import logging
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from tts_erp_v2.db.models.commerce import ChannelAccount
from tts_erp_v2.db.models.integration import OAuthState, TikTokAppCredential
from tts_erp_v2.proxy.tiktok_auth import (
    exchange_auth_code,
    fetch_authorized_shops,
)
from tts_erp_v2.proxy.token_service import (
    resolve_tiktok_app_credentials,
    upsert_credentials,
)

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
    ``app_credentials`` / ``app_key_mismatch`` / ``no_authorized_shop`` /
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
    extra: dict | None = None,
) -> tuple[str, datetime]:
    """Mint + persist a CSRF state for an authorization flow.

    Returns ``(raw_state, expires_at)``. The raw token is returned
    exactly once (embed in the authorize-link URL); only its sha256 is
    persisted, so a DB leak does not hand out usable states.

    Args:
        extra: 任意附加元数据（如 ``service_id``），存入 OAuthState.extra，
            callback 时可取出。
    """
    raw = secrets.token_urlsafe(32)
    expires_at = datetime.now(UTC) + ttl
    session.add(
        OAuthState(
            provider=provider,
            state_hash=_state_hash(raw),
            expires_at=expires_at,
            extra=extra,
        )
    )
    session.commit()
    return raw, expires_at


def pop_state(
    session: Session, raw_state: str | None
) -> tuple[str, int | None, dict | None]:
    """Atomically consume a state token.

    Returns ``(status, oauth_state_id, extra)``.

    Status is one of:

    * ``"ok"`` — valid, unconsumed, unexpired; now consumed (single use).
    * ``"unknown"`` — never registered (CSRF gate fails closed).
    * ``"reused"`` — already consumed by an earlier callback.
    * ``"expired"`` — registered but past ``expires_at``.

    Consumption is a single conditional UPDATE ... RETURNING so two
    racing callbacks cannot both win.

    ``extra`` 是注册时存入的附加元数据（如 ``service_id``），仅
    ``status == "ok"`` 时有效。
    """
    if not raw_state:
        return "unknown", None, None
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
        .returning(OAuthState.id, OAuthState.extra)
    ).first()
    if consumed is not None:
        session.commit()
        return "ok", consumed[0], consumed[1]  # id, extra

    row = session.execute(
        select(OAuthState).where(OAuthState.state_hash == state_hash)
    ).scalar_one_or_none()
    if row is None:
        return "unknown", None, None
    if row.consumed_at is not None:
        return "reused", None, None
    return "expired", None, None


def complete_tiktok_authorization(
    session: Session,
    *,
    code: str,
    state: str,
    callback_app_key: str | None = None,
) -> dict[str, Any]:
    """Authorize exactly the shop that created the single-use state."""
    status, _sid, extra = pop_state(session, state)
    if status == "reused":
        raise OAuthFlowError("state_reused", "This link has already been used; generate a fresh link")
    if status != "ok" or not isinstance(extra, dict):
        raise OAuthFlowError("state_invalid", "Unknown or expired link; generate a fresh link")
    shop_pk = extra.get("expected_shop_pk")
    expected_shop_id = extra.get("expected_shop_id")
    service_id = extra.get("service_id")
    revision = extra.get("app_revision")
    if not shop_pk or not expected_shop_id or not service_id or not revision:
        raise OAuthFlowError("state_invalid", "This link has no target shop; generate a fresh link")

    def target_configuration(*, lock: bool = False):
        query = select(ChannelAccount).where(ChannelAccount.id == shop_pk)
        app_query = select(TikTokAppCredential).where(
            TikTokAppCredential.shop_pk == shop_pk,
            TikTokAppCredential.service_id == service_id,
        )
        if lock:
            query = query.with_for_update()
            app_query = app_query.with_for_update()
        account = session.execute(query.execution_options(populate_existing=True)).scalar_one_or_none()
        app = session.execute(app_query.execution_options(populate_existing=True)).scalar_one_or_none()
        if (account is None or account.platform != "tiktok"
                or account.shop_id != expected_shop_id or account.service_id != service_id
                or app is None or app.revision != revision):
            raise OAuthFlowError("configuration_changed", "The shop App configuration changed; generate a fresh link")
        return account

    target_configuration()
    app_credentials = resolve_tiktok_app_credentials(
        session, shop_pk=shop_pk, service_id=service_id,
    )
    if app_credentials.revision != revision:
        raise OAuthFlowError("configuration_changed", "The shop App configuration changed; generate a fresh link")
    if callback_app_key and callback_app_key != app_credentials.app_key:
        raise OAuthFlowError("app_key_mismatch", "The callback App Key does not match this shop's App")
    grant = exchange_auth_code(auth_code=code, app_credentials=app_credentials)
    if grant.get("user_type") not in ALLOWED_USER_TYPES:
        raise OAuthFlowError("user_type", "The authorization must belong to a TikTok Shop seller")
    shops = fetch_authorized_shops(
        access_token=grant["access_token"], app_credentials=app_credentials,
    )
    if len(shops) != 1 or shops[0].get("shop_id") != expected_shop_id:
        raise OAuthFlowError("shop_mismatch", "Authorization must return exactly the target shop; no shop was updated")
    shop = shops[0]
    if not shop.get("shop_cipher"):
        raise OAuthFlowError("missing_shop_cipher", "The target shop's authorization is missing its shop cipher")

    account = target_configuration(lock=True)
    now = datetime.now(UTC)
    cred = upsert_credentials(
        session, provider="tiktok", external_account_id=expected_shop_id,
        plaintext_access_token=grant["access_token"],
        plaintext_refresh_token=grant.get("refresh_token"),
        plaintext_shop_cipher=shop["shop_cipher"],
        account_label=account.account_name or shop.get("account_name"),
        expires_at=grant.get("expires_at"), granted_scopes=grant.get("granted_scopes"),
        service_id=service_id, extra={"app_revision": revision},
    )
    account.account_name = account.account_name or shop.get("account_name") or grant.get("account_name")
    account.region = account.region or shop.get("region") or grant.get("region")
    account.seller_type = account.seller_type or shop.get("seller_type") or grant.get("seller_type")
    account.credential_id = cred.id
    account.status = "active"
    account.source_updated_at = now
    account.synced_at = now
    account.updated_at = now
    summary = {
        "shop_id": account.shop_id, "credential_id": cred.id, "account_id": account.id,
        "account_name": account.account_name, "region": account.region,
        "seller_type": account.seller_type, "granted_scopes": grant.get("granted_scopes"),
        "expires_at": grant.get("expires_at"),
    }
    session.commit()
    session.expire_all()
    log.info("tiktok oauth: target shop=%s authorized; awaiting scheduled sync", expected_shop_id)
    return {"shops": [summary]}


__all__ = [
    "ALLOWED_USER_TYPES",
    "STATE_TTL",
    "OAuthFlowError",
    "complete_tiktok_authorization",
    "pop_state",
    "register_state",
]
