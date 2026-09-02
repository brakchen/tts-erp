"""analytics.* — Chrome extension (tk-adv-cost-monitor) analytics ingest.

5 tables: ad_raw / ad_records / ad_daily_completeness /
ad_shop_timezones / ad_audit_log.

历史：
- v1 时代在 public schema、以 analytics_ 前缀命名，由
  analytics_sync/schema.sql 双轨维护。
- 2026-09-02 v2 化（tech-doc/analytics-v2-migration-plan.md）：迁入独立
  schema `analytics`（第 10 个），表名改 ad_ 前缀；alembic migration 0004
  负责 SET SCHEMA + RENAME（老库）/ CREATE（新库）。
- 2026-09-02 dump architecture（tech-doc/analytics/dump-architecture.md）：
  migration 0005 drop ad_daily_pages / ad_cursors（page/cursor 概念删除），
  新增 ad_raw（source-of-truth，5 元组 unique 幂等），ad_records 去
  page / expected_page_count 列，ad_daily_completeness 只剩 captured_at
  （existence 语义由 has-data 查 ad_raw 承担）。

模型声明与 migration 0005 后的现网 schema 对齐（索引/约束名保留
SET SCHEMA 时代的历史名）。本模块只作 metadata 镜像 —— 实际读写走
tts_erp_v2/analytics/repository.py（raw SQL，ad_raw 无 ORM 写入）。
"""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import (
    BigInteger,
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


# ─── ad_raw ──────────────────────────────────────────────────────────
# Source of truth：每条 dump = 一次完整 HTTP 交换（request/response 原样
# JSONB）。不派生、immutable；唯一性 = 5 元组
# (seller_id, advertiser_id, endpoint, day, campaign_id)，幂等由
# uq_analytics_raw_unit_day 保证。与 ad_records / ad_daily_completeness
# 无 FK，逻辑链接靠 shared 5 元组 key（endpoint 经 STORAGE_KEY_BY_PATH
# 映射到 storage_key）。
class AdRaw(Base):
    __tablename__ = "ad_raw"
    __table_args__ = (
        UniqueConstraint(
            "seller_id",
            "advertiser_id",
            "endpoint",
            "day",
            "campaign_id",
            name="uq_analytics_raw_unit_day",
        ),
        CheckConstraint("protocol_version > 0", name="ck_analytics_raw_protocol"),
        CheckConstraint("schema_version > 0", name="ck_analytics_raw_schema"),
        Index(
            "idx_analytics_raw_scope",
            "seller_id",
            "advertiser_id",
            "endpoint",
            "day",
        ),
        Index("idx_analytics_raw_request", "request_id"),
        Index("idx_analytics_raw_received", "received_at"),
        {"schema": "analytics"},
    )

    id: Mapped[int] = mapped_column(
        BigInteger,
        primary_key=True,
        server_default=text("generate_always_as_identity()"),
    )
    idempotency_key: Mapped[str] = mapped_column(Text, nullable=False)
    seller_id: Mapped[str] = mapped_column(Text, nullable=False)
    advertiser_id: Mapped[str] = mapped_column(Text, nullable=False)
    endpoint: Mapped[str] = mapped_column(Text, nullable=False)
    method: Mapped[str] = mapped_column(Text, nullable=False)
    day: Mapped[date] = mapped_column(Date, nullable=False)
    campaign_id: Mapped[str] = mapped_column(Text, nullable=False)
    request: Mapped[dict] = mapped_column(JSONB, nullable=False)
    response: Mapped[dict] = mapped_column(JSONB, nullable=False)
    captured_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    source: Mapped[str | None] = mapped_column(Text)
    request_id: Mapped[str | None] = mapped_column(Text)
    protocol_version: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("2")
    )
    schema_version: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("1")
    )


# ─── ad_records ──────────────────────────────────────────────────────
# 派生表：ad_raw 落库后在同事务内派生出的分析行（body 提取后形状）。
# dump architecture 后无 page / expected_page_count 列；唯一约束是
# 5 元组 (seller_id, advertiser_id, storage_key, campaign_id, day)。
class AdRecord(Base):
    __tablename__ = "ad_records"
    __table_args__ = (
        UniqueConstraint(
            "seller_id",
            "advertiser_id",
            "storage_key",
            "campaign_id",
            "day",
            name="uq_analytics_records_unit_day",
        ),
        CheckConstraint(_STORAGE_KEY_CHECK, name="ck_analytics_records_storage"),
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
    shop_name: Mapped[str | None] = mapped_column(Text)
    endpoint: Mapped[str] = mapped_column(Text, nullable=False)
    method: Mapped[str] = mapped_column(Text, nullable=False)
    request_body: Mapped[dict | None] = mapped_column(JSONB)
    response_data: Mapped[dict] = mapped_column(JSONB, nullable=False)
    source: Mapped[str] = mapped_column(Text, nullable=False)
    captured_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
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


# ─── ad_daily_completeness ───────────────────────────────────────────
# 「今天有数据」的轻量锚点（captured_at = last dump 时间）。
# dump architecture 后 is_complete / expected_page_count 语义删除——
# 完整性的真相源是 ad_raw existence（GET /cursor has-data 查它）。
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
        {"schema": "analytics"},
    )

    seller_id: Mapped[str] = mapped_column(Text, nullable=False)
    advertiser_id: Mapped[str] = mapped_column(Text, nullable=False)
    storage_key: Mapped[str] = mapped_column(Text, nullable=False)
    campaign_id: Mapped[str] = mapped_column(Text, nullable=False)
    day: Mapped[date] = mapped_column(Date, nullable=False)
    captured_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )


# ─── ad_shop_timezones ───────────────────────────────────────────────
# 每个 seller 的权威 IANA 时区（has-data 检查按 seller 时区的「今天」算）。
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
