"""fulfillment.* — multi-package logistics.

3 tables: shipments / shipment_lines / tracking_events.
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Optional

from sqlalchemy import (
    BigInteger,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    PrimaryKeyConstraint,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from tts_erp_v2.db.base import Base


class Shipment(Base):
    """A package belonging to a sales order. One order may have N shipments."""
    __tablename__ = "shipments"
    __table_args__ = (
        UniqueConstraint("sales_order_id", "external_package_id", name="uq_shipments_order_ext"),
        Index("ix_shipments_tracking_number", "tracking_number"),
        Index("ix_shipments_status", "status"),
        {"schema": "fulfillment"},
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, server_default=text("generate_always_as_identity()"))
    sales_order_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("commerce.sales_orders.id", ondelete="RESTRICT"), nullable=False)
    external_package_id: Mapped[str] = mapped_column(Text, nullable=False)
    tracking_number: Mapped[Optional[str]] = mapped_column(Text)
    provider_id: Mapped[Optional[str]] = mapped_column(Text)
    provider_name: Mapped[Optional[str]] = mapped_column(Text)
    status: Mapped[Optional[str]] = mapped_column(Text)
    shipped_at: Mapped[Optional[datetime]]
    delivered_at: Mapped[Optional[datetime]]
    raw_record_id: Mapped[Optional[int]] = mapped_column(BigInteger, ForeignKey("integration.raw_records.id", ondelete="SET NULL"))
    synced_at: Mapped[datetime] = mapped_column(nullable=False, server_default=text("now()"))


class ShipmentLine(Base):
    """Junction: which order lines ship in which shipment, and how many."""
    __tablename__ = "shipment_lines"
    __table_args__ = (
        PrimaryKeyConstraint("shipment_id", "sales_order_line_id", name="pk_shipment_lines"),
        {"schema": "fulfillment"},
    )

    shipment_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("fulfillment.shipments.id", ondelete="CASCADE"), nullable=False)
    sales_order_line_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("commerce.sales_order_lines.id", ondelete="RESTRICT"), nullable=False)
    quantity: Mapped[Optional[Decimal]] = mapped_column(Numeric(20, 4))


class TrackingEvent(Base):
    """One carrier event in the life of a shipment. Most-recent event wins
    for current status."""
    __tablename__ = "tracking_events"
    __table_args__ = (
        UniqueConstraint("shipment_id", "external_event_key", name="uq_tracking_events_shipment_key"),
        Index("ix_tracking_events_event_at", "event_at"),
        {"schema": "fulfillment"},
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, server_default=text("generate_always_as_identity()"))
    shipment_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("fulfillment.shipments.id", ondelete="CASCADE"), nullable=False)
    external_event_key: Mapped[str] = mapped_column(Text, nullable=False)
    action_code: Mapped[Optional[int]] = mapped_column(Integer)
    event_at: Mapped[Optional[datetime]]
    description: Mapped[Optional[str]] = mapped_column(Text)
    location: Mapped[Optional[str]] = mapped_column(Text)
    synced_at: Mapped[datetime] = mapped_column(nullable=False, server_default=text("now()"))
