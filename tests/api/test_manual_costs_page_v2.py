"""Manual-costs page v2 redesign — frontend HTML shell contract tests.

Scope: assert the redesigned page renders the three operational-state tabs,
references external static assets, and removes the legacy token-paste block.
We only test the HTML shell — runtime JS behaviour (fetch/upload) lives in
the browser and is covered by manual smoke tests, not FastAPI TestClient.

See ``tech-doc/procurement-ui-redesign.md`` §2 (design tokens) and §6
(frontend contracts) for the full design rationale.
"""

from __future__ import annotations


def test_manual_costs_page_v2_returns_200_html(api_client, readonly_key):
    """GET the redesigned page → 200 text/html."""
    r = api_client.get(
        "/v2/pages/manual-costs",
        headers={"Authorization": f"Bearer {readonly_key}"},
    )
    assert r.status_code == 200, r.text
    assert r.headers["content-type"].startswith("text/html"), r.headers


def test_manual_costs_page_v2_references_static_assets(api_client, readonly_key):
    """Page HTML must link Bootstrap (vendored) + console.js, prefix-relative.

    Styling is Bootstrap 5.3.8 self-hosted at /static/vendor/ (2026-08-31:
    the custom console.css design system was dropped per user decision).
    All asset paths must be RELATIVE (../../static/...) so the page works
    behind the NGINX /tts prefix as well as on :9877 directly.
    """
    r = api_client.get(
        "/v2/pages/manual-costs",
        headers={"Authorization": f"Bearer {readonly_key}"},
    )
    body = r.text
    assert "../../static/vendor/bootstrap.min.css" in body, "missing Bootstrap CSS link"
    assert "../../static/js/console.js" in body, "missing JS link"
    assert "/static/css/console.css" not in body, (
        "retired custom stylesheet still linked"
    )


def test_manual_costs_page_v2_has_two_operational_tabs(api_client, readonly_key):
    """Page must render exactly two tabs: 全部 SPU + 最近提交.

    2026-09-06: the 待处理 tab is retired. The editable catalogue (全部
    SPU, cost inline-editable + 提交全部) and the change log (最近提交,
    变更前 → 变更后) are the only two views.
    """
    r = api_client.get(
        "/v2/pages/manual-costs",
        headers={"Authorization": f"Bearer {readonly_key}"},
    )
    body = r.text
    for label in ("全部 SPU", "最近提交"):
        assert label in body, f"missing tab label: {label!r}"
    # The retired labels must be gone (regression guard).
    for retired in ("待处理", "待填成本", "待传图片"):
        assert retired not in body, f"retired tab label still rendered: {retired!r}"


def test_manual_costs_page_v2_has_shop_switcher(api_client, readonly_key):
    """Header must expose a shop switcher dropdown (id="shop-switcher")."""
    r = api_client.get(
        "/v2/pages/manual-costs",
        headers={"Authorization": f"Bearer {readonly_key}"},
    )
    body = r.text
    assert 'id="shop-switcher"' in body, "missing shop switcher control"
    assert 'name="shop_pk"' in body, "shop switcher missing name attr"


def test_manual_costs_page_v2_drops_token_paste_block(api_client, readonly_key):
    """The legacy <details>API token…</details> block must be gone.

    The page now relies on /v2/auth/login (session cookie) — token paste
    is dead. We assert by the absence of the unique identifier text.
    """
    r = api_client.get(
        "/v2/pages/manual-costs",
        headers={"Authorization": f"Bearer {readonly_key}"},
    )
    body = r.text
    assert "API token (paste once; stored in localStorage)" not in body, (
        "legacy token paste UI still present"
    )
    assert "mc_token" not in body, "legacy localStorage key still referenced"


def test_manual_costs_page_v2_asset_paths_prefix_safe(api_client, readonly_key):
    """No root-absolute asset hrefs/srcs — regression guard for the 404.

    2026-08-31: absolute /static/... links 404'd behind the NGINX /tts
    prefix (daqiang.nat100.top/static/... has no route), leaving the page
    completely unstyled in production. The page is served at
    /v2/pages/manual-costs, so ../../static/ resolves to the deployment
    root under any prefix.
    """
    r = api_client.get(
        "/v2/pages/manual-costs",
        headers={"Authorization": f"Bearer {readonly_key}"},
    )
    body = r.text
    assert 'href="/' not in body, "root-absolute href found"
    assert 'src="/' not in body, "root-absolute src found"


def test_manual_costs_page_v2_no_inline_event_handlers(api_client, readonly_key):
    """No onclick / onsubmit / onchange inline handlers.

    Per the task brief (no inline event handlers — use addEventListener).
    """
    r = api_client.get(
        "/v2/pages/manual-costs",
        headers={"Authorization": f"Bearer {readonly_key}"},
    )
    body = r.text
    for forbidden in ("onclick=", "onsubmit=", "onchange="):
        assert forbidden not in body, f"inline handler found: {forbidden}"


def test_manual_costs_page_v2_uses_design_token_variables(api_client, readonly_key):
    """No raw hex values OUTSIDE the inline <style> block.

    Design doc §2.3 enumerates 7 named hex tokens. The inline <style>
    block is allowed to define them (token declarations are the
    legitimate source of hex), but element attributes, inline style="…"
    attributes, and other non-CSS sites must NOT carry one-off colors.
    Everything that needs to render a colour must reference a class
    that resolves through :root { --accent: … } etc.
    """
    import re

    r = api_client.get(
        "/v2/pages/manual-costs",
        headers={"Authorization": f"Bearer {readonly_key}"},
    )
    body = r.text
    # Strip the inline <style>…</style> block before scanning — token
    # declarations live there, that's fine.
    stripped = re.sub(r"<style[\s\S]*?</style>", "", body)
    # Also strip the <link rel="stylesheet" href="…"> reference itself
    # (no hex inside but defensive).
    stripped = re.sub(r'<link\s+rel="stylesheet"[^>]*>', "", stripped)
    hex_colors = re.findall(r"#[0-9A-Fa-f]{6}\b", stripped)
    assert not hex_colors, (
        f"raw hex colors leaked into HTML outside <style>: {hex_colors}"
    )


def test_manual_costs_page_v2_signature_counter_present(api_client, readonly_key):
    """The signature oversized queue counter element must render.

    The .op-counter block (with id="op-counter", the .op-counter-num
    span, and the .op-counter-label) is the page's primary visual
    element. JS populates .op-counter-num with the active tab's row
    count (catalogue SPUs on 全部 SPU, change-log rows on 最近提交).
    """
    r = api_client.get(
        "/v2/pages/manual-costs",
        headers={"Authorization": f"Bearer {readonly_key}"},
    )
    body = r.text
    assert 'id="op-counter"' in body, "signature counter section missing"
    assert 'id="op-counter-num"' in body, "signature counter num span missing"
    assert "op-counter-label" in body, "counter label class missing"
    assert "全部 SPU" in body, "counter label text missing"
    # Industrial-console fingerprints in the inline <style>
    assert "--paper:" in body, "paper token not declared"
    assert "--accent:" in body, "accent token not declared"
    assert "--mono:" in body, "monospace font stack not declared"
    # Border-radius zero is part of the aesthetic (no rounded corners)
    assert "border-radius: 0" in body, "expected flat (zero radius) design"


def test_console_js_uses_redesign_class_names():
    """console.js row HTML must use the op-* class hooks from the redesign.

    Regression guard so the next refactor doesn't regress the page to
    raw Bootstrap classes (which the CSS no longer styles).
    2026-09-05 page-rework lane: currency select + upload dropzone are
    gone (fixed CNY + MinIO main-image mirror); the hooks below are the
    live set in the current row template.
    """
    from pathlib import Path

    js = (
        Path(__file__).resolve().parents[2]
        / "tts_erp_v2"
        / "static"
        / "js"
        / "console.js"
    )
    src = js.read_text(encoding="utf-8")
    for cls in (
        "op-td-sku",
        "op-td-cost",
        "op-input-cost",
        "op-currency-fixed",
        "op-mirror-thumb",
        "op-img-fallback",
        "op-loading",
        "op-cost-input",
        "row-status",
    ):
        assert cls in src, f"console.js missing class hook: {cls!r}"
    # op-btn-primary no longer appears in console.js: per-row submit
    # buttons are gone (2026-09-06) — the only submit control is the
    # toolbar 提交全部 (data-act="submit-all"), which lives in the page
    # HTML. Assert the button hook there instead.
    assert "op-btn-primary" not in src, (
        "console.js should not render per-row submit buttons anymore"
    )
    # Retired UI must stay gone (page-rework lane).
    for retired in ("op-select-currency", "op-dropzone"):
        assert retired not in src, (
            f"console.js must not reference retired upload/currency UI: {retired!r}"
        )


def test_console_js_populates_signature_counter():
    """console.js loaders must populate #op-counter-num on success.

    The signature counter is purely JS-driven — without the population
    step the page would render '·' forever.
    """
    from pathlib import Path

    js = (
        Path(__file__).resolve().parents[2]
        / "tts_erp_v2"
        / "static"
        / "js"
        / "console.js"
    )
    src = js.read_text(encoding="utf-8")
    assert '"#op-counter-num"' in src or "#op-counter-num" in src, (
        "console.js does not target #op-counter-num"
    )
    assert "data-state" in src and '"ready"' in src, (
        "console.js does not flip counter data-state to ready on success"
    )


def test_manual_costs_page_v2_self_hosted_font_hint(api_client, readonly_key):
    """Page must not link a public CDN for fonts.

    Operators reach this service over a private NAT tunnel; CDN links
    leak operator IPs. We assert no fonts.googleapis.com or
    fonts.gstatic.com link in the HTML. The page actually uses
    Bootstrap 5.3.8's default font stack now (2026-08-31 — the custom
    IBM Plex typography was dropped with the rest of console.css).
    """
    r = api_client.get(
        "/v2/pages/manual-costs",
        headers={"Authorization": f"Bearer {readonly_key}"},
    )
    body = r.text
    assert "fonts.googleapis.com" not in body, "Google Fonts CDN leak"
    assert "fonts.gstatic.com" not in body, "Google Fonts CDN leak"


def test_console_js_unwraps_api_envelope():
    """Regression guard for the '(items || []).filter is not a function' crash.

    2026-08-31: the backend rolled out an envelope
        { items: [...], total_missing_photo: N }
    on /v2/reporting/missing-cost-products. console.js must unwrap that
    envelope before iterating; otherwise loadNeedsPhoto throws on the
    Needs photo tab. Also protects loadNeedsCost + loadRecent against
    any future envelope roll-out on their endpoints.
    """
    from pathlib import Path

    js = (
        Path(__file__).resolve().parents[2]
        / "tts_erp_v2"
        / "static"
        / "js"
        / "console.js"
    )
    src = js.read_text(encoding="utf-8")
    assert "function unwrap(payload)" in src, "unwrap helper missing from console.js"
    # Both load functions (catalogue + change log) must pipe their
    # payload through unwrap().
    assert src.count("unwrap(payload)") >= 2, (
        f"unwrap(payload) called {src.count('unwrap(payload)')} times, "
        "expected ≥ 2 (all + recent tabs)"
    )
    # Spot-check: filter() must not be called on a payload that wasn't
    # unwrapped (the original bug pattern).
    assert ".filter((it)" not in src or "unwrap(payload).filter" in src, (
        ".filter called on a payload without unwrap — likely the 2026-08-31 bug"
    )


def test_page_has_submit_all_button(api_client, readonly_key):
    """The toolbar must expose a 提交全部 (batch submit) control.

    2026-09-06 operator request: one click files every EDITED row on the
    全部 SPU tab (dirty-tracking marks rows whose cost input changed).
    The button lives in the toolbar (data-act="submit-all") and the JS
    wires it to submitAllEdited(); a live status banner (.op-batch-status)
    reports filed / failed counts.
    """
    r = api_client.get(
        "/v2/pages/manual-costs",
        headers={"Authorization": f"Bearer {readonly_key}"},
    )
    assert r.status_code == 200, r.text
    body = r.text
    assert 'data-act="submit-all"' in body, "submit-all button missing"
    assert "提交全部" in body, "submit-all button label missing"
    assert 'class="op-batch-status"' in body, "batch status banner missing"

    from pathlib import Path

    js = (
        Path(__file__).resolve().parents[2]
        / "tts_erp_v2"
        / "static"
        / "js"
        / "console.js"
    )
    src = js.read_text(encoding="utf-8")
    assert "function submitAllEdited()" in src, "console.js missing submitAllEdited"
    assert "function postManualCost(tr)" in src, "console.js missing postManualCost"
    assert "submit-all" in src, "console.js must bind [data-act=submit-all]"


def test_page_has_all_spu_tab(api_client, readonly_key):
    """The tab bar exposes a 全部 SPU view.

    2026-09-06 operator request: see every SPU (not just pending / recent)
    with its status, current manual cost, and mirrored main image. The tab
    reads GET /v2/commerce/channel-products which now carries per-row
    unit_cost / currency / image_url alongside the legacy fields.
    """
    r = api_client.get(
        "/v2/pages/manual-costs",
        headers={"Authorization": f"Bearer {readonly_key}"},
    )
    assert r.status_code == 200, r.text
    body = r.text
    assert 'data-tab="all"' in body, "all-SPU tab missing"
    assert "全部 SPU" in body, "all-SPU tab label missing"
    assert 'id="badge-all"' in body, "all-SPU badge missing"

    from pathlib import Path

    js = (
        Path(__file__).resolve().parents[2]
        / "tts_erp_v2"
        / "static"
        / "js"
        / "console.js"
    )
    src = js.read_text(encoding="utf-8")
    assert "function loadAll()" in src, "console.js missing loadAll"
    assert "function renderAllRows(items)" in src, "console.js missing renderAllRows"
    assert "/v2/commerce/channel-products" in src, (
        "console.js all tab must read channel-products"
    )


def test_page_has_no_pending_tab(api_client, readonly_key):
    """待处理 tab retired: the data-tab=pending button is gone entirely."""
    r = api_client.get(
        "/v2/pages/manual-costs",
        headers={"Authorization": f"Bearer {readonly_key}"},
    )
    body = r.text
    assert 'data-tab="pending"' not in body, "pending tab button still rendered"
    assert 'id="badge-pending"' not in body, "pending badge still rendered"
    assert 'data-tab="all"' in body, "all tab must still be present"
    assert 'data-tab="recent"' in body, "recent tab must still be present"


def test_page_has_editable_catalogue_hooks(api_client, readonly_key):
    """全部 SPU rows are editable: cost input + dirty markers.

    The static thead carries the sortable headers (data-sort on 成本 /
    创建 / 更新) so the operator can re-sort the catalogue.
    """
    r = api_client.get(
        "/v2/pages/manual-costs",
        headers={"Authorization": f"Bearer {readonly_key}"},
    )
    assert r.status_code == 200, r.text
    body = r.text
    for hook in (
        'data-sort="unit_cost"',
        'data-sort="created_at"',
        'data-sort="updated_at"',
        "op-th-sortable",
        "op-sort-arrow",
    ):
        assert hook in body, f"page missing sort hook: {hook!r}"

    from pathlib import Path

    js = (
        Path(__file__).resolve().parents[2]
        / "tts_erp_v2"
        / "static"
        / "js"
        / "console.js"
    )
    src = js.read_text(encoding="utf-8")
    assert "function bindSortableHeaders(tr)" in src, "sort binder missing"
    assert "catalogueSort" in src, "catalogue sort state missing"
    assert "is-dirty" in src, "row dirty tracking missing"
    assert "submitAllEdited" in src, "edited-rows batch submit missing"
    # Sort state must flow into the channel-products request.
    assert "&sort=" in src, "loadAll must send the sort query"
    assert "source_created_at" in src, "catalogue row must render created time"
    assert "source_updated_at" in src, "catalogue row must render updated time"


def test_console_js_recent_tab_renders_prev_and_new():
    """最近提交 = change log: rows show 变更前 → 变更后.

    renderRecentRows must read prev_unit_cost / unit_cost (the backend
    LAG pairing) and render both columns.
    """
    from pathlib import Path

    js = (
        Path(__file__).resolve().parents[2]
        / "tts_erp_v2"
        / "static"
        / "js"
        / "console.js"
    )
    src = js.read_text(encoding="utf-8")
    assert "function renderRecentRows(items)" in src
    assert "prev_unit_cost" in src, "console.js must read prev_unit_cost"
    assert "变更前" in src and "变更后" in src, (
        "recent thead/rows must label the before/after price columns"
    )
    assert 'data-label="变更时间"' in src, "recent rows must show the change timestamp"


def test_page_has_status_filter_dropdown(api_client, readonly_key):
    """The toolbar exposes a 状态 filter dropdown (id="filter-status").

    Options are populated from console.js' STATUS_LABELS at boot, so the
    static HTML only carries the empty <select> — the page test asserts
    the select exists, and the JS test below locks the label mapping.
    """
    r = api_client.get(
        "/v2/pages/manual-costs",
        headers={"Authorization": f"Bearer {readonly_key}"},
    )
    assert r.status_code == 200, r.text
    body = r.text
    assert 'id="filter-status"' in body, "status filter select missing"
    assert 'aria-label="按状态过滤"' in body, "status filter aria-label missing"


def test_console_js_status_label_mapping_and_sort():
    """console.js maps upstream status codes to Chinese labels and lets
    the operator sort the status column.

    The page's 全部 SPU view shows ACTIVATE=在售 / DELETED=下架 /
    SELLER_DEACTIVATED=停售; the status header is click-sortable via
    data-sort="status" and the URL carries ?status= for filtering. The
    catalogue defaults to status-asc so in-sale products lead.
    """
    from pathlib import Path

    js = (
        Path(__file__).resolve().parents[2]
        / "tts_erp_v2"
        / "static"
        / "js"
        / "console.js"
    )
    src = js.read_text(encoding="utf-8")
    assert "STATUS_LABELS" in src, "status label map missing"
    assert 'ACTIVATE: "在售"' in src, "ACTIVATE must map to 在售"
    assert "商家" not in src, "old 商家 label must be gone"
    assert 'DELETED: "下架"' in src, "DELETED must map to 下架"
    assert "SELLER_DEACTIVATED" in src, "SELLER_DEACTIVATED mapping missing"
    assert "function statusLabel" in src, "statusLabel helper missing"
    assert 'data-sort="status"' in src, "status column must be sortable"
    assert "catalogueStatus" in src, "status filter state missing"
    assert "&status=" in src, "loadAll must forward ?status="
    # Default catalogue sort: in-sale products first.
    assert 'key: "status"' in src, "default sort must be by status"
    assert 'order: "asc"' in src, "default order must be asc (在售 first)"


def test_page_has_pager_controls(api_client, readonly_key):
    """The catalogue has prev/next paging + a row-count label.

    2026-09-06: channel-products is a bare-array contract, so the total
    travels in X-Total-Count; the pager lives under the table.
    """
    r = api_client.get(
        "/v2/pages/manual-costs",
        headers={"Authorization": f"Bearer {readonly_key}"},
    )
    assert r.status_code == 200, r.text
    body = r.text
    assert 'id="grid-pager"' in body, "pager section missing"
    assert 'id="btn-prev"' in body, "prev button missing"
    assert 'id="btn-next"' in body, "next button missing"
    assert 'id="pager-label"' in body, "pager label missing"
    assert "上一页" in body and "下一页" in body, "pager button labels missing"

    from pathlib import Path

    js = (
        Path(__file__).resolve().parents[2]
        / "tts_erp_v2"
        / "static"
        / "js"
        / "console.js"
    )
    src = js.read_text(encoding="utf-8")
    assert "function updatePager(total, pageLen)" in src, (
        "updatePager missing from console.js"
    )
    assert "pageOffset" in src, "page offset state missing"
    assert "pageLimit" in src, "page limit state missing"
    assert "X-Total-Count" in src, "console.js must read X-Total-Count"
    assert '"#filter-limit"' in src or "filter-limit" in src, (
        "每页 dropdown must be wired"
    )


def test_page_script_has_cache_bust_version(api_client, readonly_key):
    """console.js must be referenced with ?v=<content hash>.

    2026-09-06: /static has no Cache-Control and browsers heuristically
    cache console.js, so users kept seeing the previous build (e.g. the
    pre-pagination 50-row catalogue). Stamping a content-hash query makes
    every deploy invalidate the stale copy automatically.
    """
    r = api_client.get(
        "/v2/pages/manual-costs",
        headers={"Authorization": f"Bearer {readonly_key}"},
    )
    assert r.status_code == 200, r.text
    body = r.text
    import re

    m = re.search(r'src="([^"]*console\.js[^"]*)"', body)
    assert m, "console.js script tag missing"
    src = m.group(1)
    assert "?v=" in src, f"console.js src must carry ?v= cache-bust, got: {src}"
    version = src.split("?v=")[1]
    assert re.fullmatch(r"[0-9a-f]{8}", version), (
        f"version must be an 8-char content hash, got: {version!r}"
    )
