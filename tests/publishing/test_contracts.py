import hashlib
from pathlib import Path
from types import SimpleNamespace
from typing import cast
from uuid import uuid4

import pytest
from fastapi import Request, Response
from sqlalchemy.orm import Session

from tts_erp_v2.accounts.pages import required_page_permission
from tts_erp_v2.api.v2 import video_publish
from tts_erp_v2.api.v2.video_publish import _conditional, _etag
from tts_erp_v2.db.models.publishing import VideoPublishTask
from tts_erp_v2.publishing.artemis_client import ArtemisResult
from tts_erp_v2.publishing.dispatcher import (
    PublishDependencies,
    _query_after_submit_transport_error,
)
from tts_erp_v2.publishing.domain import classify_failure
from tts_erp_v2.publishing.object_store import MinioVideoStore
from tts_erp_v2.publishing.repository import touch_task
from tts_erp_v2.storage.minio_client import MinioClient

ROOT = Path(__file__).parents[2]


def test_failed_artemis_result_requires_read_only_verification() -> None:
    result = classify_failure(artemis_status="failed", steps_count=4)
    assert result.requires_verification is True
    assert result.retry_safe is None


def test_missing_artemis_session_is_terminal_for_classification() -> None:
    result = ArtemisResult(session_id=uuid4(), status="missing")
    assert result.terminal is True
    classification = classify_failure(artemis_status="missing")
    assert classification.requires_verification is True


def test_detail_api_returns_304_for_matching_etag(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = {"taskId": "task", "status": "running"}
    tag = _etag(payload)
    request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/v2/video-publish/tasks/task",
            "headers": [(b"if-none-match", tag.encode())],
        }
    )
    monkeypatch.setattr(video_publish, "_task", lambda session, task_id: object())
    monkeypatch.setattr(video_publish, "_snapshot", lambda task, **kwargs: payload)
    response = video_publish.detail(
        uuid4(), request, cast(Session, None), Response(), include_diagnostics=False
    )
    assert isinstance(response, Response)
    assert response.status_code == 304


def test_detail_conditional_response_returns_304() -> None:
    payload = {"taskId": "task", "status": "running"}
    tag = _etag(payload)
    request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/v2/video-publish/tasks/task",
            "headers": [(b"if-none-match", tag.encode())],
        }
    )
    cached = _conditional(request, Response(), payload)
    assert cached is not None
    assert cached.status_code == 304


def test_etag_ignores_fresh_server_time_metadata() -> None:
    first = _etag({"items": [], "serverTime": "2026-10-04T00:00:00Z"})
    second = _etag({"items": [], "serverTime": "2026-10-04T00:00:05Z"})
    assert first == second


def test_video_publish_api_requires_page_permission_for_session_users() -> None:
    assert required_page_permission("/v2/video-publish/tasks") == "page:video-publish"
    assert required_page_permission("/v2/pages/video-publish") == "page:video-publish"


def test_publish_store_rejects_non_dedicated_bucket(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TIKTOK_PUBLISH_MINIO_BUCKET", "tiktok-video")
    with pytest.raises(ValueError, match="PUBLISH_BUCKET_MISMATCH"):
        MinioVideoStore(cast(MinioClient, SimpleNamespace(bucket="general")))


def test_minio_download_streams_to_private_spool(tmp_path: Path) -> None:
    class Body:
        def stream(self, _size: int):
            yield b"video"

        def close(self) -> None:
            pass

        def release_conn(self) -> None:
            pass

    sdk = SimpleNamespace(get_object=lambda bucket, key: Body())
    client = cast(
        MinioClient,
        SimpleNamespace(bucket="tiktok-video", _sdk=sdk),
    )
    destination = tmp_path / "video.mp4"
    digest = MinioVideoStore(client).download("video-key", destination)
    assert destination.read_bytes() == b"video"
    assert digest == hashlib.sha256(b"video").hexdigest()


@pytest.mark.asyncio
async def test_transport_recovery_queries_before_same_session_resubmit() -> None:
    session_id = uuid4()

    class Artemis:
        def __init__(self) -> None:
            self.calls: list[str] = []

        async def get_task(self, value):
            self.calls.append(f"get:{value}")
            return ArtemisResult(value, "missing")

        async def submit(self, **kwargs):
            self.calls.append(f"submit:{kwargs['session_id']}")
            return ArtemisResult(kwargs["session_id"], "queued")

    artemis = Artemis()
    result = await _query_after_submit_transport_error(
        cast(PublishDependencies, SimpleNamespace(artemis=artemis)),
        session_id,
        "goal",
        "device",
        "com.tiktok",
    )
    assert result.status == "queued"
    assert artemis.calls == [f"get:{session_id}", f"submit:{session_id}"]


def test_cleanup_guard_precedes_adb_and_object_deletion() -> None:
    source = (ROOT / "tts_erp_v2/publishing/dispatcher.py").read_text()
    guard = source.index('script_name="video_publish.cleanup_resources"')
    assert guard < source.index("remove_staged_video", guard)
    assert guard < source.index("deps.store.remove", guard)


def test_destructive_guard_refuses_prod_shape_without_opt_in(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tts_erp_v2.api.deps import require_destructive_script_guard

    monkeypatch.setenv("TTS_ERP_DB_URL", "postgresql://example/tts_erp")
    monkeypatch.delenv("ALLOW_PROD_DESTRUCTIVE", raising=False)
    with pytest.raises(SystemExit):
        require_destructive_script_guard(
            script_name="video_publish.cleanup_object",
            confirmation=True,
            dangerous=True,
        )


def test_lease_renewal_extends_owner_heartbeat() -> None:
    task = SimpleNamespace(heartbeat_at=None, lease_expires_at=None, row_version=4)
    touch_task(cast(Session, None), cast(VideoPublishTask, task), lease_seconds=45)
    assert task.heartbeat_at is not None
    assert task.lease_expires_at > task.heartbeat_at
    assert task.row_version == 5


def test_concurrency_and_migration_contracts_include_active_protection() -> None:
    repository = (ROOT / "tts_erp_v2/publishing/repository.py").read_text()
    migration = (ROOT / "alembic/versions/0053_video_publish.py").read_text()
    assert "with_for_update(skip_locked=True)" in repository
    assert "with_for_update=True" in repository
    dispatcher = (ROOT / "tts_erp_v2/publishing/dispatcher.py").read_text()
    assert "VideoPublishTask.row_version == version" in dispatcher
    assert (
        "status IN ('created','submitting','queued','running','unknown')" in migration
    )


def test_frontend_keeps_idempotency_and_double_click_guards() -> None:
    source = (ROOT / "tts_erp_v2/static/js/video-publish.js").read_text()
    assert "state.creating=true" in source
    assert (
        "state.clientRequestId = state.clientRequestId || crypto.randomUUID()" in source
    )
    assert "!!state.upload || state.creating" in source
    assert 'headers["If-None-Match"]' in source
    assert "new AbortController()" in source
    assert "document.hidden" in source
    assert "state.currentChannel.failures" in source
    assert "state.listChannel.failures" in source
    assert "state.detailChannel.failures" in source
    assert "scheduleCurrent(mode)" in source
    assert "scheduleList(mode)" in source
    assert "scheduleDetail(mode)" in source
    assert "? (state.currentTask ? 5 : hasQueued ? 8 : 30)" in source
    assert "localStorage.setItem(REFRESH_KEY, mode)" in source
