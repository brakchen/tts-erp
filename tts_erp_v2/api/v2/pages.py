"""/v2/pages/* — server-rendered HTML pages (no SPA framework).

Both served pages render static HTML shells styled with **Bootstrap 5.3.8**,
self-hosted at ``/static/vendor/bootstrap.min.css`` (MIT — see
``static/vendor/NOTICE.md``); no CDN links. The manual-costs page puts
behaviour in ``/static/js/console.js``; the SPU-ROI page in
``/static/js/spu-roi.js``. Layout is Bootstrap grid + utilities with the
inline warm-paper skin; both pages are mobile-adapted (the ROI shell has
breakpoint column pruning + sticky first column, see the ``_SPU_ROI_PAGE_HTML``
header comment).

Asset paths are RELATIVE (``../../static/…``) so the page works both on
``127.0.0.1:9877`` directly and behind the NGINX ``/tts`` prefix
(2026-08-31: absolute ``/static/…`` links 404'd behind the prefix).

Auth classification: the page is ``readonly``-equivalent for the GET
(handler does no DB writes). The page JS calls the write endpoint
``/v2/reporting/manual-costs`` — which requires a readwrite or admin
session via the ``/v2/auth/login`` cookie flow. (2026-09-05 page-rework
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
from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

router = APIRouter(prefix="/v2/pages", tags=["pages"])


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
  """Return the shared sidebar with the current page marked as active."""
  pages = [
    ("dashboard", "台", "控制台", "__group__"),
    ("manual-costs", "采", "采购工作台", "运营"),
    ("spu-roi", "益", "SPU ROI", "运营"),
    ("shops", "店", "店铺注册", "店铺"),
    ("enum-map", "映", "枚举映射", "数据"),
    ("intercept-configs", "配", "拦截配置", "拦截"),
    ("intercept-requests", "录", "拦截记录", "拦截"),
    ("intercept-stats", "计", "拦截统计", "拦截"),
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


def _page(html: str, *, current_page: str = "") -> HTMLResponse:
  """Render a page template, stamping the JS cache-bust versions.

  *current_page* is the sidebar slug for the active link
  (e.g. ``"manual-costs"``, ``"spu-roi"``). When non-empty the shared
  sidebar navigation is injected into the page.
  """
  html = (
    html.replace("__JSV_CONSOLE__", _js_version("console.js"))
    .replace("__CSSV_SPU_ROI__", _css_version("spu-roi.css"))
    .replace("__JSV_SPU_ROI__", _js_version("spu-roi.js"))
    .replace("__JSV_SHOPS__", _js_version("shops.js"))
    .replace("__JSV_DASHBOARD__", _js_version("dashboard.js"))
    .replace("__JSV_INTERCEPT_CONFIGS__", _js_version("intercept-configs.js"))
    .replace("__JSV_INTERCEPT_REQUESTS__", _js_version("intercept-requests.js"))
    .replace("__JSV_INTERCEPT_STATS__", _js_version("intercept-stats.js"))
  )
  if current_page:
    sidebar_html = _sidebar_html(current_page)
    # Append shared CSS so page-level ``margin`` shorthands cannot erase the shell offset.
    html = html.replace("</style>", _SIDEBAR_CSS + "\n  </style>", 1)
    # Inject sidebar HTML + toggle JS after <body>
    html = html.replace(
      "<body>",
      "<body>\n  " + sidebar_html + "\n  <script>" + _SIDEBAR_TOGGLE_JS + "</script>",
      1,
    )
    # Remove the per-page "← 首页" home link (sidebar replaces it)
    for _home_link in [
      '<a href="../../v2/pages/dashboard" class="op-home-link">← 首页</a>\n        ',
      '<a href="../../v2/pages/dashboard" class="op-home-link">← 首页</a>\n      ',
      '<a href="../../v2/pages/dashboard" class="op-home-link">← 首页</a>',
    ]:
      html = html.replace(_home_link, "")
  return HTMLResponse(html)


@router.get("/shops", response_class=HTMLResponse)
def shops_page() -> HTMLResponse:
  """店铺注册台（feature/shop-registration）。

  用途：Chrome 插件同步的店铺没有 API credential，OAuth callback 不会给
  它们建 commerce.shops 行，spu-roi 等 LEFT JOIN shops 的查询关联不上。
  本页让运营人工注册这类店铺（credential_id=NULL, status='registered'）。
  注册只影响查询关联，数据同步不依赖注册状态。

  三段式：注册表单 / 待注册候选（GET /v2/admin/shops/unregistered）/
  已注册列表（GET /v2/commerce/channel-accounts）。行为在
  static/js/shops.js；写入端点 POST /v2/admin/shops/register 要 readwrite
  会话（readonly 会话降级只读展示）。
  """
  return _page(_SHOPS_PAGE_HTML, current_page="shops")


_SHOPS_PAGE_HTML = """<!doctype html>
<html lang="zh-Hans">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>店铺注册 · tts-erp</title>
  <!-- Relative path: resolves to /static/... locally and /tts/static/...
       behind the NGINX prefix. Do not make this absolute. -->
  <link rel="stylesheet" href="../../static/vendor/bootstrap.min.css">
  <style>
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
      --mono: 'JetBrains Mono', 'SF Mono', 'Cascadia Mono', Consolas, ui-monospace, monospace;
      --sans: 'Inter', 'Noto Sans SC', 'Source Han Sans SC', 'PingFang SC', 'Hiragino Sans GB', 'Microsoft YaHei', system-ui, -apple-system, sans-serif;
      --serif: 'Noto Serif SC', 'Source Han Serif SC', Georgia, ui-serif, serif;
    }
    * { box-sizing: border-box; }
    html, body {
      background: var(--paper);
      color: var(--ink);
      font-family: var(--sans);
      font-size: 16px;
      line-height: 1.6;
      margin: 0;
      -webkit-font-smoothing: antialiased;
      -moz-osx-font-smoothing: grayscale;
      text-rendering: optimizeLegibility;
    }
    a { color: var(--accent); text-decoration: none; }
    a:hover { color: var(--accent-deep); }
    .op-header { border-bottom: 1px solid var(--rule); padding: 18px 28px 14px; background: var(--paper); }
    .op-header-row { display: flex; align-items: flex-end; justify-content: space-between; gap: 24px; flex-wrap: wrap; }
    .op-header-titles { display: flex; flex-direction: column; gap: 2px; }
    .op-eyebrow { font-family: var(--mono); font-size: 13px; letter-spacing: 0.14em; text-transform: uppercase; color: var(--muted); }
    .op-title { font-family: var(--serif); font-weight: 600; font-size: 26px; margin: 0; letter-spacing: -0.01em; }
    .op-identity { font-family: var(--mono); font-size: 14px; color: var(--muted); }
    .page-main { max-width: 1280px; margin: 0 auto; padding: 24px 28px 64px; }
    .op-section { background: var(--paper); border: 1px solid var(--rule); border-radius: 0; padding: 20px 24px; margin-bottom: 24px; }
    .op-section-header { display: flex; align-items: baseline; justify-content: space-between; gap: 16px; margin-bottom: 16px; padding-bottom: 12px; border-bottom: 1px solid var(--rule-soft); flex-wrap: wrap; }
    .op-section-title { font-family: var(--serif); font-weight: 600; font-size: 18px; margin: 0; color: var(--ink); }
    .op-section-meta { font-family: var(--mono); font-size: 13px; color: var(--muted); }
    .form-grid { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 14px; align-items: end; }
    .form-actions { display: flex; justify-content: flex-end; align-items: end; }
    .form-field label { display: block; font-family: var(--mono); font-size: 12px; letter-spacing: 0.08em; text-transform: uppercase; color: var(--muted); margin-bottom: 6px; }
    .form-field input { width: 100%; font-family: var(--sans); font-size: 14px; background: transparent; border: 0; border-bottom: 1px solid var(--rule); padding: 7px 2px; color: var(--ink); border-radius: 0; }
    .form-field input:focus { outline: 0; border-bottom-color: var(--accent); }
    .form-field input::placeholder { color: var(--rule); }
    .form-field.mono input { font-family: var(--mono); }
    .form-hint { margin-top: 8px; font-family: var(--mono); font-size: 12px; color: var(--muted); }
    .btn-primary { font-family: var(--mono); font-size: 12px; font-weight: 600; letter-spacing: 0.14em; text-transform: uppercase; padding: 8px 18px; background: var(--ink); color: var(--paper); border: 0; cursor: pointer; border-radius: 0; transition: background 120ms ease; }
    .btn-primary:hover { background: var(--accent); }
    .btn-primary:disabled { background: var(--rule); color: var(--paper); cursor: not-allowed; }
    .btn-secondary { font-family: var(--mono); font-size: 12px; letter-spacing: 0.08em; text-transform: uppercase; padding: 7px 14px; background: transparent; color: var(--ink); border: 1px solid var(--rule); cursor: pointer; border-radius: 0; transition: border-color 120ms ease; }
    .btn-secondary:hover { border-color: var(--accent); }
    .op-table { width: 100%; border-collapse: collapse; font-family: var(--sans); font-size: 14px; background: var(--paper); }
    .op-th { text-align: left; font-family: var(--mono); font-size: 12px; letter-spacing: 0.14em; text-transform: uppercase; color: var(--muted); font-weight: 500; padding: 12px 12px; border-bottom: 1px solid var(--rule); white-space: nowrap; }
    .op-table td { padding: 14px 12px; border-bottom: 1px solid var(--rule-soft); vertical-align: middle; }
    .op-table tbody tr:hover { background: var(--paper-deep); }
    .op-table tbody tr:last-child td { border-bottom: 0; }
    .mono { font-family: var(--mono); font-size: 13px; }
    .badge { display: inline-block; font-family: var(--mono); font-size: 11px; font-weight: 500; letter-spacing: 0.06em; padding: 3px 8px; border-radius: 0; }
    .badge-api { background: var(--ok); color: #fff; }
    .badge-plugin { background: #8a6d1a; color: #fff; }
    .op-empty, .op-error { text-align: center; padding: 48px 20px; color: var(--muted); font-family: var(--mono); font-size: 13px; letter-spacing: 0.08em; }
    .op-error { color: var(--danger); }
    .op-note { padding: 12px 16px; border: 1px solid var(--rule); background: var(--paper-deep); color: var(--ink-soft); font-size: 13px; }
    .op-error-note { padding: 12px 16px; border: 1px solid var(--danger); background: var(--paper); color: var(--danger); font-size: 13px; }
    .op-hidden { display: none !important; }
    .op-dialog { width: min(520px, calc(100vw - 32px)); border: 1px solid var(--rule); border-radius: 0; background: var(--paper); color: var(--ink); padding: 22px 24px; }
    .op-dialog::backdrop { background: rgba(27, 24, 20, 0.48); }
    .op-dialog h2 { margin: 0 0 6px; font-family: var(--serif); font-size: 20px; }
    .op-dialog-actions { display: flex; justify-content: flex-end; gap: 10px; margin-top: 20px; }
    .op-dialog .form-field { margin-top: 14px; }
    @media (max-width: 720px) {
      .form-grid { grid-template-columns: 1fr; }
      .op-header { padding: 14px 16px 10px; }
      .page-main { padding: 16px 16px 48px; }
      .op-section { padding: 16px; }
    }
    .op-home-link { font-family: var(--mono); font-size: 12px; letter-spacing: 0.08em; text-transform: uppercase; color: var(--muted); text-decoration: none; padding: 4px 8px; border: 1px solid var(--rule); transition: border-color 120ms ease; display: inline-block; }
    .op-home-link:hover { border-color: var(--accent); color: var(--accent); }
  </style>
</head>
<body>
  <header class="op-header">
    <div class="op-header-row">
      <div class="op-header-titles">
        <a href="../../v2/pages/dashboard" class="op-home-link">← 首页</a>
        <span class="op-eyebrow">TikTok Shop · Operations</span>
        <h1 class="op-title">店铺注册</h1>
      </div>
      <div class="op-identity" id="ops-identity"></div>
    </div>
  </header>

  <main class="page-main">
    <div id="auth-note" class="op-note op-hidden"></div>
    <div id="err" class="op-error-note op-hidden"></div>

    <section class="op-section">
      <div class="op-section-header">
        <h2 class="op-section-title">注册店铺</h2>
        <span class="op-section-meta">插件同步店铺人工登记 — 仅影响查询关联，不影响数据同步</span>
      </div>
      <form id="register-form" class="form-grid">
        <div class="form-field mono">
          <label for="f-shop-id">shop_id *</label>
          <input id="f-shop-id" type="text" required pattern="[0-9]+" placeholder="19 位数字" autocomplete="off">
        </div>
        <div class="form-field">
          <label for="f-name">店铺名称</label>
          <input id="f-name" type="text" placeholder="选填，留空时回填为"未命名"" autocomplete="off">
        </div>
        <div class="form-field">
          <label for="f-region">区域</label>
          <input id="f-region" type="text" placeholder="VN" autocomplete="off">
        </div>
        <div class="form-field">
          <label for="f-opened">开店日期</label>
          <input id="f-opened" type="date" autocomplete="off">
        </div>
        <div class="form-field mono">
          <label for="f-service-id">service_id</label>
          <input id="f-service-id" type="text" placeholder="Partner Center Service ID" autocomplete="off">
        </div>
        <div class="form-field mono">
          <label for="f-app-key">App Key</label>
          <input id="f-app-key" type="text" placeholder="与 service_id 配套" autocomplete="off">
        </div>
        <div class="form-field mono">
          <label for="f-app-secret">App Secret</label>
          <input id="f-app-secret" type="password" placeholder="仅加密存储，不回显" autocomplete="new-password">
        </div>
        <div class="form-field form-actions">
          <button type="submit" class="btn-primary">注册</button>
        </div>
      </form>
      <div class="form-hint">重复注册幂等：只补填仍为空的字段，不会覆盖已有 credential / 状态。App Key 与 App Secret 必须成对填写，保存应用凭证需要 admin。</div>
    </section>

    <section class="op-section">
      <div class="op-section-header">
        <h2 class="op-section-title">待注册候选</h2>
        <span class="op-section-meta" id="cand-count">0 条</span>
      </div>
      <table class="op-table">
        <thead><tr><th class="op-th">shop_id</th><th class="op-th">数据来源</th><th class="op-th" style="width: 120px;"></th></tr></thead>
        <tbody id="cand-body"><tr><td colspan="3" class="op-empty">加载中…</td></tr></tbody>
      </table>
    </section>

    <section class="op-section">
      <div class="op-section-header">
        <h2 class="op-section-title">已注册店铺</h2>
        <span class="op-section-meta" id="shop-count">0 条</span>
      </div>
      <table class="op-table">
        <thead><tr>
          <th class="op-th">shop_id</th>
          <th class="op-th">名称</th>
          <th class="op-th">区域</th>
          <th class="op-th">service_id</th>
          <th class="op-th">App 凭证</th>
          <th class="op-th">开店日期</th>
          <th class="op-th">同步方式</th>
          <th class="op-th" style="width:120px;">操作</th>
        </tr></thead>
        <tbody id="shop-body"><tr><td colspan="8" class="op-empty">加载中…</td></tr></tbody>
      </table>
    </section>
  </main>

  <dialog id="app-credentials-dialog" class="op-dialog">
    <h2>配置 TikTok App 凭证</h2>
    <p class="form-hint">按 service_id 共享。App Secret 只加密存储，不会回显；保存需要 admin。</p>
    <form id="app-credentials-form">
      <input id="d-shop-pk" type="hidden">
      <div class="form-field mono">
        <label for="d-service-id">service_id *</label>
        <input id="d-service-id" type="text" required autocomplete="off">
      </div>
      <div class="form-field mono">
        <label for="d-app-key">App Key *</label>
        <input id="d-app-key" type="text" required autocomplete="off">
      </div>
      <div class="form-field mono">
        <label for="d-app-secret">App Secret *</label>
        <input id="d-app-secret" type="password" required autocomplete="new-password">
      </div>
      <div class="op-dialog-actions">
        <button id="d-cancel" type="button" class="btn-secondary">取消</button>
        <button type="submit" class="btn-primary">保存凭证</button>
      </div>
    </form>
  </dialog>
<script src="../../static/js/shops.js?v=__JSV_SHOPS__" defer></script>
</body>
</html>
"""


@router.get("/spu-roi", response_class=HTMLResponse)
def spu_roi_page() -> HTMLResponse:
  """SPU 实际 ROI 看板(账页式,§7 of tech-doc/analytics/spu-real-roi-dashboard.md)。

  HTML shell 只做骨架:标题/结余带/工具栏/表格容器/分页;数据与业务计算
  全部消费 GET /v2/analytics/spu-roi(只读,§5.1-1 页面不计算业务数字)。
  布局 = Bootstrap 5.3.8 栅格/工具类 + 手机端适配(见 shell 头注释),行为在
  static/js/spu-roi.js。
  """
  return _page(_SPU_ROI_PAGE_HTML, current_page="spu-roi")


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
  return _page(_PAGE_HTML, current_page="manual-costs")


# Marker for the legacy token-paste UI — kept as a comment so future
# agents know NOT to reintroduce it. The page now relies on session-cookie
# auth (see tech-doc/browser-login-design.md).
#
# NOT TO ADD BACK: <details>API token (paste once; stored in localStorage)</details>

_PAGE_HTML = """<!doctype html>
<html lang="zh-Hans">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>采购工作台 · tts-erp</title>
  <!-- Relative path: resolves to /static/... locally and /tts/static/...
       behind the NGINX prefix. Do not make this absolute. -->
  <link rel="stylesheet" href="../../static/vendor/bootstrap.min.css">
  <style>
    /* ---------- tokens ----------
       Warm paper, warm near-black, burnt sienna accent. Deliberately
       NOT the AI-default cream+terracotta landing palette — applied
       with industrial precision (oversized mono, hairline rules,
       zero rounded corners) to read as an operator workbench, not a
       marketing surface. No webfonts: system stacks only. */
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
      -webkit-font-smoothing: antialiased;
      text-rendering: optimizeLegibility;
    }
    /* Override Bootstrap defaults that compete with our tokens. */
    body { background-color: var(--paper); }
    a { color: var(--accent); text-decoration: none; }
    a:hover { color: var(--accent-deep); }

    /* ---------- HEADER ---------- */
    .op-header {
      border-bottom: 1px solid var(--rule);
      padding: 18px 28px 14px;
      background: var(--paper);
    }
    .op-header-row {
      display: flex;
      align-items: flex-end;
      justify-content: space-between;
      gap: 24px;
      flex-wrap: wrap;
    }
    .op-header-titles { display: flex; flex-direction: column; gap: 2px; }
    .op-eyebrow {
      font-family: var(--mono);
      font-size: 11px;
      letter-spacing: 0.14em;
      text-transform: uppercase;
      color: var(--muted);
    }
    .op-title {
      font-family: var(--serif);
      font-weight: 600;
      font-size: 22px;
      margin: 0;
      letter-spacing: -0.01em;
    }
    .op-header-meta {
      display: flex;
      align-items: center;
      gap: 24px;
      flex-wrap: wrap;
    }
    .op-shop {
      display: inline-flex;
      align-items: center;
      gap: 10px;
      font-family: var(--mono);
      font-size: 11px;
      letter-spacing: 0.1em;
      text-transform: uppercase;
      color: var(--muted);
    }
    .op-shop-select {
      font-family: var(--sans);
      font-size: 13px;
      background: transparent;
      border: 1px solid var(--rule);
      padding: 4px 8px;
      color: var(--ink);
      border-radius: 0;
    }
    .op-shop-select:focus { outline: 2px solid var(--accent); outline-offset: 1px; }
    .op-identity { font-family: var(--mono); font-size: 14px; color: var(--muted); }
    .op-identity code { font-family: var(--mono); color: var(--ink); }

    /* ---------- COUNTER (signature element) ----------
       The oversized queue number IS the page's job. The operator opens
       this 10x/day to grind it down. It's not decoration. */
    .op-counter {
      position: relative;
      display: flex;
      align-items: flex-end;
      gap: 22px;
      padding: 26px 28px 22px;
      border-bottom: 1px solid var(--rule);
      background: var(--paper);
    }
    .op-counter::before {
      content: "";
      position: absolute;
      left: 28px;
      top: 26px;
      bottom: 22px;
      width: 2px;
      background: var(--accent);
    }
    .op-counter-num {
      font-family: var(--mono);
      font-weight: 800;
      font-size: clamp(72px, 11vw, 132px);
      line-height: 0.88;
      color: var(--ink);
      letter-spacing: -0.04em;
      font-variant-numeric: tabular-nums;
      padding-left: 14px;
      transition: color 200ms ease;
    }
    .op-counter[data-state="ready"] .op-counter-num {
      animation: op-counter-pop 520ms cubic-bezier(0.2, 0.8, 0.3, 1);
    }
    @keyframes op-counter-pop {
      0%   { transform: scale(0.94); opacity: 0.55; }
      55%  { transform: scale(1.04); opacity: 1; }
      100% { transform: scale(1.00); opacity: 1; }
    }
    .op-counter-meta {
      padding-bottom: 16px;
      display: flex;
      flex-direction: column;
      gap: 4px;
    }
    .op-counter-label {
      font-family: var(--serif);
      font-weight: 600;
      font-size: 19px;
      color: var(--accent);
      letter-spacing: 0;
    }
    .op-counter-sub {
      font-family: var(--mono);
      font-size: 11px;
      letter-spacing: 0.12em;
      text-transform: uppercase;
      color: var(--muted);
    }
    .op-counter-stamp {
      position: absolute;
      top: 22px;
      right: 28px;
      font-family: var(--mono);
      font-size: 10px;
      letter-spacing: 0.4em;
      color: var(--rule);
      pointer-events: none;
      user-select: none;
    }

    /* ---------- TABS ---------- */
    .op-tabs {
      display: flex;
      gap: 0;
      padding: 0 28px;
      border-bottom: 1px solid var(--rule);
      background: var(--paper);
    }
    .op-tab {
      background: transparent;
      border: 0;
      padding: 13px 18px;
      font-family: var(--sans);
      font-size: 14px;
      color: var(--muted);
      cursor: pointer;
      position: relative;
      border-bottom: 2px solid transparent;
      margin-bottom: -1px;
      transition: color 120ms ease;
    }
    .op-tab:hover { color: var(--ink); }
    .op-tab-active {
      color: var(--ink);
      font-weight: 600;
      border-bottom-color: var(--accent);
    }
    .op-tab:focus-visible {
      outline: 2px solid var(--accent);
      outline-offset: 2px;
    }
    .op-badge {
      display: inline-block;
      font-family: var(--mono);
      font-size: 11px;
      font-weight: 500;
      color: var(--muted);
      margin-left: 8px;
      padding: 1px 7px;
      border: 1px solid var(--rule);
      font-variant-numeric: tabular-nums;
    }
    .op-tab-active .op-badge {
      color: var(--accent);
      border-color: var(--accent);
    }

    /* ---------- TOOLBAR ---------- */
    .op-toolbar {
      display: flex;
      align-items: center;
      gap: 32px;
      padding: 14px 28px;
      font-family: var(--mono);
      font-size: 11px;
      letter-spacing: 0.1em;
      text-transform: uppercase;
      color: var(--muted);
      flex-wrap: wrap;
    }
    .op-search, .op-pp { display: inline-flex; align-items: center; gap: 10px; }
    .op-toolbar-spacer { flex: 1; }
    .op-btn-primary[data-act="submit-all"] { padding: 6px 14px; }
    .op-btn-primary[data-act="submit-all"]:disabled { background: var(--rule); color: var(--paper); cursor: wait; }
    /* Batch submit result banner (submit-all) */
    .op-batch-status {
      font-family: var(--sans);
      font-size: 12px;
      letter-spacing: 0;
      text-transform: none;
      color: var(--muted);
      white-space: nowrap;
    }
    .op-batch-status.is-ok { color: var(--ok); }
    .op-batch-status.is-err { color: var(--danger); }
    .op-input {
      font-family: var(--sans);
      font-size: 13px;
      background: transparent;
      border: 0;
      border-bottom: 1px solid var(--rule);
      color: var(--ink);
      padding: 4px 0;
      border-radius: 0;
    }
    .op-input:focus { outline: 0; border-bottom-color: var(--accent); }
    .op-input-search { width: 240px; text-transform: none; letter-spacing: 0; }
    .op-input-search::placeholder { color: var(--rule); }
    .op-input-pp { width: 64px; font-family: var(--mono); text-transform: none; letter-spacing: 0; }

    /* ---------- TABLE ---------- */
    .op-main { max-width: 1280px; margin: 0 auto; }
    .op-table-wrap { padding: 0 28px 4px; }
    .op-pager {
      display: flex;
      align-items: center;
      gap: 18px;
      padding: 14px 28px 56px;
      font-family: var(--sans);
      font-size: 13px;
    }
    .op-pager-page { color: var(--ink); font-variant-numeric: tabular-nums; }
    .op-pager .op-btn:disabled {
      opacity: 0.45;
      cursor: not-allowed;
    }
    .op-table {
      width: 100%;
      border-collapse: collapse;
      font-family: var(--sans);
      font-size: 13px;
      background: var(--paper);
    }
    .op-th {
      text-align: left;
      font-family: var(--mono);
      font-size: 10px;
      letter-spacing: 0.14em;
      text-transform: uppercase;
      color: var(--muted);
      font-weight: 500;
      padding: 12px 12px;
      border-bottom: 1px solid var(--rule);
      white-space: nowrap;
    }
    .op-th-cost { text-align: right; }
    .op-th-action { text-align: right; }
    /* Sortable column headers (all-SPU catalogue, 2026-09-06): clicking
       a sortable th re-requests channel-products with the new sort.
       The active sort column shows ↑/↓ via .op-sort-arrow. */
    .op-th-sortable { cursor: pointer; user-select: none; }
    .op-th-sortable:hover { color: var(--accent); }
    .op-th-sortable .op-sort-arrow {
      display: inline-block;
      width: 10px;
      margin-left: 4px;
      color: var(--muted);
    }
    .op-th-sortable.is-sorted-asc .op-sort-arrow::after { content: "\2191"; }
    .op-th-sortable.is-sorted-desc .op-sort-arrow::after { content: "\2193"; }
    .op-th-sortable.is-sorted-asc,
    .op-th-sortable.is-sorted-desc { color: var(--accent); }
    /* Row edit state: an edited-but-unsubmitted cost input is highlighted
       so the operator can see what 提交全部 will file. */
    .op-cost-input.is-dirty { border-color: var(--accent); box-shadow: 0 0 0 1px var(--accent); }
    .op-cost-input.is-dirty .op-currency-fixed { background: var(--accent-deep); color: var(--paper); }
    .op-table td {
      padding: 14px 12px;
      border-bottom: 1px solid var(--rule-soft);
      vertical-align: middle;
    }
    .op-table tbody tr:hover { background: var(--paper-deep); }
    .op-table tbody tr:focus-within { background: var(--paper-deep); }
    .op-td-sku {
      font-family: var(--mono);
      font-size: 12px;
      color: var(--ink-soft);
      width: 200px;
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
      max-width: 200px;
    }
    .op-td-title { color: var(--ink); font-size: 13px; line-height: 1.4; }
    .op-td-cost { text-align: right; white-space: nowrap; }
    .op-td-action { text-align: right; white-space: nowrap; }

    /* Cost input group: number + select fused, hairline border */
    .op-cost-input {
      display: inline-flex;
      align-items: stretch;
      border: 1px solid var(--rule);
      background: var(--paper);
    }
    .op-cost-input:focus-within { border-color: var(--ink); }
    .op-input-cost {
      font-family: var(--mono);
      font-size: 13px;
      text-align: right;
      width: 120px;
      padding: 6px 10px;
      border: 0;
      background: transparent;
      color: var(--ink);
      -moz-appearance: textfield;
    }
    .op-input-cost::-webkit-outer-spin-button,
    .op-input-cost::-webkit-inner-spin-button { -webkit-appearance: none; margin: 0; }
    .op-input-cost:focus { outline: 0; }
    .op-input-cost::placeholder { color: var(--rule); }
    .op-currency-fixed {
      font-family: var(--mono);
      font-size: 11px;
      font-weight: 500;
      letter-spacing: 0.06em;
      padding: 6px 8px;
      border: 0;
      border-left: 1px solid var(--rule);
      background: var(--paper-deep);
      color: var(--ink);
      pointer-events: none;
    }
    .op-source-badge {
      font-family: var(--mono);
      font-size: 10px;
      letter-spacing: 0.06em;
      padding: 2px 6px;
      margin-left: 6px;
      border: 1px solid var(--rule-soft);
      background: var(--paper-deep);
      color: var(--muted);
      vertical-align: middle;
    }
    .op-input-note {
      width: 100%;
      font-family: var(--sans);
      font-size: 13px;
      padding: 6px 0;
      border: 0;
      border-bottom: 1px solid var(--rule);
      background: transparent;
      color: var(--ink);
      border-radius: 0;
    }
    .op-input-note:focus { outline: 0; border-bottom-color: var(--accent); }
    .op-input-note::placeholder { color: var(--rule); }

    /* Main-image mirror cell (2026-09-05 page-rework lane): the row
       shows the SPU's TikTok main image mirrored into local MinIO — no
       manual upload UI. Rows without a finished mirror render a
       fixed-size fallback box instead. */
    .op-mirror-thumb {
      display: block;
      width: 56px;
      height: 56px;
      object-fit: cover;
      border: 1px solid var(--rule);
      cursor: zoom-in;
      background: var(--paper-deep);
    }
    .op-img-fallback {
      display: inline-block;
      width: 56px;
      height: 56px;
      border: 1px dashed var(--rule);
      background: var(--paper-deep);
    }
    /* Lightbox: click row image → fullscreen overlay; click empty space
       (or × / Esc) to close. */
    .op-lightbox {
      position: fixed;
      inset: 0;
      display: none;
      align-items: center;
      justify-content: center;
      background: rgba(20, 16, 10, 0.86);
      z-index: 1000;
      cursor: zoom-out;
      padding: 40px;
    }
    .op-lightbox.is-open { display: flex; }
    .op-lightbox img {
      max-width: 100%;
      max-height: 100%;
      object-fit: contain;
      border: 1px solid var(--rule);
      background: var(--paper);
      cursor: default;
    }
    .op-lightbox-close {
      position: absolute;
      top: 12px;
      right: 18px;
      background: transparent;
      border: 0;
      color: var(--paper);
      font-size: 34px;
      line-height: 1;
      cursor: pointer;
    }
    .op-lightbox-close:hover { color: var(--accent); }
    /* Submit button — the only filled button on the page */
    .op-btn-primary {
      font-family: var(--mono);
      font-size: 11px;
      font-weight: 600;
      letter-spacing: 0.14em;
      text-transform: uppercase;
      padding: 8px 16px;
      background: var(--ink);
      color: var(--paper);
      border: 0;
      cursor: pointer;
      border-radius: 0;
      transition: background 120ms ease;
    }
    .op-btn-primary:hover { background: var(--accent); }
    .op-btn-primary:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
    .op-btn-primary:disabled { background: var(--rule); color: var(--paper); cursor: not-allowed; }

    /* Row status */
    .row-status {
      display: inline-block;
      margin-left: 12px;
      font-family: var(--mono);
      font-size: 11px;
      letter-spacing: 0.04em;
      vertical-align: middle;
    }
    .row-status.is-ok { color: var(--ok); }
    .row-status.is-err { color: var(--danger); }
    .row-status.is-saving { color: var(--muted); }
    .row-status.is-rate-limit { color: var(--accent); font-weight: 600; }

    /* Empty / loading rows */
    .op-loading, .op-empty, .op-error {
      text-align: center;
      padding: 64px 20px !important;
      color: var(--muted);
      font-family: var(--mono);
      font-size: 12px;
      letter-spacing: 0.1em;
      text-transform: uppercase;
    }
    .op-empty, .op-error {
      text-transform: none;
      letter-spacing: 0;
      font-family: var(--sans);
      font-size: 14px;
    }
    .op-error { color: var(--danger); }
    .op-error a { color: var(--danger); text-decoration: underline; }

    /* Filed-out animation */
    tr.is-filed { animation: op-filed-out 360ms ease forwards; }
    @keyframes op-filed-out { to { opacity: 0; transform: translateY(-2px); } }

    /* 429 path */
    .op-counter[aria-busy="true"]::after {
      content: "·";
      color: var(--rule);
    }

    /* Reduced motion */
    @media (prefers-reduced-motion: reduce) {
      .op-counter-num, tr.is-filed { animation: none !important; transition: none !important; }
    }

    /* Mobile: counter stays hero, table becomes a stacked card list */
    @media (max-width: 720px) {
      .op-counter { padding: 18px 16px 16px; gap: 14px; }
      .op-counter::before { left: 16px; top: 18px; bottom: 16px; }
      .op-counter-num { padding-left: 10px; font-size: 64px; }
      .op-tabs, .op-toolbar, .op-table-wrap { padding-left: 16px; padding-right: 16px; }
      .op-table thead { display: none; }
      .op-table, .op-table tbody, .op-table tr, .op-table td { display: block; width: 100%; }
      .op-table tr { border-bottom: 1px solid var(--rule); padding: 12px 0; }
      .op-table td { padding: 6px 0; border: 0; }
      .op-table td::before {
        content: attr(data-label);
        display: block;
        font-family: var(--mono);
        font-size: 10px;
        letter-spacing: 0.12em;
        text-transform: uppercase;
        color: var(--muted);
        margin-bottom: 4px;
      }
      .op-td-action { text-align: left; }
    }
    .op-home-link { font-family: var(--mono); font-size: 12px; letter-spacing: 0.08em; text-transform: uppercase; color: var(--muted); text-decoration: none; padding: 4px 8px; border: 1px solid var(--rule); transition: border-color 120ms ease; display: inline-block; }
    .op-home-link:hover { border-color: var(--accent); color: var(--accent); }
  </style>
</head>
<body>
  <header class="op-header">
    <div class="op-header-row">
      <div class="op-header-titles">
        <a href="../../v2/pages/dashboard" class="op-home-link">← 首页</a>
        <span class="op-eyebrow">TikTok Shop · Operations</span>
        <h1 class="op-title">采购工作台</h1>
      </div>
      <div class="op-header-meta">
        <label class="op-shop" for="shop-switcher">
          <span>店铺</span>
          <select id="shop-switcher" name="shop_pk" class="op-shop-select" aria-label="当前店铺"></select>
        </label>
        <span class="op-identity" id="ops-identity"></span>
      </div>
    </div>
  </header>

  <main class="op-main">
    <!-- SIGNATURE: oversized queue counter. The number is the page. -->
    <section class="op-counter" id="op-counter" data-state="loading" aria-busy="true" aria-live="polite">
      <div class="op-counter-num" id="op-counter-num">·</div>
      <div class="op-counter-meta">
        <div class="op-counter-label" id="op-counter-label">全部 SPU</div>
        <div class="op-counter-sub" id="op-counter-sub">目录 · 点击列头排序 · 编辑成本后提交</div>
      </div>
      <div class="op-counter-stamp" aria-hidden="true">CATALOG · ALL</div>
    </section>

    <nav class="op-tabs" role="tablist" aria-label="工作台标签页">
      <button class="op-tab op-tab-active" type="button" role="tab" data-tab="all" aria-selected="true" aria-controls="grid-rows">
        全部 SPU
        <span class="op-badge" id="badge-all">·</span>
      </button>
      <button class="op-tab" type="button" role="tab" data-tab="recent" aria-selected="false" aria-controls="grid-rows">
        最近提交
        <span class="op-badge" id="badge-recent">·</span>
      </button>
    </nav>

    <div class="op-toolbar">
      <label class="op-search">
        <span>搜索</span>
        <input id="filter-search" type="search" class="op-input op-input-search" placeholder="SKU 或标题" aria-label="过滤行">
      </label>
      <label class="op-pp">
        <span>每页</span>
        <select id="filter-limit" class="op-input op-input-pp" aria-label="每页行数">
          <option>25</option>
          <option selected>50</option>
          <option>100</option>
        </select>
      </label>
      <label class="op-pp">
        <span>状态</span>
        <select id="filter-status" class="op-input op-input-pp" aria-label="按状态过滤"></select>
      </label>
      <label class="op-pp op-has-orders" title="只看出现在销售订单行里的 SPU">
        <input type="checkbox" id="filter-has-orders" aria-label="仅看有单的 SPU">
        <span>仅看有单</span>
      </label>
      <span class="op-toolbar-spacer" aria-hidden="true"></span>
      <button type="button" class="op-btn-primary" data-act="submit-all" aria-label="一次性提交所有已编辑成本的行">
        提交全部
      </button>
      <span class="op-batch-status" role="status" aria-live="polite"></span>
    </div>

    <div class="op-table-wrap">
      <table class="op-table" aria-live="polite">
        <thead>
          <tr>
            <th scope="col" class="op-th op-th-sku">SKU</th>
            <th scope="col" class="op-th op-th-title">标题</th>
            <th scope="col" class="op-th op-th-sku">状态</th>
            <th scope="col" class="op-th op-th-cost op-th-sortable" data-sort="unit_cost" title="按成本价排序">成本<span class="op-sort-arrow" aria-hidden="true"></span></th>
            <th scope="col" class="op-th op-th-sku op-th-sortable" data-sort="created_at" title="按创建时间排序">创建<span class="op-sort-arrow" aria-hidden="true"></span></th>
            <th scope="col" class="op-th op-th-sku op-th-sortable" data-sort="updated_at" title="按更新时间排序">更新<span class="op-sort-arrow" aria-hidden="true"></span></th>
            <th scope="col" class="op-th op-th-photo">图片</th>
          </tr>
        </thead>
        <tbody id="grid-rows">
          <tr><td colspan="7" class="op-loading">加载店铺中…</td></tr>
        </tbody>
      </table>
    </div>

    <section class="op-pager" id="grid-pager">
      <button type="button" class="op-btn" id="btn-prev" disabled>← 上一页</button>
      <span class="op-pager-page" id="pager-label">—</span>
      <button type="button" class="op-btn" id="btn-next" disabled>下一页 →</button>
    </section>
  </main>

  <script src="../../static/js/console.js?v=__JSV_CONSOLE__" defer></script>
</body>
</html>
"""


# SPU 实际 ROI 看板(账页式)HTML shell — 结构见 tech-doc/analytics/spu-real-roi-dashboard.md §7。
# 只读:JS 消费 GET /v2/analytics/spu-roi;业务数字全在服务端算好(§5.1-1)。
# 页面布局 2026-09-29 统一到 Bootstrap 5.3.8(自托管 static/vendor/bootstrap.min.css):
#   - 页头、结余分组、工具栏、分页、页脚均由 container/row/col 与断点工具类驱动；
#   - 主表和钻取表统一使用 table-responsive，保留全部业务列，用横向滚动代替手写断点隐藏；
#   - 钻取 summary 与 P&L 用 row-cols-* 随断点切换列数，tab 用 nav-tabs + overflow-x-auto；
#   - 自定义 CSS 只保留 warm-paper 视觉 token、业务标色、sticky 首列、tooltip/lightbox 等行为皮肤，
#     不再包含 max-width/min-width media query 或 nth-child 响应式规则。
_SPU_ROI_PAGE_HTML = """<!doctype html>
<html lang="zh-Hans">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>SPU 实际 ROI · tts-erp</title>
  <!-- Relative path: resolves to /static/... locally and /tts/static/... behind NGINX. Do not make absolute. -->
  <link rel="stylesheet" href="../../static/vendor/bootstrap.min.css">
  <link rel="stylesheet" href="../../static/vendor/tom-select.bootstrap5.min.css">
  <link rel="stylesheet" href="../../static/css/spu-roi.css?v=__CSSV_SPU_ROI__">
  <style>
    :root {
      --mono: 'JetBrains Mono', 'SF Mono', 'Cascadia Mono', Consolas, monospace;
      --sans: 'Inter', 'Noto Sans SC', 'Source Han Sans SC', 'PingFang SC', 'Hiragino Sans GB', 'Microsoft YaHei', system-ui, -apple-system, sans-serif;
      --serif: 'Noto Serif SC', 'Source Han Serif SC', Georgia, ui-serif, serif;
    }
    html, body {
      font-family: var(--sans);
      font-size: 15px;
      line-height: 1.6;
      -webkit-font-smoothing: antialiased;
      -moz-osx-font-smoothing: grayscale;
      text-rendering: optimizeLegibility;
    }
    .op-home-link { font-family: var(--mono); font-size: 12px; letter-spacing: 0.08em; text-transform: uppercase; color: var(--muted); text-decoration: none; padding: 4px 8px; border: 1px solid var(--rule); transition: border-color 120ms ease; display: inline-block; }
    .op-home-link:hover { border-color: var(--accent); color: var(--accent); }
  </style>
</head>
<body>
  <header class="op-header">
    <div class="container-fluid px-3 px-lg-4 py-3 op-main">
      <div class="row g-3 align-items-center">
        <div class="col-auto">
          <a href="../../v2/pages/dashboard" class="op-home-link">← 首页</a>
        </div>
        <div class="col">
          <div class="op-eyebrow mb-1">TikTok Shop · Analytics</div>
          <h1 class="op-title mb-0">SPU 实际 ROI</h1>
        </div>
        <div class="col-12 col-lg-auto">
          <div class="op-header-meta d-flex flex-column flex-sm-row align-items-stretch align-items-sm-center justify-content-sm-end gap-2 gap-sm-3">
            <label class="op-shop mb-0" for="shop-switcher">
              <span>店铺</span>
              <select id="shop-switcher" name="shop_pk" class="form-select form-select-sm op-shop-select" aria-label="当前店铺"></select>
            </label>
            <span class="op-identity" id="ops-identity"></span>
            <span class="op-scope-note" id="sum-stamp">ROI · 账页</span>
          </div>
        </div>
      </div>
    </div>
  </header>

  <main class="container-fluid px-0 op-main">
    <!-- 结余带:Bootstrap 外层断点 + 每组 row-cols-2；同类量/额或量/率始终成对 -->
    <section class="op-counter px-3 px-lg-4 py-3 py-lg-4" id="summaries" aria-live="polite">
      <div class="row g-2 g-xl-3 row-cols-1 row-cols-md-2 row-cols-xl-3 row-cols-xxl-4">
        <div class="col">
          <div class="op-counter-group h-100">
            <div class="op-counter-group-label">总览</div>
            <div class="row g-0 row-cols-2">
              <div class="col"><span class="op-counter-item h-100 p-2 p-lg-3"><span class="op-counter-label">总单量<span class="op-hint" data-tip="总单量 = 有效订单 + 取消订单；在当前店铺和日期范围内按订单去重">?</span></span><span class="op-counter-num" id="sum-total-orders">—</span></span></div>
              <div class="col"><span class="op-counter-item h-100 p-2 p-lg-3"><span class="op-counter-label">广告消耗<span class="op-hint" data-tip="单店铺所有广告消耗 = Σ mixed_real_cost（plugin.ad_daily；随选中日期窗口裁剪；作为减项计入净利润）">?</span></span><span class="op-counter-num" id="sum-spend">—</span></span></div>
            </div>
          </div>
        </div>
        <div class="col">
          <div class="op-counter-group h-100">
            <div class="op-counter-group-label">有效</div>
            <div class="row g-0 row-cols-2">
              <div class="col"><span class="op-counter-item h-100 p-2 p-lg-3"><span class="op-counter-label">有效单量<span class="op-hint" data-tip="有效单量：订单状态为待发货、部分发货、待揽收、运输中、已送达、已完成，再减去其中退款的单量">?</span></span><span class="op-counter-num" id="sum-orders">—</span></span></div>
              <div class="col"><span class="op-counter-item h-100 p-2 p-lg-3"><span class="op-counter-label">有效销售<span class="op-hint" data-tip="有效销售额：订单状态为待发货、部分发货、待揽收、运输中、已送达、已完成；已送达和已完成减去退款金额算作有效销售额。已结算按实际到账，未结算按 GMV ×(1−平台费率) ×(1−退款率) 估算">?</span></span><span class="op-counter-num" id="sum-sales">—</span></span></div>
            </div>
          </div>
        </div>
        <div class="col">
          <div class="op-counter-group h-100">
            <div class="op-counter-group-label">退款</div>
            <div class="row g-0 row-cols-2">
              <div class="col"><span class="op-counter-item h-100 p-2 p-lg-3"><span class="op-counter-label">退款数<span class="op-hint" data-tip="订单状态为退款的单量（仅退款/退货退款已完结 case 关联的有效订单，按订单去重）">?</span></span><span class="op-counter-num" id="sum-refund-count">—</span></span></div>
              <div class="col"><span class="op-counter-item h-100 p-2 p-lg-3"><span class="op-counter-label">退款率<span class="op-hint" data-tip="退款率 = 订单状态为退款的单量 / 全部订单（有效订单 + 取消订单）">?</span></span><span class="op-counter-num" id="sum-refund-rate">—</span></span></div>
            </div>
          </div>
        </div>
        <div class="col">
          <div class="op-counter-group h-100">
            <div class="op-counter-group-label">全损</div>
            <div class="row g-0 row-cols-2">
              <div class="col"><span class="op-counter-item h-100 p-2 p-lg-3"><span class="op-counter-label">全损量<span class="op-hint" data-tip="全损量 = 订单状态为退款的单量 + 取消订单中海外取消（物流已到目的国 action_code=38301 的 CANCELLED 订单）。M13b 口径：全损 = 退货（RETURN_AND_REFUND/REFUND_ONLY 已完结，不论物流是否到海外）+ 海外取消（CANCELLED∧38301）；国内取消不计全损。采购成本实亏，计入货本">?</span></span><span class="op-counter-num" id="sum-loss-qty">—</span></span></div>
              <div class="col"><span class="op-counter-item h-100 p-2 p-lg-3"><span class="op-counter-label">全损率<span class="op-hint" data-tip="全损率 = (订单状态为退款的单量 + 取消订单中海外取消) / 全部订单（有效订单 + 取消订单）">?</span></span><span class="op-counter-num" id="sum-loss-rate">—</span></span></div>
            </div>
          </div>
        </div>
        <div class="col">
          <div class="op-counter-group h-100">
            <div class="op-counter-group-label">国内取消</div>
            <div class="row g-0 row-cols-2">
              <div class="col"><span class="op-counter-item h-100 p-2 p-lg-3"><span class="op-counter-label">国内取消量<span class="op-hint" data-tip="取消量 = 取消订单中国内取消（排除海外取消）。海外取消已计入全损，两率互斥不重叠">?</span></span><span class="op-counter-num" id="sum-cancel-count">—</span></span></div>
              <div class="col"><span class="op-counter-item h-100 p-2 p-lg-3"><span class="op-counter-label">国内取消率<span class="op-hint" data-tip="取消率 = 取消订单中国内取消（排除海外取消）/ 全部订单（有效订单 + 取消订单）">?</span></span><span class="op-counter-num" id="sum-cancel-rate">—</span></span></div>
            </div>
          </div>
        </div>
        <div class="col">
          <div class="op-counter-group h-100">
            <div class="op-counter-group-label">利润</div>
            <div class="row g-0 row-cols-2">
              <div class="col"><span class="op-counter-item h-100 p-2 p-lg-3"><span class="op-counter-label">净利润<span class="op-hint" data-tip="净利润 = 净收入 − 货本（含全损）− 广告消耗；已结算订单按实际到账，未结算订单按平台费率估算；全表统一 CNY">?</span></span><span class="op-counter-num" id="sum-net-profit">—</span></span></div>
              <div class="col"><span class="op-counter-item h-100 p-2 p-lg-3"><span class="op-counter-label">实际ROI<span class="op-hint" data-tip="实际ROI = (净收入 − 全损成本) ÷ 广告消耗。≥ 保本ROI = 赚，< 保本ROI = 亏（主判据）；无广告消耗 → —">?</span></span><span class="op-counter-num" id="sum-roi">—</span></span></div>
            </div>
          </div>
        </div>
        <div class="col">
          <div class="op-counter-group h-100">
            <div class="op-counter-group-label">ROI</div>
            <div class="row g-0 row-cols-2">
              <div class="col"><span class="op-counter-item h-100 p-2 p-lg-3"><span class="op-counter-label">实际保本ROI<span class="op-hint" data-tip="实际保本ROI = NC' ÷ (NC' − COGS_kept)，其中 NC' = 净收入 − 全损成本，COGS_kept = (售出件 − 退货件) × 单位成本。净利润 = 0 时的 ROI 临界值；实际ROI低于此值即亏">?</span></span><span class="op-counter-num" id="sum-roi-breakeven">—</span></span></div>
              <div class="col"><span class="op-counter-item h-100 p-2 p-lg-3"><span class="op-counter-label">广告系统实际ROI<span class="op-hint" data-tip="广告系统实际ROI = 广告归因GMV ÷ 广告实际消耗；无广告消耗时显示 —">?</span></span><span class="op-counter-num" id="sum-roi-ad-actual">—</span></span></div>
              <div class="col"><span class="op-counter-item h-100 p-2 p-lg-3"><span class="op-counter-label">广告系统保本ROI<span class="op-hint" data-tip="广告系统保本ROI = 广告归因GMV ÷ 最大可承受广告费；最大可承受广告费 = 预计净结算收入 − 同范围采购成本 − 结算外必要成本。当前系统尚未结构化录入退货运费、提现费、汇兑损失、包装耗材等结算外成本，因此页面以 ≈ 标记已知成本下限估算；分母≤0或无归因GMV时显示 —">?</span></span><span class="op-counter-num" id="sum-roi-ad">—</span></span></div>
            </div>
          </div>
        </div>
      </div>
    </section>

    <!-- 工具栏:原生 select multiple 由 Tom Select Bootstrap 5 主题增强；不自研多选组件。 -->
    <section class="op-toolbar px-3 px-lg-4 py-3" id="toolbar">
      <div class="row g-2 g-lg-3 align-items-end mb-2">
        <div class="col-12 col-xl">
          <label class="form-label op-fld-label mb-1" for="filter-spu-ids">SPU 筛选</label>
          <select id="filter-spu-ids" class="form-select" multiple aria-label="批量选择 SPU" disabled></select>
          <div class="form-text op-spu-help">
            支持搜索或批量粘贴中英文逗号分隔的 SPU；最多 100 个。
            <span id="spu-selection-count">已选择 0 个</span>
          </div>
          <div class="invalid-feedback" id="spu-filter-feedback"></div>
        </div>
        <div class="col-6 col-sm-auto d-grid">
          <button type="button" class="btn btn-sm btn-outline-secondary" id="btn-spu-clear" disabled>清空</button>
        </div>
        <div class="col-6 col-sm-auto d-grid">
          <button type="button" class="btn btn-sm btn-primary" id="btn-spu-apply" disabled>查询</button>
        </div>
      </div>
      <div class="row g-2 g-lg-3 align-items-end">
        <div class="col-6 col-md-3 col-xl-2" data-tip="日期范围（销售/退款/广告同口径裁剪；空 = 全历史）">
          <label class="form-label op-fld-label mb-1" for="filter-w-start">起始日</label>
          <input id="filter-w-start" type="date" class="form-control form-control-sm" aria-label="销售/退款起始日期（空 = 不限）">
        </div>
        <div class="col-6 col-md-3 col-xl-2" data-tip="销售/退款日期范围（空 = 全历史；含当日）">
          <label class="form-label op-fld-label mb-1" for="filter-w-end">截止日</label>
          <input id="filter-w-end" type="date" class="form-control form-control-sm" aria-label="销售/退款截止日期（空 = 不限）">
        </div>
        <div class="col-6 col-md-3 col-xl-1">
          <label class="form-label op-fld-label mb-1" for="filter-limit">每页</label>
          <select id="filter-limit" class="form-select form-select-sm" aria-label="每页条数">
            <option value="50">50</option>
            <option value="100" selected>100</option>
            <option value="200">200</option>
          </select>
        </div>
        <div class="col-6 col-md-3 col-xl-1" data-tip="平台佣金费率：默认参考基线 0.308（2026-09-06 实测重定，可覆写）">
          <label class="form-label op-fld-label mb-1" for="filter-fee">费率 %</label>
          <input id="filter-fee" type="text" class="form-control form-control-sm" placeholder="30.8" inputmode="decimal" autocomplete="off" aria-label="平台佣金费率（覆盖基线 0.308）">
        </div>
        <div class="col-12 col-md-6 col-xl-auto">
          <div class="form-check d-flex align-items-center gap-2 mb-0 py-2" data-tip="默认只列出当前窗口内有广告或销售/退款活动的 SPU；勾选后，处于 ACTIVE 状态但没有任意活动（无投放 / 未出单）的 SPU 也会一并列出——这类行的 ROI / 金额显示 — 或「无投放」">
            <input id="filter-include-all" type="checkbox" class="form-check-input mt-0" aria-label="含无活动 SPU">
            <label class="form-check-label op-fld-label mb-0" for="filter-include-all">含无活动</label>
            <span class="op-hint" role="note" tabindex="0" aria-label="含无活动 SPU 的说明">?</span>
          </div>
        </div>
        <div class="col-12 col-sm-auto d-grid">
          <button type="button" class="btn btn-sm op-btn" id="btn-refresh">刷新</button>
        </div>
        <div class="col-12 col-xl text-xl-end ms-xl-auto">
          <span class="op-sortable-note" id="sort-note">默认排序：实际 ROI ↑（最亏在前）</span>
        </div>
      </div>
    </section>

    <!-- 主表 6 指标与大盘 v10 同口径：广告消耗 / 有效销售 / 有效单量 / 取消率 / 全损率 / 净利润 -->
    <div class="op-table-wrap table-responsive" tabindex="0" aria-label="SPU ROI 明细，可横向滚动">
      <table class="table table-hover align-middle mb-0 op-table" aria-live="polite">
        <thead>
          <tr>
            <th scope="col" class="op-th op-th-left">商品</th>
            <th scope="col" class="op-th op-th-sort" data-sort="spend" data-tip="广告消耗（源数据 USD，服务端按汇率快照换算为 CNY；随选中日期窗口裁剪；作为减项计入净利润）">广告消耗</th>
            <th scope="col" class="op-th op-th-sort" data-sort="effective_sales" data-tip="有效销售 = 有效销售订单 GMV − 退款金额（CNY）；与大盘 totals.effective_sales 同口径">有效销售</th>
            <th scope="col" class="op-th op-th-sort" data-sort="effective_order_count" data-tip="有效单量 = 有效销售订单数 − 退款订单数；与大盘 totals.effective_order_count 同口径">有效单量</th>
            <th scope="col" class="op-th op-th-sort" data-sort="cancel_rate" data-tip="取消率 = 国内取消订单数 ÷ 全部订单；全部订单 = 有效销售订单 + 国内取消 + 海外取消，海外取消只进入全损分子">取消率%</th>
            <th scope="col" class="op-th op-th-sort" data-sort="full_loss_rate" data-tip="全损率 = (退款订单数 + 海外取消订单数) ÷ 全部订单；订单维度按当前 SPU 去重，与大盘同口径">全损率%</th>
            <th scope="col" class="op-th op-th-sort" data-sort="net_profit" data-tip="净利润 v7(M18):已结算 SETTLEMENT + 未结算 ×(1−r̂)×(1−退款率) − 货本含全损取消 − 广告;负值红字。Red/green 仅按净利判(C3 拍板,删 ROI&lt;1 硬亏档)">净利润</th>
          </tr>
        </thead>
        <tbody class="op-rows" id="rows">
          <tr><td colspan="7" class="op-loading">加载中…</td></tr>
        </tbody>
      </table>
    </div>

    <!-- D7 钻取面板模板（行内 accordion，由 spu-roi.js openDrillPanel 克隆插入） -->
    <template id="tpl-drilldown-panel">
      <tr class="op-drill-row" aria-live="polite">
        <td colspan="7" class="op-drill-wrap">
          <div class="op-drill p-2 p-md-3" data-state="loading">
            <nav class="nav nav-tabs flex-nowrap overflow-x-auto op-drill-tabs" role="tablist">
              <button type="button" class="nav-link active op-drill-tab is-active" role="tab" aria-selected="true" data-tab="pnl">利润构成</button>
              <button type="button" class="nav-link op-drill-tab" role="tab" aria-selected="false" data-tab="orders">订单·物流</button>
              <button type="button" class="nav-link op-drill-tab" role="tab" aria-selected="false" data-tab="settlements">结算</button>
              <button type="button" class="nav-link op-drill-tab" role="tab" aria-selected="false" data-tab="cases">售后</button>
              <button type="button" class="nav-link op-drill-tab" role="tab" aria-selected="false" data-tab="ads">广告</button>
            </nav>
            <div class="alert alert-warning rounded-0 py-2 px-3 op-drill-banner" data-banner="warn" hidden></div>
            <div class="op-drill-summary" data-region="summary"></div>
            <div class="op-drill-body" data-region="body"><div class="op-drill-loading">加载中…</div></div>
          </div>
        </td>
      </tr>
    </template>

    <section class="op-pager px-3 px-lg-4 py-3 pb-4">
      <div class="row g-2 align-items-center">
        <div class="col-6 col-md-auto order-2 order-md-1 d-grid">
          <button type="button" class="btn btn-sm op-btn" id="btn-prev">← 上一页</button>
        </div>
        <div class="col-12 col-md text-center order-1 order-md-2">
          <span class="op-pager-page" id="pager-label">—</span>
        </div>
        <div class="col-6 col-md-auto order-3 d-grid">
          <button type="button" class="btn btn-sm op-btn" id="btn-next">下一页 →</button>
        </div>
      </div>
    </section>

    <section class="op-footnotes px-3 px-lg-4 py-3 mb-4 d-flex flex-column flex-md-row align-items-md-center gap-2 gap-md-3" id="footnotes">
      <span id="foot-meta">—</span>
      <span class="op-warn-chip">GMV Max 归因含自然单 · 广告数字仅供对照</span>
    </section>
  </main>

  <!-- shop_pk 缺失/无效时的店铺选择弹窗(2026-09-28 用户拍板:弹窗让用户选店铺,
       不再 toast + 60s 倒计时强跳首页) -->
  <div id="ops-shop-modal" class="op-shop-modal position-fixed top-0 start-0 w-100 h-100 align-items-center justify-content-center p-3" hidden>
    <div class="card rounded-0 op-shop-modal-box" role="dialog" aria-modal="true" aria-labelledby="shop-modal-title">
      <div class="card-body p-3 p-md-4">
        <div class="card-title op-shop-modal-title" id="shop-modal-title">请选择店铺</div>
        <div class="op-shop-modal-note" id="shop-modal-note"></div>
        <div class="d-grid gap-2 op-shop-modal-list" id="shop-modal-list"></div>
        <div class="op-shop-modal-foot"><a href="../../v2/pages/dashboard">← 返回首页</a></div>
      </div>
    </div>
  </div>
  <div id="ops-tip" role="tooltip" hidden></div>
  <script src="../../static/vendor/tom-select.complete.min.js" defer></script>
  <script src="../../static/js/spu-roi.js?v=__JSV_SPU_ROI__" defer></script>
</body>
</html>
"""


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
  return _page(_DASHBOARD_PAGE_HTML, current_page="dashboard")


_DASHBOARD_PAGE_HTML = """<!doctype html>
<html lang="zh-Hans">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>控制台 · tts-erp</title>
  <link rel="stylesheet" href="../../static/vendor/bootstrap.min.css">
  <style>
    /* ---------- tokens (shared with other pages) ---------- */
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
      --mono: 'JetBrains Mono', 'SF Mono', 'Cascadia Mono', 'Fira Code', Consolas, 'Liberation Mono', ui-monospace, monospace;
      --sans: 'Inter', 'Noto Sans SC', 'Source Han Sans SC', 'PingFang SC', 'Hiragino Sans GB', 'Microsoft YaHei', system-ui, -apple-system, 'Segoe UI', sans-serif;
      --serif: 'Noto Serif SC', 'Source Han Serif SC', 'Iowan Old Style', 'Apple Garamond', Georgia, ui-serif, serif;
    }
    * { box-sizing: border-box; }
    html, body {
      background: var(--paper);
      color: var(--ink);
      font-family: var(--sans);
      font-size: 15px;
      line-height: 1.6;
      margin: 0;
      -webkit-font-smoothing: antialiased;
      -moz-osx-font-smoothing: grayscale;
      text-rendering: optimizeLegibility;
      font-feature-settings: 'kern' 1, 'liga' 1;
    }
    a { color: var(--accent); text-decoration: none; }
    a:hover { color: var(--accent-deep); }

    /* ---------- HEADER ---------- */
    .op-header {
      border-bottom: 1px solid var(--rule);
      padding: 18px 28px 14px;
      background: var(--paper);
    }
    .op-header-row {
      display: flex;
      align-items: flex-end;
      justify-content: space-between;
      gap: 24px;
      flex-wrap: wrap;
    }
    .op-header-titles { display: flex; flex-direction: column; gap: 2px; }
    .op-eyebrow {
      font-family: var(--mono);
      font-size: 12px;
      letter-spacing: 0.14em;
      text-transform: uppercase;
      color: var(--muted);
    }
    .op-title {
      font-family: var(--serif);
      font-weight: 600;
      font-size: 24px;
      margin: 0;
      letter-spacing: -0.01em;
    }
    .op-identity {
      font-family: var(--mono);
      font-size: 13px;
      color: var(--muted);
    }

    /* ---------- MAIN LAYOUT ---------- */
    .dashboard {
      max-width: 1200px;
      margin: 0 auto;
      padding: 24px 28px 64px;
    }
    .dashboard-grid {
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 24px;
    }
    @media (max-width: 768px) {
      .dashboard-grid {
        grid-template-columns: 1fr;
      }
    }

    /* ---------- SECTION CARD ---------- */
    .section-card {
      background: var(--paper);
      border: 1px solid var(--rule);
      border-radius: 0;
      padding: 20px 24px;
    }
    .section-header {
      display: flex;
      align-items: center;
      justify-content: space-between;
      margin-bottom: 16px;
      padding-bottom: 12px;
      border-bottom: 1px solid var(--rule-soft);
    }
    .section-title {
      font-family: var(--mono);
      font-size: 12px;
      letter-spacing: 0.14em;
      text-transform: uppercase;
      color: var(--muted);
      font-weight: 500;
    }
    .section-link {
      font-family: var(--mono);
      font-size: 12px;
      letter-spacing: 0.08em;
      color: var(--accent);
    }
    .section-link:hover { color: var(--accent-deep); }

    /* ---------- QUICK NAV ---------- */
    .quick-nav {
      grid-column: 1 / -1;
    }
    .nav-cards {
      display: grid;
      grid-template-columns: repeat(4, 1fr);
      gap: 16px;
    }
    @media (max-width: 768px) {
      .nav-cards {
        grid-template-columns: 1fr;
      }
    }
    .nav-card {
      display: flex;
      flex-direction: column;
      padding: 20px;
      border: 1px solid var(--rule);
      background: var(--paper);
      transition: border-color 120ms ease, background 120ms ease;
      cursor: pointer;
      text-decoration: none;
      color: var(--ink);
    }
    .nav-card:hover {
      border-color: var(--accent);
      background: var(--paper-deep);
      color: var(--ink);
    }
    .nav-card-icon {
      font-size: 28px;
      margin-bottom: 12px;
    }
    .nav-card-title {
      font-family: var(--serif);
      font-weight: 600;
      font-size: 16px;
      margin-bottom: 4px;
    }
    .nav-card-desc {
      font-size: 13px;
      color: var(--muted);
      line-height: 1.5;
    }

    /* ---------- SUMMARY CARDS ---------- */
    .summary-cards {
      display: grid;
      grid-template-columns: repeat(3, 1fr);
      gap: 12px;
    }
    @media (max-width: 480px) {
      .summary-cards {
        grid-template-columns: 1fr;
      }
    }
    .summary-card {
      display: flex;
      flex-direction: column;
      align-items: flex-start;
      gap: 8px;
      padding: 16px 18px;
      border: 1px solid var(--rule-soft);
      background: var(--paper);
      text-decoration: none;
      color: var(--ink);
      transition: border-color 120ms ease;
    }
    .summary-card:hover {
      border-color: var(--accent);
      color: var(--ink);
    }
    .summary-icon {
      font-size: 22px;
      line-height: 1;
    }
    .summary-content {
      width: 100%;
    }
    .summary-value {
      font-family: var(--mono);
      font-weight: 700;
      font-size: 26px;
      letter-spacing: -0.02em;
      font-variant-numeric: tabular-nums;
      line-height: 1.1;
    }
    .summary-label {
      font-size: 13px;
      color: var(--ink-soft);
      margin-top: 2px;
    }
    .summary-hint {
      font-size: 11px;
      color: var(--muted);
      margin-top: 4px;
      line-height: 1.4;
    }
    .summary-warn .summary-value { color: var(--accent); }
    .summary-ok .summary-value { color: var(--ok); }

    /* ---------- SHOP LIST ---------- */
    .shop-item {
      display: flex;
      align-items: center;
      justify-content: space-between;
      padding: 12px 0;
      border-bottom: 1px solid var(--rule-soft);
    }
    .shop-item:last-child { border-bottom: 0; }
    .shop-item-main {
      display: flex;
      flex-direction: column;
      gap: 2px;
    }
    .shop-name {
      font-weight: 500;
      font-size: 14px;
    }
    .shop-id {
      font-family: var(--mono);
      font-size: 11px;
      color: var(--muted);
    }
    .shop-item-meta {
      display: flex;
      align-items: center;
      gap: 8px;
    }
    .shop-region {
      font-family: var(--mono);
      font-size: 11px;
      color: var(--muted);
    }
    .badge {
      display: inline-block;
      font-family: var(--mono);
      font-size: 10px;
      font-weight: 500;
      letter-spacing: 0.06em;
      padding: 2px 8px;
      border-radius: 0;
    }
    .badge-api { background: var(--ok); color: #fff; }
    .badge-plugin { background: #8a6d1a; color: #fff; }

    .op-empty-state {
      display: flex;
      align-items: center;
      gap: 12px;
      padding: 24px;
      color: var(--muted);
      font-size: 13px;
    }
    .op-empty-icon {
      font-size: 24px;
    }

    /* ---------- SYSTEM STATUS ---------- */
    .status-item {
      display: flex;
      align-items: center;
      gap: 10px;
      padding: 12px 0;
      font-size: 13px;
    }
    .status-dot {
      width: 8px;
      height: 8px;
      border-radius: 50%;
      flex-shrink: 0;
    }
    .status-ok { background: var(--ok); }
    .status-err { background: var(--danger); }
    .status-detail {
      font-family: var(--mono);
      font-size: 11px;
      color: var(--muted);
      margin-left: auto;
    }

    /* ---------- SYNC STATUS ---------- */
    .sync-status-card { grid-column: 1 / -1; }
    .sync-table-wrap { overflow-x: auto; }
    .sync-table {
      width: 100%;
      border-collapse: collapse;
      font-size: 12px;
    }
    .sync-table th {
      text-align: left;
      font-family: var(--mono);
      font-size: 10px;
      letter-spacing: 0.12em;
      text-transform: uppercase;
      color: var(--muted);
      font-weight: 500;
      padding: 6px 10px;
      border-bottom: 1px solid var(--rule);
      white-space: nowrap;
    }
    .sync-table td {
      padding: 8px 10px;
      border-bottom: 1px solid var(--rule-soft);
      font-family: var(--mono);
      font-size: 12px;
      white-space: nowrap;
      vertical-align: middle;
    }
    .sync-table tbody tr:last-child td { border-bottom: 0; }
    .sync-dot {
      display: inline-block;
      width: 8px;
      height: 8px;
      border-radius: 50%;
    }
    .sync-dot-ok { background: var(--ok); }
    .sync-dot-warn { background: #8a6d1a; }
    .sync-dot-crit { background: var(--danger); box-shadow: 0 0 0 3px rgba(140, 26, 26, 0.18); }
    .sync-dot-unknown { background: var(--muted); }
    .sync-sev-crit td { color: var(--danger); }
    .sync-sev-crit td.sync-col-name { font-weight: 600; }
    .sync-status-text-failed { color: var(--danger); }
    .sync-loading {
      text-align: center;
      color: var(--muted);
      font-family: var(--mono);
      font-size: 12px;
      padding: 20px;
    }
    .sync-err-msg {
      display: block;
      font-size: 10px;
      color: var(--danger);
      max-width: 360px;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }

    /* ---------- AUTH NOTE ---------- */
    .auth-note {
      grid-column: 1 / -1;
      background: var(--paper-deep);
      border: 1px solid var(--rule);
      padding: 16px 20px;
      font-size: 13px;
      color: var(--ink-soft);
    }
    .auth-note strong {
      color: var(--ink);
    }

    /* ---------- FOOTER ---------- */
    .dashboard-footer {
      margin-top: 32px;
      padding-top: 16px;
      border-top: 1px solid var(--rule-soft);
      font-family: var(--mono);
      font-size: 11px;
      color: var(--muted);
      display: flex;
      justify-content: space-between;
      flex-wrap: wrap;
      gap: 8px;
    }
  </style>
</head>
<body>
  <header class="op-header">
    <div class="op-header-row">
      <div class="op-header-titles">
        <span class="op-eyebrow">TikTok Shop · Operations</span>
        <h1 class="op-title">控制台</h1>
      </div>
      <div class="op-identity" id="ops-identity"></div>
    </div>
  </header>

  <main class="dashboard">
    <div id="auth-note" class="auth-note d-none"></div>

    <div class="dashboard-grid">
      <!-- Quick Navigation -->
      <section class="section-card quick-nav">
        <div class="section-header">
          <span class="section-title">快速导航</span>
        </div>
        <div class="nav-cards">
          <a href="../../v2/pages/manual-costs" class="nav-card">
            <span class="nav-card-icon">📝</span>
            <span class="nav-card-title">采购工作台</span>
            <span class="nav-card-desc">录入 SPU 采购成本，管理商品成本数据</span>
          </a>
          <a href="../../v2/pages/spu-roi" class="nav-card">
            <span class="nav-card-icon">📊</span>
            <span class="nav-card-title">SPU ROI 看板</span>
            <span class="nav-card-desc">查看商品实际 ROI，分析利润构成</span>
          </a>
          <a href="../../v2/pages/shops" class="nav-card">
            <span class="nav-card-icon">🏪</span>
            <span class="nav-card-title">店铺注册</span>
            <span class="nav-card-desc">管理插件同步店铺的注册与关联</span>
          </a>
          <a href="../../v2/pages/intercept-configs" class="nav-card">
            <span class="nav-card-icon">🔍</span>
            <span class="nav-card-title">请求拦截</span>
            <span class="nav-card-desc">管理 HTTP 请求拦截配置，查看拦截记录</span>
          </a>
        </div>
      </section>

      <!-- Summary -->
      <section class="section-card">
        <div class="section-header">
          <span class="section-title">数据摘要</span>
          <span class="section-scope" id="summary-scope">全店铺</span>
        </div>
        <div id="summary-cards" class="summary-cards">
          <div class="summary-card">
            <span class="summary-icon">⏳</span>
            <div class="summary-content">
              <div class="summary-value">—</div>
              <div class="summary-label">加载中…</div>
            </div>
          </div>
        </div>
      </section>

      <!-- Shop List -->
      <section class="section-card">
        <div class="section-header">
          <span class="section-title">已注册店铺</span>
          <a href="../../v2/pages/shops" class="section-link">管理 →</a>
        </div>
        <div id="shop-list">
          <div class="op-empty-state">
            <span class="op-empty-icon">⏳</span>
            <span>加载中…</span>
          </div>
        </div>
      </section>

      <!-- System Status -->
      <section class="section-card">
        <div class="section-header">
          <span class="section-title">系统状态</span>
        </div>
        <div id="system-status" class="status-item">
          <span class="status-dot" style="background: var(--muted)"></span>
          <span>检查中…</span>
        </div>
        <div class="status-item">
          <span class="status-dot status-ok"></span>
          <span>数据库</span>
          <span class="status-detail">PostgreSQL</span>
        </div>
        <div class="status-item">
          <span class="status-dot status-ok"></span>
          <span>存储</span>
          <span class="status-detail">MinIO</span>
        </div>
        <div class="status-item">
          <span class="status-dot status-ok"></span>
          <span>同步服务</span>
          <span class="status-detail">APScheduler</span>
        </div>
      </section>

      <!-- Sync Status -->
      <section class="section-card sync-status-card">
        <div class="section-header">
          <span class="section-title">数据同步状态</span>
          <span class="section-scope" id="sync-updated-at">—</span>
        </div>
        <div class="sync-table-wrap">
          <table class="sync-table">
            <thead>
              <tr>
                <th style="width: 20px;"></th>
                <th>作业</th>
                <th>周期</th>
                <th>最近一次同步</th>
                <th>预计下次同步</th>
                <th>状态</th>
              </tr>
            </thead>
            <tbody id="sync-status-body">
              <tr><td colspan="6" class="sync-loading">加载中…</td></tr>
            </tbody>
          </table>
        </div>
      </section>
    </div>

    <footer class="dashboard-footer">
      <span>tts-erp v2.0</span>
      <span>TikTok Shop · 妙手采购</span>
    </footer>
  </main>

  <script src="../../static/js/dashboard.js?v=__JSV_DASHBOARD__" defer></script>
</body>
</html>
"""


@router.get("/intercept-configs", response_class=HTMLResponse)
def intercept_configs_page() -> HTMLResponse:
  """拦截配置管理页面。

  功能：配置列表、新增/编辑弹窗、批量操作、导入导出、筛选分页。
  行为在 static/js/intercept-configs.js。
  """
  return _page(_INTERCEPT_CONFIGS_PAGE_HTML, current_page="intercept-configs")


@router.get("/intercept-requests", response_class=HTMLResponse)
def intercept_requests_page() -> HTMLResponse:
  """拦截记录查询页面。

  功能：记录列表、详情弹窗、筛选（域名/路径/状态码/白名单/时间）、分页。
  行为在 static/js/intercept-requests.js。
  """
  return _page(_INTERCEPT_REQUESTS_PAGE_HTML, current_page="intercept-requests")


@router.get("/intercept-stats", response_class=HTMLResponse)
def intercept_stats_page() -> HTMLResponse:
  """拦截统计概览页面。

  功能：总览统计卡片、按域名/方法/状态码分布、最近 7 天每日趋势。
  行为在 static/js/intercept-stats.js。
  """
  return _page(_INTERCEPT_STATS_PAGE_HTML, current_page="intercept-stats")


_INTERCEPT_CONFIGS_PAGE_HTML = """<!doctype html>
<html lang="zh-Hans">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>拦截配置 · tts-erp</title>
  <link rel="stylesheet" href="../../static/vendor/bootstrap.min.css">
  <style>
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
      --mono: 'JetBrains Mono', 'SF Mono', 'Cascadia Mono', Consolas, ui-monospace, monospace;
      --sans: 'Inter', 'Noto Sans SC', 'Source Han Sans SC', 'PingFang SC', 'Hiragino Sans GB', 'Microsoft YaHei', system-ui, -apple-system, sans-serif;
      --serif: 'Noto Serif SC', 'Source Han Serif SC', Georgia, ui-serif, serif;
    }
    * { box-sizing: border-box; }
    html, body {
      background: var(--paper);
      color: var(--ink);
      font-family: var(--sans);
      font-size: 16px;
      line-height: 1.6;
      margin: 0;
      -webkit-font-smoothing: antialiased;
      -moz-osx-font-smoothing: grayscale;
      text-rendering: optimizeLegibility;
    }
    a { color: var(--accent); text-decoration: none; }
    a:hover { color: var(--accent-deep); }
    .op-header { border-bottom: 1px solid var(--rule); padding: 18px 28px 14px; background: var(--paper); }
    .op-header-row { display: flex; align-items: flex-end; justify-content: space-between; gap: 24px; flex-wrap: wrap; }
    .op-header-titles { display: flex; flex-direction: column; gap: 2px; }
    .op-eyebrow { font-family: var(--mono); font-size: 13px; letter-spacing: 0.14em; text-transform: uppercase; color: var(--muted); }
    .op-title { font-family: var(--serif); font-weight: 600; font-size: 26px; margin: 0; letter-spacing: -0.01em; }
    .op-identity { font-family: var(--mono); font-size: 14px; color: var(--muted); }
    .page-main { max-width: 1280px; margin: 0 auto; padding: 24px 28px 64px; }
    .toolbar { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; margin-bottom: 16px; padding-bottom: 16px; border-bottom: 1px solid var(--rule-soft); }
    .toolbar-field { display: flex; align-items: center; gap: 8px; }
    .toolbar-field label { font-family: var(--mono); font-size: 12px; letter-spacing: 0.08em; text-transform: uppercase; color: var(--muted); white-space: nowrap; }
    .toolbar-field input, .toolbar-field select { font-family: var(--sans); font-size: 14px; background: transparent; border: 1px solid var(--rule); padding: 7px 10px; color: var(--ink); border-radius: 0; min-width: 120px; }
    .toolbar-field input:focus, .toolbar-field select:focus { outline: 2px solid var(--accent); outline-offset: 1px; }
    .toolbar-spacer { flex: 0 1 12px; }
    .btn-primary { font-family: var(--mono); font-size: 12px; font-weight: 600; letter-spacing: 0.14em; text-transform: uppercase; padding: 8px 16px; background: var(--ink); color: var(--paper); border: 0; cursor: pointer; border-radius: 0; transition: background 120ms ease; }
    .btn-primary:hover { background: var(--accent); }
    .btn-primary:disabled { background: var(--rule); color: var(--paper); cursor: not-allowed; }
    .btn-secondary { font-family: var(--mono); font-size: 12px; letter-spacing: 0.08em; text-transform: uppercase; padding: 8px 16px; background: transparent; color: var(--ink); border: 1px solid var(--rule); cursor: pointer; border-radius: 0; transition: border-color 120ms ease; }
    .btn-secondary:hover { border-color: var(--accent); }
    .btn-secondary:disabled { opacity: 0.45; cursor: not-allowed; }
    .btn-icon { background: transparent; border: 0; cursor: pointer; font-size: 16px; padding: 4px; }
    .btn-icon:hover { opacity: 0.7; }
    .table-wrap { overflow-x: auto; }
    .op-table { width: 100%; border-collapse: collapse; font-family: var(--sans); font-size: 14px; background: var(--paper); }
    .op-th { text-align: left; font-family: var(--mono); font-size: 11px; letter-spacing: 0.14em; text-transform: uppercase; color: var(--muted); font-weight: 500; padding: 12px 12px; border-bottom: 1px solid var(--rule); white-space: nowrap; }
    .op-table td { padding: 14px 12px; border-bottom: 1px solid var(--rule-soft); vertical-align: middle; }
    .op-table tbody tr:hover { background: var(--paper-deep); }
    .mono { font-family: var(--mono); font-size: 13px; }
    .badge { display: inline-block; font-family: var(--mono); font-size: 11px; font-weight: 500; letter-spacing: 0.06em; padding: 3px 8px; border-radius: 0; }
    .badge-ok { background: var(--ok); color: #fff; }
    .badge-disabled { background: var(--rule); color: var(--ink); }
    .badge-blacklist { background: var(--danger); color: #fff; }
    .tag { display: inline-block; font-family: var(--mono); font-size: 11px; padding: 2px 7px; border: 1px solid var(--rule); margin-right: 4px; }
    .actions { white-space: nowrap; }
    .pager { display: flex; align-items: center; gap: 18px; padding-top: 16px; border-top: 1px solid var(--rule-soft); font-family: var(--sans); font-size: 14px; }
    .pager .btn-secondary:disabled { opacity: 0.45; cursor: not-allowed; }
    .op-empty, .op-error { text-align: center; padding: 48px 20px; color: var(--muted); font-family: var(--mono); font-size: 13px; letter-spacing: 0.08em; }
    .op-error { color: var(--danger); }
    .modal-overlay { position: fixed; inset: 0; display: none; align-items: center; justify-content: center; background: rgba(20, 16, 10, 0.86); z-index: 1000; }
    .modal-overlay.is-open { display: flex; }
    .modal-box { background: var(--paper); border: 1px solid var(--rule); width: 560px; max-width: 95vw; max-height: 90vh; overflow-y: auto; }
    .modal-header { display: flex; align-items: center; justify-content: space-between; padding: 16px 20px; border-bottom: 1px solid var(--rule); }
    .modal-header h2 { font-family: var(--serif); font-size: 20px; font-weight: 600; margin: 0; }
    .modal-close { background: transparent; border: 0; font-size: 24px; cursor: pointer; color: var(--muted); line-height: 1; }
    .modal-close:hover { color: var(--accent); }
    .modal-body { padding: 20px; }
    .form-group { margin-bottom: 16px; }
    .form-group label { display: block; font-family: var(--mono); font-size: 12px; letter-spacing: 0.08em; text-transform: uppercase; color: var(--muted); margin-bottom: 6px; }
    .form-group input[type="text"], .form-group textarea { width: 100%; font-family: var(--sans); font-size: 14px; background: transparent; border: 1px solid var(--rule); padding: 8px 12px; color: var(--ink); border-radius: 0; }
    .form-group input[type="text"]:focus, .form-group textarea:focus { outline: 2px solid var(--accent); outline-offset: 1px; }
    .form-group textarea { resize: vertical; min-height: 60px; }
    .form-group .hint { font-size: 12px; color: var(--muted); margin-top: 4px; }
    .form-group-checkbox { display: flex; align-items: center; gap: 8px; margin-bottom: 12px; }
    .form-group-checkbox label { font-family: var(--sans); font-size: 14px; color: var(--ink); margin: 0; text-transform: none; letter-spacing: 0; }
    .form-group-checkbox input[type="checkbox"] { width: 16px; height: 16px; }
    .form-radio { display: flex; gap: 16px; }
    .form-radio label { display: flex; align-items: center; gap: 6px; font-family: var(--sans); font-size: 14px; color: var(--ink); text-transform: none; letter-spacing: 0; cursor: pointer; }
    .form-error { color: var(--danger); font-size: 13px; margin-bottom: 12px; }
    .modal-footer { display: flex; justify-content: flex-end; gap: 12px; padding: 16px 20px; border-top: 1px solid var(--rule); }
    @media (max-width: 720px) { .toolbar { flex-direction: column; align-items: stretch; } .toolbar-field { flex-direction: column; align-items: stretch; } .toolbar-field input, .toolbar-field select { min-width: auto; } }
    .op-home-link { font-family: var(--mono); font-size: 12px; letter-spacing: 0.08em; text-transform: uppercase; color: var(--muted); text-decoration: none; padding: 4px 8px; border: 1px solid var(--rule); transition: border-color 120ms ease; display: inline-block; }
    .op-home-link:hover { border-color: var(--accent); color: var(--accent); }
  </style>
</head>
<body>
  <header class="op-header">
    <div class="op-header-row">
      <div class="op-header-titles">
        <a href="../../v2/pages/dashboard" class="op-home-link">← 首页</a>
        <span class="op-eyebrow">TikTok Shop · Interceptor</span>
        <h1 class="op-title">拦截配置</h1>
      </div>
      <div class="op-identity" id="ops-identity"></div>
    </div>
  </header>

  <main class="page-main">
    <div class="toolbar">
      <div class="toolbar-field">
        <label for="filter-domain">域名</label>
        <input id="filter-domain" type="text" placeholder="seller.tiktokglobalshop.com">
      </div>
      <div class="toolbar-field">
        <label for="filter-enabled">状态</label>
        <select id="filter-enabled">
          <option value="">全部</option>
          <option value="true">启用</option>
          <option value="false">禁用</option>
        </select>
      </div>
      <div class="toolbar-field">
        <label for="filter-search">搜索</label>
        <input id="filter-search" type="text" placeholder="域名或 endpoint">
      </div>
      <button class="btn-primary" id="btn-query">查询</button>
      <span class="toolbar-spacer"></span>
      <button class="btn-secondary" id="btn-import">导入</button>
      <button class="btn-secondary" id="btn-export">导出</button>
      <button class="btn-primary" id="btn-add">+ 新增配置</button>
    </div>

    <div class="toolbar" style="border-bottom: 0; padding-bottom: 0;">
      <button class="btn-secondary" id="btn-batch-enable">批量启用</button>
      <button class="btn-secondary" id="btn-batch-disable">批量禁用</button>
      <button class="btn-secondary" id="btn-batch-delete" style="color: var(--danger);">批量删除</button>
      <span class="toolbar-spacer"></span>
      <span style="font-family: var(--mono); font-size: 11px; color: var(--muted);">共 <span id="config-total">0</span> 条</span>
    </div>

    <div class="table-wrap">
      <table class="op-table">
        <thead>
          <tr>
            <th class="op-th" width="40"><input type="checkbox" id="select-all"></th>
            <th class="op-th" width="60">ID</th>
            <th class="op-th">域名</th>
            <th class="op-th">Endpoint</th>
            <th class="op-th" width="80">模式</th>
            <th class="op-th" width="80">状态</th>
            <th class="op-th" width="160">标签</th>
            <th class="op-th" width="80">操作</th>
          </tr>
        </thead>
        <tbody id="config-tbody">
          <tr><td colspan="8" class="op-empty">加载中…</td></tr>
        </tbody>
      </table>
    </div>

    <div class="pager">
      <button class="btn-secondary" id="btn-prev" disabled>← 上一页</button>
      <span id="pager-label">—</span>
      <button class="btn-secondary" id="btn-next" disabled>下一页 →</button>
    </div>
  </main>

  <!-- Config Modal -->
  <div class="modal-overlay" id="config-modal">
    <div class="modal-box">
      <div class="modal-header">
        <h2 id="modal-title">新增拦截配置</h2>
        <button class="modal-close" id="btn-modal-close">×</button>
      </div>
      <div class="modal-body">
        <div id="form-error" class="form-error"></div>
        <form id="config-form">
          <div class="form-group">
            <label for="form-domain">域名 *</label>
            <input id="form-domain" type="text" placeholder="seller.tiktokglobalshop.com">
            <div class="hint">输入完整域名</div>
          </div>
          <div class="form-group">
            <label for="form-endpoint">Endpoint *</label>
            <input id="form-endpoint" type="text" placeholder="/api/v1/orders/*">
            <div class="hint">支持 * 通配符，如 /api/* 匹配 /api/ 下所有路径</div>
          </div>
          <div class="form-group">
            <label>模式 *</label>
            <div class="form-radio">
              <label><input type="radio" name="mode" id="form-mode-whitelist" value="whitelist" checked> 白名单（记录匹配请求）</label>
              <label><input type="radio" name="mode" id="form-mode-blacklist" value="blacklist"> 黑名单（完全跳过）</label>
            </div>
            <div class="hint">白名单 = 记录 headers/body；黑名单 = 域名命中后完全不上传</div>
          </div>
          <div class="form-group">
            <label for="form-description">描述</label>
            <textarea id="form-description" placeholder="可选描述"></textarea>
          </div>
          <div class="form-group">
            <label for="form-tags">标签</label>
            <input id="form-tags" type="text" placeholder="订单, 物流 (逗号分隔)">
            <div class="hint">多个标签用逗号分隔</div>
          </div>
          <div class="form-group-checkbox">
            <input id="form-capture-headers" type="checkbox" checked>
            <label for="form-capture-headers">记录请求/响应头</label>
          </div>
          <div class="form-group-checkbox">
            <input id="form-capture-body" type="checkbox" checked>
            <label for="form-capture-body">记录请求/响应体</label>
          </div>
          <div class="form-group">
            <label>状态</label>
            <div class="form-radio">
              <label><input type="radio" name="enabled" id="form-enabled" value="true" checked> 启用</label>
              <label><input type="radio" name="enabled" value="false"> 禁用</label>
            </div>
          </div>
        </form>
      </div>
      <div class="modal-footer">
        <button class="btn-secondary" id="btn-cancel">取消</button>
        <button class="btn-primary" id="btn-save">保存</button>
      </div>
    </div>
  </div>

  <script src="../../static/js/intercept-configs.js?v=__JSV_INTERCEPT_CONFIGS__" defer></script>
</body>
</html>
"""


_INTERCEPT_REQUESTS_PAGE_HTML = """<!doctype html>
<html lang="zh-Hans">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>拦截记录 · tts-erp</title>
  <link rel="stylesheet" href="../../static/vendor/bootstrap.min.css">
  <style>
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
      --mono: 'JetBrains Mono', 'SF Mono', 'Cascadia Mono', Consolas, ui-monospace, monospace;
      --sans: 'Inter', 'Noto Sans SC', 'Source Han Sans SC', 'PingFang SC', 'Hiragino Sans GB', 'Microsoft YaHei', system-ui, -apple-system, sans-serif;
      --serif: 'Noto Serif SC', 'Source Han Serif SC', Georgia, ui-serif, serif;
    }
    * { box-sizing: border-box; }
    html, body {
      background: var(--paper);
      color: var(--ink);
      font-family: var(--sans);
      font-size: 16px;
      line-height: 1.6;
      margin: 0;
      -webkit-font-smoothing: antialiased;
      -moz-osx-font-smoothing: grayscale;
      text-rendering: optimizeLegibility;
    }
    a { color: var(--accent); text-decoration: none; }
    a:hover { color: var(--accent-deep); }
    .op-header { border-bottom: 1px solid var(--rule); padding: 18px 28px 14px; background: var(--paper); }
    .op-header-row { display: flex; align-items: flex-end; justify-content: space-between; gap: 24px; flex-wrap: wrap; }
    .op-header-titles { display: flex; flex-direction: column; gap: 2px; }
    .op-eyebrow { font-family: var(--mono); font-size: 13px; letter-spacing: 0.14em; text-transform: uppercase; color: var(--muted); }
    .op-title { font-family: var(--serif); font-weight: 600; font-size: 26px; margin: 0; letter-spacing: -0.01em; }
    .op-identity { font-family: var(--mono); font-size: 14px; color: var(--muted); }
    .page-main { max-width: 1280px; margin: 0 auto; padding: 24px 28px 64px; }
    .toolbar { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; margin-bottom: 16px; padding-bottom: 16px; border-bottom: 1px solid var(--rule-soft); }
    .toolbar-field { display: flex; align-items: center; gap: 8px; }
    .toolbar-field label { font-family: var(--mono); font-size: 12px; letter-spacing: 0.08em; text-transform: uppercase; color: var(--muted); white-space: nowrap; }
    .toolbar-field input, .toolbar-field select { font-family: var(--sans); font-size: 13px; background: transparent; border: 1px solid var(--rule); padding: 6px 10px; color: var(--ink); border-radius: 0; min-width: 120px; }
    .toolbar-field input:focus, .toolbar-field select:focus { outline: 2px solid var(--accent); outline-offset: 1px; }
    .toolbar-spacer { flex: 0 1 12px; }
    .btn-primary { font-family: var(--mono); font-size: 12px; font-weight: 600; letter-spacing: 0.14em; text-transform: uppercase; padding: 8px 16px; background: var(--ink); color: var(--paper); border: 0; cursor: pointer; border-radius: 0; transition: background 120ms ease; }
    .btn-primary:hover { background: var(--accent); }
    .btn-secondary { font-family: var(--mono); font-size: 12px; letter-spacing: 0.08em; text-transform: uppercase; padding: 8px 16px; background: transparent; color: var(--ink); border: 1px solid var(--rule); cursor: pointer; border-radius: 0; transition: border-color 120ms ease; }
    .btn-secondary:hover { border-color: var(--accent); }
    .btn-secondary:disabled { opacity: 0.45; cursor: not-allowed; }
    .btn-icon { background: transparent; border: 0; cursor: pointer; font-size: 16px; padding: 4px; }
    .btn-icon:hover { opacity: 0.7; }
    .table-wrap { overflow-x: auto; }
    .op-table { width: 100%; border-collapse: collapse; font-family: var(--sans); font-size: 13px; background: var(--paper); }
    .op-th { text-align: left; font-family: var(--mono); font-size: 12px; letter-spacing: 0.14em; text-transform: uppercase; color: var(--muted); font-weight: 500; padding: 12px 12px; border-bottom: 1px solid var(--rule); white-space: nowrap; }
    .op-table td { padding: 10px 12px; border-bottom: 1px solid var(--rule-soft); vertical-align: middle; }
    .op-table tbody tr:hover { background: var(--paper-deep); }
    .op-table tbody tr.clickable-row { cursor: pointer; }
    .mono { font-family: var(--mono); font-size: 12px; }
    .badge { display: inline-block; font-family: var(--mono); font-size: 11px; font-weight: 500; letter-spacing: 0.06em; padding: 3px 8px; border-radius: 0; }
    .badge-ok { background: var(--ok); color: #fff; }
    .badge-muted { background: var(--rule); color: var(--ink); }
    .method-badge { font-family: var(--mono); font-size: 10px; font-weight: 600; padding: 2px 6px; letter-spacing: 0.04em; }
    .method-get { color: var(--ok); }
    .method-post { color: var(--accent); }
    .method-put { color: #8a6d1a; }
    .method-delete { color: var(--danger); }
    .status-ok { color: var(--ok); }
    .status-err { color: var(--danger); }
    .td-host { max-width: 200px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
    .td-path { max-width: 240px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
    .pager { display: flex; align-items: center; gap: 18px; padding-top: 16px; border-top: 1px solid var(--rule-soft); font-family: var(--sans); font-size: 14px; }
    .op-empty, .op-error { text-align: center; padding: 48px 20px; color: var(--muted); font-family: var(--mono); font-size: 13px; letter-spacing: 0.08em; }
    .op-error { color: var(--danger); }
    .modal-overlay { position: fixed; inset: 0; display: none; align-items: center; justify-content: center; background: rgba(20, 16, 10, 0.86); z-index: 1000; }
    .modal-overlay.is-open { display: flex; }
    .modal-box { background: var(--paper); border: 1px solid var(--rule); width: 720px; max-width: 95vw; max-height: 90vh; overflow-y: auto; }
    .modal-header { display: flex; align-items: center; justify-content: space-between; padding: 16px 20px; border-bottom: 1px solid var(--rule); }
    .modal-header h2 { font-family: var(--serif); font-size: 20px; font-weight: 600; margin: 0; }
    .modal-close { background: transparent; border: 0; font-size: 24px; cursor: pointer; color: var(--muted); line-height: 1; }
    .modal-close:hover { color: var(--accent); }
    .modal-body { padding: 20px; }
    .detail-grid { display: flex; flex-direction: column; gap: 20px; }
    .detail-section { border: 1px solid var(--rule-soft); padding: 16px; }
    .detail-section-title { font-family: var(--mono); font-size: 11px; letter-spacing: 0.14em; text-transform: uppercase; color: var(--muted); margin: 0 0 12px 0; padding-bottom: 8px; border-bottom: 1px solid var(--rule-soft); }
    .detail-row { display: flex; gap: 12px; padding: 6px 0; border-bottom: 1px solid var(--rule-soft); }
    .detail-row:last-child { border-bottom: 0; }
    .detail-label { font-family: var(--mono); font-size: 11px; letter-spacing: 0.08em; text-transform: uppercase; color: var(--muted); min-width: 100px; flex-shrink: 0; }
    .detail-value { font-family: var(--sans); font-size: 13px; word-break: break-all; }
    .detail-url { word-break: break-all; }
    .detail-pre { background: var(--paper-deep); border: 1px solid var(--rule-soft); padding: 12px; font-family: var(--mono); font-size: 12px; overflow-x: auto; margin: 0; white-space: pre-wrap; word-break: break-all; max-height: 300px; overflow-y: auto; }
    .text-danger { color: var(--danger); }
    @media (max-width: 720px) { .toolbar { flex-direction: column; align-items: stretch; } .toolbar-field { flex-direction: column; align-items: stretch; } .toolbar-field input, .toolbar-field select { min-width: auto; } }
    .op-home-link { font-family: var(--mono); font-size: 12px; letter-spacing: 0.08em; text-transform: uppercase; color: var(--muted); text-decoration: none; padding: 4px 8px; border: 1px solid var(--rule); transition: border-color 120ms ease; display: inline-block; }
    .op-home-link:hover { border-color: var(--accent); color: var(--accent); }
  </style>
</head>
<body>
  <header class="op-header">
    <div class="op-header-row">
      <div class="op-header-titles">
        <a href="../../v2/pages/dashboard" class="op-home-link">← 首页</a>
        <span class="op-eyebrow">TikTok Shop · Interceptor</span>
        <h1 class="op-title">拦截记录</h1>
      </div>
      <div class="op-identity" id="ops-identity"></div>
    </div>
  </header>

  <main class="page-main">
    <div class="toolbar">
      <div class="toolbar-field">
        <label for="filter-host">域名</label>
        <input id="filter-host" type="text" placeholder="seller.tiktokglobalshop.com">
      </div>
      <div class="toolbar-field">
        <label for="filter-path">路径</label>
        <input id="filter-path" type="text" placeholder="/api/v1/orders">
      </div>
      <div class="toolbar-field">
        <label for="filter-status">状态码</label>
        <input id="filter-status" type="text" placeholder="200">
      </div>
      <div class="toolbar-field">
        <label for="filter-whitelisted">白名单</label>
        <select id="filter-whitelisted">
          <option value="">全部</option>
          <option value="true">是</option>
          <option value="false">否</option>
        </select>
      </div>
      <div class="toolbar-field">
        <label for="filter-method">方法</label>
        <select id="filter-method">
          <option value="">全部</option>
          <option value="GET">GET</option>
          <option value="POST">POST</option>
          <option value="PUT">PUT</option>
          <option value="DELETE">DELETE</option>
        </select>
      </div>
      <div class="toolbar-field">
        <label for="filter-from">起始</label>
        <input id="filter-from" type="date">
      </div>
      <div class="toolbar-field">
        <label for="filter-to">截止</label>
        <input id="filter-to" type="date">
      </div>
      <button class="btn-primary" id="btn-query">查询</button>
    </div>

    <div class="toolbar" style="border-bottom: 0; padding-bottom: 0;">
      <span style="font-family: var(--mono); font-size: 11px; color: var(--muted);">共 <span id="req-total">0</span> 条</span>
    </div>

    <div class="table-wrap">
      <table class="op-table">
        <thead>
          <tr>
            <th class="op-th" width="160">时间</th>
            <th class="op-th" width="70">方法</th>
            <th class="op-th">域名</th>
            <th class="op-th">路径</th>
            <th class="op-th" width="70">状态</th>
            <th class="op-th" width="80">耗时</th>
            <th class="op-th" width="60">白名单</th>
            <th class="op-th" width="50">详情</th>
          </tr>
        </thead>
        <tbody id="req-tbody">
          <tr><td colspan="8" class="op-empty">加载中…</td></tr>
        </tbody>
      </table>
    </div>

    <div class="pager">
      <button class="btn-secondary" id="btn-prev" disabled>← 上一页</button>
      <span id="pager-label">—</span>
      <button class="btn-secondary" id="btn-next" disabled>下一页 →</button>
    </div>
  </main>

  <!-- Detail Modal -->
  <div class="modal-overlay" id="detail-modal">
    <div class="modal-box">
      <div class="modal-header">
        <h2 id="modal-title">请求详情</h2>
        <button class="modal-close" id="btn-modal-close">×</button>
      </div>
      <div class="modal-body" id="detail-content">
        <div class="op-empty">加载中…</div>
      </div>
    </div>
  </div>

  <script src="../../static/js/intercept-requests.js?v=__JSV_INTERCEPT_REQUESTS__" defer></script>
</body>
</html>
"""


_INTERCEPT_STATS_PAGE_HTML = """<!doctype html>
<html lang="zh-Hans">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>拦截统计 · tts-erp</title>
  <link rel="stylesheet" href="../../static/vendor/bootstrap.min.css">
  <style>
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
      --mono: 'JetBrains Mono', 'SF Mono', 'Cascadia Mono', Consolas, ui-monospace, monospace;
      --sans: 'Inter', 'Noto Sans SC', 'Source Han Sans SC', 'PingFang SC', 'Hiragino Sans GB', 'Microsoft YaHei', system-ui, -apple-system, sans-serif;
      --serif: 'Noto Serif SC', 'Source Han Serif SC', Georgia, ui-serif, serif;
    }
    * { box-sizing: border-box; }
    html, body {
      background: var(--paper);
      color: var(--ink);
      font-family: var(--sans);
      font-size: 16px;
      line-height: 1.6;
      margin: 0;
      -webkit-font-smoothing: antialiased;
      -moz-osx-font-smoothing: grayscale;
      text-rendering: optimizeLegibility;
    }
    a { color: var(--accent); text-decoration: none; }
    a:hover { color: var(--accent-deep); }
    .op-header { border-bottom: 1px solid var(--rule); padding: 18px 28px 14px; background: var(--paper); }
    .op-header-row { display: flex; align-items: flex-end; justify-content: space-between; gap: 24px; flex-wrap: wrap; }
    .op-header-titles { display: flex; flex-direction: column; gap: 2px; }
    .op-eyebrow { font-family: var(--mono); font-size: 13px; letter-spacing: 0.14em; text-transform: uppercase; color: var(--muted); }
    .op-title { font-family: var(--serif); font-weight: 600; font-size: 26px; margin: 0; letter-spacing: -0.01em; }
    .op-identity { font-family: var(--mono); font-size: 14px; color: var(--muted); }
    .page-main { max-width: 1280px; margin: 0 auto; padding: 24px 28px 64px; }
    .stats-grid { display: grid; grid-template-columns: repeat(4, 1fr); gap: 16px; margin-bottom: 32px; }
    @media (max-width: 768px) { .stats-grid { grid-template-columns: repeat(2, 1fr); } }
    .stat-card { background: var(--paper); border: 1px solid var(--rule); padding: 20px; text-align: center; }
    .stat-icon { font-size: 28px; margin-bottom: 8px; }
    .stat-value { font-family: var(--mono); font-weight: 700; font-size: 32px; letter-spacing: -0.02em; font-variant-numeric: tabular-nums; line-height: 1.1; }
    .stat-value-warn { color: var(--accent); }
    .stat-value-ok { color: var(--ok); }
    .stat-label { font-size: 13px; color: var(--ink-soft); margin-top: 4px; }
    .stat-hint { font-size: 11px; color: var(--muted); margin-top: 4px; }
    .section { margin-bottom: 32px; }
    .section-title { font-family: var(--mono); font-size: 11px; letter-spacing: 0.14em; text-transform: uppercase; color: var(--muted); font-weight: 500; margin-bottom: 16px; padding-bottom: 12px; border-bottom: 1px solid var(--rule-soft); }
    .dist-row { display: flex; align-items: center; gap: 12px; padding: 8px 0; border-bottom: 1px solid var(--rule-soft); }
    .dist-row:last-child { border-bottom: 0; }
    .dist-label { font-family: var(--mono); font-size: 12px; min-width: 200px; flex-shrink: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
    .dist-bar-wrap { flex: 1; height: 20px; background: var(--paper-deep); border: 1px solid var(--rule-soft); }
    .dist-bar { height: 100%; background: var(--accent); transition: width 300ms ease; }
    .dist-value { font-family: var(--mono); font-size: 12px; min-width: 120px; text-align: right; color: var(--ink-soft); }
    .dist-empty { text-align: center; padding: 24px; color: var(--muted); font-size: 13px; }
    .daily-chart { display: flex; align-items: flex-end; gap: 8px; height: 200px; padding: 16px 0; }
    .daily-col { flex: 1; display: flex; flex-direction: column; align-items: center; height: 100%; }
    .daily-bar-wrap { flex: 1; width: 100%; display: flex; align-items: flex-end; }
    .daily-bar { width: 100%; background: var(--accent); transition: height 300ms ease; min-height: 2px; }
    .daily-count { font-family: var(--mono); font-size: 11px; color: var(--ink-soft); margin-top: 4px; }
    .daily-date { font-family: var(--mono); font-size: 10px; color: var(--muted); margin-top: 2px; }
    .loading { text-align: center; padding: 48px; color: var(--muted); font-family: var(--mono); font-size: 12px; }
    .error-msg { text-align: center; padding: 24px; color: var(--danger); font-size: 13px; display: none; }
    .op-home-link { font-family: var(--mono); font-size: 12px; letter-spacing: 0.08em; text-transform: uppercase; color: var(--muted); text-decoration: none; padding: 4px 8px; border: 1px solid var(--rule); transition: border-color 120ms ease; display: inline-block; }
    .op-home-link:hover { border-color: var(--accent); color: var(--accent); }
  </style>
</head>
<body>
  <header class="op-header">
    <div class="op-header-row">
      <div class="op-header-titles">
        <a href="../../v2/pages/dashboard" class="op-home-link">← 首页</a>
        <span class="op-eyebrow">TikTok Shop · Interceptor</span>
        <h1 class="op-title">拦截统计</h1>
      </div>
      <div class="op-identity" id="ops-identity"></div>
    </div>
  </header>

  <main class="page-main">
    <div id="loading" class="loading">加载中…</div>
    <div id="error-msg" class="error-msg"></div>

    <div class="stats-grid">
      <div class="stat-card">
        <div class="stat-icon">📊</div>
        <div class="stat-value" id="stat-total-requests">—</div>
        <div class="stat-label">总请求数</div>
        <div class="stat-hint">最近 7 天</div>
      </div>
      <div class="stat-card">
        <div class="stat-icon">✅</div>
        <div class="stat-value stat-value-ok" id="stat-whitelisted">—</div>
        <div class="stat-label">白名单命中</div>
        <div class="stat-hint">完整记录的请求</div>
      </div>
      <div class="stat-card">
        <div class="stat-icon">📈</div>
        <div class="stat-value" id="stat-today-requests">—</div>
        <div class="stat-label">今日请求</div>
        <div class="stat-hint">今天的拦截数</div>
      </div>
      <div class="stat-card">
        <div class="stat-icon">⚠️</div>
        <div class="stat-value stat-value-warn" id="stat-error-requests">—</div>
        <div class="stat-label">错误请求</div>
        <div class="stat-hint">最近 7 天</div>
      </div>
    </div>

    <div class="section">
      <div class="section-title">最近 7 天每日趋势</div>
      <div class="daily-chart" id="daily-chart">
        <div class="dist-empty">加载中…</div>
      </div>
    </div>

    <div class="section">
      <div class="section-title">按域名分布</div>
      <div id="dist-by-host">
        <div class="dist-empty">加载中…</div>
      </div>
    </div>

    <div class="section">
      <div class="section-title">按方法分布</div>
      <div id="dist-by-method">
        <div class="dist-empty">加载中…</div>
      </div>
    </div>

    <div class="section">
      <div class="section-title">按状态码分布</div>
      <div id="dist-by-status">
        <div class="dist-empty">加载中…</div>
      </div>
    </div>
  </main>

  <script src="../../static/js/intercept-stats.js?v=__JSV_INTERCEPT_STATS__" defer></script>
</body>
</html>
"""

# ── enum-map 管理页面 ─────────────────────────────────────────────────

_ENUM_MAP_PAGE_HTML = """<!doctype html>
<html lang="zh-Hans">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>枚举映射管理 · tts-erp</title>
  <link rel="stylesheet" href="../../static/vendor/bootstrap.min.css">
  <style>
    :root {
      --mono: 'JetBrains Mono', 'SF Mono', Consolas, monospace;
      --sans: 'Inter', 'Noto Sans SC', 'PingFang SC', 'Microsoft YaHei', system-ui, sans-serif;
      --bg: #f8f6f3; --card: #fff; --border: #e2ddd5; --accent: #5b6abf;
      --text: #2c2c2c; --muted: #888; --danger: #c0392b;
    }
    html, body { font-family: var(--sans); font-size: 14px; background: var(--bg); color: var(--text); margin: 0; }
    .wrap { max-width: 960px; margin: 0 auto; padding: 24px 20px 64px; }
    .header { display: flex; align-items: center; gap: 16px; margin-bottom: 24px; flex-wrap: wrap; }
    .header h1 { font-size: 20px; margin: 0; }
    .header a { font-family: var(--mono); font-size: 12px; color: var(--muted); text-decoration: none; border: 1px solid var(--border); padding: 3px 8px; }
    .header a:hover { border-color: var(--accent); color: var(--accent); }
    .tabs { display: flex; gap: 4px; flex-wrap: wrap; margin-bottom: 16px; }
    .tabs button { font-family: var(--sans); font-size: 13px; padding: 6px 14px; border: 1px solid var(--border); background: var(--card); cursor: pointer; border-radius: 4px; }
    .tabs button.is-active { background: var(--accent); color: #fff; border-color: var(--accent); }
    .tabs button:hover:not(.is-active) { border-color: var(--accent); }
    table { width: 100%; border-collapse: collapse; background: var(--card); }
    th, td { padding: 8px 12px; border: 1px solid var(--border); text-align: left; font-size: 13px; }
    th { background: #f0ede8; font-weight: 600; }
    td input { width: 100%; border: 1px solid var(--border); padding: 4px 6px; font-size: 13px; font-family: var(--sans); box-sizing: border-box; }
    td input:focus { outline: none; border-color: var(--accent); }
    .actions { display: flex; gap: 6px; }
    .btn { font-family: var(--sans); font-size: 12px; padding: 4px 10px; border: 1px solid var(--border); background: var(--card); cursor: pointer; border-radius: 3px; }
    .btn:hover { border-color: var(--accent); }
    .btn-danger { color: var(--danger); border-color: var(--danger); }
    .btn-danger:hover { background: var(--danger); color: #fff; }
    .btn-primary { background: var(--accent); color: #fff; border-color: var(--accent); }
    .btn-primary:hover { opacity: 0.85; }
    .add-row { display: flex; gap: 8px; align-items: center; margin-top: 12px; padding: 10px 12px; background: var(--card); border: 1px solid var(--border); }
    .add-row input { flex: 1; min-width: 120px; border: 1px solid var(--border); padding: 5px 8px; font-size: 13px; }
    .toast { position: fixed; bottom: 20px; right: 20px; padding: 10px 18px; background: #27ae60; color: #fff; border-radius: 4px; font-size: 13px; opacity: 0; transition: opacity 0.3s; pointer-events: none; }
    .toast.show { opacity: 1; }
    .empty { padding: 40px; text-align: center; color: var(--muted); }
  </style>
</head>
<body>
  <div class="wrap">
    <div class="header">
      <a href="../../v2/pages/spu-roi">← SPU ROI</a>
      <h1>枚举映射管理</h1>
    </div>
    <div class="tabs" id="type-tabs"></div>
    <table>
      <thead><tr><th>英文值</th><th>中文标签</th><th>排序</th><th>操作</th></tr></thead>
      <tbody id="rows"></tbody>
    </table>
    <div class="add-row" id="add-row">
      <input id="add-value" placeholder="英文值" autocomplete="off">
      <input id="add-label" placeholder="中文标签" autocomplete="off">
      <input id="add-sort" placeholder="排序" type="number" value="0" style="width:60px;flex:none">
      <button class="btn btn-primary" id="btn-add">添加</button>
    </div>
  </div>
  <div class="toast" id="toast"></div>
  <script src="../../static/js/enum-map.js" defer></script>
</body>
</html>
"""


@router.get("/enum-map", response_class=HTMLResponse)
def enum_map_page() -> HTMLResponse:
  """枚举映射管理页面。CRUD 管理 config.enum_map 枚举翻译。"""
  return _page(_ENUM_MAP_PAGE_HTML, current_page="enum-map")
