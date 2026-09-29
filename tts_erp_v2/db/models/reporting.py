"""reporting.* — derived tables, rebuildable, versioned.

4 tables: product_cost_snapshots / product_profit_daily /
shipment_tracking_summary / shop_fee_rate_estimates. All are deterministic
functions of upstream tables + effective_product_links view; the
cost_snapshots job rebuilds them with calculation_version monotonically
incremented.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Date,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from tts_erp_v2.db.base import Base


class ProductCostSnapshot(Base):
    """Resolved unit cost for a TikTok product at a point in time.

    cost_method ∈ {MANUAL_ENTRY, LATEST_PURCHASE_COST, PERIOD_AVERAGE_COST,
                   WEIGHTED_AVERAGE_COST, SOURCE_PRICE}。SOURCE_PRICE = 货源价
    （procurement_products.source_unit_cost，公共采集箱挂牌价）兜底估算口径，
    报表须标注“估算成本”，成交后与采购单口径对账。SPU 无任何可用口径
    ⇒ 不写行，经 monitoring / active_spus_without_cost 暴露。
    """

    __tablename__ = "product_cost_snapshots"
    __table_args__ = (
        UniqueConstraint(
            "spu_pk",
            "valid_from",
            "calculation_version",
            name="uq_cost_snapshots_pivot_version",
        ),
        Index("ix_cost_snapshots_method", "cost_method"),
        {"schema": "reporting"},
    )

    id: Mapped[int] = mapped_column(
        BigInteger,
        primary_key=True,
        server_default=text("generate_always_as_identity()"),
    )
    spu_pk: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("commerce.products_spu.id", ondelete="RESTRICT"),
        nullable=False,
    )
    cost_method: Mapped[str] = mapped_column(Text, nullable=False)
    unit_cost: Mapped[Decimal] = mapped_column(Numeric(20, 4), nullable=False)
    currency: Mapped[str] = mapped_column(Text, nullable=False)
    valid_from: Mapped[datetime] = mapped_column(
        nullable=False, server_default=text("now()")
    )
    valid_to: Mapped[datetime | None]
    source_purchase_quantity: Mapped[Decimal | None] = mapped_column(Numeric(20, 4))
    source_purchase_amount: Mapped[Decimal | None] = mapped_column(Numeric(20, 4))
    source_line_count: Mapped[int | None] = mapped_column(Integer)
    calculation_version: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("1")
    )
    calculated_at: Mapped[datetime] = mapped_column(
        nullable=False, server_default=text("now()")
    )

    created_at: Mapped[datetime] = mapped_column(
        nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        nullable=False,
        server_default=text("now()"),
        onupdate=text("now()"),
    )


class ProductProfitDaily(Base):
    """Per-(day, SPU) estimated revenue/cost/profit. Rebuildable via the
    reporting job; the previous version is retained via calculation_version
    when overlap is needed for forensics.
    """

    __tablename__ = "product_profit_daily"
    __table_args__ = (
        UniqueConstraint(
            "spu_pk",
            "profit_date",
            "calculation_version",
            name="uq_profit_daily_pivot_version",
        ),
        Index("ix_profit_daily_profit_date", "profit_date"),
        {"schema": "reporting"},
    )

    id: Mapped[int] = mapped_column(
        BigInteger,
        primary_key=True,
        server_default=text("generate_always_as_identity()"),
    )
    spu_pk: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("commerce.products_spu.id", ondelete="RESTRICT"),
        nullable=False,
    )
    profit_date: Mapped[date] = mapped_column(Date, nullable=False)
    units_sold: Mapped[Decimal | None] = mapped_column(Numeric(20, 4))
    gross_revenue: Mapped[Decimal | None] = mapped_column(Numeric(20, 4))
    estimated_cogs: Mapped[Decimal | None] = mapped_column(Numeric(20, 4))
    platform_fees: Mapped[Decimal | None] = mapped_column(Numeric(20, 4))
    shipping_cost: Mapped[Decimal | None] = mapped_column(Numeric(20, 4))
    refunds: Mapped[Decimal | None] = mapped_column(Numeric(20, 4))
    estimated_gross_profit: Mapped[Decimal | None] = mapped_column(Numeric(20, 4))
    currency: Mapped[str | None] = mapped_column(Text)
    cost_method: Mapped[str | None] = mapped_column(Text)
    calculation_version: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("1")
    )
    calculated_at: Mapped[datetime] = mapped_column(
        nullable=False, server_default=text("now()")
    )

    created_at: Mapped[datetime] = mapped_column(
        nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        nullable=False,
        server_default=text("now()"),
        onupdate=text("now()"),
    )


class ShipmentTrackingSummary(Base):
    """Denormalized tracking roll-up rebuilt from tracking_events per shipment.

    Replaces the legacy `logistics_tracking` wide-row table.
    """

    __tablename__ = "shipment_tracking_summary"
    __table_args__ = (
        UniqueConstraint(
            "shipment_id",
            "calculation_version",
            name="uq_tracking_summary_shipment_version",
        ),
        {"schema": "reporting"},
    )

    id: Mapped[int] = mapped_column(
        BigInteger,
        primary_key=True,
        server_default=text("generate_always_as_identity()"),
    )
    shipment_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("fulfillment.shipments.id", ondelete="CASCADE"),
        nullable=False,
    )
    tracking_number: Mapped[str | None] = mapped_column(Text)
    first_event_at: Mapped[datetime | None]
    last_event_at: Mapped[datetime | None]
    last_event_description: Mapped[str | None] = mapped_column(Text)
    last_location: Mapped[str | None] = mapped_column(Text)
    event_count: Mapped[int | None] = mapped_column(Integer)
    calculation_version: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("1")
    )
    created_at: Mapped[datetime] = mapped_column(
        nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        nullable=False,
        server_default=text("now()"),
        onupdate=text("now()"),
    )
    calculated_at: Mapped[datetime] = mapped_column(
        nullable=False, server_default=text("now()")
    )


class ShopFeeRateEstimate(Base):
    """Per-(shop, day) measured platform commission rate for spu-roi estimation.

    口径：``fee_rate = Σ|FEE| / Σ line_gmv``，作用域**只限未退款(kept)订单**。
    ``line_gmv`` = 订单行 ``quantity × unit_price`` = **客户实付（折扣后）**；
    **不是** ``GROSS_SALES``（那是折扣前挂牌价，实测是 line_gmv 的 169%）。
    ``FEE`` 是交易级平台总扣除（``fee_amount``：已含抽佣 + 联盟 + 运费类），
    **不是** ``PLATFORM_COMMISSION``（仅为抽佣分项）。

    ⚠️ **为何必须排除退款订单**（2026-09-29 生产实测反证）：页面公式
    ``unsettled_net = line_gmv × (1−r̂) × (1−退款率)`` 里 ``(1−退款率)``
    已单独扣过一次退款；若 r̂ 的样本里再混入全额退款订单（其 FEE 仅 ~3%），
    退款效应被算两遍。生产反证：kept 口径预测误差 ±1%，混合物口径高估 ~16%。

    生产库逐单验证（中位残差 0.000%）::

        SETTLEMENT ≈ line_gmv + FEE + CUSTOMER_REFUND

    即 FEE 已覆盖运费类，**不可再加运费分项**（会重复扣）。

    ``kept_share`` = ``kept_line_gmv / window_line_gmv``，余量 = 退款订单 +
    缺 FEE 分项的订单；**仅供观测**（前端费率卡会显示），不作门槛 ——
    用户拍板：窗口内只要有一单已结算就算。
    """

    __tablename__ = "shop_fee_rate_estimates"
    __table_args__ = (
        UniqueConstraint(
            "shop_pk",
            "calculated_on",
            name="uq_shop_fee_rate_est_shop_day",
        ),
        CheckConstraint(
            "fee_rate >= 0 AND fee_rate <= 1",
            name="ck_shop_fee_rate_est_rate",
        ),
        CheckConstraint(
            "kept_share >= 0 AND kept_share <= 1",
            name="ck_shop_fee_rate_est_kept_share",
        ),
        Index("ix_shop_fee_rate_est_shop_calc_at", "shop_pk", "calculated_at"),
        {"schema": "reporting"},
    )

    id: Mapped[int] = mapped_column(
        BigInteger,
        primary_key=True,
        server_default=text("generate_always_as_identity()"),
    )
    shop_pk: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("commerce.shops.id", ondelete="CASCADE"),
        nullable=False,
    )
    calculated_on: Mapped[date] = mapped_column(Date, nullable=False)
    lookback_days: Mapped[int] = mapped_column(Integer, nullable=False)
    fee_rate: Mapped[Decimal] = mapped_column(Numeric(8, 6), nullable=False)
    kept_order_count: Mapped[int] = mapped_column(Integer, nullable=False)
    kept_line_gmv: Mapped[Decimal] = mapped_column(Numeric(20, 4), nullable=False)
    window_line_gmv: Mapped[Decimal] = mapped_column(Numeric(20, 4), nullable=False)
    kept_share: Mapped[Decimal] = mapped_column(Numeric(8, 6), nullable=False)
    total_fee: Mapped[Decimal] = mapped_column(Numeric(20, 4), nullable=False)
    currency: Mapped[str] = mapped_column(Text, nullable=False)
    calculation_version: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text("'fee-v1'")
    )
    calculated_at: Mapped[datetime] = mapped_column(
        nullable=False, server_default=text("now()")
    )

    created_at: Mapped[datetime] = mapped_column(
        nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        nullable=False,
        server_default=text("now()"),
        onupdate=text("now()"),
    )
