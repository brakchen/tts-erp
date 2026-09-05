"""analytics.ad_raw — Chrome extension (tk-adv-cost-monitor) analytics ingest。

2026-09-05 analytics reorg（tech-doc/analytics/reorg-plan.md 决策 #1-4）：
本模块从「5 表 + 1 view」收成「1 表 + 1 view」。被删表（ad_records /
ad_daily_completeness / ad_shop_timezones / ad_audit_log）要么是 dump
architecture 之后的写放大僵尸,要么是已迁到结构化文件日志的审计职责。
唯一保留的表 = ``ad_raw``（source-of-truth,5 元组 unique 幂等 upsert）。

历史（已归档，仅作背景追溯）：
- v1 时代在 public schema、以 analytics_ 前缀命名,由 analytics_sync/schema.sql
  双轨维护。
- 2026-09-02 v2 化（tech-doc/analytics-v2-migration-plan.md）：迁入独立
  schema ``analytics``（第 10 个）,表名改 ad_ 前缀；alembic migration 0004
  负责 SET SCHEMA + RENAME（老库）/ CREATE（新库）。
- 2026-09-02 dump architecture（tech-doc/analytics/dump-architecture.md）：
  migration 0005 drop ad_daily_pages / ad_cursors（page/cursor 概念删除）,
  新增 ad_raw（source-of-truth,5 元组 unique 幂等）,ad_records 去
  page / expected_page_count 列,ad_daily_completeness 只剩 captured_at
  （existence 语义由 has-data 查 ad_raw 承担）。
- 2026-09-05 reorg（migration 0007）：删 ad_records / ad_daily_completeness /
  ad_shop_timezones / ad_audit_log,upsert_dump 缩为单表写,审计迁文件日志。
  ad_product_links VIEW 不动（仍只读 ad_raw）。

模型声明与 migration 0007 后的现网 schema 对齐。本模块只作 metadata 镜像
—— 实际读写走 tts_erp_v2/analytics/repository.py（raw SQL,ad_raw 无 ORM 写入）。
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
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from tts_erp_v2.db.base import Base

# ad_raw ─────────────────────────────────────────────────────────────
# Source of truth：每条 dump = 一次完整 HTTP 交换（request/response 原样
# JSONB）。upsert 语义（dump architecture 0005 后保留）：5 元组唯一
# (seller_id, advertiser_id, endpoint, day, campaign_id),幂等由
# uq_analytics_raw_unit_day 保证。ad_product_links VIEW 仅依赖本表。
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
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("now()"),
        onupdate=text("now()"),
    )


__all__ = ["AdRaw"]
