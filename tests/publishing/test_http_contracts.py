from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from tts_erp_v2.api import deps
from tts_erp_v2.api.v2 import video_publish
from tts_erp_v2.db.models.publishing import (
    PublishWorkerHeartbeat,
    VideoPublishAttempt,
    VideoPublishTask,
)
from tts_erp_v2.publishing.artemis_client import ArtemisClient, ArtemisTransportError
from tts_erp_v2.publishing.safe_values import mask_device_serial


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
        return db_session.scalar(
            select(VideoPublishTask).where(
                VideoPublishTask.client_request_id == UUID(request_id)
            )
        )

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
