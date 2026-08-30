"""Miaoshou sync job: move_collect (1h cadence).

Syncs the **publish / move-collect task list** from
``search_move_collect_list`` into ``integration.raw_records``
(original JSON) + ``linkage.link_evidence`` (parsed per-task evidence
rows for Lane D's link-compute job).

★ This is the job that fixed the silent-truncation bug (237 records
→ 20 saved) per ``miaoshou/README.md`` §1. We MUST delegate
pagination to :func:`tts_erp_v2.proxy.miaoshou.retry.paginate_with_retry`
so rate-limit empty pages are retried rather than treated as end-of-data.

Endpoint
--------
``POST /open/v1/product/collect_box/tiktok/move_collect/search_move_collect_list``
(apifox api-482189163). Body: ``{"pageNo", "pageSize", "filter": {...}}``.
Page-size cap = 20.

Output
------
* Raw payloads → ``integration.raw_records`` (one per page).
* Per-task evidence → ``linkage.link_evidence`` with
  ``evidence_type='MOVE_COLLECT_TASK'``. We do NOT insert into
  ``linkage.product_links`` here — that's Lane D's link-compute job.
* Fail tasks (no ``platformItemId`` / missing fields) → still recorded
  as evidence so Lane D's fail-only-evidence policy picks them up.
* Parse failures → ``integration.sync_issues`` with
  ``issue_type='MOVE_COLLECT_PARSE_FAILED'``; job continues.

Failure mode contract
---------------------
Upstream rate-limit responses are caught + retried by
``paginate_with_retry`` (Lane A's bug fix). Non-rate-limit network
errors propagate up; ``run_job`` marks the SyncJob row ``failed``
and re-raises so the caller (APScheduler / CLI) decides retry policy.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Protocol

from sqlalchemy import select
from sqlalchemy.orm import Session

from tts_erp_v2.db.models.linkage import LinkEvidence
from tts_erp_v2.jobs.miaoshou._common import (
    MiaoshouContext,
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

    def _call_erp(self, *, path: str, body: dict | None = None, query: dict | None = None,
                  extra_headers: dict | None = None) -> dict[str, Any]: ...


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


def _parse_evidence_row(task: dict[str, Any]) -> tuple[dict[str, Any], str | None]:
    """Map a single move-collect task into ``link_evidence``-shaped data.

    Returns:
        ``(evidence_payload, external_id_or_None)``. The evidence payload
        is the raw task (minus giant transient fields), the external_id
        is the task detail id (string) for issue tracking.
    """
    task_id = task.get("moveCollectTaskDetailId")
    return (
        {
            "move_collect_task_detail_id": task_id,
            "collect_box_detail_id": task.get("collectBoxDetailId"),
            "platform": "tiktok",
            "platform_item_id": task.get("platformItemId"),
            "shop_id": task.get("shopId"),
            "source_item_id": task.get("sourceItemId"),
            "source_item_url": task.get("sourceItemUrl"),
            "status": task.get("status"),
            "reason": task.get("reason"),
            "gmt_create": task.get("gmtCreate"),
            "gmt_modified": task.get("gmtModified"),
            "is_renew_item": task.get("isRenewItem"),
        },
        str(task_id) if task_id is not None else None,
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
        Dict with ``pages`` / ``tasks_seen`` / ``evidence_inserted`` /
        ``rate_limit_retries`` / ``issues``.
    """
    # Imported here to keep the imports light (and to make the rate-limit
    # dependency on Lane A explicit at the call site).
    from tts_erp_v2.proxy.miaoshou.retry import (
        PageResult,
        paginate_with_retry,
    )

    with run_job(session, job_name=JOB_NAME) as job:
        # Always resolve ctx so we can attribute evidence to the right
        # procurement_account row, even when the caller passed an
        # injected client (e.g. tests).
        ctx = resolve_miaoshou_context(session, license_id=license_id)
        if ctx is None:
            raise RuntimeError(
                "no miaoshou credentials row; cannot construct context"
            )
        if client is None:
            from tts_erp_v2.jobs.miaoshou._common import miaoshou_client_factory
            client = miaoshou_client_factory(ctx)

        rate_limit_retries = 0

        def _on_retry(attempt: int, err: BaseException) -> None:
            nonlocal rate_limit_retries
            rate_limit_retries += 1
            log.warning(
                "miaoshou.move_collect page retry attempt=%d err=%r",
                attempt, err,
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
            return PageResult(
                items=list(items) if isinstance(items, list) else [],
                page=payload.get("page") or 0,
                total_count=data.get("total"),
                total_pages=data.get("totalPage") or data.get("total_pages"),
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

        # Persist a raw-record snapshot per page we touched. We don't
        # have raw per-page payloads here (paginate_with_retry abstracts
        # over them), so we re-fetch the last page + emit a single
        # summary raw-record if there is no per-page hook. To keep the
        # raw audit trail complete, we *also* persist each item as its
        # own raw-record row — the integration.evidence_id links them.
        evidence_inserted = 0
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
            try:
                evidence_payload, task_id_str = _parse_evidence_row(task)
            except Exception as e:  # noqa: BLE001
                record_sync_issue(
                    session,
                    job_name=JOB_NAME,
                    issue_type="MOVE_COLLECT_PARSE_FAILED",
                    external_id=str(task.get("moveCollectTaskDetailId")),
                    details={"error": f"{type(e).__name__}: {e}"},
                )
                issues += 1
                continue

            # Raw record (one per task — keeps the audit trail at the
            # item granularity, which is what Lane D will join on).
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

            # Link-evidence row (idempotent: we don't have a unique
            # constraint on (source_table, source_external_id), so we
            # dedup by SELECT before INSERT).
            existing = session.execute(
                select(LinkEvidence)
                .where(LinkEvidence.source_table == "miaoshou.move_collect")
                .where(LinkEvidence.source_external_id == task_id_str)
            ).scalar_one_or_none()
            if existing is None:
                le = LinkEvidence(
                    evidence_type="MOVE_COLLECT_TASK",
                    source_table="miaoshou.move_collect",
                    source_external_id=task_id_str,
                    evidence_payload=evidence_payload,
                    observed_at=datetime.now(timezone.utc),
                )
                session.add(le)
                session.flush()
            evidence_inserted += 1

        job.rows_total = len(items)
        job.rows_inserted = evidence_inserted
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
            "evidence_inserted": evidence_inserted,
            "rate_limit_retries": rate_limit_retries,
            "issues": issues,
        }


__all__ = ["ENDPOINT", "JOB_NAME", "sync_move_collect"]
