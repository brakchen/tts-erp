"""Tests for the 2026-09-01 scheduler extension: miaoshou + reporting jobs.

Covers:
* JOBS registry contains the 6 new entries with correct wiring
  (entrypoint / interval / is_tiktok=False).
* ``_run_system_job`` dispatch: entrypoint resolution, commit on
  success, sentinel failed row on exception (mocked session — no DB).
* ``jobs/reporting.py`` wrappers: run inside the rolled-back test
  transaction so prod data is never persisted.

Note on isolation: these tests run against the dev DB inside the
rollback fixture. Assertions are bounded to TEST_-seeded rows only.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from unittest.mock import MagicMock

import pytest
from sqlalchemy import select

from tts_erp_v2.db.models import (
    ChannelAccount,
    ChannelProduct,
    ManualProductCost,
    ProductCostSnapshot,
    ProductProfitDaily,
    SalesOrder,
    SalesOrderLine,
)
from tts_erp_v2.jobs import reporting as reporting_job
from tts_erp_v2.sync_worker import scheduler
from tts_erp_v2.sync_worker.scheduler import JOBS, JobSpec

# ─── Registry wiring ────────────────────────────────────────────────


def test_jobs_registry_includes_miaoshou_and_reporting():
    expected = {
        "miaoshou.shops": ("tts_erp_v2.jobs.miaoshou.shops", "sync_shops", 21600),
        "miaoshou.collect_box": (
            "tts_erp_v2.jobs.miaoshou.collect_box",
            "sync_collect_box",
            1800,
        ),
        "miaoshou.move_collect": (
            "tts_erp_v2.jobs.miaoshou.move_collect",
            "sync_move_collect",
            1800,
        ),
        "reporting.cost_snapshots": (
            "tts_erp_v2.jobs.reporting",
            "run_cost_snapshots",
            21600,
        ),
        "reporting.profit_daily": (
            "tts_erp_v2.jobs.reporting",
            "run_profit_daily",
            3600,
        ),
    }
    # miaoshou.purchase_orders intentionally absent: its endpoint path 404s
    # against the prod ERP API (routeNotFound, verified 2026-09-01).
    for name, (module_path, entrypoint, interval) in expected.items():
        spec = JOBS.get(name)
        assert spec is not None, f"{name} missing from JOBS"
        assert spec.module_path == module_path
        assert spec.entrypoint == entrypoint
        assert spec.interval_seconds == interval
        assert spec.is_tiktok is False
        assert spec.needs_token_registry is False


def test_token_refresh_spec_uses_registry_entrypoint():
    spec = JOBS["token.refresh"]
    assert spec.is_tiktok is False
    assert spec.entrypoint == "sync_token_refresh"
    assert spec.needs_token_registry is True


# ─── _run_system_job dispatch (mocked session, no DB) ───────────────


def _fake_module(monkeypatch: pytest.MonkeyPatch, fn) -> None:
    """Inject a fake module into importlib for the dispatch test."""
    fake = MagicMock()
    fake.fake_entry = fn
    monkeypatch.setattr(scheduler.importlib, "import_module", lambda _path: fake)


def test_system_job_commits_on_success(monkeypatch):
    called = {"n": 0}

    def fake_entry(session, **_kwargs):
        called["n"] += 1
        return {"ok": True}

    _fake_module(monkeypatch, fake_entry)
    session = MagicMock()
    factory = MagicMock(return_value=session)
    spec = JobSpec(
        job_name="reporting.profit_daily",
        module_path="fake.module",
        interval_seconds=60,
        is_tiktok=False,
        entrypoint="fake_entry",
    )
    scheduler._run_system_job(spec, factory)
    assert called["n"] == 1
    session.commit.assert_called_once()
    session.close.assert_called_once()


def test_system_job_writes_sentinel_on_exception(monkeypatch):
    def boom(_session, **_kwargs):
        raise RuntimeError("boom")

    _fake_module(monkeypatch, boom)
    session = MagicMock()
    factory = MagicMock(return_value=session)
    sentinel_calls = []
    monkeypatch.setattr(
        scheduler,
        "_record_failed_tick",
        lambda sf, spec, reason: sentinel_calls.append(reason),
    )
    spec = JobSpec(
        job_name="miaoshou.shops",
        module_path="fake.module",
        interval_seconds=60,
        is_tiktok=False,
        entrypoint="fake_entry",
    )
    scheduler._run_system_job(spec, factory)  # must not raise
    session.rollback.assert_called()
    assert sentinel_calls == ["tick raised: RuntimeError: boom"]


def test_system_job_passes_registry_only_for_token_refresh(monkeypatch):
    seen_kwargs = {}

    def fake_entry(session, **kwargs):
        seen_kwargs.update(kwargs)
        return {}

    _fake_module(monkeypatch, fake_entry)
    session = MagicMock()
    factory = MagicMock(return_value=session)
    fake_registry = MagicMock(return_value=object())
    monkeypatch.setattr(
        "tts_erp_v2.proxy.tiktok_auth.build_token_registry", fake_registry
    )
    spec = JobSpec(
        job_name="token.refresh",
        module_path="fake.module",
        interval_seconds=60,
        is_tiktok=False,
        entrypoint="fake_entry",
        needs_token_registry=True,
    )
    scheduler._run_system_job(spec, factory)
    assert "registry" in seen_kwargs


# ─── reporting wrappers (rolled-back transaction) ───────────────────


def _seed_manual_cost_spu(db_session) -> ChannelProduct:
    acct = ChannelAccount(
        platform="tiktok",
        shop_id="TEST_MS_RPT",
        account_name="test",
        status="ACTIVE",
    )
    db_session.add(acct)
    db_session.flush()
    cp = ChannelProduct(
        shop_pk=acct.id,
        spu_id="TEST_MS_RPT_P1",
        title="t",
        status="ACTIVATE",
    )
    db_session.add(cp)
    db_session.flush()
    db_session.add(
        ManualProductCost(
            spu_pk=cp.id,
            unit_cost=Decimal("3.50"),
            currency="CNY",
        )
    )
    db_session.flush()
    return cp


def test_run_cost_snapshots_writes_seeded_spu(db_session):
    cp = _seed_manual_cost_spu(db_session)
    out = reporting_job.run_cost_snapshots(db_session)
    assert out["snapshots_written"] >= 1
    row = db_session.execute(
        select(ProductCostSnapshot).where(
            ProductCostSnapshot.spu_pk == cp.id
        )
    ).scalar_one()
    assert row.cost_method == "MANUAL_ENTRY"
    assert row.unit_cost == Decimal("3.5000")
    assert row.calculation_version == out["calculation_version"]


def test_run_profit_daily_far_future_date(db_session):
    """Rebuild for a date with no orders writes zero rows (and does not
    touch prod rows for today)."""
    rows = reporting_job.profit_daily.rebuild(db_session, profit_date=date(2030, 1, 1))
    assert rows == []


def test_run_profit_daily_counts_paid_order(db_session):
    """A COMPLETED order on a far-future date produces exactly one row
    for its SPU (bounded assertion — prod has no such date)."""
    acct = ChannelAccount(
        platform="tiktok",
        shop_id="TEST_MS_RPT2",
        account_name="test",
        status="ACTIVE",
    )
    db_session.add(acct)
    db_session.flush()
    cp = ChannelProduct(
        shop_pk=acct.id,
        spu_id="TEST_MS_RPT_P2",
        title="t",
        status="ACTIVATE",
    )
    db_session.add(cp)
    db_session.flush()
    order = SalesOrder(
        shop_pk=acct.id,
        order_id="TEST_MS_RPT_O1",
        status="COMPLETED",
        currency="VND",
        payment_amount=Decimal(100000),
        paid_at=datetime(2030, 1, 1, 2, 0, tzinfo=UTC),
    )
    db_session.add(order)
    db_session.flush()
    db_session.add(
        SalesOrderLine(
            order_pk=order.id,
            external_line_id="TEST_MS_RPT_L1",
            spu_pk=cp.id,
            quantity=Decimal(2),
            unit_price=Decimal(50000),
            currency="VND",
        )
    )
    db_session.flush()
    rows = reporting_job.profit_daily.rebuild(db_session, profit_date=date(2030, 1, 1))
    mine = [
        r
        for r in rows
        if r.spu_pk == cp.id and r.profit_date == date(2030, 1, 1)
    ]
    assert len(mine) == 1
    assert mine[0].units_sold == Decimal("2.0000")
    assert mine[0].gross_revenue == Decimal("100000.00")
    # No cost snapshot for this SPU → cogs/profit NULL, not fake math.
    assert mine[0].estimated_cogs is None
    assert mine[0].estimated_gross_profit is None
    # cleanup within the rolled-back tx is unnecessary; fixture rolls back.
    assert isinstance(mine[0], ProductProfitDaily)
