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

DEFAULT_NEXT = "/v2/pages/dashboard"
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
    # Scope the cookie to the NGINX prefix (e.g. /tts) so it is never
    # sent to other paths on the same domain (e.g. /spu-roi).
    cookie_path = os.environ.get("TTS_ERP_EXTERNAL_PREFIX", "/") or "/"
    resp.set_cookie(
        key=session_auth.SESSION_COOKIE_NAME,
        value=cookie,
        max_age=session_auth.session_ttl_seconds(),
        httponly=True,
        secure=session_auth.session_secure_flag(),
        samesite="lax",
        path=cookie_path,
    )
    return resp


@router.post("/logout")
def logout() -> Response:
    """Clear the session cookie (idempotent, public)."""
    resp = Response(status_code=status.HTTP_204_NO_CONTENT)
    resp.delete_cookie(
        session_auth.SESSION_COOKIE_NAME,
        path=os.environ.get("TTS_ERP_EXTERNAL_PREFIX", "/") or "/",
    )
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
<html lang="zh-Hans">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>登录 · tts-erp</title>
  <link rel="stylesheet" href="../../static/vendor/bootstrap.min.css">
  <style>
    /* ---------- tokens (shared with dashboard) ---------- */
    :root {
      --paper: #F4EFE4;
      --paper-deep: #EAE3D2;
      --ink: #1B1814;
      --ink-soft: #4A4239;
      --rule: #C9BFA8;
      --rule-soft: #DDD4BF;
      --accent: #B8390E;
      --accent-deep: #8F2C09;
      --muted: #6E6657;
      --danger: #8C1A1A;
      --ok: #2F6B3E;
      --mono: ui-monospace, 'JetBrains Mono', 'SF Mono', 'Cascadia Mono', Consolas, 'Liberation Mono', monospace;
      --sans: ui-sans-serif, system-ui, -apple-system, 'Segoe UI', 'PingFang SC', 'Hiragino Sans GB', 'Microsoft YaHei', 'Noto Sans CJK SC', sans-serif;
      --serif: ui-serif, 'Iowan Old Style', 'Apple Garamond', 'Source Han Serif SC', 'Noto Serif CJK SC', serif;
    }
    * { box-sizing: border-box; }
    html, body {
      background: var(--paper);
      color: var(--ink);
      font-family: var(--sans);
      font-size: 14px;
      line-height: 1.4;
      margin: 0;
      min-height: 100vh;
      display: flex;
      align-items: center;
      justify-content: center;
      -webkit-font-smoothing: antialiased;
    }
    a { color: var(--accent); text-decoration: none; }
    a:hover { color: var(--accent-deep); }

    /* ---------- LOGIN CARD ---------- */
    .login-card {
      background: var(--paper);
      border: 1px solid var(--rule);
      padding: 32px 36px;
      width: 380px;
      max-width: 90vw;
    }
    .login-header {
      margin-bottom: 24px;
      padding-bottom: 20px;
      border-bottom: 1px solid var(--rule-soft);
    }
    .login-eyebrow {
      font-family: var(--mono);
      font-size: 11px;
      letter-spacing: 0.14em;
      text-transform: uppercase;
      color: var(--muted);
      margin-bottom: 4px;
    }
    .login-title {
      font-family: var(--serif);
      font-weight: 600;
      font-size: 22px;
      margin: 0;
      letter-spacing: -0.01em;
    }
    .login-hint {
      font-size: 13px;
      color: var(--muted);
      margin: 8px 0 0;
    }

    /* ---------- FORM ---------- */
    .form-group {
      margin-bottom: 16px;
    }
    .form-label {
      display: block;
      font-family: var(--mono);
      font-size: 11px;
      letter-spacing: 0.1em;
      text-transform: uppercase;
      color: var(--muted);
      margin-bottom: 6px;
    }
    .form-input {
      width: 100%;
      font-family: var(--mono);
      font-size: 14px;
      padding: 10px 12px;
      border: 1px solid var(--rule);
      background: var(--paper-deep);
      color: var(--ink);
      border-radius: 0;
    }
    .form-input:focus {
      outline: 0;
      border-color: var(--accent);
      box-shadow: 0 0 0 1px var(--accent);
    }
    .form-input::placeholder {
      color: var(--rule);
    }

    /* ---------- BUTTON ---------- */
    .btn-login {
      width: 100%;
      font-family: var(--mono);
      font-size: 12px;
      font-weight: 600;
      letter-spacing: 0.14em;
      text-transform: uppercase;
      padding: 12px 16px;
      background: var(--ink);
      color: var(--paper);
      border: 0;
      cursor: pointer;
      border-radius: 0;
      transition: background 120ms ease;
    }
    .btn-login:hover {
      background: var(--accent);
    }
    .btn-login:disabled {
      background: var(--rule);
      color: var(--paper);
      cursor: wait;
    }
    .btn-login:focus-visible {
      outline: 2px solid var(--accent);
      outline-offset: 2px;
    }

    /* ---------- ERROR ---------- */
    .form-error {
      font-size: 13px;
      color: var(--danger);
      margin: 12px 0 0;
      min-height: 1.5em;
    }

    /* ---------- FOOTER ---------- */
    .login-footer {
      margin-top: 24px;
      padding-top: 16px;
      border-top: 1px solid var(--rule-soft);
      font-family: var(--mono);
      font-size: 11px;
      color: var(--muted);
      text-align: center;
    }
  </style>
</head>
<body>
  <div class="login-card">
    <div class="login-header">
      <div class="login-eyebrow">TikTok Shop · Operations</div>
      <h1 class="login-title">运营控制台</h1>
      <p class="login-hint">输入 API Key 登录以访问系统</p>
    </div>

    <form id="login-form">
      <div class="form-group">
        <label class="form-label" for="key">API Key</label>
        <input type="password" id="key" class="form-input" placeholder="输入您的 API Key" autocomplete="current-password" required>
      </div>
      <input type="hidden" id="next" value="__NEXT__">
      <button type="submit" class="btn-login" id="btn-login">登录</button>
      <p class="form-error" id="err"></p>
    </form>

    <div class="login-footer">
      <span>tts-erp v2.0</span>
    </div>
  </div>

  <script>
    // API base: works on :9877 (no prefix) and behind the NAT /tts prefix.
    const base = location.pathname.slice(0, location.pathname.indexOf("/v2/auth/login")) || "";
    const API = base + "/v2";

    document.getElementById("login-form").addEventListener("submit", async (e) => {
      e.preventDefault();
      const key = document.getElementById("key").value.trim();
      const next = document.getElementById("next").value || "/v2/pages/dashboard";
      const err = document.getElementById("err");
      const btn = document.getElementById("btn-login");

      err.textContent = "";
      btn.disabled = true;
      btn.textContent = "登录中…";

      try {
        const r = await fetch(API + "/auth/login", {
          method: "POST",
          headers: { "Content-Type": "application/json", "X-Requested-With": "tts-erp" },
          body: JSON.stringify({ key: key, next: next }),
        });
        if (r.ok) {
          location.href = next;
          return;
        }
        if (r.status === 401) {
          err.textContent = "API Key 无效或已禁用";
          return;
        }
        if (r.status === 429) {
          err.textContent = "尝试次数过多，请稍后再试";
          return;
        }
        if (r.status === 503) {
          err.textContent = "服务未配置，请联系管理员";
          return;
        }
        const t = await r.text();
        err.textContent = "HTTP " + r.status + ": " + t;
      } catch (ex) {
        err.textContent = "网络错误: " + ex.message;
      } finally {
        btn.disabled = false;
        btn.textContent = "登录";
      }
    });
  </script>
</body>
</html>
"""
