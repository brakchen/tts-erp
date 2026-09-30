"""Tests for scheduled Miaoshou purchase-price cleaning and publication."""

from __future__ import annotations

import os
from copy import deepcopy
from decimal import Decimal

import pytest
from sqlalchemy import func, select

from tts_erp_v2.db.models.commerce import ChannelAccount, ChannelProduct
from tts_erp_v2.db.models.miaoshou import (
    MiaoshouPurchaseOrderRawRecord,
    MiaoshouPurchasePriceCandidate,
    MiaoshouSyncIssue,
)
from tts_erp_v2.db.models.procurement import ManualProductCost
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
    monkey_cookie = os.environ.setdefault(
        "TEST_MIAOSHOU_WEB_COOKIE", "TEST_cookie_value"
    )
    monkey_zebra = os.environ.setdefault("TEST_MIAOSHOU_WEB_ZEBRA", "TEST_zebra_value")
    row = upsert_credentials(
        db_session,
        provider=job.WEB_PROVIDER,
        external_account_id="TEST_WEB_ACCOUNT",
        account_label="TEST web session",
        plaintext_access_token=monkey_cookie,
        plaintext_refresh_token=monkey_zebra,
        extra={"front_version": "TEST_front"},
    )
    db_session.flush()
    return row


def _seed_products(db_session, spus: list[str]) -> dict[str, ChannelProduct]:
    shop = ChannelAccount(
        platform="tiktok",
        shop_id="TEST_PRICE_SHOP",
        account_name="TEST price shop",
        status="active",
    )
    db_session.add(shop)
    db_session.flush()
    result = {}
    for spu in spus:
        row = ChannelProduct(
            shop_pk=shop.id,
            spu_id=spu,
            title=f"TEST {spu}",
            status="ACTIVATE",
        )
        db_session.add(row)
        db_session.flush()
        result[spu] = row
    return result


def _purchase_order() -> dict:
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
        {
            "sourceItemId": "OFFER_C",
            "sourceSkuId": "C_M",
            "sourceQuantity": "1",
            "sourceUnitPrice": "65.00",
        },
        {
            "sourceItemId": "OFFER_C",
            "sourceSkuId": "C_L",
            "sourceQuantity": "1",
            "sourceUnitPrice": "65.00",
        },
    ]
    platform_spus = [
        "TEST_SPU_A",
        "TEST_SPU_B",
        "TEST_SPU_B",
        "TEST_SPU_C",
        "TEST_SPU_C",
        "TEST_SPU_B",
    ]
    return {
        "purchaseOrderFilterId": "TEST_FILTER_1",
        "purchaseOrderSn": "TEST_PO_1",
        "purchaseOrderStatus": "finished",
        "gmtPurchaseOrderStart": "2026-09-29 12:04:23",
        "purchaseItems": source_items,
        "opOrderPackageList": [
            {"purchaseItems": [{"platformItemId": spu}]} for spu in platform_spus
        ],
    }


def test_clean_latest_prices_groups_by_unique_offer_and_spu() -> None:
    prices, issues = job.clean_latest_prices([_purchase_order()])
    assert issues == []
    assert {spu: row.unit_cost for spu, row in prices.items()} == {
        "TEST_SPU_A": Decimal("26.0000"),
        "TEST_SPU_B": Decimal("29.0000"),
        "TEST_SPU_C": Decimal("65.0000"),
    }
    assert prices["TEST_SPU_B"].source_item_id == "OFFER_B"
    assert len(prices["TEST_SPU_B"].source_lines) == 3


def test_group_count_mismatch_is_not_guessed() -> None:
    order = _purchase_order()
    order["opOrderPackageList"] = order["opOrderPackageList"][:1]
    prices, issues = job.clean_latest_prices([order])
    assert prices == {}
    assert issues[0]["issue_type"] == "GROUP_COUNT_MISMATCH"


def test_non_positive_or_non_finite_prices_are_rejected() -> None:
    order = _purchase_order()
    order["purchaseItems"][0]["sourceUnitPrice"] = "-1"
    order["purchaseItems"][1]["sourceUnitPrice"] = "NaN"
    prices, issues = job.clean_latest_prices([order])
    assert "TEST_SPU_A" not in prices
    assert any(issue["issue_type"] == "INVALID_SOURCE_LINE" for issue in issues)
    assert any(
        issue["issue_type"] == "SOURCE_GROUP_NO_VALID_PRICE"
        and issue["spu_id"] == "TEST_SPU_A"
        for issue in issues
    )


def test_sync_writes_candidates_raw_and_manual_costs(
    db_session, fernet_key: str
) -> None:
    credential = _seed_web_credential(db_session, fernet_key)
    products = _seed_products(db_session, ["TEST_SPU_A", "TEST_SPU_B", "TEST_SPU_C"])
    order = _purchase_order()
    order["autoLoginToken"] = "TEST_secret_token"
    order["nested"] = {"Cookie": "TEST_secret_cookie"}
    result = job.sync_purchase_prices(
        db_session,
        client=FakePurchaseClient([order]),
        external_account_id=credential.external_account_id,
    )
    db_session.commit()

    assert result["candidate_spus"] == 3
    assert result["manual_costs_written"] == 3
    assert result["manual_costs_unchanged"] == 0
    assert result["missing_products"] == 0
    raw = db_session.execute(select(MiaoshouPurchaseOrderRawRecord)).scalar_one()
    assert raw.payload["autoLoginToken"] == "***REDACTED***"
    assert raw.payload["nested"]["Cookie"] == "***REDACTED***"
    candidates = (
        db_session.execute(select(MiaoshouPurchasePriceCandidate)).scalars().all()
    )
    assert {row.spu_id: row.unit_cost for row in candidates} == {
        "TEST_SPU_A": Decimal("26.0000"),
        "TEST_SPU_B": Decimal("29.0000"),
        "TEST_SPU_C": Decimal("65.0000"),
    }
    for spu, product in products.items():
        cost = db_session.execute(
            select(ManualProductCost)
            .where(ManualProductCost.spu_pk == product.id)
            .where(ManualProductCost.valid_to.is_(None))
        ).scalar_one()
        assert (
            cost.unit_cost
            == {"TEST_SPU_A": 26, "TEST_SPU_B": 29, "TEST_SPU_C": 65}[spu]
        )
        assert cost.created_by == f"job:{job.JOB_NAME}"


def test_sync_is_idempotent_and_raw_payload_is_deduplicated(
    db_session, fernet_key: str
) -> None:
    credential = _seed_web_credential(db_session, fernet_key)
    products = _seed_products(db_session, ["TEST_SPU_A", "TEST_SPU_B", "TEST_SPU_C"])
    client = FakePurchaseClient([_purchase_order()])
    first = job.sync_purchase_prices(
        db_session, client=client, external_account_id=credential.external_account_id
    )
    second = job.sync_purchase_prices(
        db_session, client=client, external_account_id=credential.external_account_id
    )
    db_session.commit()

    assert first["manual_costs_written"] == 3
    assert second["manual_costs_written"] == 0
    assert second["manual_costs_unchanged"] == 3
    assert (
        db_session.scalar(
            select(func.count()).select_from(MiaoshouPurchaseOrderRawRecord)
        )
        == 1
    )
    assert (
        db_session.scalar(
            select(func.count())
            .select_from(ManualProductCost)
            .where(ManualProductCost.spu_pk.in_([row.id for row in products.values()]))
            .where(ManualProductCost.created_by == f"job:{job.JOB_NAME}")
        )
        == 3
    )


def test_changed_price_closes_history_and_inserts_one_open_row(
    db_session, fernet_key: str
) -> None:
    credential = _seed_web_credential(db_session, fernet_key)
    products = _seed_products(db_session, ["TEST_SPU_A", "TEST_SPU_B", "TEST_SPU_C"])
    original = _purchase_order()
    job.sync_purchase_prices(
        db_session,
        client=FakePurchaseClient([original]),
        external_account_id=credential.external_account_id,
    )
    changed = deepcopy(original)
    changed["purchaseOrderSn"] = "TEST_PO_2"
    changed["purchaseOrderFilterId"] = "TEST_FILTER_2"
    changed["gmtPurchaseOrderStart"] = "2026-09-30 12:04:23"
    changed["purchaseItems"][0]["sourceUnitPrice"] = "27.00"
    result = job.sync_purchase_prices(
        db_session,
        client=FakePurchaseClient([original, changed]),
        external_account_id=credential.external_account_id,
    )
    db_session.commit()

    assert result["manual_costs_written"] == 1
    rows = (
        db_session.execute(
            select(ManualProductCost)
            .where(ManualProductCost.spu_pk == products["TEST_SPU_A"].id)
            .order_by(ManualProductCost.id)
        )
        .scalars()
        .all()
    )
    assert len(rows) == 2
    assert rows[0].valid_to is not None
    assert rows[1].valid_to is None
    assert rows[1].unit_cost == 27


def test_operator_manual_override_is_preserved(db_session, fernet_key: str) -> None:
    credential = _seed_web_credential(db_session, fernet_key)
    products = _seed_products(db_session, ["TEST_SPU_A", "TEST_SPU_B", "TEST_SPU_C"])
    operator_row = ManualProductCost(
        spu_pk=products["TEST_SPU_A"].id,
        unit_cost=Decimal("99.00"),
        currency="CNY",
        created_by="api_key:admin",
        note="operator correction",
    )
    db_session.add(operator_row)
    db_session.flush()

    result = job.sync_purchase_prices(
        db_session,
        client=FakePurchaseClient([_purchase_order()]),
        external_account_id=credential.external_account_id,
    )
    db_session.commit()

    assert result["manual_overrides"] == 1
    current = db_session.execute(
        select(ManualProductCost)
        .where(ManualProductCost.spu_pk == products["TEST_SPU_A"].id)
        .where(ManualProductCost.valid_to.is_(None))
    ).scalar_one()
    assert current.id == operator_row.id
    assert current.unit_cost == 99
    candidate = db_session.execute(
        select(MiaoshouPurchasePriceCandidate).where(
            MiaoshouPurchasePriceCandidate.spu_id == "TEST_SPU_A"
        )
    ).scalar_one()
    assert candidate.unit_cost == 26
    assert candidate.resolution_status == "manual_override"
    assert candidate.manual_cost_id == operator_row.id


def test_missing_product_is_kept_as_candidate_and_issue(
    db_session, fernet_key: str
) -> None:
    credential = _seed_web_credential(db_session, fernet_key)
    _seed_products(db_session, ["TEST_SPU_A", "TEST_SPU_B"])
    result = job.sync_purchase_prices(
        db_session,
        client=FakePurchaseClient([_purchase_order()]),
        external_account_id=credential.external_account_id,
    )
    db_session.commit()

    assert result["missing_products"] == 1
    candidate = db_session.execute(
        select(MiaoshouPurchasePriceCandidate).where(
            MiaoshouPurchasePriceCandidate.spu_id == "TEST_SPU_C"
        )
    ).scalar_one()
    assert candidate.resolution_status == "missing_product"
    assert candidate.spu_pk is None
    issue = db_session.execute(
        select(MiaoshouSyncIssue).where(MiaoshouSyncIssue.external_id == "TEST_SPU_C")
    ).scalar_one()
    assert issue.issue_type == "MISSING_PRODUCT"


def test_missing_credentials_skips_without_network(db_session) -> None:
    client = FakePurchaseClient([_purchase_order()])
    result = job.sync_purchase_prices(
        db_session,
        client=client,
        external_account_id="TEST_NOT_CONFIGURED",
    )
    db_session.commit()
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


def test_identical_duplicate_purchase_rows_are_deduplicated(
    db_session, fernet_key: str
) -> None:
    credential = _seed_web_credential(db_session, fernet_key)
    duplicate = deepcopy(_purchase_order())
    result = job.sync_purchase_prices(
        db_session,
        client=FakePurchaseClient([_purchase_order(), duplicate]),
        external_account_id=credential.external_account_id,
        max_retries=0,
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
