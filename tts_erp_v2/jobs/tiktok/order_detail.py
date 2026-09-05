"""tiktok.order_detail — fetch detail for specific order ids and fill gaps.

Drives the orders job's known gap: the search endpoint can return rows
that don't include every line/field. This job re-fetches by id so the
normalized tables catch up.

Inputs: a list of external order ids (typically: orders whose line parse
failed in the orders job, or whose product/variant was unknown at the
time of sync).

Auto-mode (2026-09-01, lane 3 P1-5)
-----------------------------------
Previously ``order_ids`` was a required positional argument and the
scheduler didn't pass it — every scheduled tick crashed with a
``TypeError`` (99/99 since the cutover). The job now derives its input
list automatically when ``order_ids=None``:

  * pulls unresolved PARSE_ERROR / UNKNOWN_ORDER / UNKNOWN_LINE issues
    for *this* shop from ``integration.sync_issues``;
  * takes the most recent N (default 50) distinct external_ids (the
    left half of a ``{order_id}:{line_key}`` composite);
  * processes them and resolves the matching issues on success.

Explicit ``order_ids=[...]`` still works for ad-hoc triggers (CLI,
tests).
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from tts_erp_v2.db.models import (
    ChannelAccount,
    SalesOrder,
    SalesOrderLine,
    SyncIssue,
)
from tts_erp_v2.jobs.runner import record_sync_issue
from tts_erp_v2.jobs.tiktok.orders import (
    ParseError,
    ProxyCall,
    UpstreamJobError,
    _parse_line_payload,
    _parse_order_payload,
    _safe_truncate,
    _store_raw,
)
from tts_erp_v2.sync_worker.job_runner import JobResult

JOB_NAME = "tiktok.order_detail"
DETAIL_ENDPOINT = "/order/202309/orders/detail"

#: Issue types that say "go re-fetch this order, we couldn't process it
#: last time". Other types (UPSTREAM_NONZERO, AUTH_ERROR, ...) don't
#: imply a payload-level parse failure, so retrying won't help and we
#: leave them for ops to investigate manually.
AUTO_ISSUE_TYPES: tuple[str, ...] = ("PARSE_ERROR", "UNKNOWN_ORDER", "UNKNOWN_LINE")

#: Cap on how many orders we'll re-fetch per scheduled tick. Keeps a
#: single bad tick from hammering the upstream API if 1000s of issues
#: accumulated while the job was broken.
AUTO_BATCH_SIZE: int = 50


def _auto_collect_order_ids(
    session: Session,
    *,
    account_id: int,
    batch_size: int = AUTO_BATCH_SIZE,
) -> list[str]:
    """Pull order_ids implied by unresolved sync_issues for this job.

    Issues live keyed on ``(job_name, issue_type, external_id)``. The
    ``external_id`` for an order-level failure is the order id (with
    possibly a ``:line_id`` suffix for line-level failures). We split
    on the first colon to recover the order id, dedup, and take the
    most-recently-detected ``batch_size``.

    Shop-scoping note
    -----------------
    sync_issues don't carry a shop_id column — issues are global. The
    scheduler fans out per shop (one tick writes one batch of issues),
    so within a single tick the auto-collected ids are scoped by
    construction. Cross-shop noise is bounded by the upstream 404'ing
    on orders that belong to a different shop; that path writes a new
    UPSTREAM_NONZERO issue but does NOT resolve the original. We
    deliberately don't pre-filter via ``commerce.sales_orders`` because:

      * the order may not have been synced yet (the whole point of
        running order_detail is to backfill it);
      * a sales_orders join would race with concurrent sync jobs.

    The output is a list of candidate order_ids — callers fetch and
    upsert them through the normal pipeline.
    """
    rows = session.execute(
        select(SyncIssue.external_id, SyncIssue.detected_at)
        .where(SyncIssue.job_name == JOB_NAME)
        .where(SyncIssue.issue_type.in_(AUTO_ISSUE_TYPES))
        .where(SyncIssue.resolved_at.is_(None))
        .where(SyncIssue.external_id.is_not(None))
        .order_by(SyncIssue.detected_at.desc())
        .limit(batch_size * 4)
    ).all()
    out: list[str] = []
    seen: set[str] = set()
    for ext_id, _detected_at in rows:
        if ext_id is None:
            continue
        # Composite external_ids look like ``"<order_id>:<line_key>"``.
        # The detail endpoint works on order ids only.
        order_id = ext_id.split(":", 1)[0]
        if order_id in seen:
            continue
        seen.add(order_id)
        out.append(order_id)
        if len(out) >= batch_size:
            break
    return out


def _resolve_matching_issues(
    session: Session,
    *,
    order_id: str,
) -> int:
    """Mark any unresolved sync_issues for this order as resolved.

    Resolves on the literal ``external_id`` plus the ``{external_id}:*``
    composite (line-level issues) for this job. Returns the number of
    rows touched. Called only on the success path so a failed detail
    fetch keeps the issue open for the next tick.
    """
    now = datetime.now(timezone.utc)
    rows = session.execute(
        select(SyncIssue)
        .where(SyncIssue.job_name == JOB_NAME)
        .where(SyncIssue.resolved_at.is_(None))
        # Parens around the == are required: Python's ``|`` has higher
        # precedence than ``==``, so without parens this evaluates as
        # ``external_id == (order_id | external_id.like(...))`` and the
        # ``str | BinaryExpression`` raises TypeError.
        .where(
            (SyncIssue.external_id == order_id)
            | SyncIssue.external_id.like(f"{order_id}:%")
        )
    ).scalars().all()
    for row in rows:
        row.resolved_at = now
    return len(rows)


def run(
    session: Session,
    *,
    proxy_call: ProxyCall,
    shop_id: str,
    order_ids: list[str] | None = None,
) -> JobResult:
    """Fetch detail for each id and upsert.

    The proxy_call receives ``GET`` calls with the path + query string
    containing the id. Implementation detail: tests use a fake that
    keys on the order_id and returns the structured detail payload.

    When ``order_ids`` is None the job derives its input list from
    ``integration.sync_issues`` for this shop (auto-mode). An empty
    explicit list is a no-op (kept for backward compat with test
    fixtures that want to assert the no-op behaviour).
    """
    account = session.execute(
        select(ChannelAccount).where(
            ChannelAccount.platform == "tiktok",
            ChannelAccount.shop_id == shop_id,
        )
    ).scalar_one_or_none()
    if account is None:
        raise UpstreamJobError(
            f"shops row missing for tiktok shop_id={shop_id!r}"
        )

    if order_ids is None:
        order_ids = _auto_collect_order_ids(session, account_id=account.id)
    if not order_ids:
        return JobResult()

    rows_total = 0
    rows_inserted = 0
    rows_failed = 0
    for order_id in order_ids:
        rows_total += 1
        path = f"/order/202309/orders/{order_id}"
        resp = proxy_call("GET", path, body=None)
        code = resp.get("code", -1)
        if code != 0:
            rows_failed += 1
            record_sync_issue(
                session,
                job_name=JOB_NAME,
                issue_type="UPSTREAM_NONZERO",
                external_id=order_id,
                details={"code": code, "message": resp.get("message")},
            )
            continue
        raw = (resp.get("data") or {}).get("order") or {}
        try:
            fields = _parse_order_payload(raw)
        except ParseError as e:
            rows_failed += 1
            record_sync_issue(
                session,
                job_name=JOB_NAME,
                issue_type="PARSE_ERROR",
                external_id=order_id,
                details={"error": str(e), "raw": _safe_truncate(raw)},
            )
            continue

        raw_row = _store_raw(
            session,
            endpoint=DETAIL_ENDPOINT,
            external_id=order_id,
            payload=raw,
        )
        insert_values = {
            "shop_pk": account.id,
            **fields,
            "raw_record_id": raw_row.id,
        }
        update_cols = {k: insert_values[k] for k in fields}
        update_cols["raw_record_id"] = raw_row.id
        stmt = pg_insert(SalesOrder).values(**insert_values).on_conflict_do_update(
            index_elements=["shop_pk", "order_id"],
            set_=update_cols,
        )
        session.execute(stmt)
        so_row = session.execute(
            select(SalesOrder).where(
                SalesOrder.shop_pk == account.id,
                SalesOrder.order_id == order_id,
            )
        ).scalar_one()

        for raw_line in raw.get("line_items") or []:
            try:
                line_fields = _parse_line_payload(order_id, raw_line)
            except ParseError as e:
                record_sync_issue(
                    session,
                    job_name=JOB_NAME,
                    issue_type="PARSE_ERROR",
                    external_id=f"{order_id}:{raw_line.get('line_id')}",
                    details={"error": str(e), "raw": _safe_truncate(raw_line)},
                )
                continue
            li_values = {
                "order_pk": so_row.id,
                **line_fields,
                "raw_record_id": raw_row.id,
            }
            li_update = {k: li_values[k] for k in line_fields}
            li_update["raw_record_id"] = raw_row.id
            session.execute(
                pg_insert(SalesOrderLine).values(**li_values).on_conflict_do_update(
                    index_elements=["order_pk", "external_line_id"],
                    set_=li_update,
                )
            )
        # Success: resolve any open issues for this order so the next
        # tick doesn't re-fetch the same id.
        _resolve_matching_issues(session, order_id=order_id)
        rows_inserted += 1

    return JobResult(
        rows_total=rows_total,
        rows_inserted=rows_inserted,
        rows_failed=rows_failed,
    )


__all__ = [
    "run",
    "JOB_NAME",
    "DETAIL_ENDPOINT",
    "AUTO_BATCH_SIZE",
    "AUTO_ISSUE_TYPES",
]
