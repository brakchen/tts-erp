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
    # Browser login flow — these routes self-validate the API key (POST
    # /login) or the session cookie (/me) without requiring a bearer.
    # See tech-doc/browser-login-design.md §3.
    "/v2/auth/login",
    "/v2/auth/logout",
    "/v2/auth/me",
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
# Exact-match paths (no trailing slash) that are readonly. These don't
# fit the prefix pattern above (which requires ``/v2/xxx/`` with slash).
# Keep this list small — prefer adding a new prefix when adding a
# sub-namespace.
_READONLY_EXACT = {
    "/v2/llm-context",  # GET — self-describing system + data dictionary for LLM agents
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
    # v2: exact-match readonly paths (e.g. /v2/llm-context).
    if p in _READONLY_EXACT:
        return ROLE_LEVEL["readonly"]
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


def lookup_role_by_hash(key_hash: str) -> tuple[int | None, tuple[str, ...]] | None:
    """Cached role+scopes lookup keyed by the API key's sha256 hash.

    The browser login flow (tech-doc/browser-login-design.md §5) stores
    only the key hash inside the session cookie, so it needs to re-check
    the hash against ``security.api_keys`` per request. Sharing the same
    cache + DB path as ``lookup_role`` means a freshly-revoked key kills
    its sessions within one cache TTL (60 s positive / 20 s negative).

    DB errors propagate (the middleware maps them to 503).

    Returns:
        ``(role_level, scopes)`` for a valid, active key; ``None`` for
        any other case (no such key / disabled / expired / unknown
        role). Negative cache hits return ``None`` — NOT ``(None, ())`` —
        so callers can use a single ``is None`` check to mean "the key
        is no good" instead of remembering to look at ``level``.
    """
    now = time.monotonic()
    hit = _cache.get(key_hash)
    if hit and hit[2] > now:
        if hit[0] is None:
            # Negative cache hit — propagate as None so ``is None`` is
            # the single uniform "key invalid" signal for every caller
            # (middleware + login handler).
            return None
        return hit[0], hit[1]
    result = _db_lookup(key_hash)
    if result is not None:
        _cache[key_hash] = (result[0], result[1], now + CACHE_TTL)
    else:
        _cache[key_hash] = (None, (), now + NEG_CACHE_TTL)
    if result is None:
        return None
    return result


def lookup_role(key: str) -> tuple[int | None, tuple[str, ...]] | None:
    """Cached role+scopes lookup by plaintext API key.

    Thin wrapper over :func:`lookup_role_by_hash` — kept for callers
    that still hold the plaintext (login POST handler, scripts).
    """
    return lookup_role_by_hash(_sha256(key))


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


def _extract_cookie(scope: dict, name: str) -> str | None:
    """Return the value of cookie ``name`` from a request scope, or None.

    ASGI's ``scope["headers"]`` arrives as a list of raw
    ``(bytes, bytes)`` pairs; the ``Cookie`` header is a single string
    with ``name=value; name=value`` segments. We only need one cookie
    per request (the session cookie), so a linear scan is fine.
    """
    target = name.encode("latin-1")
    for hk, hv in scope.get("headers") or []:
        if hk.lower() != b"cookie":
            continue
        raw = hv.decode("latin-1")
        for segment in raw.split(";"):
            seg = segment.strip()
            if not seg:
                continue
            eq = seg.find("=")
            if eq <= 0:
                continue
            k, _, v = seg.partition("=")
            if k.encode("latin-1") == target:
                return v.strip()
    return None


def _accept_text_html(scope: dict) -> bool:
    """True when the request looks like a browser navigation.

    Browser-initiated GETs always send ``Accept: text/html, ...`` even
    when the previous page was an SPA, because the navigation is a
    document request. Programmatic clients (curl, fetch from JS that
    is going to JSON-render the response) send ``Accept: */*`` or
    ``Accept: application/json``. Used to decide between a 302 to the
    login page and the existing JSON 401. See design doc §5 point 3.
    """
    for hk, hv in scope.get("headers") or []:
        if hk.lower() == b"accept":
            raw = hv.decode("latin-1").lower()
            if "text/html" in raw:
                return True
    return False


def _prefix_of(key: str | None) -> str:
    return key[:16] if key else "-"


def _deny_response(
    status: int, msg: str
) -> tuple[int, list[tuple[bytes, bytes]], bytes]:
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

        # --- Cookie-first auth (browser login flow). ---
        # The login page mints a stateless session cookie carrying only
        # the API key's sha256 hash; we re-check the hash against
        # security.api_keys per request, so a revoked key kills its
        # sessions within one cache TTL. Cookie auth wins over header
        # auth so a browser with a valid session isn't double-charged
        # by the rate limiter. See tech-doc/browser-login-design.md §5.
        from tts_erp_v2.middleware import session_auth

        cookie_raw = _extract_cookie(scope, session_auth.SESSION_COOKIE_NAME)
        cookie_info = (
            session_auth.verify_session_cookie(cookie_raw) if cookie_raw else None
        )
        # auth_state: 'none' → no credential presented at all;
        # 'bearer' → header key validated against DB (result filled);
        # 'cookie' → cookie key_hash validated against DB (result filled).
        # 'invalid' → a credential was presented but the DB rejected it
        # (revoked/disabled/expired). Filled below.
        auth_state = "none"
        key: str | None = None
        result: tuple[int | None, tuple[str, ...]] | None = None
        if cookie_info is not None:
            kh = cookie_info["kh"]
            try:
                cookie_result = await run_sync(lookup_role_by_hash, kh)
            except Exception as exc:
                sys.stderr.write(
                    f"[auth] cookie session lookup failed ({type(exc).__name__}): {exc}\n"
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
            if cookie_result is not None:
                result = cookie_result
                auth_state = "cookie"
            else:
                # Cookie signature + exp were valid, but the underlying
                # key no longer lives in security.api_keys (revoked /
                # disabled / expired). The credential is bad; fall back
                # to header auth in case the caller also has a bearer,
                # but ultimately mark invalid below.
                auth_state = "invalid"

        if auth_state != "cookie":
            key = _extract_key(scope)
            if key:
                try:
                    header_result = await run_sync(lookup_role, key)
                except Exception as exc:
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
                if header_result is not None:
                    result = header_result
                    auth_state = "bearer"
                elif auth_state == "none":
                    # Header credential was presented and DB rejected it.
                    auth_state = "invalid"
            # key may still be None if the request had no Authorization
            # header — that's the common case for browser-cookie callers
            # and is handled by the auth_state-based deny logic below.

        level = result[0] if result else None
        scopes = result[1] if result else ()

        denied: tuple[int, str] | None = None
        if auth_state == "none":
            denied = (
                401,
                "missing bearer token (Authorization: Bearer <key> or X-API-Key: <key>)",
            )
        elif auth_state == "invalid":
            denied = (401, "invalid, disabled or expired api key")
        elif level is not None and level < needed:
            denied = (403, f"requires {_LEVEL_NAME[needed]}")

        # Populate scope for downstream middlewares (rate limiter reads api_key_hash).
        if auth_state == "cookie":
            scope["api_key_hash"] = cookie_info["kh"] if cookie_info else None
            scope["auth_method"] = "cookie"
        elif auth_state == "bearer":
            scope["api_key_hash"] = _sha256(key) if key else None
            scope["auth_method"] = "bearer"
        else:
            scope["api_key_hash"] = None
            scope["auth_method"] = None
        scope["api_key_role"] = _LEVEL_NAME.get(level) if level is not None else None
        scope["api_key_scopes"] = scopes

        if denied is None:
            await self.app(scope, receive, send)
            return

        # --- Browser 302 redirect to the login page. ---
        # A browser GET carries `Accept: text/html, ...`; an API client
        # carries `Accept: */*` or `application/json`. The browser path
        # is JSON-friendly (curl, fetch) for backwards compat; the
        # browser path gets a 302 so the operator sees the login form,
        # not a wall of JSON. TTS_ERP_EXTERNAL_PREFIX re-prepends the
        # NAT proxy's /tts/... prefix inside the `next` value so the
        # SPA reloads against the same public URL it started on.
        if (
            denied[0] == 401
            and method == "GET"
            and _accept_text_html(scope)
        ):
            qs = scope.get("query_string", b"").decode("latin-1")
            next_value = path + (("?" + qs) if qs else "")
            prefix = os.environ.get("TTS_ERP_EXTERNAL_PREFIX", "")
            location = f"/v2/auth/login?next={prefix}{next_value}"
            await send(
                {
                    "type": "http.response.start",
                    "status": 302,
                    "headers": [
                        (b"location", location.encode("latin-1")),
                        (b"content-type", b"text/plain; charset=utf-8"),
                    ],
                }
            )
            await send({"type": "http.response.body", "body": b""})
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

        bucket_id = _sha256(key) if key else (
            cookie_info["kh"] if cookie_info else f"ip:{client}"
        )
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
