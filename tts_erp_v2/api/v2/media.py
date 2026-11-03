"""/v2/media/* — global media asset library and digital humans.

Spec: docs/design/media-asset-library.md.

Flow for one asset (mirrors the proven SPU-image flow, but with a global pool
and no shop partition):

1. ``POST /v2/media/assets/upload-url`` returns a presigned PUT URL plus the
   created asset id. The browser PUTs the bytes straight to MinIO — the API
   server never proxies object bytes.
2. ``POST /v2/media/assets/{id}/confirm`` HEADs the object; a size match flips
   the row to ``ready``.
3. ``GET /v2/media/assets`` lists live assets with filters.
4. ``DELETE /v2/media/assets/{id}`` soft-deletes the row and best-effort
   removes the object from the bucket.

Digital humans are persona records. An asset may stay unattached (global
pool) and may be attached to several personas at once.

Auth:
- All endpoints need a key (cookie session or bearer API key).
- GET → readonly; writes → readwrite.
- Cookie-authed writes must carry ``X-Requested-With: tts-erp`` (CSRF guard).
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from tts_erp_v2.api.deps import (
    get_session,
    require_destructive_guard,
    require_role_at_least,
)
from tts_erp_v2.media import repository
from tts_erp_v2.media.domain import (
    AssetKind,
    DigitalHumanStatus,
    allowed_content_types,
    build_object_key,
    kind_for_content_type,
    max_bytes_for,
)
from tts_erp_v2.media.object_store import (
    MediaObjectStore,
    ObjectNotFound,
    media_store_from_env,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v2/media", tags=["media"])

_store: MediaObjectStore | None = None


def get_store() -> MediaObjectStore:
    """Lazily construct the media object store (tests override this)."""
    global _store
    if _store is None:
        _store = media_store_from_env()
    return _store


# --- pydantic schemas --------------------------------------------------------


class UploadUrlIn(BaseModel):
    filename: str = Field(min_length=1, max_length=255)
    content_type: str = Field(min_length=1, max_length=127, alias="contentType")
    size_bytes: int = Field(ge=1, alias="sizeBytes")


class UploadUrlOut(BaseModel):
    asset_id: int
    object_key: str
    upload_url: str
    upload_expires_at: datetime
    required_headers: dict[str, str]


class ConfirmOut(BaseModel):
    asset_id: int
    status: str
    object_key: str
    size_bytes: int
    content_type: str
    url: str | None = None
    url_expires_at: datetime | None = None


class AssetOut(BaseModel):
    asset_id: int
    kind: str
    filename: str
    content_type: str
    size_bytes: int
    description: str | None = None
    tags: list[str] = Field(default_factory=list)
    status: str
    failure_reason: str | None = None
    uploaded_at: datetime | None = None
    uploaded_by_prefix: str | None = None
    created_at: datetime
    url: str | None = None
    url_expires_at: datetime | None = None


class AssetListOut(BaseModel):
    items: list[AssetOut]
    total: int
    limit: int
    offset: int


class AssetUpdateIn(BaseModel):
    filename: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=2000)
    tags: list[str] | None = None


class DigitalHumanIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=2000)


class DigitalHumanUpdateIn(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=2000)
    status: DigitalHumanStatus | None = None


class DigitalHumanOut(BaseModel):
    digital_human_id: int
    name: str
    description: str | None = None
    status: str
    created_at: datetime
    asset_ids: list[int] = Field(default_factory=list)


class DigitalHumanDetailOut(DigitalHumanOut):
    assets: list[AssetOut] = Field(default_factory=list)


class LinkAssetIn(BaseModel):
    asset_id: int = Field(ge=1, alias="assetId")

    model_config = {"populate_by_name": True}


# --- helpers -----------------------------------------------------------------


def _csrf(request: Request) -> None:
    """Cookie-authed writes must carry the CSRF header.

    Bearer-authed callers are exempt: they choose their own headers and the
    request is already a deliberate cross-site call.
    """
    if (
        request.scope.get("auth_method") == "cookie"
        and request.headers.get("X-Requested-With") != "tts-erp"
    ):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "cookie-authed POST must set header X-Requested-With: tts-erp (CSRF guard)",
        )


def _actor(request: Request) -> str:
    """Render the acting principal without leaking the key itself."""
    user_id = request.scope.get("user_id")
    if user_id is not None:
        return f"user:{user_id}"
    key_prefix = request.scope.get("api_key_prefix")
    if key_prefix:
        return str(key_prefix)
    key_hash = request.scope.get("api_key_hash")
    return str(key_hash)[:12] if key_hash else "system"


def _asset_out(asset, url: str | None, expires_at: datetime | None) -> AssetOut:
    return AssetOut(
        asset_id=asset.id,
        kind=asset.kind,
        filename=asset.filename,
        content_type=asset.content_type,
        size_bytes=asset.size_bytes,
        description=asset.description,
        tags=list(asset.tags or []),
        status=asset.status,
        failure_reason=asset.failure_reason,
        uploaded_at=asset.uploaded_at,
        uploaded_by_prefix=asset.uploaded_by_prefix,
        created_at=asset.created_at,
        url=url,
        url_expires_at=expires_at,
    )


def _presigned_get(
    store: MediaObjectStore, object_key: str
) -> tuple[str | None, datetime | None]:
    """Return a read URL for one object, preferring a public CDN base."""
    public_url = store.public_url_or_none(object_key)
    if public_url is not None:
        return public_url, None
    url, expires_at = store.presign_get(object_key)
    return url, expires_at


# --- POST /assets/upload-url ------------------------------------------------


@router.post(
    "/assets/upload-url",
    response_model=UploadUrlOut,
    status_code=status.HTTP_201_CREATED,
)
def create_upload_url(
    body: UploadUrlIn,
    request: Request,
    sess: Annotated[Session, Depends(get_session)],
) -> UploadUrlOut:
    """Issue a presigned PUT URL so the browser can upload straight to MinIO."""
    require_role_at_least(request, "readwrite")
    _csrf(request)

    kind = kind_for_content_type(body.content_type)
    if kind is None:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"content_type {body.content_type!r} not allowed; expected one of "
            f"{list(allowed_content_types())}",
        )
    cap = max_bytes_for(kind)
    if body.size_bytes > cap:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"size_bytes exceeds {cap} ({kind.value} cap)",
        )

    store = get_store()
    key_id = request.scope.get("api_key_id")
    key_prefix = request.scope.get("api_key_prefix") or ""

    asset = repository.create_asset(
        sess,
        kind=kind,
        filename=body.filename,
        content_type=body.content_type,
        size_bytes=body.size_bytes,
        bucket=store.bucket(),
        object_key="placeholder",
        uploaded_by_key_id=key_id,
        uploaded_by_prefix=key_prefix,
    )
    target = build_object_key(
        bucket=store.bucket(),
        asset_id=asset.id,
        filename=body.filename,
        content_type=body.content_type,
    )
    repository.replace_object_key(sess, asset.id, target.key)

    upload_url = store.presign_put(target.key, body.content_type)
    expires_at = datetime.now(UTC) + store.default_expiry
    return UploadUrlOut(
        asset_id=asset.id,
        object_key=target.key,
        upload_url=upload_url,
        upload_expires_at=expires_at,
        required_headers={"Content-Type": body.content_type},
    )


# --- POST /assets/{id}/confirm ----------------------------------------------


@router.post("/assets/{asset_id}/confirm", response_model=ConfirmOut)
def confirm_upload(
    asset_id: int,
    request: Request,
    sess: Annotated[Session, Depends(get_session)],
) -> ConfirmOut:
    """HEAD the object; a size match flips the asset to ``ready``."""
    require_role_at_least(request, "readwrite")
    _csrf(request)

    asset = repository.get_asset(sess, asset_id)
    if asset is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            f"asset not found: {asset_id}",
        )

    store = get_store()
    try:
        meta = store.stat(asset.object_key)
    except ObjectNotFound as e:
        repository.mark_asset_failed(sess, asset_id, "object not found in object storage")
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "UPLOAD_NOT_FOUND",
        ) from e

    if meta["size"] != asset.size_bytes:
        repository.mark_asset_failed(sess, asset_id, "size mismatch")
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "SIZE_MISMATCH",
        )

    ready = repository.mark_asset_ready(sess, asset_id)
    if ready is None:
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            "failed to mark asset ready",
        )
    url, expires_at = _presigned_get(store, ready.object_key)
    return ConfirmOut(
        asset_id=ready.id,
        status=ready.status,
        object_key=ready.object_key,
        size_bytes=ready.size_bytes,
        content_type=ready.content_type,
        url=url,
        url_expires_at=expires_at,
    )


# --- GET /assets -------------------------------------------------------------


@router.get("/assets", response_model=AssetListOut)
def list_assets(
    request: Request,
    sess: Annotated[Session, Depends(get_session)],
    kind: str | None = Query(default=None),
    keyword: str | None = Query(default=None, max_length=120),
    tag: str | None = Query(default=None, max_length=64),
    digital_human_id: int | None = Query(default=None, ge=1, alias="digitalHumanId"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> AssetListOut:
    """List live assets with optional filters and a total count."""
    require_role_at_least(request, "readonly")

    parsed_kind: AssetKind | None = None
    if kind is not None:
        try:
            parsed_kind = AssetKind(kind)
        except ValueError as e:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                f"kind must be one of {[k.value for k in AssetKind]}",
            ) from e

    page = repository.list_assets(
        sess,
        repository.AssetFilter(
            kind=parsed_kind,
            keyword=keyword,
            tag=tag,
            digital_human_id=digital_human_id,
            limit=limit,
            offset=offset,
        ),
    )
    store = get_store()
    items: list[AssetOut] = []
    for asset in page.items:
        url, expires_at = _presigned_get(store, asset.object_key)
        items.append(_asset_out(asset, url, expires_at))
    return AssetListOut(items=items, total=page.total, limit=limit, offset=offset)


# --- GET /assets/{id} --------------------------------------------------------


@router.get("/assets/{asset_id}", response_model=AssetOut)
def get_asset_detail(
    asset_id: int,
    request: Request,
    sess: Annotated[Session, Depends(get_session)],
) -> AssetOut:
    """Return one asset with a read URL."""
    require_role_at_least(request, "readonly")
    asset = repository.get_asset(sess, asset_id)
    if asset is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            f"asset not found: {asset_id}",
        )
    store = get_store()
    url, expires_at = _presigned_get(store, asset.object_key)
    return _asset_out(asset, url, expires_at)


# --- PATCH /assets/{id} ------------------------------------------------------


@router.patch("/assets/{asset_id}", response_model=AssetOut)
def update_asset(
    asset_id: int,
    body: AssetUpdateIn,
    request: Request,
    sess: Annotated[Session, Depends(get_session)],
) -> AssetOut:
    """Edit the human-readable metadata of one asset."""
    require_role_at_least(request, "readwrite")
    _csrf(request)
    asset = repository.update_asset_metadata(
        sess,
        asset_id,
        filename=body.filename,
        description=body.description,
        tags=body.tags,
    )
    if asset is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            f"asset not found: {asset_id}",
        )
    store = get_store()
    url, expires_at = _presigned_get(store, asset.object_key)
    return _asset_out(asset, url, expires_at)


# --- DELETE /assets/{id} -----------------------------------------------------


@router.delete("/assets/{asset_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_asset(
    asset_id: int,
    request: Request,
    sess: Annotated[Session, Depends(get_session)],
) -> Response:
    """Soft-delete the asset and best-effort remove the stored object."""
    require_role_at_least(request, "readwrite")
    _csrf(request)
    require_destructive_guard(request, op_name="media_asset.delete")

    asset = repository.soft_delete_asset(sess, asset_id)
    if asset is not None:
        store = get_store()
        try:
            store.remove(asset.object_key)
        except Exception as e:  # noqa: BLE001 — best-effort
            logger.warning(
                "object removal failed for soft-deleted asset",
                extra={"asset_id": asset_id, "object_key": asset.object_key, "error": str(e)},
            )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# --- GET /digital-humans -----------------------------------------------------


@router.get("/digital-humans", response_model=list[DigitalHumanOut])
def list_digital_humans(
    request: Request,
    sess: Annotated[Session, Depends(get_session)],
    keyword: str | None = Query(default=None, max_length=120),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> list[DigitalHumanOut]:
    """List live personas with their linked asset ids."""
    require_role_at_least(request, "readonly")
    humans = repository.list_digital_humans(
        sess, keyword=keyword, limit=limit, offset=offset
    )
    return [
        DigitalHumanOut(
            digital_human_id=human.id,
            name=human.name,
            description=human.description,
            status=human.status,
            created_at=human.created_at,
            asset_ids=repository.asset_ids_for_digital_human(sess, human.id),
        )
        for human in humans
    ]


# --- POST /digital-humans ----------------------------------------------------


@router.post(
    "/digital-humans",
    response_model=DigitalHumanOut,
    status_code=status.HTTP_201_CREATED,
)
def create_digital_human(
    body: DigitalHumanIn,
    request: Request,
    sess: Annotated[Session, Depends(get_session)],
) -> DigitalHumanOut:
    """Create one persona record."""
    require_role_at_least(request, "readwrite")
    _csrf(request)
    human = repository.create_digital_human(
        sess,
        name=body.name,
        description=body.description,
        created_by=_actor(request),
    )
    return DigitalHumanOut(
        digital_human_id=human.id,
        name=human.name,
        description=human.description,
        status=human.status,
        created_at=human.created_at,
        asset_ids=[],
    )


# --- GET /digital-humans/{id} ------------------------------------------------


@router.get("/digital-humans/{digital_human_id}", response_model=DigitalHumanDetailOut)
def get_digital_human(
    digital_human_id: int,
    request: Request,
    sess: Annotated[Session, Depends(get_session)],
) -> DigitalHumanDetailOut:
    """Return one persona with the full asset records it references."""
    require_role_at_least(request, "readonly")
    human = repository.get_digital_human(sess, digital_human_id)
    if human is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            f"digital human not found: {digital_human_id}",
        )
    store = get_store()
    asset_ids = repository.asset_ids_for_digital_human(sess, human.id)
    assets: list[AssetOut] = []
    for asset_id in asset_ids:
        asset = repository.get_asset(sess, asset_id)
        if asset is None:
            continue
        url, expires_at = _presigned_get(store, asset.object_key)
        assets.append(_asset_out(asset, url, expires_at))
    return DigitalHumanDetailOut(
        digital_human_id=human.id,
        name=human.name,
        description=human.description,
        status=human.status,
        created_at=human.created_at,
        asset_ids=asset_ids,
        assets=assets,
    )


# --- PATCH /digital-humans/{id} ----------------------------------------------


@router.patch("/digital-humans/{digital_human_id}", response_model=DigitalHumanOut)
def update_digital_human(
    digital_human_id: int,
    body: DigitalHumanUpdateIn,
    request: Request,
    sess: Annotated[Session, Depends(get_session)],
) -> DigitalHumanOut:
    """Edit one persona."""
    require_role_at_least(request, "readwrite")
    _csrf(request)
    human = repository.update_digital_human(
        sess,
        digital_human_id,
        name=body.name,
        description=body.description,
        status=body.status,
    )
    if human is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            f"digital human not found: {digital_human_id}",
        )
    return DigitalHumanOut(
        digital_human_id=human.id,
        name=human.name,
        description=human.description,
        status=human.status,
        created_at=human.created_at,
        asset_ids=repository.asset_ids_for_digital_human(sess, human.id),
    )


# --- DELETE /digital-humans/{id} ---------------------------------------------


@router.delete(
    "/digital-humans/{digital_human_id}", status_code=status.HTTP_204_NO_CONTENT
)
def delete_digital_human(
    digital_human_id: int,
    request: Request,
    sess: Annotated[Session, Depends(get_session)],
) -> Response:
    """Soft-delete one persona and drop its asset links.

    The assets themselves stay in the global pool — deleting a persona never
    destroys media.
    """
    require_role_at_least(request, "readwrite")
    _csrf(request)
    require_destructive_guard(request, op_name="media_digital_human.delete")
    repository.soft_delete_digital_human(sess, digital_human_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# --- POST /digital-humans/{id}/assets ----------------------------------------


@router.post(
    "/digital-humans/{digital_human_id}/assets",
    response_model=DigitalHumanOut,
)
def link_asset(
    digital_human_id: int,
    body: LinkAssetIn,
    request: Request,
    sess: Annotated[Session, Depends(get_session)],
) -> DigitalHumanOut:
    """Attach one asset to one persona. Idempotent."""
    require_role_at_least(request, "readwrite")
    _csrf(request)
    human = repository.get_digital_human(sess, digital_human_id)
    if human is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            f"digital human not found: {digital_human_id}",
        )
    if not repository.link_asset_to_digital_human(
        sess, digital_human_id, body.asset_id
    ):
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            f"asset not found: {body.asset_id}",
        )
    return DigitalHumanOut(
        digital_human_id=human.id,
        name=human.name,
        description=human.description,
        status=human.status,
        created_at=human.created_at,
        asset_ids=repository.asset_ids_for_digital_human(sess, human.id),
    )


# --- DELETE /digital-humans/{id}/assets/{asset_id} ---------------------------


@router.delete(
    "/digital-humans/{digital_human_id}/assets/{asset_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
def unlink_asset(
    digital_human_id: int,
    asset_id: int,
    request: Request,
    sess: Annotated[Session, Depends(get_session)],
) -> Response:
    """Detach one asset from one persona. Idempotent."""
    require_role_at_least(request, "readwrite")
    _csrf(request)
    repository.unlink_asset_from_digital_human(sess, digital_human_id, asset_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


__all__ = ["router"]
