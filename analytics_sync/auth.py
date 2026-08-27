"""Sync auth middleware for analytics_sync.

Uses tts-erp's `api_keys` table for Bearer token validation (no separate
analytics_sync_tokens table anymore). Imports the canonical lookup
function from `tdd.auth` so auth semantics stay in sync with the rest
of tts-erp.

Per-seller scope check is done at the handler level via
`request.scope["api_key_scopes"]` (populated here from the api_keys row).

Middleware order with tts-erp:
    CORS → Auth → RateLimit → endpoint
For analytics_sync standalone (port 9878) the same order applies; we
add CORS + Auth + RateLimit here using tts-erp's helpers.
"""
from __future__ import annotations

import hashlib
import os
import sys
from pathlib import Path

# Make `tdd.auth` importable (sibling package in tts-erp).
_TDS_ERP_ROOT = Path(__file__).resolve().parent.parent
if str(_TDS_ERP_ROOT) not in sys.path:
    sys.path.insert(0, str(_TDS_ERP_ROOT))

from tdd.auth import clear_cache, lookup_role  # noqa: E402


def clear_buckets():
    """Test helper: no-op for analytics_sync's auth cache (it lives in
    tdd.auth). Provided here for test imports."""
    clear_cache()

CACHE_TTL = 60.0
EXEMPT_PATHS = {"/healthz", "/endpoints", "/openapi.json", "/docs", "/redoc"}
ANALYTICS_SYNC_PATHS = ("/v1/analytics/sync/",)
ROLE_LEVEL = {"readonly": 1, "readwrite": 2, "admin": 3}


def _sha256(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()


def scope_grants(scopes, *, seller_id, advertiser_id):
    """Return True iff the token's scopes cover the requested scope.

    Delegates to analytics_sync.app.scope_grants (single implementation).
    Kept here for backward-compat with tests that import it directly.

    Semantics (W4.3): empty/'*' = unrestricted; multiple entries in one
    dimension are OR'd; unknown prefixes fail closed.
    """
    from .app import scope_grants as _impl

    return _impl(scopes, seller_id=seller_id, advertiser_id=advertiser_id)


def _extract_token(scope: dict) -> str | None:
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
        elif k == "x-sync-token" and v.strip():
            # Backward-compat alias for older Chrome extension builds.
            return v.strip()
    return None


def _prefix_of(key: str | None) -> str:
    return key[:16] if key else "-"


def _deny_response(status: int, msg: str):
    """Build an ASGI 401/403 response."""
    import json as _json

    body = _json.dumps({"detail": msg}).encode()
    headers = [(b"content-type", b"application/json")]
    if status == 401:
        headers.append((b"www-authenticate", b"Bearer"))
    return status, headers, body


def required_role(method: str, path: str) -> int | None:
    """All /v1/analytics/sync/* paths require readwrite (writes records)."""
    if path in EXEMPT_PATHS:
        return None
    if path.startswith(ANALYTICS_SYNC_PATHS):
        return ROLE_LEVEL["readwrite"]
    return ROLE_LEVEL["admin"]


class SyncAuthMiddleware:
    """ASGI middleware. Validates Bearer token against api_keys table.

    Sets scope["api_key_hash"], scope["api_key_role"], scope["api_key_scopes"]
    for downstream handlers (rate limiter + scope check).
    """

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

        needed = required_role(scope.get("method", "GET"), path)
        if needed is None:
            await self.app(scope, receive, send)
            return

        token = _extract_token(scope)
        result = lookup_role(token) if token else None
        level = result[0] if result else None
        scopes = result[1] if result else ()

        denied: tuple[int, str] | None = None
        if token is None:
            denied = (
                401,
                "missing bearer token (Authorization: Bearer <key>)",
            )
        elif level is None:
            denied = (401, "invalid, disabled or expired api key")
        elif level < needed:
            denied = (403, f"requires {('readwrite' if needed == 2 else 'admin')}")

        scope["api_key_hash"] = _sha256(token) if token else None
        scope["api_key_role"] = (
            {1: "readonly", 2: "readwrite", 3: "admin"}.get(level) if level else None
        )
        scope["api_key_scopes"] = scopes

        if denied is None:
            await self.app(scope, receive, send)
            return

        client = (scope.get("client") or ("?",))[0]
        method = scope.get("method", "?")
        if mode == "shadow":
            sys.stderr.write(
                f"[sync-auth-shadow] would-deny {denied[0]} {method} {path} "
                f"from {client} prefix={_prefix_of(token)}\n"
            )
            await self.app(scope, receive, send)
            return

        sys.stderr.write(
            f"[sync-auth] denied {denied[0]} {method} {path} from {client} "
            f"prefix={_prefix_of(token)}\n"
        )
        s, hdrs, body = _deny_response(*denied)
        await send({"type": "http.response.start", "status": s, "headers": hdrs})
        await send({"type": "http.response.body", "body": body})
