"""add commerce.shops.data_source — 店铺数据来源枚举（api / plugin）

Revision ID: 0022_shops_data_source
Revises: 0021_shops_opened_date
Create Date: 2026-09-11

背景（feature/shop-registration 后续，用户拍板 2026-09-11）：

- 店铺有两种同步方式：API 同步（OAuth 授权，有 credential）与 Chrome
  插件同步（无 credential）。此前靠 ``credential_id IS NULL`` 隐式推导，
  改为显式枚举列 ``data_source``：'api' | 'plugin'。
- 回填规则：credential_id 非空 → 'api'，否则 → 'plugin'。
- 写入方：OAuth callback（'api'，含插件店升级翻转）、
  POST /v2/admin/shops/register（'plugin'）。
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op  # pyright: ignore[reportAttributeAccessIssue]

revision: str = "0022_shops_data_source"
down_revision: str | None = "0021_shops_opened_date"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "shops",
        sa.Column("data_source", sa.Text(), nullable=True),
        schema="commerce",
    )
    # 回填存量：有 credential = API 同步店铺，无 = 插件店铺
    op.execute(
        "UPDATE commerce.shops SET data_source = "
        "CASE WHEN credential_id IS NOT NULL THEN 'api' ELSE 'plugin' END"
    )
    op.create_check_constraint(
        "ck_channel_accounts_data_source",
        "shops",
        "data_source IN ('api', 'plugin')",
        schema="commerce",
    )
    op.alter_column("shops", "data_source", nullable=False, schema="commerce")


def downgrade() -> None:
    op.drop_constraint(
        "ck_channel_accounts_data_source", "shops", schema="commerce"
    )
    op.drop_column("shops", "data_source", schema="commerce")
