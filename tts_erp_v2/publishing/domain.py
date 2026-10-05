"""Pure publishing state vocabulary and retry policy."""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any


class TaskStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    NEEDS_REVIEW = "needs_review"
    CANCELLED = "cancelled"


class TaskStage(StrEnum):
    AWAITING_UPLOAD = "awaiting_upload"
    QUEUED = "queued"
    WAITING_DEVICE = "waiting_device"
    DOWNLOADING = "downloading"
    STAGING_DEVICE = "staging_device"
    DISPATCHING_ARTEMIS = "dispatching_artemis"
    WAITING_ARTEMIS = "waiting_artemis"
    VERIFYING = "verifying"
    CLEANING = "cleaning"
    DONE = "done"


class AttemptKind(StrEnum):
    PUBLISH = "publish"
    VERIFY = "verify"


class AttemptStatus(StrEnum):
    CREATED = "created"
    SUBMITTING = "submitting"
    QUEUED = "queued"
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"
    REJECTED = "rejected"
    CANCELLED = "cancelled"
    UNKNOWN = "unknown"


class CleanupStatus(StrEnum):
    NOT_STARTED = "not_started"
    PENDING = "pending"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class CleanupIntent(StrEnum):
    NONE = "none"
    FINALIZE_SUCCESS = "finalize_success"
    REQUEUE_PUBLISH = "requeue_publish"
    PRESERVE_STATE = "preserve_state"


@dataclass(frozen=True, slots=True)
class CleanupPlan:
    intent: CleanupIntent
    device: bool = False
    spool: bool = False
    object: bool = False


class DomainTransitionError(ValueError):
    """Raised when a task command violates the publishing state machine."""


class AllowedAction(StrEnum):
    VIEW = "view"
    CONTINUE_UPLOAD = "continue_upload"
    CANCEL = "cancel"
    RETRY = "retry"
    VERIFY = "verify"
    RETRY_CLEANUP = "retry_cleanup"
    REPLACE_UPLOAD = "replace_upload"
    COPY_ARTEMIS_ID = "copy_artemis_id"


@dataclass(frozen=True, slots=True)
class FailureClassification:
    code: str
    retry_safe: bool | None
    requires_verification: bool = False


def _set_cleanup_status(task: Any, name: str, status: CleanupStatus) -> None:
    setattr(task, f"{name}_cleanup_status", status.value)
    setattr(task, f"{name}_cleanup_next_attempt_at", None)


def plan_cleanup(
    task: Any,
    intent: CleanupIntent,
    *,
    device: bool = False,
    spool: bool = False,
    object: bool = False,
) -> CleanupPlan:
    """Persist an explicit resource plan; never infer deletion from stage."""
    task.cleanup_intent = intent.value
    for name, selected in (("device", device), ("spool", spool), ("object", object)):
        if (
            selected
            and getattr(task, f"{name}_cleanup_status")
            == CleanupStatus.NOT_STARTED.value
        ):
            _set_cleanup_status(task, name, CleanupStatus.PENDING)
    return CleanupPlan(intent, device=device, spool=spool, object=object)


def set_task_stage(
    task: Any, stage: TaskStage | str, *, now: datetime | None = None
) -> None:
    """Persist the start of every real task-stage transition."""
    value = stage.value if isinstance(stage, TaskStage) else stage
    if task.stage != value or getattr(task, "stage_started_at", None) is None:
        task.stage = value
        task.stage_started_at = now or datetime.now(UTC)


def apply_cleanup_result(task: Any, *, device_failed: bool = False) -> None:
    """Apply the sole post-cleanup business transition."""
    intent = CleanupIntent(getattr(task, "cleanup_intent", CleanupIntent.NONE.value))
    if intent is CleanupIntent.REQUEUE_PUBLISH:
        if device_failed:
            task.status = TaskStatus.PENDING.value
            set_task_stage(task, TaskStage.WAITING_DEVICE)
        else:
            task.status = TaskStatus.PENDING.value
            set_task_stage(task, TaskStage.QUEUED)
        return
    if intent is CleanupIntent.FINALIZE_SUCCESS:
        task.status = TaskStatus.SUCCEEDED.value
        set_task_stage(task, TaskStage.DONE)
        return
    if intent is CleanupIntent.PRESERVE_STATE:
        set_task_stage(task, TaskStage.DONE)
        return


def transition_task(task: Any, event: str) -> None:
    """Apply the approved business transition without accepting arbitrary states."""
    current = (str(task.status), str(task.stage))
    transitions = {
        (
            (TaskStatus.PENDING.value, TaskStage.AWAITING_UPLOAD.value),
            "UPLOAD_CONFIRMED",
        ): (TaskStatus.PENDING.value, TaskStage.QUEUED.value),
        ((TaskStatus.PENDING.value, TaskStage.QUEUED.value), "CLAIM"): (
            TaskStatus.RUNNING.value,
            TaskStage.DOWNLOADING.value,
        ),
        ((TaskStatus.PENDING.value, TaskStage.WAITING_DEVICE.value), "DEVICE_READY"): (
            TaskStatus.PENDING.value,
            TaskStage.QUEUED.value,
        ),
        ((TaskStatus.RUNNING.value, TaskStage.DOWNLOADING.value), "DOWNLOAD_OK"): (
            TaskStatus.RUNNING.value,
            TaskStage.STAGING_DEVICE.value,
        ),
        ((TaskStatus.RUNNING.value, TaskStage.STAGING_DEVICE.value), "MEDIA_VISIBLE"): (
            TaskStatus.RUNNING.value,
            TaskStage.DISPATCHING_ARTEMIS.value,
        ),
        (
            (TaskStatus.RUNNING.value, TaskStage.DISPATCHING_ARTEMIS.value),
            "ARTEMIS_ADMITTED",
        ): (TaskStatus.RUNNING.value, TaskStage.WAITING_ARTEMIS.value),
        (
            (TaskStatus.RUNNING.value, TaskStage.WAITING_ARTEMIS.value),
            "ARTEMIS_SUCCESS",
        ): (TaskStatus.SUCCEEDED.value, TaskStage.DONE.value),
        (
            (TaskStatus.RUNNING.value, TaskStage.DISPATCHING_ARTEMIS.value),
            "ARTEMIS_SUCCESS",
        ): (TaskStatus.SUCCEEDED.value, TaskStage.DONE.value),
        (
            (TaskStatus.RUNNING.value, TaskStage.VERIFYING.value),
            "VERIFY_PUBLISHED",
        ): (TaskStatus.SUCCEEDED.value, TaskStage.DONE.value),
        (
            (TaskStatus.RUNNING.value, TaskStage.WAITING_ARTEMIS.value),
            "AMBIGUOUS_FAILURE",
        ): (TaskStatus.RUNNING.value, TaskStage.VERIFYING.value),
        ((TaskStatus.RUNNING.value, TaskStage.VERIFYING.value), "INCONCLUSIVE"): (
            TaskStatus.NEEDS_REVIEW.value,
            TaskStage.DONE.value,
        ),
        (
            (TaskStatus.RUNNING.value, TaskStage.VERIFYING.value),
            "VERIFY_NOT_PUBLISHED",
        ): (TaskStatus.PENDING.value, TaskStage.WAITING_DEVICE.value),
        (
            (TaskStatus.RUNNING.value, TaskStage.STAGING_DEVICE.value),
            "SAFE_RETRY",
        ): (TaskStatus.PENDING.value, TaskStage.WAITING_DEVICE.value),
        (
            (TaskStatus.RUNNING.value, TaskStage.DISPATCHING_ARTEMIS.value),
            "SAFE_RETRY",
        ): (TaskStatus.PENDING.value, TaskStage.WAITING_DEVICE.value),
        (
            (TaskStatus.RUNNING.value, TaskStage.WAITING_ARTEMIS.value),
            "SAFE_RETRY",
        ): (TaskStatus.PENDING.value, TaskStage.WAITING_DEVICE.value),
        (
            (TaskStatus.RUNNING.value, TaskStage.VERIFYING.value),
            "SAFE_RETRY",
        ): (TaskStatus.PENDING.value, TaskStage.WAITING_DEVICE.value),
        (
            (TaskStatus.RUNNING.value, TaskStage.STAGING_DEVICE.value),
            "SAFE_RETRY_EXHAUSTED",
        ): (TaskStatus.FAILED.value, TaskStage.DONE.value),
        (
            (TaskStatus.RUNNING.value, TaskStage.DISPATCHING_ARTEMIS.value),
            "SAFE_RETRY_EXHAUSTED",
        ): (TaskStatus.FAILED.value, TaskStage.DONE.value),
        (
            (TaskStatus.RUNNING.value, TaskStage.WAITING_ARTEMIS.value),
            "SAFE_RETRY_EXHAUSTED",
        ): (TaskStatus.FAILED.value, TaskStage.DONE.value),
        (
            (TaskStatus.RUNNING.value, TaskStage.VERIFYING.value),
            "SAFE_RETRY_EXHAUSTED",
        ): (TaskStatus.FAILED.value, TaskStage.DONE.value),
        ((TaskStatus.FAILED.value, TaskStage.DONE.value), "USER_RETRY"): (
            TaskStatus.PENDING.value,
            TaskStage.QUEUED.value,
        ),
        ((TaskStatus.NEEDS_REVIEW.value, TaskStage.DONE.value), "USER_VERIFY"): (
            TaskStatus.RUNNING.value,
            TaskStage.VERIFYING.value,
        ),
    }
    result = transitions.get((current, event))
    if result is None:
        raise DomainTransitionError(f"{event} is not allowed from {current}")
    task.status = result[0]
    set_task_stage(task, result[1])
    if event in {"ARTEMIS_SUCCESS", "VERIFY_PUBLISHED", "BUSINESS_SUCCESS_FINALIZED"}:
        plan_cleanup(
            task,
            CleanupIntent.FINALIZE_SUCCESS,
            device=True,
            spool=True,
            object=True,
        )
    elif event in {"SAFE_RETRY", "VERIFY_NOT_PUBLISHED"}:
        plan_cleanup(task, CleanupIntent.REQUEUE_PUBLISH, device=True)
    elif event in {"SAFE_RETRY_EXHAUSTED", "INCONCLUSIVE"}:
        plan_cleanup(task, CleanupIntent.PRESERVE_STATE, device=True)


def classify_failure(
    *,
    artemis_status: str,
    steps_count: int | None = None,
    final_publish_observed: bool = False,
    session_missing: bool = False,
) -> FailureClassification:
    """Classify only stable execution facts, never free-form error text."""
    if final_publish_observed:
        return FailureClassification("publish_action_observed", False, True)
    if session_missing:
        return FailureClassification("session_missing", None, True)
    if steps_count == 0:
        return FailureClassification("planner_zero_steps", True)
    status = artemis_status.lower()
    if status in {"rejected", "cancelled", "canceled"}:
        return FailureClassification("admission_rejected", True)
    if status in {"failed", "error"}:
        # A failed transport/execution report does not prove that the final
        # publish action was not observed. Only an explicit zero-step result
        # is safe to retry without a read-only verification session.
        return FailureClassification("ambiguous_artemis_failure", None, True)
    return FailureClassification("session_missing", None, True)


def replace_upload_allowed(task: Any) -> bool:
    return (
        task.status == TaskStatus.FAILED.value
        and task.object_deleted_at is not None
        and task.attempt_count < int(os.environ.get("TIKTOK_PUBLISH_MAX_ATTEMPTS", "3"))
    )


def cleanup_retryable_resources(task: Any) -> tuple[str, ...]:
    """Single policy used by list snapshots and cleanup retry commands."""
    if getattr(task, "status", None) == TaskStatus.RUNNING:
        return ()
    return tuple(
        name
        for name in ("device", "spool", "object")
        if getattr(task, f"{name}_cleanup_status", None) == CleanupStatus.FAILED.value
    )


def allowed_actions(task: Any) -> tuple[AllowedAction, ...]:
    """Compute UI actions from persisted server state."""
    actions: list[AllowedAction] = [AllowedAction.VIEW]
    if task.status == TaskStatus.PENDING and task.stage == TaskStage.AWAITING_UPLOAD:
        actions += [AllowedAction.CONTINUE_UPLOAD, AllowedAction.CANCEL]
    elif task.status == TaskStatus.PENDING and task.stage in {
        TaskStage.QUEUED,
        TaskStage.WAITING_DEVICE,
    }:
        actions.append(AllowedAction.CANCEL)
    elif task.status == TaskStatus.FAILED and task.attempt_count < int(
        os.environ.get("TIKTOK_PUBLISH_MAX_ATTEMPTS", "3")
    ):
        attempts = getattr(task, "attempts", ()) or ()
        latest = max(attempts, key=lambda attempt: attempt.sequence_no, default=None)
        if replace_upload_allowed(task):
            actions.append(AllowedAction.REPLACE_UPLOAD)
        elif (
            latest is not None
            and latest.kind == "publish"
            and latest.retry_safe is True
            and task.object_uploaded_at is not None
        ):
            actions.append(AllowedAction.RETRY)
    elif task.status == TaskStatus.NEEDS_REVIEW and getattr(
        task, "device_cleanup_status", None
    ) not in {CleanupStatus.PENDING.value, CleanupStatus.FAILED.value}:
        actions.append(AllowedAction.VERIFY)
    if cleanup_retryable_resources(task):
        actions.append(AllowedAction.RETRY_CLEANUP)
    if getattr(task, "attempts", None):
        actions.append(AllowedAction.COPY_ARTEMIS_ID)
    return tuple(actions)
