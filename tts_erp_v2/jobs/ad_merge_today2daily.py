"""Sync job: merge_today_into_daily — ad_today → ad_daily 跨天合并。

每天零点后将前一天的 ad_today 数据并入 ad_daily，然后清空 ad_today 中
对应日期的行。这样当天的数据在 ad_today 中持续更新（Chrome 扩展刷新），
昨天及更早的数据归档到 ad_daily 不再变动，coverage 查询走 ad_daily。

Cadence: 每小时跑一次，但只在昨天的数据存在时才实际执行（空表时 early
return）。保护窗：scheduler 配置 jitter，实际执行窗口 UTC 00:00-01:00。

Design ref: tech-doc/analytics/daily-sync-with-coverage.md §5.6。

Bookkeeping: uses :func:`tts_erp_v2.jobs.runner.run_job`, which does
NOT commit — the scheduler's system-job executor commits on success.

⚠️  DISABLED — 2026-09-13 (user request, fix/disable-ad-merge-today2daily)
    Job 仍由 scheduler 每小时调度一次，但 :func:`run` 一开始就早返回，
    不进入 ``run_job`` 上下文，也不会 INSERT/DELETE plugin.ad_*。
    JobSpec 注册保留以保持 jobs registry count = 17（见
    tests/sync_worker/test_scheduler_jobs_coverage.py）。
    重新启用：删除 :data:`_DISABLED` 与 :func:`run` 内的早返回分支。
"""

from __future__ import annotations

import logging
from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy.orm import Session

from tts_erp_v2.plugin.ads.repository import list_merge_scope_pairs
from tts_erp_v2.jobs.runner import run_job

log = logging.getLogger("tts_erp_v2.jobs.ad_merge_today2daily")

JOB_NAME = "plugin.ad_merge_today2daily"

# 2026-09-13: 临时禁用。root cause 待定（怀疑 UTC 跨天时 Chrome 扩展仍
# 在写入"昨天"的 ad_today 行 → 过早 DELETE 会截断延迟归因数据）。
# Re-enable: set to False.
_DISABLED = True


def run(session: Session) -> dict[str, Any]:
    """Merge yesterday's ad_today rows into ad_daily for all scopes.

    Returns counters for the sync_jobs row.

    When :data:`_DISABLED` is True, this early-returns without touching
    ``plugin.ad_today`` / ``plugin.ad_daily`` and without writing a
    ``sync_jobs`` row (intentional — we don't want a "succeeded" record
    for a no-op).
    """
    if _DISABLED:
        yesterday = (datetime.now(UTC) - timedelta(days=1)).date()
        log.warning(
            "[%s] DISABLED (_DISABLED=True) — skipping merge for %s",
            JOB_NAME,
            yesterday.isoformat(),
        )
        return {
            "disabled": True,
            "reason": "_DISABLED=True (see module docstring)",
            "yesterday": yesterday.isoformat(),
            "scopes_total": 0,
            "scopes_solidified": 0,
            "scopes_failed": 0,
        }

    yesterday = (datetime.now(UTC) - timedelta(days=1)).date()

    with run_job(session, job_name=JOB_NAME) as job:
        pairs = list_merge_scope_pairs(session, yesterday=yesterday)
        total_pairs = len(pairs)
        success = 0
        failed = 0

        for seller_id, advertiser_id in pairs:
            try:
                merge_today_into_daily(
                    session,
                    seller_id=seller_id,
                    advertiser_id=advertiser_id,
                    yesterday=yesterday,
                )
                success += 1
            except Exception:
                log.exception(
                    "[%s] failed for seller=%s advertiser=%s date=%s",
                    JOB_NAME,
                    seller_id,
                    advertiser_id,
                    yesterday,
                )
                failed += 1

        job.rows_total = total_pairs
        job.rows_inserted = success
        job.rows_failed = failed
        job.extra = {
            "yesterday": yesterday.isoformat(),
            "scopes_total": total_pairs,
            "scopes_solidified": success,
            "scopes_failed": failed,
        }
        return {
            "yesterday": yesterday.isoformat(),
            "scopes_total": total_pairs,
            "scopes_solidified": success,
            "scopes_failed": failed,
        }


def merge_today_into_daily(
    session: Session,
    *,
    seller_id: str,
    advertiser_id: str,
    yesterday: date,
) -> None:
    """Solidify one scope's ad_today → ad_daily, then delete from ad_today.

    Non-committing wrapper (caller/scheduler commits).
    """
    from sqlalchemy import text

    # pi-lens-ignore: python-sql-injection
    session.execute(
        text("""
            INSERT INTO plugin.ad_daily (
                seller_id, advertiser_id, campaign_id, product_id, endpoint, day,
                mixed_real_cost, onsite_roi2_shopping_sku, onsite_roi2_shopping_value,
                onsite_mixed_real_roi2_shopping, metrics_extra, created_at
            )
            SELECT seller_id, advertiser_id, campaign_id, product_id, endpoint, day,
                   mixed_real_cost, onsite_roi2_shopping_sku, onsite_roi2_shopping_value,
                   onsite_mixed_real_roi2_shopping, metrics_extra, created_at
            FROM plugin.ad_today
            WHERE seller_id = :seller_id AND advertiser_id = :advertiser_id
              AND day = :yesterday
            ON CONFLICT ON CONSTRAINT uq_ad_daily DO NOTHING
        """),
        {
            "seller_id": seller_id,
            "advertiser_id": advertiser_id,
            "yesterday": yesterday,
        },
    )

    # pi-lens-ignore: python-sql-injection
    session.execute(
        text("""
            DELETE FROM plugin.ad_today
            WHERE seller_id = :seller_id AND advertiser_id = :advertiser_id
              AND day = :yesterday
        """),
        {
            "seller_id": seller_id,
            "advertiser_id": advertiser_id,
            "yesterday": yesterday,
        },
    )


__all__ = [
    "JOB_NAME",
    "run",
]
