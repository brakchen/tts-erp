"""Shared sync-job primitives: lifecycle + raw-record + sync-issue writes.

Lane C owns ``tts_erp_v2/jobs/``; every miaoshou job (and ``token_refresh``)
runs through :func:`run_job` so the ``integration.sync_jobs`` table gets a
row per execution (start/end/status/rows/extra), and parse / upstream
failures get isolated into ``integration.sync_issues`` rather than
aborting the whole job.

Why a shared runner instead of inline writes
-------------------------------------------
* Cross-job consistency for the ``sync_jobs`` lifecycle (start_time,
  status='running' → 'succeeded' / 'failed', row counters, error
  message, finished_at).
* Single seam to swap when sync-worker (Lane B) wraps each job in an
  APScheduler call.
* Idempotency helpers (raw-record dedup-by-hash, sync-issue dedup-by
  (job_name, issue_type, external_id)) live in one place.
"""
from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from tts_erp_v2.db.models.integration import RawRecord, SyncIssue, SyncJob

log = logging.getLogger("tts_erp_v2.jobs.runner")


# ---- sync_jobs lifecycle -------------------------------------------


def start_job(
    session: Session,
    *,
    job_name: str,
    credential_id: int | None = None,
    extra: dict | None = None,
) -> SyncJob:
    """Insert a 'running' SyncJob row. Caller commits."""
    job = SyncJob(
        job_name=job_name,
        credential_id=credential_id,
        started_at=datetime.now(timezone.utc),
        status="running",
        rows_total=0,
        rows_inserted=0,
        rows_updated=0,
        rows_failed=0,
        extra=extra,
    )
    session.add(job)
    session.flush()
    return job


def finish_job(
    session: Session,
    job: SyncJob,
    *,
    status: str,
    rows_total: int | None = None,
    rows_inserted: int | None = None,
    rows_updated: int | None = None,
    rows_failed: int | None = None,
    error_message: str | None = None,
    extra: dict | None = None,
) -> SyncJob:
    """Update the SyncJob row to terminal status.

    Counter defaults to ``None`` (preserve). Pass ``0`` to explicitly
    zero out a counter that the caller already mutated.
    """
    if status not in ("succeeded", "failed", "skipped"):
        raise ValueError(f"invalid terminal status: {status!r}")
    job.finished_at = datetime.now(timezone.utc)
    job.status = status
    if rows_total is not None:
        job.rows_total = rows_total
    if rows_inserted is not None:
        job.rows_inserted = rows_inserted
    if rows_updated is not None:
        job.rows_updated = rows_updated
    if rows_failed is not None:
        job.rows_failed = rows_failed
    if error_message is not None:
        job.error_message = error_message[:2000]  # truncate to fit TEXT
    if extra is not None:
        merged = dict(job.extra or {})
        merged.update(extra)
        job.extra = merged
    session.flush()
    return job


@contextmanager
def run_job(
    session: Session,
    *,
    job_name: str,
    credential_id: int | None = None,
    extra: dict | None = None,
) -> Iterator[SyncJob]:
    """Context manager that wraps a job's body.

    On clean exit → status='succeeded'. On exception → status='failed'
    with the exception type+message written to ``error_message`` and
    the exception re-raised (so the caller's commit/rollback logic
    remains in charge).

    Usage::

        with run_job(session, job_name="miaoshou.shops") as job:
            ...
            job.extra = {"pages": 3}
    """
    job = start_job(
        session, job_name=job_name, credential_id=credential_id, extra=extra
    )
    try:
        yield job
    except BaseException as e:
        # NOTE: we do NOT swallow. The caller's transaction will be
        # rolled back by their own outer handler — but the error
        # message is captured on the ORM object first.
        finish_job(
            session,
            job,
            status="failed",
            error_message=f"{type(e).__name__}: {e}",
        )
        log.exception("job %s failed", job_name)
        raise
    else:
        # Caller already updated counters; only stamp terminal status
        # if they haven't set it to failed/succeeded themselves.
        if job.status == "running":
            finish_job(session, job, status="succeeded")


# ---- raw_records + sync_issues -------------------------------------


def record_raw_payload(
    session: Session,
    *,
    endpoint: str,
    payload: Any,
    external_id: str | None = None,
    credential_id: int | None = None,
) -> RawRecord:
    """Persist the original API JSON in ``integration.raw_records``.

    Hash is sha256 of the canonical (sorted-keys) JSON, which gives
    downstream jobs a cheap "have I seen this exact payload before?"
    check. Callers must commit the session.
    """
    if isinstance(payload, (dict, list)):
        canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        # Note: dict-order is preserved on Postgres JSONB; this hash is
        # only for dedup-on-write, not for cryptographic equality.
        payload_for_db = payload
    else:
        canonical = str(payload)
        payload_for_db = {"_raw": str(payload)}

    payload_hash = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    row = RawRecord(
        credential_id=credential_id,
        endpoint=endpoint,
        external_id=external_id,
        captured_at=datetime.now(timezone.utc),
        payload=payload_for_db,
        payload_hash=payload_hash,
    )
    session.add(row)
    session.flush()
    return row


def record_sync_issue(
    session: Session,
    *,
    job_name: str,
    issue_type: str,
    external_id: str | None = None,
    details: dict | None = None,
) -> SyncIssue:
    """Insert a row into ``integration.sync_issues``.

    Issues never block the main job — they are advisory, surfaced to
    ops via the linkage.coverage dashboard. We dedup on
    ``(job_name, issue_type, external_id, detected_at::date)`` so a
    re-run doesn't accumulate duplicate rows for the same upstream
    failure. The dedup is best-effort (a SELECT first); the job does
    not abort on a uniqueness violation.
    """
    existing = session.execute(
        select(SyncIssue)
        .where(SyncIssue.job_name == job_name)
        .where(SyncIssue.issue_type == issue_type)
        .where(SyncIssue.external_id == external_id)
        .where(SyncIssue.resolved_at.is_(None))
    ).scalar_one_or_none()
    if existing is not None:
        existing.detected_at = datetime.now(timezone.utc)
        if details is not None:
            existing.details = details
        session.flush()
        return existing
    row = SyncIssue(
        job_name=job_name,
        issue_type=issue_type,
        external_id=external_id,
        details=details,
        detected_at=datetime.now(timezone.utc),
    )
    session.add(row)
    session.flush()
    return row


# ---- type alias for run-job callables ------------------------------

JobFn = Callable[[Session, Any], dict]
"""A job's core function: ``(session, **kwargs) -> result_dict``."""


__all__ = [
    "JobFn",
    "finish_job",
    "record_raw_payload",
    "record_sync_issue",
    "run_job",
    "start_job",
]
