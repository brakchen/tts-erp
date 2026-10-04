"""Resilient single-task dispatcher."""

from __future__ import annotations

import asyncio
import dataclasses
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session, sessionmaker

from tts_erp_v2.api.deps import require_destructive_script_guard
from tts_erp_v2.db.models.publishing import VideoPublishAttempt, VideoPublishTask
from tts_erp_v2.publishing.adb_device import AdbDevice, DeviceUnavailable
from tts_erp_v2.publishing.artemis_client import (
    ArtemisClient,
    ArtemisResult,
    ArtemisTransportError,
)
from tts_erp_v2.publishing.domain import (
    AttemptKind,
    AttemptStatus,
    TaskStage,
    TaskStatus,
    classify_failure,
)
from tts_erp_v2.publishing.object_store import VideoObjectStore
from tts_erp_v2.publishing.repository import (
    _lease_cleanup_task,
    _lease_task,
    claim_one,
    create_attempt,
    has_pending_device_cleanup,
    release_lease,
    touch_task,
)


class LeaseLost(RuntimeError):
    """The worker no longer owns the task lease."""


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


async def dispatch_one(deps: PublishDependencies) -> str:
    with deps.session_factory() as session:
        task = _lease_task(session, deps.instance_id, deps.lease_seconds)
        if task is None:
            task = _lease_cleanup_task(session, deps.instance_id, deps.lease_seconds)
        if task is None:
            task = claim_one(
                session,
                deps.instance_id,
                lease_seconds=deps.lease_seconds,
                max_attempts=deps.max_attempts,
            )
        if task is None:
            session.commit()
            return "no_task"
        task_id = task.public_id
        session.commit()
    await _execute(task_id, deps)
    return "processed"


async def _execute(task_id: UUID, deps: PublishDependencies) -> None:
    local = deps.spool_dir / str(task_id) / "video.mp4"
    pre_artemis = True
    try:
        with deps.session_factory() as session:
            task = _get(session, task_id)
            _assert_lease(task, deps.instance_id)
            if task.stage in {
                TaskStage.VERIFYING.value,
                TaskStage.WAITING_ARTEMIS.value,
                TaskStage.DISPATCHING_ARTEMIS.value,
            }:
                active_attempt = next(
                    (
                        a
                        for a in reversed(task.attempts)
                        if a.status
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
                if active_attempt is not None:
                    attempt_id = active_attempt.id
                    session.commit()
                    await _run_attempt(task_id, attempt_id, deps)
                    return
            if (
                task.stage == TaskStage.CLEANING.value
                or (
                    task.status
                    in {TaskStatus.SUCCEEDED.value, TaskStatus.CANCELLED.value}
                    and (
                        task.device_cleanup_status in {"pending", "failed"}
                        or task.object_cleanup_status in {"pending", "failed"}
                    )
                )
                or (
                    task.status
                    in {
                        TaskStatus.SUCCEEDED.value,
                        TaskStatus.CANCELLED.value,
                        TaskStatus.FAILED.value,
                        TaskStatus.NEEDS_REVIEW.value,
                    }
                    and task.spool_cleanup_status in {"pending", "failed"}
                )
            ):
                session.commit()
                await _cleanup_success(task_id, deps)
                return
            if has_pending_device_cleanup(session):
                _defer_for_device_cleanup(task)
                session.commit()
                return
            object_key = task.object_key
            size_bytes = task.size_bytes
            device_serial = task.target_device_serial
            app_package = task.target_app_package
            touch_task(
                session,
                task,
                stage=TaskStage.DOWNLOADING.value,
                lease_seconds=deps.lease_seconds,
            )
            session.commit()
        digest = await _run_external(
            task_id,
            deps,
            lambda: asyncio.to_thread(deps.store.download, object_key, local),
        )
        if local.stat().st_size != size_bytes:
            raise RuntimeError("DOWNLOAD_SIZE_MISMATCH")
        with deps.session_factory() as session:
            task = _get(session, task_id)
            _assert_lease(task, deps.instance_id)
            task.object_sha256 = digest
            touch_task(
                session,
                task,
                stage=TaskStage.STAGING_DEVICE.value,
                lease_seconds=deps.lease_seconds,
            )
            session.commit()

        device_path = deps.adb.device_path(task_id)
        with deps.session_factory() as session:
            task = _get(session, task_id)
            _assert_lease(task, deps.instance_id)
            task.device_path = device_path
            task.device_cleanup_status = "pending"
            touch_task(
                session,
                task,
                stage=TaskStage.STAGING_DEVICE.value,
                lease_seconds=deps.lease_seconds,
            )
            session.commit()

        async def stage_device() -> None:
            await deps.adb.check_device(device_serial)
            await deps.adb.check_package(device_serial, app_package)
            await deps.adb.stage_video(
                device_serial,
                local,
                deps.adb.device_path(task_id),
            )
            await deps.adb.verify_media_visible(
                device_serial, deps.adb.device_path(task_id)
            )

        await _run_external(task_id, deps, stage_device)
        with deps.session_factory() as session:
            task = _get(session, task_id)
            _assert_lease(task, deps.instance_id)
            touch_task(
                session,
                task,
                stage=TaskStage.DISPATCHING_ARTEMIS.value,
                lease_seconds=deps.lease_seconds,
            )
            attempt = create_attempt(session, task, kind=AttemptKind.PUBLISH)
            session.commit()
            attempt_id = attempt.id
        pre_artemis = False
        await _run_attempt(task_id, attempt_id, deps)
    except LeaseLost:
        return
    except DeviceUnavailable as exc:
        await _safe_retry(
            task_id,
            f"DEVICE_UNAVAILABLE:{exc}",
            deps,
            stage=TaskStage.WAITING_DEVICE.value,
        )
    except Exception as exc:  # noqa: BLE001 - persist worker failures
        if pre_artemis:
            await _safe_retry(task_id, str(exc), deps, stage=TaskStage.QUEUED.value)
        else:
            await _mark_unexpected(task_id, str(exc), deps)
    finally:
        spool_error: str | None = None
        try:
            local.unlink(missing_ok=True)
        except OSError as exc:
            spool_error = str(exc)[:500]
        if spool_error is None:
            try:
                local.parent.rmdir()
            except FileNotFoundError:
                pass
            except OSError as exc:
                spool_error = str(exc)[:500]
        await _mark_spool_cleanup(task_id, deps, error=spool_error)


async def _run_attempt(
    task_id: UUID, attempt_id: int, deps: PublishDependencies
) -> None:
    with deps.session_factory() as session:
        task = _get(session, task_id)
        _assert_lease(task, deps.instance_id)
        attempt = _require_attempt(session, attempt_id)
        should_submit = attempt.status == AttemptStatus.CREATED.value
        admission_probe = (
            attempt.status == AttemptStatus.SUBMITTING.value
            and attempt.submitted_at is None
        )
        goal = attempt.prompt_snapshot
        session_id = attempt.artemis_session_id
        device_serial = attempt.device_serial
        app_package = task.target_app_package
        attempt.status = (
            AttemptStatus.SUBMITTING.value if should_submit else attempt.status
        )
        task.stage = (
            TaskStage.VERIFYING.value
            if attempt.kind == AttemptKind.VERIFY.value
            else TaskStage.DISPATCHING_ARTEMIS.value
        )
        touch_task(session, task, lease_seconds=deps.lease_seconds)
        session.commit()

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
                ),
            )
        except ArtemisTransportError:
            try:
                result = await _run_external(
                    task_id,
                    deps,
                    lambda: _query_after_submit_transport_error(
                        deps, session_id, goal, device_serial, app_package
                    ),
                )
            except ArtemisTransportError:
                # Admission is still ambiguous; keep this session active and
                # let the next dispatch cycle poll the same ID.
                return
    elif admission_probe:
        try:
            result = await _run_external(
                task_id, deps, lambda: deps.artemis.get_task(session_id)
            )
        except ArtemisTransportError:
            return
        if result.status in {"missing", "not_found"}:
            result = await _run_external(
                task_id,
                deps,
                lambda: deps.artemis.submit(
                    goal=goal,
                    session_id=session_id,
                    device_serial=device_serial,
                    app_package=app_package,
                ),
            )
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
        attempt = _require_attempt(session, attempt_id)
        task = _get(session, task_id)
        _assert_lease(task, deps.instance_id)
        attempt.last_polled_at = datetime.now(UTC)
        if should_submit or admission_probe:
            attempt.submitted_at = attempt.submitted_at or datetime.now(UTC)
        if not result.terminal:
            attempt.status = (
                result.status
                if result.status in {"queued", "running"}
                else AttemptStatus.RUNNING.value
            )
            attempt.submitted_at = attempt.submitted_at or datetime.now(UTC)
            task.stage = (
                TaskStage.VERIFYING.value
                if attempt.kind == AttemptKind.VERIFY.value
                else TaskStage.WAITING_ARTEMIS.value
            )
            touch_task(session, task, lease_seconds=deps.lease_seconds)
            session.commit()
            return

        if attempt.kind == AttemptKind.VERIFY.value:
            verdict = (result.output or {}).get("verdict", "inconclusive")
            _save_result(attempt, result)
            if verdict == "published":
                task.stage = TaskStage.CLEANING.value
                task.device_cleanup_status = "pending"
                task.spool_cleanup_status = "pending"
                task.object_cleanup_status = "pending"
                session.commit()
                await _cleanup_success(task_id, deps)
            elif verdict == "not_published":
                task.status = TaskStatus.PENDING.value
                task.stage = TaskStage.QUEUED.value
                release_lease(task)
                session.commit()
            else:
                task.status = TaskStatus.NEEDS_REVIEW.value
                task.stage = TaskStage.DONE.value
                release_lease(task)
                session.commit()
            return

        if result.status == "success":
            _save_result(attempt, result)
            task.stage = TaskStage.CLEANING.value
            task.device_cleanup_status = "pending"
            task.spool_cleanup_status = "pending"
            task.object_cleanup_status = "pending"
            session.commit()
            await _cleanup_success(task_id, deps)
            return

        classification = classify_failure(
            artemis_status=result.status,
            steps_count=result.steps_count,
            final_publish_observed=result.final_publish_observed,
            session_missing=result.status in {"missing", "not_found"},
        )
        attempt.retry_classification = classification.code
        attempt.retry_safe = classification.retry_safe
        _save_result(attempt, result)
        session.commit()

    if classification.requires_verification:
        await _start_verify(task_id, attempt_id, deps)
    elif classification.retry_safe:
        await _safe_retry(task_id, classification.code, deps)
    else:
        await _mark_failed(task_id, classification.code, deps)


async def _query_after_submit_transport_error(
    deps: PublishDependencies,
    session_id: UUID,
    goal: str,
    device_serial: str,
    app_package: str,
) -> ArtemisResult:
    """Probe admission before retrying POST, always reusing the same ID."""
    result = await deps.artemis.get_task(session_id)
    if result.status in {"missing", "not_found"}:
        return await deps.artemis.submit(
            goal=goal,
            session_id=session_id,
            device_serial=device_serial,
            app_package=app_package,
        )
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
    with deps.session_factory() as session:
        _assert_lease(_get(session, task_id), deps.instance_id)
    return result


def _renew_lease_now(task_id: UUID, deps: PublishDependencies) -> None:
    with deps.session_factory() as session:
        task = _get(session, task_id)
        _assert_lease(task, deps.instance_id)
        expected_version = task.row_version
        result = session.execute(
            update(VideoPublishTask)
            .where(
                VideoPublishTask.public_id == task_id,
                VideoPublishTask.lease_owner == deps.instance_id,
                VideoPublishTask.lease_expires_at > func.now(),
                VideoPublishTask.row_version == expected_version,
            )
            .values(
                heartbeat_at=func.now(),
                lease_expires_at=func.now() + timedelta(seconds=deps.lease_seconds),
                row_version=expected_version + 1,
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
            with deps.session_factory() as session:
                version = session.scalar(
                    select(VideoPublishTask.row_version).where(
                        VideoPublishTask.public_id == task_id,
                        VideoPublishTask.lease_owner == deps.instance_id,
                        VideoPublishTask.lease_expires_at > func.now(),
                    )
                )
                if version is None:
                    lost.set()
                    return
                statement = (
                    update(VideoPublishTask)
                    .where(
                        VideoPublishTask.public_id == task_id,
                        VideoPublishTask.lease_owner == deps.instance_id,
                        VideoPublishTask.lease_expires_at > func.now(),
                        VideoPublishTask.row_version == version,
                    )
                    .values(
                        heartbeat_at=func.now(),
                        lease_expires_at=func.now()
                        + timedelta(seconds=deps.lease_seconds),
                        row_version=VideoPublishTask.row_version + 1,
                    )
                )
                result = session.execute(statement)
                if getattr(result, "rowcount", None) != 1:
                    session.rollback()
                    lost.set()
                    return
                session.commit()
    except asyncio.CancelledError:
        raise
    except Exception:  # noqa: BLE001 - owner loss is checked by caller
        lost.set()


def _assert_lease(task: VideoPublishTask, instance_id: str) -> None:
    if (
        task.lease_owner != instance_id
        or task.lease_expires_at is None
        or task.lease_expires_at <= datetime.now(UTC)
    ):
        raise LeaseLost(task.public_id)


def _require_attempt(session: Session, attempt_id: int) -> VideoPublishAttempt:
    attempt = session.get(VideoPublishAttempt, attempt_id)
    if attempt is None:
        raise LookupError(attempt_id)
    return attempt


def _save_result(attempt: VideoPublishAttempt, result: ArtemisResult) -> None:
    attempt.status = (
        AttemptStatus.SUCCESS.value
        if result.status == "success"
        else AttemptStatus.FAILED.value
    )
    attempt.artemis_output = result.output
    attempt.artemis_error = result.error
    attempt.steps_count = result.steps_count
    attempt.finished_at = datetime.now(UTC)


def _get(session: Session, task_id: UUID) -> VideoPublishTask:
    task = session.scalar(
        select(VideoPublishTask).where(VideoPublishTask.public_id == task_id)
    )
    if task is None:
        raise LookupError(task_id)
    return task


def _cleanup_retry_delay(attempts: int) -> int:
    return min(300, 5 * (2 ** min(attempts, 5)))


def _defer_for_device_cleanup(task: VideoPublishTask) -> None:
    task.status = TaskStatus.PENDING.value
    task.stage = TaskStage.WAITING_DEVICE.value
    task.next_attempt_at = datetime.now(UTC) + timedelta(seconds=30)
    task.lease_owner = None
    task.lease_expires_at = None
    task.heartbeat_at = None
    task.last_error_code = "DEVICE_CLEANUP_BLOCKED"
    task.last_error_message = "A prior task has failed device cleanup"
    task.row_version += 1


async def _safe_retry(
    task_id: UUID,
    error: str,
    deps: PublishDependencies,
    *,
    stage: str = TaskStage.QUEUED.value,
) -> None:
    with deps.session_factory() as session:
        task = _get(session, task_id)
        if task.lease_owner != deps.instance_id:
            session.rollback()
            return
        expected_version = task.row_version
        terminal = task.attempt_count >= deps.max_attempts
        now = datetime.now(UTC)
        delay = min(300, 5 * (2 ** min(task.attempt_count, 5)))
        values = {
            "status": TaskStatus.FAILED.value if terminal else TaskStatus.PENDING.value,
            "stage": TaskStage.DONE.value if terminal else stage,
            "last_error_code": error[:100],
            "last_error_message": error[:500],
            "queued_at": None if terminal else now,
            "next_attempt_at": None if terminal else now + timedelta(seconds=delay),
            "lease_owner": None,
            "lease_expires_at": None,
            "heartbeat_at": None,
            "row_version": expected_version + 1,
        }
        result = session.execute(
            update(VideoPublishTask)
            .where(
                VideoPublishTask.public_id == task_id,
                VideoPublishTask.lease_owner == deps.instance_id,
                VideoPublishTask.row_version == expected_version,
            )
            .values(**values)
        )
        if getattr(result, "rowcount", None) != 1:
            session.rollback()
            return
        session.commit()


async def _mark_failed(task_id: UUID, code: str, deps: PublishDependencies) -> None:
    with deps.session_factory() as session:
        task = _get(session, task_id)
        if task.lease_owner != deps.instance_id:
            session.rollback()
            return
        expected_version = task.row_version
        result = session.execute(
            update(VideoPublishTask)
            .where(
                VideoPublishTask.public_id == task_id,
                VideoPublishTask.lease_owner == deps.instance_id,
                VideoPublishTask.row_version == expected_version,
            )
            .values(
                status=TaskStatus.FAILED.value,
                stage=TaskStage.DONE.value,
                last_error_code=code,
                lease_owner=None,
                lease_expires_at=None,
                heartbeat_at=None,
                row_version=expected_version + 1,
            )
        )
        if getattr(result, "rowcount", None) != 1:
            session.rollback()
            return
        session.commit()


async def _mark_unexpected(
    task_id: UUID, error: str, deps: PublishDependencies
) -> None:
    with deps.session_factory() as session:
        task = _get(session, task_id)
        if task.lease_owner != deps.instance_id:
            session.rollback()
            return
        expected_version = task.row_version
        result = session.execute(
            update(VideoPublishTask)
            .where(
                VideoPublishTask.public_id == task_id,
                VideoPublishTask.lease_owner == deps.instance_id,
                VideoPublishTask.row_version == expected_version,
            )
            .values(
                status=TaskStatus.NEEDS_REVIEW.value,
                stage=TaskStage.DONE.value,
                last_error_code="UNEXPECTED_WORKER_FAILURE",
                last_error_message=error[:500],
                lease_owner=None,
                lease_expires_at=None,
                heartbeat_at=None,
                row_version=expected_version + 1,
            )
        )
        if getattr(result, "rowcount", None) != 1:
            session.rollback()
            return
        session.commit()


async def _mark_spool_cleanup(
    task_id: UUID, deps: PublishDependencies, *, error: str | None = None
) -> None:
    with deps.session_factory() as session:
        task = _get(session, task_id)
        if task.lease_owner not in {None, deps.instance_id}:
            session.rollback()
            return
        if task.spool_cleanup_status not in {"pending", "not_started", "failed"}:
            session.rollback()
            return
        expected_version = task.row_version
        result = session.execute(
            update(VideoPublishTask)
            .where(
                VideoPublishTask.public_id == task_id,
                VideoPublishTask.row_version == expected_version,
                (VideoPublishTask.lease_owner == deps.instance_id)
                | (VideoPublishTask.lease_owner.is_(None)),
            )
            .values(
                spool_cleanup_status="failed" if error else "succeeded",
                spool_cleanup_error=error,
                spool_cleanup_attempts=task.spool_cleanup_attempts
                + (1 if error else 0),
                spool_cleanup_next_attempt_at=(
                    datetime.now(UTC)
                    + timedelta(
                        seconds=_cleanup_retry_delay(task.spool_cleanup_attempts)
                    )
                    if error
                    else None
                ),
                row_version=expected_version + 1,
            )
        )
        if getattr(result, "rowcount", None) != 1:
            session.rollback()
            return
        session.commit()


async def _cleanup_success(task_id: UUID, deps: PublishDependencies) -> None:
    """Clean each resource independently; cleanup never changes business outcome."""
    with deps.session_factory() as session:
        task = _get(session, task_id)
        serial, device_path, object_key = (
            task.target_device_serial,
            task.device_path,
            task.object_key,
        )
        cleanup_business = (
            task.status
            in {
                TaskStatus.SUCCEEDED.value,
                TaskStatus.CANCELLED.value,
            }
            or task.stage == TaskStage.CLEANING.value
        )
        device_needed = cleanup_business and task.device_cleanup_status != "succeeded"
        object_needed = cleanup_business and task.object_cleanup_status != "succeeded"
        business_status = (
            TaskStatus.CANCELLED.value
            if task.status == TaskStatus.CANCELLED.value
            else task.status
            if task.status in {TaskStatus.FAILED.value, TaskStatus.NEEDS_REVIEW.value}
            else TaskStatus.SUCCEEDED.value
        )
        completed_at = task.completed_at or datetime.now(UTC)
        session.commit()
    guard_error = None
    try:
        # One guard covers the entire destructive cleanup routine. It must run
        # before either ADB unlink or MinIO deletion.
        require_destructive_script_guard(
            script_name="video_publish.cleanup_resources",
            confirmation=True,
            dangerous=True,
        )
    except SystemExit:
        guard_error = "DESTRUCTIVE_GUARD_BLOCKED"
    device_error = guard_error
    if guard_error is None and device_needed and device_path:
        try:
            await _run_external(
                task_id,
                deps,
                lambda: deps.adb.remove_staged_video(serial, device_path),
            )
            device_error = None
        except LeaseLost:
            raise
        except Exception as exc:  # noqa: BLE001 - persisted as cleanup status
            device_error = str(exc)[:500]
    object_error = guard_error
    if guard_error is None and object_needed:
        try:
            await _run_external(
                task_id,
                deps,
                lambda: asyncio.to_thread(deps.store.remove, object_key),
            )
            object_error = None
        except LeaseLost:
            raise
        except Exception as exc:  # noqa: BLE001 - persisted as cleanup status
            object_error = str(exc)[:500]
    with deps.session_factory() as session:
        task = _get(session, task_id)
        if task.lease_owner != deps.instance_id:
            session.rollback()
            return
        expected_version = task.row_version
        values: dict[str, Any] = {
            "status": business_status,
            "stage": TaskStage.DONE.value,
            "completed_at": completed_at,
            "lease_owner": None,
            "lease_expires_at": None,
            "heartbeat_at": None,
            "row_version": expected_version + 1,
        }
        if device_needed:
            values["device_cleanup_status"] = "failed" if device_error else "succeeded"
            values["device_cleanup_error"] = device_error
            values["device_cleanup_attempts"] = task.device_cleanup_attempts + (
                1 if device_error else 0
            )
            values["device_cleanup_next_attempt_at"] = (
                datetime.now(UTC)
                + timedelta(seconds=_cleanup_retry_delay(task.device_cleanup_attempts))
                if device_error
                else None
            )
        if object_needed:
            values["object_cleanup_status"] = "failed" if object_error else "succeeded"
            values["object_cleanup_error"] = object_error
            values["object_cleanup_attempts"] = task.object_cleanup_attempts + (
                1 if object_error else 0
            )
            values["object_cleanup_next_attempt_at"] = (
                datetime.now(UTC)
                + timedelta(seconds=_cleanup_retry_delay(task.object_cleanup_attempts))
                if object_error
                else None
            )
            if not object_error:
                values["object_deleted_at"] = datetime.now(UTC)
        result = session.execute(
            update(VideoPublishTask)
            .where(
                VideoPublishTask.public_id == task_id,
                VideoPublishTask.lease_owner == deps.instance_id,
                VideoPublishTask.row_version == expected_version,
            )
            .values(**values)
        )
        if getattr(result, "rowcount", None) != 1:
            session.rollback()
            return
        session.commit()


async def _start_verify(
    task_id: UUID, related_id: int, deps: PublishDependencies
) -> None:
    with deps.session_factory() as session:
        task = _get(session, task_id)
        _assert_lease(task, deps.instance_id)
        task.stage = TaskStage.VERIFYING.value
        attempt = _require_attempt(session, related_id)
        verify = create_attempt(session, task, kind=AttemptKind.VERIFY, related=attempt)
        verify_id = verify.id
        session.commit()
    await _run_attempt(task_id, verify_id, deps)


async def recover_active(deps: PublishDependencies) -> str:
    with deps.session_factory() as session:
        task = _lease_task(session, deps.instance_id, deps.lease_seconds)
        if task is None:
            session.commit()
            return "no_active_task"
        task_id = task.public_id
        session.commit()
    await _execute(task_id, deps)
    return "recovered"
