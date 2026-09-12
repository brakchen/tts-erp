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


# Cache-busting (2026-09-06): the operator console has no Cache-Control on
# /static, so browsers heuristically cache console.js / spu-roi.js and keep
# serving a stale build until a manual hard refresh. Each page appends
# ?v=<hash-of-content> to its script src: the version only changes when the
# file's bytes change, so a deploy ships a new URL and the browser fetches
# the fresh JS without any manual refresh.
_JS_DIR = Path(__file__).resolve().parents[2] / "static" / "js"


def _js_version(filename: str) -> str:
  try:
    digest = hashlib.sha256((_JS_DIR / filename).read_bytes()).hexdigest()
  except OSError:
    return "0"
  return digest[:8]


def _page(html: str) -> HTMLResponse:
  """Render a page template, stamping the JS cache-bust versions."""
  return HTMLResponse(
    html.replace("__JSV_CONSOLE__", _js_version("console.js"))
    .replace("__JSV_SPU_ROI__", _js_version("spu-roi.js"))
    .replace("__JSV_SHOPS__", _js_version("shops.js"))
    .replace("__JSV_DASHBOARD__", _js_version("dashboard.js"))
    .replace("__JSV_INTERCEPT_CONFIGS__", _js_version("intercept-configs.js"))
    .replace("__JSV_INTERCEPT_REQUESTS__", _js_version("intercept-requests.js"))
    .replace("__JSV_INTERCEPT_STATS__", _js_version("intercept-stats.js"))
  )


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
  return _page(_SHOPS_PAGE_HTML)


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
    .form-grid { display: grid; grid-template-columns: 1.2fr 1.4fr 0.6fr 0.8fr auto; gap: 14px; align-items: end; }
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
    @media (max-width: 720px) {
      .form-grid { grid-template-columns: 1fr; }
      .op-header { padding: 14px 16px 10px; }
      .page-main { padding: 16px 16px 48px; }
      .op-section { padding: 16px; }
    }
  </style>
</head>
<body>
  <header class="op-header">
    <div class="op-header-row">
      <div class="op-header-titles">
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
        <div class="form-field">
          <button type="submit" class="btn-primary">注册</button>
        </div>
      </form>
      <div class="form-hint">重复注册幂等：只补填仍为空的字段，不会覆盖已有 credential / 状态。</div>
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
          <th class="op-th">开店日期</th>
          <th class="op-th">同步方式</th>
        </tr></thead>
        <tbody id="shop-body"><tr><td colspan="5" class="op-empty">加载中…</td></tr></tbody>
      </table>
    </section>
  </main>
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
  return _page(_SPU_ROI_PAGE_HTML)


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
  return _page(_PAGE_HTML)


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
  </style>
</head>
<body>
  <header class="op-header">
    <div class="op-header-row">
      <div class="op-header-titles">
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
# 页面布局 2026-09-06 重构为 Bootstrap 5.3.8(自托管 static/vendor/bootstrap.min.css):
#   - 结构全部用 bootstrap 工具/栅格类(row/col-*, flex-wrap, gap-*, py/px)驱动响应式;
#   - 自定义 CSS 只留两件事:① warm-paper token 皮肤(:root 把 --bs-* 主题变量收编到同套 token,
#     border-radius 归零工业直角);② JS 依赖的行为类(data-tip 气泡 / ⚙ 列开关 / lightbox /
#     §7.2 标色 / 结余带数字),这些 JS 逐字渲染不可改名。
#   - 移动端:结余带 xs 2 / sm 3 / md 4 / lg 5(两行);工具栏 flex-wrap 自然纵向堆叠;表格
#     .table-responsive + max-height 双轴滚动框:表头在框内吸顶、首列横向溢出时吸左,
#     小屏按 nth-child 裁次要列(广告数/取消率/退货率/保本ROI),避免手机上看 25 列大海。
_SPU_ROI_PAGE_HTML = """<!doctype html>
<html lang="zh-Hans">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>SPU 实际 ROI · tts-erp</title>
  <!-- Relative path: resolves to /static/... locally and /tts/static/... behind NGINX. Do not make absolute. -->
  <link rel="stylesheet" href="../../static/vendor/bootstrap.min.css">
  <link rel="stylesheet" href="../../static/css/spu-roi.css?v=__JSV_SPU_ROI__">
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
  </style>
<body>
  <header class="op-header">
    <div class="op-main px-2 px-md-4 py-3 d-flex flex-wrap justify-content-between align-items-end gap-2 gap-md-3">
      <div>
        <div class="op-eyebrow mb-1">TikTok Shop · Analytics</div>
        <h1 class="op-title mb-0">SPU 实际 ROI</h1>
      </div>
      <div class="d-flex flex-column align-items-start align-items-sm-end text-sm-end">
        <span class="op-identity" id="ops-identity"></span>
        <span class="op-scope-note" id="sum-stamp">ROI · 账页</span>
      </div>
    </div>
  </header>

  <main class="op-main">
    <!-- 结余带:row-cols 栅格降密度(xs 2 / sm 3 / md 4 / lg 5 两行),JS 只写 #sum-* 文本 + is-err/is-ok -->
    <section class="op-counter px-2 px-md-4 py-3 py-md-4" id="summaries" aria-live="polite">
      <div class="row g-2 g-md-3 text-center row-cols-2 row-cols-sm-3 row-cols-md-4 row-cols-lg-5">
        <div class="col"><span class="op-counter-item"><span class="op-counter-label">广告消耗<span class="op-hint" data-tip="广告消耗 = Σ real_cost_total（广告视图全窗口累计，USD；作为减项计入净利润）">?</span></span><span class="op-counter-num" id="sum-spend">—</span></span></div>
        <div class="col"><span class="op-counter-item"><span class="op-counter-label">有效销售<span class="op-hint" data-tip="有效销售订单金额 = Σ quantity×unit_price（状态口径 2026-09-06：白名单状态全部订单，含 COD 在途/待收款，下单即算）；退款不在此扣减，见「退款净额」">?</span></span><span class="op-counter-num" id="sum-sales">—</span></span></div>
        <div class="col"><span class="op-counter-item"><span class="op-counter-label">GMV<span class="op-hint" data-tip="全部订单销售额 = 白名单有效 ∪ 取消订单的原始行金额（下单即计，含 COD 在途未收款与取消单原额）；有效销售 + 取消单原额 = 全单口径；≠ 行内「平台GMV」广告归因口径">?</span></span><span class="op-counter-num" id="sum-gmv">—</span></span></div>
        <div class="col"><span class="op-counter-item"><span class="op-counter-label">有效单量<span class="op-hint" data-tip="有效订单数（跨可见 SPU 全局去重；按订单状态计：已付白名单状态全部订单，含 COD 在途/待收款，下单即算订单口径）">?</span></span><span class="op-counter-num" id="sum-orders">—</span></span></div>
        <div class="col"><span class="op-counter-item"><span class="op-counter-label">总单量<span class="op-hint" data-tip="总单量 = 有效订单数 + 取消订单数（按订单状态计，含 COD 在途与未收款取消；跨可见 SPU 去重）= 与 TikTok 订单管理一致">?</span></span><span class="op-counter-num" id="sum-total-orders">—</span></span></div>
        <div class="col"><span class="op-counter-item"><span class="op-counter-label">退款净额<span class="op-hint" data-tip="有效已付订单中已完结退款的净退款额 = 仅退款(REFUND_ONLY) + 退货退款(RETURN_AND_REFUND) 的退款金额，VND→USD 换算。不含：已付被取消订单退款（见 ⚙ 列开关『已付被取消』信息列）、异常单(UNPAID 等)退款、未关联到 SPU 的退款行（页脚『未归属退款 N 行』只计行数不计金额）。与『全损退款』不同维度：这里是退给客户的钱，货的成本损失在下一格">?</span></span><span class="op-counter-num" id="sum-refund">—</span></span></div>
        <div class="col"><span class="op-counter-item"><span class="op-counter-label">全损退款<span class="op-hint" data-tip="全损退款 = 已完结退货(RETURN_AND_REFUND)按全损计（M13b）= 退货件数 × 该 SPU 单位成本(USD)。单位成本：人工成本(MANUAL)有效行优先，未录入按默认 30 CNY/件(≈$4.43)换算。注意这是成本维度，不是退款金额（退款金额见上一格）；未关联 SPU 的退货件不计入。缺人工成本的 SPU 用默认值会在行内标 ⚠">?</span></span><span class="op-counter-num" id="sum-loss">—</span></span></div>
        <div class="col"><span class="op-counter-item"><span class="op-counter-label">取消单量<span class="op-hint" data-tip="取消订单数（status=CANCELLED，按订单状态计，含未收款即取消的 COD 拒收/超时单）。原始金额已计入 GMV；退款仅信息列展示、不计净额">?</span></span><span class="op-counter-num" id="sum-cancelled-orders">—</span></span></div>
        <div class="col"><span class="op-counter-item"><span class="op-counter-label">净利润<span class="op-hint" data-tip="净利润 = (有效销售 − 净退款) − 全部售出件货本 − 广告消耗 − 平台佣金估算（USD）；负值红字。状态口径：销售含 COD 在途未收款单，回款前偏乐观">?</span></span><span class="op-counter-num" id="sum-profit">—</span></span></div>
        <div class="col"><span class="op-counter-item"><span class="op-counter-label">整体实际 ROI<span class="op-hint" data-tip="实际 ROI = (有效销售 − 净退款 − 全损退款(M13b 货损成本)) ÷ 广告消耗（M14）；≥ 保本 = 赚，< 保本 = 亏（主判据）。状态口径：销售含 COD 在途未收款单；无广告消耗 → —">?</span></span><span class="op-counter-num" id="sum-roi">—</span></span></div>
      </div>
    </section>

    <!-- 工具栏:重写为统一行高 + 响应式(见 CSS .op-toolbar-row / .op-field--*) -->
    <section class="op-toolbar px-2 px-md-4 py-3" id="toolbar">
      <div class="op-toolbar-row">
        <label class="op-field op-field--search" for="filter-q">
          <span class="op-fld-label">搜索 spu_id</span>
          <input id="filter-q" type="search" class="form-control" placeholder="spu_id 或标题" autocomplete="off" aria-label="按 spu_id 或标题搜索">
        </label>
        <label class="op-field op-field--shop" for="filter-shop" data-tip="店铺筛选：仅看该店铺 SPU（默认全部店铺）">
          <span class="op-fld-label">店铺</span>
          <select id="filter-shop" class="form-select" aria-label="筛选店铺（全部店铺 = 不限）">
            <option value="">全部店铺</option>
          </select>
        </label>
        <label class="op-field op-field--date" for="filter-w-start" data-tip="销售/退款日期范围（空 = 全历史；广告窗口始终全量）">
          <span class="op-fld-label">起始日</span>
          <input id="filter-w-start" type="date" class="form-control" aria-label="销售/退款起始日期（空 = 不限）">
        </label>
        <label class="op-field op-field--date" for="filter-w-end" data-tip="销售/退款日期范围（空 = 全历史；含当日）">
          <span class="op-fld-label">截止日</span>
          <input id="filter-w-end" type="date" class="form-control" aria-label="销售/退款截止日期（空 = 不限）">
        </label>
        <label class="op-field op-field--limit" for="filter-limit">
          <span class="op-fld-label">每页</span>
          <select id="filter-limit" class="form-select" aria-label="每页条数">
            <option value="50">50</option>
            <option value="100" selected>100</option>
            <option value="200">200</option>
          </select>
        </label>
        <label class="op-field op-field--fee" for="filter-fee" data-tip="平台佣金费率：默认参考基线 0.308（2026-09-06 实测重定，可覆写）">
          <span class="op-fld-label">费率 %</span>
          <input id="filter-fee" type="text" class="form-control" placeholder="30.8" inputmode="decimal" autocomplete="off" aria-label="平台佣金费率（覆盖基线 0.308）">
        </label>
        <label class="op-field op-field--checkbox" for="filter-include-all" data-tip="默认只列出当前窗口内有广告或销售/退款活动的 SPU；勾选后，处于 ACTIVE 状态但没有任意活动（无投放 / 未出单）的 SPU 也会一并列出——这类行的 ROI / 金额显示 — 或「无投放」">
          <input id="filter-include-all" type="checkbox" aria-label="含无活动 SPU">
          <span class="op-fld-label">含无活动</span>
          <span class="op-hint" role="note" tabindex="0" aria-label="含无活动 SPU 的说明">?</span>
        </label>
        <button type="button" class="btn btn-sm op-btn op-btn-refresh" id="btn-refresh">刷新</button>
        <span class="op-sortable-note" id="sort-note">默认排序：实际 ROI ↑（最亏在前）</span>
      </div>
    </section>

    <!-- 主表 D8 精简为 7 列（商品 + 6 指标：广告消耗 / 有效GMV / 有效出单量 / 取消率% / 全损退款率% / 净利润） -->
    <div class="op-table-wrap table-responsive">
      <table class="op-table" aria-live="polite">
        <thead>
          <tr>
            <th scope="col" class="op-th op-th-left" width="140">商品</th>
            <th scope="col" class="op-th op-th-sort" data-sort="spend" data-tip="广告消耗（USD，广告窗口全量累计；作为减项计入净利润）" width="200">广告消耗</th>
            <th scope="col" class="op-th op-th-sort" data-sort="sales" data-tip="有效GMV = 白名单状态订单行金额（USD；排除已取消订单，B1 拍板）" width="200">有效GMV</th>
            <th scope="col" class="op-th op-th-sort" data-sort="order_count" data-tip="有效出单量 = 白名单有效订单数（distinct）" width="160">有效出单量</th>
            <th scope="col" class="op-th op-th-sort" data-sort="cancel_rate" data-tip="取消率 = 取消单量 ÷ (有效单量 + 取消单量)" width="180">取消率%</th>
            <th scope="col" class="op-th op-th-sort" data-sort="full_loss_rate" data-tip="全损退款率% = 全损件数(38301) ÷ (售出件数+全损取消件数);D4 B 口径;分母0 → —" width="200">全损退款率%</th>
            <th scope="col" class="op-th op-th-sort" data-sort="net_profit" data-tip="净利润 v7(M18):已结算 SETTLEMENT + 未结算 ×(1−r̂)×(1−退款率) − 货本含全损取消 − 广告;负值红字。Red/green 仅按净利判(C3 拍板,删 ROI&lt;1 硬亏档)" width="200">净利润</th>
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
          <div class="op-drill" data-state="loading">
            <nav class="op-drill-tabs" role="tablist">
              <button type="button" class="op-drill-tab is-active" role="tab" data-tab="pnl">利润构成</button>
              <button type="button" class="op-drill-tab" role="tab" data-tab="orders">订单·物流</button>
              <button type="button" class="op-drill-tab" role="tab" data-tab="settlements">结算</button>
              <button type="button" class="op-drill-tab" role="tab" data-tab="cases">售后</button>
              <button type="button" class="op-drill-tab" role="tab" data-tab="ads">广告</button>
            </nav>
            <div class="op-drill-banner" data-banner="warn" hidden></div>
            <div class="op-drill-summary" data-region="summary"></div>
            <div class="op-drill-body" data-region="body"><div class="op-drill-loading">加载中…</div></div>
          </div>
        </td>
      </tr>
    </template>

    <section class="op-pager px-2 px-md-4 d-flex flex-wrap align-items-center gap-3 py-3 pb-4">
      <button type="button" class="btn btn-sm op-btn" id="btn-prev">← 上一页</button>
      <span class="op-pager-page" id="pager-label">—</span>
      <button type="button" class="btn btn-sm op-btn" id="btn-next">下一页 →</button>
    </section>

    <section class="op-footnotes px-2 px-md-4 py-3 mb-4" id="footnotes">
      <span id="foot-meta">—</span>
      <span class="op-warn-chip">GMV Max 归因含自然单 · 广告数字仅供对照</span>
    </section>
  </main>

  <div id="ops-tip" role="tooltip" hidden></div>
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
  return _page(_DASHBOARD_PAGE_HTML)


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
  return _page(_INTERCEPT_CONFIGS_PAGE_HTML)


@router.get("/intercept-requests", response_class=HTMLResponse)
def intercept_requests_page() -> HTMLResponse:
  """拦截记录查询页面。

  功能：记录列表、详情弹窗、筛选（域名/路径/状态码/白名单/时间）、分页。
  行为在 static/js/intercept-requests.js。
  """
  return _page(_INTERCEPT_REQUESTS_PAGE_HTML)


@router.get("/intercept-stats", response_class=HTMLResponse)
def intercept_stats_page() -> HTMLResponse:
  """拦截统计概览页面。

  功能：总览统计卡片、按域名/方法/状态码分布、最近 7 天每日趋势。
  行为在 static/js/intercept-stats.js。
  """
  return _page(_INTERCEPT_STATS_PAGE_HTML)


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
  </style>
</head>
<body>
  <header class="op-header">
    <div class="op-header-row">
      <div class="op-header-titles">
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
            <th class="op-th" width="80">状态</th>
            <th class="op-th" width="160">标签</th>
            <th class="op-th" width="80">操作</th>
          </tr>
        </thead>
        <tbody id="config-tbody">
          <tr><td colspan="7" class="op-empty">加载中…</td></tr>
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
  </style>
</head>
<body>
  <header class="op-header">
    <div class="op-header-row">
      <div class="op-header-titles">
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
  </style>
</head>
<body>
  <header class="op-header">
    <div class="op-header-row">
      <div class="op-header-titles">
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
