"""Resilient single-task dispatcher."""

from __future__ import annotations

import asyncio
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

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
    claim_one,
    create_attempt,
    release_lease,
    touch_task,
)


@dataclass(slots=True)
class PublishDependencies:
    session_factory: sessionmaker[Session]
    store: VideoObjectStore
    adb: AdbDevice
    artemis: ArtemisClient
    spool_dir: Path
    instance_id: str
    max_attempts: int = 3


async def dispatch_one(deps: PublishDependencies) -> str:
    with deps.session_factory() as session:
        active = session.scalar(
            select(VideoPublishTask)
            .where(VideoPublishTask.status == TaskStatus.RUNNING.value)
            .order_by(VideoPublishTask.id)
            .limit(1)
        )
        if active is not None:
            task_id = active.public_id
            session.commit()
        else:
            task = claim_one(session, deps.instance_id)
            if task is None:
                session.commit()
                return "no_task"
            task_id = task.public_id
            session.commit()
    await _execute(task_id, deps)
    return "processed"


async def _execute(task_id: UUID, deps: PublishDependencies) -> None:
    local = deps.spool_dir / str(task_id) / "video.mp4"
    try:
        with deps.session_factory() as session:
            task = _get(session, task_id)
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
                        }
                    ),
                    None,
                )
                if active_attempt is not None:
                    attempt_id = active_attempt.id
                    session.commit()
                    await _run_attempt(task_id, attempt_id, deps)
                    return
            touch_task(session, task, stage=TaskStage.DOWNLOADING.value)
            session.commit()
        digest = deps.store.download(task.object_key, local)
        if local.stat().st_size != task.size_bytes:
            raise RuntimeError("DOWNLOAD_SIZE_MISMATCH")
        with deps.session_factory() as session:
            task = _get(session, task_id)
            task.object_sha256 = digest
            touch_task(session, task, stage=TaskStage.STAGING_DEVICE.value)
            session.commit()
        await deps.adb.check_device(task.target_device_serial)
        await deps.adb.check_package(task.target_device_serial, task.target_app_package)
        device_path = f"/sdcard/Movies/TTSERP/tts_erp_{task_id}.mp4"
        await deps.adb.stage_video(task.target_device_serial, local, device_path)
        await deps.adb.verify_media_visible(task.target_device_serial, device_path)
        with deps.session_factory() as session:
            task = _get(session, task_id)
            task.device_path = device_path
            task.device_cleanup_status = "pending"
            touch_task(session, task, stage=TaskStage.DISPATCHING_ARTEMIS.value)
            attempt = create_attempt(session, task, kind=AttemptKind.PUBLISH)
            session.commit()
            attempt_id = attempt.id
        await _run_attempt(task_id, attempt_id, deps)
    except (DeviceUnavailable, RuntimeError) as exc:
        await _safe_retry(task_id, str(exc), deps)
    finally:
        local.unlink(missing_ok=True)
        with suppress(OSError):
            local.parent.rmdir()


async def _run_attempt(
    task_id: UUID, attempt_id: int, deps: PublishDependencies
) -> None:
    with deps.session_factory() as session:
        task = _get(session, task_id)
        attempt = _require_attempt(session, attempt_id)
        attempt.status = AttemptStatus.SUBMITTING.value
        task.stage = TaskStage.DISPATCHING_ARTEMIS.value
        session.commit()
    try:
        result = await deps.artemis.submit(
            goal=attempt.prompt_snapshot,
            session_id=attempt.artemis_session_id,
            device_serial=attempt.device_serial,
            app_package=task.target_app_package,
        )
        with deps.session_factory() as session:
            attempt = _require_attempt(session, attempt_id)
            task = _get(session, task_id)
            attempt.status = (
                result.status
                if result.status in {"queued", "running"}
                else AttemptStatus.SUBMITTING.value
            )
            attempt.submitted_at = __import__("datetime").datetime.now(
                __import__("datetime").UTC
            )
            task.stage = TaskStage.WAITING_ARTEMIS.value
            session.commit()
    except ArtemisTransportError:
        # Query admission first; any resubmission uses the persisted ID.
        with deps.session_factory() as session:
            attempt = _require_attempt(session, attempt_id)
            task = _get(session, task_id)
            session_id = attempt.artemis_session_id
            prompt = attempt.prompt_snapshot
            device_serial = attempt.device_serial
            app_package = task.target_app_package
            attempt.submit_retry_count += 1
            session.commit()
        try:
            result = await deps.artemis.get_task(session_id)
        except ArtemisTransportError:
            result = await deps.artemis.submit(
                goal=prompt,
                session_id=session_id,
                device_serial=device_serial,
                app_package=app_package,
            )
        if result.status in {"missing", "not_found"}:
            result = await deps.artemis.submit(
                goal=prompt,
                session_id=session_id,
                device_serial=device_serial,
                app_package=app_package,
            )
    if not result.terminal:
        await asyncio.sleep(0)
        return
    with deps.session_factory() as session:
        attempt = _require_attempt(session, attempt_id)
        task = _get(session, task_id)
        if attempt.kind == AttemptKind.VERIFY.value:
            verdict = (
                (result.output or {}).get("verdict")
                if result.output
                else "inconclusive"
            )
            _save_result(attempt, result)
            if verdict == "published":
                task.status = TaskStatus.SUCCEEDED.value
                task.stage = TaskStage.DONE.value
            elif verdict == "not_published":
                task.status = TaskStatus.PENDING.value
                task.stage = TaskStage.QUEUED.value
            else:
                task.status = TaskStatus.NEEDS_REVIEW.value
                task.stage = TaskStage.DONE.value
            release_lease(task)
            session.commit()
        elif result.status == "success":
            _save_result(attempt, result)
            task.stage = TaskStage.CLEANING.value
            task.device_cleanup_status = "pending"
            task.spool_cleanup_status = "pending"
            task.object_cleanup_status = "pending"
            session.commit()
            await _cleanup_success(task_id, deps)
        else:
            classification = classify_failure(
                artemis_status=result.status,
                steps_count=result.steps_count,
                final_publish_observed=result.final_publish_observed,
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


def _get(session: Session, task_id: UUID) -> VideoPublishTask:
    task = session.scalar(
        select(VideoPublishTask).where(VideoPublishTask.public_id == task_id)
    )
    if task is None:
        raise LookupError(task_id)
    return task


async def _safe_retry(task_id: UUID, error: str, deps: PublishDependencies) -> None:
    with deps.session_factory() as session:
        task = _get(session, task_id)
        task.last_error_code = "PRE_ARTEMIS_FAILURE"
        task.last_error_message = error[:500]
        task.status = TaskStatus.PENDING.value
        task.stage = TaskStage.QUEUED.value
        task.queued_at = __import__("datetime").datetime.now(__import__("datetime").UTC)
        release_lease(task)
        task.row_version += 1
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
        session.commit()
    device_error = None
    if device_path:
        try:
            await deps.adb.remove_staged_video(serial, device_path)
        except Exception as exc:  # noqa: BLE001 - persisted as cleanup status
            device_error = str(exc)[:500]
    try:
        deps.store.remove(object_key)
        object_error = None
    except Exception as exc:  # noqa: BLE001 - persisted as cleanup status
        object_error = str(exc)[:500]
    with deps.session_factory() as session:
        task = _get(session, task_id)
        task.device_cleanup_status = "failed" if device_error else "succeeded"
        task.device_cleanup_error = device_error
        task.spool_cleanup_status = "succeeded"
        task.spool_cleanup_error = None
        task.object_cleanup_status = "failed" if object_error else "succeeded"
        task.object_cleanup_error = object_error
        if not object_error:
            task.object_deleted_at = __import__("datetime").datetime.now(
                __import__("datetime").UTC
            )
        task.status = TaskStatus.SUCCEEDED.value
        task.stage = TaskStage.DONE.value
        task.completed_at = __import__("datetime").datetime.now(
            __import__("datetime").UTC
        )
        release_lease(task)
        session.commit()


async def _mark_failed(task_id: UUID, code: str, deps: PublishDependencies) -> None:
    with deps.session_factory() as session:
        task = _get(session, task_id)
        task.status = TaskStatus.FAILED.value
        task.stage = TaskStage.DONE.value
        task.last_error_code = code
        release_lease(task)
        session.commit()


async def _start_verify(
    task_id: UUID, related_id: int, deps: PublishDependencies
) -> None:
    with deps.session_factory() as session:
        task = _get(session, task_id)
        task.stage = TaskStage.VERIFYING.value
        attempt = _require_attempt(session, related_id)
        verify = create_attempt(session, task, kind=AttemptKind.VERIFY, related=attempt)
        session.commit()
    try:
        result = await deps.artemis.submit(
            goal=verify.prompt_snapshot,
            session_id=verify.artemis_session_id,
            device_serial=verify.device_serial,
            app_package=task.target_app_package,
        )
    except ArtemisTransportError:
        return
    with deps.session_factory() as session:
        task = _get(session, task_id)
        verify = _require_attempt(session, verify.id)
        verdict = (
            (result.output or {}).get("verdict") if result.output else "inconclusive"
        )
        verify.status = (
            AttemptStatus.SUCCESS.value
            if verdict in {"published", "not_published"}
            else AttemptStatus.UNKNOWN.value
        )
        if verdict == "published":
            task.status = TaskStatus.SUCCEEDED.value
            task.stage = TaskStage.DONE.value
        elif verdict == "not_published":
            task.status = TaskStatus.PENDING.value
            task.stage = TaskStage.QUEUED.value
        else:
            task.status = TaskStatus.NEEDS_REVIEW.value
            task.stage = TaskStage.DONE.value
        release_lease(task)
        session.commit()


async def recover_active(deps: PublishDependencies) -> str:
    with deps.session_factory() as session:
        task = session.scalar(
            select(VideoPublishTask)
            .where(VideoPublishTask.status == TaskStatus.RUNNING.value)
            .limit(1)
        )
        if task is None:
            return "no_active_task"
        task_id = task.public_id
    await _execute(task_id, deps)
    return "recovered"
