"""Resolve the single effective purchase price for each TikTok SPU.

Existing price entries remain authoritative; verified transaction history fills
products without an entry. Legacy table names identify retained history, not
separate business price fields.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import text
from sqlalchemy.orm import Session

PURCHASE_PRICE_JOIN = (
    "LEFT JOIN LATERAL ("
    "  SELECT min(unit_cost) AS unit_cost FROM ("
    "    SELECT pp.unit_cost, dense_rank() OVER ("
    "      ORDER BY pp.source_purchase_at DESC) AS price_rank "
    "    FROM miaoshou.purchase_prices pp "
    "    WHERE pp.shop_pk = cp.shop_pk AND pp.spu_id = cp.spu_id "
    "      AND pp.resolution_status = 'matched_shop' "
    "      AND pp.currency = 'CNY' AND pp.unit_cost > 0 "
    "      AND pp.source_purchase_at IS NOT NULL"
    "  ) ranked WHERE price_rank = 1 "
    "  HAVING count(DISTINCT unit_cost) = 1"
    ") purchase ON TRUE "
)
PURCHASE_PRICE_JOINS = (
    "LEFT JOIN procurement.manual_product_costs m "
    "  ON m.spu_pk = cp.id AND m.valid_to IS NULL "
    + PURCHASE_PRICE_JOIN
)
PURCHASE_PRICE_AMOUNT = "COALESCE(m.unit_cost, purchase.unit_cost)"
PURCHASE_PRICE_CURRENCY = (
    "CASE WHEN m.id IS NOT NULL THEN m.currency "
    "WHEN purchase.unit_cost IS NOT NULL THEN 'CNY' END"
)


@dataclass(frozen=True)
class PurchasePrice:
    unit_cost: Decimal
    currency: str
    record_id: int | None


_SQL_EFFECTIVE_PURCHASE_PRICES = text(
    "WITH requested AS (SELECT id, shop_pk, spu_id FROM commerce.products_spu "
    "WHERE id = ANY(CAST(:spu_pks AS bigint[]))), ranked AS ("
    "SELECT cp.id AS spu_pk, pp.unit_cost, dense_rank() OVER ("
    "PARTITION BY cp.id ORDER BY pp.source_purchase_at DESC) AS price_rank "
    "FROM requested cp JOIN miaoshou.purchase_prices pp "
    "ON pp.shop_pk = cp.shop_pk AND pp.spu_id = cp.spu_id "
    "WHERE pp.resolution_status = 'matched_shop' AND pp.currency = 'CNY' "
    "AND pp.unit_cost > 0 AND pp.source_purchase_at IS NOT NULL), "
    "purchase_prices AS (SELECT spu_pk, min(unit_cost) AS unit_cost FROM ranked "
    "WHERE price_rank = 1 GROUP BY spu_pk HAVING count(DISTINCT unit_cost) = 1) "
    "SELECT cp.id AS spu_pk, m.id AS record_id, " + PURCHASE_PRICE_AMOUNT + " AS unit_cost, "
    + PURCHASE_PRICE_CURRENCY + " AS currency FROM requested cp "
    "LEFT JOIN procurement.manual_product_costs m ON m.spu_pk = cp.id AND m.valid_to IS NULL "
    "LEFT JOIN purchase_prices purchase ON purchase.spu_pk = cp.id "
    "WHERE " + PURCHASE_PRICE_AMOUNT + " IS NOT NULL"
)

def load_effective_purchase_prices(
    session: Session, spu_pks: list[int],
) -> dict[int, PurchasePrice]:
    """One price owner for catalogue, editing and import previews."""
    if not spu_pks:
        return {}
    rows = session.execute(
        _SQL_EFFECTIVE_PURCHASE_PRICES, {"spu_pks": spu_pks},
    ).mappings()
    return {
        int(row["spu_pk"]): PurchasePrice(
            unit_cost=Decimal(row["unit_cost"]), currency=row["currency"],
            record_id=row["record_id"],
        )
        for row in rows
    }

__all__ = [
    "PURCHASE_PRICE_AMOUNT", "PURCHASE_PRICE_CURRENCY", "PURCHASE_PRICE_JOINS",
    "PurchasePrice", "load_effective_purchase_prices",
]
