"""/v2/oauth/tiktok/* — TikTok Shop seller authorization (new-shop onboarding).

The front half of the OAuth lifecycle that v1's standalone
``oauth-receiver`` used to own (its ``/authorize`` + ``/callback``);
v2 retires that service, so the redirect target moves here.

Routes:
- ``GET /v2/oauth/tiktok/authorize`` — readwrite or above. Mints a
  single-use CSRF ``state`` and returns the TikTok authorization link.
  Open the link in a browser, sign in as the seller, approve.
- ``GET /v2/oauth/tiktok/callback`` — **public** (TikTok redirects the
  seller's browser here with ``?code=...&state=...``). Validates state,
  exchanges the auth_code, and bootstraps the ``integration.credentials``
  + ``commerce.shops`` rows. Renders an HTML result page by default;
  pass ``?format=json`` for machine-readable output.

The Redirect URL registered in Partner Center must point at the public
form of ``/callback`` with the external prefix, no port — e.g.
``http://daqiang.nat100.top/tts/v2/oauth/tiktok/callback`` (nginx only
proxies ``/tts/*`` to :9877; the prefix comes from
``TTS_ERP_EXTERNAL_PREFIX``, currently ``/tts``).

Contract + setup: ``tech-doc/api/tiktok-shop-oauth.md``. Orchestration
logic lives in :mod:`tts_erp_v2.proxy.tiktok_oauth` (kept router-free
so it is unit-testable without HTTP); outbound token calls live in
:mod:`tts_erp_v2.proxy.tiktok_auth`.
"""

from __future__ import annotations

import html as _html
import json
import logging
import os
from typing import Any

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Query,
    Request,
    status,
)
from fastapi.responses import HTMLResponse, JSONResponse
from sqlalchemy.orm import Session

from tts_erp_v2.api.deps import get_session, require_role_at_least
from tts_erp_v2.proxy.errors import ProxyError, SigningError, UpstreamHttpError
from tts_erp_v2.proxy.tiktok_auth import build_authorize_url
from tts_erp_v2.proxy.tiktok_oauth import (
    OAuthFlowError,
    complete_tiktok_authorization,
    pop_state,
    register_state,
)

router = APIRouter(prefix="/v2/oauth/tiktok", tags=["oauth-tiktok"])

log = logging.getLogger("tts_erp_v2.oauth")

# Callback query params must be declared on the handler; FastAPI maps
# them from the redirect query string.

_CALLBACK_PATH = "/v2/oauth/tiktok/callback"  # must match middleware EXEMPT_PATHS


# ─── HTML rendering (minimal, mirrors api/v2/auth.py style) ─────────


def _page(title: str, body_html: str, *, status_line: str = "") -> str:
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{_html.escape(title)}</title>
<style>
  body {{ font-family: -apple-system, system-ui, sans-serif; background: #f6f8fa; margin: 0; min-height: 100vh; display: flex; align-items: center; justify-content: center; color: #1f2328; }}
  .card {{ background: #fff; border: 1px solid #d0d7de; border-radius: 8px; padding: 24px 28px; width: 480px; box-shadow: 0 1px 3px rgba(27,31,36,.12); }}
  h1 {{ font-size: 18px; margin: 0 0 6px; }}
  .status {{ font-size: 13px; font-weight: 600; margin: 0 0 14px; }}
  .ok .status {{ color: #1a7f37; }}
  .err .status {{ color: #cf222e; }}
  p {{ font-size: 13px; line-height: 1.5; margin: 6px 0; }}
  code {{ background: #f6f8fa; border: 1px solid #d0d7de; border-radius: 4px; padding: 1px 5px; font-size: 12px; }}
  pre {{ background: #f6f8fa; border: 1px solid #d0d7de; border-radius: 6px; padding: 10px; font-size: 12px; overflow: auto; }}
  table {{ border-collapse: collapse; font-size: 13px; width: 100%; margin: 8px 0; }}
  td, th {{ border: 1px solid #d0d7de; padding: 5px 8px; text-align: left; }}
  th {{ background: #f6f8fa; font-weight: 600; }}
  a {{ color: #0969da; }}
</style>
</head>
<body>
<div class="card {_html.escape("ok" if "success" in title.lower() else "err")}">
  <p class="status">{status_line}</p>
  {body_html}
</div>
</body>
</html>
"""


def _err_page(title: str, detail: str) -> HTMLResponse:
    return HTMLResponse(
        content=_page(
            title,
            f"<p>{_html.escape(detail)}</p>",
            status_line=_html.escape(title),
        ),
        status_code=status.HTTP_400_BAD_REQUEST,
    )


def _json(*, ok: bool, http_status: int, **fields: Any) -> JSONResponse:
    return JSONResponse(
        status_code=http_status,
        content={"ok": ok, **fields},
    )


# ─── authorize link ──────────────────────────────────────────────────


@router.get("/authorize", summary="Start TikTok seller authorization")
def authorize(
    request: Request,
    sess: Session = Depends(get_session),  # noqa: B008 — FastAPI DI 惯例
    redirect_to: str | None = Query(
        default=None,
        description="(display hint only) where the operator should land after the flow.",
    ),
):
    """Mint a single-use CSRF state and build the TikTok authorization link.

    **Readwrite or above.** Returns the ``authorize_url`` to open in a
    browser (as the seller) plus the raw ``state`` for diagnostics.
    Generating the link is harmless (no upstream call, no destructive
    DB write — just a single-use CSRF row); only the resulting callback
    mutates ``integration.credentials`` + ``commerce.shops``, and that
    surface stays under the public OAuth handshake.

    Requires ``TIKTOK_SERVICE_ID`` (Partner Center App & Service page)
    in the server env — else 500 with a config message.
    """
    require_role_at_least(request, "readwrite")
    try:
        raw_state, expires_at = register_state(sess)
        authorize_url = build_authorize_url(state=raw_state)
    except SigningError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc)
        ) from exc

    log.info(
        "oauth authorize: state registered for redirect=%s expires=%s",
        redirect_to,
        expires_at.isoformat(),
    )
    payload = {
        "authorize_url": authorize_url,
        "state": raw_state,
        "state_expires_at": expires_at.isoformat(),
        "hint": (
            "open authorize_url in a browser, approve as the seller, then "
            f"TikTok redirects to {_CALLBACK_PATH} with code+state"
        ),
    }
    if request.query_params.get("format") == "json":
        return _json(ok=True, http_status=200, **payload)
    body = (
        "<h1>Authorize a new TikTok shop</h1>"
        f"<p>State registered (single-use, expires "
        f"<code>{_html.escape(payload['state_expires_at'])}</code> UTC).</p>"
        "<p><strong>Open this link in a browser</strong> and approve as the "
        "seller:</p>"
        f'<p><a href="{_html.escape(authorize_url)}">Open authorization link</a></p>'
        f"<p><code>{_html.escape(authorize_url)}</code></p>"
        "<p>After approval, TikTok redirects here with "
        "<code>?code=...&amp;state=...</code> and this page shows the result.</p>"
    )
    return HTMLResponse(
        content=_page(
            "tts-erp · authorize", body, status_line="Authorization link ready"
        ),
        status_code=status.HTTP_200_OK,
    )


# ─── callback (public) ───────────────────────────────────────────────


@router.get("/callback", summary="TikTok OAuth redirect target (public)")
def callback(
    request: Request,
    sess: Session = Depends(get_session),  # noqa: B008 — FastAPI DI 惯例
    code: str | None = Query(default=None),
    state: str | None = Query(default=None),
    error: str | None = Query(default=None),
    format: str | None = Query(default=None),
):
    """Handle TikTok's redirect after the seller approves/rejects.

    **Public** — TikTok hits this URL (browser redirect), so it is in
    the auth-middleware exempt list and needs no API key.

    Success: state validated + consumed, auth_code exchanged, and the
    ``integration.credentials`` + ``commerce.shops`` rows upserted.
    The sync worker picks the new shop up automatically on its next
    tick (it fans out over credentials rows).
    """
    fmt = (format or "").lower() == "json"
    if code:
        log.info(
            "oauth callback received: code_prefix=%s state_present=%s fmt=%s",
            code[:10],
            bool(state),
            fmt,
        )
    else:
        log.warning(
            "oauth callback received without code (error=%s state_present=%s)",
            error,
            bool(state),
        )
    if fmt:
        return _handle_json(code=code, state=state, error=error, sess=sess)
    return _handle_html(code=code, state=state, error=error, sess=sess)


def _handle_json(
    *, code: str | None, state: str | None, error: str | None, sess: Any
) -> JSONResponse:
    code_pfx = (code or "")[:10]
    state_present = bool(state)
    # Seller rejected at the consent screen.
    if error:
        if state_present:
            pop_state(sess, state)  # spend the CSRF token best-effort
        log.warning(
            "oauth callback json denied: error=%s state_present=%s",
            error,
            state_present,
        )
        return _json(
            ok=False,
            http_status=200,
            kind="denied",
            error=error,
        )
    if not code:
        log.warning("oauth callback json missing_code: state_present=%s", state_present)
        return _json(
            ok=False,
            http_status=400,
            kind="missing_code",
            error="callback hit without ?code — start from /v2/oauth/tiktok/authorize",
        )
    try:
        out = complete_tiktok_authorization(sess, code=code, state=state or "")
    except OAuthFlowError as exc:
        log.warning(
            "oauth callback json rejected: kind=%s code_prefix=%s state_present=%s msg=%s",
            exc.kind,
            code_pfx,
            state_present,
            exc.message,
        )
        return _json(
            ok=False,
            http_status=400,
            kind=exc.kind,
            error=exc.message,
        )
    except UpstreamHttpError as exc:
        log.error(
            "oauth callback json upstream failure: code_prefix=%s upstream_code=%s %s",
            code_pfx,
            getattr(exc, "upstream_code", None),
            exc,
        )
        return _json(
            ok=False,
            http_status=502,
            kind="upstream",
            error=f"token exchange failed: {exc}",
            upstream_code=getattr(exc, "upstream_code", None),
        )
    except ProxyError as exc:
        log.error("oauth callback json proxy failure: code_prefix=%s %s", code_pfx, exc)
        return _json(
            ok=False,
            http_status=502,
            kind="proxy",
            error=str(exc),
        )
    for shop in out.get("shops") or []:
        log.info(
            "oauth callback: shop=%s authorized (json) credential_id=%s",
            shop.get("shop_id"),
            shop.get("credential_id"),
        )
    return _json(ok=True, http_status=200, kind="authorized", result=out)


def _handle_html(
    *, code: str | None, state: str | None, error: str | None, sess: Any
) -> HTMLResponse:
    code_pfx = (code or "")[:10]
    state_present = bool(state)
    if error:
        if state:
            pop_state(sess, state)
        return HTMLResponse(
            content=_page(
                "tts-erp · authorization not granted",
                f"<p>TikTok reports: <code>{_html.escape(error)}</code>.</p>"
                "<p>The seller did not grant access. Start a fresh "
                "<code>/v2/oauth/tiktok/authorize</code> when ready.</p>",
                status_line="Authorization not granted",
            ),
            status_code=status.HTTP_200_OK,
        )
    if not code:
        return HTMLResponse(
            content=_page(
                "tts-erp · callback",
                "<p>This is the OAuth redirect target — it was opened "
                "without a <code>?code</code>.</p>"
                "<p>Start the flow from "
                "<code>/v2/oauth/tiktok/authorize</code>.</p>",
                status_line="No authorization code",
            ),
            status_code=status.HTTP_400_BAD_REQUEST,
        )
    try:
        out = complete_tiktok_authorization(sess, code=code, state=state or "")
    except OAuthFlowError as exc:
        log.warning(
            "oauth callback rejected: kind=%s code_prefix=%s state_present=%s msg=%s",
            exc.kind,
            code_pfx,
            state_present,
            exc.message,
        )
        return _err_page("Authorization failed", exc.message)
    except UpstreamHttpError as exc:
        log.error(
            "oauth callback upstream failure: code_prefix=%s upstream_code=%s %s",
            code_pfx,
            getattr(exc, "upstream_code", None),
            exc,
        )
        return HTMLResponse(
            content=_page(
                "Authorization failed",
                f"<p>Token exchange was rejected upstream: "
                f"<code>{_html.escape(str(exc))}</code></p>"
                "<p>The one-time auth code is now spent — run "
                "<code>/v2/oauth/tiktok/authorize</code> again for a fresh link.</p>",
                status_line="Upstream token exchange failed",
            ),
            status_code=status.HTTP_502_BAD_GATEWAY,
        )
    except ProxyError as exc:
        log.error("oauth callback proxy failure: code_prefix=%s %s", code_pfx, exc)
        return HTMLResponse(
            content=_page(
                "Authorization failed",
                f"<p><code>{_html.escape(str(exc))}</code></p>",
                status_line="Proxy error",
            ),
            status_code=status.HTTP_502_BAD_GATEWAY,
        )

    shops = out.get("shops") or []
    for shop in shops:
        log.info(
            "oauth callback: shop=%s authorized (html) credential_id=%s account_id=%s",
            shop.get("shop_id"),
            shop.get("credential_id"),
            shop.get("account_id"),
        )
    sections = []
    for shop in shops:
        rows = []
        for label, value in (
            ("Shop id", shop.get("shop_id")),
            ("Seller name", shop.get("account_name")),
            ("Region", shop.get("region")),
            ("Seller type", shop.get("seller_type")),
            ("Credentials row", shop.get("credential_id")),
            ("Channel account row", shop.get("account_id")),
            ("Access token expires", shop.get("expires_at")),
        ):
            rows.append(
                f"<tr><th>{_html.escape(label)}</th>"
                f"<td><code>{_html.escape(str(value))}</code></td></tr>"
            )
        scopes = ", ".join(shop.get("granted_scopes") or [])
        sections.append(
            "<table>"
            + "".join(rows)
            + "</table>"
            + f"<p>Granted scopes: <code>{_html.escape(scopes)}</code></p>"
        )
    n = len(shops)
    body = (
        "<p>The shop is authorized. Sync jobs pick it up automatically "
        "on their next tick.</p>" + "<hr>".join(sections)
    )
    return HTMLResponse(
        content=_page(
            "tts-erp · shop authorized",
            body,
            status_line=f"Shop {shops[0].get('shop_id')} authorized"
            if n == 1
            else f"{n} shops authorized",
        ),
        status_code=status.HTTP_200_OK,
    )


# ─── operator console: 新店授权控制台页 ──────────────────────────────
# Self-contained HTML shell (inline CSS/JS, no vendor assets) served at
# GET /v2/oauth/tiktok/onboard (readonly-classified in middleware/auth.py
# so an unauthenticated browser 302s to the login page like /v2/pages/*).
# Behaviour lives in the inline script: probe /v2/auth/me → role-gate
# (readwrite+ may generate, readonly is shown a 403 hint) → on click
# call GET /v2/oauth/tiktok/authorize?format=json (readwrite-gated) and
# window.open the returned link in a new tab. Every click = fresh state
# (45-min, single-use); the URL itself is never cached client-side.

_ONBOARD_PAGE_HTML = """<!doctype html>
<html lang="zh-Hans">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>新店授权 · tts-erp</title>
<style>
  :root {
    --paper: #F4EFE4; --paper-deep: #EAE3D2; --ink: #1B1814; --ink-soft: #4A4239;
    --rule: #C9BFA8; --rule-soft: #DDD4BF; --accent: #B8390E; --accent-deep: #8F2C09;
    --muted: #6E6657; --danger: #8C1A1A; --ok: #2F6B3E;
    --mono: ui-monospace, 'JetBrains Mono', 'SF Mono', 'Cascadia Mono', Consolas, monospace;
    --sans: ui-sans-serif, system-ui, -apple-system, 'Segoe UI', 'PingFang SC',
            'Hiragino Sans GB', 'Microsoft YaHei', 'Noto Sans CJK SC', sans-serif;
  }
  * { box-sizing: border-box; }
  html, body { margin: 0; background: var(--paper); color: var(--ink);
    font-family: var(--sans); font-size: 14px; line-height: 1.5; }
  a { color: var(--accent); text-decoration: none; }
  a:hover { color: var(--accent-deep); }
  .wrap { max-width: 860px; margin: 0 auto; padding: 28px 24px 48px; }
  header.ops { border-bottom: 1px solid var(--rule); padding-bottom: 14px;
    margin-bottom: 22px; display: flex; justify-content: space-between;
    align-items: flex-end; gap: 12px; flex-wrap: wrap; }
  .eyebrow { font-family: var(--mono); font-size: 11px; letter-spacing: .16em;
    text-transform: uppercase; color: var(--muted); }
  h1 { font-size: 22px; margin: 4px 0 0; font-weight: 650; letter-spacing: .01em; }
  #identity { font-family: var(--mono); font-size: 12px; color: var(--muted); }
  .card { background: #FCF9F1; border: 1px solid var(--rule);
    padding: 18px 20px; margin-bottom: 16px; }
  .card h2 { font-size: 13px; font-family: var(--mono); font-weight: 600;
    letter-spacing: .08em; text-transform: uppercase; color: var(--ink-soft);
    margin: 0 0 12px; }
  .row { display: flex; gap: 10px; align-items: center; flex-wrap: wrap; }
  button { font: inherit; background: var(--ink); color: var(--paper);
    border: 0; padding: 8px 16px; cursor: pointer; }
  button:hover { background: var(--ink-soft); }
  button:disabled { opacity: .45; cursor: wait; }
  .btn-ghost { background: transparent; color: var(--accent);
    border: 1px solid var(--accent); }
  .btn-ghost:hover { background: rgba(184,57,14,.06); }
  .meta { color: var(--muted); font-size: 12px; }
  .ok { color: var(--ok); } .err { color: var(--danger); }
  .status { font-size: 13px; min-height: 20px; }
  ol.steps { padding-left: 20px; margin: 8px 0 0; }
  ol.steps li { margin: 6px 0; }
  code { font-family: var(--mono); background: var(--paper-deep);
    padding: 0 5px; font-size: 12px; }
  .hide { display: none; }
  #redirect-hint { font-family: var(--mono); font-size: 12px; color: var(--ink-soft); }
</style>
</head>
<body>
<div class="wrap">
  <header class="ops">
    <div>
      <div class="eyebrow">TikTok Shop · Seller OAuth</div>
      <h1>新店接入授权</h1>
    </div>
    <div id="identity">…</div>
  </header>

  <div class="card">
    <h2>授权链接</h2>
    <div class="row">
      <button id="btn-gen" type="button">授权新店</button>
      <span class="meta">点一次即可 · 自动新窗口打开 TikTok 授权页 · state 单次使用 · 45 分钟内有效</span>
    </div>
    <div class="status" id="status"></div>
    <div id="role-gate" class="hide">
      <p class="status err">当前会话没有 <code>readwrite</code> 或以上角色 — 「授权新店」需要 readwrite 及以上。
        换用更高权限账号登录后重试。</p>
    </div>
  </div>

  <div class="card">
    <h2>操作步骤</h2>
    <ol class="steps">
      <li>点「<strong>授权新店</strong>」—— 自动在新窗口打开 TikTok 授权页（state 单次使用 · 45 分钟内有效）。</li>
      <li>以要接入的 <strong>卖家账号</strong> 登录 TikTok Seller Center 并同意授权。</li>
      <li>TikTok 会把浏览器带回回调地址，页面会显示授权结果（店名 / 地区 / 授权范围）。</li>
      <li>落库成功即完成 —— 下个同步 tick 会自动把新店纳入数据同步
        （<code>integration.credentials</code> + <code>commerce.shops</code>，含每店 shop_cipher）。</li>
    </ol>
    <p class="meta" style="margin-bottom:4px">前置条件：</p>
    <ul class="steps">
      <li>Partner Center 的 Redirect URL 已配成公网形式：<br>
        <span id="redirect-hint"></span></li>
      <li><code>TIKTOK_SERVICE_ID</code> 已配置、app 已过审（生成接口会直接提示缺什么）。</li>
    </ul>
  </div>
</div>

<noscript><p style="text-align:center">此页面需要 JavaScript。</p></noscript>
<script>
(() => {
  'use strict';
  var PREFIX = location.pathname.replace(/\\/v2\\/oauth\\/tiktok\\/.*$/, "");
  if (!/^\\/[a-z0-9/_-]*$/i.test(PREFIX)) PREFIX = "";
  function $(s) { return document.querySelector(s); }
  function esc(s) { return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
    return ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]; }); }
  function html(el, m) { el.innerHTML = m; } // pi-lens-ignore: no-inner-html-js
  function loginUrl() {
    return PREFIX + "/v2/auth/login?next=" + PREFIX + "/v2/oauth/tiktok/onboard";
  }
  function api(path) {
    return fetch(PREFIX + path, { credentials: "include", headers: {} })
      .then(function (r) {
        if (r.status === 401) { window.location.href = loginUrl(); return null; } // pi-lens-ignore: no-open-redirect-js
        return r;
      });
  }
  var statusEl = $("#status");
  function setStatus(cls, msg) { if (!statusEl) return; statusEl.className = "status" + (cls ? " " + cls : ""); statusEl.textContent = msg || ""; }
  function openAuth(url, expiresAt) {
    // window.open must be called in a user-gesture context; the click
    // handler's .then() chain qualifies for Chrome; Firefox may still
    // block — fall back to an inline link in that case.
    var w = window.open(url, "_blank", "noopener,noreferrer");
    if (w) {
      var t = expiresAt ? expiresAt.replace("T", " ").replace(/\\.\\d+Z$/, "Z") : "";
      setStatus("ok", "已在新标签页打开授权页" + (t ? " · 有效至 " + t + " UTC" : "") + "（单次使用；被用过或过期后重新点「授权新店」）。");
    } else {
      // popup blocked — render an inline clickable fallback
      statusEl.innerHTML = '浏览器拦截了新窗口，请<a href="' + esc(url) + '" target="_blank" rel="noopener">点这里打开授权页</a>（单次使用 · 45 分钟内有效）。';
      statusEl.className = "status err";
    }
  }
  function gate() {
    var g = $("#role-gate"); if (g) g.classList.remove("hide");
    var b = $("#btn-gen"); if (b) b.disabled = true;
    setStatus("", "");
  }
  function generate() {
    var b = $("#btn-gen"); if (!b) return;
    b.disabled = true; var oldLabel = b.textContent; b.textContent = "生成中…";
    setStatus("", "正在向 TikTok 注册一次性 state…");
    api("/v2/oauth/tiktok/authorize?format=json")
      .then(function (r) {
        if (!r) return null;
        if (r.status === 403) { gate(); return null; }
        if (!r.ok) { return r.json().catch(function () { return {}; }); }
        return r.json();
      })
      .then(function (d) {
        if (!d) return;
        if (d.ok && d.authorize_url) {
          openAuth(d.authorize_url, d.state_expires_at);
        } else {
          var msg = (d && (d.error || d.detail)) || ("HTTP " + (d && d.status_code || "错误"));
          setStatus("err", "生成失败：" + msg + " — 检查服务端日志可定位（缺 TIKTOK_SERVICE_ID 会在此报配置错误）。");
        }
      })
      .catch(function (e) { setStatus("err", "请求失败：" + (e && e.message || e)); })
      .then(function () { if (b) { b.disabled = false; b.textContent = oldLabel; } });
  }
  var gen = $("#btn-gen");
  if (gen) gen.addEventListener("click", generate);
  function boot() {
    var redir = $("#redirect-hint");
    if (redir) redir.textContent = window.__REDIRECT_HINT__ || "";
    api("/v2/auth/me").then(function (r) { return r ? r.json() : null; }).then(function (me) {
      var id = $("#identity"); if (!id) return;
      if (me && me.authenticated) {
        var key = me.key_prefix || "session";
        html(id, "操作员 <code>" + esc(key) + "</code> · " + esc(me.role || "") + ' · <a href="' + PREFIX + '/v2/auth/logout">退出</a>');
      } else { html(id, '<a href="' + loginUrl() + '">登录</a>'); return; }
      // Only gate users who lack the generate privilege; do NOT
      // auto-generate on page load (would auto-open a TikTok tab
      // every time admin/readwrite visits the page — intrusive).
      if (me.role !== "admin" && me.role !== "readwrite") { gate(); }
    }).catch(function () { /* not fatal */ });
  }
  boot();
})();
</script>
</body>
</html>
"""


@router.get("/onboard", response_class=HTMLResponse, summary="新店授权控制台页 (HTML)")
def onboard_page(request: Request) -> HTMLResponse:
    """Operator console for onboarding a new TikTok shop (browser UI).

    Readonly HTML shell (``/v2/oauth/tiktok/onboard`` in the auth
    middleware's ``_READONLY_EXACT`` so an unauthenticated browser GET is
    302-redirected to the login page). The page's inline JS probes
    ``/v2/auth/me``; a ``readwrite``-or-above session may click the
    button, which calls ``GET /v2/oauth/tiktok/authorize?format=json``
    (readwrite-gated) and ``window.open``-s the returned link in a new
    tab. Readonly sessions see an inline 403 hint instead. No shop/DB
    data is rendered server-side; the shell is static.
    """
    prefix = os.environ.get("TTS_ERP_EXTERNAL_PREFIX", "")
    redirect_hint = f"{prefix}/v2/oauth/tiktok/callback"
    return HTMLResponse(
        _ONBOARD_PAGE_HTML.replace(
            'window.__REDIRECT_HINT__ || ""',
            f'window.__REDIRECT_HINT__ = {json.dumps(redirect_hint)} || ""',
        )
    )
