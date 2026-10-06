from __future__ import annotations

import importlib
import json
import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from uuid import uuid4

import pytest
from fastapi import HTTPException, Request
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from tts_erp_v2.access import AccessGrant, AuthMode, Role
from tts_erp_v2.accounts import service as account_service
from tts_erp_v2.accounts.models import RolePermission
from tts_erp_v2.api.v2 import video_publish as api
from tts_erp_v2.db.models.publishing import VideoPublishAttempt, VideoPublishTask
from tts_erp_v2.publishing.diagnostics import sanitize_text
from tts_erp_v2.publishing.domain import CleanupIntent
from tts_erp_v2.publishing.repository import (
    OperationalFailure,
    PublishLeaseToken,
    commit_publish_transition,
)
from tts_erp_v2.publishing.submission import (
    CreateCommand,
    confirm_upload,
    create_upload_ticket,
)
from tts_erp_v2.publishing.worker import _probe_device_readiness


def _request(*, role: Role = Role.READWRITE) -> Request:
    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/v2/video-publish",
            "headers": [
                (b"x-requested-with", b"tts-erp"),
                (b"x-request-id", b"TEST-v16-request"),
            ],
            "auth_method": "cookie",
            "access_grant": AccessGrant(
                mode=AuthMode.ENFORCE,
                role=role,
                scopes=("page:video-publish",),
                auth_method="cookie",
                bypass=True,
            ),
        }
    )


def _task(
    *, status: str = "pending", stage: str = "awaiting_upload"
) -> VideoPublishTask:
    return VideoPublishTask(
        public_id=uuid4(),
        client_request_id=uuid4(),
        caption="TEST caption",
        original_filename="TEST video.mp4",
        object_filename="TEST_video.mp4",
        content_type="video/mp4",
        size_bytes=4,
        object_bucket="tiktok-video",
        object_key=f"TEST/{uuid4()}.mp4",
        status=status,
        stage=stage,
        cleanup_intent=CleanupIntent.NONE.value,
        target_device_serial="TEST_device",
        target_app_package="com.tiktok",
        stage_started_at=datetime.now(UTC),
    )


@pytest.mark.parametrize(
    "identifier",
    [
        "MINIO_SECRET_KEY",
        "client_secret",
        "client-secret",
        "access_token",
        "access-token",
        "refresh_token",
        "refresh-token",
        "api_key",
        "api-key",
        "session_id",
        "session-id",
        "cookie",
        "set-cookie",
        "authorization",
        "proxy-authorization",
    ],
)
def test_credential_identifier_is_redacted_in_plain_text(identifier: str) -> None:
    secret = f"TEST_SECRET_{identifier}"
    cleaned = sanitize_text(f"prefix {identifier}={secret} suffix")
    assert secret not in cleaned
    assert "[REDACTED]" in cleaned


def test_underscore_credentials_are_redacted_at_persistence_and_response(
    db_session: Session,
) -> None:
    identifiers = (
        "MINIO_SECRET_KEY",
        "client_secret",
        "access_token",
        "refresh_token",
        "api_key",
        "session_id",
        "set-cookie",
        "authorization",
    )
    diagnostic_values = [f"TEST_DIAGNOSTIC_VALUE_{index}" for index in range(8)]
    task = _task(status="running", stage="downloading")
    task.lease_owner = "TEST-worker"
    task.lease_expires_at = datetime.now(UTC) + timedelta(minutes=1)
    db_session.add(task)
    db_session.commit()

    def factory() -> Session:
        return Session(
            bind=db_session.get_bind(), join_transaction_mode="create_savepoint"
        )

    commit_publish_transition(
        factory,
        PublishLeaseToken(task.public_id, "TEST-worker", task.row_version),
        OperationalFailure(
            action="failed",
            code="TEST_FAILURE",
            max_attempts=3,
            message="\n".join(
                f"{identifier}={value}"
                for identifier, value in zip(
                    identifiers, diagnostic_values, strict=True
                )
            ),
        ),
    )
    db_session.expire_all()
    persisted = task.last_error_message or ""
    payload = api._snapshot(
        task,
        detail=True,
        summary_attempts=[],
        publish_attempt_count=0,
        verify_attempt_count=0,
    )
    for diagnostic_value in diagnostic_values:
        assert diagnostic_value not in persisted
        assert diagnostic_value not in json.dumps(payload, default=str)
    assert "[REDACTED]" in persisted
    assert "[REDACTED]" in json.dumps(payload, default=str)


class _MissingEtagStore:
    def stat(self, _key: str) -> dict[str, Any]:
        return {"size": 4, "content_type": "video/mp4", "etag": "   "}


@pytest.mark.parametrize("etag", [None, "", "   ", 123])
def test_confirm_upload_rejects_missing_or_invalid_etag_without_queueing(
    db_session: Session, etag: object
) -> None:
    task = _task()
    db_session.add(task)
    db_session.commit()

    class Store:
        def stat(self, _key: str) -> dict[str, Any]:
            return {"size": 4, "content_type": "video/mp4", "etag": etag}

    with pytest.raises(ValueError, match="UPLOAD_ETAG_MISSING"):
        confirm_upload(
            db_session,
            task.public_id,
            cast(Any, Store()),
            expected_version=task.row_version,
        )
    db_session.refresh(task)
    assert (task.status, task.stage, task.object_etag, task.queued_at) == (
        "pending",
        "awaiting_upload",
        None,
        None,
    )


def test_confirm_upload_etag_error_is_structured_retryable(
    db_session: Session,
) -> None:
    task = _task()
    db_session.add(task)
    db_session.commit()
    with pytest.raises(HTTPException) as exc_info:
        api.confirm(
            task.public_id,
            api.ActionIn(rowVersion=task.row_version),
            _request(),
            db_session,
            cast(Any, _MissingEtagStore()),
        )
    assert exc_info.value.status_code == 422
    detail = cast(dict[str, Any], exc_info.value.detail)
    assert detail == {
        "code": "UPLOAD_ETAG_MISSING",
        "message": "UPLOAD_ETAG_MISSING",
        "retryable": True,
        "requestId": "TEST-v16-request",
    }
    db_session.refresh(task)
    assert task.stage == "awaiting_upload"


@pytest.mark.asyncio
async def test_future_cleanup_backoff_still_reports_device_busy(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ARTEMIS_DEVICE_SERIAL", "TEST_device")
    task = _task(status="succeeded", stage="done")
    task.cleanup_intent = "finalize_success"
    task.device_cleanup_status = "failed"
    task.device_cleanup_next_attempt_at = datetime.now(UTC) + timedelta(hours=1)
    task.device_cleanup_error = "MINIO_SECRET_KEY=TEST_DO_NOT_EXPOSE"
    db_session.add(task)
    db_session.commit()

    def factory() -> Session:
        return Session(
            bind=db_session.get_bind(), join_transaction_mode="create_savepoint"
        )

    deps = SimpleNamespace(session_factory=factory)
    status_value, message = await _probe_device_readiness(cast(Any, deps))
    assert status_value == "busy"
    assert "清理" in message
    assert "TEST_DO_NOT_EXPOSE" not in message


def test_needs_review_summary_exposes_related_publish_not_latest_verify(
    db_session: Session,
) -> None:
    task = _task(status="needs_review", stage="done")
    db_session.add(task)
    db_session.flush()
    publish = VideoPublishAttempt(
        task_id=task.id,
        sequence_no=1,
        kind="publish",
        artemis_session_id=uuid4(),
        status="unknown",
        prompt_version="TEST",
        prompt_snapshot="TEST",
        device_serial="TEST_device",
        artemis_profile="TEST-v16-profile",
        artemis_verification_level="TEST-v16-verification",
    )
    db_session.add(publish)
    db_session.flush()
    verify = VideoPublishAttempt(
        task_id=task.id,
        sequence_no=2,
        kind="verify",
        related_attempt_id=publish.id,
        artemis_session_id=uuid4(),
        status="success",
        prompt_version="TEST",
        prompt_snapshot="TEST",
        device_serial="TEST_device",
        artemis_output={"verdict": "inconclusive"},
    )
    db_session.add(verify)
    db_session.commit()
    summaries = cast(Any, api._attempt_summaries(db_session, [task]))
    latest, publish_count, verify_count, related_publish = summaries[task.id]
    payload = cast(Any, api._snapshot)(
        task,
        summary_attempts=latest,
        publish_attempt_count=publish_count,
        verify_attempt_count=verify_count,
        related_publish_attempt=related_publish,
    )
    assert payload["currentAttempt"]["artemisSessionId"] == str(
        verify.artemis_session_id
    )
    assert payload["relatedPublishAttempt"]["artemisSessionId"] == str(
        publish.artemis_session_id
    )


def test_confirm_and_retry_responses_include_queue_contract(
    db_session: Session,
) -> None:
    ahead = _task(status="pending", stage="queued")
    ahead.queued_at = datetime.now(UTC) - timedelta(minutes=5)
    upload = _task()
    retryable = _task(status="failed", stage="done")
    retryable.object_uploaded_at = datetime.now(UTC)
    db_session.add_all([ahead, upload, retryable])
    db_session.flush()
    db_session.add(
        VideoPublishAttempt(
            task_id=retryable.id,
            sequence_no=1,
            kind="publish",
            artemis_session_id=uuid4(),
            status="failed",
            prompt_version="TEST",
            prompt_snapshot="TEST",
            device_serial="TEST_device",
            retry_safe=True,
        )
    )
    db_session.commit()

    class Store:
        def stat(self, _key: str) -> dict[str, Any]:
            return {"size": 4, "content_type": "video/mp4", "etag": "TEST-etag"}

    confirmed = api.confirm(
        upload.public_id,
        api.ActionIn(rowVersion=upload.row_version),
        _request(),
        db_session,
        cast(Any, Store()),
    )
    assert confirmed["queuedAt"] is not None
    assert confirmed["queuePosition"] == 2
    retried = api.retry(
        retryable.public_id,
        api.ActionIn(rowVersion=retryable.row_version),
        _request(),
        db_session,
        cast(Any, Store()),
    )
    assert retried["queuedAt"] is not None
    assert retried["queuePosition"] == 3


def test_queue_position_and_labels_are_exposed_with_queue_timestamp(
    db_session: Session,
) -> None:
    first = _task(status="pending", stage="queued")
    first.queued_at = datetime.now(UTC) - timedelta(minutes=1)
    second = _task(status="pending", stage="queued")
    second.queued_at = datetime.now(UTC)
    db_session.add_all([first, second])
    db_session.commit()
    positions = cast(Any, api)._queue_positions(db_session, [first, second])
    payload = cast(Any, api._snapshot)(second, queue_position=positions[second.id])
    assert payload["queuePosition"] == 2
    assert payload["queuedAt"] == second.queued_at
    assert payload["statusLabel"] == "排队"
    assert payload["stageLabel"] == "队列中"


def test_metrics_are_read_only_bounded_and_contain_no_content(
    db_session: Session,
) -> None:
    queued = _task(status="pending", stage="queued")
    queued.queued_at = datetime.now(UTC)
    review = _task(status="needs_review", stage="done")
    review.caption = "TEST_SECRET_CAPTION"
    review.cleanup_intent = "preserve_state"
    review.device_cleanup_status = "failed"
    review.attempts.append(
        VideoPublishAttempt(
            sequence_no=1,
            kind="publish",
            artemis_session_id=uuid4(),
            status="success",
            prompt_version="TEST",
            prompt_snapshot="TEST_SECRET_PROMPT",
            device_serial="TEST_device",
        )
    )
    db_session.add_all([queued, review])
    db_session.add(
        api.PublishWorkerHeartbeat(
            instance_id="TEST_metrics_worker",
            hostname="TEST_host",
            pid=123,
            status="ready",
            device_status="ready",
            device_message="TEST ready",
            started_at=datetime.now(UTC),
            heartbeat_at=datetime.now(UTC),
        )
    )
    db_session.commit()
    payload = cast(Any, api).metrics(_request(role=Role.READONLY), db_session)
    assert payload["queueDepth"] >= 1
    assert payload["needsReview"] >= 1
    assert payload["cleanup"]["deviceFailed"] >= 1
    assert payload["tasksByStatus"]["needs_review"] >= 1
    assert payload["attemptsByKindStatus"]["publish"]["success"] >= 1
    assert payload["currentStageAgeSeconds"]["done"]["count"] >= 1
    assert payload["workerHeartbeatAgeSeconds"] is not None
    serialized = json.dumps(payload, default=str)
    assert "TEST_SECRET_CAPTION" not in serialized
    assert "TEST_SECRET_PROMPT" not in serialized


def test_observability_failure_never_changes_business_control_flow(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observability = importlib.import_module("tts_erp_v2.publishing.observability")

    def fail_log(_message: str) -> None:
        raise RuntimeError("TEST logging unavailable")

    monkeypatch.setattr(observability.logger, "info", fail_log)
    observability.emit_publish_event("publish_transition", task_id=uuid4())


def test_create_and_confirm_emit_content_free_structured_events(
    db_session: Session,
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ARTEMIS_DEVICE_SERIAL", "TEST_device")
    monkeypatch.setenv("TIKTOK_PUBLISH_MINIO_BUCKET", "tiktok-video")
    caplog.set_level(logging.INFO, logger="tts_erp_v2.publishing.observability")

    class Store:
        bucket = "tiktok-video"

        def presign_put(self, _key: str, _content_type: str) -> str:
            return "https://upload.test/TEST"

        def stat(self, _key: str) -> dict[str, Any]:
            return {"size": 4, "content_type": "video/mp4", "etag": "TEST-etag"}

    task, _, _ = create_upload_ticket(
        db_session,
        CreateCommand(
            client_request_id=uuid4(),
            filename="TEST.mp4",
            content_type="video/mp4",
            size_bytes=4,
            caption="TEST_PRIVATE_CAPTION",
        ),
        cast(Any, Store()),
    )
    confirm_upload(
        db_session,
        task.public_id,
        cast(Any, Store()),
        expected_version=task.row_version,
    )
    messages = "\n".join(record.message for record in caplog.records)
    assert "publish_task_created" in messages
    assert "upload_confirmed" in messages
    assert "TEST_PRIVATE_CAPTION" not in messages
    assert "https://upload.test" not in messages


def test_structured_observability_contains_only_safe_identifiers(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO, logger="tts_erp_v2.publishing.observability")
    observability = importlib.import_module("tts_erp_v2.publishing.observability")
    observability.emit_publish_event(
        "artemis_status_changed",
        task_id=uuid4(),
        attempt_id=12,
        artemis_session_id=uuid4(),
        attempt_kind="publish",
        stage="waiting_artemis",
        outcome="running",
        device_serial="TEST_DEVICE_12345678",
        duration_ms=215,
    )
    entry = json.loads(caplog.records[-1].message)
    assert set(entry) == {
        "event",
        "task_id",
        "attempt_id",
        "artemis_session_id",
        "attempt_kind",
        "stage",
        "outcome",
        "device_serial_masked",
        "duration_ms",
    }
    assert entry["device_serial_masked"] == "TEST…5678"
    assert entry["duration_ms"] == 215
    assert "TEST_DEVICE_12345678" not in caplog.records[-1].message
    assert not {"caption", "prompt", "output", "token"}.intersection(entry)


def test_builtin_permission_seed_withholds_video_publish_from_operator(
    db_session: Session,
) -> None:
    account_service.seed_builtin_roles(db_session)
    account_service.seed_builtin_roles(db_session)
    grants = set(
        db_session.execute(
            select(RolePermission.role_code, RolePermission.permission_code).where(
                RolePermission.permission_code == "page:video-publish"
            )
        )
    )
    assert ("admin", "page:video-publish") in grants
    assert ("operator", "page:video-publish") not in grants
    db_session.add(
        RolePermission(role_code="operator", permission_code="page:video-publish")
    )
    db_session.commit()
    account_service.seed_builtin_roles(db_session)
    assert (
        db_session.scalar(
            select(RolePermission).where(
                RolePermission.role_code == "operator",
                RolePermission.permission_code == "page:video-publish",
            )
        )
        is not None
    )


def test_0066_is_live_linear_head_with_admin_only_default(
    db_session: Session,
) -> None:
    assert db_session.scalar(text("SELECT version_num FROM alembic_version")) == (
        "0068_spu_deterioration_alert"
    )
    grants = set(
        db_session.execute(
            select(RolePermission.role_code, RolePermission.permission_code).where(
                RolePermission.permission_code == "page:video-publish"
            )
        )
    )
    assert grants == {("admin", "page:video-publish")}


def test_v16_registry_rollout_and_operations_docs_are_precise() -> None:
    root = Path(__file__).parents[2]
    registry = (root / "docs/handoff/ACTIVE.md").read_text()
    runbook = (root / "docs/ops/video-publish-runbook.md").read_text()
    external_api = (root / "docs/api/external-api.md").read_text()
    design = (root / "docs/design/tiktok-video-publish.md").read_text()
    assert "006[0-6]" in registry and "scripts/test_isolated.sh" in registry
    assert (
        "bash scripts/test_isolated.sh --refresh-template fast tests/publishing"
        in runbook
    )
    assert "PostgreSQL 18" in runbook and "pg_dump" in runbook
    assert "只自动给 admin" in runbook and "手工" in runbook
    assert "browser-only" not in external_api
    assert "systemctl --user daemon-reload" in design
    for absent_name in (
        "read_task(session",
        "list_tasks(session",
    ):
        assert absent_name not in design


def test_upload_selection_and_artemis_copy_controls_are_explicit() -> None:
    root = Path(__file__).parents[2]
    template = (root / "tts_erp_v2/templates/pages/video-publish.html").read_text()
    source = (root / "tts_erp_v2/static/js/video-publish.js").read_text()
    assert 'id="publish-file-reselect"' in template and "重新选择" in template
    assert 'id="publish-file-clear"' in template and "清除选择" in template
    assert 'document.execCommand("copy")' in source
    assert 'trigger.textContent = "已复制"' in source
    assert "artemis-copy" in source


def test_architecture_inventory_is_derived_from_live_schema(
    db_session: Session,
) -> None:
    rows = db_session.execute(
        text("""
        SELECT table_schema, count(*)
        FROM information_schema.tables
        WHERE table_type = 'BASE TABLE'
          AND table_schema NOT IN ('public', 'information_schema')
          AND table_schema NOT LIKE 'pg_%'
        GROUP BY table_schema
        """)
    ).all()
    assert len(rows) == 14
    assert sum(int(row[1]) for row in rows) == 73
