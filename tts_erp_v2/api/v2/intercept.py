"""/v2/intercept/* — 请求拦截配置管理与数据同步。

Chrome 扩展 HTTP 请求拦截配置管理 + 数据接收。

端点分类：
- 配置管理 (readwrite): CRUD + 批量操作
- 配置下发 (readonly): 插件拉取配置
- 数据接收 (readwrite): 插件上传拦截数据
- 数据查询 (readonly): 查询拦截记录
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import sys
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func, text
from sqlalchemy.orm import Session

from tts_erp_v2.api.deps import get_session

# ─── Config ───────────────────────────────────────────────────────────

PROTOCOL_VERSION = 1
SUPPORTED_PROTOCOL_VERSIONS = {1}

# ─── Logger ──────────────────────────────────────────────────────────

log = logging.getLogger("tts_erp_v2.intercept")
log.setLevel(logging.INFO)
if not any(
    isinstance(h, logging.StreamHandler) and h.stream is sys.stdout
    for h in log.handlers
):
    _handler = logging.StreamHandler(sys.stdout)
    _handler.setFormatter(logging.Formatter("%(message)s"))
    log.addHandler(_handler)


# ─── Pydantic Models ─────────────────────────────────────────────────


class InterceptConfigIn(BaseModel):
    """创建/更新配置的请求体"""

    domain: str = Field(min_length=1, max_length=255)
    endpoint: str = Field(min_length=1, max_length=512)
    capture_headers: bool = True
    capture_body: bool = True
    description: str | None = None
    tags: list[str] = []
    enabled: bool = True


class InterceptConfigToggle(BaseModel):
    """启停用配置的请求体"""

    enabled: bool


class InterceptConfigBatch(BaseModel):
    """批量操作的请求体"""

    action: str = Field(pattern=r"^(enable|disable|delete)$")
    ids: list[int] = Field(min_length=1)


class InterceptConfigImport(BaseModel):
    """批量导入的请求体"""

    configs: list[InterceptConfigIn] = Field(min_length=1)


class SessionIn(BaseModel):
    """会话信息"""

    session_id: str = Field(min_length=1, max_length=128)
    tab_id: int | None = None
    tab_url: str | None = None
    started_at: datetime


class InterceptedRequestIn(BaseModel):
    """拦截的请求记录"""

    request_id: str = Field(min_length=1, max_length=128)
    trace_id: str | None = None
    session_id: str = Field(min_length=1, max_length=128)
    method: str = Field(min_length=1, max_length=16)
    url: str = Field(min_length=1, max_length=2048)
    endpoint_path: str = Field(min_length=1, max_length=512)
    endpoint_host: str = Field(min_length=1, max_length=255)
    is_whitelisted: bool
    matched_config_id: int | None = None
    request_headers: dict[str, Any] | None = None
    request_body: dict[str, Any] | None = None
    response_status: int | None = None
    response_status_text: str | None = None
    response_headers: dict[str, Any] | None = None
    response_body: dict[str, Any] | None = None
    duration_ms: int | None = None
    error_type: str | None = None
    error_message: str | None = None
    seller_id: str | None = None
    advertiser_id: str | None = None
    business_context: dict[str, Any] | None = None
    pagination: dict[str, Any] | None = None
    captured_at: datetime


class InterceptSyncRequest(BaseModel):
    """数据同步请求"""

    protocol_version: int = Field(alias="protocolVersion")
    request_id: str | None = Field(None, alias="requestId")
    scope: dict[str, str]
    session: SessionIn
    requests: list[InterceptedRequestIn]

    class Config:
        populate_by_name = True


# ─── Helpers ─────────────────────────────────────────────────────────


def _serialize_datetime(obj: Any) -> Any:
    """序列化 datetime 对象为 ISO 格式字符串"""
    if isinstance(obj, datetime):
        return obj.isoformat()
    return obj


def _serialize_config(config: dict) -> dict:
    """序列化配置对象"""
    return {
        k: _serialize_datetime(v)
        for k, v in config.items()
    }


def _compute_url_hash(url: str) -> str:
    """计算 URL 的 SHA-256 哈希"""
    return hashlib.sha256(url.encode("utf-8")).hexdigest()


def _match_endpoint_pattern(path: str, pattern: str) -> bool:
    """匹配 endpoint 模式（支持 * 通配符）"""
    if "*" not in pattern:
        return path == pattern

    # 将模式转换为正则表达式
    escaped = re.escape(pattern)
    regex_pattern = escaped.replace(r"\*", "[^/]*")
    regex_pattern = f"^{regex_pattern}(/.*)?$"

    return bool(re.match(regex_pattern, path))


def _endpoint_specificity(endpoint: str) -> int:
    """计算 endpoint 的具体程度（路径段数）"""
    return len([s for s in endpoint.split("/") if s])


def _sanitize_message(message: Any) -> str:
    """清理消息文本"""
    return " ".join(str(message).split())[:500]


# ─── Router ──────────────────────────────────────────────────────────

router = APIRouter(prefix="/v2/intercept", tags=["intercept"])


# ─── Config Management (readwrite) ───────────────────────────────────


@router.get("/configs/export")
def export_configs(
    request: Request,
    enabled: bool | None = None,
    domain: str | None = None,
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_session),
) -> Response:
    """查询配置列表"""
    query = "SELECT * FROM plugin.intercept_configs WHERE 1=1"
    params: dict[str, Any] = {"limit": limit, "offset": offset}

    if enabled is not None:
        query += " AND enabled = :enabled"
        params["enabled"] = enabled

    if domain:
        query += " AND domain = :domain"
        params["domain"] = domain

    query += " ORDER BY id DESC LIMIT :limit OFFSET :offset"

    result = db.execute(text(query), params)
    configs = [dict(row._mapping) for row in result]

    # 获取总数
    count_query = "SELECT COUNT(*) FROM plugin.intercept_configs WHERE 1=1"
    count_params: dict[str, Any] = {}
    if enabled is not None:
        count_query += " AND enabled = :enabled"
        count_params["enabled"] = enabled
    if domain:
        count_query += " AND domain = :domain"
        count_params["domain"] = domain

    total = db.execute(text(count_query), count_params).scalar()

    # 转换 JSON 字段和序列化 datetime
    for config in configs:
        if config.get("tags") and isinstance(config["tags"], str):
            try:
                config["tags"] = json.loads(config["tags"])
            except json.JSONDecodeError:
                config["tags"] = []

    return JSONResponse(
        content={"configs": [_serialize_config(c) for c in configs], "total": total}
    )


@router.get("/configs")
def list_configs(
    config_id: int,
    db: Session = Depends(get_session),
) -> Response:
    """查询单个配置"""
    result = db.execute(
        text("SELECT * FROM plugin.intercept_configs WHERE id = :id"),
        {"id": config_id},
    )
    row = result.first()
    if not row:
        raise HTTPException(status_code=404, detail="Config not found")

    config = dict(row._mapping)
    if config.get("tags") and isinstance(config["tags"], str):
        try:
            config["tags"] = json.loads(config["tags"])
        except json.JSONDecodeError:
            config["tags"] = []

    return JSONResponse(content={"config": _serialize_config(config)})


@router.post("/configs", status_code=status.HTTP_201_CREATED)
def create_config(
    body: InterceptConfigIn,
    db: Session = Depends(get_session),
) -> Response:
    """创建配置"""
    # 检查唯一约束
    existing = db.execute(
        text(
            "SELECT id FROM plugin.intercept_configs WHERE domain = :domain AND endpoint = :endpoint"
        ),
        {"domain": body.domain, "endpoint": body.endpoint},
    ).first()
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Config with this domain and endpoint already exists",
        )

    result = db.execute(
        text(
            """
            INSERT INTO plugin.intercept_configs (domain, endpoint, capture_headers, capture_body, description, tags, enabled)
            VALUES (:domain, :endpoint, :capture_headers, :capture_body, :description, :tags, :enabled)
            RETURNING *
            """
        ),
        {
            "domain": body.domain,
            "endpoint": body.endpoint,
            "capture_headers": body.capture_headers,
            "capture_body": body.capture_body,
            "description": body.description,
            "tags": json.dumps(body.tags),
            "enabled": body.enabled,
        },
    )
    db.commit()

    config = dict(result.first()._mapping)
    if config.get("tags") and isinstance(config["tags"], str):
        try:
            config["tags"] = json.loads(config["tags"])
        except json.JSONDecodeError:
            config["tags"] = []

    log.info(f"Created intercept config: {config['id']}")
    return JSONResponse(content={"config": _serialize_config(config)}, status_code=status.HTTP_201_CREATED)


@router.put("/configs/{config_id}")
def update_config(
    config_id: int,
    body: InterceptConfigIn,
    db: Session = Depends(get_session),
) -> Response:
    """更新配置"""
    # 检查配置是否存在
    existing = db.execute(
        text("SELECT id FROM plugin.intercept_configs WHERE id = :id"),
        {"id": config_id},
    ).first()
    if not existing:
        raise HTTPException(status_code=404, detail="Config not found")

    # 检查唯一约束（排除自身）
    duplicate = db.execute(
        text(
            "SELECT id FROM plugin.intercept_configs WHERE domain = :domain AND endpoint = :endpoint AND id != :id"
        ),
        {"domain": body.domain, "endpoint": body.endpoint, "id": config_id},
    ).first()
    if duplicate:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Config with this domain and endpoint already exists",
        )

    result = db.execute(
        text(
            """
            UPDATE plugin.intercept_configs
            SET domain = :domain, endpoint = :endpoint, capture_headers = :capture_headers,
                capture_body = :capture_body, description = :description, tags = :tags, enabled = :enabled
            WHERE id = :id
            RETURNING *
            """
        ),
        {
            "id": config_id,
            "domain": body.domain,
            "endpoint": body.endpoint,
            "capture_headers": body.capture_headers,
            "capture_body": body.capture_body,
            "description": body.description,
            "tags": json.dumps(body.tags),
            "enabled": body.enabled,
        },
    )
    db.commit()

    config = dict(result.first()._mapping)
    if config.get("tags") and isinstance(config["tags"], str):
        try:
            config["tags"] = json.loads(config["tags"])
        except json.JSONDecodeError:
            config["tags"] = []

    log.info(f"Updated intercept config: {config_id}")
    return JSONResponse(content={"config": _serialize_config(config)})


@router.delete("/configs/{config_id}")
def delete_config(
    config_id: int,
    db: Session = Depends(get_session),
) -> Response:
    """删除配置"""
    result = db.execute(
        text("DELETE FROM plugin.intercept_configs WHERE id = :id RETURNING id"),
        {"id": config_id},
    )
    deleted = result.first()
    if not deleted:
        raise HTTPException(status_code=404, detail="Config not found")

    db.commit()
    log.info(f"Deleted intercept config: {config_id}")
    return JSONResponse(content={"success": True})


@router.patch("/configs/{config_id}/toggle")
def toggle_config(
    config_id: int,
    body: InterceptConfigToggle,
    db: Session = Depends(get_session),
) -> Response:
    """启停用配置"""
    result = db.execute(
        text(
            """
            UPDATE plugin.intercept_configs
            SET enabled = :enabled
            WHERE id = :id
            RETURNING *
            """
        ),
        {"id": config_id, "enabled": body.enabled},
    )
    row = result.first()
    if not row:
        raise HTTPException(status_code=404, detail="Config not found")

    db.commit()

    config = dict(row._mapping)
    if config.get("tags") and isinstance(config["tags"], str):
        try:
            config["tags"] = json.loads(config["tags"])
        except json.JSONDecodeError:
            config["tags"] = []

    log.info(f"Toggled intercept config {config_id} to enabled={body.enabled}")
    return JSONResponse(content={"config": _serialize_config(config)})


@router.post("/configs/batch")
def batch_configs(
    body: InterceptConfigBatch,
    db: Session = Depends(get_session),
) -> Response:
    """批量操作"""
    if body.action == "delete":
        result = db.execute(
            text(
                "DELETE FROM plugin.intercept_configs WHERE id = ANY(:ids) RETURNING id"
            ),
            {"ids": body.ids},
        )
        affected = len(result.fetchall())
    else:
        enabled = body.action == "enable"
        result = db.execute(
            text(
                """
                UPDATE plugin.intercept_configs
                SET enabled = :enabled
                WHERE id = ANY(:ids)
                RETURNING id
                """
            ),
            {"ids": body.ids, "enabled": enabled},
        )
        affected = len(result.fetchall())

    db.commit()
    log.info(f"Batch {body.action} on {affected} configs")
    return JSONResponse(content={"affected": affected})


@router.post("/configs/import")
def import_configs(
    body: InterceptConfigImport,
    db: Session = Depends(get_session),
) -> Response:
    """批量导入"""
    imported = 0
    skipped = 0
    errors: list[dict[str, Any]] = []

    for config in body.configs:
        try:
            # 检查是否已存在
            existing = db.execute(
                text(
                    "SELECT id FROM plugin.intercept_configs WHERE domain = :domain AND endpoint = :endpoint"
                ),
                {"domain": config.domain, "endpoint": config.endpoint},
            ).first()

            if existing:
                skipped += 1
                continue

            db.execute(
                text(
                    """
                    INSERT INTO plugin.intercept_configs (domain, endpoint, capture_headers, capture_body, description, tags, enabled)
                    VALUES (:domain, :endpoint, :capture_headers, :capture_body, :description, :tags, :enabled)
                    """
                ),
                {
                    "domain": config.domain,
                    "endpoint": config.endpoint,
                    "capture_headers": config.capture_headers,
                    "capture_body": config.capture_body,
                    "description": config.description,
                    "tags": json.dumps(config.tags),
                    "enabled": config.enabled,
                },
            )
            imported += 1
        except Exception as e:
            errors.append(
                {
                    "domain": config.domain,
                    "endpoint": config.endpoint,
                    "error": str(e),
                }
            )

    db.commit()
    log.info(f"Imported {imported} configs, skipped {skipped}, errors {len(errors)}")
    return JSONResponse(
        content={
            "imported": imported,
            "skipped": skipped,
            "errors": errors,
        }
    )


@router.get("/configs/{config_id}")
def get_config(
    db: Session = Depends(get_session),
) -> Response:
    """导出配置"""
    result = db.execute(
        text("SELECT * FROM plugin.intercept_configs ORDER BY id")
    )
    configs = [dict(row._mapping) for row in result]

    # 转换 JSON 字段
    for config in configs:
        if config.get("tags") and isinstance(config["tags"], str):
            try:
                config["tags"] = json.loads(config["tags"])
            except json.JSONDecodeError:
                config["tags"] = []

    return JSONResponse(
        content={"configs": [_serialize_config(c) for c in configs]},
        headers={
            "Content-Disposition": "attachment; filename=intercept_configs.json"
        },
    )


# ─── Config Distribution (readonly) ─────────────────────────────────


@router.get("/config")
def get_config_for_plugin(
    db: Session = Depends(get_session),
) -> Response:
    """配置下发（插件用）"""
    result = db.execute(
        text(
            "SELECT * FROM plugin.intercept_configs WHERE enabled = true ORDER BY id"
        )
    )
    configs = [dict(row._mapping) for row in result]

    # 转换 JSON 字段
    for config in configs:
        if config.get("tags") and isinstance(config["tags"], str):
            try:
                config["tags"] = json.loads(config["tags"])
            except json.JSONDecodeError:
                config["tags"] = []

    # 计算配置版本（基于配置内容的哈希）
    config_hash = hashlib.sha256(
        json.dumps(configs, sort_keys=True, default=str).encode()
    ).hexdigest()
    version = int(config_hash[:8], 16) % 1000000

    return JSONResponse(
        content={
            "version": version,
            "configs": [_serialize_config(c) for c in configs],
        }
    )


# ─── Data Sync (readwrite) ──────────────────────────────────────────


@router.post("/sync")
def sync_intercepted_requests(
    body: InterceptSyncRequest,
    db: Session = Depends(get_session),
) -> Response:
    """数据接收"""
    # 验证协议版本
    if body.protocol_version not in SUPPORTED_PROTOCOL_VERSIONS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unsupported protocol version: {body.protocol_version}",
        )

    # 更新或创建会话
    db.execute(
        text(
            """
            INSERT INTO plugin.intercept_sessions (session_id, tab_id, tab_url, started_at)
            VALUES (:session_id, :tab_id, :tab_url, :started_at)
            ON CONFLICT (session_id) DO UPDATE SET
                tab_id = COALESCE(EXCLUDED.tab_id, plugin.intercept_sessions.tab_id),
                tab_url = COALESCE(EXCLUDED.tab_url, plugin.intercept_sessions.tab_url),
                last_request_at = now()
            """
        ),
        {
            "session_id": body.session.session_id,
            "tab_id": body.session.tab_id,
            "tab_url": body.session.tab_url,
            "started_at": body.session.started_at,
        },
    )

    accepted = 0
    rejected = 0
    errors: list[dict[str, Any]] = []

    for req in body.requests:
        try:
            url_hash = _compute_url_hash(req.url)

            db.execute(
                text(
                    """
                    INSERT INTO plugin.intercepted_requests (
                        request_id, trace_id, session_id, method, url, url_hash,
                        endpoint_path, endpoint_host, is_whitelisted, matched_config_id,
                        request_headers, request_body, response_status, response_status_text,
                        response_headers, response_body, duration_ms, error_type, error_message,
                        seller_id, advertiser_id, business_context, pagination, captured_at
                    ) VALUES (
                        :request_id, :trace_id, :session_id, :method, :url, :url_hash,
                        :endpoint_path, :endpoint_host, :is_whitelisted, :matched_config_id,
                        :request_headers, :request_body, :response_status, :response_status_text,
                        :response_headers, :response_body, :duration_ms, :error_type, :error_message,
                        :seller_id, :advertiser_id, :business_context, :pagination, :captured_at
                    )
                    ON CONFLICT (request_id) DO NOTHING
                    """
                ),
                {
                    "request_id": req.request_id,
                    "trace_id": req.trace_id,
                    "session_id": req.session_id,
                    "method": req.method,
                    "url": req.url,
                    "url_hash": url_hash,
                    "endpoint_path": req.endpoint_path,
                    "endpoint_host": req.endpoint_host,
                    "is_whitelisted": req.is_whitelisted,
                    "matched_config_id": req.matched_config_id,
                    "request_headers": json.dumps(req.request_headers) if req.request_headers else None,
                    "request_body": json.dumps(req.request_body) if req.request_body else None,
                    "response_status": req.response_status,
                    "response_status_text": req.response_status_text,
                    "response_headers": json.dumps(req.response_headers) if req.response_headers else None,
                    "response_body": json.dumps(req.response_body) if req.response_body else None,
                    "duration_ms": req.duration_ms,
                    "error_type": req.error_type,
                    "error_message": req.error_message,
                    "seller_id": req.seller_id,
                    "advertiser_id": req.advertiser_id,
                    "business_context": json.dumps(req.business_context) if req.business_context else None,
                    "pagination": json.dumps(req.pagination) if req.pagination else None,
                    "captured_at": req.captured_at,
                },
            )
            accepted += 1
        except Exception as e:
            rejected += 1
            errors.append(
                {
                    "request_id": req.request_id,
                    "error": str(e),
                }
            )

    # 更新会话统计
    db.execute(
        text(
            """
            UPDATE plugin.intercept_sessions
            SET total_requests = total_requests + :total,
                whitelisted_requests = whitelisted_requests + :whitelisted,
                metadata_only_requests = metadata_only_requests + :metadata_only
            WHERE session_id = :session_id
            """
        ),
        {
            "session_id": body.session.session_id,
            "total": accepted,
            "whitelisted": sum(1 for r in body.requests if r.is_whitelisted),
            "metadata_only": sum(1 for r in body.requests if not r.is_whitelisted),
        },
    )

    # 更新同步游标
    if body.request_id:
        db.execute(
            text(
                """
                INSERT INTO plugin.intercept_sync_cursors (cursor_key, last_synced_at, total_synced)
                VALUES (:cursor_key, now(), :total_synced)
                ON CONFLICT (cursor_key) DO UPDATE SET
                    last_synced_at = now(),
                    total_synced = plugin.intercept_sync_cursors.total_synced + :total_synced,
                    updated_at = now()
                """
            ),
            {
                "cursor_key": body.request_id,
                "total_synced": accepted,
            },
        )

    db.commit()

    log.info(f"Sync completed: accepted={accepted}, rejected={rejected}")
    return JSONResponse(
        content={
            "accepted": accepted,
            "rejected": rejected,
            "errors": errors,
            "cursor": {
                "lastSyncedId": body.request_id,
                "syncedAt": datetime.now(UTC).isoformat(),
            },
        }
    )


# ─── Data Query (readonly) ──────────────────────────────────────────


@router.get("/requests/stats")
def get_requests_stats(
    request: Request,
    db: Session = Depends(get_session),
) -> Response:
    """统计信息"""
    # 总请求数
    total = db.execute(
        text("SELECT COUNT(*) FROM plugin.intercepted_requests")
    ).scalar()

    # 白名单请求数
    whitelisted = db.execute(
        text("SELECT COUNT(*) FROM plugin.intercepted_requests WHERE is_whitelisted = true")
    ).scalar()

    # 按域名分布
    by_host_result = db.execute(
        text(
            """
            SELECT endpoint_host, COUNT(*) as count
            FROM plugin.intercepted_requests
            GROUP BY endpoint_host
            ORDER BY count DESC
            LIMIT 10
            """
        )
    )
    by_host = {row.endpoint_host: row.count for row in by_host_result}

    # 按方法分布
    by_method_result = db.execute(
        text(
            """
            SELECT method, COUNT(*) as count
            FROM plugin.intercepted_requests
            GROUP BY method
            ORDER BY count DESC
            """
        )
    )
    by_method = {row.method: row.count for row in by_method_result}

    return JSONResponse(
        content={
            "total": total,
            "whitelisted": whitelisted,
            "byHost": by_host,
            "byMethod": by_method,
        }
    )


@router.get("/requests")
def list_requests(
    request: Request,
    seller_id: str | None = None,
    endpoint_host: str | None = None,
    endpoint_path: str | None = None,
    is_whitelisted: bool | None = None,
    method: str | None = None,
    status_code: int | None = None,
    from_date: datetime | None = Query(None, alias="from"),
    to_date: datetime | None = Query(None, alias="to"),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_session),
) -> Response:
    """查询拦截记录"""
    query = "SELECT * FROM plugin.intercepted_requests WHERE 1=1"
    params: dict[str, Any] = {"limit": limit, "offset": offset}

    if seller_id:
        query += " AND seller_id = :seller_id"
        params["seller_id"] = seller_id

    if endpoint_host:
        query += " AND endpoint_host = :endpoint_host"
        params["endpoint_host"] = endpoint_host

    if endpoint_path:
        query += " AND endpoint_path = :endpoint_path"
        params["endpoint_path"] = endpoint_path

    if is_whitelisted is not None:
        query += " AND is_whitelisted = :is_whitelisted"
        params["is_whitelisted"] = is_whitelisted

    if method:
        query += " AND method = :method"
        params["method"] = method

    if status_code is not None:
        query += " AND response_status = :status_code"
        params["status_code"] = status_code

    if from_date:
        query += " AND captured_at >= :from_date"
        params["from_date"] = from_date

    if to_date:
        query += " AND captured_at <= :to_date"
        params["to_date"] = to_date

    # 计算总数
    count_query = query.replace("SELECT *", "SELECT COUNT(*)")
    total = db.execute(text(count_query), params).scalar()

    query += " ORDER BY captured_at DESC LIMIT :limit OFFSET :offset"
    result = db.execute(text(query), params)
    requests = [dict(row._mapping) for row in result]

    # 转换 JSON 字段和 datetime
    for req in requests:
        for field in ["request_headers", "request_body", "response_headers", "response_body", "business_context", "pagination"]:
            if req.get(field) and isinstance(req[field], str):
                try:
                    req[field] = json.loads(req[field])
                except json.JSONDecodeError:
                    req[field] = None
        # 转换 datetime 为 ISO 格式字符串
        for field in ["captured_at", "received_at"]:
            if req.get(field) and isinstance(req[field], datetime):
                req[field] = req[field].isoformat()

    return JSONResponse(
        content={
            "requests": requests,
            "total": total,
            "pagination": {
                "limit": limit,
                "offset": offset,
                "hasMore": offset + limit < total,
            },
        }
    )


@router.get("/requests/{request_id}")
def get_request(
    request_id: str,
    db: Session = Depends(get_session),
) -> Response:
    """查询单条记录详情"""
    result = db.execute(
        text("SELECT * FROM plugin.intercepted_requests WHERE request_id = :request_id"),
        {"request_id": request_id},
    )
    row = result.first()
    if not row:
        raise HTTPException(status_code=404, detail="Request not found")

    req = dict(row._mapping)
    for field in ["request_headers", "request_body", "response_headers", "response_body", "business_context", "pagination"]:
        if req.get(field) and isinstance(req[field], str):
            try:
                req[field] = json.loads(req[field])
            except json.JSONDecodeError:
                req[field] = None

    # 转换 datetime 为 ISO 格式字符串
    for field in ["captured_at", "received_at"]:
        if req.get(field) and isinstance(req[field], datetime):
            req[field] = req[field].isoformat()

    return JSONResponse(content={"request": req})
