"""Persist automatic spool-cleanup retry scheduling.

Revision ID: 0056_spool_cleanup_retry
Revises: 0055_object_cleanup_retry
"""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import text

from alembic import op  # pyright: ignore[reportAttributeAccessIssue]

revision: str = "0056_spool_cleanup_retry"
down_revision: str | None = "0055_object_cleanup_retry"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # pi-lens-ignore: python-sql-injection
    op.execute(
        text(
            "ALTER TABLE publishing.video_publish_tasks "
            "ADD COLUMN spool_cleanup_attempts INTEGER NOT NULL DEFAULT 0, "
            "ADD COLUMN spool_cleanup_next_attempt_at TIMESTAMPTZ"
        )
    )


def downgrade() -> None:
    # pi-lens-ignore: python-sql-injection
    op.execute(
        text("""
        DO $$ BEGIN
            IF EXISTS (SELECT 1 FROM publishing.video_publish_tasks LIMIT 1) THEN
                RAISE EXCEPTION '0056 downgrade refused: publishing.video_publish_tasks is not empty';
            END IF;
        END $$
        """)
    )
    # pi-lens-ignore: python-sql-injection
    op.execute(
        text(
            "ALTER TABLE publishing.video_publish_tasks "
            "DROP COLUMN spool_cleanup_next_attempt_at, "
            "DROP COLUMN spool_cleanup_attempts"
        )
    )
