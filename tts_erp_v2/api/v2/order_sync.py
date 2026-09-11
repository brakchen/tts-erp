"""/v2/order-sync/* — Chrome 扩展订单/物流/结算数据同步。

插件从 TikTok Seller Center 抓取的 HTTP 响应通过此端点写入后端。
- POST /has-data: 批量查业务表存在性
- POST /dumps: 接收 dump → inline 解析 → 写业务表 + raw_log
- GET /synced-ids: 查询已同步 id 列表

详见 tech-doc/chrome-ext-order-sync-design.md。
"""

from __future__ import annotations

import json
import logging
import sys
import uuid
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse
from pydantic import AliasChoices, BaseModel, Field, ValidationError, field_validator
from sqlalchemy import update as sa_update
from sqlalchemy.orm import Session

from tts_erp_v2.api.deps import get_session
from tts_erp_v2.db.models.plugin import RawLog
from tts_erp_v2.plugin.orders.parser import (
    parse_logistics_response,
    parse_order_response,
    parse_statement_list_response,
    parse_statement_transaction_response,
)
from tts_erp_v2.plugin.orders.repository import (
    has_data_bulk,
    list_synced_ids,
    write_raw_log,
)

# ─── Config ───────────────────────────────────────────────────────────

PROTOCOL_VERSION = 1
MAX_BODY_BYTES = 2 * 1024 * 1024  # 2 MB
MAX_IDS = 500
VALID_DOMAINS = {"orders", "logistics", "statements"}

_PATH_HAS_DATA = "/v2/order-sync/has-data"
_PATH_DUMPS = "/v2/order-sync/dumps"
_PATH_SYNCED_IDS = "/v2/order-sync/synced-ids"


# ─── Logger ───────────────────────────────────────────────────────────

log = logging.getLogger("tts_erp_v2.order_sync.ingest")
log.setLevel(logging.INFO)
if not any(
    isinstance(h, logging.StreamHandler) and h.stream is sys.stdout
    for h in log.handlers
):
    _ingest_stdout = logging.StreamHandler(sys.stdout)
    _ingest_stdout.setFormatter(logging.Formatter("%(message)s"))
    log.addHandler(_ingest_stdout)


def _log_event(
    *,
    level: int,
    request_id: str | None,
    key_prefix: str | None,
    method: str,
    path: str,
    status: int,
    records_in: int | None = None,
    records_ok: int | None = None,
    error_code: str | None = None,
    message: str | None = None,
) -> None:
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
    if error_code:
        parts.append(f"error_code={error_code}")
    if message:
        safe_msg = " ".join(str(message).split())[:500]
        parts.append(f"message={safe_msg}")
    log.log(level, " ".join(parts))


# ─── Router ───────────────────────────────────────────────────────────

router = APIRouter(prefix="/v2/order-sync", tags=["order-sync"])


# ─── Pydantic Models ─────────────────────────────────────────────────


class ScopeIn(BaseModel):
    sellerId: str = Field(min_length=1, max_length=128)
    shopId: str = Field(min_length=1, max_length=128)


class HasDataRequest(BaseModel):
    scope: ScopeIn
    domain: str = Field(min_length=1, max_length=32)
    ids: list[str] = Field(min_length=1, max_length=MAX_IDS)

    @field_validator("domain")
    @classmethod
    def _domain_must_be_valid(cls, v: str) -> str:
        if v not in VALID_DOMAINS:
            raise ValueError(f"domain must be one of {sorted(VALID_DOMAINS)}")
        return v


class DumpRequestIn(BaseModel):
    params: dict[str, Any] | None = None
    body: dict[str, Any] | None = None


class DumpResponseIn(BaseModel):
    status: int
    body: dict[str, Any]


class DumpBodyIn(BaseModel):
    domain: str = Field(min_length=1, max_length=32)
    mainOrderId: str | None = Field(default=None, max_length=128)
    statementId: str | None = Field(default=None, max_length=128)
    statementVersion: int | None = None
    endpoint: str = Field(min_length=1, max_length=512)
    method: str = Field(min_length=1, max_length=16)
    request: DumpRequestIn
    response: DumpResponseIn
    # 统一命名 createdAt（2026-09-10 用户拍板）；capturedAt 为旧插件兼容别名。
    createdAt: datetime = Field(
        validation_alias=AliasChoices("createdAt", "capturedAt"),
    )

    @field_validator("domain")
    @classmethod
    def _domain_must_be_valid(cls, v: str) -> str:
        if v not in VALID_DOMAINS:
            raise ValueError(f"domain must be one of {sorted(VALID_DOMAINS)}")
        return v

    @field_validator("createdAt")
    @classmethod
    def _created_at_must_be_utc(cls, v: datetime) -> datetime:
        if v.tzinfo is None:
            raise ValueError(
                "createdAt must include a timezone (use ISO-8601 with 'Z' or '+00:00')"
            )
        return v


class DumpRequest(BaseModel):
    protocolVersion: int = Field(default=PROTOCOL_VERSION)
    requestId: str | None = Field(default=None, min_length=1, max_length=128)
    scope: ScopeIn
    dump: DumpBodyIn


# ─── Helpers ──────────────────────────────────────────────────────────


async def _raw_body(request: Request) -> bytes:
    return await request.body()


def _request_id(request: Request) -> str:
    rid = request.headers.get("x-request-id")
    if rid:
        return rid[:128]
    return f"req-{uuid.uuid4()}"


def _key_prefix(request: Request) -> str | None:
    key_hash = request.scope.get("api_key_hash")
    return key_hash[:16] if isinstance(key_hash, str) else None


def _error_response(
    *,
    status: int,
    code: str,
    message: str,
    request_id: str | None,
) -> JSONResponse:
    return JSONResponse(
        status_code=status,
        content={
            "code": code,
            "message": message,
            "requestId": request_id or f"req-{uuid.uuid4()}",
        },
    )


def _ok_response(
    *,
    request_id: str,
    data: dict[str, Any],
) -> JSONResponse:
    return JSONResponse(
        status_code=200,
        content={
            "code": 0,
            "requestId": request_id,
            "data": data,
        },
    )


# ─── has-data endpoint ───────────────────────────────────────────────


@router.post("/has-data")
def post_has_data(
    request: Request,
    body_bytes: bytes = Depends(_raw_body),
    sess: Session = Depends(get_session),  # noqa: B008
) -> JSONResponse:
    """批量查业务表存在性。

    插件拿到 id 列表后，一次请求查出哪些已有数据，只对缺失的发 TikTok 请求。
    """
    request_id = _request_id(request)
    key_prefix = _key_prefix(request)
    audit_path = _PATH_HAS_DATA

    if len(body_bytes) > MAX_BODY_BYTES:
        return _audit_and_error(
            request_id=request_id,
            status=413,
            code="PAYLOAD_TOO_LARGE",
            message=f"body size {len(body_bytes)} exceeds maximum {MAX_BODY_BYTES}",
            key_prefix=key_prefix,
            method="POST",
            path=audit_path,
        )

    try:
        payload = HasDataRequest.model_validate_json(body_bytes)
    except ValidationError as exc:
        return _audit_and_error(
            request_id=request_id,
            status=400,
            code="SCHEMA_INVALID",
            message=str(exc),
            key_prefix=key_prefix,
            method="POST",
            path=audit_path,
        )

    covered = has_data_bulk(
        sess,
        domain=payload.domain,
        shop_id=payload.scope.shopId,
        ids=payload.ids,
    )

    _log_event(
        level=logging.INFO,
        request_id=request_id,
        key_prefix=key_prefix,
        method="POST",
        path=audit_path,
        status=200,
        records_in=len(payload.ids),
        records_ok=sum(1 for v in covered.values() if v),
    )

    return _ok_response(
        request_id=request_id,
        data={
            "domain": payload.domain,
            "covered": covered,
        },
    )


# ─── dumps endpoint ──────────────────────────────────────────────────


@router.post("/dumps")
def post_dumps(
    request: Request,
    body_bytes: bytes = Depends(_raw_body),
    sess: Session = Depends(get_session),  # noqa: B008
) -> JSONResponse:
    """接收 dump → 解析 → 写业务表 + raw_log。"""
    request_id = _request_id(request)
    key_prefix = _key_prefix(request)
    audit_path = _PATH_DUMPS

    # 413 尺寸闸
    if len(body_bytes) > MAX_BODY_BYTES:
        return _audit_and_error(
            request_id=request_id,
            status=413,
            code="PAYLOAD_TOO_LARGE",
            message=f"body size {len(body_bytes)} exceeds maximum {MAX_BODY_BYTES}",
            key_prefix=key_prefix,
            method="POST",
            path=audit_path,
        )

    # JSON 解析
    try:
        body = json.loads(body_bytes)
    except json.JSONDecodeError as exc:
        return _audit_and_error(
            request_id=request_id,
            status=400,
            code="MALFORMED_JSON",
            message=f"JSON parse error: {exc.msg}",
            key_prefix=key_prefix,
            method="POST",
            path=audit_path,
        )

    # Pydantic 校验
    try:
        payload = DumpRequest.model_validate(body)
    except ValidationError as exc:
        return _audit_and_error(
            request_id=request_id,
            status=400,
            code="SCHEMA_INVALID",
            message=str(exc),
            key_prefix=key_prefix,
            method="POST",
            path=audit_path,
        )

    shop_id = payload.scope.shopId
    domain = payload.dump.domain
    endpoint = payload.dump.endpoint
    captured_at = payload.dump.createdAt
    request_params = payload.dump.request.params
    request_body = payload.dump.request.body
    response_body = payload.dump.response.body
    main_order_id = payload.dump.mainOrderId

    # 1. 先写 raw_log 拿到 log_id（解析函数需要 log_id 关联）
    log_id = write_raw_log(
        sess,
        domain=domain,
        shop_id=shop_id,
        endpoint=endpoint,
        captured_at=captured_at,
        request_params=request_params,
        request_body=request_body,
        response_body=response_body,
        parse_error=None,  # 先写成功，解析失败再更新
        rows_written=0,
    )
    sess.flush()  # 确保 log_id 可用

    # 2. 解析 → 写业务表（传入真实 log_id）
    parse_error: str | None = None
    rows_written = 0

    try:
        if domain == "orders":
            rows_written = parse_order_response(
                sess,
                log_id=log_id,
                shop_id=shop_id,
                response_body=response_body,
                captured_at=captured_at,
            )
        elif domain == "logistics":
            if not main_order_id:
                parse_error = "mainOrderId is required for logistics domain"
            else:
                rows_written = parse_logistics_response(
                    sess,
                    log_id=log_id,
                    shop_id=shop_id,
                    order_id=main_order_id,
                    response_body=response_body,
                    captured_at=captured_at,
                )
        elif domain == "statements":
            data = response_body.get("data") or {}
            if "sku_record" in data:
                rows_written = parse_statement_transaction_response(
                    sess,
                    log_id=log_id,
                    shop_id=shop_id,
                    response_body=response_body,
                    captured_at=captured_at,
                )
            else:
                rows_written = parse_statement_list_response(
                    sess,
                    log_id=log_id,
                    shop_id=shop_id,
                    response_body=response_body,
                    captured_at=captured_at,
                )
    except Exception as exc:
        parse_error = f"{type(exc).__name__}: {exc}"
        log.exception("parse error for domain=%s shop_id=%s", domain, shop_id)

    # 3. 更新 raw_log 的解析结果
    if parse_error or rows_written > 0:
        sess.execute(
            sa_update(RawLog)
            .where(RawLog.id == log_id)
            .values(parse_error=parse_error, rows_written=rows_written)
        )

    # commit
    sess.commit()

    if parse_error:
        _log_event(
            level=logging.WARNING,
            request_id=request_id,
            key_prefix=key_prefix,
            method="POST",
            path=audit_path,
            status=200,
            records_in=1,
            records_ok=0,
            error_code="PARSE_ERROR",
            message=parse_error,
        )
        return _ok_response(
            request_id=request_id,
            data={
                "status": "parse_error",
                "logId": log_id,
                "rowsWritten": 0,
                "parseError": parse_error,
            },
        )

    _log_event(
        level=logging.INFO,
        request_id=request_id,
        key_prefix=key_prefix,
        method="POST",
        path=audit_path,
        status=200,
        records_in=1,
        records_ok=rows_written,
    )

    return _ok_response(
        request_id=request_id,
        data={
            "status": "inserted",
            "logId": log_id,
            "rowsWritten": rows_written,
        },
    )


# ─── synced-ids endpoint ─────────────────────────────────────────────


@router.get("/synced-ids")
def get_synced_ids(
    request: Request,
    shopId: str = Query(min_length=1, max_length=128),
    domain: str = Query(min_length=1, max_length=32),
    limit: int = Query(default=500, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    sess: Session = Depends(get_session),  # noqa: B008
) -> JSONResponse:
    """查询已同步 id 列表。"""
    request_id = _request_id(request)
    key_prefix = _key_prefix(request)
    audit_path = f"{_PATH_SYNCED_IDS}?shopId={shopId}&domain={domain}"

    if domain not in VALID_DOMAINS:
        return _audit_and_error(
            request_id=request_id,
            status=400,
            code="SCHEMA_INVALID",
            message=f"domain must be one of {sorted(VALID_DOMAINS)}",
            key_prefix=key_prefix,
            method="GET",
            path=audit_path,
        )

    ids, total = list_synced_ids(
        sess,
        domain=domain,
        shop_id=shopId,
        limit=limit,
        offset=offset,
    )

    _log_event(
        level=logging.INFO,
        request_id=request_id,
        key_prefix=key_prefix,
        method="GET",
        path=audit_path,
        status=200,
        records_in=1,
        records_ok=total,
    )

    return _ok_response(
        request_id=request_id,
        data={
            "domain": domain,
            "ids": ids,
            "total": total,
            "limit": limit,
            "offset": offset,
        },
    )


# ─── audit + error helper ────────────────────────────────────────────


def _audit_and_error(
    *,
    request_id: str,
    status: int,
    code: str,
    message: str,
    key_prefix: str | None,
    method: str,
    path: str,
) -> JSONResponse:
    safe_message = " ".join(str(message).split())[:500]
    sys.stderr.write(
        f"[order-sync] reject status={status} code={code} "
        f"request_id={request_id} key_prefix={key_prefix or '-'} "
        f"method={method} path={path} message={safe_message}\n"
    )
    _log_event(
        level=logging.WARNING,
        request_id=request_id,
        key_prefix=key_prefix,
        method=method,
        path=path,
        status=status,
        error_code=code,
        message=safe_message,
    )
    return _error_response(
        status=status,
        code=code,
        message=message,
        request_id=request_id,
    )
