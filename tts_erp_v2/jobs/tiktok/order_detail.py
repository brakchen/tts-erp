"""tiktok.order_detail — fetch detail for specific order ids and fill gaps.

Drives the orders job's known gap: the search endpoint can return rows
that don't include every line/field. This job re-fetches by id so the
normalized tables catch up.

Inputs: a list of external order ids (typically: orders whose line parse
failed in the orders job, or whose product/variant was unknown at the
time of sync).
"""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from tts_erp_v2.db.models import (
    ChannelAccount,
    RawRecord,
    SalesOrder,
    SalesOrderLine,
    SyncIssue,
)
from tts_erp_v2.jobs.tiktok.orders import (
    ENDPOINT as SEARCH_ENDPOINT,
)
from tts_erp_v2.jobs.tiktok.orders import (
    ParseError,
    ProxyCall,
    UpstreamJobError,
    _parse_line_payload,
    _parse_order_payload,
    _record_issue,
    _safe_truncate,
    _store_raw,
)
from tts_erp_v2.sync_worker.job_runner import JobResult

JOB_NAME = "tiktok.order_detail"
DETAIL_ENDPOINT = "/order/202309/orders/detail"


def run(
    session: Session,
    *,
    proxy_call: ProxyCall,
    shop_id: str,
    order_ids: list[str],
) -> JobResult:
    """Fetch detail for each id and upsert.

    The proxy_call receives ``GET`` calls with the path + query string
    containing the id. Implementation detail: tests use a fake that
    keys on the order_id and returns the structured detail payload.
    """
    if not order_ids:
        return JobResult()

    account = session.execute(
        select(ChannelAccount).where(
            ChannelAccount.platform == "tiktok",
            ChannelAccount.external_account_id == shop_id,
        )
    ).scalar_one_or_none()
    if account is None:
        raise UpstreamJobError(
            f"channel_accounts row missing for tiktok shop_id={shop_id!r}"
        )

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
            _record_issue(
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
            _record_issue(
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
            "channel_account_id": account.id,
            **fields,
            "raw_record_id": raw_row.id,
        }
        update_cols = {k: insert_values[k] for k in fields}
        update_cols["raw_record_id"] = raw_row.id
        stmt = pg_insert(SalesOrder).values(**insert_values).on_conflict_do_update(
            index_elements=["channel_account_id", "external_order_id"],
            set_=update_cols,
        )
        session.execute(stmt)
        so_row = session.execute(
            select(SalesOrder).where(
                SalesOrder.channel_account_id == account.id,
                SalesOrder.external_order_id == order_id,
            )
        ).scalar_one()

        for raw_line in raw.get("line_items") or []:
            try:
                line_fields = _parse_line_payload(order_id, raw_line)
            except ParseError as e:
                _record_issue(
                    session,
                    job_name=JOB_NAME,
                    issue_type="PARSE_ERROR",
                    external_id=f"{order_id}:{raw_line.get('line_id')}",
                    details={"error": str(e), "raw": _safe_truncate(raw_line)},
                )
                continue
            li_values = {
                "sales_order_id": so_row.id,
                **line_fields,
                "raw_record_id": raw_row.id,
            }
            li_update = {k: li_values[k] for k in line_fields}
            li_update["raw_record_id"] = raw_row.id
            session.execute(
                pg_insert(SalesOrderLine).values(**li_values).on_conflict_do_update(
                    index_elements=["sales_order_id", "external_line_id"],
                    set_=li_update,
                )
            )
        rows_inserted += 1

    return JobResult(
        rows_total=rows_total,
        rows_inserted=rows_inserted,
        rows_failed=rows_failed,
    )


__all__ = ["run", "JOB_NAME", "DETAIL_ENDPOINT"]
