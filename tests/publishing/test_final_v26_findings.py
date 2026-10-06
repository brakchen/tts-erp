from __future__ import annotations

import importlib.util
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import cast
from uuid import uuid4

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import delete, inspect, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from tts_erp_v2.db.models.publishing import VideoPublishAttempt, VideoPublishTask
from tts_erp_v2.publishing import dispatcher
from tts_erp_v2.publishing.dispatcher import PublishDependencies, _execute_cleanup
from tts_erp_v2.publishing.repository import (
    CleanupClaimRequest,
    _lock_publish_slot,
    claim_cleanup_work,
    request_verification,
)

ROOT = Path(__file__).parents[2]
ACTIVE_ATTEMPT_STATUSES = ("created", "submitting", "queued", "running", "unknown")


def _load_0066():
    path = ROOT / "alembic" / "versions" / "0066_publish_execution_fences.py"
    spec = importlib.util.spec_from_file_location("test_0066_v26", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _insert_task(
    conn,
    *,
    status: str = "pending",
    stage: str = "queued",
    spool_path: str | None = None,
    device_path: str | None = None,
    object_deleted: bool = False,
) -> int:
    lease_owner = "TEST-v26-worker" if status == "running" else None
    # pi-lens-ignore: python-sql-injection
    return conn.execute(
        text(
            """
            INSERT INTO publishing.video_publish_tasks (
                client_request_id, caption, original_filename, content_type,
                size_bytes, object_bucket, object_key, status, stage,
                target_device_serial, target_app_package, lease_owner,
                lease_expires_at, heartbeat_at, spool_path, device_path,
                object_deleted_at, object_cleanup_status
            ) VALUES (
                :request_id, 'TEST_v26_caption', 'TEST_v26.mp4', 'video/mp4', 4,
                'tiktok-video', :object_key, :status, :stage,
                'TEST_v26_device', 'com.test.v26', CAST(:lease_owner AS text),
                CASE WHEN CAST(:lease_owner AS text) IS NULL THEN NULL ELSE clock_timestamp() + interval '5 minutes' END,
                CASE WHEN CAST(:lease_owner AS text) IS NULL THEN NULL ELSE clock_timestamp() END,
                CAST(:spool_path AS text), CAST(:device_path AS text),
                CASE WHEN CAST(:object_deleted AS boolean) THEN clock_timestamp() ELSE NULL END,
                CASE WHEN CAST(:object_deleted AS boolean) THEN 'succeeded' ELSE 'not_started' END
            ) RETURNING id
            """
        ),
        {
            "request_id": uuid4(),
            "object_key": f"TEST/v26/{uuid4()}.mp4",
            "status": status,
            "stage": stage,
            "lease_owner": lease_owner,
            "spool_path": spool_path,
            "device_path": device_path,
            "object_deleted": object_deleted,
        },
    ).scalar_one()


def _insert_attempt(conn, task_id: int, *, status: str) -> int:
    # pi-lens-ignore: python-sql-injection
    return conn.execute(
        text(
            """
            INSERT INTO publishing.video_publish_attempts (
                task_id, sequence_no, kind, artemis_session_id, status,
                prompt_version, prompt_snapshot, device_serial,
                target_app_package
            ) VALUES (
                :task_id, 1, 'publish', :session_id, :status,
                'TEST_v26', 'TEST_v26 prompt', 'TEST_v26_device',
                'com.test.v26'
            ) RETURNING id
            """
        ),
        {"task_id": task_id, "session_id": uuid4(), "status": status},
    ).scalar_one()


@pytest.mark.parametrize(
    ("setup", "reason"),
    [
        ("running_pre_attempt", "running task"),
        ("active_attempt", "active attempt"),
        ("legacy_spool_path", "legacy spool/device path"),
        ("legacy_device_path", "legacy spool/device path"),
    ],
)
def test_0066_refuses_unsafe_populated_predecessor_state(
    db_engine, setup: str, reason: str
) -> None:
    migration = _load_0066()
    with db_engine.connect() as conn:
        transaction = conn.begin()
        try:
            migration.__dict__["op"] = Operations(MigrationContext.configure(conn))
            migration.downgrade()
            if setup == "running_pre_attempt":
                _insert_task(conn, status="running", stage="downloading")
            elif setup == "active_attempt":
                task_id = _insert_task(conn, status="needs_review", stage="done")
                _insert_attempt(conn, task_id, status="unknown")
            elif setup == "legacy_spool_path":
                _insert_task(conn, spool_path="/TEST/legacy/task/video.mp4")
            else:
                _insert_task(
                    conn,
                    device_path="/sdcard/Movies/TTSERP/tts_erp_legacy.mp4",
                )

            savepoint = conn.begin_nested()
            with pytest.raises(Exception, match=reason):
                migration.upgrade()
            savepoint.rollback()
        finally:
            transaction.rollback()


def test_0066_preserves_terminal_attempt_identity_as_honest_unknown(
    db_engine,
) -> None:
    migration = _load_0066()
    with db_engine.connect() as conn:
        transaction = conn.begin()
        try:
            migration.__dict__["op"] = Operations(MigrationContext.configure(conn))
            migration.downgrade()
            task_id = _insert_task(conn, status="failed", stage="done")
            attempt_id = _insert_attempt(conn, task_id, status="failed")

            migration.upgrade()

            # pi-lens-ignore: python-sql-injection
            row = conn.execute(
                text(
                    "SELECT artemis_profile, artemis_verification_level "
                    "FROM publishing.video_publish_attempts WHERE id=:id"
                ),
                {"id": attempt_id},
            ).one()
            assert row == (None, None)
            checks = {
                item["name"]
                for item in inspect(conn).get_check_constraints(
                    "video_publish_attempts", schema="publishing"
                )
            }
            assert "video_publish_attempt_active_snapshot_check" in checks

            savepoint = conn.begin_nested()
            with pytest.raises(IntegrityError):
                # A null legacy identity may never become active/resubmittable.
                # pi-lens-ignore: python-sql-injection
                conn.execute(
                    text(
                        "UPDATE publishing.video_publish_attempts "
                        "SET status='unknown' WHERE id=:id"
                    ),
                    {"id": attempt_id},
                )
            savepoint.rollback()
        finally:
            transaction.rollback()


def _runtime_task(*, status: str, stage: str) -> VideoPublishTask:
    return VideoPublishTask(
        public_id=uuid4(),
        client_request_id=uuid4(),
        created_by_user_id=2626,
        caption="TEST_v26_caption",
        original_filename="TEST_v26.mp4",
        object_filename="TEST_v26.mp4",
        content_type="video/mp4",
        size_bytes=4,
        object_bucket="tiktok-video",
        object_key=f"TEST/v26/{uuid4()}/video.mp4",
        object_etag="TEST-v26-etag",
        object_uploaded_at=datetime.now(UTC),
        status=status,
        stage=stage,
        stage_started_at=datetime.now(UTC),
        cleanup_intent="none",
        target_device_serial="TEST_v26_device",
        target_app_package="com.test.v26",
    )


def _delete_tasks(factory: sessionmaker[Session], task_ids: list[int]) -> None:
    with factory() as session:
        session.execute(
            delete(VideoPublishAttempt).where(VideoPublishAttempt.task_id.in_(task_ids))
        )
        session.execute(
            delete(VideoPublishTask).where(VideoPublishTask.id.in_(task_ids))
        )
        session.commit()


def _review_task() -> VideoPublishTask:
    task = _runtime_task(status="needs_review", stage="done")
    task.attempts.append(
        VideoPublishAttempt(
            sequence_no=1,
            kind="publish",
            artemis_session_id=uuid4(),
            status="success",
            prompt_version="TEST_v26",
            prompt_snapshot="TEST_v26 prompt",
            device_serial="TEST_v26_device",
            target_app_package="com.test.v26",
            artemis_profile="TEST_v26_non_default_profile",
            artemis_verification_level="TEST_v26_non_default_verification",
        )
    )
    return task


def test_manual_verification_blocks_unrelated_device_cleanup_globally(
    db_session: Session,
) -> None:
    review = _review_task()
    cleanup = _runtime_task(status="failed", stage="done")
    cleanup.cleanup_intent = "preserve_state"
    cleanup.device_cleanup_status = "pending"
    cleanup.device_cleanup_next_attempt_at = datetime.now(UTC) - timedelta(seconds=1)
    db_session.add_all([review, cleanup])
    db_session.flush()

    with pytest.raises(ValueError, match="DEVICE_CLEANUP_BLOCKED"):
        request_verification(db_session, review.public_id)


def test_device_cleanup_selector_waits_for_verification_slot_and_then_yields(
    db_engine,
) -> None:
    factory = sessionmaker(db_engine, expire_on_commit=False)
    review = _review_task()
    with factory() as session:
        session.add(review)
        session.commit()
        review_id = review.public_id
        review_pk = review.id

    verification_started = threading.Event()
    release_verification = threading.Event()

    def reserve_verification() -> None:
        with factory() as session:
            request_verification(session, review_id)
            verification_started.set()
            assert release_verification.wait(timeout=5)
            session.commit()

    with ThreadPoolExecutor(max_workers=2) as pool:
        verification = pool.submit(reserve_verification)
        assert verification_started.wait(timeout=2)
        with factory() as session:
            cleanup = _runtime_task(status="failed", stage="done")
            cleanup.cleanup_intent = "preserve_state"
            cleanup.device_cleanup_status = "pending"
            cleanup.device_cleanup_next_attempt_at = datetime.now(UTC) - timedelta(
                seconds=1
            )
            session.add(cleanup)
            session.commit()
            cleanup_pk = cleanup.id
        cleanup_claim = pool.submit(
            claim_cleanup_work,
            factory,
            CleanupClaimRequest("TEST-v26-cleaner", 30, "device"),
        )
        threading.Event().wait(0.2)
        assert not cleanup_claim.done()
        release_verification.set()
        verification.result(timeout=5)
        assert cleanup_claim.result(timeout=5) is None
    _delete_tasks(factory, [review_pk, cleanup_pk])


def test_verification_waits_for_device_cleanup_slot_and_then_refuses(
    db_engine,
) -> None:
    factory = sessionmaker(db_engine, expire_on_commit=False)
    review = _review_task()
    cleanup = _runtime_task(status="failed", stage="done")
    cleanup.cleanup_intent = "preserve_state"
    cleanup.device_cleanup_status = "pending"
    cleanup.device_cleanup_next_attempt_at = datetime.now(UTC) - timedelta(seconds=1)
    with factory() as session:
        session.add_all([review, cleanup])
        session.commit()
        review_id = review.public_id
        cleanup_id = cleanup.id

    slot_held = threading.Event()
    release_cleanup = threading.Event()

    def reserve_cleanup() -> None:
        with factory() as session:
            _lock_publish_slot(session)
            row = session.get(VideoPublishTask, cleanup_id)
            assert row is not None
            row.cleanup_lease_owner = "TEST-v26-cleaner"
            row.cleanup_lease_expires_at = datetime.now(UTC) + timedelta(seconds=30)
            session.flush()
            slot_held.set()
            assert release_cleanup.wait(timeout=5)
            session.commit()

    def verify() -> str:
        with factory() as session:
            try:
                request_verification(session, review_id)
            except ValueError as exc:
                return str(exc)
            return "unexpected-success"

    with ThreadPoolExecutor(max_workers=2) as pool:
        cleanup_reservation = pool.submit(reserve_cleanup)
        assert slot_held.wait(timeout=2)
        verification = pool.submit(verify)
        threading.Event().wait(0.2)
        assert not verification.done()
        release_cleanup.set()
        cleanup_reservation.result(timeout=5)
        assert verification.result(timeout=5) == "DEVICE_CLEANUP_BLOCKED"
    _delete_tasks(factory, [review.id, cleanup.id])


@pytest.mark.asyncio
async def test_reopened_historical_generation_removes_delayed_put_after_hold(
    db_session: Session, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    task = _runtime_task(status="cancelled", stage="done")
    task.cleanup_intent = "preserve_state"
    task.object_deleted_at = None
    task.object_cleanup_status = "pending"
    task.object_cleanup_error = "HISTORICAL_PUT_CAPABILITY_REOPENED"
    task.object_upload_expires_at = datetime.now(UTC) - timedelta(minutes=16)
    task.object_cleanup_next_attempt_at = task.object_upload_expires_at
    db_session.add(task)
    db_session.commit()

    def factory() -> Session:
        return Session(
            bind=db_session.get_bind(), join_transaction_mode="create_savepoint"
        )

    removed: list[tuple[str, str | None]] = []
    monkeypatch.setattr(
        dispatcher, "require_destructive_script_guard", lambda **_kwargs: None
    )
    work = claim_cleanup_work(
        factory,
        CleanupClaimRequest("TEST-v26-object-cleaner", 30, "background"),
    )
    assert work is not None
    assert work.resources == ("object",)

    await _execute_cleanup(
        work,
        cast(
            PublishDependencies,
            SimpleNamespace(
                session_factory=factory,
                instance_id="TEST-v26-object-cleaner",
                lease_seconds=30,
                spool_dir=tmp_path,
                store=SimpleNamespace(
                    remove=lambda key, etag=None: removed.append((key, etag))
                ),
                adb=SimpleNamespace(),
            ),
        ),
    )

    db_session.expire_all()
    assert removed == [(task.object_key, task.object_etag)]
    assert task.object_cleanup_status == "succeeded"
    assert task.object_deleted_at is not None
    task_pk = task.id
    db_session.execute(
        delete(VideoPublishAttempt).where(VideoPublishAttempt.task_id == task_pk)
    )
    db_session.execute(delete(VideoPublishTask).where(VideoPublishTask.id == task_pk))
    db_session.commit()


def test_v26_design_uses_attempt_and_execution_generation_snapshots() -> None:
    design = Path("docs/design/tiktok-video-publish.md").read_text()
    submit = design[design.index("handle = await artemis.submit(") :]
    submit = submit[: submit.index("mark_admitted")]
    assert "profile=attempt.artemis_profile" in submit
    assert "locked_app_package=attempt.target_app_package" in submit
    assert "verification_level=attempt.artemis_verification_level" in submit
    assert "profile=config.profile" not in submit
    assert "locked_app_package=config.app_package" not in submit
    assert "verification_level=config.verification_level" not in submit
    assert "<task_public_id>/<execution_generation>/video.mp4.part|video.mp4" in design
    assert "tts_erp_<execution_generation>.mp4" in design


def test_agent_guidance_allows_only_ephemeral_isolated_runner() -> None:
    paths = [
        Path("README.md"),
        Path("AGENTS.md"),
        Path("docs/guides/agent-safety.md"),
        Path("docs/architecture/architecture-overview.md"),
        Path("docs/guides/test-domains.md"),
        *Path("docs/architecture/adr").glob("*.md"),
    ]
    forbidden = (
        "bash scripts/test.sh",
        "flock -n /tmp/tts-erp-test.lock",
        "shared fallback",
        "shared-DB fallback",
        "加锁降级流程",
    )
    for path in paths:
        source = path.read_text()
        for phrase in forbidden:
            assert phrase not in source, f"{path}: {phrase}"
    readme = Path("README.md").read_text()
    assert "bash scripts/test_isolated.sh fast" in readme
    assert "ephemeral" in readme.lower() or "临时" in readme


def test_install_test_dependencies_describes_container_default_without_fallback() -> (
    None
):
    source = Path("scripts/envsetup/install-test-deps.sh").read_text()
    assert "默认通过 PG_DOCKER=postgres 使用容器内 PostgreSQL 客户端" in source
    assert "tts_erp_v3_test）" not in source
    assert "shared-DB 回退路径" not in source
    assert "need = ['tts_erp_test_template']" in source


def test_0066_reopens_deleted_unknown_expiry_generation_for_delayed_put_cleanup(
    db_engine,
) -> None:
    migration = _load_0066()
    with db_engine.connect() as conn:
        transaction = conn.begin()
        try:
            migration.__dict__["op"] = Operations(MigrationContext.configure(conn))
            migration.downgrade()
            task_id = _insert_task(
                conn,
                status="cancelled",
                stage="done",
                object_deleted=True,
            )
            # pi-lens-ignore: python-sql-injection
            before_result = conn.execute(text("SELECT clock_timestamp()"))
            before = before_result.scalar_one()

            migration.upgrade()

            # pi-lens-ignore: python-sql-injection
            row = conn.execute(
                text(
                    """
                    SELECT object_upload_expires_at, object_deleted_at,
                           cleanup_intent, object_cleanup_status,
                           object_cleanup_next_attempt_at
                    FROM publishing.video_publish_tasks WHERE id=:id
                    """
                ),
                {"id": task_id},
            ).one()
            assert row.object_upload_expires_at >= before + timedelta(days=7)
            assert row.object_deleted_at is None
            assert row.cleanup_intent == "preserve_state"
            assert row.object_cleanup_status == "pending"
            assert row.object_cleanup_next_attempt_at == row.object_upload_expires_at
        finally:
            transaction.rollback()
