from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from tts_erp_v2.api import deps
from tts_erp_v2.api.v2 import video_publish


class _UploadStore:
    bucket = "tiktok-video"

    def presign_put(self, key: str, content_type: str) -> str:
        return f"https://upload.test/{key}"


def test_upload_ticket_http_statuses_and_foreign_replay_denial(
    db_session: Session, monkeypatch
) -> None:
    monkeypatch.setenv("ARTEMIS_DEVICE_SERIAL", "TEST_device")
    monkeypatch.setenv("TIKTOK_PUBLISH_MINIO_BUCKET", "tiktok-video")
    app = FastAPI()
    app.include_router(video_publish.router)

    @app.middleware("http")
    async def test_auth(request: Request, call_next):
        request.scope["api_key_role"] = "readwrite"
        request.scope["api_key_hash"] = request.headers.get("X-Test-Key", "key-a")
        return await call_next(request)

    store = _UploadStore()
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
        assert foreign.json() == {"detail": "TASK_NOT_FOUND"}

    app.dependency_overrides.clear()
