"""HTTP wire adapter for shop-scoped focused SPUs."""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from tts_erp_v2.api.deps import (
    caller_key_hash,
    get_session,
    require_role_at_least,
)
from tts_erp_v2.reporting.focused_spus import (
    FocusedShopNotFound,
    FocusedSpuError,
    FocusedSpuNotFoundInShop,
    FocusedSpuPatch,
    FocusedSpuPatchConflict,
    FocusedSpuPatchTooLarge,
    FocusedSpuQuery,
    apply_patch,
    list_focused_spus,
)

router = APIRouter(prefix="/v2/reporting/focused-spus", tags=["focused-spus"])


class _CamelModel(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")


class FocusedSpuItemOut(_CamelModel):
    spu_pk: int = Field(alias="spuPk")
    spu_id: str = Field(alias="spuId")
    title: str | None
    status: str | None
    created_at: datetime = Field(alias="createdAt")
    updated_at: datetime = Field(alias="updatedAt")


class FocusedSpuPageOut(_CamelModel):
    shop_pk: int = Field(alias="shopPk")
    items: list[FocusedSpuItemOut]
    total: int
    matched_total: int = Field(alias="matchedTotal")
    limit: int
    offset: int


class FocusedSpuPatchIn(_CamelModel):
    add_spu_ids: list[str] = Field(default_factory=list, alias="addSpuIds")
    remove_spu_ids: list[str] = Field(default_factory=list, alias="removeSpuIds")


class FocusedSpuPatchOut(_CamelModel):
    shop_pk: int = Field(alias="shopPk")
    total: int
    added_spu_ids: list[str] = Field(alias="addedSpuIds")
    removed_spu_ids: list[str] = Field(alias="removedSpuIds")


def _domain_error(code: str, message: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        detail={"code": code, "message": message},
    )


def _require_cookie_csrf(request: Request) -> None:
    if (
        request.scope.get("auth_method") == "cookie"
        and request.headers.get("x-requested-with") != "tts-erp"
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="cookie-authenticated mutations require X-Requested-With: tts-erp",
        )


@router.get("/{shop_pk}", response_model=FocusedSpuPageOut)
def get_focused_spus(
    shop_pk: int,
    sess: Session = Depends(get_session),  # noqa: B008
    q: str | None = Query(default=None, max_length=200),
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> FocusedSpuPageOut:
    try:
        page = list_focused_spus(
            sess,
            shop_pk=shop_pk,
            query=FocusedSpuQuery(search=q, limit=limit, offset=offset),
        )
    except FocusedShopNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return FocusedSpuPageOut(
        shopPk=page.shop_pk,
        items=[
            FocusedSpuItemOut(
                spuPk=item.spu_pk,
                spuId=item.spu_id,
                title=item.title,
                status=item.status,
                createdAt=item.created_at,
                updatedAt=item.updated_at,
            )
            for item in page.items
        ],
        total=page.total,
        matchedTotal=page.matched_total,
        limit=page.limit,
        offset=page.offset,
    )


@router.patch("/{shop_pk}", response_model=FocusedSpuPatchOut)
def patch_focused_spus(
    shop_pk: int,
    body: FocusedSpuPatchIn,
    request: Request,
    sess: Session = Depends(get_session),  # noqa: B008
) -> FocusedSpuPatchOut:
    require_role_at_least(request, "readwrite")
    _require_cookie_csrf(request)
    try:
        result = apply_patch(
            sess,
            shop_pk=shop_pk,
            patch=FocusedSpuPatch(
                add_spu_ids=tuple(body.add_spu_ids),
                remove_spu_ids=tuple(body.remove_spu_ids),
            ),
            actor=caller_key_hash(request),
        )
    except FocusedShopNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except FocusedSpuNotFoundInShop as exc:
        raise _domain_error("SPU_NOT_FOUND_IN_SHOP", str(exc)) from exc
    except FocusedSpuPatchConflict as exc:
        raise _domain_error("FOCUSED_SPU_PATCH_CONFLICT", str(exc)) from exc
    except FocusedSpuPatchTooLarge as exc:
        raise _domain_error("FOCUSED_SPU_PATCH_TOO_LARGE", str(exc)) from exc
    except FocusedSpuError as exc:
        raise _domain_error("FOCUSED_SPU_PATCH_INVALID", str(exc)) from exc
    sess.commit()
    return FocusedSpuPatchOut(
        shopPk=result.shop_pk,
        total=result.total,
        addedSpuIds=list(result.added_spu_ids),
        removedSpuIds=list(result.removed_spu_ids),
    )
