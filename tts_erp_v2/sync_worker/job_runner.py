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

Lifecycle writes are independent short transactions owned by jobs.runner.
The inner callable owns each business unit using ``with session.begin()``;
network calls run between units. Failed units roll back before the independent
failure audit. No failure path commits unfinished business writes.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session

from tts_erp_v2.db.models import SyncJob
from tts_erp_v2.jobs.runner import run_job

log = logging.getLogger("tts_erp_v2.sync_worker.job_runner")


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
    extra: dict[str, Any] | None = None,
) -> SyncJob:
    """Insert a sync_jobs row with status='running' and return it.

    The row's ``started_at`` defaults to server-side ``now()`` per the
    model definition. Caller is expected to commit via
    :func:`run_with_sync_job` — this helper does NOT commit on its own
    within the caller's explicit transaction.

    ``extra`` is operator metadata (for example ``{"shop_id": ...}`` on
    TikTok fan-out). It is not a cursor and is not read by the job itself.
    """
    row = SyncJob(
        job_name=job_name,
        credential_id=credential_id,
        status="running",
        extra=extra,
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
    extra: dict[str, Any] | None = None,
    inner: Callable[..., JobResult],
    inner_kwargs: dict[str, Any] | None = None,
) -> tuple[SyncJob, JobResult]:
    """Bracket an inner callable with sync_jobs lifecycle bookkeeping.

    Returns the (sync_jobs row, inner result). On exception the sync_jobs
    row is marked 'failed' with the exception message; the exception is
    re-raised. The running and terminal records use independent begin contexts;
    inner must leave no active transaction on either exit path.
    """
    with run_job(
        session, job_name=job_name, credential_id=credential_id, extra=extra
    ) as sync_row:
        result = inner(session, **(inner_kwargs or {}))
        sync_row.rows_total = result.rows_total
        sync_row.rows_inserted = result.rows_inserted
        sync_row.rows_updated = result.rows_updated
        sync_row.rows_failed = result.rows_failed
    return sync_row, result


def record_lock_refusal(
    session: Session,
    *,
    job_name: str,
    marker: str,
) -> SyncJob:
    """记一笔「被写者锁挡住 ⇒ 拒绝启动」的痕迹。

    状态用 ``skipped``（本仓已有的终态语义：没干活，但不是失败）；
    ``error_message`` 带稳定标记 ⇒ watchdog 能认出「锁被占 / 锁被绕过」，
    而不是一条没有上下文的失败行。

    这是「锁与断言同批落地」里断言那一半的落点：只有锁，锁被绕过时无人知道；
    只有断言，历史上没人能解释这些行。两者一起，痕迹才有意义。
    """
    now = datetime.now(UTC)
    row = SyncJob(
        job_name=job_name,
        status="skipped",
        error_message=marker,
        started_at=now,
        finished_at=now,
    )
    session.add(row)
    session.flush()
    return row


__all__ = [
    "JobResult",
    "finish_sync_job",
    "record_lock_refusal",
    "run_with_sync_job",
    "start_sync_job",
]
