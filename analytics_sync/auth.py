"""Sync-token auth + scope validation for analytics_sync.

Pattern modeled on ../tdd/auth.py but specific to this service:

- Authorization: Bearer <token> (or X-Sync-Token)
- DB stores only SHA-256 hash + 16-char prefix (analytics_sync_tokens).
- ANALYTICS_SYNC_AUTH_MODE: off | shadow | enforce (per-request read).
- In-process cache (TTL 60s) — revocation propagates within one TTL.
- Plain ASGI middleware, NOT BaseHTTPMiddleware, so scope["sync_token_hash"]
  and scope["sync_token_scopes"] are propagated to other middlewares and
  the rate limiter.

Scope validation
================

Token has `scopes TEXT[]`. Each entry is one of:

- ``seller:<seller_id>``     — grant access to this seller_id
- ``advertiser:<advertiser_id>`` — grant access to this advertiser_id
- ``*``                      — wildcard (full access)

Empty scopes array means unrestricted (operator default). The handler
queries ``scope["sync_token_scopes"]`` and rejects mismatched scope
references with 403.

Audit logging never includes the token itself, the scopes' exact text,
or any client IP beyond a coarse prefix.
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
# Cache: hash -> (is_valid, scopes_list, monotonic_deadline)
_cache: dict[str, tuple[bool, tuple[str, ...], float]] = {}

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


def _db_lookup(token_hash: str) -> tuple[bool, tuple[str, ...]]:
    """Return (is_valid, scopes_tuple).

    `is_valid` is True iff the token is enabled and unexpired.
    `scopes_tuple` is the literal scopes[] entry list (may be empty).
    """
    with _connect() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT enabled, expires_at, key_hash, scopes
            FROM analytics_sync_tokens
            WHERE key_hash = %s
            """,
            (token_hash,),
        )
        row = cur.fetchone()
        if not row:
            return False, ()
        enabled, expires_at, stored_hash, scopes = row
        if not hmac.compare_digest(stored_hash, token_hash):
            return False, ()
        if not enabled:
            return False, ()
        if expires_at is not None and expires_at <= datetime.now(timezone.utc):
            return False, ()
        # Touch last_used_at (best-effort; lazy update like tts-erp).
        cur.execute(
            "UPDATE analytics_sync_tokens SET last_used_at = now() WHERE key_hash = %s",
            (token_hash,),
        )
        conn.commit()
    return True, tuple(scopes or ())


def lookup_token(token: str) -> tuple[bool, tuple[str, ...]]:
    """Cached check. Cache TTL is 60s; revocation propagates within that window."""
    token_hash = _sha256(token)
    now = time.monotonic()
    hit = _cache.get(token_hash)
    if hit and hit[2] > now:
        return hit[0], hit[1]
    valid, scopes = _db_lookup(token_hash)
    if valid:
        _cache[token_hash] = (valid, scopes, now + CACHE_TTL)
    return valid, scopes


def scope_grants(scopes: tuple[str, ...], *, seller_id: str | None, advertiser_id: str | None) -> bool:
    """Return True iff the token's scopes cover the requested scope.

    Semantics: each scope entry is an independent constraint on one
    dimension. All constraints must be satisfied. Dimensions the token
    does not mention are unrestricted.

    - Empty scopes array means unrestricted (operator default).
    - The wildcard `*` also grants unrestricted access.
    - Otherwise, every scope entry must match the request's
      corresponding dimension.

    Examples:
      scopes=[]                                      -> always True
      scopes=["*"]                                   -> always True
      scopes=["seller:abc"]                          -> seller_id == "abc" (advertiser unrestricted)
      scopes=["seller:abc", "advertiser:adv-1"]      -> seller_id == "abc" AND advertiser_id == "adv-1"
    """
    if not scopes:
        return True
    if "*" in scopes:
        return True

    for s in scopes:
        if s.startswith("seller:"):
            target = s[len("seller:"):]
            if seller_id != target:
                return False
        elif s.startswith("advertiser:"):
            target = s[len("advertiser:"):]
            if advertiser_id != target:
                return False
        # Unknown scope format: ignored for forward compatibility.

    return True


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
    """Build an ASGI 401/403 JSON response. No echoing of the token
    or scopes."""
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
        is_valid, scopes = (False, ())
        if token:
            is_valid, scopes = lookup_token(token)

        scope["sync_token_hash"] = _sha256(token) if token else None
        scope["sync_token_prefix"] = _prefix_of(token)
        scope["sync_token_scopes"] = scopes

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
