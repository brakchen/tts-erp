"""plugin.* — Chrome 插件 dump 的全部落库表（12 张）。

订单/物流/结算（7 张）：
  raw_log + orders + order_lines + shipments + tracking_events
  + settlements + settlement_details

广告消耗 + 插件日志（5 张，2026-09-11 由 analytics schema 并入）：
  ad_today + ad_daily + ad_monthly + ad_raw_log + plugin_logs

数据来源：Chrome 扩展从 TikTok Seller Center 抓取的 HTTP 响应，
通过 /v2/order-sync/dumps 端点写入。raw_log 存完整原始 dump，
业务表存解析后的结构化数据。每张业务表有 log_id FK 回 raw_log 用于溯源。

详见 tech-doc/chrome-ext-order-sync-design.md。
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from tts_erp_v2.db.base import Base


# ── raw_log ─────────────────────────────────────────────────────────
# 同步流水日志。每条 dump 请求一行，只追加不修改。
class RawLog(Base):
    __tablename__ = "raw_log"
    __table_args__ = (
        Index("ix_raw_log_domain_shop", "domain", "shop_id"),
        Index("ix_raw_log_created", "created_at"),
        Index("ix_raw_log_endpoint", "endpoint"),
        {"schema": "plugin"},
    )

    id: Mapped[int] = mapped_column(
        BigInteger,
        primary_key=True,
        server_default=text("generate_always_as_identity()"),
    )
    domain: Mapped[str] = mapped_column(Text, nullable=False)
    shop_id: Mapped[str] = mapped_column(Text, nullable=False)
    endpoint: Mapped[str] = mapped_column(Text, nullable=False)
    captured_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    request_params: Mapped[dict | None] = mapped_column(JSONB)
    request_body: Mapped[dict | None] = mapped_column(JSONB)
    response_body: Mapped[dict] = mapped_column(JSONB, nullable=False)
    parse_error: Mapped[str | None] = mapped_column(Text)
    rows_written: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    source: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text("'chrome-ext'")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )


# ── orders ──────────────────────────────────────────────────────────
# 订单头，来自 order/list 响应。
class ChromeOrder(Base):
    __tablename__ = "orders"
    __table_args__ = (
        UniqueConstraint("shop_id", "order_id", name="uq_orders_shop_order"),
        Index("ix_orders_shop", "shop_id"),
        Index("ix_orders_status", "main_order_status"),
        {"schema": "plugin"},
    )

    id: Mapped[int] = mapped_column(
        BigInteger,
        primary_key=True,
        server_default=text("generate_always_as_identity()"),
    )
    log_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("plugin.raw_log.id"), nullable=False
    )
    shop_id: Mapped[str] = mapped_column(Text, nullable=False)
    order_id: Mapped[str] = mapped_column(Text, nullable=False)
    main_order_status: Mapped[int | None] = mapped_column(Integer)
    sku_display_status: Mapped[int | None] = mapped_column(Integer)
    currency: Mapped[str | None] = mapped_column(Text)
    payment_amount: Mapped[Decimal | None] = mapped_column(Numeric(20, 4))
    total_amount: Mapped[Decimal | None] = mapped_column(Numeric(20, 4))
    fulfillment_type: Mapped[int | None] = mapped_column(Integer)
    pay_method: Mapped[str | None] = mapped_column(Text)
    sale_region: Mapped[str | None] = mapped_column(Text)
    shipping_fee: Mapped[Decimal | None] = mapped_column(Numeric(20, 4))
    order_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    update_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    latest_rts_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    latest_tts_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    buyer_nickname: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )


# ── order_lines ─────────────────────────────────────────────────────
# 订单行（SKU 级），来自 order/list 的 sku_module/fulfill_line_module。
class ChromeOrderLine(Base):
    __tablename__ = "order_lines"
    __table_args__ = (
        UniqueConstraint(
            "shop_id", "order_id", "sku_id", name="uq_order_lines_order_sku"
        ),
        {"schema": "plugin"},
    )

    id: Mapped[int] = mapped_column(
        BigInteger,
        primary_key=True,
        server_default=text("generate_always_as_identity()"),
    )
    log_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("plugin.raw_log.id"), nullable=False
    )
    shop_id: Mapped[str] = mapped_column(Text, nullable=False)
    order_id: Mapped[str] = mapped_column(Text, nullable=False)
    sku_id: Mapped[str] = mapped_column(Text, nullable=False)
    product_id: Mapped[str | None] = mapped_column(Text)
    product_name: Mapped[str | None] = mapped_column(Text)
    variant_name: Mapped[str | None] = mapped_column(Text)
    image_url: Mapped[str | None] = mapped_column(Text)
    quantity: Mapped[Decimal | None] = mapped_column(Numeric(20, 4))
    unit_price: Mapped[Decimal | None] = mapped_column(Numeric(20, 4))
    total_price: Mapped[Decimal | None] = mapped_column(Numeric(20, 4))
    currency: Mapped[str | None] = mapped_column(Text)
    main_order_status: Mapped[int | None] = mapped_column(Integer)
    sku_display_status: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )


# ── shipments ───────────────────────────────────────────────────────
# 物流包裹，来自 logistic_detail/list 的 package_list[]。
class ChromeShipment(Base):
    __tablename__ = "shipments"
    __table_args__ = (
        UniqueConstraint("shop_id", "package_id", name="uq_shipments_shop_pkg"),
        Index("ix_shipments_order", "shop_id", "order_id"),
        {"schema": "plugin"},
    )

    id: Mapped[int] = mapped_column(
        BigInteger,
        primary_key=True,
        server_default=text("generate_always_as_identity()"),
    )
    log_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("plugin.raw_log.id"),
        nullable=False,
    )
    shop_id: Mapped[str] = mapped_column(Text, nullable=False)
    order_id: Mapped[str] = mapped_column(Text, nullable=False)
    package_id: Mapped[str] = mapped_column(Text, nullable=False)
    tracking_number: Mapped[str | None] = mapped_column(Text)
    carrier_name: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str | None] = mapped_column(Text)
    shipped_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )


# ── tracking_events ─────────────────────────────────────────────────
# 物流轨迹事件，来自 logistic_detail/list 的 track_list[]。
class ChromeTrackingEvent(Base):
    __tablename__ = "tracking_events"
    __table_args__ = (
        UniqueConstraint(
            "shop_id",
            "package_id",
            "event_key",
            name="uq_tracking_events_pkg_key",
        ),
        {"schema": "plugin"},
    )

    id: Mapped[int] = mapped_column(
        BigInteger,
        primary_key=True,
        server_default=text("generate_always_as_identity()"),
    )
    log_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("plugin.raw_log.id"),
        nullable=False,
    )
    shop_id: Mapped[str] = mapped_column(Text, nullable=False)
    package_id: Mapped[str] = mapped_column(Text, nullable=False)
    event_key: Mapped[str] = mapped_column(Text, nullable=False)
    event_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    description: Mapped[str | None] = mapped_column(Text)
    location: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )


# ── settlements ─────────────────────────────────────────────────────
# 结算单头，来自 statement/list/detail。
class ChromeSettlement(Base):
    __tablename__ = "settlements"
    __table_args__ = (
        UniqueConstraint(
            "shop_id",
            "statement_id",
            "statement_version",
            name="uq_settlements_shop_stmt",
        ),
        Index("ix_settlements_shop", "shop_id"),
        {"schema": "plugin"},
    )

    id: Mapped[int] = mapped_column(
        BigInteger,
        primary_key=True,
        server_default=text("generate_always_as_identity()"),
    )
    log_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("plugin.raw_log.id"),
        nullable=False,
    )
    shop_id: Mapped[str] = mapped_column(Text, nullable=False)
    statement_id: Mapped[str] = mapped_column(Text, nullable=False)
    statement_version: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    bill_period: Mapped[str | None] = mapped_column(Text)
    period_start: Mapped[date | None] = mapped_column(Date)
    period_end: Mapped[date | None] = mapped_column(Date)
    settlement_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    settlement_id: Mapped[str | None] = mapped_column(Text)
    payment_id: Mapped[str | None] = mapped_column(Text)
    payment_status: Mapped[str | None] = mapped_column(Text)
    statement_type: Mapped[int | None] = mapped_column(Integer)
    payment_pending_reason: Mapped[int | None] = mapped_column(Integer)
    settle_amount: Mapped[Decimal | None] = mapped_column(Numeric(20, 4))
    earning_amount: Mapped[Decimal | None] = mapped_column(Numeric(20, 4))
    fee_amount: Mapped[Decimal | None] = mapped_column(Numeric(20, 4))
    adjust_amount: Mapped[Decimal | None] = mapped_column(Numeric(20, 4))
    payable_amount: Mapped[Decimal | None] = mapped_column(Numeric(20, 4))
    shipping_amount: Mapped[Decimal | None] = mapped_column(Numeric(20, 4))
    total_reserve_amount: Mapped[Decimal | None] = mapped_column(Numeric(20, 4))
    currency: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )


# ── settlement_details ──────────────────────────────────────────────
# SKU 级结算明细 + 费用拆分，来自 statement/transaction/detail。
class ChromeSettlementDetail(Base):
    __tablename__ = "settlement_details"
    __table_args__ = (
        UniqueConstraint(
            "shop_id", "sku_detail_id", name="uq_settlement_details_shop_sku"
        ),
        Index("ix_settlement_details_stmt", "shop_id", "statement_id"),
        {"schema": "plugin"},
    )

    id: Mapped[int] = mapped_column(
        BigInteger,
        primary_key=True,
        server_default=text("generate_always_as_identity()"),
    )
    log_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("plugin.raw_log.id"),
        nullable=False,
    )
    shop_id: Mapped[str] = mapped_column(Text, nullable=False)
    statement_id: Mapped[str] = mapped_column(Text, nullable=False)
    statement_version: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    sku_detail_id: Mapped[str] = mapped_column(Text, nullable=False)
    trade_order_id: Mapped[str | None] = mapped_column(Text)
    sku_id: Mapped[str | None] = mapped_column(Text)
    product_name: Mapped[str | None] = mapped_column(Text)
    sku_name: Mapped[str | None] = mapped_column(Text)
    quantity: Mapped[Decimal | None] = mapped_column(Numeric(20, 4))
    settlement_status: Mapped[str | None] = mapped_column(Text)
    placed_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    settlement_amount: Mapped[Decimal | None] = mapped_column(Numeric(20, 4))
    earning_amount: Mapped[Decimal | None] = mapped_column(Numeric(20, 4))
    fees_amount: Mapped[Decimal | None] = mapped_column(Numeric(20, 4))
    currency: Mapped[str | None] = mapped_column(Text)
    fee_components: Mapped[dict | None] = mapped_column(JSONB)
    seller_web_cut_flow: Mapped[bool | None] = mapped_column(Boolean)
    seller_app_cut_flow: Mapped[bool | None] = mapped_column(Boolean)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )


# ── 广告消耗 dump（原 tts_erp_v2/db/models/analytics.py，2026-09-11 并入）───
# 表已在 plugin schema：ad_today / ad_daily / ad_monthly / ad_raw_log / plugin_logs


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
        {"schema": "plugin"},
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
        {"schema": "plugin"},
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
        {"schema": "plugin"},
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
        {"schema": "plugin"},
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
# migration 0019；所有读写走 tts_erp_v2/plugin/ads/repository.py（raw SQL）。
class PluginLog(Base):
    __tablename__ = "plugin_logs"
    __table_args__ = (
        CheckConstraint(
            "level IN ('info', 'warn', 'error')",
            name="ck_plugin_logs_level",
        ),
        Index("idx_plugin_logs_seller_time", "seller_id", "occurred_at"),
        Index("idx_plugin_logs_level", "level", "occurred_at"),
        {"schema": "plugin"},
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


class CampaignOptLog(Base):
    """广告操作日志表（campaign_opt_log_list）。

    记录推广计划的操作变更历史（谁在什么时间改了什么）。
    数据来源：Chrome 扩展同步 TikTok /oec_ads/shopping/v1/oec/stat/campaign_opt_log_list。
    """

    __tablename__ = "campaign_opt_logs"
    __table_args__ = (
        Index("idx_campaign_opt_logs_seller_time", "seller_id", "opt_time"),
        Index("idx_campaign_opt_logs_campaign", "campaign_id"),
        {"schema": "plugin"},
    )

    id: Mapped[int] = mapped_column(
        BigInteger,
        primary_key=True,
        server_default=text("generate_always_as_identity()"),
    )
    seller_id: Mapped[str] = mapped_column(Text, nullable=False)
    advertiser_id: Mapped[str] = mapped_column(Text, nullable=False)
    log_id: Mapped[str] = mapped_column(
        Text, nullable=False, unique=True
    )  # TikTok 操作日志 ID
    campaign_id: Mapped[str] = mapped_column(Text, nullable=False)  # object_id
    user: Mapped[str | None] = mapped_column(Text)  # 操作人
    opt_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    object_type: Mapped[str | None] = mapped_column(Text)  # 如 "推广系列"
    object_raw_type: Mapped[str | None] = mapped_column(Text)  # 如 "4"
    activity_details: Mapped[dict | None] = mapped_column(JSONB)  # 变更详情数组
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("now()"),
        onupdate=text("now()"),
    )


__all__ = [
    # 订单/物流/结算
    "RawLog",
    "ChromeOrder",
    "ChromeOrderLine",
    "ChromeShipment",
    "ChromeTrackingEvent",
    "ChromeSettlement",
    "ChromeSettlementDetail",
    # 广告消耗 + 插件日志
    "AdToday",
    "AdDaily",
    "AdMonthly",
    "AdRawLog",
    "PluginLog",
    # 广告操作日志
    "CampaignOptLog",
]
