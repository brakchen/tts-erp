from __future__ import annotations

import hashlib
from pathlib import Path
from types import SimpleNamespace
from typing import cast
from uuid import uuid4

import pytest
from fastapi import Request, Response
from sqlalchemy import Table
from sqlalchemy.orm import Session

from tts_erp_v2.accounts.pages import required_page_permission
from tts_erp_v2.api.v2 import video_publish
from tts_erp_v2.api.v2.video_publish import _conditional, _etag
from tts_erp_v2.publishing.artemis_client import ArtemisResult
from tts_erp_v2.publishing.dispatcher import (
    PublishDependencies,
    _query_after_submit_transport_error,
)
from tts_erp_v2.publishing.domain import classify_failure
from tts_erp_v2.publishing.object_store import MinioVideoStore
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
            "api_key_role": "admin",
        }
    )
    monkeypatch.setattr(video_publish, "_task", lambda session, task_id: object())
    monkeypatch.setattr(video_publish, "_snapshot", lambda task, **kwargs: payload)
    monkeypatch.setattr(video_publish, "_queue_position", lambda session, task: None)
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

    sdk = SimpleNamespace(get_object=lambda bucket, key, request_headers=None: Body())
    client = cast(
        MinioClient,
        SimpleNamespace(bucket="tiktok-video", _sdk=sdk),
    )
    destination = tmp_path / "video.mp4"
    digest = MinioVideoStore(client).download("video-key", destination, "TEST-etag")
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
            self.calls.append(
                f"submit:{kwargs['session_id']}:{kwargs['profile']}:"
                f"{kwargs['verification_level']}"
            )
            return ArtemisResult(kwargs["session_id"], "queued")

    artemis = Artemis()
    result = await _query_after_submit_transport_error(
        cast(
            PublishDependencies,
            SimpleNamespace(
                artemis=artemis,
                artemis_profile="TEST_profile",
                artemis_verification_level="TEST_verification",
            ),
        ),
        session_id,
        "goal",
        "device",
        "com.tiktok",
        "TEST_profile",
        "TEST_verification",
    )
    assert result.status == "queued"
    assert artemis.calls == [
        f"get:{session_id}",
        f"submit:{session_id}:TEST_profile:TEST_verification",
    ]


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


def test_concurrency_and_migration_contracts_include_active_protection() -> None:
    repository = (ROOT / "tts_erp_v2/publishing/repository.py").read_text()
    migration = (ROOT / "alembic/versions/0053_video_publish.py").read_text()
    assert "with_for_update(skip_locked=True)" in repository
    assert ".with_for_update()" in repository
    assert "def commit_publish_transition(" in repository
    assert "VideoPublishTask.lease_expires_at > now" in repository
    assert "VideoPublishTask.row_version == token.row_version" in repository
    assert 'cte("cleanup_candidate")' in repository
    assert (
        "status IN ('created','submitting','queued','running','unknown')" in migration
    )


def test_tracked_spool_deletion_has_one_cleanup_executor_owner() -> None:
    dispatcher = (ROOT / "tts_erp_v2/publishing/dispatcher.py").read_text()
    worker = (ROOT / "tts_erp_v2/publishing/worker.py").read_text()
    assert "_mark_spool_cleanup" not in dispatcher
    assert "shutil.rmtree" not in worker
    assert "unlink(" not in worker
    assert dispatcher.count('await run(\n            "spool"') == 1


def test_worker_wires_documented_artemis_and_timing_knobs() -> None:
    source = (ROOT / "tts_erp_v2/publishing/worker.py").read_text()
    assert 'os.environ.get("ARTEMIS_PROFILE", "pro")' in source
    assert '"ARTEMIS_VERIFICATION_LEVEL", "strict"' in source
    assert 'os.environ.get("PUBLISH_POLL_INTERVAL_SECONDS", "2")' in source
    assert "PUBLISH_WORKER_POLL_SECONDS" not in source
    assert 'os.environ.get("PUBLISH_TASK_LEASE_SECONDS", "30")' in source
    assert 'os.environ.get("PUBLISH_WORKER_HEARTBEAT_SECONDS", "5")' in source
    assert "await asyncio.to_thread(" in source
    assert "_cleanup_orphan_spool" in source


def test_queued_content_warning_is_immutable_and_immediate() -> None:
    source = (ROOT / "tts_erp_v2/templates/pages/video-publish.html").read_text()
    assert "设备空闲时任务可能立即开始" in source
    assert "入队后不能修改视频和文案" in source


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
    assert "shared.queued ? 8" in source
    assert "function sharedPollState()" in source
    assert "localStorage.setItem(REFRESH_KEY, mode)" in source
    assert "Array.from(value).length" in source
    assert ".maxLength =" not in source
    assert "[5, 10, 30, 60]" in source
    assert 'window.addEventListener("pagehide", destroy)' in source
    assert "operationalStageStartedAt" in source
    assert "publish-preview-metadata" in source
    assert "task.createdBy" in source
    assert "navigator.clipboard.writeText" in source
    assert 'document.execCommand("copy")' in source


def test_publish_model_metadata_matches_schema_indexes_and_constraints() -> None:
    from tts_erp_v2.db.models.publishing import (
        VideoPublishAttempt,
        VideoPublishTask,
    )

    task_table = cast(Table, VideoPublishTask.__table__)
    attempt_table = cast(Table, VideoPublishAttempt.__table__)
    task_indexes = {str(index.name): index for index in task_table.indexes}
    attempt_indexes = {str(index.name): index for index in attempt_table.indexes}
    task_constraints = {str(constraint.name) for constraint in task_table.constraints}
    attempt_constraints = {
        str(constraint.name) for constraint in attempt_table.constraints
    }
    assert "DESC" in str(task_indexes["ix_video_publish_history"].expressions[0])
    assert "DESC" in str(task_indexes["ix_video_publish_history"].expressions[1])
    assert "ix_video_publish_cleanup_queue" in task_indexes
    assert "ix_video_publish_attempt_task_seq" in attempt_indexes
    assert "video_publish_task_cleanup_status_check" in task_constraints
    assert "video_publish_attempt_related_check" in attempt_constraints
    assert "uq_video_publish_attempt_task_seq" in attempt_constraints


def test_publish_responsive_accessibility_contract_uses_existing_css() -> None:
    css = (ROOT / "tts_erp_v2/static/css/video-publish.css").read_text()
    template = (ROOT / "tts_erp_v2/templates/pages/video-publish.html").read_text()
    assert "max-width:1440px" in css
    assert "max-width:900px" in css
    assert "max-width:390px" in css
    assert "prefers-reduced-motion:reduce" in css
    assert 'class="btn-icon drawer-close"' in template
    assert 'id="publish-preview-metadata"' in template
    assert 'maxlength="4000"' not in template
