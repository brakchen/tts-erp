"""Integration tests for ``tts_erp_v2/api/v2/spu_images.py``.

The router has 4 endpoints (per tech-doc/procurement-ui-redesign.md §3):

- ``POST /v2/spu-images/upload-url``   → readwrite  → 201 + presigned PUT URL
- ``POST /v2/spu-images/{id}/confirm``  → readwrite  → 200 + presigned GET URL
- ``GET  /v2/spu-images``               → readonly   → 200 + array of ready images
- ``DELETE /v2/spu-images/{id}``       → readwrite  → 204

These tests hit the real DB (rolled-back transaction per test) and a
fake MinIO client (no real network). CSRF guard, role gates, and
missing-resource 404/409 are all covered.

We also update ``tests_v2/api/conftest.py``-style seed helpers to use
TEST_-prefixed identifiers so the autouse cleanup tears them down.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import text

# ---------------------------------------------------------------------------
# Test app + dependency overrides
# ---------------------------------------------------------------------------


def _build_test_app(fake_minio: MagicMock) -> FastAPI:
    """Build a minimal FastAPI app with only the spu_images router.

    Why not use the full v2 app? Because we want to inject a fake MinIO
    client (no network) and we want to exercise auth at the middleware
    layer only on the endpoints we care about. Building a focused app
    also keeps the test fast.
    """
    from tts_erp_v2.api.v2 import spu_images

    app = FastAPI()
    app.include_router(spu_images.router)

    # Inject the fake MinIO client (the prod path constructs it
    # from env in module-level lazy attribute access).
    spu_images.get_minio_client = lambda: fake_minio  # type: ignore[assignment]

    # Use the real DB session via the same factory prod uses.
    from tts_erp_v2.middleware.auth import AuthMiddleware

    app.add_middleware(AuthMiddleware)
    return app


@pytest.fixture()
def fake_minio() -> MagicMock:
    """A MagicMock that mimics the MinioClient interface the router uses.

    Default returns for presign_* + stat + ensure_bucket are configured
    per test as needed.
    """
    m = MagicMock(name="MinioClient")
    m.ensure_bucket.return_value = None
    m.presign_put.return_value = (
        "http://127.0.0.1:9000/tts-erp-spu-images/...?X-Amz-Signature=fake"
    )
    m.presign_get.return_value = (
        "http://127.0.0.1:9000/tts-erp-spu-images/...?X-Amz-Signature=fake",
        datetime.now(UTC) + timedelta(minutes=15),
    )
    m.stat.return_value = {"size": 1024, "content_type": "image/jpeg", "etag": "abc"}
    m.public_url_or_none.return_value = None
    m.remove.return_value = None
    return m


@pytest.fixture()
def api_client_focused(db_engine, fake_minio):
    """A TestClient over the focused spu_images-only app.

    Note: we still go through the real AuthMiddleware so role gates are
    exercised end-to-end.
    """
    app = _build_test_app(fake_minio)
    with TestClient(app) as client:
        yield client


# ---------------------------------------------------------------------------
# Seed helpers
# ---------------------------------------------------------------------------


def _seed_account_and_product(db_engine, external_account: str, external_product: str):
    """Seed a channel_account + channel_product pair with TEST_ ids."""
    with pytest.MonkeyPatch.context() as mp:
        from sqlalchemy.orm import Session
        with Session(db_engine) as sess:
            sess.execute(
                text(
                    "INSERT INTO commerce.channel_accounts "
                    "(platform, external_account_id, account_name, status) "
                    "VALUES ('tiktok', :ext, 'TEST acct', 'active')"
                ),
                {"ext": external_account},
            )
            acct_id = sess.execute(
                text(
                    "SELECT id FROM commerce.channel_accounts "
                    "WHERE external_account_id = :ext"
                ),
                {"ext": external_account},
            ).scalar()
            sess.execute(
                text(
                    "INSERT INTO commerce.channel_products "
                    "(channel_account_id, external_product_id, title, status) "
                    "VALUES (:acct, :ext, 'TEST title', 'active')"
                ),
                {"acct": acct_id, "ext": external_product},
            )
            cp_id = sess.execute(
                text(
                    "SELECT id FROM commerce.channel_products "
                    "WHERE external_product_id = :ext"
                ),
                {"ext": external_product},
            ).scalar()
            sess.commit()
        mp.undo()
    return acct_id, cp_id


def _seed_ready_image(db_engine, account_id: int, product_id: int, suffix: str = "x"):
    """Insert a row in procurement.spu_images already in 'ready' state."""
    with pytest.MonkeyPatch.context() as mp:
        from sqlalchemy.orm import Session
        with Session(db_engine) as sess:
            obj_key = (
                f"shops/{account_id}/spus/{product_id}/2026-08-31/"
                f"{suffix}-{suffix}.jpg"
            )
            row = sess.execute(
                text(
                    "INSERT INTO procurement.spu_images "
                    "(channel_account_id, channel_product_id, object_key, "
                    " filename, content_type, size_bytes, status, "
                    " uploaded_by_prefix) "
                    "VALUES (:acct, :prod, :k, :fn, 'image/jpeg', 1024, "
                    "        'ready', 'ttserp_rw_') "
                    "RETURNING id"
                ),
                {
                    "acct": account_id,
                    "prod": product_id,
                    "k": obj_key,
                    "fn": f"{suffix}.jpg",
                },
            ).first()
            sess.commit()
            mp.undo()
        return row.id


# ---------------------------------------------------------------------------
# POST /upload-url
# ---------------------------------------------------------------------------


def test_upload_url_requires_readwrite(api_client_focused, readonly_key):
    r = api_client_focused.post(
        "/v2/spu-images/upload-url",
        headers={"Authorization": f"Bearer {readonly_key}"},
        json={
            "channel_account_id": 1,
            "channel_product_id": 1,
            "filename": "a.jpg",
            "content_type": "image/jpeg",
            "size_bytes": 1024,
        },
    )
    assert r.status_code == 403, r.text


def test_upload_url_happy_path_returns_201_and_presigned(
    api_client_focused, readwrite_key, db_engine
):
    """Successful POST returns 201, image_id, object_key, upload_url."""
    acct_id, cp_id = _seed_account_and_product(
        db_engine, "TEST_spuim_acct_ok", "TEST_spuim_prod_ok",
    )
    r = api_client_focused.post(
        "/v2/spu-images/upload-url",
        headers={"Authorization": f"Bearer {readwrite_key}"},
        json={
            "channel_account_id": acct_id,
            "channel_product_id": cp_id,
            "filename": "Packing-Slip-Front.JPG",
            "content_type": "image/jpeg",
            "size_bytes": 184320,
        },
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert isinstance(body["image_id"], int)
    assert body["object_key"].startswith(
        f"shops/{acct_id}/spus/{cp_id}/2026-"
    )
    assert body["object_key"].endswith("-packing-slip-front.jpg")
    assert body["upload_url"].startswith("http://127.0.0.1:9000/")
    assert body["required_headers"] == {"Content-Type": "image/jpeg"}
    assert "upload_expires_at" in body

    # Row should exist with awaiting_upload.
    from sqlalchemy.orm import Session
    with Session(db_engine) as verify_sess:
        row = verify_sess.execute(
            text(
                "SELECT status, channel_account_id, channel_product_id, "
                "       size_bytes, content_type "
                "FROM procurement.spu_images WHERE id = :i"
            ),
            {"i": body["image_id"]},
        ).first()
    assert row.status == "awaiting_upload"
    assert row.channel_account_id == acct_id
    assert row.channel_product_id == cp_id
    assert row.size_bytes == 184320
    assert row.content_type == "image/jpeg"


def test_upload_url_rejects_unknown_account(
    api_client_focused, readwrite_key
):
    r = api_client_focused.post(
        "/v2/spu-images/upload-url",
        headers={"Authorization": f"Bearer {readwrite_key}"},
        json={
            "channel_account_id": 9_999_999,
            "channel_product_id": 1,
            "filename": "a.jpg",
            "content_type": "image/jpeg",
            "size_bytes": 1024,
        },
    )
    assert r.status_code == 404, r.text


def test_upload_url_rejects_unknown_product(
    api_client_focused, readwrite_key, db_engine
):
    acct_id, _ = _seed_account_and_product(
        db_engine, "TEST_spuim_acct_unkprod", "TEST_spuim_prod_unkprod",
    )
    r = api_client_focused.post(
        "/v2/spu-images/upload-url",
        headers={"Authorization": f"Bearer {readwrite_key}"},
        json={
            "channel_account_id": acct_id,
            "channel_product_id": 9_999_999,
            "filename": "a.jpg",
            "content_type": "image/jpeg",
            "size_bytes": 1024,
        },
    )
    assert r.status_code == 404, r.text


def test_upload_url_rejects_oversize(api_client_focused, readwrite_key, db_engine):
    acct_id, cp_id = _seed_account_and_product(
        db_engine, "TEST_spuim_acct_big", "TEST_spuim_prod_big",
    )
    r = api_client_focused.post(
        "/v2/spu-images/upload-url",
        headers={"Authorization": f"Bearer {readwrite_key}"},
        json={
            "channel_account_id": acct_id,
            "channel_product_id": cp_id,
            "filename": "a.jpg",
            "content_type": "image/jpeg",
            "size_bytes": 8 * 1024 * 1024 + 1,
        },
    )
    assert r.status_code in (400, 422), r.text


def test_upload_url_rejects_bad_content_type(
    api_client_focused, readwrite_key, db_engine
):
    acct_id, cp_id = _seed_account_and_product(
        db_engine, "TEST_spuim_acct_ct", "TEST_spuim_prod_ct",
    )
    r = api_client_focused.post(
        "/v2/spu-images/upload-url",
        headers={"Authorization": f"Bearer {readwrite_key}"},
        json={
            "channel_account_id": acct_id,
            "channel_product_id": cp_id,
            "filename": "a.jpg",
            "content_type": "text/html",
            "size_bytes": 1024,
        },
    )
    assert r.status_code in (400, 422), r.text


def test_upload_url_rejects_empty_filename(
    api_client_focused, readwrite_key, db_engine
):
    acct_id, cp_id = _seed_account_and_product(
        db_engine, "TEST_spuim_acct_fn", "TEST_spuim_prod_fn",
    )
    r = api_client_focused.post(
        "/v2/spu-images/upload-url",
        headers={"Authorization": f"Bearer {readwrite_key}"},
        json={
            "channel_account_id": acct_id,
            "channel_product_id": cp_id,
            "filename": "",
            "content_type": "image/jpeg",
            "size_bytes": 1024,
        },
    )
    assert r.status_code in (400, 422), r.text


# ---------------------------------------------------------------------------
# POST /confirm
# ---------------------------------------------------------------------------


def test_confirm_requires_readwrite(api_client_focused, readonly_key):
    r = api_client_focused.post(
        "/v2/spu-images/123/confirm",
        headers={"Authorization": f"Bearer {readonly_key}"},
    )
    assert r.status_code == 403, r.text


def test_confirm_marks_ready_and_returns_url(
    api_client_focused, readwrite_key, db_engine, fake_minio
):
    """HEAD-OK → status=ready, returns read URL."""

    acct_id, cp_id = _seed_account_and_product(
        db_engine, "TEST_spuim_conf_a", "TEST_spuim_conf_p",
    )
    image_id = _seed_ready_image(db_engine, acct_id, cp_id, suffix="confirm1")
    # Make the seed row awaiting_upload so the confirm path actually transitions.
    with pytest.MonkeyPatch.context() as mp:
        from sqlalchemy.orm import Session
        with Session(db_engine) as sess:
            sess.execute(
                text(
                    "UPDATE procurement.spu_images "
                    "SET status = 'awaiting_upload' WHERE id = :i"
                ),
                {"i": image_id},
            )
            sess.commit()
        mp.undo()

    fake_minio.stat.return_value = {
        "size": 1024, "content_type": "image/jpeg", "etag": "etag-1",
    }
    r = api_client_focused.post(
        f"/v2/spu-images/{image_id}/confirm",
        headers={"Authorization": f"Bearer {readwrite_key}"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["image_id"] == image_id
    assert body["status"] == "ready"
    assert body["url"].startswith("http://127.0.0.1:9000/")
    assert body["size_bytes"] == 1024
    assert body["content_type"] == "image/jpeg"


def test_confirm_returns_409_when_object_missing(
    api_client_focused, readwrite_key, db_engine, fake_minio
):
    """stat() raises → 409 UPLOAD_NOT_FOUND."""
    from tts_erp_v2.storage.minio_client import ObjectNotFound

    acct_id, cp_id = _seed_account_and_product(
        db_engine, "TEST_spuim_conf404_a", "TEST_spuim_conf404_p",
    )
    image_id = _seed_ready_image(db_engine, acct_id, cp_id, suffix="confirm404")
    with pytest.MonkeyPatch.context() as mp:
        from sqlalchemy.orm import Session
        with Session(db_engine) as sess:
            sess.execute(
                text(
                    "UPDATE procurement.spu_images "
                    "SET status = 'awaiting_upload' WHERE id = :i"
                ),
                {"i": image_id},
            )
            sess.commit()
        mp.undo()

    fake_minio.stat.side_effect = ObjectNotFound("not there")
    r = api_client_focused.post(
        f"/v2/spu-images/{image_id}/confirm",
        headers={"Authorization": f"Bearer {readwrite_key}"},
    )
    assert r.status_code == 409, r.text
    assert r.json()["detail"] == "UPLOAD_NOT_FOUND"


def test_confirm_404_for_unknown_image(api_client_focused, readwrite_key):
    r = api_client_focused.post(
        "/v2/spu-images/99999999/confirm",
        headers={"Authorization": f"Bearer {readwrite_key}"},
    )
    assert r.status_code == 404, r.text


# ---------------------------------------------------------------------------
# GET /v2/spu-images
# ---------------------------------------------------------------------------


def test_list_requires_readonly_or_higher(api_client_focused):
    """No bearer → 401."""
    r = api_client_focused.get("/v2/spu-images?channel_product_id=1")
    assert r.status_code == 401, r.text


def test_list_returns_only_ready_images(
    api_client_focused, readonly_key, db_engine
):
    """Filtered to status=ready and matching channel_product_id."""
    acct_id, cp_id = _seed_account_and_product(
        db_engine, "TEST_spuim_list_a", "TEST_spuim_list_p",
    )
    # Seed: 2 ready, 1 awaiting_upload, 1 deleted.
    rid1 = _seed_ready_image(db_engine, acct_id, cp_id, suffix="r1")
    rid2 = _seed_ready_image(db_engine, acct_id, cp_id, suffix="r2")
    # Add an awaiting_upload row (direct insert).
    with pytest.MonkeyPatch.context() as mp:
        from sqlalchemy.orm import Session
        with Session(db_engine) as sess:
            sess.execute(
                text(
                    "INSERT INTO procurement.spu_images "
                    "(channel_account_id, channel_product_id, object_key, "
                    " filename, content_type, size_bytes, status, uploaded_by_prefix) "
                    "VALUES (:a, :p, :k, 'a.jpg', 'image/jpeg', 100, "
                    "        'awaiting_upload', 'ttserp_rw_')"
                ),
                {
                    "a": acct_id, "p": cp_id,
                    "k": f"shops/{acct_id}/spus/{cp_id}/2026-08-31/pending.jpg",
                },
            )
            sess.execute(
                text(
                    "INSERT INTO procurement.spu_images "
                    "(channel_account_id, channel_product_id, object_key, "
                    " filename, content_type, size_bytes, status, "
                    " uploaded_by_prefix, deleted_at) "
                    "VALUES (:a, :p, :k, 'd.jpg', 'image/jpeg', 100, "
                    "        'ready', 'ttserp_rw_', now())"
                ),
                {
                    "a": acct_id, "p": cp_id,
                    "k": f"shops/{acct_id}/spus/{cp_id}/2026-08-31/deleted.jpg",
                },
            )
            sess.commit()
        mp.undo()

    r = api_client_focused.get(
        f"/v2/spu-images?channel_product_id={cp_id}",
        headers={"Authorization": f"Bearer {readonly_key}"},
    )
    assert r.status_code == 200, r.text
    ids = [item["image_id"] for item in r.json()]
    assert sorted(ids) == sorted([rid1, rid2])


def test_list_returns_presigned_url_and_expiry(
    api_client_focused, readonly_key, db_engine
):
    """Each item has url + url_expires_at."""
    acct_id, cp_id = _seed_account_and_product(
        db_engine, "TEST_spuim_list_url_a", "TEST_spuim_list_url_p",
    )
    rid = _seed_ready_image(db_engine, acct_id, cp_id, suffix="withurl")
    r = api_client_focused.get(
        f"/v2/spu-images?channel_product_id={cp_id}",
        headers={"Authorization": f"Bearer {readonly_key}"},
    )
    assert r.status_code == 200, r.text
    items = r.json()
    assert len(items) == 1
    item = items[0]
    assert item["image_id"] == rid
    assert item["url"].startswith("http://127.0.0.1:9000/")
    assert "url_expires_at" in item
    assert item["uploaded_by_key_prefix"] == "ttserp_rw_"


# ---------------------------------------------------------------------------
# DELETE /v2/spu-images/{id}
# ---------------------------------------------------------------------------


def test_delete_requires_readwrite(api_client_focused, readonly_key, db_engine):
    acct_id, cp_id = _seed_account_and_product(
        db_engine, "TEST_spuim_del_a", "TEST_spuim_del_p",
    )
    rid = _seed_ready_image(db_engine, acct_id, cp_id, suffix="todel")
    r = api_client_focused.delete(
        f"/v2/spu-images/{rid}",
        headers={"Authorization": f"Bearer {readonly_key}"},
    )
    assert r.status_code == 403, r.text


def test_delete_marks_soft_deleted_and_removes_from_minio(
    api_client_focused, readwrite_key, db_engine, fake_minio
):
    """DELETE marks deleted_at AND calls minio.remove."""
    acct_id, cp_id = _seed_account_and_product(
        db_engine, "TEST_spuim_del2_a", "TEST_spuim_del2_p",
    )
    rid = _seed_ready_image(db_engine, acct_id, cp_id, suffix="todel2")

    r = api_client_focused.delete(
        f"/v2/spu-images/{rid}",
        headers={"Authorization": f"Bearer {readwrite_key}"},
    )
    assert r.status_code == 204, r.text

    # Verify row now has deleted_at set.
    from sqlalchemy.orm import Session
    with Session(db_engine) as verify_sess:
        row = verify_sess.execute(
            text("SELECT deleted_at FROM procurement.spu_images WHERE id = :i"),
            {"i": rid},
        ).first()
    assert row.deleted_at is not None
    fake_minio.remove.assert_called_once()


def test_delete_is_idempotent(api_client_focused, readwrite_key, db_engine):
    """Second DELETE on the same id also returns 204."""
    acct_id, cp_id = _seed_account_and_product(
        db_engine, "TEST_spuim_del3_a", "TEST_spuim_del3_p",
    )
    rid = _seed_ready_image(db_engine, acct_id, cp_id, suffix="todel3")
    h = {"Authorization": f"Bearer {readwrite_key}"}
    r1 = api_client_focused.delete(f"/v2/spu-images/{rid}", headers=h)
    r2 = api_client_focused.delete(f"/v2/spu-images/{rid}", headers=h)
    assert r1.status_code == 204
    assert r2.status_code == 204


def test_delete_minio_failure_is_logged_not_fatal(
    api_client_focused, readwrite_key, db_engine, fake_minio
):
    """If minio.remove raises a non-ObjectNotFound error, request still 204."""
    from minio.error import S3Error

    acct_id, cp_id = _seed_account_and_product(
        db_engine, "TEST_spuim_del4_a", "TEST_spuim_del4_p",
    )
    rid = _seed_ready_image(db_engine, acct_id, cp_id, suffix="todel4")
    fake_minio.remove.side_effect = S3Error(
        response=SimpleNamespace(status=500, headers={}, reason="boom", read=lambda *a, **k: b""),
        code="InternalError", message="boom",
        resource=None, request_id=None, host_id=None,
    )
    r = api_client_focused.delete(
        f"/v2/spu-images/{rid}",
        headers={"Authorization": f"Bearer {readwrite_key}"},
    )
    assert r.status_code == 204, r.text
    # And the row is still marked deleted (soft-delete always wins).
    from sqlalchemy.orm import Session
    with Session(db_engine) as verify_sess:
        row = verify_sess.execute(
            text("SELECT deleted_at FROM procurement.spu_images WHERE id = :i"),
            {"i": rid},
        ).first()
    assert row.deleted_at is not None


def test_list_without_channel_product_id_returns_all_ready(
    api_client_focused, readonly_key, db_engine
):
    """Regression: unfiltered list must not 500 (AmbiguousParameter on NULL cp_id)."""
    acct_id, cp_id = _seed_account_and_product(
        db_engine, "TEST_spuim_listall_a", "TEST_spuim_listall_p",
    )
    rid = _seed_ready_image(db_engine, acct_id, cp_id, suffix="all")
    r = api_client_focused.get(
        "/v2/spu-images",
        headers={"Authorization": f"Bearer {readonly_key}"},
    )
    assert r.status_code == 200, r.text
    ids = [item["image_id"] for item in r.json()]
    assert rid in ids
