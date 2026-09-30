"""Fetch, clean, and publish Miaoshou browser-ERP purchase prices.

The private ERP response is the only source observed to contain both 1688
``sourceUnitPrice`` rows and linked TikTok ``platformItemId`` rows. Browser
session secrets are loaded from encrypted ``integration.credentials`` via
``token_service``; they are never logged or stored in ``miaoshou`` payloads.
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Protocol
from zoneinfo import ZoneInfo

from sqlalchemy import inspect, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from tts_erp_v2.db.models.commerce import ChannelProduct
from tts_erp_v2.db.models.integration import Credentials
from tts_erp_v2.db.models.miaoshou import (
    MiaoshouPurchaseOrderRawRecord,
    MiaoshouPurchasePriceCandidate,
    MiaoshouSyncIssue,
)
from tts_erp_v2.db.models.procurement import ManualProductCost
from tts_erp_v2.jobs.runner import finish_job, run_job
from tts_erp_v2.proxy.token_service import CredentialsView, load_credentials
from tts_erp_v2.reporting.manual_cost_lock import lock_manual_cost_spu

log = logging.getLogger("tts_erp_v2.jobs.miaoshou.purchase_price_clean")

JOB_NAME = "miaoshou.purchase_price_clean"
RESOURCE = "purchase_prices"
WEB_PROVIDER = "miaoshou_web"
ENDPOINT = "miaoshou.purchase_order.search_list"
DEFAULT_BASE_URL = "https://erp.91miaoshou.com"
SEARCH_PATH = "/api/order/purchase/purchase_order/searchList"
PAGE_SIZE = 100
MAX_PAGES = 1000
_MIAOSHOU_TZ = ZoneInfo("Asia/Shanghai")
_EXCLUDED_STATUSES = {"cancel", "wait_pay"}
_CALCULATION_VERSION = "purchase-price-v1"


class PurchaseWebClient(Protocol):
    def search_page(
        self, *, page: int, page_size: int = PAGE_SIZE
    ) -> dict[str, Any]: ...


@dataclass(frozen=True)
class PriceObservation:
    spu_id: str
    source_item_id: str
    unit_cost: Decimal
    purchase_order_sn: str
    purchase_at: datetime | None
    purchase_at_raw: str
    status: str
    filter_id: str
    source_lines: tuple[dict[str, str], ...]


class MiaoshouPurchaseWebClient:
    """Minimal client for the browser ERP purchase-order list."""

    def __init__(
        self,
        *,
        cookie: str,
        x_app_zebra: str,
        base_url: str = DEFAULT_BASE_URL,
        front_version: str = "",
        referer: str = "/order/purchase_record",
        timeout: int = 30,
    ) -> None:
        if not cookie.strip():
            raise ValueError("Miaoshou web cookie is empty")
        if not x_app_zebra.strip():
            raise ValueError("Miaoshou x-app-zebra is empty")
        self._cookie = cookie.strip()
        self._x_app_zebra = x_app_zebra.strip()
        self._base_url = base_url.rstrip("/")
        self._front_version = front_version.strip()
        self._referer = referer if referer.startswith("/") else f"/{referer}"
        self._timeout = timeout
        self._range_to = datetime.now(_MIAOSHOU_TZ).strftime("%Y-%m-%d %H:%M:%S")

    def search_page(self, *, page: int, page_size: int = PAGE_SIZE) -> dict[str, Any]:
        body = urllib.parse.urlencode(
            {
                "pageSize": str(page_size),
                "page": str(page),
                "snValue": "",
                "snRp": "eq",
                "purchasePlatform": "",
                "gmtRangeType": "gmtPurchaseOrderStart",
                "forwarderId": "",
                "gmtRangeFrom": "",
                "gmtRangeTo": self._range_to,
                "relateStatus": "",
                "appPackageStatus": "",
                "purchaseOrderSeller": "",
                "purchaseOrderSellerRp": "ss",
                "warningType": "",
                "warningTimeout": "",
                "buyerPhone": "",
                "purchaseOrderStatus": "all",
                "sortType": "gmtPurchaseOrderStartDesc",
                "snType": "purchaseOrderSn",
                "isFilterAbnormal": "",
            }
        ).encode()
        url = f"{self._base_url}{SEARCH_PATH}"
        origin = self._base_url
        headers = {
            "Accept": "application/json, text/plain, */*",
            "Content-Type": "application/x-www-form-urlencoded",
            "Cookie": self._cookie,
            "Origin": origin,
            "Referer": f"{origin}{self._referer}",
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/153 Safari/537.36",
            "X-App-Zebra": self._x_app_zebra,
            "X-Referer": f"{origin}{self._referer}",
            "X-Timestamp": f"{time.time():.0f}",
        }
        if self._front_version:
            headers["X-Front-Version"] = self._front_version
        request = urllib.request.Request(url, data=body, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(request, timeout=self._timeout) as response:
                raw = response.read()
        except urllib.error.HTTPError as exc:
            # Authenticated error bodies are untrusted and may echo Cookie/token
            # material. Never include them in exceptions, logs, or sync_jobs.
            raise RuntimeError(f"Miaoshou purchase HTTP {exc.code}") from exc
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise RuntimeError("Miaoshou purchase response is not JSON") from exc
        if payload.get("result") != "success":
            raise RuntimeError(
                "Miaoshou purchase business failure: "
                f"result={payload.get('result')!r} code={payload.get('code')!r}"
            )
        return payload


def _schema_ready(session: Session) -> bool:
    return inspect(session.get_bind()).has_table(
        "purchase_price_candidates", schema="miaoshou"
    )


def _resolve_credentials(
    session: Session, *, external_account_id: str | None = None
) -> CredentialsView | None:
    account_id = (
        external_account_id or os.environ.get("MIAOSHOU_WEB_ACCOUNT_ID", "").strip()
    )
    if account_id:
        return load_credentials(session, WEB_PROVIDER, account_id)
    rows = (
        session.execute(
            select(Credentials.external_account_id)
            .where(Credentials.provider == WEB_PROVIDER)
            .order_by(Credentials.id)
        )
        .scalars()
        .all()
    )
    if not rows:
        return None
    if len(rows) > 1:
        raise RuntimeError(
            "multiple miaoshou_web credentials; set MIAOSHOU_WEB_ACCOUNT_ID"
        )
    return load_credentials(session, WEB_PROVIDER, rows[0])


def _build_client(credentials: CredentialsView) -> MiaoshouPurchaseWebClient:
    if not credentials.refresh_token:
        raise RuntimeError("miaoshou_web credential missing encrypted x-app-zebra")
    extra = credentials.extra or {}
    return MiaoshouPurchaseWebClient(
        cookie=credentials.access_token,
        x_app_zebra=credentials.refresh_token,
        base_url=str(extra.get("base_url") or DEFAULT_BASE_URL),
        front_version=str(extra.get("front_version") or ""),
        referer=str(extra.get("referer") or "/order/purchase_record"),
    )


def _fetch_all_pages(
    client: PurchaseWebClient,
    *,
    max_retries: int,
) -> tuple[list[dict[str, Any]], int, int]:
    def fetch(page: int) -> dict[str, Any]:
        last_error: Exception | None = None
        for attempt in range(max_retries + 1):
            try:
                return client.search_page(page=page, page_size=PAGE_SIZE)
            except Exception as exc:
                last_error = exc
                if attempt >= max_retries:
                    raise
                time.sleep(attempt + 1)
        assert last_error is not None
        raise last_error

    def validate_page(
        payload: dict[str, Any],
        *,
        expected_page: int,
        expected_total: int | None = None,
        expected_page_size: int | None = None,
    ) -> tuple[list[dict[str, Any]], int, int]:
        required = {"list", "total", "page", "pageSize"}
        missing = required - payload.keys()
        if missing:
            raise RuntimeError(f"purchase page missing fields: {sorted(missing)}")
        if not isinstance(payload["list"], list):
            raise TypeError("purchase page list is not an array")
        try:
            total_value = int(payload["total"])
            page_value = int(payload["page"])
            page_size_value = int(payload["pageSize"])
        except (TypeError, ValueError) as exc:
            raise RuntimeError("invalid purchase pagination metadata") from exc
        if total_value < 0 or page_size_value < 1 or page_size_value > PAGE_SIZE:
            raise RuntimeError("purchase pagination metadata out of range")
        if page_value != expected_page:
            raise RuntimeError(
                f"purchase page mismatch: got={page_value} expected={expected_page}"
            )
        if expected_total is not None and total_value != expected_total:
            raise RuntimeError("purchase total changed during pagination")
        if expected_page_size is not None and page_size_value != expected_page_size:
            raise RuntimeError("purchase pageSize changed during pagination")
        if any(not isinstance(row, dict) for row in payload["list"]):
            raise RuntimeError("purchase page contains non-object row")
        return list(payload["list"]), total_value, page_size_value

    first_rows, total, page_size = validate_page(fetch(1), expected_page=1)
    total_pages = max(1, math.ceil(total / page_size))
    if total_pages > MAX_PAGES:
        raise RuntimeError(
            f"purchase pagination exceeds safety cap: {total_pages}>{MAX_PAGES}"
        )
    rows = first_rows
    for page in range(2, total_pages + 1):
        page_rows, _, _ = validate_page(
            fetch(page),
            expected_page=page,
            expected_total=total,
            expected_page_size=page_size,
        )
        rows.extend(page_rows)
    if len(rows) != total:
        raise RuntimeError(
            f"purchase pagination incomplete: fetched={len(rows)} advertised={total}"
        )
    deduped: dict[str, dict[str, Any]] = {}
    for row in rows:
        row_id = str(row.get("purchaseOrderFilterId") or "")
        if not row_id:
            raise RuntimeError("purchase row missing purchaseOrderFilterId")
        prior = deduped.get(row_id)
        if prior is not None and prior != row:
            raise RuntimeError("purchase pagination contains conflicting row ids")
        deduped[row_id] = row
    # The live endpoint sometimes repeats bit-for-bit-identical rows across
    # pages. Collapse only those exact duplicates; a conflicting repeated row
    # is rejected above rather than silently choosing one.
    return list(deduped.values()), total_pages, total


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


def _ordered_groups(
    items: list[Any], key: str
) -> tuple[list[str], dict[str, list[dict[str, Any]]]]:
    order: list[str] = []
    grouped: dict[str, list[dict[str, Any]]] = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        value = str(item.get(key) or "")
        if not value:
            continue
        if value not in grouped:
            grouped[value] = []
            order.append(value)
        grouped[value].append(item)
    return order, grouped


def _observations_from_order(
    order: dict[str, Any],
) -> tuple[list[PriceObservation], list[dict[str, Any]]]:
    source_order, source_groups = _ordered_groups(
        order.get("purchaseItems") or [], "sourceItemId"
    )
    platform_items = [
        item
        for package in order.get("opOrderPackageList") or []
        if isinstance(package, dict)
        for item in package.get("purchaseItems") or []
    ]
    platform_order, _platform_groups = _ordered_groups(platform_items, "platformItemId")
    order_sn = str(order.get("purchaseOrderSn") or "")
    if len(source_order) != len(platform_order):
        return [], [
            {
                "issue_type": "GROUP_COUNT_MISMATCH",
                "purchase_order_sn": order_sn,
                "source_group_count": len(source_order),
                "platform_group_count": len(platform_order),
            }
        ]
    observations: list[PriceObservation] = []
    issues: list[dict[str, Any]] = []
    purchase_at_raw = str(order.get("gmtPurchaseOrderStart") or "")
    for source_item_id, spu_id in zip(source_order, platform_order, strict=True):
        numerator = Decimal(0)
        denominator = Decimal(0)
        evidence: list[dict[str, str]] = []
        for line in source_groups[source_item_id]:
            try:
                price = Decimal(str(line.get("sourceUnitPrice")))
                quantity = Decimal(str(line.get("sourceQuantity") or "0"))
            except (InvalidOperation, TypeError):
                issues.append(
                    {
                        "issue_type": "INVALID_SOURCE_LINE",
                        "purchase_order_sn": order_sn,
                        "source_item_id": source_item_id,
                    }
                )
                continue
            if (
                not price.is_finite()
                or not quantity.is_finite()
                or price <= 0
                or quantity <= 0
            ):
                issues.append(
                    {
                        "issue_type": "INVALID_SOURCE_LINE",
                        "purchase_order_sn": order_sn,
                        "source_item_id": source_item_id,
                        "unit_price": str(price),
                        "quantity": str(quantity),
                    }
                )
                continue
            numerator += price * quantity
            denominator += quantity
            evidence.append(
                {
                    "source_sku_id": str(line.get("sourceSkuId") or ""),
                    "unit_price": str(price),
                    "quantity": str(quantity),
                }
            )
        if denominator <= 0:
            issues.append(
                {
                    "issue_type": "SOURCE_GROUP_NO_VALID_PRICE",
                    "purchase_order_sn": order_sn,
                    "source_item_id": source_item_id,
                    "spu_id": spu_id,
                }
            )
            continue
        observations.append(
            PriceObservation(
                spu_id=spu_id,
                source_item_id=source_item_id,
                unit_cost=(numerator / denominator).quantize(Decimal("0.0001")),
                purchase_order_sn=order_sn,
                purchase_at=_parse_time(purchase_at_raw),
                purchase_at_raw=purchase_at_raw,
                status=str(order.get("purchaseOrderStatus") or ""),
                filter_id=str(order.get("purchaseOrderFilterId") or ""),
                source_lines=tuple(evidence),
            )
        )
    return observations, issues


def clean_latest_prices(
    orders: list[dict[str, Any]],
) -> tuple[dict[str, PriceObservation], list[dict[str, Any]]]:
    unique: dict[tuple[str, str, str], PriceObservation] = {}
    issues: list[dict[str, Any]] = []
    for order in orders:
        observations, order_issues = _observations_from_order(order)
        issues.extend(order_issues)
        for observation in observations:
            key = (
                observation.purchase_order_sn,
                observation.source_item_id,
                observation.spu_id,
            )
            previous = unique.get(key)
            if previous is None or observation.filter_id > previous.filter_id:
                unique[key] = observation

    by_spu: dict[str, list[PriceObservation]] = {}
    for observation in unique.values():
        if observation.status in _EXCLUDED_STATUSES:
            continue
        by_spu.setdefault(observation.spu_id, []).append(observation)

    latest: dict[str, PriceObservation] = {}
    for spu_id, observations in by_spu.items():
        newest = max(
            observation.purchase_at or datetime.min.replace(tzinfo=UTC)
            for observation in observations
        )
        candidates = [
            observation
            for observation in observations
            if (observation.purchase_at or datetime.min.replace(tzinfo=UTC)) == newest
        ]
        prices = {observation.unit_cost for observation in candidates}
        if len(prices) != 1:
            issues.append(
                {
                    "issue_type": "LATEST_PRICE_AMBIGUOUS",
                    "spu_id": spu_id,
                    "prices": sorted(str(price) for price in prices),
                    "purchase_order_sns": sorted(
                        {observation.purchase_order_sn for observation in candidates}
                    ),
                }
            )
            continue
        latest[spu_id] = max(
            candidates,
            key=lambda observation: (
                observation.purchase_order_sn,
                observation.filter_id,
                observation.source_item_id,
            ),
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


def _record_raw_orders(
    session: Session,
    *,
    credential_id: int,
    orders: list[dict[str, Any]],
) -> int:
    inserted = 0
    now = datetime.now(UTC)
    for order in orders:
        order_sn = str(order.get("purchaseOrderSn") or "")
        if not order_sn:
            continue
        safe_order = _redact_secrets(order)
        canonical = json.dumps(safe_order, ensure_ascii=False, sort_keys=True)
        payload_hash = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        stmt = pg_insert(MiaoshouPurchaseOrderRawRecord).values(
            credential_id=credential_id,
            external_purchase_order_id=order_sn,
            endpoint=ENDPOINT,
            payload=safe_order,
            payload_hash=payload_hash,
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


def _resolve_spu_issues(session: Session, *, credential_id: int, spu_id: str) -> None:
    rows = session.execute(
        select(MiaoshouSyncIssue)
        .where(MiaoshouSyncIssue.credential_id == credential_id)
        .where(MiaoshouSyncIssue.resource == RESOURCE)
        .where(MiaoshouSyncIssue.external_id == spu_id)
        .where(MiaoshouSyncIssue.resolved_at.is_(None))
    ).scalars()
    resolved_at = datetime.now(UTC)
    for row in rows:
        row.resolved_at = resolved_at


def _resolve_candidate_product(session: Session, spu_id: str) -> tuple[int | None, str]:
    rows = (
        session.execute(
            select(ChannelProduct.id).where(ChannelProduct.spu_id == spu_id)
        )
        .scalars()
        .all()
    )
    if not rows:
        return None, "missing_product"
    if len(rows) > 1:
        return None, "ambiguous_product"
    return rows[0], "ready"


def _sync_manual_cost(
    session: Session,
    *,
    spu_pk: int,
    observation: PriceObservation,
) -> tuple[int, bool, bool]:
    lock_manual_cost_spu(session, spu_pk=spu_pk)
    current = session.execute(
        select(ManualProductCost)
        .where(ManualProductCost.spu_pk == spu_pk)
        .where(ManualProductCost.valid_to.is_(None))
    ).scalar_one_or_none()
    if (
        current is not None
        and current.unit_cost == observation.unit_cost
        and current.currency == "CNY"
    ):
        return current.id, False, False
    if current is not None and not _is_automated_cost(current):
        return current.id, False, True

    now = datetime.now(UTC)
    session.execute(
        update(ManualProductCost)
        .where(ManualProductCost.spu_pk == spu_pk)
        .where(ManualProductCost.valid_to.is_(None))
        .values(valid_to=now, updated_at=now)
    )
    session.flush()
    row = ManualProductCost(
        spu_pk=spu_pk,
        unit_cost=observation.unit_cost,
        currency="CNY",
        valid_from=now,
        valid_to=None,
        note=(
            f"Miaoshou purchase order {observation.purchase_order_sn}; "
            f"1688 offer {observation.source_item_id}; "
            f"purchase time {observation.purchase_at_raw}; "
            "qty-weighted sourceUnitPrice; scheduled sync"
        ),
        created_by="job:miaoshou.purchase_price_clean",
    )
    session.add(row)
    session.flush()
    return row.id, True, False


def _is_automated_cost(row: ManualProductCost) -> bool:
    if row.created_by == f"job:{JOB_NAME}":
        return True
    note = row.note or ""
    return "qty-weighted sourceUnitPrice" in note and any(
        marker in note
        for marker in ("full import", "refresh 2026-09-30", "scheduled sync")
    )


def _upsert_candidate(
    session: Session,
    *,
    credential_id: int,
    observation: PriceObservation,
    spu_pk: int | None,
    manual_cost_id: int | None,
    resolution_status: str,
) -> None:
    now = datetime.now(UTC)
    evidence = {
        "source_lines": list(observation.source_lines),
        "purchase_at_raw": observation.purchase_at_raw,
        "filter_id": observation.filter_id,
    }
    values = {
        "credential_id": credential_id,
        "spu_id": observation.spu_id,
        "spu_pk": spu_pk,
        "manual_cost_id": manual_cost_id,
        "unit_cost": observation.unit_cost,
        "currency": "CNY",
        "source_purchase_order_sn": observation.purchase_order_sn,
        "source_item_id": observation.source_item_id,
        "source_purchase_at": observation.purchase_at,
        "source_status": observation.status,
        "calculation_method": "quantity_weighted_mean",
        "calculation_version": _CALCULATION_VERSION,
        "resolution_status": resolution_status,
        "evidence": evidence,
        "last_seen_at": now,
        "synced_at": now,
    }
    stmt = pg_insert(MiaoshouPurchasePriceCandidate).values(**values)
    session.execute(
        stmt.on_conflict_do_update(
            index_elements=["credential_id", "spu_id"],
            set_={
                key: value
                for key, value in values.items()
                if key not in ("credential_id", "spu_id")
            },
        )
    )


def _skipped_result(reason: str) -> dict[str, Any]:
    return {
        "skipped": True,
        "reason": reason,
        "pages_walked": 0,
        "orders_seen": 0,
        "candidate_spus": 0,
        "manual_costs_written": 0,
        "manual_costs_unchanged": 0,
        "manual_overrides": 0,
        "missing_products": 0,
        "issues": 0,
    }


def sync_purchase_prices(
    session: Session,
    *,
    client: PurchaseWebClient | None = None,
    external_account_id: str | None = None,
    max_retries: int = 3,
) -> dict[str, Any]:
    """Run one complete purchase-price fetch/clean/publish cycle."""
    with run_job(session, job_name=JOB_NAME) as job:
        if not _schema_ready(session):
            result = _skipped_result("migration 0046 not applied")
            finish_job(
                session, job, status="skipped", extra={"reason": result["reason"]}
            )
            return result
        credentials = _resolve_credentials(
            session, external_account_id=external_account_id
        )
        if credentials is None:
            result = _skipped_result("miaoshou_web credentials not configured")
            finish_job(
                session, job, status="skipped", extra={"reason": result["reason"]}
            )
            return result
        credential_id = credentials.id
        if client is None:
            client = _build_client(credentials)

        orders, pages_walked, advertised_total = _fetch_all_pages(
            client, max_retries=max_retries
        )
        raw_inserted = _record_raw_orders(
            session,
            credential_id=credential_id,
            orders=orders,
        )
        latest, cleaning_issues = clean_latest_prices(orders)
        for issue in cleaning_issues:
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
        unchanged = 0
        manual_overrides = 0
        missing = 0
        product_issues = 0
        for observation in latest.values():
            spu_pk, resolution_status = _resolve_candidate_product(
                session, observation.spu_id
            )
            manual_cost_id: int | None = None
            if spu_pk is not None:
                manual_cost_id, changed, overridden = _sync_manual_cost(
                    session,
                    spu_pk=spu_pk,
                    observation=observation,
                )
                if overridden:
                    resolution_status = "manual_override"
                    manual_overrides += 1
                elif changed:
                    written += 1
                else:
                    unchanged += 1
                _resolve_spu_issues(
                    session,
                    credential_id=credential_id,
                    spu_id=observation.spu_id,
                )
            else:
                if resolution_status == "missing_product":
                    missing += 1
                product_issues += 1
                _record_issue(
                    session,
                    credential_id=credential_id,
                    issue_type=resolution_status.upper(),
                    external_id=observation.spu_id,
                    details={
                        "spu_id": observation.spu_id,
                        "unit_cost": str(observation.unit_cost),
                        "purchase_order_sn": observation.purchase_order_sn,
                    },
                )
            _upsert_candidate(
                session,
                credential_id=credential_id,
                observation=observation,
                spu_pk=spu_pk,
                manual_cost_id=manual_cost_id,
                resolution_status=resolution_status,
            )

        issues = len(cleaning_issues) + product_issues
        job.rows_total = len(orders)
        job.rows_inserted = raw_inserted + len(latest) + written
        job.rows_updated = unchanged
        job.rows_failed = issues
        job.extra = {
            "pages_walked": pages_walked,
            "advertised_total": advertised_total,
            "candidate_spus": len(latest),
            "raw_inserted": raw_inserted,
            "manual_costs_written": written,
            "manual_costs_unchanged": unchanged,
            "manual_overrides": manual_overrides,
            "missing_products": missing,
            "calculation_version": _CALCULATION_VERSION,
            "finished_at_iso": datetime.now(UTC).isoformat(),
        }
        return {
            "skipped": False,
            "reason": None,
            "pages_walked": pages_walked,
            "orders_seen": len(orders),
            "candidate_spus": len(latest),
            "manual_costs_written": written,
            "manual_costs_unchanged": unchanged,
            "manual_overrides": manual_overrides,
            "missing_products": missing,
            "issues": issues,
        }


__all__ = [
    "JOB_NAME",
    "MiaoshouPurchaseWebClient",
    "PriceObservation",
    "clean_latest_prices",
    "sync_purchase_prices",
]
