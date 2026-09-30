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
