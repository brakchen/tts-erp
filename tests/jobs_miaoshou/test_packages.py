"""Integration coverage for source-owned Miaoshou package persistence."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import func, select

from tts_erp_v2.db.models.fulfillment import Shipment
from tts_erp_v2.db.models.integration import RawRecord, SyncCursor
from tts_erp_v2.db.models.miaoshou import (
    MiaoshouPackage,
    MiaoshouPackageGiftItem,
    MiaoshouPackageItem,
    MiaoshouPackageRawRecord,
    MiaoshouSyncCursor,
    MiaoshouSyncIssue,
)
from tts_erp_v2.jobs.miaoshou import packages as packages_job
from tts_erp_v2.jobs.miaoshou.packages import (
    DETAIL_PATH,
    JOB_NAME,
    sync_package_detail,
    sync_packages,
)

pytestmark = [pytest.mark.domain_miaoshou, pytest.mark.layer_integration]


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
            "platformOrderStatus": "AWAITING_COLLECTION",
            "currency": "VND",
            "gmtOrderStart": "2026-09-28 19:00:00",
            "gmtOrderModified": "2026-09-28 20:05:00",
            "gmtDelivery": "2026-09-28 20:00:00",
        },
        "items": [
            {
                "opOrderItemId": "TEST_ORDER_ITEM",
                "opOrderPackageItemId": "TEST_PACKAGE_ITEM",
                "platformOrderItemIndex": "TEST_SKU",
                "quantity": 2,
                "platformItemId": "TEST_SPU",
                "platformSkuId": "TEST_SKU",
                "title": "TEST product",
                "skuSubName": "TEST variant",
                "originalPrice": "120.00",
                "discountedPrice": "100.00",
            }
        ],
        "giftItems": [
            {
                "opOrderPackageGiftId": "TEST_GIFT_ITEM",
                "goodsId": "TEST_GIFT",
                "goodsSkuId": "TEST_GIFT_SKU",
                "goodsName": "TEST gift",
                "quantity": "1",
            }
        ],
        "logisticsAgentProductInfo": {
            "logisticsAgentProductId": 99,
            "productName": "TEST logistics product",
            "logisticsCompany": "TEST carrier",
            "logisticsNo": "TEST_TRACKING_1",
        },
    }


def _list_response(package: dict) -> dict:
    return {
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


def test_sync_packages_persists_only_to_miaoshou_schema(
    db_session, fake_client, miaoshou_credentials_row
) -> None:
    package = _package_payload()
    fake_client.install(lambda **_: _list_response(package))

    result = sync_packages(db_session, client=fake_client)
    db_session.commit()

    assert result == {
        "pages_walked": 1,
        "packages_seen": 1,
        "packages_upserted": 1,
        "items_upserted": 1,
        "gifts_upserted": 1,
        "rate_limit_retries": 0,
        "issues": 0,
        "skipped": False,
    }
    row = db_session.execute(
        select(MiaoshouPackage).where(
            MiaoshouPackage.external_package_id == "TEST_PACKAGE_1"
        )
    ).scalar_one()
    assert row.credential_id == miaoshou_credentials_row.id
    assert row.platform_order_sn == "TEST_PACKAGE_ORDER"
    assert row.logistics_no == "TEST_TRACKING_1"
    assert row.source_updated_at == datetime(2026, 9, 28, 12, 5, tzinfo=UTC)
    assert row.raw_payload["items"][0]["platformSkuId"] == "TEST_SKU"
    assert (
        db_session.scalar(
            select(func.count())
            .select_from(MiaoshouPackageRawRecord)
            .where(MiaoshouPackageRawRecord.external_package_id == "TEST_PACKAGE_1")
        )
        == 1
    )
    item = db_session.execute(
        select(MiaoshouPackageItem).where(MiaoshouPackageItem.package_id == row.id)
    ).scalar_one()
    assert item.external_package_item_id == "TEST_PACKAGE_ITEM"
    assert item.platform_product_id == "TEST_SPU"
    assert item.quantity == 2
    gift = db_session.execute(
        select(MiaoshouPackageGiftItem).where(
            MiaoshouPackageGiftItem.package_id == row.id
        )
    ).scalar_one()
    assert gift.external_gift_item_id == "TEST_GIFT_ITEM"
    cursor = db_session.execute(
        select(MiaoshouSyncCursor).where(
            MiaoshouSyncCursor.credential_id == miaoshou_credentials_row.id
        )
    ).scalar_one()
    assert cursor.resource == "packages"
    assert cursor.cursor_value == "2026-09-28 20:05:00"

    # Regression guard: Miaoshou domain data must not scatter into generic
    # raw/cursor tables or fulfillment projections.
    assert (
        db_session.scalar(
            select(func.count())
            .select_from(RawRecord)
            .where(RawRecord.external_id == "TEST_PACKAGE_1")
        )
        == 0
    )
    assert (
        db_session.scalar(
            select(func.count())
            .select_from(SyncCursor)
            .where(SyncCursor.job_name == JOB_NAME)
        )
        == 0
    )
    assert (
        db_session.scalar(
            select(func.count())
            .select_from(Shipment)
            .where(Shipment.external_package_id == "TEST_PACKAGE_1")
        )
        == 0
    )


def test_sync_packages_is_idempotent_but_preserves_raw_history(
    db_session, fake_client, miaoshou_credentials_row
) -> None:
    package = _package_payload()
    fake_client.install(lambda **_: _list_response(package))

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
            .select_from(MiaoshouPackage)
            .where(MiaoshouPackage.external_package_id == "TEST_PACKAGE_1")
        )
        == 1
    )
    assert (
        db_session.scalar(
            select(func.count())
            .select_from(MiaoshouPackageItem)
            .join(MiaoshouPackage)
            .where(MiaoshouPackage.external_package_id == "TEST_PACKAGE_1")
        )
        == 1
    )
    assert (
        db_session.scalar(
            select(func.count())
            .select_from(MiaoshouPackageRawRecord)
            .where(MiaoshouPackageRawRecord.external_package_id == "TEST_PACKAGE_1")
        )
        == 2
    )


def test_partial_payload_preserves_fields_and_absent_children(
    db_session, fake_client, miaoshou_credentials_row
) -> None:
    state = {"package": _package_payload()}
    fake_client.install(lambda **_: _list_response(state["package"]))
    sync_packages(
        db_session, client=fake_client, gmt_modified_from="2026-09-28 20:00:00"
    )

    state["package"] = {
        "opOrderPackageId": "TEST_PACKAGE_1",
        "orderInfo": {
            "platformOrderSn": "TEST_PACKAGE_ORDER",
            "gmtOrderModified": "2026-09-28 20:10:00",
        },
    }
    sync_packages(
        db_session, client=fake_client, gmt_modified_from="2026-09-28 20:00:00"
    )
    db_session.commit()

    row = db_session.execute(
        select(MiaoshouPackage).where(
            MiaoshouPackage.external_package_id == "TEST_PACKAGE_1"
        )
    ).scalar_one()
    assert row.logistics_no == "TEST_TRACKING_1"
    assert row.app_package_status == "wait_receiver_confirm"
    assert (
        db_session.scalar(
            select(func.count())
            .select_from(MiaoshouPackageItem)
            .where(MiaoshouPackageItem.package_id == row.id)
            .where(MiaoshouPackageItem.active.is_(True))
        )
        == 1
    )


def test_malformed_children_do_not_clear_snapshot_and_record_external_id(
    db_session, fake_client, miaoshou_credentials_row
) -> None:
    state = {"package": _package_payload()}
    fake_client.install(lambda **_: _list_response(state["package"]))
    sync_packages(
        db_session, client=fake_client, gmt_modified_from="2026-09-28 20:00:00"
    )
    state["package"] = {
        "opOrderPackageId": "TEST_PACKAGE_1",
        "orderInfo": {
            "platformOrderSn": "TEST_PACKAGE_ORDER",
            "gmtOrderModified": "2026-09-28 20:10:00",
        },
        "items": None,
        "giftItems": {"bad": "shape"},
    }
    result = sync_packages(
        db_session, client=fake_client, gmt_modified_from="2026-09-28 20:00:00"
    )
    db_session.commit()

    assert result["issues"] == 2
    package_row = db_session.execute(
        select(MiaoshouPackage).where(
            MiaoshouPackage.external_package_id == "TEST_PACKAGE_1"
        )
    ).scalar_one()
    assert (
        db_session.scalar(
            select(func.count())
            .select_from(MiaoshouPackageItem)
            .where(MiaoshouPackageItem.package_id == package_row.id)
            .where(MiaoshouPackageItem.active.is_(True))
        )
        == 1
    )
    assert (
        db_session.scalar(
            select(func.count())
            .select_from(MiaoshouPackageGiftItem)
            .where(MiaoshouPackageGiftItem.package_id == package_row.id)
            .where(MiaoshouPackageGiftItem.active.is_(True))
        )
        == 1
    )
    issues = (
        db_session.execute(
            select(MiaoshouSyncIssue).where(
                MiaoshouSyncIssue.credential_id == miaoshou_credentials_row.id,
                MiaoshouSyncIssue.resolved_at.is_(None),
            )
        )
        .scalars()
        .all()
    )
    assert {issue.issue_type for issue in issues} == {
        "PACKAGE_ITEMS_INVALID",
        "PACKAGE_GIFTS_INVALID",
    }
    assert {issue.external_id for issue in issues} == {"TEST_PACKAGE_1"}


def test_explicit_empty_children_soft_remove_previous_rows(
    db_session, fake_client, miaoshou_credentials_row
) -> None:
    state = {"package": _package_payload()}
    fake_client.install(lambda **_: _list_response(state["package"]))
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
        "giftItems": [],
    }
    sync_packages(
        db_session, client=fake_client, gmt_modified_from="2026-09-28 20:00:00"
    )
    db_session.commit()

    package_row = db_session.execute(
        select(MiaoshouPackage).where(
            MiaoshouPackage.external_package_id == "TEST_PACKAGE_1"
        )
    ).scalar_one()
    assert (
        db_session.scalar(
            select(func.count())
            .select_from(MiaoshouPackageItem)
            .where(MiaoshouPackageItem.package_id == package_row.id)
            .where(MiaoshouPackageItem.active.is_(True))
        )
        == 0
    )
    item = db_session.execute(
        select(MiaoshouPackageItem).where(
            MiaoshouPackageItem.package_id == package_row.id
        )
    ).scalar_one()
    assert item.removed_at is not None
    gift = db_session.execute(
        select(MiaoshouPackageGiftItem).where(
            MiaoshouPackageGiftItem.package_id == package_row.id
        )
    ).scalar_one()
    assert gift.active is False
    assert gift.removed_at is not None


def test_missing_package_id_records_miaoshou_issue(
    db_session, fake_client, miaoshou_credentials_row
) -> None:
    package = _package_payload()
    package.pop("opOrderPackageId")
    fake_client.install(lambda **_: _list_response(package))

    result = sync_packages(db_session, client=fake_client)
    db_session.commit()

    assert result["packages_upserted"] == 0
    assert result["issues"] == 1
    issue = db_session.execute(
        select(MiaoshouSyncIssue).where(
            MiaoshouSyncIssue.credential_id == miaoshou_credentials_row.id
        )
    ).scalar_one()
    assert issue.resource == "packages"
    assert issue.issue_type == "PACKAGE_MISSING_ID"


def test_sync_package_detail_uses_detail_endpoint(
    db_session, fake_client, miaoshou_credentials_row
) -> None:
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

    assert result == {
        "packages_upserted": 1,
        "items_upserted": 1,
        "gifts_upserted": 1,
        "issues": 0,
        "skipped": False,
    }
    assert fake_client.calls == [
        {"path": DETAIL_PATH, "body": {"opOrderPackageId": "TEST_PACKAGE_1"}}
    ]
    row = db_session.execute(
        select(MiaoshouPackage).where(
            MiaoshouPackage.external_package_id == "TEST_PACKAGE_1"
        )
    ).scalar_one()
    assert row.source_endpoint == "miaoshou.package.get_package_info"


def test_cursor_is_scoped_per_credential(db_session, miaoshou_credentials_row) -> None:
    packages_job._save_modified_cursor(
        db_session,
        credential_id=miaoshou_credentials_row.id,
        value=datetime(2026, 9, 28, 12, 5, tzinfo=UTC),
    )
    db_session.flush()

    assert (
        packages_job._load_modified_cursor(
            db_session, credential_id=miaoshou_credentials_row.id
        )
        == "2026-09-28 20:00:00"
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
            select(MiaoshouSyncCursor).where(
                MiaoshouSyncCursor.credential_id == miaoshou_credentials_row.id
            )
        ).scalar_one_or_none()
        is None
    )


def test_job_skips_without_miaoshou_schema(
    db_session, fake_client, monkeypatch
) -> None:
    monkeypatch.setattr(packages_job, "_schema_ready", lambda _session: False)

    result = sync_packages(db_session, client=fake_client)
    db_session.commit()

    assert result["skipped"] is True
    assert fake_client.calls == []
