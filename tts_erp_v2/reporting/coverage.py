"""reporting.coverage — KPI metric queries.

Each function returns a dict so the API/dashboard can render directly.
Numbers are computed at query time (no caching yet); this is fine for
the current data scale (hundreds of SPUs).
"""
from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from tts_erp_v2.db.models import (
    ChannelProduct,
    LinkIssue,
    ProductCostSnapshot,
    ProductLink,
    SalesOrderLine,
)


def line_product_resolution_rate(session: Session) -> dict:
    """SalesOrderLine.spu_pk NOT NULL ratio.

    Denominator includes every line; numerator excludes lines with NULL
    spu_pk (typically because the product hadn't synced
    yet when the order arrived).
    """
    total = session.execute(select(func.count(SalesOrderLine.id))).scalar_one()
    resolved = session.execute(
        select(func.count(SalesOrderLine.id))
        .where(SalesOrderLine.spu_pk.is_not(None))
    ).scalar_one()
    rate = float(resolved) / float(total) if total else 0.0
    return {
        "total_lines": int(total),
        "resolved_lines": int(resolved),
        "rate": rate,
    }


def spu_linkage_coverage(session: Session) -> dict:
    """Fraction of active SPUs that have at least one effective
    product_link."""
    active = session.execute(
        select(func.count(ChannelProduct.id))
        .where(ChannelProduct.status == "ACTIVE")
    ).scalar_one()
    linked = session.execute(
        select(func.count(func.distinct(ProductLink.spu_pk)))
        .where(ProductLink.valid_to.is_(None))
    ).scalar_one()
    rate = float(linked) / float(active) if active else 0.0
    return {
        "active_spus": int(active),
        "linked_spus": int(linked),
        "rate": rate,
    }


def link_issue_rate(session: Session) -> dict:
    """Unresolved LinkIssue count divided by active SPU count."""
    active = session.execute(
        select(func.count(ChannelProduct.id))
        .where(ChannelProduct.status == "ACTIVE")
    ).scalar_one()
    issues = session.execute(
        select(func.count(LinkIssue.id))
        .where(LinkIssue.resolved_at.is_(None))
    ).scalar_one()
    rate = float(issues) / float(active) if active else 0.0
    return {
        "unresolved_issues": int(issues),
        "active_spus": int(active),
        "rate": rate,
    }


def cost_coverage_rate(session: Session) -> dict:
    """Active SPUs that have at least one effective cost snapshot."""
    active = session.execute(
        select(func.count(ChannelProduct.id))
        .where(ChannelProduct.status == "ACTIVE")
    ).scalar_one()
    costed = session.execute(
        select(func.count(func.distinct(ProductCostSnapshot.spu_pk)))
        .where(ProductCostSnapshot.valid_to.is_(None))
    ).scalar_one()
    rate = float(costed) / float(active) if active else 0.0
    return {
        "active_spus": int(active),
        "costed_spus": int(costed),
        "rate": rate,
    }
