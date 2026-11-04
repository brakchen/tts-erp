"""Source-level frontend contracts formerly exercised with API fixtures."""

from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = [pytest.mark.domain_api, pytest.mark.layer_integration]


def test_profitability_pages_do_not_keep_a_separate_palette():
    """The shared React page consumes the approved theme and table tokens."""
    root = Path(__file__).resolve().parents[2]
    styles = (root / "frontend/src/styles.css").read_text(encoding="utf-8")
    shared_tables = (root / "frontend/src/shared/tables.css").read_text(encoding="utf-8")
    page_css = (root / "frontend/src/pages/spu-profitability/profitability.css").read_text(encoding="utf-8")
    assert "static/css/tokens.css" in styles
    assert "static/css/navigation.css" in styles
    assert "./app/workspace.css" in styles and "./shared/tables.css" in styles
    assert ":root" not in page_css
    assert "rgba(184, 57, 14" not in page_css
    assert "var(--table-heading)" in page_css
    assert "var(--surface)" in page_css
    assert "var(--rule)" in page_css
    assert "var(--table-hover)" in shared_tables
    assert ".erp-sidebar" not in page_css and ".erp-nav-link" not in page_css


def test_dashboard_consumes_backend_owned_summary_fields():
    """Dashboard cards must not count list rows in browser JavaScript."""
    src = (
        Path(__file__).resolve().parents[2]
        / "frontend"
        / "src"
        / "pages"
        / "dashboard"
        / "index.tsx"
    ).read_text(encoding="utf-8")

    assert "/v2/reporting/coverage" in src
    assert "/v2/reporting/missing-cost-products" not in src
    assert "value.missing_cost_spus" in src
    assert "value.total_spus" in src
    assert "X-Total-Count" in src
    assert "value: total" in src


def test_shops_page_js_manages_service_app_credentials() -> None:
    source = Path("tts_erp_v2/static/js/shops.js").read_text()
    assert "app_secret: appSecret || null" in source
    assert (
        'configBtn.className = "shop-action shop-action--credentials btn-config-app"'
        in source
    )
    assert 'authBtn.className = "shop-action shop-action--authorize btn-auth"' in source
    assert 'setActionContent(configBtn, "⚙", "配置 App")' in source
    assert "shop-action:focus-visible" in source
    assert "app_credentials_configured" in source
    assert "bindAppCredentialsDialog();" in source
    assert "admin 会话" in source


def test_spu_profit_deterioration_page_assets_are_readonly_comparison():
    """PRD v3 retires config writes and frontend business decisions."""
    root = Path(__file__).resolve().parents[2] / "tts_erp_v2" / "static"
    js = (root / "js" / "spu-profit-deterioration.js").read_text(encoding="utf-8")
    assert "/analytics/spu-profit-deterioration" in js
    for forbidden in (
        "/config/runtime/items/",
        "meta.effectiveConfig",
        "calculateRoi",
        "net_profit =",
    ):
        assert forbidden not in js, forbidden


def test_focused_spus_profile_uses_realtime_inline_multi_select() -> None:
    """Focused editing remains immediate; selection UI now belongs to React."""
    root = Path(__file__).resolve().parents[2]
    selection = (root / "frontend/src/pages/spu-profitability/Selection.tsx").read_text(encoding="utf-8")
    query = (root / "frontend/src/pages/spu-profitability/query.ts").read_text(encoding="utf-8")
    page = (root / "frontend/src/pages/spu-profitability/index.tsx").read_text(encoding="utf-8")

    assert 'id={focused ? \'focused-spu-select\' : \'filter-spu-ids\'}' in selection
    assert "<Checkbox" in selection and "isSelected={draft.includes(item.spu_id)}" in selection
    assert "addSpuIds: add" in selection and "removeSpuIds: remove" in selection
    assert "/v2/reporting/focused-spus/" in selection and "method: 'PATCH'" in selection
    assert "resolvePaste" in selection and "parseIds(paste)" in selection
    assert "focused && !canWrite" in selection
    assert "修改后实时保存" in selection and "已实时保存" in selection
    assert "{!focused &&" in selection and 'id="btn-spu-apply"' in selection
    assert "focusedCallback.current(total)" in selection
    assert "focusedCount !== null" in page
    assert "params.set('scope', 'focused')" in query
    assert "TomSelect" not in selection
    assert "编辑关注 SPU" not in selection and "保存修改" not in selection


def test_sync_jobs_js_uses_prefix_aware_api_paths() -> None:
    src = (
        Path(__file__).resolve().parents[2]
        / "tts_erp_v2"
        / "static"
        / "js"
        / "sync-jobs.js"
    ).read_text(encoding="utf-8")

    assert "const API = `${rootPrefix}/v2`;" in src
    assert "api('/sync/jobs')" in src
    assert "api('/auth/me')" in src
    assert "CSS.escape" not in src
    assert "state.canAdmin" in src
    assert "state.canTrigger" in src
    assert "disabled" in src
    assert "window.confirm" in src
    assert "window.alert" in src
    assert "data-label=\"任务\"" in src
    assert "is-busy" in src


def test_sync_jobs_css_has_mobile_card_layout_and_disabled_cursor() -> None:
    src = (
        Path(__file__).resolve().parents[2]
        / "tts_erp_v2"
        / "static"
        / "css"
        / "sync-jobs.css"
    ).read_text(encoding="utf-8")

    assert "button:disabled" in src
    assert "cursor: not-allowed" in src
    assert "button.is-busy:disabled" in src
    assert ".jobs-table thead { display: none; }" in src
    assert "content: attr(data-label)" in src
    assert ".scope-select { width: 100%;" in src
