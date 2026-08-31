"""TDD tests for jobs.tiktok.order_detail — fetch detail for known ids.

Verifies:
* Each id is GET'd; on non-zero code we write a sync_issue (UPSTREAM_NONZERO).
* Successful details upsert into sales_orders + sales_order_lines and
  advance the raw_records row.
* Parse failures write sync_issues but the job continues.
"""
from __future__ import annotations

from typing import Any

import pytest
from sqlalchemy import select

from tts_erp_v2.db.models import (
    ChannelAccount,
    Credentials,
    RawRecord,
    SalesOrder,
    SalesOrderLine,
    SyncIssue,
)
from tts_erp_v2.jobs.tiktok import order_detail
from tts_erp_v2.sync_worker import watermarks


pytestmark = [pytest.mark.domain_commerce, pytest.mark.layer_integration]
from tts_erp_v2.sync_worker.job_runner import run_with_sync_job


class FakeProxy:
    """Keyed on the URL path. body is ignored for GET-style detail calls."""

    def __init__(self, *, pages: dict[str, dict[str, Any]]):
        self.pages = pages
        self.calls: list[tuple[str, str | None]] = []

    def __call__(self, method: str, path: str, body=None):
        self.calls.append((method, path))
        if path not in self.pages:
            return {"code": 404, "message": "not found", "data": None}
        return self.pages[path]


def _make_account(session) -> ChannelAccount:
    cred = Credentials(
        provider="tiktok",
        external_account_id="TEST_TT_DETAIL_SHOP",
        ciphertext=b"\x00" * 32,
    )
    session.add(cred)
    session.flush()
    acct = ChannelAccount(
        platform="tiktok",
        external_account_id="TEST_TT_DETAIL_SHOP",
        credential_id=cred.id,
        status="active",
    )
    session.add(acct)
    session.flush()
    return acct


def _order_payload(order_id: str, *, lines=None):
    return {
        "order_id": order_id,
        "order_status": "TO_FULFILL",
        "update_time": 1_700_000_000,
        "currency": "USD",
        "payment_amount": {"amount": "9.99", "currency": "USD"},
        "line_items": lines or [],
    }


def test_detail_writes_raw_records_and_normalized_rows(db_session) -> None:
    account = _make_account(db_session)
    proxy = FakeProxy(
        pages={
            "/order/202309/orders/O1": {
                "code": 0,
                "message": "ok",
                "data": {
                    "order": _order_payload(
                        "O1",
                        lines=[
                            {
                                "line_id": "L1",
                                "product_id": "P1",
                                "sku_id": "S1",
                                "quantity": 1,
                                "sale_price": {"amount": "9.99", "currency": "USD"},
                            }
                        ],
                    )
                },
            }
        }
    )

    _, result = run_with_sync_job(
        db_session,
        job_name="tiktok.order_detail",
        credential_id=account.credential_id,
        inner=order_detail.run,
        inner_kwargs={
            "proxy_call": proxy,
            "shop_id": account.external_account_id,
            "order_ids": ["O1"],
        },
    )

    assert result.rows_total == 1
    assert result.rows_inserted == 1
    assert result.rows_failed == 0
    so = db_session.execute(
        select(SalesOrder).where(SalesOrder.external_order_id == "O1")
    ).scalar_one()
    assert so.external_order_id == "O1"
    lines = db_session.execute(
        select(SalesOrderLine).where(
            SalesOrderLine.sales_order_id == so.id
        )
    ).scalars().all()
    assert len(lines) == 1
    raw_ids = [
        r.external_id
        for r in db_session.execute(
            select(RawRecord).where(RawRecord.external_id == "O1")
        ).scalars().all()
    ]
    assert raw_ids == ["O1"]
    assert proxy.calls == [("GET", "/order/202309/orders/O1")]


def test_detail_writes_sync_issue_on_upstream_error(db_session) -> None:
    account = _make_account(db_session)
    proxy = FakeProxy(pages={})  # O1 will 404
    _, result = run_with_sync_job(
        db_session,
        job_name="tiktok.order_detail",
        credential_id=account.credential_id,
        inner=order_detail.run,
        inner_kwargs={
            "proxy_call": proxy,
            "shop_id": account.external_account_id,
            "order_ids": ["O1"],
        },
    )
    assert result.rows_failed == 1
    issue = db_session.execute(select(SyncIssue).where(SyncIssue.job_name == "tiktok.order_detail")).scalar_one()
    assert issue.issue_type == "UPSTREAM_NONZERO"
    assert issue.external_id == "O1"


def test_detail_empty_order_ids_is_noop(db_session) -> None:
    account = _make_account(db_session)
    proxy = FakeProxy(pages={})
    _, result = run_with_sync_job(
        db_session,
        job_name="tiktok.order_detail",
        credential_id=account.credential_id,
        inner=order_detail.run,
        inner_kwargs={
            "proxy_call": proxy,
            "shop_id": account.external_account_id,
            "order_ids": [],
        },
    )
    assert result.rows_total == 0
    assert result.rows_inserted == 0


def test_detail_parse_failure_continues(db_session) -> None:
    account = _make_account(db_session)
    proxy = FakeProxy(
        pages={
            "/order/202309/orders/BAD": {
                "code": 0,
                "message": "ok",
                "data": {"order": {"order_id": "BAD"}},  # missing update_time
            },
            "/order/202309/orders/OK": {
                "code": 0,
                "message": "ok",
                "data": {"order": _order_payload("OK")},
            },
        }
    )
    _, result = run_with_sync_job(
        db_session,
        job_name="tiktok.order_detail",
        credential_id=account.credential_id,
        inner=order_detail.run,
        inner_kwargs={
            "proxy_call": proxy,
            "shop_id": account.external_account_id,
            "order_ids": ["BAD", "OK"],
        },
    )
    assert result.rows_total == 2
    assert result.rows_failed == 1
    assert result.rows_inserted == 1
    issue = db_session.execute(select(SyncIssue).where(SyncIssue.job_name == "tiktok.order_detail")).scalar_one()
    assert issue.issue_type == "PARSE_ERROR"
