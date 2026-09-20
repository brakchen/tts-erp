"""/v2/order-sync/* — Chrome 扩展订单/物流/结算数据同步。

插件从 TikTok Seller Center 抓取的 HTTP 响应通过此端点写入后端。
- POST /has-data: 批量查业务表存在性
- POST /dumps: 接收 dump → inline 解析 → 写业务表 + plugin_logs 健康记录
- POST /reconcile: 统一返回订单锚点与物流增量候选

详见 tech-doc/chrome-ext-order-sync-design.md。
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import (
    AliasChoices,
    BaseModel,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)
from sqlalchemy.orm import Session

from tts_erp_v2.api.v2._common import (
    audit_and_error as _audit_and_error,
    key_prefix as _key_prefix,
    log_event as _log_event,
    ok_response as _ok_response,
    request_id as _request_id,
)
from tts_erp_v2.api.deps import get_session
from tts_erp_v2.plugin.orders.parser import (
    parse_after_sales_response,
    parse_logistics_response,
    parse_order_response,
    parse_statement_list_response,
    parse_statement_transaction_response,
)
from tts_erp_v2.plugin.orders.repository import (
    has_data_bulk,
    record_dump_health,
    reconcile_logistics,
    reconcile_orders,
)

# ─── Config ───────────────────────────────────────────────────────────

# NOTE: PROTOCOL_VERSION was previously used as Field(default=PROTOCOL_VERSION)
# on DumpRequest.protocolVersion but the default was removed. Unlike analytics.py
# which has SUPPORTED_PROTOCOL_VERSIONS and rejects incompatible versions, this
# module accepts any integer value without validation. Dead constant removed.
MAX_BODY_BYTES = 2 * 1024 * 1024  # 2 MB
MAX_IDS = 500
VALID_DOMAINS = {"orders", "logistics", "statements", "after_sales"}

_PATH_HAS_DATA = "/v2/order-sync/has-data"
_PATH_DUMPS = "/v2/order-sync/dumps"
_PATH_RECONCILE = "/v2/order-sync/reconcile"


# ─── Logger ───────────────────────────────────────────────────────────

log = logging.getLogger("tts_erp_v2.order_sync.ingest")



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
    # statements 的业务唯一键还包含 statement_version；缺省保持旧客户端兼容。
    versions: dict[str, int | list[int]] | None = None

    @field_validator("domain")
    @classmethod
    def _domain_must_be_valid(cls, v: str) -> str:
        if v not in VALID_DOMAINS:
            raise ValueError(f"domain must be one of {sorted(VALID_DOMAINS)}")
        return v


class ReconcileOrdersRequest(BaseModel):
    pageSize: int = Field(default=20, ge=1, le=500)
    sortInfo: str = Field(default="6", min_length=1, max_length=32)
    anchorPositions: list[int] = Field(default_factory=list, max_length=16)
    hotWindowSize: int = Field(default=40, ge=0, le=500)

    @field_validator("anchorPositions")
    @classmethod
    def _anchor_positions_must_be_non_negative(cls, values: list[int]) -> list[int]:
        if any(value < 0 for value in values):
            raise ValueError("anchorPositions must contain non-negative integers")
        return values


class ReconcileLogisticsRequest(BaseModel):
    limit: int = Field(default=500, ge=1, le=500)
    cursor: str | None = Field(default=None, max_length=128)


class ReconcileRequest(BaseModel):
    protocolVersion: int
    scope: ScopeIn
    domains: list[str] = Field(min_length=1, max_length=2)
    orders: ReconcileOrdersRequest | None = None
    logistics: ReconcileLogisticsRequest | None = None

    @field_validator("domains")
    @classmethod
    def _domains_must_be_valid_and_unique(cls, values: list[str]) -> list[str]:
        allowed = {"orders", "logistics"}
        if len(set(values)) != len(values):
            raise ValueError("domains must not contain duplicates")
        invalid = set(values) - allowed
        if invalid:
            raise ValueError(f"domains must be one of {sorted(allowed)}")
        return values

    @model_validator(mode="after")
    def _domain_blocks_must_be_present(self) -> ReconcileRequest:
        if "orders" in self.domains and self.orders is None:
            raise ValueError("orders block is required when domains contains orders")
        if "logistics" in self.domains and self.logistics is None:
            raise ValueError(
                "logistics block is required when domains contains logistics"
            )
        return self


class DumpRequestIn(BaseModel):
    params: dict[str, Any] | None = None
    body: dict[str, Any] | None = None


class DumpResponseIn(BaseModel):
    status: int
    body: dict[str, Any] | None = None


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
    protocolVersion: int
    requestId: str | None = Field(default=None, min_length=1, max_length=128)
    scope: ScopeIn
    dump: DumpBodyIn


# ─── Helpers ──────────────────────────────────────────────────────────


async def _raw_body(request: Request) -> bytes:
    return await request.body()





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
            logger=log,
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
            logger=log,
        )

    covered = has_data_bulk(
        sess,
        domain=payload.domain,
        shop_id=payload.scope.shopId,
        ids=payload.ids,
        versions=payload.versions,
    )

    _log_event(
        logger=log,
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


# ─── reconcile endpoint ─────────────────────────────────────────────


@router.post("/reconcile")
def post_reconcile(
    request: Request,
    body_bytes: bytes = Depends(_raw_body),
    sess: Session = Depends(get_session),  # noqa: B008
) -> JSONResponse:
    """统一返回订单增量校验锚点和物流可恢复同步候选。"""
    request_id = _request_id(request)
    key_prefix = _key_prefix(request)

    if len(body_bytes) > MAX_BODY_BYTES:
        return _audit_and_error(
            request_id=request_id,
            status=413,
            code="PAYLOAD_TOO_LARGE",
            message=f"body size {len(body_bytes)} exceeds maximum {MAX_BODY_BYTES}",
            key_prefix=key_prefix,
            method="POST",
            path=_PATH_RECONCILE,
            logger=log,
        )

    try:
        payload = ReconcileRequest.model_validate_json(body_bytes)
    except ValidationError as exc:
        return _audit_and_error(
            request_id=request_id,
            status=400,
            code="SCHEMA_INVALID",
            message=str(exc),
            key_prefix=key_prefix,
            method="POST",
            path=_PATH_RECONCILE,
            logger=log,
        )

    data: dict[str, Any] = {}
    records_ok = 0
    if "orders" in payload.domains and payload.orders is not None:
        order_data = reconcile_orders(
            sess,
            shop_id=payload.scope.shopId,
            sort_info=payload.orders.sortInfo,
            anchor_positions=payload.orders.anchorPositions,
            hot_window_size=payload.orders.hotWindowSize,
        )
        data["orders"] = order_data
        records_ok += int(order_data["serverTotal"])
    if "logistics" in payload.domains and payload.logistics is not None:
        logistics_data = reconcile_logistics(
            sess,
            shop_id=payload.scope.shopId,
            limit=payload.logistics.limit,
            cursor=payload.logistics.cursor,
        )
        data["logistics"] = logistics_data
        records_ok += len(logistics_data["items"])

    _log_event(
        level=logging.INFO,
        request_id=request_id,
        key_prefix=key_prefix,
        method="POST",
        path=_PATH_RECONCILE,
        status=200,
        records_in=len(payload.domains),
        records_ok=records_ok,
        logger=log,
    )
    return _ok_response(request_id=request_id, data=data)


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
            logger=log,
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
            logger=log,
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
            logger=log,
        )

    shop_id = payload.scope.shopId
    domain = payload.dump.domain
    endpoint = payload.dump.endpoint
    captured_at = payload.dump.createdAt
    response_body = payload.dump.response.body
    main_order_id = payload.dump.mainOrderId

    # AGENTS.md §2.5: response.body 为 None（插件抓取失败/超时）→ 422 + EMPTY_RESPONSE_BODY
    # 理由：empty body 是 TikTok 那边的问题（chrome-plugins 侧修复前），重试无意义 = PERMANENT
    if response_body is None:
        return _audit_and_error(
            request_id=request_id,
            status=422,
            code="EMPTY_RESPONSE_BODY",
            message="dump.response.body is null; plugin must not advance progress",
            key_prefix=key_prefix,
            method="POST",
            path=audit_path,
            logger=log,
        )

    # 解析 → 写业务表
    parse_error: str | None = None
    rows_written = 0

    try:
        # Isolate parser writes in a savepoint. A malformed child row must not
        # leave a partially materialized order/statement behind.
        with sess.begin_nested():
            if domain == "orders":
                rows_written = parse_order_response(
                    sess,
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
                        shop_id=shop_id,
                        response_body=response_body,
                        captured_at=captured_at,
                    )
                else:
                    rows_written = parse_statement_list_response(
                        sess,
                        shop_id=shop_id,
                        response_body=response_body,
                        captured_at=captured_at,
                    )
            elif domain == "after_sales":
                rows_written = parse_after_sales_response(
                    sess,
                    shop_id=shop_id,
                    response_body=response_body,
                    captured_at=captured_at,
                )
    except Exception as exc:
        parse_error = f"{type(exc).__name__}: {exc}"
        log.exception("parse error for domain=%s shop_id=%s", domain, shop_id)

    # Write per-domain health metric to plugin_logs (for diagnosis).
    # AGENTS.md §2.5: rowsWritten=0 不是 parse_error 探测信号——HTTP code 是唯一失败信号。
    # （原 hack `if parse_error is None and rows_written == 0 and domain in {...}` 删除：
    #  会漏判 logistics + after_sales 域静默返 200，导致 §5.3 物流 0 行额外根因。）
    record_dump_health(
        sess,
        shop_id=shop_id,
        domain=domain,
        endpoint=endpoint,
        rows_written=rows_written,
        parse_error_class=parse_error,
        captured_at=captured_at,
    )

    # commit
    sess.commit()

    if parse_error:
        # AGENTS.md §2.5: parse_error 返 422 PERMANENT
        # _audit_and_error 内部已处理 log_event + log.warning，此处不再重复记录
        return _audit_and_error(
            request_id=request_id,
            status=422,
            code="PARSE_ERROR",
            message=parse_error,
            key_prefix=key_prefix,
            method="POST",
            path=audit_path,
            logger=log,
        )

    _log_event(
        logger=log,
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
        data={},
    )

