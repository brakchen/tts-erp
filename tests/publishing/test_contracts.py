import hashlib
from pathlib import Path
from types import SimpleNamespace
from typing import cast
from uuid import uuid4

import pytest

from tts_erp_v2.accounts.pages import required_page_permission
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


def test_concurrency_and_migration_contracts_include_active_protection() -> None:
    repository = (ROOT / "tts_erp_v2/publishing/repository.py").read_text()
    migration = (ROOT / "alembic/versions/0053_video_publish.py").read_text()
    assert "with_for_update(skip_locked=True)" in repository
    assert "with_for_update=True" in repository
    assert (
        "status IN ('created','submitting','queued','running','unknown')" in migration
    )


def test_frontend_keeps_idempotency_and_double_click_guards() -> None:
    source = (ROOT / "tts_erp_v2/static/js/video-publish.js").read_text()
    assert "state.creating=true" in source
    assert "state.clientRequestId=state.clientRequestId||crypto.randomUUID()" in source
    assert "!!state.upload||state.creating" in source
