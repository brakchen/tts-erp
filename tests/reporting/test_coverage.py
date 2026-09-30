"""TDD tests for reporting.coverage — coverage metric queries.

Returns dicts with normalized metric names. These power the §16
acceptance KPI dashboard.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from tts_erp_v2.db.models import (
    ChannelAccount,
    ChannelProduct,
    Credentials,
    ProductCostSnapshot,
    SalesOrder,
    SalesOrderLine,
)
from tts_erp_v2.reporting import coverage

pytestmark = [pytest.mark.domain_reporting, pytest.mark.layer_integration]


def _utc(year=2026, month=8, day=29):
    return datetime(year, month, day, tzinfo=UTC)


def _acct(session):
    cred = Credentials(
        provider="tiktok", external_account_id="TEST_TT_COV", ciphertext=b"\x00" * 32
    )
    session.add(cred)
    session.flush()
    a = ChannelAccount(platform="tiktok", shop_id="TEST_TT_COV", credential_id=cred.id)
    session.add(a)
    session.flush()
    return a


# ─── 1. line-product resolution rate ─────────────────────────────────


def test_line_product_resolution_rate(db_session):
    """order_line_product_resolution_rate = (lines with non-null
    spu_pk) / total lines."""
    base = coverage.line_product_resolution_rate(db_session)
    a = _acct(db_session)
    cp = ChannelProduct(
        shop_pk=a.id,
        spu_id="TEST_RES_1",
        title="TEST r",
        status="ACTIVE",
    )
    db_session.add(cp)
    db_session.flush()
    so = SalesOrder(
        shop_pk=a.id,
        order_id="TEST_SO_RES_1",
        status="PAID",
        currency="USD",
        payment_amount=Decimal(10),
        paid_at=_utc(),
    )
    db_session.add(so)
    db_session.flush()
    # 2 lines: 1 resolved, 1 unresolved
    db_session.add(
        SalesOrderLine(
            order_pk=so.id,
            external_line_id="L1",
            spu_pk=cp.id,
            quantity=Decimal(1),
            unit_price=Decimal(5),
            currency="USD",
            line_status="NORMAL",
        )
    )
    db_session.add(
        SalesOrderLine(
            order_pk=so.id,
            external_line_id="L2",
            spu_pk=None,
            quantity=Decimal(1),
            unit_price=Decimal(5),
            currency="USD",
            line_status="NORMAL",
        )
    )
    db_session.flush()

    m = coverage.line_product_resolution_rate(db_session)
    # baseline-delta: the dev DB may already hold migrated production rows
    assert m["total_lines"] == base["total_lines"] + 2
    assert m["resolved_lines"] == base["resolved_lines"] + 1
    assert m["rate"] == pytest.approx(
        (base["resolved_lines"] + 1) / (base["total_lines"] + 2)
    )


# ─── 2. cost-coverage rate ───────────────────────────────────────────


def test_cost_coverage_rate(db_session):
    """cost_coverage_rate = active spus with effective cost snapshot /
    active spus total."""
    base = coverage.cost_coverage_rate(db_session)
    a = _acct(db_session)
    cp1 = ChannelProduct(
        shop_pk=a.id,
        spu_id="TEST_COST_1",
        status="ACTIVE",
    )
    cp2 = ChannelProduct(
        shop_pk=a.id,
        spu_id="TEST_COST_2",
        status="ACTIVE",
    )
    db_session.add_all([cp1, cp2])
    db_session.flush()
    db_session.add(
        ProductCostSnapshot(
            spu_pk=cp1.id,
            cost_method="MANUAL_ENTRY",
            unit_cost=Decimal(5),
            currency="USD",
            valid_from=_utc(),
            valid_to=None,
            calculation_version=1,
        )
    )
    db_session.flush()

    m = coverage.cost_coverage_rate(db_session)
    assert m["active_spus"] == base["active_spus"] + 2
    assert m["costed_spus"] == base["costed_spus"] + 1
    assert m["rate"] == pytest.approx(
        (base["costed_spus"] + 1) / (base["active_spus"] + 2)
    )
