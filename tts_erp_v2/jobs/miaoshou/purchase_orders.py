"""Miaoshou sync job: purchase_orders (1h cadence).

Syncs the **procurement-side purchase order** list from
``search_goods_purchase_order_page`` into ``procurement.purchase_orders``
+ ``procurement.purchase_order_lines``. Current production dataset is
**empty** (the operator hasn't enabled miaoshou purchasing); the job
must terminate cleanly on an empty-list path.

⚠ Parameter names differ from the legacy ``pageNo`` convention.
The upstream apifox spec uses ``page`` / ``pageSize`` (NOT ``pageNo``).
We send exactly what the upstream expects.

Endpoint
--------
``POST /open/v1/product/purchase/goods_purchase_order/search_goods_purchase_order_page``
(per refactor-tech-plan-v2.md §4.1 + §9.4). Body: ``{"page", "pageSize"}``.
Response shape (apifox): ``{"result":"success","data":{"goodsPurchaseOrderList":[...], "total": N}}``.

Output
------
* Raw payloads → ``integration.raw_records`` (one per page).
* Per-order header → ``procurement.purchase_orders`` (upsert by
  ``(procurement_account_id, external_purchase_order_id)``).
* Per-line → ``procurement.purchase_order_lines`` (upsert by
  ``(purchase_order_id, external_line_id)``). SKU → variant_id
  resolution is best-effort: missing products land in
  ``integration.sync_issues`` and the job continues.

Failure mode contract
---------------------
Empty pages → natural pagination end (no rows, no issues). Other
parse failures → ``sync_issues`` row, job continues. Upstream
failures → ``run_job`` → SyncJob status='failed', re-raise.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Protocol

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from tts_erp_v2.db.models.procurement import (
    ProcurementProduct,
    PurchaseOrder,
    PurchaseOrderLine,
)
from tts_erp_v2.jobs.miaoshou._common import (
    MiaoshouContext,
    resolve_miaoshou_context,
)
from tts_erp_v2.jobs.runner import record_raw_payload, record_sync_issue, run_job

log = logging.getLogger("tts_erp_v2.jobs.miaoshou.purchase_orders")

JOB_NAME = "miaoshou.purchase_orders"
ENDPOINT = "miaoshou.purchase_order.search_goods_purchase_order_page"
PAGE_SIZE = 50  # upstream default for this endpoint; no documented cap
MAX_PAGES = 1000


class _MiaoshouClientProto(Protocol):
    def _call_erp(self, *, path: str, body: dict | None = None, query: dict | None = None,
                  extra_headers: dict | None = None) -> dict[str, Any]: ...


def _fetch_page(
    client: _MiaoshouClientProto, *, page: int, page_size: int = PAGE_SIZE
) -> dict[str, Any]:
    """★ Note the upstream parameter names: ``page`` + ``pageSize`` (NOT pageNo)."""
    return client._call_erp(
        path="/open/v1/product/purchase/goods_purchase_order/search_goods_purchase_order_page",
        body={"page": page, "pageSize": page_size},
    )


def _parse_order_header(order: dict[str, Any]) -> dict[str, Any] | None:
    """Map a raw order dict → ``purchase_orders`` upsert values.

    Returns ``None`` when the order has no id — caller drops to issue.
    """
    oid = order.get("goodsPurchaseOrderId") or order.get("purchaseOrderId") or order.get("id")
    if not oid:
        return None
    return {
        "external_purchase_order_id": str(oid),
        "supplier_id": str(order.get("supplierId"))
        if order.get("supplierId") is not None
        else None,
        "status": order.get("status") or order.get("orderStatus"),
        "currency": order.get("currency"),
        "total_amount": _to_decimal(order.get("totalAmount") or order.get("amount")),
        "paid_at": _parse_iso(order.get("paidTime") or order.get("paidAt")),
        "completed_at": _parse_iso(
            order.get("completedTime") or order.get("completedAt")
        ),
        "source_created_at": _parse_iso(order.get("gmtCreate") or order.get("createTime")),
        "source_updated_at": _parse_iso(order.get("gmtModified") or order.get("updateTime")),
    }


def _parse_order_line(order_id_ext: str, line: dict[str, Any]) -> dict[str, Any] | None:
    """Map a raw order line → ``purchase_order_lines`` upsert values."""
    line_id = line.get("goodsPurchaseOrderLineId") or line.get("lineId") or line.get("id")
    if not line_id:
        return None
    return {
        "external_line_id": str(line_id),
        "external_product_id": str(line.get("goodsId") or line.get("productId") or ""),
        "quantity": _to_decimal(line.get("quantity") or line.get("qty")),
        "unit_cost": _to_decimal(line.get("unitPrice") or line.get("unitCost")),
        "currency": line.get("currency"),
        "line_status": line.get("status") or line.get("lineStatus"),
        "_order_external_id": order_id_ext,
    }


def _to_decimal(value: Any) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except Exception:  # noqa: BLE001
        return None


def _parse_iso(value: Any) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, str):
        s = value.strip().replace("T", " ").split(".")[0]
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
            try:
                naive = datetime.strptime(s, fmt)  # noqa: DTZ007 -- parsed as naive; tagged UTC below
                return naive.replace(tzinfo=timezone.utc)
            except ValueError:
                continue
    return None


def sync_purchase_orders(
    session: Session,
    *,
    client: _MiaoshouClientProto | None = None,
    license_id: str | None = None,
    max_retries: int = 3,
) -> dict[str, Any]:
    """Sync the miaoshou purchase-order list.

    Args:
        session: SQLAlchemy session (caller commits).
        client: optional fake / real ``MiaoshouErpClient``.
        license_id: explicit license id; falls back to env.

    Returns:
        Dict with ``pages_walked`` / ``orders_seen`` / ``orders_upserted`` /
        ``lines_upserted`` / ``rate_limit_retries`` / ``issues``.
    """
    from tts_erp_v2.proxy.miaoshou.retry import PageResult, paginate_with_retry

    with run_job(session, job_name=JOB_NAME) as job:
        # Always resolve ctx so we can attribute orders to the right
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
                "miaoshou.purchase_orders page retry attempt=%d err=%r",
                attempt, err,
            )

        def fetch_page(page: int) -> dict[str, Any]:
            return _fetch_page(client, page=page)  # type: ignore[arg-type]

        def unwrap_page(payload: dict[str, Any]) -> PageResult:
            data = (payload.get("data") or {}) if isinstance(payload, dict) else {}
            items = data.get("goodsPurchaseOrderList") or []
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

        orders_upserted = 0
        lines_upserted = 0
        issues = 0

        for order in items:
            if not isinstance(order, dict):
                record_sync_issue(
                    session,
                    job_name=JOB_NAME,
                    issue_type="PURCHASE_ORDER_PARSE_FAILED",
                    details={"order": repr(order)[:300]},
                )
                issues += 1
                continue
            try:
                header = _parse_order_header(order)
                if header is None:
                    record_sync_issue(
                        session,
                        job_name=JOB_NAME,
                        issue_type="PURCHASE_ORDER_MISSING_ID",
                        details={"order_keys": list(order.keys())[:10]},
                    )
                    issues += 1
                    continue

                # Raw audit row per order.
                record_raw_payload(
                    session,
                    endpoint=ENDPOINT,
                    payload=order,
                    external_id=header["external_purchase_order_id"],
                    credential_id=ctx.credentials.id if ctx else None,
                )

                assert ctx is not None
                order_row = _upsert_order_header(
                    session,
                    procurement_account_id=ctx.account_id,
                    parsed=header,
                )
                orders_upserted += 1

                for line in order.get("goodsPurchaseOrderLineList") or order.get("lines") or []:
                    line_parsed = _parse_order_line(
                        header["external_purchase_order_id"], line
                    )
                    if line_parsed is None:
                        record_sync_issue(
                            session,
                            job_name=JOB_NAME,
                            issue_type="PURCHASE_ORDER_LINE_MISSING_ID",
                            external_id=header["external_purchase_order_id"],
                            details={"line_keys": list(line.keys())[:10]}
                            if isinstance(line, dict)
                            else None,
                        )
                        issues += 1
                        continue
                    product_id = _resolve_product_id(
                        session,
                        procurement_account_id=ctx.account_id,
                        external_product_id=line_parsed["external_product_id"],
                    )
                    if product_id is None:
                        record_sync_issue(
                            session,
                            job_name=JOB_NAME,
                            issue_type="PURCHASE_ORDER_PRODUCT_UNKNOWN",
                            external_id=line_parsed["external_product_id"],
                            details={
                                "purchase_order_id": header["external_purchase_order_id"],
                            },
                        )
                        issues += 1
                        continue
                    _upsert_order_line(
                        session,
                        purchase_order_id=order_row.id,
                        procurement_product_id=product_id,
                        parsed=line_parsed,
                    )
                    lines_upserted += 1
            except Exception as e:  # noqa: BLE001
                record_sync_issue(
                    session,
                    job_name=JOB_NAME,
                    issue_type="PURCHASE_ORDER_PARSE_FAILED",
                    external_id=str(
                        order.get("goodsPurchaseOrderId")
                        or order.get("purchaseOrderId")
                        or order.get("id")
                    ),
                    details={"error": f"{type(e).__name__}: {e}"},
                )
                issues += 1

        job.rows_total = len(items)
        job.rows_inserted = orders_upserted + lines_upserted
        job.rows_failed = issues
        job.extra = {
            "pages_walked": last_page,
            "rate_limit_retries": rate_limit_retries,
            "finished_at_iso": datetime.now(timezone.utc).isoformat(),
        }
        return {
            "pages_walked": last_page,
            "orders_seen": len(items),
            "orders_upserted": orders_upserted,
            "lines_upserted": lines_upserted,
            "rate_limit_retries": rate_limit_retries,
            "issues": issues,
        }


# ---- upsert helpers -------------------------------------------------


def _upsert_order_header(
    session: Session,
    *,
    procurement_account_id: int,
    parsed: dict[str, Any],
) -> PurchaseOrder:
    values = {
        "procurement_account_id": procurement_account_id,
        "external_purchase_order_id": parsed["external_purchase_order_id"],
        "supplier_id": parsed.get("supplier_id"),
        "status": parsed.get("status"),
        "currency": parsed.get("currency"),
        "total_amount": parsed.get("total_amount"),
        "paid_at": parsed.get("paid_at"),
        "completed_at": parsed.get("completed_at"),
        "source_created_at": parsed.get("source_created_at"),
        "source_updated_at": parsed.get("source_updated_at"),
    }
    insert_stmt = pg_insert(PurchaseOrder).values(**values)
    upsert_stmt = insert_stmt.on_conflict_do_update(
        index_elements=["procurement_account_id", "external_purchase_order_id"],
        set_={k: values[k] for k in values if k not in ("procurement_account_id", "external_purchase_order_id")},
    )
    session.execute(upsert_stmt)
    row = session.execute(
        select(PurchaseOrder)
        .where(PurchaseOrder.procurement_account_id == procurement_account_id)
        .where(
            PurchaseOrder.external_purchase_order_id == parsed["external_purchase_order_id"]
        )
    ).scalar_one()
    return row


def _upsert_order_line(
    session: Session,
    *,
    purchase_order_id: int,
    procurement_product_id: int,
    parsed: dict[str, Any],
) -> PurchaseOrderLine:
    values = {
        "purchase_order_id": purchase_order_id,
        "external_line_id": parsed["external_line_id"],
        "procurement_product_id": procurement_product_id,
        "quantity": parsed.get("quantity"),
        "unit_cost": parsed.get("unit_cost"),
        "currency": parsed.get("currency"),
        "line_status": parsed.get("line_status"),
    }
    insert_stmt = pg_insert(PurchaseOrderLine).values(**values)
    upsert_stmt = insert_stmt.on_conflict_do_update(
        index_elements=["purchase_order_id", "external_line_id"],
        set_={
            "quantity": values["quantity"],
            "unit_cost": values["unit_cost"],
            "currency": values["currency"],
            "line_status": values["line_status"],
        },
    )
    session.execute(upsert_stmt)
    return session.execute(
        select(PurchaseOrderLine)
        .where(PurchaseOrderLine.purchase_order_id == purchase_order_id)
        .where(PurchaseOrderLine.external_line_id == parsed["external_line_id"])
    ).scalar_one()


def _resolve_product_id(
    session: Session,
    *,
    procurement_account_id: int,
    external_product_id: str | None,
) -> int | None:
    """Look up the procurement_products row id; ``None`` if missing."""
    if not external_product_id:
        return None
    return session.execute(
        select(ProcurementProduct.id)
        .where(ProcurementProduct.procurement_account_id == procurement_account_id)
        .where(ProcurementProduct.external_product_id == external_product_id)
    ).scalar_one_or_none()


__all__ = ["ENDPOINT", "JOB_NAME", "sync_purchase_orders"]
