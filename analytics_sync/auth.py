"""Sync-token auth middleware for analytics_sync.

Pattern modeled on ../tdd/auth.py but specific to this service:

- Authorization: Bearer <token> (or X-Sync-Token)
- DB stores only SHA-256 hash + 16-char prefix (analytics_sync_tokens).
- ANALYTICS_SYNC_AUTH_MODE: off | shadow | enforce (per-request read).
- In-process cache (TTL 60s) — revocation takes effect within one TTL.
- Plain ASGI middleware, NOT BaseHTTPMiddleware, so scope["sync_token_hash"]
  is propagated to other middlewares (audit logger).
"""
from __future__ import annotations

import hashlib
import hmac
import os
import sys
import time
from datetime import datetime, timezone

import psycopg

CACHE_TTL = 60.0
_cache: dict[str, tuple[bool, float]] = {}  # hash -> (is_valid, monotonic deadline)

EXEMPT_PATHS = {
    "/healthz",
    "/endpoints",
    "/openapi.json",
    "/docs",
    "/redoc",
}


def clear_cache() -> None:
    _cache.clear()


def _sha256(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()


def _connect():
    url = os.environ.get("TTS_ERP_DB_URL") or os.environ.get("ANALYTICS_SYNC_DB_URL")
    if not url:
        raise RuntimeError(
            "TTS_ERP_DB_URL (or ANALYTICS_SYNC_DB_URL) not configured; "
            "set it in .env"
        )
    return psycopg.connect(url)


def _db_lookup(token_hash: str) -> bool:
    """Return True iff this token is currently usable.

    'enabled = true' AND (expires_at IS NULL OR expires_at > now()).
    """
    with _connect() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT enabled, expires_at, key_hash
            FROM analytics_sync_tokens
            WHERE key_hash = %s
            """,
            (token_hash,),
        )
        row = cur.fetchone()
        if not row:
            return False
        enabled, expires_at, stored_hash = row
        if not hmac.compare_digest(stored_hash, token_hash):
            return False
        if not enabled:
            return False
        if expires_at is not None and expires_at <= datetime.now(timezone.utc):
            return False
        # Touch last_used_at (best-effort; lazy update like tts-erp).
        cur.execute(
            "UPDATE analytics_sync_tokens SET last_used_at = now() WHERE key_hash = %s",
            (token_hash,),
        )
        conn.commit()
    return True


def lookup_token(token: str) -> bool:
    """Cached check. Cache TTL is 60s; revocation propagates within that window."""
    token_hash = _sha256(token)
    now = time.monotonic()
    hit = _cache.get(token_hash)
    if hit and hit[1] > now:
        return hit[0]
    valid = _db_lookup(token_hash)
    if valid:
        _cache[token_hash] = (True, now + CACHE_TTL)
    return valid


def _extract_token(scope: dict) -> str | None:
    headers = scope.get("headers") or []
    for hk, hv in headers:
        k = hk.decode("latin-1").lower()
        v = hv.decode("latin-1")
        if k == "authorization":
            scheme, _, token = v.partition(" ")
            if scheme.lower() == "bearer" and token.strip():
                return token.strip()
        elif k == "x-sync-token" and v.strip():
            return v.strip()
    return None


def _prefix_of(token: str | None) -> str:
    return token[:16] if token else "-"


def _deny_response(status: int, msg: str):
    """Build an ASGI 401/403 JSON response. No echoing of the token."""
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

        mode = os.environ.get("ANALYTICS_SYNC_AUTH_MODE", "enforce")
        if mode == "off":
            await self.app(scope, receive, send)
            return

        path = scope["path"]
        if path in EXEMPT_PATHS:
            await self.app(scope, receive, send)
            return

        token = _extract_token(scope)
        is_valid = lookup_token(token) if token else False

        scope["sync_token_hash"] = _sha256(token) if token else None
        scope["sync_token_prefix"] = _prefix_of(token)

        if token is None:
            denied: tuple[int, str] = (
                401,
                "missing bearer token (Authorization: Bearer <token> or X-Sync-Token: <token>)",
            )
        elif not is_valid:
            denied = (401, "invalid, disabled or expired sync token")
        else:
            denied = None

        if denied is None:
            await self.app(scope, receive, send)
            return

        client = (scope.get("client") or ("?",))[0]
        method = scope.get("method", "?")
        if mode == "shadow":
            sys.stderr.write(
                f"[analytics-auth-shadow] would-deny {denied[0]} {method} {path} "
                f"from {client} prefix={_prefix_of(token)}\n"
            )
            await self.app(scope, receive, send)
            return

        sys.stderr.write(
            f"[analytics-auth] denied {denied[0]} {method} {path} from {client} "
            f"prefix={_prefix_of(token)}\n"
        )
        s, hdrs, body = _deny_response(*denied)
        await send({"type": "http.response.start", "status": s, "headers": hdrs})
        await send({"type": "http.response.body", "body": body})
