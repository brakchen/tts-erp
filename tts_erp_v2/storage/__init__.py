"""tts_erp_v2.storage — SPU image storage backed by MinIO.

Public surface:
- ``MinioClient`` — thin wrapper around the ``minio`` SDK; the only
  module that imports ``minio`` (so tests can mock at this boundary).
- ``ObjectNotFound`` — re-exported for convenience.
"""
from tts_erp_v2.storage.minio_client import (
    MinioClient,
    MinioConfigError,
    ObjectNotFound,
    build_object_key,
    slugify_filename,
)

__all__ = [
    "MinioClient",
    "MinioConfigError",
    "ObjectNotFound",
    "build_object_key",
    "slugify_filename",
]
