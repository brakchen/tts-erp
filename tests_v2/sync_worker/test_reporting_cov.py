"""Coverage tests for tts_erp_v2.jobs.reporting.

Goal: lift ``reporting.py`` from 72.7% → ≥90%.

Branches covered:
* ``_purchase_order_lookup`` no-result branch (line 65) — when no row
  matches the SQL query, the lookup returns ``(None, None)``.
* ``_purchase_order_lookup`` happy path — when a row matches, returns
  ``(unit_cost, currency)``.
* ``run_profit_daily`` body (lines 98-108) — both dates are walked, the
  SyncJob row's extra JSON carries ``dates`` + ``rows``, the result
  dict has ``dates`` + ``rows_written``.
* ``run_cost_snapshots`` SyncJob row extras (line 92-95) — covers
  ``calculation_version`` + ``valid_from`` + ``snapshots_written``.

Each test seeds only TEST_-prefixed rows; per-row assertions filter on
``external_product_id LIKE 'TEST_%'`` so prod data never enters the
result set — see ``/home/schan/tts-erp/logs/diagnose-failures.md``.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy import func, select

from tts_erp_v2.db.models import (
    ChannelAccount,
    ChannelProduct,
    ManualProductCost,
    ProductCostSnapshot,
    ProductProfitDaily,
    SalesOrder,
    SalesOrderLine,
)
from tts_erp_v2.jobs.reporting import (
    JOB_COST_SNAPSHOTS,
    JOB_PROFIT_DAILY,
    _purchase_order_lookup,
    run_cost_snapshots,
    run_profit_daily,
)

pytestmark = [pytest.mark.domain_reporting, pytest.mark.layer_integration]


# ───────────────────── helpers ─────────────────────


def _seed_account_and_product(
    db_session,
    *,
    account_external_id: str,
    product_external_id: str,
    currency: str = "CNY",
    account_status: str = "ACTIVE",
    product_status: str = "ACTIVATE",
) -> tuple[ChannelAccount, ChannelProduct]:
    acct = ChannelAccount(
        platform="tiktok",
        external_account_id=account_external_id,
        account_name=f"test {account_external_id}",
        status=account_status,
    )
    db_session.add(acct)
    db_session.flush()
    cp = ChannelProduct(
        channel_account_id=acct.id,
        external_product_id=product_external_id,
        title=f"test {product_external_id}",
        status=product_status,
    )
    db_session.add(cp)
    db_session.flush()
    return acct, cp


# ───────────────────── _purchase_order_lookup branches (line 65) ─────────────────────


def test_purchase_order_lookup_returns_none_pair_when_no_row(db_session) -> None:
    """The lookup function returns ``(None, None)`` when no row matches
    the SQL — exercising line 65's `if row is None: return None, None`
    branch."""
    lookup = _purchase_order_lookup(db_session)
    # No procurement-side rows inserted → SQL returns no row → (None, None).
    unit_cost, currency = lookup(999_999_999)
    assert unit_cost is None
    assert currency is None


def test_purchase_order_lookup_returns_unit_cost_and_currency_pair(db_session) -> None:
    """When the SQL returns a row, the lookup returns ``(unit_cost,
    currency)`` (the second branch of the if/else)."""
    # Seed a procurement_product + purchase_order + purchase_order_line
    # so the SQL has a row to find.
    from tts_erp_v2.db.models.procurement import (
        ProcurementAccount,
        ProcurementProduct,
        PurchaseOrder,
        PurchaseOrderLine,
    )

    acct = ProcurementAccount(
        provider="miaoshou",
        external_account_id="TEST_lookup_acct",
        account_name="TEST lookup",
    )
    db_session.add(acct)
    db_session.flush()

    product = ProcurementProduct(
        procurement_account_id=acct.id,
        external_product_id="TEST_lookup_prod",
        title="TEST lookup product",
    )
    db_session.add(product)
    db_session.flush()

    # Link a ChannelProduct to the ProcurementProduct via effective link.
    from tts_erp_v2.db.models.linkage import (
        AccountLink,
        ProductLink,
    )
    chan_acct, chan_product = _seed_account_and_product(
        db_session,
        account_external_id="TEST_lookup_chan_acct",
        product_external_id="TEST_lookup_chan_prod",
    )
    account_link = AccountLink(
        procurement_account_id=acct.id,
        channel_account_id=chan_acct.id,
    )
    db_session.add(account_link)
    db_session.flush()
    product_link = ProductLink(
        procurement_product_id=product.id,
        channel_product_id=chan_product.id,
        relation_type="MIAOSHOU_PUBLISHED_TO_TIKTOK",
    )
    db_session.add(product_link)
    db_session.flush()

    # A purchase order with one line carrying a unit_cost.
    order = PurchaseOrder(
        procurement_account_id=acct.id,
        external_purchase_order_id="TEST_lookup_po",
    )
    db_session.add(order)
    db_session.flush()
    line = PurchaseOrderLine(
        purchase_order_id=order.id,
        external_line_id="TEST_lookup_line",
        procurement_product_id=product.id,
        unit_cost=Decimal("12.50"),
        currency="VND",
    )
    db_session.add(line)
    db_session.flush()

    lookup = _purchase_order_lookup(db_session)
    unit_cost, currency = lookup(chan_product.id)
    assert unit_cost == Decimal("12.5000")
    assert currency == "VND"


# ───────────────────── run_profit_daily body (lines 98-108) ─────────────────────


def test_run_profit_daily_walks_today_and_yesterday(db_session) -> None:
    """``run_profit_daily`` iterates over [yesterday, today] in UTC. The
    SyncJob row's ``extra`` JSON has ``dates`` + ``rows``; the result
    dict has ``dates`` + ``rows_written``. This test pins those
    branches."""
    from tts_erp_v2.db.models.integration import SyncJob

    today = datetime.now(timezone.utc).date()
    out = run_profit_daily(db_session)

    # Find the SyncJob for JOB_PROFIT_DAILY (limit to ours by status).
    job_row = db_session.execute(
        select(SyncJob)
        .where(SyncJob.job_name == JOB_PROFIT_DAILY, SyncJob.status == "succeeded")
        .order_by(SyncJob.id.desc())
        .limit(1)
    ).scalar_one_or_none()
    assert job_row is not None
    assert job_row.extra["dates"] == [(today - timedelta(days=1)).isoformat(), today.isoformat()]
    # ``extra["rows"]`` matches the result dict's ``rows_written``.
    # We don't assert absolute == 0 because prod rows may exist (today
    # / yesterday windows). Instead, check the match between extra and
    # result dict + the structure of the dates list.
    assert job_row.extra["rows"] == out["rows_written"]
    assert job_row.rows_total == out["rows_written"]
    assert job_row.rows_inserted == out["rows_written"]


def test_run_profit_daily_writes_row_for_paid_order_in_window(db_session) -> None:
    """Order on ``today - timedelta(days=1)`` (yesterday) with a paid
    status → ``profit_daily.rebuild`` produces a row."""
    from tts_erp_v2.db.constants import PAID_SALES_ORDER_STATUSES

    yesterday = datetime.now(timezone.utc).date() - timedelta(days=1)

    # Seed an account + product + paid order on YESTERDAY.
    acct, cp = _seed_account_and_product(
        db_session,
        account_external_id="TEST_rpt_yest_acct",
        product_external_id="TEST_rpt_yest_prod",
        currency="VND",
    )
    paid_status = next(iter(PAID_SALES_ORDER_STATUSES))
    order = SalesOrder(
        channel_account_id=acct.id,
        external_order_id="TEST_rpt_yest_o",
        status=paid_status,
        currency="VND",
        payment_amount=Decimal("100000"),
        paid_at=datetime.combine(yesterday, datetime.min.time(), tzinfo=timezone.utc),
    )
    db_session.add(order)
    db_session.flush()
    db_session.add(
        SalesOrderLine(
            sales_order_id=order.id,
            external_line_id="TEST_rpt_yest_l",
            channel_product_id=cp.id,
            quantity=Decimal(2),
            unit_price=Decimal(50000),
            currency="VND",
        )
    )
    db_session.flush()

    out = run_profit_daily(db_session)
    assert len(out["dates"]) == 2
    assert out["rows_written"] >= 1
    # The TEST order's row exists with our TEST channel_product_id.
    rows = (
        db_session.execute(
            select(ProductProfitDaily).where(
                ProductProfitDaily.channel_product_id == cp.id
            )
        )
        .scalars()
        .all()
    )
    matching = [
        r
        for r in rows
        if r.profit_date == yesterday
        and r.units_sold == Decimal("2.0000")
        and r.gross_revenue == Decimal("100000.00")
    ]
    assert len(matching) == 1


def test_run_profit_daily_sync_job_extra_counters_match_result_dict(
    db_session,
) -> None:
    """The SyncJob row's ``extra`` JSON ``rows`` field equals the
    ``rows_written`` field in the result dict (lines 105-106)."""
    out = run_profit_daily(db_session)
    from tts_erp_v2.db.models.integration import SyncJob

    job_row = db_session.execute(
        select(SyncJob)
        .where(SyncJob.job_name == JOB_PROFIT_DAILY, SyncJob.status == "succeeded")
        .order_by(SyncJob.id.desc())
        .limit(1)
    ).scalar_one_or_none()
    assert job_row is not None
    assert job_row.extra["rows"] == out["rows_written"]


# ───────────────────── run_cost_snapshots SyncJob extras (lines 92-95) ─────────────────────


def test_run_cost_snapshots_records_calculation_version_and_valid_from_in_extra(
    db_session,
) -> None:
    """``run_cost_snapshots`` writes a SyncJob row with
    ``calculation_version`` (= previous_max + 1) and ``valid_from`` (ISO
    string) in ``extra`` (lines 92-95)."""
    from tts_erp_v2.db.models.integration import SyncJob

    # Seed a manual cost row so the rebuild actually writes a snapshot.
    _acct, cp = _seed_account_and_product(
        db_session,
        account_external_id="TEST_cs_acct",
        product_external_id="TEST_cs_prod",
        currency="CNY",
    )
    db_session.add(
        ManualProductCost(
            channel_product_id=cp.id,
            unit_cost=Decimal("7.50"),
            currency="CNY",
        )
    )
    db_session.flush()
    # Capture prod's max calculation_version BEFORE running.
    prev_max = db_session.execute(
        select(func.max(ProductCostSnapshot.calculation_version))
    ).scalar() or 0

    out = run_cost_snapshots(db_session)
    assert out["calculation_version"] == prev_max + 1
    assert "valid_from" in out or "calculation_version" in out

    job_row = db_session.execute(
        select(SyncJob)
        .where(SyncJob.job_name == JOB_COST_SNAPSHOTS, SyncJob.status == "succeeded")
        .order_by(SyncJob.id.desc())
        .limit(1)
    ).scalar_one_or_none()
    assert job_row is not None
    assert job_row.extra["calculation_version"] == out["calculation_version"]
    assert "valid_from" in job_row.extra


def test_run_cost_snapshots_no_active_spu_returns_zero(db_session) -> None:
    """No ACTIVE SPU rows (test env: no rows seeded) → ``written=0`` →
    the SyncJob row still records the run."""
    from tts_erp_v2.db.models.integration import SyncJob

    # Don't seed any active SPUs.
    out = run_cost_snapshots(db_session)
    assert out["snapshots_written"] == 0

    job_row = db_session.execute(
        select(SyncJob)
        .where(SyncJob.job_name == JOB_COST_SNAPSHOTS, SyncJob.status == "succeeded")
        .order_by(SyncJob.id.desc())
        .limit(1)
    ).scalar_one_or_none()
    assert job_row is not None
    assert job_row.rows_total == 0
    assert job_row.rows_inserted == 0


def test_run_cost_snapshots_uses_purchase_order_lookup(db_session) -> None:
    """The integration with ``_purchase_order_lookup`` wires through.
    Without a purchase-order source the lookup returns ``(None, None)``
    → manual cost is the only source. This is the negative path; the
    positive path is covered by the seeded-procurement-row test above.
    """

    _acct, cp = _seed_account_and_product(
        db_session,
        account_external_id="TEST_cs_po_acct",
        product_external_id="TEST_cs_po_prod",
        currency="CNY",
    )
    db_session.add(
        ManualProductCost(
            channel_product_id=cp.id,
            unit_cost=Decimal("9.99"),
            currency="CNY",
        )
    )
    db_session.flush()
    out = run_cost_snapshots(db_session)
    assert out["snapshots_written"] >= 1
    # The TEST row's snapshot is MANUAL_ENTRY (since the purchase-order
    # lookup returns None for a TEST_ channel_product_id with no link).
    snap = db_session.execute(
        select(ProductCostSnapshot).where(
            ProductCostSnapshot.channel_product_id == cp.id
        )
    ).scalars().all()
    assert any(s.cost_method == "MANUAL_ENTRY" and s.unit_cost == Decimal("9.9900") for s in snap)
