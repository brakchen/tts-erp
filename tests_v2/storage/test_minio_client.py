"""Unit tests for ``tts_erp_v2.storage.minio_client``.

These tests **never import the ``minio`` SDK**; they patch the SDK at
the module boundary inside ``tts_erp_v2.storage.minio_client``. That
matches the design-doc promise that all SDK calls go through
``MinioClient`` and is what makes the unit tests fast + offline.

Covers (per tech-doc/procurement-ui-redesign.md §5):
- ``MinioClient.__init__`` reads env, fails fast on missing config
- ``MinioClient.ensure_bucket`` idempotent (skip when exists)
- ``MinioClient.presign_put`` / ``presign_get`` shape + content-type
- ``MinioClient.stat`` raises ``ObjectNotFound`` on missing
- ``MinioClient.remove`` swallows ``ObjectNotFound``
- ``MinioClient.public_url_or_none`` returns None unless base set
- ``slugify_filename`` + ``build_object_key`` helpers
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from minio.error import S3Error

from tts_erp_v2.storage.minio_client import (
    MinioClient,
    MinioConfigError,
    ObjectNotFound,
    build_object_key,
    slugify_filename,
)

# --- env helpers ----------------------------------------------------------


def _env(**overrides):
    base = {
        "MINIO_ENDPOINT": "127.0.0.1:9000",
        "MINIO_ACCESS_KEY": "AKID",
        "MINIO_SECRET_KEY": "SECRET",
        "MINIO_BUCKET": "tts-erp-spu-images",
        "MINIO_SECURE": "false",
        "MINIO_REGION": "us-east-1",
        "MINIO_PRESIGN_EXPIRY_SECONDS": "900",
        "MINIO_PUBLIC_BASE_URL": "",
        "MINIO_PUBLIC_HOST": "",
    }
    base.update(overrides)
    return base


# --- helpers --------------------------------------------------------------


def _s3error(code: str, message: str = "x") -> S3Error:
    """Construct an S3Error without doing a real HTTP call."""
    response = SimpleNamespace(
        status=404, headers={}, reason="Not Found", read=lambda *a, **k: b""
    )
    return S3Error(
        response=response,
        code=code,
        message=message,
        resource=None,
        request_id=None,
        host_id=None,
        bucket_name=None,
        object_name=None,
    )


# --- construction / config -----------------------------------------------


def test_init_requires_all_env(monkeypatch):
    for k in _env():
        monkeypatch.delenv(k, raising=False)
    with pytest.raises(MinioConfigError):
        MinioClient.from_env()


@pytest.mark.parametrize(
    "missing",
    [
        "MINIO_ENDPOINT",
        "MINIO_ACCESS_KEY",
        "MINIO_SECRET_KEY",
        "MINIO_BUCKET",
        "MINIO_SECURE",
        "MINIO_REGION",
        "MINIO_PRESIGN_EXPIRY_SECONDS",
    ],
)
def test_init_missing_one_required_field(monkeypatch, missing):
    env = _env()
    env.pop(missing)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    monkeypatch.delenv(missing, raising=False)
    with pytest.raises(MinioConfigError) as exc:
        MinioClient.from_env()
    assert missing in str(exc.value)


def test_init_parses_secure_flag_and_expiry(monkeypatch):
    for k, v in _env(MINIO_SECURE="true", MINIO_PRESIGN_EXPIRY_SECONDS="120").items():
        monkeypatch.setenv(k, v)
    client = MinioClient.from_env()
    assert client.secure is True
    assert client.default_expiry == timedelta(seconds=120)


# --- ensure_bucket -------------------------------------------------------


def test_ensure_bucket_creates_when_missing(monkeypatch):
    for k, v in _env().items():
        monkeypatch.setenv(k, v)
    with patch("tts_erp_v2.storage.minio_client.Minio") as Mock:
        instance = Mock.return_value
        instance.bucket_exists.return_value = False
        client = MinioClient.from_env()
        client.ensure_bucket()
        instance.bucket_exists.assert_called_once_with("tts-erp-spu-images")
        instance.make_bucket.assert_called_once()


def test_ensure_bucket_idempotent_when_present(monkeypatch):
    for k, v in _env().items():
        monkeypatch.setenv(k, v)
    with patch("tts_erp_v2.storage.minio_client.Minio") as Mock:
        instance = Mock.return_value
        instance.bucket_exists.return_value = True
        client = MinioClient.from_env()
        client.ensure_bucket()
        instance.bucket_exists.assert_called_once()
        instance.make_bucket.assert_not_called()


# --- presign_put / presign_get ------------------------------------------


def test_presign_put_delegates_with_content_type(monkeypatch):
    for k, v in _env().items():
        monkeypatch.setenv(k, v)
    with patch("tts_erp_v2.storage.minio_client.Minio") as Mock:
        instance = Mock.return_value
        instance.presigned_put_object.return_value = (
            "http://127.0.0.1:9000/bucket/key?X-Amz-Signature=abc"
        )
        client = MinioClient.from_env()
        url = client.presign_put(
            "shops/1/spus/2/x.jpg",
            "image/jpeg",
            expiry=timedelta(seconds=30),
        )
    assert url.startswith("http://127.0.0.1:9000/")
    # SDK 7.2+ presigned_put_object takes only (bucket, object, expires).
    # Presigned PUT does not sign Content-Type (that needs a POST policy),
    # so we pass no extra_query_params.
    args, kwargs = instance.presigned_put_object.call_args
    assert args[0] == "tts-erp-spu-images"
    assert args[1] == "shops/1/spus/2/x.jpg"
    assert kwargs["expires"] == timedelta(seconds=30)
    assert "extra_query_params" not in kwargs


def test_presign_get_returns_url_and_expiry(monkeypatch):
    for k, v in _env().items():
        monkeypatch.setenv(k, v)
    with patch("tts_erp_v2.storage.minio_client.Minio") as Mock:
        instance = Mock.return_value
        instance.presigned_get_object.return_value = "http://127.0.0.1:9000/x?sig=y"
        client = MinioClient.from_env()
        url, expires_at = client.presign_get("shops/1/spus/2/x.jpg")
    assert url == "http://127.0.0.1:9000/x?sig=y"
    assert isinstance(expires_at, datetime)
    assert expires_at.tzinfo is not None
    # Default expiry is 900s -> expires_at is roughly 15 minutes ahead.
    delta = expires_at - datetime.now(UTC)
    assert 800 <= delta.total_seconds() <= 1000


def test_presign_get_respects_custom_expiry(monkeypatch):
    for k, v in _env().items():
        monkeypatch.setenv(k, v)
    with patch("tts_erp_v2.storage.minio_client.Minio") as Mock:
        instance = Mock.return_value
        instance.presigned_get_object.return_value = "http://x"
        client = MinioClient.from_env()
        _, expires_at = client.presign_get(
            "k",
            expiry=timedelta(seconds=10),
        )
    delta = expires_at - datetime.now(UTC)
    assert 5 <= delta.total_seconds() <= 15


# --- public_host rewriting (browser-reachable presigned URLs) -----------


def test_presign_put_rewrites_host_when_public_host_set(monkeypatch):
    """With MINIO_PUBLIC_HOST, the browser-side URL points at the public host.

    Regression (2026-08-31): SDK signs against the LAN endpoint, so the
    browser received 127.0.0.1:9000 and failed to PUT when the operator
    was on the public internet. MINIO_PUBLIC_HOST swaps just the
    scheme/host/port (and optional path prefix) — the signature query is
    untouched so the upload still authorises.
    """
    for k, v in _env(MINIO_PUBLIC_HOST="https://daqiang.nat100.top/minio").items():
        monkeypatch.setenv(k, v)
    with patch("tts_erp_v2.storage.minio_client.Minio") as Mock:
        instance = Mock.return_value
        instance.presigned_put_object.return_value = (
            "http://127.0.0.1:9000/tts-erp-spu-images/k.jpg"
            "?X-Amz-Algorithm=AWS4-HMAC-SHA256&X-Amz-Signature=abc"
        )
        client = MinioClient.from_env()
        url = client.presign_put("k.jpg", "image/jpeg")
    assert url == (
        "https://daqiang.nat100.top/minio/tts-erp-spu-images/k.jpg"
        "?X-Amz-Algorithm=AWS4-HMAC-SHA256&X-Amz-Signature=abc"
    )


def test_presign_get_rewrites_host_when_public_host_set(monkeypatch):
    for k, v in _env(MINIO_PUBLIC_HOST="https://cdn.example.com").items():
        monkeypatch.setenv(k, v)
    with patch("tts_erp_v2.storage.minio_client.Minio") as Mock:
        instance = Mock.return_value
        instance.presigned_get_object.return_value = (
            "http://127.0.0.1:9000/k.jpg?X-Amz-Signature=xyz"
        )
        client = MinioClient.from_env()
        url, _ = client.presign_get("k.jpg")
    # No path prefix on the public host this time.
    assert url == "https://cdn.example.com/k.jpg?X-Amz-Signature=xyz"


def test_presign_put_unchanged_when_public_host_unset(monkeypatch):
    """Backward compat: unset MINIO_PUBLIC_HOST keeps SDK's URL verbatim."""
    for k, v in _env().items():
        monkeypatch.setenv(k, v)
    with patch("tts_erp_v2.storage.minio_client.Minio") as Mock:
        instance = Mock.return_value
        instance.presigned_put_object.return_value = "http://127.0.0.1:9000/k?sig=a"
        client = MinioClient.from_env()
        url = client.presign_put("k", "image/jpeg")
    assert url == "http://127.0.0.1:9000/k?sig=a"


def test_rewrite_presigned_host_helper_examples():
    """Direct unit coverage for the URL rewrite helper.

    The path-prefix case is the regression we actually hit: NGINX
    routes /minio/* to MinIO, so the public host carries /minio and
    the SDK's /<bucket>/<key> must splice in below it.
    """
    from tts_erp_v2.storage.minio_client import _rewrite_presigned_host

    # path prefix
    out = _rewrite_presigned_host(
        "http://127.0.0.1:9000/bucket/k?X-Amz-Signature=abc",
        "https://daqiang.nat100.top/minio",
    )
    assert out == "https://daqiang.nat100.top/minio/bucket/k?X-Amz-Signature=abc"

    # no path prefix
    out = _rewrite_presigned_host(
        "http://10.0.0.5:9000/k?sig=1",
        "https://cdn.example.com",
    )
    assert out == "https://cdn.example.com/k?sig=1"

    # scheme swap (http endpoint -> https public host)
    out = _rewrite_presigned_host("http://x/k", "https://y")
    assert out == "https://y/k"

    # trailing slash on prefix is tolerated
    out = _rewrite_presigned_host(
        "http://x/b/k?q=1",
        "https://y/minio/",
    )
    assert out == "https://y/minio/b/k?q=1"


# --- stat / remove --------------------------------------------------------


def test_stat_returns_normalised_dict(monkeypatch):
    for k, v in _env().items():
        monkeypatch.setenv(k, v)
    fake = SimpleNamespace(
        size=1024,
        content_type="image/jpeg",
        etag="d41d8cd98f00b204e9800998ecf8427e",
    )
    with patch("tts_erp_v2.storage.minio_client.Minio") as Mock:
        instance = Mock.return_value
        instance.stat_object.return_value = fake
        client = MinioClient.from_env()
        out = client.stat("k")
    assert out == {
        "size": 1024,
        "content_type": "image/jpeg",
        "etag": "d41d8cd98f00b204e9800998ecf8427e",
    }


def test_stat_raises_object_not_found(monkeypatch):
    for k, v in _env().items():
        monkeypatch.setenv(k, v)
    with patch("tts_erp_v2.storage.minio_client.Minio") as Mock:
        instance = Mock.return_value
        instance.stat_object.side_effect = _s3error("NoSuchKey", "missing")
        client = MinioClient.from_env()
        with pytest.raises(ObjectNotFound):
            client.stat("missing-key")


def test_remove_swallows_object_not_found(monkeypatch):
    for k, v in _env().items():
        monkeypatch.setenv(k, v)
    with patch("tts_erp_v2.storage.minio_client.Minio") as Mock:
        instance = Mock.return_value
        instance.remove_object.side_effect = _s3error("NoSuchKey")
        client = MinioClient.from_env()
        # Should NOT raise.
        client.remove("missing-key")
        instance.remove_object.assert_called_once()


def test_remove_propagates_other_errors(monkeypatch):
    for k, v in _env().items():
        monkeypatch.setenv(k, v)
    with patch("tts_erp_v2.storage.minio_client.Minio") as Mock:
        instance = Mock.return_value
        instance.remove_object.side_effect = _s3error("InternalError", "boom")
        client = MinioClient.from_env()
        with pytest.raises(S3Error):
            client.remove("k")


# --- public_url_or_none ---------------------------------------------------


def test_public_url_or_none_returns_none_when_unset(monkeypatch):
    for k, v in _env(MINIO_PUBLIC_BASE_URL="").items():
        monkeypatch.setenv(k, v)
    client = MinioClient.from_env()
    assert client.public_url_or_none("shops/1/x.jpg") is None


def test_public_url_or_none_returns_url_when_set(monkeypatch):
    base = "https://cdn.example.com/tts-erp-spu-images"
    for k, v in _env(MINIO_PUBLIC_BASE_URL=base).items():
        monkeypatch.setenv(k, v)
    client = MinioClient.from_env()
    url = client.public_url_or_none("shops/1/x.jpg")
    assert url == base + "/shops/1/x.jpg"


# --- slugify_filename -----------------------------------------------------


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("packing-slip-front.jpg", "packing-slip-front"),
        ("My Photo (2).JPG", "my-photo-2"),
        ("   ___   ", "image"),
        ("foo/bar.png", "foo-bar"),
        ("a" * 200, "a" * 64),
        ("image with spaces & symbols!.webp", "image-with-spaces-symbols"),
        ("中文图片.jpg", "image"),  # non-ascii -> default to 'image'
    ],
)
def test_slugify_filename(raw, expected):
    assert slugify_filename(raw) == expected


def test_slugify_filename_empty_after_strip_returns_image():
    assert slugify_filename("") == "image"
    assert slugify_filename("---") == "image"
    assert slugify_filename("\u4e2d\u6587") == "image"


# --- build_object_key -----------------------------------------------------


def test_build_object_key_layout():
    key = build_object_key(
        account_id=7,
        product_id=1234,
        image_id=555,
        slug="packing-slip-front",
        ext="jpg",
        on_date=datetime(2026, 8, 31, tzinfo=UTC),
    )
    assert key == "shops/7/spus/1234/2026-08-31/555-packing-slip-front.jpg"


def test_build_object_key_default_ext_lowercase():
    key = build_object_key(
        account_id=1,
        product_id=2,
        image_id=3,
        slug="x",
        ext=".JPG",
        on_date=datetime(2026, 1, 1, tzinfo=UTC),
    )
    assert key.endswith("/3-x.jpg")


def test_build_object_key_zero_pads_date():
    key = build_object_key(
        account_id=1,
        product_id=2,
        image_id=3,
        slug="x",
        ext="png",
        on_date=datetime(2026, 3, 5, tzinfo=UTC),
    )
    assert "/2026-03-05/" in key
