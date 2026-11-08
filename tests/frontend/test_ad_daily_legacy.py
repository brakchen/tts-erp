"""Ad-daily layout-audit mock contracts without a live API.

The retired per-page ad-daily/dashboard script implementation guards were
removed with the P6 asset retirement; the React ad-daily page owns the
behaviour now (see ``frontend/src/pages/ad-daily/``). What stays here is the
shared layout-audit mock contract, which guards the audit probes rather
than the retired page assets.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

pytestmark = [pytest.mark.domain_api, pytest.mark.layer_integration]


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
