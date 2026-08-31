"""reporting.cost_snapshots — resolve unit cost and write snapshots.

Priority chain (highest first):
    1. procurement.manual_product_costs   (MANUAL_ENTRY)        — operator-entered truth
    2. purchase_orders (LATEST_PURCHASE_COST)
    3. purchase_orders (PERIOD_AVERAGE_COST)
    4. purchase_orders (WEIGHTED_AVERAGE_COST)

1688 collect-listing price is **NOT** a valid cost source (would imply
a vendor listing price is the procurement price — false). When no
source exists, the resolver returns None and the snapshot job simply
doesn't write a row. The SPU then appears in
``active_spus_without_cost()`` so the operator can fill the manual form.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from sqlalchemy import exists, select
from sqlalchemy.orm import Session

from tts_erp_v2.db.constants import ACTIVE_PRODUCT_STATUS
from tts_erp_v2.db.models import (
    ChannelProduct,
    ManualProductCost,
    ProductCostSnapshot,
)

# Methods recognised by ``resolve_unit_cost``. Anything outside this set
# is silently ignored (e.g. COLLECT_LISTING_COST).
_VALID_METHODS = {
    "MANUAL_ENTRY",
    "LATEST_PURCHASE_COST",
    "PERIOD_AVERAGE_COST",
    "WEIGHTED_AVERAGE_COST",
}


@dataclass(frozen=True)
class ResolvedCost:
    method: str
    unit_cost: Decimal
    currency: str


def _current_manual_cost(
    session: Session, channel_product_id: int
) -> ManualProductCost | None:
    """Return the effective (valid_to IS NULL) manual cost row for the
    SPU, if any. We use valid_to IS NULL as the signal that this is
    the latest entry — historical rows are kept for forensics."""
    return session.execute(
        select(ManualProductCost).where(
            ManualProductCost.channel_product_id == channel_product_id,
            ManualProductCost.valid_to.is_(None),
        )
    ).scalar_one_or_none()


def resolve_unit_cost(
    session: Session,
    *,
    channel_product_id: int,
    purchase_order_unit_cost: Decimal | None = None,
    purchase_order_currency: str | None = None,
    collect_listing_cost: Decimal | None = None,  # explicit dead-end param
) -> ResolvedCost | None:
    """Resolve a single SPU's unit cost. Returns ResolvedCost or None.

    Manual cost always wins. If no manual row exists and a
    purchase-order-derived cost is supplied, LATEST_PURCHASE_COST is
    returned. ``collect_listing_cost`` is accepted only so the API call
    site can document the explicit no-fallback rule; the value itself
    is NEVER used.
    """
    # 1. MANUAL_ENTRY (highest priority)
    manual = _current_manual_cost(session, channel_product_id)
    if manual is not None:
        return ResolvedCost(
            method="MANUAL_ENTRY",
            unit_cost=manual.unit_cost,
            currency=manual.currency,
        )

    # 2. LATEST_PURCHASE_COST (only if caller supplied a value)
    if purchase_order_unit_cost is not None:
        return ResolvedCost(
            method="LATEST_PURCHASE_COST",
            unit_cost=purchase_order_unit_cost,
            currency=purchase_order_currency or "USD",
        )

    # 3. No source ⇒ no snapshot. The ``collect_listing_cost`` kwarg
    #    is acknowledged but never used. The schema doc-string spells
    #    out the rule; we don't even reference the parameter below
    #    this line on purpose.
    _ = collect_listing_cost  # noqa: F841 — explicitly unused
    return None


def active_spus_without_cost(session: Session) -> list[tuple[str, int]]:
    """Return [(external_product_id, channel_product_id)] for active
    channel_products that have no manual cost and no purchase-order
    cost available. Powers the "in-stock without cost" monitoring list
    that the operator uses to drive the manual-costs form."""
    cp_with_manual = exists().where(
        ManualProductCost.channel_product_id == ChannelProduct.id
    )
    rows = session.execute(
        select(ChannelProduct.external_product_id, ChannelProduct.id)
        .where(ChannelProduct.status == ACTIVE_PRODUCT_STATUS)
        .where(~cp_with_manual)
        .order_by(ChannelProduct.external_product_id)
    ).all()
    return [(r[0], r[1]) for r in rows]


def rebuild_snapshots(
    session: Session,
    *,
    calculation_version: int,
    valid_from: datetime,
    purchase_order_lookup=None,  # callable: cp_id -> (Decimal|None, str|None)
) -> int:
    """Walk every active SPU, resolve unit cost, and write a snapshot.
    Returns the count of snapshots written (no-source SPUs are skipped).
    Pass ``purchase_order_lookup=fn`` to plug in the real purchase-order
    aggregator when it ships in Lane C / Lane F; the default skips the
    purchase-order branch."""
    rows_written = 0
    spus = (
        session.execute(
            select(ChannelProduct).where(ChannelProduct.status == ACTIVE_PRODUCT_STATUS)
        )
        .scalars()
        .all()
    )
    for cp in spus:
        po_cost, po_currency = (None, None)
        if purchase_order_lookup is not None:
            po_cost, po_currency = purchase_order_lookup(cp.id)
        resolved = resolve_unit_cost(
            session,
            channel_product_id=cp.id,
            purchase_order_unit_cost=po_cost,
            purchase_order_currency=po_currency,
        )
        if resolved is None:
            continue
        snap = ProductCostSnapshot(
            channel_product_id=cp.id,
            cost_method=resolved.method,
            unit_cost=resolved.unit_cost,
            currency=resolved.currency,
            valid_from=valid_from,
            valid_to=None,
            source_purchase_quantity=None,
            source_purchase_amount=None,
            source_line_count=None,
            calculation_version=calculation_version,
            calculated_at=datetime.utcnow(),
        )
        session.add(snap)
        rows_written += 1
    session.flush()
    return rows_written
