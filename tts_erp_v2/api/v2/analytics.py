"""/v2/analytics/sync/* — Chrome extension (tk-adv-cost-monitor) analytics ingest。

2026-09-02 v2 化（tech-doc/analytics-v2-migration-plan.md）：
- 旧 ``analytics_sync/app.py``（挂在 /v1/analytics/sync，裸 psycopg 存储）
  迁移为本模块；/v1 路径随发布下线（单挂 /v2，无 alias —— 用户拍板）。
- 存储走 ``tts_erp_v2/analytics/repository.py``（SQLAlchemy，
  schema = analytics.ad_*）。
- Auth/限流/访问日志全部继承 v2 中间件栈；``required_role()`` 把
  ``/v2/analytics/sync`` 前缀分类为 readwrite（middleware/auth.py）。

协议契约（字节级锁定，见 plan D4）：envelope 形状、幂等键推导、
错误码矩阵、cursor items echo sellerId/advertiserId、
``protocolVersion`` 1/2（payload 内版本轴，与路由 /v2 无关）。

Handler 结构说明：
- ``get_cursor`` 是同步 def —— v2 惯例（同 commerce/reporting），
  FastAPI 自动丢线程池。
- ``post_batches`` 需要在 Pydantic 解析**之前**拿原始 body（413 尺寸闸 +
  MALFORMED_JSON 与 SCHEMA_INVALID 的区分），原始 body 只能异步读，
  因此用 async 依赖 ``_raw_body`` 喂给同步 handler —— handler 本体保持
  同步 + ``Depends(get_session)``，不引入 async session。
"""

from __future__ import annotations

import json
import os
import sys
import uuid
from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, ValidationError, field_validator
from sqlalchemy.orm import Session

from tts_erp_v2.analytics.domain import (
    DEFAULT_TIMEZONE,
    Record,
    Scope,
    StorageKey,
    compute_idempotency_key,
)
from tts_erp_v2.analytics.repository import (
    fetch_cursor_page,
    fetch_timezone,
    upsert_records,
    write_audit,
)
from tts_erp_v2.api.deps import get_session

# ─── Config ───────────────────────────────────────────────────────────

PROTOCOL_VERSION = 2
SUPPORTED_PROTOCOL_VERSIONS = {1, 2}
PROTOCOL_VERSION_HEADER = "X-Protocol-Version"
DEFAULT_BOOTSTRAP_LOOKBACK_DAYS = 30
MAX_BATCH_RECORDS = 100
MAX_BODY_BYTES = 2 * 1024 * 1024  # 2 MB per protocol §5
MAX_RESPONSE_DATA_BYTES = 256 * 1024  # cap individual response_data JSON

_PATH_CURSOR = "/v2/analytics/sync/cursor"
_PATH_BATCHES = "/v2/analytics/sync/batches"


# ─── Scope-grant helper (also used by tests) ──────────────────────────


def scope_grants(scopes, *, seller_id, advertiser_id):
    """Return True iff the token's scopes cover the requested scope.

    Empty scopes / wildcard '*' = unrestricted. Within one dimension,
    multiple entries are OR'd (any match grants). Unknown prefixes
    (typos like 'seler:x') fail closed — silently ignoring them would
    make a misspelled scope entry a no-op that looks like it works.

    Mirrors api_keys.py `scopes` semantics (see api_keys CLI `--scopes`).
    """
    if not scopes or "*" in scopes:
        return True
    seller_grants = [s[len("seller:") :] for s in scopes if s.startswith("seller:")]
    advertiser_grants = [
        s[len("advertiser:") :] for s in scopes if s.startswith("advertiser:")
    ]
    known = len(seller_grants) + len(advertiser_grants)
    if known != len(scopes):
        return False  # unknown prefix present → fail closed
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


class RecordIn(BaseModel):
    idempotencyKey: str = Field(min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$")
    sourceRecordId: str | None = None
    storageKey: StorageKey
    campaignId: str = Field(min_length=1, max_length=128)
    day: date
    page: int = Field(ge=1)
    expectedPageCount: int | None = Field(default=None, ge=1)
    endpoint: str = Field(min_length=1, max_length=512)
    method: str = Field(min_length=1, max_length=16)
    requestBody: dict[str, Any] | None = None
    response: dict[str, Any]
    source: str = Field(min_length=1, max_length=64)
    capturedAt: datetime
    schemaVersion: int = Field(default=1, ge=1)

    @field_validator("capturedAt")
    @classmethod
    def _captured_at_must_be_utc(cls, v: datetime) -> datetime:
        if v.tzinfo is None:
            raise ValueError(
                "capturedAt must include a timezone (use ISO-8601 with 'Z' or '+00:00')"
            )
        return v


class BatchRequest(BaseModel):
    protocolVersion: int = Field(default=PROTOCOL_VERSION)
    requestId: str | None = Field(default=None, min_length=1, max_length=128)
    scope: ScopeIn
    records: list[RecordIn] = Field(min_length=1, max_length=MAX_BATCH_RECORDS)


# ─── 依赖：原始 body（post_batches 的 413/JSON 闸需要解析前拿 body）───


async def _raw_body(request: Request) -> bytes:
    """读原始请求体（async 依赖；Starlette 会缓存，handler 可再取）。

    FastAPI 允许同步 endpoint 配 async 依赖：依赖先在 async 上下文解析，
    随后同步 handler 进线程池 —— 借此绕开「同步 handler 无法 await
    request.body()」的限制，同时保持 v2 同步 handler + Depends 惯例。
    """
    return await request.body()


# ─── Cursor endpoint ──────────────────────────────────────────────────


@router.get("/cursor")
def get_cursor(
    request: Request,
    sellerId: str = Query(min_length=1, max_length=128),
    advertiserId: str = Query(min_length=1, max_length=128),
    storageKey: StorageKey | None = Query(default=None),
    campaignId: str | None = Query(default=None, max_length=128),
    cursor: str | None = Query(default=None, max_length=4096),
    pageSize: int = Query(default=50, ge=1, le=100),
    sess: Session = Depends(get_session),
) -> JSONResponse:
    """Return the canonical latest-day state for every (storageKey, campaignId)
    pair known for this scope. Returns timezone + nextRequiredDay per row.

    nextRequiredDay is authoritative: if no record exists, the server
    returns a bootstrap date (today - ANALYTICS_SYNC_BOOTSTRAP_LOOKBACK_DAYS,
    default 30) in the shop's canonical timezone.
    """
    request_id = _request_id_from_headers(request)
    key_prefix = _key_prefix(request)
    audit_path = f"{_PATH_CURSOR}?sellerId={sellerId}&advertiserId={advertiserId}"

    if not scope_grants(
        tuple(_scopes(request)),
        seller_id=sellerId,
        advertiser_id=advertiserId,
    ):
        write_audit(
            request_id=request_id,
            endpoint="cursor",
            method="GET",
            path=audit_path,
            status=403,
            key_prefix=key_prefix,
            error_code="SCOPE_DENIED",
        )
        return _error_response(
            status=403,
            code="SCOPE_DENIED",
            message="api key does not grant access to this scope",
            request_id=request_id,
            retryable=False,
        )

    tz_name = fetch_timezone(sess, sellerId)
    today = _today_in_tz(tz_name)
    bootstrap_days = _safe_int(
        os.environ.get("ANALYTICS_SYNC_BOOTSTRAP_LOOKBACK_DAYS"),
        DEFAULT_BOOTSTRAP_LOOKBACK_DAYS,
    )

    entries = fetch_cursor_page(
        sess,
        seller_id=sellerId,
        advertiser_id=advertiserId,
        storage_key=storageKey,
        campaign_id=campaignId,
        today_in_shop_tz=today,
        bootstrap_lookback_days=bootstrap_days,
    )

    page = entries[:pageSize]
    # nextCursor 未实现（fetch_cursor_page 返回全量行；入参 cursor 被忽略）。
    # 发出服务端无法消费的 cursor 会让客户端死循环重拉第 1 页 —— 在真正的
    # keyset 分页落地前保持 null。当前 unit 数量远低于 pageSize。
    next_cursor = None

    write_audit(
        request_id=request_id,
        endpoint="cursor",
        method="GET",
        path=audit_path,
        status=200,
        key_prefix=key_prefix,
        records_in=len(page),
    )

    return JSONResponse(
        status_code=200,
        content={
            "code": 0,
            "requestId": request_id,
            "data": {
                "timezone": tz_name,
                "items": [
                    {
                        # sellerId/advertiserId 逐行 echo：Chrome extension 的
                        # parseCursor 严格按请求的 scope 匹配 items
                        # （2026-08-30 协议对齐 —— 不要删这两个字段）。
                        "sellerId": sellerId,
                        "advertiserId": advertiserId,
                        "storageKey": e.storage_key.value,
                        "campaignId": e.campaign_id,
                        "latestCompletedDay": e.latest_completed_day.isoformat()
                        if e.latest_completed_day
                        else None,
                        "nextRequiredDay": e.next_required_day.isoformat(),
                    }
                    for e in page
                ],
                "nextCursor": next_cursor,
            },
        },
    )


# ─── Batch endpoint ───────────────────────────────────────────────────


@router.post("/batches")
def post_batches(
    request: Request,
    body_bytes: bytes = Depends(_raw_body),
    sess: Session = Depends(get_session),
) -> JSONResponse:
    """Idempotent batch upload with per-record outcomes.

    Limits: 100 records max, 2 MB body max. Body size is checked BEFORE
    JSON parsing so a too-large request returns 413 cleanly.
    """
    request_id = _request_id_from_headers(request)
    key_prefix = _key_prefix(request)
    method = "POST"
    path = _PATH_BATCHES

    # 垃圾 header（int 解析失败）→ None → 跳过预检，后面的实际 body
    # 尺寸检查照样兜住超大请求。
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
        payload = BatchRequest.model_validate(body)
    except ValidationError as exc:
        # 把失败字段/路径/类型告诉客户端，便于不解自由文本就定位。
        # 消毒：丢 input/ctx（可能带 record body 值），只留安全标识三元组。
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
    except Exception as exc:
        # 意外分支：Pydantic schema 过了但 record 级 handler 内部炸了
        # （比如下游 validator bug）。按 SCHEMA_INVALID 返回同样的
        # envelope 形状但不带字段级细节；ops 从 stderr 拿异常类名。
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

    if payload.protocolVersion not in SUPPORTED_PROTOCOL_VERSIONS:
        return _audit_and_error(
            request_id=request_id,
            status=400,
            code="UNSUPPORTED_PROTOCOL_VERSION",
            message=(
                f"server supports protocolVersion in "
                f"{sorted(SUPPORTED_PROTOCOL_VERSIONS)}, client sent {payload.protocolVersion}"
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
        write_audit(
            request_id=payload.requestId,
            endpoint="batches",
            method=method,
            path=path,
            status=403,
            key_prefix=key_prefix,
            records_in=len(payload.records),
            records_ok=0,
            records_rej=0,
            error_code="SCOPE_DENIED",
        )
        return _error_response(
            status=403,
            code="SCOPE_DENIED",
            message="api key does not grant access to this scope",
            request_id=payload.requestId,
            retryable=False,
        )

    accepted: list[dict[str, str]] = []
    rejected: list[dict[str, Any]] = []
    valid_records: list[Record] = []

    # batch 内同一 daily unit 的 expectedPageCount 必须一致。
    expected_by_unit: dict[tuple[str, str, str, str, date], int] = {}

    for idx, rec_in in enumerate(payload.records):
        unit = (
            payload.scope.sellerId,
            payload.scope.advertiserId,
            rec_in.storageKey.value,
            rec_in.campaignId,
            rec_in.day,
        )

        effective_expected = _effective_expected_page_count(
            payload.protocolVersion, rec_in.expectedPageCount
        )
        if effective_expected is None:
            rejected.append(
                {
                    "idempotencyKey": rec_in.idempotencyKey,
                    "code": "SCHEMA_INVALID",
                    "message": (
                        f"records[{idx}].expectedPageCount is required for protocolVersion 2"
                    ),
                    "retryable": False,
                }
            )
            continue

        if rec_in.page > effective_expected and payload.protocolVersion == 2:
            rejected.append(
                {
                    "idempotencyKey": rec_in.idempotencyKey,
                    "code": "SCHEMA_INVALID",
                    "message": (
                        f"records[{idx}].page ({rec_in.page}) cannot exceed "
                        f"expectedPageCount ({effective_expected})"
                    ),
                    "retryable": False,
                }
            )
            continue

        existing = expected_by_unit.get(unit)
        if existing is None:
            expected_by_unit[unit] = effective_expected
        elif existing != effective_expected:
            rejected.append(
                {
                    "idempotencyKey": rec_in.idempotencyKey,
                    "code": "PAGE_COUNT_CONFLICT",
                    "message": (
                        f"records[{idx}] has expectedPageCount={effective_expected}, "
                        f"but the same daily unit already has expectedPageCount={existing} "
                        f"in this batch"
                    ),
                    "retryable": False,
                }
            )
            continue
        try:
            response_size = len(
                json.dumps(rec_in.response, ensure_ascii=False).encode()
            )
        except (TypeError, ValueError):
            response_size = 0
        if response_size > MAX_RESPONSE_DATA_BYTES:
            rejected.append(
                {
                    "idempotencyKey": rec_in.idempotencyKey,
                    "code": "RESPONSE_TOO_LARGE",
                    "message": (
                        f"records[{idx}].response is {response_size} bytes; "
                        f"max {MAX_RESPONSE_DATA_BYTES}"
                    ),
                    "retryable": False,
                }
            )
            continue

        canonical_key = compute_idempotency_key(
            seller_id=payload.scope.sellerId,
            advertiser_id=payload.scope.advertiserId,
            storage_key=rec_in.storageKey,
            campaign_id=rec_in.campaignId,
            day=rec_in.day,
            page=rec_in.page,
        )
        if canonical_key != rec_in.idempotencyKey:
            rejected.append(
                {
                    "idempotencyKey": rec_in.idempotencyKey,
                    "code": "SCHEMA_INVALID",
                    "message": (
                        f"idempotencyKey mismatch at records[{idx}]: "
                        f"client={rec_in.idempotencyKey[:16]}… "
                        f"server={canonical_key[:16]}…"
                    ),
                    "retryable": False,
                }
            )
            continue
        valid_records.append(
            Record(
                idempotency_key=rec_in.idempotencyKey,
                source_record_id=rec_in.sourceRecordId,
                storage_key=rec_in.storageKey,
                campaign_id=rec_in.campaignId,
                day=rec_in.day,
                page=rec_in.page,
                expected_page_count=expected_by_unit[unit],
                protocol_version=payload.protocolVersion,
                endpoint=rec_in.endpoint,
                method=rec_in.method,
                request_body=rec_in.requestBody,
                response=rec_in.response,
                source=rec_in.source,
                captured_at=rec_in.capturedAt,
                schema_version=rec_in.schemaVersion,
            )
        )

    scope = Scope(
        seller_id=payload.scope.sellerId,
        advertiser_id=payload.scope.advertiserId,
        shop_name=payload.scope.shopName,
    )

    if valid_records:
        try:
            tz_name = fetch_timezone(sess, payload.scope.sellerId)
            today = _today_in_tz(tz_name)
            bootstrap_days = _safe_int(
                os.environ.get(
                    "ANALYTICS_SYNC_BOOTSTRAP_LOOKBACK_DAYS",
                    str(DEFAULT_BOOTSTRAP_LOOKBACK_DAYS),
                ),
                DEFAULT_BOOTSTRAP_LOOKBACK_DAYS,
            )
            bootstrap_day = _subtract_days(today, bootstrap_days)

            result = upsert_records(
                sess,
                scope,
                valid_records,
                request_id=payload.requestId,
                today_in_shop_tz=today,
                bootstrap_day=bootstrap_day,
            )
        except Exception as exc:
            exc_class = type(exc).__name__
            sys.stderr.write(
                f"[analytics-sync] persistence failure: {exc_class}: {exc}\n"
            )
            write_audit(
                request_id=payload.requestId,
                endpoint="batches",
                method=method,
                path=path,
                status=500,
                key_prefix=key_prefix,
                records_in=len(payload.records),
                records_ok=0,
                records_rej=len(rejected),
                error_code=f"INTERNAL_ERROR:{exc_class}",
            )
            return _error_response(
                status=500,
                code="INTERNAL_ERROR",
                message="persistence failure (see server logs)",
                request_id=payload.requestId,
                retryable=True,
            )

        for ar in result.accepted:
            accepted.append({"idempotencyKey": ar.idempotency_key, "status": ar.status})
        for rr in result.rejected:
            rejected.append(
                {
                    "idempotencyKey": rr.idempotency_key,
                    "code": rr.code,
                    "message": rr.message,
                    "retryable": rr.retryable,
                }
            )

    write_audit(
        request_id=payload.requestId,
        endpoint="batches",
        method=method,
        path=path,
        status=200,
        key_prefix=key_prefix,
        records_in=len(payload.records),
        records_ok=len(accepted),
        records_rej=len(rejected),
    )

    return JSONResponse(
        status_code=200,
        content={
            "code": 0,
            "requestId": payload.requestId,
            "data": {"accepted": accepted, "rejected": rejected},
        },
    )


# ─── Helpers ──────────────────────────────────────────────────────────


def _today_in_tz(tz_name: str) -> date:
    try:
        tz = ZoneInfo(tz_name)
    except Exception:
        tz = ZoneInfo(DEFAULT_TIMEZONE)
    return datetime.now(tz).date()


def _effective_expected_page_count(
    protocol_version: int, raw: int | None
) -> int | None:
    """v1 records are implicitly single-page days. v2 records must declare
    expectedPageCount explicitly."""
    if protocol_version == 1:
        return 1
    if raw is None or raw < 1:
        return None
    return raw


def _subtract_days(d: date, n: int) -> date:
    """Subtract n days from d."""
    return d - timedelta(days=n)


def _parse_content_length(value: str | None) -> int | None:
    """Content-Length header → int；垃圾值返回 None（调用方跳过预检，
    由实际 body 尺寸检查兜底）。int() 对 isdigit 为真的部分 Unicode
    数字（如上标 ²）也会抛 ValueError，必须真 try/except。"""
    if value is None:
        return None
    try:
        return int(value)
    except ValueError:
        return None


def _safe_int(value: str | None, default: int) -> int:
    """Parse an int from an environment string, returning default on failure."""
    if value is None:
        return default
    try:
        return max(1, int(value))
    except ValueError:
        return default


def _request_id_from_headers(request: Request) -> str:
    rid = request.headers.get("x-request-id")
    if rid:
        return rid[:128]
    return f"req-{uuid.uuid4()}"


def _key_prefix(request: Request) -> str | None:
    """Read the api key's 16-char prefix from ASGI scope (set by
    AuthMiddleware). None if request is unauthenticated."""
    key_hash = request.scope.get("api_key_hash")
    return key_hash[:16] if isinstance(key_hash, str) else None


def _scopes(request: Request) -> tuple[str, ...]:
    """Read the api key's scopes tuple from ASGI scope (set by
    AuthMiddleware). Empty tuple means unrestricted."""
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
    """Build a sanitized error envelope. Never echoes tokens/headers/body.

    ``structured_errors`` is the safe identifier triple (loc/msg/type) from
    Pydantic — clients use it to programmatically identify which record and
    field failed without regex-parsing the free-form ``message``. When None
    (default) the field is omitted; we never emit an empty list because that
    would change the JSON shape for the v1 contract.
    """
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
    """Reduce Pydantic's errors() to the safe identifier triple.

    - ``type``  → 保留（安全标识，如 ``string_too_short``）
    - ``loc``   → 保留原始 Python 类型的路径段（int = list 下标，
                  str = 字段名）。client 据此定位出错记录
                  （``loc == ['records', 0, 'capturedAt']`` ⇒ 第 0 条
                  记录的 capturedAt 字段）。int→str 强转会丢数组下标
                  形状；用 ``.`` 拼接在字段名本身含点时会有歧义。
    - ``msg``   → 保留（Pydantic 自由文本，已消毒：无 body、无 token）
    - ``input`` → 丢弃 —— 可能逐字带出错的 record 字段值
    - ``ctx``   → 丢弃 —— 同理（如 ``actual_length`` 可能泄漏）
    - ``url``   → 丢弃 —— 内部文档链接，无 client 价值

    输出可 JSON 序列化、顺序稳定、以 Pydantic 错误数为界。
    """
    sanitized: list[dict[str, object]] = []
    for err in exc.errors():
        loc_segments: list[object] = []
        for segment in err.get("loc", ()):
            # Pydantic 用 int 表示 list 下标、str 表示字段名。两者都保留。
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
    """一行 stderr 诊断，让 ops 不问客户端就知道是哪个字段/规则挂了
    （2026-08-30 事故：真实流量 SCHEMA_INVALID 数小时，服务端无任何
    字段级细节）。``message`` 是 Pydantic/JSON 解析描述（字段名 +
    截断输入值），绝不含 headers/token/请求体。换行压平，方便 grep。

    ``structured_errors`` 随响应体 ``errors[]`` 下发，并写入
    ``analytics.ad_audit_log.error_message``（与 stderr 同一份 ≤500 字符
    消毒载荷），ops 可在日志轮转后按字段名查历史 400。
    """
    safe_message = " ".join(str(message).split())[:500]
    sys.stderr.write(
        f"[analytics-sync] reject status={status} code={code} "
        f"request_id={request_id} key_prefix={key_prefix or '-'} "
        f"method={method} path={path} message={safe_message}\n"
    )
    write_audit(
        request_id=request_id,
        endpoint="batches",
        method=method,
        path=path,
        status=status,
        key_prefix=key_prefix,
        error_code=error_code,
        error_message=safe_message,
    )
    return _error_response(
        status=status,
        code=code,
        message=message,
        request_id=request_id,
        retryable=retryable,
        structured_errors=structured_errors,
    )
