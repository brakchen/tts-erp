"""MinIO adapter for private video objects."""

from __future__ import annotations

import hashlib
import os
from datetime import timedelta
from pathlib import Path
from typing import Protocol

from minio.error import S3Error

from tts_erp_v2.storage.minio_client import MinioClient, ObjectNotFound


class ObjectVersionMismatch(RuntimeError):
    """The object no longer matches the version confirmed by the operator."""


class VideoObjectStore(Protocol):
    @property
    def bucket(self) -> str: ...

    @property
    def default_expiry(self) -> timedelta: ...
    def presign_put(self, key: str, content_type: str) -> str: ...
    def stat(self, key: str) -> dict: ...
    def download(self, key: str, destination: Path, expected_etag: str) -> str: ...
    def check_available(self) -> None: ...
    def remove(self, key: str) -> None: ...


class MinioVideoStore:
    """Only exposes operations needed by the publishing workflow."""

    def __init__(
        self, client: MinioClient, *, expected_bucket: str | None = None
    ) -> None:
        expected = (
            expected_bucket
            or os.environ.get("TIKTOK_PUBLISH_MINIO_BUCKET", "tiktok-video").strip()
        )
        if not expected or client.bucket != expected:
            raise ValueError("PUBLISH_BUCKET_MISMATCH")
        self._client = client

    @property
    def bucket(self) -> str:
        return self._client.bucket

    @property
    def default_expiry(self) -> timedelta:
        return self._client.default_expiry

    def presign_put(self, key: str, content_type: str) -> str:
        return self._client.presign_put(key, content_type)

    def stat(self, key: str) -> dict:
        return self._client.stat(key)

    def download(self, key: str, destination: Path, expected_etag: str) -> str:
        destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        part = destination.with_suffix(destination.suffix + ".part")
        digest = hashlib.sha256()
        etag = expected_etag.strip().strip('"')
        if not etag:
            raise ObjectVersionMismatch("CONFIRMED_OBJECT_ETAG_MISSING")
        try:
            response = self._client._sdk.get_object(
                self._client.bucket,
                key,
                request_headers={"If-Match": f'"{etag}"'},
            )
            with part.open("wb") as handle:
                for chunk in response.stream(1024 * 1024):
                    handle.write(chunk)
                    digest.update(chunk)
                handle.flush()
                os.fsync(handle.fileno())
            response.close()
            response.release_conn()
            part.replace(destination)
            destination.chmod(0o600)
            return digest.hexdigest()
        except S3Error as exc:
            if exc.code in {"NoSuchKey", "NoSuchBucket"}:
                raise ObjectNotFound(key) from exc
            if exc.code in {"PreconditionFailed", "InvalidRequest"}:
                raise ObjectVersionMismatch("CONFIRMED_OBJECT_REPLACED") from exc
            raise
        finally:
            part.unlink(missing_ok=True)

    def check_available(self) -> None:
        if not self._client._sdk.bucket_exists(self._client.bucket):
            raise RuntimeError("PUBLISH_BUCKET_UNAVAILABLE")

    def remove(self, key: str) -> None:
        self._client.remove(key)
