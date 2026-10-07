from __future__ import annotations

from types import SimpleNamespace

import pytest

from tts_erp_v2.publishing.domain import (
    TaskStage,
    TaskStatus,
    allowed_actions,
    classify_failure,
)
from tts_erp_v2.publishing.prompt import build_publish_prompt, build_verify_prompt

pytestmark = [pytest.mark.domain_publishing]


def test_ambiguous_publish_requires_verification() -> None:
    result = classify_failure(artemis_status="failed", final_publish_observed=True)
    assert result.code == "publish_action_observed"
    assert result.requires_verification is True
    assert result.retry_safe is False


def test_retry_action_requires_safe_latest_publish_and_existing_object() -> None:
    safe = SimpleNamespace(
        status=TaskStatus.FAILED,
        stage=TaskStage.DONE,
        attempt_count=1,
        object_uploaded_at=object(),
        object_deleted_at=None,
        attempts=[SimpleNamespace(sequence_no=1, kind="publish", retry_safe=True)],
    )
    unsafe = SimpleNamespace(
        **{
            **safe.__dict__,
            "attempts": [
                SimpleNamespace(sequence_no=1, kind="publish", retry_safe=None)
            ],
        }
    )
    deleted = SimpleNamespace(**{**safe.__dict__, "object_deleted_at": object()})
    assert "retry" in [item.value for item in allowed_actions(safe)]
    assert "retry" not in [item.value for item in allowed_actions(unsafe)]
    deleted_actions = [item.value for item in allowed_actions(deleted)]
    assert "retry" not in deleted_actions
    assert "replace_upload" in deleted_actions
    exhausted = SimpleNamespace(**{**deleted.__dict__, "attempt_count": 3})
    assert "replace_upload" not in [item.value for item in allowed_actions(exhausted)]


def test_queue_actions_do_not_expose_retry() -> None:
    task = SimpleNamespace(
        status=TaskStatus.PENDING, stage=TaskStage.QUEUED, attempts=[]
    )
    assert "cancel" in [item.value for item in allowed_actions(task)]
    assert "retry" not in [item.value for item in allowed_actions(task)]


def test_prompts_keep_caption_as_json_data_and_verify_is_read_only() -> None:
    publish = build_publish_prompt(
        caption='line\n"emoji 🎉"',
        app_package="com.tiktok",
        device_path="/sdcard/Movies/TTSERP/a.mp4",
        album="TTSERP",
    )
    verify = build_verify_prompt(caption="hello", app_package="com.tiktok")
    assert '"line\\n\\"emoji 🎉\\""' in publish
    assert "at most once" in publish
    assert "never click Publish" in verify
