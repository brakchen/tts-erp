"""linkage.* — product/account linkage, evidence, overrides, issues.

6 tables: account_links / product_links / variant_links / link_evidence /
link_overrides / link_issues.

Plus 1 VIEW: effective_product_links (created via hand-written Alembic
migration since SQLAlchemy models don't express DDL views).

product_links carries the corrected uniqueness:
    UNIQUE (procurement_product_id, channel_product_id, valid_from)
which lets historical versions coexist without collision.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import (
    BigInteger,
    Boolean,
    ForeignKey,
    Index,
    Integer,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from tts_erp_v2.db.base import Base


class AccountLink(Base):
    """Miaoshou account ↔ TikTok shop."""
    __tablename__ = "account_links"
    __table_args__ = (
        UniqueConstraint("procurement_account_id", "channel_account_id", "external_relation_id", name="uq_account_links_triplet"),
        Index("ix_account_links_validity", "valid_from", "valid_to"),
        {"schema": "linkage"},
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, server_default=text("generate_always_as_identity()"))
    procurement_account_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("procurement.procurement_accounts.id", ondelete="RESTRICT"), nullable=False)
    channel_account_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("commerce.channel_accounts.id", ondelete="RESTRICT"), nullable=False)
    external_relation_id: Mapped[Optional[str]] = mapped_column(Text)
    status: Mapped[Optional[str]] = mapped_column(Text)
    valid_from: Mapped[datetime] = mapped_column(nullable=False, server_default=text("now()"))
    valid_to: Mapped[Optional[datetime]]
    source_updated_at: Mapped[Optional[datetime]]
    raw_record_id: Mapped[Optional[int]] = mapped_column(BigInteger, ForeignKey("integration.raw_records.id", ondelete="SET NULL"))


class ProductLink(Base):
    """Miaoshou product ↔ TikTok product. N:M, versioned."""
    __tablename__ = "product_links"
    __table_args__ = (
        # Corrected uniqueness per refactor-tech-plan-v2 §3.2:
        # (procurement, channel, valid_from) — historical versions coexist.
        UniqueConstraint("procurement_product_id", "channel_product_id", "valid_from", name="uq_product_links_pivot_validfrom"),
        Index("ix_product_links_status", "status"),
        Index("ix_product_links_channel_product", "channel_product_id"),
        {"schema": "linkage"},
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, server_default=text("generate_always_as_identity()"))
    procurement_product_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("procurement.procurement_products.id", ondelete="RESTRICT"), nullable=False)
    channel_product_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("commerce.channel_products.id", ondelete="RESTRICT"), nullable=False)
    external_relation_id: Mapped[Optional[str]] = mapped_column(Text)
    relation_type: Mapped[str] = mapped_column(Text, nullable=False)  # MIAOSHOU_PUBLISHED_TO_TIKTOK | MIAOSHOU_BOUND_TO_TIKTOK | MIAOSHOU_PROCUREMENT_SOURCE
    status: Mapped[Optional[str]] = mapped_column(Text)
    is_primary: Mapped[Optional[bool]] = mapped_column(Boolean)
    valid_from: Mapped[datetime] = mapped_column(nullable=False, server_default=text("now()"))
    valid_to: Mapped[Optional[datetime]]
    source_updated_at: Mapped[Optional[datetime]]
    raw_record_id: Mapped[Optional[int]] = mapped_column(BigInteger, ForeignKey("integration.raw_records.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(nullable=False, server_default=text("now()"))
    updated_at: Mapped[datetime] = mapped_column(nullable=False, server_default=text("now()"))


class VariantLink(Base):
    """Miaoshou variant ↔ TikTok variant. Empty unless miaoshou supplies SKU mapping."""
    __tablename__ = "variant_links"
    __table_args__ = (
        UniqueConstraint("procurement_product_variant_id", "channel_product_variant_id", "valid_from", name="uq_variant_links_pivot_validfrom"),
        Index("ix_variant_links_validity", "valid_from", "valid_to"),
        {"schema": "linkage"},
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, server_default=text("generate_always_as_identity()"))
    procurement_product_variant_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("procurement.procurement_product_variants.id", ondelete="RESTRICT"), nullable=False)
    channel_product_variant_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("commerce.channel_product_variants.id", ondelete="RESTRICT"), nullable=False)
    external_relation_id: Mapped[Optional[str]] = mapped_column(Text)
    status: Mapped[Optional[str]] = mapped_column(Text)
    valid_from: Mapped[datetime] = mapped_column(nullable=False, server_default=text("now()"))
    valid_to: Mapped[Optional[datetime]]
    raw_record_id: Mapped[Optional[int]] = mapped_column(BigInteger, ForeignKey("integration.raw_records.id", ondelete="SET NULL"))


class LinkEvidence(Base):
    """Provenance record for a product/variant link. Survives even when the
    originating task fails (evidence kept, link not created)."""
    __tablename__ = "link_evidence"
    __table_args__ = (
        Index("ix_link_evidence_product_link", "product_link_id"),
        Index("ix_link_evidence_variant_link", "variant_link_id"),
        {"schema": "linkage"},
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, server_default=text("generate_always_as_identity()"))
    product_link_id: Mapped[Optional[int]] = mapped_column(BigInteger, ForeignKey("linkage.product_links.id", ondelete="SET NULL"))
    variant_link_id: Mapped[Optional[int]] = mapped_column(BigInteger, ForeignKey("linkage.variant_links.id", ondelete="SET NULL"))
    evidence_type: Mapped[str] = mapped_column(Text, nullable=False)  # e.g. MOVE_COLLECT_TASK | BOUND_RECORD | ...
    source_table: Mapped[Optional[str]] = mapped_column(Text)
    source_external_id: Mapped[Optional[str]] = mapped_column(Text)
    evidence_payload: Mapped[Optional[dict]] = mapped_column(JSONB)
    observed_at: Mapped[datetime] = mapped_column(nullable=False, server_default=text("now()"))


class LinkOverride(Base):
    """Operator decision: ALLOW / DENY / PRIMARY for a (procurement, channel) pair.

    Priority for effective_product_links view:
        ALLOW/DENY/PRIMARY overrides  >  valid miaoshou product_link
    """
    __tablename__ = "link_overrides"
    __table_args__ = (
        UniqueConstraint("procurement_product_id", "channel_product_id", "valid_from", name="uq_link_overrides_pivot_validfrom"),
        Index("ix_link_overrides_decision", "decision"),
        {"schema": "linkage"},
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, server_default=text("generate_always_as_identity()"))
    procurement_product_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("procurement.procurement_products.id", ondelete="RESTRICT"), nullable=False)
    channel_product_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("commerce.channel_products.id", ondelete="RESTRICT"), nullable=False)
    decision: Mapped[str] = mapped_column(Text, nullable=False)  # ALLOW | DENY | PRIMARY
    reason: Mapped[Optional[str]] = mapped_column(Text)
    valid_from: Mapped[datetime] = mapped_column(nullable=False, server_default=text("now()"))
    valid_to: Mapped[Optional[datetime]]
    created_by: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(nullable=False, server_default=text("now()"))


class LinkIssue(Base):
    """Detected linkage anomaly. Surfaces in /v2/linkage/issues API."""
    __tablename__ = "link_issues"
    __table_args__ = (
        Index("ix_link_issues_type_resolved", "issue_type", "resolved_at"),
        {"schema": "linkage"},
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, server_default=text("generate_always_as_identity()"))
    issue_type: Mapped[str] = mapped_column(Text, nullable=False)  # PRODUCT_LINK_MISSING | MULTIPLE_PRIMARY_LINKS | SOURCE_LINK_CONFLICT | ACCOUNT_LINK_MISSING | VARIANT_LINK_MISSING | AMBIGUOUS_SOURCE
    procurement_product_id: Mapped[Optional[int]] = mapped_column(BigInteger, ForeignKey("procurement.procurement_products.id", ondelete="SET NULL"))
    channel_product_id: Mapped[Optional[int]] = mapped_column(BigInteger, ForeignKey("commerce.channel_products.id", ondelete="SET NULL"))
    candidate_count: Mapped[Optional[int]] = mapped_column(Integer)
    status: Mapped[Optional[str]] = mapped_column(Text)
    details: Mapped[Optional[dict]] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(nullable=False, server_default=text("now()"))
    resolved_at: Mapped[Optional[datetime]]
