"""procurement.* — miaoshou procurement domain.

3 tables: procurement_accounts / procurement_products / manual_product_costs.
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
    # 货源价（采集层挂牌口径）：由 miaoshou.common_collect_box job 从妙手公共采集箱
    # 列表写入（`price` / `minSkuPrice` / `maxSkuPrice`）。这是 1688 货源标价，
    # 成本快照将它作为人工价之后的 SOURCE_PRICE 估算口径。
    source_unit_cost: Mapped[Decimal | None] = mapped_column(Numeric(20, 4))
    source_min_unit_cost: Mapped[Decimal | None] = mapped_column(Numeric(20, 4))
    source_max_unit_cost: Mapped[Decimal | None] = mapped_column(Numeric(20, 4))
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


class ManualProductCost(Base):
    """Operator-entered cost for a TikTok product. Historical rows are kept;
    the effective row per SPU is `valid_to IS NULL` (or the row with the most
    recent valid_from). Source of truth for cost_snapshots; priority over
    synchronized source-price estimates.
    """

    __tablename__ = "manual_product_costs"
    __table_args__ = (
        Index("ix_manual_costs_channel_product_valid", "spu_pk", "valid_from"),
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
