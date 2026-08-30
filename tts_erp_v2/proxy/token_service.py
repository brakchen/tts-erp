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
``OAUTH_DB_ENCRYPTION_KEY`` (the legacy name) — that env var is
operator-owned by oauth-receiver; tts-erp-v2 owns its own.
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from tts_erp_v2.db.models.integration import Credentials
from tts_erp_v2.proxy.errors import DecryptionError, SigningError

# ---- Fernet singleton (lazy) ---------------------------------------


_UNSET: Any = object()
_fernet: Fernet | Any | None = _UNSET

# Default refresh skew: refresh if expires_at is within this window.
DEFAULT_REFRESH_SKEW = timedelta(seconds=60)


def _resolve_key() -> str | None:
    """Read TTS_ERP_FERNET_KEY from env. Empty / unset → None."""
    raw = os.environ.get("TTS_ERP_FERNET_KEY", "").strip()
    return raw or None


def _configure_fernet(key: str) -> None:
    """Force-set the Fernet singleton (test helper)."""
    global _fernet
    _fernet = Fernet(key.encode("utf-8"))


def _reset_for_testing() -> None:
    """Drop the Fernet singleton (test helper, mirrored from legacy)."""
    global _fernet
    _fernet = _UNSET


def _get_fernet() -> Fernet | None:
    global _fernet
    if _fernet is not _UNSET:
        return _fernet  # type: ignore[return-value]
    key = _resolve_key()
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
        now = datetime.now(timezone.utc)
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
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
            raise DecryptionError(
                "credentials ciphertext is not a JSON envelope for "
                f"{row.provider}:{row.external_account_id!r} — re-run "
                "scripts/migrate_v1_to_v2/re_encrypt_credentials.py "
                f"(decrypted {len(plaintext)} bytes, not JSON)"
            ) from None
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
        "extra": extra,
        "updated_at": datetime.now(timezone.utc),
    }
    insert_stmt = pg_insert(Credentials).values(**values)
    upsert_stmt = insert_stmt.on_conflict_do_update(
        index_elements=["provider", "external_account_id"],
        set_={
            "ciphertext": blob,
            "account_label": values["account_label"],
            "expires_at": values["expires_at"],
            "granted_scopes": values["granted_scopes"],
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
        select(Credentials).where(
            Credentials.provider == provider,
            Credentials.external_account_id == external_account_id,
        )
    ).scalar_one_or_none()
    if row is None:
        return None
    return CredentialsView.from_row(row)


# ---- Refresh orchestration -----------------------------------------


#: Type signature for refresher callables. Returns a dict with at
#: minimum ``access_token``; may also include ``refresh_token``,
#: ``shop_cipher``, ``expires_at``.
RefresherFn = Callable[[str, str], dict]


def refresh_if_needed(
    session: Session,
    *,
    provider: str,
    external_account_id: str,
    refresher: RefresherFn,
    skew: timedelta = DEFAULT_REFRESH_SKEW,
) -> CredentialsView | None:
    """If the credentials are expired (or within skew), call refresher and persist.

    Args:
        session: SQLAlchemy session.
        provider: ``"tiktok"`` / ``"miaoshou"``.
        external_account_id: shop_id / licenseId.
        refresher: callable(provider, external_account_id) returning a dict.
        skew: refresh window.

    Returns:
        A :class:`CredentialsView` (post-refresh if refreshed, original
        if still fresh). Returns None if no row exists.
    """
    row = session.execute(
        select(Credentials).where(
            Credentials.provider == provider,
            Credentials.external_account_id == external_account_id,
        )
    ).scalar_one_or_none()
    if row is None:
        return None

    if not is_expired(row.expires_at, skew=skew):
        return CredentialsView.from_row(row)

    # Call the refresher (caller-supplied; tested with a fake).
    fresh = refresher(provider, external_account_id)
    if not fresh or not fresh.get("access_token"):
        # Refresher failed; return the stale row so the caller can decide.
        return CredentialsView.from_row(row)

    expires_at_raw = fresh.get("expires_at")
    expires_at: datetime | None = None
    if isinstance(expires_at_raw, datetime):
        expires_at = expires_at_raw
    elif isinstance(expires_at_raw, str):
        try:
            expires_at = datetime.fromisoformat(expires_at_raw)
            if expires_at.tzinfo is None:
                expires_at = expires_at.replace(tzinfo=timezone.utc)
        except ValueError:
            expires_at = None

    upsert_credentials(
        session,
        provider=provider,
        external_account_id=external_account_id,
        plaintext_access_token=fresh["access_token"],
        plaintext_refresh_token=fresh.get("refresh_token"),
        plaintext_shop_cipher=fresh.get("shop_cipher"),
        account_label=row.account_label,
        expires_at=expires_at,
        granted_scopes=row.granted_scopes,
        extra=row.extra,
    )
    session.commit()
    # Expire the identity map so the post-commit SELECT refetches the
    # updated ciphertext + expires_at instead of returning the stale
    # ORM-cached row (which would carry the old plaintext envelope).
    session.expire_all()
    return load_credentials(session, provider, external_account_id)


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
