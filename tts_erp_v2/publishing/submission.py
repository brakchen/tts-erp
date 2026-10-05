"""Task creation and state-changing commands used by API handlers."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import PurePosixPath
from typing import cast
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from tts_erp_v2.db.models.publishing import VideoPublishTask
from tts_erp_v2.publishing.domain import (
    CleanupIntent,
    TaskStage,
    TaskStatus,
    object_cleanup_blocks_input,
    plan_cleanup,
    replace_upload_allowed,
    replacement_cleanup_pending,
    set_task_stage,
)
from tts_erp_v2.publishing.object_store import VideoObjectStore
from tts_erp_v2.publishing.repository import (
    database_now,
    get_task,
    queue_task,
    release_lease,
)
from tts_erp_v2.storage.minio_client import ObjectNotFound

_BUCKET = os.environ.get("TIKTOK_PUBLISH_MINIO_BUCKET", "tiktok-video")
_PACKAGE = os.environ.get("ARTEMIS_APP_PACKAGE", "com.zhiliaoapp.musically")


def max_video_bytes() -> int:
    return int(os.environ.get("TIKTOK_PUBLISH_MAX_VIDEO_BYTES", str(500 * 1024 * 1024)))


def max_caption_chars() -> int:
    return int(os.environ.get("TIKTOK_PUBLISH_MAX_CAPTION_CHARS", "4000"))


def configured_device_serial() -> str:
    return os.environ.get("ARTEMIS_DEVICE_SERIAL", "").strip()


def configured_bucket() -> str:
    return os.environ.get("TIKTOK_PUBLISH_MINIO_BUCKET", "tiktok-video").strip()


def upload_expires_at(store: VideoObjectStore) -> datetime:
    expiry = getattr(store, "default_expiry", None)
    if not isinstance(expiry, timedelta):
        expiry = timedelta(
            seconds=int(os.environ.get("TIKTOK_PUBLISH_UPLOAD_TTL_SECONDS", "900"))
        )
    return datetime.now(UTC) + expiry


class TaskConflict(ValueError):
    def __init__(self, code: str, task: VideoPublishTask) -> None:
        super().__init__(code)
        self.code = code
        self.task = task


@dataclass(frozen=True, slots=True)
class CreateCommand:
    client_request_id: UUID
    filename: str
    content_type: str
    size_bytes: int
    caption: str
    actor_user_id: int | None = None
    actor_key_hash: str | None = None


def normalize_caption(value: str) -> str:
    value = value.replace("\r\n", "\n").replace("\r", "\n")
    if "\x00" in value:
        raise ValueError("caption contains NUL")
    return value


def original_basename(filename: str) -> str:
    """Return the bounded browser basename without object-key sanitization."""
    name = PurePosixPath(filename.replace("\\", "/")).name
    if not name or name in {".", ".."} or "\x00" in name:
        raise ValueError("INVALID_VIDEO_FILENAME")
    return name[:255]


def safe_filename(filename: str) -> str:
    name = original_basename(filename)
    stem = re.sub(r"[^A-Za-z0-9._-]+", "_", name).strip("._")
    return (stem or "video.mp4")[:180]


def _presign_put(store: VideoObjectStore, key: str, content_type: str) -> str:
    try:
        return cast(str, store.presign_put(key, content_type))
    except Exception as exc:
        raise ValueError("OBJECT_STORE_UNAVAILABLE") from exc


def create_upload_ticket(
    session: Session, command: CreateCommand, store: VideoObjectStore
) -> tuple[VideoPublishTask, str, bool]:
    caption = normalize_caption(command.caption)
    browser_filename = original_basename(command.filename)
    object_filename = safe_filename(browser_filename)
    if command.content_type != "video/mp4" or not browser_filename.lower().endswith(
        ".mp4"
    ):
        raise ValueError("INVALID_VIDEO_TYPE")
    if not 0 < command.size_bytes <= max_video_bytes():
        raise ValueError("VIDEO_TOO_LARGE")
    if not caption.strip():
        raise ValueError("CAPTION_REQUIRED")
    device_serial = configured_device_serial()
    if not device_serial:
        raise ValueError("DEVICE_NOT_CONFIGURED")
    if getattr(store, "bucket", configured_bucket()) != configured_bucket():
        raise ValueError("PUBLISH_BUCKET_MISMATCH")
    if len(caption) > max_caption_chars():
        raise ValueError("CAPTION_TOO_LONG")
    existing = session.scalar(
        select(VideoPublishTask).where(
            VideoPublishTask.client_request_id == command.client_request_id
        )
    )
    if existing:
        owner_matches = (
            existing.created_by_user_id == command.actor_user_id
            and existing.created_by_key_hash == command.actor_key_hash
            if command.actor_user_id is not None or command.actor_key_hash is not None
            else existing.created_by_user_id is None
            and existing.created_by_key_hash is None
        )
        if not owner_matches:
            raise PermissionError("TASK_NOT_FOUND")
        if (
            existing.original_filename,
            existing.content_type,
            existing.size_bytes,
            existing.caption,
        ) != (
            browser_filename,
            command.content_type,
            command.size_bytes,
            caption,
        ):
            raise ValueError("IDEMPOTENCY_PAYLOAD_MISMATCH")
        if existing.stage != TaskStage.AWAITING_UPLOAD.value:
            return existing, "", True
        upload_url = _presign_put(store, existing.object_key, existing.content_type)
        return existing, upload_url, True
    task_id = uuid4()
    key = f"video-publish/{datetime.now(UTC):%Y/%m}/{task_id}/{object_filename}"
    now = database_now(session)
    task = VideoPublishTask(
        public_id=task_id,
        client_request_id=command.client_request_id,
        created_by_user_id=command.actor_user_id,
        created_by_key_hash=command.actor_key_hash,
        caption=caption,
        original_filename=browser_filename,
        object_filename=object_filename,
        content_type=command.content_type,
        size_bytes=command.size_bytes,
        object_bucket=getattr(store, "bucket", _BUCKET),
        object_key=key,
        status=TaskStatus.PENDING.value,
        stage=TaskStage.AWAITING_UPLOAD.value,
        stage_started_at=now,
        target_device_serial=device_serial,
        target_app_package=_PACKAGE,
    )
    session.add(task)
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        winner = session.scalar(
            select(VideoPublishTask).where(
                VideoPublishTask.client_request_id == command.client_request_id
            )
        )
        if winner is None:
            raise
        owner_matches = (
            winner.created_by_user_id == command.actor_user_id
            and winner.created_by_key_hash == command.actor_key_hash
            if command.actor_user_id is not None or command.actor_key_hash is not None
            else winner.created_by_user_id is None
            and winner.created_by_key_hash is None
        )
        if not owner_matches:
            raise PermissionError("TASK_NOT_FOUND") from None
        if (
            winner.original_filename,
            winner.content_type,
            winner.size_bytes,
            winner.caption,
        ) != (browser_filename, command.content_type, command.size_bytes, caption):
            raise ValueError("IDEMPOTENCY_PAYLOAD_MISMATCH") from None
        if winner.stage != TaskStage.AWAITING_UPLOAD.value:
            return winner, "", True
        return (
            winner,
            _presign_put(store, winner.object_key, winner.content_type),
            True,
        )
    upload_url = _presign_put(store, key, command.content_type)
    return task, upload_url, False


def confirm_upload(
    session: Session,
    task_id: UUID,
    store: VideoObjectStore,
    *,
    expected_version: int | None = None,
) -> VideoPublishTask:
    """HEAD without a row lock, then CAS the still-awaiting task under lock."""
    observed = get_task(session, task_id)
    if observed is None:
        raise LookupError("TASK_NOT_FOUND")
    if observed.stage != TaskStage.AWAITING_UPLOAD.value:
        raise ValueError("TASK_ACTION_NOT_ALLOWED")
    if expected_version is None or observed.row_version != expected_version:
        raise ValueError("TASK_VERSION_CONFLICT")
    object_key = observed.object_key
    size_bytes = observed.size_bytes
    try:
        metadata = store.stat(object_key)
    except ObjectNotFound as exc:
        raise ValueError("UPLOAD_NOT_FOUND") from exc
    except Exception as exc:
        raise ValueError("OBJECT_STORE_UNAVAILABLE") from exc
    if metadata.get("size") != size_bytes:
        raise ValueError("UPLOAD_SIZE_MISMATCH")
    content_type = metadata.get("content_type")
    if not isinstance(content_type, str) or not content_type.strip():
        raise ValueError("UPLOAD_MIME_MISSING")
    if content_type.split(";", 1)[0].strip().lower() != "video/mp4":
        raise ValueError("UPLOAD_MIME_MISMATCH")

    session.expire_all()
    task = get_task(session, task_id, lock=True)
    if task is None:
        raise LookupError("TASK_NOT_FOUND")
    if task.stage != TaskStage.AWAITING_UPLOAD.value:
        raise TaskConflict("TASK_ACTION_NOT_ALLOWED", task)
    if task.row_version != expected_version or task.object_key != object_key:
        raise TaskConflict("TASK_VERSION_CONFLICT", task)
    now = database_now(session)
    task.object_etag = metadata.get("etag")
    task.object_uploaded_at = now
    task.status = TaskStatus.PENDING.value
    set_task_stage(task, TaskStage.QUEUED, now=now)
    task.queued_at = now
    task.row_version += 1
    session.commit()
    return task


def replace_upload(session: Session, task_id: UUID) -> VideoPublishTask:
    task = get_task(session, task_id, lock=True)
    if task is None:
        raise LookupError("TASK_NOT_FOUND")
    if task.status != TaskStatus.FAILED.value or task.object_deleted_at is None:
        raise ValueError("UPLOAD_REPLACEMENT_REQUIRED")
    if replacement_cleanup_pending(task):
        raise ValueError("CLEANUP_REQUIRED")
    if object_cleanup_blocks_input(task):
        raise ValueError("OBJECT_CLEANUP_IN_PROGRESS")
    if not replace_upload_allowed(task):
        raise ValueError("RETRY_BUDGET_EXHAUSTED")
    now = database_now(session)
    task.status = TaskStatus.PENDING.value
    set_task_stage(task, TaskStage.AWAITING_UPLOAD, now=now)
    task.object_uploaded_at = None
    task.object_deleted_at = None
    task.object_etag = None
    task.object_sha256 = None
    for name in ("device", "spool", "object"):
        setattr(task, f"{name}_cleanup_status", "not_started")
        setattr(task, f"{name}_cleanup_error", None)
        setattr(task, f"{name}_cleanup_attempts", 0)
        setattr(task, f"{name}_cleanup_next_attempt_at", None)
    task.cleanup_intent = CleanupIntent.NONE.value
    task.cleanup_lease_owner = None
    task.cleanup_lease_expires_at = None
    task.cleanup_heartbeat_at = None
    release_lease(task)
    task.last_error_code = None
    task.last_error_message = None
    task.row_version += 1
    session.commit()
    return task


def cancel_task(
    session: Session, task_id: UUID, store: VideoObjectStore
) -> VideoPublishTask:
    task = get_task(session, task_id, lock=True)
    if task is None:
        raise LookupError("TASK_NOT_FOUND")
    if task.stage not in {
        TaskStage.AWAITING_UPLOAD.value,
        TaskStage.QUEUED.value,
        TaskStage.WAITING_DEVICE.value,
    }:
        raise ValueError("TASK_ACTION_NOT_ALLOWED")
    now = database_now(session)
    task.status = TaskStatus.CANCELLED.value
    set_task_stage(task, TaskStage.DONE, now=now)
    plan_cleanup(task, CleanupIntent.PRESERVE_STATE, object=True)
    task.completed_at = now
    task.row_version += 1
    release_lease(task)
    task.object_cleanup_next_attempt_at = now
    session.commit()
    return task


def retry_task(
    session: Session, task_id: UUID, store: VideoObjectStore | None = None
) -> VideoPublishTask:
    task = get_task(session, task_id, lock=True)
    if task is None:
        raise LookupError("TASK_NOT_FOUND")
    if task.status != TaskStatus.FAILED.value:
        raise ValueError("TASK_ACTION_NOT_ALLOWED")
    if object_cleanup_blocks_input(task):
        raise ValueError("OBJECT_CLEANUP_IN_PROGRESS")
    attempts = task.attempts or []
    latest = max(attempts, key=lambda attempt: attempt.sequence_no, default=None)
    if latest is None or latest.kind != "publish" or latest.retry_safe is not True:
        raise ValueError("TASK_RETRY_NOT_SAFE")
    if task.object_uploaded_at is None or task.object_deleted_at is not None:
        raise ValueError("UPLOAD_REPLACEMENT_REQUIRED")
    if store is not None:
        try:
            store.stat(task.object_key)
        except ObjectNotFound as exc:
            task.object_deleted_at = database_now(session)
            task.object_cleanup_status = "succeeded"
            task.row_version += 1
            session.commit()
            raise ValueError("UPLOAD_REPLACEMENT_REQUIRED") from exc
        except Exception as exc:
            raise ValueError("OBJECT_STORE_UNAVAILABLE") from exc
    if task.publish_budget_used >= int(
        os.environ.get("TIKTOK_PUBLISH_MAX_ATTEMPTS", "3")
    ):
        raise ValueError("RETRY_BUDGET_EXHAUSTED")
    queue_task(session, task)
    task.row_version += 1
    session.commit()
    return task
