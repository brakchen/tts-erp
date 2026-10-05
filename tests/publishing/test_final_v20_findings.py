from __future__ import annotations

import asyncio
import json
import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from uuid import uuid4

import pytest
from fastapi import Request
from sqlalchemy import inspect, select
from sqlalchemy.orm import Session

from tts_erp_v2.access import AccessGrant, AuthMode, Role
from tts_erp_v2.api.v2 import video_publish as api
from tts_erp_v2.db.models.publishing import VideoPublishAttempt, VideoPublishTask
from tts_erp_v2.publishing.adb_device import (
    AdbDevice,
    DeviceUnavailable,
    ManagedAlbumNotEmpty,
)
from tts_erp_v2.publishing.dispatcher import (
    PublishDependencies,
    dispatch_one,
    recover_active,
)
from tts_erp_v2.publishing.repository import (
    AdvanceExecution,
    AttemptObservation,
    ObserveAttempt,
    PrepareAttempt,
    PublishLeaseToken,
    commit_publish_transition,
)
from tts_erp_v2.storage.minio_client import ObjectNotFound

ROOT = Path(__file__).parents[2]


def _request() -> Request:
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/v2/video-publish/metrics",
            "headers": [],
            "access_grant": AccessGrant(
                mode=AuthMode.ENFORCE,
                role=Role.ADMIN,
                scopes=("page:video-publish",),
                auth_method="cookie",
                bypass=False,
            ),
            "auth_method": "cookie",
            "user_id": 2020,
        }
    )


def _task(*, status: str = "pending", stage: str = "queued") -> VideoPublishTask:
    return VideoPublishTask(
        public_id=uuid4(),
        client_request_id=uuid4(),
        created_by_user_id=2020,
        caption="TEST_v20_caption",
        original_filename="TEST_v20.mp4",
        object_filename="TEST_v20.mp4",
        content_type="video/mp4",
        size_bytes=4,
        object_bucket="tiktok-video",
        object_key=f"TEST/v20/{uuid4()}.mp4",
        object_etag="TEST-v20-etag",
        object_uploaded_at=datetime.now(UTC),
        status=status,
        stage=stage,
        cleanup_intent="none",
        target_device_serial="T20SERIAL",
        target_app_package="com.tiktok",
        queued_at=datetime.now(UTC) if status == "pending" else None,
        stage_started_at=datetime.now(UTC),
    )


@pytest.mark.asyncio
async def test_spool_is_owned_before_download_rename_and_object_loss_recovery(
    db_session: Session, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    task = _task()
    db_session.add(task)
    db_session.commit()
    task_id = task.public_id

    async def inline_to_thread(function, /, *args, **kwargs):
        return function(*args, **kwargs)

    monkeypatch.setattr(
        "tts_erp_v2.publishing.dispatcher.asyncio.to_thread", inline_to_thread
    )

    class Store:
        calls = 0

        def download(self, _key, destination, _expected_etag):
            self.calls += 1
            with Session(
                bind=db_session.get_bind(), join_transaction_mode="create_savepoint"
            ) as observation_session:
                observed = observation_session.scalar(
                    select(VideoPublishTask).where(
                        VideoPublishTask.public_id == task_id
                    )
                )
                assert observed is not None
                assert observed.spool_cleanup_status == "pending"
                assert getattr(observed, "spool_path", None) == str(destination)
            if self.calls == 1:
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(b"TEST")
                raise asyncio.CancelledError
            raise ObjectNotFound("TEST object lost after crash")

        def remove(self, _key, _expected_etag=None):
            return None

    class Adb:
        def device_path(self, _task_id):
            raise AssertionError("device staging must not begin")

    deps = cast(
        PublishDependencies,
        SimpleNamespace(
            session_factory=lambda: Session(
                bind=db_session.get_bind(), join_transaction_mode="create_savepoint"
            ),
            instance_id="TEST-v20-worker",
            lease_seconds=30,
            max_attempts=3,
            store=Store(),
            adb=Adb(),
            spool_dir=tmp_path,
        ),
    )

    with pytest.raises(asyncio.CancelledError):
        await dispatch_one(deps)
    db_session.expire_all()
    destination = tmp_path / str(task_id) / "video.mp4"
    assert destination.exists()
    assert task.status == "running"
    assert task.spool_cleanup_status == "pending"
    task.lease_expires_at = datetime.now(UTC) - timedelta(seconds=1)
    db_session.commit()

    assert await recover_active(deps) == "recovered"
    db_session.expire_all()
    assert task.last_error_code == "CONFIRMED_OBJECT_MISSING"
    assert task.spool_cleanup_status == "pending"
    assert task.cleanup_intent == "preserve_state"

    assert await dispatch_one(deps) == "processed"
    db_session.expire_all()
    assert not destination.exists()
    assert task.spool_cleanup_status == "succeeded"
    assert task.cleanup_intent == "none"


@pytest.mark.asyncio
async def test_adb_cleanup_deletes_and_polls_the_exact_media_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    device = AdbDevice()
    path = device.device_path("12345678-1234-1234-1234-123456789abc")
    calls: list[tuple[str, ...]] = []
    query_results = iter([f"Row: 0 _data={path}", ""])

    async def fake_run(*args: str, timeout: float = 30) -> str:
        del timeout
        calls.append(args)
        if "query" in args:
            return next(query_results)
        return ""

    async def no_sleep(_delay: float) -> None:
        return None

    monkeypatch.setattr(device, "_run", fake_run)
    monkeypatch.setattr("tts_erp_v2.publishing.adb_device.asyncio.sleep", no_sleep)

    await device.remove_staged_video("TEST_SERIAL", path)

    assert any("delete" in call for call in calls)
    assert sum("query" in call for call in calls) == 2
    for call in calls:
        assert "*" not in call
        if "rm" in call or "delete" in call or "query" in call:
            assert any(path in argument for argument in call)


@pytest.mark.asyncio
async def test_adb_preflight_detects_mediastore_only_managed_residue(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    device = AdbDevice()
    calls: list[tuple[str, ...]] = []

    async def fake_run(*args: str, timeout: float = 30) -> str:
        del timeout
        calls.append(args)
        if "content" in args:
            return "Row: 0 _data=/sdcard/Movies/TTSERP/tts_erp_deadbeef.mp4"
        return ""

    monkeypatch.setattr(device, "_run", fake_run)
    with pytest.raises(ManagedAlbumNotEmpty, match="residue"):
        await device.ensure_album_empty("TEST_SERIAL")
    media_query = next(call for call in calls if "content" in call)
    assert "_data LIKE ?" in media_query
    assert "_data:s:/sdcard/Movies/TTSERP/tts_erp_%.mp4" in media_query


@pytest.mark.asyncio
async def test_adb_preflight_accepts_mediastore_no_result_message(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    device = AdbDevice()

    async def fake_run(*args: str, timeout: float = 30) -> str:
        del timeout
        return "No result found." if "content" in args else ""

    monkeypatch.setattr(device, "_run", fake_run)
    await device.ensure_album_empty("TEST_SERIAL")


@pytest.mark.asyncio
async def test_adb_cleanup_fails_when_exact_media_row_remains(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    device = AdbDevice()
    path = device.device_path("12345678-1234-1234-1234-123456789abc")

    async def fake_run(*args: str, timeout: float = 30) -> str:
        del timeout
        return f"Row: 0 _data={path}" if "query" in args else ""

    async def no_sleep(_delay: float) -> None:
        return None

    monkeypatch.setattr(device, "_run", fake_run)
    monkeypatch.setattr("tts_erp_v2.publishing.adb_device.asyncio.sleep", no_sleep)
    with pytest.raises(DeviceUnavailable, match="仍保留"):
        await device.remove_staged_video("TEST_SERIAL", path)


@pytest.mark.asyncio
async def test_adb_cleanup_fails_when_exact_filesystem_path_remains(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    device = AdbDevice()
    path = device.device_path("12345678-1234-1234-1234-123456789abc")

    async def fake_run(*args: str, timeout: float = 30) -> str:
        del timeout
        if "find" in args:
            return path
        return ""

    async def no_sleep(_delay: float) -> None:
        return None

    monkeypatch.setattr(device, "_run", fake_run)
    monkeypatch.setattr("tts_erp_v2.publishing.adb_device.asyncio.sleep", no_sleep)
    with pytest.raises(DeviceUnavailable, match="仍保留"):
        await device.remove_staged_video("TEST_SERIAL", path)


def test_design_never_authorizes_publish_after_one_negative_verification() -> None:
    design = (ROOT / "docs/design/tiktok-video-publish.md").read_text()
    assert "明确未发布 → 新建 publish attempt" not in design
    assert "明确未发布 → 服务端按重试预算自动排队" not in design
    assert design.count("单次 `not_published`") >= 3
    for line in design.splitlines():
        if "not_published" in line or "明确未发布" in line:
            assert "自动排队" not in line
            assert "新建 publish" not in line


def test_attempt_events_only_report_real_insert_and_status_change(
    db_session: Session, caplog: pytest.LogCaptureFixture
) -> None:
    task = _task(status="running", stage="waiting_artemis")
    task.lease_owner = "TEST-v20-owner"
    task.lease_expires_at = datetime.now(UTC) + timedelta(minutes=5)
    task.started_at = datetime.now(UTC)
    attempt = VideoPublishAttempt(
        sequence_no=1,
        kind="publish",
        artemis_session_id=uuid4(),
        status="running",
        prompt_version="TEST",
        prompt_snapshot="TEST",
        device_serial="SHORT7",
        started_at=datetime.now(UTC),
    )
    task.attempts.append(attempt)
    db_session.add(task)
    db_session.commit()
    task_id = task.public_id
    task_pk = task.id
    attempt_id = attempt.id
    caplog.set_level(logging.INFO, logger="tts_erp_v2.publishing.observability")

    def factory() -> Session:
        return Session(
            bind=db_session.get_bind(), join_transaction_mode="create_savepoint"
        )

    prepared = commit_publish_transition(
        factory,
        PublishLeaseToken(
            task_id=task_id,
            lease_owner="TEST-v20-owner",
            row_version=task.row_version,
            attempt_id=attempt_id,
            expected_attempt_status="running",
        ),
        PrepareAttempt(attempt_id=attempt_id, lease_seconds=30, max_attempts=3),
    )
    assert prepared.retained_token is not None
    commit_publish_transition(
        factory,
        prepared.retained_token,
        ObserveAttempt(
            observation=AttemptObservation(status="running", terminal=False),
            max_attempts=3,
            lease_seconds=30,
        ),
    )

    events = [json.loads(record.message)["event"] for record in caplog.records]
    assert "artemis_attempt_created" not in events
    assert "artemis_status_changed" not in events
    assert (
        db_session.scalar(
            select(VideoPublishAttempt.status).where(
                VideoPublishAttempt.id == attempt_id,
                VideoPublishAttempt.task_id == task_pk,
            )
        )
        == "running"
    )


def test_attempt_events_report_actual_insert_and_status_change(
    db_session: Session, caplog: pytest.LogCaptureFixture
) -> None:
    task = _task(status="running", stage="staging_device")
    task.lease_owner = "TEST-v20-new-attempt-owner"
    task.lease_expires_at = datetime.now(UTC) + timedelta(minutes=5)
    db_session.add(task)
    db_session.commit()
    caplog.set_level(logging.INFO, logger="tts_erp_v2.publishing.observability")

    def factory() -> Session:
        return Session(
            bind=db_session.get_bind(), join_transaction_mode="create_savepoint"
        )

    prepared = commit_publish_transition(
        factory,
        PublishLeaseToken(
            task_id=task.public_id,
            lease_owner="TEST-v20-new-attempt-owner",
            row_version=task.row_version,
        ),
        PrepareAttempt(lease_seconds=30, max_attempts=3),
    )
    assert prepared.retained_token is not None
    commit_publish_transition(
        factory,
        prepared.retained_token,
        ObserveAttempt(
            observation=AttemptObservation(status="running", terminal=False),
            max_attempts=3,
            lease_seconds=30,
        ),
    )

    events = [json.loads(record.message)["event"] for record in caplog.records]
    assert events.count("artemis_attempt_created") == 1
    assert events.count("artemis_status_changed") == 1


@pytest.mark.asyncio
async def test_recover_active_logs_only_after_leasing_existing_running_task(
    db_session: Session,
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    task = _task(status="running", stage="downloading")
    task.lease_owner = "TEST-dead-worker"
    task.lease_expires_at = datetime.now(UTC) - timedelta(minutes=1)
    task.started_at = datetime.now(UTC) - timedelta(minutes=2)
    db_session.add(task)
    db_session.commit()
    task_id = task.public_id

    executed: list[object] = []

    async def fake_execute(recovered_task_id, _deps) -> None:
        executed.append(recovered_task_id)

    monkeypatch.setattr("tts_erp_v2.publishing.dispatcher._execute", fake_execute)
    caplog.set_level(logging.INFO, logger="tts_erp_v2.publishing.observability")
    deps = cast(
        PublishDependencies,
        SimpleNamespace(
            session_factory=lambda: Session(
                bind=db_session.get_bind(), join_transaction_mode="create_savepoint"
            ),
            instance_id="TEST-v20-recovery-worker",
            lease_seconds=30,
            spool_dir=tmp_path,
        ),
    )

    assert await recover_active(deps) == "recovered"
    assert executed == [task_id]
    events = [json.loads(record.message) for record in caplog.records]
    recovered = [event for event in events if event["event"] == "worker_recovered_task"]
    assert len(recovered) == 1
    assert recovered[0]["task_id"] == str(task_id)


@pytest.mark.parametrize(
    ("serial", "masked"),
    [("A", "…"), ("AB", "A…"), ("SHORT7", "SHO…T7"), ("123456789", "1234…6789")],
)
def test_shared_serial_masker_always_hides_at_least_one_character(
    serial: str,
    masked: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    from tts_erp_v2.publishing.observability import emit_publish_event

    caplog.set_level(logging.INFO, logger="tts_erp_v2.publishing.observability")
    emit_publish_event("TEST_serial_mask", task_id=uuid4(), device_serial=serial)
    payload = json.loads(caplog.records[-1].message)
    assert api._mask(serial) == masked
    assert payload["device_serial_masked"] == masked
    assert serial not in caplog.records[-1].message


def test_metrics_name_current_stage_age_as_a_gauge(db_session: Session) -> None:
    task = _task()
    task.target_device_serial = "SHORT7"
    task.stage_started_at = datetime.now(UTC) - timedelta(seconds=5)
    db_session.add(task)
    db_session.commit()

    payload = api.metrics(_request(), db_session)

    assert "stageDurationSeconds" not in payload
    assert payload["currentStageAgeSeconds"]["queued"]["count"] == 1
    assert payload["currentStageAgeSeconds"]["queued"]["maximum"] >= 5
    assert "SHORT7" not in json.dumps(payload, default=str)


def test_transition_event_reports_the_completed_stage_duration(
    db_session: Session, caplog: pytest.LogCaptureFixture
) -> None:
    task = _task(status="running", stage="downloading")
    task.lease_owner = "TEST-v20-duration-owner"
    task.lease_expires_at = datetime.now(UTC) + timedelta(minutes=5)
    task.stage_started_at = datetime.now(UTC) - timedelta(seconds=3)
    db_session.add(task)
    db_session.commit()
    caplog.set_level(logging.INFO, logger="tts_erp_v2.publishing.observability")

    def factory() -> Session:
        return Session(
            bind=db_session.get_bind(), join_transaction_mode="create_savepoint"
        )

    commit_publish_transition(
        factory,
        PublishLeaseToken(
            task_id=task.public_id,
            lease_owner="TEST-v20-duration-owner",
            row_version=task.row_version,
        ),
        AdvanceExecution(stage="staging_device", lease_seconds=30),
    )

    transitions = [
        json.loads(record.message)
        for record in caplog.records
        if json.loads(record.message)["event"] == "publish_transition"
    ]
    assert len(transitions) == 1
    assert transitions[0]["stage"] == "downloading"
    assert transitions[0]["duration_ms"] >= 3000


def test_retry_dialog_uses_consumed_publish_budget_not_audit_attempt_count() -> None:
    source = (ROOT / "tts_erp_v2/static/js/video-publish.js").read_text()
    dialog = source[
        source.index("function confirmationMessage") : source.index(
            "function confirmTaskAction"
        )
    ]
    assert "task.retryBudgetUsed" in dialog
    assert "task.attemptCount" not in dialog


def test_test_dependency_help_uses_real_path_and_accurate_pg_client_contract() -> None:
    help_text = (ROOT / "scripts/envsetup/install-test-deps.sh").read_text()
    assert "scripts/envscripts" not in help_text
    assert "bash scripts/envsetup/install-test-deps.sh --check" in help_text
    assert "模板刷新默认" in help_text
    assert "pg_dump 必须不早于服务端主版本" in help_text


def test_implemented_layout_and_deployment_docs_match_checked_in_files() -> None:
    design = (ROOT / "docs/design/tiktok-video-publish.md").read_text()
    unit = (ROOT / "scripts/systemd/tts-erp-publish.service").read_text().strip()
    assert "tests/api/test_video_publish.py" not in design
    assert "tests/browser/test_video_publish_page.py" not in design
    assert unit in design
    assert "缺少 `ARTEMIS_DEVICE_SERIAL` 时 Worker 可启动" in design
    assert (
        "缺少 `ARTEMIS_BASE_URL` 或 `TTS_ERP_PUBLISH_SPOOL_DIR` 时 Worker 启动失败"
        in design
    )


def test_cleanup_owner_metadata_matches_live_running_branch(
    db_session: Session,
) -> None:
    inspector = inspect(db_session.get_bind())
    live_constraint = next(
        row["sqltext"]
        for row in inspector.get_check_constraints(
            "video_publish_tasks", schema="publishing"
        )
        if row["name"] == "video_publish_task_cleanup_owner_check"
    )
    model_constraint = next(
        constraint
        for constraint in cast(Any, VideoPublishTask.__table__).constraints
        if constraint.name == "video_publish_task_cleanup_owner_check"
    )
    model_sql = " ".join(str(model_constraint.sqltext).lower().split())
    live_sql = " ".join(str(live_constraint).lower().split())

    assert "spool_path" in {
        column["name"]
        for column in inspector.get_columns("video_publish_tasks", schema="publishing")
    }
    assert "object_cleanup_status not in ('pending','failed')" in model_sql
    assert "device_cleanup_status in ('pending','failed')" not in model_sql
    running_live = live_sql[
        live_sql.index("status = 'running'::text") : live_sql.index(
            "device_cleanup_status", live_sql.index("status = 'running'::text")
        )
    ]
    assert "object_cleanup_status" in running_live
    assert "device_cleanup_status" not in running_live
