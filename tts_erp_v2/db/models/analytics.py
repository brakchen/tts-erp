"""analytics.ad_raw — Chrome extension (tk-adv-cost-monitor) analytics ingest。

2026-09-05 analytics reorg（tech-doc/analytics/reorg-plan.md 决策 #1-4）：
本模块从「5 表 + 1 view」收成「1 表 + 1 view」。被删表（ad_records /
ad_daily_completeness / ad_shop_timezones / ad_audit_log）要么是 dump
architecture 之后的写放大僵尸,要么是已迁到结构化文件日志的审计职责。
唯一保留的表 = ``ad_raw``（source-of-truth,5 元组 unique 幂等 upsert）。

2026-09-07 range-aggregate（tech-doc/analytics/range-aggregate-history-sync.md
Design A，migration 0012/0013/0014）：
- ad_raw 语义从「一行=一天」升级为「一行 = (scope,endpoint,campaign,kind) 的
  live 快照（kind='history' [S..T-1] / 'today' [T..T]，区间原地更新）或 legacy
  'daily' 逐日行」；唯一键拆成两把 partial unique index（live 按 kind、daily 按日）。
- 新增 analytics.ad_sync_audit 元数据审计表（内容被取代事件一行，D-4）。

模型声明与 migration 0012/0013 后的 schema 对齐。本模块只作 metadata 镜像
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
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from tts_erp_v2.db.base import Base


# ad_raw ─────────────────────────────────────────────────────────────
# Source of truth：每条 dump = 一次完整 HTTP 交换（request/response 原样
# JSONB）。Design A（Design A 快照模型）：
#   live 行 (kind history/today) 唯一 = (seller, advertiser, endpoint, campaign, kind)，
#   区间 [day_start..day_end] 是可变内容、原地 upsert（partial unique live）。
#   legacy daily 行唯一 = 旧 5 元组（含 day_end=day），迁移期保留，首个覆盖它们的
#   v3 history 写入时同事务折叠删除。ad_product_links VIEW 仅依赖本表。
class AdRaw(Base):
    __tablename__ = "ad_raw"
    __table_args__ = (
        CheckConstraint(
            "kind IN ('history', 'today', 'daily')",
            name="ck_analytics_raw_kind",
        ),
        CheckConstraint(
            "kind <> 'today' OR day_start = day_end",
            name="ck_analytics_raw_today_single_day",
        ),
        CheckConstraint("protocol_version > 0", name="ck_analytics_raw_protocol"),
        CheckConstraint("schema_version > 0", name="ck_analytics_raw_schema"),
        Index(
            "uq_analytics_raw_live",
            "seller_id",
            "advertiser_id",
            "endpoint",
            "campaign_id",
            "kind",
            unique=True,
            postgresql_where=text("kind IN ('history', 'today')"),
        ),
        Index(
            "uq_analytics_raw_daily",
            "seller_id",
            "advertiser_id",
            "endpoint",
            "day_end",
            "campaign_id",
            unique=True,
            postgresql_where=text("kind = 'daily'"),
        ),
        Index(
            "idx_analytics_raw_scope",
            "seller_id",
            "advertiser_id",
            "endpoint",
            "day_end",
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
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    day_start: Mapped[date] = mapped_column(Date, nullable=False)
    day_end: Mapped[date] = mapped_column(Date, nullable=False)
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


# ad_sync_audit ──────────────────────────────────────────────────────
# 元数据审计（D-4，migration 0013）：内容被取代事件一行（区间/时间/原因），
# 与主写同事务原子写。**不存旧 JSON**——被取代内容无读取消费方。
class AdSyncAudit(Base):
    __tablename__ = "ad_sync_audit"
    __table_args__ = (
        CheckConstraint(
            "kind IN ('history', 'today', 'daily')",
            name="ck_ad_sync_audit_kind",
        ),
        CheckConstraint(
            "event IN ('history_replaced', 'rollover_advanced', 'window_rebuilt', "
            "'legacy_collapsed', 'today_reset')",
            name="ck_ad_sync_audit_event",
        ),
        Index(
            "idx_ad_sync_audit_scope",
            "seller_id",
            "advertiser_id",
            "campaign_id",
            "occurred_at",
        ),
        {"schema": "analytics"},
    )

    id: Mapped[int] = mapped_column(
        BigInteger,
        primary_key=True,
        server_default=text("generate_always_as_identity()"),
    )
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    seller_id: Mapped[str] = mapped_column(Text, nullable=False)
    advertiser_id: Mapped[str] = mapped_column(Text, nullable=False)
    endpoint: Mapped[str] = mapped_column(Text, nullable=False)
    campaign_id: Mapped[str] = mapped_column(Text, nullable=False)
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    event: Mapped[str] = mapped_column(Text, nullable=False)
    prev_day_start: Mapped[date | None] = mapped_column(Date)
    prev_day_end: Mapped[date | None] = mapped_column(Date)
    prev_captured_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    new_day_start: Mapped[date | None] = mapped_column(Date)
    new_day_end: Mapped[date | None] = mapped_column(Date)
    new_captured_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    reason: Mapped[str | None] = mapped_column(Text)
    request_id: Mapped[str | None] = mapped_column(Text)


__all__ = ["AdRaw", "AdSyncAudit"]
