"""Daily production materialization job for deterioration alerts."""

from __future__ import annotations

import logging
from contextlib import suppress
from datetime import date
from typing import Any

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from tts_erp_v2.analytics.spu_deterioration_alert.materialize import replace_anchor
from tts_erp_v2.analytics.spu_deterioration_alert.read import build_materialized_rows
from tts_erp_v2.jobs.runner import finish_job, start_job

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


_EXPECTED_ERRORS = (
    SQLAlchemyError,
    RuntimeError,
    ValueError,
    TypeError,
    LookupError,
    ArithmeticError,
)


def _record_failure(session: Session, *, phase: str, error_type: str) -> dict[str, Any]:
    safe_error = f"{phase}:{error_type}"
    result = {
        "status": "failed",
        "failureCount": 1,
        "phase": phase,
        "errorType": error_type,
        "error": safe_error,
    }
    try:
        session.rollback()
        job = start_job(session, job_name=JOB_NAME)
        job.extra = result
        finish_job(session, job, status="failed", rows_failed=1, extra=result)
        session.commit()
    except _EXPECTED_ERRORS as exc:
        with suppress(*_EXPECTED_ERRORS):
            session.rollback()
        raise RuntimeError(f"{phase}:audit:{type(exc).__name__}") from None
    return result


def run_scheduled(session: Session) -> dict[str, Any]:
    """Read facts in a repeatable-read snapshot, then atomically write anchors."""
    try:
        anchors = build_materialized_rows(session)
    except _EXPECTED_ERRORS as exc:
        error_type = type(exc).__name__
        log.error(
            "deterioration alert read failed phase=read error_type=%s", error_type
        )
        return _record_failure(session, phase="read", error_type=error_type)
    try:
        session.rollback()
    except _EXPECTED_ERRORS as exc:
        error_type = type(exc).__name__
        log.error(
            "deterioration alert read rollback failed phase=rollback error_type=%s",
            error_type,
        )
        return _record_failure(session, phase="rollback", error_type=error_type)
    if not anchors:
        result = {"status": "disabled", "anchorCount": 0, "rows": 0}
        try:
            job = start_job(session, job_name=JOB_NAME)
            job.extra = result
            finish_job(session, job, status="skipped", extra=result)
            session.commit()
        except _EXPECTED_ERRORS as exc:
            error_type = type(exc).__name__
            log.error(
                "deterioration alert finalize failed phase=finalize error_type=%s",
                error_type,
            )
            return _record_failure(session, phase="finalize", error_type=error_type)
        return result
    try:
        row_count = 0
        for anchor_date, rows in anchors.items():
            row_count += replace_anchor(session, rows=rows, anchor_date=anchor_date)
    except _EXPECTED_ERRORS as exc:
        error_type = type(exc).__name__
        log.error(
            "deterioration alert write failed phase=write error_type=%s", error_type
        )
        return _record_failure(session, phase="write", error_type=error_type)

    try:
        job = start_job(session, job_name=JOB_NAME)
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
    except _EXPECTED_ERRORS as exc:
        error_type = type(exc).__name__
        log.error(
            "deterioration alert finalize failed phase=finalize error_type=%s",
            error_type,
        )
        return _record_failure(session, phase="finalize", error_type=error_type)
