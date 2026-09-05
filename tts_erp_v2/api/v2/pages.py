"""/v2/pages/* — server-rendered HTML pages (no SPA framework).

The manual-costs page (the only Lane E page) renders a static HTML shell.
Styling is **Bootstrap 5.3.8**, self-hosted at ``/static/vendor/bootstrap.min.css``
(MIT — see ``static/vendor/NOTICE.md``); behaviour lives in
``/static/js/console.js``. No custom design system, no CDN links.

Asset paths are RELATIVE (``../../static/…``) so the page works both on
``127.0.0.1:9877`` directly and behind the NGINX ``/tts`` prefix
(2026-08-31: absolute ``/static/…`` links 404'd behind the prefix).

Auth classification: the page is ``readonly``-equivalent for the GET
(handler does no DB writes). The page JS calls write endpoints
(``/v2/reporting/manual-costs``, ``/v2/spu-images/*``) — those require
a readwrite or admin session via the ``/v2/auth/login`` cookie flow.

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

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

router = APIRouter(prefix="/v2/pages", tags=["pages"])


@router.get("/manual-costs", response_class=HTMLResponse)
def manual_costs_page() -> HTMLResponse:
  """Manual cost entry + SPU image upload workbench.

  The HTML shell is a small stub:
  - links to ``/static/vendor/bootstrap.min.css`` (self-hosted, MIT)
  - inline ``<style>`` block for the industrial-console personality
  - links to ``/static/js/console.js`` (shop switcher, tabs, inline filing,
    drag-drop photo upload, envelope unwrap for backend pagination,
    signature-counter population)
  - the JS handles its own /v2/auth/me probe and redirects unauthenticated
    callers to ``/v2/auth/login?next=/v2/pages/manual-costs``
  """
  return HTMLResponse(_PAGE_HTML)


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
    .op-identity { font-family: var(--mono); font-size: 12px; color: var(--muted); }
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
    .op-table-wrap { padding: 0 28px 56px; }
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
    .op-select-currency {
      font-family: var(--mono);
      font-size: 11px;
      font-weight: 500;
      letter-spacing: 0.06em;
      padding: 6px 8px;
      border: 0;
      border-left: 1px solid var(--rule);
      background: var(--paper-deep);
      color: var(--ink);
      cursor: pointer;
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

    /* Drop zone */
    .op-dropzone {
      display: inline-block;
      border: 1px dashed var(--rule);
      padding: 7px 14px;
      font-family: var(--mono);
      font-size: 11px;
      letter-spacing: 0.06em;
      color: var(--muted);
      cursor: pointer;
      min-width: 180px;
      text-align: center;
      background: var(--paper);
      transition: border-color 120ms ease, color 120ms ease;
    }
    .op-dropzone:hover { border-color: var(--ink-soft); color: var(--ink-soft); }
    .op-dropzone.is-drag { border-color: var(--accent); color: var(--accent); background: var(--paper-deep); }
    .op-gallery { display: flex; gap: 6px; flex-wrap: wrap; margin-top: 8px; }
    .op-gallery img { width: 44px; height: 44px; object-fit: cover; border: 1px solid var(--rule); }

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
        <div class="op-counter-label">待处理</div>
        <div class="op-counter-sub">活跃 SPU · 缺成本 · 缺图片</div>
      </div>
      <div class="op-counter-stamp" aria-hidden="true">FILED · 01 / 02</div>
    </section>

    <nav class="op-tabs" role="tablist" aria-label="工作台标签页">
      <button class="op-tab op-tab-active" type="button" role="tab" data-tab="pending" aria-selected="true" aria-controls="grid-rows">
        待处理
        <span class="op-badge" id="badge-pending">·</span>
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
    </div>

    <div class="op-table-wrap">
      <table class="op-table" aria-live="polite">
        <thead>
          <tr id="grid-head-pending">
            <th scope="col" class="op-th op-th-sku">SKU</th>
            <th scope="col" class="op-th op-th-title">标题</th>
            <th scope="col" class="op-th op-th-cost">单位成本</th>
            <th scope="col" class="op-th op-th-note">备注</th>
            <th scope="col" class="op-th op-th-photo">图片</th>
            <th scope="col" class="op-th op-th-action">操作</th>
          </tr>
        </thead>
        <tbody id="grid-rows">
          <tr><td colspan="6" class="op-loading">加载店铺中…</td></tr>
        </tbody>
      </table>
    </div>
  </main>

  <script src="../../static/js/console.js" defer></script>
</body>
</html>
"""
