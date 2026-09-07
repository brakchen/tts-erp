"""analytics.ad_sync_audit — 元数据审计表（Design A D-4，方案 0013）

Revision ID: 0013_ad_sync_audit
Revises: 0012_range_aggregate_ad_raw
Create Date: 2026-09-07

被取代内容不保留旧 JSON 版本；只在“内容被取代”事件写一行元数据审计
（区间/时间/原因），与主写同事务原子提交。30s 今日常规刷新不写审计
（防 2880 行/campaign/天）。字段定义 = 方案 §4.3。
"""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import text

from alembic import op  # pyright: ignore[reportAttributeAccessIssue]

revision: str = "0013_ad_sync_audit"
down_revision: str | None = "0012_range_aggregate_ad_raw"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        text(
            """
            CREATE TABLE analytics.ad_sync_audit (
                id               bigint GENERATED ALWAYS AS IDENTITY
                                 CONSTRAINT ad_sync_audit_pkey PRIMARY KEY,
                occurred_at      timestamp with time zone DEFAULT now()
                                 CONSTRAINT ad_sync_audit_occurred_at_not_null NOT NULL,
                seller_id        text CONSTRAINT ad_sync_audit_seller_id_not_null NOT NULL,
                advertiser_id    text CONSTRAINT ad_sync_audit_advertiser_id_not_null NOT NULL,
                endpoint         text CONSTRAINT ad_sync_audit_endpoint_not_null NOT NULL,
                campaign_id      text CONSTRAINT ad_sync_audit_campaign_id_not_null NOT NULL,
                kind             text CONSTRAINT ad_sync_audit_kind_not_null NOT NULL,
                event            text CONSTRAINT ad_sync_audit_event_not_null NOT NULL,
                prev_day_start   date,
                prev_day_end     date,
                prev_captured_at timestamp with time zone,
                new_day_start    date,
                new_day_end      date,
                new_captured_at  timestamp with time zone,
                reason           text,
                request_id       text,
                CONSTRAINT ck_ad_sync_audit_kind CHECK (
                    kind IN ('history', 'today', 'daily')),
                CONSTRAINT ck_ad_sync_audit_event CHECK (
                    event IN ('history_replaced', 'rollover_advanced',
                              'window_rebuilt', 'legacy_collapsed', 'today_reset'))
            )
            """
        )
    )
    op.execute(
        text(
            """
            CREATE INDEX IF NOT EXISTS idx_ad_sync_audit_scope
                ON analytics.ad_sync_audit (seller_id, advertiser_id, campaign_id, occurred_at DESC)
            """
        )
    )


def downgrade() -> None:
    op.execute(text("DROP TABLE IF EXISTS analytics.ad_sync_audit"))
