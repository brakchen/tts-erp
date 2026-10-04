"""Short-transaction repository for publish tasks."""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from sqlalchemy import Select, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, joinedload

from tts_erp_v2.db.models.publishing import VideoPublishAttempt, VideoPublishTask
from tts_erp_v2.publishing.domain import (
    AttemptKind,
    AttemptStatus,
    TaskStage,
    TaskStatus,
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
        .options(joinedload(VideoPublishTask.attempts))
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


def claim_one(
    session: Session,
    instance_id: str,
    lease_seconds: int = 30,
    max_attempts: int = 3,
) -> VideoPublishTask | None:
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


def request_verification(session: Session, task_id: UUID) -> VideoPublishTask:
    task = get_task(session, task_id, lock=True)
    if task is None:
        raise LookupError("TASK_NOT_FOUND")
    if task.status != TaskStatus.NEEDS_REVIEW.value:
        raise ValueError("TASK_ACTION_NOT_ALLOWED")
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


def queue_task(task: VideoPublishTask, *, delay_seconds: int = 0) -> None:
    task.status = TaskStatus.PENDING.value
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
