"""TDD tests for jobs.tiktok.order_detail — fetch detail for known ids.

Verifies:
* Each id is GET'd; on non-zero code we write a sync_issue (UPSTREAM_NONZERO).
* Successful details upsert into sales_orders + sales_order_lines and
  advance the raw_records row.
* Parse failures write sync_issues but the job continues.
* Auto-mode (order_ids=None) pulls unresolved PARSE_ERROR/UNKNOWN_*
  issues for this shop from ``integration.sync_issues`` and processes
  them; successful processing resolves the matching issues.
"""
from __future__ import annotations

from datetime import datetime, timezone
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
from tts_erp_v2.sync_worker.job_runner import run_with_sync_job

pytestmark = [pytest.mark.domain_commerce, pytest.mark.layer_integration]


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


# ─── P1-5: auto-mode (order_ids=None) ───────────────────────────────


def _seed_unresolved_issue(
    session,
    *,
    external_id: str,
    issue_type: str = "PARSE_ERROR",
    detected_at: datetime | None = None,
) -> SyncIssue:
    """Insert an unresolved sync_issue for the order_detail job."""
    issue = SyncIssue(
        job_name="tiktok.order_detail",
        issue_type=issue_type,
        external_id=external_id,
        detected_at=detected_at or datetime(2026, 8, 31, tzinfo=timezone.utc),
    )
    session.add(issue)
    session.flush()
    return issue


def test_detail_auto_mode_pulls_from_sync_issues(db_session) -> None:
    """Auto-mode (order_ids=None) derives inputs from unresolved
    sync_issues for this job, processes each, and resolves on success."""
    account = _make_account(db_session)
    _seed_unresolved_issue(db_session, external_id="O_AUTO_1")
    _seed_unresolved_issue(db_session, external_id="O_AUTO_2:L1")
    proxy = FakeProxy(
        pages={
            "/order/202309/orders/O_AUTO_1": {
                "code": 0,
                "message": "ok",
                "data": {"order": _order_payload("O_AUTO_1")},
            },
            "/order/202309/orders/O_AUTO_2": {
                "code": 0,
                "message": "ok",
                "data": {"order": _order_payload("O_AUTO_2")},
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
            # order_ids omitted on purpose — auto-mode
        },
    )
    assert result.rows_total == 2
    assert result.rows_inserted == 2
    assert result.rows_failed == 0
    # Both issues resolved.
    issues = db_session.execute(
        select(SyncIssue).where(SyncIssue.job_name == "tiktok.order_detail")
    ).scalars().all()
    assert len(issues) == 2
    for i in issues:
        assert i.resolved_at is not None
    assert proxy.calls == [
        ("GET", "/order/202309/orders/O_AUTO_1"),
        ("GET", "/order/202309/orders/O_AUTO_2"),
    ]


def test_detail_auto_mode_dedups_order_id_across_line_issues(db_session) -> None:
    """A line-level UNKNOWN_LINE issue and a PARSE_ERROR issue that
    both reference the same order_id should produce ONE detail call,
    not two (the dedup happens at the order-id level)."""
    account = _make_account(db_session)
    _seed_unresolved_issue(
        db_session, external_id="O_DUP:line_A", issue_type="PARSE_ERROR"
    )
    _seed_unresolved_issue(
        db_session, external_id="O_DUP:line_B", issue_type="UNKNOWN_LINE"
    )
    proxy = FakeProxy(
        pages={
            "/order/202309/orders/O_DUP": {
                "code": 0,
                "message": "ok",
                "data": {"order": _order_payload("O_DUP")},
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
        },
    )
    assert result.rows_total == 1
    assert result.rows_inserted == 1
    assert proxy.calls == [("GET", "/order/202309/orders/O_DUP")]


def test_detail_auto_mode_caps_at_batch_size(db_session) -> None:
    """More issues than AUTO_BATCH_SIZE → only the most-recent batch
    is processed in one tick."""
    from tts_erp_v2.jobs.tiktok.order_detail import AUTO_BATCH_SIZE

    account = _make_account(db_session)
    # Seed AUTO_BATCH_SIZE + 5 issues, each in its own order.
    for i in range(AUTO_BATCH_SIZE + 5):
        _seed_unresolved_issue(
            db_session,
            external_id=f"O_CAP_{i:03d}",
            detected_at=datetime(2026, 8, 31, 12, i, tzinfo=timezone.utc),
        )
    proxy = FakeProxy(
        pages={
            f"/order/202309/orders/O_CAP_{i:03d}": {
                "code": 0,
                "message": "ok",
                "data": {"order": _order_payload(f"O_CAP_{i:03d}")},
            }
            for i in range(AUTO_BATCH_SIZE + 5)
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
        },
    )
    assert result.rows_total == AUTO_BATCH_SIZE
    assert result.rows_inserted == AUTO_BATCH_SIZE
    # The oldest issues (lowest i) are still open.
    open_issues = db_session.execute(
        select(SyncIssue)
        .where(SyncIssue.job_name == "tiktok.order_detail")
        .where(SyncIssue.resolved_at.is_(None))
    ).scalars().all()
    assert len(open_issues) == 5


def test_detail_auto_mode_failed_fetch_keeps_issue_open(db_session) -> None:
    """A failed detail fetch (upstream non-zero) writes UPSTREAM_NONZERO
    and leaves the original issue open so the next tick retries."""
    account = _make_account(db_session)
    _seed_unresolved_issue(db_session, external_id="O_FAIL")
    proxy = FakeProxy(pages={})  # O_FAIL will 404
    _, result = run_with_sync_job(
        db_session,
        job_name="tiktok.order_detail",
        credential_id=account.credential_id,
        inner=order_detail.run,
        inner_kwargs={
            "proxy_call": proxy,
            "shop_id": account.external_account_id,
        },
    )
    assert result.rows_total == 1
    assert result.rows_failed == 1
    issues = db_session.execute(
        select(SyncIssue).where(SyncIssue.job_name == "tiktok.order_detail")
    ).scalars().all()
    # Original PARSE_ERROR still open, plus the new UPSTREAM_NONZERO.
    types = sorted(i.issue_type for i in issues)
    assert types == ["PARSE_ERROR", "UPSTREAM_NONZERO"]
    par = next(i for i in issues if i.issue_type == "PARSE_ERROR")
    assert par.resolved_at is None


def test_detail_auto_mode_no_issues_is_noop(db_session) -> None:
    """No unresolved issues for this shop → JobResult with zero counters."""
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
        },
    )
    assert result.rows_total == 0
    assert result.rows_inserted == 0
    assert proxy.calls == []


def test_detail_explicit_order_ids_still_resolves(db_session) -> None:
    """Explicit order_ids=[...] still works and resolves matching issues."""
    account = _make_account(db_session)
    _seed_unresolved_issue(db_session, external_id="O_EXPLICIT")
    proxy = FakeProxy(
        pages={
            "/order/202309/orders/O_EXPLICIT": {
                "code": 0,
                "message": "ok",
                "data": {"order": _order_payload("O_EXPLICIT")},
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
            "order_ids": ["O_EXPLICIT"],
        },
    )
    assert result.rows_inserted == 1
    issue = db_session.execute(
        select(SyncIssue).where(SyncIssue.external_id == "O_EXPLICIT")
    ).scalar_one()
    assert issue.resolved_at is not None
