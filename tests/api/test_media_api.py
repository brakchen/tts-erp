"""Integration tests for ``tts_erp_v2/api/v2/media.py``.

The router exposes the global media asset library and digital humans:

- ``POST   /v2/media/assets/upload-url``              → readwrite → 201 + presigned PUT
- ``POST   /v2/media/assets/{id}/confirm``             → readwrite → 200 + presigned GET
- ``GET    /v2/media/assets``                          → readonly  → 200 + page
- ``GET    /v2/media/assets/tags``                     → readonly  → 200 + labels
- ``GET    /v2/media/assets/{id}``                     → readonly  → 200
- ``PATCH  /v2/media/assets/{id}``                     → readwrite → 200
- ``DELETE /v2/media/assets/{id}``                     → readwrite → 204
- ``GET    /v2/media/digital-humans``                  → readonly  → 200
- ``POST   /v2/media/digital-humans``                  → readwrite → 201
- ``GET    /v2/media/digital-humans/{id}``             → readonly  → 200
- ``PATCH  /v2/media/digital-humans/{id}``             → readwrite → 200
- ``DELETE /v2/media/digital-humans/{id}``             → readwrite → 204
- ``POST   /v2/media/digital-humans/{id}/assets``      → readwrite → 200
- ``DELETE /v2/media/digital-humans/{id}/assets/{aid}`` → readwrite → 204

These tests hit the real DB (rolled-back transaction per test) and a fake
object store — no real network. Role gates, the CSRF guard, upload validation
and the many-to-many persona link are covered.
"""

from __future__ import annotations

from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Event
from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

# 与其他 tests/api 用例一致：归入 domain_api，否则 `scripts/test.sh api <file>`
# 的 `-m domain_api` 过滤会把本文件整体静默反选（exit 5）。
pytestmark = [pytest.mark.domain_api, pytest.mark.layer_integration]

#: Every row this file creates carries a TEST_ marker so the local cleanup
#: below can remove exactly what it wrote. The media library is a global
#: pool, so the marker — not a shop scope — is what bounds the deletion.
TEST_MARKER = "TEST_"


@pytest.fixture(autouse=True)
def _wipe_media_test_rows(db_engine):
    """Remove the media rows this file created, before and after each test.

    The shared ``tests/api/conftest.py`` wipe covers the tables of the lanes
    that existed when it was written; the media schema is new, so this file
    owns its own cleanup instead of widening that shared contract.
    """

    def wipe() -> None:
        with db_engine.begin() as conn:
            conn.execute(
                text(
                    "DELETE FROM media.digital_human_assets "
                    "WHERE asset_id IN (SELECT id FROM media.media_assets "
                    "WHERE filename LIKE 'TEST_%') "
                    "OR digital_human_id IN (SELECT id FROM media.digital_humans "
                    "WHERE name LIKE 'TEST_%')"
                )
            )
            conn.execute(
                text("DELETE FROM media.media_assets WHERE filename LIKE 'TEST_%'")
            )
            conn.execute(
                text("DELETE FROM media.digital_humans WHERE name LIKE 'TEST_%'")
            )

    wipe()
    yield
    wipe()


def _build_test_app(fake_store: MagicMock) -> FastAPI:
    """Build a minimal FastAPI app with only the media router."""
    from tts_erp_v2.api.v2 import media

    app = FastAPI()
    app.include_router(media.router)
    media.get_store = lambda: fake_store  # type: ignore[assignment]

    from tts_erp_v2.middleware.auth import AuthMiddleware

    app.add_middleware(AuthMiddleware)
    return app


@pytest.fixture()
def fake_store() -> MagicMock:
    """A MagicMock mimicking the MediaObjectStore surface the router uses."""
    store = MagicMock(name="MediaObjectStore")
    store.bucket.return_value = "tts-erp-media"
    store.default_expiry = timedelta(minutes=15)
    store.presign_put.return_value = (
        "http://127.0.0.1:9000/tts-erp-media/assets/2026-11-03/1-a.jpg"
        "?X-Amz-Signature=fake"
    )
    store.presign_get.return_value = (
        (
            "http://127.0.0.1:9000/tts-erp-media/assets/2026-11-03/1-a.jpg"
            "?X-Amz-Signature=fake"
        ),
        datetime.now(UTC) + timedelta(minutes=15),
    )
    store.public_url_or_none.return_value = None
    store.stat.return_value = {
        "size": 1024,
        "content_type": "image/jpeg",
        "etag": "abc",
    }
    store.remove.return_value = None
    store.ensure_bucket.return_value = None
    return store


@pytest.fixture()
def api_client_focused(fake_store) -> Iterator[TestClient]:
    app = _build_test_app(fake_store)
    with TestClient(app) as client:
        yield client


# ---------------------------------------------------------------------------
# POST /v2/media/assets/upload-url
# ---------------------------------------------------------------------------


def test_upload_url_requires_readwrite(api_client_focused, readonly_key):
    r = api_client_focused.post(
        "/v2/media/assets/upload-url",
        headers={"Authorization": f"Bearer {readonly_key}"},
        json={
            "filename": "TEST_a.jpg",
            "contentType": "image/jpeg",
            "sizeBytes": 1024,
        },
    )
    assert r.status_code == 403, r.text


def test_upload_url_happy_path(api_client_focused, readwrite_key):
    r = api_client_focused.post(
        "/v2/media/assets/upload-url",
        headers={"Authorization": f"Bearer {readwrite_key}"},
        json={
            "filename": "TEST_Packing-Slip-Front.JPG",
            "contentType": "image/jpeg",
            "sizeBytes": 184320,
        },
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert isinstance(body["asset_id"], int)
    assert body["object_key"].startswith("assets/")
    assert body["object_key"].endswith("-test-packing-slip-front.jpg")
    assert body["upload_url"].startswith("http://127.0.0.1:9000/")
    assert body["required_headers"] == {"Content-Type": "image/jpeg"}
    assert "upload_expires_at" in body


def test_upload_url_rejects_unknown_content_type(api_client_focused, readwrite_key):
    r = api_client_focused.post(
        "/v2/media/assets/upload-url",
        headers={"Authorization": f"Bearer {readwrite_key}"},
        json={
            "filename": "TEST_payload.html",
            "contentType": "text/html",
            "sizeBytes": 1024,
        },
    )
    assert r.status_code == 400, r.text


def test_upload_url_rejects_oversize_image(api_client_focused, readwrite_key):
    r = api_client_focused.post(
        "/v2/media/assets/upload-url",
        headers={"Authorization": f"Bearer {readwrite_key}"},
        json={
            "filename": "TEST_huge.jpg",
            "contentType": "image/jpeg",
            "sizeBytes": 20 * 1024 * 1024 + 1,
        },
    )
    assert r.status_code == 400, r.text


def test_upload_url_accepts_video(api_client_focused, readwrite_key):
    r = api_client_focused.post(
        "/v2/media/assets/upload-url",
        headers={"Authorization": f"Bearer {readwrite_key}"},
        json={
            "filename": "TEST_clip.mp4",
            "contentType": "video/mp4",
            "sizeBytes": 50 * 1024 * 1024,
        },
    )
    assert r.status_code == 201, r.text
    assert r.json()["object_key"].endswith(".mp4")


def test_upload_url_rejects_oversize_video(api_client_focused, readwrite_key):
    r = api_client_focused.post(
        "/v2/media/assets/upload-url",
        headers={"Authorization": f"Bearer {readwrite_key}"},
        json={
            "filename": "TEST_huge.mp4",
            "contentType": "video/mp4",
            "sizeBytes": 200 * 1024 * 1024 + 1,
        },
    )
    assert r.status_code == 400, r.text


# ---------------------------------------------------------------------------
# POST /v2/media/assets/{id}/confirm
# ---------------------------------------------------------------------------


def _upload_one(api_client_focused, key: str, **body) -> int:
    payload = {"filename": "TEST_a.jpg", "contentType": "image/jpeg", "sizeBytes": 1024}
    payload.update(body)
    r = api_client_focused.post(
        "/v2/media/assets/upload-url",
        headers={"Authorization": f"Bearer {key}"},
        json=payload,
    )
    assert r.status_code == 201, r.text
    return int(r.json()["asset_id"])


def test_failed_ticket_signing_does_not_leave_an_asset(
    api_client_focused, readwrite_key, fake_store
):
    fake_store.presign_put.side_effect = RuntimeError("TEST upload signing unavailable")
    filename = "TEST_ticket_signing_failure.jpg"
    with pytest.raises(RuntimeError, match="TEST upload signing unavailable"):
        _upload_one(
            api_client_focused,
            readwrite_key,
            filename=filename,
            tags=["TEST_ticket_orphan"],
        )

    listing = api_client_focused.get(
        "/v2/media/assets",
        headers={"Authorization": f"Bearer {readwrite_key}"},
        params={"keyword": filename},
    )
    assert listing.status_code == 200
    assert listing.json()["total"] == 0


def test_upload_ticket_saves_normalized_tags_before_confirmation(
    api_client_focused, readwrite_key, db_engine
):
    headers = {"Authorization": f"Bearer {readwrite_key}"}
    asset_id = _upload_one(
        api_client_focused,
        readwrite_key,
        tags=["  TEST_upload  tag ", "TEST_upload\ttag", "", "  ", "TEST_second"],
    )
    expected = ["TEST_upload tag", "TEST_second"]
    with db_engine.connect() as conn:
        assert (
            conn.scalar(
                text("SELECT tags FROM media.media_assets WHERE id = :id"),
                {"id": asset_id},
            )
            == expected
        )
    path = f"/v2/media/assets/{asset_id}"
    pending = api_client_focused.get(path, headers=headers)
    assert pending.json()["tags"] == expected
    assert pending.json()["status"] == "awaiting_upload"
    assert (
        api_client_focused.post(f"{path}/confirm", headers=headers).status_code == 200
    )
    confirmed = api_client_focused.get(path, headers=headers).json()
    assert confirmed["status"] == "ready"
    assert confirmed["tags"] == expected


def test_confirm_marks_ready(api_client_focused, readwrite_key):
    asset_id = _upload_one(api_client_focused, readwrite_key)
    r = api_client_focused.post(
        f"/v2/media/assets/{asset_id}/confirm",
        headers={"Authorization": f"Bearer {readwrite_key}"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "ready"


def test_confirm_retry_survives_storage_and_preview_failures(
    api_client_focused, readwrite_key, fake_store
):
    from tts_erp_v2.media.object_store import ObjectNotFound

    headers = {"Authorization": f"Bearer {readwrite_key}"}
    asset_id = _upload_one(api_client_focused, readwrite_key)
    path = f"/v2/media/assets/{asset_id}"
    fake_store.stat.side_effect = ObjectNotFound("TEST_upload_pending")
    missing = api_client_focused.post(f"{path}/confirm", headers=headers)
    assert missing.status_code == 409
    assert missing.json()["detail"] == "UPLOAD_NOT_FOUND"

    fake_store.stat.side_effect = None
    fake_store.presign_get.side_effect = RuntimeError(
        "TEST preview signing unavailable"
    )
    confirmed = api_client_focused.post(f"{path}/confirm", headers=headers)
    assert confirmed.status_code == 200
    assert confirmed.json()["asset_id"] == asset_id
    assert confirmed.json()["status"] == "ready"
    assert confirmed.json()["url"] is None
    before = api_client_focused.get(path, headers=headers).json()
    assert before["uploaded_at"] is not None
    assert before["failure_reason"] is None

    fake_store.stat.side_effect = ObjectNotFound("TEST replay must not HEAD")
    replayed = api_client_focused.post(f"{path}/confirm", headers=headers)
    assert replayed.status_code == 200
    assert replayed.json()["status"] == "ready"
    assert fake_store.stat.call_count == 2
    after = api_client_focused.get(path, headers=headers).json()
    assert after["uploaded_at"] == before["uploaded_at"]


def test_concurrent_missing_result_does_not_downgrade_confirmed_asset(
    api_client_focused, readwrite_key, fake_store
):
    from tts_erp_v2.media.object_store import ObjectNotFound

    headers = {"Authorization": f"Bearer {readwrite_key}"}
    asset_id = _upload_one(api_client_focused, readwrite_key)
    path = f"/v2/media/assets/{asset_id}/confirm"
    started = Event()
    release = Event()

    def stat(_object_key: str) -> dict[str, int]:
        if started.is_set():
            return {"size": 1024}
        started.set()
        assert release.wait(timeout=10), "TEST concurrent confirmation did not finish"
        raise ObjectNotFound("TEST stale missing result")

    fake_store.stat.side_effect = stat
    with ThreadPoolExecutor(max_workers=1) as executor:
        stale = executor.submit(api_client_focused.post, path, headers=headers)
        try:
            assert started.wait(timeout=10), "TEST first confirmation did not start"
            confirmed = api_client_focused.post(path, headers=headers)
            assert confirmed.status_code == 200
        finally:
            release.set()
        replayed = stale.result(timeout=10)

    assert replayed.status_code == 200
    assert replayed.json()["status"] == "ready"


def test_confirm_rejects_size_mismatch(api_client_focused, readwrite_key, fake_store):
    asset_id = _upload_one(api_client_focused, readwrite_key)
    fake_store.stat.return_value = {
        "size": 999999,
        "content_type": "image/jpeg",
        "etag": "abc",
    }
    r = api_client_focused.post(
        f"/v2/media/assets/{asset_id}/confirm",
        headers={"Authorization": f"Bearer {readwrite_key}"},
    )
    assert r.status_code == 409, r.text


def test_confirm_unknown_asset_is_404(api_client_focused, readwrite_key):
    r = api_client_focused.post(
        "/v2/media/assets/99999999/confirm",
        headers={"Authorization": f"Bearer {readwrite_key}"},
    )
    assert r.status_code == 404, r.text


# ---------------------------------------------------------------------------
# GET /v2/media/assets
# ---------------------------------------------------------------------------


def test_list_assets_requires_readonly(api_client_focused, readwrite_key):
    # readwrite satisfies readonly, so a readwrite key must still succeed.
    r = api_client_focused.get(
        "/v2/media/assets",
        headers={"Authorization": f"Bearer {readwrite_key}"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["items"] == []
    assert body["total"] == 0


def test_list_assets_returns_seeded_asset(api_client_focused, readwrite_key):
    asset_id = _upload_one(api_client_focused, readwrite_key)
    api_client_focused.post(
        f"/v2/media/assets/{asset_id}/confirm",
        headers={"Authorization": f"Bearer {readwrite_key}"},
    )
    r = api_client_focused.get(
        "/v2/media/assets",
        headers={"Authorization": f"Bearer {readwrite_key}"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["total"] >= 1
    assert any(item["asset_id"] == asset_id for item in body["items"])


def test_list_assets_rejects_unknown_kind(api_client_focused, readwrite_key):
    r = api_client_focused.get(
        "/v2/media/assets",
        headers={"Authorization": f"Bearer {readwrite_key}"},
        params={"kind": "audio"},
    )
    assert r.status_code == 400, r.text


def test_tag_candidates_are_readonly_deduplicated_and_ready_only(
    api_client_focused, readwrite_key, readonly_key, fake_store, db_engine
):
    headers = {"Authorization": f"Bearer {readwrite_key}"}
    long_tag = "TEST_" + "long_label_" * 15
    ready_ids = [
        _upload_one(
            api_client_focused,
            readwrite_key,
            filename=f"TEST_tag_candidate_{index}.jpg",
            tags=tags,
        )
        for index, tags in enumerate(
            ([" TEST_shared ", "TEST_shared", "TEST_multi\t line", ""], [long_tag])
        )
    ]
    for asset_id in ready_ids:
        assert (
            api_client_focused.post(
                f"/v2/media/assets/{asset_id}/confirm", headers=headers
            ).status_code
            == 200
        )
    # Historical tags are normalized for reading/filtering without a migration.
    with db_engine.begin() as conn:
        conn.execute(
            text("UPDATE media.media_assets SET tags = :tags WHERE id = :id"),
            {
                "tags": [long_tag, "  TEST_legacy\t tag ", "TEST_shared", " "],
                "id": ready_ids[1],
            },
        )
    _upload_one(api_client_focused, readwrite_key, tags=["TEST_pending_only"])
    failed_id = _upload_one(
        api_client_focused, readwrite_key, tags=["TEST_failed_only"]
    )
    fake_store.stat.return_value = {"size": 999}
    assert (
        api_client_focused.post(
            f"/v2/media/assets/{failed_id}/confirm", headers=headers
        ).status_code
        == 409
    )
    fake_store.stat.return_value = {"size": 1024}
    deleted_id = _upload_one(
        api_client_focused, readwrite_key, tags=["TEST_deleted_only"]
    )
    assert (
        api_client_focused.post(
            f"/v2/media/assets/{deleted_id}/confirm", headers=headers
        ).status_code
        == 200
    )
    assert (
        api_client_focused.delete(
            f"/v2/media/assets/{deleted_id}", headers=headers
        ).status_code
        == 204
    )

    def saved_rows():
        with db_engine.connect() as conn:
            return conn.execute(
                text(
                    "SELECT id, tags, status, deleted_at FROM media.media_assets ORDER BY id"
                )
            ).all()

    before = saved_rows()
    fake_store.reset_mock()
    readonly_headers = {"Authorization": f"Bearer {readonly_key}"}
    for _ in range(2):
        candidates = api_client_focused.get(
            "/v2/media/assets/tags", headers=readonly_headers
        )
        assert candidates.status_code == 200, candidates.text
        assert candidates.json() == sorted(
            [long_tag, "TEST_shared", "TEST_multi line", "TEST_legacy tag"]
        )
    assert saved_rows() == before
    assert fake_store.mock_calls == []

    exact = api_client_focused.get(
        "/v2/media/assets", headers=readonly_headers, params={"tag": long_tag}
    )
    assert exact.status_code == 200, exact.text
    assert exact.json()["total"] == 1
    assert [row["asset_id"] for row in exact.json()["items"]] == [ready_ids[1]]
    legacy = api_client_focused.get(
        "/v2/media/assets", headers=readonly_headers, params={"tag": "TEST_legacy tag"}
    )
    assert legacy.json()["total"] == 1
    assert legacy.json()["items"][0]["tags"] == [
        long_tag,
        "TEST_legacy tag",
        "TEST_shared",
    ]
    keyword = api_client_focused.get(
        "/v2/media/assets", headers=readonly_headers, params={"keyword": "legacy"}
    )
    assert keyword.status_code == 200, keyword.text
    assert keyword.json()["total"] == 1
    assert [row["asset_id"] for row in keyword.json()["items"]] == [ready_ids[1]]


def test_legacy_unicode_tag_whitespace_has_matching_candidates_and_filters(
    api_client_focused, readwrite_key, readonly_key, db_engine
):
    headers = {"Authorization": f"Bearer {readwrite_key}"}
    asset_id = _upload_one(
        api_client_focused, readwrite_key, filename="TEST_unicode_whitespace.jpg"
    )
    asset_path = f"/v2/media/assets/{asset_id}"
    assert (
        api_client_focused.post(f"{asset_path}/confirm", headers=headers).status_code
        == 200
    )
    raw_tags = [
        "\x1c TEST_legacy\x1ccontrol \x1c",
        "\u00a0TEST_legacy\u00a0nbsp\u00a0",
        "\tTEST_legacy\t tab\t",
        "\u00a0TEST_legacy\x1ccontrol\t",
        "\x1c\u00a0\t",
    ]
    # Bypass write normalization to exercise labels stored by older versions.
    with db_engine.begin() as conn:
        conn.execute(
            text("UPDATE media.media_assets SET tags = :tags WHERE id = :id"),
            {"tags": raw_tags, "id": asset_id},
        )

    expected = ["TEST_legacy control", "TEST_legacy nbsp", "TEST_legacy tab"]
    readonly_headers = {"Authorization": f"Bearer {readonly_key}"}
    candidates = api_client_focused.get(
        "/v2/media/assets/tags", headers=readonly_headers
    )
    assert candidates.status_code == 200, candidates.text
    assert candidates.json() == expected
    assert (
        api_client_focused.get(asset_path, headers=readonly_headers).json()["tags"]
        == expected
    )
    for normalized, raw in zip(expected, raw_tags[:3], strict=True):
        for name, value in (("tag", normalized), ("tag", raw), ("keyword", normalized)):
            filtered = api_client_focused.get(
                "/v2/media/assets", headers=readonly_headers, params={name: value}
            )
            assert filtered.status_code == 200, filtered.text
            assert filtered.json()["total"] == 1, (name, value, filtered.json())
            assert [row["asset_id"] for row in filtered.json()["items"]] == [asset_id]
    with db_engine.connect() as conn:
        assert (
            conn.scalar(
                text("SELECT tags FROM media.media_assets WHERE id = :id"),
                {"id": asset_id},
            )
            == raw_tags
        )


# ---------------------------------------------------------------------------
# PATCH / DELETE /v2/media/assets/{id}
# ---------------------------------------------------------------------------


def test_update_asset_metadata(api_client_focused, readwrite_key):
    asset_id = _upload_one(api_client_focused, readwrite_key)
    r = api_client_focused.patch(
        f"/v2/media/assets/{asset_id}",
        headers={"Authorization": f"Bearer {readwrite_key}"},
        json={
            "filename": "TEST_renamed.jpg",
            "description": "TEST 说明",
            "tags": [" a ", "b\t c", "a", "", "  "],
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["filename"] == "TEST_renamed.jpg"
    assert body["description"] == "TEST 说明"
    assert body["tags"] == ["a", "b c"]
    assert body["usageCount"] is None


def test_update_unknown_asset_is_404(api_client_focused, readwrite_key):
    r = api_client_focused.patch(
        "/v2/media/assets/99999999",
        headers={"Authorization": f"Bearer {readwrite_key}"},
        json={"filename": "TEST_x.jpg"},
    )
    assert r.status_code == 404, r.text


@pytest.mark.parametrize(
    ("path", "delete_operation"),
    [
        ("/v2/media/assets/1", "soft_delete_asset"),
        ("/v2/media/digital-humans/1", "soft_delete_digital_human"),
    ],
)
def test_media_delete_reports_database_protection_to_readwrite(
    api_client_focused, readwrite_key, fake_store, monkeypatch, path, delete_operation
):
    from tts_erp_v2.api import deps
    from tts_erp_v2.media import repository

    # Simulate the guard decision without replacing the isolated DB connection.
    monkeypatch.setattr(deps, "is_prod_shaped_db", lambda: True)
    monkeypatch.delenv("ALLOW_PROD_DESTRUCTIVE", raising=False)
    with patch.object(repository, delete_operation) as delete_record:
        response = api_client_focused.delete(
            path, headers={"Authorization": f"Bearer {readwrite_key}"}
        )
    assert response.status_code == 403, response.text
    assert response.json()["detail"] == "MEDIA_DELETE_PROTECTED"
    assert response.headers["X-Error-Code"] == "MEDIA_DELETE_PROTECTED"
    delete_record.assert_not_called()
    fake_store.remove.assert_not_called()


def test_delete_asset_is_soft_and_removes_object(
    api_client_focused, readwrite_key, fake_store
):
    asset_id = _upload_one(api_client_focused, readwrite_key)
    r = api_client_focused.delete(
        f"/v2/media/assets/{asset_id}",
        headers={"Authorization": f"Bearer {readwrite_key}"},
    )
    assert r.status_code == 204, r.text
    assert fake_store.remove.called

    listed = api_client_focused.get(
        "/v2/media/assets",
        headers={"Authorization": f"Bearer {readwrite_key}"},
    )
    assert all(item["asset_id"] != asset_id for item in listed.json()["items"])
    assert (
        api_client_focused.delete(
            f"/v2/media/assets/{asset_id}",
            headers={"Authorization": f"Bearer {readwrite_key}"},
        ).status_code
        == 204
    )
    assert fake_store.remove.call_count == 1


def test_referenced_asset_cannot_be_deleted_until_the_last_reference_is_removed(
    api_client_focused, readwrite_key, fake_store, db_engine
):
    headers = {"Authorization": f"Bearer {readwrite_key}"}
    asset_id = _upload_one(api_client_focused, readwrite_key)
    asset_path = f"/v2/media/assets/{asset_id}"
    confirmed = api_client_focused.post(f"{asset_path}/confirm", headers=headers)
    object_key = confirmed.json()["object_key"]
    stored_bytes = {object_key: b"TEST_original_object_bytes"}
    fake_store.remove.side_effect = stored_bytes.pop
    human_ids = [
        api_client_focused.post(
            "/v2/media/digital-humans",
            headers=headers,
            json={"name": f"TEST_delete_reference_{index}"},
        ).json()["digital_human_id"]
        for index in (1, 2)
    ]
    human_paths = [f"/v2/media/digital-humans/{human_id}" for human_id in human_ids]
    for path in human_paths:
        assert (
            api_client_focused.post(
                f"{path}/assets", headers=headers, json={"assetId": asset_id}
            ).status_code
            == 200
        )
    assert (
        api_client_focused.patch(
            human_paths[1], headers=headers, json={"status": "archived"}
        ).status_code
        == 200
    )

    for remaining in (2, 1):
        rejected = api_client_focused.delete(asset_path, headers=headers)
        assert rejected.status_code == 409, rejected.text
        assert rejected.json()["detail"] == "ASSET_IN_USE"
        assert fake_store.remove.call_count == 0
        assert stored_bytes == {object_key: b"TEST_original_object_bytes"}
        with db_engine.connect() as conn:
            assert (
                conn.scalar(
                    text("SELECT deleted_at FROM media.media_assets WHERE id = :id"),
                    {"id": asset_id},
                )
                is None
            )
        detail = api_client_focused.get(asset_path, headers=headers).json()
        assert detail["usageCount"] == remaining
        assert len(detail["used_by"]) == remaining
        assert api_client_focused.get(human_paths[1], headers=headers).json()[
            "asset_ids"
        ] == [asset_id]
        assert (
            api_client_focused.delete(
                f"{human_paths[2 - remaining]}/assets/{asset_id}", headers=headers
            ).status_code
            == 204
        )

    assert api_client_focused.get(asset_path, headers=headers).json()["usageCount"] == 0
    assert api_client_focused.delete(asset_path, headers=headers).status_code == 204
    assert stored_bytes == {}
    assert fake_store.remove.call_count == 1


def test_asset_link_and_delete_lock_each_other_in_both_directions(
    api_client_focused, readwrite_key, db_engine
):
    """Real repository operations must contend on the same asset row."""
    from tts_erp_v2.media import repository

    headers = {"Authorization": f"Bearer {readwrite_key}"}
    asset_id = _upload_one(api_client_focused, readwrite_key)
    assert (
        api_client_focused.post(
            f"/v2/media/assets/{asset_id}/confirm", headers=headers
        ).status_code
        == 200
    )
    human_id = api_client_focused.post(
        "/v2/media/digital-humans",
        headers=headers,
        json={"name": "TEST_link_delete_locks"},
    ).json()["digital_human_id"]

    # Holding commit leaves the actual operation's lock open. A bounded DB
    # timeout proves contention, without timing-dependent thread sleeps.
    for holder_operation, waiting_operation, expected_after_commit in (
        (
            lambda session: repository.link_asset_to_digital_human(
                session, human_id, asset_id
            ),
            lambda session: repository.soft_delete_asset(session, asset_id),
            repository.DeleteAssetError.ASSET_IN_USE,
        ),
        (
            lambda session: repository.soft_delete_asset(session, asset_id),
            lambda session: repository.link_asset_to_digital_human(
                session, human_id, asset_id
            ),
            repository.LinkAssetError.ASSET_NOT_FOUND,
        ),
    ):
        with Session(db_engine) as holder, Session(db_engine) as waiter:
            with patch.object(holder, "commit"):
                result = holder_operation(holder)
            assert result is not None
            assert not isinstance(
                result, (repository.LinkAssetError, repository.DeleteAssetError)
            )
            waiter.execute(text("SET LOCAL lock_timeout = '250ms'"))
            with pytest.raises(OperationalError) as blocked:
                waiting_operation(waiter)
            assert getattr(blocked.value.orig, "sqlstate", None) == "55P03"
            waiter.rollback()
            holder.commit()

        # A fresh transaction after the winning commit must observe its result,
        # rather than deleting a newly referenced asset or linking a deleted one.
        with Session(db_engine) as resumed:
            assert waiting_operation(resumed) is expected_after_commit
        with db_engine.connect() as conn:
            deleted_at = conn.scalar(
                text("SELECT deleted_at FROM media.media_assets WHERE id = :id"),
                {"id": asset_id},
            )
            links = conn.scalar(
                text(
                    "SELECT count(*) FROM media.digital_human_assets WHERE asset_id = :id"
                ),
                {"id": asset_id},
            )
        if expected_after_commit is repository.DeleteAssetError.ASSET_IN_USE:
            assert deleted_at is None
            assert links == 1
            # Clear the first winner's reference before exercising delete-first.
            with Session(db_engine) as cleanup:
                assert repository.unlink_asset_from_digital_human(
                    cleanup, human_id, asset_id
                )
        else:
            assert deleted_at is not None
            assert links == 0


def test_deleted_persona_legacy_reference_is_not_counted_or_a_delete_blocker(
    api_client_focused, readwrite_key, db_engine
):
    headers = {"Authorization": f"Bearer {readwrite_key}"}
    asset_id = _upload_one(api_client_focused, readwrite_key)
    asset_path = f"/v2/media/assets/{asset_id}"
    assert (
        api_client_focused.post(f"{asset_path}/confirm", headers=headers).status_code
        == 200
    )
    human_id = api_client_focused.post(
        "/v2/media/digital-humans",
        headers=headers,
        json={"name": "TEST_deleted_persona_link"},
    ).json()["digital_human_id"]
    assert (
        api_client_focused.post(
            f"/v2/media/digital-humans/{human_id}/assets",
            headers=headers,
            json={"assetId": asset_id},
        ).status_code
        == 200
    )
    with db_engine.begin() as conn:
        conn.execute(
            text("UPDATE media.digital_humans SET deleted_at = now() WHERE id = :id"),
            {"id": human_id},
        )
    detail = api_client_focused.get(asset_path, headers=headers).json()
    assert detail["used_by"] == []
    assert detail["usageCount"] == 0
    listing = api_client_focused.get("/v2/media/assets", headers=headers).json()
    assert (
        next(row for row in listing["items"] if row["asset_id"] == asset_id)[
            "usageCount"
        ]
        == 0
    )
    assert api_client_focused.delete(asset_path, headers=headers).status_code == 204


# ---------------------------------------------------------------------------
# digital humans
# ---------------------------------------------------------------------------


def test_create_and_read_digital_human(api_client_focused, readwrite_key):
    r = api_client_focused.post(
        "/v2/media/digital-humans",
        headers={"Authorization": f"Bearer {readwrite_key}"},
        json={"name": "TEST_digital_human_a", "description": "TEST 人设档案"},
    )
    assert r.status_code == 201, r.text
    human_id = int(r.json()["digital_human_id"])
    assert r.json()["status"] == "active"
    assert r.json()["asset_ids"] == []

    detail = api_client_focused.get(
        f"/v2/media/digital-humans/{human_id}",
        headers={"Authorization": f"Bearer {readwrite_key}"},
    )
    assert detail.status_code == 200, detail.text
    assert detail.json()["name"] == "TEST_digital_human_a"
    assert detail.json()["assets"] == []


def test_update_and_delete_digital_human(api_client_focused, readwrite_key):
    created = api_client_focused.post(
        "/v2/media/digital-humans",
        headers={"Authorization": f"Bearer {readwrite_key}"},
        json={"name": "TEST_digital_human_b"},
    )
    human_id = int(created.json()["digital_human_id"])

    patched = api_client_focused.patch(
        f"/v2/media/digital-humans/{human_id}",
        headers={"Authorization": f"Bearer {readwrite_key}"},
        json={"name": "TEST_digital_human_b2", "status": "archived"},
    )
    assert patched.status_code == 200, patched.text
    assert patched.json()["name"] == "TEST_digital_human_b2"
    assert patched.json()["status"] == "archived"

    deleted = api_client_focused.delete(
        f"/v2/media/digital-humans/{human_id}",
        headers={"Authorization": f"Bearer {readwrite_key}"},
    )
    assert deleted.status_code == 204, deleted.text

    gone = api_client_focused.get(
        f"/v2/media/digital-humans/{human_id}",
        headers={"Authorization": f"Bearer {readwrite_key}"},
    )
    assert gone.status_code == 404, gone.text


def test_link_and_unlink_asset(api_client_focused, readwrite_key):
    asset_id = _upload_one(api_client_focused, readwrite_key)
    confirmed = api_client_focused.post(
        f"/v2/media/assets/{asset_id}/confirm",
        headers={"Authorization": f"Bearer {readwrite_key}"},
    )
    assert confirmed.status_code == 200, confirmed.text
    assert (
        api_client_focused.get(
            f"/v2/media/assets/{asset_id}",
            headers={"Authorization": f"Bearer {readwrite_key}"},
        ).json()["usageCount"]
        == 0
    )
    created = api_client_focused.post(
        "/v2/media/digital-humans",
        headers={"Authorization": f"Bearer {readwrite_key}"},
        json={"name": "TEST_digital_human_c"},
    )
    human_id = int(created.json()["digital_human_id"])

    linked = api_client_focused.post(
        f"/v2/media/digital-humans/{human_id}/assets",
        headers={"Authorization": f"Bearer {readwrite_key}"},
        json={"assetId": asset_id},
    )
    assert linked.status_code == 200, linked.text
    assert linked.json()["asset_ids"] == [asset_id]
    detail = api_client_focused.get(
        f"/v2/media/digital-humans/{human_id}",
        headers={"Authorization": f"Bearer {readwrite_key}"},
    )
    assert detail.json()["assets"][0]["usageCount"] == 1

    # Linking twice is idempotent.
    again = api_client_focused.post(
        f"/v2/media/digital-humans/{human_id}/assets",
        headers={"Authorization": f"Bearer {readwrite_key}"},
        json={"assetId": asset_id},
    )
    assert again.status_code == 200, again.text
    assert again.json()["asset_ids"] == [asset_id]

    unlinked = api_client_focused.delete(
        f"/v2/media/digital-humans/{human_id}/assets/{asset_id}",
        headers={"Authorization": f"Bearer {readwrite_key}"},
    )
    assert unlinked.status_code == 204, unlinked.text

    after = api_client_focused.get(
        f"/v2/media/digital-humans/{human_id}",
        headers={"Authorization": f"Bearer {readwrite_key}"},
    )
    assert after.json()["asset_ids"] == []
    assert after.json()["assets"] == []
    assert (
        api_client_focused.get(
            f"/v2/media/assets/{asset_id}",
            headers={"Authorization": f"Bearer {readwrite_key}"},
        ).json()["usageCount"]
        == 0
    )


def test_ready_filter_and_association_reject_new_pending_link(
    api_client_focused, readwrite_key
):
    headers = {"Authorization": f"Bearer {readwrite_key}"}
    prefix = "TEST_link_readiness"
    ready_id = _upload_one(
        api_client_focused, readwrite_key, filename=f"{prefix}_ready.jpg"
    )
    confirmed = api_client_focused.post(
        f"/v2/media/assets/{ready_id}/confirm", headers=headers
    )
    assert confirmed.status_code == 200
    pending_id = _upload_one(
        api_client_focused, readwrite_key, filename=f"{prefix}_pending.jpg"
    )
    created = api_client_focused.post(
        "/v2/media/digital-humans", headers=headers, json={"name": prefix}
    )
    human_path = f"/v2/media/digital-humans/{created.json()['digital_human_id']}"

    candidate_params = {"keyword": prefix, "limit": 1}
    default_page = api_client_focused.get(
        "/v2/media/assets", headers=headers, params=candidate_params
    )
    assert default_page.json()["total"] == 2
    assert [asset["asset_id"] for asset in default_page.json()["items"]] == [pending_id]
    ready_page = api_client_focused.get(
        "/v2/media/assets",
        headers=headers,
        params={**candidate_params, "status": "ready"},
    )
    assert ready_page.json()["total"] == 1
    assert [asset["asset_id"] for asset in ready_page.json()["items"]] == [ready_id]

    rejected = api_client_focused.post(
        f"{human_path}/assets", headers=headers, json={"assetId": pending_id}
    )
    assert rejected.status_code == 409
    assert rejected.json()["detail"] == "ASSET_NOT_READY"
    assert api_client_focused.get(human_path, headers=headers).json()["asset_ids"] == []


def test_pending_legacy_link_can_be_replayed_and_removed(
    api_client_focused, readwrite_key, db_engine
):
    headers = {"Authorization": f"Bearer {readwrite_key}"}
    asset_id = _upload_one(api_client_focused, readwrite_key)
    human_id = api_client_focused.post(
        "/v2/media/digital-humans",
        headers=headers,
        json={"name": "TEST_legacy_pending_link"},
    ).json()["digital_human_id"]
    with db_engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO media.digital_human_assets (digital_human_id, asset_id) "
                "VALUES (:human_id, :asset_id)"
            ),
            {"human_id": human_id, "asset_id": asset_id},
        )

    human_path = f"/v2/media/digital-humans/{human_id}"
    replayed = api_client_focused.post(
        f"{human_path}/assets", headers=headers, json={"assetId": asset_id}
    )
    assert replayed.status_code == 200
    assert replayed.json()["asset_ids"] == [asset_id]
    assert (
        api_client_focused.delete(
            f"{human_path}/assets/{asset_id}", headers=headers
        ).status_code
        == 204
    )
    assert api_client_focused.get(human_path, headers=headers).json()["asset_ids"] == []


def test_link_unknown_asset_is_404(api_client_focused, readwrite_key):
    created = api_client_focused.post(
        "/v2/media/digital-humans",
        headers={"Authorization": f"Bearer {readwrite_key}"},
        json={"name": "TEST_digital_human_d"},
    )
    human_id = int(created.json()["digital_human_id"])
    r = api_client_focused.post(
        f"/v2/media/digital-humans/{human_id}/assets",
        headers={"Authorization": f"Bearer {readwrite_key}"},
        json={"assetId": 99999999},
    )
    assert r.status_code == 404, r.text


def test_list_digital_humans(api_client_focused, readwrite_key):
    api_client_focused.post(
        "/v2/media/digital-humans",
        headers={"Authorization": f"Bearer {readwrite_key}"},
        json={"name": "TEST_digital_human_e"},
    )
    r = api_client_focused.get(
        "/v2/media/digital-humans",
        headers={"Authorization": f"Bearer {readwrite_key}"},
    )
    assert r.status_code == 200, r.text
    assert any(item["name"] == "TEST_digital_human_e" for item in r.json())


def test_digital_human_create_requires_readwrite(api_client_focused, readonly_key):
    r = api_client_focused.post(
        "/v2/media/digital-humans",
        headers={"Authorization": f"Bearer {readonly_key}"},
        json={"name": "TEST_nope"},
    )
    assert r.status_code == 403, r.text


def test_metadata_is_available_without_configured_object_storage(
    api_client_focused, readwrite_key, monkeypatch
):
    from tts_erp_v2.api.v2 import media
    from tts_erp_v2.storage.minio_client import MinioConfigError

    headers = {"Authorization": f"Bearer {readwrite_key}"}
    asset_id = _upload_one(api_client_focused, readwrite_key)
    confirmed = api_client_focused.post(
        f"/v2/media/assets/{asset_id}/confirm", headers=headers
    )
    assert confirmed.status_code == 200
    human_id = api_client_focused.post(
        "/v2/media/digital-humans", headers=headers, json={"name": "TEST_storage_down"}
    ).json()["digital_human_id"]
    assert (
        api_client_focused.post(
            f"/v2/media/digital-humans/{human_id}/assets",
            headers=headers,
            json={"assetId": asset_id},
        ).status_code
        == 200
    )

    def unavailable():
        raise MinioConfigError("TEST storage not configured")

    monkeypatch.setattr(media, "get_store", unavailable)
    listing = api_client_focused.get("/v2/media/assets", headers=headers)
    assert listing.status_code == 200
    row = next(row for row in listing.json()["items"] if row["asset_id"] == asset_id)
    assert row["url"] is None and row["status"] == "ready"
    detail = api_client_focused.get(
        f"/v2/media/digital-humans/{human_id}", headers=headers
    )
    assert detail.status_code == 200
    assert detail.json()["assets"][0]["asset_id"] == asset_id
    assert detail.json()["assets"][0]["url"] is None
    changed = api_client_focused.patch(
        f"/v2/media/assets/{asset_id}",
        headers=headers,
        json={"filename": "TEST_renamed.png"},
    )
    assert (
        changed.status_code == 200 and changed.json()["filename"] == "TEST_renamed.png"
    )
    upload = api_client_focused.post(
        "/v2/media/assets/upload-url",
        headers=headers,
        json={
            "filename": "TEST_unavailable.jpg",
            "contentType": "image/jpeg",
            "sizeBytes": 1024,
        },
    )
    assert upload.status_code == 503


def test_asset_candidates_exclude_only_the_selected_personas_links(
    api_client_focused, readwrite_key
):
    headers = {"Authorization": f"Bearer {readwrite_key}"}
    linked_id = _upload_one(
        api_client_focused, readwrite_key, filename="TEST_candidate_linked.jpg"
    )
    other_id = _upload_one(
        api_client_focused, readwrite_key, filename="TEST_candidate_other.jpg"
    )
    for asset_id in (linked_id, other_id):
        assert (
            api_client_focused.post(
                f"/v2/media/assets/{asset_id}/confirm", headers=headers
            ).status_code
            == 200
        )
    human_ids = [
        api_client_focused.post(
            "/v2/media/digital-humans",
            headers=headers,
            json={"name": f"TEST_candidate_{index}"},
        ).json()["digital_human_id"]
        for index in (1, 2)
    ]
    for human_id, asset_id in zip(human_ids, (linked_id, other_id), strict=True):
        assert (
            api_client_focused.post(
                f"/v2/media/digital-humans/{human_id}/assets",
                headers=headers,
                json={"assetId": asset_id},
            ).status_code
            == 200
        )

    candidates = api_client_focused.get(
        "/v2/media/assets",
        headers=headers,
        params={
            "excludeDigitalHumanId": human_ids[0],
            "keyword": "TEST_candidate",
            "limit": 1,
        },
    )
    assert candidates.status_code == 200
    assert candidates.json()["total"] == 1
    assert [row["asset_id"] for row in candidates.json()["items"]] == [other_id]
    assert candidates.json()["items"][0]["usageCount"] == 1
    linked = api_client_focused.get(
        "/v2/media/assets", headers=headers, params={"digitalHumanId": human_ids[0]}
    )
    assert [row["asset_id"] for row in linked.json()["items"]] == [linked_id]


def test_deleted_asset_is_not_counted_as_a_persona_attachment(
    api_client_focused, readwrite_key, db_engine
):
    headers = {"Authorization": f"Bearer {readwrite_key}"}
    asset_id = _upload_one(api_client_focused, readwrite_key)
    assert (
        api_client_focused.post(
            f"/v2/media/assets/{asset_id}/confirm", headers=headers
        ).status_code
        == 200
    )
    human_id = api_client_focused.post(
        "/v2/media/digital-humans",
        headers=headers,
        json={"name": "TEST_deleted_asset_count"},
    ).json()["digital_human_id"]
    path = f"/v2/media/digital-humans/{human_id}"
    assert (
        api_client_focused.post(
            f"{path}/assets", headers=headers, json={"assetId": asset_id}
        ).status_code
        == 200
    )
    # Preserve support for historical soft-deleted rows. The public DELETE
    # endpoint now prevents creating this state while a live reference exists.
    with db_engine.begin() as conn:
        conn.execute(
            text("UPDATE media.media_assets SET deleted_at = now() WHERE id = :id"),
            {"id": asset_id},
        )
    detail = api_client_focused.get(path, headers=headers).json()
    listing = api_client_focused.get("/v2/media/digital-humans", headers=headers).json()
    row = next(item for item in listing if item["digital_human_id"] == human_id)
    assert detail["assets"] == []
    assert (detail["asset_ids"], row["asset_ids"]) == ([], [])


def test_shared_asset_usage_tracks_archive_restore_and_removal(
    api_client_focused, readwrite_key
):
    headers = {"Authorization": f"Bearer {readwrite_key}"}
    humans_url = "/v2/media/digital-humans"
    prefix = "TEST_shared_reference"
    asset_id = _upload_one(api_client_focused, readwrite_key, filename=f"{prefix}.jpg")
    asset_path = f"/v2/media/assets/{asset_id}"
    assert (
        api_client_focused.post(f"{asset_path}/confirm", headers=headers).status_code
        == 200
    )
    first_id, second_id = [
        api_client_focused.post(
            humans_url, headers=headers, json={"name": f"{prefix}_{suffix}"}
        ).json()["digital_human_id"]
        for suffix in ("a", "b")
    ]
    first_path, second_path = f"{humans_url}/{first_id}", f"{humans_url}/{second_id}"
    for human_path in (first_path, second_path):
        linked = api_client_focused.post(
            f"{human_path}/assets", headers=headers, json={"assetId": asset_id}
        )
        assert linked.status_code == 200

    def human_ids_by_status(status: str) -> list[int]:
        listing = api_client_focused.get(
            humans_url, headers=headers, params={"keyword": prefix, "status": status}
        )
        assert listing.status_code == 200
        return [human["digital_human_id"] for human in listing.json()]

    archived = api_client_focused.patch(
        second_path, headers=headers, json={"status": "archived"}
    )
    assert archived.json()["asset_ids"] == [asset_id]
    assert human_ids_by_status("active") == [first_id]
    assert human_ids_by_status("archived") == [second_id]
    asset_detail = api_client_focused.get(asset_path, headers=headers).json()
    assert asset_detail["usageCount"] == 2
    usage = asset_detail["used_by"]
    assert usage == [
        {"digital_human_id": second_id, "name": f"{prefix}_b", "status": "archived"},
        {"digital_human_id": first_id, "name": f"{prefix}_a", "status": "active"},
    ]
    listing = api_client_focused.get(
        "/v2/media/assets", headers=headers, params={"keyword": prefix}
    ).json()
    assert listing["items"][0]["usageCount"] == 2

    new_name = f"{prefix}_renamed.jpg"
    edited = api_client_focused.patch(
        asset_path, headers=headers, json={"filename": new_name}
    )
    assert edited.status_code == 200
    for human_path in (first_path, second_path):
        embedded = api_client_focused.get(human_path, headers=headers).json()["assets"]
        assert len(embedded) == 1
        assert embedded[0]["filename"] == new_name
        assert embedded[0]["usageCount"] == 2

    restored = api_client_focused.patch(
        second_path, headers=headers, json={"status": "active"}
    )
    assert restored.json()["asset_ids"] == [asset_id]
    assert human_ids_by_status("archived") == []
    assert human_ids_by_status("active") == [second_id, first_id]

    assert (
        api_client_focused.delete(
            f"{first_path}/assets/{asset_id}", headers=headers
        ).status_code
        == 204
    )
    first_detail = api_client_focused.get(first_path, headers=headers).json()
    assert first_detail["asset_ids"] == []
    assert first_detail["assets"] == []
    asset_detail = api_client_focused.get(asset_path, headers=headers).json()
    assert asset_detail["usageCount"] == 1
    usage = asset_detail["used_by"]
    assert usage == [
        {"digital_human_id": second_id, "name": f"{prefix}_b", "status": "active"}
    ]
    second_detail = api_client_focused.get(second_path, headers=headers).json()
    assert second_detail["asset_ids"] == [asset_id]
    assert second_detail["assets"][0]["usageCount"] == 1

    assert api_client_focused.delete(second_path, headers=headers).status_code == 204
    retained = api_client_focused.get(asset_path, headers=headers)
    assert retained.status_code == 200
    assert retained.json()["used_by"] == []
    assert retained.json()["usageCount"] == 0


def test_reference_order_appends_new_links_and_preserves_replayed_positions(
    api_client_focused, readwrite_key
):
    headers = {"Authorization": f"Bearer {readwrite_key}"}
    assets = [
        _upload_one(
            api_client_focused, readwrite_key, filename=f"TEST_reference_order_{name}.jpg"
        )
        for name in ("first", "second")
    ]
    for asset_id in assets:
        confirmed = api_client_focused.post(
            f"/v2/media/assets/{asset_id}/confirm", headers=headers
        )
        assert confirmed.status_code == 200
    created = api_client_focused.post(
        "/v2/media/digital-humans",
        headers=headers,
        json={"name": "TEST_reference_order_persona"},
    )
    assert created.status_code == 201
    path = f"/v2/media/digital-humans/{created.json()['digital_human_id']}"
    for index, asset_id in enumerate(assets):
        linked = api_client_focused.post(
            f"{path}/assets", headers=headers, json={"assetId": asset_id}
        )
        assert linked.status_code == 200
        assert linked.json()["asset_ids"] == assets[: index + 1]
    replayed = api_client_focused.post(
        f"{path}/assets", headers=headers, json={"assetId": assets[0]}
    )
    assert replayed.status_code == 200
    assert replayed.json()["asset_ids"] == assets
    detail = api_client_focused.get(path, headers=headers).json()
    assert [asset["asset_id"] for asset in detail["assets"]] == assets
    detached = api_client_focused.delete(f"{path}/assets/{assets[0]}", headers=headers)
    assert detached.status_code == 204
    reattached = api_client_focused.post(
        f"{path}/assets", headers=headers, json={"assetId": assets[0]}
    )
    assert reattached.status_code == 200
    assert reattached.json()["asset_ids"] == [assets[1], assets[0]]
