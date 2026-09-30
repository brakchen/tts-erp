"""Integration coverage for Miaoshou package list/detail synchronization."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import func, select

from tts_erp_v2.db.models.commerce import ChannelAccount, SalesOrder, SalesOrderLine
from tts_erp_v2.db.models.fulfillment import Shipment, ShipmentLine
from tts_erp_v2.db.models.integration import RawRecord, SyncCursor, SyncIssue
from tts_erp_v2.jobs.miaoshou import packages as packages_job
from tts_erp_v2.jobs.miaoshou.packages import (
    DETAIL_ENDPOINT,
    DETAIL_PATH,
    JOB_NAME,
    SEARCH_ENDPOINT,
    SEARCH_PATH,
    sync_package_detail,
    sync_packages,
)

pytestmark = [pytest.mark.domain_miaoshou, pytest.mark.layer_integration]


def _seed_order(db_session) -> tuple[SalesOrder, SalesOrderLine]:
    shop = ChannelAccount(
        platform="tiktok",
        shop_id="TEST_PACKAGE_SHOP",
        account_name="TEST package shop",
        status="active",
    )
    db_session.add(shop)
    db_session.flush()
    order = SalesOrder(
        shop_pk=shop.id,
        order_id="TEST_PACKAGE_ORDER",
        status="AWAITING_SHIPMENT",
        currency="VND",
    )
    db_session.add(order)
    db_session.flush()
    line = SalesOrderLine(
        order_pk=order.id,
        external_line_id="TEST_PACKAGE_LINE",
        external_product_id_snapshot="TEST_SPU",
        external_variant_id_snapshot="TEST_SKU",
        quantity=1,
        unit_price=100,
        currency="VND",
    )
    db_session.add(line)
    db_session.flush()
    return order, line


def _package_payload(*, package_id: str = "TEST_PACKAGE_1") -> dict:
    return {
        "opOrderPackageId": package_id,
        "platform": "tiktok",
        "site": "VN",
        "shopId": 17060852,
        "shopName": "TEST package shop",
        "appPackageNo": "TEST_APP_PACKAGE_1",
        "appPackageStatus": "wait_receiver_confirm",
        "platformPackageStatus": "SHIPPED",
        "fulfillmentType": "sellerFulfillment",
        "logisticsNo": "TEST_TRACKING_1",
        "orderInfo": {
            "platformOrderSn": "TEST_PACKAGE_ORDER",
            "currency": "VND",
            "gmtOrderModified": "2026-09-28 20:05:00",
            "gmtDelivery": "2026-09-28 20:00:00",
        },
        "items": [
            {
                "platformOrderItemIndex": "TEST_PACKAGE_LINE",
                "quantity": 2,
                "platformItemId": "TEST_SPU",
                "platformSkuId": "TEST_SKU",
            }
        ],
        "logisticsAgentProductInfo": {
            "logisticsAgentProductId": 99,
            "productName": "TEST logistics product",
            "logisticsCompany": "TEST carrier",
            "logisticsNo": "TEST_TRACKING_1",
        },
    }


def test_sync_packages_persists_shipment_raw_and_cursor(
    db_session, fake_client, miaoshou_credentials_row
) -> None:
    order, _line = _seed_order(db_session)
    package = _package_payload()
    fake_client.install(
        lambda **_: {
            "result": "success",
            "code": "success",
            "message": "",
            "data": {
                "orderPackageList": [package],
                "total": 1,
                "page": 1,
                "pageSize": 100,
            },
        }
    )

    result = sync_packages(db_session, client=fake_client)
    db_session.commit()

    assert result == {
        "pages_walked": 1,
        "packages_seen": 1,
        "shipments_upserted": 1,
        "items_seen": 1,
        "pending_recovered": 0,
        "rate_limit_retries": 0,
        "issues": 0,
    }
    assert fake_client.calls == [
        {"path": SEARCH_PATH, "body": {"page": 1, "pageSize": 100}}
    ]
    shipment = db_session.execute(
        select(Shipment).where(Shipment.order_pk == order.id)
    ).scalar_one()
    assert shipment.external_package_id == "TEST_PACKAGE_1"
    assert shipment.tracking_number == "TEST_TRACKING_1"
    assert shipment.provider_id == "99"
    assert shipment.provider_name == "TEST carrier"
    assert shipment.status == "wait_receiver_confirm"
    assert shipment.shipped_at == datetime(2026, 9, 28, 12, 0, tzinfo=UTC)
    # Miaoshou exposes SKU/product ids, not the TikTok line_id used by
    # sales_order_lines. Keep exact item membership in raw JSON; do not guess.
    assert (
        db_session.scalar(
            select(func.count())
            .select_from(ShipmentLine)
            .where(ShipmentLine.shipment_id == shipment.id)
        )
        == 0
    )
    raw = db_session.execute(
        select(RawRecord).where(RawRecord.external_id == "TEST_PACKAGE_1")
    ).scalar_one()
    assert raw.endpoint == SEARCH_ENDPOINT
    assert raw.payload["items"][0]["platformSkuId"] == "TEST_SKU"
    cursor = db_session.execute(
        select(SyncCursor).where(SyncCursor.job_name == JOB_NAME)
    ).scalar_one()
    assert cursor.cursor_value == "2026-09-28 20:05:00"
    assert cursor.scope == f"credential:{miaoshou_credentials_row.id}"


def test_sync_packages_is_idempotent(
    db_session, fake_client, miaoshou_credentials_row
) -> None:
    _seed_order(db_session)
    package = _package_payload()
    fake_client.install(
        lambda **_: {
            "result": "success",
            "data": {
                "orderPackageList": [package],
                "total": 1,
                "page": 1,
                "pageSize": 100,
            },
        }
    )

    sync_packages(
        db_session, client=fake_client, gmt_modified_from="2026-09-28 20:00:00"
    )
    sync_packages(
        db_session, client=fake_client, gmt_modified_from="2026-09-28 20:00:00"
    )
    db_session.commit()

    assert (
        db_session.scalar(
            select(func.count())
            .select_from(Shipment)
            .where(Shipment.external_package_id == "TEST_PACKAGE_1")
        )
        == 1
    )
    assert db_session.scalar(select(func.count()).select_from(ShipmentLine)) == 0


def test_partial_package_update_preserves_known_shipment_fields(
    db_session, fake_client, miaoshou_credentials_row
) -> None:
    _seed_order(db_session)
    state = {"package": _package_payload()}
    fake_client.install(
        lambda **_: {
            "result": "success",
            "data": {
                "orderPackageList": [state["package"]],
                "total": 1,
                "page": 1,
                "pageSize": 100,
            },
        }
    )
    sync_packages(
        db_session, client=fake_client, gmt_modified_from="2026-09-28 20:00:00"
    )
    state["package"] = {
        "opOrderPackageId": "TEST_PACKAGE_1",
        "orderInfo": {
            "platformOrderSn": "TEST_PACKAGE_ORDER",
            "gmtOrderModified": "2026-09-28 20:10:00",
        },
        "items": [],
    }
    sync_packages(
        db_session, client=fake_client, gmt_modified_from="2026-09-28 20:00:00"
    )
    db_session.commit()

    shipment = db_session.execute(
        select(Shipment).where(Shipment.external_package_id == "TEST_PACKAGE_1")
    ).scalar_one()
    assert shipment.tracking_number == "TEST_TRACKING_1"
    assert shipment.provider_id == "99"
    assert shipment.provider_name == "TEST carrier"
    assert shipment.status == "wait_receiver_confirm"
    assert shipment.shipped_at == datetime(2026, 9, 28, 12, 0, tzinfo=UTC)


def test_cursor_is_scoped_per_credential(db_session) -> None:
    packages_job._save_modified_cursor(
        db_session,
        scope="credential:TEST_A",
        value=datetime(2026, 9, 28, 12, 5, tzinfo=UTC),
    )
    packages_job._save_modified_cursor(
        db_session,
        scope="credential:TEST_B",
        value=datetime(2026, 9, 29, 12, 5, tzinfo=UTC),
    )
    db_session.flush()

    assert (
        packages_job._load_modified_cursor(db_session, scope="credential:TEST_A")
        == "2026-09-28 20:00:00"
    )
    assert (
        packages_job._load_modified_cursor(db_session, scope="credential:TEST_B")
        == "2026-09-29 20:00:00"
    )


def test_incomplete_pagination_fails_before_advancing_cursor(
    db_session, fake_client, miaoshou_credentials_row, monkeypatch
) -> None:
    monkeypatch.setattr(packages_job, "MAX_PAGES", 1)
    fake_client.install(
        lambda **_: {
            "result": "success",
            "data": {
                "orderPackageList": [_package_payload()] * 100,
                "total": 200,
                "page": 1,
                "pageSize": 100,
            },
        }
    )

    with pytest.raises(RuntimeError, match="pagination incomplete"):
        sync_packages(db_session, client=fake_client)

    assert (
        db_session.execute(
            select(SyncCursor).where(SyncCursor.job_name == JOB_NAME)
        ).scalar_one_or_none()
        is None
    )


def test_sync_packages_keeps_raw_and_records_unknown_order(
    db_session, fake_client, miaoshou_credentials_row
) -> None:
    package = _package_payload()
    package["orderInfo"]["platformOrderSn"] = "TEST_UNKNOWN_ORDER"
    fake_client.install(
        lambda **_: {
            "result": "success",
            "data": {
                "orderPackageList": [package],
                "total": 1,
                "page": 1,
                "pageSize": 100,
            },
        }
    )

    result = sync_packages(db_session, client=fake_client)
    db_session.commit()

    assert result["shipments_upserted"] == 0
    assert result["issues"] == 1
    assert db_session.scalar(select(func.count()).select_from(RawRecord)) >= 1
    issue = db_session.execute(
        select(SyncIssue).where(SyncIssue.job_name == JOB_NAME)
    ).scalar_one()
    assert issue.issue_type == "PACKAGE_ORDER_UNKNOWN"
    assert issue.external_id == f"{miaoshou_credentials_row.id}:TEST_PACKAGE_1"
    assert issue.details["order_id"] == "TEST_UNKNOWN_ORDER"


def test_unknown_order_is_retried_and_recovered_after_order_arrives(
    db_session, fake_client, miaoshou_credentials_row
) -> None:
    package = _package_payload()
    fake_client.install(
        lambda **_: {
            "result": "success",
            "data": {
                "orderPackageList": [package],
                "total": 1,
                "page": 1,
                "pageSize": 100,
            },
        }
    )
    package["orderInfo"]["platformOrderSn"] = "TEST_PACKAGE_ORDER"
    first = sync_packages(db_session, client=fake_client)
    db_session.commit()
    assert first["shipments_upserted"] == 0

    _seed_order(db_session)

    def second_response(*, path, **_):
        if path == SEARCH_PATH:
            return {
                "result": "success",
                "data": {
                    "orderPackageList": [],
                    "total": 0,
                    "page": 1,
                    "pageSize": 100,
                },
            }
        assert path == DETAIL_PATH
        return {
            "result": "success",
            "data": {"orderPackageInfo": package},
        }

    fake_client.install(second_response)
    second = sync_packages(db_session, client=fake_client)
    db_session.commit()

    assert second["packages_seen"] == 0
    assert second["shipments_upserted"] == 1
    assert second["pending_recovered"] == 1
    issue = db_session.execute(
        select(SyncIssue).where(
            SyncIssue.job_name == JOB_NAME,
            SyncIssue.external_id == f"{miaoshou_credentials_row.id}:TEST_PACKAGE_1",
        )
    ).scalar_one()
    assert issue.resolved_at is not None


def test_sync_package_detail_uses_detail_endpoint(
    db_session, fake_client, miaoshou_credentials_row
) -> None:
    _seed_order(db_session)
    package = _package_payload()
    fake_client.install(
        lambda **_: {
            "result": "success",
            "code": "success",
            "data": {"orderPackageInfo": package},
        }
    )

    result = sync_package_detail(
        db_session,
        client=fake_client,
        op_order_package_id="TEST_PACKAGE_1",
    )
    db_session.commit()

    assert result == {"shipments_upserted": 1, "items_seen": 1, "issues": 0}
    assert fake_client.calls == [
        {
            "path": DETAIL_PATH,
            "body": {"opOrderPackageId": "TEST_PACKAGE_1"},
        }
    ]
    raw = db_session.execute(
        select(RawRecord).where(RawRecord.external_id == "TEST_PACKAGE_1")
    ).scalar_one()
    assert raw.endpoint == DETAIL_ENDPOINT
