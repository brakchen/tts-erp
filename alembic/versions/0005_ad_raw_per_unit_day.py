"""analytics ad_raw — ad_raw 源表 + 删除 page/expected_page_count 等冗余

Revision ID: 0005_ad_raw_per_unit_day
Revises: 0004_analytics_ad_schema
Create Date: 2026-09-02

tech-doc/analytics-v2-migration-plan.md (4 决策已敲定):

D2 (schema) — dump 架构落地：
- 新增 analytics.ad_raw 表（immutable source-of-truth，每条 raw dump 一行）
- 唯一约束: (seller_id, advertiser_id, endpoint, day, campaign_id) 5 列
- 不与任何 ad_* 表建 FK（逻辑链接靠 shared idempotency_key + 5 元组 key）
- 删 ad_records.page 列（dump 1 天 1 行，page 维度消失）
- 删 ad_records.expected_page_count 列（page 没了，N 没了）
- 删 ad_daily_pages 表（page bitmap 概念消失）
- 删 ad_cursors 表（nextRequiredDay 是 per-page 的产物，dump 架构不需要）
- 改 ad_daily_completeness: 删 expected_page_count + is_complete，
  新增 captured_at timestamptz（记录 dump 落库时间，ad_raw 的索引镜像）
- ad_records 新唯一约束: (seller_id, advertiser_id, storage_key, campaign_id, day) 5 列
- 删 uq_analytics_records_idem (idempotency_key 旧唯一约束，由 5 元组 key 取代）

⚠️ 行为变化：
- 之前 cursor 协议 (per-page dump) 不再适用 → /dumps 新端点 (单 dump object body)
- cursor 端点: work-list 模式 (items + nextRequiredDay) 全删,只保留 has-data 检查
  (查 ad_raw existence)
- 任何依赖 ad_cursors.latest_completed_day / ad_daily_pages / ad_daily_completeness.is_complete
  / ad_records.expected_page_count / ad_records.page 的代码全失效,需要同时改

⚠️ 现有数据处理:
- ad_records.page / expected_page_count 列直接 drop（page 维度的旧数据不要了）
- ad_daily_completeness.is_complete=true 的行（v1 时代的"完整天"）→ backfill
  completed_at=now()，把"曾经完整"的事实存下来；is_complete=false 的行删除（未完成数据
  重建从 ad_raw 重新派生）
- ad_daily_pages / ad_cursors 直接 drop（page 维度的历史页位图和 cursor 状态无意义）
- ad_raw 初始为空，由新 /dumps 协议写入
"""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import text

from alembic import op  # pyright: ignore[reportAttributeAccessIssue]

revision: str = "0005_ad_raw_per_unit_day"
down_revision: str | None = "0004_analytics_ad_schema"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 1. ad_raw new table (source-of-truth, immutable raw dump)
    op.execute(
        text("""
        CREATE TABLE IF NOT EXISTS analytics.ad_raw (
            id                bigserial CONSTRAINT analytics_raw_id_not_null NOT NULL,
            idempotency_key   text CONSTRAINT analytics_raw_idempotency_key_not_null NOT NULL,
            -- routing 列（5 元组唯一约束的列）
            seller_id         text CONSTRAINT analytics_raw_seller_id_not_null NOT NULL,
            advertiser_id     text CONSTRAINT analytics_raw_advertiser_id_not_null NOT NULL,
            endpoint          text CONSTRAINT analytics_raw_endpoint_not_null NOT NULL,
            method            text CONSTRAINT analytics_raw_method_not_null NOT NULL,
            day               date CONSTRAINT analytics_raw_day_not_null NOT NULL,
            campaign_id       text CONSTRAINT analytics_raw_campaign_id_not_null NOT NULL,
            -- 原始 dump（plugin 抓的 HTTP 交换，jsonb 直存）
            request           jsonb CONSTRAINT analytics_raw_request_not_null NOT NULL,
            response          jsonb CONSTRAINT analytics_raw_response_not_null NOT NULL,
            -- 元数据
            captured_at       timestamp with time zone CONSTRAINT analytics_raw_captured_at_not_null NOT NULL,
            received_at       timestamp with time zone DEFAULT now() CONSTRAINT analytics_raw_received_at_not_null NOT NULL,
            source            text,
            request_id        text,
            protocol_version  integer DEFAULT 2 CONSTRAINT analytics_raw_protocol_version_not_null NOT NULL,
            schema_version    integer DEFAULT 1 CONSTRAINT analytics_raw_schema_version_not_null NOT NULL,
            CONSTRAINT analytics_raw_pkey PRIMARY KEY (id),
            CONSTRAINT uq_analytics_raw_unit_day UNIQUE (seller_id, advertiser_id, endpoint, day, campaign_id),
            CONSTRAINT ck_analytics_raw_protocol CHECK ((protocol_version > 0)),
            CONSTRAINT ck_analytics_raw_schema   CHECK ((schema_version > 0))
        )
    """)
    )

    # 索引（保留 has-data 高效查询 + 通用审计/recent 列表）
    op.execute(
        text("""
        CREATE INDEX IF NOT EXISTS idx_analytics_raw_received
            ON analytics.ad_raw (received_at DESC)
    """)
    )
    op.execute(
        text("""
        CREATE INDEX IF NOT EXISTS idx_analytics_raw_request
            ON analytics.ad_raw (request_id)
    """)
    )
    op.execute(
        text("""
        CREATE INDEX IF NOT EXISTS idx_analytics_raw_scope
            ON analytics.ad_raw (seller_id, advertiser_id, endpoint, day)
    """)
    )

    # 2. ad_daily_completeness: drop expected_page_count + is_complete,
    #    replace completed_at with captured_at timestamptz
    op.execute(
        text("""
        -- backfill：is_complete=true 的行转 completed_at=now()（曾经完整的事实存档）
        UPDATE analytics.ad_daily_completeness
        SET completed_at = COALESCE(completed_at, now())
        WHERE is_complete = true
    """)
    )
    op.execute(
        text("""
        -- 删 is_complete=false 的行（未完成的脏数据，后续从 ad_raw 重建）
        DELETE FROM analytics.ad_daily_completeness
        WHERE is_complete = false
    """)
    )
    op.execute(
        text("""
        -- 删 expected_page_count
        ALTER TABLE analytics.ad_daily_completeness
            DROP COLUMN expected_page_count
    """)
    )
    op.execute(
        text("""
        -- 删 last_recomputed_at（重组逻辑消失，无意义）
        ALTER TABLE analytics.ad_daily_completeness
            DROP COLUMN last_recomputed_at
    """)
    )
    op.execute(
        text("""
        -- 删 is_complete（被 captured_at 取代）
        ALTER TABLE analytics.ad_daily_completeness
            DROP COLUMN is_complete
    """)
    )
    op.execute(
        text("""
        -- 把 completed_at 改成 captured_at NOT NULL
        ALTER TABLE analytics.ad_daily_completeness
            DROP COLUMN completed_at
    """)
    )
    op.execute(
        text("""
        ALTER TABLE analytics.ad_daily_completeness
            ADD COLUMN captured_at timestamp with time zone DEFAULT now()
    """)
    )
    op.execute(
        text("""
        -- 对已有行补 captured_at(为 now()),后续 ALTER SET NOT NULL
        UPDATE analytics.ad_daily_completeness
        SET captured_at = now()
        WHERE captured_at IS NULL
    """)
    )
    op.execute(
        text("""
        ALTER TABLE analytics.ad_daily_completeness
            ALTER COLUMN captured_at SET NOT NULL
    """)
    )

    # 3. drop ad_daily_pages (page bitmap concept is gone)
    op.execute(text("DROP TABLE IF EXISTS analytics.ad_daily_pages"))

    # 4. drop ad_cursors (nextRequiredDay was per-page concept)
    op.execute(text("DROP TABLE IF EXISTS analytics.ad_cursors"))

    # 5. ad_records: drop page / expected_page_count, change unique constraint
    # page 维度的旧数据不要了（dump 1 天 1 行，page 隐式 = 1）
    op.execute(
        text("""
        ALTER TABLE analytics.ad_records DROP COLUMN page
    """)
    )
    # page 列上的 check 约束 ck_analytics_records_page 被 PG 自动 CASCADE 删,
    # 显式 DROP CONSTRAINT 会报"does not exist" — 这里不再写
    op.execute(
        text("""
        ALTER TABLE analytics.ad_records DROP COLUMN expected_page_count
    """)
    )
    # 删旧 idempotency_key 唯一约束（被 5 元组 key 取代）
    op.execute(
        text("""
        ALTER TABLE analytics.ad_records
            DROP CONSTRAINT uq_analytics_records_idem
    """)
    )
    # dedupe 同一 5 元组的旧行（v1/v2 协议允许多 page/同 day 并存,新 UNIQUE 不允许）。
    # 保留每组 id 最大的行(最新插入)。不可逆合并 —— dump architecture 下
    # 一天只 dump 一次,旧 page-N 行被 page-1 取代。
    op.execute(
        text("""
        DELETE FROM analytics.ad_records
        WHERE id IN (
            SELECT id FROM (
                SELECT id, ROW_NUMBER() OVER (
                    PARTITION BY seller_id, advertiser_id, storage_key, campaign_id, day
                    ORDER BY id DESC
                ) AS rn
                FROM analytics.ad_records
            ) t WHERE rn > 1
        )
    """)
    )
    # 新唯一约束: (scope, storageKey, campaignId, day) 5 元组
    op.execute(
        text("""
        ALTER TABLE analytics.ad_records
            ADD CONSTRAINT uq_analytics_records_unit_day
            UNIQUE (seller_id, advertiser_id, storage_key, campaign_id, day)
    """)
    )
    # 旧 idx_analytics_records_scope_page 索引 (含 page 列) 删
    op.execute(text("DROP INDEX IF EXISTS analytics.idx_analytics_records_scope_page"))


def downgrade() -> None:
    # 反向回滚（仅作灾难恢复，正常流程不应 downgrade 因为数据已不可逆丢失）
    op.execute(
        text("""
        ALTER TABLE analytics.ad_records
            ADD COLUMN page integer CONSTRAINT ck_analytics_records_page CHECK ((page > 0)) NOT NULL DEFAULT 1
    """)
    )
    op.execute(
        text("""
        ALTER TABLE analytics.ad_records
            ADD COLUMN expected_page_count integer
    """)
    )
    op.execute(
        text("""
        ALTER TABLE analytics.ad_records
            DROP CONSTRAINT uq_analytics_records_unit_day
    """)
    )
    op.execute(
        text("""
        ALTER TABLE analytics.ad_records
            ADD CONSTRAINT uq_analytics_records_idem UNIQUE (idempotency_key)
    """)
    )
    op.execute(
        text("""
        CREATE INDEX IF NOT EXISTS idx_analytics_records_scope_page
            ON analytics.ad_records USING btree (seller_id, advertiser_id, storage_key, campaign_id, day, page)
    """)
    )

    # ad_daily_completeness 反向：把 captured_at 还原成 is_complete + completed_at
    op.execute(
        text("""
        ALTER TABLE analytics.ad_daily_completeness
            ADD COLUMN completed_at timestamp with time zone
    """)
    )
    op.execute(
        text("""
        ALTER TABLE analytics.ad_daily_completeness
            ADD COLUMN is_complete boolean DEFAULT false NOT NULL
    """)
    )
    op.execute(
        text("""
        UPDATE analytics.ad_daily_completeness
        SET is_complete = true, completed_at = captured_at
        WHERE captured_at IS NOT NULL
    """)
    )
    op.execute(
        text("""
        ALTER TABLE analytics.ad_daily_completeness DROP COLUMN captured_at
    """)
    )
    op.execute(
        text("""
        ALTER TABLE analytics.ad_daily_completeness
            ADD COLUMN expected_page_count integer CONSTRAINT ck_analytics_daily_completeness_expected CHECK ((expected_page_count > 0)) NOT NULL DEFAULT 1
    """)
    )
    op.execute(
        text("""
        ALTER TABLE analytics.ad_daily_completeness
            ADD COLUMN last_recomputed_at timestamp with time zone DEFAULT now() NOT NULL
    """)
    )

    # ad_daily_pages / ad_cursors 重新建（结构跟 0004 一样）
    op.execute(
        text("""
        CREATE TABLE IF NOT EXISTS analytics.ad_daily_pages (
            seller_id text NOT NULL, advertiser_id text NOT NULL,
            storage_key text NOT NULL, campaign_id text NOT NULL,
            day date NOT NULL, page integer NOT NULL,
            received_at timestamp with time zone DEFAULT now() NOT NULL,
            request_id text,
            CONSTRAINT analytics_daily_pages_pkey PRIMARY KEY (seller_id, advertiser_id, storage_key, campaign_id, day, page)
        )
    """)
    )
    op.execute(
        text("""
        CREATE TABLE IF NOT EXISTS analytics.ad_cursors (
            seller_id text NOT NULL, advertiser_id text NOT NULL,
            storage_key text NOT NULL, campaign_id text NOT NULL,
            latest_completed_day date, first_seen_day date,
            updated_at timestamp with time zone DEFAULT now() NOT NULL,
            request_id text,
            CONSTRAINT analytics_cursors_pkey PRIMARY KEY (seller_id, advertiser_id, storage_key, campaign_id)
        )
    """)
    )

    op.execute(text("DROP TABLE IF EXISTS analytics.ad_raw"))
