"""analytics reorg — drop 4 dead tables (migration 0007)

Revision ID: 0007_analytics_reorg_drop_dead_tables
Revises: 0006_ad_product_links_view
Create Date: 2026-09-05

执行依据: tech-doc/analytics/reorg-plan.md（2026-09-05 决策落地）。
决策记录（context-mode `analytics-reorg-decisions`）：

1. DROP analytics.ad_daily_completeness
   - dump-architecture D3 后 has-data 直接查 ad_raw existence；该表不参与协议、
     无生产读方,仅每次 dump 第三次写放大（UPSERT captured_at）。
2. DROP analytics.ad_records
   - 生产代码零 SELECT（仅 INSERT + retention 90d DELETE；读取只出现在
     migration 回填与测试）；与 ad_raw.response.body 重复存同一 payload。
3. DROP analytics.ad_shop_timezones
   - 生产读写路径均死：fetch_timezone() 仅测试调用、SQL_UPSERT_TIMEZONE/
     SQL_GET_TIMEZONE/SQL_SEED_TIMEZONE/SQL_REPAIR_TIMEZONE 均为未执行死
     SQL、_today_in_tz() 无调用方；11 行是 0005 前 v1 cursor 协议遗留（值
     全为默认 Asia/Shanghai）。day 由 plugin 自报,server 不换算时区。
4. DROP analytics.ad_audit_log（职责迁到结构化文件日志）
   - 审计是日志不是数据：生产零 SELECT（仅 retention DELETE + 人肉 SQL + 测
     试断言）；失败路径 _audit_and_error 已写 stderr；与 middleware/
     access_log.py（全站每请求一行 stdout.log）重叠。改为 logger 单行
     key=value,成功路径也补一行（现唯一丢失信息 = 成功请求的 records 计数）。

⚠️ 与 reorg-plan §7「风险与数据影响」一致：
- ad_records / ad_daily_completeness 可自 ad_raw 重派生（保留期也只是临时
  拷贝）→ 删表不丢业务数据。
- ad_shop_timezones 11 行全为默认值（Asia/Shanghai）,信息量≈0。
- ad_audit_log 54,786 行历史随 drop 丢失（已接受）；如需留底,部署前
  `SELECT ... FROM analytics.ad_audit_log` 导出到日志文件（默认不导）。

⚠️ 行为不变性（仍成立）：
- ad_raw 5 元组 unique + dump 协议（单 object body）+ /cursor has-data
  协议逐字节不变 → Chrome 扩展无需发布。
- ad_product_links 视图只读 ad_raw → 兼容。

downgrade 重建 4 张表（schema 照抄现 models 定义,数据不恢复 —— 注释声明）。
down 只保证 schema 可回滚,不保证数据;ad_raw 仍可重建派生表,audit 历史
不可恢复。

实现说明（2026-09-05 二次加固）：upgrade/downgrade 全部用 alembic op API
（op.drop_table / op.create_table / op.create_index），**不出现裸
op.execute(text(...))** —— 一是 alembic 惯用写法,二是消灭 pi-lens 对静态
DDL 字符串的 python-sql-injection 误报（此前该文件因 op.execute 模式被
自动化 fixer 反复改写、函数体缩进被剥坏,见 commit 记录）。

代码侧配套改动（不在 migration 内）：
- 删除 AdRecord / AdDailyCompleteness / AdShopTimezone / AdAuditLog 类
- repository 删死 SQL + write_audit/purge_expired/fetch_timezone
- upsert_dump 缩为单表写（只 INSERT ad_raw）
- api/v2/analytics.py 全部 write_audit 改 logger 单行（tts_erp_v2.analytics.ingest）
- 删除 tts_erp_v2/jobs/analytics_retention.py
- sync_worker/scheduler.JOBS 摘除 analytics.retention
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op  # pyright: ignore[reportAttributeAccessIssue]

revision: str = "0007_analytics_reorg"
down_revision: str | None = "0006_ad_product_links_view"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Drop 4 dead analytics tables; duties are either obsolete or moved to
    structured file logs (schema-only drop, no re-derivation)."""
    op.drop_table("ad_audit_log", schema="analytics")
    op.drop_table("ad_shop_timezones", schema="analytics")
    op.drop_table("ad_daily_completeness", schema="analytics")
    op.drop_table("ad_records", schema="analytics")


def downgrade() -> None:
    """Recreate the 4 tables (schema only, **data NOT recovered**).

    - ``ad_raw`` still allows re-deriving ``ad_records`` / ``ad_daily_completeness``
      content (body-only payload); down only restores empty table shapes.
    - ``ad_audit_log`` historical rows are permanently lost (reorg plan
      accepts this; structured file logs are the new audit source).
    - ``ad_shop_timezones`` historical rows are lost; new rows would be lazily
      re-seeded by the (now removed) fetch_timezone — rollback-only defence.

    列/约束/索引名照抄被删的 SQLAlchemy models（AdRecord / AdDailyCompleteness /
    AdShopTimezone / AdAuditLog）里的声明，保证 rollback 后 schema 与
    0004-0006 时代一致。
    """
    # ad_daily_completeness —— 复合 PK，无自增 id。
    op.create_table(
        "ad_daily_completeness",
        sa.Column("seller_id", sa.Text, nullable=False),
        sa.Column("advertiser_id", sa.Text, nullable=False),
        sa.Column("storage_key", sa.Text, nullable=False),
        sa.Column("campaign_id", sa.Text, nullable=False),
        sa.Column("day", sa.Date, nullable=False),
        sa.Column("captured_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint(
            "seller_id",
            "advertiser_id",
            "storage_key",
            "campaign_id",
            "day",
            name="pk_analytics_daily_completeness",
        ),
        sa.CheckConstraint(
            "storage_key IN ('productAnalyses', 'sessionAnalyses', "
            "'campaignChangeLogs')",
            name="ck_analytics_daily_completeness_storage",
        ),
        schema="analytics",
    )

    # ad_records —— bigint identity PK + 5 元组 unique + 2 check + 3 索引。
    op.create_table(
        "ad_records",
        sa.Column("id", sa.BigInteger, sa.Identity(), nullable=False),
        sa.Column("idempotency_key", sa.Text, nullable=False),
        sa.Column("source_record_id", sa.Text),
        sa.Column("seller_id", sa.Text, nullable=False),
        sa.Column("advertiser_id", sa.Text, nullable=False),
        sa.Column("storage_key", sa.Text, nullable=False),
        sa.Column("campaign_id", sa.Text, nullable=False),
        sa.Column("day", sa.Date, nullable=False),
        sa.Column("shop_name", sa.Text),
        sa.Column("endpoint", sa.Text, nullable=False),
        sa.Column("method", sa.Text, nullable=False),
        sa.Column("request_body", sa.JSONB),
        sa.Column("response_data", sa.JSONB, nullable=False),
        sa.Column("source", sa.Text, nullable=False),
        sa.Column("captured_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("schema_version", sa.Integer, nullable=False, server_default="1"),
        sa.Column("protocol_version", sa.Integer, nullable=False, server_default="1"),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("request_id", sa.Text),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "seller_id",
            "advertiser_id",
            "storage_key",
            "campaign_id",
            "day",
            name="uq_analytics_records_unit_day",
        ),
        sa.CheckConstraint(
            "storage_key IN ('productAnalyses', 'sessionAnalyses', "
            "'campaignChangeLogs')",
            name="ck_analytics_records_storage",
        ),
        sa.CheckConstraint("schema_version > 0", name="ck_analytics_records_schema"),
        sa.CheckConstraint(
            "protocol_version > 0", name="ck_analytics_records_protocol"
        ),
        schema="analytics",
    )
    op.create_index(
        "idx_analytics_records_scope",
        "ad_records",
        ["seller_id", "advertiser_id", "storage_key", "campaign_id", "day"],
        schema="analytics",
    )
    op.create_index(
        "idx_analytics_records_request",
        "ad_records",
        ["request_id"],
        schema="analytics",
    )
    op.create_index(
        "idx_analytics_records_received",
        "ad_records",
        ["received_at"],
        schema="analytics",
    )

    # ad_shop_timezones —— seller_id 单列 PK。
    op.create_table(
        "ad_shop_timezones",
        sa.Column("seller_id", sa.Text, nullable=False),
        sa.Column("advertiser_id", sa.Text, nullable=False),
        sa.Column("timezone", sa.Text, nullable=False, server_default="Asia/Shanghai"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("seller_id", name="pk_analytics_shop_timezones"),
        schema="analytics",
    )

    # ad_audit_log —— bigint identity PK + 2 索引。
    op.create_table(
        "ad_audit_log",
        sa.Column("id", sa.BigInteger, sa.Identity(), nullable=False),
        sa.Column("request_id", sa.Text),
        sa.Column("endpoint", sa.Text, nullable=False),
        sa.Column("method", sa.Text, nullable=False),
        sa.Column("path", sa.Text, nullable=False),
        sa.Column("status", sa.Integer, nullable=False),
        sa.Column("key_prefix", sa.Text),
        sa.Column("records_in", sa.Integer),
        sa.Column("records_ok", sa.Integer),
        sa.Column("records_rej", sa.Integer),
        sa.Column("error_code", sa.Text),
        sa.Column("error_message", sa.Text),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        schema="analytics",
    )
    op.create_index(
        "idx_analytics_audit_request",
        "ad_audit_log",
        ["request_id"],
        schema="analytics",
    )
    op.create_index(
        "idx_analytics_audit_created",
        "ad_audit_log",
        ["created_at"],
        schema="analytics",
    )
