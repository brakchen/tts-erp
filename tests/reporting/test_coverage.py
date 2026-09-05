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
    LinkIssue,
    ProcurementAccount,
    ProcurementProduct,
    ProductCostSnapshot,
    ProductLink,
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
    a = ChannelAccount(
        platform="tiktok", shop_id="TEST_TT_COV", credential_id=cred.id
    )
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


# ─── 2. spu linkage coverage ─────────────────────────────────────────


def test_spu_linkage_coverage(db_session):
    """spu_linkage_coverage = products_spu that have at least one
    effective product_link / total active products_spu."""
    base = coverage.spu_linkage_coverage(db_session)
    a = _acct(db_session)
    cp_linked = ChannelProduct(
        shop_pk=a.id,
        spu_id="TEST_LINK_1",
        status="ACTIVE",
    )
    cp_unlinked = ChannelProduct(
        shop_pk=a.id,
        spu_id="TEST_NOLINK_1",
        status="ACTIVE",
    )
    db_session.add_all([cp_linked, cp_unlinked])
    db_session.flush()
    # Seed a real procurement_account + procurement_product so the FK
    # on product_links.procurement_product_id is satisfied.
    pa_cred = Credentials(
        provider="miaoshou",
        external_account_id="TEST_MS_COV",
        ciphertext=b"\x00" * 32,
    )
    db_session.add(pa_cred)
    db_session.flush()
    pa = ProcurementAccount(
        provider="miaoshou",
        external_account_id="TEST_MS_COV",
        credential_id=pa_cred.id,
    )
    db_session.add(pa)
    db_session.flush()
    pp = ProcurementProduct(
        procurement_account_id=pa.id,
        external_product_id="TEST_MS_PROD_COV",
        product_type="COLLECTED_PRODUCT",
        status="ACTIVE",
    )
    db_session.add(pp)
    db_session.flush()
    db_session.add(
        ProductLink(
            procurement_product_id=pp.id,
            spu_pk=cp_linked.id,
            relation_type="MIAOSHOU_PUBLISHED_TO_TIKTOK",
            valid_from=_utc(),
            valid_to=None,
        )
    )
    db_session.flush()

    m = coverage.spu_linkage_coverage(db_session)
    assert m["active_spus"] == base["active_spus"] + 2
    assert m["linked_spus"] == base["linked_spus"] + 1
    assert m["rate"] == pytest.approx(
        (base["linked_spus"] + 1) / (base["active_spus"] + 2)
    )


# ─── 3. conflict rate (link issues) ──────────────────────────────────


def test_link_issue_rate(db_session):
    """conflict_rate = unresolved link issues / total products_spu."""
    base = coverage.link_issue_rate(db_session)
    a = _acct(db_session)
    cp = ChannelProduct(
        shop_pk=a.id,
        spu_id="TEST_CONF_1",
        status="ACTIVE",
    )
    db_session.add(cp)
    db_session.flush()
    db_session.add(
        LinkIssue(
            issue_type="AMBIGUOUS_SOURCE",
            spu_pk=cp.id,
            status="OPEN",
            candidate_count=2,
        )
    )
    db_session.flush()

    m = coverage.link_issue_rate(db_session)
    assert m["unresolved_issues"] == base["unresolved_issues"] + 1
    assert m["active_spus"] == base["active_spus"] + 1
    assert m["rate"] == pytest.approx(
        (base["unresolved_issues"] + 1) / (base["active_spus"] + 1)
    )


# ─── 4. cost-coverage rate ───────────────────────────────────────────


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
