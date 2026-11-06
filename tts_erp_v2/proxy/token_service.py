"""Credential persistence + refresh orchestration for the proxy layer.

Wave 5 migration of ``tdd/oauth_receiver_core.py`` into the proxy
package. Targets:

* :func:`encrypt` / :func:`decrypt` — Fernet helpers (byte-for-byte
  equivalent to ``oauth_receiver_core.encrypt/decrypt``).
* :func:`is_expired` — expires_at comparison with a configurable skew.
* :func:`upsert_credentials` — write a Credentials row, replacing if
  ``(provider, external_account_id)`` already exists.
* :func:`load_credentials` — decrypt + return a typed dataclass.
* :func:`refresh_if_needed` — call a refresher when expired, persist
  the new tokens, return the loaded row.

What we do NOT carry over
-------------------------
* The CSRF ``register_state`` / ``pop_state`` machinery — that lives
  in the OAuth callback HTTP layer, which is built on top of FastAPI
  in Lane E.
* ``fetch_shops`` / ``call_token_endpoint`` — those are upstream-
  specific HTTP paths that belong in the per-provider client
  modules, not in a generic token store.

Encryption key
--------------
The Fernet key is read from ``TTS_ERP_FERNET_KEY``. We do NOT read
``OAUTH_DB_ENCRYPTION_KEY`` (the legacy name from the v1 oauth-receiver
service, retired and dropped on 2026-09-05; see CHANGELOG ops entry).
tts-erp-v2 owns its own key via the ``integration.credentials`` table
single source of truth (see AGENTS.md §4.1).
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from tts_erp_v2.db.models.integration import Credentials, TikTokAppCredential
from tts_erp_v2.proxy.errors import DecryptionError, SigningError
from tts_erp_v2.runtime_settings import get_setting

log = logging.getLogger("tts_erp_v2.proxy.token_service")

# ---- Fernet singleton (lazy) ---------------------------------------


_UNSET: Any = object()
_fernet: Fernet | Any | None = _UNSET
_fernet_key: str | None = None
_fernet_forced = False

# Default refresh skew: refresh if expires_at is within this window.
DEFAULT_REFRESH_SKEW = timedelta(seconds=60)


def _resolve_key() -> str | None:
    """Read TTS_ERP_FERNET_KEY from reloadable configuration."""
    raw = (get_setting("TTS_ERP_FERNET_KEY") or "").strip()
    return raw or None


def _configure_fernet(key: str) -> None:
    """Force-set the Fernet singleton (test helper)."""
    global _fernet, _fernet_forced, _fernet_key
    _fernet = Fernet(key.encode("utf-8"))
    _fernet_key = key
    _fernet_forced = True


def _reset_for_testing() -> None:
    """Drop the Fernet singleton (test helper, mirrored from legacy)."""
    global _fernet, _fernet_forced, _fernet_key
    _fernet = _UNSET
    _fernet_key = None
    _fernet_forced = False


def _get_fernet() -> Fernet | None:
    global _fernet, _fernet_key
    if _fernet_forced:
        return _fernet  # type: ignore[return-value]
    key = _resolve_key()
    if _fernet is not _UNSET and key == _fernet_key:
        return _fernet  # type: ignore[return-value]
    _fernet_key = key
    if not key:
        _fernet = None
        return None
    try:
        _fernet = Fernet(key.encode("utf-8"))
    except Exception:  # noqa: BLE001
        _fernet = None
    return _fernet  # type: ignore[return-value]


# ---- Encrypt / decrypt (byte-for-byte equivalent to legacy) -------


def encrypt(plaintext: str) -> bytes:
    f = _get_fernet()
    if f is None:
        raise SigningError("TTS_ERP_FERNET_KEY not configured")
    return f.encrypt(plaintext.encode("utf-8"))


def decrypt(blob: bytes) -> str:
    f = _get_fernet()
    if f is None:
        raise SigningError("TTS_ERP_FERNET_KEY not configured")
    try:
        return f.decrypt(bytes(blob)).decode("utf-8")
    except InvalidToken as e:
        raise DecryptionError(f"Fernet decryption failed: {e}") from e


def mask_secret(secret: str) -> str:
    """Display-safe mask: keep prefix and suffix, replace middle with '...'."""
    if not secret or len(secret) <= 12:
        return "****"
    return f"{secret[:8]}...{secret[-4:]}  (len={len(secret)})"


# ---- TikTok Partner App credentials --------------------------------


@dataclass(frozen=True)
class TikTokAppCredentials:
    """A decrypted App pair owned by a single shop."""

    service_id: str
    app_key: str
    app_secret: str
    source: str
    revision: int = 1


def upsert_tiktok_app_credentials(
    session: Session,
    *,
    shop_pk: int,
    service_id: str,
    app_key: str,
    plaintext_app_secret: str,
) -> TikTokAppCredential:
    """Insert or rotate this shop's pair; callers own the transaction."""
    service_id = service_id.strip()
    app_key = app_key.strip()
    if not service_id or not app_key or not plaintext_app_secret:
        raise SigningError("Service ID, App Key and App Secret are required")
    values = {
        "shop_pk": shop_pk,
        "service_id": service_id,
        "app_key": app_key,
        "app_secret_ciphertext": encrypt(plaintext_app_secret),
        "updated_at": datetime.now(UTC),
    }
    insert_stmt = pg_insert(TikTokAppCredential).values(**values)
    session.execute(
        insert_stmt.on_conflict_do_update(
            index_elements=["shop_pk", "service_id"],
            set_={
                "app_key": values["app_key"],
                "app_secret_ciphertext": values["app_secret_ciphertext"],
                "revision": TikTokAppCredential.revision + 1,
                "updated_at": values["updated_at"],
            },
        )
    )
    return session.execute(
        select(TikTokAppCredential)
        .where(
            TikTokAppCredential.shop_pk == shop_pk,
            TikTokAppCredential.service_id == service_id,
        )
        .execution_options(populate_existing=True)
    ).scalar_one()


def resolve_tiktok_app_credentials(
    session: Session,
    *,
    shop_pk: int,
    service_id: str,
) -> TikTokAppCredentials:
    """Resolve only this shop's configuration, without a global fallback."""
    row = session.execute(
        select(TikTokAppCredential)
        .where(
            TikTokAppCredential.shop_pk == shop_pk,
            TikTokAppCredential.service_id == service_id,
        )
        .execution_options(populate_existing=True)
    ).scalar_one_or_none()
    if row is None:
        raise SigningError(
            "Configure this shop's Service ID, App Key and App Secret first"
        )
    app_secret = decrypt(row.app_secret_ciphertext)
    if not row.app_key or not app_secret:
        raise SigningError("This shop's App configuration is incomplete")
    return TikTokAppCredentials(
        service_id=service_id,
        app_key=row.app_key,
        app_secret=app_secret,
        source="database",
        revision=row.revision,
    )


# ---- Expires-at helpers --------------------------------------------


def is_expired(
    expires_at: datetime | None,
    *,
    now: datetime | None = None,
    skew: timedelta = DEFAULT_REFRESH_SKEW,
) -> bool:
    """Return True if the token is past expires_at (within skew window).

    ``None`` expiry → never expires (returns False).
    Naive datetimes are assumed UTC.
    """
    if expires_at is None:
        return False
    if now is None:
        now = datetime.now(UTC)
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=UTC)
    return expires_at <= now + skew


# ---- Typed credential payload --------------------------------------


@dataclass
class CredentialsView:
    """Decrypted credential payload returned to callers.

    Plaintext fields are populated from ``integration.credentials``.
    """

    id: int
    provider: str
    external_account_id: str
    account_label: str | None
    access_token: str
    refresh_token: str | None
    shop_cipher: str | None
    expires_at: datetime | None
    granted_scopes: list | None
    service_id: str | None
    extra: dict | None

    @classmethod
    def from_row(cls, row: Credentials) -> CredentialsView:
        # Decode ciphertext envelope (we store a JSON bundle rather
        # than separate columns, so the legacy schema's nullable
        # shop_cipher / refresh_token can both round-trip cleanly).
        plaintext = decrypt(row.ciphertext)
        try:
            envelope = json.loads(plaintext)
        except json.JSONDecodeError:
            # Legacy / externally-overwritten format: ciphertext holds a
            # bare Fernet(access_token) instead of the JSON envelope.
            # Don't crash the whole tick — degrade to a single-token view
            # and let the operator re-run the re-encrypt migration. The
            # missing refresh_token / shop_cipher will surface as
            # upstream 401 / invalid-sign rather than a hard process crash.
            log.warning(
                "credentials ciphertext is not a JSON envelope for "
                "%s:%r — degrading to bare access_token view; "
                "re-run scripts/migrate_v1_to_v2/re_encrypt_credentials.py "
                "to repair (decrypted %d bytes, not JSON)",
                row.provider,
                row.external_account_id,
                len(plaintext),
            )
            return cls(
                id=row.id,
                provider=row.provider,
                external_account_id=row.external_account_id,
                account_label=row.account_label,
                access_token=plaintext,
                refresh_token=None,
                shop_cipher=None,
                expires_at=row.expires_at,
                granted_scopes=row.granted_scopes,
                service_id=row.service_id,
                extra=row.extra,
            )
        if not isinstance(envelope, dict) or "access_token" not in envelope:
            raise DecryptionError(
                "credentials envelope missing access_token for "
                f"{row.provider}:{row.external_account_id!r}"
            )
        return cls(
            id=row.id,
            provider=row.provider,
            external_account_id=row.external_account_id,
            account_label=row.account_label,
            access_token=envelope["access_token"],
            refresh_token=envelope.get("refresh_token"),
            shop_cipher=envelope.get("shop_cipher"),
            expires_at=row.expires_at,
            granted_scopes=row.granted_scopes,
            service_id=row.service_id,
            extra=row.extra,
        )


# ---- Upsert / load -------------------------------------------------


def upsert_credentials(
    session: Session,
    *,
    provider: str,
    external_account_id: str,
    plaintext_access_token: str,
    plaintext_refresh_token: str | None = None,
    plaintext_shop_cipher: str | None = None,
    account_label: str | None = None,
    expires_at: datetime | None = None,
    granted_scopes: list | None = None,
    service_id: str | None = None,
    extra: dict | None = None,
) -> Credentials:
    """Insert or update a Credentials row keyed by (provider, external_account_id).

    Stores the three plaintext fields in a single encrypted JSON bundle
    inside ``ciphertext`` to keep the schema tight (the existing
    ``Credentials`` model has one ``ciphertext`` bytea column).

    Returns the row (caller must ``session.commit()``).
    """
    if not plaintext_access_token:
        raise SigningError("plaintext_access_token is required")

    envelope = {
        "access_token": plaintext_access_token,
    }
    if plaintext_refresh_token:
        envelope["refresh_token"] = plaintext_refresh_token
    if plaintext_shop_cipher:
        envelope["shop_cipher"] = plaintext_shop_cipher
    blob = encrypt(json.dumps(envelope, ensure_ascii=False))

    values: dict[str, Any] = {
        "provider": provider,
        "external_account_id": external_account_id,
        "ciphertext": blob,
        "account_label": account_label,
        "expires_at": expires_at,
        "granted_scopes": granted_scopes,
        "service_id": service_id,
        "extra": extra,
        "updated_at": datetime.now(UTC),
    }
    insert_stmt = pg_insert(Credentials).values(**values)
    upsert_stmt = insert_stmt.on_conflict_do_update(
        index_elements=["provider", "external_account_id"],
        set_={
            "ciphertext": blob,
            "account_label": values["account_label"],
            "expires_at": values["expires_at"],
            "granted_scopes": values["granted_scopes"],
            "service_id": func.coalesce(
                insert_stmt.excluded.service_id,
                Credentials.service_id,
            ),
            "extra": values["extra"],
            "updated_at": values["updated_at"],
        },
    )
    session.execute(upsert_stmt)
    row = session.execute(
        select(Credentials).where(
            Credentials.provider == provider,
            Credentials.external_account_id == external_account_id,
        )
    ).scalar_one()
    return row


def load_credentials(
    session: Session,
    provider: str,
    external_account_id: str,
) -> CredentialsView | None:
    """Load + decrypt a Credentials row.

    Returns None if the row doesn't exist. Raises :class:`DecryptionError`
    if the ciphertext was tampered with or the Fernet key changed.
    """
    row = session.execute(
        select(Credentials)
        .where(
            Credentials.provider == provider,
            Credentials.external_account_id == external_account_id,
        )
        .execution_options(populate_existing=True)
    ).scalar_one_or_none()
    if row is None:
        return None
    return CredentialsView.from_row(row)


# ---- Refresh orchestration -----------------------------------------


#: Type signature for refresher callables. Returns a dict with at
#: minimum ``access_token``; may also include ``refresh_token``,
#: ``shop_cipher``, ``expires_at``.
RefresherFn = Callable[[CredentialsView], dict]


def refresh_if_needed(
    session: Session,
    *,
    provider: str,
    external_account_id: str,
    refresher: RefresherFn,
    skew: timedelta = DEFAULT_REFRESH_SKEW,
    force: bool = False,
) -> CredentialsView | None:
    """If the credentials are expired (or within skew), call refresher and persist.

    Args:
        session: SQLAlchemy session.
        provider: ``"tiktok"`` / ``"miaoshou"``.
        external_account_id: shop_id / licenseId.
        refresher: callable(CredentialsView) returning a dict; consumes the exact CAS snapshot.
        skew: refresh window.
        force: skip the ``is_expired`` check and always call the refresher.
            Used by reactive-refresh paths where the caller already
            received a 401 — that's definitive proof the token is dead,
            regardless of what ``expires_at`` says (it may be NULL).

    Returns:
        A :class:`CredentialsView` (post-refresh if refreshed, original
        if still fresh). Returns None if no row exists.
    """
    with session.begin():
        row = session.execute(
            select(Credentials)
            .where(
                Credentials.provider == provider,
                Credentials.external_account_id == external_account_id,
            )
            .execution_options(populate_existing=True)
        ).scalar_one_or_none()
        if row is None:
            return None
        old_view = CredentialsView.from_row(row)
        if not force and not is_expired(row.expires_at, skew=skew):
            return old_view
        previous_ciphertext = row.ciphertext
        metadata = {
            "account_label": row.account_label,
            "granted_scopes": row.granted_scopes,
            "service_id": row.service_id,
            "extra": row.extra,
        }

    # Call the refresher (caller-supplied; tested with a fake).
    fresh = refresher(old_view)
    if not fresh or not fresh.get("access_token"):
        # Refresher failed; return the stale row so the caller can decide.
        return old_view

    # Carry forward the shop identity when the refresh response omits
    # it. TikTok's token-refresh response has stopped returning
    # ``shop_cipher`` (observed 2026-09-05 18:00Z — the envelope was
    # rewritten without it and every downstream 202309 call started
    # failing with ``Missing identifier … shop_cipher … required``).
    # Rotating tokens must NOT silently drop shop identity: only an
    # explicit value from the refresher replaces the old one. Same
    # guard for refresh_token (a refresher that omits it must not
    # strand us token-less on the next refresh).
    next_refresh_token = fresh.get("refresh_token") or old_view.refresh_token
    next_shop_cipher = fresh.get("shop_cipher") or old_view.shop_cipher

    expires_at_raw = fresh.get("expires_at")
    expires_at: datetime | None = None
    if isinstance(expires_at_raw, datetime):
        expires_at = expires_at_raw
    elif isinstance(expires_at_raw, str):
        try:
            expires_at = datetime.fromisoformat(expires_at_raw)
            if expires_at.tzinfo is None:
                expires_at = expires_at.replace(tzinfo=UTC)
        except ValueError:
            expires_at = None

    with session.begin():
        current = session.execute(
            select(Credentials)
            .where(
                Credentials.provider == provider,
                Credentials.external_account_id == external_account_id,
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        ).scalar_one_or_none()
        if current is None:
            return None
        # A concurrent OAuth callback/refresh owns the newer identity. Never
        # overwrite it with a response obtained from the previous credentials.
        if current.id != old_view.id or current.ciphertext != previous_ciphertext:
            return CredentialsView.from_row(current)
        upsert_credentials(
            session,
            provider=provider,
            external_account_id=external_account_id,
            plaintext_access_token=fresh["access_token"],
            plaintext_refresh_token=next_refresh_token,
            plaintext_shop_cipher=next_shop_cipher,
            expires_at=expires_at,
            **metadata,
        )
        session.flush()
        session.expire(current)
        return CredentialsView.from_row(current)


__all__ = [
    "DEFAULT_REFRESH_SKEW",
    "CredentialsView",
    "DecryptionError",
    "RefresherFn",
    "SigningError",
    "decrypt",
    "encrypt",
    "is_expired",
    "load_credentials",
    "mask_secret",
    "refresh_if_needed",
    "upsert_credentials",
]
