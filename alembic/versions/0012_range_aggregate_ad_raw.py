"""analytics range-aggregate ad_raw — kind/day_start/day_end（Design A，方案 0012）

Revision ID: 0012_range_aggregate_ad_raw
Revises: 0011_oauth_states
Create Date: 2026-09-07

执行依据: tech-doc/analytics/range-aggregate-history-sync.md §4（Design A，字段级
模型已确认）。决策记录（context-mode `analytics-range-aggregate-decisions`）：

1. `day` 单字段不再表达区间语义 → 拆为区间 `[day_start .. day_end]`：
   - kind='history' 历史整段快照 [S..T-1]（每 (scope,endpoint,campaign) 1 行，原地推进）
   - kind='today'   今日快照 [T..T]（每 (scope,endpoint,campaign) 1 行，30s 原地覆盖）
   - kind='daily'   legacy 逐日行（存量迁移 + 旧 v2 插件兼容写入；首个覆盖它们的
     v3 history 写入时同事务折叠删除）
2. 唯一键改为 **partial unique index**（live 按 (scope,endpoint,campaign,kind) 唯一、
   daily 按旧 5 元组含日唯一）——两套唯一语义并存，迁移期间旧约束删除前新索引先建好。
3. 保留 protocol_version/schema_version 现有默认值；新客户端显式带上。
4. (review P2a) kind='today' 必须是单日区间：DB 层 CHECK
   (kind <> 'today' OR day_start = day_end)，API 层同步校验。

⚠️ 与 reorg-plan 红线一致：live 行只 upsert 不删（has_data_cache 无 stale-true 论证
load-bearing）；物理删除只允许发生在 kind='daily' legacy 折叠（repository 单事务内）。
"""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import text

from alembic import op  # pyright: ignore[reportAttributeAccessIssue]

revision: str = "0012_range_aggregate_ad_raw"
down_revision: str | None = "0011_oauth_states"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 1. 加列（可空起步，锁表窗口小）。注意：day_end 不在此处新增——
    #    目标列由既有 day 列改名而来（见第 3 步），避免重复列名。
    op.execute(
        text(
            """
            ALTER TABLE analytics.ad_raw
                ADD COLUMN kind TEXT
            """
        )
    )
    op.execute(
        text(
            """
            ALTER TABLE analytics.ad_raw
                ADD COLUMN day_start DATE
            """
        )
    )

    # 2. 存量逐日行回填 → kind='daily', day_start=day（day_end 由 day 改名承接）
    op.execute(
        text(
            """
            UPDATE analytics.ad_raw
               SET kind = 'daily',
                   day_start = day
             WHERE kind IS NULL
            """
        )
    )

    # 3. day → day_end（语义改名：day = 区间末日）
    op.execute(text("ALTER TABLE analytics.ad_raw RENAME COLUMN day TO day_end"))

    # 4. 约束
    op.execute(
        text(
            """
            ALTER TABLE analytics.ad_raw
                ALTER COLUMN kind SET NOT NULL
            """
        )
    )
    op.execute(
        text(
            """
            ALTER TABLE analytics.ad_raw
                ALTER COLUMN day_start SET NOT NULL
            """
        )
    )
    op.execute(
        text(
            """
            ALTER TABLE analytics.ad_raw
                ALTER COLUMN day_end SET NOT NULL
            """
        )
    )
    op.execute(
        text(
            """
            ALTER TABLE analytics.ad_raw
                ADD CONSTRAINT ck_analytics_raw_kind
                CHECK (kind IN ('history', 'today', 'daily'))
            """
        )
    )
    # 4b. today 快照必须是单日区间（review P2a）；DB 层兼底，API 层也校验。
    op.execute(
        text(
            """
            ALTER TABLE analytics.ad_raw
                ADD CONSTRAINT ck_analytics_raw_today_single_day
                CHECK (kind <> 'today' OR day_start = day_end)
            """
        )
    )

    # 5. 弃旧 5 元组唯一约束（先建好新 partial unique 再 drop，窗口内仍有唯一性兜底）
    op.execute(
        text(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS uq_analytics_raw_live
                ON analytics.ad_raw (seller_id, advertiser_id, endpoint, campaign_id, kind)
                WHERE kind IN ('history', 'today')
            """
        )
    )
    op.execute(
        text(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS uq_analytics_raw_daily
                ON analytics.ad_raw (seller_id, advertiser_id, endpoint, day_end, campaign_id)
                WHERE kind = 'daily'
            """
        )
    )
    op.execute(
        text(
            """
            ALTER TABLE analytics.ad_raw
                DROP CONSTRAINT IF EXISTS uq_analytics_raw_unit_day
            """
        )
    )

    # 6. 索引（day 列改名后 PG 自动跟随列引用；用 IF NOT EXISTS 幂等兜底）
    op.execute(
        text(
            """
            CREATE INDEX IF NOT EXISTS idx_analytics_raw_scope
                ON analytics.ad_raw (seller_id, advertiser_id, endpoint, day_end)
            """
        )
    )
    op.execute(
        text(
            """
            CREATE INDEX IF NOT EXISTS idx_analytics_raw_request
                ON analytics.ad_raw (request_id)
            """
        )
    )
    op.execute(
        text(
            """
            CREATE INDEX IF NOT EXISTS idx_analytics_raw_received
                ON analytics.ad_raw (received_at DESC)
            """
        )
    )


def downgrade() -> None:
    # 逆向：回到 5 元组含日唯一。live 多行（history+today）若 day_end 相同会冲突，
    # 由实施方在回滚前先折叠/清理；此处按 fresh 数据形态尽力回退。
    op.execute(text("DROP INDEX IF EXISTS uq_analytics_raw_live"))
    op.execute(text("DROP INDEX IF EXISTS uq_analytics_raw_daily"))
    op.execute(
        text(
            """
            ALTER TABLE analytics.ad_raw
                DROP CONSTRAINT IF EXISTS ck_analytics_raw_kind
            """
        )
    )
    op.execute(
        text(
            """
            ALTER TABLE analytics.ad_raw
                DROP CONSTRAINT IF EXISTS ck_analytics_raw_today_single_day
            """
        )
    )
    op.execute(
        text(
            """
            ALTER TABLE analytics.ad_raw RENAME COLUMN day_end TO day
            """
        )
    )
    op.execute(
        text(
            """
            ALTER TABLE analytics.ad_raw
                ADD CONSTRAINT uq_analytics_raw_unit_day
                UNIQUE (seller_id, advertiser_id, endpoint, day, campaign_id)
            """
        )
    )
    op.execute(text("ALTER TABLE analytics.ad_raw DROP COLUMN kind"))
    op.execute(text("ALTER TABLE analytics.ad_raw DROP COLUMN day_start"))
