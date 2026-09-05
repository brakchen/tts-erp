"""commerce.* — TikTok Shop sales domain.

5 tables: channel_accounts / channel_products / channel_product_variants /
sales_orders / sales_order_lines.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
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


class ChannelAccount(Base):
    """TikTok Shop store account."""

    __tablename__ = "channel_accounts"
    __table_args__ = (
        UniqueConstraint(
            "platform", "external_account_id", name="uq_channel_accounts_platform_ext"
        ),
        Index("ix_channel_accounts_status", "status"),
        {"schema": "commerce"},
    )

    id: Mapped[int] = mapped_column(
        BigInteger,
        primary_key=True,
        server_default=text("generate_always_as_identity()"),
    )
    platform: Mapped[str] = mapped_column(Text, nullable=False)
    external_account_id: Mapped[str] = mapped_column(Text, nullable=False)
    account_name: Mapped[str | None] = mapped_column(Text)
    region: Mapped[str | None] = mapped_column(Text)
    seller_type: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str | None] = mapped_column(Text)
    credential_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("integration.credentials.id", ondelete="SET NULL")
    )
    source_updated_at: Mapped[datetime | None]
    synced_at: Mapped[datetime] = mapped_column(
        nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        nullable=False,
        server_default=text("now()"),
        onupdate=text("now()"),
    )


class ChannelProduct(Base):
    """TikTok SPU (Product). NOT system-internal SKU."""

    __tablename__ = "channel_products"
    __table_args__ = (
        UniqueConstraint(
            "channel_account_id",
            "external_product_id",
            name="uq_channel_products_account_ext",
        ),
        Index("ix_channel_products_status", "status"),
        {"schema": "commerce"},
    )

    id: Mapped[int] = mapped_column(
        BigInteger,
        primary_key=True,
        server_default=text("generate_always_as_identity()"),
    )
    channel_account_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("commerce.channel_accounts.id", ondelete="RESTRICT"),
        nullable=False,
    )
    external_product_id: Mapped[str] = mapped_column(Text, nullable=False)
    title: Mapped[str | None] = mapped_column(Text)
    category_id: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str | None] = mapped_column(Text)
    main_image_url: Mapped[str | None] = mapped_column(Text)
    source_created_at: Mapped[datetime | None]
    source_updated_at: Mapped[datetime | None]
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


class ChannelProductVariant(Base):
    """TikTok SKU."""

    __tablename__ = "channel_product_variants"
    __table_args__ = (
        UniqueConstraint(
            "channel_product_id",
            "external_variant_id",
            name="uq_channel_variants_product_ext",
        ),
        Index("ix_channel_variants_seller_sku", "seller_sku"),
        {"schema": "commerce"},
    )

    id: Mapped[int] = mapped_column(
        BigInteger,
        primary_key=True,
        server_default=text("generate_always_as_identity()"),
    )
    channel_product_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("commerce.channel_products.id", ondelete="RESTRICT"),
        nullable=False,
    )
    external_variant_id: Mapped[str] = mapped_column(Text, nullable=False)
    seller_sku: Mapped[str | None] = mapped_column(Text)
    variant_name: Mapped[str | None] = mapped_column(Text)
    attributes: Mapped[dict | None] = mapped_column(JSONB)
    image_url: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str | None] = mapped_column(Text)
    source_updated_at: Mapped[datetime | None]
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


class SalesOrder(Base):
    """TikTok order header."""

    __tablename__ = "sales_orders"
    __table_args__ = (
        UniqueConstraint(
            "channel_account_id",
            "external_order_id",
            name="uq_sales_orders_account_ext",
        ),
        Index("ix_sales_orders_status", "status"),
        Index("ix_sales_orders_paid_at", "paid_at"),
        {"schema": "commerce"},
    )

    id: Mapped[int] = mapped_column(
        BigInteger,
        primary_key=True,
        server_default=text("generate_always_as_identity()"),
    )
    channel_account_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("commerce.channel_accounts.id", ondelete="RESTRICT"),
        nullable=False,
    )
    external_order_id: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str | None] = mapped_column(Text)
    currency: Mapped[str | None] = mapped_column(Text)
    payment_amount: Mapped[Decimal | None] = mapped_column(Numeric(20, 4))
    total_amount: Mapped[Decimal | None] = mapped_column(Numeric(20, 4))
    fulfillment_type: Mapped[str | None] = mapped_column(Text)
    source_created_at: Mapped[datetime | None]
    source_updated_at: Mapped[datetime | None]
    paid_at: Mapped[datetime | None]
    shipped_at: Mapped[datetime | None]
    delivered_at: Mapped[datetime | None]
    cancelled_at: Mapped[datetime | None]
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


class SalesOrderLine(Base):
    """Order line item. Snapshots name/variant/image at purchase time.

    Foreign keys to channel_products / channel_product_variants are
    intentionally nullable: when a line lands before its product is synced,
    the row still persists, and external_*_snapshot columns hold the truth
    for later join. NEVER auto-bind by title.
    """

    __tablename__ = "sales_order_lines"
    __table_args__ = (
        UniqueConstraint(
            "sales_order_id", "external_line_id", name="uq_sales_order_lines_order_ext"
        ),
        Index("ix_sales_order_lines_channel_product", "channel_product_id"),
        Index("ix_sales_order_lines_channel_variant", "channel_product_variant_id"),
        {"schema": "commerce"},
    )

    id: Mapped[int] = mapped_column(
        BigInteger,
        primary_key=True,
        server_default=text("generate_always_as_identity()"),
    )
    sales_order_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("commerce.sales_orders.id", ondelete="RESTRICT"),
        nullable=False,
    )
    external_line_id: Mapped[str] = mapped_column(Text, nullable=False)
    channel_product_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("commerce.channel_products.id", ondelete="SET NULL")
    )
    channel_product_variant_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("commerce.channel_product_variants.id", ondelete="SET NULL"),
    )
    external_product_id_snapshot: Mapped[str | None] = mapped_column(Text)
    external_variant_id_snapshot: Mapped[str | None] = mapped_column(Text)
    product_name_snapshot: Mapped[str | None] = mapped_column(Text)
    variant_name_snapshot: Mapped[str | None] = mapped_column(Text)
    image_url_snapshot: Mapped[str | None] = mapped_column(Text)
    quantity: Mapped[Decimal | None] = mapped_column(Numeric(20, 4))
    unit_price: Mapped[Decimal | None] = mapped_column(Numeric(20, 4))
    currency: Mapped[str | None] = mapped_column(Text)
    line_status: Mapped[str | None] = mapped_column(Text)
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
