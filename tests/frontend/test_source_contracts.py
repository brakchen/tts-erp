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
    assert "onPaste=" in selection and "resolveIds(ids, true)" in selection
    assert "focused && !canWrite" in selection
    assert "已实时保存" in selection
    assert "{!focused &&" in selection and 'id="btn-spu-apply"' in selection
    assert "focusedCallback.current(total)" in selection
    assert "focusedCount !== null" in page
    assert "params.set('scope', 'focused')" in query
    assert "TomSelect" not in selection
    assert "编辑关注 SPU" not in selection and "保存修改" not in selection


def test_profitability_kernel_uses_remote_sort_mode() -> None:
    source = Path(__file__).resolve().parents[2] / "frontend/src/pages/spu-profitability/ProfitabilityTable.tsx"
    assert "sortMode: 'remote'" in source.read_text(encoding="utf-8")
