"""Merge SPU price statistics and video publishing migration chains.

Revision ID: 0067_merge_price_and_publish
Revises: 0066_publish_execution_fences, 0054_tiktok_price_obs
Create Date: 2026-10-06

This is a merge migration joining two independent chains:
- 0052 -> 0053 -> ... -> 0066 (video publishing)
- 0052 -> 0054_tiktok_price_obs (SPU price statistics)
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "0067_merge_price_and_publish"
down_revision: tuple[str, ...] = ("0066_publish_execution_fences", "0054_tiktok_price_obs")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Merge migration — no schema changes, just joining two chains."""
    pass


def downgrade() -> None:
    """Merge migration — downgrade is a no-op."""
    pass
