"""Advertising-detail browser contracts for ``plugin.ad_daily``."""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import text

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
    response = api_client.get(
        "/v2/pages/ad-daily",
        headers=_auth(readonly_key),
    )

    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("text/html")
    assert "广告日明细" in response.text
    assert "../../static/css/ad-daily.css?v=" in response.text
    assert "../../static/js/ad-daily.js?v=" in response.text
    assert "plugin.ad_daily" in response.text
    assert 'href="/static/' not in response.text
    assert 'src="/static/' not in response.text


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
