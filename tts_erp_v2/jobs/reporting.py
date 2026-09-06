"""reporting.* sync-worker jobs — cost snapshots + daily profit recompute.

These two recompute pipelines existed as library functions since the v2
cutover (``tts_erp_v2.reporting.cost_snapshots.rebuild_snapshots`` /
``profit_daily.rebuild``) but were never wired into the scheduler — the
``reporting.*`` tables stayed empty in production. This module is the
thin job wrapper that registers them into ``sync_worker.scheduler.JOBS``.

Cadence (see scheduler.JOBS):
* ``reporting.cost_snapshots`` — every 6 h (cost inputs change slowly:
  manual entries + miaoshou purchase orders + 货源价 SOURCE_PRICE 兜底).
* ``reporting.profit_daily`` — every 1 h, rebuilding today + yesterday
  (UTC) so late order updates / after-sales changes land.

Bookkeeping: both use :func:`tts_erp_v2.jobs.runner.run_job`, which does
NOT commit — the scheduler's system-job executor commits on success and
writes a sentinel failed row on exception (same contract as
``token.refresh``).
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from tts_erp_v2.db.models import ProductCostSnapshot
from tts_erp_v2.jobs.runner import run_job
from tts_erp_v2.reporting import cost_snapshots, profit_daily

log = logging.getLogger("tts_erp_v2.jobs.reporting")

JOB_COST_SNAPSHOTS = "reporting.cost_snapshots"
JOB_PROFIT_DAILY = "reporting.profit_daily"

# Latest purchase price for a channel product, resolved through the
# effective link (override-aware) to the miaoshou procurement product and
# its most recently synced purchase-order line. Returns NULL rows when the
# SPU has no link or no purchase history — the caller treats that as
# "no purchase-order cost source".
_SQL_LATEST_PURCHASE_COST = text(
    "SELECT pol.unit_cost, pol.currency "
    "FROM linkage.effective_product_links epl "
    "JOIN procurement.purchase_order_lines pol "
    "  ON pol.procurement_product_id = epl.procurement_product_id "
    "JOIN procurement.purchase_orders po ON po.id = pol.purchase_order_id "
    "WHERE epl.spu_pk = :cp_id "
    "  AND pol.unit_cost IS NOT NULL "
    "ORDER BY pol.updated_at DESC NULLS LAST, pol.id DESC "
    "LIMIT 1"
)


def _purchase_order_lookup(session: Session):
    """Return a lookup fn: spu_pk → (unit_cost, currency) | (None, None)."""

    def lookup(cp_id: int) -> tuple[Decimal | None, str | None]:
        row = session.execute(_SQL_LATEST_PURCHASE_COST, {"cp_id": cp_id}).first()
        if row is None:
            return None, None
        return row[0], row[1]

    return lookup


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


def _source_cost_lookup(session: Session):
    """Return a lookup fn: spu_pk → (source_unit_cost, 'CNY') | (None, None).

    货源价口径 = 妙手公共采集箱 price（1688 挂牌标价），由
    miaoshou.common_collect_box job 维护；作 SOURCE_PRICE 估算兜底，
    非采购单成交口径。"""

    def lookup(cp_id: int) -> tuple[Decimal | None, str | None]:
        row = session.execute(_SQL_SOURCE_COST_DIRECT, {"cp_id": cp_id}).first()
        if row is not None and row[0] is not None:
            return row[0], "CNY"
        row = session.execute(_SQL_SOURCE_COST_VIA_OFFER, {"cp_id": cp_id}).first()
        if row is None or row[0] is None:
            return None, None
        return row[0], "CNY"

    return lookup


def run_cost_snapshots(session: Session) -> dict[str, Any]:
    """Rebuild cost snapshots for every ACTIVE SPU. Returns counters."""
    with run_job(session, job_name=JOB_COST_SNAPSHOTS) as job:
        current_version = session.execute(
            select(func.max(ProductCostSnapshot.calculation_version))
        ).scalar()
        calculation_version = (current_version or 0) + 1
        valid_from = datetime.now(timezone.utc)
        written = cost_snapshots.rebuild_snapshots(
            session,
            calculation_version=calculation_version,
            valid_from=valid_from,
            purchase_order_lookup=_purchase_order_lookup(session),
            source_cost_lookup=_source_cost_lookup(session),
        )
        job.rows_total = written
        job.rows_inserted = written
        job.extra = {
            "calculation_version": calculation_version,
            "valid_from": valid_from.isoformat(),
        }
        return {
            "snapshots_written": written,
            "calculation_version": calculation_version,
        }


def run_profit_daily(session: Session) -> dict[str, Any]:
    """Rebuild profit rows for today + yesterday (UTC). Returns counters."""
    today = datetime.now(timezone.utc).date()
    dates = [today - timedelta(days=1), today]
    total_rows = 0
    with run_job(session, job_name=JOB_PROFIT_DAILY) as job:
        for d in dates:
            rows = profit_daily.rebuild(session, profit_date=d)
            total_rows += len(rows)
        job.rows_total = total_rows
        job.rows_inserted = total_rows
        job.extra = {"dates": [d.isoformat() for d in dates], "rows": total_rows}
        return {"dates": [d.isoformat() for d in dates], "rows_written": total_rows}


__all__ = [
    "JOB_COST_SNAPSHOTS",
    "JOB_PROFIT_DAILY",
    "run_cost_snapshots",
    "run_profit_daily",
]
