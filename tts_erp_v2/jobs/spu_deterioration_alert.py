"""Daily production materialization job for deterioration alerts."""

from __future__ import annotations

import logging
from datetime import date
from typing import Any

from sqlalchemy.orm import Session

from tts_erp_v2.analytics.spu_deterioration_alert.materialize import replace_anchor
from tts_erp_v2.analytics.spu_deterioration_alert.read import build_materialized_rows
from tts_erp_v2.jobs.runner import finish_job, run_job

JOB_NAME = "analytics.spu_profit_deterioration_alert"
log = logging.getLogger("tts_erp_v2.jobs.spu_deterioration_alert")


def materialize_alerts(
    session: Session,
    *,
    anchor_date: date,
    rows: list[Any],
) -> dict[str, Any]:
    """Atomically replace one anchor; retained as the narrow write seam."""
    count = replace_anchor(session, rows=rows, anchor_date=anchor_date)
    return {"status": "success", "anchorDate": anchor_date.isoformat(), "rows": count}


def _record_failure(session: Session, message: str) -> dict[str, Any]:
    session.rollback()
    with run_job(session, job_name=JOB_NAME) as job:
        result = {"status": "failed", "failureCount": 1, "error": message[:500]}
        job.extra = result
        finish_job(session, job, status="failed", rows_failed=1, extra=result)
        session.commit()
    return result


def run_scheduled(session: Session) -> dict[str, Any]:
    """Read facts in a repeatable-read snapshot, then atomically write anchors."""
    try:
        anchors = build_materialized_rows(session)
    except Exception as exc:  # noqa: BLE001 — job boundary records and fails closed
        log.exception("deterioration alert read failed")
        return _record_failure(session, f"{type(exc).__name__}: {exc}")
    session.rollback()
    if not anchors:
        with run_job(session, job_name=JOB_NAME) as job:
            result = {"status": "disabled", "anchorCount": 0, "rows": 0}
            job.extra = result
            finish_job(session, job, status="skipped", extra=result)
            session.commit()
        return result
    try:
        with run_job(session, job_name=JOB_NAME) as job:
            row_count = 0
            for anchor_date, rows in anchors.items():
                row_count += replace_anchor(session, rows=rows, anchor_date=anchor_date)
            result = {
                "status": "success",
                "anchorCount": len(anchors),
                "rows": row_count,
                "failureCount": 0,
            }
            job.extra = result
            finish_job(
                session,
                job,
                status="succeeded",
                rows_total=row_count,
                rows_inserted=row_count,
                extra=result,
            )
            session.commit()
            return result
    except Exception as exc:  # noqa: BLE001 — rollback preserves previous snapshots
        log.exception("deterioration alert write failed")
        return _record_failure(session, f"{type(exc).__name__}: {exc}")
