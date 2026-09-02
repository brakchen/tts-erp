"""Miaoshou sync job: collect_box (1h cadence).

Syncs the **collect box list** (采集箱列表) from
``search_collect_box_list`` into ``integration.raw_records`` +
``procurement.procurement_products``. Current production data set is
empty (the operator hasn't loaded any items into the box), so the
job MUST pass through an empty-data path without raising.

Endpoint
--------
``POST /open/v1/product/collect_box/tiktok/collect_box/search_collect_box_list``
(apifox api-226180998, see ``miaoshou/endpoints/tk_collect_box.py``).
Body: ``{"pageNo", "pageSize"}`` + optional ``filter`` dict.
Page-size cap = 20.

Pagination
----------
We use :func:`tts_erp_v2.proxy.miaoshou.retry.paginate_with_retry` so
the rate-limit empty-page protection carries over. Empty data MUST
terminate cleanly (no infinite loop, no row inserted, no issue
written). The job is otherwise a thin wrapper that:

1. walks all pages via paginate_with_retry;
2. persists each page's raw JSON into ``integration.raw_records``;
3. upserts each item into ``procurement.procurement_products`` keyed
   by ``(procurement_account_id, external_product_id)`` where
   ``external_product_id`` is the ``itemId`` field on the collect-box
   row;
4. records parse failures as ``integration.sync_issues`` rows.

Failure mode contract
---------------------
Empty pages → natural pagination end (no error). Non-rate-limit
failures propagate to ``run_job`` → SyncJob status='failed'. Re-runs
are idempotent (ON CONFLICT DO UPDATE on the unique constraint).
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Protocol

from sqlalchemy import select
from sqlalchemy.orm import Session

from tts_erp_v2.db.models.procurement import ProcurementProduct
from tts_erp_v2.jobs.miaoshou._common import (
    resolve_miaoshou_context,
)
from tts_erp_v2.jobs.runner import record_raw_payload, record_sync_issue, run_job

log = logging.getLogger("tts_erp_v2.jobs.miaoshou.collect_box")

JOB_NAME = "miaoshou.collect_box"
ENDPOINT = "miaoshou.collect_box.search_collect_box_list"
PAGE_SIZE = 20  # documented upper bound
MAX_PAGES = 1000


class _MiaoshouClientProto(Protocol):
    def _call_erp(
        self,
        *,
        path: str,
        body: dict | None = None,
        query: dict | None = None,
        extra_headers: dict | None = None,
    ) -> dict[str, Any]: ...


def _fetch_page(
    client: _MiaoshouClientProto, *, page_no: int, page_size: int = PAGE_SIZE
) -> dict[str, Any]:
    return client._call_erp(
        path="/open/v1/product/collect_box/tiktok/collect_box/search_collect_box_detail_list",
        body={"pageNo": page_no, "pageSize": page_size},
    )


def _parse_product_row(item: dict[str, Any]) -> dict[str, Any] | None:
    """Map a collect-box row → procurement_products upsert values.

    Returns ``None`` when the row lacks an ``itemId`` so the caller
    records an issue instead of inserting a useless zero-id row.
    """
    item_id = item.get("itemId")
    if not item_id:
        return None
    return {
        "external_product_id": str(item_id),
        "product_type": item.get("productType") or "COLLECTED_PRODUCT",
        "title": item.get("title") or item.get("itemTitle"),
        "source_platform": item.get("source") or item.get("sourcePlatform"),
        "source_item_id": item.get("sourceItemId"),
        "source_item_url": item.get("sourceItemUrl") or item.get("itemUrl"),
        "status": item.get("status"),
    }


def sync_collect_box(
    session: Session,
    *,
    client: _MiaoshouClientProto | None = None,
    license_id: str | None = None,
    max_retries: int = 3,
) -> dict[str, Any]:
    """Sync the miaoshou collect-box list.

    Args:
        session: SQLAlchemy session (caller commits).
        client: optional fake / real ``MiaoshouErpClient``.
        license_id: explicit license id; falls back to env.

    Returns:
        Dict with ``pages_walked`` / ``items_seen`` / ``products_upserted`` /
        ``rate_limit_retries`` / ``issues``.
    """
    from tts_erp_v2.proxy.miaoshou.retry import PageResult, paginate_with_retry

    with run_job(session, job_name=JOB_NAME) as job:
        # Always resolve ctx so we can attribute upserts to the right
        # procurement_account row, even when the caller passed an
        # injected client (e.g. tests).
        ctx = resolve_miaoshou_context(session, license_id=license_id)
        if ctx is None:
            raise RuntimeError("no miaoshou credentials row; cannot construct context")
        if client is None:
            from tts_erp_v2.jobs.miaoshou._common import miaoshou_client_factory

            client = miaoshou_client_factory(ctx)

        rate_limit_retries = 0

        def _on_retry(attempt: int, err: BaseException) -> None:
            nonlocal rate_limit_retries
            rate_limit_retries += 1
            log.warning(
                "miaoshou.collect_box page retry attempt=%d err=%r", attempt, err
            )

        def fetch_page(page: int) -> dict[str, Any]:
            return _fetch_page(client, page_no=page)  # type: ignore[arg-type]

        def unwrap_page(payload: dict[str, Any]) -> PageResult:
            data = (payload.get("data") or {}) if isinstance(payload, dict) else {}
            items = data.get("collectBoxDetailList") or []
            return PageResult(
                items=list(items) if isinstance(items, list) else [],
                page=payload.get("page") or 0,
                total_count=data.get("total"),
                total_pages=data.get("totalPage") or data.get("total_pages"),
            )

        def wrapped_fetch(page: int) -> PageResult:
            return unwrap_page(fetch_page(page))

        items, last_page = paginate_with_retry(
            wrapped_fetch,
            start_page=1,
            max_pages=MAX_PAGES,
            max_retries=max_retries,
            on_retry=_on_retry,
        )

        products_upserted = 0
        issues = 0

        for item in items:
            if not isinstance(item, dict):
                record_sync_issue(
                    session,
                    job_name=JOB_NAME,
                    issue_type="COLLECT_BOX_PARSE_FAILED",
                    details={"item": repr(item)[:300]},
                )
                issues += 1
                continue
            try:
                parsed = _parse_product_row(item)
                if parsed is None:
                    record_sync_issue(
                        session,
                        job_name=JOB_NAME,
                        issue_type="COLLECT_BOX_MISSING_ITEM_ID",
                        details={"item_keys": list(item.keys())[:10]},
                    )
                    issues += 1
                    continue

                # Persist raw first so we always have an audit row even
                # if the upsert below fails.
                record_raw_payload(
                    session,
                    endpoint=ENDPOINT,
                    payload=item,
                    external_id=parsed["external_product_id"],
                    credential_id=ctx.credentials.id if ctx else None,
                )

                assert ctx is not None  # narrowed by the early-return above
                _upsert_product(
                    session,
                    procurement_account_id=ctx.account_id,
                    parsed=parsed,
                )
                products_upserted += 1
            except Exception as e:  # noqa: BLE001
                record_sync_issue(
                    session,
                    job_name=JOB_NAME,
                    issue_type="COLLECT_BOX_PARSE_FAILED",
                    external_id=str(item.get("itemId")),
                    details={"error": f"{type(e).__name__}: {e}"},
                )
                issues += 1

        job.rows_total = len(items)
        job.rows_inserted = products_upserted
        job.rows_failed = issues
        job.extra = {
            "pages_walked": last_page,
            "rate_limit_retries": rate_limit_retries,
            "finished_at_iso": datetime.now(timezone.utc).isoformat(),
        }
        return {
            "pages_walked": last_page,
            "items_seen": len(items),
            "products_upserted": products_upserted,
            "rate_limit_retries": rate_limit_retries,
            "issues": issues,
        }


def _upsert_product(
    session: Session,
    *,
    procurement_account_id: int,
    parsed: dict[str, Any],
) -> ProcurementProduct:
    """Idempotent upsert keyed by (procurement_account_id, external_product_id)."""
    from sqlalchemy.dialects.postgresql import insert as pg_insert

    values = {
        "procurement_account_id": procurement_account_id,
        "external_product_id": parsed["external_product_id"],
        "product_type": parsed.get("product_type"),
        "title": parsed.get("title"),
        "source_platform": parsed.get("source_platform"),
        "source_item_id": parsed.get("source_item_id"),
        "source_item_url": parsed.get("source_item_url"),
        "status": parsed.get("status"),
        "source_updated_at": datetime.now(timezone.utc),
        "synced_at": datetime.now(timezone.utc),
    }
    insert_stmt = pg_insert(ProcurementProduct).values(**values)
    upsert_stmt = insert_stmt.on_conflict_do_update(
        index_elements=["procurement_account_id", "external_product_id"],
        set_={
            "product_type": values["product_type"],
            "title": values["title"],
            "source_platform": values["source_platform"],
            "source_item_id": values["source_item_id"],
            "source_item_url": values["source_item_url"],
            "status": values["status"],
            "source_updated_at": values["source_updated_at"],
            "synced_at": values["synced_at"],
        },
    )
    session.execute(upsert_stmt)
    row = session.execute(
        select(ProcurementProduct)
        .where(ProcurementProduct.procurement_account_id == procurement_account_id)
        .where(ProcurementProduct.external_product_id == parsed["external_product_id"])
    ).scalar_one()
    return row


__all__ = ["ENDPOINT", "JOB_NAME", "sync_collect_box"]
