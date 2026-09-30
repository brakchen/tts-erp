"""Tests for scheduled Miaoshou store-matched purchase-price cleaning."""

from __future__ import annotations

import os
from copy import deepcopy
from decimal import Decimal

import pytest
from sqlalchemy import func, select

from tts_erp_v2.db.models.commerce import ChannelAccount
from tts_erp_v2.db.models.miaoshou import (
    MiaoshouPurchaseOrderRawRecord,
    MiaoshouPurchasePrice,
    MiaoshouSyncIssue,
)
from tts_erp_v2.db.models.procurement import ProcurementAccount
from tts_erp_v2.jobs.miaoshou import purchase_price_clean as job
from tts_erp_v2.proxy.token_service import upsert_credentials

pytestmark = [pytest.mark.domain_miaoshou, pytest.mark.layer_integration]


class FakePurchaseClient:
    def __init__(self, rows: list[dict], *, advertised_total: int | None = None):
        self.rows = rows
        self.advertised_total = advertised_total
        self.calls: list[tuple[int, int]] = []

    def search_page(self, *, page: int, page_size: int = 100) -> dict:
        self.calls.append((page, page_size))
        start = (page - 1) * page_size
        batch = self.rows[start : start + page_size]
        return {
            "result": "success",
            "list": batch,
            "total": str(
                self.advertised_total
                if self.advertised_total is not None
                else len(self.rows)
            ),
            "page": page,
            "pageSize": page_size,
        }


def _seed_web_credential(db_session, fernet_key: str):
    cookie = os.environ.setdefault("TEST_MIAOSHOU_WEB_COOKIE", "TEST_cookie")
    zebra = os.environ.setdefault("TEST_MIAOSHOU_WEB_ZEBRA", "TEST_zebra")
    row = upsert_credentials(
        db_session,
        provider=job.WEB_PROVIDER,
        external_account_id="TEST_WEB_ACCOUNT",
        account_label="TEST web session",
        plaintext_access_token=cookie,
        plaintext_refresh_token=zebra,
        extra={"front_version": "TEST_front"},
    )
    db_session.flush()
    return row


def _seed_store_match(
    db_session,
    *,
    miaoshou_shop_id: str = "TEST_MS_SHOP",
    name: str = "TEST store",
) -> int:
    shop = ChannelAccount(
        platform="tiktok",
        shop_id="TEST_TIKTOK_SHOP",
        account_name=name,
        status="active",
    )
    db_session.add(shop)
    db_session.flush()
    db_session.add(
        ProcurementAccount(
            provider="miaoshou",
            external_account_id=miaoshou_shop_id,
            account_name=name,
            status="normal",
        )
    )
    db_session.flush()
    return shop.id


def _purchase_order(*, miaoshou_shop_id: str = "TEST_MS_SHOP") -> dict:
    source_items = [
        {
            "sourceItemId": "OFFER_A",
            "sourceSkuId": "A_XXL",
            "sourceQuantity": "1",
            "sourceUnitPrice": "26.00",
        },
        {
            "sourceItemId": "OFFER_B",
            "sourceSkuId": "B_L",
            "sourceQuantity": "1",
            "sourceUnitPrice": "29.00",
        },
        {
            "sourceItemId": "OFFER_B",
            "sourceSkuId": "B_XL",
            "sourceQuantity": "1",
            "sourceUnitPrice": "29.00",
        },
        {
            "sourceItemId": "OFFER_B",
            "sourceSkuId": "B_2XL",
            "sourceQuantity": "1",
            "sourceUnitPrice": "29.00",
        },
    ]
    platform_spus = ["TEST_SPU_A", "TEST_SPU_B", "TEST_SPU_B", "TEST_SPU_B"]
    return {
        "purchaseOrderFilterId": "TEST_FILTER_1",
        "purchaseOrderSn": "TEST_PO_1",
        "purchaseOrderStatus": "finished",
        "gmtPurchaseOrderStart": "2026-09-29 12:04:23",
        "purchaseItems": source_items,
        "opOrderPackageList": [
            {
                "shopId": miaoshou_shop_id,
                "shopName": "TEST store",
                "purchaseItems": [{"platformItemId": spu}],
            }
            for spu in platform_spus
        ],
    }


def test_clean_groups_by_offer_spu_and_store() -> None:
    prices, issues = job.clean_latest_prices([_purchase_order()])
    assert issues == []
    assert {(shop, spu): row.unit_cost for (shop, spu), row in prices.items()} == {
        ("TEST_MS_SHOP", "TEST_SPU_A"): Decimal("26.0000"),
        ("TEST_MS_SHOP", "TEST_SPU_B"): Decimal("29.0000"),
    }


def test_group_count_mismatch_is_not_guessed() -> None:
    order = _purchase_order()
    order["opOrderPackageList"] = order["opOrderPackageList"][:1]
    prices, issues = job.clean_latest_prices([order])
    assert prices == {}
    assert issues[0]["issue_type"] == "GROUP_COUNT_MISMATCH"


def test_shop_ambiguous_in_order_is_not_written() -> None:
    order = _purchase_order()
    order["opOrderPackageList"][1]["shopId"] = "TEST_OTHER_SHOP"
    prices, issues = job.clean_latest_prices([order])
    assert ("TEST_MS_SHOP", "TEST_SPU_A") in prices
    assert ("TEST_MS_SHOP", "TEST_SPU_B") not in prices
    assert any(
        issue["issue_type"] == "SHOP_UNRESOLVED_IN_PURCHASE_ORDER" for issue in issues
    )


def test_non_positive_or_non_finite_prices_are_rejected() -> None:
    order = _purchase_order()
    order["purchaseItems"][0]["sourceUnitPrice"] = "-1"
    prices, issues = job.clean_latest_prices([order])
    assert ("TEST_MS_SHOP", "TEST_SPU_A") not in prices
    assert any(issue["issue_type"] == "INVALID_SOURCE_LINE" for issue in issues)


def test_matched_store_writes_independent_purchase_price(
    db_session, fernet_key: str
) -> None:
    credential = _seed_web_credential(db_session, fernet_key)
    shop_pk = _seed_store_match(db_session)
    order = _purchase_order()
    order["autoLoginToken"] = "TEST_secret_token"
    result = job.sync_purchase_prices(
        db_session,
        client=FakePurchaseClient([order]),
        external_account_id=credential.external_account_id,
    )
    db_session.commit()

    assert result["purchase_prices"] == 2
    assert result["matched_shops"] == 2
    assert result["unmatched_shops"] == 0
    rows = db_session.execute(select(MiaoshouPurchasePrice)).scalars().all()
    assert {row.spu_id: row.unit_cost for row in rows} == {
        "TEST_SPU_A": Decimal("26.0000"),
        "TEST_SPU_B": Decimal("29.0000"),
    }
    assert {row.shop_pk for row in rows} == {shop_pk}
    assert {row.miaoshou_shop_id for row in rows} == {"TEST_MS_SHOP"}
    raw = db_session.execute(select(MiaoshouPurchaseOrderRawRecord)).scalar_one()
    assert raw.payload["autoLoginToken"] == "***REDACTED***"


def test_price_is_written_when_product_master_is_absent(
    db_session, fernet_key: str
) -> None:
    credential = _seed_web_credential(db_session, fernet_key)
    shop_pk = _seed_store_match(db_session)
    result = job.sync_purchase_prices(
        db_session,
        client=FakePurchaseClient([_purchase_order()]),
        external_account_id=credential.external_account_id,
    )
    db_session.commit()

    assert result["matched_shops"] == 2
    price = db_session.execute(
        select(MiaoshouPurchasePrice).where(
            MiaoshouPurchasePrice.spu_id == "TEST_SPU_A"
        )
    ).scalar_one()
    assert price.shop_pk == shop_pk
    assert price.resolution_status == "matched_shop"


def test_unmatched_store_is_not_written_and_is_flagged(
    db_session, fernet_key: str
) -> None:
    credential = _seed_web_credential(db_session, fernet_key)
    result = job.sync_purchase_prices(
        db_session,
        client=FakePurchaseClient([_purchase_order()]),
        external_account_id=credential.external_account_id,
    )
    db_session.commit()

    assert result["unmatched_shops"] == 2
    assert (
        db_session.scalar(
            select(func.count())
            .select_from(MiaoshouPurchasePrice)
            .where(MiaoshouPurchasePrice.spu_id == "TEST_SPU_A")
        )
        == 0
    )
    issue = db_session.execute(
        select(MiaoshouSyncIssue).where(
            MiaoshouSyncIssue.external_id == "TEST_MS_SHOP:TEST_SPU_A"
        )
    ).scalar_one()
    assert issue.issue_type == "UNMATCHED_MIAOSHOU_SHOP"


def test_identical_duplicate_purchase_rows_are_deduplicated(
    db_session, fernet_key: str
) -> None:
    credential = _seed_web_credential(db_session, fernet_key)
    _seed_store_match(db_session)
    result = job.sync_purchase_prices(
        db_session,
        client=FakePurchaseClient([_purchase_order(), deepcopy(_purchase_order())]),
        external_account_id=credential.external_account_id,
    )
    assert result["orders_seen"] == 1


def test_conflicting_duplicate_purchase_row_ids_are_rejected(
    db_session, fernet_key: str
) -> None:
    credential = _seed_web_credential(db_session, fernet_key)
    duplicate = deepcopy(_purchase_order())
    duplicate["purchaseOrderSn"] = "TEST_PO_DUPLICATE"
    with pytest.raises(RuntimeError, match="conflicting row ids"):
        job.sync_purchase_prices(
            db_session,
            client=FakePurchaseClient([_purchase_order(), duplicate]),
            external_account_id=credential.external_account_id,
            max_retries=0,
        )


def test_missing_credentials_skips_without_network(db_session) -> None:
    client = FakePurchaseClient([_purchase_order()])
    result = job.sync_purchase_prices(
        db_session,
        client=client,
        external_account_id="TEST_NOT_CONFIGURED",
    )
    assert result["skipped"] is True
    assert client.calls == []


def test_pagination_missing_metadata_is_rejected(db_session, fernet_key: str) -> None:
    credential = _seed_web_credential(db_session, fernet_key)

    class MissingMetadataClient:
        def search_page(self, *, page: int, page_size: int = 100) -> dict:
            return {"result": "success"}

    with pytest.raises(RuntimeError, match="missing fields"):
        job.sync_purchase_prices(
            db_session,
            client=MissingMetadataClient(),
            external_account_id=credential.external_account_id,
            max_retries=0,
        )


def test_pagination_count_mismatch_fails_without_writes(
    db_session, fernet_key: str
) -> None:
    credential = _seed_web_credential(db_session, fernet_key)
    client = FakePurchaseClient([_purchase_order()], advertised_total=2)
    with pytest.raises(RuntimeError, match="pagination incomplete"):
        job.sync_purchase_prices(
            db_session,
            client=client,
            external_account_id=credential.external_account_id,
            max_retries=0,
        )
    assert (
        db_session.scalar(
            select(func.count()).select_from(MiaoshouPurchaseOrderRawRecord)
        )
        == 0
    )
