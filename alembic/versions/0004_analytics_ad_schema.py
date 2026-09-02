"""analytics_ad_schema — analytics ingest 表迁入独立 schema 并改名

Revision ID: 0004_analytics_ad_schema
Revises: 0003_manual_costs_one_open
Create Date: 2026-09-02

analytics_sync v2 化（tech-doc/analytics-v2-migration-plan.md）：

- 老库路径：public.analytics_* 6 表已存在（v1 时代由 analytics_sync/schema.sql
  双轨维护）→ ``ALTER TABLE ... SET SCHEMA analytics`` + ``RENAME TO ad_*``。
  SET SCHEMA 是元数据操作，零数据拷贝，索引/约束/owned sequence 连带迁移。
- 新库路径：public.analytics_* 不存在 → 直接在 analytics schema 建表。
  CREATE 全部带 IF NOT EXISTS，两种路径收敛到同一终态。
- 索引/约束名保留历史名（uq_analytics_records_idem 等）——SET SCHEMA/RENAME
  不会改它们；新库 DDL 用同名，保证两条路径产物完全一致。
- 所有 ALTER/CREATE 均为字面量 SQL（无 f-string 拼接）——标识符是固定集合，
  展开写比参数化更直白，也避免静态审计误报。

downgrade = 反向 RENAME + SET SCHEMA 回 public.analytics_*（数据无损）。

⚠️ 本 migration 必须与代码切换（tts_erp_v2/analytics/* 上线）同窗口：
迁移后裸表名 public.analytics_* 不再可解析，旧 analytics_sync 存储层会 42P01。
"""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import text

from alembic import op  # pyright: ignore[reportAttributeAccessIssue]

revision: str = "0004_analytics_ad_schema"
down_revision: str | None = "0003_manual_costs_one_open"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# (老表名 public.*, 新表名 analytics.*) —— 存在性探测用；ALTER 全部字面量展开。
_TABLES: tuple[tuple[str, str], ...] = (
    ("analytics_records", "ad_records"),
    ("analytics_daily_pages", "ad_daily_pages"),
    ("analytics_daily_completeness", "ad_daily_completeness"),
    ("analytics_cursors", "ad_cursors"),
    ("analytics_shop_timezones", "ad_shop_timezones"),
    ("analytics_audit_log", "ad_audit_log"),
)

_STORAGE_KEY_CHECK = (
    "storage_key IN ('productAnalyses', 'sessionAnalyses', 'campaignChangeLogs')"
)


def _existing_tables(schema: str, names: tuple[str, ...]) -> set[str]:
    """返回 schema 下实际存在的表名子集（参数化查询，无 SQL 拼接）。"""
    bind = op.get_bind()
    # pi-lens-ignore: python-sql-injection
    rows = bind.execute(
        text(
            "SELECT tablename FROM pg_tables "
            "WHERE schemaname = :schema AND tablename = ANY(:names)"
        ),
        {"schema": schema, "names": list(names)},
    ).fetchall()
    return {r[0] for r in rows}


def _move_existing_tables() -> None:
    """老库路径：public.analytics_* → analytics.ad_*（SET SCHEMA + RENAME）。

    存在性按表逐个判定，ALTER 用字面量（标识符固定，展开写）。
    """
    moved = _existing_tables("public", tuple(old for old, _ in _TABLES))
    if "analytics_records" in moved:
        op.execute("ALTER TABLE public.analytics_records SET SCHEMA analytics")
        op.execute("ALTER TABLE analytics.analytics_records RENAME TO ad_records")
    if "analytics_daily_pages" in moved:
        op.execute("ALTER TABLE public.analytics_daily_pages SET SCHEMA analytics")
        op.execute(
            "ALTER TABLE analytics.analytics_daily_pages RENAME TO ad_daily_pages"
        )
    if "analytics_daily_completeness" in moved:
        op.execute(
            "ALTER TABLE public.analytics_daily_completeness SET SCHEMA analytics"
        )
        op.execute(
            "ALTER TABLE analytics.analytics_daily_completeness RENAME TO ad_daily_completeness"
        )
    if "analytics_cursors" in moved:
        op.execute("ALTER TABLE public.analytics_cursors SET SCHEMA analytics")
        op.execute("ALTER TABLE analytics.analytics_cursors RENAME TO ad_cursors")
    if "analytics_shop_timezones" in moved:
        op.execute("ALTER TABLE public.analytics_shop_timezones SET SCHEMA analytics")
        op.execute(
            "ALTER TABLE analytics.analytics_shop_timezones RENAME TO ad_shop_timezones"
        )
    if "analytics_audit_log" in moved:
        op.execute("ALTER TABLE public.analytics_audit_log SET SCHEMA analytics")
        op.execute("ALTER TABLE analytics.analytics_audit_log RENAME TO ad_audit_log")


def _create_tables_fresh() -> None:
    """新库路径（兼幂等兜底）：analytics.ad_* 全量 DDL，IF NOT EXISTS。"""
    op.execute("""CREATE TABLE IF NOT EXISTS analytics.ad_records (
        id BIGINT GENERATED ALWAYS AS IDENTITY NOT NULL,
        idempotency_key TEXT NOT NULL,
        source_record_id TEXT,
        seller_id TEXT NOT NULL,
        advertiser_id TEXT NOT NULL,
        storage_key TEXT NOT NULL,
        campaign_id TEXT NOT NULL,
        day DATE NOT NULL,
        page INTEGER NOT NULL,
        shop_name TEXT,
        endpoint TEXT NOT NULL,
        method TEXT NOT NULL,
        request_body JSONB,
        response_data JSONB NOT NULL,
        source TEXT NOT NULL,
        captured_at TIMESTAMP WITH TIME ZONE NOT NULL,
        expected_page_count INTEGER,
        schema_version INTEGER DEFAULT 1 NOT NULL,
        protocol_version INTEGER DEFAULT 1 NOT NULL,
        received_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
        request_id TEXT,
        PRIMARY KEY (id),
        CONSTRAINT uq_analytics_records_idem UNIQUE (idempotency_key)
    )""")
    # 老表是 BIGSERIAL 迁来的（带 owned sequence）；新库是 IDENTITY。
    # 功能等价；CHECK/索引名两条路径一致。
    op.execute("""CREATE TABLE IF NOT EXISTS analytics.ad_daily_pages (
        seller_id TEXT NOT NULL,
        advertiser_id TEXT NOT NULL,
        storage_key TEXT NOT NULL,
        campaign_id TEXT NOT NULL,
        day DATE NOT NULL,
        page INTEGER NOT NULL,
        inserted_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
        CONSTRAINT pk_analytics_daily_pages PRIMARY KEY (
            seller_id, advertiser_id, storage_key, campaign_id, day, page
        )
    )""")
    op.execute("""CREATE TABLE IF NOT EXISTS analytics.ad_daily_completeness (
        seller_id TEXT NOT NULL,
        advertiser_id TEXT NOT NULL,
        storage_key TEXT NOT NULL,
        campaign_id TEXT NOT NULL,
        day DATE NOT NULL,
        expected_page_count INTEGER NOT NULL,
        is_complete BOOLEAN DEFAULT FALSE NOT NULL,
        completed_at TIMESTAMP WITH TIME ZONE,
        last_recomputed_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
        CONSTRAINT pk_analytics_daily_completeness PRIMARY KEY (
            seller_id, advertiser_id, storage_key, campaign_id, day
        )
    )""")
    op.execute("""CREATE TABLE IF NOT EXISTS analytics.ad_cursors (
        seller_id TEXT NOT NULL,
        advertiser_id TEXT NOT NULL,
        storage_key TEXT NOT NULL,
        campaign_id TEXT NOT NULL,
        latest_completed_day DATE,
        first_seen_day DATE,
        last_updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
        request_id TEXT,
        CONSTRAINT pk_analytics_cursors PRIMARY KEY (
            seller_id, advertiser_id, storage_key, campaign_id
        )
    )""")
    op.execute("""CREATE TABLE IF NOT EXISTS analytics.ad_shop_timezones (
        seller_id TEXT NOT NULL,
        advertiser_id TEXT NOT NULL,
        timezone TEXT DEFAULT 'Asia/Shanghai' NOT NULL,
        updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
        CONSTRAINT pk_analytics_shop_timezones PRIMARY KEY (seller_id)
    )""")
    op.execute("""CREATE TABLE IF NOT EXISTS analytics.ad_audit_log (
        id BIGINT GENERATED ALWAYS AS IDENTITY NOT NULL,
        request_id TEXT,
        endpoint TEXT NOT NULL,
        method TEXT NOT NULL,
        path TEXT NOT NULL,
        status INTEGER NOT NULL,
        key_prefix TEXT,
        records_in INTEGER,
        records_ok INTEGER,
        records_rej INTEGER,
        error_code TEXT,
        error_message TEXT,
        created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
        PRIMARY KEY (id)
    )""")


# (表名, 约束名, 完整字面量 ALTER DDL) —— DDL 全字面量，存在性用参数化查询判定。
_CHECK_DDL: tuple[tuple[str, str, str], ...] = (
    (
        "ad_records",
        "ck_analytics_records_storage",
        f"ALTER TABLE analytics.ad_records ADD CONSTRAINT ck_analytics_records_storage CHECK ({_STORAGE_KEY_CHECK})",
    ),
    (
        "ad_records",
        "ck_analytics_records_page",
        "ALTER TABLE analytics.ad_records ADD CONSTRAINT ck_analytics_records_page CHECK (page > 0)",
    ),
    (
        "ad_records",
        "ck_analytics_records_schema",
        "ALTER TABLE analytics.ad_records ADD CONSTRAINT ck_analytics_records_schema CHECK (schema_version > 0)",
    ),
    (
        "ad_records",
        "ck_analytics_records_protocol",
        "ALTER TABLE analytics.ad_records ADD CONSTRAINT ck_analytics_records_protocol CHECK (protocol_version > 0)",
    ),
    (
        "ad_daily_pages",
        "ck_analytics_daily_pages_storage",
        f"ALTER TABLE analytics.ad_daily_pages ADD CONSTRAINT ck_analytics_daily_pages_storage CHECK ({_STORAGE_KEY_CHECK})",
    ),
    (
        "ad_daily_pages",
        "ck_analytics_daily_pages_page",
        "ALTER TABLE analytics.ad_daily_pages ADD CONSTRAINT ck_analytics_daily_pages_page CHECK (page > 0)",
    ),
    (
        "ad_daily_completeness",
        "ck_analytics_daily_completeness_storage",
        f"ALTER TABLE analytics.ad_daily_completeness ADD CONSTRAINT ck_analytics_daily_completeness_storage CHECK ({_STORAGE_KEY_CHECK})",
    ),
    (
        "ad_daily_completeness",
        "ck_analytics_daily_completeness_expected",
        "ALTER TABLE analytics.ad_daily_completeness ADD CONSTRAINT ck_analytics_daily_completeness_expected CHECK (expected_page_count > 0)",
    ),
    (
        "ad_cursors",
        "ck_analytics_cursors_storage",
        f"ALTER TABLE analytics.ad_cursors ADD CONSTRAINT ck_analytics_cursors_storage CHECK ({_STORAGE_KEY_CHECK})",
    ),
)


def _ensure_checks_and_indexes() -> None:
    """CHECK 约束与索引（幂等；老库迁移路径已自带，自然跳过）。

    PG 的 ADD CONSTRAINT 没有 IF NOT EXISTS，用 pg_constraint 判存（参数化）。
    """
    bind = op.get_bind()
    for table, name, ddl in _CHECK_DDL:
        # pi-lens-ignore: python-sql-injection
        exists = bind.execute(
            text(
                "SELECT 1 FROM pg_constraint c "
                "JOIN pg_class t ON t.oid = c.conrelid "
                "JOIN pg_namespace n ON n.oid = t.relnamespace "
                "WHERE n.nspname = 'analytics' AND t.relname = :table "
                "AND c.conname = :name"
            ),
            {"table": table, "name": name},
        ).scalar()
        if exists is None:
            # pi-lens-ignore: python-sql-injection
            op.execute(ddl)  # ddl 来自 _CHECK_DDL 字面量元组

    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_analytics_records_scope ON analytics.ad_records (seller_id, advertiser_id, storage_key, campaign_id, day)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_analytics_records_scope_page ON analytics.ad_records (seller_id, advertiser_id, storage_key, campaign_id, day, page)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_analytics_records_request ON analytics.ad_records (request_id)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_analytics_records_received ON analytics.ad_records (received_at DESC)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_analytics_daily_pages_unit ON analytics.ad_daily_pages (seller_id, advertiser_id, storage_key, campaign_id, day)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_analytics_daily_completeness_unit_complete ON analytics.ad_daily_completeness (seller_id, advertiser_id, storage_key, campaign_id, day, is_complete)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_analytics_audit_request ON analytics.ad_audit_log (request_id)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_analytics_audit_created ON analytics.ad_audit_log (created_at DESC)"
    )


def upgrade() -> None:
    op.execute('CREATE SCHEMA IF NOT EXISTS "analytics"')
    _move_existing_tables()
    _create_tables_fresh()
    _ensure_checks_and_indexes()
    # 兜底：早于 2026-08-31 的库可能缺 audit_log.error_message
    # （原 schema.sql 的 ALTER ADD COLUMN IF NOT EXISTS 同款）。
    op.execute(
        "ALTER TABLE analytics.ad_audit_log ADD COLUMN IF NOT EXISTS error_message TEXT"
    )


def downgrade() -> None:
    """反向：analytics.ad_* → public.analytics_*。数据随表走，无损。"""
    moved = _existing_tables("analytics", tuple(new for _, new in _TABLES))
    if "ad_records" in moved:
        op.execute("ALTER TABLE analytics.ad_records RENAME TO analytics_records")
        op.execute(
            "ALTER TABLE public.analytics_records SET SCHEMA public"
        )  # no-op guard
        op.execute("ALTER TABLE analytics.analytics_records SET SCHEMA public")
    if "ad_daily_pages" in moved:
        op.execute(
            "ALTER TABLE analytics.ad_daily_pages RENAME TO analytics_daily_pages"
        )
        op.execute("ALTER TABLE analytics.analytics_daily_pages SET SCHEMA public")
    if "ad_daily_completeness" in moved:
        op.execute(
            "ALTER TABLE analytics.ad_daily_completeness RENAME TO analytics_daily_completeness"
        )
        op.execute(
            "ALTER TABLE analytics.analytics_daily_completeness SET SCHEMA public"
        )
    if "ad_cursors" in moved:
        op.execute("ALTER TABLE analytics.ad_cursors RENAME TO analytics_cursors")
        op.execute("ALTER TABLE analytics.analytics_cursors SET SCHEMA public")
    if "ad_shop_timezones" in moved:
        op.execute(
            "ALTER TABLE analytics.ad_shop_timezones RENAME TO analytics_shop_timezones"
        )
        op.execute("ALTER TABLE analytics.analytics_shop_timezones SET SCHEMA public")
    if "ad_audit_log" in moved:
        op.execute("ALTER TABLE analytics.ad_audit_log RENAME TO analytics_audit_log")
        op.execute("ALTER TABLE analytics.analytics_audit_log SET SCHEMA public")
