"""冻结 dump 生产者/接收者契约。

广告 daily 夹具来自 prod ``plugin.ad_raw_log`` id=432477；today 来自 id=367614。
订单域按最近 ``dump_processed`` 日志的 endpoint/wire 形状重建（plugin_logs 不存原文）。
after_sales 线上尚无 dump，夹具按 parser 契约补齐。
dumps 接口改动时必须同步改本文件和 ``tests/api/_fixtures/``。
"""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import text

from tts_erp_v2.plugin.orders.intake._types import DumpDomain

pytestmark = [pytest.mark.domain_api, pytest.mark.layer_integration]

FIXTURE_ROOT = Path(__file__).resolve().parent / "_fixtures"
SELLER = "TEST_cross-seller"
DUMP_DAY = "2026-10-04"
ANALYTICS_DUMPS = "/v2/analytics/sync/dumps"
ORDER_DUMPS = "/v2/order-sync/dumps"

ORDER_DOMAIN_FIXTURES: dict[DumpDomain, tuple[str, ...]] = {
    DumpDomain.ORDERS: ("cross-repo-order-dump.json",),
    DumpDomain.LOGISTICS: ("order-sync-logistics-dump.json",),
    DumpDomain.STATEMENTS: (
        "order-sync-statements-list-dump.json",
        "order-sync-statements-transaction-dump.json",
    ),
    DumpDomain.AFTER_SALES: ("order-sync-after-sales-dump.json",),
    DumpDomain.ORDER_DETAILS: ("order-sync-order-details-dump.json",),
    DumpDomain.ORDER_HISTORY: ("order-sync-order-history-dump.json",),
}

ANALYTICS_FIXTURES = (
    "cross-repo-analytics-v4-dump.json",
    "analytics-today-product-dump.json",
    "analytics-daily-campaign-opt-dump.json",
)

_CLEANUP_SQLS = (
    "DELETE FROM plugin.after_sale_items WHERE shop_id = :s",
    "DELETE FROM plugin.after_sales WHERE shop_id = :s",
    "DELETE FROM plugin.order_timeline WHERE shop_id = :s",
    "DELETE FROM plugin.order_details WHERE shop_id = :s",
    "DELETE FROM plugin.order_lines WHERE shop_id = :s",
    "DELETE FROM plugin.settlement_details WHERE shop_id = :s",
    "DELETE FROM plugin.settlements WHERE shop_id = :s",
    "DELETE FROM plugin.tracking_events WHERE shop_id = :s",
    "DELETE FROM plugin.shipments WHERE shop_id = :s",
    "DELETE FROM plugin.orders WHERE shop_id = :s",
    "DELETE FROM plugin.campaign_opt_logs WHERE seller_id = :s",
    "DELETE FROM plugin.ad_daily WHERE seller_id = :s",
    "DELETE FROM plugin.ad_today WHERE seller_id = :s",
    "DELETE FROM plugin.ad_raw_log WHERE seller_id = :s",
)


@pytest.fixture(autouse=True)
def _cleanup(db_engine):
    with db_engine.begin() as conn:
        for statement in _CLEANUP_SQLS:
            # pi-lens-ignore: python-sql-injection
            conn.execute(text(statement), {"s": SELLER})
    yield
    with db_engine.begin() as conn:
        for statement in _CLEANUP_SQLS:
            # pi-lens-ignore: python-sql-injection
            conn.execute(text(statement), {"s": SELLER})


def _load_fixture(name: str) -> dict:
    path = FIXTURE_ROOT / name
    assert path.is_file(), f"frozen dump fixture missing: {path}"
    return json.loads(path.read_text(encoding="utf-8"))


def _count(db_engine, sql: str) -> int:
    with db_engine.connect() as conn:
        # pi-lens-ignore: python-sql-injection
        return int(conn.execute(text(sql), {"s": SELLER}).scalar() or 0)


def _post_dumps(api_client, key: str, path: str, payload: dict):
    return api_client.post(
        path,
        headers={"Authorization": f"Bearer {key}"},
        json=payload,
    )


def test_every_order_sync_domain_has_frozen_fixture() -> None:
    assert set(ORDER_DOMAIN_FIXTURES) == set(DumpDomain)
    for domain, names in ORDER_DOMAIN_FIXTURES.items():
        for name in names:
            payload = _load_fixture(name)
            assert payload["dump"]["domain"] == domain.value, name


def test_every_analytics_dump_kind_has_frozen_fixture() -> None:
    kinds = set()
    endpoints = set()
    for name in ANALYTICS_FIXTURES:
        payload = _load_fixture(name)
        kinds.add(payload["dump"]["kind"])
        endpoints.add(payload["dump"]["endpoint"])
    assert kinds == {"daily", "today"}
    assert "/oec_ads/shopping/v1/oec/stat/post_product_list" in endpoints
    assert "/oec_ads/shopping/v1/oec/stat/campaign_opt_log_list" in endpoints


def test_plugin_dump_writes_business_rows_and_is_visible_next_round(
    api_client, readwrite_key, db_engine
):
    """daily 广告 + 订单列表：落库后 coverage / has-data 可见。"""
    analytics_dump = _load_fixture("cross-repo-analytics-v4-dump.json")
    order_dump = _load_fixture("cross-repo-order-dump.json")
    headers = {"Authorization": f"Bearer {readwrite_key}"}

    analytics_response = api_client.post(
        ANALYTICS_DUMPS, headers=headers, json=analytics_dump
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
    assert Decimal(str(ad_row)) == Decimal("22.10")
    assert (
        json.loads(raw_body)["body"]["data"]["table"][0]["product_id"]
        == "TEST_cross-product"
    )

    coverage_response = api_client.get(
        "/v2/analytics/sync/coverage",
        headers=headers,
        params={
            "sellerId": SELLER,
            "advertiserId": "TEST_cross-advertiser",
            "endpoint": analytics_dump["dump"]["endpoint"],
            "kind": "daily",
            "startDay": DUMP_DAY,
            "endDay": DUMP_DAY,
            "campaignId": ["TEST_cross-campaign", "TEST_new-campaign"],
        },
    )
    assert coverage_response.status_code == 200, coverage_response.text
    coverage = coverage_response.json()["data"]
    assert coverage["campaigns"]["TEST_cross-campaign"]["coveredPeriods"] == [
        DUMP_DAY
    ]
    assert coverage["campaigns"]["TEST_new-campaign"] == {
        "coveredPeriods": [],
        "totalCovered": 0,
    }

    order_response = api_client.post(
        ORDER_DUMPS, headers=headers, json=order_dump
    )
    assert order_response.status_code == 200, order_response.text
    assert _count(db_engine, "SELECT count(*) FROM plugin.orders WHERE shop_id = :s") == 1
    assert (
        _count(db_engine, "SELECT count(*) FROM plugin.order_lines WHERE shop_id = :s")
        == 1
    )

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


def test_analytics_today_product_dump_writes_ad_today(
    api_client, readwrite_key, db_engine
):
    payload = _load_fixture("analytics-today-product-dump.json")
    response = _post_dumps(api_client, readwrite_key, ANALYTICS_DUMPS, payload)
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["kind"] == "today"
    assert data["inserted"] == 1
    assert _count(db_engine, "SELECT count(*) FROM plugin.ad_today WHERE seller_id = :s") == 1
    assert _count(db_engine, "SELECT count(*) FROM plugin.ad_daily WHERE seller_id = :s") == 0
    with db_engine.connect() as conn:
        cost = conn.execute(
            text(
                "SELECT mixed_real_cost FROM plugin.ad_today "
                "WHERE seller_id = :s AND campaign_id = 'TEST_cross-campaign'"
            ),
            {"s": SELLER},
        ).scalar()
    assert Decimal(str(cost)) == Decimal("4.21")


def test_analytics_campaign_opt_dump_archives_without_product_rows(
    api_client, readwrite_key, db_engine
):
    payload = _load_fixture("analytics-daily-campaign-opt-dump.json")
    response = _post_dumps(api_client, readwrite_key, ANALYTICS_DUMPS, payload)
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["status"] == "campaign_level"
    assert data["inserted"] == 0
    assert _count(db_engine, "SELECT count(*) FROM plugin.ad_daily WHERE seller_id = :s") == 0
    assert _count(db_engine, "SELECT count(*) FROM plugin.ad_today WHERE seller_id = :s") == 0
    assert _count(db_engine, "SELECT count(*) FROM plugin.ad_raw_log WHERE seller_id = :s") == 1


@pytest.mark.parametrize(
    ("fixture_name", "count_sql", "expected"),
    [
        (
            "cross-repo-order-dump.json",
            "SELECT count(*) FROM plugin.orders WHERE shop_id = :s",
            1,
        ),
        (
            "order-sync-logistics-dump.json",
            "SELECT count(*) FROM plugin.shipments WHERE shop_id = :s",
            1,
        ),
        (
            "order-sync-statements-list-dump.json",
            "SELECT count(*) FROM plugin.settlements WHERE shop_id = :s",
            1,
        ),
        (
            "order-sync-statements-transaction-dump.json",
            "SELECT count(*) FROM plugin.settlement_details WHERE shop_id = :s",
            1,
        ),
        (
            "order-sync-after-sales-dump.json",
            "SELECT count(*) FROM plugin.after_sales WHERE shop_id = :s",
            1,
        ),
        (
            "order-sync-order-details-dump.json",
            "SELECT count(*) FROM plugin.order_details WHERE shop_id = :s",
            1,
        ),
        (
            "order-sync-order-history-dump.json",
            "SELECT count(*) FROM plugin.order_timeline WHERE shop_id = :s",
            4,
        ),
    ],
)
def test_order_sync_dump_writes_domain_rows(
    api_client, readwrite_key, db_engine, fixture_name, count_sql, expected
):
    payload = _load_fixture(fixture_name)
    response = _post_dumps(api_client, readwrite_key, ORDER_DUMPS, payload)
    assert response.status_code == 200, response.text
    assert response.json()["data"] == {}
    assert _count(db_engine, count_sql) == expected
