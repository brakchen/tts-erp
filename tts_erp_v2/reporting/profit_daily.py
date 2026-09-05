"""reporting.profit_daily — rebuild product_profit_daily.

The rebuild is incremental on calculation_version: each run writes a new
``(spu_pk, profit_date, calculation_version)`` row and
leaves older versions in place for forensics.

Revenue side: aggregate ``commerce.sales_order_lines`` joined to paid
``commerce.sales_orders`` whose ``paid_at`` falls on ``profit_date``.
"Paid" is determined by a whitelist of fulfilment-lifecycle statuses
that all imply the customer has paid — see ``PAID_SALES_ORDER_STATUSES``
in ``tts_erp_v2.db.constants``. TikTok does NOT use a literal 'PAID'
status; matching ``status == 'PAID'`` would always be empty.

Cost side: use the *latest effective* ProductCostSnapshot (valid_to IS
NULL or newest valid_from) for the channel_product. If no snapshot
exists, estimated_cogs is NULL and estimated_gross_profit is NULL.

Currency safety
---------------
Production orders are denominated in VND (TikTok Shop Vietnam) while
manual / 妙手 costs are entered in CNY. There is no FX table yet, so
mixing currencies in ``gross_revenue - estimated_cogs`` produces
nonsense (different magnitudes — see audit P1-4b). When the snapshot
currency does NOT match the order currency, the row is still written
(``gross_revenue`` is honest — that's what TikTok paid us), but
``estimated_cogs`` and ``estimated_gross_profit`` are forced to NULL
and a ``SyncIssue`` is recorded. This is a transition strategy:
once an FX table exists, we can compute a real conversion. Until then
the rule is "better to admit we don't know than to print a fake profit
that's off by 3 orders of magnitude".

This module does NOT compute platform fees / shipping / refunds — that
arrives with the finance-domain jobs. Columns are NULL until then.
"""

from __future__ import annotations

import logging
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from tts_erp_v2.db.constants import PAID_SALES_ORDER_STATUSES
from tts_erp_v2.db.models import (
    ProductCostSnapshot,
    ProductProfitDaily,
    SalesOrder,
    SalesOrderLine,
    SyncIssue,
)

log = logging.getLogger("tts_erp_v2.reporting.profit_daily")


def _latest_snapshot(
    session: Session, spu_pk: int
) -> ProductCostSnapshot | None:
    """Newest effective snapshot for a SPU (valid_to IS NULL, max
    valid_from)."""
    return session.execute(
        select(ProductCostSnapshot)
        .where(ProductCostSnapshot.spu_pk == spu_pk)
        .where(ProductCostSnapshot.valid_to.is_(None))
        .order_by(ProductCostSnapshot.valid_from.desc())
        .limit(1)
    ).scalar_one_or_none()


def _next_calculation_version(session: Session) -> int:
    """Return max(calculation_version) + 1 (or 1 if none exist)."""
    current = session.execute(
        select(func.max(ProductProfitDaily.calculation_version))
    ).scalar()
    return int(current or 0) + 1


def _record_currency_mismatch(
    session: Session,
    *,
    spu_pk: int,
    profit_date: date,
    order_currency: str | None,
    snapshot_currency: str | None,
) -> None:
    """Record a SyncIssue for a SPU whose order currency disagrees with
    its cost snapshot currency. The caller is responsible for writing
    the row with NULL estimated_cogs / estimated_gross_profit.

    ``integration.sync_issues`` is monitored by ops (and surfaced via
    /v2/admin/...), so adding a row is the production-blessed way to
    flag "we skipped profit math for this SPU today" without crashing
    the rebuild.
    """
    session.add(
        SyncIssue(
            job_name="reporting.profit_daily",
            issue_type="CURRENCY_MISMATCH",
            external_id=str(spu_pk),
            details={
                "profit_date": profit_date.isoformat(),
                "order_currency": order_currency,
                "snapshot_currency": snapshot_currency,
                "note": (
                    "cost snapshot currency != order currency; "
                    "estimated_cogs / estimated_gross_profit written NULL "
                    "until FX table exists (audit P1-4b)"
                ),
            },
        )
    )
    log.warning(
        "profit_daily: cp_id=%s profit_date=%s currency_mismatch order=%s snapshot=%s",
        spu_pk,
        profit_date.isoformat(),
        order_currency,
        snapshot_currency,
    )


def rebuild(session: Session, *, profit_date: date) -> list[ProductProfitDaily]:
    """Rebuild the profit rows for ``profit_date``. Returns the rows
    written in this run."""
    calculation_version = _next_calculation_version(session)
    calculated_at = datetime.utcnow()

    # Aggregate paid sales_order_lines by channel_product for the day.
    # SalesOrder.paid_at is timestamptz; we cast to date in SQL.
    # See module docstring for why PAID_SALES_ORDER_STATUSES (not 'PAID').
    rows = session.execute(
        select(
            SalesOrderLine.spu_pk,
            func.coalesce(func.sum(SalesOrderLine.quantity), 0).label("units"),
            func.coalesce(
                func.sum(SalesOrderLine.quantity * SalesOrderLine.unit_price), 0
            ).label("revenue"),
            func.max(SalesOrder.currency).label("currency"),
        )
        .join(SalesOrder, SalesOrder.id == SalesOrderLine.order_pk)
        .where(SalesOrder.status.in_(PAID_SALES_ORDER_STATUSES))
        .where(SalesOrder.paid_at.is_not(None))
        .where(func.date(SalesOrder.paid_at) == profit_date)
        .where(SalesOrderLine.spu_pk.is_not(None))
        .group_by(SalesOrderLine.spu_pk)
    ).all()

    out: list[ProductProfitDaily] = []
    for row in rows:
        cp_id = row.spu_pk
        units = Decimal(row.units or 0)
        revenue = Decimal(row.revenue or 0)
        currency = row.currency or "USD"
        snap = _latest_snapshot(session, cp_id)
        cogs: Decimal | None = None
        profit: Decimal | None = None
        cost_method: str | None = None
        if snap is not None:
            # Currency guard: when the cost snapshot is denominated in a
            # currency that doesn't match the order, refuse to compute
            # a cross-currency profit. gross_revenue stays honest (it's
            # what TikTok actually paid us); cogs/profit go NULL. This
            # is the documented transition strategy until an FX table
            # lands (see module docstring + audit P1-4b).
            if snap.currency != currency:
                _record_currency_mismatch(
                    session,
                    spu_pk=cp_id,
                    profit_date=profit_date,
                    order_currency=currency,
                    snapshot_currency=snap.currency,
                )
                # Do NOT set cost_method here — the snapshot isn't
                # being used for math, so claiming a method would
                # mislead the dashboard.
            else:
                cogs_value = (snap.unit_cost * units).quantize(Decimal("0.0001"))
                profit = (revenue - cogs_value).quantize(Decimal("0.0001"))
                cogs = cogs_value
                cost_method = snap.cost_method

        pd_row = ProductProfitDaily(
            spu_pk=cp_id,
            profit_date=profit_date,
            units_sold=units,
            gross_revenue=revenue.quantize(Decimal("0.01")),
            estimated_cogs=cogs,
            platform_fees=None,
            shipping_cost=None,
            refunds=None,
            estimated_gross_profit=profit,
            currency=currency,  # the order currency, always honest
            cost_method=cost_method,
            calculation_version=calculation_version,
            calculated_at=calculated_at,
        )
        session.add(pd_row)
        out.append(pd_row)

    session.flush()
    return out
