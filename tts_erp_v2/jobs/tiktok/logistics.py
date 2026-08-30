"""tiktok.logistics — shipments + tracking events.

Two endpoints:

* shipments list → ``fulfillment.shipments``
* tracking per shipment → ``fulfillment.tracking_events``

Upstream `logistics_tracking_event` rows use **epoch milliseconds** for
``update_time_millis`` and ``event_time`` (V3 §14). Our migration
already verified this; the job converts at the boundary.

Cursor: epoch ms for the shipments list. Tracking events are fetched
per-shipment on demand.
"""
from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from tts_erp_v2.db.models import (
    ChannelAccount,
    RawRecord,
    SalesOrder,
    Shipment,
    SyncIssue,
    TrackingEvent,
)
from tts_erp_v2.sync_worker.job_runner import JobResult

JOB_NAME = "tiktok.logistics"
SHIPMENTS_ENDPOINT = "/order/202309/orders/shipments"
TRACKING_ENDPOINT = "/fulfillment/202309/tracking"
ProxyCall = Callable[..., dict]


class UpstreamJobError(RuntimeError):
    pass


class ParseError(ValueError):
    pass


def _epoch_ms_to_utc(ms: int | None):
    if ms is None or ms <= 0:
        return None
    return datetime.fromtimestamp(int(ms) / 1000.0, tz=timezone.utc)


def _walk_pages(proxy_call, *, endpoint: str, base_body: dict):
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
        items = data.get("shipments") or data.get("packages") or []
        collected.extend(items)
        next_token = data.get("next_page_token") or None
        if not next_token:
            break
    return collected


def _parse_shipment(raw: dict) -> dict:
    pkg_id = raw.get("package_id") or raw.get("id")
    order_id = raw.get("order_id")
    if not pkg_id or not order_id:
        raise ParseError("package_id or order_id missing")
    return {
        "external_package_id": str(pkg_id),
        "external_order_id": str(order_id),
        "tracking_number": raw.get("tracking_number"),
        "provider_id": raw.get("shipping_provider_id") or raw.get("provider_id"),
        "provider_name": raw.get("shipping_provider_name") or raw.get("provider_name"),
        "status": raw.get("status"),
        "shipped_at": _epoch_ms_to_utc(raw.get("shipped_time_ms") or raw.get("shipped_time")),
        "delivered_at": _epoch_ms_to_utc(raw.get("delivered_time_ms") or raw.get("delivered_time")),
    }


def _parse_event(raw: dict) -> dict:
    ekey = raw.get("event_key") or raw.get("id") or raw.get("description")
    if not ekey:
        raise ParseError("event_key missing")
    return {
        "external_event_key": str(ekey),
        "action_code": raw.get("action_code"),
        "event_at": _epoch_ms_to_utc(raw.get("event_time_ms") or raw.get("update_time_millis") or raw.get("event_time")),
        "description": raw.get("description"),
        "location": raw.get("location"),
    }


def _resolve_order_id(session, account_id: int, external_order_id: str) -> int | None:
    return session.execute(
        select(SalesOrder.id).where(
            SalesOrder.channel_account_id == account_id,
            SalesOrder.external_order_id == external_order_id,
        )
    ).scalar_one_or_none()


def _upsert_shipment(session, *, sales_order_id: int, fields: dict, raw_record_id: int) -> int:
    insert_values = {
        "sales_order_id": sales_order_id,
        **{k: v for k, v in fields.items() if k != "external_order_id"},
        "raw_record_id": raw_record_id,
    }
    update_cols = {k: insert_values[k] for k in fields if k != "external_order_id"}
    update_cols["raw_record_id"] = raw_record_id
    session.execute(
        pg_insert(Shipment).values(**insert_values).on_conflict_do_update(
            index_elements=["sales_order_id", "external_package_id"],
            set_=update_cols,
        )
    )
    row = session.execute(
        select(Shipment).where(
            Shipment.sales_order_id == sales_order_id,
            Shipment.external_package_id == fields["external_package_id"],
        )
    ).scalar_one()
    return row.id


def _upsert_event(session, *, shipment_id: int, fields: dict) -> None:
    session.execute(
        pg_insert(TrackingEvent).values(
            shipment_id=shipment_id,
            **fields,
        ).on_conflict_do_update(
            index_elements=["shipment_id", "external_event_key"],
            set_={k: fields[k] for k in fields if k != "external_event_key"},
        )
    )


def run(
    session: Session,
    *,
    proxy_call: ProxyCall,
    shop_id: str,
    page_size: int = 100,
    scope: str | None = None,
    fetch_events: bool = True,
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
        # shipments list uses epoch ms
        base_body["update_time_ge_ms"] = int(watermark_ms)

    raw_shipments = _walk_pages(proxy_call, endpoint=SHIPMENTS_ENDPOINT, base_body=base_body)

    total = 0
    inserted = 0
    failed = 0
    events_written = 0
    max_update_ms: int | None = None

    for raw in raw_shipments:
        total += 1
        try:
            s_fields = _parse_shipment(raw)
        except ParseError as e:
            failed += 1
            session.add(
                SyncIssue(
                    job_name=JOB_NAME,
                    issue_type="PARSE_ERROR",
                    external_id=str(raw.get("package_id") or raw.get("id") or "<unknown>"),
                    details={"error": str(e), "section": "shipments"},
                )
            )
            continue

        so_id = _resolve_order_id(session, account.id, s_fields["external_order_id"])
        if so_id is None:
            session.add(
                SyncIssue(
                    job_name=JOB_NAME,
                    issue_type="UNKNOWN_ORDER",
                    external_id=s_fields["external_package_id"],
                    details={"external_order_id": s_fields["external_order_id"]},
                )
            )
            failed += 1
            continue

        raw_row = RawRecord(
            endpoint=SHIPMENTS_ENDPOINT,
            external_id=s_fields["external_package_id"],
            payload=raw,
        )
        session.add(raw_row)
        session.flush()
        shipment_id = _upsert_shipment(
            session,
            sales_order_id=so_id,
            fields=s_fields,
            raw_record_id=raw_row.id,
        )

        if fetch_events and s_fields.get("tracking_number"):
            try:
                resp = proxy_call(
                    "GET",
                    f"{TRACKING_ENDPOINT}/{s_fields['tracking_number']}",
                    body=None,
                )
                event_times_for_watermark: list[int] = []
                if resp.get("code") == 0:
                    for raw_e in (resp.get("data") or {}).get("events") or []:
                        try:
                            e_fields = _parse_event(raw_e)
                        except ParseError as e:
                            session.add(
                                SyncIssue(
                                    job_name=JOB_NAME,
                                    issue_type="PARSE_ERROR",
                                    external_id=f"{s_fields['external_package_id']}:<event>",
                                    details={"error": str(e), "section": "tracking_events"},
                                )
                            )
                            continue
                        _upsert_event(session, shipment_id=shipment_id, fields=e_fields)
                        events_written += 1
                        etms = e_fields.get("event_time_ms") or (
                            e_fields.get("event_at").timestamp() * 1000
                            if e_fields.get("event_at") is not None
                            else None
                        )
                        if isinstance(etms, (int, float)):
                            event_times_for_watermark.append(int(etms))
                # propagate event times to the outer watermark loop via fields
                s_fields["_event_times"] = event_times_for_watermark
            except UpstreamJobError as e:
                session.add(
                    SyncIssue(
                        job_name=JOB_NAME,
                        issue_type="UPSTREAM_NONZERO",
                        external_id=s_fields["external_package_id"],
                        details={"error": str(e), "section": "tracking_events"},
                    )
                )

        inserted += 1
        # Track the max update_time_ms we saw for the cursor advance.
        # Include both shipment times AND any tracking event times so we
        # don't miss late-arriving events on already-synced shipments.
        candidate_times: list[int] = []
        for ts in (s_fields.get("shipped_at"), s_fields.get("delivered_at")):
            if ts is None:
                continue
            candidate_times.append(int(ts.timestamp() * 1000))
        # tracking event times for this shipment (if any were fetched)
        for event_time_ms in (s_fields.get("_event_times") or []):
            candidate_times.append(int(event_time_ms))
        for ms in candidate_times:
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
    "SHIPMENTS_ENDPOINT",
    "TRACKING_ENDPOINT",
    "ProxyCall",
    "UpstreamJobError",
    "ParseError",
]
