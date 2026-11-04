"""tts_erp_v2.media — global media asset library and digital humans.

Public surface:
- ``domain`` — asset kinds, upload limits, object-key layout.
- ``object_store`` — the MinIO adapter bound to the media bucket.
- ``repository`` — short-transaction persistence for assets and personas.
"""

from tts_erp_v2.media.domain import (
    ALLOWED_IMAGE_CONTENT_TYPES,
    ALLOWED_VIDEO_CONTENT_TYPES,
    AssetKind,
    AssetStatus,
    DigitalHumanStatus,
    ObjectKey,
    build_object_key,
    kind_for_content_type,
    max_bytes_for,
    slugify_filename,
)

__all__ = [
    "ALLOWED_IMAGE_CONTENT_TYPES",
    "ALLOWED_VIDEO_CONTENT_TYPES",
    "AssetKind",
    "AssetStatus",
    "DigitalHumanStatus",
    "ObjectKey",
    "build_object_key",
    "kind_for_content_type",
    "max_bytes_for",
    "slugify_filename",
]
