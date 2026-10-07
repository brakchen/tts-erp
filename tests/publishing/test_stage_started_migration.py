from __future__ import annotations

import importlib.util
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import text

pytestmark = [pytest.mark.domain_publishing]

MIGRATION = (
    Path(__file__).parents[2] / "alembic/versions/0059_publish_stage_started_at.py"
)


def _load_migration():
    spec = importlib.util.spec_from_file_location("migration_0059_test", MIGRATION)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_0059_revision_chain_and_seeded_upgrade_downgrade(db_engine) -> None:
    migration = _load_migration()
    assert migration.revision == "0059_publish_stage_started_at"
    assert migration.down_revision == "0058_video_publish_cleanup_owner"

    with db_engine.connect() as conn:
        transaction = conn.begin()
        try:
            migration.__dict__["op"] = Operations(MigrationContext.configure(conn))
            migration.downgrade()
            started_at = datetime(2026, 10, 5, 1, 2, 3, tzinfo=UTC)
            # pi-lens-ignore: python-sql-injection
            task_id = conn.execute(
                text(
                    """
                    INSERT INTO publishing.video_publish_tasks (
                        client_request_id, caption, original_filename, content_type,
                        size_bytes, object_bucket, object_key, status, stage,
                        target_device_serial, target_app_package, started_at
                    ) VALUES (
                        :client_request_id, 'TEST_caption', 'TEST_video.mp4',
                        'video/mp4', 4, 'tiktok-video', :object_key, 'running',
                        'downloading', 'TEST_device', 'com.tiktok', :started_at
                    ) RETURNING id
                    """
                ),
                {
                    "client_request_id": uuid4(),
                    "object_key": f"TEST/stage-started-{uuid4()}.mp4",
                    "started_at": started_at,
                },
            ).scalar_one()
            migration.upgrade()
            # pi-lens-ignore: python-sql-injection
            actual = conn.scalar(
                text(
                    "SELECT stage_started_at FROM publishing.video_publish_tasks "
                    "WHERE id = :id"
                ),
                {"id": task_id},
            )
            assert actual == started_at
            savepoint = conn.begin_nested()
            with pytest.raises(Exception, match="downgrade refused"):
                migration.downgrade()
            savepoint.rollback()
            # pi-lens-ignore: python-sql-injection
            conn.execute(
                text("DELETE FROM publishing.video_publish_tasks WHERE id = :id"),
                {"id": task_id},
            )
            migration.downgrade()
            migration.upgrade()
        finally:
            transaction.rollback()
