from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from tts_erp_v2.api import deps
from tts_erp_v2.api.v2 import video_publish
from tts_erp_v2.db.models.publishing import (
    PublishWorkerHeartbeat,
    VideoPublishAttempt,
    VideoPublishConfirmation,
    VideoPublishTask,
)
from tts_erp_v2.publishing.artemis_client import ArtemisClient, ArtemisTransportError
from tts_erp_v2.publishing.repository import ConfirmationActor, confirm_published
from tts_erp_v2.publishing.safe_values import mask_device_serial

pytestmark = [pytest.mark.domain_publishing]


class _UploadStore:
    bucket = "tiktok-video"

    def __init__(self, session: Session | None = None) -> None:
        self.session = session
        self.persisted_before_presign = False

    def presign_put(self, key: str, content_type: str) -> str:
        if self.session is not None:
            self.persisted_before_presign = (
                self.session.scalar(
                    select(VideoPublishTask).where(VideoPublishTask.object_key == key)
                )
                is not None
            )
        return f"https://upload.test/{key}"


def test_upload_ticket_http_statuses_and_foreign_replay_denial(
    db_session: Session, monkeypatch
) -> None:
    monkeypatch.setenv("ARTEMIS_DEVICE_SERIAL", "TEST_device")
    monkeypatch.setenv("TIKTOK_PUBLISH_MINIO_BUCKET", "tiktok-video")
    monkeypatch.setenv("TIKTOK_PUBLISH_UPLOAD_TTL_SECONDS", "120")
    app = FastAPI()
    app.include_router(video_publish.router)

    @app.middleware("http")
    async def test_auth(request: Request, call_next):
        if request.headers.get("X-Test-Cookie"):
            request.scope["auth_method"] = "cookie"
            request.scope["api_key_role"] = "readwrite"
            request.scope["user_id"] = 1
        else:
            request.scope["auth_method"] = "bearer"
            request.scope["api_key_role"] = "readwrite"
            request.scope["api_key_hash"] = request.headers.get("X-Test-Key", "key-a")
        return await call_next(request)

    db_session.add(
        PublishWorkerHeartbeat(
            instance_id="TEST-http-worker",
            hostname="TEST-host",
            pid=2424,
            status="ready",
            device_status="ready",
            started_at=datetime.now(UTC),
            heartbeat_at=datetime.now(UTC),
        )
    )
    db_session.commit()
    store = _UploadStore(db_session)
    app.dependency_overrides[deps.get_session] = lambda: db_session
    app.dependency_overrides[video_publish.get_session] = lambda: db_session
    app.dependency_overrides[video_publish.get_store] = lambda: store
    payload = {
        "clientRequestId": "00000000-0000-0000-0000-000000000001",
        "filename": "TEST_video.mp4",
        "contentType": "video/mp4",
        "sizeBytes": 4,
        "caption": "TEST_caption",
    }
    with TestClient(app) as client:
        first = client.post("/v2/video-publish/tasks", json=payload)
        assert first.status_code == 201
        assert first.json()["idempotentReplay"] is False
        assert store.persisted_before_presign is True
        expires_at = datetime.fromisoformat(first.json()["upload"]["expiresAt"])
        now = datetime.now(UTC)
        assert now <= expires_at <= now + timedelta(seconds=121)

        replay = client.post("/v2/video-publish/tasks", json=payload)
        assert replay.status_code == 200
        assert replay.json()["idempotentReplay"] is True
        assert replay.json()["taskId"] == first.json()["taskId"]

        mismatch_payload = {**payload, "caption": "TEST_other_caption"}
        mismatch = client.post("/v2/video-publish/tasks", json=mismatch_payload)
        assert mismatch.status_code == 409
        assert mismatch.json()["detail"]["code"] == "IDEMPOTENCY_PAYLOAD_MISMATCH"

        foreign = client.post(
            "/v2/video-publish/tasks",
            json=payload,
            headers={"X-Test-Key": "key-b"},
        )
        assert foreign.status_code == 404
        assert foreign.json()["detail"]["code"] == "TASK_NOT_FOUND"
        assert foreign.json()["detail"]["retryable"] is False
        assert foreign.json()["detail"]["requestId"]

        cookie_payload = {
            **payload,
            "clientRequestId": "00000000-0000-0000-0000-000000000002",
        }
        cookie_denied = client.post(
            "/v2/video-publish/tasks",
            json=cookie_payload,
            headers={"X-Test-Cookie": "1"},
        )
        assert cookie_denied.status_code == 403
        assert cookie_denied.json()["detail"]["code"] == "CSRF_HEADER_REQUIRED"
        assert "X-Requested-With" in cookie_denied.json()["detail"]["message"]
        cookie_allowed = client.post(
            "/v2/video-publish/tasks",
            json=cookie_payload,
            headers={"X-Test-Cookie": "1", "X-Requested-With": "tts-erp"},
        )
        assert cookie_allowed.status_code == 201

    app.dependency_overrides.clear()


def _verify_client(db_session: Session) -> TestClient:
    """Cookie-authed readwrite client whose user owns the created tasks."""
    app = FastAPI()
    app.include_router(video_publish.router)

    @app.middleware("http")
    async def test_auth(request: Request, call_next):
        request.scope["auth_method"] = "cookie"
        request.scope["api_key_role"] = "readwrite"
        request.scope["user_id"] = 1
        return await call_next(request)

    app.dependency_overrides[deps.get_session] = lambda: db_session
    app.dependency_overrides[video_publish.get_session] = lambda: db_session
    app.dependency_overrides[video_publish.get_store] = lambda: _UploadStore(db_session)
    return TestClient(app)


def _http_review_task() -> VideoPublishTask:
    task = VideoPublishTask(
        public_id=uuid4(),
        client_request_id=uuid4(),
        created_by_user_id=1,
        caption="TEST_http_caption",
        original_filename="TEST_http.mp4",
        object_filename="TEST_http.mp4",
        content_type="video/mp4",
        size_bytes=4,
        object_bucket="tiktok-video",
        object_key=f"TEST/http/{uuid4()}/video.mp4",
        object_etag="TEST-http-etag",
        object_uploaded_at=datetime.now(UTC),
        status="needs_review",
        stage="done",
        stage_started_at=datetime.now(UTC),
        cleanup_intent="none",
        target_device_serial="TEST_device",
        target_app_package="com.test.http",
    )
    task.attempts.append(
        VideoPublishAttempt(
            sequence_no=1,
            kind="publish",
            artemis_session_id=uuid4(),
            status="success",
            prompt_version="TEST_http",
            prompt_snapshot="TEST_http prompt",
            device_serial="TEST_device",
            target_app_package="com.test.http",
            artemis_profile="TEST_http_non_default_profile",
            artemis_verification_level="TEST_http_non_default_verification",
        )
    )
    return task


def test_verify_http_contract_blocks_on_global_cleanup_and_running_task(
    db_session: Session,
) -> None:
    """POST /tasks/{id}/verify honours the global device slot at the HTTP edge:
    409 DEVICE_CLEANUP_BLOCKED while any task owns pending/failed device cleanup,
    409 DEVICE_BUSY while any other task is running, then 200 once the slot is
    free."""
    review = _http_review_task()
    db_session.add(review)
    db_session.commit()
    review_id = review.public_id
    initial_row_version = review.row_version
    blocked_cleanup = VideoPublishTask(
        public_id=uuid4(),
        client_request_id=uuid4(),
        created_by_user_id=1,
        caption="TEST_http_cleanup_caption",
        original_filename="TEST_http_cleanup.mp4",
        object_filename="TEST_http_cleanup.mp4",
        content_type="video/mp4",
        size_bytes=4,
        object_bucket="tiktok-video",
        object_key=f"TEST/http/{uuid4()}/video.mp4",
        status="failed",
        stage="done",
        stage_started_at=datetime.now(UTC),
        cleanup_intent="preserve_state",
        device_cleanup_status="pending",
        device_cleanup_next_attempt_at=datetime.now(UTC) - timedelta(seconds=1),
        target_device_serial="TEST_device",
        target_app_package="com.test.http",
    )
    db_session.add(blocked_cleanup)
    db_session.commit()

    headers = {"X-Requested-With": "tts-erp"}
    with _verify_client(db_session) as client:
        denied = client.post(
            f"/v2/video-publish/tasks/{review_id}/verify",
            json={"rowVersion": review.row_version},
            headers=headers,
        )
        assert denied.status_code == 409
        detail = denied.json()["detail"]
        assert detail["code"] == "DEVICE_CLEANUP_BLOCKED"
        assert detail["retryable"] is False
        assert detail["requestId"]
        assert detail["rowVersion"] == review.row_version
        assert isinstance(detail["allowedActions"], list)

        blocked_cleanup.device_cleanup_status = "succeeded"
        blocked_cleanup.cleanup_intent = "none"
        running = VideoPublishTask(
            public_id=uuid4(),
            client_request_id=uuid4(),
            created_by_user_id=1,
            caption="TEST_http_running_caption",
            original_filename="TEST_http_running.mp4",
            object_filename="TEST_http_running.mp4",
            content_type="video/mp4",
            size_bytes=4,
            object_bucket="tiktok-video",
            object_key=f"TEST/http/{uuid4()}/video.mp4",
            status="running",
            stage="downloading",
            stage_started_at=datetime.now(UTC),
            lease_owner="TEST-http-worker",
            lease_expires_at=datetime.now(UTC) + timedelta(seconds=30),
            heartbeat_at=datetime.now(UTC),
            cleanup_intent="none",
            target_device_serial="TEST_device",
            target_app_package="com.test.http",
        )
        db_session.add(running)
        db_session.commit()

        busy = client.post(
            f"/v2/video-publish/tasks/{review_id}/verify",
            json={"rowVersion": review.row_version},
            headers=headers,
        )
        assert busy.status_code == 409
        busy_detail = busy.json()["detail"]
        assert busy_detail["code"] == "DEVICE_BUSY"
        assert busy_detail["requestId"]
        assert busy_detail["rowVersion"] == review.row_version

        db_session.execute(
            delete(VideoPublishTask).where(VideoPublishTask.id == running.id)
        )
        db_session.commit()

        accepted = client.post(
            f"/v2/video-publish/tasks/{review_id}/verify",
            json={"rowVersion": review.row_version},
            headers=headers,
        )
        assert accepted.status_code == 200
        body = accepted.json()
        assert body["status"] == "running"
        assert body["stage"] == "verifying"
        assert body["verifyAttemptCount"] == 1
        assert body["rowVersion"] == initial_row_version + 1
        assert review.row_version == initial_row_version + 1

    task_pk = review.id
    cleanup_pk = blocked_cleanup.id
    db_session.execute(
        delete(VideoPublishAttempt).where(VideoPublishAttempt.task_id == task_pk)
    )
    db_session.execute(delete(VideoPublishTask).where(VideoPublishTask.id == task_pk))
    db_session.execute(
        delete(VideoPublishTask).where(VideoPublishTask.id == cleanup_pk)
    )
    db_session.commit()


def test_create_task_device_serial_override_default_and_invalid(
    db_session: Session, monkeypatch
) -> None:
    """POST /tasks honours optional deviceSerial (design §21.3): an explicit
    serial is snapshotted onto the task, omission falls back to
    ARTEMIS_DEVICE_SERIAL, and malformed or explicitly-empty serials fail with
    422 DEVICE_SERIAL_INVALID. Replaying the idempotency key without
    deviceSerial keeps the original task and its device snapshot."""
    monkeypatch.setenv("ARTEMIS_DEVICE_SERIAL", "TEST_device")
    monkeypatch.setenv("TIKTOK_PUBLISH_MINIO_BUCKET", "tiktok-video")
    monkeypatch.setenv("TIKTOK_PUBLISH_UPLOAD_TTL_SECONDS", "120")
    app = FastAPI()
    app.include_router(video_publish.router)

    @app.middleware("http")
    async def test_auth(request: Request, call_next):
        request.scope["auth_method"] = "bearer"
        request.scope["api_key_role"] = "readwrite"
        request.scope["api_key_hash"] = "key-a"
        return await call_next(request)

    db_session.add(
        PublishWorkerHeartbeat(
            instance_id="TEST-device-worker",
            hostname="TEST-host",
            pid=2425,
            status="ready",
            device_status="ready",
            started_at=datetime.now(UTC),
            heartbeat_at=datetime.now(UTC),
        )
    )
    db_session.commit()
    store = _UploadStore(db_session)
    app.dependency_overrides[deps.get_session] = lambda: db_session
    app.dependency_overrides[video_publish.get_session] = lambda: db_session
    app.dependency_overrides[video_publish.get_store] = lambda: store

    def payload(request_id: str, **extra) -> dict:
        return {
            "clientRequestId": request_id,
            "filename": "TEST_device.mp4",
            "contentType": "video/mp4",
            "sizeBytes": 4,
            "caption": "TEST_device_caption",
            **extra,
        }

    def task_for(request_id: str) -> VideoPublishTask:
        found = db_session.scalar(
            select(VideoPublishTask).where(
                VideoPublishTask.client_request_id == UUID(request_id)
            )
        )
        assert found is not None, "task row disappeared"
        return found

    with TestClient(app) as client:
        override_id = "00000000-0000-0000-0000-000000000d01"
        override = client.post(
            "/v2/video-publish/tasks",
            json=payload(override_id, deviceSerial="TEST_other_device"),
        )
        assert override.status_code == 201
        assert task_for(override_id).target_device_serial == "TEST_other_device"

        default_id = "00000000-0000-0000-0000-000000000d02"
        default = client.post("/v2/video-publish/tasks", json=payload(default_id))
        assert default.status_code == 201
        assert task_for(default_id).target_device_serial == "TEST_device"

        invalid_id = "00000000-0000-0000-0000-000000000d03"
        invalid = client.post(
            "/v2/video-publish/tasks",
            json=payload(invalid_id, deviceSerial="bad serial!"),
        )
        assert invalid.status_code == 422
        assert invalid.json()["detail"]["code"] == "DEVICE_SERIAL_INVALID"
        assert invalid.json()["detail"]["retryable"] is False

        empty_id = "00000000-0000-0000-0000-000000000d04"
        empty = client.post(
            "/v2/video-publish/tasks",
            json=payload(empty_id, deviceSerial=""),
        )
        assert empty.status_code == 422
        assert empty.json()["detail"]["code"] == "DEVICE_SERIAL_INVALID"

        replay = client.post("/v2/video-publish/tasks", json=payload(override_id))
        assert replay.status_code == 200
        assert replay.json()["idempotentReplay"] is True
        assert task_for(override_id).target_device_serial == "TEST_other_device"

    app.dependency_overrides.clear()


def test_devices_endpoint_proxies_artemis_and_fails_closed(
    db_session: Session, monkeypatch
) -> None:
    """GET /devices (design §21.15) proxies Artemis GET /api/devices with the
    exact serial plus its masked twin, never forwards task/session text, and
    maps an unreachable or unconfigured Artemis to 503 ARTEMIS_UNREACHABLE."""
    monkeypatch.setenv("ARTEMIS_BASE_URL", "http://artemis.test:8001")
    app = FastAPI()
    app.include_router(video_publish.router)

    @app.middleware("http")
    async def test_auth(request: Request, call_next):
        request.scope["auth_method"] = "bearer"
        request.scope["api_key_role"] = "readwrite"
        request.scope["api_key_hash"] = "key-a"
        return await call_next(request)

    app.dependency_overrides[deps.get_session] = lambda: db_session
    app.dependency_overrides[video_publish.get_session] = lambda: db_session

    async def fake_request(self, method, path, **kwargs):
        assert (method, path) == ("GET", "/api/devices")
        return {
            "devices": [
                {
                    "serial": "TEST_device_A",
                    "state": "device",
                    "model": "NX712J",
                    "product": "CN_TEST",
                    "is_emulator": False,
                    "is_busy": True,
                    "active_pid": 42,
                    "active_task_desc": "TEST prompt secret",
                    "active_session_id": "TEST-session",
                    "acquired_at": None,
                }
            ]
        }

    monkeypatch.setattr(ArtemisClient, "_request", fake_request)
    with TestClient(app) as client:
        ok = client.get("/v2/video-publish/devices")
        assert ok.status_code == 200
        devices = ok.json()["devices"]
        assert len(devices) == 1
        device = devices[0]
        assert device["serial"] == "TEST_device_A"
        assert device["serialMasked"] == mask_device_serial("TEST_device_A")
        assert device["model"] == "NX712J"
        assert device["product"] == "CN_TEST"
        assert device["state"] == "device"
        assert device["isBusy"] is True
        assert device["isEmulator"] is False
        assert "activeTaskDesc" not in device
        assert "activeSessionId" not in device

        async def fail_request(self, method, path, **kwargs):
            raise ArtemisTransportError("ARTEMIS_TRANSPORT_ERROR")

        monkeypatch.setattr(ArtemisClient, "_request", fail_request)
        down = client.get("/v2/video-publish/devices")
        assert down.status_code == 503
        detail = down.json()["detail"]
        assert detail["code"] == "ARTEMIS_UNREACHABLE"
        assert detail["retryable"] is True

        monkeypatch.delenv("ARTEMIS_BASE_URL")
        unconfigured = client.get("/v2/video-publish/devices")
        assert unconfigured.status_code == 503
        assert unconfigured.json()["detail"]["code"] == "ARTEMIS_UNREACHABLE"

    app.dependency_overrides.clear()


class _RetryStore:
    """Object store where the uploaded object is still present."""

    bucket = "tiktok-video"

    def stat(self, key: str) -> dict:
        return {"key": key, "size": 4}


def _retry_client(db_session: Session) -> TestClient:
    """Cookie-authed readwrite client whose user owns the created tasks.

    ``raise_server_exceptions=False`` so an unhandled server-side exception
    surfaces as a real HTTP 5xx instead of propagating out of the test.
    """
    app = FastAPI()
    app.include_router(video_publish.router)

    @app.middleware("http")
    async def test_auth(request: Request, call_next):
        request.scope["auth_method"] = "cookie"
        request.scope["api_key_role"] = "readwrite"
        request.scope["user_id"] = 1
        return await call_next(request)

    app.dependency_overrides[deps.get_session] = lambda: db_session
    app.dependency_overrides[video_publish.get_session] = lambda: db_session
    app.dependency_overrides[video_publish.get_store] = lambda: _RetryStore()
    return TestClient(app, raise_server_exceptions=False)


def _retryable_failed_task(*, device_cleanup_status: str) -> VideoPublishTask:
    task = VideoPublishTask(
        public_id=uuid4(),
        client_request_id=uuid4(),
        created_by_user_id=1,
        caption="TEST_retry_caption",
        original_filename="TEST_retry.mp4",
        object_filename="TEST_retry.mp4",
        content_type="video/mp4",
        size_bytes=4,
        object_bucket="tiktok-video",
        object_key=f"TEST/http/{uuid4()}/video.mp4",
        object_etag="TEST-retry-etag",
        object_uploaded_at=datetime.now(UTC),
        status="failed",
        stage="done",
        stage_started_at=datetime.now(UTC),
        attempt_count=1,
        publish_budget_used=1,
        cleanup_intent="preserve_state",
        target_device_serial="TEST_device",
        target_app_package="com.test.http",
        device_cleanup_status=device_cleanup_status,
    )
    task.attempts.append(
        VideoPublishAttempt(
            sequence_no=1,
            kind="publish",
            artemis_session_id=uuid4(),
            status="failed",
            prompt_version="TEST_retry",
            prompt_snapshot="TEST_retry prompt",
            device_serial="TEST_device",
            target_app_package="com.test.http",
            artemis_profile="TEST_retry_non_default_profile",
            artemis_verification_level="TEST_retry_non_default_verification",
            retry_safe=True,
        )
    )
    return task


def test_retry_http_contract_requeues_when_device_cleanup_is_unfinished(
    db_session: Session,
) -> None:
    """Batch 3A: ``POST /tasks/{id}/retry`` re-queues even while the device/spool
    cleanup is still pending or failed.

    Historically this write raised IntegrityError on
    ``video_publish_task_cleanup_owner_check`` because the re-queue set
    ``cleanup_intent='none'`` while the device row was still
    pending/failed (that was the bare 500). ``queue_task`` now keeps the
    cleanup-gated marker instead, so the row is valid *and* the readiness gate
    can still see the task.
    """
    for cleanup_status in ("pending", "failed"):
        task = _retryable_failed_task(device_cleanup_status=cleanup_status)
        db_session.add(task)
        db_session.commit()
        task_id = task.public_id

        with _retry_client(db_session) as client:
            response = client.post(
                f"/v2/video-publish/tasks/{task_id}/retry",
                json={"rowVersion": task.row_version},
                headers={"X-Requested-With": "tts-erp"},
            )

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["status"] == "pending"
        assert body["stage"] == "queued"

        db_session.expire_all()
        requeued = db_session.scalar(
            select(VideoPublishTask).where(VideoPublishTask.public_id == task_id)
        )
        assert requeued is not None
        assert requeued.status == "pending"
        assert requeued.stage == "queued"
        # The gate must remain able to see this task...
        assert requeued.cleanup_intent == "requeue_publish"
        # ...and retry must not fake a clean device.
        assert requeued.device_cleanup_status == cleanup_status
        assert len(requeued.attempts) == 1
        # The operator keeps a way to clear the cleanup itself.
        assert ("retry_cleanup" in body["allowedActions"]) is (
            cleanup_status == "failed"
        )


def test_retry_cleanup_http_contract_unblocks_after_device_cleanup_failure(
    db_session: Session,
) -> None:
    """Positive control for the test above: with the same unfinished device
    cleanup, ``cleanup/retry`` must still be offered and must still work, so the
    publish retry is not a redirect through a dead end.
    """
    task = _retryable_failed_task(device_cleanup_status="failed")
    db_session.add(task)
    db_session.commit()
    task_id = task.public_id

    headers = {"X-Requested-With": "tts-erp"}
    with _retry_client(db_session) as client:
        requeued = client.post(
            f"/v2/video-publish/tasks/{task_id}/retry",
            json={"rowVersion": task.row_version},
            headers=headers,
        )
        assert requeued.status_code == 200, requeued.text
        body = requeued.json()
        assert "retry_cleanup" in body["allowedActions"]

        retried = client.post(
            f"/v2/video-publish/tasks/{task_id}/cleanup/retry",
            json={"resources": ["device"], "rowVersion": body["rowVersion"]},
            headers=headers,
        )
        assert retried.status_code == 200, retried.text

    db_session.expire_all()
    reloaded = db_session.scalar(
        select(VideoPublishTask).where(VideoPublishTask.public_id == task_id)
    )
    assert reloaded is not None
    # cleanup/retry re-arms the failed cleanup as in-flight.
    assert reloaded.device_cleanup_status == "pending"


def test_retry_http_contract_requeues_when_no_cleanup_is_outstanding(
    db_session: Session,
) -> None:
    """The healthy path is unchanged: a cleanly failed task still re-queues."""
    task = _retryable_failed_task(device_cleanup_status="not_started")
    task.cleanup_intent = "none"
    db_session.add(task)
    db_session.commit()
    task_id = task.public_id

    with _retry_client(db_session) as client:
        response = client.post(
            f"/v2/video-publish/tasks/{task_id}/retry",
            json={"rowVersion": task.row_version},
            headers={"X-Requested-With": "tts-erp"},
        )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "pending"
    assert body["stage"] == "queued"
    db_session.expire_all()
    requeued = db_session.scalar(
        select(VideoPublishTask).where(VideoPublishTask.public_id == task_id)
    )
    assert requeued is not None
    assert requeued.status == "pending"
    assert requeued.cleanup_intent == "none"


# ── 02 §7.4 / §9.5 / §9.7: confirmation, cancellation boundary, settings ──


class _AuditedStore:
    """Object store where the uploaded object is still present."""

    bucket = "tiktok-video"

    def stat(self, key: str) -> dict:
        return {"key": key, "size": 4, "etag": "TEST_etag"}


def _action_client(db_session: Session, *, role: str = "readwrite", user_id: int = 1):
    """Cookie-authed client whose authenticated principal is ``user_id``.

    The principal is what the confirmation audit must attribute the click to,
    so it is injected by the auth middleware exactly as production does.
    """
    app = FastAPI()
    app.include_router(video_publish.router)

    @app.middleware("http")
    async def test_auth(request: Request, call_next):
        request.scope["auth_method"] = "cookie"
        request.scope["api_key_role"] = role
        request.scope["user_id"] = user_id
        return await call_next(request)

    app.dependency_overrides[deps.get_session] = lambda: db_session
    app.dependency_overrides[video_publish.get_session] = lambda: db_session
    app.dependency_overrides[video_publish.get_store] = lambda: _AuditedStore()
    return TestClient(app, raise_server_exceptions=False)


def _review_task(
    *,
    status: str = "needs_review",
    stage: str = "done",
    params_locked_at: datetime | None = None,
    device_serial: str = "TEST_device",
    created_by_user_id: int | None = 1,
) -> VideoPublishTask:
    task = VideoPublishTask(
        public_id=uuid4(),
        client_request_id=uuid4(),
        created_by_user_id=created_by_user_id,
        caption="TEST_confirm_caption",
        original_filename="TEST_confirm.mp4",
        object_filename="TEST_confirm.mp4",
        content_type="video/mp4",
        size_bytes=4,
        object_bucket="tiktok-video",
        object_key=f"TEST/confirm/{uuid4()}.mp4",
        object_etag="TEST-confirm-etag",
        object_uploaded_at=datetime.now(UTC),
        status=status,
        stage=stage,
        stage_started_at=datetime.now(UTC),
        cleanup_intent="none",
        device_cleanup_status="succeeded",
        spool_cleanup_status="succeeded",
        params_locked_at=params_locked_at,
        target_device_serial=device_serial,
        target_app_package="com.test.confirm",
    )
    task.attempts.append(
        VideoPublishAttempt(
            sequence_no=1,
            kind="publish",
            artemis_session_id=uuid4(),
            status="success",
            prompt_version="TEST_confirm",
            prompt_snapshot="TEST_confirm prompt",
            device_serial=device_serial,
            target_app_package="com.test.confirm",
            artemis_profile="TEST_confirm_profile",
            artemis_verification_level="TEST_confirm_level",
        )
    )
    return task


HEADERS = {"X-Requested-With": "tts-erp"}


def test_confirm_published_records_the_authenticated_principal(
    db_session: Session,
) -> None:
    """02 §7.4 step 4: the operator comes from the request principal and the
    time from the database, so the audit row never echoes client input."""
    task = _review_task()
    db_session.add(task)
    db_session.commit()
    task_id = task.public_id
    before = task.row_version

    with _action_client(db_session, user_id=42) as client:
        response = client.post(
            f"/v2/video-publish/tasks/{task_id}/confirm-published",
            json={"rowVersion": before, "requestId": str(uuid4())},
            headers=HEADERS,
        )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "succeeded"
    assert body["rowVersion"] == before + 1
    confirmation = body["confirmation"]
    assert confirmation["confirmedBy"] == "user:42"
    assert confirmation["fromStatus"] == "needs_review"
    assert datetime.fromisoformat(confirmation["confirmedAt"])
    # A confirmed task offers no undo and no further publish action.
    assert "confirm_published" not in body["allowedActions"]
    assert body["paramsLockedAt"] is None

    db_session.expire_all()
    row = db_session.scalar(
        select(VideoPublishConfirmation).where(
            VideoPublishConfirmation.task_id == task.id
        )
    )
    assert row is not None
    assert row.operator_user_id == 42
    assert row.operator_key_hash is None
    assert row.from_status == "needs_review"
    assert row.to_status == "succeeded"


def test_confirm_published_replay_is_idempotent_and_keeps_the_first_writer(
    db_session: Session,
) -> None:
    """02 §7.4 step 3: a repeated click replays the stored fact. The second
    operator must not overwrite who confirmed or when."""
    task = _review_task()
    db_session.add(task)
    db_session.commit()
    task_id = task.public_id

    with _action_client(db_session, user_id=42) as client:
        first = client.post(
            f"/v2/video-publish/tasks/{task_id}/confirm-published",
            json={"rowVersion": task.row_version},
            headers=HEADERS,
        )
        repeat = client.post(
            f"/v2/video-publish/tasks/{task_id}/confirm-published",
            json={},
            headers=HEADERS,
        )

    assert first.status_code == 200, first.text
    assert repeat.status_code == 200, repeat.text
    assert repeat.json()["confirmation"] == first.json()["confirmation"]
    assert repeat.json()["rowVersion"] == first.json()["rowVersion"]

    db_session.expire_all()
    rows = list(
        db_session.scalars(
            select(VideoPublishConfirmation).where(
                VideoPublishConfirmation.task_id == task.id
            )
        )
    )
    assert len(rows) == 1
    assert rows[0].operator_user_id == 42


def test_confirm_published_concurrent_first_write_replays_one_fact(
    db_session: Session,
) -> None:
    """02 §7.4 step 6: the unique index decides the winner and the loser is
    told the confirmed fact instead of getting a 500."""
    task = _review_task()
    db_session.add(task)
    db_session.commit()
    task_id = task.public_id
    outcome = confirm_published(
        lambda: Session(
            bind=db_session.get_bind(), join_transaction_mode="create_savepoint"
        ),
        task_id,
        actor=ConfirmationActor(user_id=7),
        request_id=uuid4(),
    )
    db_session.expire_all()

    # A second principal racing the same row gets the first operator's fact.
    with _action_client(db_session, user_id=99) as client:
        response = client.post(
            f"/v2/video-publish/tasks/{task_id}/confirm-published",
            json={},
            headers=HEADERS,
        )

    assert response.status_code == 200, response.text
    assert response.json()["confirmation"]["confirmedBy"] == "user:7"
    assert (
        datetime.fromisoformat(response.json()["confirmation"]["confirmedAt"])
        == outcome.confirmed_at
    )
    db_session.expire_all()
    assert len(list(db_session.scalars(select(VideoPublishConfirmation)))) == 1


def test_confirm_published_rejects_states_that_are_not_waiting_for_a_human(
    db_session: Session,
) -> None:
    """02 §7.4 step 2: only needs_review/failed at stage done may confirm."""
    pending = _review_task(status="pending", stage="queued")
    db_session.add(pending)
    db_session.commit()

    with _action_client(db_session) as client:
        response = client.post(
            f"/v2/video-publish/tasks/{pending.public_id}/confirm-published",
            json={},
            headers=HEADERS,
        )

    assert response.status_code == 409, response.text
    detail = response.json()["detail"]
    assert detail["code"] == "TASK_ACTION_NOT_ALLOWED"
    assert detail["retryable"] is False
    assert detail["requestId"]
    assert detail["rowVersion"] == pending.row_version
    assert isinstance(detail["allowedActions"], list)
    db_session.expire_all()
    assert list(db_session.scalars(select(VideoPublishConfirmation))) == []


def test_confirm_published_rejects_a_task_with_an_active_attempt(
    db_session: Session,
) -> None:
    """02 §7.4 step 2: an unfinished attempt means a worker still owns the task."""
    task = _review_task()
    task.attempts[0].status = "running"
    db_session.add(task)
    db_session.commit()

    with _action_client(db_session) as client:
        response = client.post(
            f"/v2/video-publish/tasks/{task.public_id}/confirm-published",
            json={},
            headers=HEADERS,
        )

    assert response.status_code == 409, response.text
    assert response.json()["detail"]["code"] == "TASK_ACTION_NOT_ALLOWED"


def test_confirm_published_not_found_and_csrf_are_mapped(
    db_session: Session,
) -> None:
    """The endpoint keeps the shared 404 and cookie-CSRF contracts."""
    with _action_client(db_session) as client:
        missing = client.post(
            f"/v2/video-publish/tasks/{uuid4()}/confirm-published",
            json={},
            headers=HEADERS,
        )
        assert missing.status_code == 404
        assert missing.json()["detail"]["code"] == "TASK_NOT_FOUND"

        task = _review_task()
        db_session.add(task)
        db_session.commit()
        no_header = client.post(
            f"/v2/video-publish/tasks/{task.public_id}/confirm-published",
            json={},
        )
    assert no_header.status_code == 403
    assert no_header.json()["detail"]["code"] == "CSRF_HEADER_REQUIRED"


def test_cancel_pending_succeeds_directly(db_session: Session) -> None:
    """02 §6.5: an unpublished pending task is cancelled in one transaction and
    its MinIO object is kept."""
    task = _review_task(status="pending", stage="queued")
    db_session.add(task)
    db_session.commit()

    with _action_client(db_session) as client:
        response = client.post(
            f"/v2/video-publish/tasks/{task.public_id}/cancel",
            json={"rowVersion": task.row_version},
            headers=HEADERS,
        )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "cancelled"
    assert body["stage"] == "done"
    assert "cancel" not in body["allowedActions"]
    db_session.expire_all()
    reloaded = db_session.scalar(
        select(VideoPublishTask).where(VideoPublishTask.id == task.id)
    )
    assert reloaded is not None
    assert reloaded.object_deleted_at is None
    assert reloaded.object_uploaded_at is not None


def test_cancel_in_flight_admission_is_409_task_submitting(
    db_session: Session,
) -> None:
    """02 §6.5: while the admission request is in flight the caller must retry on
    the real state instead of trusting a cancel flag nobody would read."""
    task = _review_task(status="running", stage="dispatching_artemis")
    task.attempts[0].status = "running"
    db_session.add(task)
    db_session.commit()

    with _action_client(db_session) as client:
        response = client.post(
            f"/v2/video-publish/tasks/{task.public_id}/cancel",
            json={"rowVersion": task.row_version},
            headers=HEADERS,
        )

    assert response.status_code == 409, response.text
    detail = response.json()["detail"]
    assert detail["code"] == "TASK_SUBMITTING"
    assert detail["retryable"] is False
    db_session.expire_all()
    reloaded = db_session.scalar(
        select(VideoPublishTask).where(VideoPublishTask.id == task.id)
    )
    assert reloaded is not None
    assert reloaded.status == "running"
    assert reloaded.cancel_requested is False


def test_cancel_after_admission_is_409_task_already_submitted(
    db_session: Session,
) -> None:
    """02 §6.5: once params_locked_at is set the phone owns the outcome; no
    Artemis stop is ever sent and the cancel entry point disappears."""
    task = _review_task(
        status="running",
        stage="staging_device",
        params_locked_at=datetime.now(UTC),
    )
    task.attempts[0].status = "running"
    db_session.add(task)
    db_session.commit()

    with _action_client(db_session) as client:
        response = client.post(
            f"/v2/video-publish/tasks/{task.public_id}/cancel",
            json={"rowVersion": task.row_version},
            headers=HEADERS,
        )

    assert response.status_code == 409, response.text
    detail = response.json()["detail"]
    assert detail["code"] == "TASK_ALREADY_SUBMITTED"
    db_session.expire_all()
    reloaded = db_session.scalar(
        select(VideoPublishTask).where(VideoPublishTask.id == task.id)
    )
    assert reloaded is not None
    assert reloaded.cancel_requested is False
    assert "cancel" not in detail["allowedActions"]


def test_cancel_running_before_admission_sets_the_cancel_flag(
    db_session: Session,
) -> None:
    """02 §6.5: a running task that has not been admitted is stopped at the next
    stage boundary instead of being dropped to cancelled right away."""
    task = _review_task(status="running", stage="staging_device")
    task.attempts[0].status = "running"
    db_session.add(task)
    db_session.commit()

    with _action_client(db_session) as client:
        response = client.post(
            f"/v2/video-publish/tasks/{task.public_id}/cancel",
            json={"rowVersion": task.row_version},
            headers=HEADERS,
        )

    assert response.status_code == 200, response.text
    assert response.json()["status"] == "running"
    db_session.expire_all()
    reloaded = db_session.scalar(
        select(VideoPublishTask).where(VideoPublishTask.id == task.id)
    )
    assert reloaded is not None
    assert reloaded.cancel_requested is True


def test_settings_edit_before_submission_updates_all_fields(
    db_session: Session,
) -> None:
    """02 §9.5/§16.3: an unlocked pending task accepts a parameter edit."""
    task = _review_task(status="pending", stage="queued")
    db_session.add(task)
    db_session.commit()
    before = task.row_version

    with _action_client(db_session) as client:
        response = client.patch(
            f"/v2/video-publish/tasks/{task.public_id}/settings",
            json={
                "rowVersion": before,
                "prompt": "TEST_prompt_body",
                "deviceSerial": "TEST_device_two",
                "appPackage": "com.test.other",
            },
            headers=HEADERS,
        )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["rowVersion"] == before + 1
    assert body["target"]["deviceSerialMasked"] == mask_device_serial("TEST_device_two")
    assert "edit_settings" in body["allowedActions"]
    db_session.expire_all()
    reloaded = db_session.scalar(
        select(VideoPublishTask).where(VideoPublishTask.id == task.id)
    )
    assert reloaded is not None
    assert reloaded.prompt_override == "TEST_prompt_body"
    assert reloaded.target_device_serial == "TEST_device_two"
    assert reloaded.target_app_package == "com.test.other"


def test_settings_edit_is_refused_once_the_task_is_submitted(
    db_session: Session,
) -> None:
    """02 §16.3: params_locked_at is the permanent boundary, so the refusal wins
    over every other check and nothing is written."""
    task = _review_task(
        status="running",
        stage="waiting_artemis",
        params_locked_at=datetime.now(UTC),
    )
    db_session.add(task)
    db_session.commit()
    before = task.row_version

    with _action_client(db_session) as client:
        response = client.patch(
            f"/v2/video-publish/tasks/{task.public_id}/settings",
            json={
                "prompt": "TEST_should_not_apply",
                "deviceSerial": "TEST_device_three",
            },
            headers=HEADERS,
        )

    assert response.status_code == 409, response.text
    assert response.json()["detail"]["code"] == "TASK_ALREADY_SUBMITTED"
    db_session.expire_all()
    reloaded = db_session.scalar(
        select(VideoPublishTask).where(VideoPublishTask.id == task.id)
    )
    assert reloaded is not None
    assert reloaded.prompt_override is None
    assert reloaded.target_device_serial == "TEST_device"
    assert reloaded.row_version == before


def test_settings_edit_on_a_running_task_is_409_task_busy(db_session: Session) -> None:
    """02 §9.5: an unlocked running task is editable only after it settles."""
    task = _review_task(status="running", stage="downloading")
    db_session.add(task)
    db_session.commit()

    with _action_client(db_session) as client:
        response = client.patch(
            f"/v2/video-publish/tasks/{task.public_id}/settings",
            json={"prompt": "TEST_busy"},
            headers=HEADERS,
        )

    assert response.status_code == 409, response.text
    assert response.json()["detail"]["code"] == "TASK_BUSY"


def test_settings_edit_rejects_a_bad_field_without_partial_writes(
    db_session: Session,
) -> None:
    """02 §9.5: validation runs before any assignment, so a rejected edit leaves
    the stored parameters untouched."""
    task = _review_task(status="pending", stage="queued")
    db_session.add(task)
    db_session.commit()
    before = task.row_version

    with _action_client(db_session) as client:
        response = client.patch(
            f"/v2/video-publish/tasks/{task.public_id}/settings",
            json={"prompt": "TEST_valid", "deviceSerial": "bad serial!"},
            headers=HEADERS,
        )

    assert response.status_code == 422, response.text
    assert response.json()["detail"]["code"] == "DEVICE_SERIAL_INVALID"
    db_session.expire_all()
    reloaded = db_session.scalar(
        select(VideoPublishTask).where(VideoPublishTask.id == task.id)
    )
    assert reloaded is not None
    assert reloaded.prompt_override is None
    assert reloaded.row_version == before


def test_current_groups_in_flight_tasks_per_device(db_session: Session) -> None:
    """02 §17.2: /current returns every device's in-flight task, not one global
    task, so two devices can be tracked side by side."""
    running_a = _review_task(
        status="running", stage="waiting_artemis", device_serial="TEST_device_a"
    )
    running_b = _review_task(
        status="running", stage="staging_device", device_serial="TEST_device_b"
    )
    for row in (running_a, running_b):
        row.attempts[0].status = "running"
    db_session.add_all([running_a, running_b])
    db_session.commit()

    with _action_client(db_session) as client:
        response = client.get("/v2/video-publish/tasks/current")

    assert response.status_code == 200, response.text
    body = response.json()
    assert "task" not in body
    entries = {entry["deviceSerialMasked"]: entry for entry in body["tasks"]}
    assert set(entries) == {
        mask_device_serial("TEST_device_a"),
        mask_device_serial("TEST_device_b"),
    }
    by_stage = {entry["stage"] for entry in body["tasks"]}
    assert by_stage == {"waiting_artemis", "staging_device"}
    for entry in body["tasks"]:
        assert entry["currentAttempt"]["kindLabel"] == "正式发布"
