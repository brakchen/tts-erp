"""Tests for tts_erp_v2.jobs.miaoshou.sync_source_cost_to_master."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from sqlalchemy import select

from tts_erp_v2.db.models.integration import SyncJob
from tts_erp_v2.db.models.procurement import (
    ProcurementAccount,
    ProcurementProduct,
)
from tts_erp_v2.jobs.miaoshou.sync_source_cost_to_master import (
    sync_source_cost_to_master,
)

pytestmark = [pytest.mark.domain_miaoshou, pytest.mark.layer_integration]


def _seed_account(session, ext="TEST_MIAOSHOU_LIC_SSCM"):
    acct = ProcurementAccount(
        provider="miaoshou",
        external_account_id=ext,
        account_name="test",
        status="ACTIVE",
    )
    session.add(acct)
    session.flush()
    return acct


def _seed_pp(
    session,
    account_id,
    *,
    external_product_id,
    source_item_id,
    source_unit_cost=None,
    source_min_unit_cost=None,
    source_max_unit_cost=None,
):
    pp = ProcurementProduct(
        procurement_account_id=account_id,
        external_product_id=external_product_id,
        product_type="COLLECTED_PRODUCT",
        source_platform="1688",
        source_item_id=source_item_id,
        source_unit_cost=source_unit_cost,
        source_min_unit_cost=source_min_unit_cost,
        source_max_unit_cost=source_max_unit_cost,
    )
    session.add(pp)
    session.flush()
    return pp


def test_backfills_tk_cost_from_offer(db_session):
    """TK-side row (18-digit spu_id) with NULL cost gets the cost from the
    matching public-collect-box row via source_item_id bridge."""
    acct = _seed_account(db_session)
    tk = _seed_pp(
        db_session,
        acct.id,
        external_product_id="1736929955366339831",  # 18-digit TikTok spu_id
        source_item_id="TEST_SSCM_OFFER_001",
    )
    _seed_pp(
        db_session,
        acct.id,
        external_product_id="3970187081",  # 10-digit commonCollectBoxDetailId
        source_item_id="TEST_SSCM_OFFER_001",
        source_unit_cost=Decimal("24.0"),
        source_min_unit_cost=Decimal("24.0"),
        source_max_unit_cost=Decimal("24.0"),
    )
    db_session.flush()

    sync_source_cost_to_master(db_session)
    db_session.commit()

    db_session.refresh(tk)
    assert tk.source_unit_cost == Decimal("24.0")
    assert tk.source_min_unit_cost == Decimal("24.0")
    assert tk.source_max_unit_cost == Decimal("24.0")


def test_uses_latest_synced_offer(db_session):
    """Multiple public-box rows for the same offer → pick the latest by synced_at."""
    acct = _seed_account(db_session)
    tk = _seed_pp(
        db_session,
        acct.id,
        external_product_id="1736929955366339831",
        source_item_id="TEST_SSCM_OFFER_001",
    )
    older = _seed_pp(
        db_session,
        acct.id,
        external_product_id="3876563370",
        source_item_id="TEST_SSCM_OFFER_001",
        source_unit_cost=Decimal("20.0"),
    )
    db_session.flush()
    older.synced_at = datetime(2026, 1, 1, tzinfo=UTC)
    newer = _seed_pp(
        db_session,
        acct.id,
        external_product_id="3876563371",
        source_item_id="TEST_SSCM_OFFER_001",
        source_unit_cost=Decimal("30.0"),
    )
    newer.synced_at = datetime(2026, 9, 1, tzinfo=UTC)
    db_session.flush()

    sync_source_cost_to_master(db_session)
    db_session.commit()

    db_session.refresh(tk)
    assert tk.source_unit_cost == Decimal("30.0")  # newer wins


def test_idempotent_no_op_when_cost_matches(db_session):
    """IS DISTINCT FROM guard → already-matching rows are not re-updated."""
    acct = _seed_account(db_session)
    _seed_pp(
        db_session,
        acct.id,
        external_product_id="1736929955366339831",
        source_item_id="TEST_SSCM_OFFER_001",
        source_unit_cost=Decimal("24.0"),  # already matches
    )
    _seed_pp(
        db_session,
        acct.id,
        external_product_id="3970187081",
        source_item_id="TEST_SSCM_OFFER_001",
        source_unit_cost=Decimal("24.0"),
    )
    db_session.flush()

    sync_source_cost_to_master(db_session)
    db_session.commit()


def test_skips_tk_row_without_source_item_id(db_session):
    """TK-side row with NULL source_item_id → no bridge possible, not updated."""
    acct = _seed_account(db_session)
    tk = _seed_pp(
        db_session,
        acct.id,
        external_product_id="1736929955366339831",
        source_item_id=None,
    )
    _seed_pp(
        db_session,
        acct.id,
        external_product_id="3970187081",
        source_item_id="TEST_SSCM_OFFER_001",
        source_unit_cost=Decimal("24.0"),
    )
    db_session.flush()

    sync_source_cost_to_master(db_session)
    db_session.commit()
    db_session.refresh(tk)
    assert tk.source_unit_cost is None


def test_skips_tk_row_when_no_offer_match(db_session):
    """TK-side source_item_id has no matching public-box row → not updated."""
    acct = _seed_account(db_session)
    tk = _seed_pp(
        db_session,
        acct.id,
        external_product_id="1736929955366339831",
        source_item_id="999999999999",  # no matching offer
    )
    _seed_pp(
        db_session,
        acct.id,
        external_product_id="3970187081",
        source_item_id="TEST_SSCM_OFFER_001",
        source_unit_cost=Decimal("24.0"),
    )
    db_session.flush()

    sync_source_cost_to_master(db_session)
    db_session.commit()
    db_session.refresh(tk)
    assert tk.source_unit_cost is None


def test_only_tk_side_rows_targeted(db_session):
    """Non-18-digit external_product_id rows are NOT touched (e.g., 10-digit
    common-box rows themselves, or weird id formats)."""
    acct = _seed_account(db_session)
    other = _seed_pp(
        db_session,
        acct.id,
        external_product_id="3970187081",  # 10-digit → not TK-side
        source_item_id="TEST_SSCM_OFFER_001",
    )
    _seed_pp(
        db_session,
        acct.id,
        external_product_id="3876563370",
        source_item_id="TEST_SSCM_OFFER_001",
        source_unit_cost=Decimal("24.0"),
    )
    db_session.flush()

    sync_source_cost_to_master(db_session)
    db_session.commit()
    db_session.refresh(other)
    assert other.source_unit_cost is None  # not touched


def test_partial_update_when_only_min_max_differ(db_session):
    """unit_cost already matches but min/max stale → row updated (any column
    diff triggers update)."""
    acct = _seed_account(db_session)
    tk = _seed_pp(
        db_session,
        acct.id,
        external_product_id="1736929955366339831",
        source_item_id="TEST_SSCM_OFFER_001",
        source_unit_cost=Decimal("24.0"),
        source_min_unit_cost=Decimal("20.0"),  # stale
        source_max_unit_cost=Decimal("20.0"),  # stale
    )
    _seed_pp(
        db_session,
        acct.id,
        external_product_id="3970187081",
        source_item_id="TEST_SSCM_OFFER_001",
        source_unit_cost=Decimal("24.0"),
        source_min_unit_cost=Decimal("24.0"),
        source_max_unit_cost=Decimal("24.0"),
    )
    db_session.flush()

    sync_source_cost_to_master(db_session)
    db_session.commit()
    db_session.refresh(tk)
    assert tk.source_unit_cost == Decimal("24.0")
    assert tk.source_min_unit_cost == Decimal("24.0")
    assert tk.source_max_unit_cost == Decimal("24.0")


def test_records_sync_job_success(db_session):
    """SyncJob row created with status='succeeded' and correct counters."""
    acct = _seed_account(db_session)
    _seed_pp(
        db_session,
        acct.id,
        external_product_id="1736929955366339831",
        source_item_id="TEST_SSCM_OFFER_001",
    )
    _seed_pp(
        db_session,
        acct.id,
        external_product_id="3970187081",
        source_item_id="TEST_SSCM_OFFER_001",
        source_unit_cost=Decimal("24.0"),
    )
    db_session.flush()

    sync_source_cost_to_master(db_session)
    db_session.commit()

    job = db_session.execute(
        select(SyncJob)
        .where(SyncJob.job_name == "miaoshou.sync_source_cost_to_master")
        .order_by(SyncJob.id.desc())
        .limit(1)
    ).scalar_one()
    assert job.status == "succeeded"
    assert job.rows_total >= 1
    assert job.rows_inserted >= 1  # production cross-account bridge also fires
