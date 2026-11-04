"""Authenticated page routes and retained legacy-document rendering helpers.

Registered pages use the manifest-backed React renderer; HTTP route and role
gates remain here. Both profitability URLs load one shared React module.
Their former Jinja shell and global DOM-owning scripts are retired.

Legacy helpers remain for other retained documents and their tests. They own
Jinja context, shared navigation and independent CSS/JS content hashes only.
Frontend assets and API URLs respect the server-provided root_path.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any, cast

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from jinja2 import Environment, FileSystemLoader, select_autoescape
from markupsafe import escape

from tts_erp_v2.access._context import user_label_var, user_pages_var
from tts_erp_v2.accounts.pages import PAGES
from tts_erp_v2.api.deps import require_role_at_least
from tts_erp_v2.api.v2 import frontend_pages

router = APIRouter(prefix="/v2/pages", tags=["pages"])

# FastAPI requires a Request annotation, while legacy tests call renderers directly.
_DIRECT_RENDER_REQUEST = cast(Request, None)


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
    /* Shared navigation visuals live in navigation.css, also imported by React. */
    body { --sidebar-width: 248px; margin-left: var(--sidebar-width); }
    body.sidebar-collapsed { margin-left: 0; }
    body.sidebar-mobile-open { overflow: hidden; }
    #sidebar.sidebar { position: fixed; inset: 0 auto 0 0; z-index: 30; max-height: 100dvh; overflow-y: auto; }
    #sidebar-overlay { position: fixed; inset: 0; z-index: 40; background: #172f2980; }
    #sidebar-overlay[aria-hidden="true"] { display: none; }
    .erp-legacy-toggle[aria-expanded="false"] .sidebar-collapse-chevron { transform: translateX(24px) scaleX(-1); }
    @media (min-width: 1024px) {
      #sidebar.sidebar.is-collapsed, #sidebar-toggle, #sidebar-close, #sidebar-overlay { display: none; }
    }
    @media (max-width: 1023.98px) {
      body, body.sidebar-collapsed { margin-left: 0; }
      #sidebar.sidebar { z-index: 50; width: min(248px, calc(100vw - 48px)); transform: translateX(-100%); }
      #sidebar.sidebar.is-open { transform: translateX(0); }
      #sidebar-collapse { display: none; }
      #sidebar-close { position: absolute; top: 8px; right: 8px; color: var(--navigation-foreground); }
    }
"""


def _sidebar_html(current_page: str) -> str:
  """Use the same brand, menu slots and visual owner as the React workspace."""
  allowed = user_pages_var.get()
  pages = [page for page in PAGES if allowed is None or page.permission_code in allowed]
  groups: dict[str, list[str]] = {}
  for page in pages:
    active = ' aria-current="page"' if page.page_id == current_page else ''
    groups.setdefault(page.group, []).append(
      f'<li><a href="../../v2/pages/{escape(page.page_id)}" class="erp-nav-link"'
      f' title="{escape(page.label)}"{active}>'
      '<span class="erp-nav-indicator" aria-hidden="true"></span>'
      f'<span>{escape(page.label)}</span></a></li>'
    )
  navigation = ''.join(
    '<section class="erp-nav-section">'
    f'<h2 class="erp-nav-group">{escape(group)}</h2>'
    f'<ul class="erp-nav-list">{"".join(links)}</ul></section>'
    for group, links in groups.items()
  )
  current = next((page for page in pages if page.page_id == current_page), None)
  title = escape(current.label) if current else '运营工作台'
  context = f' / {escape(current.group)}' if current else ''
  user_label = user_label_var.get()
  session = (
    '<div class="erp-session-actions">'
    f'<span>{escape(user_label)}</span>'
    '<button type="button" id="sidebar-logout">退出登录</button></div>'
  ) if user_label else ''
  icon = (
    '<svg aria-hidden="true" viewBox="0 0 24 24" fill="none" stroke="currentColor"'
    ' stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round">'
    '<rect x="3" y="4" width="18" height="16" rx="2" />'
    '<path d="M9 4v16" /><path class="sidebar-collapse-chevron" d="m16 9-3 3 3 3" /></svg>'
  )
  return f"""<nav class="sidebar erp-sidebar" id="sidebar" aria-label="主导航">
    <button type="button" id="sidebar-close" class="erp-navigation-toggle erp-legacy-toggle" aria-label="关闭主导航"><svg aria-hidden="true" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"><path d="m6 6 12 12M6 18 18 6" /></svg></button>
    <div class="erp-brand" aria-label="TTS ERP">
      <svg aria-hidden="true" width="30" height="30" viewBox="0 0 34 34" fill="none"><path d="M5 8h24M17 8v21M5 17h7M24 17h5" stroke="currentColor" stroke-width="3.5" /></svg>
      <span>tts<span class="erp-brand-suffix"> / erp</span></span>
    </div>
    <div class="erp-navigation">{navigation}</div>
    <p class="erp-sidebar-note">SHOP OPERATIONS</p>
  </nav>
  <div id="sidebar-overlay" aria-hidden="true"></div>
  <header class="erp-header">
    <button type="button" id="sidebar-collapse" class="erp-navigation-toggle erp-legacy-toggle" aria-label="折叠侧边栏" title="折叠侧边栏" aria-controls="sidebar" aria-expanded="true">{icon}</button>
    <button type="button" id="sidebar-toggle" class="erp-navigation-toggle erp-legacy-toggle" aria-label="打开主导航" title="打开主导航" aria-controls="sidebar" aria-expanded="false">{icon}</button>
    <div class="erp-header-titles"><p class="erp-header-context">运营工作台{context}</p><h1 class="erp-header-title">{title}</h1></div>
    {session}
  </header>"""


_SIDEBAR_TOGGLE_JS = """
(function() {
  var btn = document.getElementById('sidebar-toggle');
  var sb = document.getElementById('sidebar');
  var ov = document.getElementById('sidebar-overlay');
  var collapseBtn = document.getElementById('sidebar-collapse');
  var closeBtn = document.getElementById('sidebar-close');
  var desktopQuery = window.matchMedia('(min-width: 1024px)');
  if (!btn || !sb || !ov) return;

  function readCollapsed() {
    try { return localStorage.getItem('sidebar-collapsed') === '1'; }
    catch (_) { return false; }
  }

  function setCollapsed(collapsed, persist) {
    sb.classList.toggle('is-collapsed', collapsed);
    document.body.classList.toggle('sidebar-collapsed', collapsed);
    if (collapseBtn) {
      collapseBtn.setAttribute('aria-expanded', collapsed ? 'false' : 'true');
      collapseBtn.setAttribute('aria-label', collapsed ? '展开侧边栏' : '折叠侧边栏');
      collapseBtn.title = collapsed ? '展开侧边栏' : '折叠侧边栏';
    }
    setMobileOpen(sb.classList.contains('is-open'), false);
    if (persist) {
      try { localStorage.setItem('sidebar-collapsed', collapsed ? '1' : '0'); }
      catch (_) { /* Storage can be unavailable in privacy mode. */ }
    }
  }

  function setMobileOpen(open, restoreFocus) {
    var shouldOpen = open && !desktopQuery.matches;
    sb.classList.toggle('is-open', shouldOpen);
    var hidden = desktopQuery.matches ? sb.classList.contains('is-collapsed') : !shouldOpen;
    sb.toggleAttribute('inert', hidden);
    if (hidden) sb.setAttribute('aria-hidden', 'true');
    else sb.removeAttribute('aria-hidden');
    ov.setAttribute('aria-hidden', shouldOpen ? 'false' : 'true');
    btn.setAttribute('aria-expanded', shouldOpen ? 'true' : 'false');
    btn.setAttribute('aria-label', shouldOpen ? '关闭主导航' : '打开主导航');
    btn.title = shouldOpen ? '关闭主导航' : '打开主导航';
    document.body.classList.toggle('sidebar-mobile-open', shouldOpen);
    if (!shouldOpen && restoreFocus) btn.focus();
  }

  setMobileOpen(false, false);
  btn.addEventListener('click', function() {
    setMobileOpen(!sb.classList.contains('is-open'), false);
    if (sb.classList.contains('is-open') && closeBtn) closeBtn.focus();
  });
  if (closeBtn) closeBtn.addEventListener('click', function() { setMobileOpen(false, true); });
  ov.addEventListener('click', function() { setMobileOpen(false, true); });
  document.addEventListener('keydown', function(e) {
    if (e.key === 'Escape' && sb.classList.contains('is-open')) {
      setMobileOpen(false, true);
    }
    if (e.key === 'Tab' && sb.classList.contains('is-open')) {
      var items = sb.querySelectorAll('button, a[href]');
      var first = items[0];
      var last = items[items.length - 1];
      if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
      else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
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
  return _TrustedHtml(
    '@import url("../../static/css/navigation.css?v='
    + _css_version("navigation.css") + '");\n' + _SIDEBAR_CSS
  ) if current_page else _TrustedHtml("")


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
def shops_page(request: Request = _DIRECT_RENDER_REQUEST) -> HTMLResponse:
  """店铺注册台（feature/shop-registration）。

  用途：Chrome 插件同步的店铺没有 API credential，OAuth callback 不会给
  它们建 commerce.shops 行，spu-roi 等 LEFT JOIN shops 的查询关联不上。
  本页让运营人工注册这类店铺（credential_id=NULL, status='registered'）。
  注册只影响查询关联，数据同步不依赖注册状态。

  两段式：注册表单 / 已注册列表（GET /v2/commerce/channel-accounts）。
  已迁移行为在 frontend/src/pages/shops/；写入端点 POST /v2/admin/shops/register 要 readwrite
  会话（readonly 会话降级只读展示）。注：「待注册候选」section 已于
  2026-10-31：候选端点移除；插件直接登记 commerce.shops 元数据。
  """
  if "shops" in frontend_pages.MIGRATED_PAGE_IDS:
    if request is None:
      raise RuntimeError("Migrated pages require a request")
    return frontend_pages.render_frontend_page(request, "shops")
  return _render_page("shops.html", current_page="shops")







@router.get("/spu-roi", response_class=HTMLResponse)
def spu_roi_page(request: Request) -> HTMLResponse:
  """Shared React profitability page; all business values remain API-owned."""
  return frontend_pages.render_frontend_page(request, "spu-roi")


@router.get("/focused-spus", response_class=HTMLResponse)
def focused_spus_page(request: Request) -> HTMLResponse:
  """Shared React profitability page; all business values remain API-owned."""
  return frontend_pages.render_frontend_page(request, "focused-spus")


@router.get("/spu-profit-deterioration", response_class=HTMLResponse)
def spu_profit_deterioration_page(request: Request = _DIRECT_RENDER_REQUEST) -> HTMLResponse:
  """SPU 盈利变化的只读 7/3 日对比；日期、事实与可用性由既有 API 决定。"""
  if "spu-profit-deterioration" in frontend_pages.MIGRATED_PAGE_IDS:
    if request is None:
      raise RuntimeError("Migrated pages require a request")
    return frontend_pages.render_frontend_page(request, "spu-profit-deterioration")
  return _render_page(
    "spu-profit-deterioration.html", current_page="spu-profit-deterioration"
  )


@router.get("/manual-costs", response_class=HTMLResponse)
def manual_costs_page(request: Request = _DIRECT_RENDER_REQUEST) -> HTMLResponse:
  """Manual cost workbench; existing APIs own identities, currency and persistence."""
  if "manual-costs" in frontend_pages.MIGRATED_PAGE_IDS:
    if request is None:
      raise RuntimeError("Migrated pages require a request")
    return frontend_pages.render_frontend_page(request, "manual-costs")
  return _render_page("manual-costs.html", current_page="manual-costs")


# Marker for the legacy token-paste UI — kept as a comment so future
# agents know NOT to reintroduce it. The page now relies on session-cookie
# auth (see docs/archive/browser-login-design.md).
#
# NOT TO ADD BACK: <details>API token (paste once; stored in localStorage)</details>




@router.get("/dashboard", response_class=HTMLResponse)
def dashboard_page(request: Request = _DIRECT_RENDER_REQUEST) -> HTMLResponse:
  """主页仪表板 — 快速导航 + 店铺概览 + 数据摘要。

  用途：运营入口页，提供：
  1. 快速跳转到三个现有页面（采购工作台/ROI 看板/店铺注册）
  2. 已注册店铺列表（精简版）
  3. 关键业务指标摘要（待录成本/SPU 总数/店铺数量）
  4. 系统状态指示

  设计风格：延续工业操作台视觉语言（暖纸/等宽/细线），
  但更轻量 — 卡片式布局，适合快速扫描和点击。
  """
  if "dashboard" in frontend_pages.MIGRATED_PAGE_IDS:
    if request is None:
      raise RuntimeError("Migrated pages require a request")
    return frontend_pages.render_frontend_page(request, "dashboard")
  return _render_page("dashboard.html", current_page="dashboard")





@router.get("/intercept-configs", response_class=HTMLResponse)
def intercept_configs_page(request: Request = _DIRECT_RENDER_REQUEST) -> HTMLResponse:
  """拦截配置管理页面。

  功能：配置列表、新增/编辑弹窗、批量操作、导入导出、筛选分页。
  行为在 static/js/intercept-configs.js。
  """
  if "intercept-configs" in frontend_pages.MIGRATED_PAGE_IDS:
    if request is None:
      raise RuntimeError("Migrated pages require a request")
    return frontend_pages.render_frontend_page(request, "intercept-configs")
  return _render_page("intercept-configs.html", current_page="intercept-configs")


@router.get("/intercept-requests", response_class=HTMLResponse)
def intercept_requests_page(request: Request = _DIRECT_RENDER_REQUEST) -> HTMLResponse:
  """拦截记录查询页面。

  功能：记录列表、详情弹窗、筛选（域名/路径/状态码/白名单/时间）、分页。
  行为在 static/js/intercept-requests.js。
  """
  if "intercept-requests" in frontend_pages.MIGRATED_PAGE_IDS:
    if request is None:
      raise RuntimeError("Migrated pages require a request")
    return frontend_pages.render_frontend_page(request, "intercept-requests")
  return _render_page("intercept-requests.html", current_page="intercept-requests")


@router.get("/intercept-stats", response_class=HTMLResponse)
def intercept_stats_page(request: Request = _DIRECT_RENDER_REQUEST) -> HTMLResponse:
  """拦截统计概览页面。

  功能：总览统计卡片、按域名/方法/状态码分布、最近 7 天每日趋势。
  已迁移内容在 frontend/src/pages/intercept-stats/。
  """
  if "intercept-stats" in frontend_pages.MIGRATED_PAGE_IDS:
    if request is None:
      raise RuntimeError("Migrated pages require a request")
    return frontend_pages.render_frontend_page(request, "intercept-stats")
  return _render_page("intercept-stats.html", current_page="intercept-stats")










# ── enum-map 管理页面 ─────────────────────────────────────────────────




@router.get("/enum-map", response_class=HTMLResponse)
def enum_map_page(request: Request = _DIRECT_RENDER_REQUEST) -> HTMLResponse:
  """枚举映射管理页面。CRUD 管理 config.enum_map 枚举翻译。"""
  if "enum-map" in frontend_pages.MIGRATED_PAGE_IDS:
    if request is None:
      raise RuntimeError("Migrated pages require a request")
    return frontend_pages.render_frontend_page(request, "enum-map")
  return _render_page("enum-map.html", current_page="enum-map")


@router.get("/runtime-configs", response_class=HTMLResponse)
def runtime_configs_page(request: Request = _DIRECT_RENDER_REQUEST) -> HTMLResponse:
  """Versioned runtime configuration and encrypted secret-reference console."""
  if "runtime-configs" in frontend_pages.MIGRATED_PAGE_IDS:
    if request is None:
      raise RuntimeError("Migrated pages require a request")
    return frontend_pages.render_frontend_page(request, "runtime-configs")
  return _render_page("runtime-configs.html", current_page="runtime-configs")


@router.get(
  "/sync-jobs", response_class=HTMLResponse, dependencies=[Depends(_require_readwrite)]
)
def sync_jobs_page(request: Request = _DIRECT_RENDER_REQUEST) -> HTMLResponse:
  """定时任务管理页：启停周期调度，或手动触发系统/店铺级任务。"""
  if "sync-jobs" in frontend_pages.MIGRATED_PAGE_IDS:
    if request is None:
      raise RuntimeError("Migrated pages require a request")
    return frontend_pages.render_frontend_page(request, "sync-jobs")
  return _render_page("sync-jobs.html", current_page="sync-jobs")


@router.get("/video-publish", response_class=HTMLResponse)
def video_publish_page(request: Request = _DIRECT_RENDER_REQUEST) -> HTMLResponse:
  """Serial TikTok video publishing workbench."""
  if "video-publish" in frontend_pages.MIGRATED_PAGE_IDS:
    if request is None:
      raise RuntimeError("Migrated pages require a request")
    return frontend_pages.render_frontend_page(request, "video-publish")
  return _render_page("video-publish.html", current_page="video-publish")


@router.get("/users", response_class=HTMLResponse)
def users_page(request: Request = _DIRECT_RENDER_REQUEST) -> HTMLResponse:
  """用户管理（设计 §9）：用户 + 角色权限两页签。

  入口受 ``page:users`` 权限点控制（access 层按路由判定）；页面行为在
  static/js/users.js，数据 API 为 /v2/users* 与 /v2/roles*。
  """
  if "users" in frontend_pages.MIGRATED_PAGE_IDS:
    if request is None:
      raise RuntimeError("Migrated pages require a request")
    return frontend_pages.render_frontend_page(request, "users")
  return _render_page("users.html", current_page="users")


@router.get("/media-assets", response_class=HTMLResponse)
def media_assets_page(request: Request = _DIRECT_RENDER_REQUEST) -> HTMLResponse:
  """Global media asset library: upload, browse, preview and delete media."""
  if "media-assets" in frontend_pages.MIGRATED_PAGE_IDS:
    if request is None:
      raise RuntimeError("Migrated pages require a request")
    return frontend_pages.render_frontend_page(request, "media-assets")
  return _render_page("media-assets.html", current_page="media-assets")


@router.get("/digital-humans", response_class=HTMLResponse)
def digital_humans_page(request: Request = _DIRECT_RENDER_REQUEST) -> HTMLResponse:
  """Digital human (persona) console: manage personas and their assets."""
  if "digital-humans" in frontend_pages.MIGRATED_PAGE_IDS:
    if request is None:
      raise RuntimeError("Migrated pages require a request")
    return frontend_pages.render_frontend_page(request, "digital-humans")
  return _render_page("digital-humans.html", current_page="digital-humans")






