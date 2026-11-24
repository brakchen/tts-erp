"""reporting.cost_snapshots — resolve unit cost and write snapshots.

Priority chain (highest first):
    1. procurement.manual_product_costs (MANUAL_ENTRY) — operator-entered truth
    2. miaoshou.purchase_prices (PURCHASE_PRICE) — verified purchase transaction

Public collect-box list prices remain in procurement_products for comparison;
they are not purchase costs. When neither confirmed cost exists, no snapshot
is written.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import exists, select
from sqlalchemy.orm import Session

from tts_erp_v2.db.constants import ACTIVE_PRODUCT_STATUS
from tts_erp_v2.db.models import (
    ChannelProduct,
    ManualProductCost,
    ProductCostSnapshot,
)
from tts_erp_v2.reporting.purchase_cost import load_purchase_costs


@dataclass(frozen=True)
class ResolvedCost:
    method: str
    unit_cost: Decimal
    currency: str


def _current_manual_cost(session: Session, spu_pk: int) -> ManualProductCost | None:
    """Return the effective (valid_to IS NULL) manual cost row for the
    SPU, if any. We use valid_to IS NULL as the signal that this is
    the latest entry — historical rows are kept for forensics."""
    return session.execute(
        select(ManualProductCost).where(
            ManualProductCost.spu_pk == spu_pk,
            ManualProductCost.valid_to.is_(None),
        )
    ).scalar_one_or_none()


def resolve_unit_cost(
    session: Session,
    *,
    spu_pk: int,
    purchase_cost_cny: Decimal | None = None,
) -> ResolvedCost | None:
    """Resolve one SPU using manual cost, then verified purchase price."""
    # 1. MANUAL_ENTRY (highest priority)
    manual = _current_manual_cost(session, spu_pk)
    if manual is not None:
        return ResolvedCost(
            method="MANUAL_ENTRY",
            unit_cost=manual.unit_cost,
            currency=manual.currency,
        )

    if purchase_cost_cny is not None:
        return ResolvedCost(
            method="PURCHASE_PRICE",
            unit_cost=purchase_cost_cny,
            currency="CNY",
        )

    # A public collect-box list price cannot stand in for a transaction.
    return None


def active_spus_without_cost(session: Session) -> list[tuple[str, int]]:
    """Return active products with neither manual nor verified purchase cost."""
    cp_with_manual = exists().where(ManualProductCost.spu_pk == ChannelProduct.id)
    rows = session.execute(
        select(ChannelProduct.spu_id, ChannelProduct.id)
        .where(ChannelProduct.status == ACTIVE_PRODUCT_STATUS)
        .where(~cp_with_manual)
        .order_by(ChannelProduct.spu_id)
    ).all()
    purchase_costs = load_purchase_costs(session, [r[1] for r in rows])
    return [(r[0], r[1]) for r in rows if r[1] not in purchase_costs]


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
    purchase_costs = load_purchase_costs(session, [cp.id for cp in spus])
    for cp in spus:
        resolved = resolve_unit_cost(
            session,
            spu_pk=cp.id,
            purchase_cost_cny=purchase_costs.get(cp.id),
        )
        if resolved is None:
            continue
        snap = ProductCostSnapshot(
            spu_pk=cp.id,
            cost_method=resolved.method,
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
