"""TDD tests for reporting.profit_daily — daily product-profit rebuild.

The job rebuilds reporting.product_profit_daily with monotonically
incremented calculation_version. Old rows are retained (not deleted) for
forensics.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from sqlalchemy import select

from tts_erp_v2.db.constants import (
    ACTIVE_PRODUCT_STATUS,
    PAID_SALES_ORDER_STATUSES,
)
from tts_erp_v2.db.models import (
    ChannelAccount,
    ChannelProduct,
    Credentials,
    ProductCostSnapshot,
    ProductProfitDaily,
    SalesOrder,
    SalesOrderLine,
    SyncIssue,
)
from tts_erp_v2.reporting import profit_daily

pytestmark = [pytest.mark.domain_reporting, pytest.mark.domain_finance, pytest.mark.layer_integration]


def _utc():
    return datetime(2026, 8, 29, tzinfo=UTC)


def _seed(session, *, channel_product_id: int, currency="USD", status="COMPLETED"):
    """Helper: a single paid sales order with one line, with given
    gross_revenue. Cost comes from ProductCostSnapshot, if any.

    ``status`` defaults to 'COMPLETED' (a member of
    ``PAID_SALES_ORDER_STATUSES``); callers can pass any other paid
    status. Tests that exercise the exclusion list should pass
    e.g. status='UNPAID' or status='CANCELLED' explicitly.
    """
    acct = session.execute(select(ChannelAccount)).scalars().first()
    so = SalesOrder(
        channel_account_id=acct.id,
        external_order_id="TEST_ORDER_001",
        status=status,
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
        status=ACTIVE_PRODUCT_STATUS,
    )
    session.add(cp)
    session.flush()
    return acct, cp


# ─── 1. rebuild increments calculation_version ────────────────────────


def test_rebuild_increments_calculation_version(db_session):
    """Two rebuild calls produce two consecutive versions of the same SPU.

    _next_calculation_version uses ``MAX(calculation_version) + 1`` GLOBALLY
    (intentional, see profit_daily.py:71-75) so the absolute starting
    version depends on whatever else has ever been rebuilt in prod — not
    1. We assert the *delta* (consecutive +1) rather than absolute values.
    """
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
    assert len(versions) == 2, f"expected exactly 2 versions, got {versions}"
    assert versions[1] == versions[0] + 1, (
        f"calculation_version should increment by 1 between rebuilds; "
        f"got {versions}"
    )
    # Old (lower-version) rows are NOT deleted.
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
    """Rebuild for a date with no paid orders ⇒ no rows.

    Uses a far-future date so we don't accidentally pick up production
    paid_at data (the suite shares one DB). The PAID-status whitelist
    fix in audit P1-4a unblocked the production data path, so
       in-range dates now legitimately return production rows.
    """
    _acct, cp = _make_account_and_product(db_session)
    rows = profit_daily.rebuild(db_session, profit_date=date(2030, 1, 1))
    assert rows == []


# ─── 5. PAID whitelist (audit P1-4a) ─────────────────────────────────
# SalesOrder.status == 'PAID' never matches real data — TikTok uses
# fulfilment-lifecycle statuses (COMPLETED / DELIVERED / IN_TRANSIT /
# AWAITING_SHIPMENT / AWAITING_COLLECTION / PARTIAL_SHIPPING) for paid
# orders and UNPAID / CANCELLED for the rest. The rebuild must use
# the whitelist PAID_SALES_ORDER_STATUSES, not a literal 'PAID'.


def test_paid_status_whitelist_includes_completed(db_session):
    """COMPLETED (the most common production status) is treated as paid."""
    _acct, cp = _make_account_and_product(db_session)
    _seed(db_session, channel_product_id=cp.id, status="COMPLETED")
    rows = profit_daily.rebuild(db_session, profit_date=date(2026, 8, 29))
    assert any(r.channel_product_id == cp.id for r in rows)


def test_paid_status_whitelist_includes_awaiting_shipment(db_session):
    """AWAITING_SHIPMENT (paid but not yet shipped) is also treated as paid."""
    _acct, cp = _make_account_and_product(db_session)
    _seed(db_session, channel_product_id=cp.id, status="AWAITING_SHIPMENT")
    rows = profit_daily.rebuild(db_session, profit_date=date(2026, 8, 29))
    assert any(r.channel_product_id == cp.id for r in rows)


def test_paid_status_whitelist_includes_in_transit(db_session):
    _acct, cp = _make_account_and_product(db_session)
    _seed(db_session, channel_product_id=cp.id, status="IN_TRANSIT")
    rows = profit_daily.rebuild(db_session, profit_date=date(2026, 8, 29))
    assert any(r.channel_product_id == cp.id for r in rows)


def test_unpaid_status_excluded(db_session):
    """UNPAID orders must not appear in revenue aggregations."""
    _acct, cp = _make_account_and_product(db_session)
    _seed(db_session, channel_product_id=cp.id, status="UNPAID")
    rows = profit_daily.rebuild(db_session, profit_date=date(2026, 8, 29))
    assert all(r.channel_product_id != cp.id for r in rows)


def test_cancelled_status_excluded(db_session):
    """CANCELLED orders must not appear in revenue aggregations even
    though they sometimes have paid_at set (e.g. partial-refund)."""
    _acct, cp = _make_account_and_product(db_session)
    _seed(db_session, channel_product_id=cp.id, status="CANCELLED")
    rows = profit_daily.rebuild(db_session, profit_date=date(2026, 8, 29))
    assert all(r.channel_product_id != cp.id for r in rows)


def test_paid_whitelist_is_documented_in_constants():
    """Document the invariant: PAID_SALES_ORDER_STATUSES is the single
    source of truth. If the rebuild were to use a literal 'PAID'
    status the rebuild would always be empty (see audit P1-4a)."""
    # The whitelist must contain the lifecycle statuses production uses.
    expected = {
        "AWAITING_SHIPMENT",
        "PARTIAL_SHIPPING",
        "AWAITING_COLLECTION",
        "IN_TRANSIT",
        "DELIVERED",
        "COMPLETED",
    }
    assert set(PAID_SALES_ORDER_STATUSES) == expected
    # And it must NOT contain statuses that look "paid-ish" but aren't.
    for excluded in ("UNPAID", "ON_HOLD", "CANCELLED"):
        assert excluded not in PAID_SALES_ORDER_STATUSES


# ─── 6. currency mismatch (audit P1-4b) ──────────────────────────────
# Production orders are VND; manual / 妙手 costs are entered in CNY.
# Without an FX table, computing gross_revenue - estimated_cogs across
# currencies produces nonsense. The transition strategy: when the
# snapshot currency disagrees with the order currency, write the row
# with NULL estimated_cogs / estimated_gross_profit and emit a
# SyncIssue so ops sees the gap.


def test_currency_mismatch_writes_null_cogs_and_logs_sync_issue(db_session):
    """Order in VND + cost snapshot in CNY ⇒ NULL cogs / profit + SyncIssue."""
    _acct, cp = _make_account_and_product(db_session)
    # Cost snapshot in CNY (operator entered / 妙手).
    db_session.add(
        ProductCostSnapshot(
            channel_product_id=cp.id,
            cost_method="MANUAL_ENTRY",
            unit_cost=Decimal("10.00"),
            currency="CNY",
            valid_from=_utc(),
            calculated_at=_utc(),
            calculation_version=1,
        )
    )
    db_session.flush()
    # Order in VND (production reality).
    _seed(db_session, channel_product_id=cp.id, currency="VND")

    rows = profit_daily.rebuild(db_session, profit_date=date(2026, 8, 29))
    row = next(r for r in rows if r.channel_product_id == cp.id)

    # gross_revenue stays honest.
    assert row.gross_revenue == Decimal("100.00")
    assert row.currency == "VND"
    # Cogs / profit are NULL — we refuse to do cross-currency math.
    assert row.estimated_cogs is None
    assert row.estimated_gross_profit is None
    # cost_method is also None because the snapshot wasn't actually used.
    assert row.cost_method is None

    # A SyncIssue row should be recorded for ops.
    issues = (
        db_session.execute(
            select(SyncIssue).where(
                SyncIssue.job_name == "reporting.profit_daily",
                SyncIssue.issue_type == "CURRENCY_MISMATCH",
                SyncIssue.external_id == str(cp.id),
            )
        )
        .scalars()
        .all()
    )
    assert len(issues) == 1
    assert issues[0].details["order_currency"] == "VND"
    assert issues[0].details["snapshot_currency"] == "CNY"


def test_same_currency_does_not_emit_sync_issue(db_session):
    """Same currency ⇒ normal computation, no SyncIssue."""
    _acct, cp = _make_account_and_product(db_session)
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
    _seed(db_session, channel_product_id=cp.id, currency="USD")

    rows = profit_daily.rebuild(db_session, profit_date=date(2026, 8, 29))
    row = next(r for r in rows if r.channel_product_id == cp.id)
    assert row.estimated_cogs == Decimal("20.00")
    assert row.estimated_gross_profit == Decimal("80.00")
    assert row.cost_method == "MANUAL_ENTRY"

    issues = (
        db_session.execute(
            select(SyncIssue).where(
                SyncIssue.job_name == "reporting.profit_daily",
                SyncIssue.issue_type == "CURRENCY_MISMATCH",
                SyncIssue.external_id == str(cp.id),
            )
        )
        .scalars()
        .all()
    )
    assert issues == []
