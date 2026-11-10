"""reporting.cost_snapshots — resolve unit cost and write snapshots.

Priority chain (highest first):
    1. procurement.manual_product_costs (MANUAL_ENTRY) — operator-entered truth
    2. procurement_products.source_unit_cost (SOURCE_PRICE) — 货源价估算

The source price is the synchronized public-collect-box price maintained by
``miaoshou.common_collect_box``. Reports must label it as estimated cost. When
neither source exists, the resolver returns None and the job writes no snapshot.

This module owns the whole cost convention: the priority chain, the 货源价
lookup (:func:`source_cost_lookup`) and the batch form of the resolver
(:func:`resolve_unit_costs`). Consumers must call these instead of
re-implementing the chain — one rule, one place.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import exists, select, text
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


# 货源价（SOURCE_PRICE 兜底）lookup。两跳：
# 1) SPU 主档行本身（procurement_products.external_product_id == spu_id）
#    已有 source_unit_cost → 直接用（将来采集行价格回填到主档行后走这里）。
# 2) 否则按主档行 source_item_id（1688 offer）去公共采集箱行找最新价格。
# 只读，返回 NULL 行由调用方视为“无货源价”。
_SQL_SOURCE_COST_DIRECT = text(
    "SELECT pp.source_unit_cost "
    "FROM commerce.products_spu cp "
    "JOIN procurement.procurement_products pp "
    "  ON pp.external_product_id = cp.spu_id "
    "WHERE cp.id = :cp_id AND pp.source_unit_cost IS NOT NULL "
    "ORDER BY pp.synced_at DESC NULLS LAST, pp.id DESC "
    "LIMIT 1"
)

_SQL_SOURCE_COST_VIA_OFFER = text(
    "WITH offer AS ( "
    "  SELECT pp.source_item_id "
    "  FROM commerce.products_spu cp "
    "  JOIN procurement.procurement_products pp "
    "    ON pp.external_product_id = cp.spu_id "
    "  WHERE cp.id = :cp_id AND pp.source_item_id IS NOT NULL "
    "  LIMIT 1 "
    ") "
    "SELECT pp.source_unit_cost "
    "FROM procurement.procurement_products pp "
    "JOIN offer o ON pp.source_item_id = o.source_item_id "
    "WHERE pp.source_unit_cost IS NOT NULL "
    "ORDER BY pp.synced_at DESC NULLS LAST, pp.id DESC "
    "LIMIT 1"
)


def source_cost_lookup(session: Session):
    """Return a lookup fn: spu_pk → (source_unit_cost, 'CNY') | (None, None).

    货源价口径 = 妙手公共采集箱 price（1688 挂牌标价），由
    miaoshou.common_collect_box job 维护；作 SOURCE_PRICE 估算兜底，
    非采购单成交口径。

    它属于成本口径的 owner，因此住在这里而不是调用方——快照作业与看板都从
    这里取同一个货源价口径，不各写一份 SQL。
    """

    def lookup(cp_id: int) -> tuple[Decimal | None, str | None]:
        row = session.execute(_SQL_SOURCE_COST_DIRECT, {"cp_id": cp_id}).first()
        if row is not None and row[0] is not None:
            return row[0], "CNY"
        row = session.execute(_SQL_SOURCE_COST_VIA_OFFER, {"cp_id": cp_id}).first()
        if row is None or row[0] is None:
            return None, None
        return row[0], "CNY"

    return lookup


def resolve_unit_costs(
    session: Session,
    *,
    spu_pks: list[int],
    source_cost_lookup=None,  # callable: spu_pk -> (Decimal|None, str|None)
) -> dict[int, ResolvedCost]:
    """Resolve many SPUs with the owner's priority chain.

    Batch form of :func:`resolve_unit_cost`: the current manual costs (the
    common tier) come from ONE query, and the 货源价 fallback is consulted
    only for the SPUs that have no current manual row.

    The chain is the same one :func:`resolve_unit_cost` applies — that
    function delegates here, so there is a single rule:
    ``MANUAL_ENTRY`` → ``SOURCE_PRICE`` → 无源（不解析，调用方按“无成本”处理）。
    """
    if not spu_pks:
        return {}
    rows = session.execute(
        select(ManualProductCost).where(
            ManualProductCost.spu_pk.in_(spu_pks),
            ManualProductCost.valid_to.is_(None),
        )
    ).scalars().all()
    manual = {row.spu_pk: row for row in rows}
    out: dict[int, ResolvedCost] = {}
    for pk in spu_pks:
        current = manual.get(pk)
        if current is not None:
            out[pk] = ResolvedCost(
                method="MANUAL_ENTRY",
                unit_cost=current.unit_cost,
                currency=current.currency,
            )
            continue
        src_cost, src_currency = (
            source_cost_lookup(pk) if source_cost_lookup is not None else (None, None)
        )
        if src_cost is None:
            continue
        out[pk] = ResolvedCost(
            method="SOURCE_PRICE",
            unit_cost=src_cost,
            currency=src_currency or "CNY",
        )
    return out


def resolve_unit_cost(
    session: Session,
    *,
    spu_pk: int,
    source_unit_cost: Decimal | None = None,
    source_currency: str | None = None,
) -> ResolvedCost | None:
    """Resolve one SPU using manual cost first, then source-price estimate."""
    resolved = resolve_unit_costs(
        session,
        spu_pks=[spu_pk],
        source_cost_lookup=lambda _pk: (source_unit_cost, source_currency),
    )
    return resolved.get(spu_pk)


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
    spu_pks: list[int] | None = None,
    source_cost_lookup=None,  # callable: spu_pk -> (Decimal|None, str|None)
) -> int:
    """Walk every active SPU, resolve unit cost, and write a snapshot.
    Returns the count of snapshots written (no-source SPUs are skipped).

    Pass ``source_cost_lookup=fn`` to plug in the 货源价 resolver.
    It defaults to ``None`` when not provided."""
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
