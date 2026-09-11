"""rename schema chrome_sync → plugin

Revision ID: 0023_chrome_sync_to_plugin
Revises: 0022_shops_data_source
Create Date: 2026-09-11

背景（用户拍板 2026-09-11，PLUGIN_ARCH_CLEANUP lane 2）：

- v2 的两类数据来源要**物理隔离到不同 schema**：
  - **API 同步**（OAuth 授权，sync-worker 经 Open API 拉）→ `commerce.*` /
    `fulfillment.*` / `finance.*` / `after_sales.*`
  - **Chrome 插件 dumps** → `plugin.*`（本迁移后的统一 namespace）
- 原 schema 名 `chrome_sync` 与实现细节（Chrome 扩展）耦合，而实际语义是
  「插件同步的数据」——插件名/形态可变，数据来源语义不变。故改名为 `plugin`。
- 后续 lane 3 会把 `analytics` schema 的 5 张表（ad_daily / ad_today /
  ad_monthly / ad_raw_log / plugin_logs）也 `SET SCHEMA plugin`，让插件数据
  收敛到单一 namespace。

本迁移**只做 DDL（schema 改名），零行级 DML**：

- `ALTER SCHEMA ... RENAME` 是 catalog 改名，7 张表 + 6 个 FK（全部在 schema
  内部，child → ``raw_log``）+ 7 个 IDENTITY 序列 + 索引 + 触发器随之迁移；
  **表数据一行都不动**。
- 依赖顺序：fresh DB 由 `0016_chrome_sync_schema` 建出 `chrome_sync`，
  本迁移再改名为 `plugin`；已存在的库（prod / test 已在 0022）只跑本迁移。
  两条路径收敛到同一终态。
- 与代码同批发布：改名瞬间仍用旧 schema 名的运行进程会报
  ``relation "chrome_sync.xxx" does not exist``，故迁移与
  ``systemctl --user restart`` 需挨着执行。
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op  # pyright: ignore[reportAttributeAccessIssue]

revision: str = "0023_chrome_sync_to_plugin"
down_revision: str | None = "0022_shops_data_source"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 幂等守卫：仅在 chrome_sync 存在且 plugin 尚不存在时改名。
    # （唯一会让本迁移重复执行的场景是人工干预；守卫避免二次执行报
    #   `schema "chrome_sync" does not exist` 而中断整个 upgrade 链。）
    op.execute(
        sa.text(
            """
            DO $$
            BEGIN
                IF EXISTS (SELECT 1 FROM pg_namespace WHERE nspname = 'chrome_sync')
                   AND NOT EXISTS (SELECT 1 FROM pg_namespace WHERE nspname = 'plugin')
                THEN
                    ALTER SCHEMA chrome_sync RENAME TO plugin;
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
                IF EXISTS (SELECT 1 FROM pg_namespace WHERE nspname = 'plugin')
                   AND NOT EXISTS (SELECT 1 FROM pg_namespace WHERE nspname = 'chrome_sync')
                THEN
                    ALTER SCHEMA plugin RENAME TO chrome_sync;
                END IF;
            END $$;
            """
        )
    )
