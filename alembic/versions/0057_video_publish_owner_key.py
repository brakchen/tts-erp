"""Track API-key ownership for video-publish idempotency.

Revision ID: 0057_publish_owner_key
Revises: 0056_spool_cleanup_retry
"""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import text

from alembic import op  # pyright: ignore[reportAttributeAccessIssue]

revision: str = "0057_publish_owner_key"
down_revision: str | None = "0056_spool_cleanup_retry"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # pi-lens-ignore: python-sql-injection
    op.execute(
        text(
            "ALTER TABLE publishing.video_publish_tasks "
            "ADD COLUMN created_by_key_hash TEXT"
        )
    )


def downgrade() -> None:
    # pi-lens-ignore: python-sql-injection
    op.execute(
        text(
            "ALTER TABLE publishing.video_publish_tasks DROP COLUMN created_by_key_hash"
        )
    )
