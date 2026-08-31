"""/v2/pages/* — server-rendered HTML pages (no SPA framework).

The manual-costs page (the only Lane E page) renders a static HTML shell.
All CSS / JS lives in ``tts_erp_v2/static/`` (mounted by the v2 app via
``StaticFiles`` under ``/static/``). The JS does the runtime work; the
server-side response is markup only.

Auth classification: the page is ``readonly``-equivalent for the GET
(handler does no DB writes). The page JS calls write endpoints
(``/v2/reporting/manual-costs``, ``/v2/spu-images/*``) — those require
a readwrite or admin session via the ``/v2/auth/login`` cookie flow.

Design rationale: ``tech-doc/procurement-ui-redesign.md`` §2.
"""

from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

router = APIRouter(prefix="/v2/pages", tags=["pages"])


@router.get("/manual-costs", response_class=HTMLResponse)
def manual_costs_page() -> HTMLResponse:
    """Manual cost entry + SPU image upload workbench.

    The HTML shell is a small stub:
    - links to ``/static/css/console.css`` (design tokens + layout)
    - links to ``/static/js/console.js`` (shop switcher, tabs, inline filing,
      drag-drop photo upload, the FILED stamp animation)
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
  <title>tts-erp · procurement</title>
  <link rel="stylesheet" href="/static/css/console.css">
</head>
<body>
  <header class="console-header" role="banner">
    <div class="brand">tts-erp<span class="dot"></span>procurement</div>
    <div class="right">
      <label class="shop-switcher" for="shop-switcher">shop
        <select id="shop-switcher" name="channel_account_id" aria-label="active shop"></select>
      </label>
      <span class="ops" id="ops-identity"></span>
    </div>
  </header>

  <main class="workbench" role="main">
    <nav class="tabs" role="tablist" aria-label="workbench tabs">
      <button class="tab" type="button" role="tab" data-tab="needs_cost" aria-selected="true" aria-controls="grid-rows">
        Needs cost <span class="badge" id="badge-cost">·</span>
      </button>
      <button class="tab" type="button" role="tab" data-tab="needs_photo" aria-selected="false" aria-controls="grid-rows">
        Needs photo <span class="badge" id="badge-photo">·</span>
      </button>
      <button class="tab" type="button" role="tab" data-tab="recent" aria-selected="false" aria-controls="grid-rows">
        Recently filed <span class="badge" id="badge-recent">·</span>
      </button>
    </nav>

    <div class="filters">
      <label for="filter-search">search</label>
      <input type="search" id="filter-search" placeholder="SKU or title" aria-label="filter rows">
      <label for="filter-limit">page</label>
      <select id="filter-limit" aria-label="rows per page">
        <option>25</option>
        <option selected>50</option>
        <option>100</option>
      </select>
    </div>

    <table class="grid" aria-live="polite">
      <thead>
        <tr id="grid-head-cost">
          <th class="sku" scope="col">SKU</th>
          <th scope="col">Title</th>
          <th class="code" scope="col">State</th>
          <th class="money" scope="col">Unit cost</th>
          <th scope="col">Note</th>
          <th scope="col">Action</th>
        </tr>
      </thead>
      <tbody id="grid-rows">
        <tr><td colspan="6" class="empty">loading shops…</td></tr>
      </tbody>
    </table>
  </main>

  <script src="/static/js/console.js" defer></script>
</body>
</html>
"""
