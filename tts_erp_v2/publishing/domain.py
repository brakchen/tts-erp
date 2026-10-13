"""Pure publishing state vocabulary and action policy."""

from __future__ import annotations

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
    EDIT_SETTINGS = "edit_settings"
    CANCEL = "cancel"
    RETRY = "retry"
    VERIFY = "verify"
    CONFIRM_PUBLISHED = "confirm_published"
    RETRY_CLEANUP = "retry_cleanup"
    REPLACE_UPLOAD = "replace_upload"
    COPY_ARTEMIS_ID = "copy_artemis_id"


@dataclass(frozen=True, slots=True)
class FailureClassification:
    """Diagnostic code for a finished publish round (02 §14.4).

    ``ambiguous`` only routes the terminal status: an ambiguous result waits
    for a human decision (``needs_review``), a certain failure stops at
    ``failed``. It never re-queues, retries or starts a verification.
    """

    code: str
    ambiguous: bool


# Codes that do not prove the publish action was absent. They are the only
# reason a finished round may end in needs_review instead of failed.
AMBIGUOUS_FAILURE_CODES = frozenset(
    {
        "ambiguous_artemis_failure",
        "ambiguous_terminal_execution",
        "publish_action_observed",
        "recovered_ambiguous_failure",
        "session_missing",
    }
)

# One attempt per task may be unfinished at a time; the partial unique index
# uq_video_publish_task_active_attempt enforces it in the database.
ACTIVE_ATTEMPT_STATUSES = frozenset(
    {"created", "submitting", "queued", "running", "unknown"}
)

# Running stages that a user may still cancel: params_locked_at stays NULL
# until Artemis admits the publish, so no submission can exist yet (02 §6.5).
# dispatching_artemis is excluded on purpose: the admission request is already
# in flight there, so the API answers 409 TASK_SUBMITTING instead of setting a
# cancel flag the worker would never read (02 §6.5).
CANCELLABLE_PENDING_STAGES = frozenset({"awaiting_upload", "queued", "waiting_device"})
CANCELLABLE_RUNNING_STAGES = frozenset({"downloading", "staging_device"})


def attempt_is_active(attempt: Any) -> bool:
    return attempt is not None and attempt.status in ACTIVE_ATTEMPT_STATUSES


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
        return FailureClassification("publish_action_observed", True)
    if session_missing:
        return FailureClassification("session_missing", True)
    status = artemis_status.lower()
    if status in {"rejected", "cancelled", "canceled"}:
        return FailureClassification("ambiguous_terminal_execution", True)
    if steps_count == 0:
        # The planner never opened the publish UI: nothing was published.
        return FailureClassification("planner_zero_steps", False)
    if status in {"failed", "error"}:
        # A failed transport/execution report does not prove that the final
        # publish action was not observed, so it stays ambiguous.
        return FailureClassification("ambiguous_artemis_failure", True)
    return FailureClassification("session_missing", True)


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
    """Replacing the source video is never capped: there is no publish budget."""
    return (
        task.status == TaskStatus.FAILED.value
        and task.object_deleted_at is not None
        and not replacement_cleanup_pending(task)
        and not object_cleanup_blocks_input(task)
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
    has_active_attempt: bool | None = None,
) -> tuple[AllowedAction, ...]:
    """Compute UI actions without lazy loading when a summary is supplied."""
    attempts = None
    summary_supplied = (
        latest_attempt is not _LATEST_ATTEMPT_UNSET
        and has_attempts is not _HAS_ATTEMPTS_UNSET
    )
    if not summary_supplied:
        attempts = getattr(task, "attempts", ()) or ()
    if has_active_attempt is None:
        # Only one attempt per task may be unfinished, so the latest one
        # decides whether the task still owns the device slot.
        if summary_supplied:
            has_active_attempt = attempt_is_active(latest_attempt)
        elif attempts is not None:
            has_active_attempt = any(attempt_is_active(item) for item in attempts)
        else:
            has_active_attempt = False
    idle = not has_active_attempt
    locked = getattr(task, "params_locked_at", None) is not None
    cancellable = not locked and (
        (task.status == TaskStatus.PENDING and task.stage in CANCELLABLE_PENDING_STAGES)
        or (
            task.status == TaskStatus.RUNNING
            and task.stage in CANCELLABLE_RUNNING_STAGES
        )
    )
    actions: list[AllowedAction] = [AllowedAction.VIEW]
    if task.status == TaskStatus.PENDING and task.stage == TaskStage.AWAITING_UPLOAD:
        actions.append(AllowedAction.CONTINUE_UPLOAD)
    # Pre-submission editing shares the cancel boundary: once Artemis admitted
    # the publish (params_locked_at) the parameters are frozen for good.
    if not locked and task.status == TaskStatus.PENDING:
        actions.append(AllowedAction.EDIT_SETTINGS)
    if cancellable:
        actions.append(AllowedAction.CANCEL)
    if idle and task.status in {TaskStatus.FAILED, TaskStatus.NEEDS_REVIEW}:
        # Human confirmation is the only success path (02 §7.4).
        actions.append(AllowedAction.CONFIRM_PUBLISHED)
    if task.status == TaskStatus.FAILED and idle:
        if replace_upload_allowed(task):
            actions.append(AllowedAction.REPLACE_UPLOAD)
        elif (
            task.object_uploaded_at is not None
            and task.object_deleted_at is None
            and not object_cleanup_blocks_input(task)
        ):
            actions.append(AllowedAction.RETRY)
    if (
        task.status == TaskStatus.NEEDS_REVIEW
        and idle
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
