"""0069 per-device mutex + manual confirmation audit migration guard."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any, cast
from uuid import uuid4

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError

pytestmark = [pytest.mark.domain_publishing]

ROOT = Path(__file__).parents[2]

MIGRATION = ROOT / "alembic/versions/0069_video_publish_confirmation.py"

NEW_TASK_COLUMNS = frozenset(
    {
        "prompt_override",
        "params_locked_at",
        "confirmed_at",
        "confirmed_by_user_id",
        "confirmed_by_key_hash",
        "cancel_requested",
        "last_verify_verdict",
    }
)


def _load_migration():
    spec = importlib.util.spec_from_file_location("migration_0069_test", MIGRATION)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _columns(conn, table: str) -> set[str]:
    return {
        row["name"] for row in inspect(conn).get_columns(table, schema="publishing")
    }


def _indexes(conn, table: str) -> dict[str, dict]:
    return {
        row["name"]: row
        for row in inspect(conn).get_indexes(table, schema="publishing")
    }


def _insert_task(conn, device_serial: str, *, status: str = "pending") -> int:
    running = status == "running"
    stage = "downloading" if running else "queued"
    # pi-lens-ignore: python-sql-injection
    return conn.execute(
        text(
            """
            INSERT INTO publishing.video_publish_tasks (
                client_request_id, caption, original_filename, content_type,
                size_bytes, object_bucket, object_key, status, stage,
                target_device_serial, target_app_package, lease_owner
            ) VALUES (
                :client_request_id, 'TEST_caption', 'TEST_video.mp4',
                'video/mp4', 4, 'tiktok-video', :object_key, :status, :stage,
                :device_serial, 'com.tiktok', :lease_owner
            ) RETURNING id
            """
        ),
        {
            "client_request_id": uuid4(),
            "object_key": f"TEST/confirm-{uuid4()}.mp4",
            "status": status,
            "stage": stage,
            "device_serial": device_serial,
            "lease_owner": "TEST_worker" if running else None,
        },
    ).scalar_one()


def _insert_confirmation(
    conn,
    task_id: int,
    request_id,
    *,
    action: str = "confirm_published",
    to_status: str = "succeeded",
) -> None:
    # pi-lens-ignore: python-sql-injection
    conn.execute(
        text(
            """
            INSERT INTO publishing.video_publish_confirmations (
                task_id, action, from_status, to_status, operator_user_id,
                operator_key_hash, request_id
            ) VALUES (
                :task_id, :action, 'needs_review', :to_status, 4242,
                'TEST_key_hash', :request_id
            )
            """
        ),
        {
            "task_id": task_id,
            "action": action,
            "to_status": to_status,
            "request_id": request_id,
        },
    )


def test_0069_revision_chain_and_model_metadata_match_migration() -> None:
    migration = _load_migration()
    assert migration.revision == "0069_video_publish_confirmation"
    assert migration.down_revision == "0068_spu_deterioration_alert"

    from tts_erp_v2.db.models.publishing import (
        VideoPublishConfirmation,
        VideoPublishTask,
    )

    task_table = cast(Any, VideoPublishTask.__table__)
    assert NEW_TASK_COLUMNS <= {column.name for column in task_table.columns}
    task_indexes = {index.name: index for index in task_table.indexes}
    assert "uq_video_publish_one_running" not in task_indexes
    per_device = task_indexes["uq_video_publish_one_running_per_device"]
    assert per_device.unique
    assert [str(column) for column in per_device.columns] == [
        "video_publish_tasks.target_device_serial"
    ]
    assert (
        str(per_device.dialect_options["postgresql"]["where"]) == "status = 'running'"
    )
    assert task_table.c.cancel_requested.server_default is not None
    assert task_table.c.cancel_requested.nullable is False

    confirmation_table = cast(Any, VideoPublishConfirmation.__table__)
    assert confirmation_table.schema == "publishing"
    assert {column.name for column in confirmation_table.columns} == {
        "id",
        "task_id",
        "action",
        "from_status",
        "to_status",
        "operator_user_id",
        "operator_key_hash",
        "request_id",
        "created_at",
    }
    assert {
        constraint.name
        for constraint in confirmation_table.constraints
        if constraint.__class__.__name__ == "UniqueConstraint"
    } == {"uq_video_publish_confirmation_request_id"}
    (task_fk,) = confirmation_table.foreign_keys
    assert task_fk.column.table.schema == "publishing"
    assert task_fk.column.table.name == "video_publish_tasks"
    assert task_fk.column.name == "id"
    assert task_fk.ondelete == "RESTRICT"


def test_0069_upgrade_creates_confirmation_audit_and_per_device_mutex(
    db_engine,
) -> None:
    migration = _load_migration()
    with db_engine.connect() as conn:
        transaction = conn.begin()
        try:
            migration.__dict__["op"] = Operations(MigrationContext.configure(conn))
            migration.downgrade()
            assert "video_publish_confirmations" not in inspect(conn).get_table_names(
                schema="publishing"
            )
            assert not NEW_TASK_COLUMNS & _columns(conn, "video_publish_tasks")
            assert "uq_video_publish_one_running" in _indexes(
                conn, "video_publish_tasks"
            )

            migration.upgrade()
            assert "video_publish_confirmations" in inspect(conn).get_table_names(
                schema="publishing"
            )
            assert NEW_TASK_COLUMNS <= _columns(conn, "video_publish_tasks")
            task_indexes = _indexes(conn, "video_publish_tasks")
            assert "uq_video_publish_one_running" not in task_indexes
            per_device = task_indexes["uq_video_publish_one_running_per_device"]
            assert per_device["unique"] and per_device["column_names"] == [
                "target_device_serial"
            ]
            assert "status = 'running'" in str(
                per_device.get("dialect_options", {}).get("postgresql_where")
            )
            confirm_indexes = _indexes(conn, "video_publish_confirmations")
            assert confirm_indexes["uq_video_publish_confirm_once"]["unique"]
            assert "action = 'confirm_published'" in str(
                confirm_indexes["uq_video_publish_confirm_once"]
                .get("dialect_options", {})
                .get("postgresql_where")
            )
            foreign_keys = inspect(conn).get_foreign_keys(
                "video_publish_confirmations", schema="publishing"
            )
            assert len(foreign_keys) == 1
            assert foreign_keys[0]["options"]["ondelete"] == "RESTRICT"

            # One confirmation per task is the audit invariant, replay of the
            # same idempotency key included.
            task_id = _insert_task(conn, "TEST_device_confirm")
            request_id = uuid4()
            _insert_confirmation(conn, task_id, request_id)
            savepoint = conn.begin_nested()
            with pytest.raises(IntegrityError, match="uq_video_publish_confirm_once"):
                _insert_confirmation(conn, task_id, uuid4())
            savepoint.rollback()
            other_task_id = _insert_task(conn, "TEST_device_confirm_replay")
            savepoint = conn.begin_nested()
            with pytest.raises(
                IntegrityError, match="uq_video_publish_confirmation_request_id"
            ):
                _insert_confirmation(conn, other_task_id, request_id)
            savepoint.rollback()

            # One running slot per device, not per system.
            running_id = _insert_task(conn, "TEST_device_running_a", status="running")
            savepoint = conn.begin_nested()
            with pytest.raises(
                IntegrityError, match="uq_video_publish_one_running_per_device"
            ):
                _insert_task(conn, "TEST_device_running_a", status="running")
            savepoint.rollback()
            _insert_task(conn, "TEST_device_running_b", status="running")
            # pi-lens-ignore: python-sql-injection
            conn.execute(
                text(
                    "UPDATE publishing.video_publish_tasks SET cancel_requested=true "
                    "WHERE id=:id"
                ),
                {"id": running_id},
            )
            assert conn.scalar(
                text(
                    "SELECT cancel_requested FROM publishing.video_publish_tasks "
                    "WHERE id=:id"
                ),
                {"id": running_id},
            )
        finally:
            transaction.rollback()


def test_0069_downgrade_refuses_audit_rows_and_concurrency_then_reverses(
    db_engine,
) -> None:
    migration = _load_migration()
    with db_engine.connect() as conn:
        transaction = conn.begin()
        try:
            migration.__dict__["op"] = Operations(MigrationContext.configure(conn))
            migration.downgrade()
            assert not NEW_TASK_COLUMNS & _columns(conn, "video_publish_tasks")
            assert "uq_video_publish_one_running" in _indexes(
                conn, "video_publish_tasks"
            )
            migration.upgrade()

            task_id = _insert_task(conn, "TEST_device_guard")
            _insert_confirmation(conn, task_id, uuid4())
            savepoint = conn.begin_nested()
            with pytest.raises(Exception, match="confirmation audit rows exist"):
                migration.downgrade()
            savepoint.rollback()

            _insert_task(conn, "TEST_device_guard_a", status="running")
            _insert_task(conn, "TEST_device_guard_b", status="running")
            savepoint = conn.begin_nested()
            with pytest.raises(Exception, match="drain concurrent running tasks"):
                migration.downgrade()
            savepoint.rollback()

            # pi-lens-ignore: python-sql-injection
            conn.execute(
                text(
                    "DELETE FROM publishing.video_publish_confirmations "
                    "WHERE task_id=:task_id"
                ),
                {"task_id": task_id},
            )
            # pi-lens-ignore: python-sql-injection
            conn.execute(
                text(
                    "DELETE FROM publishing.video_publish_tasks "
                    "WHERE target_device_serial LIKE 'TEST_device_guard%'"
                )
            )
            migration.downgrade()
            assert "video_publish_confirmations" not in inspect(conn).get_table_names(
                schema="publishing"
            )
            assert not NEW_TASK_COLUMNS & _columns(conn, "video_publish_tasks")
            task_indexes = _indexes(conn, "video_publish_tasks")
            assert "uq_video_publish_one_running_per_device" not in task_indexes
            assert task_indexes["uq_video_publish_one_running"]["unique"]
            migration.upgrade()
            assert NEW_TASK_COLUMNS <= _columns(conn, "video_publish_tasks")
        finally:
            transaction.rollback()
