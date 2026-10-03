"""Live 冒烟：鉴权边界（enforce 模式下的 401/302/公开豁免）。

校验 docs/api/external-api.md 的鉴权契约在 live 服务上成立：

- 公开路径（/healthz、/v2/auth/me）无凭据可达，且生产 auth_mode=enforce；
- 只读 JSON 端点缺凭据 → 401 ``missing bearer token``；
- 无效 key → 401 ``invalid, disabled or expired api key``；
- ``X-API-Key`` 与 ``Authorization: Bearer`` 等价；
- 浏览器（Accept: text/html）未登录 → 302 跳登录页并带 ``next`` 回跳，
  而 JSON 客户端同一路径 → 401（不重定向）。
"""

from __future__ import annotations

import pytest

from conftest import request_json, request_no_redirect, service_key


def test_healthz_public_and_auth_mode_enforce() -> None:
    """/healthz 公开可达；生产口径要求 auth_mode=enforce（external-api.md）。"""
    status, body = request_json("GET", "/healthz", require_key=False, send_key=False)
    assert status == 200, body
    assert isinstance(body, dict)
    assert body.get("status") in ("ok", "up"), body
    assert body.get("auth_mode") == "enforce", f"生产服务 auth_mode 应为 enforce: {body}"


def test_auth_me_public_without_session() -> None:
    """/v2/auth/me 公开：无会话 → 200 {authenticated: false}（不是 401）。"""
    status, body = request_json("GET", "/v2/auth/me", require_key=False, send_key=False)
    assert status == 200, body
    assert isinstance(body, dict)
    assert body.get("authenticated") is False, body


def test_readonly_endpoint_missing_bearer_401() -> None:
    """enforce：JSON 客户端缺凭据 → 401 + 文档约定的 detail 文案。"""
    status, body = request_json("GET", "/v2/fx/latest", require_key=False, send_key=False)
    assert status == 401, body
    assert "missing bearer token" in str(body), body


def test_invalid_api_key_401() -> None:
    """无效 key → 401，且文案与 external-api.md 的错误表一致。"""
    status, body = request_json(
        "GET",
        "/v2/fx/latest",
        require_key=False,
        send_key=False,
        headers={"Authorization": "Bearer definitely-not-a-real-key"},
    )
    assert status == 401, body
    assert "invalid, disabled or expired api key" in str(body), body


def test_api_key_via_x_api_key_header() -> None:
    """``X-API-Key`` header 与 ``Bearer`` 等价（external-api.md §鉴权）。"""
    key = service_key()
    if not key:
        pytest.skip("TTS_ERP_SERVICE_KEY 未配置（.env 缺失），跳过需鉴权的 live 用例")
    status, body = request_json("GET", "/v2/fx/latest", send_key=False, headers={"X-API-Key": key})
    assert status == 200, body
    assert isinstance(body, dict), body


def test_browser_redirects_to_login_with_next() -> None:
    """浏览器未登录 → 302 登录页并带 next 回跳（不跟随重定向断言）。"""
    status, headers, _text = request_no_redirect(
        "GET", "/v2/pages/manual-costs", headers={"Accept": "text/html"}
    )
    assert status == 302, f"期望 302, 实际 {status}: {headers}"
    location = headers.get("location", "")
    assert "/v2/auth/login" in location, f"Location 应指向登录页: {location}"
    assert "next=/v2/pages/manual-costs" in location, f"Location 应带回 next 回跳: {location}"


def test_api_client_gets_json_401_not_redirect() -> None:
    """同一路径的 JSON 客户端 → 401 JSON，而不是 302 跳转。"""
    status, body = request_json("GET", "/v2/pages/manual-costs", require_key=False, send_key=False)
    assert status == 401, body
    assert "missing bearer token" in str(body), body
