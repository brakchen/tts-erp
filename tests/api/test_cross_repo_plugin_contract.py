"""跨仓库真实 dump 生产者/接收者契约。

夹具由 ads-data-sync 的 dump constructors 断言生成；本测试再把同一 wire
request 送进 tts-erp 的真实 FastAPI 接收路由，验证业务表和下一轮查询结果。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from sqlalchemy import text

pytestmark = [pytest.mark.domain_api, pytest.mark.layer_integration]

PLUGIN_ROOT = Path(__file__).resolve().parents[3] / "ads-data-sync"
FIXTURE_ROOT = PLUGIN_ROOT / "tests" / "_fixtures"
SELLER = "TEST_cross-seller"


@pytest.fixture(autouse=True)
def _cleanup(db_engine):
    with db_engine.begin() as conn:
        for statement in (
            "DELETE FROM plugin.order_lines WHERE shop_id = :s",
            "DELETE FROM plugin.settlement_details WHERE shop_id = :s",
            "DELETE FROM plugin.settlements WHERE shop_id = :s",
            "DELETE FROM plugin.tracking_events WHERE shop_id = :s",
            "DELETE FROM plugin.shipments WHERE shop_id = :s",
            "DELETE FROM plugin.orders WHERE shop_id = :s",
            "DELETE FROM plugin.ad_daily WHERE seller_id = :s",
            "DELETE FROM plugin.ad_today WHERE seller_id = :s",
            "DELETE FROM plugin.ad_monthly WHERE seller_id = :s",
            "DELETE FROM plugin.ad_raw_log WHERE seller_id = :s",
            "DELETE FROM plugin.raw_log WHERE shop_id = :s",
        ):
            conn.execute(text(statement), {"s": SELLER})
    yield
    with db_engine.begin() as conn:
        for statement in (
            "DELETE FROM plugin.order_lines WHERE shop_id = :s",
            "DELETE FROM plugin.settlement_details WHERE shop_id = :s",
            "DELETE FROM plugin.settlements WHERE shop_id = :s",
            "DELETE FROM plugin.tracking_events WHERE shop_id = :s",
            "DELETE FROM plugin.shipments WHERE shop_id = :s",
            "DELETE FROM plugin.orders WHERE shop_id = :s",
            "DELETE FROM plugin.ad_daily WHERE seller_id = :s",
            "DELETE FROM plugin.ad_today WHERE seller_id = :s",
            "DELETE FROM plugin.ad_monthly WHERE seller_id = :s",
            "DELETE FROM plugin.ad_raw_log WHERE seller_id = :s",
            "DELETE FROM plugin.raw_log WHERE shop_id = :s",
        ):
            conn.execute(text(statement), {"s": SELLER})


def _load_fixture(name: str) -> dict:
    path = FIXTURE_ROOT / name
    if not path.exists():
        pytest.skip(f"shared plugin fixture is unavailable: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def test_plugin_dump_writes_business_rows_and_is_visible_next_round(
    api_client, readwrite_key, db_engine
):
    """plugin producer → backend receiver → business tables → coverage/has-data。"""
    analytics_dump = _load_fixture("cross-repo-analytics-v4-dump.json")
    order_dump = _load_fixture("cross-repo-order-dump.json")
    headers = {"Authorization": f"Bearer {readwrite_key}"}

    analytics_response = api_client.post(
        "/v2/analytics/sync/dumps", headers=headers, json=analytics_dump
    )
    assert analytics_response.status_code == 200, analytics_response.text
    assert analytics_response.json()["data"]["inserted"] == 1

    with db_engine.connect() as conn:
        ad_row = conn.execute(
            text(
                "SELECT mixed_real_cost FROM plugin.ad_daily "
                "WHERE seller_id = :s AND campaign_id = 'TEST_cross-campaign'"
            ),
            {"s": SELLER},
        ).scalar()
        raw_body = conn.execute(
            text(
                "SELECT response_body::text FROM plugin.ad_raw_log "
                "WHERE seller_id = :s ORDER BY id DESC LIMIT 1"
            ),
            {"s": SELLER},
        ).scalar()
    assert str(ad_row) == "123.45"
    assert json.loads(raw_body)["body"]["data"]["table"][0]["product_id"] == "TEST_cross-product"

    coverage_response = api_client.get(
        "/v2/analytics/sync/coverage",
        headers=headers,
        params={
            "sellerId": SELLER,
            "advertiserId": "TEST_cross-advertiser",
            "endpoint": analytics_dump["dump"]["endpoint"],
            "kind": "daily",
            "startDay": "2026-09-10",
            "endDay": "2026-09-10",
            "campaignId": ["TEST_cross-campaign", "TEST_new-campaign"],
        },
    )
    assert coverage_response.status_code == 200, coverage_response.text
    coverage = coverage_response.json()["data"]
    assert coverage["campaigns"]["TEST_cross-campaign"]["coveredPeriods"] == [
        "2026-09-10"
    ]
    assert coverage["campaigns"]["TEST_new-campaign"] == {
        "coveredPeriods": [],
        "totalCovered": 0,
    }

    order_response = api_client.post(
        "/v2/order-sync/dumps", headers=headers, json=order_dump
    )
    assert order_response.status_code == 200, order_response.text
    assert order_response.json()["data"]["rowsWritten"] == 2

    with db_engine.connect() as conn:
        order_count, line_count = conn.execute(
            text(
                "SELECT "
                "(SELECT count(*) FROM plugin.orders WHERE shop_id = :s), "
                "(SELECT count(*) FROM plugin.order_lines WHERE shop_id = :s)"
            ),
            {"s": SELLER},
        ).one()
    assert order_count == 1
    assert line_count == 1

    has_data_response = api_client.post(
        "/v2/order-sync/has-data",
        headers=headers,
        json={
            "scope": {"sellerId": SELLER, "shopId": SELLER},
            "domain": "orders",
            "ids": ["TEST_cross-order", "TEST_missing-order"],
        },
    )
    assert has_data_response.status_code == 200, has_data_response.text
    assert has_data_response.json()["data"]["covered"] == {
        "TEST_cross-order": True,
        "TEST_missing-order": False,
    }
