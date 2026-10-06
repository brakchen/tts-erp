"""/v2/pages/* — server-rendered HTML pages (no SPA framework).

Pages are **Jinja2 templates** under ``tts_erp_v2/templates/pages/`` (one
``.html`` per page), rendered by :func:`_render_page`. Templates are styled
with **Bootstrap 5.3.8**, self-hosted at ``/static/vendor/bootstrap.min.css``
(MIT — see ``static/vendor/NOTICE.md``); no CDN links. Page behaviour lives in
``/static/js/*.js``. Layout is Bootstrap grid + utilities with the inline
warm-paper skin; pages are mobile-adapted (the ROI shell has breakpoint
column pruning + sticky first column).

Template context/globals (see ``_render_page``):

* ``current_page`` — sidebar slug; drives ``{{ sidebar(current_page) }}``
  (markup + toggle JS) and ``{{ sidebar_css(current_page) }}`` injections.
* ``js_version(name)`` / ``css_version(name)`` — SHA-256 content stamps for
  cache-busting (``?v=`` tokens), replacing the old ``__JSV_*__`` placeholders.
* The SPU-profitability shell additionally takes ``title`` / ``profile_id`` /
  ``entrypoint_js`` (see :class:`_SpuProfitabilityPageConfig`).

Cache-busting: /static has no explicit Cache-Control, so every mutable asset
gets its own content hash. CSS and JS must not share a version token: a CSS-
only hotfix otherwise keeps the old URL and remains stale on mobile browsers.

Asset paths are RELATIVE (``../../static/…``) so the page works both on
``127.0.0.1:9877`` directly and behind the NGINX ``/tts`` prefix
(2026-08-31: absolute ``/static/…`` links 404'd behind the prefix).

Auth classification: most pages are ``readonly``-equivalent for the GET
(handler does no DB writes), while operational consoles such as
``/v2/pages/sync-jobs`` can add handler-level ``readwrite`` gates. The page JS
calls write endpoints such as ``/v2/reporting/manual-costs`` — which require a
readwrite or admin session via the ``/v2/auth/login`` cookie flow.
(2026-09-05 page-rework
lane: the page no longer calls the ``/v2/spu-images/*`` upload/confirm
endpoints — the image column renders the MinIO mirror instead.)

Visual design (2026-09-01 redesign)
----------------------------------
Tone: industrial operations console — like a customs manifest or
shipping dock dashboard. Honest, dense, monospace-heavy. The page's
signature element is the oversized queue counter at the top: the
operator's daily job is to grind that number down. Bootstrap stays for
layout primitives (``d-flex``, ``gap-2``, ``mt-3`` …) but the visible
personality comes from the inline ``<style>`` block — warm paper bg,
hairline rules, burnt-sienna accent used in 3–4 specific places only.
Constraint respected: no external CSS file (per the 2026-08-31 decision
that retired ``/static/css/console.css``), and no webfonts (no CDN).
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from jinja2 import Environment, FileSystemLoader, select_autoescape
from markupsafe import escape

from tts_erp_v2.access._context import user_label_var, user_pages_var
from tts_erp_v2.accounts.pages import PAGES
from tts_erp_v2.api.deps import require_role_at_least

router = APIRouter(prefix="/v2/pages", tags=["pages"])


def _require_readwrite(request: Request) -> None:
  """Operational consoles with manual job controls require readwrite+."""
  require_role_at_least(request, "readwrite")


# Cache-busting: /static has no explicit Cache-Control, so every mutable asset
# gets its own content hash. CSS and JS must not share a version token: a CSS-
# only hotfix otherwise keeps the old URL and remains stale on mobile browsers.
_STATIC_DIR = Path(__file__).resolve().parents[2] / "static"
_JS_DIR = _STATIC_DIR / "js"
_CSS_DIR = _STATIC_DIR / "css"

# ── Shared sidebar navigation ─────────────────────────────────────────
# Bootstrap 5 utilities + a shared responsive navigation shell.

_SIDEBAR_CSS = """
    /* Shared sidebar shell: injected after each page's own styles on purpose. */
    body {
      --sidebar-width: 220px;
      --sidebar-rail-width: 64px;
      --sidebar-paper: var(--paper, var(--bg, #F4EFE4));
      --sidebar-paper-deep: var(--paper-deep, var(--card, #EAE3D2));
      --sidebar-ink: var(--ink, var(--text, #1B1814));
      --sidebar-ink-soft: var(--ink-soft, var(--muted, #4A4239));
      --sidebar-rule: var(--rule, var(--border, #C9BFA8));
      --sidebar-rule-soft: var(--rule-soft, var(--border, #DDD4BF));
      --sidebar-accent: var(--accent, #B8390E);
      --sidebar-accent-deep: var(--accent-deep, var(--accent, #8F2C09));
      --sidebar-muted: var(--muted, #6E6657);
      --sidebar-mono: var(--mono, ui-monospace, monospace);
      margin-left: var(--sidebar-width);
      transition: margin-left 180ms ease;
    }
    body.sidebar-collapsed { margin-left: var(--sidebar-rail-width); }
    body.sidebar-mobile-open { overflow: hidden; }
    .op-home-link { display: none; }

    #sidebar.sidebar {
      width: var(--sidebar-width);
      background: var(--sidebar-paper-deep);
      color: var(--sidebar-ink);
      z-index: 105;
      box-shadow: 1px 0 0 var(--sidebar-rule);
      transition: width 180ms ease, transform 180ms ease;
    }
    #sidebar .sidebar-brand {
      min-height: 58px;
      color: var(--sidebar-ink);
      letter-spacing: 0.04em;
    }
    #sidebar .sidebar-brand-mark { color: var(--sidebar-accent); }
    #sidebar .sidebar-nav { scrollbar-width: thin; }
    #sidebar .sidebar-section-title {
      color: var(--sidebar-muted) !important;
      font-family: var(--sidebar-mono);
      font-size: 10px;
      letter-spacing: 0.18em;
    }
    #sidebar .nav-link {
      position: relative;
      display: flex;
      align-items: center;
      gap: 9px;
      min-height: 38px;
      margin: 2px 8px;
      padding: 7px 10px !important;
      border: 1px solid transparent;
      color: var(--sidebar-ink-soft);
      line-height: 1.25;
      white-space: nowrap;
      transition: color 120ms ease, background 120ms ease, border-color 120ms ease;
    }
    #sidebar .nav-link:hover {
      color: var(--sidebar-ink);
      background: color-mix(in srgb, var(--sidebar-paper) 74%, transparent);
      border-color: var(--sidebar-rule-soft);
    }
    #sidebar .nav-link:focus-visible,
    .sidebar-collapse-btn:focus-visible,
    .sidebar-mobile-toggle:focus-visible {
      outline: 2px solid var(--sidebar-accent);
      outline-offset: 2px;
    }
    #sidebar .nav-link.active {
      color: var(--sidebar-accent-deep);
      background: var(--sidebar-paper);
      border-color: var(--sidebar-rule);
      box-shadow: inset 3px 0 0 var(--sidebar-accent);
      font-weight: 600;
    }
    #sidebar .sidebar-nav-icon {
      display: inline-grid;
      place-items: center;
      width: 22px;
      min-width: 22px;
      height: 22px;
      border: 1px solid var(--sidebar-rule);
      color: var(--sidebar-muted);
      font-family: var(--sidebar-mono);
      font-size: 11px;
      line-height: 1;
    }
    #sidebar .nav-link.active .sidebar-nav-icon {
      border-color: var(--sidebar-accent);
      color: var(--sidebar-accent);
    }
    #sidebar .sidebar-footer { background: var(--sidebar-paper-deep); }
    .sidebar-collapse-btn {
      display: flex;
      align-items: center;
      gap: 8px;
      width: 100%;
      padding: 7px 10px;
      border: 1px solid var(--sidebar-rule);
      border-radius: 0;
      background: transparent;
      color: var(--sidebar-muted);
      cursor: pointer;
      text-align: left;
    }
    .sidebar-collapse-btn:hover {
      color: var(--sidebar-accent);
      border-color: var(--sidebar-accent);
      background: var(--sidebar-paper);
    }
    .sidebar-collapse-icon {
      display: inline-grid;
      place-items: center;
      width: 20px;
      min-width: 20px;
      font-family: var(--sidebar-mono);
    }
    .sidebar-mobile-toggle {
      z-index: 110;
      border-radius: 0;
      background: var(--sidebar-paper-deep);
      color: var(--sidebar-ink);
    }
    #sidebar-overlay {
      z-index: 102;
      background: rgba(27, 24, 20, 0.52);
      backdrop-filter: blur(1px);
    }

    /* Collapsed desktop rail. Labels hide, but distinct icons keep it usable. */
    #sidebar.sidebar.is-collapsed { width: var(--sidebar-rail-width); }
    #sidebar.is-collapsed .sidebar-brand { justify-content: center; padding-inline: 0 !important; }
    #sidebar.is-collapsed .sidebar-brand-text,
    #sidebar.is-collapsed .sidebar-section-title,
    #sidebar.is-collapsed .sidebar-label { display: none; }
    #sidebar.is-collapsed .sidebar-nav { padding-top: 8px !important; }
    #sidebar.is-collapsed .nav-link {
      justify-content: center;
      min-height: 42px;
      margin-inline: 7px;
      padding-inline: 0 !important;
    }
    #sidebar.is-collapsed .nav-link.active {
      box-shadow: inset 0 -2px 0 var(--sidebar-accent);
    }
    #sidebar.is-collapsed .sidebar-footer { padding: 8px !important; }
    #sidebar.is-collapsed .sidebar-collapse-btn { justify-content: center; padding-inline: 0; }
    #sidebar.is-collapsed .sidebar-collapse-text { display: none; }

    @media (max-width: 991.98px) {
      body,
      body.sidebar-collapsed { margin-left: 0; }
      #sidebar.sidebar,
      #sidebar.sidebar.is-collapsed {
        width: min(280px, calc(100vw - 48px));
        transform: translateX(-100%);
        box-shadow: 8px 0 24px rgba(27, 24, 20, 0.18);
      }
      #sidebar.sidebar.is-open { transform: translateX(0); }
      #sidebar.is-collapsed .sidebar-brand { justify-content: flex-start; padding-inline: 1rem !important; }
      #sidebar.is-collapsed .sidebar-brand-text,
      #sidebar.is-collapsed .sidebar-section-title,
      #sidebar.is-collapsed .sidebar-label { display: block; }
      #sidebar.is-collapsed .sidebar-nav { padding-top: 0.5rem !important; }
      #sidebar.is-collapsed .nav-link {
        justify-content: flex-start;
        min-height: 38px;
        margin-inline: 8px;
        padding: 7px 10px !important;
      }
      #sidebar.is-collapsed .nav-link.active { box-shadow: inset 3px 0 0 var(--sidebar-accent); }
      .sidebar-collapse-btn { display: none !important; }
      .op-header { padding-left: 56px; }
    }
    @media (min-width: 992px) {
      #sidebar-overlay { display: none !important; }
    }
    @media (prefers-reduced-motion: reduce) {
      body,
      #sidebar.sidebar { transition: none; }
    }
"""


def _sidebar_html(current_page: str) -> str:
  """Return the shared sidebar with the current page marked as active.

  侧边栏按会话用户的页面权限过滤（设计 §7.1）：``user_pages_var`` 为
  None（API key / auth off）时全量显示；否则只列出有权限点的页面。
  权限点清单与渲染共用 ``accounts.pages.PAGES`` 注册表（唯一来源）。
  """
  allowed = user_pages_var.get()
  pages = [
    (p.page_id, p.icon, p.label, p.group)
    for p in PAGES
    if allowed is None or p.permission_code in allowed
  ]
  links = []
  current_group = None
  for page_id, icon, label, group in pages:
    if group != "__group__" and group != current_group:
      current_group = group
      links.append(
        '<div class="sidebar-section-title text-uppercase fw-semibold px-3 pt-3 pb-1">'
        f"{group}</div>"
      )
    active = " active" if page_id == current_page else ""
    aria_current = ' aria-current="page"' if page_id == current_page else ""
    links.append(
      f'<a href="../../v2/pages/{page_id}" class="nav-link{active}"'
      f' title="{label}"{aria_current}>'
      f'<span class="sidebar-nav-icon" aria-hidden="true">{icon}</span>'
      f'<span class="sidebar-label">{label}</span></a>'
    )
  nav_html = "\n      ".join(links)
  user_label = user_label_var.get()
  user_html = ""
  if user_label:
    label = escape(user_label)
    user_html = (
      '<div class="d-flex align-items-center justify-content-between gap-2 pb-2 mb-2 border-bottom">'
      f'<span class="small text-truncate" title="{label}">{label}</span>'
      '<button type="button" id="sidebar-logout" class="btn btn-sm flex-shrink-0">登出</button>'
      "</div>"
    )
  return f"""<button type="button"
    class="sidebar-mobile-toggle btn btn-sm d-lg-none position-fixed top-0 start-0 mt-2 ms-2"
    id="sidebar-toggle" aria-label="打开主导航" aria-controls="sidebar" aria-expanded="false">☰</button>
  <div class="d-none position-fixed top-0 start-0 w-100 h-100"
    id="sidebar-overlay" aria-hidden="true"></div>
  <nav class="sidebar d-flex flex-column position-fixed top-0 start-0 h-100 border-end"
    id="sidebar" aria-label="主导航">

    <a href="../../v2/pages/dashboard"
      class="sidebar-brand d-flex align-items-center gap-2 p-3 border-bottom text-decoration-none">
      <span class="sidebar-brand-mark" aria-hidden="true">◆</span>
      <span class="sidebar-brand-text fw-semibold">tts-erp</span>
    </a>

    <div class="sidebar-nav flex-grow-1 py-2 overflow-auto">
      {nav_html}
    </div>

    <div class="sidebar-footer px-3 py-2 border-top">
      {user_html}
      <button type="button" class="sidebar-collapse-btn"
        id="sidebar-collapse" aria-label="折叠侧边栏" aria-controls="sidebar" aria-expanded="true">
        <span class="sidebar-collapse-icon" aria-hidden="true">«</span>
        <span class="sidebar-collapse-text small">折叠</span>
      </button>
    </div>
  </nav>"""


_SIDEBAR_TOGGLE_JS = """
(function() {
  var btn = document.getElementById('sidebar-toggle');
  var sb = document.getElementById('sidebar');
  var ov = document.getElementById('sidebar-overlay');
  var collapseBtn = document.getElementById('sidebar-collapse');
  var desktopQuery = window.matchMedia('(min-width: 992px)');
  if (!btn || !sb || !ov) return;

  function readCollapsed() {
    try { return localStorage.getItem('sidebar-collapsed') === '1'; }
    catch (_) { return false; }
  }

  function setCollapsed(collapsed, persist) {
    sb.classList.toggle('is-collapsed', collapsed);
    document.body.classList.toggle('sidebar-collapsed', collapsed);
    if (collapseBtn) {
      var icon = collapseBtn.querySelector('.sidebar-collapse-icon');
      var text = collapseBtn.querySelector('.sidebar-collapse-text');
      if (icon) icon.textContent = collapsed ? '»' : '«';
      if (text) text.textContent = collapsed ? '展开' : '折叠';
      collapseBtn.setAttribute('aria-expanded', collapsed ? 'false' : 'true');
      collapseBtn.setAttribute('aria-label', collapsed ? '展开侧边栏' : '折叠侧边栏');
      collapseBtn.title = collapsed ? '展开侧边栏' : '折叠侧边栏';
    }
    if (persist) {
      try { localStorage.setItem('sidebar-collapsed', collapsed ? '1' : '0'); }
      catch (_) { /* Storage can be unavailable in privacy mode. */ }
    }
  }

  function setMobileOpen(open, restoreFocus) {
    var shouldOpen = open && !desktopQuery.matches;
    sb.classList.toggle('is-open', shouldOpen);
    ov.classList.toggle('d-none', !shouldOpen);
    var hidden = !desktopQuery.matches && !shouldOpen;
    sb.toggleAttribute('inert', hidden);
    if (hidden) sb.setAttribute('aria-hidden', 'true');
    else sb.removeAttribute('aria-hidden');
    ov.setAttribute('aria-hidden', shouldOpen ? 'false' : 'true');
    btn.setAttribute('aria-expanded', shouldOpen ? 'true' : 'false');
    btn.setAttribute('aria-label', shouldOpen ? '关闭主导航' : '打开主导航');
    document.body.classList.toggle('sidebar-mobile-open', shouldOpen);
    if (!shouldOpen && restoreFocus) btn.focus();
  }

  setMobileOpen(false, false);
  btn.addEventListener('click', function() {
    setMobileOpen(!sb.classList.contains('is-open'), false);
  });
  ov.addEventListener('click', function() { setMobileOpen(false, true); });
  document.addEventListener('keydown', function(e) {
    if (e.key === 'Escape' && sb.classList.contains('is-open')) {
      setMobileOpen(false, true);
    }
  });

  if (collapseBtn) {
    setCollapsed(readCollapsed(), false);
    collapseBtn.addEventListener('click', function() {
      setCollapsed(!sb.classList.contains('is-collapsed'), true);
    });
  }

  function handleBreakpointChange() { setMobileOpen(false, false); }
  if (desktopQuery.addEventListener) {
    desktopQuery.addEventListener('change', handleBreakpointChange);
  } else {
    desktopQuery.addListener(handleBreakpointChange);
  }

  // Navigation closes the mobile drawer immediately; normal link navigation continues.
  sb.querySelectorAll('a[href]').forEach(function(a) {
    a.addEventListener('click', function() { setMobileOpen(false, false); });
  });

  // 会话登出（仅登录用户渲染；cookie 会话清吊销后跳登录页）。
  var logoutBtn = document.getElementById('sidebar-logout');
  if (logoutBtn) {
    logoutBtn.addEventListener('click', function() {
      logoutBtn.disabled = true;
      fetch('../../v2/auth/logout', {
        method: 'POST',
        headers: { 'X-Requested-With': 'tts-erp' }
      }).finally(function() { location.href = '../../v2/auth/login'; });
    });
  }
})();
"""


def _asset_version(path: Path) -> str:
  try:
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
  except OSError:
    return "0"
  return digest[:8]


def _js_version(filename: str) -> str:
  return _asset_version(_JS_DIR / filename)


def _css_version(filename: str) -> str:
  return _asset_version(_CSS_DIR / filename)


def _sidebar(current_page: str = "") -> _TrustedHtml:
  """Jinja global: sidebar markup + toggle JS, empty without a page slug."""
  if not current_page:
    return _TrustedHtml("")
  return _TrustedHtml(
    _sidebar_html(current_page)
    + "\n  <script>"
    + _SIDEBAR_TOGGLE_JS
    + "</script>"
  )


def _sidebar_css(current_page: str = "") -> _TrustedHtml:
  """Jinja global: sidebar CSS, empty without a page slug."""
  return _TrustedHtml(_SIDEBAR_CSS) if current_page else _TrustedHtml("")


_TEMPLATES_DIR = Path(__file__).resolve().parents[2] / "templates"
class _TrustedHtml(str):
  """HTML generated only from fixed templates and escaped labels."""
  def __html__(self) -> str:
    return self


_templates_env = Environment(
  loader=FileSystemLoader(_TEMPLATES_DIR),
  autoescape=select_autoescape(["html"]),
  keep_trailing_newline=True,
)
_templates_env.globals.update(
  js_version=_js_version,
  css_version=_css_version,
  sidebar=_sidebar,
  sidebar_css=_sidebar_css,
)


def _render_page(template: str, *, current_page: str = "", **context: Any) -> HTMLResponse:
  """Render ``templates/pages/<template>`` (Jinja2) into an HTML response.

  Template context: ``current_page`` (sidebar active slug) plus per-page
  extras. Asset cache-busting is exposed to templates as the
  ``js_version``/``css_version`` globals (SHA-256 content stamps).
  """
  html = _templates_env.get_template(f"pages/{template}").render(
    current_page=current_page, **context
  )
  return HTMLResponse(html)


@router.get("/shops", response_class=HTMLResponse)
def shops_page() -> HTMLResponse:
  """店铺注册台（feature/shop-registration）。

  用途：Chrome 插件同步的店铺没有 API credential，OAuth callback 不会给
  它们建 commerce.shops 行，spu-roi 等 LEFT JOIN shops 的查询关联不上。
  本页让运营人工注册这类店铺（credential_id=NULL, status='registered'）。
  注册只影响查询关联，数据同步不依赖注册状态。

  两段式：注册表单 / 已注册列表（GET /v2/commerce/channel-accounts）。
  行为在 static/js/shops.js；写入端点 POST /v2/admin/shops/register 要 readwrite
  会话（readonly 会话降级只读展示）。注：「待注册候选」section 已于
  2026-09-29 用户拍板下线；后端 GET /v2/admin/shops/unregistered 端点保留。
  """
  return _render_page("shops.html", current_page="shops")





@dataclass(frozen=True, slots=True)
class _SpuProfitabilityPageConfig:
  slug: Literal["spu-roi", "focused-spus"]
  title: str
  profile_id: Literal["standard-roi", "focused-spus"]
  entrypoint_js: Literal["spu-roi.js", "focused-spus.js"]


@router.get("/spu-roi", response_class=HTMLResponse)
def spu_roi_page() -> HTMLResponse:
  """SPU 实际 ROI 看板(账页式,§7 of docs/archive/spu-real-roi-dashboard.md)。

  HTML shell 只做骨架:标题/结余带/工具栏/表格容器/分页;数据与业务计算
  全部消费 GET /v2/analytics/spu-roi(只读,§5.1-1 页面不计算业务数字)。
  布局 = Bootstrap 5.3.8 栅格/工具类 + 手机端适配(见 shell 头注释),行为在
  static/js/spu-roi.js。
  """
  return _render_spu_profitability_page(
    _SpuProfitabilityPageConfig(
      slug="spu-roi",
      title="SPU 实际 ROI",
      profile_id="standard-roi",
      entrypoint_js="spu-roi.js",
    )
  )


@router.get("/focused-spus", response_class=HTMLResponse)
def focused_spus_page() -> HTMLResponse:
  """Persistent shop-scoped focused SPUs rendered by the shared page kernel."""
  return _render_spu_profitability_page(
    _SpuProfitabilityPageConfig(
      slug="focused-spus",
      title="重点关注 SPU",
      profile_id="focused-spus",
      entrypoint_js="focused-spus.js",
    )
  )


@router.get("/manual-costs", response_class=HTMLResponse)
def manual_costs_page() -> HTMLResponse:
  """Manual cost entry workbench (main-image mirror display).

  The HTML shell is a small stub:
  - links to ``/static/vendor/bootstrap.min.css`` (self-hosted, MIT)
  - inline ``<style>`` block for the industrial-console personality
  - links to ``/static/js/console.js`` (shop switcher, tabs, inline filing,
    envelope unwrap for backend pagination, signature-counter population,
    lightbox preview of the SPU's mirrored main image)
  - the JS handles its own /v2/auth/me probe and redirects unauthenticated
    callers to ``/v2/auth/login?next=/v2/pages/manual-costs``

  2026-09-05 page-rework lane: the supplier-reference-photo upload flow
  was removed. The 图片 column now shows the TikTok main image mirrored
  into local MinIO (``image_url`` from the backend, fallback icon when the
  mirror hasn't finished); cost currency is fixed to CNY.
  """
  return _render_page("manual-costs.html", current_page="manual-costs")


# Marker for the legacy token-paste UI — kept as a comment so future
# agents know NOT to reintroduce it. The page now relies on session-cookie
# auth (see docs/archive/browser-login-design.md).
#
# NOT TO ADD BACK: <details>API token (paste once; stored in localStorage)</details>




# SPU 实际 ROI 看板(账页式)HTML shell — 结构见 docs/archive/spu-real-roi-dashboard.md §7。
# 只读:JS 消费 GET /v2/analytics/spu-roi;业务数字全在服务端算好(§5.1-1)。
# 页面布局 2026-09-29 统一到 Bootstrap 5.3.8(自托管 static/vendor/bootstrap.min.css):
#   - 页头、结余分组、工具栏、分页、页脚均由 container/row/col 与断点工具类驱动；
#   - 主表和钻取表统一使用 table-responsive，保留全部业务列，用横向滚动代替手写断点隐藏；
#   - 钻取 summary 与 P&L 用 row-cols-* 随断点切换列数，tab 用 nav-tabs + overflow-x-auto；
#   - 自定义 CSS 只保留 warm-paper 视觉 token、业务标色、sticky 首列、tooltip/lightbox 等行为皮肤，
#     不再包含 max-width/min-width media query 或 nth-child 响应式规则。



def _render_spu_profitability_page(
  config: _SpuProfitabilityPageConfig,
) -> HTMLResponse:
  return _render_page(
    "spu-profitability.html",
    current_page=config.slug,
    title=config.title,
    profile_id=config.profile_id,
    entrypoint_js=config.entrypoint_js,
  )


@router.get("/dashboard", response_class=HTMLResponse)
def dashboard_page() -> HTMLResponse:
  """主页仪表板 — 快速导航 + 店铺概览 + 数据摘要。

  用途：运营入口页，提供：
  1. 快速跳转到三个现有页面（采购工作台/ROI 看板/店铺注册）
  2. 已注册店铺列表（精简版）
  3. 关键业务指标摘要（待录成本/SPU 总数/店铺数量）
  4. 系统状态指示

  设计风格：延续工业操作台视觉语言（暖纸/等宽/细线），
  但更轻量 — 卡片式布局，适合快速扫描和点击。
  """
  return _render_page("dashboard.html", current_page="dashboard")





@router.get("/intercept-configs", response_class=HTMLResponse)
def intercept_configs_page() -> HTMLResponse:
  """拦截配置管理页面。

  功能：配置列表、新增/编辑弹窗、批量操作、导入导出、筛选分页。
  行为在 static/js/intercept-configs.js。
  """
  return _render_page("intercept-configs.html", current_page="intercept-configs")


@router.get("/intercept-requests", response_class=HTMLResponse)
def intercept_requests_page() -> HTMLResponse:
  """拦截记录查询页面。

  功能：记录列表、详情弹窗、筛选（域名/路径/状态码/白名单/时间）、分页。
  行为在 static/js/intercept-requests.js。
  """
  return _render_page("intercept-requests.html", current_page="intercept-requests")


@router.get("/intercept-stats", response_class=HTMLResponse)
def intercept_stats_page() -> HTMLResponse:
  """拦截统计概览页面。

  功能：总览统计卡片、按域名/方法/状态码分布、最近 7 天每日趋势。
  行为在 static/js/intercept-stats.js。
  """
  return _render_page("intercept-stats.html", current_page="intercept-stats")










# ── enum-map 管理页面 ─────────────────────────────────────────────────




@router.get("/enum-map", response_class=HTMLResponse)
def enum_map_page() -> HTMLResponse:
  """枚举映射管理页面。CRUD 管理 config.enum_map 枚举翻译。"""
  return _render_page("enum-map.html", current_page="enum-map")


@router.get("/runtime-configs", response_class=HTMLResponse)
def runtime_configs_page() -> HTMLResponse:
  """Versioned runtime configuration and encrypted secret-reference console."""
  return _render_page("runtime-configs.html", current_page="runtime-configs")


@router.get(
  "/sync-jobs", response_class=HTMLResponse, dependencies=[Depends(_require_readwrite)]
)
def sync_jobs_page() -> HTMLResponse:
  """定时任务管理页：启停周期调度，或手动触发系统/店铺级任务。"""
  return _render_page("sync-jobs.html", current_page="sync-jobs")


@router.get("/video-publish", response_class=HTMLResponse)
def video_publish_page() -> HTMLResponse:
  """Serial TikTok video publishing workbench."""
  return _render_page("video-publish.html", current_page="video-publish")


@router.get("/users", response_class=HTMLResponse)
def users_page() -> HTMLResponse:
  """用户管理（设计 §9）：用户 + 角色权限两页签。

  入口受 ``page:users`` 权限点控制（access 层按路由判定）；页面行为在
  static/js/users.js，数据 API 为 /v2/users* 与 /v2/roles*。
  """
  return _render_page("users.html", current_page="users")






