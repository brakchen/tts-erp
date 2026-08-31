"""TDD tests for jobs.tiktok.after_sales — returns + cancellations."""
from __future__ import annotations

from typing import Any

import pytest
from sqlalchemy import select

from tts_erp_v2.db.models import (
    Case,
    CaseLine,
    ChannelAccount,
    Credentials,
    RawRecord,
    SalesOrder,
    SalesOrderLine,
    SyncIssue,
)
from tts_erp_v2.jobs.tiktok import after_sales as after_sales_job
from tts_erp_v2.sync_worker import watermarks
from tts_erp_v2.sync_worker.job_runner import run_with_sync_job


pytestmark = [pytest.mark.domain_after_sales, pytest.mark.layer_integration]


class FakeProxy:
    def __init__(self, *, returns_pages=None, cancels_pages=None):
        self.returns_pages = returns_pages or []
        self.cancels_pages = cancels_pages or []
        self.calls: list[tuple[str, str]] = []

    def __call__(self, method: str, path: str, body=None):
        self.calls.append((method, path))
        # dispatch on endpoint
        if "returns" in path:
            return self.returns_pages.pop(0) if self.returns_pages else {"code": 0, "data": {"returns": []}}
        if "cancellations" in path:
            return self.cancels_pages.pop(0) if self.cancels_pages else {"code": 0, "data": {"cancellations": []}}
        return {"code": 404}


def _make_account_with_order(session) -> ChannelAccount:
    cred = Credentials(
        provider="tiktok",
        external_account_id="TEST_TT_AFT_SHOP",
        ciphertext=b"\x00" * 32,
    )
    session.add(cred)
    session.flush()
    acct = ChannelAccount(
        platform="tiktok",
        external_account_id="TEST_TT_AFT_SHOP",
        credential_id=cred.id,
        status="active",
    )
    session.add(acct)
    session.flush()
    so = SalesOrder(
        channel_account_id=acct.id,
        external_order_id="TEST_SO_A",
        status="SHIPPED",
        currency="USD",
    )
    session.add(so)
    session.flush()
    sol = SalesOrderLine(
        sales_order_id=so.id,
        external_line_id="CL1",
        quantity=1,
        unit_price=9.99,
        currency="USD",
        line_status="NORMAL",
    )
    session.add(sol)
    session.flush()
    return acct


def _return_payload(rid: str, order_id: str, *, update_time: int = 1_700_000_500, lines=None):
    return {
        "return_id": rid,
        "order_id": order_id,
        "status": "AWAITING",
        "update_time": update_time,
        "return_line_items": lines or [],
    }


def _cancel_payload(cid: str, order_id: str, *, update_time: int = 1_700_000_600, lines=None):
    return {
        "cancel_id": cid,
        "order_id": order_id,
        "status": "AWAITING",
        "update_time": update_time,
        "cancel_line_items": lines or [],
    }


def test_after_sales_writes_returns_and_cancellations(db_session) -> None:
    account = _make_account_with_order(db_session)
    proxy = FakeProxy(
        returns_pages=[
            {
                "code": 0,
                "data": {
                    "returns": [_return_payload("R1", "TEST_SO_A")],
                    "next_page_token": "",
                }
            }
        ],
        cancels_pages=[
            {
                "code": 0,
                "data": {
                    "cancellations": [
                        _cancel_payload(
                            "C1",
                            "TEST_SO_A",
                            lines=[
                                {
                                    "line_id": "CL1",
                                    "quantity": 1,
                                    "refund_amount": {"amount": "5.00", "currency": "USD"},
                                }
                            ],
                        )
                    ],
                    "next_page_token": "",
                }
            }
        ],
    )
    _, result = run_with_sync_job(
        db_session,
        job_name="tiktok.after_sales",
        credential_id=account.credential_id,
        inner=after_sales_job.run,
        inner_kwargs={"proxy_call": proxy, "shop_id": account.external_account_id},
    )
    assert result.rows_inserted == 2
    cases = db_session.execute(
        select(Case).where(Case.external_case_id.in_(["R1", "C1"]))
    ).scalars().all()
    assert {c.case_type for c in cases} == {"RETURN", "CANCEL"}
    assert {c.external_case_id for c in cases} == {"R1", "C1"}
    case_lines = db_session.execute(
        select(CaseLine).where(
            CaseLine.case_id.in_([c.id for c in cases])
        )
    ).scalars().all()
    assert len(case_lines) == 1
    assert case_lines[0].external_case_line_id == "CL1"
    # Watermark advances to the max update_time seen (across both)
    cursor = watermarks.get_cursor(
        db_session, job_name="tiktok.after_sales", scope=account.external_account_id
    )
    assert cursor == 1_700_000_600_000


def test_after_sales_unknown_order_writes_sync_issue(db_session) -> None:
    account = _make_account_with_order(db_session)
    proxy = FakeProxy(
        returns_pages=[
            {
                "code": 0,
                "data": {
                    "returns": [_return_payload("R2", "TEST_SO_NOT_FOUND")],
                    "next_page_token": "",
                }
            }
        ],
        cancels_pages=[{"code": 0, "data": {"cancellations": [], "next_page_token": ""}}],
    )
    _, result = run_with_sync_job(
        db_session,
        job_name="tiktok.after_sales",
        credential_id=account.credential_id,
        inner=after_sales_job.run,
        inner_kwargs={"proxy_call": proxy, "shop_id": account.external_account_id},
    )
    assert result.rows_failed == 1
    issue = db_session.execute(
        select(SyncIssue).where(SyncIssue.job_name == "tiktok.after_sales")
    ).scalar_one()
    assert issue.issue_type == "UNKNOWN_ORDER"
