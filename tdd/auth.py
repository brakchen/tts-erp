"""API key auth middleware for tts-erp.

Design: ../tech-doc/api-key-auth-design.md

- Authorization: Bearer <key> (or X-API-Key) ; DB stores only SHA-256
  hashes (table api_keys).
- Roles: readonly < readwrite < admin; path rules via required_role().
- TTS_ERP_AUTH_MODE: off | shadow | enforce (read per request).
- In-process cache (TTL 60s) — revocation takes effect within one TTL.
  Invalid keys are negative-cached for NEG_CACHE_TTL (20s) so brute-force
  retries don't hit PG on every request.
- Key lookup runs in a worker thread (anyio.to_thread) — sync psycopg I/O
  must not block the event loop. Lookup failure (PG down) fails closed
  with 503 in enforce mode, passes through in shadow mode.
- Denied requests (401/403) are counted into rate_limit's shared per-key
  bucket: they short-circuit before RateLimitMiddleware, so without this
  an attacker could brute-force keys unthrottled (each attempt = a new
  PG connection). Over-limit denials get 429 + Retry-After.
- Plain ASGI middleware (NOT BaseHTTPMiddleware) so scope["api_key_hash"]
  is propagated to other ASGI middlewares downstream. starlette <0.31's
  BaseHTTPMiddleware wraps requests in a fresh scope, which breaks state
  sharing across layers.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import sys
import time
from datetime import datetime, timezone

from anyio.to_thread import run_sync

import tts_erp  # db_connect (repo root is on sys.path via tts_erp_fastapi)

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
    "/ads-monitor",  # TikTok OAuth Advertiser redirect target; public
    "/callback",  # Wave 4: TikTok OAuth redirect target (protocol contract)
    "/authorize",  # Wave 4: OAuth browser-flow entrypoint (CSRF state)
}
ORDER_WRITE_VERBS = {
    "confirm",
    "cancel",
    "update_status",
    "shipping_info",
    "verify_shipping",
}
READONLY_PREFIXES = ("/db/", "/shops", "/finance/", "/logistics/")
READONLY_EXACT = {"/returns/search", "/cancellations/search"}
LAST_USED_WRITE_AFTER_S = 3600


def clear_cache() -> None:
    _cache.clear()


def required_role(method: str, path: str) -> int | None:
    if path in EXEMPT_PATHS:
        return None
    if path.startswith("/sync/"):
        return ROLE_LEVEL["readwrite"]
    # analytics_sync: Chrome extension uploads records (write) and
    # queries cursors (read). readwrite is sufficient; per-seller
    # scope is checked at the handler level via scope["api_key_scopes"].
    if path.startswith("/v1/analytics/sync/"):
        return ROLE_LEVEL["readwrite"]
    if path.startswith("/orders/"):
        if method == "POST":
            last = path.rstrip("/").rsplit("/", 1)[-1]
            if last in ORDER_WRITE_VERBS:
                return ROLE_LEVEL["readwrite"]
        return ROLE_LEVEL["readonly"]
    if path in READONLY_EXACT or path.startswith(READONLY_PREFIXES):
        return ROLE_LEVEL["readonly"]
    return ROLE_LEVEL["admin"]


def _sha256(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()


def _db_lookup(key_hash: str) -> tuple[int | None, tuple[str, ...]] | None:
    """Return (role_level, scopes_tuple) for a valid key, else None.

    role_level is int | None because ROLE_LEVEL.get() returns None for
    unknown roles — in practice the api_keys.role column has a CHECK
    constraint, so this is defensive typing.
    """
    with tts_erp.db_connect() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT role, enabled, expires_at, last_used_at, key_hash, scopes"
            " FROM api_keys WHERE key_hash = %s",
            (key_hash,),
        )
        row = cur.fetchone()
        if not row:
            return None
        role, enabled, expires_at, last_used_at, stored_hash, scopes = row
        if not hmac.compare_digest(stored_hash, key_hash):
            return None
        if not enabled:
            return None
        if expires_at is not None and expires_at <= datetime.now(timezone.utc):
            return None
        if (
            last_used_at is None
            or (datetime.now(timezone.utc) - last_used_at).total_seconds()
            > LAST_USED_WRITE_AFTER_S
        ):
            cur.execute(
                "UPDATE api_keys SET last_used_at = now() WHERE key_hash = %s",
                (key_hash,),
            )
        return ROLE_LEVEL.get(role), tuple(scopes or ())


def lookup_role(key: str) -> tuple[int | None, tuple[str, ...]] | None:
    """Cached role+scopes lookup. Valid keys cache for CACHE_TTL (60s);
    invalid keys negative-cache for NEG_CACHE_TTL (20s) so brute-force
    retries don't open a fresh PG connection per request. Returns None
    for invalid keys. DB errors propagate (middleware maps to 503)."""
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
    return result


def _extract_key(scope: dict) -> str | None:
    # headers are list[tuple[bytes, bytes]] in raw ASGI scope
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


def _deny_response(status: int, msg: str):
    """Return a 401/403 response body bytes."""
    import json as _json

    body = _json.dumps({"detail": msg}).encode()
    headers = [
        (b"content-type", b"application/json"),
    ]
    if status == 401:
        headers.append((b"www-authenticate", b"Bearer"))
    return status, headers, body


class AuthMiddleware:
    """Plain ASGI middleware. Wire with app.add_middleware(AuthMiddleware)."""

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
                # Sync psycopg I/O must not run on the event loop.
                result = await run_sync(lookup_role, key)
            except Exception as exc:
                # Auth store unreachable — fail closed (503) in enforce
                # mode; shadow mode observes only, so pass through.
                sys.stderr.write(
                    f"[auth] key lookup failed ({type(exc).__name__}): {exc}\n"
                )
                if mode == "shadow":
                    await self.app(scope, receive, send)
                    return
                s, hdrs, body = _deny_response(503, "auth store unavailable")
                await send(
                    {"type": "http.response.start", "status": s, "headers": hdrs}
                )
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

        # Populate scope for downstream middlewares (rate limiter reads api_key_hash;
        # analytics_sync handlers read api_key_scopes for per-seller checks).
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
        client = (scope.get("client") or ("?",))[0]
        # Denied requests short-circuit before RateLimitMiddleware — count
        # them here against the same shared per-key budget so brute-forcing
        # keys is throttled. Bucket by key hash, or by client IP when the
        # request carried no key at all.
        # Lazy import: analytics_sync/auth.py imports this module in a
        # context where tdd/ is not on sys.path, so a top-level
        # `import rate_limit` would break that consumer.
        import rate_limit

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
