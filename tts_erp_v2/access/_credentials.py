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


def _db_lookup(key_hash: str, *, require_current: bool = False) -> Credential | None:
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
        if require_current and row.rotated_to_key_hash is not None:
            # 已被轮换取代的旧 key 不再是有效凭证，即使 status 尚未翻转。
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


def authenticate_hash_uncached(key_hash: str) -> Credential | None:
    """Re-read a credential straight from the store, ignoring the TTL cache.

    ``authenticate_hash`` memoises a success for up to ``CACHE_TTL`` seconds, so
    it cannot answer "is this key still good right now?". A boundary that must
    react to revocation uses this instead, and a rejection evicts the stale
    positive entry so it does not keep living for another TTL.
    """

    result = _db_lookup(key_hash, require_current=True)
    if result is None:
        _cache.pop(key_hash, None)
    return result


def authenticate_monitor_delivery_key(plaintext: str) -> Credential | None:
    """Return the credential allowed to read one shop's monitor delivery.

    This is the authoritative check for the delivery boundary
    (docs/monitor-config-sync.md §4.2 约束 4/5). Unlike the middleware, which
    gates on a possibly cached credential, it re-reads the key so that disabling
    or rotating it takes effect on the very next request, and it refuses
    anything that is not an active, un-rotated, shop-bound ``monitor_config``
    key — a general key or a browser session is not delivery authorization.
    """

    credential = authenticate_hash_uncached(hash_key(plaintext))
    if credential is None:
        return None
    if credential.purpose is not ApiKeyPurpose.MONITOR_CONFIG:
        return None
    if credential.shop_pk is None:
        return None
    return credential
