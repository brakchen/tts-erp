"""TDD tests for reporting.profit_daily — daily product-profit rebuild.

The job rebuilds reporting.product_profit_daily with monotonically
incremented calculation_version. Old rows are retained (not deleted) for
forensics.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal

import pytest

from sqlalchemy import select
from tts_erp_v2.reporting import profit_daily

from tts_erp_v2.db.models import (
    ChannelAccount,
    ChannelProduct,
    Credentials,
    ProductCostSnapshot,
    ProductProfitDaily,
    SalesOrder,
    SalesOrderLine,
)


pytestmark = [pytest.mark.domain_reporting, pytest.mark.domain_finance, pytest.mark.layer_integration]


def _utc():
    return datetime(2026, 8, 29, tzinfo=timezone.utc)


def _seed(session, *, channel_product_id: int, currency="USD"):
    """Helper: a single paid sales order with one line, with given
    gross_revenue. Cost comes from ProductCostSnapshot, if any."""
    acct = session.execute(select(ChannelAccount)).scalars().first()
    so = SalesOrder(
        channel_account_id=acct.id,
        external_order_id="TEST_ORDER_001",
        status="PAID",
        currency=currency,
        payment_amount=Decimal("100.00"),
        total_amount=Decimal("100.00"),
        paid_at=_utc(),
    )
    session.add(so)
    session.flush()
    line = SalesOrderLine(
        sales_order_id=so.id,
        external_line_id="TEST_LINE_001",
        channel_product_id=channel_product_id,
        quantity=Decimal("2.0000"),
        unit_price=Decimal("50.00"),
        currency=currency,
        line_status="NORMAL",
    )
    session.add(line)
    session.flush()


def _make_account_and_product(session):
    cred = Credentials(
        provider="tiktok", external_account_id="TEST_TT_PROFIT", ciphertext=b"\x00" * 32
    )
    session.add(cred)
    session.flush()
    acct = ChannelAccount(
        platform="tiktok", external_account_id="TEST_TT_PROFIT", credential_id=cred.id
    )
    session.add(acct)
    session.flush()
    cp = ChannelProduct(
        channel_account_id=acct.id,
        external_product_id="TEST_PROFIT_SPU",
        title="TEST profit product",
        status="ACTIVE",
    )
    session.add(cp)
    session.flush()
    return acct, cp


# ─── 1. rebuild increments calculation_version ────────────────────────


def test_rebuild_increments_calculation_version(db_session):
    """Calling rebuild twice writes rows with v=1 then v=2."""
    _acct, cp = _make_account_and_product(db_session)
    _seed(db_session, channel_product_id=cp.id)

    profit_daily.rebuild(db_session, profit_date=date(2026, 8, 29))
    profit_daily.rebuild(db_session, profit_date=date(2026, 8, 29))

    rows = (
        db_session.execute(
            select(ProductProfitDaily).where(
                ProductProfitDaily.channel_product_id == cp.id
            )
        )
        .scalars()
        .all()
    )
    versions = sorted({r.calculation_version for r in rows})
    assert versions == [1, 2]
    # Old (v=1) rows are NOT deleted.
    assert len(rows) == 2


# ─── 2. basic revenue/cost aggregation ──────────────────────────────


def test_basic_revenue_and_cogs(db_session):
    _acct, cp = _make_account_and_product(db_session)
    # seed a cost snapshot at unit_cost 10
    db_session.add(
        ProductCostSnapshot(
            channel_product_id=cp.id,
            cost_method="MANUAL_ENTRY",
            unit_cost=Decimal("10.00"),
            currency="USD",
            valid_from=_utc(),
            calculated_at=_utc(),
            calculation_version=1,
        )
    )
    db_session.flush()
    _seed(db_session, channel_product_id=cp.id)

    rows = profit_daily.rebuild(db_session, profit_date=date(2026, 8, 29))
    assert len(rows) >= 1
    row = next(r for r in rows if r.channel_product_id == cp.id)
    # 2 units * $50 = $100 gross_revenue, 2 units * $10 = $20 estimated_cogs
    assert row.gross_revenue == Decimal("100.00")
    assert row.estimated_cogs == Decimal("20.00")
    assert row.estimated_gross_profit == Decimal("80.00")
    assert row.units_sold == Decimal("2.0000")
    assert row.cost_method == "MANUAL_ENTRY"
    assert row.currency == "USD"


# ─── 3. no cost snapshot ⇒ row still written but cogs NULL ───────────


def test_no_cost_snapshot_means_cogs_null(db_session):
    _acct, cp = _make_account_and_product(db_session)
    _seed(db_session, channel_product_id=cp.id)

    rows = profit_daily.rebuild(db_session, profit_date=date(2026, 8, 29))
    row = next(r for r in rows if r.channel_product_id == cp.id)
    assert row.gross_revenue == Decimal("100.00")
    assert row.estimated_cogs is None  # unknown cost
    assert row.estimated_gross_profit is None  # can't compute
    assert row.cost_method is None


# ─── 4. unrelated day / no orders ⇒ no row ───────────────────────────


def test_no_orders_for_date_no_row(db_session):
    _acct, cp = _make_account_and_product(db_session)
    rows = profit_daily.rebuild(db_session, profit_date=date(2026, 8, 28))
    assert rows == []
