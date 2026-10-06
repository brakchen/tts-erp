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


def apply_cleanup_result(
    task: Any, *, device_failed: bool = False, now: datetime | None = None
) -> None:
    """Apply the sole post-cleanup business transition."""
    intent = CleanupIntent(getattr(task, "cleanup_intent", CleanupIntent.NONE.value))
    if intent is CleanupIntent.REQUEUE_PUBLISH:
        if device_failed:
            task.status = TaskStatus.PENDING.value
            set_task_stage(task, TaskStage.WAITING_DEVICE, now=now)
        else:
            task.status = TaskStatus.PENDING.value
            set_task_stage(task, TaskStage.QUEUED, now=now)
        return
    if intent is CleanupIntent.FINALIZE_SUCCESS:
        task.status = TaskStatus.SUCCEEDED.value
        set_task_stage(task, TaskStage.DONE, now=now)
        return
    if intent is CleanupIntent.PRESERVE_STATE:
        set_task_stage(task, TaskStage.DONE, now=now)
        return


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
    status = artemis_status.lower()
    if status in {"rejected", "cancelled", "canceled"}:
        return FailureClassification("ambiguous_terminal_execution", None, True)
    if steps_count == 0:
        return FailureClassification("planner_zero_steps", True)
    if status in {"failed", "error"}:
        # A failed transport/execution report does not prove that the final
        # publish action was not observed. Only an explicit zero-step result
        # is safe to retry without a read-only verification session.
        return FailureClassification("ambiguous_artemis_failure", None, True)
    return FailureClassification("session_missing", None, True)


def replacement_cleanup_pending(task: Any) -> bool:
    """Replacement cannot erase cleanup ownership for old local resources."""
    return any(
        getattr(task, f"{name}_cleanup_status", None)
        in {CleanupStatus.PENDING.value, CleanupStatus.FAILED.value}
        for name in ("device", "spool")
    )


def object_cleanup_blocks_input(task: Any) -> bool:
    """Retention deletion must finish before retry or replacement mutates input."""
    return (
        getattr(task, "object_cleanup_status", None)
        in {CleanupStatus.PENDING.value, CleanupStatus.FAILED.value}
        or getattr(task, "cleanup_lease_owner", None) is not None
    )


def replace_upload_allowed(task: Any) -> bool:
    return (
        task.status == TaskStatus.FAILED.value
        and task.object_deleted_at is not None
        and not replacement_cleanup_pending(task)
        and not object_cleanup_blocks_input(task)
        and getattr(task, "publish_budget_used", task.attempt_count)
        < int(os.environ.get("TIKTOK_PUBLISH_MAX_ATTEMPTS", "3"))
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


_LATEST_ATTEMPT_UNSET = object()
_HAS_ATTEMPTS_UNSET = object()


def allowed_actions(
    task: Any,
    *,
    latest_attempt: Any = _LATEST_ATTEMPT_UNSET,
    has_attempts: Any = _HAS_ATTEMPTS_UNSET,
) -> tuple[AllowedAction, ...]:
    """Compute UI actions without lazy loading when a summary is supplied."""
    attempts = None
    if latest_attempt is _LATEST_ATTEMPT_UNSET or has_attempts is _HAS_ATTEMPTS_UNSET:
        attempts = getattr(task, "attempts", ()) or ()
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
        and getattr(task, "publish_budget_used", task.attempt_count)
        < int(os.environ.get("TIKTOK_PUBLISH_MAX_ATTEMPTS", "3"))
        and not object_cleanup_blocks_input(task)
    ):
        latest = (
            max(
                attempts or (),
                key=lambda attempt: attempt.sequence_no,
                default=None,
            )
            if latest_attempt is _LATEST_ATTEMPT_UNSET
            else latest_attempt
        )
        if replace_upload_allowed(task):
            actions.append(AllowedAction.REPLACE_UPLOAD)
        elif (
            latest is not None
            and latest.kind == "publish"
            and latest.retry_safe is True
            and task.object_uploaded_at is not None
        ):
            actions.append(AllowedAction.RETRY)
    elif (
        task.status == TaskStatus.NEEDS_REVIEW
        and getattr(task, "cleanup_intent", CleanupIntent.NONE.value)
        == CleanupIntent.NONE.value
        and getattr(task, "device_cleanup_status", None)
        not in {CleanupStatus.PENDING.value, CleanupStatus.FAILED.value}
    ):
        actions.append(AllowedAction.VERIFY)
    if cleanup_retryable_resources(task):
        actions.append(AllowedAction.RETRY_CLEANUP)
    if bool(attempts) if has_attempts is _HAS_ATTEMPTS_UNSET else bool(has_attempts):
        actions.append(AllowedAction.COPY_ARTEMIS_ID)
    return tuple(actions)
