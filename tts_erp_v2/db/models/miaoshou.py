"""miaoshou.* — source-owned Miaoshou ERP data.

Miaoshou payloads stay in this schema. Cross-domain reporting may read these
rows, but ingestion must not project them into commerce/fulfillment tables.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Numeric,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from tts_erp_v2.db.base import Base


class MiaoshouPackageRawRecord(Base):
    """Immutable raw package payload captured from a Miaoshou endpoint."""

    __tablename__ = "package_raw_records"
    __table_args__ = (
        Index(
            "ix_miaoshou_package_raw_external",
            "credential_id",
            "external_package_id",
        ),
        Index("ix_miaoshou_package_raw_captured", "captured_at"),
        {"schema": "miaoshou"},
    )

    id: Mapped[int] = mapped_column(
        BigInteger,
        primary_key=True,
        server_default=text("generate_always_as_identity()"),
    )
    credential_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("integration.credentials.id", ondelete="SET NULL"),
    )
    external_package_id: Mapped[str | None] = mapped_column(Text)
    endpoint: Mapped[str] = mapped_column(Text, nullable=False)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    payload_hash: Mapped[str] = mapped_column(Text, nullable=False)
    captured_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )


class MiaoshouPackage(Base):
    """Latest normalized state for one Miaoshou package."""

    __tablename__ = "packages"
    __table_args__ = (
        UniqueConstraint(
            "credential_id",
            "external_package_id",
            name="uq_miaoshou_packages_credential_external",
        ),
        Index("ix_miaoshou_packages_order", "platform_order_sn"),
        Index("ix_miaoshou_packages_status", "app_package_status"),
        Index("ix_miaoshou_packages_source_updated", "source_updated_at"),
        {"schema": "miaoshou"},
    )

    id: Mapped[int] = mapped_column(
        BigInteger,
        primary_key=True,
        server_default=text("generate_always_as_identity()"),
    )
    credential_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("integration.credentials.id", ondelete="RESTRICT"),
        nullable=False,
    )
    external_package_id: Mapped[str] = mapped_column(Text, nullable=False)
    raw_record_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("miaoshou.package_raw_records.id", ondelete="RESTRICT"),
        nullable=False,
    )
    source_endpoint: Mapped[str] = mapped_column(Text, nullable=False)
    platform: Mapped[str | None] = mapped_column(Text)
    site: Mapped[str | None] = mapped_column(Text)
    shop_id: Mapped[str | None] = mapped_column(Text)
    shop_name: Mapped[str | None] = mapped_column(Text)
    shop_nick: Mapped[str | None] = mapped_column(Text)
    app_package_no: Mapped[str | None] = mapped_column(Text)
    app_package_status: Mapped[str | None] = mapped_column(Text)
    app_package_status_text: Mapped[str | None] = mapped_column(Text)
    platform_package_status: Mapped[str | None] = mapped_column(Text)
    fulfillment_type: Mapped[str | None] = mapped_column(Text)
    platform_order_sn: Mapped[str | None] = mapped_column(Text)
    platform_order_status: Mapped[str | None] = mapped_column(Text)
    currency: Mapped[str | None] = mapped_column(Text)
    logistics_no: Mapped[str | None] = mapped_column(Text)
    logistics_company: Mapped[str | None] = mapped_column(Text)
    logistics_product_id: Mapped[str | None] = mapped_column(Text)
    logistics_product_name: Mapped[str | None] = mapped_column(Text)
    source_created_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    source_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    shipped_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    order_info: Mapped[dict | None] = mapped_column(JSONB)
    consignee_info: Mapped[dict | None] = mapped_column(JSONB)
    logistics_info: Mapped[dict | None] = mapped_column(JSONB)
    last_mile_info: Mapped[dict | None] = mapped_column(JSONB)
    raw_payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    synced_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("now()"),
        onupdate=text("now()"),
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )


class MiaoshouPackageItem(Base):
    """A normal item contained in a Miaoshou package."""

    __tablename__ = "package_items"
    __table_args__ = (
        UniqueConstraint(
            "package_id",
            "external_package_item_id",
            name="uq_miaoshou_package_items_package_external",
        ),
        Index("ix_miaoshou_package_items_product", "platform_product_id"),
        Index("ix_miaoshou_package_items_sku", "platform_sku_id"),
        {"schema": "miaoshou"},
    )

    id: Mapped[int] = mapped_column(
        BigInteger,
        primary_key=True,
        server_default=text("generate_always_as_identity()"),
    )
    package_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("miaoshou.packages.id", ondelete="CASCADE"),
        nullable=False,
    )
    external_package_item_id: Mapped[str] = mapped_column(Text, nullable=False)
    external_order_item_id: Mapped[str | None] = mapped_column(Text)
    platform_order_item_index: Mapped[str | None] = mapped_column(Text)
    platform_product_id: Mapped[str | None] = mapped_column(Text)
    platform_sku_id: Mapped[str | None] = mapped_column(Text)
    platform_item_num: Mapped[str | None] = mapped_column(Text)
    platform_outer_sku_id: Mapped[str | None] = mapped_column(Text)
    title: Mapped[str | None] = mapped_column(Text)
    sku_name: Mapped[str | None] = mapped_column(Text)
    quantity: Mapped[Decimal | None] = mapped_column(Numeric(20, 4))
    original_price: Mapped[Decimal | None] = mapped_column(Numeric(20, 4))
    discounted_price: Mapped[Decimal | None] = mapped_column(Numeric(20, 4))
    image_url: Mapped[str | None] = mapped_column(Text)
    original_image_url: Mapped[str | None] = mapped_column(Text)
    raw_payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("true")
    )
    removed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    synced_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("now()"),
        onupdate=text("now()"),
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )


class MiaoshouPackageGiftItem(Base):
    """A gift item contained in a Miaoshou package."""

    __tablename__ = "package_gift_items"
    __table_args__ = (
        UniqueConstraint(
            "package_id",
            "external_gift_item_id",
            name="uq_miaoshou_package_gifts_package_external",
        ),
        {"schema": "miaoshou"},
    )

    id: Mapped[int] = mapped_column(
        BigInteger,
        primary_key=True,
        server_default=text("generate_always_as_identity()"),
    )
    package_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("miaoshou.packages.id", ondelete="CASCADE"),
        nullable=False,
    )
    external_gift_item_id: Mapped[str] = mapped_column(Text, nullable=False)
    goods_id: Mapped[str | None] = mapped_column(Text)
    goods_sku_id: Mapped[str | None] = mapped_column(Text)
    goods_name: Mapped[str | None] = mapped_column(Text)
    item_num: Mapped[str | None] = mapped_column(Text)
    sku_name: Mapped[str | None] = mapped_column(Text)
    goods_sku_outer_id: Mapped[str | None] = mapped_column(Text)
    quantity: Mapped[Decimal | None] = mapped_column(Numeric(20, 4))
    original_price: Mapped[Decimal | None] = mapped_column(Numeric(20, 4))
    discounted_price: Mapped[Decimal | None] = mapped_column(Numeric(20, 4))
    image_url: Mapped[str | None] = mapped_column(Text)
    raw_payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("true")
    )
    removed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    synced_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("now()"),
        onupdate=text("now()"),
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )


class MiaoshouSyncCursor(Base):
    """Incremental watermark owned by one Miaoshou credential/resource."""

    __tablename__ = "sync_cursors"
    __table_args__ = (
        UniqueConstraint(
            "credential_id",
            "resource",
            name="uq_miaoshou_sync_cursors_credential_resource",
        ),
        {"schema": "miaoshou"},
    )

    id: Mapped[int] = mapped_column(
        BigInteger,
        primary_key=True,
        server_default=text("generate_always_as_identity()"),
    )
    credential_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("integration.credentials.id", ondelete="CASCADE"),
        nullable=False,
    )
    resource: Mapped[str] = mapped_column(Text, nullable=False)
    cursor_value: Mapped[str | None] = mapped_column(Text)
    cursor_epoch_ms: Mapped[int | None] = mapped_column(BigInteger)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("now()"),
        onupdate=text("now()"),
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )


class MiaoshouSyncIssue(Base):
    """Miaoshou-owned parse/synchronization issue."""

    __tablename__ = "sync_issues"
    __table_args__ = (
        Index(
            "ix_miaoshou_sync_issues_resource_resolved",
            "resource",
            "resolved_at",
        ),
        {"schema": "miaoshou"},
    )

    id: Mapped[int] = mapped_column(
        BigInteger,
        primary_key=True,
        server_default=text("generate_always_as_identity()"),
    )
    credential_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("integration.credentials.id", ondelete="CASCADE"),
        nullable=False,
    )
    resource: Mapped[str] = mapped_column(Text, nullable=False)
    issue_type: Mapped[str] = mapped_column(Text, nullable=False)
    external_id: Mapped[str | None] = mapped_column(Text)
    details: Mapped[dict | None] = mapped_column(JSONB)
    detected_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("now()"),
        onupdate=text("now()"),
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )


__all__ = [
    "MiaoshouPackage",
    "MiaoshouPackageGiftItem",
    "MiaoshouPackageItem",
    "MiaoshouPackageRawRecord",
    "MiaoshouSyncCursor",
    "MiaoshouSyncIssue",
]
