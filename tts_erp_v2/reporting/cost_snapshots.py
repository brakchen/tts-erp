"""reporting.cost_snapshots — resolve unit cost and write snapshots.

Priority chain (highest first):
    1. procurement.manual_product_costs (MANUAL_ENTRY) — operator-entered truth
    2. procurement_products.source_unit_cost (SOURCE_PRICE) — 货源价估算

The source price is the synchronized public-collect-box price maintained by
``miaoshou.common_collect_box``. Reports must label it as estimated cost. When
neither source exists, the resolver returns None and the job writes no snapshot.
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
    source_unit_cost: Decimal | None = None,
    source_currency: str | None = None,
) -> ResolvedCost | None:
    """Resolve one SPU using manual cost first, then source-price estimate."""
    # 1. MANUAL_ENTRY (highest priority)
    manual = _current_manual_cost(session, spu_pk)
    if manual is not None:
        return ResolvedCost(
            method="MANUAL_ENTRY",
            unit_cost=manual.unit_cost,
            currency=manual.currency,
        )

    # 2. SOURCE_PRICE — 货源价兜底（method 区分，报表须标注估算成本）
    if source_unit_cost is not None:
        return ResolvedCost(
            method="SOURCE_PRICE",
            unit_cost=source_unit_cost,
            currency=source_currency or "CNY",
        )

    # No source ⇒ no snapshot.
    return None


def active_spus_without_cost(session: Session) -> list[tuple[str, int]]:
    """Return active products with no manual cost for operator follow-up."""
    cp_with_manual = exists().where(ManualProductCost.spu_pk == ChannelProduct.id)
    rows = session.execute(
        select(ChannelProduct.spu_id, ChannelProduct.id)
        .where(ChannelProduct.status == ACTIVE_PRODUCT_STATUS)
        .where(~cp_with_manual)
        .order_by(ChannelProduct.spu_id)
    ).all()
    return [(r[0], r[1]) for r in rows]


def rebuild_snapshots(
    session: Session,
    *,
    calculation_version: int,
    valid_from: datetime,
    source_cost_lookup=None,  # callable: spu_pk -> (Decimal|None, str|None)
) -> int:
    """Walk every active SPU, resolve unit cost, and write a snapshot.
    Returns the count of snapshots written (no-source SPUs are skipped).

    Pass ``source_cost_lookup=fn`` to plug in the 货源价 resolver.
    It defaults to ``None`` when not provided."""
    rows_written = 0
    spus = (
        session.execute(
            select(ChannelProduct).where(ChannelProduct.status == ACTIVE_PRODUCT_STATUS)
        )
        .scalars()
        .all()
    )
    for cp in spus:
        src_cost, src_currency = (None, None)
        if source_cost_lookup is not None:
            src_cost, src_currency = source_cost_lookup(cp.id)
        resolved = resolve_unit_cost(
            session,
            spu_pk=cp.id,
            source_unit_cost=src_cost,
            source_currency=src_currency,
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
