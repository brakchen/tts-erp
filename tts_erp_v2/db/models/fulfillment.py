"""fulfillment.* — shipment headers and carrier tracking events."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    ForeignKey,
    Index,
    Integer,
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
        UniqueConstraint(
            "order_pk", "external_package_id", name="uq_shipments_order_ext"
        ),
        Index("ix_shipments_tracking_number", "tracking_number"),
        Index("ix_shipments_status", "status"),
        {"schema": "fulfillment"},
    )

    id: Mapped[int] = mapped_column(
        BigInteger,
        primary_key=True,
        server_default=text("generate_always_as_identity()"),
    )
    order_pk: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("commerce.sales_orders.id", ondelete="RESTRICT"),
        nullable=False,
    )
    external_package_id: Mapped[str] = mapped_column(Text, nullable=False)
    tracking_number: Mapped[str | None] = mapped_column(Text)
    provider_id: Mapped[str | None] = mapped_column(Text)
    provider_name: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str | None] = mapped_column(Text)
    shipped_at: Mapped[datetime | None]
    delivered_at: Mapped[datetime | None]
    raw_record_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("integration.raw_records.id", ondelete="SET NULL")
    )
    synced_at: Mapped[datetime] = mapped_column(
        nullable=False, server_default=text("now()")
    )

    updated_at: Mapped[datetime] = mapped_column(
        nullable=False,
        server_default=text("now()"),
        onupdate=text("now()"),
    )


class TrackingEvent(Base):
    """One carrier event in the life of a shipment. Most-recent event wins
    for current status."""

    __tablename__ = "tracking_events"
    __table_args__ = (
        UniqueConstraint(
            "shipment_id", "external_event_key", name="uq_tracking_events_shipment_key"
        ),
        Index("ix_tracking_events_event_at", "event_at"),
        {"schema": "fulfillment"},
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
    external_event_key: Mapped[str] = mapped_column(Text, nullable=False)
    action_code: Mapped[int | None] = mapped_column(Integer)
    event_at: Mapped[datetime | None]
    description: Mapped[str | None] = mapped_column(Text)
    location: Mapped[str | None] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(
        nullable=False,
        server_default=text("now()"),
        onupdate=text("now()"),
    )
    synced_at: Mapped[datetime] = mapped_column(
        nullable=False, server_default=text("now()")
    )
