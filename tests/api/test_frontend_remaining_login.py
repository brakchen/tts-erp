"""Login presentation must consume the existing server-owned auth contract."""

from __future__ import annotations

import json

import pytest
from starlette.requests import Request

from tts_erp_v2.api.v2 import auth
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
