"""reporting.* — derived tables, rebuildable, versioned.

3 tables: product_cost_snapshots / product_profit_daily /
shipment_tracking_summary. All are deterministic functions of upstream
tables + effective_product_links view; the cost_snapshots job rebuilds
them with calculation_version monotonically incremented.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
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
                   WEIGHTED_AVERAGE_COST}. 1688 listing price is NOT a
    valid cost source. SPU with no available source ⇒ no row written,
    surfaced via LinkIssue or monitoring report.
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
