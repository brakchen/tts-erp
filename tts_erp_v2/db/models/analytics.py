"""analytics schema — ad_today/daily/monthly/raw_log + plugin_logs。

2026-09-10 daily-sync-with-coverage（tech-doc/analytics/daily-sync-with-coverage.md §1）：
- ad_today：今天实时表（30s ON CONFLICT DO UPDATE 刷新，跨天固化到 ad_daily 后清空）
- ad_daily：天级结构化表（历史数据 ON CONFLICT DO NOTHING，写入后不可变）
- ad_monthly：月级结构化表（独立同步，不依赖 daily）
- ad_raw_log：原始请求日志（kind CHECK: daily/today/monthly）
- plugin_logs：插件端日志上传表（Chrome 扩展运行时日志）

模型声明与 migration 0018/0019 后的 schema 对齐。本模块只作 metadata 镜像
—— 实际读写走 tts_erp_v2/analytics/repository.py（raw SQL,无 ORM 写入）。

注意（2026-09-11 已清理）：analytics.ad_raw / ad_sync_audit 两张表与
analytics.ad_product_links 视图已由 **migration 0020** 删除。
它们是 v3 区间聚合协议（kind history/today + day_start/day_end）的遗留物：
- ad_raw 早已冻结（现行 repository 只写 ad_raw_log，最后真实写入 2026-09-09）
- ad_product_links 是 ad_raw 的唯一依赖者，但**零生产消费者** —— SPU ROI 的
  _SQL_ROI_AD（tts_erp_v2/analytics/spu_roi.py:85）直接读 ad_daily ∪ ad_today
  并自行 JOIN commerce，从不经过该视图（见 migration 0020 的引用面审计）

回滚材料：/home/schan/backups/analytics_ad_raw_*.sql.gz（1467 行）、
/home/schan/backups/analytics_ad_sync_audit_*.sql.gz（2287 行）。
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
    Numeric,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from tts_erp_v2.db.base import Base


# ad_today ────────────────────────────────────────────────────────────
# 今天实时表（30s ON CONFLICT DO UPDATE 刷新，跨天固化到 ad_daily 后清空）。
# 结构和 ad_daily 完全一致，唯一区别是用途（实时 vs 历史不可变）。
# tech-doc/analytics/daily-sync-with-coverage.md §1.1
class AdToday(Base):
    __tablename__ = "ad_today"
    __table_args__ = (
        Index(
            "uq_ad_today",
            "seller_id",
            "advertiser_id",
            "endpoint",
            "campaign_id",
            "product_id",
            "day",
            unique=True,
        ),
        Index(
            "idx_ad_today_coverage",
            "seller_id",
            "advertiser_id",
            "endpoint",
            "campaign_id",
            "day",
        ),
        {"schema": "analytics"},
    )

    id: Mapped[int] = mapped_column(
        BigInteger,
        primary_key=True,
        server_default=text("generate_always_as_identity()"),
    )
    seller_id: Mapped[str] = mapped_column(Text, nullable=False)
    advertiser_id: Mapped[str] = mapped_column(Text, nullable=False)
    campaign_id: Mapped[str] = mapped_column(Text, nullable=False)
    product_id: Mapped[str] = mapped_column(Text, nullable=False)
    endpoint: Mapped[str] = mapped_column(Text, nullable=False)
    day: Mapped[date] = mapped_column(Date, nullable=False)
    mixed_real_cost: Mapped[float | None] = mapped_column(Numeric(20, 4))
    onsite_roi2_shopping_sku: Mapped[int | None] = mapped_column(BigInteger)
    onsite_roi2_shopping_value: Mapped[float | None] = mapped_column(Numeric(20, 4))
    onsite_mixed_real_roi2_shopping: Mapped[float | None] = mapped_column(
        Numeric(20, 4)
    )
    metrics_extra: Mapped[dict | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )


# ad_daily ────────────────────────────────────────────────────────────
# 天级结构化表（历史数据 ON CONFLICT DO NOTHING，写入后不可变）。
# tech-doc/analytics/daily-sync-with-coverage.md §1.2
class AdDaily(Base):
    __tablename__ = "ad_daily"
    __table_args__ = (
        Index(
            "uq_ad_daily",
            "seller_id",
            "advertiser_id",
            "endpoint",
            "campaign_id",
            "product_id",
            "day",
            unique=True,
        ),
        Index(
            "idx_ad_daily_coverage",
            "seller_id",
            "advertiser_id",
            "endpoint",
            "campaign_id",
            "day",
        ),
        Index("idx_ad_daily_product_day", "product_id", "day"),
        {"schema": "analytics"},
    )

    id: Mapped[int] = mapped_column(
        BigInteger,
        primary_key=True,
        server_default=text("generate_always_as_identity()"),
    )
    seller_id: Mapped[str] = mapped_column(Text, nullable=False)
    advertiser_id: Mapped[str] = mapped_column(Text, nullable=False)
    campaign_id: Mapped[str] = mapped_column(Text, nullable=False)
    product_id: Mapped[str] = mapped_column(Text, nullable=False)
    endpoint: Mapped[str] = mapped_column(Text, nullable=False)
    day: Mapped[date] = mapped_column(Date, nullable=False)
    mixed_real_cost: Mapped[float | None] = mapped_column(Numeric(20, 4))
    onsite_roi2_shopping_sku: Mapped[int | None] = mapped_column(BigInteger)
    onsite_roi2_shopping_value: Mapped[float | None] = mapped_column(Numeric(20, 4))
    onsite_mixed_real_roi2_shopping: Mapped[float | None] = mapped_column(
        Numeric(20, 4)
    )
    metrics_extra: Mapped[dict | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )


# ad_monthly ──────────────────────────────────────────────────────────
# 月级结构化表（独立同步，不依赖 daily；TikTok API 传月初/月末返回月级聚合）。
# tech-doc/analytics/daily-sync-with-coverage.md §1.3
class AdMonthly(Base):
    __tablename__ = "ad_monthly"
    __table_args__ = (
        Index(
            "uq_ad_monthly",
            "seller_id",
            "advertiser_id",
            "endpoint",
            "campaign_id",
            "product_id",
            "year_month",
            unique=True,
        ),
        Index(
            "idx_ad_monthly_coverage",
            "seller_id",
            "advertiser_id",
            "endpoint",
            "campaign_id",
            "year_month",
        ),
        {"schema": "analytics"},
    )

    id: Mapped[int] = mapped_column(
        BigInteger,
        primary_key=True,
        server_default=text("generate_always_as_identity()"),
    )
    seller_id: Mapped[str] = mapped_column(Text, nullable=False)
    advertiser_id: Mapped[str] = mapped_column(Text, nullable=False)
    campaign_id: Mapped[str] = mapped_column(Text, nullable=False)
    product_id: Mapped[str] = mapped_column(Text, nullable=False)
    endpoint: Mapped[str] = mapped_column(Text, nullable=False)
    year_month: Mapped[str] = mapped_column(Text, nullable=False)
    mixed_real_cost: Mapped[float | None] = mapped_column(Numeric(20, 4))
    onsite_roi2_shopping_sku: Mapped[int | None] = mapped_column(BigInteger)
    onsite_roi2_shopping_value: Mapped[float | None] = mapped_column(Numeric(20, 4))
    onsite_mixed_real_roi2_shopping: Mapped[float | None] = mapped_column(
        Numeric(20, 4)
    )
    metrics_extra: Mapped[dict | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )


# ad_raw_log ──────────────────────────────────────────────────────────
# 原始请求日志（kind CHECK: daily/today/monthly）。纯日志表，不参与业务查询。
# 保留原始 request/response 用于调试、审计、数据恢复；建议 retention 90 天自动清理。
# tech-doc/analytics/daily-sync-with-coverage.md §1.4
class AdRawLog(Base):
    __tablename__ = "ad_raw_log"
    __table_args__ = (
        CheckConstraint(
            "kind IN ('daily', 'today', 'monthly')",
            name="ck_ad_raw_log_kind",
        ),
        Index("idx_ad_raw_log_day", "day"),
        Index("idx_ad_raw_log_request_id", "request_id"),
        {"schema": "analytics"},
    )

    id: Mapped[int] = mapped_column(
        BigInteger,
        primary_key=True,
        server_default=text("generate_always_as_identity()"),
    )
    seller_id: Mapped[str] = mapped_column(Text, nullable=False)
    advertiser_id: Mapped[str] = mapped_column(Text, nullable=False)
    endpoint: Mapped[str] = mapped_column(Text, nullable=False)
    campaign_id: Mapped[str | None] = mapped_column(Text)
    product_id: Mapped[str | None] = mapped_column(Text)
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    day: Mapped[date | None] = mapped_column(Date)
    year_month: Mapped[str | None] = mapped_column(Text)
    request_url: Mapped[str] = mapped_column(Text, nullable=False)
    request_method: Mapped[str] = mapped_column(Text, nullable=False)
    request_body: Mapped[dict | None] = mapped_column(JSONB)
    response_status: Mapped[int | None] = mapped_column(Integer)
    response_body: Mapped[dict | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    request_id: Mapped[str | None] = mapped_column(Text)
    source: Mapped[str | None] = mapped_column(Text)


# plugin_logs ────────────────────────────────────────────────────────
# 插件端日志上传表（Chrome 扩展运行时日志）。
# migration 0019；所有读写走 tts_erp_v2/analytics/repository.py（raw SQL）。
class PluginLog(Base):
    __tablename__ = "plugin_logs"
    __table_args__ = (
        CheckConstraint(
            "level IN ('info', 'warn', 'error')",
            name="ck_plugin_logs_level",
        ),
        Index("idx_plugin_logs_seller_time", "seller_id", "occurred_at"),
        Index("idx_plugin_logs_level", "level", "occurred_at"),
        {"schema": "analytics"},
    )

    id: Mapped[int] = mapped_column(
        BigInteger,
        primary_key=True,
        server_default=text("generate_always_as_identity()"),
    )
    seller_id: Mapped[str] = mapped_column(Text, nullable=False)
    advertiser_id: Mapped[str] = mapped_column(Text, nullable=False)
    plugin_version: Mapped[str] = mapped_column(Text, nullable=False)
    level: Mapped[str] = mapped_column(Text, nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    context: Mapped[dict | None] = mapped_column(JSONB)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("now()"),
        onupdate=text("now()"),
    )


__all__ = [
    "AdDaily",
    "AdMonthly",
    "AdRawLog",
    "AdToday",
    "PluginLog",
]
