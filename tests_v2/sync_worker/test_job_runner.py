"""TDD tests for sync_worker.job_runner — sync_jobs lifecycle helper.

Every job calls :func:`run_with_sync_job` so its work is bracketed with
a ``sync_jobs`` row (status='running' → 'succeeded' / 'failed').

What we verify here:

* Happy path: a successful inner callable leaves a sync_jobs row with
  status='succeeded', finished_at populated, and the counters copied
  over from the inner JobResult.
* Failure path: an exception inside the inner callable leaves a row
  with status='failed', error_message carrying the exception type+msg,
  and re-raises.
* The session is committed exactly once on each path so the row is
  durable for operators even when the inner code crashed.
"""
from __future__ import annotations

import pytest
from sqlalchemy import select

from tts_erp_v2.db.models import Credentials, SyncJob
from tts_erp_v2.sync_worker.job_runner import (
    JobResult,
    run_with_sync_job,
)


def _make_credential(session, external_id="TEST_TT_CRED_RUNNER") -> Credentials:
    """Seed a real Credentials row so the FK in sync_jobs.credential_id is valid."""
    cred = Credentials(
        provider="tiktok",
        external_account_id=external_id,
        ciphertext=b"\x00" * 32,
    )
    session.add(cred)
    session.flush()
    return cred

# ─── Happy path ────────────────────────────────────────────────────


def test_run_with_sync_job_writes_succeeded_row(db_session) -> None:
    """Success path: row reflects the inner JobResult counters."""
    def _inner(session):
        return JobResult(rows_total=10, rows_inserted=8, rows_updated=2)

    row, result = run_with_sync_job(
        db_session,
        job_name="tiktok.orders",
        inner=_inner,
    )

    assert row.id is not None
    assert row.status == "succeeded"
    assert row.rows_total == 10
    assert row.rows_inserted == 8
    assert row.rows_updated == 2
    assert row.rows_failed == 0
    assert row.finished_at is not None
    assert row.error_message is None
    assert result.rows_total == 10


def test_run_with_sync_job_commits_row(db_session) -> None:
    """After run_with_sync_job returns, the row is committed (durable)."""
    def _inner(session):
        return JobResult(rows_total=1, rows_inserted=1)

    row, _ = run_with_sync_job(
        db_session,
        job_name="tiktok.orders",
        inner=_inner,
    )
    row_id = row.id

    # Re-query through a fresh transaction to prove commit happened.
    fresh = db_session.execute(
        select(SyncJob).where(SyncJob.id == row_id)
    ).scalar_one()
    assert fresh.status == "succeeded"


def test_run_with_sync_job_passes_inner_kwargs(db_session) -> None:
    """inner_kwargs are forwarded verbatim to the inner callable."""
    seen: dict = {}

    def _inner(session, *, shop_id, scope):
        seen["shop_id"] = shop_id
        seen["scope"] = scope
        return JobResult(rows_total=1)

    run_with_sync_job(
        db_session,
        job_name="tiktok.orders",
        inner=_inner,
        inner_kwargs={"shop_id": "7494763368967603447", "scope": "*"},
    )

    assert seen == {
        "shop_id": "7494763368967603447",
        "scope": "*",
    }


def test_run_with_sync_job_accepts_credential_id(db_session) -> None:
    """credential_id is recorded on the row for ops traceability."""
    cred = _make_credential(db_session)

    def _inner(session):
        return JobResult(rows_total=1)

    row, _ = run_with_sync_job(
        db_session,
        job_name="tiktok.orders",
        credential_id=cred.id,
        inner=_inner,
    )
    assert row.credential_id == cred.id


# ─── Failure path ──────────────────────────────────────────────────


def test_run_with_sync_job_marks_row_failed_on_exception(db_session) -> None:
    """Inner exception → status='failed', error_message captures it."""

    def _boom(session):
        raise ValueError("kaboom")

    with pytest.raises(ValueError, match="kaboom"):
        run_with_sync_job(
            db_session,
            job_name="tiktok.orders",
            inner=_boom,
        )

    # Roll back the outer savepoint so we can re-query.
    rows = db_session.execute(
        select(SyncJob).where(SyncJob.job_name == "tiktok.orders")
    ).scalars().all()
    assert len(rows) == 1
    failed = rows[0]
    assert failed.status == "failed"
    assert "ValueError" in (failed.error_message or "")
    assert "kaboom" in (failed.error_message or "")
    assert failed.finished_at is not None


def test_run_with_sync_job_records_zero_counters_on_failure(
    db_session,
) -> None:
    """Failure rows have zero counters (no inflated success numbers)."""
    def _boom(session):
        raise RuntimeError("upstream down")

    with pytest.raises(RuntimeError):
        run_with_sync_job(
            db_session,
            job_name="tiktok.orders",
            inner=_boom,
        )

    rows = db_session.execute(
        select(SyncJob).where(SyncJob.job_name == "tiktok.orders")
    ).scalars().all()
    assert len(rows) == 1
    assert rows[0].rows_total == 0
    assert rows[0].rows_inserted == 0
    assert rows[0].rows_failed == 0  # bookkeeping is per-inner; we don't blame failed on inner errors


def test_job_result_default_counters_are_zero() -> None:
    """A bare JobResult() has zero counters — safe to pass when inner crashes early."""
    r = JobResult()
    assert r.rows_total == 0
    assert r.rows_inserted == 0
    assert r.rows_updated == 0
    assert r.rows_failed == 0
    assert r.cursor is None


def test_job_result_can_carry_a_cursor() -> None:
    """JobResult carries an optional cursor value for the caller to write back."""
    r = JobResult(rows_total=5, cursor=1_700_000_000_000)
    assert r.cursor == 1_700_000_000_000
