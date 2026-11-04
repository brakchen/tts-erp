"""Auxiliary views preserve protocol/status and expose only display-safe facts."""
from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest
from starlette.requests import Request

from tests.api.test_frontend_pages import _bootstrap
from tts_erp_v2.api.v2 import frontend_pages, oauth
from tts_erp_v2.middleware.auth import _deny_response
from tts_erp_v2.proxy.errors import ProxyError, UpstreamHttpError
from tts_erp_v2.proxy.tiktok_oauth import OAuthFlowError

pytestmark = [pytest.mark.domain_api, pytest.mark.layer_integration]


@pytest.fixture
def auxiliary_assets(monkeypatch):
    monkeypatch.delenv("TTS_ERP_FRONTEND_DEV_ORIGIN", raising=False)
    monkeypatch.setattr(frontend_pages, "_production_assets", lambda prefix: frontend_pages._Assets(
        "TEST_auxiliary", f"{prefix}/static/frontend/TEST_auxiliary/main.js",
        (f"{prefix}/static/frontend/TEST_auxiliary/main.css",), ()))


def request(prefix=""):
    return Request({"type": "http", "root_path": prefix, "path": "/v2/oauth/tiktok/callback"})


@pytest.mark.parametrize("prefix", ["", "/tts"])
def test_auxiliary_message_is_public_escaped_and_prefix_aware(auxiliary_assets, prefix):
    hostile = '</script><script>TEST_XSS</script><img src=x onerror=TEST_XSS>'
    response = frontend_pages.render_message_page(prefix, title=hostile,
        message=hostile, tone="danger", status_code=403, action="login")
    text = bytes(response.body).decode()
    data = _bootstrap(text)
    assert response.status_code == 403
    assert response.headers["cache-control"] == "private, no-store"
    assert data["pageId"] == "message" and data["navigation"] == []
    assert data["roleCapabilities"] == {"readwrite": False, "admin": False}
    assert data["pageData"]["title"] == hostile
    assert '<script>TEST_XSS' not in text and '<img src=x' not in text
    assert f'href="{prefix}/v2/auth/login"' in text
    assert f'{prefix}/static/frontend/TEST_auxiliary/main.css' in text
    assert "message" not in frontend_pages.MIGRATED_PAGE_IDS


@pytest.mark.parametrize("status_code", [200, 400, 403, 502])
def test_auxiliary_result_keeps_status_and_facts_when_release_is_missing(monkeypatch, status_code):
    def missing(_prefix):
        raise FileNotFoundError("TEST_PRIVATE_ASSET_FAILURE")
    monkeypatch.delenv("TTS_ERP_FRONTEND_DEV_ORIGIN", raising=False)
    monkeypatch.setattr(frontend_pages, "_production_assets", missing)
    response = frontend_pages.render_message_page("/tts", title="TEST_result",
        message="TEST_completed_fact", tone="default", status_code=status_code)
    text = bytes(response.body).decode()
    assert response.status_code == status_code
    assert "TEST_completed_fact" in text and "TEST_PRIVATE_ASSET_FAILURE" not in text
    assert '<script type="module"' not in text
    assert _bootstrap(text)["releaseId"] == "unavailable"
    # The result is readable without JavaScript, not merely hidden in bootstrap.
    assert '<p>TEST_completed_fact</p>' in text


def test_forbidden_html_uses_same_renderer_and_keeps_json_protocol(auxiliary_assets):
    status, headers, body = _deny_response(403, "TEST_denied", accepts_html=True, root_path="/tts")
    assert status == 403
    assert dict(headers)[b"cache-control"] == b"private, no-store"
    assert _bootstrap(body.decode())["pageData"]["message"] == "TEST_denied"
    assert b'/tts/v2/auth/login' in body
    status, headers, body = _deny_response(403, "TEST_denied", root_path="/tts")
    assert status == 403 and json.loads(body) == {"detail": "TEST_denied"}
    assert dict(headers)[b"content-type"] == b"application/json"


def test_html_callback_projects_whitelisted_facts_and_calls_flow_once(auxiliary_assets, monkeypatch):
    calls = []
    def complete(sess, **kwargs):
        calls.append(kwargs)
        return {"access_token": "TEST_NEVER_PUBLIC_TOKEN", "shops": [{
            "shop_id": "TEST_shop", "account_name": '</script><img src=x>', "region": "VN",
            "seller_type": "TEST_seller", "credential_id": 17, "account_id": 19,
            "expires_at": datetime(2026, 11, 2, tzinfo=UTC),
            "granted_scopes": ["seller.order.read"],
            "access_token": "TEST_NEVER_PUBLIC_TOKEN", "refresh_token": "TEST_NEVER_PUBLIC_REFRESH",
            "shop_cipher": "TEST_NEVER_PUBLIC_CIPHER", "extra": {"secret": "TEST_NEVER_PUBLIC_EXTRA"}}]}
    monkeypatch.setattr(oauth, "complete_tiktok_authorization", complete)
    response = oauth._handle_html(request=request("/tts"), code="TEST_code", state="TEST_state",
        error=None, callback_app_key="TEST_app", sess=object())
    assert response.status_code == 200 and len(calls) == 1
    text = bytes(response.body).decode()
    assert "TEST_NEVER_PUBLIC" not in text
    data = _bootstrap(text)["pageData"]
    facts = {fact["label"]: fact["value"] for fact in data["sections"][0]["facts"]}
    assert facts["店铺 ID"] == "TEST_shop"
    assert facts["店铺名称"] == '</script><img src=x>'
    assert facts["访问令牌到期时间"] == "2026-11-02T00:00:00+00:00"
    assert "<img src=x>" not in text
    assert calls == [{"code": "TEST_code", "state": "TEST_state", "callback_app_key": "TEST_app"}]


@pytest.mark.parametrize("failure,http_status", [
    (OAuthFlowError("state_invalid", "TEST_SECRET_UPSTREAM"), 400),
    (UpstreamHttpError(502, "TEST_SECRET_UPSTREAM"), 502),
    (ProxyError("TEST_SECRET_UPSTREAM"), 502),
])
def test_html_callback_errors_preserve_status_without_disclosing_upstream(auxiliary_assets, monkeypatch, failure, http_status):
    def complete(*args, **kwargs):
        raise failure
    monkeypatch.setattr(oauth, "complete_tiktok_authorization", complete)
    response = oauth._handle_html(request=request(), code="TEST_code", state="TEST_state",
        error=None, callback_app_key=None, sess=object())
    assert response.status_code == http_status
    assert "TEST_SECRET_UPSTREAM" not in bytes(response.body).decode()


def test_seller_denial_consumes_state_once_preserves_200_and_never_exchanges(auxiliary_assets, monkeypatch):
    spent = []
    monkeypatch.setattr(oauth, "pop_state", lambda sess, state: spent.append(state))
    def unexpected(*args, **kwargs):
        raise AssertionError("Seller denial must not exchange a code")
    monkeypatch.setattr(oauth, "complete_tiktok_authorization", unexpected)
    response = oauth._handle_html(request=request(), code=None, state="TEST_state",
        error="TEST_DENIED_UNTRUSTED", callback_app_key=None, sess=object())
    assert response.status_code == 200 and spent == ["TEST_state"]
    assert "TEST_DENIED_UNTRUSTED" not in bytes(response.body).decode()


def test_public_callback_route_retains_400_without_login_redirect(api_client, auxiliary_assets):
    response = api_client.get("/v2/oauth/tiktok/callback")
    assert response.status_code == 400 and not response.history
    assert _bootstrap(response.text)["pageId"] == "message"


@pytest.mark.parametrize("prefix", ["", "/tts"])
def test_onboard_reuses_shop_owner_without_issuing_authorization(auxiliary_assets, monkeypatch, prefix):
    def unexpected(*args, **kwargs):
        raise AssertionError("Viewing instructions must not generate authorization")
    monkeypatch.setattr(oauth, "register_state", unexpected)
    response = oauth.onboard_page(request(prefix))
    text = bytes(response.body).decode()
    assert response.status_code == 200
    assert _bootstrap(text)["pageData"] == {"kind": "oauth-onboard"}
    assert f'href="{prefix}/v2/pages/shops"' in text
    assert "readwrite" in text and "只读" in text
    assert "window.open" not in text and "authorize?format=json" not in text


@pytest.mark.parametrize("url", ["javascript:alert(1)", "data:text/html,TEST", "https://user:password@example.test/auth"])
def test_message_rejects_unsafe_authorization_links(auxiliary_assets, url):
    with pytest.raises(ValueError, match="authorization link"):
        frontend_pages.render_message_page("", title="TEST", message="TEST", tone="success",
            status_code=200, authorization={"url": url, "expiresAt": "TEST_date"})
