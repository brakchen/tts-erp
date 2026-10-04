from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import cast
from uuid import uuid4

import pytest
from fastapi import HTTPException, Request, Response
from sqlalchemy.orm import Session

from tts_erp_v2.api.v2.video_publish import config, list_tasks, retry_cleanup
from tts_erp_v2.db.models.publishing import VideoPublishTask
from tts_erp_v2.publishing.dispatcher import (
    LeaseLost,
    PublishDependencies,
    _cleanup_success,
    _defer_for_device_cleanup,
    _mark_spool_cleanup,
    _safe_retry,
    dispatch_one,
)
from tts_erp_v2.publishing.domain import TaskStage, TaskStatus
from tts_erp_v2.publishing.object_store import VideoObjectStore
from tts_erp_v2.publishing.repository import _lease_task, claim_one
from tts_erp_v2.publishing.submission import CreateCommand, create_upload_ticket


def _task(
    *,
    device_cleanup_status: str = "not_started",
    object_cleanup_status: str = "not_started",
    spool_cleanup_status: str = "not_started",
    status: str = TaskStatus.PENDING.value,
    stage: str = TaskStage.QUEUED.value,
    device_path: str | None = "/sdcard/Movies/TEST/video.mp4",
) -> VideoPublishTask:
    return VideoPublishTask(
        public_id=uuid4(),
        client_request_id=uuid4(),
        caption="TEST_caption",
        original_filename="TEST_video.mp4",
        content_type="video/mp4",
        size_bytes=4,
        object_bucket="tiktok-video",
        object_key=f"TEST/{uuid4()}.mp4",
        status=status,
        stage=stage,
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


def test_device_gate_defers_staging_without_changing_business_result() -> None:
    task = _task(status=TaskStatus.RUNNING.value, stage=TaskStage.DOWNLOADING.value)
    task.row_version = 1
    _defer_for_device_cleanup(task)
    assert task.status == TaskStatus.PENDING.value
    assert task.stage == TaskStage.WAITING_DEVICE.value
    assert task.last_error_code == "DEVICE_CLEANUP_BLOCKED"
    assert task.lease_owner is None


def test_failed_device_cleanup_blocks_later_claim(db_session: Session) -> None:
    residue = _task(device_cleanup_status="failed")
    queued = _task()
    cleanup = _task(status=TaskStatus.RUNNING.value, stage=TaskStage.CLEANING.value)
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
    result = retry_cleanup(task.public_id, _request(), db_session)
    assert result["status"] == TaskStatus.CANCELLED.value
    assert result["stage"] == TaskStage.CLEANING.value
    assert result["cleanup"]["object"]["status"] == "pending"
    db_session.expire_all()
    assert task.status == TaskStatus.CANCELLED.value
    assert task.stage == TaskStage.CLEANING.value
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

    stale = _task(
        status=TaskStatus.SUCCEEDED.value,
        stage=TaskStage.DONE.value,
        device_cleanup_status="succeeded",
        object_cleanup_status="succeeded",
        spool_cleanup_status="failed",
    )
    stale.lease_owner = "worker-b"
    db_session.add(stale)
    db_session.flush()
    db_session.commit()
    await _mark_spool_cleanup(stale.public_id, deps)
    db_session.expire_all()
    assert stale.spool_cleanup_status == "failed"
    assert stale.lease_owner == "worker-b"


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
    from tts_erp_v2.publishing.repository import _lease_cleanup_task

    assert _lease_cleanup_task(db_session, "worker-a", 30) is not None
    db_session.commit()
    task = db_session.get(VideoPublishTask, task.id)
    assert task is not None
    task.lease_owner = "worker-b"
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
    with pytest.raises(LeaseLost):
        await _cleanup_success(task.public_id, deps)
    db_session.expire_all()
    assert task.lease_owner == "worker-b"
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
    assert payload["pollState"] == {"running": True, "queued": True}


def _request() -> Request:
    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/v2/video-publish/tasks/cleanup/retry",
            "headers": [],
            "api_key_role": "readwrite",
        }
    )


def test_cleanup_retry_rejects_live_lease_and_requeues_expired_lease(
    db_session: Session,
) -> None:
    task = _task(
        status=TaskStatus.SUCCEEDED.value,
        stage=TaskStage.DONE.value,
        device_cleanup_status="failed",
    )
    task.lease_owner = "auto-worker"
    task.lease_expires_at = datetime.now(UTC) + timedelta(minutes=1)
    db_session.add(task)
    db_session.flush()
    db_session.commit()
    with pytest.raises(HTTPException) as exc_info:
        retry_cleanup(task.public_id, _request(), db_session)
    assert exc_info.value.detail == "CLEANUP_LEASE_BUSY"
    db_session.rollback()

    task = db_session.get(VideoPublishTask, task.id)
    assert task is not None
    task.lease_expires_at = datetime.now(UTC) - timedelta(seconds=1)
    db_session.commit()
    result = retry_cleanup(task.public_id, _request(), db_session)
    assert result["status"] == TaskStatus.RUNNING.value
    assert result["stage"] == TaskStage.CLEANING.value
    assert result["cleanup"]["device"]["status"] == "pending"
    assert task.lease_owner is None


def test_config_exposes_configured_album(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TIKTOK_PUBLISH_ALBUM", "TEST_CAMPAIGN")
    payload = config(db_session)
    assert payload["target"]["album"] == "TEST_CAMPAIGN"
