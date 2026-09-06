"""/v2/pages/* — server-rendered HTML pages (no SPA framework).

The manual-costs page (the only Lane E page) renders a static HTML shell.
Styling is **Bootstrap 5.3.8**, self-hosted at ``/static/vendor/bootstrap.min.css``
(MIT — see ``static/vendor/NOTICE.md``); behaviour lives in
``/static/js/console.js``. No custom design system, no CDN links.

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

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

router = APIRouter(prefix="/v2/pages", tags=["pages"])


@router.get("/spu-roi", response_class=HTMLResponse)
def spu_roi_page() -> HTMLResponse:
    """SPU 实际 ROI 看板(账页式,§7 of tech-doc/analytics/spu-real-roi-dashboard.md)。

    HTML shell 只做骨架:标题/结余带/工具栏/表格容器/分页;数据与业务计算
    全部消费 GET /v2/analytics/spu-roi(只读,§5.1-1 页面不计算业务数字)。
    样式沿用操作台家族 token(warm-paper),行为在 static/js/spu-roi.js。
    """
    return HTMLResponse(_SPU_ROI_PAGE_HTML)


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
  </main>

  <script src="../../static/js/console.js" defer></script>
</body>
</html>
"""


# SPU 实际 ROI 看板(账页式)HTML shell — 结构见 tech-doc/analytics/spu-real-roi-dashboard.md §7。
# 只读:JS 消费 GET /v2/analytics/spu-roi;业务数字全在服务端算好(§5.1-1)。
_SPU_ROI_PAGE_HTML = """<!doctype html>
<html lang="zh-Hans">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>SPU 实际 ROI · tts-erp</title>
  <link rel="stylesheet" href="../../static/vendor/bootstrap.min.css">
  <style>
    /* warm-paper 工业操作台 token(与 manual-costs 同源,§7) */
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
      --warn: #A16207;
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
    body { background-color: var(--paper); }
    a { color: var(--accent); text-decoration: none; }
    a:hover { color: var(--accent-deep); }

    /* ---------- 头 ---------- */
    .op-header {
      border-bottom: 1px solid var(--rule);
      padding: 18px 28px 14px;
    }
    .op-header-row { display: flex; align-items: flex-end; justify-content: space-between; gap: 24px; flex-wrap: wrap; }
    .op-eyebrow {
      font-family: var(--sans); font-size: 12px; letter-spacing: 0.14em;
      text-transform: uppercase; color: var(--muted);
    }
    /* 页标题 = 唯一 serif 例外(大标题);正文统一 --sans */
    .op-title { font-family: var(--serif); font-weight: 700; font-size: 26px; margin: 0; letter-spacing: -0.01em; }
    .op-identity { font-family: var(--sans); font-size: 12px; color: var(--muted); }
    .op-identity code { font-family: var(--mono); color: var(--ink); }

    /* ---------- 结余带(家族 signature,§3.1/§7) ---------- */
    /* 结余带(汇总):数字加大、整带水平居中,每格标签/数字各自居中 */
    .op-counter {
      position: relative;
      display: flex; flex-wrap: wrap; justify-content: center; align-items: center;
      gap: 12px 40px;
      padding: 24px 28px 22px; border-bottom: 1px solid var(--rule);
      background: var(--paper);
      font-family: var(--sans); font-variant-numeric: tabular-nums;
    }
    .op-counter-item {
      display: inline-flex; flex-direction: column; align-items: center;
      gap: 6px; text-align: center; min-width: 88px;
    }
    .op-counter-label {
      font-family: var(--sans); font-size: 12px; font-weight: 500;
      letter-spacing: 0.08em; color: var(--muted); white-space: nowrap;
    }
    .op-counter-num { font-size: 32px; font-weight: 700; line-height: 1.1; color: var(--ink); white-space: nowrap; }
    .op-counter-num.is-err { color: var(--danger); }
    .op-counter-num.is-ok { color: var(--ok); }
    /* 口径戳移入页头(2026-09-06 ROI UI lane):原为结余带绝对定位右侧,
       窗口 1180–1600 时与居中的汇总数字重叠 → 改静态放 header 右侧,不再遮挡 */
    .op-header-side {
      display: flex; flex-direction: column; align-items: flex-end;
      gap: 5px; padding-bottom: 2px;
    }
    .op-scope-note {
      font-family: var(--sans); font-size: 11px; letter-spacing: 0.05em;
      color: var(--muted); user-select: none; text-align: right;
    }

    /* ---------- 列开关 ⚙(§7.5) ---------- */
    .op-colswitch {
      display: inline-flex; align-items: center; gap: 12px; flex-wrap: wrap;
      text-transform: none; letter-spacing: 0;
    }
    .op-cols-title { color: var(--muted); cursor: default; }
    .op-cols-item {
      display: inline-flex; align-items: center; gap: 5px; cursor: pointer;
      color: var(--ink);
    }
    .op-cols-item:hover { color: var(--accent); }
    .op-cols-item input { width: auto; margin: 0; cursor: pointer; accent-color: var(--accent); }
    .col-hidden { display: none; }

    /* ---------- 工具栏 ---------- */
    .op-toolbar {
      display: flex; align-items: center; gap: 22px; flex-wrap: wrap;
      padding: 13px 28px; border-bottom: 1px solid var(--rule);
      font-family: var(--sans); font-size: 13px; letter-spacing: 0;
      text-transform: none; color: var(--muted);
    }
    .op-search, .op-field { display: inline-flex; align-items: center; gap: 8px; }
    .op-input {
      font-family: var(--sans); font-size: 13px; background: transparent;
      border: 0; border-bottom: 1px solid var(--rule); color: var(--ink);
      padding: 4px 0; border-radius: 0;
    }
    .op-input:focus { outline: 0; border-bottom-color: var(--accent); }
    .op-input-search { width: 240px; text-transform: none; letter-spacing: 0; }
    .op-input-search::placeholder { color: var(--rule); }
    select.op-input { border: 1px solid var(--rule); padding: 3px 6px; cursor: pointer; }
    input.op-input-date { width: 150px; text-transform: none; letter-spacing: 0; color-scheme: light; }
    .op-btn {
      font-family: var(--sans); font-size: 13px; font-weight: 600;
      letter-spacing: 0; padding: 6px 14px;
      background: transparent; color: var(--ink); border: 1px solid var(--rule);
      cursor: pointer; border-radius: 0;
    }
    .op-btn:hover { border-color: var(--accent); color: var(--accent); }
    .op-btn:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
    .op-th-sort { cursor: pointer; user-select: none; }
    .op-th-sort:hover { color: var(--accent); }
    .op-th-sort .arrow { color: var(--accent); }
    .op-sortable-note { font-family: var(--sans); font-size: 12px; color: var(--muted); margin-left: 10px; }

    /* ---------- 主表 ---------- */
    .op-main { max-width: 1680px; margin: 0 auto; }
    .op-table-wrap { padding: 0 28px 30px; overflow-x: auto; }
    table.op-table { width: 100%; border-collapse: collapse; min-width: 1500px; }
    .op-th {
      text-align: right; font-family: var(--sans); font-size: 12px;
      letter-spacing: 0.04em; color: var(--muted);
      font-weight: 600; padding: 10px 8px; border-bottom: 1px solid var(--rule);
      white-space: nowrap; background: var(--paper);
    }
    .op-th-left { text-align: left; }
    thead.op-sticky .op-th { position: sticky; top: 0; z-index: 2; background: var(--paper); }
    tbody.op-rows td {
      padding: 10px 8px; border-bottom: 1px solid var(--rule-soft);
      text-align: right; white-space: nowrap; font-variant-numeric: tabular-nums;
      font-family: var(--sans); font-size: 13px;
    }
    tbody.op-rows td.td-left { text-align: left; }
    tbody.op-rows tr:hover { background: var(--paper-deep); }
    tr.row-bad { box-shadow: inset 2px 0 0 var(--danger); }
    tr.row-bad td { background: rgba(140, 26, 26, 0.045); }
    tr.row-bad .roi-red { color: var(--danger); font-weight: 700; }
    tr.row-bad .np-red { color: var(--danger); font-weight: 700; }
    /* §7.2 标色:实际ROI<1.0 更深红红底浅字(广告回本线) */
    .roi-hard {
      background: #7f1212; color: #fdf3ec; font-weight: 700;
      padding: 2px 6px;
    }
    /* §7.2:≥保本但 < 及格线 1.5 → 浅橙,不标红 */
    .roi-subpar { color: var(--warn); font-weight: 700; }
    /* §7.2:退款率 > 30% → 退款率红字 */
    .rr-high { color: var(--danger); font-weight: 700; }
    .warn-rr { color: var(--warn); cursor: help; font-size: 12px; }
    /* §5.1-5:无投放文案 */
    .no-ad { color: var(--muted); letter-spacing: 0.04em; }
    /* 原始 ID/状态码保留 mono 作为"代码"标注,其余全 sans */
    .td-spu { font-family: var(--mono); font-size: 12px; color: var(--muted); }
    .td-title { max-width: 300px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
    .td-null { color: var(--rule); }
    .warn-default { color: var(--warn); cursor: help; font-size: 12px; }
    /* SPU 主图格(2026-09-06 ROI UI lane):56px 主图(原 34px) + 右侧两行
       (spu_id / 标题) 垂直居中;主图可点击 → lightbox 放大 —— 与
       manual-costs 同一家族交互。无图 → 虚线占位,保持行高/对齐。 */
    .td-spu-cell { display: flex; align-items: center; gap: 12px; }
    .spu-img {
      width: 56px; height: 56px; flex: none; object-fit: cover;
      border: 1px solid var(--rule); background: var(--paper-deep);
      cursor: zoom-in;
    }
    .spu-img:hover { border-color: var(--accent); }
    .spu-img-missing {
      width: 56px; height: 56px; flex: none; display: flex;
      align-items: center; justify-content: center;
      border: 1px dashed var(--rule); background: var(--paper-deep);
      color: var(--rule); font-size: 10px; letter-spacing: 0.12em;
      font-family: var(--sans); user-select: none;
    }
    .td-spu-meta { display: flex; flex-direction: column; gap: 2px; min-width: 0; }
    .spu-status { font-family: var(--mono); font-size: 11px; color: var(--muted); margin-left: 8px; }
    .spu-status.is-down { color: var(--warn); }
    /* Lightbox:click 主图 → 全屏叠层;点背景(非放大图)/×/Esc 关闭。
       CSS 与 manual-costs 同款;JS 在 spu-roi.js(openLightbox)。 */
    .op-lightbox {
      position: fixed; inset: 0; display: none;
      align-items: center; justify-content: center;
      background: rgba(20, 16, 10, 0.86);
      z-index: 1000; cursor: zoom-out; padding: 40px;
    }
    .op-lightbox.is-open { display: flex; }
    .op-lightbox img {
      max-width: 100%; max-height: 100%; object-fit: contain;
      border: 1px solid var(--rule); background: var(--paper);
      cursor: default;
    }
    .op-lightbox-close {
      position: absolute; top: 12px; right: 18px;
      background: transparent; border: 0; color: var(--paper);
      font-size: 34px; line-height: 1; cursor: pointer;
    }
    .op-lightbox-close:hover { color: var(--accent); }

    /* ---------- 分页 ---------- */
    .op-pager { display: flex; align-items: center; gap: 18px; padding: 4px 28px 44px; font-family: var(--sans); font-size: 13px; }
    .op-pager-page { color: var(--ink); font-variant-numeric: tabular-nums; }
    .op-pager button.op-btn { padding: 4px 12px; }

    /* ---------- 提示行 ---------- */
    .op-footnotes {
      padding: 8px 28px 26px; font-size: 12px; color: var(--muted);
      border-top: 1px solid var(--rule-soft);
    }
    .op-warn-chip {
      display: inline-block; margin-left: 14px; padding: 2px 10px;
      border: 1px solid var(--warn); color: var(--warn); font-size: 12px;
      font-family: var(--sans);
    }
    .op-loading, .op-empty, .op-error { text-align: center; padding: 60px 20px !important; color: var(--muted); }
    .op-error { color: var(--danger); }
    .op-empty { color: var(--muted); }
    @media (prefers-reduced-motion: reduce) { * { transition: none !important; } }
    /* ---------- hover 说明气泡(data-tip,JS 委托;替代原生 title) ---------- */
    [data-tip] { cursor: help; }
    input[data-tip], select[data-tip], textarea[data-tip] { cursor: auto; }
    #ops-tip {
      position: fixed; z-index: 1000; max-width: 340px;
      background: var(--ink); color: var(--paper);
      font-family: var(--sans); font-size: 12px; line-height: 1.5;
      padding: 8px 11px; border-radius: 0;
      box-shadow: 0 2px 12px rgba(27, 24, 20, 0.28);
      pointer-events: none;
    }

    @media (max-width: 900px) {
      .op-counter, .op-toolbar, .op-table-wrap, .op-pager, .op-footnotes { padding-left: 14px; padding-right: 14px; }
      .op-counter { gap: 10px 22px; padding-top: 20px; padding-bottom: 18px; }
      .op-counter-num { font-size: 24px; }
    }
  </style>
</head>
<body>
  <header class="op-header">
    <div class="op-header-row">
      <div>
        <span class="op-eyebrow">TikTok Shop · Analytics</span>
        <h1 class="op-title">SPU 实际 ROI</h1>
      </div>
      <div class="op-header-side">
        <span class="op-identity" id="ops-identity"></span>
        <span class="op-scope-note" id="sum-stamp">ROI · 账页</span>
      </div>
    </div>
  </header>

  <main class="op-main">
    <section class="op-counter" id="summaries" aria-live="polite">
      <span class="op-counter-item"><span class="op-counter-label">SPU</span><span class="op-counter-num" id="sum-n">·</span></span>
      <span class="op-counter-item"><span class="op-counter-label">消耗 $</span><span class="op-counter-num" id="sum-spend">—</span></span>
      <span class="op-counter-item"><span class="op-counter-label">有效销售 $</span><span class="op-counter-num" id="sum-sales">—</span></span>
      <span class="op-counter-item"><span class="op-counter-label">退款净额 $</span><span class="op-counter-num" id="sum-refund">—</span></span>
      <span class="op-counter-item"><span class="op-counter-label">全损货损 $</span><span class="op-counter-num" id="sum-loss">—</span></span>
      <span class="op-counter-item"><span class="op-counter-label">净利润 $</span><span class="op-counter-num" id="sum-profit">—</span></span>
      <span class="op-counter-item"><span class="op-counter-label">整体实际 ROI</span><span class="op-counter-num" id="sum-roi">—</span></span>
    </section>

    <section class="op-toolbar" id="toolbar">
      <label class="op-search">
        <span>搜索 spu_id</span>
        <input id="filter-q" type="search" class="op-input op-input-search" placeholder="例如 1736527242804888823">
      </label>
      <label class="op-field" data-tip="店铺筛选：仅看该店铺 SPU（默认全部店铺）">
        <span>店铺</span>
        <select id="filter-shop" class="op-input" aria-label="筛选店铺（全部店铺 = 不限）">
          <option value="">全部店铺</option>
        </select>
      </label>
      <label class="op-field" data-tip="销售/退款日期范围（空 = 全历史；广告窗口始终全量）">
        <span>起始日</span>
        <input id="filter-w-start" type="date" class="op-input op-input-date" aria-label="销售/退款起始日期（空 = 不限）">
      </label>
      <label class="op-field" data-tip="销售/退款日期范围（空 = 全历史；含当日）">
        <span>截止日</span>
        <input id="filter-w-end" type="date" class="op-input op-input-date" aria-label="销售/退款截止日期（空 = 不限）">
      </label>
      <label class="op-field">
        <span>每页</span>
        <select id="filter-limit" class="op-input">
          <option value="50">50</option>
          <option value="100" selected>100</option>
          <option value="200">200</option>
        </select>
      </label>
      <label class="op-field" data-tip="平台佣金费率：默认参考基线 0.1156（可覆写）">
        <span>费率 %</span>
        <input id="filter-fee" type="text" class="op-input op-input-search" style="width:70px" placeholder="11.56" inputmode="decimal">
      </label>
      <label class="op-field">
        <span>含无活动</span>
        <input id="filter-include-all" type="checkbox" style="width:auto">
      </label>
      <span class="op-colswitch" id="colswitch" data-tip="列开关：显示/隐藏信息列（默认折叠）">
        <span class="op-cols-title">⚙ 列</span>
        <label class="op-cols-item"><input type="checkbox" id="col-toggle-refundsplit" class="col-toggle" data-colgroup="cg-refundsplit">仅退/退货拆分</label>
        <label class="op-cols-item"><input type="checkbox" id="col-toggle-cancel" class="col-toggle" data-colgroup="cg-cancel">已付被取消</label>
        <label class="op-cols-item"><input type="checkbox" id="col-toggle-fee" class="col-toggle" data-colgroup="cg-fee">平台佣金</label>
      </span>
      <button type="button" class="op-btn" id="btn-refresh">刷新</button>
      <span class="op-sortable-note" id="sort-note">默认排序：实际 ROI ↑（最亏在前）</span>
    </section>

    <div class="op-table-wrap">
      <table class="op-table" aria-live="polite">
        <thead class="op-sticky">
          <tr id="head-row">
            <th scope="col" class="op-th op-th-left">商品</th>
            <th scope="col" class="op-th op-th-sort" data-sort="ad_count">广告数</th>
            <th scope="col" class="op-th op-th-sort" data-sort="spend">消耗 USD</th>
            <th scope="col" class="op-th op-th-sort" data-sort="gmv_ad">平台GMV</th>
            <th scope="col" class="op-th">ROI₀</th>
            <th scope="col" class="op-th op-th-sort" data-sort="order_count">有效单</th>
            <th scope="col" class="op-th op-th-sort" data-sort="units_sold">件数</th>
            <th scope="col" class="op-th op-th-sort" data-sort="sales">销售$</th>
            <th scope="col" class="op-th col-hidden" data-cg="cg-refundsplit">仅退件</th>
            <th scope="col" class="op-th col-hidden" data-cg="cg-refundsplit">仅退$</th>
            <th scope="col" class="op-th col-hidden" data-cg="cg-refundsplit">退货件</th>
            <th scope="col" class="op-th col-hidden" data-cg="cg-refundsplit">退货$</th>
            <th scope="col" class="op-th op-th-sort" data-sort="refund_net_amount">退款净额$</th>
            <th scope="col" class="op-th op-th-sort" data-sort="refund_rate">退款率%</th>
            <th scope="col" class="op-th col-hidden" data-cg="cg-cancel">取消件</th>
            <th scope="col" class="op-th col-hidden" data-cg="cg-cancel">取消退款$</th>
            <th scope="col" class="op-th col-hidden" data-cg="cg-cancel">金额未知行</th>
            <th scope="col" class="op-th op-th-sort" data-sort="net_profit">净利润$</th>
            <th scope="col" class="op-th op-th-sort" data-sort="return_loss">货损$</th>
            <th scope="col" class="op-th col-hidden" data-cg="cg-fee">平台佣金$</th>
            <th scope="col" class="op-th op-th-sort" data-sort="roi_breakeven">保本</th>
            <th scope="col" class="op-th op-th-sort" data-sort="roi_real">实际ROI</th>
          </tr>
        </thead>
        <tbody class="op-rows" id="rows">
          <tr><td colspan="14" class="op-loading">加载中…</td></tr>
        </tbody>
      </table>
    </div>

    <section class="op-pager">
      <button type="button" class="op-btn" id="btn-prev">← 上一页</button>
      <span class="op-pager-page" id="pager-label">—</span>
      <button type="button" class="op-btn" id="btn-next">下一页 →</button>
    </section>

    <section class="op-footnotes" id="footnotes">
      <span id="foot-meta">—</span>
      <span class="op-warn-chip">GMV Max 归因含自然单 · 广告数字仅供对照</span>
    </section>
  </main>

  <div id="ops-tip" role="tooltip" hidden></div>
  <script src="../../static/js/spu-roi.js" defer></script>
</body>
</html>
"""
