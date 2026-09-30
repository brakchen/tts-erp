"""Synchronize Miaoshou packages into ``fulfillment.shipments``.

The scheduled path uses ``search_package_list`` with a per-credential modified-
time watermark. ``sync_package_detail`` exposes the companion
``get_package_info`` endpoint for one-package repair/enrichment without adding a
public HTTP route.

Miaoshou does not return the TikTok ``line_id`` stored by
``commerce.sales_order_lines``. Its ``platformOrderItemIndex`` is the platform
SKU id in live responses, so package item membership is retained in the raw
record rather than guessed into ``fulfillment.shipment_lines``.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from tts_erp_v2.db.models.commerce import SalesOrder
from tts_erp_v2.db.models.fulfillment import Shipment
from tts_erp_v2.db.models.integration import SyncCursor, SyncIssue
from tts_erp_v2.jobs.miaoshou._common import resolve_miaoshou_context
from tts_erp_v2.jobs.runner import record_raw_payload, record_sync_issue, run_job

log = logging.getLogger("tts_erp_v2.jobs.miaoshou.packages")

JOB_NAME = "miaoshou.packages"
DETAIL_JOB_NAME = "miaoshou.package_detail"
SEARCH_ENDPOINT = "miaoshou.package.search_package_list"
DETAIL_ENDPOINT = "miaoshou.package.get_package_info"
SEARCH_PATH = "/open/v1/order/package/fetch/search_package_list"
DETAIL_PATH = "/open/v1/order/package/fetch/get_package_info"
PAGE_SIZE = 100
MAX_PAGES = 1000
MAX_PENDING_RETRIES = 100
_CURSOR_OVERLAP = timedelta(minutes=5)
_MIAOSHOU_TZ = ZoneInfo("Asia/Shanghai")
_RETRYABLE_ISSUES = ("PACKAGE_ORDER_UNKNOWN", "PACKAGE_PARSE_FAILED")


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
    client: _MiaoshouClientProto,
    *,
    page: int,
    page_size: int = PAGE_SIZE,
    gmt_modified_from: str | None = None,
) -> dict[str, Any]:
    body: dict[str, Any] = {"page": page, "pageSize": page_size}
    if gmt_modified_from is not None:
        body["gmtModifiedFrom"] = gmt_modified_from
    return client._call_erp(path=SEARCH_PATH, body=body)


def _fetch_detail(
    client: _MiaoshouClientProto, *, op_order_package_id: int | str
) -> dict[str, Any]:
    return client._call_erp(
        path=DETAIL_PATH,
        body={"opOrderPackageId": op_order_package_id},
    )


def _unwrap_detail(payload: dict[str, Any]) -> dict[str, Any] | None:
    data = payload.get("data")
    if isinstance(data, list):
        data = next((row for row in data if isinstance(row, dict)), None)
    if not isinstance(data, dict):
        return None
    info = data.get("orderPackageInfo")
    if not isinstance(info, dict):
        return None
    if set(info) == {""} and isinstance(info[""], dict):
        return info[""]
    return info


def _parse_datetime(value: Any) -> datetime | None:
    """Parse Miaoshou wall-clock timestamps as China time and store UTC."""
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        normalized = value.strip().replace("T", " ").split(".")[0]
        parsed = None
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
            try:
                parsed = datetime.strptime(normalized, fmt).replace(tzinfo=_MIAOSHOU_TZ)
                break
            except ValueError:
                continue
        if parsed is None:
            return None
    else:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=_MIAOSHOU_TZ)
    return parsed.astimezone(UTC)


def _parse_package(package: dict[str, Any]) -> dict[str, Any] | None:
    package_id = package.get("opOrderPackageId")
    order_info = package.get("orderInfo")
    if package_id in (None, "") or not isinstance(order_info, dict):
        return None
    order_id = order_info.get("platformOrderSn")
    if order_id in (None, ""):
        return None

    logistics = package.get("logisticsAgentProductInfo")
    if not isinstance(logistics, dict) or not logistics:
        logistics = package.get("opOrderPackageToPlatformLastMile")
    if not isinstance(logistics, dict):
        logistics = {}

    provider_id = logistics.get("logisticsAgentProductId")
    if provider_id in (None, ""):
        provider_id = logistics.get("logisticsAgentId")
    provider_name = logistics.get("logisticsCompany") or logistics.get("productName")
    tracking_number = (
        package.get("logisticsNo")
        or logistics.get("logisticsNo")
        or logistics.get("platformPackageNo")
    )

    provided_fields: set[str] = set()
    if "logisticsNo" in package or any(
        key in logistics for key in ("logisticsNo", "platformPackageNo")
    ):
        provided_fields.add("tracking_number")
    if any(key in logistics for key in ("logisticsAgentProductId", "logisticsAgentId")):
        provided_fields.add("provider_id")
    if any(key in logistics for key in ("logisticsCompany", "productName")):
        provided_fields.add("provider_name")
    if "appPackageStatus" in package or "platformPackageStatus" in package:
        provided_fields.add("status")
    if "gmtDelivery" in order_info:
        provided_fields.add("shipped_at")

    return {
        "external_package_id": str(package_id),
        "external_order_id": str(order_id),
        "tracking_number": str(tracking_number) if tracking_number else None,
        "provider_id": str(provider_id) if provider_id not in (None, "") else None,
        "provider_name": str(provider_name) if provider_name else None,
        "status": package.get("appPackageStatus")
        or package.get("platformPackageStatus"),
        "shipped_at": _parse_datetime(order_info.get("gmtDelivery")),
        "source_updated_at": _parse_datetime(order_info.get("gmtOrderModified")),
        "items_seen": sum(
            1 for item in package.get("items") or [] if isinstance(item, dict)
        ),
        "provided_fields": provided_fields,
    }


def _cursor_scope(credential_id: int) -> str:
    return f"credential:{credential_id}"


def _load_modified_cursor(session: Session, *, scope: str) -> str | None:
    value = session.execute(
        select(SyncCursor.cursor_value)
        .where(SyncCursor.job_name == JOB_NAME)
        .where(SyncCursor.scope == scope)
    ).scalar_one_or_none()
    parsed = _parse_datetime(value)
    if parsed is None:
        return value
    overlapped = parsed - _CURSOR_OVERLAP
    return overlapped.astimezone(_MIAOSHOU_TZ).strftime("%Y-%m-%d %H:%M:%S")


def _save_modified_cursor(session: Session, *, scope: str, value: datetime) -> None:
    local_value = value.astimezone(_MIAOSHOU_TZ).strftime("%Y-%m-%d %H:%M:%S")
    try:
        cursor_epoch_ms = int(value.timestamp() * 1000)
    except (OverflowError, ValueError):
        cursor_epoch_ms = None
    stmt = pg_insert(SyncCursor).values(
        job_name=JOB_NAME,
        scope=scope,
        cursor_value=local_value,
        cursor_epoch_ms=cursor_epoch_ms,
    )
    session.execute(
        stmt.on_conflict_do_update(
            index_elements=["job_name", "scope"],
            set_={
                "cursor_value": local_value,
                "cursor_epoch_ms": cursor_epoch_ms,
                "updated_at": datetime.now(UTC),
            },
        )
    )


def _resolve_order_pk(
    session: Session, external_order_id: str
) -> tuple[int | None, str | None]:
    rows = (
        session.execute(
            select(SalesOrder.id).where(SalesOrder.order_id == external_order_id)
        )
        .scalars()
        .all()
    )
    if not rows:
        return None, "missing"
    if len(rows) > 1:
        return None, "ambiguous"
    return rows[0], None


def _issue_external_id(credential_id: int | None, package_id: str) -> str:
    return f"{credential_id or 'unknown'}:{package_id}"


def _resolve_package_issues(
    session: Session,
    *,
    credential_id: int | None,
    external_package_id: str,
    resolved_at: datetime,
) -> None:
    issues = session.execute(
        select(SyncIssue)
        .where(SyncIssue.job_name.in_((JOB_NAME, DETAIL_JOB_NAME)))
        .where(SyncIssue.issue_type.in_(_RETRYABLE_ISSUES))
        .where(
            SyncIssue.external_id
            == _issue_external_id(credential_id, external_package_id)
        )
        .where(SyncIssue.resolved_at.is_(None))
    ).scalars()
    for issue in issues:
        issue.resolved_at = resolved_at


def _upsert_shipment(
    session: Session,
    *,
    order_pk: int,
    parsed: dict[str, Any],
    raw_record_id: int,
) -> Shipment:
    values = {
        "order_pk": order_pk,
        "external_package_id": parsed["external_package_id"],
        "tracking_number": parsed.get("tracking_number"),
        "provider_id": parsed.get("provider_id"),
        "provider_name": parsed.get("provider_name"),
        "status": parsed.get("status"),
        "shipped_at": parsed.get("shipped_at"),
        "raw_record_id": raw_record_id,
        "synced_at": datetime.now(UTC),
    }
    update_values = {
        "raw_record_id": raw_record_id,
        "synced_at": values["synced_at"],
    }
    update_values.update(
        {key: values[key] for key in parsed["provided_fields"] if key in values}
    )
    insert_stmt = pg_insert(Shipment).values(**values)
    session.execute(
        insert_stmt.on_conflict_do_update(
            index_elements=["order_pk", "external_package_id"],
            set_=update_values,
        )
    )
    return session.execute(
        select(Shipment)
        .where(Shipment.order_pk == order_pk)
        .where(Shipment.external_package_id == parsed["external_package_id"])
    ).scalar_one()


def _persist_package(
    session: Session,
    *,
    package: dict[str, Any],
    credential_id: int | None,
    endpoint: str,
    job_name: str,
) -> tuple[int, int, int]:
    package_id = package.get("opOrderPackageId")
    external_id = str(package_id) if package_id not in (None, "") else None
    raw = record_raw_payload(
        session,
        endpoint=endpoint,
        payload=package,
        external_id=external_id,
        credential_id=credential_id,
    )
    parsed = _parse_package(package)
    if parsed is None:
        record_sync_issue(
            session,
            job_name=job_name,
            issue_type="PACKAGE_PARSE_FAILED",
            external_id=(
                _issue_external_id(credential_id, external_id)
                if external_id is not None
                else None
            ),
            details={
                "package_id": external_id,
                "credential_id": credential_id,
                "package_keys": list(package.keys())[:30],
            },
        )
        return 0, 0, 1

    order_pk, reason = _resolve_order_pk(session, parsed["external_order_id"])
    if order_pk is None:
        record_sync_issue(
            session,
            job_name=job_name,
            issue_type="PACKAGE_ORDER_UNKNOWN",
            external_id=_issue_external_id(
                credential_id, parsed["external_package_id"]
            ),
            details={
                "package_id": parsed["external_package_id"],
                "credential_id": credential_id,
                "order_id": parsed["external_order_id"],
                "reason": reason,
            },
        )
        return 0, parsed["items_seen"], 1

    _upsert_shipment(
        session,
        order_pk=order_pk,
        parsed=parsed,
        raw_record_id=raw.id,
    )
    _resolve_package_issues(
        session,
        credential_id=credential_id,
        external_package_id=parsed["external_package_id"],
        resolved_at=datetime.now(UTC),
    )
    return 1, parsed["items_seen"], 0


def _retry_unresolved_packages(
    session: Session,
    *,
    client: _MiaoshouClientProto,
    credential_id: int,
    detected_before: datetime,
) -> tuple[int, int, int]:
    pending = (
        session.execute(
            select(SyncIssue)
            .where(SyncIssue.job_name == JOB_NAME)
            .where(SyncIssue.issue_type.in_(_RETRYABLE_ISSUES))
            .where(SyncIssue.resolved_at.is_(None))
            .where(SyncIssue.external_id.is_not(None))
            .where(SyncIssue.details["credential_id"].as_integer() == credential_id)
            .where(SyncIssue.detected_at < detected_before)
            .order_by(SyncIssue.detected_at, SyncIssue.id)
            .limit(MAX_PENDING_RETRIES)
        )
        .scalars()
        .all()
    )
    recovered = 0
    items_seen = 0
    retry_failures = 0
    for issue in pending:
        package_id = (issue.details or {}).get("package_id")
        if not package_id:
            retry_failures += 1
            continue
        try:
            payload = _fetch_detail(
                client,
                op_order_package_id=str(package_id),
            )
            package = _unwrap_detail(payload)
            if package is None:
                retry_failures += 1
                continue
            shipment_count, item_count, issue_count = _persist_package(
                session,
                package=package,
                credential_id=credential_id,
                endpoint=DETAIL_ENDPOINT,
                job_name=JOB_NAME,
            )
            recovered += shipment_count
            items_seen += item_count
            retry_failures += issue_count
        except Exception as exc:  # noqa: BLE001 -- one retry target must not block others
            retry_failures += 1
            log.warning(
                "miaoshou.packages pending retry failed package=%s err=%r",
                package_id,
                exc,
            )
    return recovered, items_seen, retry_failures


def sync_packages(
    session: Session,
    *,
    client: _MiaoshouClientProto | None = None,
    license_id: str | None = None,
    gmt_modified_from: str | None = None,
    max_retries: int = 3,
) -> dict[str, Any]:
    """Incrementally synchronize the package list into shipments."""
    from tts_erp_v2.proxy.miaoshou.retry import PageResult, paginate_with_retry

    with run_job(session, job_name=JOB_NAME) as job:
        ctx = resolve_miaoshou_context(session, license_id=license_id)
        if ctx is None:
            raise RuntimeError("no miaoshou credentials row; cannot construct context")
        if client is None:
            from tts_erp_v2.jobs.miaoshou._common import miaoshou_client_factory

            client = miaoshou_client_factory(ctx)

        scope = _cursor_scope(ctx.credentials.id)
        modified_from = gmt_modified_from
        if modified_from is None:
            modified_from = _load_modified_cursor(session, scope=scope)
        rate_limit_retries = 0
        advertised_total_pages: int | None = None
        advertised_total_count: int | None = None

        def _on_retry(attempt: int, err: BaseException) -> None:
            nonlocal rate_limit_retries
            rate_limit_retries += 1
            log.warning("miaoshou.packages page retry attempt=%d err=%r", attempt, err)

        def wrapped_fetch(page: int) -> PageResult:
            nonlocal advertised_total_count, advertised_total_pages
            payload = _fetch_page(
                client,  # type: ignore[arg-type]
                page=page,
                gmt_modified_from=modified_from,
            )
            data = payload.get("data") or {}
            if isinstance(data, list):
                pages = [row for row in data if isinstance(row, dict)]
                items = [
                    item for row in pages for item in row.get("orderPackageList") or []
                ]
                data = pages[-1] if pages else {}
            elif isinstance(data, dict):
                items = data.get("orderPackageList") or []
            else:
                items = []
                data = {}
            total = data.get("total")
            total_pages = None
            if isinstance(total, int):
                total_pages = (total + PAGE_SIZE - 1) // PAGE_SIZE
                advertised_total_count = total
                advertised_total_pages = total_pages
            return PageResult(
                items=list(items) if isinstance(items, list) else [],
                page=data.get("page") or page,
                total_count=total,
                total_pages=total_pages,
            )

        packages, last_page = paginate_with_retry(
            wrapped_fetch,
            start_page=1,
            max_pages=MAX_PAGES,
            max_retries=max_retries,
            on_retry=_on_retry,
        )
        if advertised_total_pages is not None and last_page < advertised_total_pages:
            raise RuntimeError(
                "package pagination incomplete: "
                f"walked={last_page} advertised={advertised_total_pages}"
            )
        if (
            advertised_total_count is not None
            and len(packages) < advertised_total_count
        ):
            raise RuntimeError(
                "package pagination row-count mismatch: "
                f"fetched={len(packages)} advertised={advertised_total_count}"
            )

        shipments_upserted = 0
        items_seen = 0
        issues = 0
        max_modified: datetime | None = None
        for package in packages:
            if not isinstance(package, dict):
                record_sync_issue(
                    session,
                    job_name=JOB_NAME,
                    issue_type="PACKAGE_PARSE_FAILED",
                    details={"package": repr(package)[:300]},
                )
                issues += 1
                continue
            shipment_count, item_count, issue_count = _persist_package(
                session,
                package=package,
                credential_id=ctx.credentials.id,
                endpoint=SEARCH_ENDPOINT,
                job_name=JOB_NAME,
            )
            shipments_upserted += shipment_count
            items_seen += item_count
            issues += issue_count
            order_info = package.get("orderInfo")
            if isinstance(order_info, dict):
                modified = _parse_datetime(order_info.get("gmtOrderModified"))
                if modified is not None and (
                    max_modified is None or modified > max_modified
                ):
                    max_modified = modified

        recovered, retried_items, retry_failures = _retry_unresolved_packages(
            session,
            client=client,  # type: ignore[arg-type]
            credential_id=ctx.credentials.id,
            detected_before=job.started_at,
        )
        shipments_upserted += recovered
        items_seen += retried_items
        issues += retry_failures

        if max_modified is not None:
            _save_modified_cursor(session, scope=scope, value=max_modified)

        job.rows_total = len(packages)
        job.rows_inserted = shipments_upserted
        job.rows_failed = issues
        job.extra = {
            "pages_walked": last_page,
            "modified_from": modified_from,
            "cursor_scope": scope,
            "max_modified": max_modified.isoformat() if max_modified else None,
            "items_seen": items_seen,
            "pending_recovered": recovered,
            "rate_limit_retries": rate_limit_retries,
            "finished_at_iso": datetime.now(UTC).isoformat(),
        }
        return {
            "pages_walked": last_page,
            "packages_seen": len(packages),
            "shipments_upserted": shipments_upserted,
            "items_seen": items_seen,
            "pending_recovered": recovered,
            "rate_limit_retries": rate_limit_retries,
            "issues": issues,
        }


def sync_package_detail(
    session: Session,
    *,
    op_order_package_id: int | str,
    client: _MiaoshouClientProto | None = None,
    license_id: str | None = None,
) -> dict[str, Any]:
    """Fetch and persist one package via ``get_package_info``."""
    with run_job(session, job_name=DETAIL_JOB_NAME) as job:
        ctx = resolve_miaoshou_context(session, license_id=license_id)
        if ctx is None:
            raise RuntimeError("no miaoshou credentials row; cannot construct context")
        if client is None:
            from tts_erp_v2.jobs.miaoshou._common import miaoshou_client_factory

            client = miaoshou_client_factory(ctx)
        payload = _fetch_detail(
            client,  # type: ignore[arg-type]
            op_order_package_id=op_order_package_id,
        )
        package = _unwrap_detail(payload)
        if package is None:
            record_sync_issue(
                session,
                job_name=DETAIL_JOB_NAME,
                issue_type="PACKAGE_DETAIL_MISSING",
                external_id=str(op_order_package_id),
                details={"response_keys": list(payload.keys())[:20]},
            )
            job.rows_total = 0
            job.rows_inserted = 0
            job.rows_failed = 1
            return {"shipments_upserted": 0, "items_seen": 0, "issues": 1}

        shipment_count, item_count, issues = _persist_package(
            session,
            package=package,
            credential_id=ctx.credentials.id,
            endpoint=DETAIL_ENDPOINT,
            job_name=DETAIL_JOB_NAME,
        )
        job.rows_total = 1
        job.rows_inserted = shipment_count
        job.rows_failed = issues
        return {
            "shipments_upserted": shipment_count,
            "items_seen": item_count,
            "issues": issues,
        }


__all__ = [
    "DETAIL_ENDPOINT",
    "DETAIL_JOB_NAME",
    "JOB_NAME",
    "SEARCH_ENDPOINT",
    "sync_package_detail",
    "sync_packages",
]
