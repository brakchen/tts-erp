"""MinIO client wrapper for SPU image storage.

All SDK calls in the project go through this module — no other module
imports ``minio`` directly. That keeps the surface narrow and makes
unit tests trivial (patch ``tts_erp_v2.storage.minio_client.Minio``).

Spec: tech-doc/procurement-ui-redesign.md §5.
"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from minio import Minio
from minio.error import S3Error

logger = logging.getLogger(__name__)


# 8 MiB hard cap (matches the procurement.spu_images CHECK constraint).
MAX_SIZE_BYTES = 8 * 1024 * 1024

# Allowed content types for direct browser uploads. Strict allowlist —
# if we need more types later we extend explicitly.
ALLOWED_CONTENT_TYPES: frozenset[str] = frozenset({
    "image/jpeg",
    "image/png",
    "image/webp",
    "image/gif",
})


class MinioConfigError(RuntimeError):
    """Raised when required env vars are missing or unparseable."""


class ObjectNotFound(LookupError):
    """Raised when ``stat_object`` reports ``NoSuchKey`` / ``NoSuchBucket``."""


@dataclass(frozen=True)
class _Config:
    endpoint: str
    access_key: str
    secret_key: str
    bucket: str
    secure: bool
    region: str
    presign_expiry: timedelta
    public_base_url: str | None


def _read_config() -> _Config:
    required = {
        "MINIO_ENDPOINT": os.environ.get("MINIO_ENDPOINT", "").strip(),
        "MINIO_ACCESS_KEY": os.environ.get("MINIO_ACCESS_KEY", "").strip(),
        "MINIO_SECRET_KEY": os.environ.get("MINIO_SECRET_KEY", "").strip(),
        "MINIO_BUCKET": os.environ.get("MINIO_BUCKET", "").strip(),
        "MINIO_SECURE": os.environ.get("MINIO_SECURE", "").strip().lower(),
        "MINIO_REGION": os.environ.get("MINIO_REGION", "").strip(),
        "MINIO_PRESIGN_EXPIRY_SECONDS":
            os.environ.get("MINIO_PRESIGN_EXPIRY_SECONDS", "").strip(),
    }
    missing = [k for k, v in required.items() if not v]
    if missing:
        raise MinioConfigError(
            "MinIO not configured: missing env vars " + ", ".join(missing)
            + ". See tech-doc/procurement-ui-redesign.md §5."
        )
    if required["MINIO_SECURE"] not in {"true", "false"}:
        raise MinioConfigError(
            "MINIO_SECURE must be 'true' or 'false' (got "
            f"{required['MINIO_SECURE']!r})."
        )
    try:
        expiry_seconds = int(required["MINIO_PRESIGN_EXPIRY_SECONDS"])
    except ValueError as e:
        raise MinioConfigError(
            "MINIO_PRESIGN_EXPIRY_SECONDS must be an integer (got "
            f"{required['MINIO_PRESIGN_EXPIRY_SECONDS']!r})."
        ) from e
    if expiry_seconds <= 0:
        raise MinioConfigError(
            "MINIO_PRESIGN_EXPIRY_SECONDS must be > 0 "
            f"(got {expiry_seconds})."
        )
    base = os.environ.get("MINIO_PUBLIC_BASE_URL", "").strip()
    return _Config(
        endpoint=required["MINIO_ENDPOINT"],
        access_key=required["MINIO_ACCESS_KEY"],
        secret_key=required["MINIO_SECRET_KEY"],
        bucket=required["MINIO_BUCKET"],
        secure=required["MINIO_SECURE"] == "true",
        region=required["MINIO_REGION"],
        presign_expiry=timedelta(seconds=expiry_seconds),
        public_base_url=base or None,
    )


class MinioClient:
    """Thin wrapper around the MinIO SDK.

    Construction is via :meth:`from_env` (env-driven) or the
    constructor (for tests that want to inject a pre-built ``Minio``
    SDK instance).

    The public surface is intentionally small:
    - :meth:`ensure_bucket`
    - :meth:`presign_put`
    - :meth:`presign_get`
    - :meth:`stat`
    - :meth:`remove`
    - :meth:`public_url_or_none`
    """

    def __init__(self, sdk: Minio, *, config: _Config) -> None:
        self._sdk = sdk
        self._config = config

    # -- factories --------------------------------------------------------

    @classmethod
    def from_env(cls) -> MinioClient:
        """Read required env vars and construct the SDK client."""
        cfg = _read_config()
        sdk = Minio(
            cfg.endpoint,
            access_key=cfg.access_key,
            secret_key=cfg.secret_key,
            secure=cfg.secure,
            region=cfg.region,
        )
        return cls(sdk, config=cfg)

    # -- accessors (for callers that need them) --------------------------

    @property
    def bucket(self) -> str:
        return self._config.bucket

    @property
    def secure(self) -> bool:
        return self._config.secure

    @property
    def default_expiry(self) -> timedelta:
        return self._config.presign_expiry

    # -- bucket lifecycle ------------------------------------------------

    def ensure_bucket(self) -> None:
        """Create the configured bucket if it doesn't exist.

        Idempotent — safe to call repeatedly (called once per app
        startup from the FastAPI lifespan hook).
        """
        if not self._sdk.bucket_exists(self._config.bucket):
            self._sdk.make_bucket(self._config.bucket, location=self._config.region)
            logger.info(
                "minio bucket created",
                extra={"bucket": self._config.bucket},
            )

    # -- presign ---------------------------------------------------------

    def presign_put(
        self,
        object_key: str,
        content_type: str,
        expiry: timedelta | None = None,
    ) -> str:
        """Return a presigned PUT URL the browser can upload to.

        SDK 7.2+: ``presigned_put_object`` takes only
        ``(bucket, object_name, expires)`` — presigned PUT does not
        sign Content-Type (that requires a POST policy), and
        ``response-content-type`` is a GET-only override param, so we
        pass no extra query params. The browser just PUTs the bytes.
        """
        return self._sdk.presigned_put_object(
            self._config.bucket,
            object_key,
            expires=expiry or self._config.presign_expiry,
        )

    def presign_get(
        self,
        object_key: str,
        expiry: timedelta | None = None,
    ) -> tuple[str, datetime]:
        """Return ``(url, expires_at)`` for a presigned GET."""
        url = self._sdk.presigned_get_object(
            self._config.bucket,
            object_key,
            expires=expiry or self._config.presign_expiry,
        )
        expires_at = datetime.now(UTC) + (
            expiry or self._config.presign_expiry
        )
        return url, expires_at

    # -- object inspection / removal ------------------------------------

    def stat(self, object_key: str) -> dict:
        """HEAD the object and return ``{size, content_type, etag}``.

        Raises :class:`ObjectNotFound` if the key doesn't exist.
        """
        try:
            obj = self._sdk.stat_object(self._config.bucket, object_key)
        except S3Error as e:
            if e.code in {"NoSuchKey", "NoSuchBucket"}:
                raise ObjectNotFound(object_key) from e
            raise
        return {
            "size": obj.size,
            "content_type": obj.content_type,
            "etag": obj.etag,
        }

    def remove(self, object_key: str) -> None:
        """Best-effort delete; swallows :class:`ObjectNotFound`.

        Other S3 errors propagate so the caller can decide whether to
        log them (the soft-delete path logs but does not fail).
        """
        try:
            self._sdk.remove_object(self._config.bucket, object_key)
        except S3Error as e:
            if e.code in {"NoSuchKey", "NoSuchBucket"}:
                return
            raise

    # -- public CDN URL --------------------------------------------------

    def public_url_or_none(self, object_key: str) -> str | None:
        """Return the public CDN URL if a base is configured, else None.

        When ``MINIO_PUBLIC_BASE_URL`` is set (e.g. a CDN in front of
        the bucket) we hand callers a plain URL — no presigning
        needed and no expiry. When unset, callers must use
        :meth:`presign_get` and pass the expiry back to the client.
        """
        if not self._config.public_base_url:
            return None
        base = self._config.public_base_url.rstrip("/")
        key = object_key.lstrip("/")
        return f"{base}/{key}"


# --- helpers ---------------------------------------------------------------


_SLUG_RE = re.compile(r"[^a-z0-9]+")
_SLUG_MAX = 64
_SLUG_FALLBACK = "image"


def slugify_filename(raw: str) -> str:
    """Lowercase / hyphen / strip; cap at 64 chars; default to ``image``.

    >>> slugify_filename("Packing-Slip-Front.JPG")
    'packing-slip-front'
    >>> slugify_filename("   ___   ")
    'image'
    >>> slugify_filename("中文.jpg")
    'image'
    """
    if not raw:
        return _SLUG_FALLBACK
    # Strip the extension first so we don't carry it through as a token.
    stem = raw.rsplit(".", 1)[0] if "." in raw else raw
    slug = _SLUG_RE.sub("-", stem.lower()).strip("-")
    if not slug:
        return _SLUG_FALLBACK
    return slug[:_SLUG_MAX]


def build_object_key(
    *,
    account_id: int,
    product_id: int,
    image_id: int,
    slug: str,
    ext: str,
    on_date: datetime,
) -> str:
    """Compose the S3/MinIO object key.

    Layout: ``shops/<acct>/spus/<spu>/<YYYY-MM-DD>/<id>-<slug>.<ext>``
    """
    clean_slug = slugify_filename(slug)
    clean_ext = ext.lstrip(".").lower() or "bin"
    day = on_date.strftime("%Y-%m-%d")
    return (
        f"shops/{account_id}/spus/{product_id}/{day}/"
        f"{image_id}-{clean_slug}.{clean_ext}"
    )
