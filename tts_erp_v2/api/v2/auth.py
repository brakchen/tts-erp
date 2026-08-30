"""/v2/auth/* — browser login flow.

The operator console pages (``/v2/pages/*``) are behind bearer auth; a
browser cannot attach a Bearer header on a plain navigation, so this
router provides the front door: it exchanges an API key for a stateless
HMAC-signed session cookie (``tts_session``) which ``AuthMiddleware``
honors on every subsequent request.

Design: tech-doc/browser-login-design.md

Routes:
- ``GET  /v2/auth/login``  — login page (HTML, public)
- ``POST /v2/auth/login``  — validate key → set cookie (public, throttled)
- ``POST /v2/auth/logout`` — clear cookie (public)
- ``GET  /v2/auth/me``     — session state for page JS (public; self-validates)

Security notes:
- The API key itself is never stored client-side — only its SHA-256 hash
  inside the signed cookie; the middleware re-checks the hash against
  ``security.api_keys`` per request, so revoking a key kills its sessions.
- Login attempts are IP-throttled (``TTS_ERP_LOGIN_RATE_LIMIT``, default
  10/min) because the endpoint is exempt from auth and the shared rate
  limiter skips anonymous requests.
- ``next`` is validated (internal absolute path only) to prevent open
  redirects.
"""

from __future__ import annotations

import html as _html
import logging
import os
import sys

from fastapi import APIRouter, Request, Response, status
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, Field

from tts_erp_v2.middleware import session_auth
from tts_erp_v2.middleware.access_log import _key_prefix
from tts_erp_v2.middleware.auth import ROLE_LEVEL, lookup_role, lookup_role_by_hash

router = APIRouter(prefix="/v2/auth", tags=["auth"])

# Dedicated logger for login-flow business events. Operators grep this
# to answer "what key did the user just try?" without scraping the
# access log (which has the key hash for SUCCESSFUL logins only —
# the request body never reaches the access log, so failed-attempt
# key correlation is impossible there).
login_logger = logging.getLogger("tts_erp_v2.auth.login")

# Same uvicorn-root-has-no-handler problem documented in
# tts_erp_v2/middleware/access_log.py. Attach a stdout handler
# explicitly so the structured event reaches logs/stdout.log
# (via systemd's StandardOutput=append:). ``propagate=False`` keeps
# the same line from also being emitted to stderr through the
# lastResort handler. setLevel(INFO) for the same reason: without
# it the default WARNING drops info-level records before the
# handler runs.
login_logger.setLevel(logging.INFO)
if not any(
    isinstance(h, logging.StreamHandler) and h.stream is sys.stdout
    for h in login_logger.handlers
):
    _stdout = logging.StreamHandler(sys.stdout)
    _stdout.setFormatter(logging.Formatter("%(message)s"))
    login_logger.addHandler(_stdout)
    login_logger.propagate = False

DEFAULT_NEXT = "/v2/pages/manual-costs"
_LEVEL_TO_NAME = {v: k for k, v in ROLE_LEVEL.items()}


class LoginBody(BaseModel):
    key: str = Field(min_length=1, max_length=512)
    next: str | None = None


def _valid_next(raw: str | None) -> str:
    """Open-redirect guard: only same-origin absolute paths are allowed."""
    if not raw:
        return DEFAULT_NEXT
    if raw.startswith("/") and not raw.startswith("//") and "\\" not in raw:
        return raw
    return DEFAULT_NEXT


def _client_bucket(request: Request) -> str:
    """Throttle bucket for one login client.

    Uses the direct peer IP (the NAT proxy in production). X-Forwarded-For
    is deliberately NOT trusted — it is client-spoofable, while a shared
    proxy bucket is acceptable for a single-operator tool.
    """
    return request.client.host if request.client else "?"


@router.get("/login", response_class=HTMLResponse)
def login_page(request: Request) -> HTMLResponse:
    """Render the login form (public). Injects the validated ``next``
    (prepended with the NGINX prefix so the post-login redirect lands
    on a path NGINX actually serves)."""
    raw_next = _valid_next(request.query_params.get("next"))
    prefix = os.environ.get("TTS_ERP_EXTERNAL_PREFIX", "")
    next_url = f"{prefix}{raw_next}" if prefix else raw_next
    return HTMLResponse(_LOGIN_HTML.replace("__NEXT__", _html.escape(next_url)))


@router.post("/login")
def login(body: LoginBody, request: Request) -> Response:
    """Validate an API key and mint a session cookie."""
    key_prefix = _key_prefix(body.key)
    retry_after = session_auth.login_throttle_hit(_client_bucket(request))
    if retry_after is not None:
        login_logger.info(
            "result=throttled key=%s retry_after=%d",
            key_prefix,
            retry_after,
        )
        return JSONResponse(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            content={
                "detail": "too many login attempts",
                "retry_after_s": retry_after,
            },
        )
    if not session_auth.session_secret_configured():
        login_logger.warning("result=secret_not_configured")
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={"detail": "TTS_ERP_SESSION_SECRET not configured"},
        )
    try:
        result = lookup_role(body.key)
    except Exception as exc:  # noqa: BLE001 — auth store unreachable → fail closed
        # Auth store unreachable — mirror the middleware's fail-closed 503.
        login_logger.warning(
            "result=store_unavailable key=%s error=%s",
            key_prefix,
            type(exc).__name__,
        )
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={"detail": f"auth store unavailable: {type(exc).__name__}"},
        )
    if result is None:
        # The most common failure mode in production: user pastes a
        # stale or revoked key. The access log has the real client IP
        # + status; this event is the only place the ATTEMPTED key
        # shows up. Pair these two for a complete picture.
        login_logger.info("result=invalid key=%s", key_prefix)
        return JSONResponse(
            status_code=status.HTTP_401_UNAUTHORIZED,
            content={"detail": "invalid, disabled or expired api key"},
        )
    level, _scopes = result
    role = _LEVEL_TO_NAME.get(level or 0, "readonly")
    cookie = session_auth.mint_session_cookie(body.key, role)
    resp = JSONResponse(content={"ok": True, "role": role})
    resp.set_cookie(
        key=session_auth.SESSION_COOKIE_NAME,
        value=cookie,
        max_age=session_auth.session_ttl_seconds(),
        httponly=True,
        secure=session_auth.session_secure_flag(),
        samesite="lax",
        path="/",
    )
    return resp


@router.post("/logout")
def logout() -> Response:
    """Clear the session cookie (idempotent, public)."""
    resp = Response(status_code=status.HTTP_204_NO_CONTENT)
    resp.delete_cookie(session_auth.SESSION_COOKIE_NAME, path="/")
    return resp


@router.get("/me")
def me(request: Request) -> dict:
    """Return session state for page JS (public; self-validates the cookie).

    The DB is re-checked so a revoked key reports ``authenticated: false``
    (within the auth cache TTL, same as every other request).
    """
    raw = request.cookies.get(session_auth.SESSION_COOKIE_NAME)
    if not raw:
        return {"authenticated": False}
    info = session_auth.verify_session_cookie(raw)
    if info is None:
        return {"authenticated": False}
    try:
        result = lookup_role_by_hash(info["kh"])
    except Exception:  # noqa: BLE001 — auth store unreachable; report unauthenticated
        result = None
    if result is None:
        return {"authenticated": False}
    return {"authenticated": True, "role": info["role"]}


_LOGIN_HTML = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>tts-erp · sign in</title>
<style>
  body { font-family: -apple-system, system-ui, sans-serif; background: #f6f8fa; margin: 0; min-height: 100vh; display: flex; align-items: center; justify-content: center; color: #1f2328; }
  .card { background: #fff; border: 1px solid #d0d7de; border-radius: 8px; padding: 28px 32px; width: 340px; box-shadow: 0 1px 3px rgba(27,31,36,.12); }
  h1 { font-size: 18px; margin: 0 0 4px; }
  .hint { font-size: 13px; color: #57606a; margin: 0 0 18px; }
  input[type=password] { width: 100%; box-sizing: border-box; font: inherit; padding: 8px 10px; border: 1px solid #d0d7de; border-radius: 6px; margin-bottom: 12px; }
  button { width: 100%; font: inherit; font-weight: 600; padding: 8px 12px; border: 1px solid #1f883d; border-radius: 6px; background: #1f883d; color: #fff; cursor: pointer; }
  button:hover { background: #1a7f37; }
  .err { font-size: 13px; color: #cf222e; margin: 12px 0 0; min-height: 1em; }
</style>
</head>
<body>
<div class="card">
  <h1>tts-erp sign in</h1>
  <p class="hint">Enter an operator API key to open the console.</p>
  <form id="login-form">
    <input type="password" id="key" placeholder="operator API key" autocomplete="current-password" required>
    <input type="hidden" id="next" value="__NEXT__">
    <button type="submit">sign in</button>
    <p class="err" id="err"></p>
  </form>
</div>
<script>
// API base: works on :9877 (no prefix) and behind the NAT /tts prefix.
const base = location.pathname.slice(0, location.pathname.indexOf("/v2/auth/login")) || "";
const API = base + "/v2";

document.getElementById("login-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const key = document.getElementById("key").value.trim();
  const next = document.getElementById("next").value || "/v2/pages/manual-costs";
  const err = document.getElementById("err");
  err.textContent = "";
  try {
    const r = await fetch(API + "/auth/login", {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-Requested-With": "tts-erp" },
      body: JSON.stringify({ key: key, next: next }),
    });
    if (r.ok) { location.href = next; return; }
    if (r.status === 401) { err.textContent = "invalid, disabled or expired api key"; return; }
    if (r.status === 429) { err.textContent = "too many attempts — wait a minute and retry"; return; }
    if (r.status === 503) { err.textContent = "login is not configured on the server"; return; }
    const t = await r.text();
    err.textContent = "HTTP " + r.status + ": " + t;
  } catch (ex) {
    err.textContent = "network error: " + ex.message;
  }
});
</script>
</body>
</html>
"""
