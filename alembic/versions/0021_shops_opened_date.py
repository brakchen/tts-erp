"""add commerce.shops.opened_date — 开店时间（天级）

Revision ID: 0021_shops_opened_date
Revises: 0020_drop_v3_analytics_leftovers
Create Date: 2026-09-11

背景（feature/shop-registration lane）：

- 店铺人工注册页需要记录「开店时间」，精确到天即可，不要求时分秒。
- 列可空：存量店铺（OAuth 授权建行的）未知开店时间，保持 NULL。
- 写入路径：`POST /v2/admin/shops/register`（人工注册/补录）；
  OAuth callback 的 upsert ``set_`` **不含**本列，不覆盖人工填的值。
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op  # pyright: ignore[reportAttributeAccessIssue]

revision: str = "0021_shops_opened_date"
down_revision: str | None = "0020_drop_v3_analytics_leftovers"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "shops",
        sa.Column("opened_date", sa.Date(), nullable=True),
        schema="commerce",
    )


def downgrade() -> None:
    op.drop_column("shops", "opened_date", schema="commerce")
