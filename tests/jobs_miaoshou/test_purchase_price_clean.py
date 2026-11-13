"""Tests for scheduled Miaoshou store-matched purchase-price cleaning."""

from __future__ import annotations

from copy import deepcopy
from decimal import Decimal

import pytest
from sqlalchemy import select

from tests._job_invocation import invoke_seeded_job
from tts_erp_v2.db.models.commerce import ChannelAccount
from tts_erp_v2.db.models.miaoshou import (
    MiaoshouPackage,
    MiaoshouPackageItem,
    MiaoshouPackageRawRecord,
    MiaoshouPurchaseOrderRawRecord,
    MiaoshouPurchasePrice,
    MiaoshouSyncIssue,
)
from tts_erp_v2.db.models.procurement import ProcurementAccount
from tts_erp_v2.jobs.miaoshou import purchase_price_clean as job
from tts_erp_v2.proxy.token_service import upsert_credentials

pytestmark = [pytest.mark.domain_miaoshou, pytest.mark.layer_integration]


class FakePurchaseClient:
    def __init__(self, rows: list[dict], *, advertised_total: int | str | None = None):
        self.rows = rows
        self.advertised_total = advertised_total
        self.calls: list[dict] = []

    def _call_erp(self, *, path: str, body: dict) -> dict:
        assert path == "/open/v1/order/purchase_order/purchase_record/search_list"
        assert body["purchaseOrderStatus"] == "finished"
        self.calls.append(body)
        start = (body["page"] - 1) * body["pageSize"]
        return {
            "result": "success",
            "data": {
                "list": self.rows[start : start + body["pageSize"]],
                "total": len(self.rows)
                if self.advertised_total is None
                else self.advertised_total,
            },
        }


def _seed_credential(db_session):
    return upsert_credentials(
        db_session,
        provider="miaoshou",
        external_account_id="TEST_PURCHASE_APP",
        plaintext_access_token="TEST_APP_KEY",
        plaintext_refresh_token="TEST_APP_SECRET",
    )


def _seed_package(
    db_session, credential_id: int, *, shop_id: str = "TEST_MS_SHOP"
) -> None:
    raw = MiaoshouPackageRawRecord(
        credential_id=credential_id,
        endpoint="TEST_package",
        payload={},
        payload_hash="TEST_hash",
    )
    db_session.add(raw)
    db_session.flush()
    package = MiaoshouPackage(
        credential_id=credential_id,
        external_package_id=f"TEST_PACKAGE_{shop_id}",
        raw_record_id=raw.id,
        source_endpoint="TEST_package",
        platform="tiktok",
        shop_id=shop_id,
        platform_order_sn="TEST_TIKTOK_ORDER",
        raw_payload={},
    )
    db_session.add(package)
    db_session.flush()
    db_session.add(
        MiaoshouPackageItem(
            package_id=package.id,
            external_package_item_id="TEST_ITEM",
            platform_product_id="TEST_SPU",
            raw_payload={},
        )
    )


def _seed_store_match(db_session) -> int:
    shop = ChannelAccount(
        platform="tiktok", shop_id="TEST_TIKTOK_SHOP", account_name="TEST store"
    )
    db_session.add(shop)
    db_session.add(
        ProcurementAccount(
            provider="miaoshou",
            external_account_id="TEST_MS_SHOP",
            account_name="TEST store",
        )
    )
    db_session.flush()
    return shop.id


def _purchase_order() -> dict:
    return {
        "purchaseOrderSn": "TEST_PO",
        "purchasePlatformName": "1688",
        "purchaseOrderPayment": "102.00",
        "gmtPurchaseOrderStart": "2026-09-29 12:04:23",
        "purchaseItems": [
            {
                "sourceTitle": "TEST OFFER",
                "sourceSkuSubName": "TEST M",
                "sourceQuantity": "1",
                "sourceUnitPrice": "26.00",
            },
            {
                "sourceTitle": "TEST OFFER",
                "sourceSkuSubName": "TEST L",
                "sourceQuantity": "2",
                "sourceUnitPrice": "36.00",
            },
        ],
        "opOrderPackageList": [
            {
                "platform": "tiktok",
                "relateStatus": "normal",
                "platformOrderSn": "TEST_TIKTOK_ORDER",
                "shopName": "TEST store",
                "purchaseItems": [
                    {"platformItemId": "TEST_SPU", "unitPrice": "100000"}
                ],
            }
        ],
    }


def _run(db_session, client):
    return invoke_seeded_job(
        job.sync_purchase_prices,
        db_session,
        client=client,
        external_account_id="TEST_PURCHASE_APP",
        max_retries=0,
    )


def test_writes_weighted_cost_without_a_product_master_and_redacts_raw_payload(
    db_session,
    fernet_key: str,
) -> None:
    credential = _seed_credential(db_session)
    _seed_package(db_session, credential.id)
    shop_pk = _seed_store_match(db_session)
    order = _purchase_order()
    order["autoLoginToken"] = "TEST_SECRET"
    client = FakePurchaseClient([order, deepcopy(order)])

    result = _run(db_session, client)
    assert result["orders_seen"] == 1
    assert result["purchase_prices"] == 1
    assert client.calls[0]["gmtRangeType"] == "gmtPurchaseOrderStart"
    price = db_session.scalars(select(MiaoshouPurchasePrice)).one()
    assert (price.shop_pk, price.miaoshou_shop_id, price.spu_id) == (
        shop_pk,
        "TEST_MS_SHOP",
        "TEST_SPU",
    )
    assert price.unit_cost == Decimal("32.6667")
    assert price.currency == "CNY"
    raw = db_session.scalars(select(MiaoshouPurchaseOrderRawRecord)).one()
    assert raw.payload["autoLoginToken"] == "***REDACTED***"
    assert raw.endpoint == "miaoshou.purchase_record.search_list"


@pytest.mark.parametrize(
    ("case", "issue_type"),
    [
        ("multiple_spus", "PURCHASE_ITEM_MAPPING_UNVERIFIED"),
        ("multiple_titles", "PURCHASE_ITEM_MAPPING_UNVERIFIED"),
        ("missing_package", "SHOP_UNRESOLVED_IN_PURCHASE_ORDER"),
        ("ambiguous_package", "SHOP_UNRESOLVED_IN_PURCHASE_ORDER"),
        ("unmatched_store", "UNMATCHED_MIAOSHOU_SHOP"),
        ("invalid_price", "INVALID_SOURCE_LINE"),
        ("unknown_currency", "PURCHASE_CURRENCY_UNVERIFIED"),
        ("cancelled", "PURCHASE_STATUS_UNVERIFIED"),
    ],
)
def test_skips_unverified_orders(
    db_session, fernet_key: str, case: str, issue_type: str
) -> None:
    credential = _seed_credential(db_session)
    order = _purchase_order()
    if case != "missing_package":
        _seed_package(db_session, credential.id)
    if case != "unmatched_store":
        _seed_store_match(db_session)
    if case == "multiple_spus":
        order["opOrderPackageList"][0]["purchaseItems"].append(
            {"platformItemId": "TEST_OTHER_SPU"}
        )
    elif case == "multiple_titles":
        order["purchaseItems"][1]["sourceTitle"] = "TEST OTHER OFFER"
    elif case == "ambiguous_package":
        _seed_package(db_session, credential.id, shop_id="TEST_OTHER_SHOP")
    elif case == "invalid_price":
        order["purchaseItems"][1]["sourceUnitPrice"] = "NaN"
    elif case == "unknown_currency":
        order["purchasePlatformName"] = "TEST UNKNOWN PLATFORM"
    elif case == "cancelled":
        order["purchaseOrderStatus"] = "cancel"

    assert _run(db_session, FakePurchaseClient([order]))["purchase_prices"] == 0
    assert db_session.scalars(select(MiaoshouPurchasePrice)).first() is None
    assert db_session.scalars(select(MiaoshouSyncIssue)).one().issue_type == issue_type


def test_distinct_records_with_the_same_order_number_expose_a_price_conflict(
    db_session,
    fernet_key: str,
) -> None:
    credential = _seed_credential(db_session)
    _seed_package(db_session, credential.id)
    _seed_store_match(db_session)
    order = _purchase_order()
    duplicate = deepcopy(order)
    duplicate["purchaseItems"][0]["sourceUnitPrice"] = "27"

    result = _run(db_session, FakePurchaseClient([order, duplicate]))
    assert result["orders_seen"] == 2
    assert result["purchase_prices"] == 0
    assert (
        db_session.scalars(select(MiaoshouSyncIssue)).one().issue_type
        == "LATEST_PRICE_AMBIGUOUS"
    )


def test_older_observation_does_not_replace_a_newer_stored_price(
    db_session, fernet_key: str
) -> None:
    credential = _seed_credential(db_session)
    _seed_package(db_session, credential.id)
    _seed_store_match(db_session)
    newer = _purchase_order()
    newer["gmtPurchaseOrderStart"] = "2026-10-01 00:00:00"
    assert _run(db_session, FakePurchaseClient([newer]))["purchase_prices"] == 1

    older = _purchase_order()
    older["purchaseItems"][0]["sourceUnitPrice"] = "99"
    assert _run(db_session, FakePurchaseClient([older]))["purchase_prices"] == 0
    assert db_session.scalars(select(MiaoshouPurchasePrice)).one().unit_cost == Decimal(
        "32.6667"
    )


def test_missing_credentials_skips_without_network(db_session) -> None:
    client = FakePurchaseClient([_purchase_order()])
    assert _run(db_session, client)["skipped"]
    assert client.calls == []


def test_api_updates_the_existing_price_without_creating_a_duplicate(
    db_session,
    fernet_key: str,
) -> None:
    credential = _seed_credential(db_session)
    _seed_package(db_session, credential.id)
    shop_pk = _seed_store_match(db_session)
    previous = upsert_credentials(
        db_session,
        provider="miaoshou_web",
        external_account_id="TEST_RETIRED_SOURCE",
        plaintext_access_token="TEST_RETIRED",
    )
    db_session.add(
        MiaoshouPurchasePrice(
            credential_id=previous.id,
            miaoshou_shop_id="TEST_MS_SHOP",
            shop_pk=shop_pk,
            spu_id="TEST_SPU",
            unit_cost=Decimal(10),
            currency="CNY",
            source_purchase_order_sn="TEST_OLD",
            source_item_id="TEST_OLD_ITEM",
            resolution_status="matched_shop",
            evidence={},
        )
    )
    db_session.flush()
    price_id = db_session.scalars(select(MiaoshouPurchasePrice.id)).one()

    assert (
        _run(db_session, FakePurchaseClient([_purchase_order()]))["purchase_prices"]
        == 1
    )
    price = db_session.scalars(select(MiaoshouPurchasePrice)).one()
    assert price.id == price_id
    assert price.credential_id == credential.id
    assert price.unit_cost == Decimal("32.6667")


def test_newer_unverified_record_does_not_publish_an_older_price(
    db_session,
    fernet_key: str,
) -> None:
    credential = _seed_credential(db_session)
    _seed_package(db_session, credential.id)
    _seed_store_match(db_session)
    older = _purchase_order()
    newer = _purchase_order() | {
        "purchaseOrderSn": "TEST_NEWER_PO",
        "gmtPurchaseOrderStart": "2026-10-01 00:00:00",
    }
    newer["opOrderPackageList"][0]["purchaseItems"].append(
        {"platformItemId": "TEST_OTHER_SPU"}
    )
    assert _run(db_session, FakePurchaseClient([older, newer]))["purchase_prices"] == 0
    assert db_session.scalars(select(MiaoshouPurchasePrice)).first() is None


def test_pagination_count_mismatch_fails_without_writes(
    db_session, fernet_key: str
) -> None:
    _seed_credential(db_session)
    client = FakePurchaseClient([_purchase_order()], advertised_total=2)
    with pytest.raises(RuntimeError, match="pagination incomplete"):
        _run(db_session, client)
    assert db_session.scalars(select(MiaoshouPurchaseOrderRawRecord)).first() is None


@pytest.mark.parametrize("advertised_total", [3, "3"])
def test_pagination_follows_data_total_and_keeps_a_fixed_upper_bound(
    db_session,
    fernet_key: str,
    monkeypatch,
    advertised_total: int | str,
) -> None:
    _seed_credential(db_session)
    monkeypatch.setattr(job, "PAGE_SIZE", 2)
    rows = [
        _purchase_order() | {"purchaseOrderSn": f"TEST_PO_{index}"}
        for index in range(3)
    ]
    client = FakePurchaseClient(rows, advertised_total=advertised_total)
    result = _run(db_session, client)
    assert result["pages_walked"] == 2
    assert result["orders_seen"] == 3
    assert [body["page"] for body in client.calls] == [1, 2]
    assert len({body["gmtRangeTo"] for body in client.calls}) == 1
