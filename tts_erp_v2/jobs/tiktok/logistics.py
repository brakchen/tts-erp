"""tiktok.logistics — tracking events for shipped orders.

Verified live on 2026-08-30 against the production shop:

* TikTok 202309 has **no** ``/order/202309/orders/shipments`` list
  endpoint — that path returns ``UpstreamHttpError: Invalid path``.
  The legacy endpoint that works is ``GET /fulfillment/202309/orders/
  {order_id}/tracking`` → ``{ code, data: { tracking: [ {action_code,
  description, update_time_millis} ] } }``.
* The orders-search payload already carries ``tracking_number`` /
  ``shipping_provider_id`` / ``packages``, so this job sources its
  targets from ``integration.raw_records`` (the raw orders payloads)
  instead of a non-existent shipments list.

Data flow
---------
1. Select candidate orders from ``integration.raw_records`` whose
   payload has a ``tracking_number`` and whose order is not already in a
   terminal logistics state (DELIVERED / RETURNED_TO_SELLER).
2. For each candidate: ``GET /fulfillment/202309/orders/{id}/tracking``.
3. Upsert ``fulfillment.shipments`` (one row per package) and
   ``fulfillment.tracking_events`` (one row per event; most-recent wins
   via the unique ``(shipment_id, external_event_key)`` constraint).
4. Advance the cursor watermark to the max ``update_time_millis`` seen.

Timestamps
----------
TikTok tracking events use **epoch milliseconds** (``update_time_millis``);
we convert at the boundary to UTC ``datetime`` for storage (V3 §14).
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

#: Confirmed live 2026-08-30: this path returns code 0 + data.tracking.
#: The old ``/fulfillment/202309/tracking/{tracking_number}`` shape 404s.
TRACKING_ENDPOINT_TEMPLATE = "/fulfillment/202309/orders/{order_id}/tracking"

#: Raw orders payloads carry tracking info; we source targets there.
ORDERS_ENDPOINT = "/order/202309/orders/search"

#: Terminal logistics states — tracking never changes after these.
FINAL_STATUSES: tuple[str, ...] = ("DELIVERED", "RETURNED_TO_SELLER")

#: Same cap as the legacy cron's LOGISTICS_TARGET_LIMIT.
TARGET_LIMIT = 300

ProxyCall = Callable[..., dict]


class UpstreamJobError(RuntimeError):
    pass


class ParseError(ValueError):
    pass


def _epoch_ms_to_utc(ms: int | None) -> datetime | None:
    if ms is None or ms <= 0:
        return None
    try:
        return datetime.fromtimestamp(int(ms) / 1000.0, tz=timezone.utc)
    except (TypeError, ValueError, OverflowError):
        return None


def _safe_int(value: Any, default: int = 0) -> int:
    """Coerce to int without raising; ``None``/garbage → ``default``."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _parse_event(raw: dict) -> dict:
    """Normalise one tracking event: {action_code, description,
    update_time_millis}. The event key is the description (TikTok sends
    no stable event id)."""
    ekey = raw.get("event_key") or raw.get("id") or raw.get("description")
    if not ekey:
        raise ParseError("event_key missing")
    return {
        "external_event_key": str(ekey),
        "action_code": raw.get("action_code"),
        "event_at": _epoch_ms_to_utc(raw.get("update_time_millis")),
        "description": raw.get("description"),
        "location": raw.get("location"),
    }


def _resolve_order_id(session: Session, account_id: int, order_id: str) -> int | None:
    return session.execute(
        select(SalesOrder.id).where(
            SalesOrder.shop_pk == account_id,
            SalesOrder.order_id == order_id,
        )
    ).scalar_one_or_none()


def _upsert_shipment(
    session: Session, *, order_pk: int, fields: dict, raw_record_id: int
) -> int:
    insert_values = {
        "order_pk": order_pk,
        **{k: v for k, v in fields.items() if k != "order_id"},
        "raw_record_id": raw_record_id,
    }
    update_cols = {k: insert_values[k] for k in fields if k != "order_id"}
    update_cols["raw_record_id"] = raw_record_id
    session.execute(
        pg_insert(Shipment).values(**insert_values).on_conflict_do_update(
            index_elements=["order_pk", "external_package_id"],
            set_=update_cols,
        )
    )
    row = session.execute(
        select(Shipment).where(
            Shipment.order_pk == order_pk,
            Shipment.external_package_id == fields["external_package_id"],
        )
    ).scalar_one()
    return row.id


def _upsert_event(session: Session, *, shipment_id: int, fields: dict) -> None:
    session.execute(
        pg_insert(TrackingEvent).values(
            shipment_id=shipment_id,
            **fields,
        ).on_conflict_do_update(
            index_elements=["shipment_id", "external_event_key"],
            set_={k: fields[k] for k in fields if k != "external_event_key"},
        )
    )


def _select_tracking_targets(session: Session, *, limit: int = TARGET_LIMIT) -> list[dict]:
    """Select orders that need a tracking refresh.

    Sources from ``integration.raw_records`` (the raw orders-search
    payloads) — the payload carries ``tracking_number`` /
    ``shipping_provider_id`` / ``packages``. Orders already in a
    terminal logistics state are excluded (their tracking never changes).
    """
    terminal_orders = (
        select(SalesOrder.order_id)
        .join(Shipment, Shipment.order_pk == SalesOrder.id)
        .where(Shipment.status.in_(FINAL_STATUSES))
    )
    rows = session.execute(
        select(RawRecord)
        .where(RawRecord.endpoint == ORDERS_ENDPOINT)
        .where(RawRecord.payload["id"].astext.not_in(terminal_orders))
        .order_by(RawRecord.captured_at.desc())
        .limit(limit)
    ).scalars().all()

    targets: list[dict] = []
    for row in rows:
        payload = row.payload if isinstance(row.payload, dict) else {}
        oid = payload.get("id") or payload.get("order_id")
        tracking = payload.get("tracking_number") or ""
        if not oid or not tracking:
            continue
        packages = payload.get("packages") or []
        pkg_id = packages[0].get("id") if packages and isinstance(packages[0], dict) else None
        targets.append(
            {
                "order_id": str(oid),
                "tracking_number": tracking,
                "external_package_id": str(pkg_id) if pkg_id else str(oid),
                "provider_id": payload.get("shipping_provider_id"),
                "provider_name": payload.get("shipping_provider_name"),
            }
        )
    return targets


def _classify_status(action_codes: list[int | None]) -> str | None:
    """Derive a shipment status from the tracking event action codes.

    Mirrors the legacy classification in tts_erp.py (5-digit action codes
    only; 6-digit special codes like 110101/cancel are ignored).
    """
    codes = [c for c in action_codes if isinstance(c, int) and 10000 <= c <= 99999]
    if not codes:
        return None
    if 50101 in codes:
        return "DELIVERED"
    if 80101 in codes:
        return "RETURNED_TO_SELLER"
    if any(70000 <= c <= 79999 for c in codes):
        return "DELIVERY_FAILED"
    if any(38301 <= c <= 39999 for c in codes) or any(
        40000 <= c <= 49999 for c in codes
    ):
        return "ARRIVED_DEST"
    if any(34301 <= c <= 38299 for c in codes):
        return "CROSS_BORDER"
    if any(30201 <= c <= 34299 for c in codes):
        return "IN_ORIGIN"
    if 20101 in codes:
        return "AWAITING_PICKUP"
    return "PRE_PICKUP"


def run(
    session: Session,
    *,
    proxy_call: ProxyCall,
    shop_id: str,
    page_size: int = 100,
    scope: str | None = None,
    fetch_events: bool = True,
) -> JobResult:
    """Sync tracking events for shipped orders of one shop.

    Args:
        session: SQLAlchemy session (the helper commits it).
        proxy_call: callable doing signed GET + token injection.
        shop_id: TikTok shop id (shop_id).
        page_size: cap on how many orders to process this tick
            (mapped to TARGET_LIMIT).
        scope: cursor scope (defaults to ``shop_id``).
        fetch_events: when False, only upsert the shipment rows from the
            order payload without calling the tracking endpoint.

    Returns:
        :class:`JobResult` with rows_total / rows_inserted / rows_failed
        and the max event watermark (epoch ms).
    """
    from tts_erp_v2.sync_worker import watermarks

    cursor_scope = scope or shop_id
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

    watermark_ms = watermarks.get_cursor(
        session, job_name=JOB_NAME, scope=cursor_scope
    )
    limit = page_size if page_size and page_size > 0 else TARGET_LIMIT
    targets = _select_tracking_targets(session, limit=limit)

    total = 0
    inserted = 0
    failed = 0
    events_written = 0
    max_update_ms: int | None = None

    for target in targets:
        total += 1
        oid = target["order_id"]
        so_id = _resolve_order_id(session, account.id, oid)
        if so_id is None:
            session.add(
                SyncIssue(
                    job_name=JOB_NAME,
                    issue_type="UNKNOWN_ORDER",
                    external_id=target["external_package_id"],
                    details={"order_id": oid},
                )
            )
            failed += 1
            continue

        event_times_ms: list[int] = []
        action_codes: list[int | None] = []
        raw_tracking: list[dict] = []
        if fetch_events:
            try:
                resp = proxy_call(
                    "GET",
                    TRACKING_ENDPOINT_TEMPLATE.format(order_id=oid),
                    body=None,
                )
                if resp.get("code") == 0:
                    raw_tracking = (resp.get("data") or {}).get("tracking") or []
                else:
                    session.add(
                        SyncIssue(
                            job_name=JOB_NAME,
                            issue_type="UPSTREAM_NONZERO",
                            external_id=target["external_package_id"],
                            details={
                                "error": f"code={resp.get('code')} "
                                f"msg={resp.get('message')!r}",
                                "section": "tracking",
                            },
                        )
                    )
            except UpstreamJobError as e:
                session.add(
                    SyncIssue(
                        job_name=JOB_NAME,
                        issue_type="UPSTREAM_NONZERO",
                        external_id=target["external_package_id"],
                        details={"error": str(e), "section": "tracking"},
                    )
                )
                failed += 1
                continue

        raw_row = RawRecord(
            endpoint=TRACKING_ENDPOINT_TEMPLATE.format(order_id=oid),
            external_id=target["external_package_id"],
            payload=raw_tracking,
        )
        session.add(raw_row)
        session.flush()

        s_fields = {
            "external_package_id": target["external_package_id"],
            "order_id": oid,
            "tracking_number": target["tracking_number"],
            "provider_id": target["provider_id"],
            "provider_name": target["provider_name"],
            "status": None,
            "shipped_at": None,
            "delivered_at": None,
        }
        shipment_id = _upsert_shipment(
            session,
            order_pk=so_id,
            fields=s_fields,
            raw_record_id=raw_row.id,
        )

        for raw_e in raw_tracking:
            try:
                e_fields = _parse_event(raw_e)
            except ParseError as e:
                session.add(
                    SyncIssue(
                        job_name=JOB_NAME,
                        issue_type="PARSE_ERROR",
                        external_id=f"{target['external_package_id']}:<event>",
                        details={"error": str(e), "section": "tracking_events"},
                    )
                )
                continue
            _upsert_event(session, shipment_id=shipment_id, fields=e_fields)
            events_written += 1
            action_codes.append(e_fields.get("action_code"))
            if e_fields.get("event_at") is not None:
                event_times_ms.append(
                    _safe_int(e_fields["event_at"].timestamp() * 1000)
                )

        # Derive + persist the shipment status from the events we just
        # wrote (most-recent-event semantics are covered by the upsert).
        status = _classify_status(action_codes)
        if status is not None:
            session.execute(
                pg_insert(Shipment)
                .values(
                    order_pk=so_id,
                    external_package_id=target["external_package_id"],
                    status=status,
                )
                .on_conflict_do_update(
                    index_elements=["order_pk", "external_package_id"],
                    set_={"status": status},
                )
            )

        inserted += 1
        for ms in event_times_ms:
            if max_update_ms is None or ms > max_update_ms:
                max_update_ms = ms

    new_cursor_ms: int | None = None
    if max_update_ms is not None and (
        watermark_ms is None
        or max_update_ms > _safe_int(watermark_ms)
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
    "TRACKING_ENDPOINT_TEMPLATE",
    "FINAL_STATUSES",
    "TARGET_LIMIT",
    "ProxyCall",
    "UpstreamJobError",
    "ParseError",
]
