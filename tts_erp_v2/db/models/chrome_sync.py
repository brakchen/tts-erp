"""chrome_sync.* — Chrome 扩展订单/物流/结算数据同步。

7 张表：raw_log + orders + order_lines + shipments + tracking_events
         + settlements + settlement_details。

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
        {"schema": "chrome_sync"},
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
        {"schema": "chrome_sync"},
    )

    id: Mapped[int] = mapped_column(
        BigInteger,
        primary_key=True,
        server_default=text("generate_always_as_identity()"),
    )
    log_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("chrome_sync.raw_log.id"), nullable=False
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
        {"schema": "chrome_sync"},
    )

    id: Mapped[int] = mapped_column(
        BigInteger,
        primary_key=True,
        server_default=text("generate_always_as_identity()"),
    )
    log_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("chrome_sync.raw_log.id"), nullable=False
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
        {"schema": "chrome_sync"},
    )

    id: Mapped[int] = mapped_column(
        BigInteger,
        primary_key=True,
        server_default=text("generate_always_as_identity()"),
    )
    log_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("chrome_sync.raw_log.id"),
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
        {"schema": "chrome_sync"},
    )

    id: Mapped[int] = mapped_column(
        BigInteger,
        primary_key=True,
        server_default=text("generate_always_as_identity()"),
    )
    log_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("chrome_sync.raw_log.id"),
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
        {"schema": "chrome_sync"},
    )

    id: Mapped[int] = mapped_column(
        BigInteger,
        primary_key=True,
        server_default=text("generate_always_as_identity()"),
    )
    log_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("chrome_sync.raw_log.id"),
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
        {"schema": "chrome_sync"},
    )

    id: Mapped[int] = mapped_column(
        BigInteger,
        primary_key=True,
        server_default=text("generate_always_as_identity()"),
    )
    log_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("chrome_sync.raw_log.id"),
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


__all__ = [
    "RawLog",
    "ChromeOrder",
    "ChromeOrderLine",
    "ChromeShipment",
    "ChromeTrackingEvent",
    "ChromeSettlement",
    "ChromeSettlementDetail",
]
