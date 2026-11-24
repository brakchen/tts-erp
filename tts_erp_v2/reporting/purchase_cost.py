"""Resolve verified Miaoshou purchase prices for TikTok SPUs."""

from __future__ import annotations

from decimal import Decimal

from sqlalchemy import text
from sqlalchemy.orm import Session

_SQL_LATEST_PURCHASE_COSTS = text(
    """
    WITH ranked AS (
        SELECT cp.id AS spu_pk,
               pp.unit_cost,
               dense_rank() OVER (
                   PARTITION BY cp.id ORDER BY pp.source_purchase_at DESC
               ) AS purchase_rank
        FROM commerce.products_spu cp
        JOIN miaoshou.purchase_prices pp
          ON pp.shop_pk = cp.shop_pk AND pp.spu_id = cp.spu_id
        WHERE cp.id = ANY(CAST(:spu_pks AS bigint[]))
          AND pp.resolution_status = 'matched_shop'
          AND pp.currency = 'CNY'
          AND pp.unit_cost > 0
          AND pp.source_purchase_at IS NOT NULL
    )
    SELECT spu_pk, min(unit_cost) AS unit_cost
    FROM ranked
    WHERE purchase_rank = 1
    GROUP BY spu_pk
    HAVING count(DISTINCT unit_cost) = 1
    """
)


def load_purchase_costs(session: Session, spu_pks: list[int]) -> dict[int, Decimal]:
    """Return the latest confirmed CNY cost; ambiguous same-time prices are omitted."""
    if not spu_pks:
        return {}
    rows = session.execute(
        _SQL_LATEST_PURCHASE_COSTS, {"spu_pks": spu_pks}
    ).mappings()
    return {int(row["spu_pk"]): Decimal(row["unit_cost"]) for row in rows}


__all__ = ["load_purchase_costs"]
