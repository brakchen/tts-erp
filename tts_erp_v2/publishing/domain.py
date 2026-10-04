"""Pure publishing state vocabulary and retry policy."""

from __future__ import annotations

from dataclasses import dataclass
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


class DomainTransitionError(ValueError):
    """Raised when a task command violates the publishing state machine."""


class AllowedAction(StrEnum):
    VIEW = "view"
    CONTINUE_UPLOAD = "continue_upload"
    CANCEL = "cancel"
    RETRY = "retry"
    VERIFY = "verify"
    RETRY_CLEANUP = "retry_cleanup"
    COPY_ARTEMIS_ID = "copy_artemis_id"


@dataclass(frozen=True, slots=True)
class FailureClassification:
    code: str
    retry_safe: bool | None
    requires_verification: bool = False


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
        ): (TaskStatus.RUNNING.value, TaskStage.CLEANING.value),
        (
            (TaskStatus.RUNNING.value, TaskStage.WAITING_ARTEMIS.value),
            "AMBIGUOUS_FAILURE",
        ): (TaskStatus.RUNNING.value, TaskStage.VERIFYING.value),
        ((TaskStatus.RUNNING.value, TaskStage.VERIFYING.value), "INCONCLUSIVE"): (
            TaskStatus.NEEDS_REVIEW.value,
            TaskStage.DONE.value,
        ),
        (
            (TaskStatus.RUNNING.value, TaskStage.CLEANING.value),
            "BUSINESS_SUCCESS_FINALIZED",
        ): (TaskStatus.SUCCEEDED.value, TaskStage.DONE.value),
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
    task.status, task.stage = result


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
        return FailureClassification("failed_before_publish_ui", True)
    return FailureClassification("session_missing", None, True)


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
    elif (
        task.status == TaskStatus.FAILED
        and getattr(task, "retry_safe", False)
        and task.attempt_count < 3
    ):
        actions.append(AllowedAction.RETRY)
    elif task.status == TaskStatus.NEEDS_REVIEW:
        actions.append(AllowedAction.VERIFY)
    if any(
        getattr(task, f"{name}_cleanup_status", None) == CleanupStatus.FAILED
        for name in ("device", "spool", "object")
    ):
        actions.append(AllowedAction.RETRY_CLEANUP)
    if getattr(task, "attempts", None):
        actions.append(AllowedAction.COPY_ARTEMIS_ID)
    return tuple(actions)
