"""Sync job: analytics.retention（日级）。

2026-09-02 analytics v2 化（tech-doc/analytics-v2-migration-plan.md D6）：
原 ``analytics_sync/retention.sql`` 写了但从未有 cron 执行（audit 表无界
增长到 55k 行才发现）。现注册为 sync-worker 日级 job，与 token.refresh
等 system-wide job 同模式。

语义与原 retention.sql 一致（dump architecture 0005 后）：
- ``analytics.ad_records`` 按 ``received_at`` 保留 90 天
- ``analytics.ad_audit_log`` 按 ``created_at`` 保留 30 天
- ``ad_raw`` 是 source-of-truth 不 purge（dump architecture：ad_raw 永久保留，
  派生表 ad_records / ad_daily_completeness 可重建；ad_cursors / ad_daily_pages
  已在 0005 删除）
- ``ad_shop_timezones`` 永久保留

事务边界：本 entrypoint 不 commit —— sync-worker 的 ``run_with_sync_job``
统一 commit（见 job_runner.py 注释：Callers should NOT commit again）。
"""

from __future__ import annotations

import logging
import os
from typing import Any

from sqlalchemy.orm import Session

from tts_erp_v2.analytics.repository import purge_expired
from tts_erp_v2.jobs.runner import run_job

log = logging.getLogger(__name__)

JOB_NAME = "analytics.retention"

_DEFAULT_RECORDS_DAYS = 90
_DEFAULT_AUDIT_DAYS = 30


def _env_int(name: str, default: int) -> int:
    """保留期可用 env 覆盖（运维调参不用改代码）；垃圾值回落默认。"""
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        return max(1, int(raw))
    except ValueError:
        log.warning("%s=%r 不是合法整数，用默认 %d", name, raw, default)
        return default


def run_analytics_retention(session: Session) -> dict[str, Any]:
    """Purge expired analytics rows. Returns per-table delete counters."""
    records_days = _env_int("ANALYTICS_RETENTION_RECORDS_DAYS", _DEFAULT_RECORDS_DAYS)
    audit_days = _env_int("ANALYTICS_RETENTION_AUDIT_DAYS", _DEFAULT_AUDIT_DAYS)
    with run_job(session, job_name=JOB_NAME) as job:
        counts = purge_expired(
            session, records_days=records_days, audit_days=audit_days
        )
        total = counts["records_deleted"] + counts["audit_deleted"]
        job.rows_total = total
        job.rows_inserted = 0
        job.rows_updated = 0
        job.extra = {
            "records_days": records_days,
            "audit_days": audit_days,
            **counts,
        }
        log.info(
            "analytics.retention: records -%d (>%dd), audit -%d (>%dd)",
            counts["records_deleted"],
            records_days,
            counts["audit_deleted"],
            audit_days,
        )
        return counts


__all__ = ["JOB_NAME", "run_analytics_retention"]
