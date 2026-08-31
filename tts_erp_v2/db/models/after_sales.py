"""after_sales.* — unified refund/cancellation/return cases.

2 tables: cases / case_lines. case_type ∈ {CANCELLATION, REFUND_ONLY, RETURN_AND_REFUND}.
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    Boolean,
    ForeignKey,
    Index,
    Numeric,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from tts_erp_v2.db.base import Base


class Case(Base):
    """Unified after-sales entry. One row per cancellation, refund-only,
    or return-and-refund request.
    """
    __tablename__ = "cases"
    __table_args__ = (
        UniqueConstraint("channel_account_id", "external_case_id", name="uq_cases_account_ext"),
        Index("ix_cases_sales_order", "sales_order_id"),
        Index("ix_cases_case_type_status", "case_type", "status"),
        {"schema": "after_sales"},
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, server_default=text("generate_always_as_identity()"))
    channel_account_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("commerce.channel_accounts.id", ondelete="RESTRICT"), nullable=False)
    sales_order_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("commerce.sales_orders.id", ondelete="RESTRICT"), nullable=False)
    external_case_id: Mapped[str] = mapped_column(Text, nullable=False)
    case_type: Mapped[str] = mapped_column(Text, nullable=False)  # CANCELLATION | REFUND_ONLY | RETURN_AND_REFUND
    status: Mapped[str | None] = mapped_column(Text)
    reason_code: Mapped[str | None] = mapped_column(Text)
    reason_text: Mapped[str | None] = mapped_column(Text)
    created_at_source: Mapped[datetime | None]
    updated_at_source: Mapped[datetime | None]
    raw_record_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("integration.raw_records.id", ondelete="SET NULL"))
    # Case-level refund amount. TikTok cancellations/returns put a
    # ``refund_amount`` object on the case payload itself, separate
    # from the per-line refund totals on case_lines. Captured here so
    # case-level reports (refund rate, GMV reconciliation) don't have
    # to SUM every line. Added in migration 0002_cases_refund_amount.
    refund_amount: Mapped[Decimal | None] = mapped_column(Numeric(20, 4))
    currency: Mapped[str | None] = mapped_column(Text)
    synced_at: Mapped[datetime] = mapped_column(nullable=False, server_default=text("now()"))


class CaseLine(Base):
    """Per-line breakdown of a case. Required for refund-per-SPU and
    return-rate analytics.
    """
    __tablename__ = "case_lines"
    __table_args__ = (
        UniqueConstraint("case_id", "external_case_line_id", name="uq_case_lines_case_ext"),
        Index("ix_case_lines_sales_order_line", "sales_order_line_id"),
        {"schema": "after_sales"},
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, server_default=text("generate_always_as_identity()"))
    case_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("after_sales.cases.id", ondelete="CASCADE"), nullable=False)
    sales_order_line_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("commerce.sales_order_lines.id", ondelete="RESTRICT"), nullable=False)
    external_case_line_id: Mapped[str | None] = mapped_column(Text)
    quantity: Mapped[Decimal | None] = mapped_column(Numeric(20, 4))
    refund_amount: Mapped[Decimal | None] = mapped_column(Numeric(20, 4))
    currency: Mapped[str | None] = mapped_column(Text)
    should_replenish_stock: Mapped[bool | None] = mapped_column(Boolean)
