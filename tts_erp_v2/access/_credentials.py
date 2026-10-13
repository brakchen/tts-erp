"""Concrete PostgreSQL credential lookup and process-local cache."""

from __future__ import annotations

import hashlib
import hmac
import time
from datetime import UTC, datetime

from tts_erp_v2.access._types import ApiKeyPurpose, Credential, Role

CACHE_TTL = 60.0
NEG_CACHE_TTL = 20.0

_cache: dict[str, tuple[Credential | None, float]] = {}


def clear_credential_cache() -> None:
    _cache.clear()


def hash_key(plaintext: str) -> str:
    return hashlib.sha256(plaintext.encode()).hexdigest()


def _purpose_of(purpose: str | None, shop_pk: int | None) -> ApiKeyPurpose | None:
    """Return the key's restricted purpose, or None when it is not usable.

    A ``monitor_config`` key without a shop binding could serve any shop, so it
    is treated as no credential at all rather than degrading to a general key.
    The 0070 CHECK constraint makes this unreachable in the database; this is
    the second line of defence so the access owner never relies on it.
    """

    try:
        parsed = ApiKeyPurpose(purpose or ApiKeyPurpose.GENERAL)
    except ValueError:
        return None
    if parsed is ApiKeyPurpose.MONITOR_CONFIG and shop_pk is None:
        return None
    return parsed


def _db_lookup(key_hash: str) -> Credential | None:
    from sqlalchemy import select

    from tts_erp_v2.db.base import get_session_factory
    from tts_erp_v2.db.models.security import ApiKey

    session_factory = get_session_factory()
    with session_factory() as session:
        row = session.execute(
            select(ApiKey).where(ApiKey.key_hash == key_hash)
        ).scalar_one_or_none()
        if row is None or not hmac.compare_digest(row.key_hash, key_hash):
            return None
        if row.status != "active":
            return None
        if row.last_used_at is not None and (
            row.last_used_at < datetime(1970, 1, 1, tzinfo=UTC)
        ):
            return None
        try:
            role = Role(row.role)
        except ValueError:
            return None
        purpose = _purpose_of(row.purpose, row.shop_pk)
        if purpose is None:
            return None
        try:
            row.last_used_at = datetime.now(UTC)
            session.commit()
        except Exception:  # noqa: BLE001 — last-used update is best-effort
            session.rollback()
        return Credential(
            key_hash=key_hash, role=role, purpose=purpose, shop_pk=row.shop_pk
        )


def authenticate_hash(key_hash: str) -> Credential | None:
    """Return a current credential for a SHA-256 hash, with TTL caching."""

    now = time.monotonic()
    hit = _cache.get(key_hash)
    if hit is not None and hit[1] > now:
        return hit[0]

    result = _db_lookup(key_hash)
    ttl = CACHE_TTL if result is not None else NEG_CACHE_TTL
    _cache[key_hash] = (result, now + ttl)
    return result


def authenticate_key(plaintext: str) -> Credential | None:
    """Return a current credential for a plaintext API key."""

    return authenticate_hash(hash_key(plaintext))
