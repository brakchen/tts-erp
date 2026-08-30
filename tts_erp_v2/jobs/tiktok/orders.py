"""tiktok.orders job — incremental order sync.

Wave 3 sync-worker job. Pulls new / updated orders from the TikTok Shop
Order API and lands them in ``commerce.sales_orders`` /
``commerce.sales_order_lines``. Raw JSON lives in
``integration.raw_records``; parse failures flow into
``integration.sync_issues`` (NEVER block the main loop).

Incremental strategy
--------------------
The cursor is ``integration.sync_cursors.cursor_epoch_ms`` for
``scope=shop_id`` (or ``scope='*'`` for system-wide). The upstream API
expects ``update_time_ge`` in **seconds**, while the cursor stores
**milliseconds**; the job converts at the boundary in both directions
so the storage unit stays canonical (ms, matches the v3 data-model
convention used by the migration scripts).

Pagination
----------
Uses ``next_page_token`` per the 202309 spec. The job keeps paging
until the upstream returns an empty / falsy token. If a page fails to
parse, we record an issue and continue with the next page.

Reentrancy / idempotency
------------------------
All inserts go through ``INSERT ... ON CONFLICT DO UPDATE`` keyed on
the natural ``(channel_account_id, external_*)`` constraints. A second
run on the same window updates existing rows instead of duplicating.

Proxy contract
--------------
The job receives ``proxy_call(method, path, *, body) -> dict`` so it
can be exercised end-to-end in tests with a fake. The production
caller (scheduler / CLI) wraps ``TiktokShopClient`` + ``token_service``
in a thin adapter that does the actual signing + token injection.
"""
from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from datetime import datetime, timezone
from decimal import Decimal
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
from tts_erp_v2.sync_worker.job_runner import JobResult

# Upstream path (TikTok 202309 spec). Frozen string so it appears
# verbatim in raw_records.endpoint and in logs.
ENDPOINT = "/order/202309/orders/search"
JOB_NAME = "tiktok.orders"

#: Type alias for the proxy callable the job uses. Production wraps
#: :class:`tts_erp_v2.proxy.tts_shop.client.TiktokShopClient`; tests
#: use a fake.
ProxyCall = Callable[..., dict]


class UpstreamJobError(RuntimeError):
    """Upstream returned a non-zero code. Surfaces as sync_jobs.status='failed'."""


class ParseError(ValueError):
    """A row from the upstream could not be coerced into the normalized shape."""


# ─── Time conversion helpers (epoch seconds ↔ epoch milliseconds) ──


def _safe_int(value: Any, default: int = 0) -> int:
    """Coerce to int without raising; ``None``/garbage → ``default``.

    Defensive wrapper for epoch conversions — upstream fields and
    stored watermarks are integers in practice, but a stray string or
    float should degrade to ``default`` instead of crashing the whole
    job page.
    """
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _epoch_seconds_to_utc(seconds: int | None) -> datetime | None:
    if seconds is None or seconds <= 0:
        return None
    return datetime.fromtimestamp(_safe_int(seconds), tz=timezone.utc)


def _epoch_ms_to_seconds(ms: int) -> int:
    return _safe_int(ms) // 1000


# ─── Channel-account resolution ──────────────────────────────────


def _ensure_channel_account(session: Session, shop_id: str) -> ChannelAccount:
    """Return the ChannelAccount for ``(platform='tiktok', shop_id)``.

    If the row doesn't exist, this job does NOT create one — the
    upstream /authorize + /callback flow (Lane E) owns account
    bootstrapping. We raise so a missing account surfaces as a hard
    failure rather than silently landing rows under the wrong account.
    """
    row = session.execute(
        select(ChannelAccount).where(
            ChannelAccount.platform == "tiktok",
            ChannelAccount.external_account_id == shop_id,
        )
    ).scalar_one_or_none()
    if row is None:
        raise UpstreamJobError(
            f"channel_accounts row missing for tiktok shop_id={shop_id!r}; "
            "complete /authorize + /callback first"
        )
    return row


# ─── raw_records write ────────────────────────────────────────────


def _store_raw(
    session: Session,
    *,
    endpoint: str,
    external_id: str | None,
    payload: dict,
) -> RawRecord:
    """Insert a raw_records row and return the new ORM instance.

    Caller is expected to be inside an outer transaction managed by
    :func:`run_with_sync_job`; we commit only when the job commits.
    """
    payload_bytes = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode(
        "utf-8"
    )
    payload_hash = hashlib.sha256(payload_bytes).hexdigest()
    row = RawRecord(
        endpoint=endpoint,
        external_id=external_id,
        payload=payload,
        payload_hash=payload_hash,
    )
    session.add(row)
    session.flush()
    return row


# ─── Sync-issues write ────────────────────────────────────────────


def _record_issue(
    session: Session,
    *,
    job_name: str,
    issue_type: str,
    external_id: str | None,
    details: dict,
) -> None:
    session.add(
        SyncIssue(
            job_name=job_name,
            issue_type=issue_type,
            external_id=external_id,
            details=details,
        )
    )


# ─── Parsers (raw → normalized row payload) ───────────────────────


def _parse_order_payload(raw: dict[str, Any]) -> dict[str, Any]:
    """Validate + normalize a single order item.

    Returns a dict ready to be upserted into ``commerce.sales_orders``
    (channel_account_id is filled in by the caller). Raises
    :class:`ParseError` when the row cannot be coerced (missing
    required fields).

    TikTok 202309 field mapping (verified 2026-08-30 against live
    /order/202309/orders/search responses):

    * order id: ``id`` (NOT ``order_id``)
    * money: nested ``payment`` object — ``payment.total_amount`` /
      ``payment.sub_total`` / ``payment.original_total_product_price``
      (there is NO ``payment_amount`` on the order anymore)
    * ship timestamp: ``rts_time`` (NOT ``ship_time``)
    """
    order_id = raw.get("order_id") or raw.get("id")
    if not order_id:
        raise ParseError("order_id missing from upstream order")
    update_time_s = raw.get("update_time")
    if update_time_s is None:
        raise ParseError(f"update_time missing from order {order_id}")
    payment = raw.get("payment") or {}
    # TikTok 202309: the payable total lives on the nested ``payment``
    # object. Legacy v1 code read ``payment_amount.amount`` — that
    # shape no longer exists.
    payable = payment.get("total_amount") or payment.get("sub_total")
    return {
        "external_order_id": str(order_id),
        "status": raw.get("status") or raw.get("order_status"),
        "currency": raw.get("currency") or payment.get("currency"),
        "payment_amount": _to_decimal(payable),
        "total_amount": _to_decimal(
            payment.get("total_amount")
            or payment.get("sub_total")
            or (raw.get("total_amount") or {}).get("amount")
        ),
        "fulfillment_type": raw.get("fulfillment_type"),
        "source_created_at": _epoch_seconds_to_utc(raw.get("create_time")),
        "source_updated_at": _epoch_seconds_to_utc(update_time_s),
        "paid_at": _epoch_seconds_to_utc(raw.get("paid_time")),
        "shipped_at": _epoch_seconds_to_utc(
            raw.get("rts_time") or raw.get("ship_time")
        ),
        "delivered_at": _epoch_seconds_to_utc(
            raw.get("delivered_time") or raw.get("delivery_time")
        ),
        "cancelled_at": _epoch_seconds_to_utc(
            raw.get("cancel_time") or raw.get("cancelled_time")
        ),
    }


def _parse_line_payload(order_id: str, raw: dict[str, Any]) -> dict[str, Any]:
    line_id = raw.get("line_id") or raw.get("id")
    if not line_id:
        raise ParseError(f"line_id missing from order {order_id} line")
    # TikTok 202309: ``sale_price`` is a plain numeric string
    # (e.g. "553169"), NOT a ``{"amount": ...}`` object.
    sale_price_raw = raw.get("sale_price")
    if isinstance(sale_price_raw, dict):
        sale_price = sale_price_raw.get("amount")
        currency = sale_price_raw.get("currency") or raw.get("currency")
    else:
        sale_price = sale_price_raw
        currency = raw.get("currency")
    return {
        "external_line_id": str(line_id),
        # channel_product_id / channel_product_variant_id stay NULL
        # when the product hasn't been synced yet — the snapshot
        # columns hold the truth for later join.
        "external_product_id_snapshot": raw.get("product_id"),
        "external_variant_id_snapshot": raw.get("sku_id"),
        "product_name_snapshot": raw.get("product_name"),
        "variant_name_snapshot": raw.get("sku_name"),
        "image_url_snapshot": raw.get("sku_image") or raw.get("product_image_url"),
        "quantity": _to_decimal(raw.get("quantity")),
        "unit_price": _to_decimal(sale_price),
        "currency": currency,
        "line_status": raw.get("display_status") or raw.get("line_status"),
    }


def _to_decimal(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value))
    except Exception:  # noqa: BLE001 — coerce any garbage to None rather than crashing the page
        return None


# ─── Upserts ──────────────────────────────────────────────────────


def _upsert_sales_order(
    session: Session,
    *,
    channel_account_id: int,
    fields: dict[str, Any],
    raw_record_id: int,
) -> SalesOrder:
    insert_values = {
        "channel_account_id": channel_account_id,
        **fields,
        "raw_record_id": raw_record_id,
    }
    stmt = pg_insert(SalesOrder).values(**insert_values)
    update_cols = {k: insert_values[k] for k in fields}
    update_cols["raw_record_id"] = raw_record_id
    stmt = stmt.on_conflict_do_update(
        index_elements=["channel_account_id", "external_order_id"],
        set_=update_cols,
    )
    session.execute(stmt)
    row = session.execute(
        select(SalesOrder).where(
            SalesOrder.channel_account_id == channel_account_id,
            SalesOrder.external_order_id == fields["external_order_id"],
        )
    ).scalar_one()
    return row


def _upsert_sales_order_line(
    session: Session,
    *,
    sales_order_id: int,
    fields: dict[str, Any],
    raw_record_id: int,
) -> SalesOrderLine:
    insert_values = {
        "sales_order_id": sales_order_id,
        **fields,
        "raw_record_id": raw_record_id,
    }
    stmt = pg_insert(SalesOrderLine).values(**insert_values)
    update_cols = {k: insert_values[k] for k in fields}
    update_cols["raw_record_id"] = raw_record_id
    stmt = stmt.on_conflict_do_update(
        index_elements=["sales_order_id", "external_line_id"],
        set_=update_cols,
    )
    session.execute(stmt)
    row = session.execute(
        select(SalesOrderLine).where(
            SalesOrderLine.sales_order_id == sales_order_id,
            SalesOrderLine.external_line_id == fields["external_line_id"],
        )
    ).scalar_one()
    return row


# ─── Page walk ────────────────────────────────────────────────────


def _walk_pages(
    proxy_call: ProxyCall,
    *,
    base_body: dict[str, Any],
) -> list[dict[str, Any]]:
    """Return every order across every page (driven by next_page_token).

    Raises :class:`UpstreamJobError` if any page returns ``code != 0``.
    """
    collected: list[dict[str, Any]] = []
    next_token: str | None = None
    body = dict(base_body)
    while True:
        page_body = dict(body)
        if next_token:
            page_body["next_page_token"] = next_token
        resp = proxy_call("POST", ENDPOINT, body=page_body)
        code = resp.get("code", -1)
        if code != 0:
            raise UpstreamJobError(
                f"upstream returned non-zero code={code} message={resp.get('message')!r}"
            )
        data = resp.get("data") or {}
        orders = data.get("orders") or []
        collected.extend(orders)
        next_token = data.get("next_page_token") or None
        if not next_token:
            break
    return collected


# ─── Main job entry ───────────────────────────────────────────────


def run(
    session: Session,
    *,
    proxy_call: ProxyCall,
    shop_id: str,
    page_size: int = 100,
    scope: str | None = None,
) -> JobResult:
    """Sync orders for one shop. See module docstring for behaviour.

    Args:
        session: SQLAlchemy session (the helper commits it).
        proxy_call: callable doing signed POST + token injection.
        shop_id: TikTok shop id (external_account_id).
        page_size: page size to ask the upstream for.
        scope: cursor scope (defaults to ``shop_id``).

    Returns:
        :class:`JobResult` with rows_total / rows_inserted / rows_failed
        and the new watermark value (in epoch ms).
    """
    from tts_erp_v2.sync_worker import watermarks

    cursor_scope = scope or shop_id
    account = _ensure_channel_account(session, shop_id)
    watermark_ms = watermarks.get_cursor(
        session, job_name=JOB_NAME, scope=cursor_scope
    )

    base_body: dict[str, Any] = {"page_size": page_size}
    if watermark_ms:
        base_body["update_time_ge"] = _epoch_ms_to_seconds(
            _safe_int(watermark_ms)
        )

    raw_orders = _walk_pages(proxy_call, base_body=base_body)

    rows_total = 0
    rows_inserted = 0
    rows_failed = 0
    max_update_time_ms: int | None = None

    for raw in raw_orders:
        rows_total += 1
        try:
            fields = _parse_order_payload(raw)
        except ParseError as exc:
            rows_failed += 1
            _record_issue(
                session,
                job_name=JOB_NAME,
                issue_type="PARSE_ERROR",
                external_id=str(raw.get("order_id") or "<unknown>"),
                details={"error": str(exc), "raw": _safe_truncate(raw)},
            )
            continue

        order_external_id = fields["external_order_id"]
        raw_row = _store_raw(
            session,
            endpoint=ENDPOINT,
            external_id=order_external_id,
            payload=raw,
        )

        sales_order = _upsert_sales_order(
            session,
            channel_account_id=account.id,
            fields=fields,
            raw_record_id=raw_row.id,
        )

        for raw_line in raw.get("line_items") or []:
            try:
                line_fields = _parse_line_payload(order_external_id, raw_line)
            except ParseError as exc:
                _record_issue(
                    session,
                    job_name=JOB_NAME,
                    issue_type="PARSE_ERROR",
                    external_id=f"{order_external_id}:{raw_line.get('line_id')}",
                    details={
                        "error": str(exc),
                        "raw": _safe_truncate(raw_line),
                        "order_external_id": order_external_id,
                    },
                )
                continue
            _upsert_sales_order_line(
                session,
                sales_order_id=sales_order.id,
                fields=line_fields,
                raw_record_id=raw_row.id,
            )

        rows_inserted += 1
        update_ms = (
            fields.get("source_updated_at")
            and _safe_int(fields["source_updated_at"].timestamp() * 1000)
        )
        if update_ms and (max_update_time_ms is None or update_ms > max_update_time_ms):
            max_update_time_ms = update_ms

    # Advance the watermark only when we actually saw new data. An
    # empty-page run leaves the watermark untouched (otherwise we'd
    # spuriously reset it to 0).
    new_cursor_ms: int | None = None
    if max_update_time_ms is not None and (
        watermark_ms is None
        or max_update_time_ms > _safe_int(watermark_ms)
    ):
        watermarks.set_cursor(
            session,
            job_name=JOB_NAME,
            scope=cursor_scope,
            cursor_epoch_ms=max_update_time_ms,
        )
        new_cursor_ms = max_update_time_ms

    return JobResult(
        rows_total=rows_total,
        rows_inserted=rows_inserted,
        rows_failed=rows_failed,
        cursor=new_cursor_ms,
    )


def _safe_truncate(raw: Any, *, max_chars: int = 1000) -> Any:
    """Bound the size of raw blobs stashed in sync_issues.details.

    Keeps the issue row small (JSONB indexing cost) while still
    giving an operator enough to triage.
    """
    try:
        text = json.dumps(raw, ensure_ascii=False, default=str)
    except Exception:  # noqa: BLE001
        return {"_unserializable": str(type(raw))}
    if len(text) <= max_chars:
        return raw
    return {"_truncated": True, "preview": text[:max_chars]}


__all__ = [
    "run",
    "ENDPOINT",
    "JOB_NAME",
    "ProxyCall",
    "UpstreamJobError",
    "ParseError",
]
