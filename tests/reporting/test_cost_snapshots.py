"""TDD tests for reporting.cost_snapshots.

Verifies the priority chain: MANUAL_ENTRY > SOURCE_PRICE. Both missing means
no snapshot and the SPU appears in the no-cost inventory.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from sqlalchemy import select

from tts_erp_v2.db.constants import ACTIVE_PRODUCT_STATUS
from tts_erp_v2.db.models import (
    ChannelAccount,
    ChannelProduct,
    Credentials,
    ManualProductCost,
    ProductCostSnapshot,
)
from tts_erp_v2.reporting import cost_snapshots

pytestmark = [
    pytest.mark.domain_reporting,
    pytest.mark.domain_finance,
    pytest.mark.layer_integration,
]


def _utc(year=2026, month=8, day=29):
    return datetime(year, month, day, tzinfo=UTC)


def _make_channel_account(session, external_id="TEST_TT_SHOP_C"):
    cred = Credentials(
        provider="tiktok", external_account_id=external_id, ciphertext=b"\x00" * 32
    )
    session.add(cred)
    session.flush()
    acct = ChannelAccount(platform="tiktok", shop_id=external_id, credential_id=cred.id)
    session.add(acct)
    session.flush()
    return acct


def _make_channel_product(session, account, external_id, status=ACTIVE_PRODUCT_STATUS):
    p = ChannelProduct(
        shop_pk=account.id,
        spu_id=external_id,
        title=f"TEST product {external_id}",
        status=status,
    )
    session.add(p)
    session.flush()
    return p


# ─── 1. priority: MANUAL_ENTRY wins ───────────────────────────────────


def test_manual_entry_wins_over_source_price(db_session):
    """Manual cost wins over the synchronized source-price estimate."""
    ca = _make_channel_account(db_session)
    cp = _make_channel_product(db_session, ca, "TEST_SPU_MANUAL")

    # Inject a manual cost and a different source-price estimate.
    manual = ManualProductCost(
        spu_pk=cp.id,
        unit_cost=Decimal("5.50"),
        currency="USD",
        valid_from=_utc(),
        valid_to=None,
        created_by="TEST_user",
    )
    db_session.add(manual)
    db_session.flush()

    actual = cost_snapshots.resolve_unit_cost(
        db_session,
        spu_pk=cp.id,
        source_unit_cost=Decimal("99.99"),
    )
    assert actual is not None
    assert actual.method == "MANUAL_ENTRY"
    assert actual.unit_cost == Decimal("5.50")
    assert actual.currency == "USD"


# ─── 2. no source ⇒ NO snapshot ──────────────────────────────────────


def test_no_source_produces_no_snapshot(db_session):
    """No manual or source-price input means no snapshot."""
    ca = _make_channel_account(db_session)
    cp = _make_channel_product(db_session, ca, "TEST_SPU_NOSRC")

    actual = cost_snapshots.resolve_unit_cost(db_session, spu_pk=cp.id)
    assert actual is None

    # No snapshot row should have been written by the resolver itself
    snaps = (
        db_session.execute(
            select(ProductCostSnapshot).where(ProductCostSnapshot.spu_pk == cp.id)
        )
        .scalars()
        .all()
    )
    assert len(snaps) == 0


# ─── 3. SOURCE_PRICE (货源价) fallback ───────────────────────────────


def test_source_price_fallback_when_no_other_source(db_session):
    """No manual cost but a 货源价 means SOURCE_PRICE with CNY default."""
    ca = _make_channel_account(db_session)
    cp = _make_channel_product(db_session, ca, "TEST_SPU_SOURCE")

    actual = cost_snapshots.resolve_unit_cost(
        db_session,
        spu_pk=cp.id,
        source_unit_cost=Decimal("7.77"),
    )
    assert actual is not None
    assert actual.method == "SOURCE_PRICE"
    assert actual.unit_cost == Decimal("7.77")
    assert actual.currency == "CNY"


def test_source_price_loses_to_manual_cost(db_session):
    """Manual entry always wins even when a 货源价 is present."""
    ca = _make_channel_account(db_session)
    cp = _make_channel_product(db_session, ca, "TEST_SPU_SRC_MANUAL")
    db_session.add(
        ManualProductCost(
            spu_pk=cp.id,
            unit_cost=Decimal("5.50"),
            currency="USD",
            valid_from=_utc(),
            valid_to=None,
            created_by="TEST_user",
        )
    )
    db_session.flush()

    actual = cost_snapshots.resolve_unit_cost(
        db_session,
        spu_pk=cp.id,
        source_unit_cost=Decimal("7.77"),
    )
    assert actual is not None
    assert actual.method == "MANUAL_ENTRY"
    assert actual.unit_cost == Decimal("5.50")


def test_rebuild_snapshots_writes_source_price_with_lookup(db_session):
    """rebuild_snapshots accepts a source_cost_lookup and writes
    SOURCE_PRICE rows for SPUs that only have a 货源价."""
    ca = _make_channel_account(db_session)
    cp_with = _make_channel_product(db_session, ca, "TEST_SPU_SRC_OK")

    written = cost_snapshots.rebuild_snapshots(
        db_session,
        calculation_version=7,
        valid_from=_utc(),
        source_cost_lookup=lambda spu_pk: (
            (Decimal("34.00"), "CNY") if spu_pk == cp_with.id else (None, None)
        ),
    )
    assert written >= 1

    snap = db_session.execute(
        select(ProductCostSnapshot).where(
            ProductCostSnapshot.spu_pk == cp_with.id,
            ProductCostSnapshot.calculation_version == 7,
        )
    ).scalar_one()
    assert snap.cost_method == "SOURCE_PRICE"
    assert snap.unit_cost == Decimal("34.0000")
    assert snap.currency == "CNY"


# ─── 4. no-cost inventory query ───────────────────────────────────────


def test_no_cost_inventory_lists_active_spus_without_snapshot(db_session):
    """active_spus_without_cost() returns active SPUs with no manual cost."""
    ca = _make_channel_account(db_session)
    cp_active_no_cost = _make_channel_product(db_session, ca, "TEST_SPU_ACTIVE_NC")
    cp_active_with_cost = _make_channel_product(db_session, ca, "TEST_SPU_ACTIVE_OK")
    cp_inactive_no_cost = _make_channel_product(
        db_session, ca, "TEST_SPU_DELISTED", status="DELETED"
    )
    _ = (cp_active_no_cost, cp_inactive_no_cost)

    # cp_active_with_cost gets a manual entry
    db_session.add(
        ManualProductCost(
            spu_pk=cp_active_with_cost.id,
            unit_cost=Decimal("9.99"),
            currency="USD",
            valid_from=_utc(),
            valid_to=None,
            created_by="TEST_user",
        )
    )
    db_session.flush()

    rows = cost_snapshots.active_spus_without_cost(db_session)
    external_ids = {r[0] for r in rows}
    assert "TEST_SPU_ACTIVE_NC" in external_ids
    assert "TEST_SPU_ACTIVE_OK" not in external_ids
    assert "TEST_SPU_DELISTED" not in external_ids  # inactive = excluded


# ─── 5. historical manual cost (valid_to set) is not picked up ────────


def test_historical_manual_cost_not_picked_up(db_session):
    """An old manual cost (valid_to NOT NULL) must NOT be used. Only the
    effective row (valid_to IS NULL) counts."""
    ca = _make_channel_account(db_session)
    cp = _make_channel_product(db_session, ca, "TEST_SPU_HIST")

    db_session.add(
        ManualProductCost(
            spu_pk=cp.id,
            unit_cost=Decimal("2.00"),  # old cheap price
            currency="USD",
            valid_from=_utc(2025, 1, 1),
            valid_to=_utc(2025, 6, 1),
            created_by="TEST_user",
        )
    )
    db_session.flush()

    actual = cost_snapshots.resolve_unit_cost(db_session, spu_pk=cp.id)
    assert actual is None  # only old (closed) manual entry exists


# ─── 8. owner ownership of the cost convention ────────────────────────
# The 货源价 lookup and the batch resolver belong to the owner module
# (tts_erp_v2.reporting.cost_snapshots). The scheduler job and every other
# consumer call the owner instead of carrying their own copy of the SQL —
# otherwise the same cost convention would exist in two places.


def test_source_price_lookup_is_owned_by_the_reporting_module():
    """位置断言：货源价取数归 owner，作业只调用不自带一份。"""
    import inspect

    from tts_erp_v2.jobs import reporting as job_reporting

    assert hasattr(cost_snapshots, "source_cost_lookup")
    assert not hasattr(job_reporting, "_source_cost_lookup")
    assert not hasattr(job_reporting, "_SQL_SOURCE_COST_DIRECT")
    assert not hasattr(job_reporting, "_SQL_SOURCE_COST_VIA_OFFER")

    job_source = inspect.getsource(job_reporting.run_cost_snapshots)
    assert "cost_snapshots.source_cost_lookup(session)" in job_source


def test_resolve_unit_costs_returns_exactly_the_resolvable_spus(db_session):
    """数量断言：批量解析只返回有源的 SPU，口径分布数量正确。"""
    ca = _make_channel_account(db_session)
    manual_cp = _make_channel_product(db_session, ca, "TEST_BATCH_MANUAL")
    source_cp = _make_channel_product(db_session, ca, "TEST_BATCH_SOURCE")
    bare_cp = _make_channel_product(db_session, ca, "TEST_BATCH_BARE")

    db_session.add(
        ManualProductCost(
            spu_pk=manual_cp.id,
            unit_cost=Decimal("5.50"),
            currency="USD",
            valid_from=_utc(),
            valid_to=None,
            created_by="TEST_user",
        )
    )
    db_session.flush()

    def lookup(cp_id: int):
        if cp_id == source_cp.id:
            return Decimal("7.77"), "CNY"
        return None, None

    resolved = cost_snapshots.resolve_unit_costs(
        db_session,
        spu_pks=[manual_cp.id, source_cp.id, bare_cp.id],
        source_cost_lookup=lookup,
    )

    assert len(resolved) == 2
    assert sum(1 for r in resolved.values() if r.method == "MANUAL_ENTRY") == 1
    assert sum(1 for r in resolved.values() if r.method == "SOURCE_PRICE") == 1
    assert bare_cp.id not in resolved


def test_resolve_unit_costs_agrees_with_single_resolution(db_session):
    """分支覆盖：批量解析与逐个解析结果完全一致（同一口径规则）。"""
    ca = _make_channel_account(db_session)
    manual_cp = _make_channel_product(db_session, ca, "TEST_EQUIV_MANUAL")
    source_cp = _make_channel_product(db_session, ca, "TEST_EQUIV_SOURCE")
    bare_cp = _make_channel_product(db_session, ca, "TEST_EQUIV_BARE")

    db_session.add(
        ManualProductCost(
            spu_pk=manual_cp.id,
            unit_cost=Decimal("5.50"),
            currency="USD",
            valid_from=_utc(),
            valid_to=None,
            created_by="TEST_user",
        )
    )
    db_session.flush()

    def lookup(cp_id: int):
        if cp_id == source_cp.id:
            return Decimal("7.77"), "CNY"
        return None, None

    pks = [manual_cp.id, source_cp.id, bare_cp.id]
    batch = cost_snapshots.resolve_unit_costs(
        db_session, spu_pks=pks, source_cost_lookup=lookup
    )

    for pk in pks:
        src_cost, src_currency = lookup(pk)
        single = cost_snapshots.resolve_unit_cost(
            db_session,
            spu_pk=pk,
            source_unit_cost=src_cost,
            source_currency=src_currency,
        )
        assert (single is None) == (pk not in batch)
        if single is not None:
            assert batch[pk].method == single.method
            assert batch[pk].unit_cost == single.unit_cost
            assert batch[pk].currency == single.currency


def test_resolve_unit_costs_without_lookup_leaves_source_only_spus_unresolved(db_session):
    """分支覆盖：没有货源价取数能力时，只有人工成本的 SPU 能被解析。"""
    ca = _make_channel_account(db_session)
    manual_cp = _make_channel_product(db_session, ca, "TEST_NOLK_MANUAL")
    source_cp = _make_channel_product(db_session, ca, "TEST_NOLK_SOURCE")

    db_session.add(
        ManualProductCost(
            spu_pk=manual_cp.id,
            unit_cost=Decimal("5.50"),
            currency="USD",
            valid_from=_utc(),
            valid_to=None,
            created_by="TEST_user",
        )
    )
    db_session.flush()

    resolved = cost_snapshots.resolve_unit_costs(
        db_session, spu_pks=[manual_cp.id, source_cp.id]
    )

    assert len(resolved) == 1
    assert manual_cp.id in resolved
    assert source_cp.id not in resolved
