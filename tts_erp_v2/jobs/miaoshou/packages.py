"""Synchronize Miaoshou package APIs into the source-owned ``miaoshou`` schema.

Package headers, items, gifts, raw payloads, cursors, and issues all remain
under ``miaoshou.*``. The only cross-cutting write is the generic
``integration.sync_jobs`` execution audit supplied by :func:`run_job`.
"""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any, Protocol
from zoneinfo import ZoneInfo

from sqlalchemy import inspect, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from tts_erp_v2.db.models.miaoshou import (
    MiaoshouPackage,
    MiaoshouPackageGiftItem,
    MiaoshouPackageItem,
    MiaoshouPackageRawRecord,
    MiaoshouSyncCursor,
    MiaoshouSyncIssue,
)
from tts_erp_v2.jobs.miaoshou._common import resolve_miaoshou_context
from tts_erp_v2.jobs.runner import finish_job, run_job

log = logging.getLogger("tts_erp_v2.jobs.miaoshou.packages")

JOB_NAME = "miaoshou.packages"
DETAIL_JOB_NAME = "miaoshou.package_detail"
SEARCH_ENDPOINT = "miaoshou.package.search_package_list"
DETAIL_ENDPOINT = "miaoshou.package.get_package_info"
SEARCH_PATH = "/open/v1/order/package/fetch/search_package_list"
DETAIL_PATH = "/open/v1/order/package/fetch/get_package_info"
RESOURCE = "packages"
PAGE_SIZE = 100
MAX_PAGES = 1000
_CURSOR_OVERLAP = timedelta(minutes=5)
_MIAOSHOU_TZ = ZoneInfo("Asia/Shanghai")


class _MiaoshouClientProto(Protocol):
    def _call_erp(
        self,
        *,
        path: str,
        body: dict | None = None,
        query: dict | None = None,
        extra_headers: dict | None = None,
    ) -> dict[str, Any]: ...


def _schema_ready(session: Session) -> bool:
    """Allow code deployment before the human-operated production migration."""
    return inspect(session.get_bind()).has_table("packages", schema="miaoshou")


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


def _to_decimal(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value))
    except Exception:  # noqa: BLE001
        return None


def _string(value: Any) -> str | None:
    return str(value) if value not in (None, "") else None


def _parse_package(
    package: dict[str, Any], *, source_endpoint: str
) -> dict[str, Any] | None:
    package_id = _string(package.get("opOrderPackageId"))
    if package_id is None:
        return None
    order_info = package.get("orderInfo")
    if not isinstance(order_info, dict):
        order_info = {}
    logistics = package.get("logisticsAgentProductInfo")
    if not isinstance(logistics, dict) or not logistics:
        logistics = package.get("opOrderPackageToPlatformLastMile")
    if not isinstance(logistics, dict):
        logistics = {}

    tracking_number = (
        package.get("logisticsNo")
        or logistics.get("logisticsNo")
        or logistics.get("platformPackageNo")
    )
    logistics_product_id = logistics.get("logisticsAgentProductId")
    if logistics_product_id in (None, ""):
        logistics_product_id = logistics.get("logisticsAgentId")

    field_sources = {
        "platform": (package, "platform"),
        "site": (package, "site"),
        "shop_id": (package, "shopId"),
        "shop_name": (package, "shopName"),
        "shop_nick": (package, "shopNick"),
        "app_package_no": (package, "appPackageNo"),
        "app_package_status": (package, "appPackageStatus"),
        "app_package_status_text": (package, "appPackageStatusText"),
        "platform_package_status": (package, "platformPackageStatus"),
        "fulfillment_type": (package, "fulfillmentType"),
        "platform_order_sn": (order_info, "platformOrderSn"),
        "platform_order_status": (order_info, "platformOrderStatus"),
        "currency": (order_info, "currency"),
        "source_created_at": (order_info, "gmtOrderStart"),
        "source_updated_at": (order_info, "gmtOrderModified"),
        "shipped_at": (order_info, "gmtDelivery"),
    }
    provided_fields = {
        column for column, (source, key) in field_sources.items() if key in source
    }
    if "logisticsNo" in package or any(
        key in logistics for key in ("logisticsNo", "platformPackageNo")
    ):
        provided_fields.add("logistics_no")
    if any(key in logistics for key in ("logisticsCompany",)):
        provided_fields.add("logistics_company")
    if any(key in logistics for key in ("logisticsAgentProductId", "logisticsAgentId")):
        provided_fields.add("logistics_product_id")
    if "productName" in logistics:
        provided_fields.add("logistics_product_name")
    for column, key in (
        ("order_info", "orderInfo"),
        ("consignee_info", "consigneeInfo"),
        ("logistics_info", "logisticsAgentProductInfo"),
        ("last_mile_info", "opOrderPackageToPlatformLastMile"),
    ):
        if key in package:
            provided_fields.add(column)

    values = {
        "external_package_id": package_id,
        "source_endpoint": source_endpoint,
        "platform": _string(package.get("platform")),
        "site": _string(package.get("site")),
        "shop_id": _string(package.get("shopId")),
        "shop_name": _string(package.get("shopName")),
        "shop_nick": _string(package.get("shopNick")),
        "app_package_no": _string(package.get("appPackageNo")),
        "app_package_status": _string(package.get("appPackageStatus")),
        "app_package_status_text": _string(package.get("appPackageStatusText")),
        "platform_package_status": _string(package.get("platformPackageStatus")),
        "fulfillment_type": _string(package.get("fulfillmentType")),
        "platform_order_sn": _string(order_info.get("platformOrderSn")),
        "platform_order_status": _string(order_info.get("platformOrderStatus")),
        "currency": _string(order_info.get("currency")),
        "logistics_no": _string(tracking_number),
        "logistics_company": _string(logistics.get("logisticsCompany")),
        "logistics_product_id": _string(logistics_product_id),
        "logistics_product_name": _string(logistics.get("productName")),
        "source_created_at": _parse_datetime(order_info.get("gmtOrderStart")),
        "source_updated_at": _parse_datetime(order_info.get("gmtOrderModified")),
        "shipped_at": _parse_datetime(order_info.get("gmtDelivery")),
        "order_info": order_info or None,
        "consignee_info": package.get("consigneeInfo")
        if isinstance(package.get("consigneeInfo"), dict)
        else None,
        "logistics_info": package.get("logisticsAgentProductInfo")
        if isinstance(package.get("logisticsAgentProductInfo"), dict)
        else None,
        "last_mile_info": package.get("opOrderPackageToPlatformLastMile")
        if isinstance(package.get("opOrderPackageToPlatformLastMile"), dict)
        else None,
        "raw_payload": package,
        "provided_fields": provided_fields,
        "items_present": "items" in package,
        "items": package.get("items") if isinstance(package.get("items"), list) else [],
        "gifts_present": "giftItems" in package,
        "gifts": package.get("giftItems")
        if isinstance(package.get("giftItems"), list)
        else [],
    }
    return values


def _load_modified_cursor(session: Session, *, credential_id: int) -> str | None:
    value = session.execute(
        select(MiaoshouSyncCursor.cursor_value)
        .where(MiaoshouSyncCursor.credential_id == credential_id)
        .where(MiaoshouSyncCursor.resource == RESOURCE)
    ).scalar_one_or_none()
    parsed = _parse_datetime(value)
    if parsed is None:
        return value
    overlapped = parsed - _CURSOR_OVERLAP
    return overlapped.astimezone(_MIAOSHOU_TZ).strftime("%Y-%m-%d %H:%M:%S")


def _save_modified_cursor(
    session: Session, *, credential_id: int, value: datetime
) -> None:
    local_value = value.astimezone(_MIAOSHOU_TZ).strftime("%Y-%m-%d %H:%M:%S")
    try:
        cursor_epoch_ms = int(value.timestamp() * 1000)
    except (OverflowError, ValueError):
        cursor_epoch_ms = None
    stmt = pg_insert(MiaoshouSyncCursor).values(
        credential_id=credential_id,
        resource=RESOURCE,
        cursor_value=local_value,
        cursor_epoch_ms=cursor_epoch_ms,
    )
    session.execute(
        stmt.on_conflict_do_update(
            index_elements=["credential_id", "resource"],
            set_={
                "cursor_value": local_value,
                "cursor_epoch_ms": cursor_epoch_ms,
                "updated_at": datetime.now(UTC),
            },
        )
    )


def _record_issue(
    session: Session,
    *,
    credential_id: int,
    issue_type: str,
    external_id: str | None,
    details: dict[str, Any],
) -> MiaoshouSyncIssue:
    existing = (
        session.execute(
            select(MiaoshouSyncIssue)
            .where(MiaoshouSyncIssue.credential_id == credential_id)
            .where(MiaoshouSyncIssue.resource == RESOURCE)
            .where(MiaoshouSyncIssue.issue_type == issue_type)
            .where(MiaoshouSyncIssue.external_id == external_id)
            .where(MiaoshouSyncIssue.resolved_at.is_(None))
            .limit(1)
        )
        .scalars()
        .first()
    )
    if existing is not None:
        existing.detected_at = datetime.now(UTC)
        existing.details = details
        return existing
    row = MiaoshouSyncIssue(
        credential_id=credential_id,
        resource=RESOURCE,
        issue_type=issue_type,
        external_id=external_id,
        details=details,
        detected_at=datetime.now(UTC),
    )
    session.add(row)
    session.flush()
    return row


def _resolve_issues(session: Session, *, credential_id: int, external_id: str) -> None:
    rows = session.execute(
        select(MiaoshouSyncIssue)
        .where(MiaoshouSyncIssue.credential_id == credential_id)
        .where(MiaoshouSyncIssue.resource == RESOURCE)
        .where(MiaoshouSyncIssue.external_id == external_id)
        .where(MiaoshouSyncIssue.resolved_at.is_(None))
    ).scalars()
    resolved_at = datetime.now(UTC)
    for row in rows:
        row.resolved_at = resolved_at


def _record_raw_payload(
    session: Session,
    *,
    credential_id: int,
    package: dict[str, Any],
    endpoint: str,
) -> MiaoshouPackageRawRecord:
    canonical = json.dumps(package, ensure_ascii=False, sort_keys=True)
    row = MiaoshouPackageRawRecord(
        credential_id=credential_id,
        external_package_id=_string(package.get("opOrderPackageId")),
        endpoint=endpoint,
        payload=package,
        payload_hash=hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
        captured_at=datetime.now(UTC),
    )
    session.add(row)
    session.flush()
    return row


def _upsert_package(
    session: Session,
    *,
    credential_id: int,
    raw_record_id: int,
    parsed: dict[str, Any],
) -> MiaoshouPackage:
    excluded = {
        "provided_fields",
        "items_present",
        "items",
        "gifts_present",
        "gifts",
    }
    values = {
        "credential_id": credential_id,
        "raw_record_id": raw_record_id,
        **{key: value for key, value in parsed.items() if key not in excluded},
        "synced_at": datetime.now(UTC),
    }
    update_values = {
        "raw_record_id": raw_record_id,
        "source_endpoint": parsed["source_endpoint"],
        "raw_payload": parsed["raw_payload"],
        "synced_at": values["synced_at"],
    }
    update_values.update(
        {key: values[key] for key in parsed["provided_fields"] if key in values}
    )
    stmt = pg_insert(MiaoshouPackage).values(**values)
    session.execute(
        stmt.on_conflict_do_update(
            index_elements=["credential_id", "external_package_id"],
            set_=update_values,
        )
    )
    return session.execute(
        select(MiaoshouPackage)
        .where(MiaoshouPackage.credential_id == credential_id)
        .where(MiaoshouPackage.external_package_id == parsed["external_package_id"])
    ).scalar_one()


def _upsert_items(
    session: Session,
    *,
    package_id: int,
    items: list[Any],
    credential_id: int,
) -> tuple[int, int]:
    written = 0
    issues = 0
    active_ids: list[str] = []
    for item in items:
        if not isinstance(item, dict):
            issues += 1
            _record_issue(
                session,
                credential_id=credential_id,
                issue_type="PACKAGE_ITEM_PARSE_FAILED",
                external_id=str(package_id),
                details={"item": repr(item)[:300]},
            )
            continue
        external_id = _string(item.get("opOrderPackageItemId"))
        if external_id is None:
            issues += 1
            _record_issue(
                session,
                credential_id=credential_id,
                issue_type="PACKAGE_ITEM_MISSING_ID",
                external_id=str(package_id),
                details={"item_keys": list(item.keys())[:30]},
            )
            continue
        active_ids.append(external_id)
        values = {
            "package_id": package_id,
            "external_package_item_id": external_id,
            "external_order_item_id": _string(item.get("opOrderItemId")),
            "platform_order_item_index": _string(item.get("platformOrderItemIndex")),
            "platform_product_id": _string(item.get("platformItemId")),
            "platform_sku_id": _string(item.get("platformSkuId")),
            "platform_item_num": _string(item.get("platformItemNum")),
            "platform_outer_sku_id": _string(item.get("platformOuterSkuId")),
            "title": _string(item.get("title")),
            "sku_name": _string(item.get("skuSubName")),
            "quantity": _to_decimal(item.get("quantity")),
            "original_price": _to_decimal(item.get("originalPrice")),
            "discounted_price": _to_decimal(item.get("discountedPrice")),
            "image_url": _string(item.get("picUrl")),
            "original_image_url": _string(item.get("originalPicUrl")),
            "raw_payload": item,
            "active": True,
            "removed_at": None,
            "synced_at": datetime.now(UTC),
        }
        stmt = pg_insert(MiaoshouPackageItem).values(**values)
        session.execute(
            stmt.on_conflict_do_update(
                index_elements=["package_id", "external_package_item_id"],
                set_={
                    key: value
                    for key, value in values.items()
                    if key not in ("package_id", "external_package_item_id")
                },
            )
        )
        written += 1
    stale = update(MiaoshouPackageItem).where(
        MiaoshouPackageItem.package_id == package_id,
        MiaoshouPackageItem.active.is_(True),
    )
    if active_ids:
        stale = stale.where(
            MiaoshouPackageItem.external_package_item_id.not_in(active_ids)
        )
    session.execute(stale.values(active=False, removed_at=datetime.now(UTC)))
    return written, issues


def _upsert_gifts(
    session: Session,
    *,
    package_id: int,
    gifts: list[Any],
    credential_id: int,
) -> tuple[int, int]:
    written = 0
    issues = 0
    active_ids: list[str] = []
    for gift in gifts:
        if not isinstance(gift, dict):
            issues += 1
            continue
        external_id = _string(gift.get("opOrderPackageGiftId"))
        if external_id is None:
            issues += 1
            _record_issue(
                session,
                credential_id=credential_id,
                issue_type="PACKAGE_GIFT_MISSING_ID",
                external_id=str(package_id),
                details={"gift_keys": list(gift.keys())[:30]},
            )
            continue
        active_ids.append(external_id)
        values = {
            "package_id": package_id,
            "external_gift_item_id": external_id,
            "goods_id": _string(gift.get("goodsId")),
            "goods_sku_id": _string(gift.get("goodsSkuId")),
            "goods_name": _string(gift.get("goodsName")),
            "item_num": _string(gift.get("itemNum")),
            "sku_name": _string(gift.get("goodsSkuSubName")),
            "goods_sku_outer_id": _string(gift.get("goodsSkuOuterId")),
            "quantity": _to_decimal(gift.get("quantity")),
            "original_price": _to_decimal(gift.get("originalPrice")),
            "discounted_price": _to_decimal(gift.get("discountedPrice")),
            "image_url": _string(gift.get("picUrl")),
            "raw_payload": gift,
            "active": True,
            "removed_at": None,
            "synced_at": datetime.now(UTC),
        }
        stmt = pg_insert(MiaoshouPackageGiftItem).values(**values)
        session.execute(
            stmt.on_conflict_do_update(
                index_elements=["package_id", "external_gift_item_id"],
                set_={
                    key: value
                    for key, value in values.items()
                    if key not in ("package_id", "external_gift_item_id")
                },
            )
        )
        written += 1
    stale = update(MiaoshouPackageGiftItem).where(
        MiaoshouPackageGiftItem.package_id == package_id,
        MiaoshouPackageGiftItem.active.is_(True),
    )
    if active_ids:
        stale = stale.where(
            MiaoshouPackageGiftItem.external_gift_item_id.not_in(active_ids)
        )
    session.execute(stale.values(active=False, removed_at=datetime.now(UTC)))
    return written, issues


def _persist_package(
    session: Session,
    *,
    package: dict[str, Any],
    credential_id: int,
    source_endpoint: str,
) -> tuple[int, int, int, int]:
    raw = _record_raw_payload(
        session,
        credential_id=credential_id,
        package=package,
        endpoint=source_endpoint,
    )
    parsed = _parse_package(package, source_endpoint=source_endpoint)
    if parsed is None:
        _record_issue(
            session,
            credential_id=credential_id,
            issue_type="PACKAGE_MISSING_ID",
            external_id=None,
            details={"package_keys": list(package.keys())[:30]},
        )
        return 0, 0, 0, 1
    row = _upsert_package(
        session,
        credential_id=credential_id,
        raw_record_id=raw.id,
        parsed=parsed,
    )
    items_written = 0
    gifts_written = 0
    issues = 0
    if parsed["items_present"]:
        items_written, item_issues = _upsert_items(
            session,
            package_id=row.id,
            items=parsed["items"],
            credential_id=credential_id,
        )
        issues += item_issues
    if parsed["gifts_present"]:
        gifts_written, gift_issues = _upsert_gifts(
            session,
            package_id=row.id,
            gifts=parsed["gifts"],
            credential_id=credential_id,
        )
        issues += gift_issues
    _resolve_issues(
        session,
        credential_id=credential_id,
        external_id=parsed["external_package_id"],
    )
    return 1, items_written, gifts_written, issues


def _skipped_result() -> dict[str, Any]:
    return {
        "pages_walked": 0,
        "packages_seen": 0,
        "packages_upserted": 0,
        "items_upserted": 0,
        "gifts_upserted": 0,
        "rate_limit_retries": 0,
        "issues": 0,
        "skipped": True,
    }


def sync_packages(
    session: Session,
    *,
    client: _MiaoshouClientProto | None = None,
    license_id: str | None = None,
    gmt_modified_from: str | None = None,
    max_retries: int = 3,
) -> dict[str, Any]:
    """Incrementally synchronize package list data into ``miaoshou.*``."""
    from tts_erp_v2.proxy.miaoshou.retry import PageResult, paginate_with_retry

    with run_job(session, job_name=JOB_NAME) as job:
        if not _schema_ready(session):
            result = _skipped_result()
            finish_job(
                session,
                job,
                status="skipped",
                extra={"reason": "miaoshou schema migration not applied"},
            )
            return result
        ctx = resolve_miaoshou_context(session, license_id=license_id)
        if ctx is None:
            raise RuntimeError("no miaoshou credentials row; cannot construct context")
        if client is None:
            from tts_erp_v2.jobs.miaoshou._common import miaoshou_client_factory

            client = miaoshou_client_factory(ctx)

        modified_from = gmt_modified_from
        if modified_from is None:
            modified_from = _load_modified_cursor(
                session, credential_id=ctx.credentials.id
            )
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

        packages_upserted = 0
        items_upserted = 0
        gifts_upserted = 0
        issues = 0
        max_modified: datetime | None = None
        for package in packages:
            if not isinstance(package, dict):
                issues += 1
                _record_issue(
                    session,
                    credential_id=ctx.credentials.id,
                    issue_type="PACKAGE_PARSE_FAILED",
                    external_id=None,
                    details={"package": repr(package)[:300]},
                )
                continue
            package_count, item_count, gift_count, issue_count = _persist_package(
                session,
                package=package,
                credential_id=ctx.credentials.id,
                source_endpoint=SEARCH_ENDPOINT,
            )
            packages_upserted += package_count
            items_upserted += item_count
            gifts_upserted += gift_count
            issues += issue_count
            order_info = package.get("orderInfo")
            if isinstance(order_info, dict):
                modified = _parse_datetime(order_info.get("gmtOrderModified"))
                if modified is not None and (
                    max_modified is None or modified > max_modified
                ):
                    max_modified = modified

        if max_modified is not None:
            _save_modified_cursor(
                session,
                credential_id=ctx.credentials.id,
                value=max_modified,
            )

        job.rows_total = len(packages)
        job.rows_inserted = packages_upserted + items_upserted + gifts_upserted
        job.rows_failed = issues
        job.extra = {
            "pages_walked": last_page,
            "modified_from": modified_from,
            "max_modified": max_modified.isoformat() if max_modified else None,
            "items_upserted": items_upserted,
            "gifts_upserted": gifts_upserted,
            "rate_limit_retries": rate_limit_retries,
            "finished_at_iso": datetime.now(UTC).isoformat(),
        }
        return {
            "pages_walked": last_page,
            "packages_seen": len(packages),
            "packages_upserted": packages_upserted,
            "items_upserted": items_upserted,
            "gifts_upserted": gifts_upserted,
            "rate_limit_retries": rate_limit_retries,
            "issues": issues,
            "skipped": False,
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
        if not _schema_ready(session):
            finish_job(
                session,
                job,
                status="skipped",
                extra={"reason": "miaoshou schema migration not applied"},
            )
            return {
                "packages_upserted": 0,
                "items_upserted": 0,
                "gifts_upserted": 0,
                "issues": 0,
                "skipped": True,
            }
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
            _record_issue(
                session,
                credential_id=ctx.credentials.id,
                issue_type="PACKAGE_DETAIL_MISSING",
                external_id=str(op_order_package_id),
                details={"response_keys": list(payload.keys())[:20]},
            )
            job.rows_total = 0
            job.rows_inserted = 0
            job.rows_failed = 1
            return {
                "packages_upserted": 0,
                "items_upserted": 0,
                "gifts_upserted": 0,
                "issues": 1,
                "skipped": False,
            }

        package_count, item_count, gift_count, issues = _persist_package(
            session,
            package=package,
            credential_id=ctx.credentials.id,
            source_endpoint=DETAIL_ENDPOINT,
        )
        job.rows_total = 1
        job.rows_inserted = package_count + item_count + gift_count
        job.rows_failed = issues
        return {
            "packages_upserted": package_count,
            "items_upserted": item_count,
            "gifts_upserted": gift_count,
            "issues": issues,
            "skipped": False,
        }


__all__ = [
    "DETAIL_ENDPOINT",
    "DETAIL_JOB_NAME",
    "JOB_NAME",
    "SEARCH_ENDPOINT",
    "sync_package_detail",
    "sync_packages",
]
