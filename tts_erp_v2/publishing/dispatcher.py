"""Resilient single-task dispatcher with repository-owned fenced transitions."""

from __future__ import annotations

import asyncio
import contextlib
import dataclasses
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from time import monotonic
from typing import Any
from uuid import UUID

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session, sessionmaker

from tts_erp_v2.api.deps import require_destructive_script_guard
from tts_erp_v2.db.models.publishing import VideoPublishAttempt, VideoPublishTask
from tts_erp_v2.publishing.adb_device import AdbDevice, DeviceUnavailable
from tts_erp_v2.publishing.artemis_client import (
    ArtemisAdmissionRejected,
    ArtemisClient,
    ArtemisResult,
    ArtemisTransportError,
)
from tts_erp_v2.publishing.diagnostics import sanitize_artemis_output, sanitize_text
from tts_erp_v2.publishing.domain import AttemptStatus, TaskStage
from tts_erp_v2.publishing.object_store import (
    ObjectVersionMismatch,
    VideoObjectStore,
)
from tts_erp_v2.publishing.observability import emit_publish_event
from tts_erp_v2.publishing.repository import (
    AdmissionRejected,
    AdvanceExecution,
    AttemptObservation,
    CleanupClaimRequest,
    CleanupWork,
    InvalidateConfirmedObject,
    LeaseLost,
    ObserveAttempt,
    OperationalFailure,
    PrepareAttempt,
    PublishLeaseToken,
    PublishTransitionCommand,
    PublishTransitionOutcome,
    RecoverPersistedAttempt,
    _lease_task,
    claim_cleanup_work,
    claim_one,
    commit_publish_transition,
    finish_cleanup_work,
    has_pending_device_cleanup,
    renew_cleanup_work,
    schedule_retention_cleanup,
)
from tts_erp_v2.storage.minio_client import ObjectNotFound


@dataclass(slots=True)
class PublishDependencies:
    session_factory: sessionmaker[Session]
    store: VideoObjectStore
    adb: AdbDevice
    artemis: ArtemisClient
    spool_dir: Path
    instance_id: str
    max_attempts: int = 3
    lease_seconds: int = 30
    poll_seconds: float = 2.0
    artemis_profile: str = "pro"
    artemis_verification_level: str = "strict"


async def dispatch_one(deps: PublishDependencies) -> str:
    with deps.session_factory() as session:
        schedule_retention_cleanup(session)
        session.commit()
        task = _lease_task(session, deps.instance_id, deps.lease_seconds)
        if task is not None:
            task_id = task.public_id
            device_serial = task.target_device_serial
            session.commit()
            emit_publish_event(
                "publish_task_claimed",
                task_id=task_id,
                stage=task.stage,
                outcome=task.status,
                device_serial=device_serial,
            )
        else:
            session.rollback()
            task_id = None
    if task_id is not None:
        await _execute(task_id, deps)
        return "processed"

    work = claim_cleanup_work(
        deps.session_factory,
        CleanupClaimRequest(
            owner=deps.instance_id,
            lease_seconds=deps.lease_seconds,
            scope="device",
        ),
    )
    if work is not None:
        await _execute_cleanup(work, deps)
        return "processed"

    with deps.session_factory() as session:
        task = claim_one(
            session,
            deps.instance_id,
            lease_seconds=deps.lease_seconds,
            max_attempts=deps.max_attempts,
        )
        if task is not None:
            task_id = task.public_id
            device_serial = task.target_device_serial
            session.commit()
            emit_publish_event(
                "publish_task_claimed",
                task_id=task_id,
                stage=task.stage,
                outcome=task.status,
                device_serial=device_serial,
            )
        else:
            session.rollback()
            task_id = None
    if task_id is not None:
        await _execute(task_id, deps)
        return "processed"

    work = claim_cleanup_work(
        deps.session_factory,
        CleanupClaimRequest(
            owner=deps.instance_id,
            lease_seconds=deps.lease_seconds,
            scope="background",
        ),
    )
    if work is not None:
        await _execute_cleanup(work, deps)
        return "processed"
    return "no_task"


def _publish_token(
    deps: PublishDependencies,
    task_id: UUID,
    *,
    attempt_id: int | None = None,
) -> PublishLeaseToken:
    with deps.session_factory() as session:
        row = session.execute(
            select(
                VideoPublishTask.lease_owner,
                VideoPublishTask.lease_expires_at,
                VideoPublishTask.row_version,
            ).where(VideoPublishTask.public_id == task_id)
        ).one_or_none()
        if (
            row is None
            or row.lease_owner != deps.instance_id
            or row.lease_expires_at is None
        ):
            raise LeaseLost(task_id)
        expected_status = None
        if attempt_id is not None:
            expected_status = session.scalar(
                select(VideoPublishAttempt.status).where(
                    VideoPublishAttempt.id == attempt_id,
                    VideoPublishAttempt.task_id
                    == select(VideoPublishTask.id)
                    .where(VideoPublishTask.public_id == task_id)
                    .scalar_subquery(),
                )
            )
            if expected_status is None:
                raise LeaseLost(task_id)
        return PublishLeaseToken(
            task_id=task_id,
            lease_owner=deps.instance_id,
            row_version=row.row_version,
            attempt_id=attempt_id,
            expected_attempt_status=expected_status,
        )


def _apply_publish_command(
    deps: PublishDependencies,
    task_id: UUID,
    command: PublishTransitionCommand,
    *,
    attempt_id: int | None = None,
) -> PublishTransitionOutcome:
    token = _publish_token(deps, task_id, attempt_id=attempt_id)
    return commit_publish_transition(deps.session_factory, token, command)


async def _execute(task_id: UUID, deps: PublishDependencies) -> None:
    local = deps.spool_dir / str(task_id) / "video.mp4"
    pre_artemis = True
    try:
        with deps.session_factory() as session:
            task = session.scalar(
                select(VideoPublishTask)
                .where(VideoPublishTask.public_id == task_id)
                .options()
            )
            if task is None:
                raise LookupError(task_id)
            attempts = list(
                session.scalars(
                    select(VideoPublishAttempt)
                    .where(VideoPublishAttempt.task_id == task.id)
                    .order_by(VideoPublishAttempt.sequence_no)
                )
            )
            latest = attempts[-1] if attempts else None
            if (
                latest is not None
                and latest.kind == "publish"
                and latest.status
                in {
                    AttemptStatus.SUCCESS.value,
                    AttemptStatus.FAILED.value,
                    AttemptStatus.REJECTED.value,
                    AttemptStatus.CANCELLED.value,
                }
                and task.stage
                in {
                    TaskStage.DISPATCHING_ARTEMIS.value,
                    TaskStage.WAITING_ARTEMIS.value,
                    TaskStage.VERIFYING.value,
                }
            ):
                recovery_id = latest.id
            else:
                recovery_id = None
            active = next(
                (
                    attempt
                    for attempt in reversed(attempts)
                    if attempt.status
                    in {
                        AttemptStatus.CREATED.value,
                        AttemptStatus.SUBMITTING.value,
                        AttemptStatus.QUEUED.value,
                        AttemptStatus.RUNNING.value,
                        AttemptStatus.UNKNOWN.value,
                    }
                ),
                None,
            )
            if (
                recovery_id is None
                and active is not None
                and task.stage
                in {
                    TaskStage.VERIFYING.value,
                    TaskStage.WAITING_ARTEMIS.value,
                    TaskStage.DISPATCHING_ARTEMIS.value,
                }
            ):
                active_id = active.id
            else:
                active_id = None
            object_key = task.object_key
            object_etag = task.object_etag
            size_bytes = task.size_bytes
            device_serial = task.target_device_serial
            app_package = task.target_app_package

        if recovery_id is not None:
            outcome = _apply_publish_command(
                deps,
                task_id,
                RecoverPersistedAttempt(
                    max_attempts=deps.max_attempts,
                    lease_seconds=deps.lease_seconds,
                ),
                attempt_id=recovery_id,
            )
            if outcome.follow_up == "run_verify":
                assert outcome.verify_attempt_id is not None
                await _run_attempt(task_id, outcome.verify_attempt_id, deps)
            elif outcome.follow_up == "claim_device_cleanup":
                await _cleanup_success(task_id, deps)
            return
        if active_id is not None:
            await _run_attempt(task_id, active_id, deps)
            return
        with deps.session_factory() as session:
            blocked = has_pending_device_cleanup(session)
        if blocked:
            _apply_publish_command(
                deps,
                task_id,
                OperationalFailure(
                    action="safe_retry",
                    code="DEVICE_CLEANUP_BLOCKED",
                    max_attempts=deps.max_attempts,
                    retry_stage=TaskStage.WAITING_DEVICE.value,
                ),
            )
            return

        _apply_publish_command(
            deps,
            task_id,
            AdvanceExecution(
                stage=TaskStage.DOWNLOADING.value,
                lease_seconds=deps.lease_seconds,
                spool_path=str(local),
                register_spool_cleanup=True,
            ),
        )
        if not object_etag:
            raise ObjectVersionMismatch("CONFIRMED_OBJECT_ETAG_MISSING")
        download_started = monotonic()
        emit_publish_event(
            "object_download_started",
            task_id=task_id,
            stage=TaskStage.DOWNLOADING.value,
            outcome="started",
            device_serial=device_serial,
        )
        digest = await _run_external(
            task_id,
            deps,
            lambda: asyncio.to_thread(
                deps.store.download, object_key, local, object_etag
            ),
        )
        _apply_publish_command(
            deps,
            task_id,
            AdvanceExecution(
                stage=TaskStage.DOWNLOADING.value,
                lease_seconds=deps.lease_seconds,
                object_sha256=digest,
            ),
        )
        if local.stat().st_size != size_bytes:
            raise RuntimeError("DOWNLOAD_SIZE_MISMATCH")
        emit_publish_event(
            "object_download_succeeded",
            task_id=task_id,
            stage=TaskStage.DOWNLOADING.value,
            outcome="succeeded",
            device_serial=device_serial,
            duration_ms=int((monotonic() - download_started) * 1000),
        )
        device_path = deps.adb.device_path(task_id)

        async def preflight_device() -> None:
            await deps.adb.check_device(device_serial)
            await deps.adb.check_package(device_serial, app_package)
            await deps.adb.ensure_album_empty(device_serial)

        await _run_external(task_id, deps, preflight_device)
        _apply_publish_command(
            deps,
            task_id,
            AdvanceExecution(
                stage=TaskStage.STAGING_DEVICE.value,
                lease_seconds=deps.lease_seconds,
                device_path=device_path,
                register_cleanup=True,
            ),
        )

        async def stage_device() -> None:
            await deps.adb.stage_video(device_serial, local, device_path)
            await deps.adb.verify_media_visible(device_serial, device_path)

        emit_publish_event(
            "device_stage_started",
            task_id=task_id,
            stage=TaskStage.STAGING_DEVICE.value,
            outcome="started",
            device_serial=device_serial,
        )
        await _run_external(task_id, deps, stage_device)
        outcome = _apply_publish_command(
            deps,
            task_id,
            PrepareAttempt(
                lease_seconds=deps.lease_seconds,
                max_attempts=deps.max_attempts,
            ),
        )
        if outcome.retained_token is None or outcome.retained_token.attempt_id is None:
            raise LeaseLost(task_id)
        pre_artemis = False
        await _run_attempt(
            task_id, outcome.retained_token.attempt_id, deps, prepared=True
        )
    except LeaseLost:
        return
    except ObjectVersionMismatch as exc:
        reason = (
            "etag_missing"
            if str(exc) == "CONFIRMED_OBJECT_ETAG_MISSING"
            else "replaced"
        )
        _apply_publish_command(deps, task_id, InvalidateConfirmedObject(reason=reason))
    except ObjectNotFound:
        _apply_publish_command(
            deps, task_id, InvalidateConfirmedObject(reason="missing")
        )
    except DeviceUnavailable as exc:
        await _safe_retry(
            task_id,
            "DEVICE_UNAVAILABLE",
            deps,
            stage=TaskStage.WAITING_DEVICE.value,
            message=str(exc),
        )
    except Exception as exc:  # noqa: BLE001 - persist sanitized worker failures
        if pre_artemis:
            await _safe_retry(
                task_id,
                "WORKER_OPERATION_FAILED",
                deps,
                stage=TaskStage.QUEUED.value,
                message=str(exc),
            )
        else:
            await _mark_unexpected(task_id, str(exc), deps)


async def _handle_admission_rejection(
    task_id: UUID,
    attempt_id: int,
    code: str,
    deps: PublishDependencies,
) -> None:
    outcome = _apply_publish_command(
        deps,
        task_id,
        AdmissionRejected(
            code=sanitize_text(code),
            max_attempts=deps.max_attempts,
            lease_seconds=deps.lease_seconds,
        ),
        attempt_id=attempt_id,
    )
    if outcome.follow_up == "claim_device_cleanup":
        await _cleanup_success(task_id, deps)


async def _run_attempt(
    task_id: UUID,
    attempt_id: int,
    deps: PublishDependencies,
    *,
    prepared: bool = False,
) -> None:
    with deps.session_factory() as session:
        initial = session.get(VideoPublishAttempt, attempt_id)
        if initial is None:
            raise LeaseLost(task_id)
        initial_status = initial.status
        initial_submitted_at = initial.submitted_at
    should_submit = prepared or initial_status == AttemptStatus.CREATED.value
    admission_probe = (
        initial_status == AttemptStatus.SUBMITTING.value
        and initial_submitted_at is None
        and not prepared
    )
    if not prepared:
        _apply_publish_command(
            deps,
            task_id,
            PrepareAttempt(
                attempt_id=attempt_id,
                lease_seconds=deps.lease_seconds,
                max_attempts=deps.max_attempts,
            ),
            attempt_id=attempt_id,
        )
    with deps.session_factory() as session:
        attempt = session.get(VideoPublishAttempt, attempt_id)
        task = session.scalar(
            select(VideoPublishTask).where(VideoPublishTask.public_id == task_id)
        )
        if attempt is None or task is None:
            raise LeaseLost(task_id)
        goal = attempt.prompt_snapshot
        session_id = attempt.artemis_session_id
        device_serial = attempt.device_serial
        app_package = task.target_app_package

    result: ArtemisResult
    if should_submit:
        try:
            result = await _run_external(
                task_id,
                deps,
                lambda: deps.artemis.submit(
                    goal=goal,
                    session_id=session_id,
                    device_serial=device_serial,
                    app_package=app_package,
                    profile=getattr(deps, "artemis_profile", "pro"),
                    verification_level=getattr(
                        deps, "artemis_verification_level", "strict"
                    ),
                ),
            )
        except ArtemisAdmissionRejected as exc:
            await _handle_admission_rejection(task_id, attempt_id, exc.code, deps)
            return
        except ArtemisTransportError:
            try:
                result = await _run_external(
                    task_id,
                    deps,
                    lambda: _query_after_submit_transport_error(
                        deps, session_id, goal, device_serial, app_package
                    ),
                )
            except ArtemisAdmissionRejected as exc:
                await _handle_admission_rejection(task_id, attempt_id, exc.code, deps)
                return
            except ArtemisTransportError:
                return
    elif admission_probe:
        try:
            result = await _run_external(
                task_id, deps, lambda: deps.artemis.get_task(session_id)
            )
        except ArtemisTransportError:
            return
        if result.status in {"missing", "not_found"}:
            try:
                result = await _run_external(
                    task_id,
                    deps,
                    lambda: deps.artemis.submit(
                        goal=goal,
                        session_id=session_id,
                        device_serial=device_serial,
                        app_package=app_package,
                        profile=getattr(deps, "artemis_profile", "pro"),
                        verification_level=getattr(
                            deps, "artemis_verification_level", "strict"
                        ),
                    ),
                )
                result = dataclasses.replace(result, same_session_resubmitted=True)
            except ArtemisAdmissionRejected as exc:
                await _handle_admission_rejection(task_id, attempt_id, exc.code, deps)
                return
            except ArtemisTransportError:
                # This same-session submit may have been admitted. Preserve the
                # active attempt for a later query; never enter business retry.
                return
    else:
        try:
            result = await _run_external(
                task_id, deps, lambda: deps.artemis.get_task(session_id)
            )
        except ArtemisTransportError:
            return

    if result.terminal and result.status != "success" and result.steps_count is None:
        try:
            steps_count = await _run_external(
                task_id, deps, lambda: deps.artemis.get_steps_count(session_id)
            )
            result = dataclasses.replace(result, steps_count=steps_count)
        except ArtemisTransportError:
            pass

    with deps.session_factory() as session:
        kind = session.scalar(
            select(VideoPublishAttempt.kind).where(VideoPublishAttempt.id == attempt_id)
        )
    candidate_verdict = (result.output or {}).get("verdict")
    verdict = (
        candidate_verdict
        if kind == "verify"
        and result.status == "success"
        and candidate_verdict in {"published", "not_published", "inconclusive"}
        else "inconclusive"
        if kind == "verify" and result.terminal
        else None
    )
    observation = AttemptObservation(
        status=result.status,
        terminal=result.terminal,
        verdict=verdict,
        steps_count=result.steps_count,
        final_publish_observed=result.final_publish_observed,
        same_session_resubmitted=result.same_session_resubmitted,
        sanitized_output=sanitize_artemis_output(result.output),
        sanitized_error=sanitize_text(result.error) if result.error else None,
        submitted=should_submit or admission_probe,
    )
    outcome = _apply_publish_command(
        deps,
        task_id,
        ObserveAttempt(
            observation=observation,
            max_attempts=deps.max_attempts,
            lease_seconds=deps.lease_seconds,
        ),
        attempt_id=attempt_id,
    )
    if outcome.follow_up == "run_verify":
        assert outcome.verify_attempt_id is not None
        await _run_attempt(task_id, outcome.verify_attempt_id, deps)
    elif outcome.follow_up == "claim_device_cleanup":
        await _cleanup_success(task_id, deps)


async def _query_after_submit_transport_error(
    deps: PublishDependencies,
    session_id: UUID,
    goal: str,
    device_serial: str,
    app_package: str,
) -> ArtemisResult:
    result = await deps.artemis.get_task(session_id)
    if result.status in {"missing", "not_found"}:
        result = await deps.artemis.submit(
            goal=goal,
            session_id=session_id,
            device_serial=device_serial,
            app_package=app_package,
            profile=getattr(deps, "artemis_profile", "pro"),
            verification_level=getattr(deps, "artemis_verification_level", "strict"),
        )
        return dataclasses.replace(result, same_session_resubmitted=True)
    return result


async def _run_external(
    task_id: UUID,
    deps: PublishDependencies,
    operation: Callable[[], Awaitable[Any]],
) -> Any:
    lost = asyncio.Event()
    _renew_lease_now(task_id, deps)
    heartbeat = asyncio.create_task(
        _lease_heartbeat(task_id, deps, lost), name="publish-lease-heartbeat"
    )
    operation_error: BaseException | None = None
    result: Any = None
    try:
        result = await operation()
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001 - re-raise after fencing check
        operation_error = exc
    finally:
        heartbeat.cancel()
        await asyncio.gather(heartbeat, return_exceptions=True)
    if lost.is_set():
        raise LeaseLost(task_id)
    if operation_error is not None:
        raise operation_error
    return result


def _renew_lease_now(task_id: UUID, deps: PublishDependencies) -> None:
    with deps.session_factory() as session:
        database_now = session.scalar(select(func.clock_timestamp()))
        if database_now is None:
            raise RuntimeError("DATABASE_TIME_UNAVAILABLE")
        version = session.scalar(
            select(VideoPublishTask.row_version).where(
                VideoPublishTask.public_id == task_id,
                VideoPublishTask.lease_owner == deps.instance_id,
                VideoPublishTask.lease_expires_at > database_now,
            )
        )
        if version is None:
            raise LeaseLost(task_id)
        result = session.execute(
            update(VideoPublishTask)
            .where(
                VideoPublishTask.public_id == task_id,
                VideoPublishTask.lease_owner == deps.instance_id,
                VideoPublishTask.lease_expires_at > database_now,
                VideoPublishTask.row_version == version,
            )
            .values(
                heartbeat_at=database_now,
                lease_expires_at=database_now + timedelta(seconds=deps.lease_seconds),
                row_version=version + 1,
            )
        )
        if getattr(result, "rowcount", None) != 1:
            session.rollback()
            raise LeaseLost(task_id)
        session.commit()


async def _lease_heartbeat(
    task_id: UUID, deps: PublishDependencies, lost: asyncio.Event
) -> None:
    try:
        while True:
            await asyncio.sleep(max(1, min(5, deps.lease_seconds / 3)))
            try:
                await asyncio.to_thread(_renew_lease_now, task_id, deps)
            except LeaseLost:
                lost.set()
                return
    except asyncio.CancelledError:
        raise
    except Exception:  # noqa: BLE001 - owner loss is checked by caller
        lost.set()


async def _safe_retry(
    task_id: UUID,
    code: str,
    deps: PublishDependencies,
    *,
    stage: str = TaskStage.QUEUED.value,
    message: str | None = None,
) -> None:
    try:
        _apply_publish_command(
            deps,
            task_id,
            OperationalFailure(
                action="safe_retry",
                code=code,
                max_attempts=deps.max_attempts,
                retry_stage=stage,
                message=sanitize_text(message) if message else None,
            ),
        )
    except LeaseLost:
        return


async def _mark_failed(task_id: UUID, code: str, deps: PublishDependencies) -> None:
    try:
        _apply_publish_command(
            deps,
            task_id,
            OperationalFailure(
                action="failed",
                code=code,
                max_attempts=deps.max_attempts,
                message=code,
            ),
        )
    except LeaseLost:
        return


async def _mark_unexpected(
    task_id: UUID, error: str, deps: PublishDependencies
) -> None:
    try:
        _apply_publish_command(
            deps,
            task_id,
            OperationalFailure(
                action="unexpected",
                code="UNEXPECTED_WORKER_FAILURE",
                max_attempts=deps.max_attempts,
                message=sanitize_text(error),
            ),
        )
    except LeaseLost:
        return


async def _cleanup_lease_heartbeat(
    holder: list,
    deps: PublishDependencies,
    lost: asyncio.Event,
) -> None:
    try:
        while True:
            await asyncio.sleep(max(0.05, min(5, deps.lease_seconds / 3)))
            try:
                holder[0] = await asyncio.to_thread(
                    renew_cleanup_work,
                    deps.session_factory,
                    holder[0],
                    deps.lease_seconds,
                )
            except LeaseLost:
                lost.set()
                return
    except asyncio.CancelledError:
        raise
    except Exception:  # noqa: BLE001 - caller treats renewal failure as owner loss
        lost.set()


async def _run_cleanup_external(
    token,
    deps: PublishDependencies,
    operation: Callable[[], Awaitable[Any]],
):
    holder = [
        await asyncio.to_thread(
            renew_cleanup_work,
            deps.session_factory,
            token,
            deps.lease_seconds,
        )
    ]
    lost = asyncio.Event()
    heartbeat = asyncio.create_task(
        _cleanup_lease_heartbeat(holder, deps, lost),
        name="cleanup-lease-heartbeat",
    )
    operation_error: BaseException | None = None
    try:
        await operation()
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001 - fence before reporting resource error
        operation_error = exc
    finally:
        heartbeat.cancel()
        await asyncio.gather(heartbeat, return_exceptions=True)
    if lost.is_set():
        raise LeaseLost(token.task_id)
    return holder[0], operation_error


async def _execute_cleanup(work: CleanupWork, deps: PublishDependencies) -> None:
    errors: dict[str, str | None] = {}
    try:
        require_destructive_script_guard(
            script_name="video_publish.cleanup_resources",
            confirmation=True,
            dangerous=True,
        )
        guard_error = None
    except SystemExit:
        guard_error = "DESTRUCTIVE_GUARD_BLOCKED"

    token = work.token

    async def run(name: str, operation: Callable[[], Awaitable[Any]]) -> None:
        nonlocal token
        if name not in work.resources:
            return
        if guard_error is not None:
            errors[name] = guard_error
            return
        token, operation_error = await _run_cleanup_external(token, deps, operation)
        if operation_error is None:
            errors[name] = None
        else:
            errors[name] = (
                f"CLEANUP_{name.upper()}_FAILED: {sanitize_text(operation_error)}"
            )[:2000]

    try:
        await run(
            "device",
            lambda: deps.adb.remove_staged_video(
                work.device_serial, work.device_path or ""
            ),
        )
        expected_spool_path = deps.spool_dir / str(work.task_id) / "video.mp4"
        spool_path = Path(work.spool_path) if work.spool_path else expected_spool_path
        if spool_path != expected_spool_path:

            async def reject_unmanaged_spool() -> None:
                raise ValueError("refusing to remove an unmanaged spool path")

            spool_cleanup = reject_unmanaged_spool
        else:

            async def remove_managed_spool() -> None:
                await _remove_spool(spool_path)

            spool_cleanup = remove_managed_spool
        await run(
            "spool",
            spool_cleanup,
        )
        await run(
            "object", lambda: asyncio.to_thread(deps.store.remove, work.object_key)
        )
        await asyncio.to_thread(
            finish_cleanup_work, deps.session_factory, token, errors
        )
    except LeaseLost:
        # A takeover owns reconciliation. This worker must not finalize.
        return


async def _remove_spool(path: Path) -> None:
    await asyncio.to_thread(path.unlink, missing_ok=True)
    with contextlib.suppress(FileNotFoundError):
        await asyncio.to_thread(path.parent.rmdir)


async def _cleanup_success(task_id: UUID, deps: PublishDependencies) -> None:
    work = claim_cleanup_work(
        deps.session_factory,
        CleanupClaimRequest(
            owner=deps.instance_id,
            lease_seconds=deps.lease_seconds,
            scope="device",
            task_id=task_id,
        ),
    )
    if work is not None:
        await _execute_cleanup(work, deps)


async def recover_active(deps: PublishDependencies) -> str:
    with deps.session_factory() as session:
        task = _lease_task(session, deps.instance_id, deps.lease_seconds)
        if task is None:
            session.commit()
            return "no_active_task"
        task_id = task.public_id
        stage = task.stage
        status = task.status
        device_serial = task.target_device_serial
        session.commit()
    emit_publish_event(
        "worker_recovered_task",
        task_id=task_id,
        stage=stage,
        outcome=status,
        device_serial=device_serial,
    )
    await _execute(task_id, deps)
    return "recovered"
