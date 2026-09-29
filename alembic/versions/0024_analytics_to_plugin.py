"""move analytics.* tables into plugin schema

Revision ID: 0024_analytics_to_plugin
Revises: 0023_chrome_sync_to_plugin
Create Date: 2026-09-11

背景（用户拍板 2026-09-11，PLUGIN_ARCH_CLEANUP lane 3）：

- lane 2 已把订单/物流/结算 dump 的 schema 从 ``chrome_sync`` 改名为 ``plugin``。
  本迁移把**广告消耗 + 插件日志**的 5 张表也搬进同一个 ``plugin`` schema，
  让「Chrome 插件 dump」数据收敛到单一 namespace，然后删掉 ``analytics`` schema。
- 与代码同批发布：`tts_erp_v2/analytics/{domain,repository}.py` →
  `tts_erp_v2/plugin/ads/`；模型 `db/models/analytics.py` 并入
  `db/models/plugin.py`；job `analytics.solidify` → `plugin.ad_merge_today2daily`。
- 读侧 ``spu_roi.py`` 留在 `tts_erp_v2/analytics/`，只改 SQL 里的 schema 名。

本迁移**只做 DDL（表换 namespace），零行级 DML**：

- ``ALTER TABLE ... SET SCHEMA`` 是 catalog 变更 —— 表数据一行不动；索引、
  约束、IDENTITY 序列（``deptype='i'``，随表自动迁移）、触发器一并跟随。
- 仅当表仍在 ``analytics`` 且 ``plugin`` 中尚不存在时才搬（幂等守卫）。
- ``DROP SCHEMA analytics`` 只在 schema 已无任何表时执行 —— 已确认该 schema
  内除这 5 张表外没有视图/函数/独立序列。
- 依赖：``plugin`` schema 由 ``0023_chrome_sync_to_plugin`` 建立，故本迁移
  必须在其之后执行。
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op  # pyright: ignore[reportAttributeAccessIssue]

revision: str = "0024_analytics_to_plugin"
down_revision: str | None = "0023_chrome_sync_to_plugin"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# 搬迁顺序无关（表之间无 FK）；显式列出便于审计。
_TABLES = ("ad_today", "ad_daily", "ad_monthly", "ad_raw_log", "plugin_logs")


def upgrade() -> None:
    op.execute(
        sa.text(
            """
            DO $$
            DECLARE
                t text;
            BEGIN
                FOREACH t IN ARRAY ARRAY[
                    'ad_today', 'ad_daily', 'ad_monthly',
                    'ad_raw_log', 'plugin_logs'
                ]
                LOOP
                    IF EXISTS (SELECT 1 FROM information_schema.tables
                               WHERE table_schema = 'analytics' AND table_name = t)
                       AND NOT EXISTS (SELECT 1 FROM information_schema.tables
                                       WHERE table_schema = 'plugin' AND table_name = t)
                    THEN
                        EXECUTE format('ALTER TABLE analytics.%I SET SCHEMA plugin', t);
                    END IF;
                END LOOP;

                -- 仅当 analytics 里已无表时删 schema（防误删遗留对象）
                IF EXISTS (SELECT 1 FROM pg_namespace WHERE nspname = 'analytics')
                   AND NOT EXISTS (SELECT 1 FROM information_schema.tables
                                   WHERE table_schema = 'analytics')
                THEN
                    DROP SCHEMA analytics;
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
            DECLARE
                t text;
            BEGIN
                IF NOT EXISTS (SELECT 1 FROM pg_namespace WHERE nspname = 'analytics') THEN
                    CREATE SCHEMA analytics;
                END IF;

                FOREACH t IN ARRAY ARRAY[
                    'ad_today', 'ad_daily', 'ad_monthly',
                    'ad_raw_log', 'plugin_logs'
                ]
                LOOP
                    IF EXISTS (SELECT 1 FROM information_schema.tables
                               WHERE table_schema = 'plugin' AND table_name = t)
                    THEN
                        EXECUTE format('ALTER TABLE plugin.%I SET SCHEMA analytics', t);
                    END IF;
                END LOOP;
            END $$;
            """
        )
    )
