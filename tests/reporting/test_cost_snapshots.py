"""TDD tests for reporting.cost_snapshots.

Verifies the priority chain: MANUAL_ENTRY > PURCHASE_PRICE. Both missing means
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
    MiaoshouPurchasePrice,
    ProductCostSnapshot,
)
from tts_erp_v2.reporting import cost_snapshots
from tts_erp_v2.reporting.purchase_cost import load_purchase_costs

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


def test_manual_entry_wins_over_purchase_price(db_session):
    """Manual cost wins over a different verified purchase price."""
    ca = _make_channel_account(db_session)
    cp = _make_channel_product(db_session, ca, "TEST_SPU_MANUAL")

    # Inject a manual cost and a different verified purchase price.
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
        purchase_cost_cny=Decimal("99.99"),
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


# ─── 3. Public list price is not purchase cost ───────────────────────


def test_source_price_cannot_be_used_as_cost(db_session):
    """A collect-box listing alone must not create a cost snapshot."""
    ca = _make_channel_account(db_session)
    cp = _make_channel_product(db_session, ca, "TEST_SPU_SOURCE")

    actual = cost_snapshots.resolve_unit_cost(
        db_session,
        spu_pk=cp.id,
    )
    assert actual is None


def test_manual_cost_is_used_without_purchase_price(db_session):
    """Manual entry is used when there is no purchase price."""
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
    )
    assert actual is not None
    assert actual.method == "MANUAL_ENTRY"
    assert actual.unit_cost == Decimal("5.50")


def test_confirmed_purchase_price_precedes_list_price_and_writes_snapshot(db_session):
    account = _make_channel_account(db_session, "TEST_PURCHASE_SHOP")
    spu = _make_channel_product(db_session, account, "TEST_PURCHASE_SPU")
    credential = Credentials(
        provider="miaoshou",
        external_account_id="TEST_PURCHASE_APP",
        ciphertext=b"\x00" * 32,
    )
    db_session.add(credential)
    db_session.flush()
    db_session.add(
        MiaoshouPurchasePrice(
            credential_id=credential.id,
            miaoshou_shop_id="TEST_MS_PURCHASE_SHOP",
            shop_pk=account.id,
            spu_id=spu.spu_id,
            unit_cost=Decimal("27.25"),
            currency="CNY",
            source_purchase_order_sn="TEST_PURCHASE_ORDER",
            source_item_id="TEST_OFFER",
            source_purchase_at=_utc(),
            resolution_status="matched_shop",
            evidence={},
        )
    )
    db_session.flush()

    assert load_purchase_costs(db_session, [spu.id]) == {spu.id: Decimal("27.25")}
    resolved = cost_snapshots.resolve_unit_cost(
        db_session,
        spu_pk=spu.id,
        purchase_cost_cny=Decimal("27.25"),
    )
    assert resolved is not None
    assert (resolved.method, resolved.unit_cost) == (
        "PURCHASE_PRICE",
        Decimal("27.25"),
    )
    cost_snapshots.rebuild_snapshots(
        db_session,
        calculation_version=8,
        valid_from=_utc(),
        spu_pks=[spu.id],
    )
    snapshot = db_session.scalars(
        select(ProductCostSnapshot).where(
            ProductCostSnapshot.spu_pk == spu.id,
            ProductCostSnapshot.calculation_version == 8,
        )
    ).one()
    assert (snapshot.cost_method, snapshot.unit_cost) == (
        "PURCHASE_PRICE",
        Decimal("27.25"),
    )


def test_conflicting_latest_purchase_prices_are_not_guessed(db_session):
    account = _make_channel_account(db_session, "TEST_CONFLICT_SHOP")
    spu = _make_channel_product(db_session, account, "TEST_CONFLICT_SPU")
    for index, price in enumerate(("21.00", "23.00")):
        credential = Credentials(
            provider="miaoshou",
            external_account_id=f"TEST_CONFLICT_APP_{index}",
            ciphertext=b"\x00" * 32,
        )
        db_session.add(credential)
        db_session.flush()
        db_session.add(
            MiaoshouPurchasePrice(
                credential_id=credential.id,
                miaoshou_shop_id=f"TEST_CONFLICT_MS_{index}",
                shop_pk=account.id,
                spu_id=spu.spu_id,
                unit_cost=Decimal(price),
                currency="CNY",
                source_purchase_order_sn=f"TEST_CONFLICT_ORDER_{index}",
                source_item_id="TEST_CONFLICT_OFFER",
                source_purchase_at=_utc(),
                resolution_status="matched_shop",
                evidence={},
            )
        )
    db_session.flush()

    assert load_purchase_costs(db_session, [spu.id]) == {}


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
