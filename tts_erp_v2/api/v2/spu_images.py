"""/v2/spu-images/* — SPU image upload, confirm, list, delete.

Spec: tech-doc/procurement-ui-redesign.md §3.

Flow:
1. Browser POSTs to ``/v2/spu-images/upload-url`` and gets a presigned
   PUT URL + an ``image_id``.
2. Browser PUTs the file directly to MinIO (server doesn't proxy bytes).
3. Browser POSTs to ``/v2/spu-images/{id}/confirm``; server HEADs the
   object and flips status to ``ready``.
4. ``GET /v2/spu-images?channel_product_id=X`` returns ready images
   with presigned GET URLs.
5. ``DELETE /v2/spu-images/{id}`` soft-deletes and best-effort removes
   from MinIO.

Auth:
- All endpoints need a key (cookie or bearer).
- POST / upload-url, POST / confirm, DELETE → readwrite
- GET → readonly
- CSRF: cookie-authed POSTs must carry ``X-Requested-With: tts-erp``,
  same as ``/v2/reporting/manual-costs``.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.orm import Session

from tts_erp_v2.api.deps import get_session, require_role_at_least
from tts_erp_v2.storage.minio_client import (
    ALLOWED_CONTENT_TYPES,
    MAX_SIZE_BYTES,
    MinioClient,
    ObjectNotFound,
    build_object_key,
    slugify_filename,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v2/spu-images", tags=["spu-images"])


# --- module-level singletons (overridable from tests) -------------------


_minio_client_singleton: MinioClient | None = None


def get_minio_client() -> MinioClient:
    """Lazily construct the MinIO client.

    Tests override this function attribute to inject a fake client.
    """
    global _minio_client_singleton
    if _minio_client_singleton is None:
        _minio_client_singleton = MinioClient.from_env()
    return _minio_client_singleton


# --- pydantic schemas ------------------------------------------------------


class UploadUrlIn(BaseModel):
    channel_account_id: int = Field(ge=1)
    channel_product_id: int = Field(ge=1)
    filename: str = Field(min_length=1, max_length=255)
    content_type: str = Field(min_length=1, max_length=127)
    size_bytes: int = Field(ge=1)


class UploadUrlOut(BaseModel):
    image_id: int
    object_key: str
    upload_url: str
    upload_expires_at: datetime
    required_headers: dict[str, str]


class ConfirmOut(BaseModel):
    image_id: int
    status: str
    object_key: str
    size_bytes: int
    content_type: str
    url: str | None = None
    url_expires_at: datetime | None = None


class ImageOut(BaseModel):
    image_id: int
    channel_product_id: int
    channel_product_external_id: str | None = None
    object_key: str
    filename: str
    content_type: str
    size_bytes: int
    uploaded_at: datetime
    uploaded_by_key_prefix: str | None = None
    url: str | None = None
    url_expires_at: datetime | None = None


# --- SQL constants (module-level, bound params) --------------------------


SQL_VERIFY_ACCOUNT = (
    "SELECT id FROM commerce.channel_accounts WHERE id = :acct"
)
SQL_VERIFY_PRODUCT = (
    "SELECT id FROM commerce.channel_products "
    "WHERE id = :cp AND channel_account_id = :acct"
)
SQL_INSERT_IMAGE = (
    "INSERT INTO procurement.spu_images ("
    "channel_account_id, channel_product_id, object_key, "
    "filename, content_type, size_bytes, status, "
    "uploaded_by_key_id, uploaded_by_prefix) "
    "VALUES (:acct, :cp, :key, :fn, :ct, :size, 'awaiting_upload', "
    "        :key_id, :key_prefix) "
    "RETURNING id, object_key"
)
SQL_GET_IMAGE_FOR_CONFIRM = (
    "SELECT id, channel_account_id, channel_product_id, object_key, "
    "       filename, content_type, size_bytes, status, deleted_at "
    "FROM procurement.spu_images WHERE id = :id"
)
SQL_MARK_READY = (
    "UPDATE procurement.spu_images "
    "SET status = 'ready', uploaded_at = COALESCE(uploaded_at, now()) "
    "WHERE id = :id "
    "RETURNING id, status, object_key, size_bytes, content_type, uploaded_at"
)
SQL_MARK_FAILED = (
    "UPDATE procurement.spu_images "
    "SET status = 'failed', failure_reason = :why WHERE id = :id"
)
SQL_LIST_READY_IMAGES = (
    "SELECT si.id, si.channel_product_id, cp.external_product_id, "
    "       si.object_key, si.filename, si.content_type, si.size_bytes, "
    "       si.uploaded_at, si.uploaded_by_prefix "
    "FROM procurement.spu_images si "
    "JOIN commerce.channel_products cp ON cp.id = si.channel_product_id "
    "WHERE si.deleted_at IS NULL "
    "AND si.status = 'ready' "
    "AND (CAST(:cp_id AS bigint) IS NULL OR si.channel_product_id = :cp_id) "
    "ORDER BY si.uploaded_at DESC LIMIT :limit OFFSET :offset"
)
SQL_SOFT_DELETE = (
    "UPDATE procurement.spu_images SET deleted_at = now() "
    "WHERE id = :id AND deleted_at IS NULL RETURNING object_key"
)


_STMT_VERIFY_ACCOUNT = text(SQL_VERIFY_ACCOUNT)
_STMT_VERIFY_PRODUCT = text(SQL_VERIFY_PRODUCT)
_STMT_INSERT_IMAGE = text(SQL_INSERT_IMAGE)
_STMT_GET_IMAGE_FOR_CONFIRM = text(SQL_GET_IMAGE_FOR_CONFIRM)
_STMT_MARK_READY = text(SQL_MARK_READY)
_STMT_MARK_FAILED = text(SQL_MARK_FAILED)
_STMT_LIST_READY_IMAGES = text(SQL_LIST_READY_IMAGES)
_STMT_SOFT_DELETE = text(SQL_SOFT_DELETE)


# --- CSRF guard -----------------------------------------------------------


def _csrf_guard_if_cookie(request: Request) -> None:
    """Mirror the POST /v2/reporting/manual-costs CSRF guard.

    Cookie-authed POSTs must carry ``X-Requested-With: tts-erp``.
    Bearer-authed callers are exempt (they choose their own headers
    and the request is already a deliberate cross-site call).
    """
    if (
        request.scope.get("auth_method") == "cookie"
        and request.headers.get("X-Requested-With") != "tts-erp"
    ):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "cookie-authed POST must set header X-Requested-With: tts-erp (CSRF guard)",
        )


# --- POST /upload-url -----------------------------------------------------


@router.post(
    "/upload-url",
    response_model=UploadUrlOut,
    status_code=status.HTTP_201_CREATED,
)
def create_upload_url(
    body: UploadUrlIn,
    request: Request,
    sess: Annotated[Session, Depends(get_session)],
) -> UploadUrlOut:
    """Issue a presigned PUT URL for the browser to upload directly."""
    require_role_at_least(request, "readwrite")
    _csrf_guard_if_cookie(request)

    # Validate the requested size and content type BEFORE we hit the
    # database or MinIO. Bad input → 400.
    if body.size_bytes > MAX_SIZE_BYTES:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"size_bytes exceeds {MAX_SIZE_BYTES} (8 MiB cap)",
        )
    if body.content_type not in ALLOWED_CONTENT_TYPES:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"content_type {body.content_type!r} not allowed; "
            f"expected one of {sorted(ALLOWED_CONTENT_TYPES)}",
        )

    # Verify both foreign keys exist (and product belongs to account).
    if sess.execute(
        _STMT_VERIFY_ACCOUNT, {"acct": body.channel_account_id},
    ).first() is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            f"channel account not found: {body.channel_account_id}",
        )
    if sess.execute(
        _STMT_VERIFY_PRODUCT,
        {"cp": body.channel_product_id, "acct": body.channel_account_id},
    ).first() is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            f"channel product not found in account "
            f"{body.channel_account_id}: {body.channel_product_id}",
        )

    # Insert with status='awaiting_upload' and a placeholder object_key;
    # we'll UPDATE the real key once we have the generated id.
    # (We can't compose the key without the id, so we do a 2-step.)
    key_id = request.scope.get("api_key_id")
    key_prefix = request.scope.get("api_key_prefix") or ""

    placeholder = "placeholder"
    row = sess.execute(
        _STMT_INSERT_IMAGE,
        {
            "acct": body.channel_account_id,
            "cp": body.channel_product_id,
            "key": placeholder,
            "fn": body.filename,
            "ct": body.content_type,
            "size": body.size_bytes,
            "key_id": key_id,
            "key_prefix": key_prefix,
        },
    ).first()
    sess.commit()
    if row is None:
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            "failed to allocate image row",
        )
    image_id = row.id

    # Build the real object key and presign the PUT.
    slug = slugify_filename(body.filename)
    ext = body.filename.rsplit(".", 1)[-1].lower() if "." in body.filename else ""
    object_key = build_object_key(
        account_id=body.channel_account_id,
        product_id=body.channel_product_id,
        image_id=image_id,
        slug=slug,
        ext=ext or "bin",
        on_date=datetime.now(UTC),
    )

    # Patch the row with the real key.
    sess.execute(
        text("UPDATE procurement.spu_images SET object_key = :k WHERE id = :i"),
        {"k": object_key, "i": image_id},
    )
    sess.commit()

    minio = get_minio_client()
    upload_url = minio.presign_put(object_key, body.content_type)
    expires_at = datetime.now(UTC) + minio.default_expiry

    return UploadUrlOut(
        image_id=image_id,
        object_key=object_key,
        upload_url=upload_url,
        upload_expires_at=expires_at,
        required_headers={"Content-Type": body.content_type},
    )


# --- POST /confirm --------------------------------------------------------


@router.post(
    "/{image_id}/confirm",
    response_model=ConfirmOut,
)
def confirm_upload(
    image_id: int,
    request: Request,
    sess: Annotated[Session, Depends(get_session)],
) -> ConfirmOut:
    """HEAD the MinIO object; if it exists, mark the row ready."""
    require_role_at_least(request, "readwrite")
    _csrf_guard_if_cookie(request)

    row = sess.execute(
        _STMT_GET_IMAGE_FOR_CONFIRM, {"id": image_id},
    ).first()
    if row is None or row.deleted_at is not None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            f"image not found: {image_id}",
        )

    minio = get_minio_client()
    try:
        meta = minio.stat(row.object_key)
    except ObjectNotFound as e:
        # Mark failed but keep the row for operator inspection.
        sess.execute(
            _STMT_MARK_FAILED,
            {"id": image_id, "why": "object not found in MinIO"},
        )
        sess.commit()
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "UPLOAD_NOT_FOUND",
        ) from e

    # Size sanity: if the uploaded file is wildly different from what
    # the browser claimed, treat it as failed (defends against a
    # tampered upload).
    if meta["size"] != row.size_bytes:
        sess.execute(
            _STMT_MARK_FAILED,
            {"id": image_id, "why": "size mismatch"},
        )
        sess.commit()
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "SIZE_MISMATCH",
        )

    updated = sess.execute(
        _STMT_MARK_READY, {"id": image_id},
    ).first()
    sess.commit()
    if updated is None:
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            "failed to mark image ready",
        )

    public_url = minio.public_url_or_none(updated.object_key)
    if public_url is not None:
        return ConfirmOut(
            image_id=updated.id,
            status=updated.status,
            object_key=updated.object_key,
            size_bytes=updated.size_bytes,
            content_type=updated.content_type,
            url=public_url,
            url_expires_at=None,
        )
    url, expires_at = minio.presign_get(updated.object_key)
    return ConfirmOut(
        image_id=updated.id,
        status=updated.status,
        object_key=updated.object_key,
        size_bytes=updated.size_bytes,
        content_type=updated.content_type,
        url=url,
        url_expires_at=expires_at,
    )


# --- GET ----------------------------------------------------------------


@router.get("", response_model=list[ImageOut])
def list_images(
    sess: Annotated[Session, Depends(get_session)],
    channel_product_id: int | None = Query(default=None, ge=1),
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> list[ImageOut]:
    """List ready (non-deleted) images, optionally filtered by SPU."""
    rows = sess.execute(
        _STMT_LIST_READY_IMAGES,
        {"cp_id": channel_product_id, "limit": limit, "offset": offset},
    ).all()
    minio = get_minio_client()
    out: list[ImageOut] = []
    for r in rows:
        public_url = minio.public_url_or_none(r.object_key)
        if public_url is not None:
            url, expires_at = public_url, None
        else:
            url, expires_at = minio.presign_get(r.object_key)
        out.append(
            ImageOut(
                image_id=r.id,
                channel_product_id=r.channel_product_id,
                channel_product_external_id=r.external_product_id,
                object_key=r.object_key,
                filename=r.filename,
                content_type=r.content_type,
                size_bytes=r.size_bytes,
                uploaded_at=r.uploaded_at,
                uploaded_by_key_prefix=r.uploaded_by_prefix,
                url=url,
                url_expires_at=expires_at,
            )
        )
    return out


# --- DELETE --------------------------------------------------------------


@router.delete("/{image_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_image(
    image_id: int,
    request: Request,
    sess: Annotated[Session, Depends(get_session)],
) -> Response:
    """Soft-delete (mark deleted_at) and best-effort remove from MinIO."""
    require_role_at_least(request, "readwrite")

    row = sess.execute(
        _STMT_SOFT_DELETE, {"id": image_id},
    ).first()
    sess.commit()

    # Whether or not we hit an actual row, we try to remove the
    # MinIO object on a best-effort basis. Idempotent: a second
    # DELETE just finds no row and returns 204.
    if row is not None:
        minio = get_minio_client()
        try:
            minio.remove(row.object_key)
        except Exception as e:  # noqa: BLE001 — best-effort
            logger.warning(
                "minio remove failed for soft-deleted image",
                extra={"image_id": image_id, "object_key": row.object_key,
                       "error": str(e)},
            )

    return Response(status_code=status.HTTP_204_NO_CONTENT)
