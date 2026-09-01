"""Tests for tts_erp_v2.jobs.miaoshou.purchase_orders.

In production the miaoshou purchase-order API has 0 records (business
hasn't enabled it yet). These tests assert the empty path works and
that the job would write rows when data exists.

Endpoint (apifox api-482189163): search_goods_purchase_order_page.
Param shape: page / pageSize (NOT pageNo). Response key:
goodsPurchaseOrderList + total.
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


def test_sync_purchase_orders_writes_rows_when_present(
    db_session, fake_client, miaoshou_credentials_row
) -> None:
    """Hypothetical data shape — verifies the job writes when records appear."""
    payload = {
        "goodsPurchaseOrderId": "PO_1",
        "sourceItemId": "src_1",
        "quantity": 5,
        "unitPrice": "3.00",
        "currency": "CNY",
        "gmtCreate": "2026-08-01 10:00:00",
        "gmtModified": "2026-08-15 10:00:00",
    }

    def side_effect(*, path, body, **_kwargs):
        page = int(body.get("pageNo", body.get("page", 1)))
        if page == 1:
            return {
                "result": "success",
                "data": {"goodsPurchaseOrderList": [payload], "total": 1},
            }
        return {
            "result": "success",
            "data": {"goodsPurchaseOrderList": [], "total": 1},
        }

    fake_client.install(side_effect)
    result = sync_purchase_orders(db_session, client=fake_client)
    db_session.commit()
    assert result["orders_upserted"] >= 1
