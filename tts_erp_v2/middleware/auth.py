"""API key auth middleware (ported from tdd/auth.py).

Differences from the legacy implementation:
- Lookup goes through SQLAlchemy ORM against ``security.api_keys`` (Lane 0
  already migrated that table from the legacy ``api_keys`` public table).
- No legacy paths (``/db/*``, ``/orders/*``, ``/sync/*``, ``/token/*``) are
  recognized; the v2 app never registers them. ``required_role()`` only
  classifies v2 + analytics-sync + exempt utility paths.
- Same in-process TTL cache (60 s positive / 20 s negative) so DB lookups
  don't dominate latency.

Auth contract (matches AGENTS.md §9.1):
- ``Authorization: Bearer <key>`` OR ``X-API-Key: <key>``
- Roles: ``readonly < readwrite < admin``
- ``TTS_ERP_AUTH_MODE``: ``off | shadow | enforce`` (read per request)
- 401 / 403 in enforce; pass-through + audit log in shadow; pass-through in off
- Denied requests (401/403) are counted into the shared per-key bucket
  before being rejected, so brute-forcing keys is throttled
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import sys
import time
from datetime import datetime, timezone

from anyio.to_thread import run_sync

ROLE_LEVEL = {"readonly": 1, "readwrite": 2, "admin": 3}
_LEVEL_NAME = {v: k for k, v in ROLE_LEVEL.items()}

CACHE_TTL = 60.0
NEG_CACHE_TTL = 20.0  # invalid-key negative cache: brute-force shield

# key_hash -> (role_level, scopes_tuple, monotonic_deadline)
# Negative entries store (None, (), deadline) and expire after NEG_CACHE_TTL.
_cache: dict[str, tuple[int | None, tuple[str, ...], float]] = {}

EXEMPT_PATHS = {
    "/healthz",
    "/endpoints",
    "/openapi.json",
    "/docs",
    "/redoc",
    "/docs/oauth2-redirect",
}

# Path-level required role for the v2 app.
# Order matters: more-specific prefix wins.
# Read-only paths take role=readonly; the manual-costs POST takes readwrite.
_READONLY_PREFIXES = (
    "/v2/commerce/",
    "/v2/linkage/",
    "/v2/reporting/",
    "/v2/pages/",
)
_READWRITE_EXACT = {
    "/v2/reporting/manual-costs",  # POST only — GET below stays readonly
}
# All other /v2/* paths default to admin (defensive: unknown = privileged).


def clear_cache() -> None:
    _cache.clear()


def required_role(method: str, path: str) -> int | None:
    """Return the minimum role level for ``(method, path)``, or None if exempt."""
    # Strip query string if any.
    p = path.split("?", 1)[0]
    if p in EXEMPT_PATHS:
        return None
    # analytics_sync is readwrite (Chrome extension uploads).
    if p.startswith("/v1/analytics/sync"):
        return ROLE_LEVEL["readwrite"]
    # Miaoshou callback nodes are public (TikTok shop server-to-server push).
    if p.startswith("/miaoshou/callback"):
        return None
    # v2: manual-costs POST requires readwrite.
    if method.upper() == "POST" and p in _READWRITE_EXACT:
        return ROLE_LEVEL["readwrite"]
    # v2: commerce/linkage/reporting GETs are readonly.
    for prefix in _READONLY_PREFIXES:
        if p.startswith(prefix):
            return ROLE_LEVEL["readonly"]
    # Default: admin (fail-closed for unknown paths).
    return ROLE_LEVEL["admin"]


def _sha256(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()


def _db_lookup(key_hash: str) -> tuple[int | None, tuple[str, ...]] | None:
    """Return ``(role_level, scopes_tuple)`` for a valid key, else None.

    Uses SQLAlchemy 2.0 ORM against ``security.api_keys``. Disabled,
    expired, or unknown keys all return None.

    role_level is ``int | None`` because ``ROLE_LEVEL.get()`` returns
    ``None`` for unknown roles. ``security.api_keys`` has no CHECK
    constraint on ``role`` (Lane 0 ships the column plain), so the
    function defensively returns ``None`` when the role string doesn't
    map to a known level — callers treat that as "unknown key" → 401.
    """
    # Imported here to avoid import-time cycle (db.base reads env).
    from sqlalchemy import select

    from tts_erp_v2.db.base import get_session_factory
    from tts_erp_v2.db.models.security import ApiKey

    SessionLocal = get_session_factory()
    with SessionLocal() as sess:
        row = sess.execute(
            select(ApiKey).where(ApiKey.key_hash == key_hash)
        ).scalar_one_or_none()
        if row is None:
            return None
        if not hmac.compare_digest(row.key_hash, key_hash):
            return None
        if row.status != "active":
            return None
        if row.last_used_at is not None and (
            row.last_used_at < datetime(1970, 1, 1, tzinfo=timezone.utc)
        ):
            return None  # defensive: corrupted last_used_at
        # Bump last_used_at on cache miss (best-effort, ignore failure).
        try:
            row.last_used_at = datetime.now(timezone.utc)
            sess.commit()
        except Exception:
            sess.rollback()
        # Lane 0's ApiKey model has no `scopes` column (per the V3 schema
        # decision: api_keys is plain role + status). Scope-based checks
        # are not part of the v2 contract yet.
        level = ROLE_LEVEL.get(row.role)
        if level is None:
            return None  # unknown role string → treat as invalid key
        return level, ()


def lookup_role(key: str) -> tuple[int | None, tuple[str, ...]] | None:
    """Cached role+scopes lookup. DB errors propagate (middleware maps to 503)."""
    key_hash = _sha256(key)
    now = time.monotonic()
    hit = _cache.get(key_hash)
    if hit and hit[2] > now:
        return hit[0], hit[1]
    result = _db_lookup(key_hash)
    if result is not None:
        _cache[key_hash] = (result[0], result[1], now + CACHE_TTL)
    else:
        _cache[key_hash] = (None, (), now + NEG_CACHE_TTL)
    if result is None:
        return None
    return result


def _extract_key(scope: dict) -> str | None:
    headers = scope.get("headers") or []
    for hk, hv in headers:
        k = hk.decode("latin-1").lower()
        v = hv.decode("latin-1")
        if k == "authorization":
            scheme, _, token = v.partition(" ")
            if scheme.lower() == "bearer" and token.strip():
                return token.strip()
        elif k == "x-api-key" and v.strip():
            return v.strip()
    return None


def _prefix_of(key: str | None) -> str:
    return key[:16] if key else "-"


def _deny_response(status: int, msg: str) -> tuple[int, list[tuple[bytes, bytes]], bytes]:
    body = json.dumps({"detail": msg}).encode()
    headers: list[tuple[bytes, bytes]] = [(b"content-type", b"application/json")]
    if status == 401:
        headers.append((b"www-authenticate", b"Bearer"))
    return status, headers, body


class AuthMiddleware:
    """Plain ASGI middleware. Wire with ``app.add_middleware(AuthMiddleware)``.

    Order rule: ``CORS → Auth → RateLimit`` ⇒ register
    ``add_middleware(AuthMiddleware)`` AFTER ``RateLimitMiddleware`` (the
    outermost layer is added LAST in FastAPI's reverse-wrap model).
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        mode = os.environ.get("TTS_ERP_AUTH_MODE", "off")
        if mode == "off":
            await self.app(scope, receive, send)
            return

        method = scope["method"]
        path = scope["path"]
        needed = required_role(method, path)
        if needed is None:
            await self.app(scope, receive, send)
            return

        key = _extract_key(scope)
        if key:
            try:
                result = await run_sync(lookup_role, key)
            except Exception as exc:
                sys.stderr.write(
                    f"[auth] key lookup failed ({type(exc).__name__}): {exc}\n"
                )
                if mode == "shadow":
                    await self.app(scope, receive, send)
                    return
                s, hdrs, body = _deny_response(503, "auth store unavailable")
                await send({"type": "http.response.start", "status": s, "headers": hdrs})
                await send({"type": "http.response.body", "body": body})
                return
        else:
            result = None
        level = result[0] if result else None
        scopes = result[1] if result else ()

        denied: tuple[int, str] | None = None
        if key is None:
            denied = (
                401,
                "missing bearer token (Authorization: Bearer <key> or X-API-Key: <key>)",
            )
        elif level is None:
            denied = (401, "invalid, disabled or expired api key")
        elif level < needed:
            denied = (403, f"requires {_LEVEL_NAME[needed]}")

        # Populate scope for downstream middlewares (rate limiter reads api_key_hash).
        scope["api_key_hash"] = _sha256(key) if key else None
        scope["api_key_role"] = _LEVEL_NAME[level] if level is not None else None
        scope["api_key_scopes"] = scopes

        if denied is None:
            await self.app(scope, receive, send)
            return

        client = (scope.get("client") or ("?",))[0]
        if mode == "shadow":
            sys.stderr.write(
                f"[auth-shadow] would-deny {denied[0]} {method} {path} "
                f"from {client} key_prefix={_prefix_of(key)}\n"
            )
            await self.app(scope, receive, send)
            return

        status, msg = denied
        # Count denied requests against the shared per-key budget so brute
        # force is throttled. Lazy import to avoid cycle.
        from tts_erp_v2.middleware import rate_limit

        bucket_id = _sha256(key) if key else f"ip:{client}"
        retry_after = rate_limit.shared_hit(bucket_id)
        if retry_after is not None:
            sys.stderr.write(
                f"[rate-limit] 429 (auth-denied) bucket={bucket_id[:16]} "
                f"path={path} retry_after={retry_after}s\n"
            )
            s, hdrs, body = rate_limit.too_many_response(
                rate_limit.shared_counter().limit, retry_after
            )
            await send({"type": "http.response.start", "status": s, "headers": hdrs})
            await send({"type": "http.response.body", "body": body})
            return
        sys.stderr.write(
            f"[auth] denied {status} {method} {path} from {client} key_prefix={_prefix_of(key)}\n"
        )
        s, hdrs, body = _deny_response(status, msg)
        await send({"type": "http.response.start", "status": s, "headers": hdrs})
        await send({"type": "http.response.body", "body": body})
