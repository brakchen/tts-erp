"""miaoshou_client 单元测试.

不打真实网络，用 monkeypatch 替换 urllib.request.urlopen。
"""

from __future__ import annotations

import base64
import json
import urllib.error
from io import BytesIO
from unittest.mock import MagicMock, patch

import pytest

from miaoshou import (  # type: ignore[reportMissingImports]
    EnvConfig,
    MiaoshouApiError,
    MiaoshouApiResponse,
    MiaoshouClient,
)

# ---- 工厂 / 配置 ----


def test_from_env_missing_vars(monkeypatch):
    monkeypatch.delenv("MIAOSHOU_LICENSE_ID", raising=False)
    monkeypatch.delenv("MIAOSHOU_COMPANY_SECRET", raising=False)
    with pytest.raises(RuntimeError, match="缺少 MIAOSHOU_LICENSE_ID"):
        MiaoshouClient.from_env()


def test_from_env_success(monkeypatch):
    monkeypatch.setenv("MIAOSHOU_LICENSE_ID", "LIC")
    monkeypatch.setenv("MIAOSHOU_COMPANY_SECRET", "SECRET")
    monkeypatch.setenv("MIAOSHOU_ENV", "test")
    c = MiaoshouClient.from_env()
    assert c.license_id == "LIC"
    assert c.company_secret == "SECRET"
    assert c.env == "test"


def test_env_config_prod_test():
    assert EnvConfig.from_name("prod").name == "prod"
    assert EnvConfig.from_name("test").name == "test"
    with pytest.raises(ValueError, match="未知 MIAOSHOU_ENV"):
        EnvConfig.from_name("staging")


def test_timeout_invalid(monkeypatch):
    monkeypatch.setenv("MIAOSHOU_LICENSE_ID", "L")
    monkeypatch.setenv("MIAOSHOU_COMPANY_SECRET", "S")
    monkeypatch.setenv("MIAOSHOU_HTTP_TIMEOUT", "not-an-int")
    with pytest.raises(RuntimeError, match="MIAOSHOU_HTTP_TIMEOUT"):
        MiaoshouClient.from_env()


# ---- _call ----


def _make_urlopen_response(payload: dict, status: int = 200):
    """构造 urllib.request.urlopen 的返回值。"""
    body = json.dumps(payload).encode("utf-8")
    resp = MagicMock()
    resp.read.return_value = body
    resp.__enter__ = lambda s: s
    resp.__exit__ = lambda s, *a: None
    return resp


def _assert_envelope(urlopen_call, expected_sign: str):
    """断言 urlopen 收到的请求体 envelope 结构正确。"""
    request = urlopen_call.args[0]
    body = request.data
    payload = json.loads(body.decode("utf-8"))
    assert set(payload.keys()) == {
        "licenseId",
        "companySecret",
        "sign",
        "busData",
        "timestamp",
    }
    assert isinstance(payload["timestamp"], int)
    assert len(payload["sign"]) == 32
    assert payload["sign"] == expected_sign
    # busData base64 解码后等价于某 JSON
    decoded = json.loads(base64.b64decode(payload["busData"]).decode("utf-8"))
    return decoded


def test_call_returns_api_response(monkeypatch):
    c = MiaoshouClient(license_id="LIC", company_secret="SECRET")
    fake_resp = _make_urlopen_response(
        {"code": 200, "message": "ok", "data": {"orderId": "X"}}
    )
    with patch("urllib.request.urlopen", return_value=fake_resp) as m:
        resp = c.orders.batch_create_async(order_list=[{"orderId": "O1"}])
        # envelope 验证（在 with 块内，m.call_args 还在）
        request = m.call_args.args[0]
        body = request.data
        payload = json.loads(body.decode("utf-8"))
        assert payload["licenseId"] == "LIC"
        assert len(payload["sign"]) == 32
        decoded = json.loads(base64.b64decode(payload["busData"]).decode("utf-8"))
        assert decoded == {"orderList": [{"orderId": "O1"}]}
    assert resp.ok
    assert resp.code == 200
    assert resp.data == {"orderId": "X"}


def test_call_business_error(monkeypatch):
    c = MiaoshouClient(license_id="LIC", company_secret="SECRET")
    fake_resp = _make_urlopen_response(
        {"code": 500, "message": "参数错误", "data": None}
    )
    with (
        patch("urllib.request.urlopen", return_value=fake_resp),
        pytest.raises(MiaoshouApiError) as exc,
    ):
        c.orders.batch_create_async(order_list=[])
    assert exc.value.code == 500
    assert exc.value.message == "参数错误"


def test_response_raise_for_status_helper():
    """MiaoshouApiResponse.raise_for_status() 作为可选的显式检查路径."""
    resp = MiaoshouApiResponse(code=200, message="ok", data=None)
    assert resp.raise_for_status() is resp  # 成功路径返回自己

    err_resp = MiaoshouApiResponse(code=400, message="bad", data=None)
    with pytest.raises(MiaoshouApiError) as exc:
        err_resp.raise_for_status()
    assert exc.value.code == 400


def test_call_http_error(monkeypatch):
    c = MiaoshouClient(license_id="LIC", company_secret="SECRET")
    err = urllib.error.HTTPError(
        url="x",
        code=404,
        msg="not found",
        hdrs={},  # type: ignore[arg-type]
        fp=BytesIO(b""),
    )
    with (
        patch("urllib.request.urlopen", side_effect=err),
        pytest.raises(MiaoshouApiError) as exc,
    ):
        c.orders.batch_create_async(order_list=[])
    assert exc.value.code == 404


def test_call_unparseable_response(monkeypatch):
    c = MiaoshouClient(license_id="LIC", company_secret="SECRET")
    fake_resp = _make_urlopen_response({"code": "not-an-int", "message": "x"})
    with (
        patch("urllib.request.urlopen", return_value=fake_resp),
        pytest.raises(MiaoshouApiError) as exc,
    ):
        c.orders.batch_create_async(order_list=[])
    assert exc.value.code == 0
    assert "无法解析响应" in exc.value.message


def test_all_endpoint_namespaces_present():
    c = MiaoshouClient(license_id="LIC", company_secret="SECRET")
    namespaces = [
        "orders",
        "fees",
        "refunds",
        "arbitrations",
        "closes",
        "complaints",
        "queries",
        "accounts",
        "products",
        "logistics",
        "aftersales",
        "tests",
    ]
    for ns in namespaces:
        assert hasattr(c, ns), f"missing namespace: {ns}"
