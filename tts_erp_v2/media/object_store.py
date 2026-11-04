"""MinIO adapter for the media library.

The media library has its own bucket (``MEDIA_MINIO_BUCKET``, default
``tts-erp-media``) so its object lifecycle is independent from the SPU image
bucket and from the TikTok publish bucket. The SDK itself stays owned by
:mod:`tts_erp_v2.storage.minio_client` — this module only binds a different
bucket to the same client, exactly like the publish workflow does.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta
from typing import Protocol

from tts_erp_v2.storage.minio_client import (
    MinioClient,
    MinioConfigError,
    ObjectNotFound,
)

#: Bucket used when ``MEDIA_MINIO_BUCKET`` is not configured.
DEFAULT_MEDIA_BUCKET = "tts-erp-media"


class MediaObjectStore(Protocol):
    """The narrow object-store surface the media library needs."""

    def bucket(self) -> str: ...

    def presign_put(self, object_key: str, content_type: str) -> str: ...

    def presign_get(self, object_key: str) -> tuple[str, datetime]: ...

    def stat(self, object_key: str) -> dict: ...

    def remove(self, object_key: str) -> None: ...

    def ensure_bucket(self) -> None: ...

    def public_url_or_none(self, object_key: str) -> str | None: ...

    @property
    def default_expiry(self) -> timedelta: ...


class MinioMediaStore:
    """Bind :class:`MinioClient` to the media bucket."""

    def __init__(self, client: MinioClient) -> None:
        self._client = client

    @property
    def client(self) -> MinioClient:
        return self._client

    def bucket(self) -> str:
        return self._client.bucket

    def presign_put(self, object_key: str, content_type: str) -> str:
        return self._client.presign_put(object_key, content_type)

    def presign_get(self, object_key: str) -> tuple[str, datetime]:
        return self._client.presign_get(object_key)

    def stat(self, object_key: str) -> dict:
        return self._client.stat(object_key)

    def remove(self, object_key: str) -> None:
        self._client.remove(object_key)

    def ensure_bucket(self) -> None:
        self._client.ensure_bucket()

    def public_url_or_none(self, object_key: str) -> str | None:
        return self._client.public_url_or_none(object_key)

    @property
    def default_expiry(self) -> timedelta:
        return self._client.default_expiry


def media_store_from_env() -> MinioMediaStore:
    """Build the media store from the environment.

    ``MEDIA_MINIO_BUCKET`` selects the bucket; every other MinIO setting is
    shared with the rest of the project (``MINIO_ENDPOINT`` and friends).
    """
    bucket = os.environ.get("MEDIA_MINIO_BUCKET", DEFAULT_MEDIA_BUCKET).strip()
    if not bucket:
        raise MinioConfigError("MEDIA_MINIO_BUCKET must not be empty")
    return MinioMediaStore(MinioClient.from_env().with_bucket(bucket))


__all__ = [
    "DEFAULT_MEDIA_BUCKET",
    "MediaObjectStore",
    "MinioMediaStore",
    "ObjectNotFound",
    "media_store_from_env",
]
