"""Bounded XLSX upload and atomic purchase-price history import."""

from __future__ import annotations

from decimal import Decimal
from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Query, Request
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from tts_erp_v2.api.deps import SessionDep, require_cookie_csrf, require_role_at_least
from tts_erp_v2.reporting.purchase_price_import import (
    MAX_FILE_BYTES,
    CostImportError,
    parse_cost_file,
    preview_cost_import,
)
from tts_erp_v2.reporting.purchase_price_lock import lock_purchase_price_spu
from tts_erp_v2.reporting.purchase_prices import append_purchase_price

router = APIRouter(prefix="/import")
UPLOAD_SCHEMA = {"requestBody": {"required": True, "content": {
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": {
        "schema": {"type": "string", "format": "binary"}
    }
}}}


async def read_upload(request: Request) -> bytes:
    require_role_at_least(request, "readwrite")
    require_cookie_csrf(request)
    content = bytearray()
    async for chunk in request.stream():
        if len(content) + len(chunk) > MAX_FILE_BYTES:
            raise HTTPException(413, "文件不能超过 8 MB")
        content.extend(chunk)
    return bytes(content)


def process_import(
    session: Session, content: bytes, created_by: str, fingerprint: str | None = None,
    *, write: bool = False,
) -> dict[str, Any]:
    """All synchronous parsing/database work runs off the event loop."""
    try:
        parsed = parse_cost_file(content)
        preview = preview_cost_import(session, content, parsed)
        if not write and fingerprint is None:
            return preview
        # Match and current costs are re-read after taking all shared writer locks.
        locked = {item["spuPk"] for item in preview["items"]}
        for item in preview["items"]:
            lock_purchase_price_spu(session, spu_pk=int(item["spuPk"]))
        preview = preview_cost_import(session, content, parsed)
        if any(item["spuPk"] not in locked for item in preview["items"]):
            raise HTTPException(409, "商品匹配已变化，请重新上传文件")
        if fingerprint is not None and preview["fingerprint"] != fingerprint:
            raise HTTPException(409, "文件、商品匹配或当前采购价已变化，请重新预览后确认")
        if fingerprint is not None and not preview["items"]:
            raise HTTPException(422, "没有可以导入的商品")
        for item in preview["items"]:
            append_purchase_price(
                session,
                spu_pk=int(item["spuPk"]),
                unit_cost=Decimal(item["unitCost"]),
                currency="CNY",
                note=f"订单包裹文件导入；{item['sampleCount']} 条非空采购金额的算术平均值",
                created_by=created_by,
            )
        session.commit()
        return {"importedCount": preview["readyCount"], "importRows": preview["importRows"],
                "totalRows": preview["totalRows"], "blankCostRows": preview["blankCostRows"],
                "skippedRows": preview["skippedRows"], "issues": preview["issues"]}
    except CostImportError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.post("", status_code=201, openapi_extra=UPLOAD_SCHEMA)
async def import_prices(request: Request, session: SessionDep) -> dict[str, Any]:
    content = await read_upload(request)
    created_by = f"api_key:{request.scope.get('api_key_role') or 'unknown'}"
    return await run_in_threadpool(process_import, session, content, created_by, write=True)


@router.post("/preview", deprecated=True, openapi_extra=UPLOAD_SCHEMA)
async def preview_import(request: Request, session: SessionDep) -> dict[str, Any]:
    content = await read_upload(request)
    return await run_in_threadpool(process_import, session, content, "")


@router.post("/confirm", status_code=201, deprecated=True, openapi_extra=UPLOAD_SCHEMA)
async def confirm_import(
    request: Request,
    session: SessionDep,
    fingerprint: Annotated[str, Query(min_length=64, max_length=64, pattern="^[a-f0-9]{64}$")],
) -> dict[str, Any]:
    content = await read_upload(request)
    created_by = f"api_key:{request.scope.get('api_key_role') or 'unknown'}"
    return await run_in_threadpool(process_import, session, content, created_by, fingerprint)
