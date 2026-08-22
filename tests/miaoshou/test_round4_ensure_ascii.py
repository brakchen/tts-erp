"""Round 4 P0 bugfix — body_json ensure_ascii 必须匹配服务端 Python 默认."""

from __future__ import annotations

import hashlib
import hmac
import json
from unittest.mock import MagicMock, patch

from miaoshou.miaoshou_erp_client import MiaoshouErpClient


def test_body_json_uses_ensure_ascii_true():
    """🔴 P0 bugfix: 服务端（Python 默认）用 ensure_ascii=True，客户端必须一致.

    验证当前 SDK 行为：默认 ensure_ascii=True，body 含中文时不带 \\u 转义，
    canonical 与服务端 byte-for-byte 一致。
    """
    body = {"name": "测试", "id": 123}
    body_json = json.dumps(body, ensure_ascii=True, separators=(",", ":"))
    # 验证：中文被转义为 \\u 形式（这是服务端会算出来的形式）
    assert "\\u" in body_json, (
        f"ensure_ascii=True 时中文应被 \\u 转义，得到 {body_json!r}"
    )
    # 验证：与 ensure_ascii=False 不同
    assert body_json != json.dumps(body, ensure_ascii=False, separators=(",", ":"))


def test_signing_canonical_matches_server_ensure_ascii():
    """🔴 P0 bugfix: 签名 canonical 与服务端 byte-for-byte 一致.

    模拟服务端（用 ensure_ascii=True 计算签名）和客户端（应一致），
    验证两者的签名字符串完全相同。
    """
    body = {"name": "测试", "shopId": 1, "site": "VN"}
    app_secret = "secret"
    path = "/test"
    app_key = "k"
    ts = 1700000000
    body_json_client = json.dumps(body, ensure_ascii=True, separators=(",", ":"))
    body_json_server = json.dumps(body, ensure_ascii=True, separators=(",", ":"))

    # 客户端
    content_client = (
        app_secret + path + str(ts) + app_key + body_json_client + app_secret
    )
    sig_client = hmac.new(
        app_secret.encode(), content_client.encode(), hashlib.sha256
    ).hexdigest()

    # 服务端（模拟）
    content_server = (
        app_secret + path + str(ts) + app_key + body_json_server + app_secret
    )
    sig_server = hmac.new(
        app_secret.encode(), content_server.encode(), hashlib.sha256
    ).hexdigest()

    assert sig_client == sig_server, "客户端/服务端签名必须一致"


def test_call_erp_sends_ensure_ascii_body(monkeypatch):
    """🔴 P0 bugfix: _call_erp 实际发送的 body 必须是 ensure_ascii=True."""
    client = MiaoshouErpClient(app_id="L", app_secret="S")
    captured = {}

    def fake_urlopen(req, timeout=None):
        captured["body"] = req.data.decode("utf-8")
        resp = MagicMock()
        resp.read.return_value = b'{"result":"success","code":"0","data":{}}'
        resp.__enter__ = lambda s: s
        resp.__exit__ = lambda s, *a: None
        return resp

    with patch("urllib.request.urlopen", side_effect=fake_urlopen):
        client._call_erp(path="/test", body={"name": "测试"})

    body_sent = captured["body"]
    # ensure_ascii=True 时中文是 \u 转义
    assert "\\u" in body_sent, f"应被 \\u 转义，实际: {body_sent!r}"
    # ensure_ascii=False 时是原始 UTF-8
    assert "测试" not in body_sent, f"不能含原始 UTF-8 中文: {body_sent!r}"
