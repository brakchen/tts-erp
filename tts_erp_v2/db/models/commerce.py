"""commerce.* — TikTok Shop sales domain.

5 tables: channel_accounts / channel_products / channel_product_variants /
sales_orders / sales_order_lines.
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Optional

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
        UniqueConstraint("platform", "external_account_id", name="uq_channel_accounts_platform_ext"),
        Index("ix_channel_accounts_status", "status"),
        {"schema": "commerce"},
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, server_default=text("generate_always_as_identity()"))
    platform: Mapped[str] = mapped_column(Text, nullable=False)
    external_account_id: Mapped[str] = mapped_column(Text, nullable=False)
    account_name: Mapped[Optional[str]] = mapped_column(Text)
    region: Mapped[Optional[str]] = mapped_column(Text)
    seller_type: Mapped[Optional[str]] = mapped_column(Text)
    status: Mapped[Optional[str]] = mapped_column(Text)
    credential_id: Mapped[Optional[int]] = mapped_column(BigInteger, ForeignKey("integration.credentials.id", ondelete="SET NULL"))
    source_updated_at: Mapped[Optional[datetime]]
    synced_at: Mapped[datetime] = mapped_column(nullable=False, server_default=text("now()"))


class ChannelProduct(Base):
    """TikTok SPU (Product). NOT system-internal SKU."""
    __tablename__ = "channel_products"
    __table_args__ = (
        UniqueConstraint("channel_account_id", "external_product_id", name="uq_channel_products_account_ext"),
        Index("ix_channel_products_status", "status"),
        {"schema": "commerce"},
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, server_default=text("generate_always_as_identity()"))
    channel_account_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("commerce.channel_accounts.id", ondelete="RESTRICT"), nullable=False)
    external_product_id: Mapped[str] = mapped_column(Text, nullable=False)
    title: Mapped[Optional[str]] = mapped_column(Text)
    category_id: Mapped[Optional[str]] = mapped_column(Text)
    status: Mapped[Optional[str]] = mapped_column(Text)
    main_image_url: Mapped[Optional[str]] = mapped_column(Text)
    source_created_at: Mapped[Optional[datetime]]
    source_updated_at: Mapped[Optional[datetime]]
    raw_record_id: Mapped[Optional[int]] = mapped_column(BigInteger, ForeignKey("integration.raw_records.id", ondelete="SET NULL"))
    synced_at: Mapped[datetime] = mapped_column(nullable=False, server_default=text("now()"))


class ChannelProductVariant(Base):
    """TikTok SKU."""
    __tablename__ = "channel_product_variants"
    __table_args__ = (
        UniqueConstraint("channel_product_id", "external_variant_id", name="uq_channel_variants_product_ext"),
        Index("ix_channel_variants_seller_sku", "seller_sku"),
        {"schema": "commerce"},
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, server_default=text("generate_always_as_identity()"))
    channel_product_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("commerce.channel_products.id", ondelete="RESTRICT"), nullable=False)
    external_variant_id: Mapped[str] = mapped_column(Text, nullable=False)
    seller_sku: Mapped[Optional[str]] = mapped_column(Text)
    variant_name: Mapped[Optional[str]] = mapped_column(Text)
    attributes: Mapped[Optional[dict]] = mapped_column(JSONB)
    image_url: Mapped[Optional[str]] = mapped_column(Text)
    status: Mapped[Optional[str]] = mapped_column(Text)
    source_updated_at: Mapped[Optional[datetime]]
    raw_record_id: Mapped[Optional[int]] = mapped_column(BigInteger, ForeignKey("integration.raw_records.id", ondelete="SET NULL"))
    synced_at: Mapped[datetime] = mapped_column(nullable=False, server_default=text("now()"))


class SalesOrder(Base):
    """TikTok order header."""
    __tablename__ = "sales_orders"
    __table_args__ = (
        UniqueConstraint("channel_account_id", "external_order_id", name="uq_sales_orders_account_ext"),
        Index("ix_sales_orders_status", "status"),
        Index("ix_sales_orders_paid_at", "paid_at"),
        {"schema": "commerce"},
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, server_default=text("generate_always_as_identity()"))
    channel_account_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("commerce.channel_accounts.id", ondelete="RESTRICT"), nullable=False)
    external_order_id: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[Optional[str]] = mapped_column(Text)
    currency: Mapped[Optional[str]] = mapped_column(Text)
    payment_amount: Mapped[Optional[Decimal]] = mapped_column(Numeric(20, 4))
    total_amount: Mapped[Optional[Decimal]] = mapped_column(Numeric(20, 4))
    fulfillment_type: Mapped[Optional[str]] = mapped_column(Text)
    source_created_at: Mapped[Optional[datetime]]
    source_updated_at: Mapped[Optional[datetime]]
    paid_at: Mapped[Optional[datetime]]
    shipped_at: Mapped[Optional[datetime]]
    delivered_at: Mapped[Optional[datetime]]
    cancelled_at: Mapped[Optional[datetime]]
    raw_record_id: Mapped[Optional[int]] = mapped_column(BigInteger, ForeignKey("integration.raw_records.id", ondelete="SET NULL"))
    synced_at: Mapped[datetime] = mapped_column(nullable=False, server_default=text("now()"))


class SalesOrderLine(Base):
    """Order line item. Snapshots name/variant/image at purchase time.

    Foreign keys to channel_products / channel_product_variants are
    intentionally nullable: when a line lands before its product is synced,
    the row still persists, and external_*_snapshot columns hold the truth
    for later join. NEVER auto-bind by title.
    """
    __tablename__ = "sales_order_lines"
    __table_args__ = (
        UniqueConstraint("sales_order_id", "external_line_id", name="uq_sales_order_lines_order_ext"),
        Index("ix_sales_order_lines_channel_product", "channel_product_id"),
        Index("ix_sales_order_lines_channel_variant", "channel_product_variant_id"),
        {"schema": "commerce"},
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, server_default=text("generate_always_as_identity()"))
    sales_order_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("commerce.sales_orders.id", ondelete="RESTRICT"), nullable=False)
    external_line_id: Mapped[str] = mapped_column(Text, nullable=False)
    channel_product_id: Mapped[Optional[int]] = mapped_column(BigInteger, ForeignKey("commerce.channel_products.id", ondelete="SET NULL"))
    channel_product_variant_id: Mapped[Optional[int]] = mapped_column(BigInteger, ForeignKey("commerce.channel_product_variants.id", ondelete="SET NULL"))
    external_product_id_snapshot: Mapped[Optional[str]] = mapped_column(Text)
    external_variant_id_snapshot: Mapped[Optional[str]] = mapped_column(Text)
    product_name_snapshot: Mapped[Optional[str]] = mapped_column(Text)
    variant_name_snapshot: Mapped[Optional[str]] = mapped_column(Text)
    image_url_snapshot: Mapped[Optional[str]] = mapped_column(Text)
    quantity: Mapped[Optional[Decimal]] = mapped_column(Numeric(20, 4))
    unit_price: Mapped[Optional[Decimal]] = mapped_column(Numeric(20, 4))
    currency: Mapped[Optional[str]] = mapped_column(Text)
    line_status: Mapped[Optional[str]] = mapped_column(Text)
    raw_record_id: Mapped[Optional[int]] = mapped_column(BigInteger, ForeignKey("integration.raw_records.id", ondelete="SET NULL"))
    synced_at: Mapped[datetime] = mapped_column(nullable=False, server_default=text("now()"))
