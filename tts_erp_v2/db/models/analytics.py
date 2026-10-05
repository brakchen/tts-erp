"""Analytics snapshot models."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    SmallInteger,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from tts_erp_v2.db.base import Base


class SpuDeteriorationAlert(Base):
    """Materialized, auditable shop × SPU deterioration decision."""

    __tablename__ = "spu_deterioration_alerts"
    __table_args__ = (
        UniqueConstraint(
            "shop_pk",
            "spu_pk",
            "anchor_date",
            "window_days",
            "layer",
            name="uq_spu_deterioration_alert_anchor",
        ),
        Index("ix_spu_deterioration_alert_latest", "anchor_date", "severity"),
        Index("ix_spu_deterioration_alert_shop", "shop_pk", "anchor_date"),
        Index("ix_spu_deterioration_alert_severity", "severity", "window_days"),
        {"schema": "analytics"},
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    shop_pk: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("commerce.shops.id", ondelete="RESTRICT"), nullable=False
    )
    spu_pk: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("commerce.products_spu.id", ondelete="RESTRICT"),
        nullable=False,
    )
    anchor_date: Mapped[date] = mapped_column(Date, nullable=False)
    window_days: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    layer: Mapped[str] = mapped_column(Text, nullable=False)
    severity: Mapped[str] = mapped_column(Text, nullable=False)
    state: Mapped[str] = mapped_column(Text, nullable=False)
    sample_status: Mapped[str] = mapped_column(Text, nullable=False)
    prior_roi: Mapped[Decimal | None] = mapped_column(Numeric(24, 12))
    current_roi: Mapped[Decimal | None] = mapped_column(Numeric(24, 12))
    roi_decline: Mapped[Decimal | None] = mapped_column(Numeric(24, 12))
    prior_net_profit_cny: Mapped[Decimal | None] = mapped_column(Numeric(24, 6))
    current_net_profit_cny: Mapped[Decimal | None] = mapped_column(Numeric(24, 6))
    net_profit_decline: Mapped[Decimal | None] = mapped_column(Numeric(24, 12))
    previous_spend_cny: Mapped[Decimal | None] = mapped_column(Numeric(24, 6))
    current_spend_cny: Mapped[Decimal | None] = mapped_column(Numeric(24, 6))
    previous_order_count: Mapped[int | None] = mapped_column(Integer)
    current_order_count: Mapped[int | None] = mapped_column(Integer)
    previous_ad_orders: Mapped[int | None] = mapped_column(Integer)
    current_ad_orders: Mapped[int | None] = mapped_column(Integer)
    effective_config_source: Mapped[str] = mapped_column(Text, nullable=False)
    effective_config_version: Mapped[int | None] = mapped_column(Integer)
    effective_config_updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    effective_config_updated_by: Mapped[str | None] = mapped_column(Text)
    config_payload_hash: Mapped[str] = mapped_column(Text, nullable=False)
    basis_calculated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
