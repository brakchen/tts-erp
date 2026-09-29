"""Tests for tts_erp_v2.jobs.miaoshou.purchase_orders.

In production the miaoshou purchase-order API has 0 records (business
hasn't enabled it yet). These tests assert the empty path works and
that the job would write rows when data exists.

Endpoint (Apifox api-479599781): EWM search_goods_purchase_order_page.
Param shape: page / pageSize (NOT pageNo, maximum pageSize 100). Response
lines are goodsPurchaseOrderSkuList; list response key is goodsPurchaseOrderList + total.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from tts_erp_v2.db.models.integration import SyncJob
from tts_erp_v2.jobs.miaoshou.purchase_orders import sync_purchase_orders

pytestmark = [pytest.mark.domain_miaoshou, pytest.mark.layer_integration]


def test_sync_purchase_orders_empty_path(
    db_session, fake_client, miaoshou_credentials_row
) -> None:
    """Real production state: 0 records → noop succeeds."""
    fake_client.install(
        lambda **_: {
            "result": "success",
            "data": {"goodsPurchaseOrderList": [], "total": 0},
        }
    )
    result = sync_purchase_orders(db_session, client=fake_client)
    db_session.commit()
    assert result["orders_upserted"] == 0
    job = db_session.execute(
        select(SyncJob)
        .where(SyncJob.job_name == "miaoshou.purchase_orders")
        .order_by(SyncJob.id.desc())
        .limit(1)
    ).scalar_one()
    assert job.status == "succeeded"


def test_sync_purchase_orders_uses_ewm_list_contract(
    db_session, fake_client, miaoshou_credentials_row
) -> None:
    """The job calls the released EWM path with the documented page payload."""
    payload = {
        "goodsPurchaseOrderId": "PO_1",
        "goodsPurchaseOrderAmount": "15.00",
        "gmtCreate": "2026-08-01 10:00:00",
        "goodsPurchaseOrderSkuList": [
            {
                "goodsPurchaseOrderSkuId": "PO_SKU_1",
                "goodsSkuId": "SKU_1",
                "purchaseNum": "5",
                "purchasePrice": "3.00",
            }
        ],
    }

    fake_client.install(
        lambda **_: {
            "result": "success",
            "data": {"goodsPurchaseOrderList": [payload], "total": 1},
        }
    )
    result = sync_purchase_orders(db_session, client=fake_client)
    db_session.commit()

    assert result["orders_upserted"] == 1
    assert fake_client.calls == [
        {
            "path": (
                "/open/v1/ewm/goods_purchase_order/goods_purchase_order/fetch/"
                "search_goods_purchase_order_page"
            ),
            "body": {"page": 1, "pageSize": 100},
        }
    ]
