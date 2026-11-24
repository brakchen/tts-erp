"""reporting.* sync-worker jobs — cost snapshots + daily profit recompute.

These two recompute pipelines existed as library functions since the v2
cutover (``tts_erp_v2.reporting.cost_snapshots.rebuild_snapshots`` /
``profit_daily.rebuild``) but were never wired into the scheduler — the
``reporting.*`` tables stayed empty in production. This module is the
thin job wrapper that registers them into ``sync_worker.scheduler.JOBS``.

Cadence (see scheduler.JOBS):
* ``reporting.cost_snapshots`` — every 6 h (manual + verified purchase cost).
* ``reporting.profit_daily`` — every 1 h, rebuilding today + yesterday
  (UTC) so late order updates / after-sales changes land.

Bookkeeping uses :func:`tts_erp_v2.jobs.runner.run_job` for independent
audit transactions. Each job owns bounded business transactions through
``session.begin()``; the scheduler manages only Session lifetime.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from tts_erp_v2.db.models import ChannelProduct, ProductCostSnapshot
from tts_erp_v2.jobs.runner import run_job
from tts_erp_v2.reporting import cost_snapshots, profit_daily

log = logging.getLogger("tts_erp_v2.jobs.reporting")

JOB_COST_SNAPSHOTS = "reporting.cost_snapshots"
JOB_PROFIT_DAILY = "reporting.profit_daily"

BATCH_SIZE = 100


def _spu_ids(session: Session) -> list[int]:
    with session.begin():
        return list(
            session.scalars(select(ChannelProduct.id).order_by(ChannelProduct.id))
        )


def run_cost_snapshots(session: Session) -> dict[str, Any]:
    with run_job(session, job_name=JOB_COST_SNAPSHOTS) as job:
        with session.begin():
            version = (
                session.scalar(
                    select(func.max(ProductCostSnapshot.calculation_version))
                )
                or 0
            ) + 1
        valid_from = datetime.now(UTC)
        ids = _spu_ids(session)
        written = 0
        for offset in range(0, len(ids), BATCH_SIZE):
            with session.begin():
                written += cost_snapshots.rebuild_snapshots(
                    session,
                    calculation_version=version,
                    valid_from=valid_from,
                    spu_pks=ids[offset : offset + BATCH_SIZE],
                )
        job.rows_total = job.rows_inserted = written
        job.extra = {
            "calculation_version": version,
            "valid_from": valid_from.isoformat(),
        }
        return {"snapshots_written": written, "calculation_version": version}


def run_profit_daily(session: Session) -> dict[str, Any]:
    today = datetime.now(UTC).date()
    dates = [today - timedelta(days=1), today]
    total_rows = 0
    with run_job(session, job_name=JOB_PROFIT_DAILY) as job:
        ids = _spu_ids(session)
        for day in dates:
            with session.begin():
                version = profit_daily._next_calculation_version(session)
            for offset in range(0, len(ids), BATCH_SIZE):
                with session.begin():
                    total_rows += len(
                        profit_daily.rebuild(
                            session,
                            profit_date=day,
                            spu_pks=ids[offset : offset + BATCH_SIZE],
                            calculation_version=version,
                        )
                    )
        job.rows_total = job.rows_inserted = total_rows
        job.extra = {"dates": [d.isoformat() for d in dates], "rows": total_rows}
        return {"dates": [d.isoformat() for d in dates], "rows_written": total_rows}


__all__ = [
    "JOB_COST_SNAPSHOTS",
    "JOB_PROFIT_DAILY",
    "run_cost_snapshots",
    "run_profit_daily",
]
