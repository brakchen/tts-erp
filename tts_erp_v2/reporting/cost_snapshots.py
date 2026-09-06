"""reporting.cost_snapshots — resolve unit cost and write snapshots.

Priority chain (highest first):
    1. procurement.manual_product_costs   (MANUAL_ENTRY)        — operator-entered truth
    2. purchase_orders (LATEST_PURCHASE_COST)
    3. procurement_products.source_unit_cost (SOURCE_PRICE)   — 货源价兜底估算

2026-09-06 决策（用户拍板“货源价就是我们的采购价格”）：货源价
（公共采集箱 ``price``，由 miaoshou.common_collect_box job 维护）作为
**兜底估算**成本口径落账，用 ``SOURCE_PRICE`` method 与成交口径区分，
便于将来有采购单时对账修正。它仍是 1688 挂牌标价而非成交价（§11.3
“标价≠成本”），报表只能叫“估算成本”。当 manual / purchase / source
都不存在时，resolver 返回 None，job 不写行，SPU 进入
``active_spus_without_cost()`` 等人工填写。
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

# Methods recognised by ``resolve_unit_cost``. Anything outside this set
# is silently ignored.
_VALID_METHODS = {
    "MANUAL_ENTRY",
    "LATEST_PURCHASE_COST",
    "PERIOD_AVERAGE_COST",
    "WEIGHTED_AVERAGE_COST",
    "SOURCE_PRICE",
}


@dataclass(frozen=True)
class ResolvedCost:
    method: str
    unit_cost: Decimal
    currency: str


def _current_manual_cost(
    session: Session, spu_pk: int
) -> ManualProductCost | None:
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
    purchase_order_unit_cost: Decimal | None = None,
    purchase_order_currency: str | None = None,
    source_unit_cost: Decimal | None = None,
    source_currency: str | None = None,
) -> ResolvedCost | None:
    """Resolve a single SPU's unit cost. Returns ResolvedCost or None.

    Manual cost always wins. If no manual row exists and a
    purchase-order-derived cost is supplied, LATEST_PURCHASE_COST is
    returned. If neither exists but a 货源价（公共采集箱挂牌价）is
    supplied, SOURCE_PRICE is returned as an **估算兜底** (method-tagged
    so reports can label it 估算成本 and reconcile against purchase
    orders later).
    """
    # 1. MANUAL_ENTRY (highest priority)
    manual = _current_manual_cost(session, spu_pk)
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

    # 3. SOURCE_PRICE — 货源价兜底（method 区分，报表须标注估算成本）
    if source_unit_cost is not None:
        return ResolvedCost(
            method="SOURCE_PRICE",
            unit_cost=source_unit_cost,
            currency=source_currency or "CNY",
        )

    # 4. No source ⇒ no snapshot.
    return None


def active_spus_without_cost(session: Session) -> list[tuple[str, int]]:
    """Return [(spu_id, spu_pk)] for active
    products_spu that have no manual cost and no purchase-order
    cost available. Powers the "in-stock without cost" monitoring list
    that the operator uses to drive the manual-costs form."""
    cp_with_manual = exists().where(
        ManualProductCost.spu_pk == ChannelProduct.id
    )
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
    purchase_order_lookup=None,  # callable: spu_pk -> (Decimal|None, str|None)
    source_cost_lookup=None,  # callable: spu_pk -> (Decimal|None, str|None)
) -> int:
    """Walk every active SPU, resolve unit cost, and write a snapshot.
    Returns the count of snapshots written (no-source SPUs are skipped).

    Pass ``purchase_order_lookup=fn`` to plug in the purchase-order
    aggregator; pass ``source_cost_lookup=fn`` to plug in the 货源价
    (procurement_products.source_unit_cost) resolver. Either defaults
    to ``None`` (branch skipped) when not provided."""
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
        src_cost, src_currency = (None, None)
        if source_cost_lookup is not None:
            src_cost, src_currency = source_cost_lookup(cp.id)
        resolved = resolve_unit_cost(
            session,
            spu_pk=cp.id,
            purchase_order_unit_cost=po_cost,
            purchase_order_currency=po_currency,
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
            source_purchase_quantity=None,
            source_purchase_amount=None,
            source_line_count=None,
            calculation_version=calculation_version,
            calculated_at=datetime.now(UTC),
        )
        session.add(snap)
        rows_written += 1
    session.flush()
    return rows_written
