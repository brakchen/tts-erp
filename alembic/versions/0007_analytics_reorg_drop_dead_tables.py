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

from sqlalchemy import text

from alembic import op  # pyright: ignore[reportAttributeAccessIssue]

revision: str = "0007_analytics_reorg"
down_revision: str | None = "0006_ad_product_links_view"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# ─── 表与索引的现 schema（downgrade 重建用，从现 models 抄）──────────

# ad_daily_completeness —— schema 照抄 tts_erp_v2/db/models/analytics.py
# AdDailyCompleteness + check constraint。
_AD_DAILY_COMPLETENESS_DDL = """
CREATE TABLE analytics.ad_daily_completeness (
    seller_id     text        NOT NULL,
    advertiser_id text        NOT NULL,
    storage_key   text        NOT NULL,
    campaign_id   text        NOT NULL,
    day           date        NOT NULL,
    captured_at   timestamp with time zone DEFAULT now() NOT NULL,
    updated_at    timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT pk_analytics_daily_completeness PRIMARY KEY (
        seller_id, advertiser_id, storage_key, campaign_id, day
    ),
    CONSTRAINT ck_analytics_daily_completeness_storage
        CHECK (storage_key IN ('productAnalyses', 'sessionAnalyses', 'campaignChangeLogs'))
)
"""

# ad_records —— schema 照抄 AdRecord。id bigint identity（Postgres 14+
# GENERATED ALWAYS AS IDENTITY）；原 schema 用 bigserial,这里用 bigserial
# 与现库一致。
_AD_RECORDS_DDL = """
CREATE TABLE analytics.ad_records (
    id              bigserial PRIMARY KEY,
    idempotency_key text NOT NULL,
    source_record_id text,
    seller_id       text NOT NULL,
    advertiser_id   text NOT NULL,
    storage_key     text NOT NULL,
    campaign_id     text NOT NULL,
    day             date NOT NULL,
    shop_name       text,
    endpoint        text NOT NULL,
    method          text NOT NULL,
    request_body    jsonb,
    response_data   jsonb NOT NULL,
    source          text NOT NULL,
    captured_at     timestamp with time zone NOT NULL,
    schema_version  integer DEFAULT 1 NOT NULL,
    protocol_version integer DEFAULT 1 NOT NULL,
    received_at     timestamp with time zone DEFAULT now() NOT NULL,
    request_id      text,
    updated_at      timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT uq_analytics_records_unit_day UNIQUE (
        seller_id, advertiser_id, storage_key, campaign_id, day
    ),
    CONSTRAINT ck_analytics_records_storage
        CHECK (storage_key IN ('productAnalyses', 'sessionAnalyses', 'campaignChangeLogs')),
    CONSTRAINT ck_analytics_records_schema CHECK (schema_version > 0),
    CONSTRAINT ck_analytics_records_protocol CHECK (protocol_version > 0)
);
CREATE INDEX idx_analytics_records_scope
    ON analytics.ad_records (seller_id, advertiser_id, storage_key, campaign_id, day);
CREATE INDEX idx_analytics_records_request
    ON analytics.ad_records (request_id);
CREATE INDEX idx_analytics_records_received
    ON analytics.ad_records (received_at);
"""

# ad_shop_timezones —— 照抄 AdShopTimezone。
_AD_SHOP_TIMEZONES_DDL = """
CREATE TABLE analytics.ad_shop_timezones (
    seller_id     text NOT NULL,
    advertiser_id text NOT NULL,
    timezone      text DEFAULT 'Asia/Shanghai' NOT NULL,
    updated_at    timestamp with time zone DEFAULT now() NOT NULL,
    created_at    timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT pk_analytics_shop_timezones PRIMARY KEY (seller_id)
)
"""

# ad_audit_log —— 照抄 AdAuditLog。索引也重建。
_AD_AUDIT_LOG_DDL = """
CREATE TABLE analytics.ad_audit_log (
    id            bigserial PRIMARY KEY,
    request_id    text,
    endpoint      text NOT NULL,
    method        text NOT NULL,
    path          text NOT NULL,
    status        integer NOT NULL,
    key_prefix    text,
    records_in    integer,
    records_ok    integer,
    records_rej   integer,
    error_code    text,
    error_message text,
    created_at    timestamp with time zone DEFAULT now() NOT NULL,
    updated_at    timestamp with time zone DEFAULT now() NOT NULL
);
CREATE INDEX idx_analytics_audit_request ON analytics.ad_audit_log (request_id);
CREATE INDEX idx_analytics_audit_created ON analytics.ad_audit_log (created_at);
"""


def upgrade() -> None:
    """Drop 4 dead analytics tables; their duties are either obsolete or
    moved to structured file logs."""
    # 顺序：先无依赖 → 有依赖；本组表之间无 FK，顺序不敏感。
    # pi-lens-ignore: python-sql-injection — literal DDL, constant table names only
    op.execute(text("DROP TABLE IF EXISTS analytics.ad_audit_log CASCADE"))
    # pi-lens-ignore: python-sql-injection — literal DDL, constant table names only
    op.execute(text("DROP TABLE IF EXISTS analytics.ad_shop_timezones CASCADE"))
    # pi-lens-ignore: python-sql-injection — literal DDL, constant table names only
    op.execute(text("DROP TABLE IF EXISTS analytics.ad_daily_completeness CASCADE"))
    # pi-lens-ignore: python-sql-injection — literal DDL, constant table names only
    op.execute(text("DROP TABLE IF EXISTS analytics.ad_records CASCADE"))


def downgrade() -> None:
    """Recreate the 4 tables (schema only).

    Schema definitions mirror the deleted SQLAlchemy models in
    ``tts_erp_v2/db/models/analytics.py``. **Data is NOT recovered**:
    - ``ad_raw`` still allows re-deriving ``ad_records`` / ``ad_daily_completeness``
      content (body-only payload); the down itself does not re-derive, it only
      restores the empty table shape.
    - ``ad_audit_log`` historical rows are permanently lost (the reorg plan
      accepts this; structured file logs are the new source of audit history).
    - ``ad_shop_timezones`` historical rows are lost; new rows will be lazily
      re-seeded on the first fetch_timezone call (which is itself removed
      from production code paths — this is a defence-in-depth rollback only).
    """
    # pi-lens-ignore: python-sql-injection — literal DDL constants, no user input
    op.execute(text(_AD_RECORDS_DDL))
    # pi-lens-ignore: python-sql-injection — literal DDL constants, no user input
    op.execute(text(_AD_DAILY_COMPLETENESS_DDL))
    # pi-lens-ignore: python-sql-injection — literal DDL constants, no user input
    op.execute(text(_AD_SHOP_TIMEZONES_DDL))
    # pi-lens-ignore: python-sql-injection — literal DDL constants, no user input
    op.execute(text(_AD_AUDIT_LOG_DDL))
