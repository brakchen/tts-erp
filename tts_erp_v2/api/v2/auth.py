"""/v2/auth/* — browser login flow（用户名 + 密码 + 服务端会话）.

设计：tech-doc/user-account-authz-design.md

Routes:
- ``GET  /v2/auth/login``           — login page (HTML, public)
- ``POST /v2/auth/login``           — username+password → session cookie（公开，限流）
- ``POST /v2/auth/logout``          — 吊销服务端会话 + 清 cookie（公开，幂等）
- ``GET  /v2/auth/me``              — 会话状态（公开；自校验 cookie）
- ``POST /v2/auth/change-password`` — 改密（登录用户；吊销其他会话）

Security notes:
- 会话凭证 = 不透明随机 token，库 ``security.user_sessions`` 只存 sha256；
  登出/禁用/改密走 ``revoked_at`` 即时生效。
- 登录失败信息统一（防用户枚举）；用户不存在也跑一次 argon2 校验。
- 登录限流（IP 滑动窗口，``TTS_ERP_LOGIN_RATE_LIMIT``，默认 10 次/分）；
  端点免 auth，共享限流跳过匿名请求，不设防就是免费暴力破解入口。
- ``next`` 校验（仅同源绝对路径）防 open redirect。
- 同域多服务部署：cookie 专属名 tts_erp_session + Path=root_path 严格隔离（§5.2）。
"""

from __future__ import annotations

import html as _html
import logging
import sys

from fastapi import APIRouter, Request, Response, status
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, Field

from tts_erp_v2.accounts import service
from tts_erp_v2.accounts.passwords import PasswordPolicyError
from tts_erp_v2.middleware import session_auth
from tts_erp_v2.middleware.access_log import _key_prefix

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


class LoginBody(BaseModel):
  username: str = Field(min_length=1, max_length=64)
  password: str = Field(min_length=1, max_length=128)
  next: str | None = None


class ChangePasswordBody(BaseModel):
  oldPassword: str = Field(min_length=1, max_length=128)
  newPassword: str = Field(min_length=1, max_length=128)


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
  # root_path (set once from TTS_ERP_EXTERNAL_PREFIX in app.py) is the
  # single source of truth for the external mount prefix.
  prefix = request.scope.get("root_path", "")
  # Middleware redirects keep ``next`` route-relative, but page JS from older
  # deployments may already include root_path (for example /tts/v2/pages/...).
  # Prefix idempotently so either caller lands on exactly one external prefix;
  # a duplicated /tts/tts/... path is unknown to the policy and falls back to
  # admin, producing a misleading readwrite 403.
  already_prefixed = bool(prefix) and (
    raw_next == prefix or raw_next.startswith(f"{prefix}/")
  )
  next_url = raw_next if already_prefixed else f"{prefix}{raw_next}"
  return HTMLResponse(_LOGIN_HTML.replace("__NEXT__", _html.escape(next_url)))


@router.post("/login")
def login(body: LoginBody, request: Request) -> Response:
  """用户名+密码认证并签发服务端会话 cookie。"""
  username = body.username.strip().lower()
  retry_after = session_auth.login_throttle_hit(_client_bucket(request))
  if retry_after is not None:
    login_logger.info(
      "result=throttled user=%s retry_after=%d",
      _key_prefix(username),
      retry_after,
    )
    return JSONResponse(
      status_code=status.HTTP_429_TOO_MANY_REQUESTS,
      content={
        "detail": "too many login attempts",
        "retry_after_s": retry_after,
      },
    )
  from tts_erp_v2.db.base import get_session_factory

  try:
    with get_session_factory()() as db:
      user = service.authenticate(db, username, body.password)
      if user is None:
        # 统一错误文案（防用户枚举）；用户名前缀仅供运维日志排查。
        login_logger.info("result=invalid user=%s", _key_prefix(username))
        return JSONResponse(
          status_code=status.HTTP_401_UNAUTHORIZED,
          content={"detail": "用户名或密码错误"},
        )
      token = service.sessions.create_session(
        db,
        user_id=user.id,
        ttl_seconds=session_auth.session_ttl_seconds(),
        ip=request.client.host if request.client else None,
        user_agent=(request.headers.get("user-agent") or "")[:256] or None,
      )
      service.touch_login(db, user)
      context = service.load_user_context(db, user.id)
  except Exception as exc:  # noqa: BLE001 — auth store unreachable → fail closed
    login_logger.warning(
      "result=store_unavailable user=%s error=%s",
      _key_prefix(username),
      type(exc).__name__,
    )
    return JSONResponse(
      status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
      content={"detail": f"auth store unavailable: {type(exc).__name__}"},
    )

  login_logger.info(
    "result=ok user=%s uid=%d", _key_prefix(username), user.id
  )
  resp = JSONResponse(
    content={
      "ok": True,
      "username": user.username,
      "displayName": user.display_name,
      "role": context.api_tier if context else "readonly",
      "pages": sorted(context.pages) if context else [],
    }
  )
  # Path=root_path（生产 /tts）：同域其他路径的服务收不到本 cookie。
  resp.set_cookie(
    key=session_auth.SESSION_COOKIE_NAME,
    value=token,
    max_age=session_auth.session_ttl_seconds(),
    httponly=True,
    secure=session_auth.session_secure_flag(),
    samesite="lax",
    path=session_auth.cookie_path(request.scope.get("root_path", "")),
  )
  return resp


@router.post("/logout")
def logout(request: Request) -> Response:
  """吊销当前会话并清 cookie（幂等，公开）。"""
  raw = request.cookies.get(session_auth.SESSION_COOKIE_NAME)
  if raw:
    try:
      from tts_erp_v2.db.base import get_session_factory

      with get_session_factory()() as db:
        service.sessions.revoke_session(db, raw)
    except Exception:  # noqa: BLE001 — 登出尽力而为：cookie 仍会清除
      login_logger.warning("result=revoke_failed")
  resp = Response(status_code=status.HTTP_204_NO_CONTENT)
  resp.delete_cookie(
    session_auth.SESSION_COOKIE_NAME,
    path=session_auth.cookie_path(request.scope.get("root_path", "")),
  )
  return resp


@router.get("/me")
def me(request: Request) -> dict:
  """会话状态（公开；自校验 cookie + 回库复查，吊销/禁用即时反映）."""
  raw = request.cookies.get(session_auth.SESSION_COOKIE_NAME)
  if not raw:
    return {"authenticated": False}
  try:
    from tts_erp_v2.db.base import get_session_factory

    with get_session_factory()() as db:
      credential = service.authenticate_session_token(db, raw)
  except Exception:  # noqa: BLE001 — auth store unreachable; report unauthenticated
    credential = None
  if credential is None:
    return {"authenticated": False}
  return {
    "authenticated": True,
    "username": credential.username,
    "displayName": credential.display_name,
    # role 字段保留 = api_tier 文本（页面 JS 现用字段，兼容不破）。
    "role": credential.role.value,
    "roles": [],  # 页面暂不消费；需要时用 /v2/users/{id} 查
    "pages": sorted(credential.pages),
  }


@router.post("/change-password")
def change_password(body: ChangePasswordBody, request: Request) -> Response:
  """登录用户改自己的密码；成功后吊销其他会话。"""
  raw = request.cookies.get(session_auth.SESSION_COOKIE_NAME)
  credential = None
  if raw:
    try:
      from tts_erp_v2.db.base import get_session_factory

      with get_session_factory()() as db:
        credential = service.authenticate_session_token(db, raw)
        if credential is None:
          return JSONResponse(
            status_code=status.HTTP_401_UNAUTHORIZED,
            content={"detail": "未登录"},
          )
        try:
          service.change_password(
            db,
            user_id=credential.user_id,
            old_password=body.oldPassword,
            new_password=body.newPassword,
            keep_session_id=credential.session_id,
          )
        except (service.AccountError, PasswordPolicyError) as exc:
          return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST, content={"detail": str(exc)}
          )
    except Exception as exc:  # noqa: BLE001 — fail closed
      login_logger.warning("result=pw_change_store_error error=%s", type(exc).__name__)
      return JSONResponse(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        content={"detail": f"auth store unavailable: {type(exc).__name__}"},
      )
  else:
    return JSONResponse(
      status_code=status.HTTP_401_UNAUTHORIZED, content={"detail": "未登录"}
    )
  login_logger.info("result=pw_changed uid=%d", credential.user_id)
  return JSONResponse(content={"ok": True})


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
      <p class="login-hint">使用账号登录以访问系统</p>
    </div>

    <form id="login-form">
      <div class="form-group">
        <label class="form-label" for="username">用户名</label>
        <input type="text" id="username" class="form-input" placeholder="输入用户名" autocomplete="username" required>
      </div>
      <div class="form-group">
        <label class="form-label" for="password">密码</label>
        <input type="password" id="password" class="form-input" placeholder="输入密码" autocomplete="current-password" required>
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

    // If already logged in, redirect to dashboard (or the ?next target).
    (async () => {
      try {
        const r = await fetch(API + "/auth/me", { headers: { "X-Requested-With": "tts-erp" } });
        if (r.ok) {
          const data = await r.json();
          if (data.authenticated) {
            const next = document.getElementById("next").value || (base + "/v2/pages/dashboard");
            location.replace(next);
            return;
          }
        }
      } catch (_) { /* network error — stay on login page */ }
    })();

    document.getElementById("login-form").addEventListener("submit", async (e) => {
      e.preventDefault();
      const username = document.getElementById("username").value.trim();
      const password = document.getElementById("password").value;
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
          body: JSON.stringify({ username: username, password: password, next: next }),
        });
        if (r.ok) {
          location.href = next;
          return;
        }
        if (r.status === 401) {
          err.textContent = "用户名或密码错误";
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
