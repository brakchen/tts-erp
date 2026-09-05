"""procurement.* — miaoshou procurement domain.

6 tables: procurement_accounts / procurement_products /
procurement_product_variants / purchase_orders / purchase_order_lines /
manual_product_costs.
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


class ProcurementAccount(Base):
    """Miaoshou license / supplier-side account."""

    __tablename__ = "procurement_accounts"
    __table_args__ = (
        UniqueConstraint(
            "provider",
            "external_account_id",
            name="uq_procurement_accounts_provider_ext",
        ),
        Index("ix_procurement_accounts_status", "status"),
        {"schema": "procurement"},
    )

    id: Mapped[int] = mapped_column(
        BigInteger,
        primary_key=True,
        server_default=text("generate_always_as_identity()"),
    )
    provider: Mapped[str] = mapped_column(Text, nullable=False)
    external_account_id: Mapped[str] = mapped_column(Text, nullable=False)
    account_name: Mapped[str | None] = mapped_column(Text)
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


class ProcurementProduct(Base):
    """Miaoshou-side procurement product (SPU-level)."""

    __tablename__ = "procurement_products"
    __table_args__ = (
        UniqueConstraint(
            "procurement_account_id",
            "external_product_id",
            name="uq_procurement_products_account_ext",
        ),
        Index("ix_procurement_products_status", "status"),
        Index("ix_procurement_products_product_type", "product_type"),
        {"schema": "procurement"},
    )

    id: Mapped[int] = mapped_column(
        BigInteger,
        primary_key=True,
        server_default=text("generate_always_as_identity()"),
    )
    procurement_account_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("procurement.procurement_accounts.id", ondelete="RESTRICT"),
        nullable=False,
    )
    external_product_id: Mapped[str] = mapped_column(Text, nullable=False)
    product_type: Mapped[str | None] = mapped_column(
        Text
    )  # COLLECTED_PRODUCT | PROCUREMENT_PRODUCT | SPU
    title: Mapped[str | None] = mapped_column(Text)
    source_platform: Mapped[str | None] = mapped_column(Text)
    source_item_id: Mapped[str | None] = mapped_column(Text)
    source_item_url: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str | None] = mapped_column(Text)
    raw_record_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("integration.raw_records.id", ondelete="SET NULL")
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


class ProcurementProductVariant(Base):
    """Miaoshou SKU. Empty in practice — only populated when miaoshou actually
    returns variant-level data."""

    __tablename__ = "procurement_product_variants"
    __table_args__ = (
        UniqueConstraint(
            "procurement_product_id",
            "external_variant_id",
            name="uq_procurement_variants_product_ext",
        ),
        Index("ix_procurement_variants_supplier_sku", "supplier_sku"),
        {"schema": "procurement"},
    )

    id: Mapped[int] = mapped_column(
        BigInteger,
        primary_key=True,
        server_default=text("generate_always_as_identity()"),
    )
    procurement_product_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("procurement.procurement_products.id", ondelete="RESTRICT"),
        nullable=False,
    )
    external_variant_id: Mapped[str] = mapped_column(Text, nullable=False)
    variant_name: Mapped[str | None] = mapped_column(Text)
    attributes: Mapped[dict | None] = mapped_column(JSONB)
    supplier_sku: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str | None] = mapped_column(Text)
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


class PurchaseOrder(Base):
    """Miaoshou purchase order header."""

    __tablename__ = "purchase_orders"
    __table_args__ = (
        UniqueConstraint(
            "procurement_account_id",
            "external_purchase_order_id",
            name="uq_purchase_orders_account_ext",
        ),
        Index("ix_purchase_orders_status", "status"),
        {"schema": "procurement"},
    )

    id: Mapped[int] = mapped_column(
        BigInteger,
        primary_key=True,
        server_default=text("generate_always_as_identity()"),
    )
    procurement_account_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("procurement.procurement_accounts.id", ondelete="RESTRICT"),
        nullable=False,
    )
    external_purchase_order_id: Mapped[str] = mapped_column(Text, nullable=False)
    supplier_id: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str | None] = mapped_column(Text)
    currency: Mapped[str | None] = mapped_column(Text)
    total_amount: Mapped[Decimal | None] = mapped_column(Numeric(20, 4))
    source_created_at: Mapped[datetime | None]
    source_updated_at: Mapped[datetime | None]
    paid_at: Mapped[datetime | None]
    completed_at: Mapped[datetime | None]
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


class PurchaseOrderLine(Base):
    """Miaoshou purchase order line."""

    __tablename__ = "purchase_order_lines"
    __table_args__ = (
        UniqueConstraint(
            "purchase_order_id",
            "external_line_id",
            name="uq_purchase_order_lines_order_ext",
        ),
        Index("ix_purchase_order_lines_product", "procurement_product_id"),
        {"schema": "procurement"},
    )

    id: Mapped[int] = mapped_column(
        BigInteger,
        primary_key=True,
        server_default=text("generate_always_as_identity()"),
    )
    purchase_order_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("procurement.purchase_orders.id", ondelete="RESTRICT"),
        nullable=False,
    )
    external_line_id: Mapped[str] = mapped_column(Text, nullable=False)
    procurement_product_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("procurement.procurement_products.id", ondelete="RESTRICT"),
        nullable=False,
    )
    procurement_product_variant_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("procurement.procurement_product_variants.id", ondelete="SET NULL"),
    )
    quantity: Mapped[Decimal | None] = mapped_column(Numeric(20, 4))
    unit_cost: Mapped[Decimal | None] = mapped_column(Numeric(20, 4))
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


class ManualProductCost(Base):
    """Operator-entered cost for a TikTok product. Historical rows are kept;
    the effective row per SPU is `valid_to IS NULL` (or the row with the most
    recent valid_from). Source of truth for cost_snapshots; priority over
    miaoshou purchase prices.
    """

    __tablename__ = "manual_product_costs"
    __table_args__ = (
        Index(
            "ix_manual_costs_channel_product_valid", "spu_pk", "valid_from"
        ),
        {"schema": "procurement"},
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
    unit_cost: Mapped[Decimal] = mapped_column(Numeric(20, 4), nullable=False)
    currency: Mapped[str] = mapped_column(Text, nullable=False)
    valid_from: Mapped[datetime] = mapped_column(
        nullable=False, server_default=text("now()")
    )
    valid_to: Mapped[datetime | None]
    note: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[str | None] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(
        nullable=False,
        server_default=text("now()"),
        onupdate=text("now()"),
    )
    created_at: Mapped[datetime] = mapped_column(
        nullable=False, server_default=text("now()")
    )
