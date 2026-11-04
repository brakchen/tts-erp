"""Legacy ad-daily UI and audit-mock contracts without a live API."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

pytestmark = [pytest.mark.domain_api, pytest.mark.layer_integration]


def test_dashboard_installs_ad_daily_navigation_card():
    source = Path("tts_erp_v2/static/js/dashboard.js").read_text()

    assert "installAdDailyNav();" in source
    assert "../../v2/pages/ad-daily" in source
    assert "广告日明细" in source


def test_ad_daily_frontend_resets_filters_and_stale_results():
    source = Path("tts_erp_v2/static/js/ad-daily.js").read_text()

    assert "elements.seller.value = state.seller_id;" in source
    assert "elements.advertiser.value = state.advertiser_id;" in source
    assert "elements.endpoint.value = state.endpoint;" in source
    assert "function clearResults()" in source
    assert source.count("clearResults();") >= 2
    assert "elements.sumSpend.textContent = '—';" in source
    assert "elements.prev.disabled = true;" in source


def test_ad_daily_frontend_wires_header_sorting():
    """前端把点击翻成 sort/order 参数，并保持 URL 可分享。"""
    source = Path("tts_erp_v2/static/js/ad-daily.js").read_text()

    assert "const SORT_KEYS = ['day', 'shop', 'campaign', 'product'" in source
    assert "function updateSortIndicators()" in source
    assert "params.set('sort', state.sort)" in source
    assert "params.set('order', state.order)" in source
    assert "state.offset = 0;" in source


def test_ui_audit_mock_follows_current_ad_daily_contract():
    """巡检 mock 必须跟现行 ad_daily 契约同字段（静态锁）。

    2026-10-04 finding f-6001d47e-db2：布局巡检自带的 ad-daily mock 停在旧契约
    （``stat_date`` / ``campaign_name``），页面在残缺数据上被巡检，截图里日期、
    金额全是「—」，字号与列宽回归因此漏检。修法是把载荷抽到
    ``scripts/ui_audit_mocks.js`` 由两支探针共用，这里锁住不得回退。
    """
    def code_only(path: str) -> str:
        """去掉 JS 注释再扫：注释里必须能写「已退役字段」的名字来解释历史。"""
        source = Path(path).read_text(encoding="utf-8")
        source = re.sub(r"/\*.*?\*/", "", source, flags=re.DOTALL)
        return re.sub(r"^\s*//.*$", "", source, flags=re.MULTILINE)

    shared = code_only("scripts/ui_audit_mocks.js")
    for key in (
        "day",
        "seller_id",
        "product_title",
        "mixed_real_cost",
        "onsite_roi2_shopping_sku",
        "onsite_roi2_shopping_value",
        "onsite_mixed_real_roi2_shopping",
        "row_count",
        "weighted_roi",
    ):
        assert key in shared, f"共享 mock 缺契约字段 {key}"
    assert "stat_date" not in shared and "campaign_name" not in shared, (
        "共享 mock 回退到了旧契约字段"
    )

    for probe in ("scripts/probe_ui_layout_audit.js", "scripts/probe_ui_font_audit.js"):
        source = code_only(probe)
        assert 'require("./ui_audit_mocks")' in source, f"{probe} 未共用 ui_audit_mocks"
        assert "stat_date" not in source and "campaign_name" not in source, (
            f"{probe} 仍内联旧契约 ad-daily mock"
        )
