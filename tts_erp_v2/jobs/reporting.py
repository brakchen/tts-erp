"""reporting.* sync-worker jobs — cost snapshots + daily profit recompute.

These two recompute pipelines existed as library functions since the v2
cutover (``tts_erp_v2.reporting.cost_snapshots.rebuild_snapshots`` /
``profit_daily.rebuild``) but were never wired into the scheduler — the
``reporting.*`` tables stayed empty in production. This module is the
thin job wrapper that registers them into ``sync_worker.scheduler.JOBS``.

Cadence (see scheduler.JOBS):
* ``reporting.cost_snapshots`` — every 6 h (manual entries + 货源价
  SOURCE_PRICE 兜底).
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

from tts_erp_v2.db.models import ChannelProduct, ProductCostSnapshot
from tts_erp_v2.jobs.runner import run_job
from tts_erp_v2.reporting import cost_snapshots, profit_daily

log = logging.getLogger("tts_erp_v2.jobs.reporting")

JOB_COST_SNAPSHOTS = "reporting.cost_snapshots"
JOB_PROFIT_DAILY = "reporting.profit_daily"

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


BATCH_SIZE = 100


def _spu_ids(session: Session) -> list[int]:
    with session.begin():
        return list(session.scalars(select(ChannelProduct.id).order_by(ChannelProduct.id)))


def run_cost_snapshots(session: Session) -> dict[str, Any]:
    with run_job(session, job_name=JOB_COST_SNAPSHOTS) as job:
        with session.begin():
            version = (session.scalar(select(func.max(ProductCostSnapshot.calculation_version))) or 0) + 1
        valid_from = datetime.now(timezone.utc)
        ids = _spu_ids(session)
        written = 0
        for offset in range(0, len(ids), BATCH_SIZE):
            with session.begin():
                written += cost_snapshots.rebuild_snapshots(
                    session, calculation_version=version, valid_from=valid_from,
                    spu_pks=ids[offset:offset + BATCH_SIZE],
                    source_cost_lookup=_source_cost_lookup(session),
                )
        job.rows_total = job.rows_inserted = written
        job.extra = {"calculation_version": version, "valid_from": valid_from.isoformat()}
        return {"snapshots_written": written, "calculation_version": version}


def run_profit_daily(session: Session) -> dict[str, Any]:
    today = datetime.now(timezone.utc).date()
    dates = [today - timedelta(days=1), today]
    total_rows = 0
    with run_job(session, job_name=JOB_PROFIT_DAILY) as job:
        ids = _spu_ids(session)
        with session.begin():
            version = profit_daily._next_calculation_version(session)
        for day in dates:
            for offset in range(0, len(ids), BATCH_SIZE):
                with session.begin():
                    total_rows += len(profit_daily.rebuild(
                        session, profit_date=day, spu_pks=ids[offset:offset + BATCH_SIZE],
                        calculation_version=version,
                    ))
        job.rows_total = job.rows_inserted = total_rows
        job.extra = {"dates": [d.isoformat() for d in dates], "rows": total_rows}
        return {"dates": [d.isoformat() for d in dates], "rows_written": total_rows}


__all__ = [
    "JOB_COST_SNAPSHOTS",
    "JOB_PROFIT_DAILY",
    "run_cost_snapshots",
    "run_profit_daily",
]
