"""Wire reviewed content through the one renderer, preserving existing gates."""

from __future__ import annotations

import json
import re

import pytest

from tts_erp_v2.api.v2 import frontend_pages

pytestmark = [pytest.mark.domain_api, pytest.mark.layer_integration]


@pytest.fixture
def assets(monkeypatch):
    monkeypatch.delenv("TTS_ERP_FRONTEND_DEV_ORIGIN", raising=False)
    monkeypatch.setattr(
        frontend_pages,
        "_production_assets",
        lambda prefix: frontend_pages._Assets(
            "TEST_remaining", f"{prefix}/static/frontend/TEST_remaining/main.js", (), ()
        ),
    )


def _bootstrap(text):
    match = re.search(
        r'<script id="frontend-bootstrap" type="application/json">(.*?)</script>',
        text,
        re.DOTALL,
    )
    assert match is not None, (
        "The real page route must use the shared frontend document"
    )
    return json.loads(match[1])


@pytest.mark.parametrize(
    "page_id",
    [
        "ad-daily",
        "sync-jobs",
        "users",
        "runtime-configs",
        "intercept-configs",
        "spu-profit-deterioration",
        "manual-costs",
    ],
)
def test_actual_reviewed_page_routes_use_one_document(
    api_client, admin_key, assets, page_id
):
    response = api_client.get(
        f"/v2/pages/{page_id}", headers={"Authorization": f"Bearer {admin_key}"}
    )
    assert response.status_code == 200, response.text
    data = _bootstrap(response.text)
    assert data["pageId"] == page_id and data["releaseId"] == "TEST_remaining"
    assert data["roleCapabilities"] == {"readwrite": True, "admin": True}
    assert response.headers["cache-control"] == "private, no-store"
    assert 'id="frontend-root"' in response.text
    assert f"static/js/{page_id}.js" not in response.text
    assert admin_key not in response.text


@pytest.mark.parametrize(
    "page_id",
    [
        "ad-daily",
        "sync-jobs",
        "users",
        "runtime-configs",
        "intercept-configs",
        "spu-profit-deterioration",
        "manual-costs",
    ],
)
def test_missing_candidate_assets_are_not_a_legacy_fallback(
    api_client, admin_key, assets, monkeypatch, page_id
):
    def unavailable(_prefix):
        raise FileNotFoundError("TEST_missing_candidate")

    monkeypatch.setattr(frontend_pages, "_production_assets", unavailable)
    response = api_client.get(
        f"/v2/pages/{page_id}", headers={"Authorization": f"Bearer {admin_key}"}
    )
    assert response.status_code == 503, response.text
    assert response.headers["cache-control"] == "private, no-store"
    assert "TEST_missing_candidate" not in response.text
    assert f"static/js/{page_id}.js" not in response.text


def test_existing_sync_page_gate_is_not_broadened(
    api_client, readonly_key, readwrite_key, assets
):
    denied = api_client.get(
        "/v2/pages/sync-jobs", headers={"Authorization": f"Bearer {readonly_key}"}
    )
    assert denied.status_code == 403
    accepted = api_client.get(
        "/v2/pages/sync-jobs", headers={"Authorization": f"Bearer {readwrite_key}"}
    )
    assert accepted.status_code == 200
    assert _bootstrap(accepted.text)["roleCapabilities"] == {
        "readwrite": True,
        "admin": False,
    }


def test_ad_page_stays_readonly_and_requires_auth(api_client, readonly_key, assets):
    assert api_client.get("/v2/pages/ad-daily").status_code == 401
    response = api_client.get(
        "/v2/pages/ad-daily", headers={"Authorization": f"Bearer {readonly_key}"}
    )
    assert response.status_code == 200
    assert _bootstrap(response.text)["roleCapabilities"] == {
        "readwrite": False,
        "admin": False,
    }


def test_only_the_authoritative_registry_activates_reviewed_pages():
    assert {
        "ad-daily",
        "sync-jobs",
        "users",
        "runtime-configs",
        "intercept-configs",
        "spu-profit-deterioration",
        "login",
    } <= frontend_pages.MIGRATED_PAGE_IDS
    assert not {"spu-roi", "focused-spus"} & frontend_pages.MIGRATED_PAGE_IDS


def test_anonymous_login_uses_the_public_route_without_business_hints(
    api_client, assets
):
    response = api_client.get("/v2/auth/login", params={"next": "/v2/pages/users"})
    assert response.status_code == 200
    data = _bootstrap(response.text)
    assert data["pageId"] == "login"
    assert data["navigation"] == []
    assert data["roleCapabilities"] == {"readwrite": False, "admin": False}
    assert data["loginNextPath"] == "/v2/pages/users"
    assert response.headers["cache-control"] == "private, no-store"
    assert "<form" not in response.text


def test_login_missing_assets_fail_closed_without_old_form(
    api_client, assets, monkeypatch
):
    def unavailable(_prefix):
        raise FileNotFoundError("TEST_private_login_asset")

    monkeypatch.setattr(frontend_pages, "_production_assets", unavailable)
    response = api_client.get("/v2/auth/login")
    assert response.status_code == 503
    assert response.headers["cache-control"] == "private, no-store"
    assert "TEST_private_login_asset" not in response.text
    assert "<form" not in response.text
