"""Resolve the unified purchase price and retain derived cost snapshots.

Listing reference prices never create a purchase price or snapshot.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from tts_erp_v2.db.constants import ACTIVE_PRODUCT_STATUS
from tts_erp_v2.db.models import (
    ChannelProduct,
    ProductCostSnapshot,
)
from tts_erp_v2.reporting.purchase_cost import load_effective_purchase_prices


@dataclass(frozen=True)
class ResolvedCost:
    method: str
    unit_cost: Decimal
    currency: str


def resolve_unit_cost(
    session: Session,
    *,
    spu_pk: int,
    purchase_cost_cny: Decimal | None = None,
) -> ResolvedCost | None:
    """Resolve the effective purchase price through the shared owner."""
    price = load_effective_purchase_prices(session, [spu_pk]).get(spu_pk)
    if price is not None:
        return ResolvedCost("PURCHASE_PRICE", price.unit_cost, price.currency)
    if purchase_cost_cny is not None:
        return ResolvedCost("PURCHASE_PRICE", purchase_cost_cny, "CNY")
    return None


def active_spus_without_cost(session: Session) -> list[tuple[str, int]]:
    """Return active products without an effective purchase price."""
    rows = session.execute(
        select(ChannelProduct.spu_id, ChannelProduct.id)
        .where(ChannelProduct.status == ACTIVE_PRODUCT_STATUS)
        .order_by(ChannelProduct.spu_id)
    ).all()
    prices = load_effective_purchase_prices(session, [row[1] for row in rows])
    return [(row[0], row[1]) for row in rows if row[1] not in prices]


def rebuild_snapshots(
    session: Session,
    *,
    calculation_version: int,
    valid_from: datetime,
    spu_pks: list[int] | None = None,
) -> int:
    """Walk every active SPU, resolve unit cost, and write a snapshot.
    Returns the count of snapshots written (no-source SPUs are skipped).

    Public collect-box list prices do not create cost snapshots."""
    rows_written = 0
    spus = (
        session.execute(
            select(ChannelProduct).where(
                ChannelProduct.status == ACTIVE_PRODUCT_STATUS,
                *([ChannelProduct.id.in_(spu_pks)] if spu_pks is not None else []),
            )
        )
        .scalars()
        .all()
    )
    prices = load_effective_purchase_prices(session, [cp.id for cp in spus])
    for cp in spus:
        resolved = prices.get(cp.id)
        if resolved is None:
            continue
        snap = ProductCostSnapshot(
            spu_pk=cp.id,
            cost_method="PURCHASE_PRICE",
            unit_cost=resolved.unit_cost,
            currency=resolved.currency,
            valid_from=valid_from,
            valid_to=None,
            calculation_version=calculation_version,
            calculated_at=datetime.now(UTC),
        )
        session.add(snap)
        rows_written += 1
    session.flush()
    return rows_written
