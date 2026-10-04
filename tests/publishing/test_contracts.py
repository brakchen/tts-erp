from pathlib import Path
from types import SimpleNamespace
from typing import cast
from uuid import uuid4

import pytest

from tts_erp_v2.accounts.pages import required_page_permission
from tts_erp_v2.publishing.artemis_client import ArtemisResult
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


def test_frontend_keeps_idempotency_and_double_click_guards() -> None:
    source = (ROOT / "tts_erp_v2/static/js/video-publish.js").read_text()
    assert "state.creating=true" in source
    assert "state.clientRequestId=state.clientRequestId||crypto.randomUUID()" in source
    assert "!!state.upload||state.creating" in source
