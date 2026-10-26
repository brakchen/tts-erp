"""Advertising-detail browser contracts for ``plugin.ad_daily``."""

from __future__ import annotations

import json
import re
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import text

from tests.api.test_frontend_pages import _bootstrap
from tts_erp_v2.api.v2.ad_daily import _PAGE_HTML

pytestmark = [pytest.mark.domain_api, pytest.mark.layer_integration]

_SELLER = "TEST_AD_DAILY_BROWSER"
_ENDPOINT = "/oec_ads/shopping/v1/oec/stat/post_product_list"


def _seed_rows(db_engine) -> None:
    rows = [
        {
            "advertiser_id": "TEST_ADV_A",
            "campaign_id": "TEST_CAMP_100",
            "product_id": "TEST_PRODUCT_1",
            "day": "2026-09-02",
            "cost": Decimal("10.0000"),
            "orders": 2,
            "gmv": Decimal("30.0000"),
            "roi": Decimal("3.0000"),
            "extra": {"clicks": 41, "product_name": "测试商品一"},
        },
        {
            "advertiser_id": "TEST_ADV_A",
            "campaign_id": "TEST_CAMP_200",
            "product_id": "TEST_PRODUCT_2",
            "day": "2026-09-03",
            "cost": Decimal("5.5000"),
            "orders": 1,
            "gmv": Decimal("17.0000"),
            "roi": Decimal("3.0909"),
            "extra": {"clicks": 12},
        },
        {
            "advertiser_id": "TEST_ADV_B",
            "campaign_id": "TEST_CAMP_300",
            "product_id": "TEST_PRODUCT_3",
            "day": "2026-09-04",
            "cost": None,
            "orders": None,
            "gmv": None,
            "roi": None,
            "extra": {},
        },
    ]
    stmt = text(
        """
        INSERT INTO plugin.ad_daily (
          seller_id, advertiser_id, campaign_id, product_id, endpoint, day,
          mixed_real_cost, onsite_roi2_shopping_sku, onsite_roi2_shopping_value,
          onsite_mixed_real_roi2_shopping, metrics_extra
        ) VALUES (
          :seller_id, :advertiser_id, :campaign_id, :product_id, :endpoint,
          CAST(:day AS date), :cost, :orders, :gmv, :roi,
          CAST(:extra AS jsonb)
        )
        ON CONFLICT ON CONSTRAINT uq_ad_daily DO UPDATE SET
          mixed_real_cost = EXCLUDED.mixed_real_cost,
          onsite_roi2_shopping_sku = EXCLUDED.onsite_roi2_shopping_sku,
          onsite_roi2_shopping_value = EXCLUDED.onsite_roi2_shopping_value,
          onsite_mixed_real_roi2_shopping = EXCLUDED.onsite_mixed_real_roi2_shopping,
          metrics_extra = EXCLUDED.metrics_extra
        """
    )
    with db_engine.begin() as conn:
        for row in rows:
            conn.execute(
                stmt,
                {
                    **row,
                    "seller_id": _SELLER,
                    "endpoint": _ENDPOINT,
                    "extra": json.dumps(row["extra"], ensure_ascii=False),
                },
            )


def _auth(readonly_key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {readonly_key}"}


def test_ad_daily_page_returns_authenticated_shell(api_client, readonly_key):
    """The real readonly route uses React; old DOM belongs only to its artifact."""
    response = api_client.get(
        "/v2/pages/ad-daily",
        headers=_auth(readonly_key),
    )

    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("text/html")
    assert response.headers["cache-control"] == "private, no-store"
    assert "广告日明细" in response.text
    data = _bootstrap(response.text)
    assert data["pageId"] == "ad-daily"
    assert data["roleCapabilities"] == {"readwrite": False, "admin": False}
    assert readonly_key not in response.text
    for legacy_asset in (
        "bootstrap.min.css",
        "static/css/ad-daily.css",
        "static/js/ad-daily.js",
    ):
        assert legacy_asset not in response.text

    # Shared manifest/prefix and fail-closed contracts: test_frontend_pages.py
    # and test_frontend_remaining_pages.py. Active nine-column remote sorting:
    # frontend/src/pages/ad-daily/{index,query}.test.* (not legacy DOM hooks).
    # Sidebar markup/offset: test_pages.py::test_sidebar_*; active navigation
    # and current-page semantics: frontend/src/app/AppShell.test.tsx.
    # This retained inline artifact is NOT the response or a fallback renderer.
    legacy_body = _PAGE_HTML
    assert "../../static/css/ad-daily.css?v=" in legacy_body
    assert "../../static/js/ad-daily.js?v=" in legacy_body
    assert "plugin.ad_daily" in legacy_body
    assert 'data-sort="day"' in legacy_body
    assert 'class="mld-sort-button"' in legacy_body
    assert legacy_body.count('class="mld-sort-button"') == 9
    assert 'href="/static/' not in legacy_body
    assert 'src="/static/' not in legacy_body


def test_ad_daily_page_requires_auth(api_client):
    response = api_client.get("/v2/pages/ad-daily")
    assert response.status_code == 401


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


def test_ad_daily_list_filters_pages_and_summarizes_full_scope(
    api_client,
    readonly_key,
    db_engine,
):
    _seed_rows(db_engine)

    response = api_client.get(
        "/v2/reporting/ad-daily",
        headers=_auth(readonly_key),
        params={
            "seller_id": _SELLER,
            "advertiser_id": "TEST_ADV_A",
            "limit": 1,
            "offset": 0,
        },
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["total"] == 2
    assert payload["limit"] == 1
    assert payload["offset"] == 0
    assert len(payload["items"]) == 1
    assert payload["items"][0]["campaign_id"] == "TEST_CAMP_200"
    assert payload["items"][0]["mixed_real_cost"] == "5.5000"
    assert payload["items"][0]["metrics_extra"] == {"clicks": 12}
    assert payload["summary"] == {
        "row_count": 2,
        "spend": "15.5000",
        "attributed_orders": 3,
        "attributed_gmv": "47.0000",
        "weighted_roi": "3.0323",
        "currency": "USD",
    }


def test_ad_daily_list_search_is_literal_and_date_bounded(
    api_client,
    readonly_key,
    db_engine,
):
    _seed_rows(db_engine)
    headers = _auth(readonly_key)

    matched = api_client.get(
        "/v2/reporting/ad-daily",
        headers=headers,
        params={
            "seller_id": _SELLER,
            "q": "CAMP_2",
            "day_from": "2026-09-03",
            "day_to": "2026-09-03",
        },
    )
    wildcard = api_client.get(
        "/v2/reporting/ad-daily",
        headers=headers,
        params={"seller_id": _SELLER, "q": "%"},
    )

    assert matched.status_code == 200, matched.text
    assert [item["campaign_id"] for item in matched.json()["items"]] == [
        "TEST_CAMP_200"
    ]
    assert wildcard.status_code == 200, wildcard.text
    assert wildcard.json()["total"] == 0


def test_ad_daily_options_describe_observed_source(api_client, readonly_key, db_engine):
    _seed_rows(db_engine)

    response = api_client.get(
        "/v2/reporting/ad-daily/options",
        headers=_auth(readonly_key),
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    seller = next(item for item in payload["sellers"] if item["seller_id"] == _SELLER)
    assert seller["row_count"] == 3
    assert {
        (item["seller_id"], item["advertiser_id"])
        for item in payload["advertisers"]
        if item["seller_id"] == _SELLER
    } == {(_SELLER, "TEST_ADV_A"), (_SELLER, "TEST_ADV_B")}
    assert payload["min_day"] <= "2026-09-02"
    assert payload["max_day"] >= "2026-09-04"
    assert any(item["endpoint"] == _ENDPOINT for item in payload["endpoints"])


def test_ad_daily_rejects_reversed_date_range(api_client, readonly_key):
    response = api_client.get(
        "/v2/reporting/ad-daily",
        headers=_auth(readonly_key),
        params={"day_from": "2026-09-04", "day_to": "2026-09-03"},
    )

    assert response.status_code == 422
    assert response.json()["detail"] == "day_from must be <= day_to"


def test_ad_daily_list_sorts_by_whitelisted_header(
    api_client,
    readonly_key,
    db_engine,
):
    """表头排序走白名单：可升可降，缺指标的行固定垫底。"""
    _seed_rows(db_engine)
    headers = _auth(readonly_key)
    base = {"seller_id": _SELLER}

    default = api_client.get("/v2/reporting/ad-daily", headers=headers, params=base)
    spend_asc = api_client.get(
        "/v2/reporting/ad-daily",
        headers=headers,
        params={**base, "sort": "spend", "order": "asc"},
    )
    day_asc = api_client.get(
        "/v2/reporting/ad-daily",
        headers=headers,
        params={**base, "sort": "day", "order": "asc"},
    )
    product_desc = api_client.get(
        "/v2/reporting/ad-daily",
        headers=headers,
        params={**base, "sort": "product", "order": "desc"},
    )

    assert default.status_code == 200, default.text
    assert [i["campaign_id"] for i in default.json()["items"]] == [
        "TEST_CAMP_300",
        "TEST_CAMP_200",
        "TEST_CAMP_100",
    ]
    # mixed_real_cost 为 NULL 的 TEST_CAMP_300 必须排在最后（NULLS LAST）
    assert [i["campaign_id"] for i in spend_asc.json()["items"]] == [
        "TEST_CAMP_200",
        "TEST_CAMP_100",
        "TEST_CAMP_300",
    ]
    assert [i["campaign_id"] for i in day_asc.json()["items"]] == [
        "TEST_CAMP_100",
        "TEST_CAMP_200",
        "TEST_CAMP_300",
    ]
    assert [i["product_id"] for i in product_desc.json()["items"]] == [
        "TEST_PRODUCT_3",
        "TEST_PRODUCT_2",
        "TEST_PRODUCT_1",
    ]


def test_ad_daily_rejects_unknown_sort_and_order(api_client, readonly_key):
    """排序键/方向不在白名单 → 422，而不是静默回退成未排序。"""
    headers = _auth(readonly_key)

    unknown_key = api_client.get(
        "/v2/reporting/ad-daily",
        headers=headers,
        params={"sort": "seller_id"},
    )
    unknown_order = api_client.get(
        "/v2/reporting/ad-daily",
        headers=headers,
        params={"order": "sideways"},
    )

    assert unknown_key.status_code == 422
    assert unknown_key.json()["detail"].startswith("sort must be one of:")
    assert "campaign" in unknown_key.json()["detail"]
    assert unknown_order.status_code == 422
    assert unknown_order.json()["detail"] == "order must be asc or desc"


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
        source = re.sub(r"/\*.*?\*/", "", source, flags=re.S)
        return re.sub(r"^\s*//.*$", "", source, flags=re.M)

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
