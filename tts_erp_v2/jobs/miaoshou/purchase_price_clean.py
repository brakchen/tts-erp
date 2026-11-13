"""Synchronize store-level TikTok purchase costs from the Miaoshou Open Platform."""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Protocol
from urllib.error import URLError
from zoneinfo import ZoneInfo

from sqlalchemy import and_, func, inspect, or_, select, tuple_
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from tts_erp_v2.db.models.commerce import ChannelAccount
from tts_erp_v2.db.models.integration import Credentials
from tts_erp_v2.db.models.miaoshou import (
    MiaoshouPackage,
    MiaoshouPackageItem,
    MiaoshouPurchaseOrderRawRecord,
    MiaoshouPurchasePrice,
    MiaoshouSyncIssue,
)
from tts_erp_v2.db.models.procurement import ProcurementAccount
from tts_erp_v2.jobs.miaoshou._common import (
    miaoshou_client_factory,
    resolve_miaoshou_context,
)
from tts_erp_v2.jobs.runner import finish_job, run_job
from tts_erp_v2.proxy.miaoshou.client import MiaoshouApiError
from tts_erp_v2.proxy.miaoshou.retry import (
    MiaoshouRetryExhausted,
    call_with_retry,
    is_retryable_miaoshou_error,
)

JOB_NAME = "miaoshou.purchase_price_clean"
RESOURCE = "purchase_prices"
SEARCH_PATH = "/open/v1/order/purchase_order/purchase_record/search_list"
ENDPOINT = "miaoshou.purchase_record.search_list"
PAGE_SIZE = 20
MAX_PAGES = 1000
_MIAOSHOU_TZ = ZoneInfo("Asia/Shanghai")
_COMPLETED_STATUS = "finished"
_CNY_PURCHASE_PLATFORMS = {"1688", "1688妙手"}
_CALCULATION_VERSION = "purchase-price-open-api-v1"
_PREVIOUS_CALCULATION_VERSION = "purchase-price-v1"


class PurchaseApiClient(Protocol):
    def _call_erp(self, *, path: str, body: dict[str, Any]) -> dict[str, Any]: ...


@dataclass(frozen=True)
class PriceObservation:
    miaoshou_shop_id: str
    shop_name: str | None
    spu_id: str
    source_item_id: str
    unit_cost: Decimal
    purchase_order_sn: str
    purchase_at: datetime
    purchase_at_raw: str
    platform_order_sns: tuple[str, ...]
    source_lines: tuple[dict[str, str], ...]


def _schema_ready(session: Session) -> bool:
    return inspect(session.get_bind()).has_table("purchase_prices", schema="miaoshou")


def _retryable(error: BaseException) -> bool:
    return (
        is_retryable_miaoshou_error(error)
        or isinstance(error, (URLError, TimeoutError))
        or isinstance(error, MiaoshouApiError)
        and error.code in (429, 500, 502, 503, 504)
    )


def _fetch_all_pages(
    client: PurchaseApiClient, *, max_retries: int
) -> tuple[list[dict[str, Any]], int, int]:
    end = datetime.now(_MIAOSHOU_TZ).strftime("%Y-%m-%d %H:%M:%S")
    rows: dict[str, dict[str, Any]] = {}
    seen_pages: set[tuple[str, ...]] = set()
    total = None
    page_count = 1
    for page in range(1, MAX_PAGES + 1):
        body = {
            "page": page,
            "pageSize": PAGE_SIZE,
            "purchaseOrderStatus": _COMPLETED_STATUS,
            "gmtRangeType": "gmtPurchaseOrderStart",
            "gmtRangeTo": end,
            "sortType": "gmtPurchaseOrderStartDesc",
        }
        try:
            payload = call_with_retry(
                lambda: client._call_erp(path=SEARCH_PATH, body=body),
                is_retryable=_retryable,
                max_retries=max_retries,
            )
        except (
            MiaoshouApiError,
            MiaoshouRetryExhausted,
            URLError,
            TimeoutError,
        ) as exc:
            error = exc.last_error if isinstance(exc, MiaoshouRetryExhausted) else exc
            code = getattr(error, "code", None)
            safe_code = code if isinstance(code, int) else type(error).__name__
            raise RuntimeError(
                f"Miaoshou purchase API failed on page {page}: {safe_code}"
            ) from None
        data = payload.get("data")
        if payload.get("result") != "success" or not isinstance(data, dict):
            raise RuntimeError("invalid Miaoshou purchase API response")
        batch = data.get("list")
        advertised = data.get("total")
        if (
            type(advertised) is not int
            or advertised < 0
            or not isinstance(batch, list)
            or any(not isinstance(row, dict) for row in batch)
        ):
            raise RuntimeError("invalid purchase pagination metadata")
        if total is None:
            total = advertised
            page_count = max(1, math.ceil(total / PAGE_SIZE))
            if page_count > MAX_PAGES:
                raise RuntimeError("purchase pagination exceeds safety cap")
        if total != advertised:
            raise RuntimeError("purchase total changed during pagination")
        if len(batch) != min(PAGE_SIZE, total - (page - 1) * PAGE_SIZE):
            raise RuntimeError("purchase pagination incomplete")
        for row in batch:
            _validate_order(row)
        fingerprints = tuple(_fingerprint(_redact_secrets(row)) for row in batch)
        page_signature = tuple(sorted(fingerprints))
        if page_signature in seen_pages:
            raise RuntimeError("purchase pagination repeated a page")
        seen_pages.add(page_signature)
        for fingerprint, row in zip(fingerprints, batch, strict=True):
            rows[fingerprint] = row
        if page == page_count:
            return list(rows.values()), page_count, total
    raise RuntimeError("purchase pagination exceeds safety cap")


def _parse_time(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    normalized = value.strip().replace("T", " ").split(".")[0]
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return (
                datetime.strptime(normalized, fmt)
                .replace(tzinfo=_MIAOSHOU_TZ)
                .astimezone(UTC)
            )
        except ValueError:
            continue
    return None


def _validate_order(row: dict[str, Any]) -> None:
    for key in ("purchaseItems", "opOrderPackageList"):
        entries = row.get(key)
        if not isinstance(entries, list) or any(
            not isinstance(entry, dict) for entry in entries
        ):
            raise RuntimeError(f"invalid purchase record {key}")
    for package in row["opOrderPackageList"]:
        items = package.get("purchaseItems")
        if not isinstance(items, list) or any(
            not isinstance(item, dict) for item in items
        ):
            raise RuntimeError("invalid linked package purchaseItems")


def _package_links(order: dict[str, Any]) -> set[tuple[str, str]]:
    return {
        (
            str(package.get("platformOrderSn") or ""),
            str(item.get("platformItemId") or ""),
        )
        for package in order.get("opOrderPackageList") or []
        if package.get("platform") == "tiktok"
        and package.get("relateStatus") == "normal"
        for item in package.get("purchaseItems") or []
    }


def _load_package_shops(
    session: Session,
    credential_id: int,
    orders: list[dict[str, Any]],
) -> dict[tuple[str, str], set[str]]:
    refs = sorted({link for order in orders for link in _package_links(order)})
    shops: dict[tuple[str, str], set[str]] = {}
    for offset in range(0, len(refs), 500):
        stmt = (
            select(
                MiaoshouPackage.platform_order_sn,
                MiaoshouPackageItem.platform_product_id,
                MiaoshouPackage.shop_id,
            )
            .join(
                MiaoshouPackageItem,
                MiaoshouPackageItem.package_id == MiaoshouPackage.id,
            )
            .where(
                MiaoshouPackage.credential_id == credential_id,
                MiaoshouPackage.platform == "tiktok",
                MiaoshouPackageItem.active.is_(True),
                tuple_(
                    MiaoshouPackage.platform_order_sn,
                    MiaoshouPackageItem.platform_product_id,
                ).in_(refs[offset : offset + 500]),
            )
        )
        for order_sn, spu_id, shop_id in session.execute(stmt):
            shops.setdefault((order_sn, spu_id), set()).add(shop_id or "")
    return shops


def _positive_decimal(value: Any) -> Decimal:
    amount = Decimal(str(value))
    if not amount.is_finite() or amount <= 0:
        raise InvalidOperation
    return amount


def _observations_from_order(
    order: dict[str, Any],
    package_shops: dict[tuple[str, str], set[str]],
) -> tuple[PriceObservation | None, str | None]:
    if order.get("purchaseOrderStatus", _COMPLETED_STATUS) != _COMPLETED_STATUS:
        return None, "PURCHASE_STATUS_UNVERIFIED"
    packages = order.get("opOrderPackageList") or []
    links = _package_links(order)
    if not links:
        return None, "NO_ACTIVE_TIKTOK_LINK"
    if any(
        package.get("platform") != "tiktok"
        or package.get("relateStatus") != "normal"
        or not package.get("purchaseItems")
        for package in packages
    ):
        return None, "MIXED_PURCHASE_LINKS"
    spu_ids = {spu for _, spu in links}
    source = order.get("purchaseItems") or []
    titles = {str(line.get("sourceTitle") or "").strip() for line in source}
    if len(spu_ids) != 1 or len(titles) != 1 or "" in titles:
        return None, "PURCHASE_ITEM_MAPPING_UNVERIFIED"

    shop_candidates = [package_shops.get(link, set()) for link in links]
    shops = set().union(*shop_candidates)
    if (
        any(not order_sn or not spu for order_sn, spu in links)
        or any(len(shop) != 1 for shop in shop_candidates)
        or len(shops) != 1
        or "" in shops
    ):
        return None, "SHOP_UNRESOLVED_IN_PURCHASE_ORDER"
    if order.get("purchasePlatformName") not in _CNY_PURCHASE_PLATFORMS:
        return None, "PURCHASE_CURRENCY_UNVERIFIED"

    order_sn = str(order.get("purchaseOrderSn") or "").strip()
    purchase_at_raw = str(order.get("gmtPurchaseOrderStart") or "")
    purchase_at = _parse_time(purchase_at_raw)
    if not order_sn or purchase_at is None:
        return None, "INVALID_PURCHASE_IDENTITY"

    title = next(iter(titles))
    try:
        _positive_decimal(order.get("purchaseOrderPayment"))
        lines = [
            (
                _positive_decimal(line.get("sourceUnitPrice")),
                _positive_decimal(line.get("sourceQuantity")),
                str(line.get("sourceSkuSubName") or ""),
            )
            for line in source
        ]
        quantity = sum((units for _, units, _ in lines), Decimal(0))
        cost = (sum(price * units for price, units, _ in lines) / quantity).quantize(
            Decimal("0.0001")
        )
        if not Decimal(0) < cost < Decimal("1e16"):
            raise InvalidOperation
    except (InvalidOperation, ZeroDivisionError):
        return None, "INVALID_SOURCE_LINE"

    return PriceObservation(
        miaoshou_shop_id=next(iter(shops)),
        shop_name=packages[0].get("shopName"),
        spu_id=next(iter(spu_ids)),
        source_item_id=f"title:{hashlib.sha256(title.encode('utf-8')).hexdigest()}",
        unit_cost=cost,
        purchase_order_sn=order_sn,
        purchase_at=purchase_at,
        purchase_at_raw=purchase_at_raw,
        platform_order_sns=tuple(sorted({order_sn for order_sn, _ in links})),
        source_lines=tuple(
            {
                "source_title": title,
                "source_sku_name": sku,
                "unit_price": str(price),
                "quantity": str(units),
            }
            for price, units, sku in lines
        ),
    ), None


def clean_latest_prices(
    orders: list[dict[str, Any]],
    package_shops: dict[tuple[str, str], set[str]],
) -> tuple[dict[tuple[str, str], PriceObservation], list[dict[str, Any]]]:
    by_shop_spu: dict[tuple[str, str], list[PriceObservation]] = {}
    unresolved: dict[tuple[str, str], datetime] = {}
    issues: list[dict[str, Any]] = []
    for order in orders:
        observation, issue_type = _observations_from_order(order, package_shops)
        if issue_type is not None:
            issues.append(
                {
                    "issue_type": issue_type,
                    "purchase_order_sn": order.get("purchaseOrderSn"),
                }
            )
            bound = _parse_time(
                order.get("gmtPurchaseOrderStart")
            ) or datetime.max.replace(tzinfo=UTC)
            for link in _package_links(order):
                for shop in package_shops.get(link, set()):
                    key = (shop, link[1])
                    unresolved[key] = max(unresolved.get(key, bound), bound)
        if observation is not None:
            by_shop_spu.setdefault(
                (observation.miaoshou_shop_id, observation.spu_id), []
            ).append(observation)

    latest: dict[tuple[str, str], PriceObservation] = {}
    for shop_spu, observations in by_shop_spu.items():
        newest = max(observation.purchase_at for observation in observations)
        if shop_spu in unresolved and unresolved[shop_spu] >= newest:
            continue
        candidates = [
            observation
            for observation in observations
            if observation.purchase_at == newest
        ]
        prices = {observation.unit_cost for observation in candidates}
        if len(prices) != 1:
            issues.append(
                {
                    "issue_type": "LATEST_PRICE_AMBIGUOUS",
                    "miaoshou_shop_id": shop_spu[0],
                    "spu_id": shop_spu[1],
                    "prices": sorted(str(price) for price in prices),
                    "purchase_order_sns": sorted(
                        {observation.purchase_order_sn for observation in candidates}
                    ),
                }
            )
            continue
        latest[shop_spu] = max(
            candidates, key=lambda row: (row.purchase_order_sn, row.source_item_id)
        )
    return latest, issues


def _redact_secrets(value: Any) -> Any:
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for key, item in value.items():
            lowered = key.lower()
            if any(
                marker in lowered
                for marker in (
                    "token",
                    "secret",
                    "cookie",
                    "authorization",
                    "zebra",
                    "password",
                )
            ):
                result[key] = "***REDACTED***"
            else:
                result[key] = _redact_secrets(item)
        return result
    if isinstance(value, list):
        return [_redact_secrets(item) for item in value]
    return value


def _fingerprint(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()


def _record_raw_orders(
    session: Session,
    *,
    credential_id: int,
    orders: list[dict[str, Any]],
) -> int:
    inserted = 0
    now = datetime.now(UTC)
    for order in orders:
        with session.begin():
            order_sn = str(order.get("purchaseOrderSn") or "")
            if not order_sn:
                continue
            safe_order = _redact_secrets(order)
            stmt = pg_insert(MiaoshouPurchaseOrderRawRecord).values(
                credential_id=credential_id,
                external_purchase_order_id=order_sn,
                endpoint=ENDPOINT,
                payload=safe_order,
                payload_hash=_fingerprint(safe_order),
                captured_at=now,
            )
            inserted_id = session.execute(
                stmt.on_conflict_do_nothing(
                    index_elements=[
                        "credential_id",
                        "external_purchase_order_id",
                        "payload_hash",
                    ]
                ).returning(MiaoshouPurchaseOrderRawRecord.id)
            ).scalar_one_or_none()
            if inserted_id is not None:
                inserted += 1
    return inserted


def _record_issue(
    session: Session,
    *,
    credential_id: int,
    issue_type: str,
    external_id: str | None,
    details: dict[str, Any],
) -> None:
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
        return
    session.add(
        MiaoshouSyncIssue(
            credential_id=credential_id,
            resource=RESOURCE,
            issue_type=issue_type,
            external_id=external_id,
            details=details,
            detected_at=datetime.now(UTC),
        )
    )


def _resolve_shop_issues(
    session: Session,
    *,
    credential_id: int,
    miaoshou_shop_id: str,
    spu_id: str,
) -> None:
    external_id = f"{miaoshou_shop_id}:{spu_id}"
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


def _resolve_database_shop(
    session: Session,
    observation: PriceObservation,
) -> tuple[int | None, str]:
    account = session.execute(
        select(ProcurementAccount)
        .where(ProcurementAccount.provider == "miaoshou")
        .where(ProcurementAccount.external_account_id == observation.miaoshou_shop_id)
    ).scalar_one_or_none()
    if account is None:
        return None, "unmatched_miaoshou_shop"
    if not account.account_name:
        return None, "miaoshou_shop_missing_name"
    shops = (
        session.execute(
            select(ChannelAccount.id).where(
                ChannelAccount.platform == "tiktok",
                func.lower(ChannelAccount.account_name) == account.account_name.lower(),
            )
        )
        .scalars()
        .all()
    )
    if not shops:
        return None, "unmatched_database_shop"
    if len(shops) > 1:
        return None, "ambiguous_database_shop"
    return shops[0], "matched_shop"


def _upsert_purchase_price(
    session: Session,
    *,
    credential_id: int,
    observation: PriceObservation,
    shop_pk: int,
) -> bool:
    now = datetime.now(UTC)
    evidence = {
        "source_lines": list(observation.source_lines),
        "purchase_at_raw": observation.purchase_at_raw,
        "platform_order_sns": list(observation.platform_order_sns),
        "endpoint": ENDPOINT,
        "requested_status": _COMPLETED_STATUS,
        "miaoshou_shop_id": observation.miaoshou_shop_id,
    }
    values = {
        "credential_id": credential_id,
        "miaoshou_shop_id": observation.miaoshou_shop_id,
        "shop_name": observation.shop_name,
        "shop_pk": shop_pk,
        "spu_id": observation.spu_id,
        "unit_cost": observation.unit_cost,
        "currency": "CNY",
        "source_purchase_order_sn": observation.purchase_order_sn,
        "source_item_id": observation.source_item_id,
        "source_purchase_at": observation.purchase_at,
        "source_status": None,
        "calculation_method": "quantity_weighted_mean",
        "calculation_version": _CALCULATION_VERSION,
        "resolution_status": "matched_shop",
        "evidence": evidence,
        "last_seen_at": now,
        "synced_at": now,
    }
    # Reuse a matching pre-cutover price row when the API has an equally recent
    # or newer observation. Historical raw records keep their source credential.
    lock_key = f"{JOB_NAME}:{observation.miaoshou_shop_id}:{observation.spu_id}"
    session.execute(
        select(func.pg_advisory_xact_lock(func.hashtextextended(lock_key, 0)))
    )
    stored = session.scalars(
        select(MiaoshouPurchasePrice)
        .join(Credentials, Credentials.id == MiaoshouPurchasePrice.credential_id)
        .where(
            MiaoshouPurchasePrice.miaoshou_shop_id == observation.miaoshou_shop_id,
            MiaoshouPurchasePrice.spu_id == observation.spu_id,
            or_(
                MiaoshouPurchasePrice.credential_id == credential_id,
                and_(
                    MiaoshouPurchasePrice.calculation_version == _PREVIOUS_CALCULATION_VERSION,
                    Credentials.provider == "miaoshou_web",
                    MiaoshouPurchasePrice.shop_pk == shop_pk,
                ),
            ),
        )
    ).all()
    if len(stored) > 1:
        _record_issue(
            session,
            credential_id=credential_id,
            issue_type="AMBIGUOUS_STORED_PURCHASE_PRICE",
            external_id=f"{observation.miaoshou_shop_id}:{observation.spu_id}",
            details={"purchase_order_sn": observation.purchase_order_sn},
        )
        return False
    if stored:
        row = stored[0]
        if row.source_purchase_at and row.source_purchase_at > observation.purchase_at:
            return False
        if (
            row.source_purchase_at == observation.purchase_at
            and row.unit_cost != observation.unit_cost
        ):
            _record_issue(
                session,
                credential_id=credential_id,
                issue_type="STORED_PURCHASE_PRICE_CONFLICT",
                external_id=f"{observation.miaoshou_shop_id}:{observation.spu_id}",
                details={"purchase_order_sn": observation.purchase_order_sn},
            )
            return False
        for key, value in values.items():
            setattr(row, key, value)
    else:
        session.add(MiaoshouPurchasePrice(**values))
    return True


def _skipped_result(reason: str) -> dict[str, Any]:
    return {
        "skipped": True,
        "reason": reason,
        "pages_walked": 0,
        "orders_seen": 0,
        "purchase_prices": 0,
        "matched_shops": 0,
        "unmatched_shops": 0,
        "issues": 0,
    }


def sync_purchase_prices(
    session: Session,
    *,
    client: PurchaseApiClient | None = None,
    external_account_id: str | None = None,
    max_retries: int = 3,
) -> dict[str, Any]:
    """Run one complete purchase-price fetch/clean/publish cycle."""
    with run_job(session, job_name=JOB_NAME) as job:
        with session.begin():
            if not _schema_ready(session):
                result = _skipped_result("migration 0047 not applied")
                finish_job(
                    session, job, status="skipped", extra={"reason": result["reason"]}
                )
                return result
        context = resolve_miaoshou_context(session, license_id=external_account_id)
        if context is None:
            result = _skipped_result(
                "Miaoshou Open Platform credentials not configured"
            )
            finish_job(
                session, job, status="skipped", extra={"reason": result["reason"]}
            )
            return result
        credential_id = context.credentials.id
        if client is None:
            client = miaoshou_client_factory(context)

        orders, pages_walked, advertised_total = _fetch_all_pages(
            client, max_retries=max_retries
        )
        raw_inserted = _record_raw_orders(
            session,
            credential_id=credential_id,
            orders=orders,
        )
        with session.begin():
            package_shops = _load_package_shops(session, credential_id, orders)
        latest, cleaning_issues = clean_latest_prices(orders, package_shops)
        for issue in cleaning_issues:
            with session.begin():
                _record_issue(
                    session,
                    credential_id=credential_id,
                    issue_type=str(issue["issue_type"]),
                    external_id=str(
                        issue.get("spu_id") or issue.get("purchase_order_sn") or ""
                    )
                    or None,
                    details=issue,
                )

        written = 0
        matched_shops = 0
        unmatched_shops = 0
        shop_issues = 0
        for observation in latest.values():
            with session.begin():
                shop_pk, resolution_status = _resolve_database_shop(
                    session, observation
                )
                if shop_pk is not None:
                    matched_shops += 1
                    if _upsert_purchase_price(
                        session,
                        credential_id=credential_id,
                        observation=observation,
                        shop_pk=shop_pk,
                    ):
                        written += 1
                        _resolve_shop_issues(
                            session,
                            credential_id=credential_id,
                            miaoshou_shop_id=observation.miaoshou_shop_id,
                            spu_id=observation.spu_id,
                        )
                    continue
                unmatched_shops += 1
                shop_issues += 1
                _record_issue(
                    session,
                    credential_id=credential_id,
                    issue_type=resolution_status.upper(),
                    external_id=f"{observation.miaoshou_shop_id}:{observation.spu_id}",
                    details={
                        "miaoshou_shop_id": observation.miaoshou_shop_id,
                        "spu_id": observation.spu_id,
                        "purchase_order_sn": observation.purchase_order_sn,
                    },
                )

        issues = len(cleaning_issues) + shop_issues
        job.rows_total = len(orders)
        job.rows_inserted = raw_inserted + written
        job.rows_updated = 0
        job.rows_failed = issues
        job.extra = {
            "pages_walked": pages_walked,
            "advertised_total": advertised_total,
            "purchase_prices": written,
            "raw_inserted": raw_inserted,
            "matched_shops": matched_shops,
            "unmatched_shops": unmatched_shops,
            "calculation_version": _CALCULATION_VERSION,
            "finished_at_iso": datetime.now(UTC).isoformat(),
        }
        return {
            "skipped": False,
            "reason": None,
            "pages_walked": pages_walked,
            "orders_seen": len(orders),
            "purchase_prices": written,
            "matched_shops": matched_shops,
            "unmatched_shops": unmatched_shops,
            "issues": issues,
        }


__all__ = [
    "JOB_NAME",
    "PriceObservation",
    "clean_latest_prices",
    "sync_purchase_prices",
]
