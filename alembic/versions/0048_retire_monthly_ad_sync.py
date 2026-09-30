"""Retire monthly advertising dump and coverage support.

Revision ID: 0048_retire_monthly_ad_sync
Revises: 0047_miaoshou_purchase_price
"""

from __future__ import annotations
from collections.abc import Sequence
from alembic import op  # pyright: ignore[reportAttributeAccessIssue]

revision: str = "0048_retire_monthly_ad_sync"
down_revision: str | None = "0047_miaoshou_purchase_price"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("DELETE FROM plugin.ad_raw_log WHERE kind = 'monthly'")
    op.execute(
        "ALTER TABLE plugin.ad_raw_log DROP CONSTRAINT IF EXISTS ck_ad_raw_log_kind"
    )
    op.execute("ALTER TABLE plugin.ad_raw_log DROP COLUMN IF EXISTS year_month")
    op.execute(
        "ALTER TABLE plugin.ad_raw_log ADD CONSTRAINT ck_ad_raw_log_kind CHECK (kind IN ('daily', 'today'))"
    )


def downgrade() -> None:
    raise RuntimeError(
        "0048 retires monthly advertising data; restore from backup instead of downgrading"
    )
