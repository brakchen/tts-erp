from __future__ import annotations

import importlib.util
from pathlib import Path
from uuid import uuid4

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import inspect, text

ROOT = Path(__file__).parents[2]


def _load(name: str):
    path = ROOT / "alembic" / "versions" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"test_{name}", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _insert_task(conn) -> int:
    # pi-lens-ignore: python-sql-injection
    return conn.execute(
        text(
            """
            INSERT INTO publishing.video_publish_tasks (
                client_request_id, caption, original_filename, content_type,
                size_bytes, object_bucket, object_key, status, stage,
                target_device_serial, target_app_package
            ) VALUES (
                :request_id, 'TEST_caption', 'TEST_video.mp4', 'video/mp4', 4,
                'tiktok-video', :object_key, 'pending', 'queued',
                'TEST_device', 'com.tiktok'
            ) RETURNING id
            """
        ),
        {"request_id": uuid4(), "object_key": f"TEST/{uuid4()}.mp4"},
    ).scalar_one()


@pytest.mark.parametrize(
    "name",
    [
        "0054_video_publish_cleanup_retry",
        "0055_video_publish_object_cleanup_retry",
        "0056_video_publish_spool_cleanup_retry",
        "0057_video_publish_owner_key",
    ],
)
def test_state_bearing_intermediate_downgrades_refuse_populated_table(
    db_engine, name: str
) -> None:
    migration = _load(name)
    with db_engine.connect() as conn:
        transaction = conn.begin()
        try:
            migration.__dict__["op"] = Operations(MigrationContext.configure(conn))
            _insert_task(conn)
            savepoint = conn.begin_nested()
            with pytest.raises(Exception, match="downgrade refused"):
                migration.downgrade()
            savepoint.rollback()
        finally:
            transaction.rollback()


def test_0061_revision_backfill_and_integrity_metadata(db_engine) -> None:
    migration = _load("0061_publish_safety")
    assert migration.down_revision == "0060_video_publish_invariants"
    with db_engine.connect() as conn:
        transaction = conn.begin()
        try:
            migration.__dict__["op"] = Operations(MigrationContext.configure(conn))
            migration.downgrade()
            task_id = _insert_task(conn)
            # pi-lens-ignore: python-sql-injection
            conn.execute(
                text(
                    """
                    INSERT INTO publishing.video_publish_attempts (
                        task_id, sequence_no, kind, artemis_session_id, status,
                        prompt_version, prompt_snapshot, device_serial,
                        retry_safe, retry_classification
                    ) VALUES
                        (:task_id, 1, 'publish', :session_one, 'rejected',
                         'TEST', 'TEST', 'TEST_device', true, 'device_locked'),
                        (:task_id, 2, 'publish', :session_two, 'failed',
                         'TEST', 'TEST', 'TEST_device', false, 'planner_zero_steps')
                    """
                ),
                {
                    "task_id": task_id,
                    "session_one": uuid4(),
                    "session_two": uuid4(),
                },
            )
            migration.upgrade()
            # pi-lens-ignore: python-sql-injection
            counts = conn.execute(
                text(
                    "SELECT attempt_count, publish_budget_used "
                    "FROM publishing.video_publish_tasks WHERE id=:id"
                ),
                {"id": task_id},
            ).one()
            assert counts == (2, 1)
            inspector = inspect(conn)
            task_checks = {
                row["name"]
                for row in inspector.get_check_constraints(
                    "video_publish_tasks", schema="publishing"
                )
            }
            heartbeat_checks = {
                row["name"]
                for row in inspector.get_check_constraints(
                    "worker_heartbeats", schema="publishing"
                )
            }
            columns = {
                row["name"]
                for row in inspector.get_columns(
                    "worker_heartbeats", schema="publishing"
                )
            }
            # pi-lens-ignore: python-sql-injection
            trigger_count = conn.execute(
                text(
                    "SELECT count(*) FROM pg_trigger "
                    "WHERE tgname='trg_video_publish_attempt_related' "
                    "AND NOT tgisinternal"
                )
            ).scalar_one()
            assert "video_publish_task_budget_check" in task_checks
            assert "worker_heartbeat_device_status_check" in heartbeat_checks
            assert {"device_status", "device_message"} <= columns
            assert trigger_count == 1
        finally:
            transaction.rollback()


def test_0061_downgrade_refuses_state_bearing_rows(db_engine) -> None:
    migration = _load("0061_publish_safety")
    with db_engine.connect() as conn:
        transaction = conn.begin()
        try:
            migration.__dict__["op"] = Operations(MigrationContext.configure(conn))
            _insert_task(conn)
            savepoint = conn.begin_nested()
            with pytest.raises(Exception, match="0061 downgrade refused"):
                migration.downgrade()
            savepoint.rollback()
        finally:
            transaction.rollback()


def test_0060_revision_and_constraints_round_trip(db_engine) -> None:
    migration = _load("0060_video_publish_invariants")
    assert migration.down_revision == "0059_publish_stage_started_at"
    with db_engine.connect() as conn:
        transaction = conn.begin()
        try:
            migration.__dict__["op"] = Operations(MigrationContext.configure(conn))
            migration.downgrade()
            migration.upgrade()
            inspector = inspect(conn)
            checks = {
                row["name"]
                for row in inspector.get_check_constraints(
                    "video_publish_attempts", schema="publishing"
                )
            }
            task_checks = {
                row["name"]
                for row in inspector.get_check_constraints(
                    "video_publish_tasks", schema="publishing"
                )
            }
            indexes = {
                row["name"]
                for row in inspector.get_indexes(
                    "video_publish_attempts", schema="publishing"
                )
            }
            assert "video_publish_attempt_related_check" in checks
            assert "video_publish_task_cleanup_status_check" in task_checks
            assert "ix_video_publish_attempt_task_seq" in indexes
        finally:
            transaction.rollback()
