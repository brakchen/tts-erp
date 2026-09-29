"""EWM-contract tests for :mod:`tts_erp_v2.jobs.miaoshou.purchase_orders`.

The released Apifox API (api-479599781) exposes purchase-order lines as
``goodsPurchaseOrderSkuList``. These tests deliberately reject the previous,
unreleased response shape so an old endpoint cannot be reintroduced.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from sqlalchemy import select

from tts_erp_v2.db.models.integration import RawRecord, SyncIssue, SyncJob
from tts_erp_v2.db.models.procurement import (
    ProcurementProduct,
    PurchaseOrder,
    PurchaseOrderLine,
)
from tts_erp_v2.jobs.miaoshou._common import resolve_miaoshou_context
from tts_erp_v2.jobs.miaoshou.purchase_orders import (
    ENDPOINT,
    JOB_NAME,
    _parse_iso,
    _parse_order_header,
    _parse_order_line,
    _resolve_product_id,
    _to_decimal,
    sync_purchase_orders,
)

pytestmark = [pytest.mark.domain_miaoshou, pytest.mark.layer_integration]


EWM_PATH = (
    "/open/v1/ewm/goods_purchase_order/goods_purchase_order/fetch/"
    "search_goods_purchase_order_page"
)


def _seed_procurement_product(db_session, account_id: int, external_id: str) -> None:
    db_session.add(
        ProcurementProduct(
            procurement_account_id=account_id,
            external_product_id=external_id,
            title=f"TEST product {external_id}",
        )
    )
    db_session.flush()


def _order(
    *,
    order_id: int = 1001,
    sku_id: int = 3001,
    goods_sku_id: str = "TEST_sku_1",
) -> dict[str, object]:
    return {
        "goodsPurchaseOrderId": order_id,
        "goodsPurchaseOrderSupplier": {"sellerId": 2001},
        "status": "finish",
        "goodsPurchaseOrderAmount": "15.00",
        "gmtCreate": "2026-08-01 10:00:00",
        "goodsPurchaseOrderSkuList": [
            {
                "goodsPurchaseOrderSkuId": sku_id,
                "goodsSkuId": goods_sku_id,
                "goodsSkuOuterId": "OUTER_SKU_1",
                "purchaseNum": "5",
                "purchasePrice": "3.00",
            }
        ],
    }


def test_to_decimal_and_parse_iso_handle_contract_values() -> None:
    assert _to_decimal("12.50") == Decimal("12.50")
    assert _to_decimal("not-a-number") is None
    assert _to_decimal(None) is None
    assert _parse_iso("2026-08-01T10:00:00.123") == datetime(
        2026, 8, 1, 10, 0, 0, tzinfo=UTC
    )
    assert _parse_iso("not-a-date") is None


def test_parse_order_header_maps_ewm_fields() -> None:
    parsed = _parse_order_header(_order())
    assert parsed == {
        "external_purchase_order_id": "1001",
        "supplier_id": "2001",
        "status": "finish",
        "currency": None,
        "total_amount": Decimal("15.00"),
        "paid_at": None,
        "completed_at": None,
        "source_created_at": datetime(2026, 8, 1, 10, 0, 0, tzinfo=UTC),
        "source_updated_at": None,
    }


def test_parse_order_header_rejects_legacy_identifiers() -> None:
    assert _parse_order_header({"purchaseOrderId": "TEST_old"}) is None
    assert _parse_order_header({"goodsPurchaseOrderId": ""}) is None
    assert _parse_order_header({}) is None


def test_parse_order_line_maps_ewm_sku_fields() -> None:
    parsed = _parse_order_line(
        "1001",
        {
            "goodsPurchaseOrderSkuId": 3001,
            "goodsSkuId": "TEST_sku_1",
            "purchaseNum": "5",
            "purchasePrice": "3.00",
        },
    )
    assert parsed == {
        "external_line_id": "3001",
        "external_product_id": "TEST_sku_1",
        "quantity": Decimal("5"),
        "unit_cost": Decimal("3.00"),
        "currency": None,
        "line_status": None,
        "_order_external_id": "1001",
    }


def test_parse_order_line_rejects_legacy_identifiers() -> None:
    assert _parse_order_line(
        "1001", {"goodsPurchaseOrderLineId": "TEST_old"}
    ) is None
    assert _parse_order_line("1001", {"goodsPurchaseOrderSkuId": ""}) is None
    assert _parse_order_line("1001", {}) is None


def test_sync_purchase_orders_writes_ewm_order_and_sku_line(
    db_session, fake_client, miaoshou_credentials_row
) -> None:
    ctx = resolve_miaoshou_context(db_session)
    assert ctx is not None
    _seed_procurement_product(db_session, ctx.account_id, "TEST_sku_1")
    fake_client.install(
        lambda **_: {
            "result": "success",
            "data": {"goodsPurchaseOrderList": [_order()], "total": 1},
        }
    )

    result = sync_purchase_orders(db_session, client=fake_client)
    db_session.commit()

    assert result == {
        "pages_walked": 1,
        "orders_seen": 1,
        "orders_upserted": 1,
        "lines_upserted": 1,
        "rate_limit_retries": 0,
        "issues": 0,
    }
    assert fake_client.calls == [
        {"path": EWM_PATH, "body": {"page": 1, "pageSize": 100}}
    ]

    order = db_session.execute(
        select(PurchaseOrder).where(PurchaseOrder.external_purchase_order_id == "1001")
    ).scalar_one()
    assert order.supplier_id == "2001"
    assert order.total_amount == Decimal("15.0000")
    assert order.source_created_at == datetime(2026, 8, 1, 10, 0, 0, tzinfo=UTC)

    line = db_session.execute(
        select(PurchaseOrderLine).where(PurchaseOrderLine.external_line_id == "3001")
    ).scalar_one()
    assert line.quantity == Decimal("5.0000")
    assert line.unit_cost == Decimal("3.0000")
    assert line.currency is None
    assert line.line_status is None


def test_sync_purchase_orders_calculates_pages_from_total_count(
    db_session, fake_client, miaoshou_credentials_row
) -> None:
    """The EWM response has a record count, not a total-page field."""
    ctx = resolve_miaoshou_context(db_session)
    assert ctx is not None
    _seed_procurement_product(db_session, ctx.account_id, "TEST_page_1")
    _seed_procurement_product(db_session, ctx.account_id, "TEST_page_2")

    def side_effect(*, path, body, **_kwargs):
        page = body["page"]
        return {
            "result": "success",
            "data": {
                "goodsPurchaseOrderList": [
                    _order(
                        order_id=1000 + page,
                        sku_id=3000 + page,
                        goods_sku_id=f"TEST_page_{page}",
                    )
                ],
                "total": 101,
            },
        }

    fake_client.install(side_effect)
    result = sync_purchase_orders(db_session, client=fake_client)
    db_session.commit()

    assert result["pages_walked"] == 2
    assert result["orders_upserted"] == 2
    assert result["lines_upserted"] == 2
    assert [call["body"]["page"] for call in fake_client.calls] == [1, 2]


def test_sync_purchase_orders_reports_unknown_goods_sku(
    db_session, fake_client, miaoshou_credentials_row
) -> None:
    fake_client.install(
        lambda **_: {
            "result": "success",
            "data": {
                "goodsPurchaseOrderList": [_order(goods_sku_id="TEST_unknown_sku")],
                "total": 1,
            },
        }
    )

    result = sync_purchase_orders(db_session, client=fake_client)
    db_session.commit()

    assert result["orders_upserted"] == 1
    assert result["lines_upserted"] == 0
    issue = db_session.execute(
        select(SyncIssue).where(
            SyncIssue.job_name == JOB_NAME,
            SyncIssue.issue_type == "PURCHASE_ORDER_PRODUCT_UNKNOWN",
            SyncIssue.external_id == "TEST_unknown_sku",
        )
    ).scalar_one()
    assert issue.details == {"purchase_order_id": "1001"}


def test_sync_purchase_orders_reports_missing_ewm_sku_id(
    db_session, fake_client, miaoshou_credentials_row
) -> None:
    order = _order()
    order["goodsPurchaseOrderSkuList"] = [{"goodsSkuId": "TEST_sku_1"}]
    fake_client.install(
        lambda **_: {
            "result": "success",
            "data": {"goodsPurchaseOrderList": [order], "total": 1},
        }
    )

    result = sync_purchase_orders(db_session, client=fake_client)
    db_session.commit()

    assert result["issues"] == 1
    issue = db_session.execute(
        select(SyncIssue).where(
            SyncIssue.job_name == JOB_NAME,
            SyncIssue.issue_type == "PURCHASE_ORDER_LINE_MISSING_ID",
            SyncIssue.external_id == "1001",
        )
    ).scalar_one()
    assert issue is not None


def test_sync_purchase_orders_ignores_removed_legacy_line_container(
    db_session, fake_client, miaoshou_credentials_row
) -> None:
    """The old ``goodsPurchaseOrderLineList`` fallback is intentionally gone."""
    order = _order()
    order.pop("goodsPurchaseOrderSkuList")
    order["goodsPurchaseOrderLineList"] = [
        {
            "goodsPurchaseOrderLineId": "TEST_old_line",
            "goodsId": "TEST_old_product",
            "quantity": 1,
            "unitPrice": "1.00",
        }
    ]
    fake_client.install(
        lambda **_: {
            "result": "success",
            "data": {"goodsPurchaseOrderList": [order], "total": 1},
        }
    )

    result = sync_purchase_orders(db_session, client=fake_client)
    db_session.commit()
    assert result["orders_upserted"] == 1
    assert result["lines_upserted"] == 0
    assert result["issues"] == 0


def test_sync_purchase_orders_records_ewm_raw_payload(
    db_session, fake_client, miaoshou_credentials_row
) -> None:
    fake_client.install(
        lambda **_: {
            "result": "success",
            "data": {"goodsPurchaseOrderList": [_order()], "total": 1},
        }
    )

    sync_purchase_orders(db_session, client=fake_client)
    db_session.commit()
    raw = db_session.execute(
        select(RawRecord).where(
            RawRecord.endpoint == ENDPOINT,
            RawRecord.external_id == "1001",
        )
    ).scalar_one()
    assert raw.payload["goodsPurchaseOrderSkuList"][0]["goodsSkuId"] == "TEST_sku_1"
    assert raw.credential_id == miaoshou_credentials_row.id


def test_resolve_product_id_returns_none_for_empty_external_id(
    db_session, miaoshou_credentials_row
) -> None:
    ctx = resolve_miaoshou_context(db_session)
    assert ctx is not None
    assert _resolve_product_id(
        db_session, procurement_account_id=ctx.account_id, external_product_id=None
    ) is None
    assert _resolve_product_id(
        db_session, procurement_account_id=ctx.account_id, external_product_id=""
    ) is None


def test_sync_purchase_orders_records_successful_empty_job(
    db_session, fake_client, miaoshou_credentials_row
) -> None:
    fake_client.install(
        lambda **_: {
            "result": "success",
            "data": {"goodsPurchaseOrderList": [], "total": 0},
        }
    )

    result = sync_purchase_orders(db_session, client=fake_client)
    db_session.commit()

    assert result["orders_seen"] == 0
    job = db_session.execute(
        select(SyncJob)
        .where(SyncJob.job_name == JOB_NAME, SyncJob.status == "succeeded")
        .order_by(SyncJob.id.desc())
    ).scalars().first()
    assert job is not None
    assert job.extra["pages_walked"] == 1
    assert job.extra["rate_limit_retries"] == 0


def test_sync_purchase_orders_requires_miaoshou_credentials(
    db_session, fake_client, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MIAOSHOU_LICENSE_ID", "")
    with pytest.raises(RuntimeError, match="no miaoshou credentials row"):
        sync_purchase_orders(db_session, client=fake_client)
