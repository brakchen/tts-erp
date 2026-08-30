"""tiktok.after_sales — returns + cancellations (the two case types).

Drives the ``after_sales.cases`` + ``after_sales.case_lines`` tables for
case_type='RETURN' and case_type='CANCEL'. Each upstream endpoint maps to
one case_type; we run them in a single job so they share the same
watermark cursor logic.

Cursor: epoch ms in ``integration.sync_cursors`` (scope=shop_id).
"""
from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from tts_erp_v2.db.models import (
    Case,
    CaseLine,
    ChannelAccount,
    RawRecord,
    SalesOrder,
    SalesOrderLine,
    SyncIssue,
)
from tts_erp_v2.sync_worker.job_runner import JobResult

JOB_NAME = "tiktok.after_sales"
RETURNS_ENDPOINT = "/return_refund/202309/returns/search"
CANCELLATIONS_ENDPOINT = "/return_refund/202309/cancellations/search"
ProxyCall = Callable[..., dict]


class UpstreamJobError(RuntimeError):
    pass


class ParseError(ValueError):
    pass


def _epoch_seconds_to_utc(seconds: int | None):
    if seconds is None or seconds <= 0:
        return None
    return datetime.fromtimestamp(int(seconds), tz=timezone.utc)


def _to_decimal(v):
    if v is None or v == "":
        return None
    try:
        return Decimal(str(v))
    except Exception:  # noqa: BLE001
        return None


def _walk_pages(proxy_call, *, endpoint: str, base_body: dict) -> list[dict]:
    collected: list[dict] = []
    next_token: str | None = None
    body = dict(base_body)
    while True:
        page_body = dict(body)
        if next_token:
            page_body["next_page_token"] = next_token
        resp = proxy_call("POST", endpoint, body=page_body)
        code = resp.get("code", -1)
        if code != 0:
            raise UpstreamJobError(
                f"{endpoint} non-zero code={code} message={resp.get('message')!r}"
            )
        data = resp.get("data") or {}
        # returns: data.returns; cancellations: data.cancellations
        items = data.get("returns") or data.get("cancellations") or []
        collected.extend(items)
        next_token = data.get("next_page_token") or None
        if not next_token:
            break
    return collected


def _parse_case(case_type: str, raw: dict) -> dict:
    cid = raw.get("return_id") or raw.get("cancel_id") or raw.get("id")
    if not cid:
        raise ParseError(f"{case_type} id missing")
    order_id = (
        raw.get("order_id")
        or raw.get("related_order_id")
        or raw.get("order", {}).get("order_id")
    )
    return {
        "case_type": case_type,
        "external_case_id": str(cid),
        "external_order_id": str(order_id) if order_id else None,
        "status": raw.get("status") or raw.get("return_status") or raw.get("cancel_status"),
        "reason_code": raw.get("reason_code"),
        "reason_text": raw.get("reason_text"),
        "created_at_source": _epoch_seconds_to_utc(raw.get("create_time")),
        "updated_at_source": _epoch_seconds_to_utc(raw.get("update_time")),
    }


def _resolve_sales_order_id(session: Session, account_id: int, external_order_id: str | None) -> int | None:
    if not external_order_id:
        return None
    return session.execute(
        select(SalesOrder.id).where(
            SalesOrder.channel_account_id == account_id,
            SalesOrder.external_order_id == external_order_id,
        )
    ).scalar_one_or_none()


def _process_one_type(
    session: Session,
    *,
    proxy_call: ProxyCall,
    account_id: int,
    case_type: str,
    endpoint: str,
    raw_cases: list[dict],
) -> tuple[int, int, int]:
    """Returns (total, inserted, failed) for one case_type."""
    total = len(raw_cases)
    inserted = 0
    failed = 0
    for raw in raw_cases:
        try:
            fields = _parse_case(case_type, raw)
        except ParseError as e:
            failed += 1
            session.add(
                SyncIssue(
                    job_name=JOB_NAME,
                    issue_type="PARSE_ERROR",
                    external_id=str(raw.get("id") or raw.get("return_id") or raw.get("cancel_id") or "<unknown>"),
                    details={"error": str(e), "case_type": case_type},
                )
            )
            continue

        so_id = _resolve_sales_order_id(session, account_id, fields["external_order_id"])
        if so_id is None:
            # V3 §14 — surface unknown order as a sync_issue, not silent drop
            session.add(
                SyncIssue(
                    job_name=JOB_NAME,
                    issue_type="UNKNOWN_ORDER",
                    external_id=fields["external_case_id"],
                    details={"external_order_id": fields["external_order_id"]},
                )
            )
            failed += 1
            continue

        raw_row = RawRecord(
            endpoint=endpoint,
            external_id=fields["external_case_id"],
            payload=raw,
        )
        session.add(raw_row)
        session.flush()

        insert_values = {
            "channel_account_id": account_id,
            "sales_order_id": so_id,
            **{k: v for k, v in fields.items() if k != "external_order_id"},
            "raw_record_id": raw_row.id,
        }
        update_cols = {k: insert_values[k] for k in fields if k != "external_order_id"}
        update_cols["raw_record_id"] = raw_row.id
        session.execute(
            pg_insert(Case).values(**insert_values).on_conflict_do_update(
                index_elements=["channel_account_id", "external_case_id"],
                set_=update_cols,
            )
        )
        case_row = session.execute(
            select(Case).where(
                Case.channel_account_id == account_id,
                Case.external_case_id == fields["external_case_id"],
            )
        ).scalar_one()

        for raw_line in (
            raw.get("return_line_items") or raw.get("cancel_line_items") or []
        ):
            ext_line_id = raw_line.get("line_id") or raw_line.get("id")
            if not ext_line_id:
                session.add(
                    SyncIssue(
                        job_name=JOB_NAME,
                        issue_type="PARSE_ERROR",
                        external_id=f"{fields['external_case_id']}:<line>",
                        details={"error": "line_id missing"},
                    )
                )
                continue
            line_values = {
                "case_id": case_row.id,
                "external_case_line_id": str(ext_line_id),
                "quantity": _to_decimal(raw_line.get("quantity")),
                "refund_amount": _to_decimal(
                    (raw_line.get("refund_amount") or {}).get("amount")
                ),
                "currency": (raw_line.get("refund_amount") or {}).get("currency"),
                "should_replenish_stock": raw_line.get("restock"),
            }
            # Resolve sales_order_line_id (NOT NULL FK on case_lines).
            # The line is keyed by (sales_order_id, external_line_id).
            sol_id = session.execute(
                select(SalesOrderLine.id).where(
                    SalesOrderLine.sales_order_id == so_id,
                    SalesOrderLine.external_line_id == str(ext_line_id),
                )
            ).scalar_one_or_none()
            if sol_id is None:
                # The case's line references a sales_order_line that
                # hasn't been synced yet — surface as a sync_issue and
                # skip rather than crash the page.
                session.add(
                    SyncIssue(
                        job_name=JOB_NAME,
                        issue_type="UNKNOWN_LINE",
                        external_id=f"{fields['external_case_id']}:{ext_line_id}",
                        details={
                            "sales_order_id": so_id,
                            "external_line_id": str(ext_line_id),
                        },
                    )
                )
                continue
            line_values["sales_order_line_id"] = sol_id
            session.execute(
                pg_insert(CaseLine).values(**line_values).on_conflict_do_update(
                    index_elements=["case_id", "external_case_line_id"],
                    set_={k: line_values[k] for k in line_values if k != "case_id"},
                )
            )
        inserted += 1

    return total, inserted, failed


def run(
    session: Session,
    *,
    proxy_call: ProxyCall,
    shop_id: str,
    page_size: int = 100,
    scope: str | None = None,
) -> JobResult:
    from tts_erp_v2.sync_worker import watermarks

    cursor_scope = scope or shop_id
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

    watermark_ms = watermarks.get_cursor(
        session, job_name=JOB_NAME, scope=cursor_scope
    )
    base_body: dict[str, Any] = {"page_size": page_size}
    if watermark_ms:
        base_body["update_time_ge"] = int(watermark_ms) // 1000

    returns = _walk_pages(proxy_call, endpoint=RETURNS_ENDPOINT, base_body=base_body)
    cancellations = _walk_pages(
        proxy_call, endpoint=CANCELLATIONS_ENDPOINT, base_body=base_body
    )

    r_total, r_ins, r_fail = _process_one_type(
        session,
        proxy_call=proxy_call,
        account_id=account.id,
        case_type="RETURN",
        endpoint=RETURNS_ENDPOINT,
        raw_cases=returns,
    )
    c_total, c_ins, c_fail = _process_one_type(
        session,
        proxy_call=proxy_call,
        account_id=account.id,
        case_type="CANCEL",
        endpoint=CANCELLATIONS_ENDPOINT,
        raw_cases=cancellations,
    )

    total = r_total + c_total
    inserted = r_ins + c_ins
    failed = r_fail + c_fail

    # Watermark: advance only when at least one row was processed
    max_update_ms: int | None = None
    for raw in (*returns, *cancellations):
        ts = _epoch_seconds_to_utc(raw.get("update_time"))
        if ts:
            ms = int(ts.timestamp() * 1000)
            if max_update_ms is None or ms > max_update_ms:
                max_update_ms = ms

    new_cursor_ms: int | None = None
    if max_update_ms is not None and (
        watermark_ms is None or max_update_ms > int(watermark_ms)
    ):
        watermarks.set_cursor(
            session,
            job_name=JOB_NAME,
            scope=cursor_scope,
            cursor_epoch_ms=max_update_ms,
        )
        new_cursor_ms = max_update_ms

    return JobResult(
        rows_total=total,
        rows_inserted=inserted,
        rows_failed=failed,
        cursor=new_cursor_ms,
    )


__all__ = [
    "run",
    "JOB_NAME",
    "RETURNS_ENDPOINT",
    "CANCELLATIONS_ENDPOINT",
    "ProxyCall",
    "UpstreamJobError",
    "ParseError",
]
