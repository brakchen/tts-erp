"""config API — 枚举映射 CRUD。

GET    /v2/config/enum-map     — 一次性返回全量映射（readonly）
PUT    /v2/config/enum-map     — 新增/更新单条映射（admin）
DELETE /v2/config/enum-map/{id} — 删除单条映射（admin）

详见 tech-doc/spu-roi-enum-translation-plan.md。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from tts_erp_v2.api.deps import get_session, require_role_at_least
from tts_erp_v2.db.models.config import EnumMap

router = APIRouter(prefix="/v2/config", tags=["config"])


# ── response / request schemas ──────────────────────────────────────


class EnumMapUpsert(BaseModel):
    """PUT body: 新增或更新一条枚举映射。"""

    enum_type: str = Field(..., max_length=64)
    enum_value: str = Field(..., max_length=128)
    label_zh: str = Field(..., max_length=256)
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
    rows = sess.execute(
        select(EnumMap).order_by(EnumMap.enum_type, EnumMap.sort_order, EnumMap.id)
    ).scalars().all()

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
    rows = sess.execute(
        select(EnumMap).order_by(EnumMap.enum_type, EnumMap.sort_order, EnumMap.id)
    ).scalars().all()
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
        return {"id": existing.id, "action": "updated"}

    new_row = EnumMap(
        enum_type=body.enum_type,
        enum_value=body.enum_value,
        label_zh=body.label_zh,
        sort_order=body.sort_order,
    )
    sess.add(new_row)
    sess.flush()
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
    return {"deleted": id}
