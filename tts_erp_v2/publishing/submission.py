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
    plan_cleanup,
)
from tts_erp_v2.publishing.object_store import VideoObjectStore
from tts_erp_v2.publishing.repository import get_task, queue_task, release_lease
from tts_erp_v2.storage.minio_client import ObjectNotFound

MAX_VIDEO_BYTES = int(
    os.environ.get("TIKTOK_PUBLISH_MAX_VIDEO_BYTES", str(500 * 1024 * 1024))
)
MAX_CAPTION_CHARS = int(os.environ.get("TIKTOK_PUBLISH_MAX_CAPTION_CHARS", "4000"))
_BUCKET = os.environ.get("TIKTOK_PUBLISH_MINIO_BUCKET", "tiktok-video")
_PACKAGE = os.environ.get("ARTEMIS_APP_PACKAGE", "com.zhiliaoapp.musically")


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


def safe_filename(filename: str) -> str:
    name = PurePosixPath(filename).name
    stem = re.sub(r"[^A-Za-z0-9._-]+", "_", name).strip("._")
    return (stem or "video.mp4")[:180]


def create_upload_ticket(
    session: Session, command: CreateCommand, store: VideoObjectStore
) -> tuple[VideoPublishTask, str, bool]:
    caption = normalize_caption(command.caption)
    if command.content_type != "video/mp4" or not safe_filename(
        command.filename
    ).lower().endswith(".mp4"):
        raise ValueError("INVALID_VIDEO_TYPE")
    if not 0 < command.size_bytes <= MAX_VIDEO_BYTES:
        raise ValueError("VIDEO_TOO_LARGE")
    if not caption.strip():
        raise ValueError("CAPTION_REQUIRED")
    device_serial = configured_device_serial()
    if not device_serial:
        raise ValueError("DEVICE_NOT_CONFIGURED")
    if getattr(store, "bucket", configured_bucket()) != configured_bucket():
        raise ValueError("PUBLISH_BUCKET_MISMATCH")
    if len(caption) > MAX_CAPTION_CHARS:
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
            safe_filename(command.filename),
            command.content_type,
            command.size_bytes,
            caption,
        ):
            raise ValueError("IDEMPOTENCY_PAYLOAD_MISMATCH")
        if existing.stage != TaskStage.AWAITING_UPLOAD.value:
            return existing, "", True
        upload_url = cast(
            str, store.presign_put(existing.object_key, existing.content_type)
        )
        return existing, upload_url, True
    task_id = uuid4()
    filename = safe_filename(command.filename)
    key = f"video-publish/{datetime.now(UTC):%Y/%m}/{task_id}/{filename}"
    task = VideoPublishTask(
        public_id=task_id,
        client_request_id=command.client_request_id,
        created_by_user_id=command.actor_user_id,
        created_by_key_hash=command.actor_key_hash,
        caption=caption,
        original_filename=filename,
        content_type=command.content_type,
        size_bytes=command.size_bytes,
        object_bucket=getattr(store, "bucket", _BUCKET),
        object_key=key,
        status=TaskStatus.PENDING.value,
        stage=TaskStage.AWAITING_UPLOAD.value,
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
        ) != (filename, command.content_type, command.size_bytes, caption):
            raise ValueError("IDEMPOTENCY_PAYLOAD_MISMATCH") from None
        if winner.stage != TaskStage.AWAITING_UPLOAD.value:
            return winner, "", True
        return (
            winner,
            cast(str, store.presign_put(winner.object_key, winner.content_type)),
            True,
        )
    upload_url = cast(str, store.presign_put(key, command.content_type))
    return task, upload_url, False


def confirm_upload(
    session: Session, task_id: UUID, store: VideoObjectStore
) -> VideoPublishTask:
    task = get_task(session, task_id, lock=True)
    if task is None:
        raise LookupError("TASK_NOT_FOUND")
    if task.stage != TaskStage.AWAITING_UPLOAD.value:
        return task
    try:
        metadata = store.stat(task.object_key)
    except Exception as exc:
        raise ValueError("UPLOAD_NOT_FOUND") from exc
    if (
        metadata.get("size") != task.size_bytes
        or metadata.get("content_type", "video/mp4").split(";")[0].lower()
        != "video/mp4"
    ):
        raise ValueError("UPLOAD_SIZE_MISMATCH")
    task.object_etag = metadata.get("etag")
    task.object_uploaded_at = datetime.now(UTC)
    task.status = TaskStatus.PENDING.value
    task.stage = TaskStage.QUEUED.value
    task.queued_at = datetime.now(UTC)
    task.row_version += 1
    session.commit()
    return task


def replace_upload(session: Session, task_id: UUID) -> VideoPublishTask:
    task = get_task(session, task_id, lock=True)
    if task is None:
        raise LookupError("TASK_NOT_FOUND")
    if task.status != TaskStatus.FAILED.value or task.object_deleted_at is None:
        raise ValueError("UPLOAD_REPLACEMENT_REQUIRED")
    task.status = TaskStatus.PENDING.value
    task.stage = TaskStage.AWAITING_UPLOAD.value
    task.object_uploaded_at = None
    task.object_deleted_at = None
    task.object_etag = None
    task.object_sha256 = None
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
    task.status = TaskStatus.CANCELLED.value
    task.stage = TaskStage.DONE.value
    plan_cleanup(task, CleanupIntent.PRESERVE_STATE, object=True)
    task.completed_at = datetime.now(UTC)
    task.row_version += 1
    release_lease(task)
    task.object_cleanup_next_attempt_at = datetime.now(UTC)
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
            task.object_deleted_at = datetime.now(UTC)
            task.object_cleanup_status = "succeeded"
            task.row_version += 1
            session.commit()
            raise ValueError("UPLOAD_REPLACEMENT_REQUIRED") from exc
        except Exception as exc:
            raise ValueError("OBJECT_STORE_UNAVAILABLE") from exc
    if task.attempt_count >= int(os.environ.get("TIKTOK_PUBLISH_MAX_ATTEMPTS", "3")):
        raise ValueError("RETRY_BUDGET_EXHAUSTED")
    queue_task(task)
    task.row_version += 1
    session.commit()
    return task
