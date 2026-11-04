"""Pure media-asset vocabulary: kinds, upload limits and object-key layout.

The media library is a **global** pool: assets are not partitioned by shop and
a digital human may reference the same asset from several personas. Nothing in
this module touches the database or the object store — it only fixes the
vocabulary that the API, the ORM models and the migration agree on.

Owner: ``tts_erp_v2/media`` (see docs/design/media-asset-library.md).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum


class AssetKind(StrEnum):
    """The two media families the library stores."""

    IMAGE = "image"
    VIDEO = "video"


class AssetStatus(StrEnum):
    """Lifecycle of one asset row.

    ``AWAITING_UPLOAD`` → the ticket was issued but the browser has not yet
    confirmed the object. ``READY`` → the object exists in the bucket and its
    size matched the declared size. ``FAILED`` → the object is missing or its
    size mismatched; the row is kept for operator inspection.
    """

    AWAITING_UPLOAD = "awaiting_upload"
    READY = "ready"
    FAILED = "failed"


class DigitalHumanStatus(StrEnum):
    """Lifecycle of one digital-human (persona) record."""

    ACTIVE = "active"
    ARCHIVED = "archived"


# --- Upload allowlist -------------------------------------------------------

#: Image content types accepted by the browser upload endpoint.
ALLOWED_IMAGE_CONTENT_TYPES: frozenset[str] = frozenset(
    {
        "image/jpeg",
        "image/png",
        "image/webp",
        "image/gif",
    }
)

#: Video content types accepted by the browser upload endpoint.
ALLOWED_VIDEO_CONTENT_TYPES: frozenset[str] = frozenset(
    {
        "video/mp4",
        "video/webm",
        "video/quicktime",
    }
)

#: Hard cap for a single image (20 MiB).
MAX_IMAGE_BYTES = 20 * 1024 * 1024

#: Hard cap for a single video (200 MiB).
MAX_VIDEO_BYTES = 200 * 1024 * 1024

#: Extension per content type. The browser sends its own filename, but the
#: stored object key always uses this canonical extension so a renamed file
#: cannot claim a content type it does not have.
CONTENT_TYPE_EXTENSIONS: dict[str, str] = {
    "image/jpeg": "jpg",
    "image/png": "png",
    "image/webp": "webp",
    "image/gif": "gif",
    "video/mp4": "mp4",
    "video/webm": "webm",
    "video/quicktime": "mov",
}


def kind_for_content_type(content_type: str) -> AssetKind | None:
    """Return the asset kind for a content type, or ``None`` when rejected."""
    if content_type in ALLOWED_IMAGE_CONTENT_TYPES:
        return AssetKind.IMAGE
    if content_type in ALLOWED_VIDEO_CONTENT_TYPES:
        return AssetKind.VIDEO
    return None


def max_bytes_for(kind: AssetKind) -> int:
    """Return the size cap that applies to ``kind``."""
    if kind is AssetKind.IMAGE:
        return MAX_IMAGE_BYTES
    return MAX_VIDEO_BYTES


def allowed_content_types() -> tuple[str, ...]:
    """Every accepted content type, sorted — used for error messages."""
    return tuple(
        sorted(ALLOWED_IMAGE_CONTENT_TYPES | ALLOWED_VIDEO_CONTENT_TYPES)
    )


# --- Object key layout ------------------------------------------------------

_SLUG_RE = re.compile(r"[^a-z0-9]+")
_SLUG_MAX = 64
_SLUG_FALLBACK = "asset"


def slugify_filename(raw: str) -> str:
    """Lowercase / hyphen / strip; cap at 64 chars; default to ``asset``.

    >>> slugify_filename("Product-Shot-01.JPG")
    'product-shot-01'
    >>> slugify_filename("   ___   ")
    'asset'
    >>> slugify_filename("中文.mp4")
    'asset'
    """
    if not raw:
        return _SLUG_FALLBACK
    stem = raw.rsplit(".", 1)[0] if "." in raw else raw
    slug = _SLUG_RE.sub("-", stem.lower()).strip("-")
    if not slug:
        return _SLUG_FALLBACK
    return slug[:_SLUG_MAX]


@dataclass(frozen=True, slots=True)
class ObjectKey:
    """The object key and bucket one asset is stored under."""

    bucket: str
    key: str


def build_object_key(
    *,
    bucket: str,
    asset_id: int,
    filename: str,
    content_type: str,
    on_date: datetime | None = None,
) -> ObjectKey:
    """Compose the MinIO object key for one asset.

    Layout: ``assets/<YYYY-MM-DD>/<id>-<slug>.<ext>``. The library is global,
    so the key carries no shop segment; the asset id keeps keys unique and the
    date prefix keeps directories browsable.
    """
    day = (on_date or datetime.now(UTC)).strftime("%Y-%m-%d")
    slug = slugify_filename(filename)
    ext = CONTENT_TYPE_EXTENSIONS.get(content_type, "bin")
    return ObjectKey(
        bucket=bucket,
        key=f"assets/{day}/{asset_id}-{slug}.{ext}",
    )
