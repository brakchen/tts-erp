"""MinIO adapter for private video objects."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
from typing import Protocol

from minio.error import S3Error

from tts_erp_v2.storage.minio_client import MinioClient, ObjectNotFound


class VideoObjectStore(Protocol):
    @property
    def bucket(self) -> str: ...
    def presign_put(self, key: str, content_type: str) -> str: ...
    def stat(self, key: str) -> dict: ...
    def download(self, key: str, destination: Path) -> str: ...
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

    def presign_put(self, key: str, content_type: str) -> str:
        return self._client.presign_put(key, content_type)

    def stat(self, key: str) -> dict:
        return self._client.stat(key)

    def download(self, key: str, destination: Path) -> str:
        destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        part = destination.with_suffix(destination.suffix + ".part")
        digest = hashlib.sha256()
        try:
            response = self._client._sdk.get_object(self._client.bucket, key)
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
            raise
        finally:
            part.unlink(missing_ok=True)

    def remove(self, key: str) -> None:
        self._client.remove(key)
