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
from datetime import UTC, date, datetime, time, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, ValidationError, field_validator
from sqlalchemy import text
from sqlalchemy.orm import Session

from tts_erp_v2.analytics import has_data_cache
from tts_erp_v2.analytics.domain import (
    DumpPayload,
    HasDataResult,
)
from tts_erp_v2.analytics.repository import (
    STORAGE_KEY_BY_PATH,
    has_data,
    load_campaign_pairs,
    upsert_dump,
)
from tts_erp_v2.api.deps import get_session
from tts_erp_v2.db.base import get_session_factory
from tts_erp_v2.db.constants import PAID_SALES_ORDER_STATUSES
from tts_erp_v2.fx.rates import load_rate_map

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


def _cursor_ok(
    *,
    request_id: str,
    day_iso: str,
    endpoint: str,
    storage_key_value: str,
    has_data: bool,
    campaign_id: str | None,
) -> JSONResponse:
    """200 envelope 唯一构造点(缓存命中/DB 回源两条路径共用,body 恒同形)。"""
    response_data: dict[str, object] = {
        "day": day_iso,
        "endpoint": endpoint,
        "storageKey": storage_key_value,
        "hasData": has_data,
    }
    if campaign_id is not None:
        response_data["campaignId"] = campaign_id
    return JSONResponse(
        status_code=200,
        content={
            "code": 0,
            "requestId": request_id,
            "data": response_data,
        },
    )


@router.get("/cursor")
def get_cursor(
    request: Request,
    sellerId: str = Query(min_length=1, max_length=128),
    advertiserId: str = Query(min_length=1, max_length=128),
    endpoint: str = Query(min_length=1, max_length=512),
    day: date = Query(...),  # noqa: B008 — FastAPI Query default 惯例
    campaignId: str | None = Query(default=None, max_length=128),
) -> JSONResponse:
    """has-data 检查:这个 (scope, endpoint, day[, campaignId]) 有没有数据。

    Plugin 端用此做防 TikTok 风控的预检闸,hasData=true → 跳过该天抓取。
    cursor 协议 work-list 模式 (items / nextRequiredDay / pageSize / cursor
    / timezone) 全部删除 —— tech-doc/analytics/dump-architecture.md D3。

    2026-09-06 缓存(has_data_cache.py,设计见 dump-architecture.md「cursor
    has-data 缓存」):campaign-scoped 请求先查内存 (endpoint, day) 集合 ——
    命中不碰 DB/session;miss 才按需开 session 回源灌桶。无 campaignId 请求
    (scope 级任意行存在性,~60/天)不缓存,走 has_data 原 EXISTS 路径。
    """
    request_id = _request_id_from_headers(request)
    key_prefix = _key_prefix(request)
    day_iso = day.isoformat()
    audit_path = (
        f"{_PATH_CURSOR}?sellerId={sellerId}&advertiserId={advertiserId}"
        f"&endpoint={endpoint}&day={day_iso}"
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

    # endpoint 白名单提前到缓存判定之前:命中路径不经过 has_data 的
    # ValueError,这里统一兜(400 SCHEMA_INVALID,行为与回源路径一致)。
    storage_key = STORAGE_KEY_BY_PATH.get(endpoint)
    if storage_key is None:
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

    # 1) 缓存命中(仅 campaign-scoped;空集合 = 已加载确无数据,也命中)
    if campaignId is not None:
        pairs = has_data_cache.get(
            seller_id=sellerId,
            advertiser_id=advertiserId,
            campaign_id=campaignId,
        )
        if pairs is not None:
            has = (endpoint, day_iso) in pairs
            _log_ingest_event(
                level=logging.INFO,
                request_id=request_id,
                key_prefix=key_prefix,
                method="GET",
                path=audit_path,
                status=200,
                records_in=1,
                records_ok=1 if has else 0,
            )
            return _cursor_ok(
                request_id=request_id,
                day_iso=day_iso,
                endpoint=endpoint,
                storage_key_value=storage_key.value,
                has_data=has,
                campaign_id=campaignId,
            )

    # 2) 缓存 miss / 无 campaignId → DB 回源。命中路径不开 session(避免
    #    checkout 往返),所以这里按 deps.get_session 同款语义按需开/收。
    SessionLocal = get_session_factory()
    sess = SessionLocal()
    try:
        if campaignId is not None:
            # 灌桶:一次性拉该 campaign 的 (endpoint, day) 全集,后续命中免 DB
            pairs = load_campaign_pairs(
                sess,
                seller_id=sellerId,
                advertiser_id=advertiserId,
                campaign_id=campaignId,
            )
            has_data_cache.put(
                seller_id=sellerId,
                advertiser_id=advertiserId,
                campaign_id=campaignId,
                pairs=pairs,
            )
            has = (endpoint, day_iso) in pairs
        else:
            result: HasDataResult = has_data(
                sess,
                seller_id=sellerId,
                advertiser_id=advertiserId,
                endpoint=endpoint,
                day=day,
                campaign_id=None,
            )
            has = result.has_data
    finally:
        try:
            sess.rollback()
        finally:
            sess.close()

    _log_ingest_event(
        level=logging.INFO,
        request_id=request_id,
        key_prefix=key_prefix,
        method="GET",
        path=audit_path,
        status=200,
        records_in=1,
        records_ok=1 if has else 0,
    )

    return _cursor_ok(
        request_id=request_id,
        day_iso=day_iso,
        endpoint=endpoint,
        storage_key_value=storage_key.value,
        has_data=has,
        campaign_id=campaignId,
    )


# ─── Dumps endpoint (单 dump object,严禁批量) ────────────────────────


@router.post("/dumps")
def post_dumps(
    request: Request,
    body_bytes: bytes = Depends(_raw_body),
    sess: Session = Depends(get_session),  # noqa: B008 — FastAPI DI 惯例
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
    except Exception as exc:  # noqa: BLE001 — 意外分支兜底(见下注释)
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
    except Exception as exc:  # noqa: BLE001 — 持久化意外分支兜底
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

    # Write-through：cursor has-data 缓存立刻看到刚落库的 (endpoint, day)。
    # 桶未加载时 mark_present no-op —— 下次 GET 回源重载（新行已落库），
    # 结果必对（见 has_data_cache.mark_present docstring）。
    has_data_cache.mark_present(
        seller_id=dump.seller_id,
        advertiser_id=dump.advertiser_id,
        campaign_id=dump.campaign_id,
        endpoint=dump.endpoint,
        day=dump.day.isoformat(),
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


# ═════════════════════════════════════════════════════════════════════
# SPU 实际 ROI 看板 — GET /v2/analytics/spu-roi（只读，role=readonly）
#
# 口径唯一真相 = tech-doc/analytics/spu-real-roi-dashboard.md §4/§5
# （2026-09-05 拍板：D4/D6/D9/D10 —— 全表统一 USD、固定汇率、单位成本
# 按 SPU 解析 人工优先/缺省 K1=30 CNY、平台佣金按 sales×费率基线估算）。
# 端点只读 view/表并在服务层一次换算后序列化（money=4 位小数、比率=2 位）；
# 页面与 totals 全部消费本端点，不另行计算（§5.1）。
# ═════════════════════════════════════════════════════════════════════

roi_router = APIRouter(prefix="/v2/analytics", tags=["analytics"])

# 固定常量（决策 D9：USD→VND=26,330 / CNY→USD=0.14774，展示 0.1477；
# D4：K1=30 CNY/件 仅作未命中人工成本的默认；D10：平台佣金基线 r̂）
FX_USD_VND = Decimal(26330)
FX_CNY_USD = Decimal("0.14774")
K1_DEFAULT_CNY = Decimal(30)
FEE_RATE_BASELINE = Decimal("0.1156")
FX_AS_OF = "2026-09-05"

_MONEY_Q = Decimal("0.0001")
_RATIO_Q = Decimal("0.01")

#: 在线汇率派生中间精度(与 fx 存储 Numeric(20,8) 一致)
_RATE_Q8 = Decimal("0.00000001")


def _resolve_fx_rates(
    sess: Session,
) -> tuple[Decimal, Decimal, str, str]:
    """ROI 账页换算汇率：在线 fx 缓存优先(D1 落地，见 fx-agent-handbook)。

    读 fx.* 最新 USD 快照 → (CNY→USD, USD→VND, as_of, source)：

    - 快照存在且含 VND/CNY → ``source="fx-cache"``：USD→VND 直取快照
      ``rates["VND"]``；CNY→USD = 1/rates["CNY"]（桥式倒数）量化到 8 dp。
      as_of = 上游 last_update 的日期(UTC)。
    - 快照缺失 / 币种不全 → 回退 D9 固定常量 ``source="fixed-const"``，
      账页金额换算永不因缓存缺位而空白（宁可承认口径旧）。

    每次请求只查一次；API 进程零上游调用（fx.* 是同步 job 的缓存）。
    """
    rm = load_rate_map(sess, base_code="USD")
    if rm is not None and {"VND", "CNY"} <= set(rm.rates):
        cny_usd = (Decimal(1) / rm.rates["CNY"]).quantize(
            _RATE_Q8, rounding=ROUND_HALF_UP
        )
        return (
            cny_usd,
            rm.rates["VND"],
            rm.upstream_last_update.date().isoformat(),
            "fx-cache",
        )
    return (FX_CNY_USD, FX_USD_VND, FX_AS_OF, "fixed-const")

_PAID_STATUSES = sorted(PAID_SALES_ORDER_STATUSES)

_CASE_COMPLETED_STATUSES = (
    "CANCELLATION_REQUEST_COMPLETE",
    "RETURN_OR_REFUND_REQUEST_COMPLETE",
)

# 汇总键统一 spu_pk(§2/§5.2);spu_pk IS NULL 的未关联行不进主表,
# 未归属退款行数单独在 meta 上报(§5.1-5/§6.5)。
_SQL_ROI_AD = text(
    """
    SELECT spu_pk,
           count(DISTINCT campaign_id)::int          AS ad_count,
           coalesce(sum(real_cost_total), 0)         AS spend,
           coalesce(sum(order_value_total), 0)       AS gmv_ad,
           coalesce(sum(order_sku_total), 0)::bigint AS ad_orders,
           min(first_day)                            AS ad_first_day,
           max(last_day)                             AS ad_last_day
    FROM analytics.ad_product_links
    WHERE spu_pk IS NOT NULL
    GROUP BY spu_pk
    """
)

_SQL_ROI_SALES = text(
    """
    SELECT sl.spu_pk,
           count(DISTINCT so.id)::int                    AS order_count,
           coalesce(sum(sl.quantity), 0)                 AS units_sold,
           coalesce(sum(sl.quantity * sl.unit_price), 0) AS sales
    FROM commerce.sales_order_lines sl
    JOIN commerce.sales_orders so ON so.id = sl.order_pk
    WHERE sl.spu_pk IS NOT NULL
      AND so.status = ANY(CAST(:paid_statuses AS text[]))
      AND so.paid_at IS NOT NULL
      AND (CAST(:ws AS timestamptz) IS NULL
           OR so.paid_at >= CAST(:ws AS timestamptz))
      AND (CAST(:we AS timestamptz) IS NULL
           OR so.paid_at < CAST(:we AS timestamptz))
    GROUP BY sl.spu_pk
    """
)

_SQL_ROI_REFUNDS = text(
    """
    SELECT sl.spu_pk,
           coalesce(sum(cl.quantity)       FILTER (
               WHERE so.status = ANY(CAST(:paid_statuses AS text[]))
                 AND c.case_type = 'REFUND_ONLY'), 0)      AS refund_only_qty,
           coalesce(sum(cl.refund_amount)  FILTER (
               WHERE so.status = ANY(CAST(:paid_statuses AS text[]))
                 AND c.case_type = 'REFUND_ONLY'), 0)      AS refund_only_amount,
           coalesce(sum(cl.quantity)       FILTER (
               WHERE so.status = ANY(CAST(:paid_statuses AS text[]))
                 AND c.case_type = 'RETURN_AND_REFUND'), 0) AS refund_return_qty,
           coalesce(sum(cl.refund_amount)  FILTER (
               WHERE so.status = ANY(CAST(:paid_statuses AS text[]))
                 AND c.case_type = 'RETURN_AND_REFUND'), 0) AS refund_return_amount,
           coalesce(sum(cl.quantity)       FILTER (
               WHERE so.status = 'CANCELLED'
                 AND c.case_type IN ('CANCELLATION', 'CANCEL')), 0) AS refund_cancelled_qty,
           coalesce(sum(cl.refund_amount)  FILTER (
               WHERE so.status = 'CANCELLED'
                 AND c.case_type IN ('CANCELLATION', 'CANCEL')
                 AND cl.refund_amount IS NOT NULL), 0)      AS refund_cancelled_amount,
           count(*)                         FILTER (
               WHERE so.status = 'CANCELLED'
                 AND c.case_type IN ('CANCELLATION', 'CANCEL')
                 AND cl.refund_amount IS NULL)::int         AS refund_cancelled_missing_lines
    FROM after_sales.cases c
    JOIN after_sales.case_lines cl ON cl.case_id = c.id
    JOIN commerce.sales_order_lines sl ON sl.id = cl.sales_order_line_id
    JOIN commerce.sales_orders so ON so.id = c.order_pk
    -- §4.2 rule 0：白名单/CANCELLED 之外的异常订单状态(UNPAID/ON_HOLD 等)
    -- 的已完结退款行,所有 FILTER 都不命中 → 不进任何 refund_* 金额桶、
    -- 不进行内金额;这类行在 meta.unattributed_refund_lines 显式计数上报
    WHERE c.status IN (:st0, :st1)
      AND sl.spu_pk IS NOT NULL
      AND (CAST(:ws AS timestamptz) IS NULL
           OR c.updated_at_source >= CAST(:ws AS timestamptz))
      AND (CAST(:we AS timestamptz) IS NULL
           OR c.updated_at_source < CAST(:we AS timestamptz))
    GROUP BY sl.spu_pk
    """
)

_SQL_ROI_COSTS = text(
    """
    SELECT spu_pk, unit_cost
    FROM procurement.manual_product_costs
    WHERE valid_to IS NULL
    """
)

_SQL_ROI_CATALOG = text(
    """
    SELECT cp.id AS spu_pk, cp.shop_pk, cp.spu_id, cp.title, cp.status,
           cp.main_image_url, s.shop_id, s.account_name AS shop_name
    FROM commerce.products_spu cp
    LEFT JOIN commerce.shops s ON s.id = cp.shop_pk
    WHERE (CAST(:shop_pk AS bigint) IS NULL
           OR cp.shop_pk = CAST(:shop_pk AS bigint))
      AND (CAST(:q AS text) IS NULL OR cp.spu_id ILIKE '%' || :q || '%')
      -- §5.1-7: include_all = 全部 ACTIVE SPU(参照 reporting.py 的
      -- status ILIKE 'activate' 用法,兼容 TikTok 存的 ACTIVATE 大写)
      AND (CAST(:active_only AS boolean) IS NOT TRUE
           OR cp.status ILIKE 'activate')
    ORDER BY cp.id
    """
)

_SQL_ROI_WINDOW = text(
    "SELECT min(first_day) AS first_day, max(last_day) AS last_day "
    "FROM analytics.ad_product_links"
)

_SQL_ROI_DATA_WINDOW = text(
    """
    -- 起始/截止日可裁剪数据(销售∪退款)的真实时间跨度:供页面回填日期框。
    -- 范围与 w_start/w_end 的实际裁剪口径一致(销售按 paid_at、退款按
    -- updated_at_source、同一批状态白名单),不传窗口时全跨度 = 不限。
    -- 注意:ad 不在此列 —— 广告视图按窗口聚合无法按日切片,日期不影响 ad。
    WITH croppable AS (
        SELECT (so.paid_at AT TIME ZONE 'UTC')::date AS d
        FROM commerce.sales_orders so
        WHERE so.status = ANY(CAST(:paid_statuses AS text[]))
          AND (CAST(:shop_pk AS bigint) IS NULL
               OR so.shop_pk = CAST(:shop_pk AS bigint))
        UNION
        SELECT (c.updated_at_source AT TIME ZONE 'UTC')::date AS d
        FROM after_sales.cases c
        WHERE c.status IN (:st0, :st1)
          AND (CAST(:shop_pk AS bigint) IS NULL
               OR c.shop_pk = CAST(:shop_pk AS bigint))
    )
    SELECT min(d) AS first_day, max(d) AS last_day FROM croppable
    """
)

_SQL_ROI_UNATTRIBUTED = text(
    """
    SELECT count(*)::int AS n
    FROM (
        -- 未归属已完结退款行(§4.2 rule 0 + rule 3)：两类行都进 meta
        -- unattributed_refund_lines 页脚提示,不静默丢。
        -- (a) 归属不上：case_lines 无 sales_order_line_id,或 line 无 spu
        SELECT cl.id
        FROM after_sales.cases c
        JOIN after_sales.case_lines cl ON cl.case_id = c.id
        LEFT JOIN commerce.sales_order_lines sl ON sl.id = cl.sales_order_line_id
        WHERE c.status IN (:st0, :st1)
          AND (cl.sales_order_line_id IS NULL OR sl.spu_pk IS NULL)
          AND (CAST(:shop_pk AS bigint) IS NULL
               OR c.shop_pk = CAST(:shop_pk AS bigint))
        UNION ALL
        -- (b) 订单状态异常(白名单外且非 CANCELLED,如 UNPAID/ON_HOLD)的
        --     已完结退款行：§4.2 rule 0 防御性进未归属。这些行在退款 SQL 里
        --     有 spu_pk 但因状态不命中任何 FILTER → 不进 refund_* 桶,
        --     这里显式计数;与 (a) 的 spu_pk IS NOT NULL 条件互斥不重复
        SELECT cl.id
        FROM after_sales.cases c
        JOIN after_sales.case_lines cl ON cl.case_id = c.id
        JOIN commerce.sales_order_lines sl ON sl.id = cl.sales_order_line_id
        JOIN commerce.sales_orders so ON so.id = c.order_pk
        WHERE c.status IN (:st0, :st1)
          AND sl.spu_pk IS NOT NULL
          AND so.status <> ALL (CAST(:paid_statuses AS text[]))
          AND so.status <> 'CANCELLED'
          AND (CAST(:shop_pk AS bigint) IS NULL
               OR c.shop_pk = CAST(:shop_pk AS bigint))
    ) u
    """
)


def _fmt_money(value: Decimal | None) -> str | None:
    """money → 4 位小数字符串(§5.1-4);None 保持 JSON null。"""
    if value is None:
        return None
    return format(value.quantize(_MONEY_Q, rounding=ROUND_HALF_UP), ".4f")


def _fmt_ratio(value: Decimal | None) -> str | None:
    """比率 → 2 位小数字符串(§5.1-4);None 保持 JSON null。"""
    if value is None:
        return None
    return format(value.quantize(_RATIO_Q, rounding=ROUND_HALF_UP), ".2f")


def _row_int(value) -> int:
    """Aggregate 列安全转 int（COUNT/SUM/COALESCE 永不为非数字，锚点防呆）。"""
    try:
        return int(value) if value is not None else 0
    except (TypeError, ValueError):
        return 0


def _query_spu_roi(
    sess: Session,
    *,
    q: str | None,
    shop_pk: int | None,
    include_all: bool,
    sort_field: str,
    ascending: bool,
    limit: int,
    offset: int,
    fee_rate: Decimal | None,
    w_start: date | None = None,
    w_end: date | None = None,
) -> dict:
    """查询 + 计算 + 分页，返回 §5.3 envelope（items/total/totals/meta）。

    行与 totals 同源:totals 由行级 USD 值(同一组 CTE 结果)服务端加总;
    totals.roi_real 额外用原生合计(Σ net_cash 原币一次换算)对账(§5.4-4)。
    金额底层原币计算、输出层一次换算(§4.2 通用规则),绝不在客户端换算。
    fee_rate=None → 用固定基线 FEE_RATE_BASELINE;有值 → 页面覆写。
    w_start/w_end(ISO 日期,可选):提供时销售按 paid_at、退款按
    updated_at_source 裁剪(左闭右开,+1 天);不提供 → 全历史累计(§4.5)。
    """
    rate = fee_rate if fee_rate is not None else FEE_RATE_BASELINE
    # 汇率解析：在线 fx 缓存优先、D9 常量回退（每次请求一次 DB 读）
    fx_cny_usd, fx_usd_vnd, fx_as_of, fx_source = _resolve_fx_rates(sess)
    # 窗口边界:yyyymmdd → UTC 当日 00:00 / 次日 00:00(w_end 含当日)
    ws_dt = datetime.combine(w_start, time.min, tzinfo=UTC) if w_start else None
    we_dt = (
        datetime.combine(w_end + timedelta(days=1), time.min, tzinfo=UTC)
        if w_end
        else None
    )
    cats = (
        sess.execute(
            _SQL_ROI_CATALOG,
            {"shop_pk": shop_pk, "q": q, "active_only": include_all},
        )
        .mappings()
        .all()
    )
    # pi-lens-ignore: python-sql-injection
    ad_rows = sess.execute(_SQL_ROI_AD).mappings().all()
    ad_map = {r["spu_pk"]: r for r in ad_rows if r["spu_pk"] is not None}
    # pi-lens-ignore: python-sql-injection
    sales_rows = (
        sess.execute(
            _SQL_ROI_SALES,
            {
                "paid_statuses": _PAID_STATUSES,
                "ws": ws_dt,
                "we": we_dt,
            },
        )
        .mappings()
        .all()
    )
    sales_map = {r["spu_pk"]: r for r in sales_rows if r["spu_pk"] is not None}
    # pi-lens-ignore: python-sql-injection
    refund_rows = (
        sess.execute(
            _SQL_ROI_REFUNDS,
            {
                "paid_statuses": _PAID_STATUSES,
                "st0": _CASE_COMPLETED_STATUSES[0],
                "st1": _CASE_COMPLETED_STATUSES[1],
                "ws": ws_dt,
                "we": we_dt,
            },
        )
        .mappings()
        .all()
    )
    refund_map = {r["spu_pk"]: r for r in refund_rows if r["spu_pk"] is not None}
    # pi-lens-ignore: python-sql-injection
    cost_rows = sess.execute(_SQL_ROI_COSTS).mappings().all()
    cost_map = {r["spu_pk"]: Decimal(r["unit_cost"]) for r in cost_rows}

    # ── 每 SPU 一行(原币聚合 → USD 换算 → 派生指标)──────────────────
    plain: list[dict] = []
    # totals.roi_real 用原生累计(Σ net_cash 原币)一次换算对账(§5.4-4)
    total_native_net_cash_vnd = Decimal(0)
    total_spend_dec = Decimal(0)
    total_return_loss_dec = Decimal(0)
    for cat in cats:
        pk = cat["spu_pk"]
        ad = ad_map.get(pk)
        sales = sales_map.get(pk)
        refund = refund_map.get(pk)
        if not include_all and ad is None and sales is None and refund is None:
            continue  # §5.1-7:默认只含有活动(广告∨销售∨退款)的 SPU

        spend = Decimal(ad["spend"]) if ad else Decimal(0)
        gmv_ad = Decimal(ad["gmv_ad"]) if ad else Decimal(0)
        ad_orders = _row_int(ad["ad_orders"]) if ad else 0
        ad_count = _row_int(ad["ad_count"]) if ad else 0
        ad_first_day = ad["ad_first_day"] if ad else None
        ad_last_day = ad["ad_last_day"] if ad else None

        order_count = _row_int(sales["order_count"]) if sales else 0
        units_dec = Decimal(sales["units_sold"]) if sales else Decimal(0)
        units_sold = _row_int(units_dec)
        sales_vnd = Decimal(sales["sales"]) if sales else Decimal(0)

        # refund 行级金额(缺失行不造数:净额桶金额直取,取消桶分已知/未知)
        refund_only_vnd = (
            Decimal(refund["refund_only_amount"]) if refund else Decimal(0)
        )
        refund_return_vnd = (
            Decimal(refund["refund_return_amount"]) if refund else Decimal(0)
        )
        refund_cancelled_vnd = (
            Decimal(refund["refund_cancelled_amount"]) if refund else Decimal(0)
        )
        refund_only_qty = _row_int(refund["refund_only_qty"]) if refund else 0
        refund_return_qty = _row_int(refund["refund_return_qty"]) if refund else 0
        refund_cancelled_qty = _row_int(refund["refund_cancelled_qty"]) if refund else 0
        refund_cancelled_missing = (
            _row_int(refund["refund_cancelled_missing_lines"]) if refund else 0
        )
        refund_net_vnd = refund_only_vnd + refund_return_vnd

        # 单位成本解析(§4.2):MANUAL 有效行优先,未命中 → K1=30 CNY
        unit_cost_cny = cost_map.get(pk, K1_DEFAULT_CNY)
        cost_source = "MANUAL" if pk in cost_map else "DEFAULT_K1"
        unit_cost_usd = unit_cost_cny * fx_cny_usd

        # USD 换算(先原币加总、再一次换算,§4.2 通用规则)
        sales_usd = sales_vnd / fx_usd_vnd
        net_cash_vnd = sales_vnd - refund_net_vnd
        net_cash_usd = net_cash_vnd / fx_usd_vnd
        refund_only_usd = refund_only_vnd / fx_usd_vnd
        refund_return_usd = refund_return_vnd / fx_usd_vnd
        refund_net_usd = refund_only_usd + refund_return_usd
        refund_cancelled_usd = refund_cancelled_vnd / fx_usd_vnd

        fee_usd = sales_usd * rate  # M19:本期全按 sales×费率估算
        return_loss_usd = refund_return_qty * unit_cost_usd  # M13b 全损
        cogs_all_usd = units_dec * unit_cost_usd  # M18 全售出货本(含退回件)
        net_profit = net_cash_usd - cogs_all_usd - spend - fee_usd  # M18
        nc_prime = net_cash_usd - return_loss_usd  # M17 NC′(全 USD)
        cogs_kept = (units_dec - refund_return_qty) * unit_cost_usd  # M17

        roi_real: Decimal | None = None
        if spend != 0:
            roi_real = nc_prime / spend  # M14
        roi_l0: Decimal | None = gmv_ad / spend if spend != 0 else None  # M4
        cpa: Decimal | None = spend / ad_orders if ad_orders else None  # M15
        refund_rate: Decimal | None = (
            refund_net_usd / sales_usd if sales_usd != 0 else None
        )  # M12

        breakeven_denom = nc_prime - cogs_kept - fee_usd
        roi_breakeven: Decimal | None = None
        if spend != 0 and breakeven_denom > 0:
            roi_breakeven = nc_prime / breakeven_denom  # M17

        total_native_net_cash_vnd += net_cash_vnd
        total_spend_dec += spend
        total_return_loss_dec += return_loss_usd

        plain.append(
            {
                "spu_pk": pk,
                "spu_id": cat["spu_id"],
                "title": cat["title"],
                "status": cat["status"],
                "main_image_url": cat["main_image_url"],
                "shop_id": cat["shop_id"],
                "shop_name": cat["shop_name"],
                "ad_count": ad_count,
                "ad_orders": ad_orders,
                "spend": spend,
                "gmv_ad": gmv_ad,
                "roi_l0": roi_l0,
                "ad_first_day": ad_first_day,
                "ad_last_day": ad_last_day,
                "order_count": order_count,
                "units_sold": units_sold,
                "sales": sales_usd,
                "refund_only_qty": refund_only_qty,
                "refund_only_amount": refund_only_usd,
                "refund_return_qty": refund_return_qty,
                "refund_return_amount": refund_return_usd,
                "refund_net_qty": refund_only_qty + refund_return_qty,
                "refund_net_amount": refund_net_usd,
                "refund_rate": refund_rate,
                "refund_cancelled_qty": refund_cancelled_qty,
                "refund_cancelled_amount": refund_cancelled_usd,
                "refund_cancelled_missing_lines": refund_cancelled_missing,
                "return_loss": return_loss_usd,
                "net_profit": net_profit,
                "platform_fee": fee_usd,
                "roi_real": roi_real,
                "roi_breakeven": roi_breakeven,
                "cpa": cpa,
                "unit_cost_used": unit_cost_usd,
                "cost_source": cost_source,
            }
        )

    # ── 排序(None 沉底;实际 ROI 升序遇同值按消耗降序 → 可复现,§7.6)──
    def _sort_key(row: dict):
        value = row[sort_field]
        if value is None:
            return (True, Decimal(0), Decimal(0))
        primary = value if ascending else -value
        tie = -row["spend"] if ascending else row["spend"]
        return (False, primary, tie)

    plain.sort(key=_sort_key)

    # ── totals(跨分页、当前筛选;行级 USD 服务端加总)─────────────────
    money_total = {
        "spend": sum((r["spend"] for r in plain), Decimal(0)),
        "sales": sum((r["sales"] for r in plain), Decimal(0)),
        "refund_net_amount": sum((r["refund_net_amount"] for r in plain), Decimal(0)),
        "return_loss": sum((r["return_loss"] for r in plain), Decimal(0)),
        "net_profit": sum((r["net_profit"] for r in plain), Decimal(0)),
    }
    # 整体实际 ROI = Σ(net_cash−return_loss) / Σspend(全 USD;原生 VND
    # 合计后一次换算,2 位小数串);Σspend=0 → null(页面显示 —)
    roi_real_total: str | None = None
    if total_spend_dec != 0:
        total_nc_prime = total_native_net_cash_vnd / fx_usd_vnd - total_return_loss_dec
        roi_real_total = _fmt_ratio(total_nc_prime / total_spend_dec)
    totals = {
        "row_count": len(plain),
        "spend": _fmt_money(money_total["spend"]),
        "sales": _fmt_money(money_total["sales"]),
        "refund_net_amount": _fmt_money(money_total["refund_net_amount"]),
        "return_loss": _fmt_money(money_total["return_loss"]),
        "net_profit": _fmt_money(money_total["net_profit"]),
        "roi_real": roi_real_total,
    }

    # ── meta(§5.3)────────────────────────────────────────────────────
    window_row = sess.execute(_SQL_ROI_WINDOW).mappings().first()
    data_window_row = (
        sess.execute(
            _SQL_ROI_DATA_WINDOW,
            {
                "paid_statuses": _PAID_STATUSES,
                "st0": _CASE_COMPLETED_STATUSES[0],
                "st1": _CASE_COMPLETED_STATUSES[1],
                "shop_pk": shop_pk,
            },
        )
        .mappings()
        .first()
    )
    unattributed = (
        sess.execute(
            _SQL_ROI_UNATTRIBUTED,
            {
                "st0": _CASE_COMPLETED_STATUSES[0],
                "st1": _CASE_COMPLETED_STATUSES[1],
                "paid_statuses": _PAID_STATUSES,
                "shop_pk": shop_pk,
            },
        )
        .mappings()
        .first()
    )
    override_rate = None if fee_rate is None else str(fee_rate)
    if w_start is None and w_end is None:
        window_note = (
            "ad=视图全窗口累计(供参考)；销售/退款=全历史(未裁剪,可传 w_start/w_end)"
        )
    else:
        window_note = (
            "ad=视图全窗口累计(供参考)；销售/退款已裁剪:"
            f"{w_start.isoformat() if w_start else '不限'}"
            f" ~ {w_end.isoformat() if w_end else '不限'}(含 w_end 当日)"
        )
    meta = {
        "fx": {
            "usd_vnd": _fmt_money(fx_usd_vnd),
            "cny_usd": format(
                fx_cny_usd.quantize(_MONEY_Q, rounding=ROUND_HALF_UP), ".4f"
            ),
            "as_of": fx_as_of,
            "source": fx_source,
        },
        "cost_assumption": (
            "按 SPU 解析：人工成本(MANUAL)有效行优先，未命中 → "
            "K1=30 CNY/件 ≈ $4.43/件；DEFAULT_K1 行页面 ⚠ 可跳 manual-costs 补录"
        ),
        "fee": {
            "mode": "override" if override_rate is not None else "baseline",
            "rate": override_rate
            if override_rate is not None
            else str(FEE_RATE_BASELINE),
            "override": override_rate,
            "note": (
                "平台佣金=平台从销售额直接扣除的全部费用(抽佣/联盟/运费类)；"
                "已结算按实际，未结算按基线；解析上线后自动分层"
            ),
        },
        "window": {
            "first_day": (
                window_row["first_day"].isoformat() if window_row["first_day"] else None
            ),
            "last_day": (
                window_row["last_day"].isoformat() if window_row["last_day"] else None
            ),
            #: 日期过滤可裁剪数据(销售∪退款)的真实跨度 —— 页面用它把起始/
            #: 截止日按当前数据真实呈现(全跨度 = 不限,结果一致;仅回填展示)。
            "coverage_first_day": (
                data_window_row["first_day"].isoformat()
                if data_window_row and data_window_row["first_day"]
                else None
            ),
            "coverage_last_day": (
                data_window_row["last_day"].isoformat()
                if data_window_row and data_window_row["last_day"]
                else None
            ),
            "note": window_note,
        },
        "unattributed_refund_lines": unattributed["n"] if unattributed else 0,
        "computed_at": datetime.now(UTC).isoformat(),
        "currency": {
            "display": "USD",
            "native": {"ad": "USD", "sales_refund": "VND", "cost": "CNY"},
        },
    }

    # ── 分页切片 + money/比率序列化 ─────────────────────────────────
    page = plain[offset : offset + limit]
    items = []
    for r in page:
        items.append(
            {
                "spu_pk": r["spu_pk"],
                "spu_id": r["spu_id"],
                "title": r["title"],
                "status": r["status"],
                "main_image_url": r["main_image_url"],
                "shop_id": r["shop_id"],
                "shop_name": r["shop_name"],
                "ad_count": r["ad_count"],
                "ad_orders": r["ad_orders"],
                "spend": _fmt_money(r["spend"]),
                "gmv_ad": _fmt_money(r["gmv_ad"]),
                "roi_l0": _fmt_ratio(r["roi_l0"]),
                "ad_first_day": (
                    r["ad_first_day"].isoformat() if r["ad_first_day"] else None
                ),
                "ad_last_day": (
                    r["ad_last_day"].isoformat() if r["ad_last_day"] else None
                ),
                "order_count": r["order_count"],
                "units_sold": r["units_sold"],
                "sales": _fmt_money(r["sales"]),
                "refund_only_qty": r["refund_only_qty"],
                "refund_only_amount": _fmt_money(r["refund_only_amount"]),
                "refund_return_qty": r["refund_return_qty"],
                "refund_return_amount": _fmt_money(r["refund_return_amount"]),
                "refund_net_qty": r["refund_net_qty"],
                "refund_net_amount": _fmt_money(r["refund_net_amount"]),
                "refund_rate": _fmt_ratio(r["refund_rate"]),
                "refund_cancelled_qty": r["refund_cancelled_qty"],
                "refund_cancelled_amount": _fmt_money(r["refund_cancelled_amount"]),
                "refund_cancelled_missing_lines": r["refund_cancelled_missing_lines"],
                "return_loss": _fmt_money(r["return_loss"]),
                "net_profit": _fmt_money(r["net_profit"]),
                "platform_fee": _fmt_money(r["platform_fee"]),
                "roi_real": _fmt_ratio(r["roi_real"]),
                "roi_breakeven": _fmt_ratio(r["roi_breakeven"]),
                "cpa": _fmt_money(r["cpa"]),
                "unit_cost_used": _fmt_money(r["unit_cost_used"]),
                "cost_source": r["cost_source"],
            }
        )

    return {"items": items, "total": len(plain), "totals": totals, "meta": meta}


_ROI_SORT_FIELDS = (
    "roi_real",
    "spend",
    "refund_rate",
    "net_profit",
    "sales",
    "ad_count",
    "gmv_ad",
    "order_count",
    "units_sold",
    "refund_net_amount",
    "return_loss",
    "roi_breakeven",
)


@roi_router.get("/spu-roi")
def list_spu_roi(
    sess: Session = Depends(get_session),  # noqa: B008 — FastAPI DI 惯例
    q: str | None = Query(default=None, max_length=200),
    sort: Literal[
        "roi_real",
        "spend",
        "refund_rate",
        "net_profit",
        "sales",
        "ad_count",
        "gmv_ad",
        "order_count",
        "units_sold",
        "refund_net_amount",
        "return_loss",
        "roi_breakeven",
    ] = "roi_real",
    order: Literal["asc", "desc"] = "asc",
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    include_all: bool = Query(default=False),
    shop_pk: int | None = Query(default=None, ge=1),
    fee_rate: str | None = Query(default=None, max_length=20),
    w_start: date | None = Query(default=None),  # noqa: B008 — FastAPI 惯例
    w_end: date | None = Query(default=None),  # noqa: B008 — FastAPI 惯例
) -> dict:
    """SPU 实际 ROI 主表(每 SPU 一行)。readonly;消费方为 /v2/pages/spu-roi。

    q = spu_id 子串搜索;sort/order 控制排序(默认实际 ROI 升序,§7.3);
    include_all=true 把无任何活动的目录 SPU 也拉进来(§5.1-7,限 ACTIVE);
    fee_rate 页面覆写平台佣金费率(缺省用固定基线 0.1156,D10);
    w_start/w_end(ISO 日期)裁剪销售(paid_at)与退款(updated_at_source),
    不传 = 全历史累计;ad 视图无日期参数,始终全窗口累计(§4.5)。
    """
    fee_value: Decimal | None = None
    if fee_rate is not None and fee_rate.strip():
        raw = fee_rate.strip()
        try:
            fee_value = Decimal(raw)
        except Exception as exc:
            # Decimal 解析失败(pydantic InvalidOperation 语义)
            raise HTTPException(
                status_code=422, detail="fee_rate must be a decimal"
            ) from exc
        if not fee_value.is_finite():
            # Decimal('NaN')/('Infinity') 与 0 比较不报错(NaN 比较恒 False),
            # 会穿透到 _fmt_money quantize 造成 500;统一按 422 拒掉
            raise HTTPException(
                status_code=422, detail="fee_rate must be a finite decimal"
            )
        if fee_value.copy_abs() > Decimal("1e6"):
            # 有限但指数量级巨大(如 1e9999999)会穿透后续乘法/_fmt_money
            # quantize 抛 Overflow/InvalidOperation → 500;超合理范围按 422 拒掉。
            # 注意用 copy_abs():内置 abs()/一元负号套默认 context(Emax=999999),
            # 对超量级值自身就抛 Overflow;copy_abs() 不套 context、比较是精确的
            raise HTTPException(
                status_code=422,
                detail="fee_rate out of reasonable range (|fee_rate| <= 1e6)",
            )
        if fee_value < 0:
            raise HTTPException(status_code=422, detail="fee_rate must be >= 0")
    if w_start is not None and w_end is not None and w_start > w_end:
        raise HTTPException(status_code=422, detail="w_start must be <= w_end")
    return _query_spu_roi(
        sess,
        q=q or None,
        shop_pk=shop_pk,
        include_all=include_all,
        sort_field=sort,
        ascending=(order != "desc"),
        limit=limit,
        offset=offset,
        fee_rate=fee_value,  # None → 基线(baseline);有值 → override
        w_start=w_start,
        w_end=w_end,
    )
