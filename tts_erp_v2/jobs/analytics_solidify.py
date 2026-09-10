"""Sync job: solidify_yesterday — ad_today → ad_daily daily固化。

每天零点后将前一天的 ad_today 数据固化到 ad_daily，然后清空 ad_today 中
对应日期的行。这样当天的数据在 ad_today 中持续更新（Chrome 扩展刷新），
昨天及更早的数据归档到 ad_daily 不再变动，coverage 查询走 ad_daily。

Cadence: 每小时跑一次，但只在昨天的数据存在时才实际执行（空表时 early
return）。保护窗：scheduler 配置 jitter，实际执行窗口 UTC 00:00-01:00。

Design ref: tech-doc/analytics/daily-sync-with-coverage.md §5.6。

Bookkeeping: uses :func:`tts_erp_v2.jobs.runner.run_job`, which does
NOT commit — the scheduler's system-job executor commits on success.
"""

from __future__ import annotations

import logging
from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy.orm import Session

from tts_erp_v2.analytics.repository import solidify_yesterday_scope_pairs
from tts_erp_v2.jobs.runner import run_job

log = logging.getLogger("tts_erp_v2.jobs.analytics_solidify")

JOB_NAME = "analytics.solidify"


def run(session: Session) -> dict[str, Any]:
    """Solidify yesterday's ad_today data into ad_daily for all scopes.

    Returns counters for the sync_jobs row.
    """
    yesterday = (datetime.now(UTC) - timedelta(days=1)).date()

    with run_job(session, job_name=JOB_NAME) as job:
        pairs = solidify_yesterday_scope_pairs(session, yesterday=yesterday)
        total_pairs = len(pairs)
        success = 0
        failed = 0

        for seller_id, advertiser_id in pairs:
            try:
                solidify_yesterday(
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


def solidify_yesterday(
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
            INSERT INTO analytics.ad_daily (
                seller_id, advertiser_id, campaign_id, product_id, endpoint, day,
                mixed_real_cost, onsite_roi2_shopping_sku, onsite_roi2_shopping_value,
                onsite_mixed_real_roi2_shopping, metrics_extra, created_at
            )
            SELECT seller_id, advertiser_id, campaign_id, product_id, endpoint, day,
                   mixed_real_cost, onsite_roi2_shopping_sku, onsite_roi2_shopping_value,
                   onsite_mixed_real_roi2_shopping, metrics_extra, created_at
            FROM analytics.ad_today
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
            DELETE FROM analytics.ad_today
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
