"""TDD tests for jobs.tiktok.after_sales — returns + cancellations."""

from __future__ import annotations

from decimal import Decimal

import pytest
from sqlalchemy import select, text

from tts_erp_v2.db.models import (
    Case,
    CaseLine,
    ChannelAccount,
    Credentials,
    SalesOrder,
    SalesOrderLine,
    SyncIssue,
)
from tts_erp_v2.jobs.tiktok import after_sales as after_sales_job
from tts_erp_v2.sync_worker import watermarks
from tts_erp_v2.sync_worker.job_runner import run_with_sync_job

pytestmark = [pytest.mark.domain_after_sales, pytest.mark.layer_integration]


@pytest.fixture(autouse=True)
def _apply_cases_refund_columns(db_session):
    """Apply the migration 0002_cases_refund_amount DDL within this test's
    transaction so the new columns exist for the ORM. The conftest's
    outer-transaction-rollback unwinds the DDL at test end — no
    persistent change to the prod DB.
    """
    db_session.execute(
        text(
            "ALTER TABLE after_sales.cases "
            "ADD COLUMN IF NOT EXISTS refund_amount NUMERIC(20, 4)"
        )
    )
    db_session.execute(
        text("ALTER TABLE after_sales.cases ADD COLUMN IF NOT EXISTS currency TEXT")
    )
    db_session.flush()
    yield


class FakeProxy:
    def __init__(self, *, returns_pages=None, cancels_pages=None):
        self.returns_pages = returns_pages or []
        self.cancels_pages = cancels_pages or []
        self.calls: list[tuple[str, str]] = []

    def __call__(self, method: str, path: str, body=None):
        self.calls.append((method, path))
        # dispatch on endpoint
        if "returns" in path:
            return (
                self.returns_pages.pop(0)
                if self.returns_pages
                else {"code": 0, "data": {"returns": []}}
            )
        if "cancellations" in path:
            return (
                self.cancels_pages.pop(0)
                if self.cancels_pages
                else {"code": 0, "data": {"cancellations": []}}
            )
        return {"code": 404}


def _make_account_with_order(
    session,
    *,
    shop_id: str = "TEST_TT_AFT_SHOP",
    order_id: str = "TEST_SO_A",
    line_id: str = "CL1",
    sku_id: str | None = "CL1_SKU",
    unit_price=9.99,
) -> ChannelAccount:
    cred = Credentials(
        provider="tiktok",
        external_account_id=shop_id,
        ciphertext=b"\x00" * 32,
    )
    session.add(cred)
    session.flush()
    acct = ChannelAccount(
        platform="tiktok",
        external_account_id=shop_id,
        credential_id=cred.id,
        status="active",
    )
    session.add(acct)
    session.flush()
    so = SalesOrder(
        channel_account_id=acct.id,
        external_order_id=order_id,
        status="SHIPPED",
        currency="USD",
    )
    session.add(so)
    session.flush()
    sol = SalesOrderLine(
        sales_order_id=so.id,
        external_line_id=line_id,
        external_variant_id_snapshot=sku_id,
        quantity=1,
        unit_price=unit_price,
        currency="USD",
        line_status="NORMAL",
    )
    session.add(sol)
    session.flush()
    return acct


def _return_payload(
    rid: str, order_id: str, *, update_time: int = 1_700_000_500, lines=None
):
    return {
        "return_id": rid,
        "order_id": order_id,
        "status": "AWAITING",
        "update_time": update_time,
        "return_line_items": lines or [],
    }


def _cancel_payload(
    cid: str, order_id: str, *, update_time: int = 1_700_000_600, lines=None
):
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
                },
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
                                    "refund_amount": {
                                        "amount": "5.00",
                                        "currency": "USD",
                                    },
                                }
                            ],
                        )
                    ],
                    "next_page_token": "",
                },
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
    cases = (
        db_session.execute(select(Case).where(Case.external_case_id.in_(["R1", "C1"])))
        .scalars()
        .all()
    )
    assert {c.case_type for c in cases} == {"RETURN", "CANCEL"}
    assert {c.external_case_id for c in cases} == {"R1", "C1"}
    case_lines = (
        db_session.execute(
            select(CaseLine).where(CaseLine.case_id.in_([c.id for c in cases]))
        )
        .scalars()
        .all()
    )
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
                },
            }
        ],
        cancels_pages=[
            {"code": 0, "data": {"cancellations": [], "next_page_token": ""}}
        ],
    )
    _, result = run_with_sync_job(
        db_session,
        job_name="tiktok.after_sales",
        credential_id=account.credential_id,
        inner=after_sales_job.run,
        inner_kwargs={"proxy_call": proxy, "shop_id": account.external_account_id},
    )
    assert result.rows_failed == 1
    # Filter by the unique (issue_type, external_id) the test inserted —
    # prod has 53 PARSE_ERROR rows for this job_name that would otherwise
    # blow up .scalar_one().
    issue = db_session.execute(
        select(SyncIssue).where(
            SyncIssue.job_name == "tiktok.after_sales",
            SyncIssue.issue_type == "UNKNOWN_ORDER",
            SyncIssue.external_id == "R2",
        )
    ).scalar_one()
    assert issue.issue_type == "UNKNOWN_ORDER"


# ─── P1-1: line id resolution chain ─────────────────────────────────


def test_after_sales_resolves_line_via_line_id(db_session) -> None:
    """order_line_item_id (the production-observed key, equivalent to
    line_id) must resolve through external_line_id to a sales_order_line."""
    account = _make_account_with_order(db_session)
    proxy = FakeProxy(
        returns_pages=[{"code": 0, "data": {"returns": [], "next_page_token": ""}}],
        cancels_pages=[
            {
                "code": 0,
                "data": {
                    "cancellations": [
                        _cancel_payload(
                            "C_LINE",
                            "TEST_SO_A",
                            lines=[
                                {
                                    "order_line_item_id": "CL1",
                                    "cancel_line_item_id": "CXL_LINE",
                                    "sku_id": "CL1_SKU",
                                    "quantity": 1,
                                }
                            ],
                        )
                    ],
                    "next_page_token": "",
                },
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
    assert result.rows_inserted == 1
    cl = db_session.execute(
        select(CaseLine)
        .join(Case, CaseLine.case_id == Case.id)
        .where(Case.external_case_id == "C_LINE")
    ).scalar_one()
    # external_case_line_id prefers the case-side id when present.
    assert cl.external_case_line_id == "CXL_LINE"
    # sales_order_line_id resolved through order_line_item_id → external_line_id.
    sol = db_session.execute(
        select(SalesOrderLine).where(SalesOrderLine.external_line_id == "CL1")
    ).scalar_one()
    assert cl.sales_order_line_id == sol.id
    # No UNKNOWN_LINE issues raised.
    issues = (
        db_session.execute(
            select(SyncIssue).where(
                SyncIssue.job_name == "tiktok.after_sales",
                SyncIssue.issue_type == "UNKNOWN_LINE",
            )
        )
        .scalars()
        .all()
    )
    assert issues == []


def test_after_sales_resolves_line_via_sku_id(db_session) -> None:
    """sku-only path: line has only sku_id; resolution uses
    external_variant_id_snapshot lookup against the order."""
    account = _make_account_with_order(db_session)
    proxy = FakeProxy(
        returns_pages=[{"code": 0, "data": {"returns": [], "next_page_token": ""}}],
        cancels_pages=[
            {
                "code": 0,
                "data": {
                    "cancellations": [
                        _cancel_payload(
                            "C_SKU",
                            "TEST_SO_A",
                            lines=[
                                {
                                    "sku_id": "CL1_SKU",
                                    "quantity": 1,
                                }
                            ],
                        )
                    ],
                    "next_page_token": "",
                },
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
    assert result.rows_inserted == 1
    assert result.rows_failed == 0
    cl = db_session.execute(
        select(CaseLine)
        .join(Case, CaseLine.case_id == Case.id)
        .where(Case.external_case_id == "C_SKU")
    ).scalar_one()
    # sku-only path stores the sku_id as the external_case_line_id.
    assert cl.external_case_line_id == "CL1_SKU"
    sol = db_session.execute(
        select(SalesOrderLine).where(
            SalesOrderLine.external_variant_id_snapshot == "CL1_SKU"
        )
    ).scalar_one()
    assert cl.sales_order_line_id == sol.id


def test_after_sales_unknown_line_when_sku_does_not_match(db_session) -> None:
    """sku_id that doesn't match any line for the order → UNKNOWN_LINE issue."""
    account = _make_account_with_order(db_session)
    proxy = FakeProxy(
        returns_pages=[{"code": 0, "data": {"returns": [], "next_page_token": ""}}],
        cancels_pages=[
            {
                "code": 0,
                "data": {
                    "cancellations": [
                        _cancel_payload(
                            "C_SKU_BAD",
                            "TEST_SO_A",
                            lines=[
                                {
                                    "sku_id": "WRONG_SKU_NOT_IN_ORDER",
                                    "quantity": 1,
                                }
                            ],
                        )
                    ],
                    "next_page_token": "",
                },
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
    # The case itself still inserts; only the line failed.
    assert result.rows_inserted == 1
    cl_count = (
        db_session.execute(
            select(CaseLine)
            .join(Case, CaseLine.case_id == Case.id)
            .where(Case.external_case_id == "C_SKU_BAD")
        )
        .scalars()
        .all()
    )
    assert cl_count == []
    issue = db_session.execute(
        select(SyncIssue).where(
            SyncIssue.job_name == "tiktok.after_sales",
            SyncIssue.issue_type == "UNKNOWN_LINE",
        )
    ).scalar_one()
    assert issue.details["lookup_path"] == "external_variant_id_snapshot"
    assert issue.details["lookup_value"] == "WRONG_SKU_NOT_IN_ORDER"


# ─── case-level refund amount ──────────────────────────────────────


def test_after_sales_persists_case_level_refund_amount(db_session) -> None:
    """case-level refund_amount at the payload top level is parsed into
    cases.refund_amount + cases.currency (migration 0002)."""
    account = _make_account_with_order(db_session)
    proxy = FakeProxy(
        returns_pages=[{"code": 0, "data": {"returns": [], "next_page_token": ""}}],
        cancels_pages=[
            {
                "code": 0,
                "data": {
                    "cancellations": [
                        {
                            "cancel_id": "C_REFUND",
                            "order_id": "TEST_SO_A",
                            "cancel_status": "COMPLETED",
                            "update_time": 1_700_000_700,
                            # newer API shape (production-observed 2026-08-31):
                            "refund_amount": {
                                "currency": "VND",
                                "refund_total": "686850",
                                "refund_tax": "62441",
                                "refund_subtotal": "686850",
                                "refund_shipping_fee": "0",
                            },
                            "cancel_line_items": [],
                        }
                    ],
                    "next_page_token": "",
                },
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
    assert result.rows_inserted == 1
    case = db_session.execute(
        select(Case).where(Case.external_case_id == "C_REFUND")
    ).scalar_one()
    assert case.refund_amount == Decimal(686850)
    assert case.currency == "VND"


def test_after_sales_persists_case_level_refund_amount_legacy_shape(db_session) -> None:
    """Legacy shape {amount, currency} is also accepted."""
    account = _make_account_with_order(db_session)
    proxy = FakeProxy(
        returns_pages=[{"code": 0, "data": {"returns": [], "next_page_token": ""}}],
        cancels_pages=[
            {
                "code": 0,
                "data": {
                    "cancellations": [
                        {
                            "cancel_id": "C_REFUND_LEGACY",
                            "order_id": "TEST_SO_A",
                            "update_time": 1_700_000_701,
                            "refund_amount": {"amount": "5.00", "currency": "USD"},
                            "cancel_line_items": [],
                        }
                    ],
                    "next_page_token": "",
                },
            }
        ],
    )
    run_with_sync_job(
        db_session,
        job_name="tiktok.after_sales",
        credential_id=account.credential_id,
        inner=after_sales_job.run,
        inner_kwargs={"proxy_call": proxy, "shop_id": account.external_account_id},
    )
    case = db_session.execute(
        select(Case).where(Case.external_case_id == "C_REFUND_LEGACY")
    ).scalar_one()
    assert case.refund_amount == Decimal("5.00")
    assert case.currency == "USD"


def test_after_sales_bare_string_refund_does_not_crash(db_session) -> None:
    """A malformed payload (refund_amount is a bare string) must NOT
    crash the job — columns stay NULL and the row still inserts."""
    account = _make_account_with_order(db_session)
    proxy = FakeProxy(
        returns_pages=[{"code": 0, "data": {"returns": [], "next_page_token": ""}}],
        cancels_pages=[
            {
                "code": 0,
                "data": {
                    "cancellations": [
                        {
                            "cancel_id": "C_BAD_REFUND",
                            "order_id": "TEST_SO_A",
                            "update_time": 1_700_000_702,
                            "refund_amount": "0",  # bogus shape from upstream
                            "cancel_line_items": [],
                        }
                    ],
                    "next_page_token": "",
                },
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
    assert result.rows_inserted == 1
    assert result.rows_failed == 0
    case = db_session.execute(
        select(Case).where(Case.external_case_id == "C_BAD_REFUND")
    ).scalar_one()
    assert case.refund_amount is None
    assert case.currency is None


# ─── sync_issues dedup (record_sync_issue) ─────────────────────────


def test_after_sales_unknown_order_dedup_across_ticks(db_session) -> None:
    """The 2026-08-31 audit saw 32 duplicate line_id-missing rows for 2
    cases that re-ticked every 15 min. record_sync_issue dedups on
    (job_name, issue_type, external_id, resolved_at IS NULL) so re-running
    on the same unknown order keeps exactly one row."""
    account = _make_account_with_order(db_session)
    proxy = FakeProxy(
        returns_pages=[
            {
                "code": 0,
                "data": {
                    "returns": [_return_payload("R_DUP", "TEST_SO_NOT_FOUND")],
                    "next_page_token": "",
                },
            }
        ],
        cancels_pages=[
            {"code": 0, "data": {"cancellations": [], "next_page_token": ""}}
        ],
    )
    # First tick
    run_with_sync_job(
        db_session,
        job_name="tiktok.after_sales",
        credential_id=account.credential_id,
        inner=after_sales_job.run,
        inner_kwargs={"proxy_call": proxy, "shop_id": account.external_account_id},
    )
    # Second tick (proxy pages already drained by the first run; both endpoints
    # fall back to empty-page defaults).
    run_with_sync_job(
        db_session,
        job_name="tiktok.after_sales",
        credential_id=account.credential_id,
        inner=after_sales_job.run,
        inner_kwargs={"proxy_call": proxy, "shop_id": account.external_account_id},
    )
    issues = (
        db_session.execute(
            select(SyncIssue).where(
                SyncIssue.job_name == "tiktok.after_sales",
                SyncIssue.issue_type == "UNKNOWN_ORDER",
                # Scope to this test's row: committed production UNKNOWN_ORDER
                # rows (real after_sales sync) would otherwise leak into the count.
                SyncIssue.external_id == "R_DUP",
            )
        )
        .scalars()
        .all()
    )
    # record_sync_issue dedups: exactly one row, not two.
    assert len(issues) == 1
    assert issues[0].external_id == "R_DUP"
