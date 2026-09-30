"""Miaoshou sync job: move_collect (1h cadence).

Syncs the publish / move-collect task list from
``search_move_collect_list`` into ``integration.raw_records``. The raw audit
trail is retained even though the unused linkage projection was retired.

This job carries the silent-truncation regression fix (237 records → 20 saved)
from ``miaoshou/README.md``: pagination must go through
:func:`tts_erp_v2.proxy.miaoshou.retry.paginate_with_retry` so rate-limit empty
pages are retried instead of being mistaken for end-of-data.

Non-dict rows and raw-record failures are recorded in
``integration.sync_issues`` while other tasks continue. Non-rate-limit network
errors propagate; ``run_job`` marks the SyncJob failed and re-raises.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Protocol

from sqlalchemy.orm import Session

from tts_erp_v2.jobs.miaoshou._common import (
    resolve_miaoshou_context,
)
from tts_erp_v2.jobs.runner import record_raw_payload, record_sync_issue, run_job

log = logging.getLogger("tts_erp_v2.jobs.miaoshou.move_collect")

JOB_NAME = "miaoshou.move_collect"
ENDPOINT = "miaoshou.move_collect.search_move_collect_list"
PAGE_SIZE = 20  # documented upper bound (apifox api-482189163)
MAX_PAGES = 1000  # paginate_with_retry also has its own safety cap


class _MiaoshouClientProto(Protocol):
    """Minimal protocol — the job only needs ``_call_erp``."""

    def _call_erp(
        self,
        *,
        path: str,
        body: dict | None = None,
        query: dict | None = None,
        extra_headers: dict | None = None,
    ) -> dict[str, Any]: ...


def _fetch_page(
    client: _MiaoshouClientProto,
    *,
    page_no: int,
    page_size: int = PAGE_SIZE,
    status: str | None = None,
) -> dict[str, Any]:
    body: dict[str, Any] = {"pageNo": page_no, "pageSize": page_size}
    if status is not None:
        body["filter"] = {"status": status}
    return client._call_erp(
        path="/open/v1/product/collect_box/tiktok/move_collect/search_move_collect_list",
        body=body,
    )


def sync_move_collect(
    session: Session,
    *,
    client: _MiaoshouClientProto | None = None,
    status: str | None = None,
    license_id: str | None = None,
    max_retries: int = 3,  # lower default — paginate_with_retry also retries per-page
) -> dict[str, Any]:
    """Sync the move-collect task list.

    Args:
        session: SQLAlchemy session (caller commits).
        client: optional ``MiaoshouErpClient``-like object. If omitted,
            the default factory is used.
        status: optional upstream ``filter.status`` (e.g. ``"success"``).
        license_id: explicit license id; falls back to env.

    Returns:
        Dict with ``pages_walked`` / ``tasks_seen`` / ``tasks_recorded`` /
        ``rate_limit_retries`` / ``issues``.
    """
    # Imported here to keep the imports light (and to make the rate-limit
    # dependency on Lane A explicit at the call site).
    from tts_erp_v2.proxy.miaoshou.retry import (
        PageResult,
        paginate_with_retry,
    )

    with run_job(session, job_name=JOB_NAME) as job:
        # Always resolve context so raw records carry the source credential,
        # even when the caller injects a fake client in tests.
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
                "miaoshou.move_collect page retry attempt=%d err=%r",
                attempt,
                err,
            )

        def fetch_page(page: int) -> dict[str, Any]:
            return _fetch_page(client, page_no=page, status=status)  # type: ignore[arg-type]

        def unwrap_page(payload: dict[str, Any]) -> PageResult:
            """Pull the moveCollectDetailList array out of the miaoshou
            envelope. paginate_with_retry's ``_coerce_page_payload``
            expects ``data`` to be the list itself, but the actual SDK
            returns ``data.moveCollectDetailList``. Return a PageResult
            so the paginator handles list extraction + totals uniformly.
            """
            data = (payload.get("data") or {}) if isinstance(payload, dict) else {}
            items = data.get("moveCollectDetailList") or []
            # Miaoshou 的 totalPage 字段实际返回的是总条数（如 382），不是总页数。
            # 用实际返回的 item 数量推断 page_size，再算出真实页数。
            raw_total_count = data.get("total")
            raw_total_page = data.get("totalPage") or data.get("total_pages")
            actual_total_pages: int | None = None
            if isinstance(raw_total_count, int) and raw_total_count > 0:
                effective_page_size = len(items) if items else PAGE_SIZE
                actual_total_pages = -(
                    -raw_total_count // effective_page_size
                )  # ceil division
            elif isinstance(raw_total_page, int) and raw_total_page > 0:
                actual_total_pages = raw_total_page
            return PageResult(
                items=list(items) if isinstance(items, list) else [],
                page=payload.get("page") or 0,
                total_count=raw_total_count,
                total_pages=actual_total_pages,
            )

        # Re-wrap fetch_page so the paginator receives PageResult.
        def wrapped_fetch(page: int) -> PageResult:
            payload = fetch_page(page)
            result = unwrap_page(payload)
            return result

        items, last_page = paginate_with_retry(
            wrapped_fetch,
            start_page=1,
            max_pages=MAX_PAGES,
            max_retries=max_retries,
            on_retry=_on_retry,
        )

        # Persist each task as its own raw record. The paginator intentionally
        # abstracts away per-page payloads, so item-level records are the durable
        # audit trail.
        tasks_recorded = 0
        issues = 0

        for task in items:
            if not isinstance(task, dict):
                record_sync_issue(
                    session,
                    job_name=JOB_NAME,
                    issue_type="MOVE_COLLECT_PARSE_FAILED",
                    details={"task": repr(task)[:300]},
                )
                issues += 1
                continue
            task_id = task.get("moveCollectTaskDetailId")
            task_id_str = str(task_id) if task_id is not None else None
            try:
                record_raw_payload(
                    session,
                    endpoint=ENDPOINT,
                    payload=task,
                    external_id=task_id_str,
                    credential_id=ctx.credentials.id if ctx else None,
                )
            except Exception as e:  # noqa: BLE001
                record_sync_issue(
                    session,
                    job_name=JOB_NAME,
                    issue_type="RAW_RECORD_FAILED",
                    external_id=task_id_str,
                    details={"error": f"{type(e).__name__}: {e}"},
                )
                issues += 1
                continue

            tasks_recorded += 1

        job.rows_total = len(items)
        job.rows_inserted = tasks_recorded
        job.rows_failed = issues
        job.extra = {
            "pages_walked": last_page,
            "rate_limit_retries": rate_limit_retries,
            "filter_status": status,
            "finished_at_iso": datetime.now(timezone.utc).isoformat(),
        }
        return {
            "pages_walked": last_page,
            "tasks_seen": len(items),
            "tasks_recorded": tasks_recorded,
            "rate_limit_retries": rate_limit_retries,
            "issues": issues,
        }


__all__ = ["ENDPOINT", "JOB_NAME", "sync_move_collect"]
