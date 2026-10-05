from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from uuid import uuid4

import httpx
import pytest
from fastapi import HTTPException, Request, Response
from sqlalchemy import inspect
from sqlalchemy.orm import Session

from tts_erp_v2.access import AccessGrant, AuthMode, Role
from tts_erp_v2.api.v2 import video_publish as api
from tts_erp_v2.db.models.publishing import (
    PublishWorkerHeartbeat,
    VideoPublishAttempt,
    VideoPublishTask,
)
from tts_erp_v2.publishing import artemis_client as artemis_module
from tts_erp_v2.publishing.adb_device import DeviceLocked
from tts_erp_v2.publishing.artemis_client import (
    ArtemisAdmissionRejected,
    ArtemisClient,
    ArtemisResult,
    ArtemisTransportError,
)
from tts_erp_v2.publishing.dispatcher import (
    PublishDependencies,
    _execute_cleanup,
    _run_attempt,
)
from tts_erp_v2.publishing.domain import (
    AttemptStatus,
    CleanupIntent,
    TaskStage,
    TaskStatus,
    allowed_actions,
    classify_failure,
)
from tts_erp_v2.publishing.object_store import VideoObjectStore
from tts_erp_v2.publishing.repository import (
    AdmissionRejected,
    AttemptObservation,
    CleanupLeaseToken,
    CleanupWork,
    LeaseLost,
    ObserveAttempt,
    PublishLeaseToken,
    commit_publish_transition,
    request_verification,
)
from tts_erp_v2.publishing.submission import confirm_upload, retry_task
from tts_erp_v2.publishing.worker import _probe_device_readiness
from tts_erp_v2.storage.minio_client import ObjectNotFound


def _factory(db_session: Session):
    def factory():
        return Session(
            bind=db_session.get_bind(), join_transaction_mode="create_savepoint"
        )

    return factory


def _task(
    *,
    status: str = TaskStatus.RUNNING.value,
    stage: str = TaskStage.VERIFYING.value,
) -> VideoPublishTask:
    return VideoPublishTask(
        public_id=uuid4(),
        client_request_id=uuid4(),
        caption="TEST caption",
        original_filename="TEST.mp4",
        content_type="video/mp4",
        size_bytes=4,
        object_bucket="tiktok-video",
        object_key=f"TEST/{uuid4()}.mp4",
        status=status,
        stage=stage,
        cleanup_intent=CleanupIntent.NONE.value,
        target_device_serial="TEST_device",
        target_app_package="com.tiktok",
        lease_owner="worker-a" if status == TaskStatus.RUNNING.value else None,
        lease_expires_at=(
            datetime.now(UTC) + timedelta(minutes=5)
            if status == TaskStatus.RUNNING.value
            else None
        ),
        queued_at=datetime.now(UTC),
    )


def _verify_task(db_session: Session) -> tuple[VideoPublishTask, VideoPublishAttempt]:
    task = _task()
    publish = VideoPublishAttempt(
        sequence_no=1,
        kind="publish",
        status=AttemptStatus.FAILED.value,
        artemis_session_id=uuid4(),
        prompt_version="TEST",
        prompt_snapshot="TEST",
        device_serial="TEST_device",
    )
    task.attempts.append(publish)
    db_session.add(task)
    db_session.flush()
    verify = VideoPublishAttempt(
        sequence_no=2,
        kind="verify",
        related_attempt_id=publish.id,
        status=AttemptStatus.RUNNING.value,
        artemis_session_id=uuid4(),
        prompt_version="TEST",
        prompt_snapshot="TEST",
        device_serial="TEST_device",
    )
    task.attempts.append(verify)
    task.attempt_count = 1
    cast(Any, task).publish_budget_used = 1
    db_session.flush()
    db_session.commit()
    return task, verify


def _request(*, request_id: str = "TEST-request-id") -> Request:
    grant = AccessGrant(
        mode=AuthMode.ENFORCE,
        role=Role.READWRITE,
        scopes=("page:video-publish",),
        auth_method="cookie",
        bypass=True,
    )
    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/v2/video-publish",
            "headers": [
                (b"x-requested-with", b"tts-erp"),
                (b"x-request-id", request_id.encode()),
            ],
            "auth_method": "cookie",
            "access_grant": grant,
        }
    )
    return request


def test_cancelled_or_rejected_execution_is_never_retry_safe() -> None:
    for status in ("cancelled", "canceled", "rejected"):
        result = classify_failure(artemis_status=status, steps_count=0)
        assert result.retry_safe is not True
        assert result.requires_verification is True


@pytest.mark.asyncio
@pytest.mark.parametrize("code", ["DEVICE_LOCKED", "DEVICE_BUSY"])
async def test_only_known_409_codes_are_typed_pre_admission_rejections(
    monkeypatch: pytest.MonkeyPatch, code: str
) -> None:
    class FakeAsyncClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def request(self, method, path, **_kwargs):
            request = httpx.Request(method, f"https://artemis.test{path}")
            return httpx.Response(409, request=request, json={"code": code})

    monkeypatch.setattr(
        artemis_module.httpx, "AsyncClient", lambda **_kwargs: FakeAsyncClient()
    )
    with pytest.raises(ArtemisAdmissionRejected, match=code):
        await ArtemisClient("https://artemis.test").get_task(uuid4())


@pytest.mark.asyncio
@pytest.mark.parametrize("payload", [{"code": "UNKNOWN"}, {}, None])
async def test_unknown_or_malformed_409_is_transport_ambiguity(
    monkeypatch: pytest.MonkeyPatch, payload: dict | None
) -> None:
    class FakeAsyncClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def request(self, method, path, **_kwargs):
            request = httpx.Request(method, f"https://artemis.test{path}")
            if payload is None:
                return httpx.Response(409, request=request, content=b"not-json")
            return httpx.Response(409, request=request, json=payload)

    monkeypatch.setattr(
        artemis_module.httpx, "AsyncClient", lambda **_kwargs: FakeAsyncClient()
    )
    with pytest.raises(ArtemisTransportError):
        await ArtemisClient("https://artemis.test").get_task(uuid4())


@pytest.mark.parametrize(
    ("status", "verdict", "expected_status"),
    [
        ("failed", "published", TaskStatus.NEEDS_REVIEW.value),
        ("cancelled", "not_published", TaskStatus.NEEDS_REVIEW.value),
        ("success", "PUBLISHED", TaskStatus.NEEDS_REVIEW.value),
        ("success", "unknown", TaskStatus.NEEDS_REVIEW.value),
        ("success", "published", TaskStatus.SUCCEEDED.value),
    ],
)
def test_verify_verdict_requires_success_and_strict_enum(
    db_session: Session, status: str, verdict: str, expected_status: str
) -> None:
    task, verify = _verify_task(db_session)
    outcome = commit_publish_transition(
        _factory(db_session),
        PublishLeaseToken(
            task.public_id,
            "worker-a",
            task.row_version,
            verify.id,
            AttemptStatus.RUNNING.value,
        ),
        ObserveAttempt(
            AttemptObservation(status=status, terminal=True, verdict=verdict),
            max_attempts=3,
        ),
    )
    assert outcome.task_status == expected_status
    db_session.expire_all()
    assert task.status == expected_status
    if expected_status == TaskStatus.NEEDS_REVIEW.value:
        assert task.stage == TaskStage.DONE.value
        assert task.cleanup_intent == CleanupIntent.NONE.value


def test_rejected_verify_stays_needs_review_and_never_queues_publish(
    db_session: Session,
) -> None:
    task, verify = _verify_task(db_session)
    outcome = commit_publish_transition(
        _factory(db_session),
        PublishLeaseToken(
            task.public_id,
            "worker-a",
            task.row_version,
            verify.id,
            AttemptStatus.RUNNING.value,
        ),
        AdmissionRejected("DEVICE_BUSY", max_attempts=3),
    )
    assert (outcome.task_status, outcome.task_stage) == (
        TaskStatus.NEEDS_REVIEW.value,
        TaskStage.DONE.value,
    )
    db_session.expire_all()
    assert verify.status == AttemptStatus.REJECTED.value
    assert task.attempt_count == 1
    assert cast(Any, task).publish_budget_used == 1


def test_rejected_automatic_verify_preserves_registered_cleanup(
    db_session: Session,
) -> None:
    task, verify = _verify_task(db_session)
    task.device_path = "/sdcard/Movies/TTSERP/tts_erp_TEST.mp4"
    task.device_cleanup_status = "pending"
    task.spool_cleanup_status = "pending"
    db_session.commit()
    outcome = commit_publish_transition(
        _factory(db_session),
        PublishLeaseToken(
            task.public_id,
            "worker-a",
            task.row_version,
            verify.id,
            AttemptStatus.RUNNING.value,
        ),
        AdmissionRejected("DEVICE_LOCKED", max_attempts=3),
    )
    assert outcome.task_status == TaskStatus.NEEDS_REVIEW.value
    db_session.expire_all()
    assert task.cleanup_intent == CleanupIntent.PRESERVE_STATE.value
    assert task.device_cleanup_status == "pending"
    assert task.spool_cleanup_status == "pending"


def test_publish_admission_refunds_budget_without_rewriting_audit_count(
    db_session: Session,
) -> None:
    task = _task(stage=TaskStage.DISPATCHING_ARTEMIS.value)
    publish = VideoPublishAttempt(
        sequence_no=1,
        kind="publish",
        status=AttemptStatus.SUBMITTING.value,
        artemis_session_id=uuid4(),
        prompt_version="TEST",
        prompt_snapshot="TEST",
        device_serial="TEST_device",
    )
    task.attempts.append(publish)
    task.attempt_count = 1
    cast(Any, task).publish_budget_used = 1
    db_session.add(task)
    db_session.flush()
    db_session.commit()
    commit_publish_transition(
        _factory(db_session),
        PublishLeaseToken(
            task.public_id,
            "worker-a",
            task.row_version,
            publish.id,
            AttemptStatus.SUBMITTING.value,
        ),
        AdmissionRejected("DEVICE_LOCKED", max_attempts=3),
    )
    db_session.expire_all()
    assert task.attempt_count == 1
    assert cast(Any, task).publish_budget_used == 0


def test_retention_cleanup_suppresses_and_rejects_retry(db_session: Session) -> None:
    task = _task(status=TaskStatus.FAILED.value, stage=TaskStage.DONE.value)
    task.lease_owner = None
    task.lease_expires_at = None
    task.object_uploaded_at = datetime.now(UTC)
    task.object_cleanup_status = "pending"
    task.object_cleanup_next_attempt_at = datetime.now(UTC)
    task.cleanup_intent = CleanupIntent.PRESERVE_STATE.value
    task.attempt_count = 1
    cast(Any, task).publish_budget_used = 1
    task.attempts.append(
        VideoPublishAttempt(
            sequence_no=1,
            kind="publish",
            status=AttemptStatus.FAILED.value,
            retry_safe=True,
            artemis_session_id=uuid4(),
            prompt_version="TEST",
            prompt_snapshot="TEST",
            device_serial="TEST_device",
        )
    )
    db_session.add(task)
    db_session.flush()
    assert "retry" not in {action.value for action in allowed_actions(task)}
    with pytest.raises(ValueError, match="OBJECT_CLEANUP_IN_PROGRESS"):
        retry_task(db_session, task.public_id)


def test_confirm_distinguishes_missing_transport_mime_and_size(
    db_session: Session,
) -> None:
    class Store:
        def __init__(self, result=None, error: Exception | None = None) -> None:
            self.result = result
            self.error = error

        def stat(self, _key):
            if self.error:
                raise self.error
            return self.result

    cases = [
        (Store(error=ObjectNotFound("missing")), "UPLOAD_NOT_FOUND"),
        (Store(error=RuntimeError("transport")), "OBJECT_STORE_UNAVAILABLE"),
        (Store({"size": 4}), "UPLOAD_MIME_MISSING"),
        (Store({"size": 5, "content_type": "video/mp4"}), "UPLOAD_SIZE_MISMATCH"),
        (Store({"size": 4, "content_type": "text/plain"}), "UPLOAD_MIME_MISMATCH"),
    ]
    for store, code in cases:
        task = _task(
            status=TaskStatus.PENDING.value, stage=TaskStage.AWAITING_UPLOAD.value
        )
        task.lease_owner = None
        task.lease_expires_at = None
        db_session.add(task)
        db_session.flush()
        with pytest.raises(ValueError, match=code):
            confirm_upload(
                db_session,
                task.public_id,
                cast(VideoObjectStore, store),
                expected_version=task.row_version,
            )
        db_session.rollback()


@pytest.mark.parametrize(
    ("metadata", "error", "expected_status", "expected_code", "retryable"),
    [
        (
            None,
            RuntimeError("service unavailable"),
            503,
            "OBJECT_STORE_UNAVAILABLE",
            True,
        ),
        (None, ObjectNotFound("missing"), 422, "UPLOAD_NOT_FOUND", True),
        ({"size": 4}, None, 422, "UPLOAD_MIME_MISSING", True),
        (
            {"size": 4, "content_type": "text/plain"},
            None,
            422,
            "UPLOAD_MIME_MISMATCH",
            True,
        ),
    ],
)
def test_confirm_api_exposes_structured_storage_errors(
    db_session: Session,
    metadata,
    error: Exception | None,
    expected_status: int,
    expected_code: str,
    retryable: bool,
) -> None:
    class Store:
        def stat(self, _key):
            if error:
                raise error
            return metadata

    task = _task(status=TaskStatus.PENDING.value, stage=TaskStage.AWAITING_UPLOAD.value)
    task.lease_owner = None
    task.lease_expires_at = None
    db_session.add(task)
    db_session.flush()
    with pytest.raises(HTTPException) as exc_info:
        api.confirm(
            task.public_id,
            api.ActionIn(rowVersion=task.row_version),
            _request(),
            db_session,
            cast(VideoObjectStore, Store()),
        )
    assert exc_info.value.status_code == expected_status
    detail = cast(dict, exc_info.value.detail)
    assert detail["code"] == expected_code
    assert detail["retryable"] is retryable
    assert detail["requestId"] == "TEST-request-id"


def test_stateful_error_envelope_has_request_and_retry_contract(
    db_session: Session,
) -> None:
    task = _task(status=TaskStatus.PENDING.value, stage=TaskStage.QUEUED.value)
    task.lease_owner = None
    task.lease_expires_at = None
    db_session.add(task)
    db_session.flush()
    with pytest.raises(HTTPException) as exc_info:
        api.refresh_upload_url(
            task.public_id,
            _request(),
            db_session,
            cast(VideoObjectStore, SimpleNamespace()),
        )
    assert exc_info.value.status_code == 409
    detail = cast(dict, exc_info.value.detail)
    assert detail == {
        "code": "TASK_ACTION_NOT_ALLOWED",
        "message": "TASK_ACTION_NOT_ALLOWED",
        "retryable": False,
        "requestId": "TEST-request-id",
        "rowVersion": task.row_version,
        "allowedActions": ["view", "cancel"],
    }


def test_user_verification_is_immediately_claimable(db_session: Session) -> None:
    task = _task(status=TaskStatus.NEEDS_REVIEW.value, stage=TaskStage.DONE.value)
    task.lease_owner = None
    task.lease_expires_at = None
    task.attempts.append(
        VideoPublishAttempt(
            sequence_no=1,
            kind="publish",
            status=AttemptStatus.FAILED.value,
            artemis_session_id=uuid4(),
            prompt_version="TEST",
            prompt_snapshot="TEST",
            device_serial="TEST_device",
        )
    )
    db_session.add(task)
    db_session.flush()
    request_verification(db_session, task.public_id)
    db_session.commit()
    db_session.expire_all()
    assert task.lease_owner is None
    assert task.lease_expires_at is None
    assert task.status == TaskStatus.RUNNING.value
    assert task.stage == TaskStage.VERIFYING.value


@pytest.mark.asyncio
async def test_slow_cleanup_renews_lease_until_operation_finishes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    renewals = 0
    finished = False

    def renew(_factory, token, lease_seconds):
        nonlocal renewals
        renewals += 1
        return CleanupLeaseToken(
            token.task_id,
            token.owner,
            token.row_version + 1,
            datetime.now(UTC) + timedelta(seconds=lease_seconds),
        )

    def finish(_factory, _token, results):
        nonlocal finished
        finished = True
        assert results == {"device": None}

    class Adb:
        async def remove_staged_video(self, _serial, _path):
            await asyncio.sleep(0.2)

    monkeypatch.setattr("tts_erp_v2.publishing.dispatcher.renew_cleanup_work", renew)
    monkeypatch.setattr("tts_erp_v2.publishing.dispatcher.finish_cleanup_work", finish)
    monkeypatch.setattr(
        "tts_erp_v2.publishing.dispatcher.require_destructive_script_guard",
        lambda **_kwargs: None,
    )
    task_id = uuid4()
    work = CleanupWork(
        task_id,
        CleanupLeaseToken(
            task_id,
            "worker-a",
            1,
            datetime.now(UTC) + timedelta(seconds=1),
        ),
        ("device",),
        "TEST_device",
        "/sdcard/Movies/TTSERP/tts_erp_TEST.mp4",
        None,
        "TEST/object.mp4",
    )
    deps = cast(
        PublishDependencies,
        SimpleNamespace(
            session_factory=lambda: None,
            lease_seconds=0.15,
            adb=Adb(),
            spool_dir=tmp_path,
            store=SimpleNamespace(),
        ),
    )
    await _execute_cleanup(work, deps)
    assert renewals >= 2
    assert finished is True


def test_list_cursor_is_opaque_created_at_id_keyset(db_session: Session) -> None:
    created = datetime(2026, 10, 5, tzinfo=UTC)
    first = _task(status=TaskStatus.FAILED.value, stage=TaskStage.DONE.value)
    second = _task(status=TaskStatus.FAILED.value, stage=TaskStage.DONE.value)
    for task in (first, second):
        task.lease_owner = None
        task.lease_expires_at = None
        task.created_at = created
    db_session.add_all([first, second])
    db_session.flush()
    page_one = cast(
        dict,
        api.list_tasks(
            _request(), db_session, Response(), status_filter=None, limit=1, cursor=None
        ),
    )
    assert isinstance(page_one["nextCursor"], str)
    assert not page_one["nextCursor"].isdigit()
    page_two = cast(
        dict,
        api.list_tasks(
            _request(),
            db_session,
            Response(),
            status_filter=None,
            limit=1,
            cursor=cast(Any, page_one["nextCursor"]),
        ),
    )
    assert page_two["items"][0]["taskId"] != page_one["items"][0]["taskId"]


def test_device_config_uses_server_probe_state(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ARTEMIS_DEVICE_SERIAL", "TEST_device")
    now = datetime.now(UTC)
    heartbeat = PublishWorkerHeartbeat(
        instance_id="TEST-worker",
        hostname="TEST-host",
        pid=1,
        status="ready",
        started_at=now,
        heartbeat_at=now,
    )
    cast(Any, heartbeat).device_status = "locked"
    cast(Any, heartbeat).device_message = "Device is locked"
    db_session.add(heartbeat)
    db_session.flush()
    payload = api.config(_request(), db_session)
    assert payload["device"] == {
        "status": "locked",
        "message": "Device is locked",
    }
    assert payload["canWrite"] is True
    assert payload["writeBlockReason"] is None


@pytest.mark.asyncio
async def test_worker_heartbeat_probe_reports_locked_device_without_raw_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ARTEMIS_DEVICE_SERIAL", "TEST_device")

    class Adb:
        async def check_device(self, _serial):
            raise DeviceLocked("Bearer TEST_SECRET")

    class NoCleanupSession:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def scalar(self, _query):
            return False

    status_value, message = await _probe_device_readiness(
        cast(
            PublishDependencies,
            SimpleNamespace(adb=Adb(), session_factory=NoCleanupSession),
        )
    )
    assert status_value == "locked"
    assert message == "设备在线但仍处于锁屏状态"
    assert "TEST_SECRET" not in message


def test_related_verify_attempt_must_reference_publish_in_same_task(
    db_session: Session,
) -> None:
    first = _task(status=TaskStatus.FAILED.value, stage=TaskStage.DONE.value)
    second = _task(status=TaskStatus.FAILED.value, stage=TaskStage.DONE.value)
    for task in (first, second):
        task.lease_owner = None
        task.lease_expires_at = None
    publish = VideoPublishAttempt(
        sequence_no=1,
        kind="publish",
        status="failed",
        artemis_session_id=uuid4(),
        prompt_version="TEST",
        prompt_snapshot="TEST",
        device_serial="TEST_device",
    )
    first.attempts.append(publish)
    db_session.add_all([first, second])
    db_session.flush()
    second.attempts.append(
        VideoPublishAttempt(
            sequence_no=1,
            kind="verify",
            related_attempt_id=publish.id,
            status="created",
            artemis_session_id=uuid4(),
            prompt_version="TEST",
            prompt_snapshot="TEST",
            device_serial="TEST_device",
        )
    )
    with pytest.raises(Exception, match="related attempt"):
        db_session.flush()


def test_model_metadata_declares_all_publishing_checks_and_desc_index() -> None:
    task_checks = {
        constraint.name
        for constraint in cast(Any, VideoPublishTask.__table__).constraints
        if constraint.__class__.__name__ == "CheckConstraint"
    }
    assert {
        "video_publish_task_status_check",
        "video_publish_task_stage_check",
        "video_publish_task_size_check",
        "video_publish_task_cleanup_intent_check",
        "video_publish_task_status_stage_check",
        "video_publish_task_cleanup_owner_check",
        "video_publish_task_cleanup_status_check",
        "video_publish_task_budget_check",
    } <= task_checks
    attempt_checks = {
        constraint.name
        for constraint in cast(Any, VideoPublishAttempt.__table__).constraints
        if constraint.__class__.__name__ == "CheckConstraint"
    }
    assert {
        "video_publish_attempt_kind_check",
        "video_publish_attempt_status_check",
        "video_publish_attempt_related_check",
    } <= attempt_checks
    heartbeat_checks = {
        constraint.name
        for constraint in cast(Any, PublishWorkerHeartbeat.__table__).constraints
        if constraint.__class__.__name__ == "CheckConstraint"
    }
    assert {
        "worker_heartbeat_status_check",
        "worker_heartbeat_device_status_check",
    } <= heartbeat_checks
    index = next(
        item
        for item in cast(Any, VideoPublishAttempt.__table__).indexes
        if item.name == "ix_video_publish_attempt_task_seq"
    )
    assert str(list(index.expressions)[1]).endswith(" DESC")


@pytest.mark.asyncio
async def test_recovery_resubmit_transport_ambiguity_preserves_same_attempt(
    db_session: Session, tmp_path: Path
) -> None:
    class Artemis:
        async def get_task(self, session_id):
            return ArtemisResult(session_id, "not_found")

        async def submit(self, **_kwargs):
            raise ArtemisTransportError("TEST timeout after possible admission")

    task = _task(stage=TaskStage.DISPATCHING_ARTEMIS.value)
    attempt = VideoPublishAttempt(
        sequence_no=1,
        kind="publish",
        status=AttemptStatus.SUBMITTING.value,
        artemis_session_id=uuid4(),
        prompt_version="TEST",
        prompt_snapshot="TEST",
        device_serial="TEST_device",
        submitted_at=None,
    )
    task.attempts.append(attempt)
    task.attempt_count = 1
    task.publish_budget_used = 1
    db_session.add(task)
    db_session.flush()
    db_session.commit()
    deps = cast(
        PublishDependencies,
        SimpleNamespace(
            session_factory=_factory(db_session),
            instance_id="worker-a",
            lease_seconds=30,
            max_attempts=3,
            artemis=Artemis(),
            spool_dir=tmp_path,
        ),
    )
    await _run_attempt(task.public_id, attempt.id, deps)
    db_session.expire_all()
    assert task.status == TaskStatus.RUNNING.value
    assert task.stage == TaskStage.DISPATCHING_ARTEMIS.value
    assert task.attempt_count == 1
    assert task.publish_budget_used == 1
    assert attempt.status == AttemptStatus.SUBMITTING.value
    assert attempt.artemis_session_id is not None


@pytest.mark.asyncio
async def test_cleanup_heartbeat_owner_loss_stops_finalization(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    renewals = 0
    finalized = False

    def renew(_factory, token, lease_seconds):
        nonlocal renewals
        renewals += 1
        if renewals > 1:
            raise LeaseLost(token.task_id)
        return CleanupLeaseToken(
            token.task_id,
            token.owner,
            token.row_version + 1,
            datetime.now(UTC) + timedelta(seconds=lease_seconds),
        )

    def finish(*_args):
        nonlocal finalized
        finalized = True

    class Adb:
        async def remove_staged_video(self, _serial, _path):
            await asyncio.sleep(0.2)

    monkeypatch.setattr("tts_erp_v2.publishing.dispatcher.renew_cleanup_work", renew)
    monkeypatch.setattr("tts_erp_v2.publishing.dispatcher.finish_cleanup_work", finish)
    monkeypatch.setattr(
        "tts_erp_v2.publishing.dispatcher.require_destructive_script_guard",
        lambda **_kwargs: None,
    )
    task_id = uuid4()
    work = CleanupWork(
        task_id,
        CleanupLeaseToken(
            task_id,
            "worker-a",
            1,
            datetime.now(UTC) + timedelta(seconds=1),
        ),
        ("device",),
        "TEST_device",
        "/sdcard/Movies/TTSERP/tts_erp_TEST.mp4",
        None,
        "TEST/object.mp4",
    )
    deps = cast(
        PublishDependencies,
        SimpleNamespace(
            session_factory=lambda: None,
            lease_seconds=0.15,
            adb=Adb(),
            spool_dir=tmp_path,
            store=SimpleNamespace(),
        ),
    )
    await _execute_cleanup(work, deps)
    assert renewals >= 2
    assert finalized is False


def test_api_defensively_sanitizes_legacy_task_attempt_and_cleanup_errors(
    db_session: Session,
) -> None:
    sensitive_value = f"Bearer TEST_{uuid4().hex}"
    diagnostic = f"{sensitive_value} https://example.test/path?token={uuid4().hex}"
    task = _task(status=TaskStatus.FAILED.value, stage=TaskStage.DONE.value)
    task.lease_owner = None
    task.lease_expires_at = None
    task.last_error_code = diagnostic
    task.last_error_message = diagnostic * 200
    task.cleanup_intent = CleanupIntent.PRESERVE_STATE.value
    task.device_cleanup_status = "failed"
    task.device_cleanup_error = diagnostic
    task.attempts.append(
        VideoPublishAttempt(
            sequence_no=1,
            kind="publish",
            status=AttemptStatus.FAILED.value,
            artemis_session_id=uuid4(),
            prompt_version="TEST",
            prompt_snapshot="TEST",
            device_serial="TEST_device",
            artemis_error=diagnostic,
        )
    )
    db_session.add(task)
    db_session.flush()
    payload = cast(
        dict,
        api.detail(
            task.public_id,
            _request(),
            db_session,
            Response(),
            include_diagnostics=False,
        ),
    )
    encoded = str(payload)
    assert sensitive_value not in encoded
    assert "[REDACTED]" in encoded
    assert len(payload["lastErrorMessage"]) <= 2000
    assert len(payload["cleanup"]["device"]["error"]) <= 2000


def test_summary_queries_do_not_eager_load_attempt_history() -> None:
    source = Path("tts_erp_v2/api/v2/video_publish.py").read_text()
    current_section = source[
        source.index("def current(") : source.index(
            '@router.get("/tasks",', source.index("def current(")
        )
    ]
    list_section = source[
        source.index("def list_tasks(") : source.index(
            '@router.get("/tasks/{task_id}"', source.index("def list_tasks(")
        )
    ]
    assert "selectinload(VideoPublishTask.attempts)" not in current_section
    assert "selectinload(VideoPublishTask.attempts)" not in list_section
    assert "_attempt_summaries" in current_section
    assert "_attempt_summaries" in list_section


def test_live_schema_matches_publishing_model_constraints_and_indexes(
    db_session: Session,
) -> None:
    inspector = inspect(db_session.get_bind())
    tables = (
        (VideoPublishTask, "video_publish_tasks"),
        (VideoPublishAttempt, "video_publish_attempts"),
        (PublishWorkerHeartbeat, "worker_heartbeats"),
    )
    for model, table_name in tables:
        model_checks = {
            constraint.name
            for constraint in cast(Any, model.__table__).constraints
            if constraint.__class__.__name__ == "CheckConstraint"
        }
        live_checks = {
            row["name"]
            for row in inspector.get_check_constraints(table_name, schema="publishing")
        }
        assert model_checks <= live_checks
    attempt_indexes = {
        row["name"]: row
        for row in inspector.get_indexes("video_publish_attempts", schema="publishing")
    }
    assert attempt_indexes["ix_video_publish_attempt_task_seq"].get(
        "column_sorting", {}
    ).get("sequence_no") == ("desc",)


def test_browser_contract_has_codepoint_count_dialog_and_load_more() -> None:
    source = Path("tts_erp_v2/static/js/video-publish.js").read_text()
    template = Path("tts_erp_v2/templates/pages/video-publish.html").read_text()
    assert "Array.from(value).length" in source
    assert ".maxLength =" not in source
    assert "window.confirm" not in source
    assert 'id="publish-action-dialog"' in template
    assert 'id="publish-load-more"' in template
    assert 'role="status" aria-live="polite"' in template
    assert "resumeTask.filename" in source
    assert "resumeTask.sizeBytes" in source
    assert "error.allowedActions" in source
