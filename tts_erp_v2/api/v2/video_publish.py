"""HTTP boundary for ttsERP video publishing; adapters stay server-side."""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import os
from datetime import UTC, datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import and_, false, func, or_, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, aliased, selectinload

from tts_erp_v2.access import Role
from tts_erp_v2.api.deps import (
    caller_key_hash,
    get_session,
    require_destructive_guard,
    require_role_at_least,
)
from tts_erp_v2.api.v2._common import request_id
from tts_erp_v2.db.models.publishing import (
    PublishWorkerHeartbeat,
    VideoPublishAttempt,
    VideoPublishTask,
)
from tts_erp_v2.publishing.diagnostics import sanitize_artemis_output, sanitize_text
from tts_erp_v2.publishing.domain import allowed_actions, cleanup_retryable_resources
from tts_erp_v2.publishing.object_store import MinioVideoStore, VideoObjectStore
from tts_erp_v2.publishing.repository import (
    _lock_publish_slot,
    request_verification,
    retry_cleanup_resources,
)
from tts_erp_v2.publishing.submission import (
    CreateCommand,
    TaskConflict,
    cancel_task,
    confirm_upload,
    create_upload_ticket,
    max_caption_chars,
    max_video_bytes,
    replace_upload,
    retry_task,
    upload_expires_at,
)
from tts_erp_v2.storage.minio_client import MinioClient

router = APIRouter(prefix="/v2/video-publish", tags=["video-publish"])
_store: VideoObjectStore | None = None


def get_store() -> VideoObjectStore:
    global _store
    if _store is None:
        _store = MinioVideoStore(MinioClient.from_env())
    assert _store is not None
    return _store


def _csrf(request: Request) -> None:
    if (
        request.scope.get("auth_method") == "cookie"
        and request.headers.get("X-Requested-With") != "tts-erp"
    ):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            _error_detail(
                request,
                "CSRF_HEADER_REQUIRED",
                "cookie-authed POST must set header X-Requested-With: tts-erp",
                retryable=False,
            ),
        )


class CreateIn(BaseModel):
    client_request_id: UUID = Field(alias="clientRequestId")
    filename: str = Field(min_length=1, max_length=255)
    content_type: str = Field(alias="contentType")
    size_bytes: int = Field(alias="sizeBytes", gt=0)
    caption: str

    model_config = {"populate_by_name": True}


class ActionIn(BaseModel):
    row_version: int | None = Field(default=None, alias="rowVersion")
    resources: list[str] | None = None
    model_config = {"populate_by_name": True}


def _attempt(a: VideoPublishAttempt, *, diagnostics: bool = False) -> dict:
    output = sanitize_artemis_output(a.artemis_output) if diagnostics else None
    result = {
        "sequenceNo": a.sequence_no,
        "kind": a.kind,
        "kindLabel": "正式发布" if a.kind == "publish" else "自动核验",
        "artemisSessionId": str(a.artemis_session_id),
        "status": a.status,
        "stepsCount": a.steps_count,
        "retryClassification": a.retry_classification,
        "retrySafe": a.retry_safe,
        "error": sanitize_text(a.artemis_error) if a.artemis_error else None,
        "startedAt": a.started_at,
        "finishedAt": a.finished_at,
    }
    if diagnostics:
        result.update(promptSnapshot=a.prompt_snapshot, artemisOutput=output)
    return result


def _snapshot(
    task: VideoPublishTask,
    *,
    detail: bool = False,
    diagnostics: bool = False,
    expose_client_request_id: bool = False,
    summary_attempts: list[VideoPublishAttempt] | None = None,
    publish_attempt_count: int | None = None,
    verify_attempt_count: int | None = None,
    related_publish_attempt: VideoPublishAttempt | None = None,
    queue_position: int | None = None,
) -> dict:
    attempts = sorted(
        summary_attempts if summary_attempts is not None else task.attempts,
        key=lambda a: a.sequence_no,
        reverse=True,
    )
    latest = attempts[0] if attempts else None
    if related_publish_attempt is None and latest is not None:
        if latest.kind == "publish":
            related_publish_attempt = latest
        elif latest.related_attempt_id is not None:
            related_publish_attempt = next(
                (
                    attempt
                    for attempt in attempts
                    if attempt.id == latest.related_attempt_id
                    and attempt.kind == "publish"
                ),
                None,
            )
    actions = allowed_actions(
        task,
        latest_attempt=latest,
        has_attempts=(
            bool(attempts) or bool(publish_attempt_count) or bool(verify_attempt_count)
        ),
    )
    operational_stage = (
        "cleaning"
        if task.device_cleanup_status in {"pending", "failed"}
        and task.cleanup_intent != "none"
        else task.stage
    )
    data = {
        "taskId": str(task.public_id),
        "rowVersion": task.row_version,
        "filename": task.original_filename,
        "objectFilename": task.object_filename,
        "sizeBytes": task.size_bytes,
        "captionPreview": task.caption.splitlines()[0][:160] if task.caption else "",
        "status": task.status,
        "statusLabel": {
            "pending": "排队",
            "running": "执行中",
            "succeeded": "成功",
            "failed": "失败",
            "needs_review": "需核验",
            "cancelled": "已取消",
        }.get(task.status, task.status),
        "stage": task.stage,
        "stageLabel": {
            "awaiting_upload": "等待上传",
            "queued": "队列中",
            "waiting_device": "等待设备",
            "downloading": "下载视频",
            "staging_device": "写入相册",
            "dispatching_artemis": "提交 Artemis",
            "waiting_artemis": "等待 Artemis",
            "verifying": "自动核验",
            "done": "已完成",
        }.get(task.stage, task.stage),
        "queuedAt": task.queued_at,
        "queuePosition": queue_position,
        "operationalStage": operational_stage,
        "stageStartedAt": task.stage_started_at,
        "operationalStageStartedAt": (
            task.cleanup_heartbeat_at
            or task.device_cleanup_next_attempt_at
            or task.updated_at
            if operational_stage == "cleaning"
            else task.stage_started_at
        ),
        "latestArtemisSessionId": str(latest.artemis_session_id) if latest else None,
        "attemptCount": task.attempt_count,
        "retryBudgetUsed": task.publish_budget_used,
        "publishAttemptCount": (
            publish_attempt_count
            if publish_attempt_count is not None
            else sum(a.kind == "publish" for a in attempts)
        ),
        "verifyAttemptCount": (
            verify_attempt_count
            if verify_attempt_count is not None
            else sum(a.kind == "verify" for a in attempts)
        ),
        "lastErrorCode": (
            sanitize_text(task.last_error_code)[:100] if task.last_error_code else None
        ),
        "lastErrorMessage": (
            sanitize_text(task.last_error_message) if task.last_error_message else None
        ),
        "createdAt": task.created_at,
        "updatedAt": task.updated_at,
        "createdBy": (
            f"user:{task.created_by_user_id}"
            if task.created_by_user_id is not None
            else "api-key"
            if task.created_by_key_hash
            else "system"
        ),
        "startedAt": task.started_at,
        "allowedActions": [a.value for a in actions],
        "cleanupRetryableResources": list(cleanup_retryable_resources(task)),
    }
    if expose_client_request_id:
        data["clientRequestId"] = str(task.client_request_id)
    if "continue_upload" in data["allowedActions"]:
        data["caption"] = task.caption
    if latest:
        data["currentAttempt"] = _attempt(latest)
    if related_publish_attempt is not None:
        data["relatedPublishAttempt"] = _attempt(related_publish_attempt)
    if detail:
        data.update(
            {
                "contentType": task.content_type,
                "caption": task.caption,
                "rowVersion": task.row_version,
                "target": {
                    "appName": "TikTok",
                    "appPackage": task.target_app_package,
                    "deviceSerialMasked": _mask(task.target_device_serial),
                    "album": os.environ.get("TIKTOK_PUBLISH_ALBUM", "TTSERP"),
                },
                "object": {
                    "bucket": task.object_bucket,
                    "key": task.object_key,
                    "etag": task.object_etag,
                    "deletedAt": task.object_deleted_at,
                },
                "attempts": [_attempt(a, diagnostics=diagnostics) for a in attempts],
                "cleanup": {
                    "device": {
                        "status": task.device_cleanup_status,
                        "error": sanitize_text(task.device_cleanup_error)
                        if task.device_cleanup_error
                        else None,
                    },
                    "spool": {
                        "status": task.spool_cleanup_status,
                        "error": sanitize_text(task.spool_cleanup_error)
                        if task.spool_cleanup_error
                        else None,
                    },
                    "object": {
                        "status": task.object_cleanup_status,
                        "error": sanitize_text(task.object_cleanup_error)
                        if task.object_cleanup_error
                        else None,
                    },
                },
            }
        )
    return data


def _mask(value: str) -> str:
    return value if len(value) <= 8 else f"{value[:4]}…{value[-4:]}"


def _is_privileged(request: Request) -> bool:
    grant = request.scope.get("access_grant")
    role = getattr(getattr(grant, "role", None), "value", None)
    return bool(
        getattr(grant, "bypass", False)
        or role == "admin"
        or request.scope.get("api_key_role") == "admin"
    )


def _owner_clause(request: Request):
    if _is_privileged(request):
        return None
    user_id = request.scope.get("user_id")
    if user_id is not None:
        return VideoPublishTask.created_by_user_id == user_id
    key_hash = caller_key_hash(request)
    if key_hash:
        return VideoPublishTask.created_by_key_hash == key_hash
    return false()


def _owns_task(task: VideoPublishTask, request: Request) -> bool:
    if _is_privileged(request):
        return True
    user_id = request.scope.get("user_id")
    if user_id is not None:
        return task.created_by_user_id == user_id
    key_hash = caller_key_hash(request)
    return bool(key_hash and task.created_by_key_hash == key_hash)


def _task_for_actor(
    session: Session, task_id: UUID, request: Request, *, lock: bool = False
) -> VideoPublishTask:
    try:
        task = _task(session, task_id, lock=True) if lock else _task(session, task_id)
    except HTTPException as exc:
        if exc.status_code != status.HTTP_404_NOT_FOUND:
            raise
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            _error_detail(request, "TASK_NOT_FOUND", "TASK_NOT_FOUND", retryable=False),
        ) from exc
    if not _owns_task(task, request):
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            _error_detail(request, "TASK_NOT_FOUND", "TASK_NOT_FOUND", retryable=False),
        )
    return task


def _error_detail(
    request: Request,
    code: str,
    message: str,
    *,
    retryable: bool,
    task: VideoPublishTask | None = None,
) -> dict:
    detail = {
        "code": code,
        "message": sanitize_text(message),
        "retryable": retryable,
        "requestId": request_id(request),
    }
    if task is not None:
        detail.update(
            rowVersion=task.row_version,
            allowedActions=[a.value for a in allowed_actions(task)],
        )
    return detail


def _action_conflict(
    request: Request,
    task: VideoPublishTask,
    code: str,
    message: str,
    *,
    retryable: bool = False,
) -> dict:
    return _error_detail(request, code, message, retryable=retryable, task=task)


def _require_row_version(
    request: Request, task: VideoPublishTask, expected: int | None
) -> None:
    if expected is None or expected != task.row_version:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            _action_conflict(
                request,
                task,
                "TASK_VERSION_CONFLICT",
                "任务已更新，请刷新后重试。",
                retryable=True,
            ),
        )


def _encode_cursor(created_at: datetime, task_id: int) -> str:
    raw = json.dumps(
        {"createdAt": created_at.isoformat(), "id": task_id},
        separators=(",", ":"),
    ).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _decode_cursor(value: str) -> tuple[datetime, int]:
    try:
        padded = value + "=" * (-len(value) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded).decode())
        created_at = datetime.fromisoformat(payload["createdAt"])
        task_id = int(payload["id"])
        if created_at.tzinfo is None or task_id <= 0:
            raise ValueError
        return created_at, task_id
    except (
        ValueError,
        TypeError,
        KeyError,
        json.JSONDecodeError,
        binascii.Error,
    ) as exc:
        raise ValueError("INVALID_CURSOR") from exc


def _attempt_summaries(
    session: Session, tasks: list[VideoPublishTask]
) -> dict[
    int,
    tuple[list[VideoPublishAttempt], int, int, VideoPublishAttempt | None],
]:
    task_ids = [task.id for task in tasks]
    if not task_ids:
        return {}
    counts = {
        row.task_id: (int(row.publish_count), int(row.verify_count))
        for row in session.execute(
            select(
                VideoPublishAttempt.task_id,
                func.count()
                .filter(VideoPublishAttempt.kind == "publish")
                .label("publish_count"),
                func.count()
                .filter(VideoPublishAttempt.kind == "verify")
                .label("verify_count"),
            )
            .where(VideoPublishAttempt.task_id.in_(task_ids))
            .group_by(VideoPublishAttempt.task_id)
        )
    }
    latest_sequence = (
        select(
            VideoPublishAttempt.task_id.label("task_id"),
            func.max(VideoPublishAttempt.sequence_no).label("sequence_no"),
        )
        .where(VideoPublishAttempt.task_id.in_(task_ids))
        .group_by(VideoPublishAttempt.task_id)
        .subquery()
    )
    related = aliased(VideoPublishAttempt)
    latest = {
        attempt.task_id: (
            attempt,
            attempt if attempt.kind == "publish" else related_attempt,
        )
        for attempt, related_attempt in session.execute(
            select(VideoPublishAttempt, related)
            .join(
                latest_sequence,
                and_(
                    VideoPublishAttempt.task_id == latest_sequence.c.task_id,
                    VideoPublishAttempt.sequence_no == latest_sequence.c.sequence_no,
                ),
            )
            .outerjoin(related, VideoPublishAttempt.related_attempt_id == related.id)
        )
    }
    return {
        task_id: (
            [latest[task_id][0]] if task_id in latest else [],
            counts.get(task_id, (0, 0))[0],
            counts.get(task_id, (0, 0))[1],
            latest[task_id][1] if task_id in latest else None,
        )
        for task_id in task_ids
    }


def _queue_positions(session: Session, tasks: list[VideoPublishTask]) -> dict[int, int]:
    task_ids = [
        task.id
        for task in tasks
        if task.status == "pending"
        and task.stage in {"queued", "waiting_device"}
        and task.queued_at is not None
    ]
    if not task_ids:
        return {}
    ranked = (
        select(
            VideoPublishTask.id.label("task_id"),
            func.row_number()
            .over(order_by=(VideoPublishTask.queued_at, VideoPublishTask.id))
            .label("position"),
        )
        .where(
            VideoPublishTask.status == "pending",
            VideoPublishTask.stage.in_(["queued", "waiting_device"]),
            VideoPublishTask.queued_at.is_not(None),
        )
        .subquery()
    )
    return {
        int(row.task_id): int(row.position)
        for row in session.execute(
            select(ranked.c.task_id, ranked.c.position).where(
                ranked.c.task_id.in_(task_ids)
            )
        )
    }


def _queue_position(session: Session, task: VideoPublishTask) -> int | None:
    return _queue_positions(session, [task]).get(task.id)


def _etag(payload: object) -> str:
    # serverTime is response metadata, not resource state.
    if isinstance(payload, dict):
        payload = {key: value for key, value in payload.items() if key != "serverTime"}
    encoded = json.dumps(payload, default=str, sort_keys=True, separators=(",", ":"))
    return '"' + hashlib.sha256(encoded.encode()).hexdigest()[:16] + '"'


def _conditional(
    request: Request, response: Response, payload: object
) -> Response | None:
    tag = _etag(payload)
    response.headers["ETag"] = tag
    if request.headers.get("if-none-match") == tag:
        return Response(status_code=status.HTTP_304_NOT_MODIFIED, headers={"ETag": tag})
    return None


@router.get("/config")
def config(request: Request, session: Annotated[Session, Depends(get_session)]) -> dict:
    heartbeat_row = session.scalar(
        select(PublishWorkerHeartbeat)
        .where(
            PublishWorkerHeartbeat.status == "ready",
            PublishWorkerHeartbeat.heartbeat_at
            >= text("now() - interval '15 seconds'"),
        )
        .order_by(PublishWorkerHeartbeat.heartbeat_at.desc())
        .limit(1)
    )
    heartbeat = heartbeat_row.heartbeat_at if heartbeat_row else None
    configured_serial = bool(os.environ.get("ARTEMIS_DEVICE_SERIAL"))
    device_status = (
        heartbeat_row.device_status
        if heartbeat_row is not None and configured_serial
        else "unknown"
    )
    device_message = (
        sanitize_text(heartbeat_row.device_message)
        if heartbeat_row is not None and heartbeat_row.device_message
        else "未配置设备序列号"
        if not configured_serial
        else "Worker 未报告近期设备探测结果"
    )
    # Config deliberately does not expose credentials or signed URLs.
    grant = request.scope.get("access_grant")
    actor_can_write = bool(
        grant is not None
        and getattr(grant, "allows", lambda _role: False)(Role.READWRITE)
    ) or request.scope.get("api_key_role") in {"readwrite", "admin"}
    can_write = bool(heartbeat and actor_can_write and configured_serial)
    return {
        "acceptedContentTypes": ["video/mp4"],
        "acceptedExtensions": [".mp4"],
        "maxVideoBytes": max_video_bytes(),
        "maxCaptionCharacters": max_caption_chars(),
        "maxPublishAttempts": int(os.environ.get("TIKTOK_PUBLISH_MAX_ATTEMPTS", "3")),
        "uploadUrlTtlSeconds": int(
            os.environ.get("TIKTOK_PUBLISH_UPLOAD_TTL_SECONDS", "900")
        ),
        "target": {
            "appName": "TikTok",
            "appPackage": os.environ.get(
                "ARTEMIS_APP_PACKAGE", "com.zhiliaoapp.musically"
            ),
            "deviceSerialMasked": _mask(os.environ.get("ARTEMIS_DEVICE_SERIAL", "")),
            "album": os.environ.get("TIKTOK_PUBLISH_ALBUM", "TTSERP"),
        },
        "worker": {
            "status": "ready" if heartbeat else "unavailable",
            "lastHeartbeatAt": heartbeat,
        },
        "device": {
            "status": device_status,
            "message": device_message,
        },
        "artemis": {
            "profile": os.environ.get("ARTEMIS_PROFILE", "pro"),
            "verificationLevel": os.environ.get("ARTEMIS_VERIFICATION_LEVEL", "strict"),
        },
        "workerTiming": {
            "pollSeconds": float(os.environ.get("PUBLISH_POLL_INTERVAL_SECONDS", "2")),
            "leaseSeconds": int(os.environ.get("PUBLISH_TASK_LEASE_SECONDS", "30")),
            "heartbeatSeconds": float(
                os.environ.get("PUBLISH_WORKER_HEARTBEAT_SECONDS", "5")
            ),
        },
        "canWrite": can_write,
        "canViewDiagnostics": _is_privileged(request),
        "writeBlockReason": None
        if can_write
        else "发布权限不足"
        if not actor_can_write
        else "发布设备未配置"
        if not configured_serial
        else "发布 Worker 不可用",
        "serverTime": datetime.now(UTC),
    }


@router.post("/tasks", status_code=status.HTTP_201_CREATED)
def create_task(
    body: CreateIn,
    request: Request,
    session: Annotated[Session, Depends(get_session)],
    store: Annotated[VideoObjectStore, Depends(get_store)],
    response: Response,
) -> dict:
    require_role_at_least(request, "readwrite")
    _csrf(request)
    try:
        task, url, replay = create_upload_ticket(
            session,
            CreateCommand(
                client_request_id=body.client_request_id,
                filename=body.filename,
                content_type=body.content_type,
                size_bytes=body.size_bytes,
                caption=body.caption,
                actor_user_id=request.scope.get("user_id"),
                actor_key_hash=caller_key_hash(request),
            ),
            store,
        )
    except PermissionError as exc:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            _error_detail(request, "TASK_NOT_FOUND", "TASK_NOT_FOUND", retryable=False),
        ) from exc
    except ValueError as exc:
        code = str(exc)
        error_status = (
            status.HTTP_503_SERVICE_UNAVAILABLE
            if code == "OBJECT_STORE_UNAVAILABLE"
            else status.HTTP_413_CONTENT_TOO_LARGE
            if code == "VIDEO_TOO_LARGE"
            else status.HTTP_409_CONFLICT
            if code == "IDEMPOTENCY_PAYLOAD_MISMATCH"
            else status.HTTP_422_UNPROCESSABLE_CONTENT
        )
        raise HTTPException(
            error_status,
            _error_detail(
                request,
                code,
                code,
                retryable=code
                in {
                    "OBJECT_STORE_UNAVAILABLE",
                    "VIDEO_TOO_LARGE",
                    "CAPTION_REQUIRED",
                    "CAPTION_TOO_LONG",
                },
            ),
        ) from exc
    response.status_code = status.HTTP_200_OK if replay else status.HTTP_201_CREATED
    data = _snapshot(task, expose_client_request_id=True)
    data.update(
        {
            "idempotentReplay": replay,
            "rowVersion": task.row_version,
            "upload": {
                "method": "PUT",
                "url": url,
                "headers": {"Content-Type": body.content_type},
                "expiresAt": upload_expires_at(store),
            },
        }
    )
    return data


@router.post("/tasks/{task_id}/upload-url")
def refresh_upload_url(
    task_id: UUID,
    request: Request,
    session: Annotated[Session, Depends(get_session)],
    store: Annotated[VideoObjectStore, Depends(get_store)],
) -> dict:
    require_role_at_least(request, "readwrite")
    _csrf(request)
    task = _task_for_actor(session, task_id, request)
    if task.stage != "awaiting_upload":
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            _action_conflict(
                request, task, "TASK_ACTION_NOT_ALLOWED", "TASK_ACTION_NOT_ALLOWED"
            ),
        )
    try:
        upload_url = store.presign_put(task.object_key, task.content_type)
    except Exception as exc:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            _error_detail(
                request,
                "OBJECT_STORE_UNAVAILABLE",
                "OBJECT_STORE_UNAVAILABLE",
                retryable=True,
                task=task,
            ),
        ) from exc
    return {
        **_snapshot(task, expose_client_request_id=True),
        "upload": {
            "method": "PUT",
            "url": upload_url,
            "headers": {"Content-Type": task.content_type},
            "expiresAt": upload_expires_at(store),
        },
    }


@router.post("/tasks/{task_id}/confirm-upload")
def confirm(
    task_id: UUID,
    body: ActionIn,
    request: Request,
    session: Annotated[Session, Depends(get_session)],
    store: Annotated[VideoObjectStore, Depends(get_store)],
) -> dict:
    require_role_at_least(request, "readwrite")
    _csrf(request)
    task_snapshot = _task_for_actor(session, task_id, request)
    _require_row_version(request, task_snapshot, body.row_version)
    if task_snapshot.stage != "awaiting_upload":
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            _action_conflict(
                request,
                task_snapshot,
                "TASK_ACTION_NOT_ALLOWED",
                "TASK_ACTION_NOT_ALLOWED",
            ),
        )
    try:
        task = confirm_upload(
            session,
            task_id,
            store,
            expected_version=task_snapshot.row_version,
        )
    except LookupError as exc:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            _error_detail(request, "TASK_NOT_FOUND", "TASK_NOT_FOUND", retryable=False),
        ) from exc
    except TaskConflict as exc:
        conflict = _action_conflict(
            request, exc.task, exc.code, exc.code, retryable=True
        )
        session.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, conflict) from exc
    except ValueError as exc:
        code = str(exc)
        error_status = (
            status.HTTP_503_SERVICE_UNAVAILABLE
            if code == "OBJECT_STORE_UNAVAILABLE"
            else status.HTTP_409_CONFLICT
            if code in {"TASK_ACTION_NOT_ALLOWED", "TASK_VERSION_CONFLICT"}
            else status.HTTP_422_UNPROCESSABLE_CONTENT
        )
        raise HTTPException(
            error_status,
            _error_detail(
                request,
                code,
                code,
                retryable=code
                in {
                    "OBJECT_STORE_UNAVAILABLE",
                    "UPLOAD_NOT_FOUND",
                    "UPLOAD_SIZE_MISMATCH",
                    "UPLOAD_MIME_MISSING",
                    "UPLOAD_MIME_MISMATCH",
                    "UPLOAD_ETAG_MISSING",
                    "TASK_VERSION_CONFLICT",
                },
                task=task_snapshot
                if error_status == status.HTTP_409_CONFLICT
                else None,
            ),
        ) from exc
    return _snapshot(
        task,
        expose_client_request_id=True,
        queue_position=_queue_position(session, task),
    )


def _task(session: Session, task_id: UUID, *, lock: bool = False) -> VideoPublishTask:
    query = (
        select(VideoPublishTask)
        .where(VideoPublishTask.public_id == task_id)
        .options(selectinload(VideoPublishTask.attempts))
    )
    if lock:
        query = query.with_for_update()
    task = session.scalar(query)
    if task is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "TASK_NOT_FOUND")
    # Access the relationship before the session closes.
    _ = task.attempts
    return task


def _poll_state(session: Session) -> dict[str, bool]:
    return {
        "running": session.scalar(
            select(VideoPublishTask.id)
            .where(VideoPublishTask.status == "running")
            .limit(1)
        )
        is not None,
        "cleaning": session.scalar(
            select(VideoPublishTask.id)
            .where(
                VideoPublishTask.cleanup_intent != "none",
                VideoPublishTask.device_cleanup_status.in_(["pending", "failed"]),
            )
            .limit(1)
        )
        is not None,
        "queued": session.scalar(
            select(VideoPublishTask.id)
            .where(
                VideoPublishTask.status == "pending",
                VideoPublishTask.stage.in_(["queued", "waiting_device"]),
            )
            .limit(1)
        )
        is not None,
    }


@router.get("/tasks/current", response_model=None)
def current(
    request: Request,
    session: Annotated[Session, Depends(get_session)],
    response: Response,
) -> dict | Response:
    current_query = (
        select(VideoPublishTask)
        .where(
            (VideoPublishTask.status == "running")
            | (
                (VideoPublishTask.cleanup_intent != "none")
                & VideoPublishTask.device_cleanup_status.in_(["pending", "failed"])
            )
        )
        .order_by(VideoPublishTask.id)
        .limit(1)
    )
    owner_clause = _owner_clause(request)
    if owner_clause is not None:
        current_query = current_query.where(owner_clause)
    task = session.scalar(current_query)
    poll_state = _poll_state(session)
    summary = _attempt_summaries(session, [task]) if task else {}
    current_summary = summary.get(task.id if task else 0)
    attempts = current_summary[0] if current_summary else []
    publish_count = current_summary[1] if current_summary else 0
    verify_count = current_summary[2] if current_summary else 0
    related_publish = current_summary[3] if current_summary else None
    payload = {
        "task": (
            _snapshot(
                task,
                expose_client_request_id=True,
                summary_attempts=attempts,
                publish_attempt_count=publish_count,
                verify_attempt_count=verify_count,
                related_publish_attempt=related_publish,
            )
            if task
            else None
        ),
        "pollState": poll_state,
        "suggestedPollSeconds": 2 if task else 30,
        "serverTime": datetime.now(UTC),
    }
    cached = _conditional(request, response, payload)
    return cached if cached is not None else payload


@router.get("/tasks", response_model=None)
def list_tasks(
    request: Request,
    session: Annotated[Session, Depends(get_session)],
    response: Response,
    status_filter: str | None = Query(default=None, alias="status"),
    limit: int = Query(default=30, ge=1, le=100),
    cursor: str | None = Query(default=None),
) -> dict | Response:
    query = select(VideoPublishTask).order_by(
        VideoPublishTask.created_at.desc(), VideoPublishTask.id.desc()
    )
    owner_clause = _owner_clause(request)
    if owner_clause is not None:
        query = query.where(owner_clause)
    if status_filter:
        query = query.where(VideoPublishTask.status == status_filter)
    if cursor:
        try:
            cursor_created_at, cursor_id = _decode_cursor(cursor)
        except ValueError as exc:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT,
                _error_detail(
                    request,
                    "INVALID_CURSOR",
                    "INVALID_CURSOR",
                    retryable=False,
                ),
            ) from exc
        query = query.where(
            or_(
                VideoPublishTask.created_at < cursor_created_at,
                and_(
                    VideoPublishTask.created_at == cursor_created_at,
                    VideoPublishTask.id < cursor_id,
                ),
            )
        )
    fetched = list(session.scalars(query.limit(limit + 1)))
    has_more = len(fetched) > limit
    rows = fetched[:limit]
    summaries = _attempt_summaries(session, rows)
    queue_positions = _queue_positions(session, rows)
    payload = {
        "items": [
            _snapshot(
                task,
                expose_client_request_id=True,
                summary_attempts=summaries[task.id][0],
                publish_attempt_count=summaries[task.id][1],
                verify_attempt_count=summaries[task.id][2],
                related_publish_attempt=summaries[task.id][3],
                queue_position=queue_positions.get(task.id),
            )
            for task in rows
        ],
        "pollState": _poll_state(session),
        "nextCursor": (
            _encode_cursor(rows[-1].created_at, rows[-1].id)
            if has_more and rows
            else None
        ),
        "totalApprox": len(rows),
        "serverTime": datetime.now(UTC),
    }
    cached = _conditional(request, response, payload)
    return cached if cached is not None else payload


@router.get("/metrics")
def metrics(
    request: Request,
    session: Annotated[Session, Depends(get_session)],
) -> dict:
    """Return content-free operational gauges at the caller's visibility scope."""
    require_role_at_least(request, "readonly")
    query = select(
        func.count()
        .filter(
            VideoPublishTask.status == "pending",
            VideoPublishTask.stage.in_(["queued", "waiting_device"]),
        )
        .label("queue_depth"),
        func.count().filter(VideoPublishTask.status == "running").label("running"),
        func.count()
        .filter(VideoPublishTask.status == "needs_review")
        .label("needs_review"),
        func.count()
        .filter(VideoPublishTask.device_cleanup_status == "pending")
        .label("device_pending"),
        func.count()
        .filter(VideoPublishTask.device_cleanup_status == "failed")
        .label("device_failed"),
        func.count()
        .filter(VideoPublishTask.spool_cleanup_status == "failed")
        .label("spool_failed"),
        func.count()
        .filter(VideoPublishTask.object_cleanup_status == "failed")
        .label("object_failed"),
    ).select_from(VideoPublishTask)
    owner_clause = _owner_clause(request)
    if owner_clause is not None:
        query = query.where(owner_clause)
    row = session.execute(query).one()

    task_counts_query = select(VideoPublishTask.status, func.count()).group_by(
        VideoPublishTask.status
    )
    if owner_clause is not None:
        task_counts_query = task_counts_query.where(owner_clause)
    tasks_by_status = {
        str(task_status): int(count)
        for task_status, count in session.execute(task_counts_query)
    }

    attempts_query = (
        select(VideoPublishAttempt.kind, VideoPublishAttempt.status, func.count())
        .join(VideoPublishTask, VideoPublishTask.id == VideoPublishAttempt.task_id)
        .group_by(VideoPublishAttempt.kind, VideoPublishAttempt.status)
    )
    if owner_clause is not None:
        attempts_query = attempts_query.where(owner_clause)
    attempts_by_kind_status: dict[str, dict[str, int]] = {}
    for kind, attempt_status, count in session.execute(attempts_query):
        attempts_by_kind_status.setdefault(str(kind), {})[str(attempt_status)] = int(
            count
        )

    elapsed = func.extract(
        "epoch", func.clock_timestamp() - VideoPublishTask.stage_started_at
    )
    durations_query = (
        select(
            VideoPublishTask.stage,
            func.count(),
            func.avg(elapsed),
            func.max(elapsed),
        )
        .where(VideoPublishTask.stage_started_at.is_not(None))
        .group_by(VideoPublishTask.stage)
    )
    if owner_clause is not None:
        durations_query = durations_query.where(owner_clause)
    stage_durations = {
        str(stage_name): {
            "count": int(count),
            "average": float(average or 0),
            "maximum": float(maximum or 0),
        }
        for stage_name, count, average, maximum in session.execute(durations_query)
    }

    heartbeat_age = session.scalar(
        select(
            func.extract(
                "epoch",
                func.clock_timestamp() - func.max(PublishWorkerHeartbeat.heartbeat_at),
            )
        )
    )
    return {
        "queueDepth": int(row.queue_depth),
        "running": int(row.running),
        "needsReview": int(row.needs_review),
        "tasksByStatus": tasks_by_status,
        "attemptsByKindStatus": attempts_by_kind_status,
        "stageDurationSeconds": stage_durations,
        "workerHeartbeatAgeSeconds": (
            max(0.0, float(heartbeat_age)) if heartbeat_age is not None else None
        ),
        "cleanup": {
            "devicePending": int(row.device_pending),
            "deviceFailed": int(row.device_failed),
            "spoolFailed": int(row.spool_failed),
            "objectFailed": int(row.object_failed),
        },
        "serverTime": datetime.now(UTC),
    }


@router.get("/tasks/{task_id}", response_model=None)
def detail(
    task_id: UUID,
    request: Request,
    session: Annotated[Session, Depends(get_session)],
    response: Response,
    include_diagnostics: bool = Query(default=False, alias="includeDiagnostics"),
) -> dict | Response:
    if include_diagnostics:
        require_role_at_least(request, "admin")
    task = _task_for_actor(session, task_id, request)
    payload = _snapshot(
        task,
        detail=True,
        diagnostics=include_diagnostics,
        expose_client_request_id=True,
        queue_position=_queue_position(session, task),
    )
    cached = _conditional(request, response, payload)
    return cached if cached is not None else payload


@router.post("/tasks/{task_id}/cancel")
def cancel(
    task_id: UUID,
    body: ActionIn,
    request: Request,
    session: Annotated[Session, Depends(get_session)],
    store: Annotated[VideoObjectStore, Depends(get_store)],
) -> dict:
    require_role_at_least(request, "readwrite")
    _csrf(request)
    require_destructive_guard(request, op_name="video_publish.cancel_object")
    task_snapshot = _task_for_actor(session, task_id, request, lock=True)
    _require_row_version(request, task_snapshot, body.row_version)
    try:
        return _snapshot(
            cancel_task(session, task_id, store),
            detail=True,
            expose_client_request_id=True,
        )
    except LookupError as exc:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            _error_detail(request, "TASK_NOT_FOUND", "TASK_NOT_FOUND", retryable=False),
        ) from exc
    except ValueError as exc:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            _action_conflict(request, task_snapshot, str(exc), str(exc)),
        ) from exc


@router.post("/tasks/{task_id}/retry")
def retry(
    task_id: UUID,
    body: ActionIn,
    request: Request,
    session: Annotated[Session, Depends(get_session)],
    store: Annotated[VideoObjectStore, Depends(get_store)],
) -> dict:
    require_role_at_least(request, "readwrite")
    _csrf(request)
    task_snapshot = _task_for_actor(session, task_id, request, lock=True)
    _require_row_version(request, task_snapshot, body.row_version)
    try:
        task = retry_task(session, task_id, store)
        return _snapshot(
            task,
            expose_client_request_id=True,
            queue_position=_queue_position(session, task),
        )
    except LookupError as exc:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            _error_detail(request, "TASK_NOT_FOUND", "TASK_NOT_FOUND", retryable=False),
        ) from exc
    except ValueError as exc:
        code = str(exc)
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE
            if code == "OBJECT_STORE_UNAVAILABLE"
            else status.HTTP_409_CONFLICT,
            _action_conflict(
                request,
                task_snapshot,
                code,
                code,
                retryable=code == "OBJECT_STORE_UNAVAILABLE",
            ),
        ) from exc


@router.post("/tasks/{task_id}/replace-upload")
def replace_upload_task(
    task_id: UUID,
    body: ActionIn,
    request: Request,
    session: Annotated[Session, Depends(get_session)],
) -> dict:
    require_role_at_least(request, "readwrite")
    _csrf(request)
    task_snapshot = _task_for_actor(session, task_id, request, lock=True)
    _require_row_version(request, task_snapshot, body.row_version)
    try:
        return _snapshot(
            replace_upload(session, task_id), detail=True, expose_client_request_id=True
        )
    except LookupError as exc:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            _error_detail(request, "TASK_NOT_FOUND", "TASK_NOT_FOUND", retryable=False),
        ) from exc
    except ValueError as exc:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            _action_conflict(request, task_snapshot, str(exc), str(exc)),
        ) from exc


@router.post("/tasks/{task_id}/verify")
def verify(
    task_id: UUID,
    body: ActionIn,
    request: Request,
    session: Annotated[Session, Depends(get_session)],
) -> dict:
    require_role_at_least(request, "readwrite")
    _csrf(request)
    _lock_publish_slot(session)
    task_snapshot = _task_for_actor(session, task_id, request, lock=True)
    _require_row_version(request, task_snapshot, body.row_version)
    try:
        task = request_verification(session, task_id)
        session.commit()
        return _snapshot(task, detail=True, expose_client_request_id=True)
    except LookupError as exc:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            _error_detail(request, "TASK_NOT_FOUND", "TASK_NOT_FOUND", retryable=False),
        ) from exc
    except ValueError as exc:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            _action_conflict(request, task_snapshot, str(exc), str(exc)),
        ) from exc
    except IntegrityError as exc:
        session.rollback()
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            _action_conflict(
                request,
                task_snapshot,
                "VERIFY_ALREADY_RUNNING",
                "A verification attempt is already running",
            ),
        ) from exc


@router.post("/tasks/{task_id}/cleanup/retry")
def retry_cleanup(
    task_id: UUID,
    body: ActionIn,
    request: Request,
    session: Annotated[Session, Depends(get_session)],
) -> dict:
    require_role_at_least(request, "readwrite")
    _csrf(request)
    require_destructive_guard(request, op_name="video_publish.retry_cleanup")
    task = _task_for_actor(session, task_id, request, lock=True)
    _require_row_version(request, task, body.row_version)
    try:
        retry_cleanup_resources(session, task, resources=body.resources)
    except ValueError as exc:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            _action_conflict(request, task, str(exc), str(exc)),
        ) from exc
    session.commit()
    return _snapshot(
        _task_for_actor(session, task_id, request),
        detail=True,
        expose_client_request_id=True,
    )
