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
    ProductCostSnapshot,
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
        select(func.count(SalesOrderLine.id)).where(SalesOrderLine.spu_pk.is_not(None))
    ).scalar_one()
    rate = float(resolved) / float(total) if total else 0.0
    return {
        "total_lines": int(total),
        "resolved_lines": int(resolved),
        "rate": rate,
    }


def cost_coverage_rate(session: Session) -> dict:
    """Active SPUs that have at least one effective cost snapshot."""
    active = session.execute(
        select(func.count(ChannelProduct.id)).where(ChannelProduct.status == "ACTIVE")
    ).scalar_one()
    costed = session.execute(
        select(func.count(func.distinct(ProductCostSnapshot.spu_pk))).where(
            ProductCostSnapshot.valid_to.is_(None)
        )
    ).scalar_one()
    rate = float(costed) / float(active) if active else 0.0
    return {
        "active_spus": int(active),
        "costed_spus": int(costed),
        "rate": rate,
    }
