"""finance.* — payouts / settlements / transactions / components.

4 tables: payouts / settlement_statements / settlement_transactions /
settlement_components. The 58-column raw statement lives in
integration.raw_records; settlement_components holds only NON-zero
amount lines (per refactor-tech-plan-v2 §3.2).
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    Date,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from tts_erp_v2.db.base import Base


class Payout(Base):
    """Money paid out to the seller. Statement is a child of payout."""

    __tablename__ = "payouts"
    __table_args__ = (
        UniqueConstraint(
            "channel_account_id", "external_payout_id", name="uq_payouts_account_ext"
        ),
        Index("ix_payouts_status", "status"),
        {"schema": "finance"},
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
    external_payout_id: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str | None] = mapped_column(Text)
    currency: Mapped[str | None] = mapped_column(Text)
    amount: Mapped[Decimal | None] = mapped_column(Numeric(20, 4))
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


class SettlementStatement(Base):
    """Statement header. Owns many transactions."""

    __tablename__ = "settlement_statements"
    __table_args__ = (
        UniqueConstraint(
            "payout_id",
            "external_statement_id",
            name="uq_settlement_statements_payout_ext",
        ),
        Index("ix_settlement_statements_statement_time", "statement_time"),
        {"schema": "finance"},
    )

    id: Mapped[int] = mapped_column(
        BigInteger,
        primary_key=True,
        server_default=text("generate_always_as_identity()"),
    )
    payout_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("finance.payouts.id", ondelete="RESTRICT"),
        nullable=False,
    )
    external_statement_id: Mapped[str] = mapped_column(Text, nullable=False)
    statement_time: Mapped[datetime | None]
    period_start: Mapped[date | None] = mapped_column(Date)
    period_end: Mapped[date | None] = mapped_column(Date)
    currency: Mapped[str | None] = mapped_column(Text)
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


class SettlementTransaction(Base):
    """One transaction row in a statement. May optionally link back to a
    sales order line or after-sales case.
    """

    __tablename__ = "settlement_transactions"
    __table_args__ = (
        UniqueConstraint(
            "settlement_statement_id",
            "external_transaction_id",
            name="uq_settlement_txn_stmt_ext",
        ),
        Index("ix_settlement_txn_sales_order", "sales_order_id"),
        Index("ix_settlement_txn_order_line", "sales_order_line_id"),
        Index("ix_settlement_txn_case", "after_sales_case_id"),
        {"schema": "finance"},
    )

    id: Mapped[int] = mapped_column(
        BigInteger,
        primary_key=True,
        server_default=text("generate_always_as_identity()"),
    )
    settlement_statement_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("finance.settlement_statements.id", ondelete="RESTRICT"),
        nullable=False,
    )
    external_transaction_id: Mapped[str] = mapped_column(Text, nullable=False)
    sales_order_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("commerce.sales_orders.id", ondelete="SET NULL")
    )
    sales_order_line_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("commerce.sales_order_lines.id", ondelete="SET NULL")
    )
    after_sales_case_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("after_sales.cases.id", ondelete="SET NULL")
    )
    transaction_time: Mapped[datetime | None]
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


class SettlementComponent(Base):
    """One non-zero amount line per transaction (e.g. GROSS_SALES,
    PLATFORM_COMMISSION). Wide-row 58-column raw lives in
    integration.raw_records and is reconstructable there; this table is
    the analytical EAV view.
    """

    __tablename__ = "settlement_components"
    __table_args__ = (
        UniqueConstraint(
            "transaction_id", "component_code", name="uq_settlement_components_txn_code"
        ),
        Index("ix_settlement_components_code", "component_code"),
        {"schema": "finance"},
    )

    id: Mapped[int] = mapped_column(
        BigInteger,
        primary_key=True,
        server_default=text("generate_always_as_identity()"),
    )
    transaction_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("finance.settlement_transactions.id", ondelete="CASCADE"),
        nullable=False,
    )
    component_code: Mapped[str] = mapped_column(Text, nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(20, 4), nullable=False)
    currency: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        nullable=False,
        server_default=text("now()"),
        onupdate=text("now()"),
    )
    source_order: Mapped[int | None] = mapped_column(
        Integer
    )  # original column index in raw, for traceability
    created_at: Mapped[datetime] = mapped_column(
        nullable=False, server_default=text("now()")
    )
