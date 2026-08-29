"""reporting.profit_daily — rebuild product_profit_daily.

The rebuild is incremental on calculation_version: each run writes a new
``(channel_product_id, profit_date, calculation_version)`` row and
leaves older versions in place for forensics.

Revenue side: aggregate ``commerce.sales_order_lines`` joined to paid
``commerce.sales_orders`` whose ``paid_at`` falls on ``profit_date``.

Cost side: use the *latest effective* ProductCostSnapshot (valid_to IS
NULL or newest valid_from) for the channel_product. If no snapshot
exists, estimated_cogs is NULL and estimated_gross_profit is NULL.

This module does NOT compute platform fees / shipping / refunds — that
arrives with the finance-domain jobs in Lane C. Columns are NULL until
then.
"""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from tts_erp_v2.db.models import (
    ProductCostSnapshot,
    ProductProfitDaily,
    SalesOrder,
    SalesOrderLine,
)


def _latest_snapshot(
    session: Session, channel_product_id: int
) -> ProductCostSnapshot | None:
    """Newest effective snapshot for a SPU (valid_to IS NULL, max
    valid_from)."""
    return session.execute(
        select(ProductCostSnapshot)
        .where(ProductCostSnapshot.channel_product_id == channel_product_id)
        .where(ProductCostSnapshot.valid_to.is_(None))
        .order_by(ProductCostSnapshot.valid_from.desc())
        .limit(1)
    ).scalar_one_or_none()


def _next_calculation_version(session: Session) -> int:
    """Return max(calculation_version) + 1 (or 1 if none exist)."""
    current = session.execute(select(func.max(ProductProfitDaily.calculation_version))).scalar()
    return int(current or 0) + 1


def rebuild(session: Session, *, profit_date: date) -> list[ProductProfitDaily]:
    """Rebuild the profit rows for ``profit_date``. Returns the rows
    written in this run."""
    calculation_version = _next_calculation_version(session)
    calculated_at = datetime.utcnow()

    # Aggregate paid sales_order_lines by channel_product for the day.
    # SalesOrder.paid_at is timestamptz; we cast to date in SQL.
    rows = session.execute(
        select(
            SalesOrderLine.channel_product_id,
            func.coalesce(func.sum(SalesOrderLine.quantity), 0).label("units"),
            func.coalesce(
                func.sum(SalesOrderLine.quantity * SalesOrderLine.unit_price), 0
            ).label("revenue"),
            func.max(SalesOrder.currency).label("currency"),
        )
        .join(SalesOrder, SalesOrder.id == SalesOrderLine.sales_order_id)
        .where(SalesOrder.status == "PAID")
        .where(SalesOrder.paid_at.is_not(None))
        .where(func.date(SalesOrder.paid_at) == profit_date)
        .where(SalesOrderLine.channel_product_id.is_not(None))
        .group_by(SalesOrderLine.channel_product_id)
    ).all()

    out: list[ProductProfitDaily] = []
    for row in rows:
        cp_id = row.channel_product_id
        units = Decimal(row.units or 0)
        revenue = Decimal(row.revenue or 0)
        currency = row.currency or "USD"
        snap = _latest_snapshot(session, cp_id)
        cogs: Decimal | None = None
        profit: Decimal | None = None
        cost_method: str | None = None
        if snap is not None:
            cogs = (snap.unit_cost * units).quantize(Decimal("0.0001"))
            profit = (revenue - cogs).quantize(Decimal("0.0001"))
            cost_method = snap.cost_method

        pd_row = ProductProfitDaily(
            channel_product_id=cp_id,
            profit_date=profit_date,
            units_sold=units,
            gross_revenue=revenue.quantize(Decimal("0.01")),
            estimated_cogs=cogs,
            platform_fees=None,
            shipping_cost=None,
            refunds=None,
            estimated_gross_profit=profit,
            currency=currency,
            cost_method=cost_method,
            calculation_version=calculation_version,
            calculated_at=calculated_at,
        )
        session.add(pd_row)
        out.append(pd_row)

    session.flush()
    return out
