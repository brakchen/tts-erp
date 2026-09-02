"""analytics.* — Chrome extension (tk-adv-cost-monitor) analytics ingest.

6 tables: ad_records / ad_daily_pages / ad_daily_completeness /
ad_cursors / ad_shop_timezones / ad_audit_log.

历史：v1 时代这些表在 public schema、以 analytics_ 前缀命名
（public.analytics_records 等），由 analytics_sync/schema.sql 双轨维护。
2026-09-02 v2 化（tech-doc/analytics-v2-migration-plan.md）：
- 迁入独立 schema `analytics`（第 10 个），表名改 ad_ 前缀
- alembic migration 0004 负责 SET SCHEMA + RENAME（老库）/ CREATE（新库）
- 存储层从裸 psycopg 改为 SQLAlchemy（tts_erp_v2/analytics/repository.py）

注意：索引/约束名保留历史名（如 uq_analytics_records_idem），
SET SCHEMA + RENAME TABLE 不会自动改它们；模型声明与现网保持一致。
"""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Index,
    Integer,
    PrimaryKeyConstraint,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from tts_erp_v2.db.base import Base

_STORAGE_KEY_CHECK = (
    "storage_key IN ('productAnalyses', 'sessionAnalyses', 'campaignChangeLogs')"
)


# ─── ad_records ──────────────────────────────────────────────────────
# 原始批次记录：Chrome extension 上传的 response JSON + 归一化 scope 列。
# uq idempotency_key 保证幂等（重复插入 = duplicate，不是错误）。
class AdRecord(Base):
    __tablename__ = "ad_records"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_analytics_records_idem"),
        CheckConstraint(_STORAGE_KEY_CHECK, name="ck_analytics_records_storage"),
        CheckConstraint("page > 0", name="ck_analytics_records_page"),
        CheckConstraint("schema_version > 0", name="ck_analytics_records_schema"),
        CheckConstraint("protocol_version > 0", name="ck_analytics_records_protocol"),
        Index(
            "idx_analytics_records_scope",
            "seller_id",
            "advertiser_id",
            "storage_key",
            "campaign_id",
            "day",
        ),
        Index(
            "idx_analytics_records_scope_page",
            "seller_id",
            "advertiser_id",
            "storage_key",
            "campaign_id",
            "day",
            "page",
        ),
        Index("idx_analytics_records_request", "request_id"),
        Index("idx_analytics_records_received", "received_at"),
        {"schema": "analytics"},
    )

    id: Mapped[int] = mapped_column(
        BigInteger,
        primary_key=True,
        server_default=text("generate_always_as_identity()"),
    )
    idempotency_key: Mapped[str] = mapped_column(Text, nullable=False)
    source_record_id: Mapped[str | None] = mapped_column(Text)
    seller_id: Mapped[str] = mapped_column(Text, nullable=False)
    advertiser_id: Mapped[str] = mapped_column(Text, nullable=False)
    storage_key: Mapped[str] = mapped_column(Text, nullable=False)
    campaign_id: Mapped[str] = mapped_column(Text, nullable=False)
    day: Mapped[date] = mapped_column(Date, nullable=False)
    page: Mapped[int] = mapped_column(Integer, nullable=False)
    shop_name: Mapped[str | None] = mapped_column(Text)
    endpoint: Mapped[str] = mapped_column(Text, nullable=False)
    method: Mapped[str] = mapped_column(Text, nullable=False)
    request_body: Mapped[dict | None] = mapped_column(JSONB)
    response_data: Mapped[dict] = mapped_column(JSONB, nullable=False)
    source: Mapped[str] = mapped_column(Text, nullable=False)
    captured_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    expected_page_count: Mapped[int | None] = mapped_column(Integer)
    schema_version: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("1")
    )
    protocol_version: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("1")
    )
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    request_id: Mapped[str | None] = mapped_column(Text)


# ─── ad_daily_pages ──────────────────────────────────────────────────
# 页位图：每个 (scope, storageKey, campaignId, day, page) 已持久化一行。
# 复合 PK 让并发 batch 的 ON CONFLICT DO NOTHING 安全 race。
class AdDailyPage(Base):
    __tablename__ = "ad_daily_pages"
    __table_args__ = (
        PrimaryKeyConstraint(
            "seller_id",
            "advertiser_id",
            "storage_key",
            "campaign_id",
            "day",
            "page",
            name="pk_analytics_daily_pages",
        ),
        CheckConstraint(_STORAGE_KEY_CHECK, name="ck_analytics_daily_pages_storage"),
        CheckConstraint("page > 0", name="ck_analytics_daily_pages_page"),
        Index(
            "idx_analytics_daily_pages_unit",
            "seller_id",
            "advertiser_id",
            "storage_key",
            "campaign_id",
            "day",
        ),
        {"schema": "analytics"},
    )

    seller_id: Mapped[str] = mapped_column(Text, nullable=False)
    advertiser_id: Mapped[str] = mapped_column(Text, nullable=False)
    storage_key: Mapped[str] = mapped_column(Text, nullable=False)
    campaign_id: Mapped[str] = mapped_column(Text, nullable=False)
    day: Mapped[date] = mapped_column(Date, nullable=False)
    page: Mapped[int] = mapped_column(Integer, nullable=False)
    inserted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )


# ─── ad_daily_completeness ───────────────────────────────────────────
# 「这天齐了吗」的聚合真相源。batch 事务内在 ad_daily_pages 变化后重算。
class AdDailyCompleteness(Base):
    __tablename__ = "ad_daily_completeness"
    __table_args__ = (
        PrimaryKeyConstraint(
            "seller_id",
            "advertiser_id",
            "storage_key",
            "campaign_id",
            "day",
            name="pk_analytics_daily_completeness",
        ),
        CheckConstraint(
            _STORAGE_KEY_CHECK, name="ck_analytics_daily_completeness_storage"
        ),
        CheckConstraint(
            "expected_page_count > 0", name="ck_analytics_daily_completeness_expected"
        ),
        Index(
            "idx_analytics_daily_completeness_unit_complete",
            "seller_id",
            "advertiser_id",
            "storage_key",
            "campaign_id",
            "day",
            "is_complete",
        ),
        {"schema": "analytics"},
    )

    seller_id: Mapped[str] = mapped_column(Text, nullable=False)
    advertiser_id: Mapped[str] = mapped_column(Text, nullable=False)
    storage_key: Mapped[str] = mapped_column(Text, nullable=False)
    campaign_id: Mapped[str] = mapped_column(Text, nullable=False)
    day: Mapped[date] = mapped_column(Date, nullable=False)
    expected_page_count: Mapped[int] = mapped_column(Integer, nullable=False)
    is_complete: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("FALSE")
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_recomputed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )


# ─── ad_cursors ──────────────────────────────────────────────────────
# 每个 (seller, advertiser, storageKey, campaignId) 一行。
# latest_completed_day 只进不退；first_seen_day 是连续链 anchor。
class AdCursor(Base):
    __tablename__ = "ad_cursors"
    __table_args__ = (
        PrimaryKeyConstraint(
            "seller_id",
            "advertiser_id",
            "storage_key",
            "campaign_id",
            name="pk_analytics_cursors",
        ),
        CheckConstraint(_STORAGE_KEY_CHECK, name="ck_analytics_cursors_storage"),
        {"schema": "analytics"},
    )

    seller_id: Mapped[str] = mapped_column(Text, nullable=False)
    advertiser_id: Mapped[str] = mapped_column(Text, nullable=False)
    storage_key: Mapped[str] = mapped_column(Text, nullable=False)
    campaign_id: Mapped[str] = mapped_column(Text, nullable=False)
    latest_completed_day: Mapped[date | None] = mapped_column(Date)
    first_seen_day: Mapped[date | None] = mapped_column(Date)
    last_updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    request_id: Mapped[str | None] = mapped_column(Text)


# ─── ad_shop_timezones ───────────────────────────────────────────────
# 每个 seller 的权威 IANA 时区（cursor bootstrap / nextRequiredDay 按它算）。
class AdShopTimezone(Base):
    __tablename__ = "ad_shop_timezones"
    __table_args__ = (
        PrimaryKeyConstraint("seller_id", name="pk_analytics_shop_timezones"),
        {"schema": "analytics"},
    )

    seller_id: Mapped[str] = mapped_column(Text, nullable=False)
    advertiser_id: Mapped[str] = mapped_column(Text, nullable=False)
    timezone: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text("'Asia/Shanghai'")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )


# ─── ad_audit_log ────────────────────────────────────────────────────
# requestId 键的运维审计。无敏感信息：只存状态码/计数/key 前缀。
# 保留策略：analytics.retention job（30d）。
class AdAuditLog(Base):
    __tablename__ = "ad_audit_log"
    __table_args__ = (
        Index("idx_analytics_audit_request", "request_id"),
        Index("idx_analytics_audit_created", "created_at"),
        {"schema": "analytics"},
    )

    id: Mapped[int] = mapped_column(
        BigInteger,
        primary_key=True,
        server_default=text("generate_always_as_identity()"),
    )
    request_id: Mapped[str | None] = mapped_column(Text)
    endpoint: Mapped[str] = mapped_column(Text, nullable=False)
    method: Mapped[str] = mapped_column(Text, nullable=False)
    path: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[int] = mapped_column(Integer, nullable=False)
    key_prefix: Mapped[str | None] = mapped_column(Text)
    records_in: Mapped[int | None] = mapped_column(Integer)
    records_ok: Mapped[int | None] = mapped_column(Integer)
    records_rej: Mapped[int | None] = mapped_column(Integer)
    error_code: Mapped[str | None] = mapped_column(Text)
    error_message: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
