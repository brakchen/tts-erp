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


def test_ambiguous_publish_waits_for_a_human_decision() -> None:
    result = classify_failure(artemis_status="failed", final_publish_observed=True)
    assert result.code == "publish_action_observed"
    # 02 §7.3: ambiguous results land in needs_review and never auto-verify.
    assert result.ambiguous is True


def test_zero_step_result_is_a_certain_failure() -> None:
    result = classify_failure(artemis_status="failed", steps_count=0)
    assert result.code == "planner_zero_steps"
    assert result.ambiguous is False


def test_retry_action_requires_idle_task_and_existing_object() -> None:
    finished = SimpleNamespace(
        status=TaskStatus.FAILED,
        stage=TaskStage.DONE,
        object_uploaded_at=object(),
        object_deleted_at=None,
        attempts=[SimpleNamespace(sequence_no=1, kind="publish", status="failed")],
    )
    active = SimpleNamespace(
        **{
            **finished.__dict__,
            "attempts": [
                SimpleNamespace(sequence_no=1, kind="publish", status="running")
            ],
        }
    )
    deleted = SimpleNamespace(**{**finished.__dict__, "object_deleted_at": object()})
    assert "retry" in [item.value for item in allowed_actions(finished)]
    # No retry_safe gate any more: an active attempt is the only blocker.
    assert "retry" not in [item.value for item in allowed_actions(active)]
    deleted_actions = [item.value for item in allowed_actions(deleted)]
    assert "retry" not in deleted_actions
    assert "replace_upload" in deleted_actions
    # No publish budget: a long attempt history still allows replacement.
    exhausted = SimpleNamespace(
        **{**deleted.__dict__, "attempt_count": 3, "publish_budget_used": 3}
    )
    assert "replace_upload" in [item.value for item in allowed_actions(exhausted)]


def test_queue_actions_do_not_expose_retry() -> None:
    task = SimpleNamespace(
        status=TaskStatus.PENDING, stage=TaskStage.QUEUED, attempts=[]
    )
    assert "cancel" in [item.value for item in allowed_actions(task)]
    assert "retry" not in [item.value for item in allowed_actions(task)]


def test_cancel_is_withdrawn_once_parameters_are_locked() -> None:
    pending = SimpleNamespace(
        status=TaskStatus.PENDING,
        stage=TaskStage.QUEUED,
        params_locked_at=None,
        attempts=[],
    )
    running = SimpleNamespace(
        status=TaskStatus.RUNNING,
        stage=TaskStage.STAGING_DEVICE,
        params_locked_at=None,
        attempts=[],
    )
    locked = SimpleNamespace(
        **{
            **running.__dict__,
            "params_locked_at": object(),
            "stage": TaskStage.WAITING_ARTEMIS,
        }
    )
    assert "cancel" in [item.value for item in allowed_actions(pending)]
    assert "cancel" in [item.value for item in allowed_actions(running)]
    # 02 §6.5: a submitted task can no longer be cancelled.
    assert "cancel" not in [item.value for item in allowed_actions(locked)]


def test_confirm_published_is_offered_for_needs_review_and_failed() -> None:
    idle = {
        "attempts": [SimpleNamespace(sequence_no=1, kind="publish", status="failed")],
        "cleanup_intent": "none",
        "device_cleanup_status": "not_started",
        "object_uploaded_at": object(),
        "object_deleted_at": None,
        "params_locked_at": object(),
    }
    review = SimpleNamespace(
        **{
            **idle,
            "status": TaskStatus.NEEDS_REVIEW,
            "stage": TaskStage.DONE,
        }
    )
    failed = SimpleNamespace(
        **{**idle, "status": TaskStatus.FAILED, "stage": TaskStage.DONE}
    )
    running = SimpleNamespace(
        **{
            **idle,
            "attempts": [
                SimpleNamespace(sequence_no=2, kind="verify", status="running")
            ],
            "status": TaskStatus.NEEDS_REVIEW,
            "stage": TaskStage.VERIFYING,
        }
    )
    review_actions = [item.value for item in allowed_actions(review)]
    assert "confirm_published" in review_actions
    assert "verify" in review_actions
    assert "confirm_published" in [item.value for item in allowed_actions(failed)]
    # A running verification keeps the device slot; no human action is offered.
    assert "confirm_published" not in [item.value for item in allowed_actions(running)]


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
