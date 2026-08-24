"""tts_erp Handler 路由层 miaoshou 集成测试 —— 不启动真实服务."""

from __future__ import annotations

import pytest


@pytest.fixture
def handler_stub():
    """构造一个最小 Handler 子类实例，不触发 socket 初始化."""
    from tts_erp import (
        _miaoshou_client_cache,
    )

    # 清空 cache，确保每次测试都重新读 env
    _miaoshou_client_cache.clear()

    class StubHandler:
        """只实现 _send 的最小 Handler（绕过 BaseHTTPRequestHandler.__init__）."""

        def __init__(self):
            self._sent = []

        def _send(self, code, obj):
            self._sent.append((code, obj))

    return StubHandler()


def test_unsupported_domain_returns_404(handler_stub):
    from tts_erp import _miaoshou_call_endpoint

    _miaoshou_call_endpoint(handler_stub, "unknown/thing", {})
    code, body = handler_stub._sent[0]
    assert code == 404
    assert "unknown miaoshou domain" in body["_error"]


def test_malformed_path_returns_400(handler_stub):
    from tts_erp import _miaoshou_call_endpoint

    _miaoshou_call_endpoint(handler_stub, "orders", {})
    code, body = handler_stub._sent[0]
    assert code == 400
    assert "domain/method 两段" in body["_error"]
    assert "supported_domains" in body


def test_unknown_method_returns_404(monkeypatch, handler_stub):
    """env 凭据都配上，但方法不存在 → 404 with supported_methods."""
    monkeypatch.setenv("MIAOSHOU_LICENSE_ID", "L")
    monkeypatch.setenv("MIAOSHOU_COMPANY_SECRET", "S")
    from tts_erp import _miaoshou_call_endpoint

    _miaoshou_call_endpoint(handler_stub, "orders/nonexistent_method", {})
    code, body = handler_stub._sent[0]
    assert code == 404
    assert "unknown method" in body["_error"]
    assert "batch_create_async" in body["supported_methods"]


def test_wrong_body_returns_400(monkeypatch, handler_stub):
    """env 凭据都配上，但 body 参数错（缺 orderNo） → 400."""
    monkeypatch.setenv("MIAOSHOU_LICENSE_ID", "L")
    monkeypatch.setenv("MIAOSHOU_COMPANY_SECRET", "S")
    from tts_erp import _miaoshou_call_endpoint

    # orders.pay(order_no, pay_type) — 不传 order_no → TypeError
    _miaoshou_call_endpoint(handler_stub, "orders/pay", {"pay_type": "wechat"})
    code, body = handler_stub._sent[0]
    assert code == 400
    assert "参数错误" in body["_error"]


def test_callback_all_unknown_status(handler_stub):
    """POST /miaoshou/callback/all with unknown orderStatus → 400."""
    # 直接调 dispatch_callback 验证逻辑（Handler 内部就是这么调的）
    from miaoshou.callbacks.router import (
        dispatch_callback,  # type: ignore[reportMissingImports]  # type: ignore[reportMissingImports]
    )

    status, body = dispatch_callback("nonexistent", {"thirdOrderId": "X"})
    assert status == 400
    assert body["code"] == 400


def test_domains_complete():
    """所有 12 个业务域都在白名单里."""
    from tts_erp import _MIAOSHOU_DOMAINS

    expected = {
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
    }
    assert expected == _MIAOSHOU_DOMAINS
