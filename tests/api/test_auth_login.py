"""新登录页（GET /v2/auth/login）与浏览器登录入口测试。

旧文件测的 HMAC cookie 流程（``mint_session_cookie`` / ``tts_session`` / key
表单登录）已随用户名+密码改造删除。本文件收窄为两块仍然成立的契约：
- 登录页 HTML：用户名 + 密码表单、``next`` 注入/校验、外部前缀幂等；
- 浏览器登录入口：HTML GET 未登录 302 → 登录页，API 形态保持 JSON 401。

登录/登出/会话/改密流程见 tests/api/test_user_auth.py。
"""

from __future__ import annotations

import pytest

pytestmark = [pytest.mark.domain_api, pytest.mark.layer_integration]


# ---------------------------------------------------------------- login page


def test_login_page_renders_username_password_form(api_client):
    r = api_client.get("/v2/auth/login")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/html")
    assert 'id="username"' in r.text
    assert 'id="password"' in r.text
    assert 'id="login-form"' in r.text
    assert 'type="password"' in r.text
    # 无自助注册/找回入口（设计 §1.1：账号只能由管理员代建）。
    assert "注册" not in r.text


def test_login_page_injects_next(api_client):
    r = api_client.get(
        "/v2/auth/login", params={"next": "/v2/pages/manual-costs?shop_id=749"}
    )
    assert r.status_code == 200
    assert 'value="/v2/pages/manual-costs?shop_id=749"' in r.text


def test_login_page_validates_next(api_client):
    r = api_client.get("/v2/auth/login", params={"next": "https://evil.example/x"})
    assert r.status_code == 200
    assert 'value="/v2/pages/dashboard"' in r.text
    assert "evil.example" not in r.text


def test_login_page_does_not_duplicate_external_prefix(prefixed_client):
    """Page JS may pass an already-prefixed next path after a fetch 401."""
    response = prefixed_client.get(
        "/v2/auth/login",
        params={"next": "/tts/v2/pages/spu-roi"},
    )
    assert response.status_code == 200, response.text
    assert 'value="/tts/v2/pages/spu-roi"' in response.text
    assert "/tts/tts/" not in response.text


# ----------------------------------------------------- browser redirect entry


def test_browser_redirect_to_login(api_client):
    r = api_client.get("/v2/pages/manual-costs", headers={"Accept": "text/html"})
    # TestClient follows redirects; the 302 is in history.
    assert r.history and r.history[0].status_code == 302, r.text
    assert (
        r.history[0].headers["location"] == "/v2/auth/login?next=/v2/pages/manual-costs"
    )


def test_browser_redirect_keeps_query(api_client):
    r = api_client.get(
        "/v2/pages/manual-costs?shop_id=749",
        headers={"Accept": "text/html"},
    )
    assert r.history and r.history[0].status_code == 302, r.text
    assert (
        r.history[0].headers["location"]
        == "/v2/auth/login?next=/v2/pages/manual-costs?shop_id=749"
    )


def test_browser_redirect_respects_external_prefix(prefixed_client):
    # Location carries the app root_path prefix (/tts) so the browser lands
    # on the public URL the gateway serves; the next value stays
    # route-relative (login page re-prepends root_path when rendering).
    r = prefixed_client.get(
        "/v2/pages/manual-costs",
        headers={"Accept": "text/html"},
        follow_redirects=False,
    )
    assert r.status_code == 302, r.text
    assert r.headers["location"] == "/tts/v2/auth/login?next=/v2/pages/manual-costs"


def test_api_accept_keeps_json_401(api_client):
    r = api_client.get(
        "/v2/pages/manual-costs",
        headers={"Accept": "application/json"},
    )
    assert r.status_code == 401, r.text
    assert "location" not in r.headers
    assert r.json()["detail"]
