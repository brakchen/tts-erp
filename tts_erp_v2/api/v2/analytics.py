"""/v2/analytics/sync/* — Chrome extension (tk-adv-cost-monitor) analytics ingest.

v4 protocol（tech-doc/analytics/daily-sync-with-coverage.md）：
- POST /dumps: 结构化 rows 写入 ad_today / ad_daily / ad_monthly
- GET /coverage: 批量查询已同步的 coverage 数据
- POST /plugin-logs: 插件运行时日志上传

Handler 结构说明：
- ``post_dumps`` 需要在 Pydantic 解析**之前**拿原始 body（413 尺寸闸 +
  MALFORMED_JSON 与 SCHEMA_INVALID 的区分），原始 body 只能异步读,
  因此用 async 依赖 ``_raw_body`` 喂给同步 handler —— handler 本体保持
  同步 + ``Depends(get_session)``,不引入 async session。
"""

from __future__ import annotations

import json
import logging
import re
import sys
import uuid
from datetime import date, datetime
from typing import Any

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse
from pydantic import AliasChoices, BaseModel, Field, ValidationError, field_validator
from sqlalchemy.orm import Session

from tts_erp_v2.api.deps import get_session

# ─── Config ───────────────────────────────────────────────────────────

PROTOCOL_VERSION = 4
SUPPORTED_PROTOCOL_VERSIONS = {4}
PROTOCOL_VERSION_HEADER = "X-Protocol-Version"
MAX_BODY_BYTES = 2 * 1024 * 1024  # 2 MB per protocol §5
MAX_RESPONSE_DATA_BYTES = 256 * 1024  # cap individual response_data JSON

_PATH_COVERAGE = "/v2/analytics/sync/coverage"
_PATH_DUMPS = "/v2/analytics/sync/dumps"

# Known endpoint whitelist (used for coverage validation)
_KNOWN_ENDPOINTS: dict[str, str] = {
    "/oec_ads/shopping/v1/oec/stat/post_product_list": "productAnalyses",
    "/oec_ads/shopping/v1/oec/stat/campaign_opt_log_list": "campaignChangeLogs",
}


# ─── Logger（审计迁移自 DB → 文件日志）──────────────────────────────
log = logging.getLogger("tts_erp_v2.analytics.ingest")
log.setLevel(logging.INFO)
if not any(
    isinstance(h, logging.StreamHandler) and h.stream is sys.stdout
    for h in log.handlers
):
    _ingest_stdout = logging.StreamHandler(sys.stdout)
    _ingest_stdout.setFormatter(logging.Formatter("%(message)s"))
    log.addHandler(_ingest_stdout)


def _log_ingest_event(
    *,
    level: int,
    request_id: str | None,
    key_prefix: str | None,
    method: str,
    path: str,
    status: int,
    records_in: int | None = None,
    records_ok: int | None = None,
    records_rej: int | None = None,
    error_code: str | None = None,
    message: str | None = None,
) -> None:
    """Emit a single key=value ingest log line."""
    parts: list[str] = [
        f"request_id={request_id or '-'}",
        f"key_prefix={key_prefix or '-'}",
        f"method={method}",
        f"path={path}",
        f"status={status}",
    ]
    if records_in is not None:
        parts.append(f"records_in={records_in}")
    if records_ok is not None:
        parts.append(f"records_ok={records_ok}")
    if records_rej is not None:
        parts.append(f"records_rej={records_rej}")
    if error_code:
        parts.append(f"error_code={error_code}")
    if message:
        parts.append(f"message={_sanitize_message(message)}")
    log.log(level, " ".join(parts))


def _sanitize_message(message: Any) -> str:
    """Mirror the previous DB-column + stderr sanitization: whitespace
    flattened, ≤500 chars, no newlines (single-line grep-friendly)."""
    return " ".join(str(message).split())[:500]


# ─── Scope-grant helper ──────────────────────────────────────────────


def scope_grants(scopes, *, seller_id, advertiser_id):
    """Return True iff the token's scopes cover the requested scope."""
    if not scopes or "*" in scopes:
        return True
    seller_grants = [s[len("seller:") :] for s in scopes if s.startswith("seller:")]
    advertiser_grants = [
        s[len("advertiser:") :] for s in scopes if s.startswith("advertiser:")
    ]
    known = len(seller_grants) + len(advertiser_grants)
    if known != len(scopes):
        return False
    if seller_grants and seller_id not in seller_grants:
        return False
    return not (advertiser_grants and advertiser_id not in advertiser_grants)


# ─── Router ───────────────────────────────────────────────────────────


router = APIRouter(prefix="/v2/analytics/sync", tags=["analytics"])


# ─── Models ───────────────────────────────────────────────────────────


class ScopeIn(BaseModel):
    sellerId: str = Field(min_length=1, max_length=128)
    advertiserId: str = Field(min_length=1, max_length=128)
    shopName: str | None = None


class DumpBodyIn(BaseModel):
    """dump 协议 body 里的 dump object 字段（v4 结构化 rows）。"""

    endpoint: str = Field(min_length=1, max_length=512)
    method: str = Field(min_length=1, max_length=16)
    day: date | None = None
    kind: str | None = Field(default=None, max_length=16)
    campaignId: str = Field(min_length=1, max_length=128)
    rows: list[dict[str, Any]] | None = None
    yearMonth: str | None = Field(default=None, max_length=7)
    request: dict[str, Any]
    response: dict[str, Any]
    # 统一命名 createdAt（2026-09-10 用户拍板）。capturedAt 仅作旧插件兼容别名，
    # 插件 wire 字段 / 文档 / 测试一律用 createdAt。
    createdAt: datetime = Field(
        validation_alias=AliasChoices("createdAt", "capturedAt"),
    )
    source: str = Field(default="tiktok-shop-data-sync", min_length=1, max_length=64)
    schemaVersion: int = Field(default=1, ge=1)

    @field_validator("createdAt")
    @classmethod
    def _created_at_must_be_utc(cls, v: datetime) -> datetime:
        if v.tzinfo is None:
            raise ValueError(
                "createdAt must include a timezone (use ISO-8601 with 'Z' or '+00:00')"
            )
        return v


class DumpRequest(BaseModel):
    """dump 协议顶层 envelope。"""

    protocolVersion: int = Field(default=PROTOCOL_VERSION)
    requestId: str | None = Field(default=None, min_length=1, max_length=128)
    scope: ScopeIn
    dump: DumpBodyIn


# ─── 依赖：原始 body ────────────────────────────────────────────────


async def _raw_body(request: Request) -> bytes:
    """读原始请求体（async 依赖）。"""
    return await request.body()


# ─── Coverage endpoint (v4) ──────────────────────────────────────────


@router.get("/coverage")
def get_coverage_endpoint(
    request: Request,
    sellerId: str = Query(min_length=1, max_length=128),
    advertiserId: str = Query(min_length=1, max_length=128),
    endpoint: str = Query(min_length=1, max_length=512),
    kind: str = Query(..., max_length=16),
    startDay: date | None = Query(default=None),
    endDay: date | None = Query(default=None),
    startMonth: str | None = Query(default=None, max_length=7),
    endMonth: str | None = Query(default=None, max_length=7),
    # 2026-09-11 加分页（隐患 #3）：默认 page=1, pageSize=500。客户端 fetchBatchCoverage
    # 会自动迭代到最后一页，把所有 campaign 合并成一个 BatchCoverageResponse。
    # 不在 Query 上加 le/ge 是因为 FastAPI 会返 422，与端点其它校验（返 400）不一致；
    # 在下面手动校验返 400 SCHEMA_INVALID。
    page: int = Query(default=1, description="分页页码，从 1 开始"),
    pageSize: int = Query(default=500, description="每页 campaign 数，1-1000"),
    sess: Session = Depends(get_session),
) -> JSONResponse:
    """Coverage 批量查询（方案 B）：分页返回 campaign 的覆盖数据。

    tech-doc/analytics/daily-sync-with-coverage.md §5.1。
    支持 kind=daily 和 kind=monthly 两种粒度。
    响应新增 pagination 字段：{page, pageSize, totalCampaigns, totalPages, hasMore}。
    """
    request_id = _request_id_from_headers(request)
    key_prefix = _key_prefix(request)
    audit_path = (
        f"{_PATH_COVERAGE}?sellerId={sellerId}&advertiserId={advertiserId}"
        f"&endpoint={endpoint}&kind={kind}"
        f"&startDay={startDay or ''}&endDay={endDay or ''}"
        f"&startMonth={startMonth or ''}&endMonth={endMonth or ''}"
    )

    if not scope_grants(
        tuple(_scopes(request)),
        seller_id=sellerId,
        advertiser_id=advertiserId,
    ):
        _log_ingest_event(
            level=logging.WARNING,
            request_id=request_id,
            key_prefix=key_prefix,
            method="GET",
            path=audit_path,
            status=403,
            error_code="SCOPE_DENIED",
        )
        return _error_response(
            status=403,
            code="SCOPE_DENIED",
            message="api key does not grant access to this scope",
            request_id=request_id,
            retryable=False,
        )

    storage_key_value = _KNOWN_ENDPOINTS.get(endpoint)
    if storage_key_value is None:
        return _audit_and_error(
            request_id=request_id,
            status=400,
            code="SCHEMA_INVALID",
            message=f"unknown endpoint: {endpoint}",
            retryable=False,
            key_prefix=key_prefix,
            error_code="SCHEMA_INVALID",
            method="GET",
            path=audit_path,
        )

    if kind not in ("daily", "monthly"):
        return _audit_and_error(
            request_id=request_id,
            status=400,
            code="SCHEMA_INVALID",
            message="kind must be 'daily' or 'monthly'",
            retryable=False,
            key_prefix=key_prefix,
            error_code="SCHEMA_INVALID",
            method="GET",
            path=audit_path,
        )

    # 2026-09-11：手动校验 page/pageSize（与端点其它校验返 400 一致）
    if page < 1:
        return _audit_and_error(
            request_id=request_id,
            status=400,
            code="SCHEMA_INVALID",
            message="page must be >= 1",
            retryable=False,
            key_prefix=key_prefix,
            error_code="SCHEMA_INVALID",
            method="GET",
            path=audit_path,
        )
    if pageSize < 1 or pageSize > 1000:
        return _audit_and_error(
            request_id=request_id,
            status=400,
            code="SCHEMA_INVALID",
            message="pageSize must be between 1 and 1000",
            retryable=False,
            key_prefix=key_prefix,
            error_code="SCHEMA_INVALID",
            method="GET",
            path=audit_path,
        )

    if kind == "daily":
        if startDay is None or endDay is None:
            return _audit_and_error(
                request_id=request_id,
                status=400,
                code="SCHEMA_INVALID",
                message="startDay and endDay are required for kind=daily",
                retryable=False,
                key_prefix=key_prefix,
                error_code="SCHEMA_INVALID",
                method="GET",
                path=audit_path,
            )
        if startDay > endDay:
            return _audit_and_error(
                request_id=request_id,
                status=400,
                code="SCHEMA_INVALID",
                message="startDay must be <= endDay",
                retryable=False,
                key_prefix=key_prefix,
                error_code="SCHEMA_INVALID",
                method="GET",
                path=audit_path,
            )
    else:  # kind == "monthly"
        if startMonth is None or endMonth is None:
            return _audit_and_error(
                request_id=request_id,
                status=400,
                code="SCHEMA_INVALID",
                message="startMonth and endMonth are required for kind=monthly",
                retryable=False,
                key_prefix=key_prefix,
                error_code="SCHEMA_INVALID",
                method="GET",
                path=audit_path,
            )
        if not re.match(r"^\d{4}-\d{2}$", startMonth) or not re.match(
            r"^\d{4}-\d{2}$", endMonth
        ):
            return _audit_and_error(
                request_id=request_id,
                status=400,
                code="SCHEMA_INVALID",
                message="startMonth/endMonth must be YYYY-MM format",
                retryable=False,
                key_prefix=key_prefix,
                error_code="SCHEMA_INVALID",
                method="GET",
                path=audit_path,
            )
        if startMonth > endMonth:
            return _audit_and_error(
                request_id=request_id,
                status=400,
                code="SCHEMA_INVALID",
                message="startMonth must be <= endMonth",
                retryable=False,
                key_prefix=key_prefix,
                error_code="SCHEMA_INVALID",
                method="GET",
                path=audit_path,
            )

    from tts_erp_v2.analytics.repository import (
        get_coverage_daily,
        get_coverage_monthly,
    )

    # 2026-09-11：分页实现。get_coverage_* 现在返回 (campaigns_page, totalCampaigns)
    if kind == "daily":
        campaigns, total_campaigns = get_coverage_daily(
            sess,
            seller_id=sellerId,
            advertiser_id=advertiserId,
            endpoint=endpoint,
            start_day=startDay,  # type: ignore[arg-type]
            end_day=endDay,  # type: ignore[arg-type]
            page=page,
            page_size=pageSize,
        )
        total_requested = (endDay - startDay).days + 1  # type: ignore[operator]
    else:
        campaigns, total_campaigns = get_coverage_monthly(
            sess,
            seller_id=sellerId,
            advertiser_id=advertiserId,
            endpoint=endpoint,
            start_month=startMonth,  # type: ignore[arg-type]
            end_month=endMonth,  # type: ignore[arg-type]
            page=page,
            page_size=pageSize,
        )
        # 防御型 parse：上方的 re.match 锁了 YYYY-MM 格式，但万一未来加了手调用。
        try:
            sy, sm = int(startMonth[:4]), int(startMonth[5:])  # type: ignore[index]
            ey, em = int(endMonth[:4]), int(endMonth[5:])  # type: ignore[index]
        except (TypeError, ValueError) as exc:
            return _audit_and_error(
                request_id=request_id,
                status=400,
                code="SCHEMA_INVALID",
                message=f"startMonth/endMonth must be YYYY-MM format: {exc}",
                retryable=False,
                key_prefix=key_prefix,
                error_code="SCHEMA_INVALID",
                method="GET",
                path=audit_path,
            )
        total_requested = (ey - sy) * 12 + (em - sm + 1)

    total_pages = (
        (total_campaigns + pageSize - 1) // pageSize if total_campaigns > 0 else 0
    )
    has_more = page < total_pages

    coverage_data: dict[str, object] = {
        "kind": kind,
        "endpoint": endpoint,
        "storageKey": storage_key_value,
        "totalRequested": total_requested,
        "campaigns": {
            cid: {
                "coveredPeriods": periods,
                "totalCovered": len(periods),
            }
            for cid, periods in campaigns.items()
        },
        "pagination": {
            "page": page,
            "pageSize": pageSize,
            "totalCampaigns": total_campaigns,
            "totalPages": total_pages,
            "hasMore": has_more,
        },
    }
    if kind == "daily":
        coverage_data["startDay"] = startDay.isoformat()  # type: ignore[union-attr]
        coverage_data["endDay"] = endDay.isoformat()  # type: ignore[union-attr]
    else:
        coverage_data["startMonth"] = startMonth
        coverage_data["endMonth"] = endMonth

    _log_ingest_event(
        level=logging.INFO,
        request_id=request_id,
        key_prefix=key_prefix,
        method="GET",
        path=audit_path,
        status=200,
        records_in=1,
        records_ok=1,
    )

    return JSONResponse(
        status_code=200,
        content={
            "code": 0,
            "requestId": request_id,
            "data": coverage_data,
        },
    )


# ─── Dumps endpoint (v4 protocol only) ───────────────────────────────


@router.post("/dumps")
def post_dumps(
    request: Request,
    body_bytes: bytes = Depends(_raw_body),
    sess: Session = Depends(get_session),  # noqa: B008
) -> JSONResponse:
    """v4 结构化 rows 写入协议。

    协议契约（tech-doc/analytics/daily-sync-with-coverage.md §5）：
    - protocolVersion = 4
    - dump.kind ∈ {daily, today, monthly}
    - dump.rows = 结构化行数组
    - 2 MB body 上限
    """
    request_id = _request_id_from_headers(request)
    key_prefix = _key_prefix(request)
    method = "POST"
    path = _PATH_DUMPS

    cl = _parse_content_length(request.headers.get("content-length"))
    if cl is not None and cl > MAX_BODY_BYTES:
        return _audit_and_error(
            request_id=request_id,
            status=413,
            code="PAYLOAD_TOO_LARGE",
            message=f"Content-Length {cl} exceeds maximum {MAX_BODY_BYTES} bytes",
            retryable=False,
            key_prefix=key_prefix,
            error_code="PAYLOAD_TOO_LARGE",
            method=method,
            path=path,
        )

    if len(body_bytes) > MAX_BODY_BYTES:
        return _audit_and_error(
            request_id=request_id,
            status=413,
            code="PAYLOAD_TOO_LARGE",
            message=f"actual body size {len(body_bytes)} exceeds maximum {MAX_BODY_BYTES} bytes",
            retryable=False,
            key_prefix=key_prefix,
            error_code="PAYLOAD_TOO_LARGE",
            method=method,
            path=path,
        )

    try:
        body = json.loads(body_bytes)
    except json.JSONDecodeError as exc:
        return _audit_and_error(
            request_id=request_id,
            status=400,
            code="MALFORMED_JSON",
            message=f"JSON parse error: {exc.msg}",
            retryable=False,
            key_prefix=key_prefix,
            error_code="MALFORMED_JSON",
            method=method,
            path=path,
        )

    try:
        payload = DumpRequest.model_validate(body)
    except ValidationError as exc:
        return _audit_and_error(
            request_id=request_id,
            status=400,
            code="SCHEMA_INVALID",
            message=str(exc),
            retryable=False,
            key_prefix=key_prefix,
            error_code="SCHEMA_INVALID",
            method=method,
            path=path,
            structured_errors=_sanitize_pydantic_errors(exc),
        )
    except Exception as exc:  # noqa: BLE001
        return _audit_and_error(
            request_id=request_id,
            status=400,
            code="SCHEMA_INVALID",
            message=str(exc),
            retryable=False,
            key_prefix=key_prefix,
            error_code="SCHEMA_INVALID",
            method=method,
            path=path,
            structured_errors=[
                {
                    "loc": [],
                    "msg": type(exc).__name__,
                    "type": "internal_error",
                }
            ],
        )

    if payload.protocolVersion != 4:
        return _audit_and_error(
            request_id=request_id,
            status=400,
            code="UNSUPPORTED_PROTOCOL_VERSION",
            message=(
                f"server supports protocolVersion 4 only, "
                f"client sent {payload.protocolVersion}"
            ),
            retryable=False,
            key_prefix=key_prefix,
            error_code="UNSUPPORTED_PROTOCOL_VERSION",
            method=method,
            path=path,
        )

    if not scope_grants(
        tuple(_scopes(request)),
        seller_id=payload.scope.sellerId,
        advertiser_id=payload.scope.advertiserId,
    ):
        _log_ingest_event(
            level=logging.WARNING,
            request_id=payload.requestId or request_id,
            key_prefix=key_prefix,
            method=method,
            path=path,
            status=403,
            records_in=0,
            records_ok=0,
            records_rej=0,
            error_code="SCOPE_DENIED",
        )
        return _error_response(
            status=403,
            code="SCOPE_DENIED",
            message="api key does not grant access to this scope",
            request_id=request_id,
            retryable=False,
        )

    dump_kind = payload.dump.kind
    if dump_kind is None:
        return _audit_and_error(
            request_id=request_id,
            status=400,
            code="SCHEMA_INVALID",
            message="dump.kind is required for protocolVersion 4",
            retryable=False,
            key_prefix=key_prefix,
            error_code="SCHEMA_INVALID",
            method=method,
            path=_PATH_DUMPS,
        )
    if dump_kind not in ("daily", "today", "monthly"):
        return _audit_and_error(
            request_id=request_id,
            status=400,
            code="SCHEMA_INVALID",
            message=f"dump.kind must be daily/today/monthly for v4, got {dump_kind!r}",
            retryable=False,
            key_prefix=key_prefix,
            error_code="SCHEMA_INVALID",
            method=method,
            path=_PATH_DUMPS,
        )

    rows = payload.dump.rows
    if rows is None:
        return _audit_and_error(
            request_id=request_id,
            status=400,
            code="SCHEMA_INVALID",
            message="dump.rows is required for protocolVersion 4",
            retryable=False,
            key_prefix=key_prefix,
            error_code="SCHEMA_INVALID",
            method=method,
            path=_PATH_DUMPS,
        )

    if dump_kind in ("daily", "today"):
        if payload.dump.day is None:
            return _audit_and_error(
                request_id=request_id,
                status=400,
                code="SCHEMA_INVALID",
                message=f"dump.day is required for kind={dump_kind}",
                retryable=False,
                key_prefix=key_prefix,
                error_code="SCHEMA_INVALID",
                method=method,
                path=_PATH_DUMPS,
            )
    elif dump_kind == "monthly":
        if payload.dump.yearMonth is None:
            return _audit_and_error(
                request_id=request_id,
                status=400,
                code="SCHEMA_INVALID",
                message="dump.yearMonth is required for kind=monthly",
                retryable=False,
                key_prefix=key_prefix,
                error_code="SCHEMA_INVALID",
                method=method,
                path=_PATH_DUMPS,
            )
        if not re.match(r"^\d{4}-\d{2}$", payload.dump.yearMonth):
            return _audit_and_error(
                request_id=request_id,
                status=400,
                code="SCHEMA_INVALID",
                message="dump.yearMonth must be YYYY-MM format",
                retryable=False,
                key_prefix=key_prefix,
                error_code="SCHEMA_INVALID",
                method=method,
                path=_PATH_DUMPS,
            )

    from tts_erp_v2.analytics.repository import (
        upsert_daily_rows,
        upsert_monthly_rows,
        upsert_today_rows,
    )

    repo_fn = {
        "daily": upsert_daily_rows,
        "today": upsert_today_rows,
        "monthly": upsert_monthly_rows,
    }[dump_kind]

    request_url = (
        (payload.dump.request.get("url") or "")
        if isinstance(payload.dump.request, dict)
        else ""
    )
    response_status = (
        payload.dump.response.get("status")
        if isinstance(payload.dump.response, dict)
        else None
    )

    try:
        common_kwargs: dict = {
            "sess": sess,
            "seller_id": payload.scope.sellerId,
            "advertiser_id": payload.scope.advertiserId,
            "endpoint": payload.dump.endpoint,
            "campaign_id": payload.dump.campaignId,
            "rows": rows,
            "request_url": request_url,
            "request_body": payload.dump.request,
            "response_status": response_status,
            "response_body": payload.dump.response,
            "created_at": payload.dump.createdAt,
            "request_id": payload.requestId or request_id,
            "source": payload.dump.source,
        }
        if dump_kind == "monthly":
            common_kwargs["year_month"] = payload.dump.yearMonth
        else:
            common_kwargs["day"] = payload.dump.day
        inserted = repo_fn(**common_kwargs)
    except Exception as exc:  # noqa: BLE001 — 落库失败统一转 500，细节进 stderr/ingest log
        exc_class = type(exc).__name__
        sys.stderr.write(
            f"[analytics-sync] v4 persistence failure: {exc_class}: {exc}\n"
        )
        _log_ingest_event(
            level=logging.ERROR,
            request_id=payload.requestId or request_id,
            key_prefix=key_prefix,
            method=method,
            path=_PATH_DUMPS,
            status=500,
            records_in=len(rows),
            records_ok=0,
            records_rej=0,
            error_code=f"INTERNAL_ERROR:{exc_class}",
        )
        return _error_response(
            status=500,
            code="INTERNAL_ERROR",
            message="persistence failure (see server logs)",
            request_id=request_id,
            retryable=True,
        )

    _log_ingest_event(
        level=logging.INFO,
        request_id=payload.requestId or request_id,
        key_prefix=key_prefix,
        method=method,
        path=_PATH_DUMPS,
        status=200,
        records_in=len(rows),
        records_ok=inserted,
        records_rej=0,
    )

    resp_data: dict[str, object] = {
        "kind": dump_kind,
        "rowCount": len(rows),
        "inserted": inserted,
        "duplicates": len(rows) - inserted,
    }
    if dump_kind == "monthly":
        resp_data["yearMonth"] = payload.dump.yearMonth
    else:
        resp_data["day"] = payload.dump.day.isoformat()  # type: ignore[union-attr]

    return JSONResponse(
        status_code=200,
        content={"code": 0, "requestId": request_id, "data": resp_data},
    )


# ─── Helpers ──────────────────────────────────────────────────────────


def _parse_content_length(value: str | None) -> int | None:
    """Content-Length header → int；垃圾值返回 None。"""
    if value is None:
        return None
    try:
        return int(value)
    except ValueError:
        return None


def _request_id_from_headers(request: Request) -> str:
    rid = request.headers.get("x-request-id")
    if rid:
        return rid[:128]
    return f"req-{uuid.uuid4()}"


def _key_prefix(request: Request) -> str | None:
    """Read the api key's 16-char prefix from ASGI scope."""
    key_hash = request.scope.get("api_key_hash")
    return key_hash[:16] if isinstance(key_hash, str) else None


def _scopes(request: Request) -> tuple[str, ...]:
    """Read the api key's scopes tuple from ASGI scope."""
    return request.scope.get("api_key_scopes", ())  # type: ignore[no-any-return]


def _error_response(
    *,
    status: int,
    code: str,
    message: str,
    request_id: str | None,
    retryable: bool,
    structured_errors: list[dict[str, object]] | None = None,
) -> JSONResponse:
    """Build a sanitized error envelope."""
    payload: dict[str, object] = {
        "code": code,
        "message": message,
        "requestId": request_id or f"req-{uuid.uuid4()}",
        "retryable": retryable,
    }
    if structured_errors:
        payload["errors"] = structured_errors
    return JSONResponse(status_code=status, content=payload)


def _sanitize_pydantic_errors(exc: ValidationError) -> list[dict[str, object]]:
    """Reduce Pydantic's errors() to the safe identifier triple."""
    sanitized: list[dict[str, object]] = []
    for err in exc.errors():
        loc_segments: list[object] = []
        for segment in err.get("loc", ()):
            if isinstance(segment, (int, str)):
                loc_segments.append(segment)
            else:
                loc_segments.append(str(segment))
        sanitized.append(
            {
                "loc": loc_segments,
                "msg": str(err.get("msg", "")),
                "type": str(err.get("type", "unknown")),
            }
        )
    return sanitized


def _audit_and_error(
    *,
    request_id: str,
    status: int,
    code: str,
    message: str,
    retryable: bool,
    key_prefix: str | None,
    error_code: str,
    method: str,
    path: str,
    structured_errors: list[dict[str, object]] | None = None,
) -> JSONResponse:
    """一行 stderr 诊断 + 结构化日志。"""
    safe_message = _sanitize_message(message)
    sys.stderr.write(
        f"[analytics-sync] reject status={status} code={code} "
        f"request_id={request_id} key_prefix={key_prefix or '-'} "
        f"method={method} path={path} message={safe_message}\n"
    )
    _log_ingest_event(
        level=logging.WARNING,
        request_id=request_id,
        key_prefix=key_prefix,
        method=method,
        path=path,
        status=status,
        error_code=error_code,
        message=safe_message,
    )
    return _error_response(
        status=status,
        code=code,
        message=message,
        request_id=request_id,
        retryable=retryable,
        structured_errors=structured_errors,
    )


# ═════════════════════════════════════════════════════════════════════
# Plugin logs upload
# ═════════════════════════════════════════════════════════════════════

_PATH_PLUGIN_LOGS = "/v2/analytics/sync/plugin-logs"


class PluginLogEntryIn(BaseModel):
    level: str = Field(default="info", max_length=16)
    message: str = Field(min_length=1, max_length=10000)
    context: dict[str, Any] | None = None
    occurredAt: datetime

    @field_validator("level")
    @classmethod
    def _level_must_be_valid(cls, v: str) -> str:
        if v not in ("info", "warn", "error"):
            raise ValueError(f"level must be one of info/warn/error, got {v!r}")
        return v

    @field_validator("occurredAt")
    @classmethod
    def _occurred_at_must_be_utc(cls, v: datetime) -> datetime:
        if v.tzinfo is None:
            raise ValueError(
                "occurredAt must include a timezone (use ISO-8601 with 'Z' or '+00:00')"
            )
        return v


class PluginLogsRequest(BaseModel):
    scope: ScopeIn
    pluginVersion: str = Field(min_length=1, max_length=64)
    logs: list[PluginLogEntryIn] = Field(min_length=1, max_length=1000)


@router.post("/plugin-logs")
def post_plugin_logs(
    request: Request,
    body_bytes: bytes = Depends(_raw_body),
    sess: Session = Depends(get_session),
) -> JSONResponse:
    """插件日志上传端点。"""
    request_id = _request_id_from_headers(request)
    key_prefix = _key_prefix(request)
    method = "POST"
    path = _PATH_PLUGIN_LOGS

    try:
        body = json.loads(body_bytes)
    except json.JSONDecodeError as exc:
        return _audit_and_error(
            request_id=request_id,
            status=400,
            code="MALFORMED_JSON",
            message=f"JSON parse error: {exc.msg}",
            retryable=False,
            key_prefix=key_prefix,
            error_code="MALFORMED_JSON",
            method=method,
            path=path,
        )

    try:
        payload = PluginLogsRequest.model_validate(body)
    except ValidationError as exc:
        return _audit_and_error(
            request_id=request_id,
            status=400,
            code="SCHEMA_INVALID",
            message=str(exc),
            retryable=False,
            key_prefix=key_prefix,
            error_code="SCHEMA_INVALID",
            method=method,
            path=path,
            structured_errors=_sanitize_pydantic_errors(exc),
        )

    if not scope_grants(
        tuple(_scopes(request)),
        seller_id=payload.scope.sellerId,
        advertiser_id=payload.scope.advertiserId,
    ):
        _log_ingest_event(
            level=logging.WARNING,
            request_id=request_id,
            key_prefix=key_prefix,
            method=method,
            path=path,
            status=403,
            error_code="SCOPE_DENIED",
        )
        return _error_response(
            status=403,
            code="SCOPE_DENIED",
            message="api key does not grant access to this scope",
            request_id=request_id,
            retryable=False,
        )

    from tts_erp_v2.analytics.repository import insert_plugin_logs

    log_dicts = [
        {
            "seller_id": payload.scope.sellerId,
            "advertiser_id": payload.scope.advertiserId,
            "plugin_version": payload.pluginVersion,
            "level": entry.level,
            "message": entry.message,
            "context": entry.context or {},
            "occurred_at": entry.occurredAt,
        }
        for entry in payload.logs
    ]

    try:
        inserted = insert_plugin_logs(sess, logs=log_dicts)
    except Exception as exc:  # noqa: BLE001
        exc_class = type(exc).__name__
        sys.stderr.write(
            f"[analytics-sync] plugin-logs persistence failure: {exc_class}: {exc}\n"
        )
        _log_ingest_event(
            level=logging.ERROR,
            request_id=request_id,
            key_prefix=key_prefix,
            method=method,
            path=path,
            status=500,
            records_in=len(log_dicts),
            records_ok=0,
            records_rej=0,
            error_code=f"INTERNAL_ERROR:{exc_class}",
        )
        return _error_response(
            status=500,
            code="INTERNAL_ERROR",
            message="persistence failure (see server logs)",
            request_id=request_id,
            retryable=True,
        )

    _log_ingest_event(
        level=logging.INFO,
        request_id=request_id,
        key_prefix=key_prefix,
        method=method,
        path=path,
        status=200,
        records_in=len(log_dicts),
        records_ok=inserted,
    )

    return JSONResponse(
        status_code=200,
        content={
            "code": 0,
            "requestId": request_id,
            "data": {"inserted": inserted},
        },
    )


# ═════════════════════════════════════════════════════════════════════
# SPU 实际 ROI 看板 + 钻取面板（D6/D7/D8，2026-09-07）
# ═════════════════════════════════════════════════════════════════════

from tts_erp_v2.analytics.spu_roi import (  # noqa: E402,F401 — re-export
    drilldown_router,
    roi_router,
)
