from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import cast
from uuid import UUID, uuid4

import pytest
from fastapi import HTTPException, Request, Response
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from tts_erp_v2.access import AccessGrant, AuthMode, Role
from tts_erp_v2.api.v2.video_publish import (
    ActionIn,
    CreateIn,
    config,
    confirm,
    create_task,
    current,
    detail,
    list_tasks,
    refresh_upload_url,
    replace_upload_task,
    retry,
    retry_cleanup,
)
from tts_erp_v2.db.models.publishing import (
    PublishWorkerHeartbeat,
    VideoPublishAttempt,
    VideoPublishTask,
)
from tts_erp_v2.publishing.artemis_client import (
    ArtemisAdmissionRejected,
    ArtemisResult,
    ArtemisTransportError,
)
from tts_erp_v2.publishing.dispatcher import (
    LeaseLost,
    PublishDependencies,
    _cleanup_success,
    _execute_cleanup,
    _run_attempt,
    _safe_retry,
    dispatch_one,
)
from tts_erp_v2.publishing.domain import (
    AttemptStatus,
    TaskStage,
    TaskStatus,
    transition_task,
)
from tts_erp_v2.publishing.object_store import VideoObjectStore
from tts_erp_v2.publishing.repository import (
    CleanupClaimRequest,
    _lease_task,
    claim_cleanup_work,
    claim_one,
    schedule_retention_cleanup,
)
from tts_erp_v2.publishing.submission import (
    CreateCommand,
    create_upload_ticket,
    replace_upload,
    retry_task,
)
from tts_erp_v2.storage.minio_client import ObjectNotFound


def _task(
    *,
    device_cleanup_status: str = "not_started",
    object_cleanup_status: str = "not_started",
    spool_cleanup_status: str = "not_started",
    status: str = TaskStatus.PENDING.value,
    stage: str = TaskStage.QUEUED.value,
    device_path: str | None = "/sdcard/Movies/TEST/video.mp4",
    created_by_user_id: int | None = None,
    created_by_key_hash: str | None = None,
) -> VideoPublishTask:
    effective_stage = (
        TaskStage.DONE.value
        if status
        in {
            TaskStatus.SUCCEEDED.value,
            TaskStatus.FAILED.value,
            TaskStatus.NEEDS_REVIEW.value,
            TaskStatus.CANCELLED.value,
        }
        else stage
    )
    return VideoPublishTask(
        public_id=uuid4(),
        client_request_id=uuid4(),
        created_by_user_id=created_by_user_id,
        created_by_key_hash=created_by_key_hash,
        caption="TEST_caption",
        original_filename="TEST_video.mp4",
        content_type="video/mp4",
        size_bytes=4,
        object_bucket="tiktok-video",
        object_key=f"TEST/{uuid4()}.mp4",
        status=status,
        stage=effective_stage,
        cleanup_intent=(
            "requeue_publish"
            if status == TaskStatus.PENDING.value
            and any(
                value in {"pending", "failed"}
                for value in (
                    device_cleanup_status,
                    object_cleanup_status,
                    spool_cleanup_status,
                )
            )
            else "preserve_state"
            if any(
                value in {"pending", "failed"}
                for value in (
                    device_cleanup_status,
                    object_cleanup_status,
                    spool_cleanup_status,
                )
            )
            else "none"
        ),
        target_device_serial="TEST_device",
        target_app_package="com.tiktok",
        device_path=device_path,
        device_cleanup_status=device_cleanup_status,
        object_cleanup_status=object_cleanup_status,
        spool_cleanup_status=spool_cleanup_status,
        queued_at=datetime.now(UTC),
    )


def _factory(db_session: Session):
    def factory():
        return Session(
            bind=db_session.get_bind(), join_transaction_mode="create_savepoint"
        )

    return factory


def _add_verify_attempt(
    session: Session,
    task: VideoPublishTask,
    *,
    status: str = AttemptStatus.CREATED.value,
    prompt_snapshot: str = "TEST",
    output: dict | None = None,
) -> VideoPublishAttempt:
    publish = VideoPublishAttempt(
        sequence_no=1,
        kind="publish",
        status=AttemptStatus.FAILED.value,
        artemis_session_id=uuid4(),
        prompt_version="TEST_PUBLISH",
        prompt_snapshot="TEST_PUBLISH",
        device_serial="TEST_device",
    )
    task.attempts.append(publish)
    session.add(task)
    session.flush()
    verify_attempt = VideoPublishAttempt(
        sequence_no=2,
        kind="verify",
        related_attempt_id=publish.id,
        status=status,
        artemis_session_id=uuid4(),
        prompt_version="TEST_VERIFY",
        prompt_snapshot=prompt_snapshot,
        device_serial="TEST_device",
        artemis_output=output,
    )
    task.attempts.append(verify_attempt)
    session.flush()
    return verify_attempt


def test_global_claim_and_expired_lease_takeover(db_session: Session) -> None:
    first = _task()
    second = _task()
    db_session.add_all([first, second])
    db_session.flush()

    claimed = claim_one(db_session, "worker-a")
    assert claimed is not None
    assert claimed.public_id == first.public_id
    db_session.commit()

    assert claim_one(db_session, "worker-b") is None

    first.lease_expires_at = datetime.now(UTC) - timedelta(seconds=1)
    db_session.commit()
    takeover = _lease_task(db_session, "worker-b", 30)
    assert takeover is not None
    assert takeover.public_id == first.public_id
    assert takeover.lease_owner == "worker-b"
    db_session.rollback()


def test_failed_device_cleanup_blocks_later_claim(db_session: Session) -> None:
    residue = _task(device_cleanup_status="failed")
    queued = _task()
    cleanup = _task(
        status=TaskStatus.RUNNING.value, stage=TaskStage.WAITING_ARTEMIS.value
    )
    db_session.add_all([residue, queued, cleanup])
    db_session.flush()
    assert claim_one(db_session, "worker") is None
    reclaimed = _lease_task(db_session, "worker", 30)
    assert reclaimed is not None
    assert reclaimed.public_id == cleanup.public_id
    db_session.commit()

    residue = db_session.get(VideoPublishTask, residue.id)
    cleanup = db_session.get(VideoPublishTask, cleanup.id)
    assert residue is not None and cleanup is not None
    residue.device_cleanup_status = "succeeded"
    residue.cleanup_intent = "none"
    cleanup.status = TaskStatus.SUCCEEDED.value
    cleanup.stage = TaskStage.DONE.value
    cleanup.lease_owner = None
    cleanup.lease_expires_at = None
    cleanup.heartbeat_at = None
    db_session.commit()
    assert claim_one(db_session, "worker") is not None
    db_session.rollback()


@pytest.mark.asyncio
async def test_safe_retry_requeues_and_fences_stale_worker(db_session: Session) -> None:
    task = _task()
    db_session.add(task)
    db_session.flush()
    assert claim_one(db_session, "worker-a") is not None
    db_session.commit()

    deps = cast(
        PublishDependencies,
        SimpleNamespace(
            session_factory=_factory(db_session),
            instance_id="worker-a",
            max_attempts=3,
        ),
    )
    await _safe_retry(task.public_id, "DOWNLOAD_TEMPORARY", deps)
    db_session.expire_all()
    assert task.status == TaskStatus.PENDING.value
    assert task.stage == TaskStage.QUEUED.value
    assert task.lease_owner is None
    assert task.next_attempt_at is not None

    task = _task()
    db_session.add(task)
    db_session.flush()
    assert claim_one(db_session, "worker-a") is not None
    db_session.commit()
    task.lease_owner = "worker-b"
    task.row_version += 1
    db_session.commit()
    await _safe_retry(task.public_id, "STALE", deps)
    db_session.expire_all()
    assert task.status == TaskStatus.RUNNING.value
    assert task.lease_owner == "worker-b"


class _CleanupAdb:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.calls = 0

    async def remove_staged_video(self, _serial: str, _path: str) -> None:
        self.calls += 1
        if self.fail:
            raise RuntimeError("TEST_DEVICE_BUSY")


class _CleanupStore:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.calls = 0

    def remove(self, _key: str) -> None:
        self.calls += 1
        if self.fail:
            raise RuntimeError("TEST_OBJECT_BUSY")


@pytest.mark.asyncio
async def test_success_cleanup_releases_device_gate_before_background_cleanup(
    db_session: Session,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "tts_erp_v2.publishing.dispatcher.require_destructive_script_guard",
        lambda **_kwargs: None,
    )
    task_a = _task(
        status=TaskStatus.SUCCEEDED.value,
        stage=TaskStage.DONE.value,
        device_cleanup_status="pending",
        spool_cleanup_status="pending",
        object_cleanup_status="pending",
    )
    task_a.cleanup_intent = "finalize_success"
    task_b = _task()
    db_session.add_all([task_a, task_b])
    db_session.flush()
    db_session.commit()
    adb = _CleanupAdb()
    store = _CleanupStore(fail=True)
    deps = cast(
        PublishDependencies,
        SimpleNamespace(
            session_factory=_factory(db_session),
            instance_id="success-worker",
            lease_seconds=30,
            max_attempts=3,
            adb=adb,
            store=store,
            spool_dir=tmp_path,
        ),
    )

    await _cleanup_success(task_a.public_id, deps)
    db_session.expire_all()
    assert adb.calls == 1
    assert store.calls == 0
    assert task_a.device_cleanup_status == "succeeded"
    assert task_a.spool_cleanup_status == "pending"
    assert task_a.object_cleanup_status == "pending"
    assert claim_one(db_session, "next-publish") is not None
    db_session.rollback()


def test_replace_upload_resets_object_cleanup_and_rejects_exhausted_budget(
    db_session: Session,
) -> None:
    task = _task(
        status=TaskStatus.FAILED.value,
        stage=TaskStage.DONE.value,
        object_cleanup_status="failed",
    )
    task.object_deleted_at = datetime.now(UTC)
    task.object_cleanup_error = "TEST_OBJECT_BUSY"
    task.object_cleanup_attempts = 4
    task.object_cleanup_next_attempt_at = datetime.now(UTC)
    task.cleanup_intent = "preserve_state"
    task.attempt_count = 3
    db_session.add(task)
    db_session.flush()
    with pytest.raises(ValueError, match="RETRY_BUDGET_EXHAUSTED"):
        replace_upload(db_session, task.public_id)
    db_session.rollback()

    task = _task(
        status=TaskStatus.FAILED.value,
        stage=TaskStage.DONE.value,
        object_cleanup_status="failed",
    )
    task.object_deleted_at = datetime.now(UTC)
    task.object_cleanup_error = "TEST_OBJECT_BUSY"
    task.object_cleanup_attempts = 4
    task.object_cleanup_next_attempt_at = datetime.now(UTC)
    task.cleanup_intent = "preserve_state"
    task.attempt_count = 1
    db_session.add(task)
    db_session.flush()
    replaced = replace_upload(db_session, task.public_id)
    assert replaced.stage == TaskStage.AWAITING_UPLOAD.value
    assert replaced.cleanup_intent == "none"
    assert replaced.object_cleanup_status == "not_started"
    assert replaced.object_cleanup_error is None
    assert replaced.object_cleanup_attempts == 0
    assert replaced.object_cleanup_next_attempt_at is None
    assert replaced.object_deleted_at is None
    db_session.rollback()


def test_replace_upload_api_confirm_and_claim_round_trip(
    db_session: Session,
) -> None:
    task = _task(
        status=TaskStatus.FAILED.value,
        stage=TaskStage.DONE.value,
        object_cleanup_status="failed",
    )
    task.object_deleted_at = datetime.now(UTC)
    task.cleanup_intent = "preserve_state"
    task.attempt_count = 1
    task.stage_started_at = datetime(2026, 10, 5, tzinfo=UTC)
    db_session.add(task)
    db_session.flush()
    replaced = replace_upload_task(
        task.public_id,
        ActionIn(rowVersion=task.row_version),
        _request(),
        db_session,
    )
    assert replaced["stage"] == TaskStage.AWAITING_UPLOAD.value
    assert replaced["stageStartedAt"] > datetime(2026, 10, 5, tzinfo=UTC)
    assert set(replaced["allowedActions"]) >= {"continue_upload", "cancel"}

    class UploadedStore:
        def stat(self, _key: str) -> dict:
            return {"size": task.size_bytes, "content_type": task.content_type}

    queued = confirm(
        task.public_id,
        ActionIn(rowVersion=replaced["rowVersion"]),
        _request(),
        db_session,
        cast(VideoObjectStore, UploadedStore()),
    )
    assert queued["stage"] == TaskStage.QUEUED.value
    assert queued["stageStartedAt"] >= replaced["stageStartedAt"]
    claimed = claim_one(db_session, "replacement-worker", max_attempts=3)
    assert claimed is not None
    assert claimed.public_id == task.public_id
    assert claimed.stage == TaskStage.DOWNLOADING.value
    assert claimed.stage_started_at >= queued["stageStartedAt"]
    db_session.rollback()


def test_replace_upload_conflict_is_structured_when_budget_is_exhausted(
    db_session: Session,
) -> None:
    task = _task(status=TaskStatus.FAILED.value, stage=TaskStage.DONE.value)
    task.object_deleted_at = datetime.now(UTC)
    task.attempt_count = 3
    db_session.add(task)
    db_session.flush()
    with pytest.raises(HTTPException) as exc_info:
        replace_upload_task(
            task.public_id,
            ActionIn(rowVersion=task.row_version),
            _request(),
            db_session,
        )
    assert exc_info.value.status_code == 409
    assert cast(dict, exc_info.value.detail) == {
        "code": "RETRY_BUDGET_EXHAUSTED",
        "message": "RETRY_BUDGET_EXHAUSTED",
        "rowVersion": task.row_version,
        "allowedActions": ["view"],
    }
    db_session.rollback()


def test_cleanup_retry_resources_resets_only_requested_failed_rows(
    db_session: Session,
) -> None:
    task = _task(
        status=TaskStatus.SUCCEEDED.value,
        stage=TaskStage.DONE.value,
        device_cleanup_status="failed",
        object_cleanup_status="failed",
    )
    task.spool_cleanup_status = "succeeded"
    db_session.add(task)
    db_session.flush()
    result = retry_cleanup(
        task.public_id,
        ActionIn(rowVersion=task.row_version, resources=["device"]),
        _request(),
        db_session,
    )
    assert result["cleanup"]["device"]["status"] == "pending"
    assert result["cleanup"]["object"]["status"] == "failed"
    assert result["cleanupRetryableResources"] == ["object"]
    with pytest.raises(HTTPException) as exc_info:
        retry_cleanup(
            task.public_id,
            ActionIn(rowVersion=result["rowVersion"], resources=["spool"]),
            _request(),
            db_session,
        )
    assert cast(dict, exc_info.value.detail)["code"] == (
        "CLEANUP_RESOURCE_NOT_RETRYABLE"
    )
    db_session.rollback()


@pytest.mark.asyncio
async def test_worker_automatically_retries_due_cleanup_with_backoff(
    db_session: Session,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "tts_erp_v2.publishing.dispatcher.require_destructive_script_guard",
        lambda **_kwargs: None,
    )
    task = _task(
        status=TaskStatus.SUCCEEDED.value,
        stage=TaskStage.DONE.value,
        device_cleanup_status="failed",
    )
    task.device_cleanup_next_attempt_at = datetime.now(UTC) - timedelta(seconds=1)
    db_session.add(task)
    db_session.flush()
    db_session.commit()
    adb = _CleanupAdb(fail=True)
    store = _CleanupStore()
    deps = cast(
        PublishDependencies,
        SimpleNamespace(
            session_factory=_factory(db_session),
            instance_id="cleanup-worker",
            lease_seconds=30,
            max_attempts=3,
            adb=adb,
            store=store,
            spool_dir=tmp_path,
        ),
    )
    await dispatch_one(deps)
    db_session.expire_all()
    assert adb.calls == 1
    assert task.device_cleanup_status == "failed"
    assert task.device_cleanup_attempts == 1
    next_attempt = task.device_cleanup_next_attempt_at
    assert next_attempt is not None
    assert next_attempt > datetime.now(UTC)
    assert task.lease_owner is None

    adb.fail = False
    task.device_cleanup_next_attempt_at = datetime.now(UTC) - timedelta(seconds=1)
    db_session.commit()
    await dispatch_one(deps)
    db_session.expire_all()
    assert task.device_cleanup_status == "succeeded"
    assert task.device_cleanup_next_attempt_at is None
    assert task.status == TaskStatus.SUCCEEDED.value
    assert task.stage == TaskStage.DONE.value
    assert store.calls == 0
    assert task.attempt_count == 0


@pytest.mark.asyncio
async def test_worker_automatically_retries_object_cleanup_with_backoff(
    db_session: Session,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "tts_erp_v2.publishing.dispatcher.require_destructive_script_guard",
        lambda **_kwargs: None,
    )
    task = _task(
        status=TaskStatus.SUCCEEDED.value,
        stage=TaskStage.DONE.value,
        device_cleanup_status="succeeded",
        object_cleanup_status="failed",
    )
    task.object_cleanup_next_attempt_at = datetime.now(UTC) - timedelta(seconds=1)
    db_session.add(task)
    db_session.flush()
    db_session.commit()
    adb = _CleanupAdb()
    store = _CleanupStore(fail=True)
    deps = cast(
        PublishDependencies,
        SimpleNamespace(
            session_factory=_factory(db_session),
            instance_id="cleanup-worker",
            lease_seconds=30,
            max_attempts=3,
            adb=adb,
            store=store,
            spool_dir=tmp_path,
        ),
    )
    await dispatch_one(deps)
    db_session.expire_all()
    assert store.calls == 1
    assert task.object_cleanup_attempts == 1
    assert task.object_cleanup_status == "failed"
    next_attempt = task.object_cleanup_next_attempt_at
    assert next_attempt is not None and next_attempt > datetime.now(UTC)
    store.fail = False
    task.object_cleanup_next_attempt_at = datetime.now(UTC) - timedelta(seconds=1)
    db_session.commit()
    await dispatch_one(deps)
    db_session.expire_all()
    assert task.object_cleanup_status == "succeeded"
    assert task.object_cleanup_next_attempt_at is None


@pytest.mark.asyncio
async def test_cancelled_object_cleanup_supports_auto_and_manual_retry(
    db_session: Session,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "tts_erp_v2.publishing.dispatcher.require_destructive_script_guard",
        lambda **_kwargs: None,
    )
    task = _task(
        status=TaskStatus.CANCELLED.value,
        stage=TaskStage.DONE.value,
        device_cleanup_status="succeeded",
        object_cleanup_status="failed",
        spool_cleanup_status="succeeded",
    )
    task.object_cleanup_next_attempt_at = datetime.now(UTC) - timedelta(seconds=1)
    db_session.add(task)
    db_session.flush()
    db_session.commit()
    store = _CleanupStore(fail=True)
    deps = cast(
        PublishDependencies,
        SimpleNamespace(
            session_factory=_factory(db_session),
            instance_id="cleanup-worker",
            lease_seconds=30,
            max_attempts=3,
            adb=_CleanupAdb(),
            store=store,
            spool_dir=tmp_path,
        ),
    )
    await dispatch_one(deps)
    db_session.expire_all()
    assert task.status == TaskStatus.CANCELLED.value
    assert task.object_cleanup_status == "failed"
    assert task.object_cleanup_attempts == 1
    store.fail = False
    result = retry_cleanup(
        task.public_id,
        ActionIn(rowVersion=task.row_version),
        _request(),
        db_session,
    )
    assert result["status"] == TaskStatus.CANCELLED.value
    assert result["stage"] == TaskStage.DONE.value
    assert result["cleanup"]["object"]["status"] == "pending"
    db_session.expire_all()
    assert task.status == TaskStatus.CANCELLED.value
    assert task.stage == TaskStage.DONE.value
    assert task.lease_owner is None
    assert task.object_cleanup_status == "pending"
    next_attempt = task.object_cleanup_next_attempt_at
    assert next_attempt is not None
    assert next_attempt <= datetime.now(UTC)
    dispatch_result = await dispatch_one(deps)
    db_session.expire_all()
    assert dispatch_result == "processed"
    assert task.status == TaskStatus.CANCELLED.value
    assert task.object_cleanup_status == "succeeded"


@pytest.mark.asyncio
async def test_spool_unlink_failure_persists_and_retries_with_fencing(
    db_session: Session,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "tts_erp_v2.publishing.dispatcher.require_destructive_script_guard",
        lambda **_kwargs: None,
    )
    task = _task(
        status=TaskStatus.SUCCEEDED.value,
        stage=TaskStage.DONE.value,
        device_cleanup_status="succeeded",
        object_cleanup_status="succeeded",
        spool_cleanup_status="pending",
    )
    task.spool_cleanup_next_attempt_at = datetime.now(UTC) - timedelta(seconds=1)
    db_session.add(task)
    db_session.flush()
    db_session.commit()
    deps = cast(
        PublishDependencies,
        SimpleNamespace(
            session_factory=_factory(db_session),
            instance_id="cleanup-worker",
            lease_seconds=30,
            max_attempts=3,
            adb=_CleanupAdb(),
            store=_CleanupStore(),
            spool_dir=tmp_path,
        ),
    )
    original_unlink = Path.unlink

    def fail_video_unlink(path: Path, *, missing_ok: bool = False) -> None:
        if path.name == "video.mp4":
            raise OSError("TEST_SPOOL_BUSY")
        original_unlink(path, missing_ok=missing_ok)

    monkeypatch.setattr(Path, "unlink", fail_video_unlink)
    await dispatch_one(deps)
    db_session.expire_all()
    assert task.status == TaskStatus.SUCCEEDED.value
    assert task.spool_cleanup_status == "failed"
    assert task.spool_cleanup_error == "TEST_SPOOL_BUSY"
    assert task.spool_cleanup_attempts == 1
    task.spool_cleanup_next_attempt_at = datetime.now(UTC) - timedelta(seconds=1)
    db_session.commit()
    monkeypatch.setattr(Path, "unlink", original_unlink)
    await dispatch_one(deps)
    db_session.expire_all()
    assert task.spool_cleanup_status == "succeeded"
    assert task.spool_cleanup_next_attempt_at is None


@pytest.mark.asyncio
async def test_cleanup_lease_fences_stale_worker(
    db_session: Session,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "tts_erp_v2.publishing.dispatcher.require_destructive_script_guard",
        lambda **_kwargs: None,
    )
    task = _task(
        status=TaskStatus.SUCCEEDED.value,
        stage=TaskStage.DONE.value,
        device_cleanup_status="failed",
    )
    task.device_cleanup_next_attempt_at = datetime.now(UTC) - timedelta(seconds=1)
    db_session.add(task)
    db_session.flush()
    assert (
        claim_cleanup_work(
            _factory(db_session),
            CleanupClaimRequest("worker-a", 30, "device", task.public_id),
        )
        is not None
    )
    task = db_session.get(VideoPublishTask, task.id)
    assert task is not None
    task.cleanup_lease_owner = "worker-b"
    task.row_version += 1
    db_session.commit()
    deps = cast(
        PublishDependencies,
        SimpleNamespace(
            session_factory=_factory(db_session),
            instance_id="worker-a",
            lease_seconds=30,
            max_attempts=3,
            adb=_CleanupAdb(),
            store=_CleanupStore(),
            spool_dir=tmp_path,
        ),
    )
    await _cleanup_success(task.public_id, deps)
    db_session.expire_all()
    assert task.cleanup_lease_owner == "worker-b"
    assert task.device_cleanup_status == "failed"


def test_continue_upload_reuses_awaiting_upload_ticket(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ARTEMIS_DEVICE_SERIAL", "TEST_device")
    monkeypatch.setenv("TIKTOK_PUBLISH_MINIO_BUCKET", "tiktok-video")

    class UploadStore:
        bucket = "tiktok-video"

        def presign_put(self, key: str, content_type: str) -> str:
            return f"https://upload.test/{key}"

    request_id = uuid4()
    task = _task(stage=TaskStage.AWAITING_UPLOAD.value)
    task.client_request_id = request_id
    db_session.add(task)
    db_session.flush()
    replayed, url, replay = create_upload_ticket(
        db_session,
        CreateCommand(
            client_request_id=request_id,
            filename=task.original_filename,
            content_type="video/mp4",
            size_bytes=task.size_bytes,
            caption=task.caption,
            actor_user_id=None,
        ),
        cast(VideoObjectStore, UploadStore()),
    )
    assert replayed.public_id == task.public_id
    assert replay is True
    assert url.endswith(task.object_key)


def test_upload_url_illegal_state_uses_structured_conflict(
    db_session: Session,
) -> None:
    task = _task(status=TaskStatus.PENDING.value, stage=TaskStage.QUEUED.value)
    db_session.add(task)
    db_session.flush()
    with pytest.raises(HTTPException) as exc_info:
        refresh_upload_url(
            task.public_id,
            _request(),
            db_session,
            cast(VideoObjectStore, SimpleNamespace()),
        )
    assert exc_info.value.status_code == 409
    assert cast(dict, exc_info.value.detail) == {
        "code": "TASK_ACTION_NOT_ALLOWED",
        "message": "TASK_ACTION_NOT_ALLOWED",
        "rowVersion": task.row_version,
        "allowedActions": ["view", "cancel"],
    }


def test_confirm_head_then_cas_detects_concurrent_task_update(
    db_session: Session,
) -> None:
    task = _task(stage=TaskStage.AWAITING_UPLOAD.value)
    db_session.add(task)
    db_session.commit()
    expected_version = task.row_version

    class RacingStore:
        def stat(self, _key: str) -> dict:
            with Session(bind=db_session.get_bind()) as other:
                # pi-lens-ignore: python-sql-injection
                other.execute(
                    text(
                        "UPDATE publishing.video_publish_tasks "
                        "SET row_version=row_version+1 WHERE id=:id"
                    ),
                    {"id": task.id},
                )
                other.commit()
            return {"size": task.size_bytes, "content_type": task.content_type}

    with pytest.raises(HTTPException) as exc_info:
        confirm(
            task.public_id,
            ActionIn(rowVersion=expected_version),
            _request(),
            db_session,
            cast(VideoObjectStore, RacingStore()),
        )
    assert exc_info.value.status_code == 409
    assert cast(dict, exc_info.value.detail)["code"] == "TASK_VERSION_CONFLICT"
    assert cast(dict, exc_info.value.detail)["rowVersion"] == expected_version + 1


def test_create_declared_oversize_is_413_and_caption_limit_is_configured(
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ARTEMIS_DEVICE_SERIAL", "TEST_device")
    monkeypatch.setenv("TIKTOK_PUBLISH_MINIO_BUCKET", "tiktok-video")
    monkeypatch.setenv("TIKTOK_PUBLISH_MAX_VIDEO_BYTES", "10")
    monkeypatch.setenv("TIKTOK_PUBLISH_MAX_CAPTION_CHARS", "5000")

    class Store:
        bucket = "tiktok-video"

        def presign_put(self, key: str, _content_type: str) -> str:
            return f"https://upload.test/{key}"

    with pytest.raises(HTTPException) as exc_info:
        create_task(
            CreateIn(
                clientRequestId=uuid4(),
                filename="TEST.mp4",
                contentType="video/mp4",
                sizeBytes=11,
                caption="TEST",
            ),
            _request(role="readwrite"),
            db_session,
            cast(VideoObjectStore, Store()),
            Response(),
        )
    assert exc_info.value.status_code == 413
    created = create_task(
        CreateIn(
            clientRequestId=uuid4(),
            filename="TEST.mp4",
            contentType="video/mp4",
            sizeBytes=4,
            caption="x" * 4500,
        ),
        _request(role="readwrite"),
        db_session,
        cast(VideoObjectStore, Store()),
        Response(),
    )
    assert len(created["caption"]) == 4500


def test_confirm_rejects_stale_row_version(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ARTEMIS_DEVICE_SERIAL", "TEST_device")
    monkeypatch.setenv("TIKTOK_PUBLISH_MINIO_BUCKET", "tiktok-video")

    class UploadStore:
        bucket = "tiktok-video"

        def stat(self, key: str) -> dict:
            raise AssertionError("stale version must not inspect MinIO")

    task = _task(stage=TaskStage.AWAITING_UPLOAD.value, created_by_user_id=1)
    db_session.add(task)
    db_session.flush()
    task.row_version = 2
    request = _request(role="readwrite", user_id=1)
    with pytest.raises(HTTPException) as exc_info:
        confirm(
            task.public_id,
            ActionIn(rowVersion=1),
            request,
            db_session,
            cast(VideoObjectStore, UploadStore()),
        )
    assert exc_info.value.status_code == 409
    assert cast(dict, exc_info.value.detail)["code"] == "TASK_VERSION_CONFLICT"
    db_session.rollback()


def test_api_key_owner_can_list_detail_and_replay(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ARTEMIS_DEVICE_SERIAL", "TEST_device")
    monkeypatch.setenv("TIKTOK_PUBLISH_MINIO_BUCKET", "tiktok-video")
    key_a = "a" * 64

    class UploadStore:
        bucket = "tiktok-video"

        def presign_put(self, key: str, content_type: str) -> str:
            return f"https://upload.test/{key}"

    task = _task(
        stage=TaskStage.AWAITING_UPLOAD.value,
        created_by_key_hash=key_a,
    )
    db_session.add(task)
    db_session.flush()
    request = _request(role="readwrite", key_hash=key_a)
    listed = cast(
        dict,
        list_tasks(
            request,
            db_session,
            Response(),
            status_filter=None,
            limit=30,
            cursor=None,
        ),
    )
    assert [item["taskId"] for item in listed["items"]] == [str(task.public_id)]
    assert listed["items"][0]["clientRequestId"] == str(task.client_request_id)
    viewed = cast(
        dict,
        detail(
            task.public_id,
            request,
            db_session,
            Response(),
            include_diagnostics=False,
        ),
    )
    assert viewed["taskId"] == str(task.public_id)
    replayed = create_task(
        CreateIn(
            clientRequestId=task.client_request_id,
            filename=task.original_filename,
            contentType=task.content_type,
            sizeBytes=task.size_bytes,
            caption=task.caption,
        ),
        request,
        db_session,
        cast(VideoObjectStore, UploadStore()),
        Response(),
    )
    assert replayed["idempotentReplay"] is True
    assert replayed["taskId"] == str(task.public_id)


def test_api_key_b_cannot_list_detail_or_replay_key_a_task(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ARTEMIS_DEVICE_SERIAL", "TEST_device")
    monkeypatch.setenv("TIKTOK_PUBLISH_MINIO_BUCKET", "tiktok-video")
    key_a = "a" * 64
    key_b = "b" * 64

    class UploadStore:
        bucket = "tiktok-video"
        presign_calls = 0

        def presign_put(self, key: str, content_type: str) -> str:
            self.presign_calls += 1
            return f"https://upload.test/{key}"

    task = _task(
        stage=TaskStage.AWAITING_UPLOAD.value,
        created_by_key_hash=key_a,
    )
    db_session.add(task)
    db_session.flush()
    request = _request(role="readwrite", key_hash=key_b)
    listed = cast(
        dict,
        list_tasks(
            request,
            db_session,
            Response(),
            status_filter=None,
            limit=30,
            cursor=None,
        ),
    )
    assert listed["items"] == []
    with pytest.raises(HTTPException) as detail_error:
        detail(
            task.public_id,
            request,
            db_session,
            Response(),
            include_diagnostics=False,
        )
    assert detail_error.value.status_code == 404
    assert detail_error.value.detail == "TASK_NOT_FOUND"
    session_request = _request(
        role="readwrite",
        user_id=2,
        grant=AccessGrant(mode=AuthMode.ENFORCE, role=Role.READWRITE),
    )
    with pytest.raises(HTTPException) as session_error:
        detail(
            task.public_id,
            session_request,
            db_session,
            Response(),
            include_diagnostics=False,
        )
    assert session_error.value.status_code == 404
    assert session_error.value.detail == "TASK_NOT_FOUND"
    store = UploadStore()
    with pytest.raises(HTTPException) as replay_error:
        create_task(
            CreateIn(
                clientRequestId=task.client_request_id,
                filename=task.original_filename,
                contentType=task.content_type,
                sizeBytes=task.size_bytes,
                caption=task.caption,
            ),
            request,
            db_session,
            cast(VideoObjectStore, store),
            Response(),
        )
    assert replay_error.value.status_code == 404
    assert replay_error.value.detail == "TASK_NOT_FOUND"
    assert store.presign_calls == 0


@pytest.mark.parametrize(
    "privileged_grant",
    [
        AccessGrant(mode=AuthMode.ENFORCE, role=Role.ADMIN, key_hash="admin"),
        AccessGrant(mode=AuthMode.OFF, bypass=True),
    ],
    ids=["admin-role", "explicit-bypass"],
)
def test_admin_or_bypass_can_view_and_operate_cross_owner_task(
    db_session: Session,
    privileged_grant: AccessGrant,
) -> None:
    task = _task(
        status=TaskStatus.FAILED.value,
        created_by_key_hash="a" * 64,
    )
    task.attempt_count = 1
    task.object_uploaded_at = datetime.now(UTC)
    task.attempts.append(
        VideoPublishAttempt(
            sequence_no=1,
            kind="publish",
            artemis_session_id=uuid4(),
            prompt_version="TEST",
            prompt_snapshot="TEST",
            device_serial="TEST_device",
            retry_safe=True,
        )
    )
    db_session.add(task)
    db_session.flush()
    request = _request(
        role="readwrite",
        key_hash="admin" * 16,
        grant=privileged_grant,
    )
    viewed = cast(
        dict,
        detail(
            task.public_id,
            request,
            db_session,
            Response(),
            include_diagnostics=False,
        ),
    )
    assert viewed["taskId"] == str(task.public_id)

    class RetryStore:
        def stat(self, _key: str) -> dict:
            return {"size": task.size_bytes, "content_type": task.content_type}

    operated = retry(
        task.public_id,
        ActionIn(rowVersion=task.row_version),
        request,
        db_session,
        cast(VideoObjectStore, RetryStore()),
    )
    assert operated["taskId"] == str(task.public_id)
    assert operated["status"] == TaskStatus.PENDING.value
    assert operated["stage"] == TaskStage.QUEUED.value


def test_retry_rejects_missing_object_even_when_attempt_is_safe(
    db_session: Session,
) -> None:
    task = _task(status=TaskStatus.FAILED.value)
    task.attempt_count = 1
    task.object_uploaded_at = datetime.now(UTC)
    task.attempts.append(
        VideoPublishAttempt(
            sequence_no=1,
            kind="publish",
            artemis_session_id=uuid4(),
            prompt_version="TEST",
            prompt_snapshot="TEST",
            device_serial="TEST_device",
            retry_safe=True,
        )
    )
    db_session.add(task)
    db_session.flush()

    class MissingStore:
        def stat(self, _key: str) -> dict:
            raise ObjectNotFound("TEST_missing")

    with pytest.raises(ValueError, match="UPLOAD_REPLACEMENT_REQUIRED"):
        retry_task(db_session, task.public_id, cast(VideoObjectStore, MissingStore()))
    db_session.rollback()


def test_retry_preserves_retry_when_object_store_is_transiently_unavailable(
    db_session: Session,
) -> None:
    task = _task(status=TaskStatus.FAILED.value)
    task.attempt_count = 1
    task.object_uploaded_at = datetime.now(UTC)
    task.attempts.append(
        VideoPublishAttempt(
            sequence_no=1,
            kind="publish",
            artemis_session_id=uuid4(),
            prompt_version="TEST",
            prompt_snapshot="TEST",
            device_serial="TEST_device",
            retry_safe=True,
        )
    )
    db_session.add(task)
    db_session.flush()

    class UnavailableStore:
        def stat(self, _key: str) -> dict:
            raise TimeoutError("TEST_store_timeout")

    with pytest.raises(ValueError, match="OBJECT_STORE_UNAVAILABLE"):
        retry_task(
            db_session, task.public_id, cast(VideoObjectStore, UnavailableStore())
        )
    db_session.refresh(task)
    assert task.object_deleted_at is None
    db_session.rollback()


def test_upload_ticket_replay_rejects_cross_user_owner(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ARTEMIS_DEVICE_SERIAL", "TEST_device")
    monkeypatch.setenv("TIKTOK_PUBLISH_MINIO_BUCKET", "tiktok-video")

    class UploadStore:
        bucket = "tiktok-video"

        def presign_put(self, key: str, content_type: str) -> str:
            return f"https://upload.test/{key}"

    task = _task(stage=TaskStage.AWAITING_UPLOAD.value, created_by_user_id=1)
    db_session.add(task)
    db_session.flush()
    with pytest.raises(PermissionError, match="TASK_NOT_FOUND"):
        create_upload_ticket(
            db_session,
            CreateCommand(
                client_request_id=task.client_request_id,
                filename=task.original_filename,
                content_type=task.content_type,
                size_bytes=task.size_bytes,
                caption=task.caption,
                actor_user_id=2,
            ),
            cast(VideoObjectStore, UploadStore()),
        )


def test_api_upload_replay_hides_cross_user_task(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ARTEMIS_DEVICE_SERIAL", "TEST_device")
    monkeypatch.setenv("TIKTOK_PUBLISH_MINIO_BUCKET", "tiktok-video")

    class UploadStore:
        bucket = "tiktok-video"

        def presign_put(self, key: str, content_type: str) -> str:
            return f"https://upload.test/{key}"

    task = _task(stage=TaskStage.AWAITING_UPLOAD.value, created_by_user_id=1)
    db_session.add(task)
    db_session.flush()
    with pytest.raises(HTTPException) as exc_info:
        create_task(
            CreateIn(
                clientRequestId=task.client_request_id,
                filename=task.original_filename,
                contentType=task.content_type,
                sizeBytes=task.size_bytes,
                caption=task.caption,
            ),
            _request(role="readwrite", user_id=2),
            db_session,
            cast(VideoObjectStore, UploadStore()),
            Response(),
        )
    assert exc_info.value.status_code == 404
    assert exc_info.value.detail == "TASK_NOT_FOUND"


def test_filtered_task_list_exposes_unfiltered_poll_state(db_session: Session) -> None:
    running = _task(status=TaskStatus.RUNNING.value, stage=TaskStage.DOWNLOADING.value)
    queued = _task(status=TaskStatus.PENDING.value, stage=TaskStage.QUEUED.value)
    succeeded = _task(status=TaskStatus.SUCCEEDED.value, stage=TaskStage.DONE.value)
    db_session.add_all([running, queued, succeeded])
    db_session.flush()
    payload = cast(
        dict,
        list_tasks(
            _request(),
            db_session,
            Response(),
            status_filter=TaskStatus.SUCCEEDED.value,
            limit=30,
            cursor=None,
        ),
    )
    assert len(payload["items"]) == 1
    assert payload["items"][0]["status"] == TaskStatus.SUCCEEDED.value
    assert payload["pollState"] == {
        "running": True,
        "cleaning": False,
        "queued": True,
    }


def test_current_exposes_device_cleaning_but_not_background_cleanup(
    db_session: Session,
) -> None:
    device_cleanup = _task(
        status=TaskStatus.SUCCEEDED.value,
        stage=TaskStage.DONE.value,
        device_cleanup_status="pending",
    )
    device_cleanup.stage_started_at = datetime.now(UTC)
    background_cleanup = _task(
        status=TaskStatus.SUCCEEDED.value,
        stage=TaskStage.DONE.value,
        device_cleanup_status="succeeded",
        object_cleanup_status="failed",
    )
    db_session.add_all([device_cleanup, background_cleanup])
    db_session.flush()
    payload = cast(dict, current(_request(), db_session, Response()))
    assert payload["task"]["taskId"] == str(device_cleanup.public_id)
    assert payload["task"]["operationalStage"] == "cleaning"
    assert payload["pollState"] == {
        "running": False,
        "cleaning": True,
        "queued": False,
    }
    db_session.rollback()


def test_task_list_and_detail_are_owner_scoped(
    db_session: Session,
) -> None:
    owned = _task(created_by_user_id=1)
    other = _task(created_by_user_id=2)
    db_session.add_all([owned, other])
    db_session.flush()
    payload = cast(
        dict,
        list_tasks(
            _request(role="readwrite", user_id=1),
            db_session,
            Response(),
            status_filter=None,
            limit=30,
            cursor=None,
        ),
    )
    assert [item["taskId"] for item in payload["items"]] == [str(owned.public_id)]
    assert payload["items"][0]["clientRequestId"] == str(owned.client_request_id)
    with pytest.raises(HTTPException) as exc_info:
        detail(
            other.public_id,
            _request(role="readwrite", user_id=1),
            db_session,
            Response(),
            include_diagnostics=False,
        )
    assert exc_info.value.status_code == 404
    assert exc_info.value.detail == "TASK_NOT_FOUND"


def test_awaiting_upload_is_not_reported_as_queued_poll_work(
    db_session: Session,
) -> None:
    draft = _task(stage=TaskStage.AWAITING_UPLOAD.value)
    db_session.add(draft)
    db_session.flush()
    payload = cast(
        dict,
        list_tasks(
            _request(),
            db_session,
            Response(),
            status_filter=TaskStatus.PENDING.value,
            limit=30,
            cursor=None,
        ),
    )
    assert payload["pollState"] == {
        "running": False,
        "cleaning": False,
        "queued": False,
    }


def _request(
    *,
    role: str = "admin",
    user_id: int | None = None,
    key_hash: str | None = None,
    grant: AccessGrant | None = None,
) -> Request:
    scope: dict[str, object] = {
        "type": "http",
        "method": "POST",
        "path": "/v2/video-publish/tasks/cleanup/retry",
        "headers": [],
        "api_key_role": role,
    }
    if user_id is not None:
        scope["user_id"] = user_id
    if key_hash is not None:
        scope["api_key_hash"] = key_hash
    if grant is not None:
        scope["access_grant"] = grant
    return Request(scope)


@pytest.mark.asyncio
async def test_state_owner_repair_red_cases(
    db_session: Session,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Executable RED coverage for the owner-level cleanup boundary."""
    monkeypatch.setattr(
        "tts_erp_v2.publishing.dispatcher.require_destructive_script_guard",
        lambda **_kwargs: None,
    )
    task = _task()
    db_session.add(task)
    db_session.flush()
    assert claim_one(db_session, "owner") is not None
    task.stage = TaskStage.STAGING_DEVICE.value
    db_session.commit()
    deps = cast(
        PublishDependencies,
        SimpleNamespace(
            session_factory=_factory(db_session),
            instance_id="owner",
            lease_seconds=30,
            max_attempts=3,
            adb=_CleanupAdb(),
            store=_CleanupStore(),
            spool_dir=tmp_path,
        ),
    )
    await _safe_retry(task.public_id, "TEST_SAFE", deps, stage=TaskStage.QUEUED.value)
    db_session.expire_all()
    assert task.status == TaskStatus.PENDING.value
    assert task.stage == TaskStage.WAITING_DEVICE.value
    assert getattr(task, "cleanup_intent", None) == "requeue_publish"
    assert await dispatch_one(deps) == "processed"
    db_session.expire_all()
    assert task.object_cleanup_status == "not_started"


@pytest.mark.asyncio
async def test_verify_not_published_cleans_device_before_requeue(
    db_session: Session,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "tts_erp_v2.publishing.dispatcher.require_destructive_script_guard",
        lambda **_kwargs: None,
    )

    class VerifyArtemis:
        async def submit(self, **kwargs):
            return ArtemisResult(
                kwargs["session_id"], "success", output={"verdict": "not_published"}
            )

    task = _task(status=TaskStatus.RUNNING.value, stage=TaskStage.VERIFYING.value)
    task.lease_owner = "verify-worker"
    task.lease_expires_at = datetime.now(UTC) + timedelta(minutes=1)
    verify_attempt = _add_verify_attempt(db_session, task)
    db_session.commit()
    deps = cast(
        PublishDependencies,
        SimpleNamespace(
            session_factory=_factory(db_session),
            instance_id="verify-worker",
            lease_seconds=30,
            max_attempts=3,
            adb=_CleanupAdb(),
            store=_CleanupStore(),
            artemis=VerifyArtemis(),
            spool_dir=tmp_path,
        ),
    )
    await _run_attempt(task.public_id, verify_attempt.id, deps)
    db_session.expire_all()
    assert task.status == TaskStatus.PENDING.value
    assert task.stage == TaskStage.WAITING_DEVICE.value
    assert task.cleanup_intent == "requeue_publish"
    assert await dispatch_one(deps) == "processed"
    db_session.expire_all()
    assert task.stage == TaskStage.QUEUED.value
    assert task.device_cleanup_status == "succeeded"
    assert task.object_cleanup_status == "not_started"


@pytest.mark.asyncio
async def test_verify_published_is_terminal_and_not_reclaimable(
    db_session: Session,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "tts_erp_v2.publishing.dispatcher.require_destructive_script_guard",
        lambda **_kwargs: None,
    )

    class PublishedVerify:
        async def submit(self, **kwargs):
            return ArtemisResult(
                kwargs["session_id"], "success", output={"verdict": "published"}
            )

    task = _task(status=TaskStatus.RUNNING.value, stage=TaskStage.VERIFYING.value)
    task.lease_owner = "verify-worker"
    task.lease_expires_at = datetime.now(UTC) + timedelta(minutes=1)
    verify_attempt = _add_verify_attempt(db_session, task)
    db_session.commit()
    deps = cast(
        PublishDependencies,
        SimpleNamespace(
            session_factory=_factory(db_session),
            instance_id="verify-worker",
            lease_seconds=30,
            max_attempts=3,
            adb=_CleanupAdb(),
            store=_CleanupStore(),
            artemis=PublishedVerify(),
            spool_dir=tmp_path,
        ),
    )
    await _run_attempt(task.public_id, verify_attempt.id, deps)
    db_session.expire_all()
    assert task.status == TaskStatus.SUCCEEDED.value
    assert task.stage == TaskStage.DONE.value
    assert task.lease_owner is None
    assert task.cleanup_intent == "finalize_success"
    assert await dispatch_one(deps) == "processed"
    assert await dispatch_one(deps) == "no_task"


@pytest.mark.asyncio
async def test_queued_worker_reaches_staging_and_terminal_success(
    db_session: Session,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "tts_erp_v2.publishing.dispatcher.require_destructive_script_guard",
        lambda **_kwargs: None,
    )

    class Store:
        def download(self, _key: str, path: Path) -> str:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"TEST")
            return "sha256-test"

        def remove(self, _key: str) -> None:
            pass

    class Adb:
        def __init__(self) -> None:
            self.cleanup_registered_before_stage = False

        def device_path(self, _task_id: UUID) -> str:
            return "/sdcard/TEST/video.mp4"

        async def check_device(self, _serial: str) -> None:
            pass

        async def check_package(self, _serial: str, _package: str) -> None:
            pass

        async def stage_video(self, _serial: str, _local: Path, _device: str) -> None:
            with _factory(db_session)() as check_session:
                row = check_session.execute(
                    select(
                        VideoPublishTask.device_cleanup_status,
                        VideoPublishTask.spool_cleanup_status,
                    ).where(VideoPublishTask.public_id == task.public_id)
                ).one()
            self.cleanup_registered_before_stage = row == ("pending", "pending")

        async def verify_media_visible(self, _serial: str, _device: str) -> None:
            pass

        async def remove_staged_video(self, _serial: str, _device: str) -> None:
            pass

    class Artemis:
        async def submit(self, **kwargs):
            return ArtemisResult(kwargs["session_id"], "success")

    task = _task()
    task.object_uploaded_at = datetime.now(UTC)
    db_session.add(task)
    db_session.flush()
    db_session.commit()
    adb = Adb()
    deps = cast(
        PublishDependencies,
        SimpleNamespace(
            session_factory=_factory(db_session),
            instance_id="pipeline-worker",
            lease_seconds=30,
            max_attempts=3,
            adb=adb,
            store=Store(),
            artemis=Artemis(),
            spool_dir=tmp_path,
        ),
    )
    assert await dispatch_one(deps) == "processed"
    db_session.expire_all()
    assert task.status == TaskStatus.SUCCEEDED.value
    assert task.stage == TaskStage.DONE.value
    assert adb.cleanup_registered_before_stage is True
    assert await dispatch_one(deps) == "processed"
    assert await dispatch_one(deps) == "no_task"


def test_selector_priority_gates_device_but_not_background_cleanup(
    db_session: Session,
) -> None:
    device = _task(
        status=TaskStatus.SUCCEEDED.value,
        stage=TaskStage.DONE.value,
        device_cleanup_status="failed",
    )
    background = _task(
        status=TaskStatus.SUCCEEDED.value,
        stage=TaskStage.DONE.value,
        device_cleanup_status="succeeded",
        object_cleanup_status="failed",
    )
    queued = _task()
    db_session.add_all([device, background, queued])
    db_session.flush()
    claimed = claim_cleanup_work(
        _factory(db_session), CleanupClaimRequest("selector", 30, "device")
    )
    assert claimed is not None and claimed.task_id == device.public_id
    device = db_session.get(VideoPublishTask, device.id)
    assert device is not None
    device.device_cleanup_status = "succeeded"
    device.cleanup_intent = "none"
    device.cleanup_lease_owner = None
    device.cleanup_lease_expires_at = None
    db_session.commit()
    assert claim_one(db_session, "publisher") is not None
    db_session.rollback()


@pytest.mark.asyncio
async def test_terminal_safe_retry_preserves_state_and_cleans_device(
    db_session: Session,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "tts_erp_v2.publishing.dispatcher.require_destructive_script_guard",
        lambda **_kwargs: None,
    )
    task = _task()
    db_session.add(task)
    db_session.flush()
    assert claim_one(db_session, "terminal-worker", max_attempts=1) is not None
    task.stage = TaskStage.STAGING_DEVICE.value
    task.attempt_count = 1
    db_session.commit()
    deps = cast(
        PublishDependencies,
        SimpleNamespace(
            session_factory=_factory(db_session),
            instance_id="terminal-worker",
            lease_seconds=30,
            max_attempts=1,
            adb=_CleanupAdb(),
            store=_CleanupStore(),
            spool_dir=tmp_path,
        ),
    )
    await _safe_retry(task.public_id, "BUDGET_EXHAUSTED", deps)
    db_session.expire_all()
    assert task.status == TaskStatus.FAILED.value
    assert task.stage == TaskStage.DONE.value
    assert task.cleanup_intent == "preserve_state"
    assert task.device_cleanup_status == "pending"
    assert await dispatch_one(deps) == "processed"
    db_session.expire_all()
    assert task.device_cleanup_status == "succeeded"
    assert task.object_cleanup_status == "not_started"


@pytest.mark.asyncio
async def test_pre_device_spool_failure_is_retryable(
    db_session: Session,
    tmp_path: Path,
) -> None:
    task = _task()
    db_session.add(task)
    db_session.flush()
    assert claim_one(db_session, "spool-worker") is not None
    db_session.commit()
    deps = cast(
        PublishDependencies,
        SimpleNamespace(
            session_factory=_factory(db_session),
            instance_id="spool-worker",
            lease_seconds=30,
            max_attempts=3,
            adb=_CleanupAdb(),
            store=_CleanupStore(),
            spool_dir=tmp_path,
        ),
    )
    await _safe_retry(task.public_id, "DOWNLOAD_FAILED", deps)
    db_session.expire_all()
    assert task.spool_cleanup_status == "not_started"
    assert task.cleanup_intent == "none"
    assert task.stage == TaskStage.QUEUED.value


@pytest.mark.parametrize(
    ("status", "stage", "event", "expected_status", "expected_stage", "intent"),
    [
        ("pending", "queued", "CLAIM", "running", "downloading", "none"),
        (
            "running",
            "waiting_artemis",
            "ARTEMIS_SUCCESS",
            "succeeded",
            "done",
            "finalize_success",
        ),
        (
            "running",
            "verifying",
            "VERIFY_NOT_PUBLISHED",
            "pending",
            "waiting_device",
            "requeue_publish",
        ),
        (
            "running",
            "staging_device",
            "SAFE_RETRY_EXHAUSTED",
            "failed",
            "done",
            "preserve_state",
        ),
    ],
)
def test_domain_transition_table(
    status: str,
    stage: str,
    event: str,
    expected_status: str,
    expected_stage: str,
    intent: str,
) -> None:
    task = _task(status=status, stage=stage)
    transition_task(task, event)
    assert (task.status, task.stage, task.cleanup_intent) == (
        expected_status,
        expected_stage,
        intent,
    )


def test_database_rejects_pending_cleanup_with_none_intent(
    db_session: Session,
) -> None:
    task = _task(status=TaskStatus.SUCCEEDED.value, stage=TaskStage.DONE.value)
    task.cleanup_intent = "none"
    task.device_cleanup_status = "failed"
    db_session.add(task)
    with pytest.raises(IntegrityError):
        db_session.flush()
    db_session.rollback()


def test_database_rejects_illegal_business_and_cleanup_combinations(
    db_session: Session,
) -> None:
    task = _task(status=TaskStatus.PENDING.value, stage=TaskStage.QUEUED.value)
    db_session.add(task)
    db_session.flush()
    with pytest.raises(IntegrityError):
        db_session.execute(
            text(
                "UPDATE publishing.video_publish_tasks SET stage='cleaning' WHERE id=:id"
            ),
            {"id": task.id},
        )
    db_session.rollback()
    task = _task(status=TaskStatus.PENDING.value, stage=TaskStage.WAITING_DEVICE.value)
    task.cleanup_intent = "requeue_publish"
    task.object_cleanup_status = "pending"
    db_session.add(task)
    with pytest.raises(IntegrityError):
        db_session.flush()
    db_session.rollback()


@pytest.mark.asyncio
async def test_cleanup_takeover_during_external_call_fences_final_write(
    db_session: Session,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "tts_erp_v2.publishing.dispatcher.require_destructive_script_guard",
        lambda **_kwargs: None,
    )
    task = _task(
        status=TaskStatus.SUCCEEDED.value,
        stage=TaskStage.DONE.value,
        device_cleanup_status="failed",
    )
    task.device_cleanup_next_attempt_at = datetime.now(UTC) - timedelta(seconds=1)
    db_session.add(task)
    db_session.flush()
    work = claim_cleanup_work(
        _factory(db_session),
        CleanupClaimRequest("worker-a", 30, "device", task.public_id),
    )
    assert work is not None
    task = db_session.get(VideoPublishTask, task.id)
    assert task is not None

    class TakeoverAdb(_CleanupAdb):
        async def remove_staged_video(self, _serial: str, _path: str) -> None:
            task.cleanup_lease_owner = "worker-b"
            task.cleanup_lease_expires_at = datetime.now(UTC) + timedelta(minutes=1)
            task.row_version += 1
            db_session.commit()

    deps = cast(
        PublishDependencies,
        SimpleNamespace(
            session_factory=_factory(db_session),
            instance_id="worker-a",
            lease_seconds=30,
            max_attempts=3,
            adb=TakeoverAdb(),
            store=_CleanupStore(),
            spool_dir=tmp_path,
        ),
    )
    with pytest.raises(LeaseLost):
        await _execute_cleanup(work, deps)
    db_session.expire_all()
    assert task.cleanup_lease_owner == "worker-b"
    assert task.device_cleanup_status == "failed"


@pytest.mark.asyncio
async def test_live_success_is_terminal_and_not_reclaimable(
    db_session: Session,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "tts_erp_v2.publishing.dispatcher.require_destructive_script_guard",
        lambda **_kwargs: None,
    )

    class SuccessArtemis:
        async def submit(self, **kwargs):
            return ArtemisResult(kwargs["session_id"], "success")

    task = _task(
        status=TaskStatus.RUNNING.value, stage=TaskStage.DISPATCHING_ARTEMIS.value
    )
    task.lease_owner = "publish-worker"
    task.lease_expires_at = datetime.now(UTC) + timedelta(minutes=1)
    task.attempts.append(
        VideoPublishAttempt(
            sequence_no=1,
            kind="publish",
            status=AttemptStatus.CREATED.value,
            artemis_session_id=uuid4(),
            prompt_version="TEST",
            prompt_snapshot="TEST",
            device_serial="TEST_device",
        )
    )
    db_session.add(task)
    db_session.flush()
    db_session.commit()
    deps = cast(
        PublishDependencies,
        SimpleNamespace(
            session_factory=_factory(db_session),
            instance_id="publish-worker",
            lease_seconds=30,
            max_attempts=3,
            adb=_CleanupAdb(),
            store=_CleanupStore(),
            artemis=SuccessArtemis(),
            spool_dir=tmp_path,
        ),
    )
    await _run_attempt(task.public_id, task.attempts[0].id, deps)
    db_session.expire_all()
    assert task.status == TaskStatus.SUCCEEDED.value
    assert task.stage == TaskStage.DONE.value
    assert task.cleanup_intent == "finalize_success"
    assert task.attempts[0].started_at is not None
    assert task.attempts[0].submitted_at is not None
    assert task.attempts[0].last_polled_at is not None
    assert task.lease_owner is None
    assert await dispatch_one(deps) == "processed"
    assert await dispatch_one(deps) == "no_task"


def test_cleanup_retry_calls_http_destructive_guard_before_mutation(
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    task = _task(
        status=TaskStatus.SUCCEEDED.value,
        stage=TaskStage.DONE.value,
        device_cleanup_status="failed",
    )
    db_session.add(task)
    db_session.flush()
    calls: list[str] = []
    monkeypatch.setattr(
        "tts_erp_v2.api.v2.video_publish.require_destructive_guard",
        lambda _request, *, op_name: calls.append(op_name),
    )
    retry_cleanup(
        task.public_id,
        ActionIn(rowVersion=task.row_version, resources=["device"]),
        _request(),
        db_session,
    )
    assert calls == ["video_publish.retry_cleanup"]


def test_cleanup_retry_rejects_live_lease_and_requeues_expired_lease(
    db_session: Session,
) -> None:
    task = _task(
        status=TaskStatus.SUCCEEDED.value,
        stage=TaskStage.DONE.value,
        device_cleanup_status="failed",
    )
    task.cleanup_lease_owner = "auto-worker"
    task.cleanup_lease_expires_at = datetime.now(UTC) + timedelta(minutes=1)
    db_session.add(task)
    db_session.flush()
    db_session.commit()
    with pytest.raises(HTTPException) as exc_info:
        retry_cleanup(
            task.public_id,
            ActionIn(rowVersion=task.row_version),
            _request(),
            db_session,
        )
    assert cast(dict, exc_info.value.detail)["code"] == "CLEANUP_LEASE_BUSY"
    db_session.rollback()

    task = db_session.get(VideoPublishTask, task.id)
    assert task is not None
    task.cleanup_lease_expires_at = datetime.now(UTC) - timedelta(seconds=1)
    db_session.commit()
    result = retry_cleanup(
        task.public_id,
        ActionIn(rowVersion=task.row_version),
        _request(),
        db_session,
    )
    assert result["status"] == TaskStatus.SUCCEEDED.value
    assert result["stage"] == TaskStage.DONE.value
    assert result["cleanup"]["device"]["status"] == "pending"
    assert task.lease_owner is None


def test_diagnostics_are_admin_only_and_redacted_by_default(
    db_session: Session,
) -> None:
    task = _task()
    _add_verify_attempt(
        db_session,
        task,
        status=AttemptStatus.SUCCESS.value,
        prompt_snapshot="SECRET_VERIFY_PROMPT",
        output={"verdict": "published"},
    )
    publish_attempt = min(task.attempts, key=lambda attempt: attempt.sequence_no)
    publish_attempt.status = AttemptStatus.SUCCESS.value
    publish_attempt.prompt_snapshot = "SECRET_PROMPT"
    publish_attempt.artemis_output = {"secret": "SECRET_OUTPUT"}
    db_session.flush()
    with pytest.raises(HTTPException) as denied:
        detail(
            task.public_id,
            _request(role="readwrite"),
            db_session,
            Response(),
            include_diagnostics=True,
        )
    assert denied.value.status_code == 403
    normal = cast(
        dict,
        detail(
            task.public_id,
            _request(role="admin"),
            db_session,
            Response(),
            include_diagnostics=False,
        ),
    )
    assert "promptSnapshot" not in normal["attempts"][0]
    assert normal["publishAttemptCount"] == 1
    assert normal["verifyAttemptCount"] == 1
    assert normal["currentAttempt"]["kind"] == "verify"
    diagnostics = cast(
        dict,
        detail(
            task.public_id,
            _request(role="admin"),
            db_session,
            Response(),
            include_diagnostics=True,
        ),
    )
    assert diagnostics["attempts"][0]["promptSnapshot"] == ("SECRET_VERIFY_PROMPT")
    assert diagnostics["attempts"][0]["artemisOutput"] == {"verdict": "published"}


def test_config_exposes_server_owned_device_and_worker_configuration(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    now = datetime.now(UTC)
    db_session.add(
        PublishWorkerHeartbeat(
            instance_id="TEST_worker",
            hostname="TEST_host",
            pid=123,
            status="ready",
            started_at=now,
            heartbeat_at=now,
        )
    )
    db_session.flush()
    monkeypatch.setenv("ARTEMIS_DEVICE_SERIAL", "TEST_device")
    monkeypatch.setenv("TIKTOK_PUBLISH_ALBUM", "TEST_CAMPAIGN")
    monkeypatch.setenv("ARTEMIS_PROFILE", "TEST_profile")
    monkeypatch.setenv("ARTEMIS_VERIFICATION_LEVEL", "TEST_strict")
    monkeypatch.setenv("PUBLISH_POLL_INTERVAL_SECONDS", "7.5")
    monkeypatch.setenv("PUBLISH_TASK_LEASE_SECONDS", "41")
    monkeypatch.setenv("PUBLISH_WORKER_HEARTBEAT_SECONDS", "9")
    payload = config(_request(role="readwrite"), db_session)
    assert payload["target"]["album"] == "TEST_CAMPAIGN"
    assert payload["device"] == {
        "status": "configured",
        "message": "Worker 心跳正常；设备已配置，领取任务时检查在线与解锁状态",
    }
    assert payload["artemis"] == {
        "profile": "TEST_profile",
        "verificationLevel": "TEST_strict",
    }
    assert payload["workerTiming"] == {
        "pollSeconds": 7.5,
        "leaseSeconds": 41,
        "heartbeatSeconds": 9.0,
    }
    assert payload["canWrite"] is True


@pytest.mark.parametrize("device_path", ["/sdcard/Movies/TEST/video.mp4", None])
@pytest.mark.asyncio
async def test_verify_not_published_at_exhausted_budget_is_terminal(
    db_session: Session,
    tmp_path: Path,
    device_path: str | None,
) -> None:
    class VerifyArtemis:
        async def submit(self, **kwargs):
            return ArtemisResult(
                kwargs["session_id"], "success", output={"verdict": "not_published"}
            )

    task = _task(
        status=TaskStatus.RUNNING.value,
        stage=TaskStage.VERIFYING.value,
        device_path=device_path,
    )
    task.attempt_count = 3
    task.lease_owner = "verify-budget-worker"
    task.lease_expires_at = datetime.now(UTC) + timedelta(minutes=1)
    publish_attempt = VideoPublishAttempt(
        sequence_no=1,
        kind="publish",
        status=AttemptStatus.FAILED.value,
        artemis_session_id=uuid4(),
        prompt_version="TEST",
        prompt_snapshot="TEST",
        device_serial="TEST_device",
    )
    task.attempts.append(publish_attempt)
    db_session.add(task)
    db_session.flush()
    verify_attempt = VideoPublishAttempt(
        task_id=task.id,
        sequence_no=2,
        kind="verify",
        status=AttemptStatus.CREATED.value,
        artemis_session_id=uuid4(),
        prompt_version="TEST",
        prompt_snapshot="TEST",
        device_serial="TEST_device",
        related_attempt_id=publish_attempt.id,
    )
    db_session.add(verify_attempt)
    db_session.flush()
    db_session.commit()
    deps = cast(
        PublishDependencies,
        SimpleNamespace(
            session_factory=_factory(db_session),
            instance_id="verify-budget-worker",
            lease_seconds=30,
            max_attempts=3,
            adb=_CleanupAdb(),
            store=_CleanupStore(),
            artemis=VerifyArtemis(),
            spool_dir=tmp_path,
        ),
    )
    await _run_attempt(task.public_id, verify_attempt.id, deps)
    db_session.expire_all()
    assert task.status == TaskStatus.FAILED.value
    assert task.stage == TaskStage.DONE.value
    assert task.last_error_code == "retry_budget_exhausted"
    assert task.lease_owner is None
    if device_path:
        assert task.cleanup_intent == "preserve_state"
        assert task.device_cleanup_status == "pending"
    else:
        assert task.cleanup_intent == "none"


def test_replace_upload_waits_for_device_and_spool_cleanup(db_session: Session) -> None:
    for resource in ("device", "spool"):
        task = _task(
            status=TaskStatus.FAILED.value,
            stage=TaskStage.DONE.value,
            device_cleanup_status="failed" if resource == "device" else "succeeded",
            spool_cleanup_status="failed" if resource == "spool" else "succeeded",
            object_cleanup_status="failed",
        )
        task.object_deleted_at = datetime.now(UTC)
        task.attempt_count = 1
        db_session.add(task)
        db_session.flush()
        with pytest.raises(ValueError, match="CLEANUP_REQUIRED"):
            replace_upload(db_session, task.public_id)
        db_session.rollback()


def test_confirm_illegal_state_is_structured_and_does_not_head(
    db_session: Session,
) -> None:
    class Store:
        def stat(self, _key: str) -> dict:
            raise AssertionError("illegal state must not inspect object storage")

    task = _task(status=TaskStatus.PENDING.value, stage=TaskStage.QUEUED.value)
    db_session.add(task)
    db_session.flush()
    with pytest.raises(HTTPException) as exc_info:
        confirm(
            task.public_id,
            ActionIn(rowVersion=task.row_version),
            _request(),
            db_session,
            cast(VideoObjectStore, Store()),
        )
    assert exc_info.value.status_code == 409
    assert cast(dict, exc_info.value.detail) == {
        "code": "TASK_ACTION_NOT_ALLOWED",
        "message": "TASK_ACTION_NOT_ALLOWED",
        "rowVersion": task.row_version,
        "allowedActions": ["view", "cancel"],
    }


@pytest.mark.asyncio
async def test_artemis_locked_admission_waits_without_consuming_budget(
    db_session: Session,
    tmp_path: Path,
) -> None:
    class LockedArtemis:
        async def submit(self, **_kwargs):
            raise ArtemisAdmissionRejected("DEVICE_LOCKED")

    task = _task(
        status=TaskStatus.RUNNING.value,
        stage=TaskStage.DISPATCHING_ARTEMIS.value,
    )
    task.attempt_count = 1
    task.lease_owner = "locked-worker"
    task.lease_expires_at = datetime.now(UTC) + timedelta(minutes=1)
    task.attempts.append(
        VideoPublishAttempt(
            sequence_no=1,
            kind="publish",
            status=AttemptStatus.CREATED.value,
            artemis_session_id=uuid4(),
            prompt_version="TEST",
            prompt_snapshot="TEST",
            device_serial="TEST_device",
        )
    )
    db_session.add(task)
    db_session.flush()
    db_session.commit()
    deps = cast(
        PublishDependencies,
        SimpleNamespace(
            session_factory=_factory(db_session),
            instance_id="locked-worker",
            lease_seconds=30,
            max_attempts=3,
            artemis=LockedArtemis(),
            spool_dir=tmp_path,
        ),
    )
    await _run_attempt(task.public_id, task.attempts[0].id, deps)
    db_session.expire_all()
    assert task.attempt_count == 0
    assert task.status == TaskStatus.PENDING.value
    assert task.stage == TaskStage.WAITING_DEVICE.value
    assert task.attempts[0].status == AttemptStatus.REJECTED.value
    assert task.attempts[0].retry_safe is True


@pytest.mark.asyncio
async def test_same_session_resubmit_increments_transport_retry_count(
    db_session: Session,
    tmp_path: Path,
) -> None:
    class RecoveringArtemis:
        def __init__(self) -> None:
            self.submits = 0

        async def submit(self, **kwargs):
            self.submits += 1
            if self.submits == 1:
                raise ArtemisTransportError("TEST timeout")
            return ArtemisResult(kwargs["session_id"], "queued")

        async def get_task(self, session_id):
            return ArtemisResult(session_id, "not_found")

    task = _task(
        status=TaskStatus.RUNNING.value,
        stage=TaskStage.DISPATCHING_ARTEMIS.value,
    )
    task.attempt_count = 1
    task.lease_owner = "retry-worker"
    task.lease_expires_at = datetime.now(UTC) + timedelta(minutes=1)
    task.attempts.append(
        VideoPublishAttempt(
            sequence_no=1,
            kind="publish",
            status=AttemptStatus.CREATED.value,
            artemis_session_id=uuid4(),
            prompt_version="TEST",
            prompt_snapshot="TEST",
            device_serial="TEST_device",
        )
    )
    db_session.add(task)
    db_session.flush()
    db_session.commit()
    deps = cast(
        PublishDependencies,
        SimpleNamespace(
            session_factory=_factory(db_session),
            instance_id="retry-worker",
            lease_seconds=30,
            max_attempts=3,
            artemis=RecoveringArtemis(),
            spool_dir=tmp_path,
        ),
    )
    await _run_attempt(task.public_id, task.attempts[0].id, deps)
    db_session.expire_all()
    assert task.attempts[0].submit_retry_count == 1
    assert task.attempts[0].status == AttemptStatus.QUEUED.value


def test_retention_schedules_abandoned_and_expired_failed_objects_only(
    db_session: Session,
) -> None:
    old = datetime.now(UTC) - timedelta(days=40)
    abandoned = _task(stage=TaskStage.AWAITING_UPLOAD.value)
    abandoned.created_at = old
    failed = _task(status=TaskStatus.FAILED.value)
    failed.object_uploaded_at = old
    failed.completed_at = old
    needs_review = _task(status=TaskStatus.NEEDS_REVIEW.value)
    needs_review.object_uploaded_at = old
    needs_review.completed_at = old
    db_session.add_all([abandoned, failed, needs_review])
    db_session.flush()
    db_session.expire_all()
    assert schedule_retention_cleanup(db_session, failed_retention_days=30) == 2
    assert abandoned.status == TaskStatus.CANCELLED.value
    assert abandoned.stage == TaskStage.DONE.value
    assert abandoned.object_cleanup_status == "pending"
    assert failed.status == TaskStatus.FAILED.value
    assert failed.object_cleanup_status == "pending"
    assert needs_review.object_cleanup_status == "not_started"
    assert schedule_retention_cleanup(db_session, failed_retention_days=30) == 0


def test_lease_expiry_uses_database_time_despite_process_clock_skew(
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tts_erp_v2.publishing import repository

    task = _task(status=TaskStatus.RUNNING.value, stage=TaskStage.DOWNLOADING.value)
    task.lease_owner = "old-worker"
    task.lease_expires_at = datetime.now(UTC) - timedelta(seconds=1)
    db_session.add(task)
    db_session.flush()

    class SkewedDateTime:
        @classmethod
        def now(cls, _timezone):
            return datetime(2000, 1, 1, tzinfo=UTC)

    monkeypatch.setattr(repository, "datetime", SkewedDateTime)
    leased = _lease_task(db_session, "new-worker", 30)
    assert leased is not None
    assert leased.lease_expires_at is not None
    assert leased.lease_expires_at > datetime.now(UTC)


def test_startup_orphan_spool_cleanup_schedules_without_deleting(
    db_session: Session,
    tmp_path: Path,
) -> None:
    from tts_erp_v2.publishing.worker import _cleanup_orphan_spool

    old = datetime.now(UTC) - timedelta(days=2)
    terminal = _task(status=TaskStatus.FAILED.value)
    terminal.completed_at = old
    active = _task(status=TaskStatus.PENDING.value, stage=TaskStage.QUEUED.value)
    leased = _task(status=TaskStatus.FAILED.value)
    leased.completed_at = old
    leased.cleanup_intent = "preserve_state"
    leased.cleanup_lease_owner = "cleanup-owner"
    leased.cleanup_lease_expires_at = datetime.now(UTC) + timedelta(minutes=5)
    db_session.add_all([terminal, active, leased])
    db_session.flush()
    db_session.commit()
    for name in (
        str(terminal.public_id),
        str(active.public_id),
        str(leased.public_id),
        "unknown",
    ):
        directory = tmp_path / name
        directory.mkdir()
        (directory / "video.mp4").write_bytes(b"TEST")
    assert _cleanup_orphan_spool(_factory(db_session), tmp_path) == 1
    assert (tmp_path / str(terminal.public_id)).exists()
    assert (tmp_path / str(active.public_id)).exists()
    assert (tmp_path / str(leased.public_id)).exists()
    assert (tmp_path / "unknown").exists()
    db_session.expire_all()
    assert terminal.cleanup_intent == "preserve_state"
    assert terminal.spool_cleanup_status == "pending"
    assert terminal.spool_cleanup_next_attempt_at is not None
    assert leased.spool_cleanup_status == "not_started"
    assert leased.cleanup_lease_owner == "cleanup-owner"
