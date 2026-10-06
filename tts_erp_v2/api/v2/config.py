"""config API — 枚举映射 CRUD。

GET    /v2/config/enum-map     — 一次性返回全量映射（readonly）
PUT    /v2/config/enum-map     — 新增/更新单条映射（admin）
DELETE /v2/config/enum-map/{id} — 删除单条映射（admin）

详见 docs/design/spu-roi-enum-translation-plan.md。
"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from hashlib import sha256
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from tts_erp_v2.api.deps import get_session, require_role_at_least
from tts_erp_v2.db.models.config import (
    EnumMap,
    RuntimeConfigItem,
    RuntimeConfigRevision,
    RuntimeConfigSecret,
)
from tts_erp_v2.runtime_config.repository import (
    RetiredSecretError,
    locked_item,
    publish,
    published_revision,
    secret_is_referenced_by_active_config,
    upsert_secret,
)
from tts_erp_v2.runtime_config.resolver import select_payload
from tts_erp_v2.runtime_config.validation import (
    ConfigValidationError,
    infer_schema,
    validate_payload,
    validate_rollout,
    validate_schema,
    validate_spu_deterioration_alert_runtime_mutation,
)

router = APIRouter(prefix="/v2/config", tags=["config"])


# ── response / request schemas ──────────────────────────────────────


class EnumMapUpsert(BaseModel):
    """PUT body: 新增或更新一条枚举映射。"""

    enum_type: str = Field(..., min_length=1, max_length=64)
    enum_value: str = Field(..., min_length=1, max_length=128)
    label_zh: str = Field(..., min_length=1, max_length=256)
    sort_order: int = Field(default=0, ge=0)


class EnumMapOut(BaseModel):
    """单条映射（用于 list/detail）。"""

    id: int
    enum_type: str
    enum_value: str
    label_zh: str
    sort_order: int


# ── GET /v2/config/enum-map — 一次性返回全量 ──────────────────────


@router.get("/enum-map")
def get_enum_map(sess: Session = Depends(get_session)) -> dict[str, dict[str, str]]:
    """返回所有枚举映射，按 enum_type 分组。

    返回格式：
    {
      "order_status": {"AWAITING_SHIPMENT": "待发货", ...},
      "case_type": {"RETURN_AND_REFUND": "退货退款", ...},
      ...
    }
    """
    rows = (
        sess.execute(
            select(EnumMap).order_by(EnumMap.enum_type, EnumMap.sort_order, EnumMap.id)
        )
        .scalars()
        .all()
    )

    result: dict[str, dict[str, str]] = {}
    for r in rows:
        if r.enum_type not in result:
            result[r.enum_type] = {}
        result[r.enum_type][r.enum_value] = r.label_zh
    return result


# ── GET /v2/config/enum-map/list — 带 id 的完整列表（管理页面用）──


@router.get("/enum-map/list")
def list_enum_map(sess: Session = Depends(get_session)) -> list[dict]:
    """返回所有枚举映射（带 id），管理页面 CRUD 用。"""
    rows = (
        sess.execute(
            select(EnumMap).order_by(EnumMap.enum_type, EnumMap.sort_order, EnumMap.id)
        )
        .scalars()
        .all()
    )
    return [
        {
            "id": r.id,
            "enum_type": r.enum_type,
            "enum_value": r.enum_value,
            "label_zh": r.label_zh,
            "sort_order": r.sort_order,
        }
        for r in rows
    ]


# ── PUT /v2/config/enum-map — 新增/更新单条 ────────────────────────


@router.put("/enum-map")
def upsert_enum_map(
    body: EnumMapUpsert,
    request: Request,
    sess: Session = Depends(get_session),
) -> dict:
    """新增或更新一条枚举映射。admin 角色。"""
    require_role_at_least(request, "admin")

    existing = sess.execute(
        select(EnumMap).where(
            EnumMap.enum_type == body.enum_type,
            EnumMap.enum_value == body.enum_value,
        )
    ).scalar_one_or_none()

    if existing:
        existing.label_zh = body.label_zh
        existing.sort_order = body.sort_order
        sess.flush()
        sess.commit()
        return {"id": existing.id, "action": "updated"}

    new_row = EnumMap(
        enum_type=body.enum_type,
        enum_value=body.enum_value,
        label_zh=body.label_zh,
        sort_order=body.sort_order,
    )
    sess.add(new_row)
    sess.flush()
    sess.commit()
    return {"id": new_row.id, "action": "created"}


# ── DELETE /v2/config/enum-map/{id} ─────────────────────────────────


@router.delete("/enum-map/{id}")
def delete_enum_map(
    id: int,
    request: Request,
    sess: Session = Depends(get_session),
) -> dict:
    """删除一条枚举映射。admin 角色。"""
    require_role_at_least(request, "admin")

    row = sess.get(EnumMap, id)
    if not row:
        raise HTTPException(status_code=404, detail="enum_map entry not found")
    sess.delete(row)
    sess.flush()
    sess.commit()
    return {"deleted": id}


# ── Versioned runtime configuration ─────────────────────────────────


class _RuntimeWireModel(BaseModel):
    model_config = ConfigDict(populate_by_name=True)


class RuntimeConfigCreate(_RuntimeWireModel):
    config_key: str = Field(alias="configKey", min_length=1, max_length=128, pattern=r"^[a-z][a-z0-9_.-]*$")
    display_name: str = Field(alias="displayName", min_length=1, max_length=256)
    json_schema: dict[str, Any] | None = Field(default=None, alias="jsonSchema")
    draft_payload: dict[str, Any] | None = Field(default=None, alias="draftPayload")
    draft_rollout: list[dict[str, Any]] = Field(default_factory=list, alias="draftRollout")


class RuntimeSchemaPreview(_RuntimeWireModel):
    payload: dict[str, Any]


class RuntimeDraftUpdate(_RuntimeWireModel):
    expected_draft_version: int = Field(alias="expectedDraftVersion", ge=0)
    payload: dict[str, Any]
    rollout: list[dict[str, Any]] = Field(default_factory=list)


class RuntimePublish(_RuntimeWireModel):
    expected_draft_version: int = Field(alias="expectedDraftVersion", ge=0)
    comment: str | None = Field(default=None, max_length=2_000)


class RuntimeRollback(_RuntimeWireModel):
    expected_draft_version: int = Field(alias="expectedDraftVersion", ge=0)
    target_version: int = Field(alias="targetVersion", ge=1)
    comment: str | None = Field(default=None, max_length=2_000)


class RuntimeSecretUpsert(_RuntimeWireModel):
    value: str = Field(min_length=1, max_length=16_384)


def _runtime_actor(request: Request) -> str:
    key_hash = request.scope.get("api_key_hash")
    if isinstance(key_hash, str) and key_hash:
        return f"api_key:{key_hash[:16]}"
    return "browser-session"


def _runtime_item_or_404(sess: Session, config_key: str) -> RuntimeConfigItem:
    item = sess.get(RuntimeConfigItem, config_key)
    if item is None:
        raise HTTPException(status_code=404, detail="runtime config not found")
    return item


def _active_runtime_item_or_409(sess: Session, config_key: str) -> RuntimeConfigItem:
    item = locked_item(sess, config_key)
    if item is None:
        raise HTTPException(status_code=404, detail="runtime config not found")
    if item.retired_at is not None:
        raise HTTPException(status_code=409, detail="runtime config is retired; restore it first")
    return item


def _validate_secret_name(name: str) -> None:
    if not name or len(name) > 128 or any(
        char not in "abcdefghijklmnopqrstuvwxyz0123456789_.-" for char in name
    ):
        raise HTTPException(
            status_code=422,
            detail="secret name must use lowercase letters, digits, dot, dash, or underscore",
        )


def _published_out(sess: Session, item: RuntimeConfigItem) -> dict[str, Any]:
    revision = published_revision(sess, item)
    return {
        "configKey": item.config_key,
        "displayName": item.display_name,
        "publishedVersion": item.published_version,
        "hasDraft": item.draft_payload is not None,
        "updatedAt": item.updated_at.isoformat(),
        "publishedAt": revision.created_at.isoformat() if revision else None,
        "retiredAt": item.retired_at.isoformat() if item.retired_at else None,
    }


def _detail_out(sess: Session, item: RuntimeConfigItem) -> dict[str, Any]:
    revision = published_revision(sess, item)
    return {
        **_published_out(sess, item),
        "jsonSchema": item.json_schema,
        "draftPayload": item.draft_payload,
        "draftRollout": item.draft_rollout,
        "draftVersion": item.draft_version,
        "publishedPayload": revision.payload if revision else None,
        "publishedRollout": revision.rollout if revision else [],
    }


def _validation_error(exc: ConfigValidationError | KeyError | ValueError) -> HTTPException:
    return HTTPException(status_code=422, detail=str(exc))


@router.get("/runtime/items")
def list_runtime_config_items(
    request: Request,
    include_retired: bool = Query(default=False, alias="includeRetired"),
    sess: Session = Depends(get_session),
) -> dict[str, list[dict[str, Any]]]:
    """List runtime keys; retired records are operator-only and opt-in."""
    if include_retired:
        require_role_at_least(request, "readwrite")
    statement = select(RuntimeConfigItem).order_by(RuntimeConfigItem.config_key)
    if not include_retired:
        statement = statement.where(RuntimeConfigItem.retired_at.is_(None))
    items = sess.execute(statement).scalars()
    return {"items": [_published_out(sess, item) for item in items]}


@router.post("/runtime/schema/preview")
def preview_runtime_config_schema(
    body: RuntimeSchemaPreview,
    request: Request,
) -> dict[str, dict[str, Any]]:
    """Infer the same permissive schema used when an item is created without one."""
    require_role_at_least(request, "readwrite")
    return {"jsonSchema": infer_schema(body.payload)}


@router.post("/runtime/items", status_code=201)
def create_runtime_config_item(
    body: RuntimeConfigCreate,
    request: Request,
    sess: Session = Depends(get_session),
) -> dict[str, Any]:
    require_role_at_least(request, "readwrite")
    existing = sess.get(RuntimeConfigItem, body.config_key)
    if existing is not None:
        detail = (
            "runtime config is retired; restore it instead"
            if existing.retired_at is not None
            else "runtime config key already exists"
        )
        raise HTTPException(status_code=409, detail=detail)
    try:
        if body.json_schema is None:
            if body.draft_payload is None:
                raise ConfigValidationError("draftPayload is required when jsonSchema is omitted")
            json_schema = infer_schema(body.draft_payload)
        else:
            json_schema = body.json_schema
        validate_schema(json_schema)
        if body.draft_payload is not None:
            validate_payload(body.draft_payload, json_schema)
            body.draft_rollout = validate_rollout(body.draft_rollout, json_schema)
        elif body.draft_rollout:
            raise ConfigValidationError("draftRollout requires draftPayload")
        validate_spu_deterioration_alert_runtime_mutation(
            body.config_key, payload=body.draft_payload, draft_rollout=body.draft_rollout
        )
    except ConfigValidationError as exc:
        raise _validation_error(exc) from exc
    item = RuntimeConfigItem(
        config_key=body.config_key,
        display_name=body.display_name,
        json_schema=json_schema,
        draft_payload=body.draft_payload,
        draft_rollout=body.draft_rollout,
        draft_version=1 if body.draft_payload is not None else 0,
    )
    sess.add(item)
    try:
        sess.commit()
    except IntegrityError as exc:
        sess.rollback()
        raise HTTPException(status_code=409, detail="runtime config key already exists") from exc
    return _detail_out(sess, item)


@router.get("/runtime/items/{config_key}")
def get_runtime_config_item(
    config_key: str,
    request: Request,
    sess: Session = Depends(get_session),
) -> dict[str, Any]:
    require_role_at_least(request, "readwrite")
    return _detail_out(sess, _runtime_item_or_404(sess, config_key))


@router.put("/runtime/items/{config_key}/draft")
def save_runtime_config_draft(
    config_key: str,
    body: RuntimeDraftUpdate,
    request: Request,
    sess: Session = Depends(get_session),
) -> dict[str, Any]:
    require_role_at_least(request, "readwrite")
    item = _active_runtime_item_or_409(sess, config_key)
    if body.expected_draft_version != item.draft_version:
        raise HTTPException(status_code=409, detail="draft version conflict; reload before saving")
    try:
        validate_payload(body.payload, item.json_schema)
        rollout = validate_rollout(body.rollout, item.json_schema)
        validate_spu_deterioration_alert_runtime_mutation(
            config_key, payload=body.payload, rollout=rollout
        )
    except ConfigValidationError as exc:
        raise _validation_error(exc) from exc
    item.draft_payload = body.payload
    item.draft_rollout = rollout
    item.draft_version += 1
    sess.commit()
    return _detail_out(sess, item)


@router.post("/runtime/items/{config_key}/publish")
def publish_runtime_config_item(
    config_key: str,
    body: RuntimePublish,
    request: Request,
    sess: Session = Depends(get_session),
) -> dict[str, Any]:
    require_role_at_least(request, "readwrite")
    item = _active_runtime_item_or_409(sess, config_key)
    if body.expected_draft_version != item.draft_version:
        raise HTTPException(status_code=409, detail="draft version conflict; reload before publishing")
    if item.draft_payload is None:
        raise HTTPException(status_code=422, detail="a draft payload is required before publishing")
    try:
        revision = publish(
            sess,
            item=item,
            payload=item.draft_payload,
            rollout=item.draft_rollout,
            comment=body.comment,
            actor=_runtime_actor(request),
        )
    except (ConfigValidationError, KeyError) as exc:
        raise _validation_error(exc) from exc
    sess.commit()
    return {"configKey": config_key, "publishedVersion": revision.version}


@router.get("/runtime/items/{config_key}/revisions")
def list_runtime_config_revisions(
    config_key: str,
    request: Request,
    sess: Session = Depends(get_session),
) -> dict[str, list[dict[str, Any]]]:
    require_role_at_least(request, "readwrite")
    _runtime_item_or_404(sess, config_key)
    rows = sess.execute(
        select(RuntimeConfigRevision)
        .where(RuntimeConfigRevision.config_key == config_key)
        .order_by(RuntimeConfigRevision.version.desc())
    ).scalars()
    return {
        "items": [
            {
                "version": row.version,
                "payload": row.payload,
                "rollout": row.rollout,
                "comment": row.comment,
                "createdBy": row.created_by,
                "createdAt": row.created_at.isoformat(),
            }
            for row in rows
        ]
    }


@router.post("/runtime/items/{config_key}/rollback")
def rollback_runtime_config_item(
    config_key: str,
    body: RuntimeRollback,
    request: Request,
    sess: Session = Depends(get_session),
) -> dict[str, Any]:
    """Republish a historical revision as a higher immutable version."""
    require_role_at_least(request, "readwrite")
    item = _active_runtime_item_or_409(sess, config_key)
    if body.expected_draft_version != item.draft_version:
        raise HTTPException(status_code=409, detail="draft version conflict; reload before rolling back")
    target = sess.execute(
        select(RuntimeConfigRevision).where(
            RuntimeConfigRevision.config_key == config_key,
            RuntimeConfigRevision.version == body.target_version,
        )
    ).scalar_one_or_none()
    if target is None:
        raise HTTPException(status_code=404, detail="runtime config revision not found")
    try:
        revision = publish(
            sess,
            item=item,
            payload=target.payload,
            rollout=target.rollout,
            comment=body.comment or f"rollback to version {target.version}",
            actor=_runtime_actor(request),
        )
    except (ConfigValidationError, KeyError) as exc:
        raise _validation_error(exc) from exc
    sess.commit()
    return {"configKey": config_key, "publishedVersion": revision.version, "rolledBackFrom": target.version}


@router.post("/runtime/items/{config_key}/retire")
def retire_runtime_config_item(
    config_key: str,
    request: Request,
    sess: Session = Depends(get_session),
) -> dict[str, str]:
    """Retire a key without deleting its immutable audit history."""
    require_role_at_least(request, "readwrite")
    item = locked_item(sess, config_key)
    if item is None:
        raise HTTPException(status_code=404, detail="runtime config not found")
    if item.retired_at is None:
        item.retired_at = datetime.now(UTC)
        item.retired_by = _runtime_actor(request)
        sess.commit()
    return {"configKey": item.config_key, "status": "retired"}


@router.post("/runtime/items/{config_key}/restore")
def restore_runtime_config_item(
    config_key: str,
    request: Request,
    sess: Session = Depends(get_session),
) -> dict[str, str]:
    require_role_at_least(request, "readwrite")
    item = locked_item(sess, config_key)
    if item is None:
        raise HTTPException(status_code=404, detail="runtime config not found")
    item.retired_at = None
    item.retired_by = None
    sess.commit()
    return {"configKey": item.config_key, "status": "active"}


@router.get("/runtime/snapshot")
def runtime_config_snapshot(
    request: Request,
    subject: str | None = Query(default=None, max_length=256),
    sess: Session = Depends(get_session),
) -> Response:
    """Return published configs with deterministic rollout selection and ETag caching.

    Secret references remain references here; only in-process resolver callers
    may decrypt values.
    """
    salt = os.environ.get("TTS_ERP_RUNTIME_CONFIG_SALT", "tts-erp-runtime-config-v1")
    items = sess.execute(
        select(RuntimeConfigItem)
        .where(
            RuntimeConfigItem.published_version.is_not(None),
            RuntimeConfigItem.retired_at.is_(None),
        )
        .order_by(RuntimeConfigItem.config_key)
    ).scalars()
    values: dict[str, dict[str, Any]] = {}
    versions: dict[str, int] = {}
    for item in items:
        revision = published_revision(sess, item)
        assert revision is not None
        payload, rule_index = select_payload(
            revision.payload,
            revision.rollout,
            rollout_salt=salt,
            config_key=item.config_key,
            subject=subject,
        )
        values[item.config_key] = {"version": revision.version, "payload": payload, "rolloutRuleIndex": rule_index}
        versions[item.config_key] = revision.version
    etag_material = {"subject": subject, "versions": versions}
    etag = '"' + sha256(json.dumps(etag_material, sort_keys=True).encode()).hexdigest() + '"'
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304, headers={"ETag": etag})
    return JSONResponse(
        content={"items": values, "subject": subject},
        headers={"ETag": etag, "Cache-Control": "private, max-age=60"},
    )


@router.get("/runtime/secrets")
def list_runtime_config_secrets(
    request: Request,
    include_retired: bool = Query(default=False, alias="includeRetired"),
    sess: Session = Depends(get_session),
) -> dict[str, list[dict[str, Any]]]:
    """List secret metadata; encrypted values are never returned."""
    require_role_at_least(request, "readwrite")
    statement = select(RuntimeConfigSecret).order_by(RuntimeConfigSecret.name)
    if not include_retired:
        statement = statement.where(RuntimeConfigSecret.retired_at.is_(None))
    rows = sess.execute(statement).scalars()
    return {
        "items": [
            {
                "name": row.name,
                "ref": f"secret://{row.name}",
                "fingerprint": row.fingerprint,
                "updatedAt": row.updated_at.isoformat(),
                "retiredAt": row.retired_at.isoformat() if row.retired_at else None,
            }
            for row in rows
        ]
    }


@router.put("/runtime/secrets/{name}")
def save_runtime_config_secret(
    name: str,
    body: RuntimeSecretUpsert,
    request: Request,
    sess: Session = Depends(get_session),
) -> dict[str, str]:
    require_role_at_least(request, "readwrite")
    _validate_secret_name(name)
    try:
        row = upsert_secret(sess, name=name, value=body.value)
    except RetiredSecretError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    sess.commit()
    return {"name": row.name, "ref": f"secret://{row.name}", "fingerprint": row.fingerprint}


@router.post("/runtime/secrets/{name}/retire")
def retire_runtime_config_secret(
    name: str,
    request: Request,
    sess: Session = Depends(get_session),
) -> dict[str, str]:
    """Retire an unreferenced secret; history remains encrypted and intact."""
    require_role_at_least(request, "readwrite")
    _validate_secret_name(name)
    row = sess.execute(
        select(RuntimeConfigSecret)
        .where(RuntimeConfigSecret.name == name)
        .with_for_update()
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="runtime config secret not found")
    if row.retired_at is None and secret_is_referenced_by_active_config(sess, name):
        raise HTTPException(status_code=409, detail="secret is referenced by an active config or draft")
    if row.retired_at is None:
        row.retired_at = datetime.now(UTC)
        row.retired_by = _runtime_actor(request)
        sess.commit()
    return {"name": row.name, "status": "retired"}


@router.post("/runtime/secrets/{name}/restore")
def restore_runtime_config_secret(
    name: str,
    request: Request,
    sess: Session = Depends(get_session),
) -> dict[str, str]:
    require_role_at_least(request, "readwrite")
    _validate_secret_name(name)
    row = sess.execute(
        select(RuntimeConfigSecret)
        .where(RuntimeConfigSecret.name == name)
        .with_for_update()
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="runtime config secret not found")
    row.retired_at = None
    row.retired_by = None
    sess.commit()
    return {"name": row.name, "status": "active"}
