"""Backfill TK-side procurement_products.source_unit_cost from public collect box.

The TK-side rows (external_product_id is the 18-digit TikTok platformItemId
= spu_id) are populated by miaoshou.move_collect / miaoshou.collect_box,
which carry source_item_id but not source_unit_cost. The public collect box
rows (external_product_id is the 10-digit commonCollectBoxDetailId) populated
by miaoshou.common_collect_box carry source_unit_cost / source_min / source_max.
This job bridges the two via source_item_id (1688 offer id) — latest by
synced_at — so spu_id → source_price becomes a direct lookup on the TK-side
row instead of needing the offer-bridge at read time (the latter is still
kept as a secondary fallback in reporting._source_cost_lookup).

Single SQL pass; idempotent via IS DISTINCT FROM guard; no external API.
Runs after common_collect_box (6h cadence aligned) so the offer data is fresh.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from tts_erp_v2.jobs.runner import run_job

log = logging.getLogger("tts_erp_v2.jobs.miaoshou.sync_source_cost_to_master")

JOB_NAME = "miaoshou.sync_source_cost_to_master"
ENDPOINT = "miaoshou.sync_source_cost_to_master.local_db_backfill"

# Single SQL pass:
#   - CTE picks the latest public-box row per source_item_id (10-digit
#     external_product_id, NOT the 18-digit TK-side id space) with a known cost.
#   - UPDATE writes all 3 cost columns on TK-side rows (18-digit id space) whose
#     source_item_id matches, scoped to the same procurement_account_id.
#   - IS DISTINCT FROM guard per column makes the run idempotent: rows already
#     matching the offer stay untouched, rows with any stale value get refreshed.
_SQL_BACKFILL = text(
    """
    WITH latest_offer AS (
        SELECT DISTINCT ON (source_item_id)
               source_item_id,
               source_unit_cost,
               source_min_unit_cost,
               source_max_unit_cost
        FROM procurement.procurement_products
        WHERE NOT (external_product_id ~ '^[0-9]{15,20}$')
          AND source_unit_cost IS NOT NULL
          AND source_item_id IS NOT NULL
        ORDER BY source_item_id, synced_at DESC NULLS LAST, id DESC
    )
    -- NB: no procurement_account_id join — source_item_id is the global
    -- 1688 offer id, not account-scoped. A TK-side row written under one
    -- miaoshou license can freely bridge to a public-box row written under
    -- another (they both reference the same physical 1688 offer).
    UPDATE procurement.procurement_products tk
    SET source_unit_cost = lo.source_unit_cost,
        source_min_unit_cost = lo.source_min_unit_cost,
        source_max_unit_cost = lo.source_max_unit_cost,
        source_updated_at = now()
    FROM latest_offer lo
    WHERE tk.external_product_id ~ '^[0-9]{15,20}$'
      AND tk.source_item_id = lo.source_item_id
      AND (
          tk.source_unit_cost IS DISTINCT FROM lo.source_unit_cost
          OR tk.source_min_unit_cost IS DISTINCT FROM lo.source_min_unit_cost
          OR tk.source_max_unit_cost IS DISTINCT FROM lo.source_max_unit_cost
      )
    RETURNING tk.id, tk.external_product_id, tk.source_unit_cost
    """
)

_SQL_COUNT_TK_TOTAL = text(
    "SELECT count(*) FROM procurement.procurement_products "
    "WHERE external_product_id ~ '^[0-9]{15,20}$'"
)


def sync_source_cost_to_master(session: Session) -> dict[str, Any]:
    """Backfill TK-side procurement_products source_unit_cost via offer bridge.

    Returns a dict with counters: tk_rows_total / rows_updated / issues.
    SyncJob row (rows_total/inserted/failed) is set inside run_job.
    Caller commits the session (matches other jobs in this directory).
    """
    with run_job(session, job_name=JOB_NAME) as job:
        tk_rows_total = session.execute(_SQL_COUNT_TK_TOTAL).scalar() or 0
        result = session.execute(_SQL_BACKFILL)
        updated = result.fetchall()
        rows_updated = len(updated)

        job.rows_total = tk_rows_total
        job.rows_inserted = rows_updated
        job.rows_failed = 0
        job.extra = {
            "tk_rows_total": tk_rows_total,
            "rows_updated": rows_updated,
            "finished_at_iso": datetime.now(timezone.utc).isoformat(),
        }
        log.info(
            "miaoshou.sync_source_cost_to_master: tk_total=%d updated=%d",
            tk_rows_total,
            rows_updated,
        )

        return {
            "tk_rows_total": tk_rows_total,
            "rows_updated": rows_updated,
            "issues": 0,
        }


__all__ = ["ENDPOINT", "JOB_NAME", "sync_source_cost_to_master"]
