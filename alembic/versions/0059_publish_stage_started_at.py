"""Persist the current publishing stage start time.

Revision ID: 0059_publish_stage_started_at
Revises: 0058_video_publish_cleanup_owner
"""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import text

from alembic import op  # pyright: ignore[reportAttributeAccessIssue]

revision: str = "0059_publish_stage_started_at"
down_revision: str | None = "0058_video_publish_cleanup_owner"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # pi-lens-ignore: python-sql-injection
    op.execute(
        text(
            "ALTER TABLE publishing.video_publish_tasks "
            "ADD COLUMN stage_started_at TIMESTAMPTZ DEFAULT now()"
        )
    )
    # pi-lens-ignore: python-sql-injection
    op.execute(
        text(
            "UPDATE publishing.video_publish_tasks "
            "SET stage_started_at = COALESCE(started_at, created_at)"
        )
    )
    # pi-lens-ignore: python-sql-injection
    op.execute(
        text(
            "ALTER TABLE publishing.video_publish_tasks "
            "ALTER COLUMN stage_started_at SET NOT NULL"
        )
    )


def downgrade() -> None:
    # Stage timing is state-bearing audit data. Refuse to discard it while any
    # publishing task remains.
    # pi-lens-ignore: python-sql-injection
    op.execute(
        text("""
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM publishing.video_publish_tasks LIMIT 1) THEN
                RAISE EXCEPTION '0059 downgrade refused: publishing.video_publish_tasks is not empty';
            END IF;
        END $$
    """)
    )
    # pi-lens-ignore: python-sql-injection
    op.execute(
        text(
            "ALTER TABLE publishing.video_publish_tasks "
            "DROP COLUMN IF EXISTS stage_started_at"
        )
    )
