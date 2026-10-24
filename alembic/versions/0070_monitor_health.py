"""Add the monitor's single current-state table.

Revision ID: 0070_monitor_health
Revises: 0069_video_publish_confirmation
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op  # pyright: ignore[reportAttributeAccessIssue]

revision: str = "0070_monitor_health"
down_revision: str | None = "0069_video_publish_confirmation"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "monitor_health",
        sa.Column("shop_id", sa.String(128), primary_key=True),
        sa.Column("shop_name", sa.String(200), nullable=False),
        sa.Column("state", postgresql.JSONB(), nullable=False),
        schema="plugin",
    )


def downgrade() -> None:
    # Current notice acknowledgements must survive rollback/restart too.
    raise RuntimeError("0070 downgrade refused: preserve monitor health state")
