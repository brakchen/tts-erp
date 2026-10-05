"""Short-transaction repository for publish tasks."""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from sqlalchemy import Select, and_, func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from tts_erp_v2.db.models.publishing import VideoPublishAttempt, VideoPublishTask
from tts_erp_v2.publishing.domain import (
    AttemptKind,
    AttemptStatus,
    CleanupIntent,
    TaskStage,
    TaskStatus,
    cleanup_retryable_resources,
)
from tts_erp_v2.publishing.prompt import (
    PUBLISH_PROMPT_VERSION,
    VERIFY_PROMPT_VERSION,
    build_publish_prompt,
    build_verify_prompt,
)


def get_task(
    session: Session, public_id: UUID, *, lock: bool = False
) -> VideoPublishTask | None:
    query: Select[VideoPublishTask] = (
        select(VideoPublishTask)
        .where(VideoPublishTask.public_id == public_id)
        .options(selectinload(VideoPublishTask.attempts))
    )
    if lock:
        query = query.with_for_update()
    return session.scalars(query).unique().first()


def create_attempt(
    session: Session,
    task: VideoPublishTask,
    *,
    kind: AttemptKind,
    related: VideoPublishAttempt | None = None,
    album: str | None = None,
) -> VideoPublishAttempt:
    locked_task = session.get(VideoPublishTask, task.id, with_for_update=True)
    if locked_task is None:
        raise LookupError(task.id)
    task = locked_task
    if kind == AttemptKind.PUBLISH and task.attempt_count >= int(
        os.environ.get("TIKTOK_PUBLISH_MAX_ATTEMPTS", "3")
    ):
        raise ValueError("RETRY_BUDGET_EXHAUSTED")
    latest = (
        session.scalar(
            select(func.max(VideoPublishAttempt.sequence_no)).where(
                VideoPublishAttempt.task_id == task.id
            )
        )
        or 0
    )
    session_id = uuid4()
    if kind == AttemptKind.PUBLISH:
        prompt_version = PUBLISH_PROMPT_VERSION
        album = album or os.environ.get("TIKTOK_PUBLISH_ALBUM", "TTSERP")
        prompt = build_publish_prompt(
            caption=task.caption,
            app_package=task.target_app_package,
            device_path=task.device_path
            or f"/sdcard/Movies/TTSERP/tts_erp_{task.public_id}.mp4",
            album=album,
        )
    else:
        prompt_version = VERIFY_PROMPT_VERSION
        prompt = build_verify_prompt(
            caption=task.caption, app_package=task.target_app_package
        )
    attempt = VideoPublishAttempt(
        task_id=task.id,
        sequence_no=latest + 1,
        kind=kind.value,
        related_attempt_id=related.id if related else None,
        artemis_session_id=session_id,
        # Created attempts are covered by the active-attempt partial index;
        # admission is advanced to submitting in the worker transaction.
        status=AttemptStatus.CREATED.value,
        prompt_version=prompt_version,
        prompt_snapshot=prompt,
        device_serial=task.target_device_serial,
        device_path=task.device_path,
    )
    session.add(attempt)
    if kind == AttemptKind.PUBLISH:
        task.attempt_count += 1
    return attempt


def _lease_task(
    session: Session, instance_id: str, lease_seconds: int
) -> VideoPublishTask | None:
    now = datetime.now(UTC)
    task = session.scalars(
        select(VideoPublishTask)
        .where(
            VideoPublishTask.status == TaskStatus.RUNNING.value,
            (VideoPublishTask.lease_owner == instance_id)
            | (VideoPublishTask.lease_expires_at.is_(None))
            | (VideoPublishTask.lease_expires_at < func.now()),
        )
        .order_by(VideoPublishTask.id)
        .with_for_update(skip_locked=True)
        .limit(1)
    ).first()
    if task is None:
        return None
    task.lease_owner = instance_id
    task.lease_expires_at = now + timedelta(seconds=lease_seconds)
    task.heartbeat_at = now
    task.row_version += 1
    session.flush()
    return task


def _lock_publish_slot(session: Session) -> None:
    session.execute(select(func.pg_advisory_xact_lock(738041)))


def has_pending_device_cleanup(session: Session) -> bool:
    return (
        session.scalar(
            select(VideoPublishTask.id).where(
                VideoPublishTask.cleanup_intent != CleanupIntent.NONE.value,
                VideoPublishTask.device_cleanup_status.in_(["pending", "failed"]),
            )
        )
        is not None
    )


def _lease_cleanup_task(
    session: Session,
    instance_id: str,
    lease_seconds: int,
    *,
    device_only: bool | None = None,
) -> VideoPublishTask | None:
    """Lease explicit cleanup work without occupying the publish slot."""
    now = datetime.now(UTC)

    def due(column):
        return (column.is_(None)) | (column <= now)

    device_due = and_(
        VideoPublishTask.device_cleanup_status.in_(["pending", "failed"]),
        due(VideoPublishTask.device_cleanup_next_attempt_at),
    )
    background_due = or_(
        and_(
            VideoPublishTask.spool_cleanup_status.in_(["pending", "failed"]),
            due(VideoPublishTask.spool_cleanup_next_attempt_at),
        ),
        and_(
            VideoPublishTask.object_cleanup_status.in_(["pending", "failed"]),
            due(VideoPublishTask.object_cleanup_next_attempt_at),
        ),
    )
    resource_due = (
        device_due
        if device_only is True
        else background_due
        if device_only is False
        else or_(device_due, background_due)
    )
    task = session.scalars(
        select(VideoPublishTask)
        .where(
            VideoPublishTask.cleanup_intent != CleanupIntent.NONE.value,
            resource_due,
            (VideoPublishTask.cleanup_lease_owner.is_(None))
            | (VideoPublishTask.cleanup_lease_expires_at.is_(None))
            | (VideoPublishTask.cleanup_lease_expires_at < now),
        )
        .order_by(VideoPublishTask.cleanup_lease_expires_at, VideoPublishTask.id)
        .with_for_update(skip_locked=True)
        .limit(1)
    ).first()
    if task is None:
        return None
    task.cleanup_lease_owner = instance_id
    task.cleanup_lease_expires_at = now + timedelta(seconds=lease_seconds)
    task.cleanup_heartbeat_at = now
    task.row_version += 1
    session.flush()
    return task


def claim_one(
    session: Session,
    instance_id: str,
    lease_seconds: int = 30,
    max_attempts: int = 3,
) -> VideoPublishTask | None:
    _lock_publish_slot(session)
    if has_pending_device_cleanup(session):
        return None
    if (
        session.scalar(
            select(VideoPublishTask.id)
            .where(VideoPublishTask.status == TaskStatus.RUNNING.value)
            .limit(1)
        )
        is not None
    ):
        return None
    query = (
        select(VideoPublishTask)
        .where(
            VideoPublishTask.status == TaskStatus.PENDING.value,
            VideoPublishTask.stage.in_(
                [TaskStage.QUEUED.value, TaskStage.WAITING_DEVICE.value]
            ),
            (VideoPublishTask.next_attempt_at.is_(None))
            | (VideoPublishTask.next_attempt_at <= func.now()),
            VideoPublishTask.attempt_count < max_attempts,
        )
        .order_by(VideoPublishTask.queued_at, VideoPublishTask.id)
        .with_for_update(skip_locked=True)
        .limit(1)
    )
    task = session.scalars(query).first()
    if task is None:
        return None
    now = datetime.now(UTC)
    task.status = TaskStatus.RUNNING.value
    task.stage = TaskStage.DOWNLOADING.value
    task.lease_owner = instance_id
    task.lease_expires_at = now + timedelta(seconds=lease_seconds)
    task.heartbeat_at = now
    task.started_at = task.started_at or now
    task.row_version += 1
    try:
        session.flush()
    except IntegrityError:
        # Another worker won the global running-task race.
        session.rollback()
        return None
    return task


def touch_task(
    session: Session,
    task: VideoPublishTask,
    *,
    stage: str | None = None,
    lease_seconds: int = 30,
) -> None:
    task.heartbeat_at = datetime.now(UTC)
    task.lease_expires_at = datetime.now(UTC) + timedelta(seconds=lease_seconds)
    if stage:
        task.stage = stage
    task.row_version += 1


def release_lease(task: VideoPublishTask) -> None:
    task.lease_owner = None
    task.lease_expires_at = None
    task.heartbeat_at = None


def renew_cleanup_lease(
    session: Session, task_id: UUID, instance_id: str, lease_seconds: int
) -> None:
    now = datetime.now(UTC)
    result = session.execute(
        update(VideoPublishTask)
        .where(
            VideoPublishTask.public_id == task_id,
            VideoPublishTask.cleanup_lease_owner == instance_id,
            VideoPublishTask.cleanup_lease_expires_at > now,
        )
        .values(
            cleanup_heartbeat_at=now,
            cleanup_lease_expires_at=now + timedelta(seconds=lease_seconds),
            row_version=VideoPublishTask.row_version + 1,
        )
    )
    if getattr(result, "rowcount", None) != 1:
        raise ValueError("CLEANUP_LEASE_LOST")


def request_verification(session: Session, task_id: UUID) -> VideoPublishTask:
    task = get_task(session, task_id, lock=True)
    if task is None:
        raise LookupError("TASK_NOT_FOUND")
    if task.status != TaskStatus.NEEDS_REVIEW.value:
        raise ValueError("TASK_ACTION_NOT_ALLOWED")
    if task.device_cleanup_status in {"pending", "failed"}:
        raise ValueError("DEVICE_CLEANUP_BLOCKED")
    related = next(
        (a for a in reversed(task.attempts) if a.kind == AttemptKind.PUBLISH.value),
        None,
    )
    if related is None:
        raise ValueError("VERIFY_NOT_AVAILABLE")
    task.status = TaskStatus.RUNNING.value
    task.stage = TaskStage.VERIFYING.value
    task.lease_owner = "api-verification"
    task.lease_expires_at = datetime.now(UTC) + timedelta(seconds=30)
    create_attempt(session, task, kind=AttemptKind.VERIFY, related=related)
    task.row_version += 1
    return task


def retry_cleanup_resources(
    session: Session, task: VideoPublishTask, *, now: datetime | None = None
) -> tuple[str, ...]:
    """Reset only failed resources while preserving business status/stage."""
    now = now or datetime.now(UTC)
    failed = cleanup_retryable_resources(task)
    if not failed:
        raise ValueError("CLEANUP_RETRY_NOT_AVAILABLE")
    if (
        task.cleanup_lease_owner
        and task.cleanup_lease_expires_at
        and task.cleanup_lease_expires_at > now
    ):
        raise ValueError("CLEANUP_LEASE_BUSY")
    if task.cleanup_intent == CleanupIntent.NONE.value:
        task.cleanup_intent = CleanupIntent.PRESERVE_STATE.value
    for name in failed:
        setattr(task, f"{name}_cleanup_status", "pending")
        setattr(task, f"{name}_cleanup_next_attempt_at", now)
    task.cleanup_lease_owner = None
    task.cleanup_lease_expires_at = None
    task.cleanup_heartbeat_at = None
    task.row_version += 1
    return failed


def queue_task(task: VideoPublishTask, *, delay_seconds: int = 0) -> None:
    task.status = TaskStatus.PENDING.value
    task.cleanup_intent = CleanupIntent.NONE.value
    task.cleanup_lease_owner = None
    task.cleanup_lease_expires_at = None
    task.cleanup_heartbeat_at = None
    task.stage = TaskStage.QUEUED.value
    task.queued_at = datetime.now(UTC)
    task.next_attempt_at = (
        datetime.now(UTC) + timedelta(seconds=delay_seconds) if delay_seconds else None
    )
    task.completed_at = None
    task.last_error_code = None
    task.last_error_message = None
    release_lease(task)
    task.row_version += 1
