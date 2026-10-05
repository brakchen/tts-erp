from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from fastapi import Request
from sqlalchemy import event
from sqlalchemy.orm import Session

from tts_erp_v2.access import AccessGrant, AuthMode, Role
from tts_erp_v2.api.v2 import video_publish as api
from tts_erp_v2.db.models.publishing import (
    PublishWorkerHeartbeat,
    VideoPublishAttempt,
    VideoPublishTask,
)

ROOT = Path(__file__).parents[2]


def _request(role: Role, *, user_id: int | None = None) -> Request:
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/v2/video-publish",
            "headers": [(b"x-request-id", b"TEST-v18")],
            "access_grant": AccessGrant(
                mode=AuthMode.ENFORCE,
                role=role,
                scopes=("page:video-publish",),
                auth_method="cookie",
                bypass=False,
            ),
            "auth_method": "cookie",
            "user_id": user_id,
        }
    )


def _task(
    *, user_id: int, status: str = "pending", stage: str = "queued"
) -> VideoPublishTask:
    return VideoPublishTask(
        public_id=uuid4(),
        client_request_id=uuid4(),
        created_by_user_id=user_id,
        caption=f"TEST_PRIVATE_CAPTION_{user_id}",
        original_filename="TEST.mp4",
        object_filename="TEST.mp4",
        content_type="video/mp4",
        size_bytes=4,
        object_bucket="tiktok-video",
        object_key=f"TEST/{uuid4()}.mp4",
        object_etag="TEST-etag",
        object_uploaded_at=datetime.now(UTC),
        status=status,
        stage=stage,
        cleanup_intent="none",
        target_device_serial="TEST_DEVICE_12345678",
        target_app_package="com.tiktok",
        queued_at=datetime.now(UTC) if status == "pending" else None,
        stage_started_at=datetime.now(UTC),
    )


def test_config_exposes_admin_diagnostics_capability(
    db_session: Session, monkeypatch
) -> None:
    now = datetime.now(UTC)
    db_session.add(
        PublishWorkerHeartbeat(
            instance_id="TEST-v18-worker",
            hostname="TEST-host",
            pid=123,
            status="ready",
            device_status="ready",
            device_message="TEST ready",
            started_at=now,
            heartbeat_at=now,
        )
    )
    db_session.commit()
    monkeypatch.setenv("ARTEMIS_DEVICE_SERIAL", "TEST_DEVICE_12345678")

    assert (
        api.config(_request(Role.READONLY, user_id=1), db_session)["canViewDiagnostics"]
        is False
    )
    assert (
        api.config(_request(Role.ADMIN, user_id=1), db_session)["canViewDiagnostics"]
        is True
    )


def test_expanded_metrics_are_owner_scoped_bounded_and_content_free(
    db_session: Session,
) -> None:
    own = _task(user_id=101)
    other = _task(user_id=202)
    own.attempts.append(
        VideoPublishAttempt(
            sequence_no=1,
            kind="publish",
            artemis_session_id=uuid4(),
            status="failed",
            prompt_version="TEST",
            prompt_snapshot="TEST_PRIVATE_PROMPT_101",
            device_serial="TEST_DEVICE_12345678",
        )
    )
    other.attempts.append(
        VideoPublishAttempt(
            sequence_no=1,
            kind="verify",
            related_attempt_id=None,
            artemis_session_id=uuid4(),
            status="success",
            prompt_version="TEST",
            prompt_snapshot="TEST_PRIVATE_PROMPT_202",
            device_serial="TEST_DEVICE_87654321",
        )
    )
    # The related-attempt constraint requires verify rows to reference publish;
    # use a publish kind for the foreign owner's row since only scoping matters.
    other.attempts[0].kind = "publish"
    db_session.add_all([own, other])
    db_session.commit()

    statements: list[str] = []

    def capture(_conn, _cursor, statement, _parameters, _context, _executemany):
        statements.append(statement)

    event.listen(db_session.get_bind(), "before_cursor_execute", capture)
    try:
        payload = api.metrics(_request(Role.READONLY, user_id=101), db_session)
    finally:
        event.remove(db_session.get_bind(), "before_cursor_execute", capture)

    assert payload["queueDepth"] == 1
    assert payload["tasksByStatus"] == {"pending": 1}
    assert payload["attemptsByKindStatus"] == {"publish": {"failed": 1}}
    assert payload["currentStageAgeSeconds"]["queued"]["count"] == 1
    assert (
        len(
            [
                statement
                for statement in statements
                if statement.lstrip().upper().startswith("SELECT")
            ]
        )
        <= 5
    )
    serialized = json.dumps(payload, default=str)
    assert "TEST_PRIVATE" not in serialized
    assert "TEST_DEVICE" not in serialized


def test_browser_handles_settled_replay_and_non_admin_polling_once() -> None:
    source = (ROOT / "tts_erp_v2/static/js/video-publish.js").read_text()
    assert 'ticket.idempotentReplay && ticket.stage !== "awaiting_upload"' in source
    settled = source.index(
        'ticket.idempotentReplay && ticket.stage !== "awaiting_upload"'
    )
    confirm = source.index("confirm-upload", settled)
    replay_block = source[settled:confirm]
    assert "clearForm()" in replay_block
    assert "state.clientRequestId = crypto.randomUUID()" in replay_block
    assert "await refresh()" in replay_block
    assert "state.config?.canViewDiagnostics === true" in source
    assert "?includeDiagnostics=true" in source
    assert "error.status !== 403" not in source
    assert "attempt?.kindLabel || attempt?.kind" in source
    assert "attempt.kindLabel || attempt.kind" in source
    assert "copyText(id, event.currentTarget)" in source


def test_observability_inventory_is_wired_at_commit_boundaries() -> None:
    repository = (ROOT / "tts_erp_v2/publishing/repository.py").read_text()
    dispatcher = (ROOT / "tts_erp_v2/publishing/dispatcher.py").read_text()
    combined = repository + dispatcher
    for event_name in (
        "publish_task_claimed",
        "object_download_started",
        "object_download_succeeded",
        "device_stage_started",
        "device_stage_succeeded",
        "artemis_attempt_created",
        "artemis_submit_retried",
        "artemis_status_changed",
        "verification_started",
        "verification_finished",
        "publish_task_terminal",
        "cleanup_resource_finished",
        "worker_recovered_task",
    ):
        assert f'"{event_name}"' in combined
    observability = (ROOT / "tts_erp_v2/publishing/observability.py").read_text()
    assert '"device_serial_masked"' in observability
    assert '"duration_ms"' in observability


def test_generated_schema_and_architecture_cover_publishing_head() -> None:
    schema_readme = (ROOT / "docs/schema/README.md").read_text()
    schema_sql = (ROOT / "docs/schema/schema_tts_erp.sql").read_text()
    process = (ROOT / "docs/architecture/process-architecture.md").read_text()
    assert "0066_publish_execution_fences" in schema_readme
    assert "13 个业务 schema、71 张表" in schema_readme
    assert "### publishing" in schema_readme
    assert "CREATE SCHEMA publishing" in schema_sql
    assert "CREATE TABLE IF NOT EXISTS publishing.video_publish_tasks" in schema_sql
    assert "CREATE TABLE IF NOT EXISTS publishing.video_publish_attempts" in schema_sql
    assert "CREATE TABLE IF NOT EXISTS publishing.worker_heartbeats" in schema_sql
    assert "13 schema SQLAlchemy" in process
    assert "tts-erp-publish.service" in process
    assert "publishing.py" in process


def test_pg18_tooling_and_deployment_docs_are_self_contained() -> None:
    importer = (ROOT / "scripts/import_prod_to_test.sh").read_text()
    installer = (ROOT / "scripts/envsetup/install-test-deps.sh").read_text()
    design = (ROOT / "docs/design/tiktok-video-publish.md").read_text()
    assert 'if [[ -n "$PG_DOCKER" ]]' in importer
    assert "pg_dump 必须不早于服务端主版本" in installer
    assert "User=" not in design
    assert "WantedBy=default.target" in design
    assert "systemctl --user enable --now tts-erp-publish.service" in design
    assert "缺少 `ARTEMIS_DEVICE_SERIAL`" in design
    assert "只自动授权 `admin`" in design
