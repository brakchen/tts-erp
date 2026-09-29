"""drop commerce.shops.data_source

Revision ID: 0025_drop_shops_data_source
Revises: 0024_analytics_to_plugin
Create Date: 2026-09-11

背景（用户拍板 2026-09-11，PLUGIN_ARCH_CLEANUP lane 4）：

- `data_source` 是 migration 0022 引入的「数据来源枚举」（'api' | 'plugin'）。
  它的信息**完全等价于** `credential_id IS NOT NULL` —— 0022 自己的回填规则
  就是 `CASE WHEN credential_id IS NOT NULL THEN 'api' ELSE 'plugin' END`，
  之后 OAuth callback 也只在写 `credential_id` 的同时翻该列。即它是一个
  可完全推导的冗余列。
- 更关键的是：这个列曾用于 `shop_is_api_managed()` 守卫，把
  `data_source='api'` 店铺的插件 dumps **全域静默吞掉**。但**广告 dump 没有
  server-side 替代路径**（JOBS 里无 ad job），守卫把唯一数据来源挡掉了。
- lane 2/3 已把插件数据收敛到 `plugin` schema，与 api 同步数据
  （`commerce.*` / `fulfillment.*` / `finance.*` / `after_sales.*`）
  **按 schema 物理隔离** —— 不再需要「来源判定」来决定拦不拦。

本迁移**只做 DDL（删约束 + 删列），零行级 DML**：

- `ALTER TABLE ... DROP COLUMN` 不读写任何行数据；仅丢失该列的值，
  而该值可由 `credential_id` 完整重建（见 downgrade）。
- 幂等守卫：仅在列存在时执行。

downgrade 需把该列重建出来，因此**必然包含一条 UPDATE**（`credential_id`
非空 → 'api'，否则 → 'plugin'）—— 这是 0022 原始回填公式的复现，属于
纯再推导，不引入新信息、不改动其它列。
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op  # pyright: ignore[reportAttributeAccessIssue]

revision: str = "0025_drop_shops_data_source"
down_revision: str | None = "0024_analytics_to_plugin"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        sa.text(
            """
            DO $$
            BEGIN
                IF EXISTS (SELECT 1 FROM information_schema.columns
                           WHERE table_schema = 'commerce'
                             AND table_name = 'shops'
                             AND column_name = 'data_source')
                THEN
                    ALTER TABLE commerce.shops
                        DROP CONSTRAINT IF EXISTS ck_channel_accounts_data_source;
                    ALTER TABLE commerce.shops DROP COLUMN data_source;
                END IF;
            END $$;
            """
        )
    )


def downgrade() -> None:
    op.execute(
        sa.text(
            """
            DO $$
            BEGIN
                IF NOT EXISTS (SELECT 1 FROM information_schema.columns
                               WHERE table_schema = 'commerce'
                                 AND table_name = 'shops'
                                 AND column_name = 'data_source')
                THEN
                    ALTER TABLE commerce.shops ADD COLUMN data_source text;
                    -- 复现 0022 的回填公式（有 credential = API 同步店铺）
                    UPDATE commerce.shops
                       SET data_source = CASE
                             WHEN credential_id IS NOT NULL THEN 'api'
                             ELSE 'plugin'
                           END;
                    ALTER TABLE commerce.shops
                        ALTER COLUMN data_source SET NOT NULL;
                    ALTER TABLE commerce.shops
                        ADD CONSTRAINT ck_channel_accounts_data_source
                        CHECK (data_source IN ('api', 'plugin'));
                END IF;
            END $$;
            """
        )
    )
