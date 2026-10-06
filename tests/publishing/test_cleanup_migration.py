from __future__ import annotations

import importlib.util
from pathlib import Path
from uuid import uuid4

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import text
from sqlalchemy.orm import Session

from tts_erp_v2.publishing.repository import request_verification

MIGRATION = (
    Path(__file__).parents[2] / "alembic/versions/0058_video_publish_cleanup_owner.py"
)


def _load_migration():
    spec = importlib.util.spec_from_file_location("migration_0058_test", MIGRATION)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _insert_legacy_task(conn, *, stage: str, device: str, spool: str, obj: str):
    # pi-lens-ignore: python-sql-injection
    return conn.execute(
        text(
            """
            INSERT INTO publishing.video_publish_tasks (
                client_request_id, caption, original_filename, content_type, size_bytes,
                object_bucket, object_key, status, stage, target_device_serial,
                target_app_package, device_path, device_cleanup_status,
                spool_cleanup_status, object_cleanup_status, lease_owner,
                lease_expires_at, heartbeat_at, queued_at
            ) VALUES (
                :client_request_id, 'TEST_caption', 'TEST_video.mp4', 'video/mp4', 4,
                'tiktok-video', :object_key, 'pending', :stage, 'TEST_device',
                'com.tiktok', '/sdcard/TEST/video.mp4', :device, :spool, :obj,
                'legacy-worker', now(), now(), now()
            ) RETURNING id
            """
        ),
        {
            "client_request_id": uuid4(),
            "object_key": f"TEST/migration-{uuid4()}.mp4",
            "stage": stage,
            "device": device,
            "spool": spool,
            "obj": obj,
        },
    ).scalar_one()


def _read_task(conn, task_id):
    # pi-lens-ignore: python-sql-injection
    return conn.execute(
        text(
            """
            SELECT status, stage, cleanup_intent, lease_owner,
                   cleanup_lease_owner
            FROM publishing.video_publish_tasks WHERE id = :id
            """
        ),
        {"id": task_id},
    ).one()


def test_0058_backfills_legacy_pending_cleanup_states(db_engine) -> None:
    migration = _load_migration()
    with db_engine.connect() as conn:
        transaction = conn.begin()
        try:
            migration.__dict__["op"] = Operations(MigrationContext.configure(conn))
            migration.downgrade()
            rows = {
                "device": _insert_legacy_task(
                    conn,
                    stage="queued",
                    device="pending",
                    spool="pending",
                    obj="not_started",
                ),
                "object": _insert_legacy_task(
                    conn,
                    stage="waiting_device",
                    device="not_started",
                    spool="not_started",
                    obj="failed",
                ),
                "clean": _insert_legacy_task(
                    conn,
                    stage="queued",
                    device="not_started",
                    spool="not_started",
                    obj="not_started",
                ),
            }
            migration.upgrade()
            actual = {key: _read_task(conn, row_id) for key, row_id in rows.items()}
            assert actual["device"] == (
                "pending",
                "waiting_device",
                "requeue_publish",
                None,
                None,
            )
            assert actual["object"] == (
                "needs_review",
                "done",
                "none",
                None,
                None,
            )
            assert actual["clean"] == ("pending", "queued", "none", None, None)
        finally:
            transaction.rollback()


@pytest.mark.parametrize("legacy_stage", ["done", "cleaning"])
def test_0058_ambiguous_running_rows_retain_minio(db_engine, legacy_stage: str) -> None:
    migration = _load_migration()
    with db_engine.connect() as conn:
        transaction = conn.begin()
        try:
            migration.__dict__["op"] = Operations(MigrationContext.configure(conn))
            migration.downgrade()
            # pi-lens-ignore: python-sql-injection
            task_id = conn.execute(
                text(
                    """
                    INSERT INTO publishing.video_publish_tasks (
                        client_request_id, caption, original_filename, content_type,
                        size_bytes, object_bucket, object_key, status, stage,
                        target_device_serial, target_app_package, device_path,
                        device_cleanup_status, spool_cleanup_status,
                        object_cleanup_status
                    ) VALUES (
                        :client_request_id, 'TEST_caption', 'TEST_video.mp4',
                        'video/mp4', 4, 'tiktok-video', :object_key, 'running',
                        :stage, 'TEST_device', 'com.tiktok', '/sdcard/TEST/video.mp4',
                        'pending', 'failed', 'pending'
                    ) RETURNING id
                    """
                ),
                {
                    "client_request_id": uuid4(),
                    "object_key": f"TEST/ambiguous-{uuid4()}.mp4",
                    "stage": legacy_stage,
                },
            ).scalar_one()
            migration.upgrade()
            # pi-lens-ignore: python-sql-injection
            row = conn.execute(
                text(
                    """
                    SELECT status, stage, cleanup_intent, device_cleanup_status,
                           spool_cleanup_status, object_cleanup_status,
                           object_deleted_at
                    FROM publishing.video_publish_tasks WHERE id = :id
                    """
                ),
                {"id": task_id},
            ).one()
            assert row == (
                "needs_review",
                "done",
                "preserve_state",
                "pending",
                "failed",
                "not_started",
                None,
            )
        finally:
            transaction.rollback()


def test_0058_no_work_ambiguous_row_blocks_legacy_manual_verification(
    db_engine,
) -> None:
    """0066 contract: legacy attempts with unknown profile/verification must not
    be resubmitted under the same session, including via request_verification.
    The task stays needs_review; the immutability trigger prevents supplying a
    different identity, so recovery must come from a new publish attempt created
    with explicit values.
    """
    migration = _load_migration()
    with db_engine.connect() as conn:
        transaction = conn.begin()
        try:
            migration.__dict__["op"] = Operations(MigrationContext.configure(conn))
            migration.downgrade()
            # pi-lens-ignore: python-sql-injection
            task_id = conn.execute(
                text(
                    """
                    INSERT INTO publishing.video_publish_tasks (
                        client_request_id, caption, original_filename, content_type,
                        size_bytes, object_bucket, object_key, status, stage,
                        target_device_serial, target_app_package, device_path,
                        device_cleanup_status, spool_cleanup_status,
                        object_cleanup_status
                    ) VALUES (
                        :client_request_id, 'TEST_caption', 'TEST_video.mp4',
                        'video/mp4', 4, 'tiktok-video', :object_key, 'running',
                        'done', 'TEST_device', 'com.tiktok', '/sdcard/TEST/video.mp4',
                        'succeeded', 'succeeded', 'pending'
                    ) RETURNING id
                    """
                ),
                {
                    "client_request_id": uuid4(),
                    "object_key": f"TEST/no-work-{uuid4()}.mp4",
                },
            ).scalar_one()
            # pi-lens-ignore: python-sql-injection
            conn.execute(
                text(
                    """
                    INSERT INTO publishing.video_publish_attempts (
                        task_id, sequence_no, kind, artemis_session_id, status,
                        prompt_version, prompt_snapshot, device_serial
                    ) VALUES (
                        :task_id, 1, 'publish', :session_id, 'failed',
                        'TEST', 'TEST', 'TEST_device'
                    )
                    """
                ),
                {"task_id": task_id, "session_id": uuid4()},
            )
            migration.upgrade()
            # pi-lens-ignore: python-sql-injection
            row = conn.execute(
                text(
                    """
                    SELECT public_id, status, stage, cleanup_intent,
                           object_cleanup_status, object_deleted_at
                    FROM publishing.video_publish_tasks WHERE id = :id
                    """
                ),
                {"id": task_id},
            ).one()
            assert row[1:] == (
                "needs_review",
                "done",
                "none",
                "not_started",
                None,
            )
            session = Session(bind=conn, join_transaction_mode="create_savepoint")
            try:
                with pytest.raises(ValueError, match="VERIFY_SNAPSHOT_UNKNOWN"):
                    request_verification(session, row.public_id)
                session.rollback()
                # The identity fields are immutable, so attempts cannot be
                # backfilled to fake provenance; the trigger blocks any UPDATE.
                with pytest.raises(Exception, match="identity fields are immutable"):
                    # pi-lens-ignore: python-sql-injection
                    conn.execute(
                        text(
                            """
                            UPDATE publishing.video_publish_attempts
                            SET artemis_profile='TEST_pro',
                                artemis_verification_level='TEST_strict'
                            WHERE task_id = :task_id
                            """
                        ),
                        {"task_id": task_id},
                    )
            finally:
                session.close()
        finally:
            transaction.rollback()


@pytest.mark.parametrize(
    ("verify_status", "verdict", "expected_status", "expected_intent"),
    [
        ("success", "published", "succeeded", "finalize_success"),
        ("success", "not_published", "needs_review", "preserve_state"),
        ("success", "inconclusive", "needs_review", "preserve_state"),
        ("failed", "published", "needs_review", "preserve_state"),
    ],
)
def test_0058_verify_backfill_requires_successful_published_verdict(
    db_engine,
    verify_status: str,
    verdict: str,
    expected_status: str,
    expected_intent: str,
) -> None:
    migration = _load_migration()
    with db_engine.connect() as conn:
        transaction = conn.begin()
        try:
            migration.__dict__["op"] = Operations(MigrationContext.configure(conn))
            migration.downgrade()
            # pi-lens-ignore: python-sql-injection
            task_id = conn.execute(
                text(
                    """
                    INSERT INTO publishing.video_publish_tasks (
                        client_request_id, caption, original_filename, content_type,
                        size_bytes, object_bucket, object_key, status, stage,
                        target_device_serial, target_app_package, device_path,
                        device_cleanup_status, spool_cleanup_status,
                        object_cleanup_status
                    ) VALUES (
                        :client_request_id, 'TEST_caption', 'TEST_video.mp4',
                        'video/mp4', 4, 'tiktok-video', :object_key, 'running',
                        'cleaning', 'TEST_device', 'com.tiktok', '/sdcard/TEST/video.mp4',
                        'pending', 'pending', 'pending'
                    ) RETURNING id
                    """
                ),
                {
                    "client_request_id": uuid4(),
                    "object_key": f"TEST/verify-backfill-{uuid4()}.mp4",
                },
            ).scalar_one()
            # pi-lens-ignore: python-sql-injection
            publish_id = conn.execute(
                text(
                    """
                    INSERT INTO publishing.video_publish_attempts (
                        task_id, sequence_no, kind, artemis_session_id, status,
                        prompt_version, prompt_snapshot, device_serial
                    ) VALUES (
                        :task_id, 1, 'publish', :session_id, 'failed',
                        'TEST', 'TEST', 'TEST_device'
                    ) RETURNING id
                    """
                ),
                {"task_id": task_id, "session_id": uuid4()},
            ).scalar_one()
            # pi-lens-ignore: python-sql-injection
            conn.execute(
                text(
                    """
                    INSERT INTO publishing.video_publish_attempts (
                        task_id, sequence_no, kind, related_attempt_id,
                        artemis_session_id, status, prompt_version,
                        prompt_snapshot, device_serial, artemis_output
                    ) VALUES (
                        :task_id, 2, 'verify', :publish_id, :session_id,
                        :status, 'TEST', 'TEST', 'TEST_device',
                        CAST(:output AS JSONB)
                    )
                    """
                ),
                {
                    "task_id": task_id,
                    "publish_id": publish_id,
                    "session_id": uuid4(),
                    "status": verify_status,
                    "output": f'{{"verdict":"{verdict}"}}',
                },
            )
            migration.upgrade()
            # pi-lens-ignore: python-sql-injection
            migrated = conn.execute(
                text(
                    """
                    SELECT status, stage, cleanup_intent, object_deleted_at
                    FROM publishing.video_publish_tasks WHERE id = :id
                    """
                ),
                {"id": task_id},
            ).one()
            assert migrated == (
                expected_status,
                "done",
                expected_intent,
                None,
            )
        finally:
            transaction.rollback()


@pytest.mark.parametrize(
    "terminal_status", ["succeeded", "failed", "needs_review", "cancelled"]
)
def test_0058_clears_stale_publish_lease_from_every_terminal_row(
    db_engine, terminal_status: str
) -> None:
    migration = _load_migration()
    with db_engine.connect() as conn:
        transaction = conn.begin()
        try:
            migration.__dict__["op"] = Operations(MigrationContext.configure(conn))
            migration.downgrade()
            # pi-lens-ignore: python-sql-injection
            task_id = conn.scalar(
                text("""
                INSERT INTO publishing.video_publish_tasks (
                    client_request_id, caption, original_filename, content_type,
                    size_bytes, object_bucket, object_key, status, stage,
                    target_device_serial, target_app_package, lease_owner,
                    lease_expires_at, heartbeat_at
                ) VALUES (
                    :request_id, 'TEST', 'TEST.mp4', 'video/mp4', 4,
                    'tiktok-video', :object_key, :terminal_status, 'done',
                    'TEST_device', 'com.tiktok', 'stale-publish-worker',
                    now() + interval '1 hour', now()
                ) RETURNING id
                """),
                {
                    "request_id": uuid4(),
                    "object_key": f"TEST/stale-{uuid4()}.mp4",
                    "terminal_status": terminal_status,
                },
            )
            migration.upgrade()
            # pi-lens-ignore: python-sql-injection
            lease = conn.execute(
                text("""
                SELECT lease_owner, lease_expires_at, heartbeat_at
                FROM publishing.video_publish_tasks WHERE id=:id
                """),
                {"id": task_id},
            ).one()
            assert lease == (None, None, None)
        finally:
            transaction.rollback()


def test_0058_downgrade_refuses_populated_table(db_engine) -> None:
    migration = _load_migration()
    with db_engine.connect() as conn:
        transaction = conn.begin()
        try:
            migration.__dict__["op"] = Operations(MigrationContext.configure(conn))
            _insert_legacy_task(
                conn,
                stage="queued",
                device="not_started",
                spool="not_started",
                obj="not_started",
            )
            with pytest.raises(Exception, match="downgrade refused"):
                migration.downgrade()
        finally:
            transaction.rollback()


def test_0058_roundtrip_preserves_backfilled_business_state(db_engine) -> None:
    migration = _load_migration()
    with db_engine.connect() as conn:
        transaction = conn.begin()
        try:
            migration.__dict__["op"] = Operations(MigrationContext.configure(conn))
            migration.downgrade()
            task_id = _insert_legacy_task(
                conn,
                stage="queued",
                device="failed",
                spool="not_started",
                obj="not_started",
            )
            migration.upgrade()
            # pi-lens-ignore: python-sql-injection
            migrated = conn.execute(
                text(
                    "SELECT status, stage, cleanup_intent FROM publishing.video_publish_tasks WHERE id = :id"
                ),
                {"id": task_id},
            ).one()
            assert migrated == ("pending", "waiting_device", "requeue_publish")
            # pi-lens-ignore: python-sql-injection
            conn.execute(
                text("DELETE FROM publishing.video_publish_tasks WHERE id = :id"),
                {"id": task_id},
            )
            migration.downgrade()
            migration.upgrade()
            count = conn.scalar(
                text("SELECT count(*) FROM publishing.video_publish_tasks")
            )
            assert count == 0
        finally:
            transaction.rollback()
