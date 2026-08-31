"""TDD tests for reporting.cost_snapshots.

Verifies the priority chain: MANUAL_ENTRY > LATEST_PURCHASE_COST >
PERIOD_AVERAGE_COST > WEIGHTED_AVERAGE_COST. 1688 collect listing cost
is explicitly NOT a fallback. Products with no source produce NO
snapshot and show up in the no-cost inventory.
"""

from __future__ import annotations

from datetime import datetime, timezone
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
    return datetime(year, month, day, tzinfo=timezone.utc)


def _make_channel_account(session, external_id="TEST_TT_SHOP_C"):
    cred = Credentials(
        provider="tiktok", external_account_id=external_id, ciphertext=b"\x00" * 32
    )
    session.add(cred)
    session.flush()
    acct = ChannelAccount(
        platform="tiktok", external_account_id=external_id, credential_id=cred.id
    )
    session.add(acct)
    session.flush()
    return acct


def _make_channel_product(session, account, external_id, status=ACTIVE_PRODUCT_STATUS):
    p = ChannelProduct(
        channel_account_id=account.id,
        external_product_id=external_id,
        title=f"TEST product {external_id}",
        status=status,
    )
    session.add(p)
    session.flush()
    return p


# ─── 1. priority: MANUAL_ENTRY wins ───────────────────────────────────


def test_manual_entry_wins_over_purchase_order(db_session):
    """When both manual_product_costs AND purchase_order_lines exist for
    a SPU, the snapshot uses MANUAL_ENTRY (highest priority)."""
    ca = _make_channel_account(db_session)
    cp = _make_channel_product(db_session, ca, "TEST_SPU_MANUAL")

    # Inject a manual cost (cheaper than the purchase-order-derived cost)
    manual = ManualProductCost(
        channel_product_id=cp.id,
        unit_cost=Decimal("5.50"),
        currency="USD",
        valid_from=_utc(),
        valid_to=None,
        created_by="TEST_user",
    )
    db_session.add(manual)
    db_session.flush()

    # Inject a fake purchase order line — the snapshot engine must NOT
    # use it when manual exists. We use a sentinel raw value via
    # compute_unit_cost_from_purchase_orders returning a different cost.
    actual = cost_snapshots.resolve_unit_cost(
        db_session,
        channel_product_id=cp.id,
        purchase_order_unit_cost=Decimal("99.99"),
    )
    assert actual is not None
    assert actual.method == "MANUAL_ENTRY"
    assert actual.unit_cost == Decimal("5.50")
    assert actual.currency == "USD"


# ─── 2. fallback to LATEST_PURCHASE_COST ──────────────────────────────


def test_fallback_to_latest_purchase_cost_when_no_manual(db_session):
    """No manual cost ⇒ use purchase-order-derived LATEST_PURCHASE_COST."""
    ca = _make_channel_account(db_session)
    cp = _make_channel_product(db_session, ca, "TEST_SPU_PURCHASE")

    actual = cost_snapshots.resolve_unit_cost(
        db_session,
        channel_product_id=cp.id,
        purchase_order_unit_cost=Decimal("12.34"),
    )
    assert actual is not None
    assert actual.method == "LATEST_PURCHASE_COST"
    assert actual.unit_cost == Decimal("12.34")


# ─── 3. no source ⇒ NO snapshot ──────────────────────────────────────


def test_no_source_produces_no_snapshot(db_session):
    """When both manual AND purchase-order inputs are missing, return
    None. Cost-snapshot job will then skip this SPU; it will appear in
    the no-cost inventory (a separate query)."""
    ca = _make_channel_account(db_session)
    cp = _make_channel_product(db_session, ca, "TEST_SPU_NOSRC")

    actual = cost_snapshots.resolve_unit_cost(
        db_session,
        channel_product_id=cp.id,
        purchase_order_unit_cost=None,
    )
    assert actual is None

    # No snapshot row should have been written by the resolver itself
    snaps = (
        db_session.execute(
            select(ProductCostSnapshot).where(
                ProductCostSnapshot.channel_product_id == cp.id
            )
        )
        .scalars()
        .all()
    )
    assert len(snaps) == 0


# ─── 4. COLLECT_LISTING_COST is rejected ─────────────────────────────


def test_collect_listing_cost_is_rejected_explicitly(db_session):
    """Passing the COLLECT_LISTING_COST method (1688 listing price) is
    FORBIDDEN. Even if the caller provides a price, resolve_unit_cost
    must ignore it and return None when no other source exists."""
    ca = _make_channel_account(db_session)
    cp = _make_channel_product(db_session, ca, "TEST_SPU_LISTING")

    # If a caller tries to pass listing cost, the function must still
    # only accept MANUAL_ENTRY or purchase-order-derived values.
    actual = cost_snapshots.resolve_unit_cost(
        db_session,
        channel_product_id=cp.id,
        purchase_order_unit_cost=None,
        # An attempted listing cost bypass should be rejected:
        collect_listing_cost=Decimal("7.77"),
    )
    assert actual is None


# ─── 5. no-cost inventory query ───────────────────────────────────────


def test_no_cost_inventory_lists_active_spus_without_snapshot(db_session):
    """active_spus_without_cost() returns active channel_products with no
    effective cost (no manual, no purchase_order unit cost)."""
    ca = _make_channel_account(db_session)
    cp_active_no_cost = _make_channel_product(db_session, ca, "TEST_SPU_ACTIVE_NC")
    cp_active_with_cost = _make_channel_product(db_session, ca, "TEST_SPU_ACTIVE_OK")
    cp_inactive_no_cost = _make_channel_product(
        db_session, ca, "TEST_SPU_DELISTED", status="DELETED"
    )
    _ = (cp_active_no_cost, cp_inactive_no_cost)  # noqa: F841

    # cp_active_with_cost gets a manual entry
    db_session.add(
        ManualProductCost(
            channel_product_id=cp_active_with_cost.id,
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


# ─── 6. historical manual cost (valid_to set) is not picked up ────────


def test_historical_manual_cost_not_picked_up(db_session):
    """An old manual cost (valid_to NOT NULL) must NOT be used. Only the
    effective row (valid_to IS NULL) counts."""
    ca = _make_channel_account(db_session)
    cp = _make_channel_product(db_session, ca, "TEST_SPU_HIST")

    db_session.add(
        ManualProductCost(
            channel_product_id=cp.id,
            unit_cost=Decimal("2.00"),  # old cheap price
            currency="USD",
            valid_from=_utc(2025, 1, 1),
            valid_to=_utc(2025, 6, 1),
            created_by="TEST_user",
        )
    )
    db_session.flush()

    actual = cost_snapshots.resolve_unit_cost(
        db_session,
        channel_product_id=cp.id,
        purchase_order_unit_cost=None,
    )
    assert actual is None  # only old (closed) manual entry exists
