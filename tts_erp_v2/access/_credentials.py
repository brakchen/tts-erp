"""Concrete PostgreSQL credential lookup and process-local cache."""

from __future__ import annotations

import hashlib
import hmac
import time
from datetime import UTC, datetime

from tts_erp_v2.access._types import Credential, Role

CACHE_TTL = 60.0
NEG_CACHE_TTL = 20.0

_cache: dict[str, tuple[Credential | None, float]] = {}


def clear_credential_cache() -> None:
    _cache.clear()


def hash_key(plaintext: str) -> str:
    return hashlib.sha256(plaintext.encode()).hexdigest()


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
        try:
            row.last_used_at = datetime.now(UTC)
            session.commit()
        except Exception:
            session.rollback()
        return Credential(key_hash=key_hash, role=role)


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
