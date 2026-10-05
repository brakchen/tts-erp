"""Short-transaction repository for publish tasks."""

from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from types import SimpleNamespace
from typing import Any, Literal
from uuid import UUID, uuid4

from sqlalchemy import Select, and_, func, or_, select, true, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from tts_erp_v2.db.models.publishing import VideoPublishAttempt, VideoPublishTask
from tts_erp_v2.publishing.diagnostics import sanitize_artemis_output, sanitize_text
from tts_erp_v2.publishing.domain import (
    AttemptKind,
    AttemptStatus,
    CleanupIntent,
    TaskStage,
    TaskStatus,
    apply_cleanup_result,
    classify_failure,
    cleanup_retryable_resources,
    set_task_stage,
)
from tts_erp_v2.publishing.observability import emit_publish_event
from tts_erp_v2.publishing.prompt import (
    PUBLISH_PROMPT_VERSION,
    VERIFY_PROMPT_VERSION,
    build_publish_prompt,
    build_verify_prompt,
)


class LeaseLost(RuntimeError):
    """The worker no longer owns the fenced publish or cleanup lease."""


@dataclass(frozen=True, slots=True)
class PublishLeaseToken:
    task_id: UUID
    lease_owner: str
    row_version: int
    attempt_id: int | None = None
    expected_attempt_status: str | None = None


@dataclass(frozen=True, slots=True)
class AttemptObservation:
    status: str
    terminal: bool
    verdict: str | None = None
    steps_count: int | None = None
    final_publish_observed: bool = False
    same_session_resubmitted: bool = False
    sanitized_output: dict[str, Any] | None = None
    sanitized_error: str | None = None
    submitted: bool = False


@dataclass(frozen=True, slots=True)
class PrepareAttempt:
    attempt_id: int | None = None
    lease_seconds: int = 30
    max_attempts: int = 3


@dataclass(frozen=True, slots=True)
class ObserveAttempt:
    observation: AttemptObservation
    max_attempts: int
    lease_seconds: int = 30


@dataclass(frozen=True, slots=True)
class AdmissionRejected:
    code: str
    max_attempts: int
    lease_seconds: int = 30


@dataclass(frozen=True, slots=True)
class AdvanceExecution:
    stage: str
    lease_seconds: int = 30
    object_sha256: str | None = None
    device_path: str | None = None
    spool_path: str | None = None
    register_cleanup: bool = False
    register_spool_cleanup: bool = False


@dataclass(frozen=True, slots=True)
class RecoverPersistedAttempt:
    max_attempts: int
    lease_seconds: int = 30


@dataclass(frozen=True, slots=True)
class InvalidateConfirmedObject:
    reason: Literal["missing", "replaced", "etag_missing"]


@dataclass(frozen=True, slots=True)
class OperationalFailure:
    action: Literal["safe_retry", "failed", "unexpected"]
    code: str
    max_attempts: int
    retry_stage: str = TaskStage.QUEUED.value
    message: str | None = None


type PublishTransitionCommand = (
    PrepareAttempt
    | ObserveAttempt
    | AdmissionRejected
    | AdvanceExecution
    | RecoverPersistedAttempt
    | InvalidateConfirmedObject
    | OperationalFailure
)


@dataclass(frozen=True, slots=True)
class PublishTransitionOutcome:
    task_status: str
    task_stage: str
    row_version: int
    follow_up: Literal["none", "poll", "run_verify", "claim_device_cleanup"]
    verify_attempt_id: int | None = None
    retained_token: PublishLeaseToken | None = None


@dataclass(frozen=True, slots=True)
class CleanupClaimRequest:
    owner: str
    lease_seconds: int
    scope: Literal["device", "background"]
    task_id: UUID | None = None


@dataclass(frozen=True, slots=True)
class CleanupLeaseToken:
    task_id: UUID
    owner: str
    row_version: int
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class CleanupWork:
    task_id: UUID
    token: CleanupLeaseToken
    resources: tuple[str, ...]
    device_serial: str
    device_path: str | None
    spool_path: str | None
    object_generation: UUID
    object_key: str
    object_etag: str | None


@dataclass(frozen=True, slots=True)
class CleanupOutcome:
    task_status: str
    task_stage: str
    cleanup_intent: str
    row_version: int


def database_now(session: Session) -> datetime:
    """Read the PostgreSQL wall clock used by persisted publishing decisions."""
    value = session.scalar(select(func.clock_timestamp()))
    if value is None:
        raise RuntimeError("DATABASE_TIME_UNAVAILABLE")
    return value


_db_now = database_now


def _stage_values(task: VideoPublishTask, stage: str, now: datetime) -> dict[str, Any]:
    return {
        "stage": stage,
        "stage_started_at": (
            now
            if task.stage != stage or task.stage_started_at is None
            else task.stage_started_at
        ),
    }


def _has_outstanding_cleanup(task: VideoPublishTask) -> bool:
    return any(
        getattr(task, f"{name}_cleanup_status") in {"pending", "failed"}
        for name in ("device", "spool", "object")
    )


def _cleanup_pending_values(
    task: VideoPublishTask,
    now: datetime,
    *,
    intent: str,
    include_object: bool,
) -> dict[str, Any]:
    values: dict[str, Any] = {"cleanup_intent": intent}
    selected = {
        "device": task.device_path is not None,
        "spool": True,
        "object": include_object,
    }
    for name, enabled in selected.items():
        if enabled and getattr(task, f"{name}_cleanup_status") == "not_started":
            values[f"{name}_cleanup_status"] = "pending"
            values[f"{name}_cleanup_next_attempt_at"] = (
                max(filter(None, (now, task.object_upload_expires_at)))
                if name == "object"
                else now
            )
    return values


def _attempt_terminal_values(
    observation: AttemptObservation, now: datetime
) -> dict[str, Any]:
    status = observation.status.lower()
    return {
        "status": {
            "success": AttemptStatus.SUCCESS.value,
            "rejected": AttemptStatus.REJECTED.value,
            "cancelled": AttemptStatus.CANCELLED.value,
            "canceled": AttemptStatus.CANCELLED.value,
        }.get(status, AttemptStatus.FAILED.value),
        "artemis_output": sanitize_artemis_output(observation.sanitized_output),
        "artemis_error": (
            sanitize_text(observation.sanitized_error)
            if observation.sanitized_error
            else None
        ),
        "steps_count": observation.steps_count,
        "finished_at": now,
        "last_polled_at": now,
    }


def _new_publish_attempt(
    session: Session,
    task: VideoPublishTask,
    now: datetime,
    max_attempts: int,
) -> VideoPublishAttempt:
    if task.publish_budget_used >= max_attempts:
        raise ValueError("RETRY_BUDGET_EXHAUSTED")
    latest = (
        session.scalar(
            select(func.max(VideoPublishAttempt.sequence_no)).where(
                VideoPublishAttempt.task_id == task.id
            )
        )
        or 0
    )
    album = os.environ.get("TIKTOK_PUBLISH_ALBUM", "TTSERP")
    attempt = VideoPublishAttempt(
        task_id=task.id,
        sequence_no=latest + 1,
        kind=AttemptKind.PUBLISH.value,
        artemis_session_id=uuid4(),
        status=AttemptStatus.SUBMITTING.value,
        prompt_version=PUBLISH_PROMPT_VERSION,
        prompt_snapshot=build_publish_prompt(
            caption=task.caption,
            app_package=task.target_app_package,
            device_path=task.device_path
            or f"/sdcard/Movies/TTSERP/tts_erp_{task.public_id}.mp4",
            album=album,
        ),
        device_serial=task.target_device_serial,
        device_path=task.device_path,
        target_app_package=task.target_app_package,
        started_at=now,
    )
    session.add(attempt)
    session.flush()
    return attempt


def _new_verify_attempt(
    session: Session,
    task: VideoPublishTask,
    related: VideoPublishAttempt,
    now: datetime,
) -> VideoPublishAttempt:
    latest = (
        session.scalar(
            select(func.max(VideoPublishAttempt.sequence_no)).where(
                VideoPublishAttempt.task_id == task.id
            )
        )
        or 0
    )
    attempt = VideoPublishAttempt(
        task_id=task.id,
        sequence_no=latest + 1,
        kind=AttemptKind.VERIFY.value,
        related_attempt_id=related.id,
        artemis_session_id=uuid4(),
        status=AttemptStatus.CREATED.value,
        prompt_version=VERIFY_PROMPT_VERSION,
        prompt_snapshot=build_verify_prompt(
            caption=task.caption,
            app_package=task.target_app_package,
            source_filename=task.original_filename,
            object_identity=(
                f"etag:{task.object_etag or 'unknown'} "
                f"sha256:{task.object_sha256 or 'not-yet-recorded'}"
            ),
            expected_publish_after=(
                related.submitted_at
                or related.started_at
                or task.object_uploaded_at
                or now
            ).isoformat(),
            expected_publish_before=now.isoformat(),
        ),
        device_serial=task.target_device_serial,
        device_path=task.device_path,
        target_app_package=task.target_app_package,
    )
    session.add(attempt)
    session.flush()
    return attempt


def _safe_retry_values(
    task: VideoPublishTask,
    now: datetime,
    *,
    code: str,
    max_attempts: int,
    retry_stage: str,
    attempt_count: int | None = None,
    message: str | None = None,
) -> dict[str, Any]:
    consumed_attempts = (
        task.publish_budget_used if attempt_count is None else attempt_count
    )
    terminal = consumed_attempts >= max_attempts
    cleanup_gate = task.device_path is not None and task.stage in {
        TaskStage.STAGING_DEVICE.value,
        TaskStage.DISPATCHING_ARTEMIS.value,
        TaskStage.WAITING_ARTEMIS.value,
        TaskStage.VERIFYING.value,
        TaskStage.CLEANING.value,
    }
    values: dict[str, Any] = {
        "last_error_code": sanitize_text(code)[:100],
        "last_error_message": sanitize_text(message or code),
        "lease_owner": None,
        "lease_expires_at": None,
        "heartbeat_at": None,
        "queued_at": None if terminal else now,
        "next_attempt_at": (
            None
            if terminal
            else now + timedelta(seconds=min(300, 5 * (2 ** min(consumed_attempts, 5))))
        ),
    }
    if cleanup_gate:
        values.update(
            status=(TaskStatus.FAILED.value if terminal else TaskStatus.PENDING.value),
            cleanup_intent=(
                CleanupIntent.PRESERVE_STATE.value
                if terminal
                else CleanupIntent.REQUEUE_PUBLISH.value
            ),
            device_cleanup_status="pending",
            device_cleanup_next_attempt_at=now,
            cleanup_lease_owner=None,
            cleanup_lease_expires_at=None,
            cleanup_heartbeat_at=None,
            completed_at=now if terminal else None,
        )
        values.update(
            _stage_values(
                task,
                TaskStage.DONE.value if terminal else TaskStage.WAITING_DEVICE.value,
                now,
            )
        )
    else:
        spool_tracked = task.spool_cleanup_status in {"pending", "failed"}
        values.update(
            status=(TaskStatus.FAILED.value if terminal else TaskStatus.PENDING.value),
            cleanup_intent=(
                CleanupIntent.PRESERVE_STATE.value
                if spool_tracked and terminal
                else CleanupIntent.REQUEUE_PUBLISH.value
                if spool_tracked
                else CleanupIntent.NONE.value
            ),
            completed_at=now if terminal else None,
        )
        if spool_tracked:
            values.update(
                spool_cleanup_status="pending",
                spool_cleanup_next_attempt_at=now,
                cleanup_lease_owner=None,
                cleanup_lease_expires_at=None,
                cleanup_heartbeat_at=None,
            )
        values.update(
            _stage_values(
                task,
                TaskStage.DONE.value if terminal else retry_stage,
                now,
            )
        )
    return values


def commit_publish_transition(
    session_factory: Callable[[], Session],
    token: PublishLeaseToken,
    command: PublishTransitionCommand,
) -> PublishTransitionOutcome:
    """Commit one complete publish/attempt transition behind a lease/version fence."""
    with session_factory() as session:
        task = session.scalar(
            select(VideoPublishTask).where(VideoPublishTask.public_id == token.task_id)
        )
        if task is None:
            raise LeaseLost(token.task_id)
        now = _db_now(session)
        previous_stage = task.stage
        previous_stage_started_at = task.stage_started_at
        attempt: VideoPublishAttempt | None = None
        if token.attempt_id is not None:
            attempt = session.scalar(
                select(VideoPublishAttempt).where(
                    VideoPublishAttempt.id == token.attempt_id,
                    VideoPublishAttempt.task_id == task.id,
                )
            )
            if attempt is None:
                raise LeaseLost(token.task_id)
        task_values: dict[str, Any] = {}
        attempt_values: dict[str, Any] | None = None
        follow_up: Literal["none", "poll", "run_verify", "claim_device_cleanup"] = (
            "none"
        )
        verify_attempt_id: int | None = None
        verify_attempt: VideoPublishAttempt | None = None
        inserted_attempt: VideoPublishAttempt | None = None

        if isinstance(command, PrepareAttempt):
            creating_publish = command.attempt_id is None
            if creating_publish:
                if token.attempt_id is not None:
                    raise LeaseLost(token.task_id)
            elif attempt is None or attempt.id != command.attempt_id:
                raise LeaseLost(token.task_id)
            task_values.update(
                heartbeat_at=now,
                lease_expires_at=now + timedelta(seconds=command.lease_seconds),
            )
            task_values.update(
                _stage_values(
                    task,
                    TaskStage.VERIFYING.value
                    if attempt is not None and attempt.kind == AttemptKind.VERIFY.value
                    else TaskStage.DISPATCHING_ARTEMIS.value,
                    now,
                )
            )
            if creating_publish:
                task_values.update(
                    attempt_count=task.attempt_count + 1,
                    publish_budget_used=task.publish_budget_used + 1,
                )
            else:
                assert attempt is not None
                attempt_values = {
                    "status": (
                        AttemptStatus.SUBMITTING.value
                        if attempt.status == AttemptStatus.CREATED.value
                        else attempt.status
                    ),
                    "started_at": attempt.started_at or now,
                }
            follow_up = "poll"
        elif isinstance(command, AdvanceExecution):
            task_values.update(
                heartbeat_at=now,
                lease_expires_at=now + timedelta(seconds=command.lease_seconds),
            )
            task_values.update(_stage_values(task, command.stage, now))
            if command.object_sha256 is not None:
                task_values["object_sha256"] = command.object_sha256
            if command.device_path is not None:
                task_values["device_path"] = command.device_path
            if command.spool_path is not None:
                task_values["spool_path"] = command.spool_path
            if command.register_spool_cleanup:
                if command.spool_path is None and task.spool_path is None:
                    raise ValueError("SPOOL_PATH_REQUIRED")
                task_values.update(
                    spool_cleanup_status="pending",
                    spool_cleanup_next_attempt_at=now,
                )
            if command.register_cleanup:
                task_values.update(
                    device_cleanup_status="pending",
                    device_cleanup_next_attempt_at=now,
                )
        elif isinstance(command, InvalidateConfirmedObject):
            code = {
                "missing": "CONFIRMED_OBJECT_MISSING",
                "replaced": "CONFIRMED_OBJECT_REPLACED",
                "etag_missing": "CONFIRMED_OBJECT_ETAG_MISSING",
            }[command.reason]
            task_values.update(
                status=TaskStatus.FAILED.value,
                completed_at=now,
                queued_at=None,
                next_attempt_at=None,
                lease_owner=None,
                lease_expires_at=None,
                heartbeat_at=None,
                last_error_code=code,
                last_error_message=code,
            )
            task_values.update(_stage_values(task, TaskStage.DONE.value, now))
            # Missing now is not permanent while a previously issued PUT ticket
            # can still complete. Every recovery path remains tracked until the
            # persisted generation expiry and one idempotent selector deletion.
            task_values.update(
                cleanup_intent=CleanupIntent.PRESERVE_STATE.value,
                object_cleanup_status="pending",
                object_cleanup_error=None,
                object_cleanup_next_attempt_at=max(
                    filter(None, (now, task.object_upload_expires_at))
                ),
                cleanup_lease_owner=None,
                cleanup_lease_expires_at=None,
                cleanup_heartbeat_at=None,
            )
        elif isinstance(command, AdmissionRejected):
            if attempt is None:
                raise LeaseLost(token.task_id)
            code = sanitize_text(command.code)[:100]
            attempt_values = {
                "status": AttemptStatus.REJECTED.value,
                "retry_classification": code.lower(),
                "retry_safe": attempt.kind == AttemptKind.PUBLISH.value,
                "artemis_error": code,
                "finished_at": now,
                "last_polled_at": now,
            }
            if attempt.kind == AttemptKind.VERIFY.value:
                task_values.update(
                    status=TaskStatus.NEEDS_REVIEW.value,
                    cleanup_intent=(
                        CleanupIntent.PRESERVE_STATE.value
                        if _has_outstanding_cleanup(task)
                        else CleanupIntent.NONE.value
                    ),
                    last_error_code="VERIFY_ADMISSION_REJECTED",
                    last_error_message=code,
                    completed_at=now,
                    lease_owner=None,
                    lease_expires_at=None,
                    heartbeat_at=None,
                )
                task_values.update(_stage_values(task, TaskStage.DONE.value, now))
            else:
                refunded_budget = max(0, task.publish_budget_used - 1)
                task_values["publish_budget_used"] = refunded_budget
                task_values.update(
                    _safe_retry_values(
                        task,
                        now,
                        code=code,
                        max_attempts=command.max_attempts,
                        retry_stage=TaskStage.WAITING_DEVICE.value,
                        attempt_count=refunded_budget,
                    )
                )
        elif isinstance(command, OperationalFailure):
            if command.action == "safe_retry":
                task_values.update(
                    _safe_retry_values(
                        task,
                        now,
                        code=command.code,
                        max_attempts=command.max_attempts,
                        retry_stage=command.retry_stage,
                        message=command.message,
                    )
                )
            elif command.action == "unexpected" and attempt is not None:
                # Keep the immutable session queryable. Recovery leases this same
                # running task and polls the same attempt instead of creating one.
                attempt_values = {
                    "status": AttemptStatus.UNKNOWN.value,
                    "artemis_error": sanitize_text(command.message or command.code),
                    "last_polled_at": now,
                }
                task_values.update(
                    status=TaskStatus.RUNNING.value,
                    last_error_code="UNEXPECTED_WORKER_FAILURE",
                    last_error_message=sanitize_text(command.message or command.code),
                    completed_at=None,
                    lease_owner=None,
                    lease_expires_at=None,
                    heartbeat_at=None,
                )
                task_values.update(
                    _stage_values(
                        task,
                        TaskStage.VERIFYING.value
                        if attempt.kind == AttemptKind.VERIFY.value
                        else TaskStage.WAITING_ARTEMIS.value,
                        now,
                    )
                )
            else:
                task_values.update(
                    status=(
                        TaskStatus.FAILED.value
                        if command.action == "failed"
                        else TaskStatus.NEEDS_REVIEW.value
                    ),
                    cleanup_intent=(
                        CleanupIntent.PRESERVE_STATE.value
                        if _has_outstanding_cleanup(task)
                        else CleanupIntent.NONE.value
                    ),
                    last_error_code=(
                        sanitize_text(command.code)[:100]
                        if command.action == "failed"
                        else "UNEXPECTED_WORKER_FAILURE"
                    ),
                    last_error_message=sanitize_text(command.message or command.code),
                    completed_at=now,
                    lease_owner=None,
                    lease_expires_at=None,
                    heartbeat_at=None,
                )
                task_values.update(_stage_values(task, TaskStage.DONE.value, now))
        else:
            observation = (
                command.observation if isinstance(command, ObserveAttempt) else None
            )
            if attempt is None:
                raise LeaseLost(token.task_id)
            if isinstance(command, RecoverPersistedAttempt):
                observation = AttemptObservation(
                    status=attempt.status,
                    terminal=True,
                    sanitized_output=attempt.artemis_output,
                    sanitized_error=attempt.artemis_error,
                    steps_count=attempt.steps_count,
                )
            assert observation is not None
            max_attempts = command.max_attempts
            common_attempt: dict[str, Any] = {
                "last_polled_at": now,
            }
            if observation.same_session_resubmitted:
                common_attempt["submit_retry_count"] = attempt.submit_retry_count + 1
            if observation.submitted:
                common_attempt["submitted_at"] = attempt.submitted_at or now
            if not observation.terminal:
                common_attempt.update(
                    status=(
                        observation.status
                        if observation.status in {"queued", "running"}
                        else AttemptStatus.RUNNING.value
                    ),
                    submitted_at=attempt.submitted_at or now,
                )
                attempt_values = common_attempt
                task_values.update(
                    heartbeat_at=now,
                    lease_expires_at=now + timedelta(seconds=command.lease_seconds),
                )
                task_values.update(
                    _stage_values(
                        task,
                        TaskStage.VERIFYING.value
                        if attempt.kind == AttemptKind.VERIFY.value
                        else TaskStage.WAITING_ARTEMIS.value,
                        now,
                    )
                )
                follow_up = "poll"
            else:
                attempt_values = _attempt_terminal_values(observation, now)
                attempt_values.update(common_attempt)
                if attempt.kind == AttemptKind.VERIFY.value:
                    verdict = (
                        observation.verdict
                        if observation.status == AttemptStatus.SUCCESS.value
                        and observation.verdict
                        in {"published", "not_published", "inconclusive"}
                        else "inconclusive"
                    )
                    if verdict == "published":
                        task_values.update(
                            status=TaskStatus.SUCCEEDED.value,
                            completed_at=now,
                            lease_owner=None,
                            lease_expires_at=None,
                            heartbeat_at=None,
                        )
                        task_values.update(
                            _stage_values(task, TaskStage.DONE.value, now)
                        )
                        task_values.update(
                            _cleanup_pending_values(
                                task,
                                now,
                                intent=CleanupIntent.FINALIZE_SUCCESS.value,
                                include_object=True,
                            )
                        )
                        follow_up = "claim_device_cleanup"
                    elif verdict == "not_published":
                        # A single immediate negative cannot prove that an
                        # ambiguous publish is absent; never auto-republish.
                        task_values.update(
                            status=TaskStatus.NEEDS_REVIEW.value,
                            last_error_code="VERIFY_NOT_PUBLISHED",
                            last_error_message=(
                                "Verification did not find the publication; "
                                "manual review is required"
                            ),
                            completed_at=now,
                            queued_at=None,
                            next_attempt_at=None,
                            lease_owner=None,
                            lease_expires_at=None,
                            heartbeat_at=None,
                        )
                        task_values.update(
                            _cleanup_pending_values(
                                task,
                                now,
                                intent=CleanupIntent.PRESERVE_STATE.value,
                                include_object=False,
                            )
                        )
                        task_values.update(
                            _stage_values(task, TaskStage.DONE.value, now)
                        )
                    else:
                        task_values.update(
                            status=TaskStatus.NEEDS_REVIEW.value,
                            cleanup_intent=(
                                CleanupIntent.PRESERVE_STATE.value
                                if _has_outstanding_cleanup(task)
                                else CleanupIntent.NONE.value
                            ),
                            last_error_code="VERIFY_INCONCLUSIVE",
                            last_error_message="Verification did not confirm publication",
                            completed_at=now,
                            lease_owner=None,
                            lease_expires_at=None,
                            heartbeat_at=None,
                        )
                        task_values.update(
                            _stage_values(task, TaskStage.DONE.value, now)
                        )
                elif observation.status == "success":
                    task_values.update(
                        status=TaskStatus.SUCCEEDED.value,
                        completed_at=now,
                        lease_owner=None,
                        lease_expires_at=None,
                        heartbeat_at=None,
                    )
                    task_values.update(_stage_values(task, TaskStage.DONE.value, now))
                    task_values.update(
                        _cleanup_pending_values(
                            task,
                            now,
                            intent=CleanupIntent.FINALIZE_SUCCESS.value,
                            include_object=True,
                        )
                    )
                    follow_up = "claim_device_cleanup"
                else:
                    classification = classify_failure(
                        artemis_status=observation.status,
                        steps_count=observation.steps_count,
                        final_publish_observed=observation.final_publish_observed,
                        session_missing=observation.status in {"missing", "not_found"},
                    )
                    if isinstance(command, RecoverPersistedAttempt):
                        classification = (
                            classification.__class__(
                                attempt.retry_classification
                                or "recovered_safe_failure",
                                True,
                            )
                            if attempt.retry_safe is True
                            else classification.__class__(
                                attempt.retry_classification
                                or "recovered_ambiguous_failure",
                                attempt.retry_safe,
                                True,
                            )
                        )
                    attempt_values.update(
                        retry_classification=classification.code,
                        retry_safe=classification.retry_safe,
                    )
                    if classification.requires_verification:
                        task_values.update(
                            _stage_values(task, TaskStage.VERIFYING.value, now)
                        )
                        task_values.update(
                            heartbeat_at=now,
                            lease_expires_at=now
                            + timedelta(seconds=command.lease_seconds),
                        )
                        follow_up = "run_verify"
                    elif classification.retry_safe:
                        task_values.update(
                            _safe_retry_values(
                                task,
                                now,
                                code=classification.code,
                                max_attempts=max_attempts,
                                retry_stage=TaskStage.QUEUED.value,
                            )
                        )
                    else:
                        task_values.update(
                            status=TaskStatus.FAILED.value,
                            completed_at=now,
                            cleanup_intent=(
                                CleanupIntent.PRESERVE_STATE.value
                                if task.device_path
                                else CleanupIntent.NONE.value
                            ),
                            last_error_code=classification.code,
                            lease_owner=None,
                            lease_expires_at=None,
                            heartbeat_at=None,
                        )
                        task_values.update(
                            _stage_values(task, TaskStage.DONE.value, now)
                        )

        attempt_status_changed = bool(
            attempt is not None
            and attempt_values is not None
            and "status" in attempt_values
            and attempt_values["status"] != attempt.status
        )
        new_version = token.row_version + 1
        task_values["row_version"] = new_version
        result = session.execute(
            update(VideoPublishTask)
            .where(
                VideoPublishTask.public_id == token.task_id,
                VideoPublishTask.lease_owner == token.lease_owner,
                VideoPublishTask.lease_expires_at > now,
                VideoPublishTask.row_version == token.row_version,
            )
            .values(**task_values)
            .execution_options(synchronize_session=False)
        )
        if getattr(result, "rowcount", None) != 1:
            session.rollback()
            raise LeaseLost(token.task_id)
        if isinstance(command, PrepareAttempt) and command.attempt_id is None:
            attempt = _new_publish_attempt(session, task, now, command.max_attempts)
            inserted_attempt = attempt
            token = PublishLeaseToken(
                task_id=token.task_id,
                lease_owner=token.lease_owner,
                row_version=token.row_version,
                attempt_id=attempt.id,
                expected_attempt_status=AttemptStatus.SUBMITTING.value,
            )
        if attempt_values is not None:
            attempt_statement = update(VideoPublishAttempt).where(
                VideoPublishAttempt.id == token.attempt_id,
                VideoPublishAttempt.task_id == task.id,
            )
            if token.expected_attempt_status is not None:
                attempt_statement = attempt_statement.where(
                    VideoPublishAttempt.status == token.expected_attempt_status
                )
            attempt_result = session.execute(
                attempt_statement.values(**attempt_values).execution_options(
                    synchronize_session=False
                )
            )
            if getattr(attempt_result, "rowcount", None) != 1:
                session.rollback()
                raise LeaseLost(token.task_id)
        if follow_up == "run_verify":
            assert attempt is not None
            verify_attempt = _new_verify_attempt(session, task, attempt, now)
            verify_attempt_id = verify_attempt.id
            inserted_attempt = verify_attempt
        session.commit()
        next_stage = str(task_values.get("stage", task.stage))
        stage_changed = next_stage != previous_stage
        event_context = {
            "task_id": token.task_id,
            "attempt_id": attempt.id if attempt is not None else None,
            "artemis_session_id": (
                attempt.artemis_session_id if attempt is not None else None
            ),
            "attempt_kind": attempt.kind if attempt is not None else None,
            "stage": next_stage,
            "outcome": str(task_values.get("status", task.status)),
            "device_serial": (
                attempt.device_serial
                if attempt is not None
                else task.target_device_serial
            ),
            "duration_ms": None,
        }
        transition_context = dict(event_context)
        if stage_changed and previous_stage_started_at is not None:
            transition_context.update(
                stage=previous_stage,
                duration_ms=max(
                    0, int((now - previous_stage_started_at).total_seconds() * 1000)
                ),
            )
        emit_publish_event("publish_transition", **transition_context)
        if (
            isinstance(command, PrepareAttempt)
            and previous_stage == TaskStage.STAGING_DEVICE.value
        ):
            emit_publish_event("device_stage_succeeded", **transition_context)
        if inserted_attempt is not None:
            emit_publish_event(
                "artemis_attempt_created",
                task_id=token.task_id,
                attempt_id=inserted_attempt.id,
                artemis_session_id=inserted_attempt.artemis_session_id,
                attempt_kind=inserted_attempt.kind,
                stage=next_stage,
                outcome=inserted_attempt.status,
                device_serial=inserted_attempt.device_serial,
            )
        if attempt_status_changed:
            status_context = dict(event_context)
            assert attempt_values is not None
            status_context["outcome"] = str(attempt_values["status"])
            emit_publish_event("artemis_status_changed", **status_context)
        if (
            isinstance(command, ObserveAttempt)
            and command.observation.same_session_resubmitted
        ):
            emit_publish_event("artemis_submit_retried", **event_context)
        if follow_up == "run_verify" and verify_attempt is not None:
            emit_publish_event(
                "verification_started",
                task_id=token.task_id,
                attempt_id=verify_attempt.id,
                artemis_session_id=verify_attempt.artemis_session_id,
                attempt_kind=verify_attempt.kind,
                stage=TaskStage.VERIFYING.value,
                outcome=AttemptStatus.CREATED.value,
                device_serial=verify_attempt.device_serial,
            )
        if (
            isinstance(command, (ObserveAttempt, RecoverPersistedAttempt))
            and attempt is not None
            and attempt.kind == AttemptKind.VERIFY.value
            and attempt_values is not None
            and attempt_values.get("finished_at") is not None
        ):
            emit_publish_event("verification_finished", **event_context)
        if isinstance(command, InvalidateConfirmedObject):
            emit_publish_event("confirmed_object_recovery", **event_context)
        if str(task_values.get("status", task.status)) in {
            TaskStatus.SUCCEEDED.value,
            TaskStatus.FAILED.value,
            TaskStatus.NEEDS_REVIEW.value,
            TaskStatus.CANCELLED.value,
        }:
            emit_publish_event("publish_task_terminal", **event_context)
        retained = None
        if task_values.get("lease_owner", token.lease_owner) is not None:
            expected_status = (
                attempt_values.get("status", attempt.status)
                if attempt is not None and attempt_values is not None
                else token.expected_attempt_status
            )
            retained = PublishLeaseToken(
                task_id=token.task_id,
                lease_owner=token.lease_owner,
                row_version=new_version,
                attempt_id=(verify_attempt_id or token.attempt_id),
                expected_attempt_status=(
                    AttemptStatus.CREATED.value
                    if verify_attempt_id is not None
                    else expected_status
                ),
            )
        return PublishTransitionOutcome(
            task_status=str(task_values.get("status", task.status)),
            task_stage=str(task_values.get("stage", task.stage)),
            row_version=new_version,
            follow_up=follow_up,
            verify_attempt_id=verify_attempt_id,
            retained_token=retained,
        )


def claim_cleanup_work(
    session_factory: Callable[[], Session], request: CleanupClaimRequest
) -> CleanupWork | None:
    """Atomically select and lease one explicit, database-time-due cleanup row."""
    with session_factory() as session:
        clock = select(func.clock_timestamp().label("database_now")).cte(
            "cleanup_clock"
        )
        now = clock.c.database_now

        def due(status, retry):
            return and_(
                status.in_(["pending", "failed"]),
                or_(retry.is_(None), retry <= now),
            )

        device_due = due(
            VideoPublishTask.device_cleanup_status,
            VideoPublishTask.device_cleanup_next_attempt_at,
        )
        spool_due = due(
            VideoPublishTask.spool_cleanup_status,
            VideoPublishTask.spool_cleanup_next_attempt_at,
        )
        object_due = due(
            VideoPublishTask.object_cleanup_status,
            VideoPublishTask.object_cleanup_next_attempt_at,
        )
        resource_due = (
            device_due if request.scope == "device" else or_(spool_due, object_due)
        )
        predicates = [
            VideoPublishTask.cleanup_intent != CleanupIntent.NONE.value,
            resource_due,
            or_(
                VideoPublishTask.cleanup_lease_owner.is_(None),
                VideoPublishTask.cleanup_lease_expires_at.is_(None),
                VideoPublishTask.cleanup_lease_expires_at < now,
            ),
        ]
        if request.task_id is not None:
            predicates.append(VideoPublishTask.public_id == request.task_id)
        candidate = (
            select(
                VideoPublishTask.id.label("task_pk"),
                now.label("claimed_at"),
                device_due.label("device_due"),
                spool_due.label("spool_due"),
                object_due.label("object_due"),
            )
            .select_from(VideoPublishTask)
            .join(clock, true())
            .where(*predicates)
            .order_by(VideoPublishTask.cleanup_lease_expires_at, VideoPublishTask.id)
            .with_for_update(skip_locked=True)
            .limit(1)
            .cte("cleanup_candidate")
        )
        statement = (
            update(VideoPublishTask)
            .where(VideoPublishTask.id == candidate.c.task_pk)
            .values(
                cleanup_lease_owner=request.owner,
                cleanup_lease_expires_at=candidate.c.claimed_at
                + timedelta(seconds=request.lease_seconds),
                cleanup_heartbeat_at=candidate.c.claimed_at,
                row_version=VideoPublishTask.row_version + 1,
            )
            .returning(
                VideoPublishTask.public_id,
                VideoPublishTask.row_version,
                VideoPublishTask.cleanup_lease_expires_at,
                VideoPublishTask.target_device_serial,
                VideoPublishTask.device_path,
                VideoPublishTask.spool_path,
                VideoPublishTask.object_generation,
                VideoPublishTask.object_key,
                VideoPublishTask.object_etag,
                candidate.c.device_due,
                candidate.c.spool_due,
                candidate.c.object_due,
            )
        )
        row = session.execute(statement).one_or_none()
        if row is None:
            session.rollback()
            return None
        resources = tuple(
            name
            for name, selected in (
                ("device", row.device_due and request.scope == "device"),
                ("spool", row.spool_due and request.scope == "background"),
                ("object", row.object_due and request.scope == "background"),
            )
            if selected
        )
        work = CleanupWork(
            task_id=row.public_id,
            token=CleanupLeaseToken(
                task_id=row.public_id,
                owner=request.owner,
                row_version=row.row_version,
                expires_at=row.cleanup_lease_expires_at,
            ),
            resources=resources,
            device_serial=row.target_device_serial,
            device_path=row.device_path,
            spool_path=row.spool_path,
            object_generation=row.object_generation,
            object_key=row.object_key,
            object_etag=row.object_etag,
        )
        session.commit()
        return work


def renew_cleanup_work(
    session_factory: Callable[[], Session],
    token: CleanupLeaseToken,
    lease_seconds: int,
) -> CleanupLeaseToken:
    with session_factory() as session:
        now = _db_now(session)
        expires_at = now + timedelta(seconds=lease_seconds)
        result = session.execute(
            update(VideoPublishTask)
            .where(
                VideoPublishTask.public_id == token.task_id,
                VideoPublishTask.cleanup_lease_owner == token.owner,
                VideoPublishTask.cleanup_lease_expires_at > now,
                VideoPublishTask.row_version == token.row_version,
            )
            .values(
                cleanup_heartbeat_at=now,
                cleanup_lease_expires_at=expires_at,
                row_version=token.row_version + 1,
            )
        )
        if getattr(result, "rowcount", None) != 1:
            session.rollback()
            raise LeaseLost(token.task_id)
        session.commit()
        return CleanupLeaseToken(
            task_id=token.task_id,
            owner=token.owner,
            row_version=token.row_version + 1,
            expires_at=expires_at,
        )


def _cleanup_retry_delay(attempts: int) -> int:
    return min(300, 5 * (2 ** min(attempts, 5)))


def finish_cleanup_work(
    session_factory: Callable[[], Session],
    token: CleanupLeaseToken,
    resource_results: dict[str, str | None],
) -> CleanupOutcome:
    """Persist cleanup results and release ownership through one DB-time CAS."""
    with session_factory() as session:
        task = session.scalar(
            select(VideoPublishTask).where(VideoPublishTask.public_id == token.task_id)
        )
        if task is None:
            raise LeaseLost(token.task_id)
        now = _db_now(session)
        values: dict[str, Any] = {
            "cleanup_lease_owner": None,
            "cleanup_lease_expires_at": None,
            "cleanup_heartbeat_at": None,
            "row_version": token.row_version + 1,
        }
        projected = {
            name: getattr(task, f"{name}_cleanup_status")
            for name in ("device", "spool", "object")
        }
        for name, error in resource_results.items():
            if name not in projected:
                raise ValueError("UNKNOWN_CLEANUP_RESOURCE")
            status = "failed" if error else "succeeded"
            projected[name] = status
            values[f"{name}_cleanup_status"] = status
            if error:
                safe_error = sanitize_text(error)
                if not safe_error.startswith("CLEANUP_"):
                    safe_error = f"CLEANUP_{name.upper()}_FAILED: {safe_error}"
                values[f"{name}_cleanup_error"] = safe_error[:2000]
            else:
                values[f"{name}_cleanup_error"] = None
            if error:
                attempts = getattr(task, f"{name}_cleanup_attempts") + 1
                values[f"{name}_cleanup_attempts"] = attempts
                values[f"{name}_cleanup_next_attempt_at"] = now + timedelta(
                    seconds=_cleanup_retry_delay(attempts - 1)
                )
            else:
                values[f"{name}_cleanup_next_attempt_at"] = None
                if name == "object":
                    values["object_deleted_at"] = now
        decision = SimpleNamespace(
            cleanup_intent=task.cleanup_intent,
            status=task.status,
            stage=task.stage,
            stage_started_at=task.stage_started_at,
        )
        apply_cleanup_result(
            decision,
            device_failed=projected["device"] == "failed",
            now=now,
        )
        status = decision.status
        stage = decision.stage
        values.update(status=status, stage=stage)
        if decision.stage_started_at != task.stage_started_at:
            values["stage_started_at"] = decision.stage_started_at
        remaining = any(
            resource_status in {"pending", "failed"}
            for resource_status in projected.values()
        )
        values["cleanup_intent"] = (
            task.cleanup_intent if remaining else CleanupIntent.NONE.value
        )
        if task.cleanup_intent in {
            CleanupIntent.FINALIZE_SUCCESS.value,
            CleanupIntent.PRESERVE_STATE.value,
        }:
            values["completed_at"] = task.completed_at or now
        result = session.execute(
            update(VideoPublishTask)
            .where(
                VideoPublishTask.public_id == token.task_id,
                VideoPublishTask.cleanup_lease_owner == token.owner,
                VideoPublishTask.cleanup_lease_expires_at > now,
                VideoPublishTask.row_version == token.row_version,
            )
            .values(**values)
            .execution_options(synchronize_session=False)
        )
        if getattr(result, "rowcount", None) != 1:
            session.rollback()
            raise LeaseLost(token.task_id)
        session.commit()
        for resource, error in resource_results.items():
            emit_publish_event(
                "cleanup_resource_finished",
                task_id=token.task_id,
                stage=stage,
                outcome=f"{resource}:{'failed' if error else 'succeeded'}",
                device_serial=task.target_device_serial,
            )
        return CleanupOutcome(
            task_status=status,
            task_stage=stage,
            cleanup_intent=str(values["cleanup_intent"]),
            row_version=token.row_version + 1,
        )


def schedule_spool_reconciliation(
    session_factory: Callable[[], Session], task_id: UUID
) -> bool:
    """Schedule tracked terminal spool residue; physical deletion is selector-owned."""
    with session_factory() as session:
        task = session.scalar(
            select(VideoPublishTask).where(VideoPublishTask.public_id == task_id)
        )
        if task is None:
            return False
        now = _db_now(session)
        age = task.completed_at or task.created_at
        if (
            task.status
            not in {
                TaskStatus.SUCCEEDED.value,
                TaskStatus.FAILED.value,
                TaskStatus.NEEDS_REVIEW.value,
                TaskStatus.CANCELLED.value,
            }
            or age is None
            or age > now - timedelta(hours=24)
            or task.spool_cleanup_status in {"pending", "failed"}
        ):
            return False
        result = session.execute(
            update(VideoPublishTask)
            .where(
                VideoPublishTask.public_id == task_id,
                VideoPublishTask.row_version == task.row_version,
                or_(
                    VideoPublishTask.cleanup_lease_owner.is_(None),
                    VideoPublishTask.cleanup_lease_expires_at.is_(None),
                    VideoPublishTask.cleanup_lease_expires_at <= now,
                ),
                VideoPublishTask.spool_cleanup_status.not_in(["pending", "failed"]),
            )
            .values(
                cleanup_intent=CleanupIntent.PRESERVE_STATE.value,
                spool_cleanup_status="pending",
                spool_cleanup_error="SPOOL_RESIDUE_RECONCILED",
                spool_cleanup_next_attempt_at=now,
                cleanup_lease_owner=None,
                cleanup_lease_expires_at=None,
                cleanup_heartbeat_at=None,
                row_version=task.row_version + 1,
            )
        )
        if getattr(result, "rowcount", None) != 1:
            session.rollback()
            return False
        session.commit()
        return True


def get_task(
    session: Session, public_id: UUID, *, lock: bool = False
) -> VideoPublishTask | None:
    query: Select[VideoPublishTask] = (
        select(VideoPublishTask)
        .where(VideoPublishTask.public_id == public_id)
        .options(selectinload(VideoPublishTask.attempts))
    )
    if lock:
        query = query.with_for_update()
    return session.scalars(query).unique().first()


def _lease_task(
    session: Session, instance_id: str, lease_seconds: int
) -> VideoPublishTask | None:
    now = _db_now(session)
    task = session.scalars(
        select(VideoPublishTask)
        .where(
            VideoPublishTask.status == TaskStatus.RUNNING.value,
            (VideoPublishTask.lease_owner == instance_id)
            | (VideoPublishTask.lease_expires_at.is_(None))
            | (VideoPublishTask.lease_expires_at < now),
        )
        .order_by(VideoPublishTask.id)
        .with_for_update(skip_locked=True)
        .limit(1)
    ).first()
    if task is None:
        return None
    task.lease_owner = instance_id
    task.lease_expires_at = now + timedelta(seconds=lease_seconds)
    task.heartbeat_at = now
    task.row_version += 1
    session.flush()
    return task


def _lock_publish_slot(session: Session) -> None:
    session.execute(select(func.pg_advisory_xact_lock(738041)))


def has_pending_device_cleanup(session: Session) -> bool:
    return (
        session.scalar(
            select(VideoPublishTask.id).where(
                VideoPublishTask.cleanup_intent != CleanupIntent.NONE.value,
                VideoPublishTask.device_cleanup_status.in_(["pending", "failed"]),
            )
        )
        is not None
    )


def claim_one(
    session: Session,
    instance_id: str,
    lease_seconds: int = 30,
    max_attempts: int = 3,
) -> VideoPublishTask | None:
    _lock_publish_slot(session)
    now = _db_now(session)
    if has_pending_device_cleanup(session):
        return None
    if (
        session.scalar(
            select(VideoPublishTask.id)
            .where(VideoPublishTask.status == TaskStatus.RUNNING.value)
            .limit(1)
        )
        is not None
    ):
        return None
    query = (
        select(VideoPublishTask)
        .where(
            VideoPublishTask.status == TaskStatus.PENDING.value,
            VideoPublishTask.stage.in_(
                [TaskStage.QUEUED.value, TaskStage.WAITING_DEVICE.value]
            ),
            VideoPublishTask.cleanup_intent == CleanupIntent.NONE.value,
            (VideoPublishTask.next_attempt_at.is_(None))
            | (VideoPublishTask.next_attempt_at <= now),
            VideoPublishTask.publish_budget_used < max_attempts,
        )
        .order_by(VideoPublishTask.queued_at, VideoPublishTask.id)
        .with_for_update(skip_locked=True)
        .limit(1)
    )
    task = session.scalars(query).first()
    if task is None:
        return None
    task.status = TaskStatus.RUNNING.value
    set_task_stage(task, TaskStage.DOWNLOADING, now=now)
    task.lease_owner = instance_id
    task.lease_expires_at = now + timedelta(seconds=lease_seconds)
    task.heartbeat_at = now
    task.started_at = task.started_at or now
    task.row_version += 1
    try:
        session.flush()
    except IntegrityError:
        # Another worker won the global running-task race.
        session.rollback()
        return None
    return task


def release_lease(task: VideoPublishTask) -> None:
    task.lease_owner = None
    task.lease_expires_at = None
    task.heartbeat_at = None


def request_verification(session: Session, task_id: UUID) -> VideoPublishTask:
    task = get_task(session, task_id, lock=True)
    if task is None:
        raise LookupError("TASK_NOT_FOUND")
    if task.status != TaskStatus.NEEDS_REVIEW.value:
        raise ValueError("TASK_ACTION_NOT_ALLOWED")
    if task.cleanup_intent != CleanupIntent.NONE.value:
        raise ValueError("CLEANUP_REQUIRED")
    if task.device_cleanup_status in {"pending", "failed"}:
        raise ValueError("DEVICE_CLEANUP_BLOCKED")
    related = next(
        (a for a in reversed(task.attempts) if a.kind == AttemptKind.PUBLISH.value),
        None,
    )
    if related is None:
        raise ValueError("VERIFY_NOT_AVAILABLE")
    now = _db_now(session)
    task.status = TaskStatus.RUNNING.value
    set_task_stage(task, TaskStage.VERIFYING, now=now)
    # Reserving the global running row prevents another publish claim. Leaving
    # the execution lease empty lets the real worker claim this verify now.
    task.lease_owner = None
    task.lease_expires_at = None
    task.heartbeat_at = None
    _new_verify_attempt(session, task, related, now)
    task.row_version += 1
    return task


def retry_cleanup_resources(
    session: Session,
    task: VideoPublishTask,
    *,
    resources: list[str] | None = None,
    now: datetime | None = None,
) -> tuple[str, ...]:
    """Reset only failed resources while preserving business status/stage."""
    now = now or _db_now(session)
    failed = cleanup_retryable_resources(task)
    if resources is not None:
        requested = tuple(dict.fromkeys(resources))
        if any(name not in {"device", "spool", "object"} for name in requested):
            raise ValueError("CLEANUP_RESOURCE_NOT_RETRYABLE")
        if any(name not in failed for name in requested):
            raise ValueError("CLEANUP_RESOURCE_NOT_RETRYABLE")
        failed = tuple(name for name in failed if name in requested)
    if not failed:
        raise ValueError("CLEANUP_RETRY_NOT_AVAILABLE")
    if (
        task.cleanup_lease_owner
        and task.cleanup_lease_expires_at
        and task.cleanup_lease_expires_at > now
    ):
        raise ValueError("CLEANUP_LEASE_BUSY")
    if task.cleanup_intent == CleanupIntent.NONE.value:
        task.cleanup_intent = CleanupIntent.PRESERVE_STATE.value
    for name in failed:
        setattr(task, f"{name}_cleanup_status", "pending")
        setattr(task, f"{name}_cleanup_next_attempt_at", now)
    task.cleanup_lease_owner = None
    task.cleanup_lease_expires_at = None
    task.cleanup_heartbeat_at = None
    task.row_version += 1
    return failed


def schedule_retention_cleanup(
    session: Session,
    *,
    failed_retention_days: int | None = None,
) -> int:
    """Schedule only policy-proven MinIO deletion; execution remains guarded."""
    now = _db_now(session)
    retention_days = (
        failed_retention_days
        if failed_retention_days is not None
        else int(os.environ.get("TIKTOK_PUBLISH_FAILED_RETENTION_DAYS", "30"))
    )
    abandoned_before = now - timedelta(hours=24)
    failed_before = now - timedelta(days=max(0, retention_days))
    tasks = list(
        session.scalars(
            select(VideoPublishTask)
            .where(
                VideoPublishTask.object_deleted_at.is_(None),
                VideoPublishTask.object_cleanup_status.not_in(["pending", "failed"]),
                or_(
                    and_(
                        VideoPublishTask.status == TaskStatus.PENDING.value,
                        VideoPublishTask.stage == TaskStage.AWAITING_UPLOAD.value,
                        VideoPublishTask.created_at <= abandoned_before,
                    ),
                    and_(
                        VideoPublishTask.status == TaskStatus.FAILED.value,
                        VideoPublishTask.stage == TaskStage.DONE.value,
                        func.coalesce(
                            VideoPublishTask.completed_at,
                            VideoPublishTask.updated_at,
                        )
                        <= failed_before,
                    ),
                ),
                (VideoPublishTask.cleanup_lease_owner.is_(None))
                | (VideoPublishTask.cleanup_lease_expires_at.is_(None))
                | (VideoPublishTask.cleanup_lease_expires_at < now),
            )
            .order_by(VideoPublishTask.id)
            .with_for_update(skip_locked=True)
        )
    )
    for task in tasks:
        if task.stage == TaskStage.AWAITING_UPLOAD.value:
            task.status = TaskStatus.CANCELLED.value
            set_task_stage(task, TaskStage.DONE, now=now)
            task.completed_at = now
            release_lease(task)
            task.last_error_code = "abandoned_upload_expired"
            task.last_error_message = "Upload was not confirmed within 24 hours"
        task.cleanup_intent = CleanupIntent.PRESERVE_STATE.value
        task.object_cleanup_status = "pending"
        task.object_cleanup_error = None
        task.object_cleanup_next_attempt_at = max(
            filter(None, (now, task.object_upload_expires_at))
        )
        task.cleanup_lease_owner = None
        task.cleanup_lease_expires_at = None
        task.cleanup_heartbeat_at = None
        task.row_version += 1
    session.flush()
    return len(tasks)


def queue_task(
    session: Session, task: VideoPublishTask, *, delay_seconds: int = 0
) -> None:
    now = _db_now(session)
    task.status = TaskStatus.PENDING.value
    task.cleanup_intent = CleanupIntent.NONE.value
    task.cleanup_lease_owner = None
    task.cleanup_lease_expires_at = None
    task.cleanup_heartbeat_at = None
    set_task_stage(task, TaskStage.QUEUED, now=now)
    task.queued_at = now
    task.next_attempt_at = (
        now + timedelta(seconds=delay_seconds) if delay_seconds else None
    )
    task.completed_at = None
    task.last_error_code = None
    task.last_error_message = None
    release_lease(task)
    task.row_version += 1
