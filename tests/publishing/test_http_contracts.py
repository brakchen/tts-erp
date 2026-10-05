from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from tts_erp_v2.api import deps
from tts_erp_v2.api.v2 import video_publish
from tts_erp_v2.db.models.publishing import PublishWorkerHeartbeat, VideoPublishTask


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
