"""TDD tests for reporting.profit_daily — daily product-profit rebuild.

The job rebuilds reporting.product_profit_daily with monotonically
incremented calculation_version. Old rows are retained (not deleted) for
forensics.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
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
    ExchangeRate,
    ExchangeRateSnapshot,
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


def _seed(session, *, spu_pk: int, currency="USD", status="COMPLETED"):
    """Helper: a single paid sales order with one line, with given
    gross_revenue. Cost comes from ProductCostSnapshot, if any.

    ``status`` defaults to 'COMPLETED' (a member of
    ``PAID_SALES_ORDER_STATUSES``); callers can pass any other paid
    status. Tests that exercise the exclusion list should pass
    e.g. status='UNPAID' or status='CANCELLED' explicitly.
    """
    acct = session.execute(select(ChannelAccount)).scalars().first()
    so = SalesOrder(
        shop_pk=acct.id,
        order_id="TEST_ORDER_001",
        status=status,
        currency=currency,
        payment_amount=Decimal("100.00"),
        total_amount=Decimal("100.00"),
        paid_at=_utc(),
    )
    session.add(so)
    session.flush()
    line = SalesOrderLine(
        order_pk=so.id,
        external_line_id="TEST_LINE_001",
        spu_pk=spu_pk,
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
        platform="tiktok", shop_id="TEST_TT_PROFIT", credential_id=cred.id
    )
    session.add(acct)
    session.flush()
    cp = ChannelProduct(
        shop_pk=acct.id,
        spu_id="TEST_PROFIT_SPU",
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
    _seed(db_session, spu_pk=cp.id)

    profit_daily.rebuild(db_session, profit_date=date(2026, 8, 29))
    profit_daily.rebuild(db_session, profit_date=date(2026, 8, 29))

    rows = (
        db_session.execute(
            select(ProductProfitDaily).where(
                ProductProfitDaily.spu_pk == cp.id
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
            spu_pk=cp.id,
            cost_method="MANUAL_ENTRY",
            unit_cost=Decimal("10.00"),
            currency="USD",
            valid_from=_utc(),
            calculated_at=_utc(),
            calculation_version=1,
        )
    )
    db_session.flush()
    _seed(db_session, spu_pk=cp.id)

    rows = profit_daily.rebuild(db_session, profit_date=date(2026, 8, 29))
    assert len(rows) >= 1
    row = next(r for r in rows if r.spu_pk == cp.id)
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
    _seed(db_session, spu_pk=cp.id)

    rows = profit_daily.rebuild(db_session, profit_date=date(2026, 8, 29))
    row = next(r for r in rows if r.spu_pk == cp.id)
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
    _seed(db_session, spu_pk=cp.id, status="COMPLETED")
    rows = profit_daily.rebuild(db_session, profit_date=date(2026, 8, 29))
    assert any(r.spu_pk == cp.id for r in rows)


def test_paid_status_whitelist_includes_awaiting_shipment(db_session):
    """AWAITING_SHIPMENT (paid but not yet shipped) is also treated as paid."""
    _acct, cp = _make_account_and_product(db_session)
    _seed(db_session, spu_pk=cp.id, status="AWAITING_SHIPMENT")
    rows = profit_daily.rebuild(db_session, profit_date=date(2026, 8, 29))
    assert any(r.spu_pk == cp.id for r in rows)


def test_paid_status_whitelist_includes_in_transit(db_session):
    _acct, cp = _make_account_and_product(db_session)
    _seed(db_session, spu_pk=cp.id, status="IN_TRANSIT")
    rows = profit_daily.rebuild(db_session, profit_date=date(2026, 8, 29))
    assert any(r.spu_pk == cp.id for r in rows)


def test_unpaid_status_excluded(db_session):
    """UNPAID orders must not appear in revenue aggregations."""
    _acct, cp = _make_account_and_product(db_session)
    _seed(db_session, spu_pk=cp.id, status="UNPAID")
    rows = profit_daily.rebuild(db_session, profit_date=date(2026, 8, 29))
    assert all(r.spu_pk != cp.id for r in rows)


def test_cancelled_status_excluded(db_session):
    """CANCELLED orders must not appear in revenue aggregations even
    though they sometimes have paid_at set (e.g. partial-refund)."""
    _acct, cp = _make_account_and_product(db_session)
    _seed(db_session, spu_pk=cp.id, status="CANCELLED")
    rows = profit_daily.rebuild(db_session, profit_date=date(2026, 8, 29))
    assert all(r.spu_pk != cp.id for r in rows)


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


# ─── 7. cross-currency conversion through the FX owner ────────────────
# Production orders are VND; manual / 妙手 costs are entered in CNY.
# The cost snapshot is CONVERTED into the order currency through
# tts_erp_v2.fx.rates.convert_or_none — the single owner of rate maths.
# When no rate can be obtained the cost stays NULL and a SyncIssue is
# recorded: an unknown cost never degrades into 0 or a default price.
#
# Assertion classes are kept apart on purpose:
#   * location      — where the conversion happens (delegated, not re-derived)
#   * quantity      — exact row / issue counts
#   * branch cover  — same currency / cross currency / unknown rate / no snapshot

_CNY_PER_USD = Decimal("6.6866")
_VND_PER_USD = Decimal("25539.8311")
# CNY→VND through the USD base: rates[VND] / rates[CNY], quantized to 8 dp.
_CNY_TO_VND = Decimal("3819.55419794")
# unit_cost 10.00 CNY → 38195.54197940 VND → ×2 units → 76391.08395880 → 4 dp.
_COGS_VND = Decimal("76391.0840")
_PROFIT_VND = Decimal("23608.9160")


def _seed_cost_snapshot(session, spu_pk, *, unit_cost, currency):
    session.add(
        ProductCostSnapshot(
            spu_pk=spu_pk,
            cost_method="MANUAL_ENTRY",
            unit_cost=unit_cost,
            currency=currency,
            valid_from=_utc(),
            calculated_at=_utc(),
            calculation_version=1,
        )
    )
    session.flush()


def _seed_fx_snapshot(session, rates, *, base="USD"):
    """One cached FX snapshot in the shape tts_erp_v2.fx.rates.load_rate_map reads."""
    now = datetime(2026, 8, 29, tzinfo=UTC)
    snap = ExchangeRateSnapshot(
        base_code=base,
        upstream_last_update=now,
        next_update_at=now + timedelta(days=1),
        fetched_at=now,
        rates_count=len(rates),
    )
    session.add(snap)
    session.flush()
    for code, value in rates.items():
        session.add(
            ExchangeRate(
                snapshot_id=snap.id,
                base_code=base,
                target_code=code,
                rate=value,
            )
        )
    session.flush()
    return snap


def _seed_order(session, *, spu_pk, currency, quantity, unit_price, order_id):
    """A paid sales order with one line, sized by the caller."""
    acct = session.execute(select(ChannelAccount)).scalars().first()
    so = SalesOrder(
        shop_pk=acct.id,
        order_id=order_id,
        status="COMPLETED",
        currency=currency,
        payment_amount=quantity * unit_price,
        total_amount=quantity * unit_price,
        paid_at=_utc(),
    )
    session.add(so)
    session.flush()
    session.add(
        SalesOrderLine(
            order_pk=so.id,
            external_line_id=f"{order_id}_L1",
            spu_pk=spu_pk,
            quantity=quantity,
            unit_price=unit_price,
            currency=currency,
            line_status="NORMAL",
        )
    )
    session.flush()


def _issues(session, spu_pk):
    return (
        session.execute(
            select(SyncIssue).where(
                SyncIssue.job_name == "reporting.profit_daily",
                SyncIssue.external_id == str(spu_pk),
            )
        )
        .scalars()
        .all()
    )


# ─── 7a. location ────────────────────────────────────────────────────


def test_cost_conversion_is_delegated_to_the_fx_owner(db_session, monkeypatch):
    """位置断言：换算由 FX owner 拥有，profit_daily 不自写汇率数学。

    - 模块直接复用 ``tts_erp_v2.fx.rates.convert_or_none``（同一函数对象）
    - 旧的「币种不一致就拒绝计算」守卫与 CURRENCY_MISMATCH 已不存在
    - 模块内没有任何本地汇率表 / 汇率除法 / 汇率量化
    - 调用时传入的是快照币种 → 订单币种与快照单位成本
    """
    import inspect

    import tts_erp_v2.fx.rates as fx_rates

    assert profit_daily.convert_or_none is fx_rates.convert_or_none

    source = inspect.getsource(profit_daily)
    assert "_record_currency_mismatch" not in source
    assert "CURRENCY_MISMATCH" not in source
    assert "_cost_in_order_currency" in source
    # 本地汇率数学的特征符号：一个都不该出现在本模块
    assert "_bridge_rate" not in source
    assert "quantize_rate" not in source
    assert "rm.rates" not in source

    _acct, cp = _make_account_and_product(db_session)
    _seed_cost_snapshot(db_session, cp.id, unit_cost=Decimal("10.00"), currency="CNY")
    _seed_order(
        db_session,
        spu_pk=cp.id,
        currency="VND",
        quantity=Decimal(2),
        unit_price=Decimal(50000),
        order_id="TEST_FX_LOCATION",
    )

    seen: dict[str, object] = {}

    def _capture(session, *, amount, from_code, to_code, **kwargs):
        seen.update(amount=amount, from_code=from_code, to_code=to_code)
        return fx_rates.convert_or_none(
            session, amount=amount, from_code=from_code, to_code=to_code, **kwargs
        )

    monkeypatch.setattr(profit_daily, "convert_or_none", _capture)
    profit_daily.rebuild(db_session, profit_date=date(2026, 8, 29))

    assert seen["amount"] == Decimal("10.00")
    assert seen["from_code"] == "CNY"
    assert seen["to_code"] == "VND"


# ─── 7b. quantity ────────────────────────────────────────────────────


def test_cross_currency_with_rate_writes_exactly_one_valued_row_and_no_issue(db_session):
    """数量断言：有汇率时恰好 1 行有值、0 条 issue。"""
    _acct, cp = _make_account_and_product(db_session)
    _seed_cost_snapshot(db_session, cp.id, unit_cost=Decimal("10.00"), currency="CNY")
    _seed_fx_snapshot(
        db_session,
        {"USD": Decimal(1), "CNY": _CNY_PER_USD, "VND": _VND_PER_USD},
    )
    _seed_order(
        db_session,
        spu_pk=cp.id,
        currency="VND",
        quantity=Decimal(2),
        unit_price=Decimal(50000),
        order_id="TEST_FX_COUNT_OK",
    )

    rows = profit_daily.rebuild(db_session, profit_date=date(2026, 8, 29))

    assert len(rows) == 1
    assert sum(1 for r in rows if r.estimated_cogs is not None) == 1
    assert sum(1 for r in rows if r.estimated_gross_profit is not None) == 1
    assert sum(1 for r in rows if r.cost_method is not None) == 1
    assert len(_issues(db_session, cp.id)) == 0


def test_cross_currency_without_rate_writes_exactly_one_issue_and_no_valued_row(db_session):
    """数量断言：取不到汇率时恰好 1 条 issue、0 行有值。"""
    _acct, cp = _make_account_and_product(db_session)
    _seed_cost_snapshot(db_session, cp.id, unit_cost=Decimal("10.00"), currency="CNY")
    # Order currency is absent from every cached rate map ⇒ no rate available.
    _seed_order(
        db_session,
        spu_pk=cp.id,
        currency="XTS",
        quantity=Decimal(2),
        unit_price=Decimal(50000),
        order_id="TEST_FX_COUNT_MISSING",
    )

    rows = profit_daily.rebuild(db_session, profit_date=date(2026, 8, 29))

    assert len(rows) == 1
    assert sum(1 for r in rows if r.estimated_cogs is not None) == 0
    assert sum(1 for r in rows if r.estimated_gross_profit is not None) == 0
    assert sum(1 for r in rows if r.cost_method is not None) == 0

    issues = _issues(db_session, cp.id)
    assert len(issues) == 1
    assert issues[0].issue_type == "CURRENCY_CONVERSION_UNAVAILABLE"


# ─── 7c. branch coverage ─────────────────────────────────────────────


def test_same_currency_branch_never_calls_the_fx_owner(db_session, monkeypatch):
    """分支覆盖：同币种直接用快照值，不走 FX。"""

    def _boom(*args, **kwargs):
        raise AssertionError("same-currency branch must not call the FX owner")

    monkeypatch.setattr(profit_daily, "convert_or_none", _boom)

    _acct, cp = _make_account_and_product(db_session)
    _seed_cost_snapshot(db_session, cp.id, unit_cost=Decimal("10.00"), currency="USD")
    _seed_order(
        db_session,
        spu_pk=cp.id,
        currency="USD",
        quantity=Decimal(2),
        unit_price=Decimal("50.00"),
        order_id="TEST_FX_BRANCH_SAME",
    )

    rows = profit_daily.rebuild(db_session, profit_date=date(2026, 8, 29))
    row = next(r for r in rows if r.spu_pk == cp.id)

    assert row.estimated_cogs == Decimal("20.00")
    assert row.estimated_gross_profit == Decimal("80.00")
    assert row.cost_method == "MANUAL_ENTRY"
    assert len(_issues(db_session, cp.id)) == 0


def test_cross_currency_branch_converts_through_the_cached_rate(db_session):
    """分支覆盖：跨币种按 owner 的交叉汇率换算，cogs/profit 用订单币种。"""
    _acct, cp = _make_account_and_product(db_session)
    _seed_cost_snapshot(db_session, cp.id, unit_cost=Decimal("10.00"), currency="CNY")
    _seed_fx_snapshot(
        db_session,
        {"USD": Decimal(1), "CNY": _CNY_PER_USD, "VND": _VND_PER_USD},
    )
    _seed_order(
        db_session,
        spu_pk=cp.id,
        currency="VND",
        quantity=Decimal(2),
        unit_price=Decimal(50000),
        order_id="TEST_FX_BRANCH_CROSS",
    )

    rows = profit_daily.rebuild(db_session, profit_date=date(2026, 8, 29))
    row = next(r for r in rows if r.spu_pk == cp.id)

    assert row.gross_revenue == Decimal("100000.00")
    assert row.currency == "VND"
    assert row.estimated_cogs == _COGS_VND
    assert row.estimated_gross_profit == _PROFIT_VND
    assert row.cost_method == "MANUAL_ENTRY"
    assert len(_issues(db_session, cp.id)) == 0


def test_unknown_rate_branch_keeps_null_and_records_issue(db_session):
    """分支覆盖：目标币种无缓存汇率 ⇒ cogs/profit/cost_method 全 NULL + 1 条 issue。

    成本未知就是未知：不得退化成 0，也不得退化成任何默认价。
    """
    _acct, cp = _make_account_and_product(db_session)
    _seed_cost_snapshot(db_session, cp.id, unit_cost=Decimal("10.00"), currency="CNY")
    _seed_fx_snapshot(db_session, {"USD": Decimal(1), "CNY": _CNY_PER_USD})
    _seed_order(
        db_session,
        spu_pk=cp.id,
        currency="VND",
        quantity=Decimal(2),
        unit_price=Decimal(50000),
        order_id="TEST_FX_BRANCH_UNKNOWN",
    )

    rows = profit_daily.rebuild(db_session, profit_date=date(2026, 8, 29))
    row = next(r for r in rows if r.spu_pk == cp.id)

    assert row.estimated_cogs is None
    assert row.estimated_gross_profit is None
    assert row.cost_method is None
    assert row.gross_revenue == Decimal("100000.00")
    assert row.estimated_cogs != Decimal(0)

    issues = _issues(db_session, cp.id)
    assert len(issues) == 1
    assert issues[0].issue_type == "CURRENCY_CONVERSION_UNAVAILABLE"
    assert issues[0].details["order_currency"] == "VND"
    assert issues[0].details["snapshot_currency"] == "CNY"
    assert "unknown_currency" in issues[0].details["reason"]


def test_missing_rate_snapshot_branch_keeps_null_and_records_issue(db_session, monkeypatch):
    """分支覆盖：完全没有汇率快照（owner 返回 None）⇒ NULL + issue，reason=no_rate_snapshot。"""
    monkeypatch.setattr(profit_daily, "convert_or_none", lambda *a, **k: None)

    _acct, cp = _make_account_and_product(db_session)
    _seed_cost_snapshot(db_session, cp.id, unit_cost=Decimal("10.00"), currency="CNY")
    _seed_order(
        db_session,
        spu_pk=cp.id,
        currency="VND",
        quantity=Decimal(2),
        unit_price=Decimal(50000),
        order_id="TEST_FX_BRANCH_NOSNAP",
    )

    rows = profit_daily.rebuild(db_session, profit_date=date(2026, 8, 29))
    row = next(r for r in rows if r.spu_pk == cp.id)

    assert row.estimated_cogs is None
    assert row.estimated_gross_profit is None
    assert row.cost_method is None

    issues = _issues(db_session, cp.id)
    assert len(issues) == 1
    assert issues[0].issue_type == "CURRENCY_CONVERSION_UNAVAILABLE"
    assert issues[0].details["reason"] == "no_rate_snapshot"


def test_same_currency_does_not_emit_sync_issue(db_session):
    """Same currency ⇒ normal computation, no SyncIssue."""
    _acct, cp = _make_account_and_product(db_session)
    db_session.add(
        ProductCostSnapshot(
            spu_pk=cp.id,
            cost_method="MANUAL_ENTRY",
            unit_cost=Decimal("10.00"),
            currency="USD",
            valid_from=_utc(),
            calculated_at=_utc(),
            calculation_version=1,
        )
    )
    db_session.flush()
    _seed(db_session, spu_pk=cp.id, currency="USD")

    rows = profit_daily.rebuild(db_session, profit_date=date(2026, 8, 29))
    row = next(r for r in rows if r.spu_pk == cp.id)
    assert row.estimated_cogs == Decimal("20.00")
    assert row.estimated_gross_profit == Decimal("80.00")
    assert row.cost_method == "MANUAL_ENTRY"

    issues = (
        db_session.execute(
            select(SyncIssue).where(
                SyncIssue.job_name == "reporting.profit_daily",
                SyncIssue.issue_type == "CURRENCY_CONVERSION_UNAVAILABLE",
                SyncIssue.external_id == str(cp.id),
            )
        )
        .scalars()
        .all()
    )
    assert issues == []
