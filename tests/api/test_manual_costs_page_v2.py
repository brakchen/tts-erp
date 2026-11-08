"""Manual-costs page v2 — HTTP document and console-asset contracts.

Scope: the route serves one React bootstrap document. The HTTP-level checks
below cover status, content type, cache headers, the absence of the retired
token-paste UI and of inline event handlers.

The retired Jinja template is gone with the P6 asset retirement, so its
asset / tab / shop-switcher / pager guards were old-implementation guards
and were removed together with it. The active interactions live in
``frontend/src/pages/manual-costs/`` and the browser suites.

``console.js`` is mounted as a public static asset and is **not** part of
this retirement, so its source-level contracts stay here.

See ``docs/archive/procurement-ui-redesign.md`` §2 (design tokens) and §6
(frontend contracts) for the full design rationale.
"""

from __future__ import annotations

import re
from pathlib import Path

_CONSOLE_JS = (
    Path(__file__).resolve().parents[2]
    / "tts_erp_v2"
    / "static"
    / "js"
    / "console.js"
)


def _console_js() -> str:
    """Read the still-mounted console asset."""
    return _CONSOLE_JS.read_text(encoding="utf-8")


def test_manual_costs_page_v2_returns_200_html(api_client, readonly_key):
    """GET the redesigned page → 200 text/html."""
    r = api_client.get(
        "/v2/pages/manual-costs",
        headers={"Authorization": f"Bearer {readonly_key}"},
    )
    assert r.status_code == 200, r.text
    assert r.headers["content-type"].startswith("text/html"), r.headers


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


def test_page_does_not_render_source_price_badge(api_client, readonly_key):
    """Source-price values may prefill the input, but the badge is removed."""
    r = api_client.get(
        "/v2/pages/manual-costs",
        headers={"Authorization": f"Bearer {readonly_key}"},
    )
    assert r.status_code == 200, r.text
    assert "op-source-badge" not in r.text

    assert "op-source-badge" not in _console_js()


def test_console_js_uses_redesign_class_names():
    """console.js row HTML must use the op-* class hooks from the redesign.

    Regression guard so the next refactor doesn't regress the page to
    raw Bootstrap classes (which the CSS no longer styles).
    2026-09-05 page-rework lane: currency select + upload dropzone are
    gone (fixed CNY + MinIO main-image mirror); the hooks below are the
    live set in the current row template.
    """
    src = _console_js()
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
    src = _console_js()
    assert '"#op-counter-num"' in src or "#op-counter-num" in src, (
        "console.js does not target #op-counter-num"
    )
    assert "data-state" in src and '"ready"' in src, (
        "console.js does not flip counter data-state to ready on success"
    )


def test_console_search_is_server_side():
    """Search must be part of API scope before pagination, never a DOM filter."""
    src = _console_js()

    assert '"&q=" + encodeURIComponent(searchQuery)' in src
    assert "setTimeout(refreshActiveTab, 250)" in src
    assert "function applyFilter()" not in src
    assert "tr.style.display" not in src


def test_console_js_unwraps_api_envelope():
    """Regression guard for the '(items || []).filter is not a function' crash.

    2026-08-31: the backend rolled out an envelope
        { items: [...], total_missing_photo: N }
    on /v2/reporting/missing-cost-products. console.js must unwrap that
    envelope before iterating; otherwise loadNeedsPhoto throws on the
    Needs photo tab. Also protects loadNeedsCost + loadRecent against
    any future envelope roll-out on their endpoints.
    """
    src = _console_js()
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


def test_console_js_recent_tab_renders_prev_and_new():
    """最近提交 = change log: rows show 变更前 → 变更后.

    renderRecentRows must read prev_unit_cost / unit_cost (the backend
    LAG pairing) and render both columns.
    """
    src = _console_js()
    assert "function renderRecentRows(items)" in src
    assert "prev_unit_cost" in src, "console.js must read prev_unit_cost"
    assert "变更前" in src and "变更后" in src, (
        "recent thead/rows must label the before/after price columns"
    )
    assert 'data-label="变更时间"' in src, "recent rows must show the change timestamp"


def test_console_js_status_label_mapping_and_sort():
    """console.js maps upstream status codes to Chinese labels and lets
    the operator sort the status column.

    The page's 全部 SPU view shows ACTIVATE=在售 / DELETED=下架 /
    SELLER_DEACTIVATED=停售; the status header is click-sortable via
    data-sort="status" and the URL carries ?status= for filtering. The
    catalogue defaults to status-asc so in-sale products lead.
    """
    src = _console_js()
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


def test_console_js_batches_edited_rows_and_submits_each_row():
    """提交全部 / 逐行提交：console.js 的绑定与调用。"""
    src = _console_js()
    assert "function submitAllEdited()" in src, "console.js missing submitAllEdited"
    assert "function postManualCost(tr)" in src, "console.js missing postManualCost"
    assert "submit-all" in src, "console.js must bind [data-act=submit-all]"


def test_console_js_renders_the_all_spu_catalogue():
    """全部 SPU 目录：loadAll / renderAllRows 与它的数据源。"""
    src = _console_js()
    assert "function loadAll()" in src, "console.js missing loadAll"
    assert "function renderAllRows(items)" in src, "console.js missing renderAllRows"
    assert "/v2/commerce/channel-products" in src, (
        "console.js all tab must read channel-products"
    )


def test_console_js_labels_spu_values_as_spu():
    """工作台渲染的是 ``spu_id``，所以所有标签都写 SPU。"""
    src = _console_js()
    assert "SKU" not in src, "workbench must not label spu_id values as SKU"
    assert 'data-label="SPU"' in src
    assert 'search.placeholder = "SPU 或标题"' in src


def test_console_js_tracks_dirty_rows_and_sorts_the_catalogue():
    """全部 SPU 行可编辑：成本输入 + 脏标记 + 可排序列。"""
    src = _console_js()
    assert "function bindSortableHeaders(tr)" in src, "sort binder missing"
    assert "catalogueSort" in src, "catalogue sort state missing"
    assert "is-dirty" in src, "row dirty tracking missing"
    assert "submitAllEdited" in src, "edited-rows batch submit missing"
    # Sort state must flow into the channel-products request.
    assert "&sort=" in src, "loadAll must send the sort query"
    assert "source_created_at" in src, "catalogue row must render created time"
    assert "source_updated_at" in src, "catalogue row must render updated time"


def test_console_js_paginates_the_catalogue():
    """目录翻页：updatePager / 页偏移 / 每页条数。

    2026-09-06: channel-products is a bare-array contract, so the total
    travels in X-Total-Count.
    """
    src = _console_js()
    assert "function updatePager(total, pageLen)" in src, (
        "updatePager missing from console.js"
    )
    assert "pageOffset" in src, "page offset state missing"
    assert "pageLimit" in src, "page limit state missing"
    assert "X-Total-Count" in src, "console.js must read X-Total-Count"
    assert '"#filter-limit"' in src or "filter-limit" in src, (
        "每页 dropdown must be wired"
    )


def test_console_js_forwards_the_catalogue_filters():
    """工具栏过滤条件：仅看有单 / 仅展示未登记 SPU 都交给 API。"""
    src = _console_js()
    assert "catalogueHasOrders" in src, "has-orders state missing"
    assert "has_orders=true" in src, "loadAll must forward has_orders=true"
    assert 'id="filter-has-orders"' in src or "filter-has-orders" in src, (
        "checkbox binding missing"
    )
    assert "catalogueMissingManualCost" in src
    assert "missing_manual_cost=true" in src
    assert "filter-missing-manual-cost" in src
    assert "catalogueMissingManualCost || it.source_unit_cost == null" in src, (
        "unregistered-only rows must not prefill the source price"
    )
