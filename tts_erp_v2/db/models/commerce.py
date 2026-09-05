"""commerce.* — TikTok Shop sales domain.

5 tables: shops / products_spu / products_sku /
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

    __tablename__ = "shops"
    __table_args__ = (
        UniqueConstraint(
            "platform", "shop_id", name="uq_channel_accounts_platform_ext"
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
    shop_id: Mapped[str] = mapped_column(Text, nullable=False)
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

    __tablename__ = "products_spu"
    __table_args__ = (
        UniqueConstraint(
            "shop_pk",
            "spu_id",
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
    shop_pk: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("commerce.shops.id", ondelete="RESTRICT"),
        nullable=False,
    )
    spu_id: Mapped[str] = mapped_column(Text, nullable=False)
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

    __tablename__ = "products_sku"
    __table_args__ = (
        UniqueConstraint(
            "spu_pk",
            "sku_id",
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
    spu_pk: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("commerce.products_spu.id", ondelete="RESTRICT"),
        nullable=False,
    )
    sku_id: Mapped[str] = mapped_column(Text, nullable=False)
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
            "shop_pk",
            "order_id",
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
    shop_pk: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("commerce.shops.id", ondelete="RESTRICT"),
        nullable=False,
    )
    order_id: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str | None] = mapped_column(Text)
    currency: Mapped[str | None] = mapped_column(Text)
    payment_amount: Mapped[Decimal | None] = mapped_column(Numeric(20, 4))
    total_amount: Mapped[Decimal | None] = mapped_column(Numeric(20, 4))
    fulfillment_type: Mapped[str | None] = mapped_column(Text)
    order_time: Mapped[datetime | None]
    order_modify_time: Mapped[datetime | None]
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

    Foreign keys to products_spu / products_sku are
    intentionally nullable: when a line lands before its product is synced,
    the row still persists, and external_*_snapshot columns hold the truth
    for later join. NEVER auto-bind by title.
    """

    __tablename__ = "sales_order_lines"
    __table_args__ = (
        UniqueConstraint(
            "order_pk", "external_line_id", name="uq_sales_order_lines_order_ext"
        ),
        Index("ix_sales_order_lines_channel_product", "spu_pk"),
        Index("ix_sales_order_lines_channel_variant", "sku_pk"),
        {"schema": "commerce"},
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
    external_line_id: Mapped[str] = mapped_column(Text, nullable=False)
    spu_pk: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("commerce.products_spu.id", ondelete="SET NULL")
    )
    sku_pk: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("commerce.products_sku.id", ondelete="SET NULL"),
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
