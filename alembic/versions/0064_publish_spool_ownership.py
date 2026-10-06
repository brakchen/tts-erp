"""Persist spool ownership before download side effects.

Revision ID: 0064_publish_spool_ownership
Revises: 0063_publish_authz
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy import text

from alembic import op

revision = "0064_publish_spool_ownership"
down_revision = "0063_publish_authz"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "video_publish_tasks",
        sa.Column("spool_path", sa.Text(), nullable=True),
        schema="publishing",
    )


def downgrade() -> None:
    # Refuse to discard persisted side-effect ownership while any row uses it.
    # pi-lens-ignore: python-sql-injection
    op.execute(
        text("""
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1
                FROM publishing.video_publish_tasks
                WHERE spool_path IS NOT NULL
                LIMIT 1
            ) THEN
                RAISE EXCEPTION '0064 downgrade refused: spool ownership exists';
            END IF;
        END $$
        """)
    )
    op.drop_column("video_publish_tasks", "spool_path", schema="publishing")
