"""Persist automatic device-cleanup retry scheduling.

Revision ID: 0054_video_publish_cleanup_retry
Revises: 0053_video_publish
"""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import text

from alembic import op  # pyright: ignore[reportAttributeAccessIssue]

revision: str = "0054_video_publish_cleanup_retry"
down_revision: str | None = "0053_video_publish"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # pi-lens-ignore: python-sql-injection
    op.execute(
        text(
            "ALTER TABLE publishing.video_publish_tasks "
            "ADD COLUMN device_cleanup_attempts INTEGER NOT NULL DEFAULT 0, "
            "ADD COLUMN device_cleanup_next_attempt_at TIMESTAMPTZ"
        )
    )


def downgrade() -> None:
    # pi-lens-ignore: python-sql-injection
    op.execute(
        text(
            "ALTER TABLE publishing.video_publish_tasks "
            "DROP COLUMN device_cleanup_next_attempt_at, "
            "DROP COLUMN device_cleanup_attempts"
        )
    )
