"""Login presentation must consume the existing server-owned auth contract."""

from __future__ import annotations

import json
import re

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from tts_erp_v2.access import AccessGrant, AuthMode, Role
from tts_erp_v2.api.v2 import auth, frontend_pages
from tts_erp_v2.db import base

pytestmark = [pytest.mark.domain_api, pytest.mark.layer_unit]


@pytest.mark.parametrize("seconds", [1, 60, 600])
def test_login_throttle_publishes_standard_header_without_changing_legacy_body_or_cookie(
    monkeypatch, seconds
):
    monkeypatch.setattr(
        auth.session_auth, "login_throttle_hit", lambda _bucket: seconds
    )

    def forbidden_store():
        pytest.fail("Throttled login must not reach authentication/session storage")

    monkeypatch.setattr(base, "get_session_factory", forbidden_store)
    request = Request(
        {
            "type": "http",
            "method": "POST",
            "headers": [],
            "client": ("127.0.0.1", 12345),
            "root_path": "/tts",
        }
    )
    response = auth.login(
        auth.LoginBody(username="TEST_login", password="TEST_not_sent_to_store"),
        request,
    )
    assert response.status_code == 429
    assert response.headers.get("retry-after") == str(seconds)
    assert json.loads(response.body)["retry_after_s"] == seconds
    assert "set-cookie" not in response.headers


@pytest.fixture
def login_assets(monkeypatch):
    monkeypatch.delenv("TTS_ERP_FRONTEND_DEV_ORIGIN", raising=False)
    monkeypatch.setattr(
        frontend_pages,
        "_production_assets",
        lambda prefix: frontend_pages._Assets(
            "TEST_public_release", f"{prefix}/static/frontend/TEST_main.js", (), ()
        ),
    )


@pytest.mark.parametrize("prefix", ["", "/tts"])
def test_public_document_carries_canonical_target_without_business_authority(
    login_assets, prefix
):
    request = Request({"type": "http", "root_path": prefix})
    request.scope["access_grant"] = AccessGrant(
        mode=AuthMode.ENFORCE, role=Role.ADMIN, bypass=False
    )
    target = f"{prefix}/v2/pages/dashboard?source=TEST_login"
    response = frontend_pages.render_frontend_page(
        request, "login", login_next_path=target
    )
    assert response.status_code == 200
    assert response.headers["cache-control"] == "private, no-store"
    body = bytes(response.body).decode()
    match = re.search(
        r'<script id="frontend-bootstrap" type="application/json">(.*?)</script>',
        body,
        re.DOTALL,
    )
    assert match is not None
    payload = json.loads(match[1])
    assert payload["navigation"] == []
    assert payload["roleCapabilities"] == {"readwrite": False, "admin": False}
    assert payload["loginNextPath"] == target
    assert payload["rootPath"] == prefix
    assert payload["pageId"] == "login"
    assert "<form" not in body  # Existing renderer, not the legacy inline auth UI.


@pytest.mark.parametrize("target", [None, ""])
def test_public_document_without_canonical_target_fails_closed(login_assets, target):
    with pytest.raises(HTTPException) as failure:
        frontend_pages.render_frontend_page(
            Request({"type": "http", "root_path": ""}),
            "login",
            login_next_path=target,
        )
    assert failure.value.status_code == 503
    assert failure.value.headers == {"Cache-Control": "private, no-store"}


@pytest.mark.parametrize(
    "raw",
    [
        "/\n/TEST.invalid",
        "/%2f%2fTEST.invalid",
        "/../v2/pages/users",
        "/tts/%5cTEST.invalid",
        "/tts/%ZZ",
        "/tts/%C0%AFTEST.invalid",
    ],
)
def test_canonical_next_rejects_paths_the_shared_url_boundary_cannot_navigate(raw):
    assert auth._valid_next(raw) == auth.DEFAULT_NEXT


@pytest.mark.parametrize(
    "raw",
    [
        "/v2/pages/dashboard",
        "/tts/v2/pages/dashboard?source=TEST_login",
        "/v2/pages/shops?q=TEST_%E7%8C%AB",
    ],
)
def test_canonical_next_preserves_existing_safe_targets(raw):
    assert auth._valid_next(raw) == raw
