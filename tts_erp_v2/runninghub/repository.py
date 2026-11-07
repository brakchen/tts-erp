"""PostgreSQL claims and fencing. A process semaphore is never the cloud quota."""

from __future__ import annotations

from datetime import timedelta

from sqlalchemy import func, or_, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from tts_erp_v2.db.models.runninghub import (
    CloudQuota,
    GenerationAttempt,
    GenerationBatch,
    GenerationJob,
    new_id,
)

LEASE = timedelta(minutes=15)
LOCAL_PHASES = {"DOWNLOADING", "FINALIZING"}
STOPPING_BATCH_STATES = {"TERMINATING", "TERMINATED"}


def reserve(session: Session, job_id: str, quota_id: str, limit: int) -> str | None:
    session.execute(
        insert(CloudQuota)
        .values(id=quota_id, limit_count=limit)
        .on_conflict_do_nothing()
    )
    quota = session.scalar(
        select(CloudQuota)
        .where(CloudQuota.id == quota_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    # A lower profile limit takes precedence for the shared credential scope.
    quota.limit_count = min(quota.limit_count, limit)
    used = session.scalar(
        select(func.count())
        .select_from(GenerationAttempt)
        .where(
            GenerationAttempt.quota_id == quota_id, GenerationAttempt.reserved.is_(True)
        )
    )
    if quota.paused_reason or used >= quota.limit_count:
        return None
    job = session.get(GenerationJob, job_id)
    if job is None:
        return None
    batch = session.scalar(
        select(GenerationBatch)
        .where(GenerationBatch.id == job.batch_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    job = session.scalar(
        select(GenerationJob)
        .where(GenerationJob.id == job_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if batch.status != "ACTIVE" or job.status != "PLANNED":
        return None
    number = (
        session.scalar(
            select(func.count())
            .select_from(GenerationAttempt)
            .where(GenerationAttempt.job_id == job.id)
        )
        + 1
    )
    attempt = GenerationAttempt(
        job_id=job.id,
        number=number,
        quota_id=quota_id,
        lease_token=new_id(),
        lease_until=session.scalar(select(func.now())) + LEASE,
    )
    session.add(attempt)
    session.flush()
    job.current_attempt_id, job.status, job.error = attempt.id, "PREPARING", None
    return attempt.id


def recover_and_claim(session: Session) -> str | None:
    now = session.scalar(select(func.now()))
    candidates = session.scalars(
        select(GenerationAttempt)
        .join(GenerationJob, GenerationJob.current_attempt_id == GenerationAttempt.id)
        .where(
            or_(
                GenerationAttempt.lease_until.is_(None),
                GenerationAttempt.lease_until < now,
            ),
            GenerationJob.status.in_(
                [
                    "PREPARING",
                    "SUBMITTING",
                    "QUEUED",
                    "RUNNING",
                    "DOWNLOADING",
                    "FINALIZING",
                ]
            ),
            or_(
                GenerationAttempt.next_poll_at.is_(None),
                GenerationAttempt.next_poll_at <= now,
            ),
        )
        .order_by(GenerationAttempt.created_at)
        .with_for_update(of=GenerationJob, skip_locked=True)
        .limit(20)
    )
    for attempt in candidates:
        job = session.get(GenerationJob, attempt.job_id)
        if job.status == "SUBMITTING":
            job.status, job.error = (
                "SUBMISSION_UNKNOWN",
                "WORKER_INTERRUPTED_DURING_CREATE",
            )
            continue
        if job.status in LOCAL_PHASES:
            # Cross-process local-stage concurrency is independently bounded by a DB quota row.
            session.execute(
                insert(CloudQuota)
                .values(id="local-processing", limit_count=2)
                .on_conflict_do_nothing()
            )
            session.scalar(
                select(CloudQuota)
                .where(CloudQuota.id == "local-processing")
                .with_for_update()
            )
            active = session.scalar(
                select(func.count())
                .select_from(GenerationAttempt)
                .join(
                    GenerationJob,
                    GenerationJob.current_attempt_id == GenerationAttempt.id,
                )
                .where(
                    GenerationJob.status.in_(LOCAL_PHASES),
                    GenerationAttempt.lease_until > now,
                )
            )
            if active >= 2:
                continue
        attempt.lease_token, attempt.lease_until = new_id(), now + LEASE
        return attempt.id
    return None


def owned(
    session: Session, attempt_id: str, token: str
) -> tuple[GenerationAttempt, GenerationJob, GenerationBatch] | None:
    state = lock_state(session, attempt_id)
    if state is None or state[0].lease_token != token:
        return None
    if state[1].current_attempt_id != attempt_id:
        return None
    return state


def lock_state(
    session: Session, attempt_id: str
) -> tuple[GenerationAttempt, GenerationJob, GenerationBatch] | None:
    attempt = session.get(GenerationAttempt, attempt_id)
    if attempt is None:
        return None
    session.scalar(
        select(CloudQuota)
        .where(CloudQuota.id == attempt.quota_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    job = session.get(GenerationJob, attempt.job_id)
    # All state transitions take quota -> batch -> job -> attempt, matching dispatch.
    batch = session.scalar(
        select(GenerationBatch)
        .where(GenerationBatch.id == job.batch_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    job = session.scalar(
        select(GenerationJob)
        .where(GenerationJob.id == job.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    attempt = session.scalar(
        select(GenerationAttempt)
        .where(GenerationAttempt.id == attempt_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    return attempt, job, batch


def pause_failure(batch: GenerationBatch, reason: str) -> None:
    if batch.status == "ACTIVE":
        batch.status, batch.error = "PAUSED", reason


def request_termination(session: Session, batch: GenerationBatch) -> None:
    """Stop dispatch under the batch lock; wake tracking without revoking leases."""
    batch.status, batch.error = "TERMINATING", None
    session.execute(
        update(GenerationJob)
        .where(
            GenerationJob.batch_id == batch.id,
            GenerationJob.status.in_(["PLANNED", "DEFERRED"]),
        )
        .values(status="TERMINATED")
    )
    tracking = session.execute(
        select(GenerationJob, GenerationAttempt)
        .join(
            GenerationAttempt, GenerationAttempt.id == GenerationJob.current_attempt_id
        )
        .where(
            GenerationJob.batch_id == batch.id,
            GenerationJob.status.in_(["QUEUED", "RUNNING", "TRACKING_INTERRUPTED"]),
            GenerationAttempt.reserved.is_(True),
            GenerationAttempt.task_id.is_not(None),
        )
        .with_for_update(of=GenerationJob)
    )
    for job, attempt in tracking:
        attempt.next_poll_at = None
        attempt.poll_failures = 0
        if job.status == "TRACKING_INTERRUPTED":
            job.status, job.error = "QUEUED", None
    complete_batch(session, batch)


def complete_batch(session: Session, batch: GenerationBatch) -> None:
    if batch.status == "TERMINATED":
        return
    session.flush()
    if batch.status == "TERMINATING":
        reserved = (
            select(GenerationAttempt.id)
            .join(GenerationJob, GenerationJob.id == GenerationAttempt.job_id)
            .where(
                GenerationJob.batch_id == batch.id, GenerationAttempt.reserved.is_(True)
            )
            .exists()
        )
        local_processing = (
            select(GenerationJob.id)
            .where(
                GenerationJob.batch_id == batch.id,
                GenerationJob.status.in_(LOCAL_PHASES),
            )
            .exists()
        )
        if not session.scalar(select(or_(reserved, local_processing))):
            batch.status = "TERMINATED"
        return
    remaining = set(
        session.scalars(
            select(GenerationJob.status)
            .where(
                GenerationJob.batch_id == batch.id, GenerationJob.status != "SUCCEEDED"
            )
            .distinct()
        )
    )
    if remaining == {"DEFERRED"}:
        batch.status, batch.error = "FIRST_CLIP_READY", None
    elif not remaining:
        batch.status, batch.error = "SUCCEEDED", None
