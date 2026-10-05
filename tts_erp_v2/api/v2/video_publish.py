"""HTTP boundary for ttsERP video publishing; adapters stay server-side."""

from __future__ import annotations

import hashlib
import json
import os
from datetime import UTC, datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import false, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from tts_erp_v2.access import Role
from tts_erp_v2.api.deps import (
    caller_key_hash,
    get_session,
    require_destructive_guard,
    require_role_at_least,
)
from tts_erp_v2.db.models.publishing import (
    PublishWorkerHeartbeat,
    VideoPublishAttempt,
    VideoPublishTask,
)
from tts_erp_v2.publishing.domain import allowed_actions
from tts_erp_v2.publishing.object_store import MinioVideoStore, VideoObjectStore
from tts_erp_v2.publishing.repository import (
    _lock_publish_slot,
    request_verification,
    retry_cleanup_resources,
)
from tts_erp_v2.publishing.submission import (
    CreateCommand,
    cancel_task,
    confirm_upload,
    create_upload_ticket,
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
            "cookie-authed POST must set header X-Requested-With: tts-erp",
        )


class CreateIn(BaseModel):
    client_request_id: UUID = Field(alias="clientRequestId")
    filename: str = Field(min_length=1, max_length=255)
    content_type: str = Field(alias="contentType")
    size_bytes: int = Field(alias="sizeBytes", gt=0)
    caption: str = Field(max_length=4000)

    model_config = {"populate_by_name": True}


class ActionIn(BaseModel):
    row_version: int | None = Field(default=None, alias="rowVersion")
    model_config = {"populate_by_name": True}


def _attempt(a: VideoPublishAttempt, *, diagnostics: bool = False) -> dict:
    output = a.artemis_output if diagnostics else None
    result = {
        "sequenceNo": a.sequence_no,
        "kind": a.kind,
        "artemisSessionId": str(a.artemis_session_id),
        "status": a.status,
        "stepsCount": a.steps_count,
        "retryClassification": a.retry_classification,
        "retrySafe": a.retry_safe,
        "error": a.artemis_error,
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
) -> dict:
    attempts = sorted(task.attempts, key=lambda a: a.sequence_no, reverse=True)
    latest = attempts[0] if attempts else None
    actions = allowed_actions(task)
    data = {
        "taskId": str(task.public_id),
        "rowVersion": task.row_version,
        "filename": task.original_filename,
        "sizeBytes": task.size_bytes,
        "captionPreview": task.caption.splitlines()[0][:160] if task.caption else "",
        "status": task.status,
        "stage": task.stage,
        "latestArtemisSessionId": str(latest.artemis_session_id) if latest else None,
        "publishAttemptCount": sum(a.kind == "publish" for a in attempts),
        "verifyAttemptCount": sum(a.kind == "verify" for a in attempts),
        "lastErrorCode": task.last_error_code,
        "lastErrorMessage": task.last_error_message,
        "createdAt": task.created_at,
        "updatedAt": task.updated_at,
        "startedAt": task.started_at,
        "allowedActions": [a.value for a in actions],
    }
    if expose_client_request_id:
        data["clientRequestId"] = str(task.client_request_id)
    if "continue_upload" in data["allowedActions"]:
        data["caption"] = task.caption
    if latest:
        data["currentAttempt"] = _attempt(latest)
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
                        "error": task.device_cleanup_error,
                    },
                    "spool": {
                        "status": task.spool_cleanup_status,
                        "error": task.spool_cleanup_error,
                    },
                    "object": {
                        "status": task.object_cleanup_status,
                        "error": task.object_cleanup_error,
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
    task = _task(session, task_id, lock=True) if lock else _task(session, task_id)
    if not _owns_task(task, request):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "TASK_NOT_FOUND")
    return task


def _action_conflict(task: VideoPublishTask, code: str, message: str) -> dict:
    return {
        "code": code,
        "message": message,
        "rowVersion": task.row_version,
        "allowedActions": [a.value for a in allowed_actions(task)],
    }


def _require_row_version(task: VideoPublishTask, expected: int | None) -> None:
    if expected is None or expected != task.row_version:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            _action_conflict(
                task, "ROW_VERSION_CONFLICT", "任务已更新，请刷新后重试。"
            ),
        )


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
    # Config deliberately does not expose credentials or signed URLs.
    grant = request.scope.get("access_grant")
    actor_can_write = bool(
        grant is not None
        and getattr(grant, "allows", lambda _role: False)(Role.READWRITE)
    ) or request.scope.get("api_key_role") in {"readwrite", "admin"}
    can_write = bool(os.environ.get("ARTEMIS_DEVICE_SERIAL")) and bool(
        heartbeat and actor_can_write
    )
    return {
        "acceptedContentTypes": ["video/mp4"],
        "acceptedExtensions": [".mp4"],
        "maxVideoBytes": int(
            os.environ.get("TIKTOK_PUBLISH_MAX_VIDEO_BYTES", str(500 * 1024 * 1024))
        ),
        "maxCaptionCharacters": int(
            os.environ.get("TIKTOK_PUBLISH_MAX_CAPTION_CHARS", "4000")
        ),
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
        "device": {"status": "unknown", "message": "发布服务将在任务执行前检查设备"},
        "canWrite": can_write,
        "writeBlockReason": None
        if can_write
        else "发布权限不足"
        if not actor_can_write
        else "发布服务或设备不可用",
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
        raise HTTPException(status.HTTP_404_NOT_FOUND, "TASK_NOT_FOUND") from exc
    except ValueError as exc:
        code = str(exc)
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY
            if code not in {"IDEMPOTENCY_PAYLOAD_MISMATCH"}
            else status.HTTP_409_CONFLICT,
            {"code": code, "message": code},
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
        raise HTTPException(status.HTTP_409_CONFLICT, "TASK_ACTION_NOT_ALLOWED")
    return {
        **_snapshot(task, expose_client_request_id=True),
        "upload": {
            "method": "PUT",
            "url": store.presign_put(task.object_key, task.content_type),
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
    task_snapshot = _task_for_actor(session, task_id, request, lock=True)
    _require_row_version(task_snapshot, body.row_version)
    try:
        task = confirm_upload(session, task_id, store)
    except LookupError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            {"code": str(exc), "message": str(exc)},
        ) from exc
    return _snapshot(task, expose_client_request_id=True)


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
        .where(VideoPublishTask.status == "running")
        .options(selectinload(VideoPublishTask.attempts))
        .order_by(VideoPublishTask.id)
        .limit(1)
    )
    owner_clause = _owner_clause(request)
    if owner_clause is not None:
        current_query = current_query.where(owner_clause)
    task = session.scalar(current_query)
    poll_state = _poll_state(session)
    payload = {
        "task": _snapshot(task, expose_client_request_id=True) if task else None,
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
    cursor: int | None = Query(default=None),
) -> dict | Response:
    query = (
        select(VideoPublishTask)
        .options(selectinload(VideoPublishTask.attempts))
        .order_by(VideoPublishTask.created_at.desc(), VideoPublishTask.id.desc())
        .limit(limit)
    )
    owner_clause = _owner_clause(request)
    if owner_clause is not None:
        query = query.where(owner_clause)
    if status_filter:
        query = query.where(VideoPublishTask.status == status_filter)
    if cursor:
        query = query.where(VideoPublishTask.id < cursor)
    rows = list(session.scalars(query))
    payload = {
        "items": [_snapshot(t, expose_client_request_id=True) for t in rows],
        "pollState": _poll_state(session),
        "nextCursor": rows[-1].id if len(rows) == limit else None,
        "totalApprox": len(rows),
        "serverTime": datetime.now(UTC),
    }
    cached = _conditional(request, response, payload)
    return cached if cached is not None else payload


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
    payload = _snapshot(
        _task_for_actor(session, task_id, request),
        detail=True,
        diagnostics=include_diagnostics,
        expose_client_request_id=True,
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
    _require_row_version(task_snapshot, body.row_version)
    try:
        return _snapshot(
            cancel_task(session, task_id, store),
            detail=True,
            expose_client_request_id=True,
        )
    except LookupError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(
            status.HTTP_409_CONFLICT, {"code": str(exc), "message": str(exc)}
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
    _require_row_version(task_snapshot, body.row_version)
    try:
        return _snapshot(
            retry_task(session, task_id, store), expose_client_request_id=True
        )
    except LookupError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    except ValueError as exc:
        code = str(exc)
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE
            if code == "OBJECT_STORE_UNAVAILABLE"
            else status.HTTP_409_CONFLICT,
            _action_conflict(task_snapshot, code, code),
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
    _require_row_version(task_snapshot, body.row_version)
    try:
        return _snapshot(
            replace_upload(session, task_id), detail=True, expose_client_request_id=True
        )
    except LookupError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(
            status.HTTP_409_CONFLICT, {"code": str(exc), "message": str(exc)}
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
    _require_row_version(task_snapshot, body.row_version)
    try:
        task = request_verification(session, task_id)
        session.commit()
        return _snapshot(task, detail=True, expose_client_request_id=True)
    except LookupError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            _action_conflict(task_snapshot, str(exc), str(exc)),
        ) from exc
    except IntegrityError as exc:
        session.rollback()
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            _action_conflict(
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
    task = _task_for_actor(session, task_id, request, lock=True)
    _require_row_version(task, body.row_version)
    try:
        retry_cleanup_resources(session, task)
    except ValueError as exc:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            _action_conflict(task, str(exc), str(exc)),
        ) from exc
    session.commit()
    return _snapshot(
        _task_for_actor(session, task_id, request),
        detail=True,
        expose_client_request_id=True,
    )
