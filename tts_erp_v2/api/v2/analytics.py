"""/v2/analytics/sync/* — Chrome extension (tk-adv-cost-monitor) analytics ingest。

2026-09-02 v2 dump architecture（tech-doc/analytics/dump-architecture.md）：
- cursor 协议只剩 has-data 模式（防风控预检闸）:GET /cursor?endpoint&day[&campaignId]
- dumps 协议替换 batches:POST /dumps,body 是单 dump object（严禁批量）
- ad_raw 是 source-of-truth,server 端从 dump.request/dump.response 派生
  ad_records + ad_daily_completeness。3 张表同事务原子写。
- 协议契约:dumps 字段单 object、page 隐式 = 1、storageKey 由 server 端
  STORAGE_KEY_BY_PATH 从 endpoint 推导（消除 client 端 enum 知识）、
  ad_raw 5 元组 unique (seller_id, advertiser_id, endpoint, day, campaign_id)。

2026-09-05 reorg（tech-doc/analytics/reorg-plan.md 决策 #1-#4）：
- ad_records / ad_daily_completeness / ad_shop_timezones / ad_audit_log 删
  除;upsert_dump 缩为单表写（只 INSERT ad_raw）。
- **审计改文件日志**:`analytics.ad_audit_log` 删,改为 logger
  ``tts_erp_v2.analytics.ingest`` 单行 key=value 结构化日志。失败路径
  ``_audit_and_error`` 仍打 stderr（沿用 2026-08-30 事故回归守护点）,
  并把"再写一条 DB"的 write_audit 换成同一份结构化 log。成功路径也补
  一行（这是 audit → 日志后唯一会"丢"的信息：成功请求的 records
  计数,现在落在日志而非 DB 行）。

Handler 结构说明：
- ``get_cursor`` / ``post_dumps`` 都是同步 def —— v2 惯例（同 commerce/
  reporting）,FastAPI 自动丢线程池。
- ``post_dumps`` 需要在 Pydantic 解析**之前**拿原始 body（413 尺寸闸 +
  MALFORMED_JSON 与 SCHEMA_INVALID 的区分），原始 body 只能异步读,
  因此用 async 依赖 ``_raw_body`` 喂给同步 handler —— handler 本体保持
  同步 + ``Depends(get_session)``,不引入 async session。
"""

from __future__ import annotations

import json
import logging
import sys
import uuid
from datetime import date, datetime
from typing import Any

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, ValidationError, field_validator
from sqlalchemy.orm import Session

from tts_erp_v2.analytics.domain import (
    DumpPayload,
    HasDataResult,
)
from tts_erp_v2.analytics.repository import (
    STORAGE_KEY_BY_PATH,
    has_data,
    upsert_dump,
)
from tts_erp_v2.api.deps import get_session

# ─── Config ───────────────────────────────────────────────────────────

PROTOCOL_VERSION = 2
SUPPORTED_PROTOCOL_VERSIONS = {1, 2}
PROTOCOL_VERSION_HEADER = "X-Protocol-Version"
MAX_BODY_BYTES = 2 * 1024 * 1024  # 2 MB per protocol §5
MAX_RESPONSE_DATA_BYTES = 256 * 1024  # cap individual response_data JSON

_PATH_CURSOR = "/v2/analytics/sync/cursor"
_PATH_DUMPS = "/v2/analytics/sync/dumps"


# ─── Logger（审计迁移自 DB → 文件日志）──────────────────────────────
# 替代原 analytics.ad_audit_log。每条 ingest 请求（含成功与失败路径）一行
# key=value,字段见 ``_log_ingest_event``。
#
# handler 接法与 ``tts_erp_v2.auth.login_logger`` / ``access_log.access``
# 一致：uvicorn dictConfig 只给 uvicorn.* logger 挂 handler，root 无
# handler（2026-08-30 教训：不显式 setLevel + 接 handler 的 logger 是静默
# no-op），因此这里 setLevel(INFO) + 自接 stdout StreamHandler。
# ⚠️ 差异点：**propagate 保持 True**（不学 login_logger 的 False）——
# tests/api/test_analytics_v2_*.py 用 caplog（root handler）断言 ingest
# 行，propagate=False 会断掉捕获。自身 handler 已把记录置 handled=True，
# 生产下不会触发 lastResort 重复。
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
    """Emit a single key=value ingest log line.

    Field contract（与原 ad_audit_log 列 1:1 对齐,确保历史 SQL 查询可
    用同一组 key grep 重写）:

    - request_id, key_prefix, method, path, status, records_in,
      records_ok, records_rej, error_code, message.
    - message 沿用 ``_sanitize_message``（≤500 字符、空白压平,无换行），
      与 stderr 同一份消毒载荷（2026-08-30 事故回归守护）。

    LogRecord args intentionally omitted — the line is fully formatted in
    ``msg=`` so it grep-stably includes all fields.
    """
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


class DumpBodyIn(BaseModel):
    """dump 协议 body 里的 dump object 字段。

    协议契约（tech-doc/analytics/dump-architecture.md D2）：
    - endpoint 必带;server 端用 STORAGE_KEY_BY_PATH 推导 storageKey
    - request / response 是 plugin 抓的完整 HTTP 交换（url+headers+body /
      status+headers+body）
    - 不带 page（隐式 = 1）/ 不带 expectedPageCount（删除）/ 不带 storageKey
      （server 推导）/ 不带 sourceRecordId（dump 协议无 client-id 概念）
    """

    endpoint: str = Field(min_length=1, max_length=512)
    method: str = Field(min_length=1, max_length=16)
    day: date
    campaignId: str = Field(min_length=1, max_length=128)
    request: dict[str, Any]
    response: dict[str, Any]
    capturedAt: datetime
    source: str = Field(default="tiktok-shop-data-sync", min_length=1, max_length=64)
    schemaVersion: int = Field(default=1, ge=1)

    @field_validator("capturedAt")
    @classmethod
    def _captured_at_must_be_utc(cls, v: datetime) -> datetime:
        if v.tzinfo is None:
            raise ValueError(
                "capturedAt must include a timezone (use ISO-8601 with 'Z' or '+00:00')"
            )
        return v


class DumpRequest(BaseModel):
    """dump 协议顶层 envelope。

    顶层 wrapper 而不是 list —— dumps 字段是单 dump object,
    plugin 严禁批量同步（per tech-doc/analytics/dump-architecture.md D2）。
    """

    protocolVersion: int = Field(default=PROTOCOL_VERSION)
    requestId: str | None = Field(default=None, min_length=1, max_length=128)
    scope: ScopeIn
    dump: DumpBodyIn


# ─── 依赖：原始 body（post_dumps 的 413/JSON 闸需要解析前拿 body）────────


async def _raw_body(request: Request) -> bytes:
    """读原始请求体（async 依赖；Starlette 会缓存,handler 可再取）。

    FastAPI 允许同步 endpoint 配 async 依赖：依赖先在 async 上下文解析,
    随后同步 handler 进线程池 —— 借此绕开「同步 handler 无法 await
    request.body()」的限制,同时保持 v2 同步 handler + Depends 惯例。
    """
    return await request.body()


# ─── Cursor endpoint (has-data 模式) ──────────────────────────────


@router.get("/cursor")
def get_cursor(
    request: Request,
    sellerId: str = Query(min_length=1, max_length=128),
    advertiserId: str = Query(min_length=1, max_length=128),
    endpoint: str = Query(min_length=1, max_length=512),
    day: date = Query(...),
    campaignId: str | None = Query(default=None, max_length=128),
    sess: Session = Depends(get_session),
) -> JSONResponse:
    """has-data 检查:这个 (scope, endpoint, day[, campaignId]) 有没有数据。

    Plugin 端用此做防 TikTok 风控的预检闸,hasData=true → 跳过该天抓取。
    cursor 协议 work-list 模式 (items / nextRequiredDay / pageSize / cursor
    / timezone) 全部删除 —— tech-doc/analytics/dump-architecture.md D3。
    """
    request_id = _request_id_from_headers(request)
    key_prefix = _key_prefix(request)
    audit_path = (
        f"{_PATH_CURSOR}?sellerId={sellerId}&advertiserId={advertiserId}"
        f"&endpoint={endpoint}&day={day.isoformat()}"
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

    try:
        result: HasDataResult = has_data(
            sess,
            seller_id=sellerId,
            advertiser_id=advertiserId,
            endpoint=endpoint,
            day=day,
            campaign_id=campaignId,
        )
    except ValueError as exc:
        # endpoint 不在 4 路径白名单（STORAGE_KEY_BY_PATH）
        return _audit_and_error(
            request_id=request_id,
            status=400,
            code="SCHEMA_INVALID",
            message=str(exc),
            retryable=False,
            key_prefix=key_prefix,
            error_code="SCHEMA_INVALID",
            method="GET",
            path=audit_path,
        )

    _log_ingest_event(
        level=logging.INFO,
        request_id=request_id,
        key_prefix=key_prefix,
        method="GET",
        path=audit_path,
        status=200,
        records_in=1,
        records_ok=1 if result.has_data else 0,
    )

    response_data: dict[str, object] = {
        "day": day.isoformat(),
        "endpoint": endpoint,
        "storageKey": result.storage_key.value,
        "hasData": result.has_data,
    }
    if campaignId is not None:
        response_data["campaignId"] = campaignId

    return JSONResponse(
        status_code=200,
        content={
            "code": 0,
            "requestId": request_id,
            "data": response_data,
        },
    )


# ─── Dumps endpoint (单 dump object,严禁批量) ────────────────────────


@router.post("/dumps")
def post_dumps(
    request: Request,
    body_bytes: bytes = Depends(_raw_body),
    sess: Session = Depends(get_session),
) -> JSONResponse:
    """单 dump 写入协议。

    协议契约（tech-doc/analytics/dump-architecture.md D2）：
    - dumps 字段是单 object（plugin 严禁批量同步）
    - page 隐式 = 1
    - endpoint 必带;server 端 STORAGE_KEY_BY_PATH 推导 storage_key
    - 单事务写 1 张表（ad_raw）—— 2026-09-05 reorg 后由 3 张缩为 1 张
      （ad_records / ad_daily_completeness 已删,见 reorg-plan 决策 #1-#2）。
    - 2 MB body 上限（与旧 /batches 保持一致）
    """
    request_id = _request_id_from_headers(request)
    key_prefix = _key_prefix(request)
    method = "POST"
    path = _PATH_DUMPS

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
        payload = DumpRequest.model_validate(body)
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
    except Exception as exc:  # noqa: no-boolean-in-except
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

    # 推导 storage_key（endpoint → 1:1 映射）
    try:
        storage_key = STORAGE_KEY_BY_PATH[payload.dump.endpoint]
    except KeyError:
        return _audit_and_error(
            request_id=request_id,
            status=400,
            code="SCHEMA_INVALID",
            message=f"unknown endpoint: {payload.dump.endpoint}",
            retryable=False,
            key_prefix=key_prefix,
            error_code="SCHEMA_INVALID",
            method=method,
            path=path,
        )

    # 校验 response 体积（与旧 /batches 行为一致:response_data 不超 256 KiB）
    try:
        response_size = len(
            json.dumps(payload.dump.response, ensure_ascii=False).encode()
        )
    except (TypeError, ValueError):
        response_size = 0
    if response_size > MAX_RESPONSE_DATA_BYTES:
        return _audit_and_error(
            request_id=request_id,
            status=400,
            code="RESPONSE_TOO_LARGE",
            message=(
                f"dump.response is {response_size} bytes; max {MAX_RESPONSE_DATA_BYTES}"
            ),
            retryable=False,
            key_prefix=key_prefix,
            error_code="RESPONSE_TOO_LARGE",
            method=method,
            path=path,
        )

    # 构造 DumpPayload（包含 server-推的 storage_key）
    dump = DumpPayload(
        seller_id=payload.scope.sellerId,
        advertiser_id=payload.scope.advertiserId,
        endpoint=payload.dump.endpoint,
        method=payload.dump.method,
        day=payload.dump.day,
        campaign_id=payload.dump.campaignId,
        storage_key=storage_key,
        request=payload.dump.request,
        response=payload.dump.response,
        captured_at=payload.dump.capturedAt,
        request_id=payload.requestId or request_id,
        source=payload.dump.source,
        protocol_version=payload.protocolVersion,
        schema_version=payload.dump.schemaVersion,
    )

    try:
        result = upsert_dump(sess, dump, request_id=payload.requestId or request_id)
    except Exception as exc:  # noqa: no-boolean-in-except
        exc_class = type(exc).__name__
        sys.stderr.write(f"[analytics-sync] persistence failure: {exc_class}: {exc}\n")
        _log_ingest_event(
            level=logging.ERROR,
            request_id=payload.requestId or request_id,
            key_prefix=key_prefix,
            method=method,
            path=path,
            status=500,
            records_in=1,
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
        path=path,
        status=200,
        records_in=1,
        records_ok=1 if result.status == "inserted" else 0,
        records_rej=0,
    )

    return JSONResponse(
        status_code=200,
        content={
            "code": 0,
            "requestId": request_id,
            "data": {
                "idempotencyKey": result.idempotency_key,
                "status": result.status,
            },
        },
    )


# ─── Helpers ──────────────────────────────────────────────────────────


def _parse_content_length(value: str | None) -> int | None:
    """Content-Length header → int；垃圾值返回 None（调用方跳过预检,
    由实际 body 尺寸检查兜底）。int() 对 isdigit 为真的部分 Unicode
    数字（如上标 ²）也会抛 ValueError,必须真 try/except。"""
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
    - ``loc``   → 保留原始 Python 类型的路径段（int = list 下标,
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
    （2026-08-30 事故：真实流量 SCHEMA_INVALID 数小时,服务端无任何
    字段级细节）。``message`` 是 Pydantic/JSON 解析描述（字段名 +
    截断输入值），绝不含 headers/token/请求体。换行压平，方便 grep。

    2026-09-05 reorg 后：原本的 "再写一条 DB audit_log" 改为结构化
    文件日志（``_log_ingest_event``），统一走 stderr（与 access_log
    同源）。同时仍打 stderr 这行（与历史回归守护点 1:1：每次拒绝都
    有一行 stderr 可 grep）。
    """
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
