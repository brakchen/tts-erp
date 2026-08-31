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
  - links to ``/static/js/console.js`` (shop switcher, tabs, inline filing,
    drag-drop photo upload, envelope unwrap for backend pagination)
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
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>tts-erp · 采购工作台</title>
  <!-- Relative path: resolves to /static/... locally and /tts/static/...
       behind the NGINX prefix. Do not make this absolute. -->
  <link rel="stylesheet" href="../../static/vendor/bootstrap.min.css">
</head>
<body>
  <nav class="navbar bg-white border-bottom sticky-top">
    <div class="container-fluid px-3 px-lg-4">
      <span class="navbar-brand mb-0">tts-erp · 采购工作台</span>
      <div class="d-flex align-items-center gap-3">
        <label class="d-flex align-items-center gap-2 small text-secondary mb-0" for="shop-switcher">店铺
          <select id="shop-switcher" name="channel_account_id" class="form-select form-select-sm w-auto" aria-label="当前店铺"></select>
        </label>
        <span class="small text-secondary" id="ops-identity"></span>
      </div>
    </div>
  </nav>

  <main class="container-fluid px-3 px-lg-4 py-3" style="max-width: 1280px">
    <ul class="nav nav-tabs" role="tablist" aria-label="工作台标签页">
      <li class="nav-item" role="presentation">
        <button class="tab nav-link active" type="button" role="tab" data-tab="needs_cost" aria-selected="true" aria-controls="grid-rows">
          待填成本 <span class="badge text-bg-secondary" id="badge-cost">·</span>
        </button>
      </li>
      <li class="nav-item" role="presentation">
        <button class="tab nav-link" type="button" role="tab" data-tab="needs_photo" aria-selected="false" aria-controls="grid-rows">
          待传图片 <span class="badge text-bg-secondary" id="badge-photo">·</span>
        </button>
      </li>
      <li class="nav-item" role="presentation">
        <button class="tab nav-link" type="button" role="tab" data-tab="recent" aria-selected="false" aria-controls="grid-rows">
          最近提交 <span class="badge text-bg-secondary" id="badge-recent">·</span>
        </button>
      </li>
    </ul>

    <div class="d-flex align-items-center gap-2 my-3 small text-secondary">
      <label for="filter-search">搜索</label>
      <input type="search" id="filter-search" class="form-control form-control-sm" style="max-width: 220px" placeholder="SKU 或标题" aria-label="过滤行">
      <label for="filter-limit">每页</label>
      <select id="filter-limit" class="form-select form-select-sm w-auto" aria-label="每页行数">
        <option>25</option>
        <option selected>50</option>
        <option>100</option>
      </select>
    </div>

    <div class="table-responsive">
      <table class="table table-hover align-middle" aria-live="polite">
        <thead>
          <tr id="grid-head-cost">
            <th scope="col">SKU</th>
            <th scope="col">标题</th>
            <th scope="col">状态</th>
            <th scope="col" class="text-end">单位成本</th>
            <th scope="col">备注</th>
            <th scope="col">操作</th>
          </tr>
        </thead>
        <tbody id="grid-rows">
          <tr><td colspan="6" class="text-secondary">加载店铺中…</td></tr>
        </tbody>
      </table>
    </div>
  </main>

  <script src="../../static/js/console.js" defer></script>
</body>
</html>
"""
