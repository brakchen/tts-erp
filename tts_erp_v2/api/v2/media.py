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
4. ``DELETE /v2/media/assets/{id}`` rejects referenced assets; otherwise it
   soft-deletes the row and best-effort removes the object from the bucket.

Digital humans are persona records. An asset may stay unattached (global
pool) and may be attached to several personas at once.

Auth:
- All endpoints need a key (cookie session or bearer API key).
- GET → readonly; writes → readwrite.
- Cookie-authed writes must carry ``X-Requested-With: tts-erp`` (CSRF guard).
"""

from __future__ import annotations

import base64
import binascii
import json
import logging
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from tts_erp_v2.api.deps import get_session, require_role_at_least
from tts_erp_v2.api.v2 import media_packs
from tts_erp_v2.db.models.media import DigitalHuman, MediaAsset
from tts_erp_v2.media import packs, repository
from tts_erp_v2.media.domain import (
    AssetKind,
    AssetStatus,
    DigitalHumanStatus,
    allowed_content_types,
    kind_for_content_type,
    max_bytes_for,
)
from tts_erp_v2.media.object_store import (
    MediaObjectStore,
    ObjectNotFound,
    media_store_from_env,
)
from tts_erp_v2.storage.minio_client import MinioConfigError

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
    tags: list[str] = Field(default_factory=list)
    is_portrait: bool = Field(default=False, alias="isPortrait")


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
    model_config = {"populate_by_name": True}

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
    usage_count: int | None = Field(default=None, alias="usageCount")


class DigitalHumanReferenceOut(BaseModel):
    digital_human_id: int
    name: str
    status: DigitalHumanStatus


class PackReferenceOut(BaseModel):
    pack_id: int = Field(serialization_alias="packId")
    name: str


class AssetDetailOut(AssetOut):
    used_by: list[DigitalHumanReferenceOut]
    used_by_packs: list[PackReferenceOut] = Field(serialization_alias="usedByPacks")


class AssetListOut(BaseModel):
    items: list[AssetOut]
    total: int
    limit: int
    offset: int
    next_cursor: str | None = Field(default=None, serialization_alias="nextCursor")


class AssetUpdateIn(BaseModel):
    filename: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=2000)
    tags: list[str] | None = None


class DigitalHumanIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=2000)
    portrait_asset_id: int | None = Field(default=None, ge=1, alias="portraitAssetId")


class DigitalHumanUpdateIn(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=2000)
    status: DigitalHumanStatus | None = None
    portrait_asset_id: int | None = Field(default=None, ge=1, alias="portraitAssetId")


class DigitalHumanOut(BaseModel):
    digital_human_id: int
    name: str
    description: str | None = None
    status: str
    created_at: datetime
    asset_ids: list[int] = Field(default_factory=list)
    portrait_url: str | None = Field(default=None, serialization_alias="portraitUrl")


class DigitalHumanPageOut(BaseModel):
    items: list[DigitalHumanOut]
    total: int
    next_cursor: str | None = Field(serialization_alias="nextCursor")


class DigitalHumanDetailOut(DigitalHumanOut):
    assets: list[AssetOut] = Field(default_factory=list)


class LinkAssetIn(BaseModel):
    asset_id: int = Field(ge=1, alias="assetId")

    model_config = {"populate_by_name": True}


class UnlinkAssetsIn(BaseModel):
    asset_ids: list[Annotated[int, Field(ge=1)]] = Field(min_length=1, alias="assetIds")


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


def _asset_out(
    asset: MediaAsset,
    url: str | None,
    expires_at: datetime | None,
    *,
    usage_count: int | None = None,
) -> AssetOut:
    return AssetOut(
        asset_id=asset.id,
        kind=asset.kind,
        filename=asset.filename,
        content_type=asset.content_type,
        size_bytes=asset.size_bytes,
        description=asset.description,
        tags=repository.normalize_tags(asset.tags or []),
        status=asset.status,
        failure_reason=asset.failure_reason,
        uploaded_at=asset.uploaded_at,
        uploaded_by_prefix=asset.uploaded_by_prefix,
        created_at=asset.created_at,
        url=url,
        url_expires_at=expires_at,
        usage_count=usage_count,
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


def _required_store() -> MediaObjectStore:
    try:
        return get_store()
    except MinioConfigError as error:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, "Media object storage is unavailable"
        ) from error


def _asset_preview(asset: MediaAsset) -> tuple[str | None, datetime | None]:
    if asset.status != AssetStatus.READY.value:
        return None, None
    try:
        return _presigned_get(get_store(), asset.object_key)
    except MinioConfigError:
        return None, None
    except Exception as error:  # noqa: BLE001 — a preview is optional after persistence
        logger.warning(
            "media preview URL unavailable",
            extra={"asset_id": asset.id, "error_type": type(error).__name__},
        )
        return None, None


def _asset_with_preview(
    asset: MediaAsset, *, usage_count: int | None = None
) -> AssetOut:
    return _asset_out(asset, *_asset_preview(asset), usage_count=usage_count)


def _human_out(
    session: Session, human: DigitalHuman, asset_ids: list[int] | None = None
) -> DigitalHumanOut:
    portrait_url = None
    if human.portrait_asset_id is not None:
        photo = repository.get_asset(
            session, human.portrait_asset_id, include_portrait=True
        )
        if photo is not None:
            portrait_url, _ = _asset_preview(photo)
    return DigitalHumanOut(
        digital_human_id=human.id,
        name=human.name,
        description=human.description,
        status=human.status,
        created_at=human.created_at,
        asset_ids=asset_ids
        if asset_ids is not None
        else repository.asset_ids_for_digital_human(session, human.id),
        portrait_url=portrait_url,
    )


def _after(cursor: str | None, offset: int) -> tuple[datetime, int] | None:
    if cursor is not None and offset:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "OFFSET_WITH_CURSOR")
    if not cursor:
        return None
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        payload = json.loads(base64.b64decode(padded, altchars=b"-_", validate=True))
        updated_at = datetime.fromisoformat(payload["updatedAt"])
        row_id = payload["id"]
        if (
            updated_at.tzinfo is None
            or type(row_id) is not int
            or not 0 < row_id < 2**63
        ):
            raise ValueError
        return updated_at, row_id
    except (TypeError, KeyError, ValueError, binascii.Error) as error:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "INVALID_CURSOR") from error


def _next_cursor(rows: Sequence[MediaAsset | DigitalHuman], limit: int) -> str | None:
    if len(rows) <= limit:
        return None
    row = rows[limit - 1]
    payload = json.dumps({"updatedAt": row.updated_at.isoformat(), "id": row.id})
    return base64.urlsafe_b64encode(payload.encode()).decode().rstrip("=")


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
    if body.is_portrait and kind != AssetKind.IMAGE:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "PORTRAIT_MUST_BE_IMAGE")
    cap = max_bytes_for(kind)
    if body.size_bytes > cap:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"size_bytes exceeds {cap} ({kind.value} cap)",
        )

    store = _required_store()
    key_id = request.scope.get("api_key_id")
    key_prefix = request.scope.get("api_key_prefix") or ""

    asset = repository.create_asset(
        sess,
        kind=kind,
        filename=body.filename,
        content_type=body.content_type,
        size_bytes=body.size_bytes,
        bucket=store.bucket(),
        uploaded_by_key_id=key_id,
        uploaded_by_prefix=key_prefix,
        tags=[] if body.is_portrait else body.tags,
        is_portrait=body.is_portrait,
    )
    upload_url = store.presign_put(asset.object_key, body.content_type)
    expires_at = datetime.now(UTC) + store.default_expiry
    ticket = UploadUrlOut(
        asset_id=asset.id,
        object_key=asset.object_key,
        upload_url=upload_url,
        upload_expires_at=expires_at,
        required_headers={"Content-Type": body.content_type},
    )
    sess.commit()
    return ticket


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

    asset = repository.get_asset(sess, asset_id, include_portrait=True)
    if asset is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            f"asset not found: {asset_id}",
        )

    if asset.status != AssetStatus.READY.value:
        store = _required_store()
        failure_reason = None
        failure_code = None
        try:
            meta = store.stat(asset.object_key)
        except ObjectNotFound:
            failure_reason = "object not found in object storage"
            failure_code = "UPLOAD_NOT_FOUND"
        else:
            if meta["size"] != asset.size_bytes:
                failure_reason = "size mismatch"
                failure_code = "SIZE_MISMATCH"
        asset = repository.record_upload_verification(sess, asset_id, failure_reason)
        if asset is None:
            raise HTTPException(
                status.HTTP_404_NOT_FOUND, f"asset not found: {asset_id}"
            )
        if failure_code is not None and asset.status != AssetStatus.READY.value:
            raise HTTPException(status.HTTP_409_CONFLICT, failure_code)

    url, expires_at = _asset_preview(asset)
    return ConfirmOut(
        asset_id=asset.id,
        status=asset.status,
        object_key=asset.object_key,
        size_bytes=asset.size_bytes,
        content_type=asset.content_type,
        url=url,
        url_expires_at=expires_at,
    )


# --- GET /assets -------------------------------------------------------------


@router.get("/assets", response_model=AssetListOut)
def list_assets(
    request: Request,
    sess: Annotated[Session, Depends(get_session)],
    kind: str | None = Query(default=None),
    asset_status: Annotated[AssetStatus | None, Query(alias="status")] = None,
    keyword: str | None = Query(default=None, max_length=120),
    tag: str | None = Query(default=None),
    digital_human_id: int | None = Query(default=None, ge=1, alias="digitalHumanId"),
    exclude_digital_human_id: int | None = Query(
        default=None, ge=1, alias="excludeDigitalHumanId"
    ),
    pack_id: int | None = Query(default=None, ge=1, alias="packId"),
    exclude_pack_id: int | None = Query(default=None, ge=1, alias="excludePackId"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    cursor: str | None = Query(default=None, max_length=256),
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
            exclude_digital_human_id=exclude_digital_human_id,
            pack_id=pack_id,
            exclude_pack_id=exclude_pack_id,
            status=asset_status,
            limit=limit + (cursor is not None),
            offset=offset,
            recent=cursor is not None,
            after=_after(cursor, offset),
        ),
    )
    rows = page.items[:limit]
    usage_counts = repository.usage_counts_for_assets(
        sess, [asset.id for asset in rows]
    )
    items = [
        _asset_with_preview(asset, usage_count=usage_counts[asset.id]) for asset in rows
    ]
    return AssetListOut(
        items=items,
        total=page.total,
        limit=limit,
        offset=offset,
        next_cursor=_next_cursor(page.items, limit),
    )


@router.get("/assets/tags", response_model=list[str])
def list_asset_tags(
    request: Request,
    sess: Annotated[Session, Depends(get_session)],
) -> list[str]:
    """Read tag candidates from ready, undeleted assets."""
    require_role_at_least(request, "readonly")
    return repository.list_asset_tags(sess)


# --- GET /assets/{id} --------------------------------------------------------


@router.get("/assets/{asset_id}", response_model=AssetDetailOut)
def get_asset_detail(
    asset_id: int,
    request: Request,
    sess: Annotated[Session, Depends(get_session)],
) -> AssetDetailOut:
    """Return one asset with a read URL and its live persona references."""
    require_role_at_least(request, "readonly")
    asset = repository.get_asset(sess, asset_id)
    if asset is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            f"asset not found: {asset_id}",
        )
    humans = repository.digital_humans_for_asset(sess, asset_id)
    return AssetDetailOut(
        **_asset_with_preview(asset, usage_count=len(humans)).model_dump(),
        used_by=[
            DigitalHumanReferenceOut(
                digital_human_id=human.id,
                name=human.name,
                status=DigitalHumanStatus(human.status),
            )
            for human in humans
        ],
        used_by_packs=[
            PackReferenceOut(pack_id=pack_id, name=name)
            for pack_id, name in packs.packs_for_video(sess, asset_id)
        ],
    )


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
    return _asset_with_preview(asset)


# --- DELETE /assets/{id} -----------------------------------------------------


@router.delete("/assets/{asset_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_asset(
    asset_id: int,
    request: Request,
    sess: Annotated[Session, Depends(get_session)],
) -> Response:
    """Reject referenced assets, otherwise delete their metadata and object."""
    require_role_at_least(request, "readwrite")
    _csrf(request)

    asset = repository.soft_delete_asset(sess, asset_id)
    if asset is repository.DeleteAssetError.ASSET_IN_USE:
        raise HTTPException(status.HTTP_409_CONFLICT, "ASSET_IN_USE")
    if asset is not None:
        try:
            get_store().remove(asset.object_key)
        except Exception as e:  # noqa: BLE001 — best-effort
            logger.warning(
                "object removal failed for soft-deleted asset",
                extra={
                    "asset_id": asset_id,
                    "object_key": asset.object_key,
                    "error": str(e),
                },
            )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# --- GET /digital-humans -----------------------------------------------------


@router.get(
    "/digital-humans", response_model=list[DigitalHumanOut] | DigitalHumanPageOut
)
def list_digital_humans(
    request: Request,
    sess: Annotated[Session, Depends(get_session)],
    keyword: str | None = Query(default=None, max_length=120),
    human_status: Annotated[DigitalHumanStatus | None, Query(alias="status")] = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    cursor: str | None = Query(default=None, max_length=256),
) -> list[DigitalHumanOut] | DigitalHumanPageOut:
    """List live personas with their linked asset ids."""
    require_role_at_least(request, "readonly")
    humans = repository.list_digital_humans(
        sess,
        keyword=keyword,
        status=human_status,
        limit=limit + (cursor is not None),
        offset=offset,
        recent=cursor is not None,
        after=_after(cursor, offset),
    )
    items = [_human_out(sess, human) for human in humans[:limit]]
    if cursor is None:
        return items
    return DigitalHumanPageOut(
        items=items,
        total=repository.count_digital_humans(sess, keyword, human_status),
        next_cursor=_next_cursor(humans, limit),
    )


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
    try:
        human = repository.create_digital_human(
            sess,
            name=body.name,
            description=body.description,
            created_by=_actor(request),
            portrait_asset_id=body.portrait_asset_id,
        )
    except repository.PortraitNotReady as error:
        raise HTTPException(status.HTTP_409_CONFLICT, "PORTRAIT_NOT_READY") from error
    return _human_out(sess, human, [])


# --- GET /digital-humans/{id} ------------------------------------------------


@router.get("/digital-humans/{digital_human_id}", response_model=DigitalHumanDetailOut)
def get_digital_human(
    digital_human_id: int,
    request: Request,
    sess: Annotated[Session, Depends(get_session)],
    include_assets: bool = Query(default=True, alias="includeAssets"),
    include_pack_videos: bool = Query(default=False, alias="includePackVideos"),
) -> DigitalHumanDetailOut:
    """Return one persona with the full asset records it references."""
    require_role_at_least(request, "readonly")
    human = repository.get_digital_human(sess, digital_human_id)
    if human is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            f"digital human not found: {digital_human_id}",
        )
    asset_ids = repository.asset_ids_for_digital_human(
        sess, human.id, include_pack_videos=include_pack_videos
    )
    if not include_assets:
        return DigitalHumanDetailOut(**_human_out(sess, human, asset_ids).model_dump())
    usage_counts = repository.usage_counts_for_assets(sess, asset_ids)
    assets: list[AssetOut] = []
    for asset_id in asset_ids:
        asset = repository.get_asset(sess, asset_id)
        if asset is None:
            continue
        assets.append(_asset_with_preview(asset, usage_count=usage_counts[asset.id]))
    return DigitalHumanDetailOut(
        **_human_out(sess, human, [asset.asset_id for asset in assets]).model_dump(),
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
    try:
        human = repository.update_digital_human(
            sess,
            digital_human_id,
            name=body.name,
            description=body.description,
            status=body.status,
            portrait_asset_id=body.portrait_asset_id,
        )
    except repository.PortraitNotReady as error:
        raise HTTPException(status.HTTP_409_CONFLICT, "PORTRAIT_NOT_READY") from error
    if human is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            f"digital human not found: {digital_human_id}",
        )
    return _human_out(sess, human)


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
    human = repository.link_asset_to_digital_human(
        sess, digital_human_id, body.asset_id
    )
    if human is repository.LinkAssetError.HUMAN_NOT_FOUND:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            f"digital human not found: {digital_human_id}",
        )
    if human is repository.LinkAssetError.ASSET_NOT_FOUND:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            f"asset not found: {body.asset_id}",
        )
    if human is repository.LinkAssetError.ASSET_NOT_READY:
        raise HTTPException(status.HTTP_409_CONFLICT, "ASSET_NOT_READY")
    return _human_out(sess, human)


@router.post(
    "/digital-humans/{digital_human_id}/assets/batch-remove",
    status_code=status.HTTP_204_NO_CONTENT,
)
def unlink_assets(
    digital_human_id: int,
    body: UnlinkAssetsIn,
    request: Request,
    sess: Annotated[Session, Depends(get_session)],
) -> Response:
    """Remove multiple links in one transaction. Repeating the request is safe."""
    require_role_at_least(request, "readwrite")
    _csrf(request)
    if (
        repository.unlink_assets_from_digital_human(
            sess, digital_human_id, body.asset_ids
        )
        is None
    ):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "DIGITAL_HUMAN_NOT_FOUND")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


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


router.include_router(media_packs.router)

__all__ = ["router"]
