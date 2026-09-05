"""Shared sync_jobs lifecycle wrapper for sync-worker jobs.

Every job in ``tts_erp_v2/jobs/`` calls :func:`run_job` to bracket its
work with a ``sync_jobs`` row (status='running' → 'succeeded' / 'failed').
This centralises the bookkeeping so individual job modules stay focused
on the actual sync logic.

Contract
--------
The inner callable receives an *open* session and any keyword args
the caller passes to :func:`run_job`. It returns a :class:`JobResult`
whose counters are written to the sync_jobs row.

If the inner callable raises, :func:`run_job`:

* Marks the sync_jobs row status='failed' with the exception message.
* Re-raises so the caller (scheduler / CLI) can react.

Note: ``session.commit()`` is called inside the helper. Callers should
NOT commit again (the helper manages transaction boundaries).
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session

from tts_erp_v2.db.models import SyncJob


@dataclass(frozen=True)
class JobResult:
    """Counters a job returns on success."""

    rows_total: int = 0
    rows_inserted: int = 0
    rows_updated: int = 0
    rows_failed: int = 0
    rows_image_fetch_failed: int = 0
    cursor: int | str | None = None  # watermark value to write post-run


def start_sync_job(
    session: Session,
    *,
    job_name: str,
    credential_id: int | None = None,
) -> SyncJob:
    """Insert a sync_jobs row with status='running' and return it.

    The row's ``started_at`` defaults to server-side ``now()`` per the
    model definition. Caller is expected to commit via
    :func:`run_with_sync_job` — this helper does NOT commit on its own
    so the lifecycle row + first business rows share one transaction.
    """
    row = SyncJob(
        job_name=job_name,
        credential_id=credential_id,
        status="running",
    )
    session.add(row)
    session.flush()  # populate row.id for FK references / log clarity
    return row


def finish_sync_job(
    session: Session,
    row: SyncJob,
    *,
    result: JobResult,
    status: str,
    error_message: str | None = None,
) -> None:
    """Update the sync_jobs row with final counters / status."""
    row.finished_at = datetime.now(UTC)
    row.status = status
    row.rows_total = result.rows_total
    row.rows_inserted = result.rows_inserted
    row.rows_updated = result.rows_updated
    row.rows_failed = result.rows_failed
    if error_message is not None:
        row.error_message = error_message


def run_with_sync_job(
    session: Session,
    *,
    job_name: str,
    credential_id: int | None = None,
    inner: Callable[..., JobResult],
    inner_kwargs: dict[str, Any] | None = None,
) -> tuple[SyncJob, JobResult]:
    """Bracket an inner callable with sync_jobs lifecycle bookkeeping.

    Returns the (sync_jobs row, inner result). On exception the sync_jobs
    row is marked 'failed' with the exception message; the exception is
    re-raised. Commits happen on both the success and failure path so the
    sync_jobs row is durable (operators MUST see the run record even when
    the job crashed).
    """
    sync_row = start_sync_job(session, job_name=job_name, credential_id=credential_id)
    try:
        result = inner(session, **(inner_kwargs or {}))
    except Exception as exc:
        finish_sync_job(
            session,
            sync_row,
            result=JobResult(),
            status="failed",
            error_message=f"{type(exc).__name__}: {exc}",
        )
        session.commit()
        raise
    finish_sync_job(session, sync_row, result=result, status="succeeded")
    session.commit()
    return sync_row, result


__all__ = [
    "JobResult",
    "finish_sync_job",
    "run_with_sync_job",
    "start_sync_job",
]
