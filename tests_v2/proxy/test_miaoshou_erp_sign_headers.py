"""Regression test: MiaoshouErpClient must SEND the computed HMAC signature.

Production fault (2026-09-01): the Wave-5 proxy client computed
``hmac_sha256_sign(...)`` in ``_call_erp`` but never attached it to the
request — upstream rejected every call with ``[code=signMissing] fail``.
The legacy ``miaoshou/miaoshou_erp_client.py`` sends x-app-key /
x-timestamp / x-sign headers; this test pins that contract on the v2
client so the signature can never be dropped again.
"""
from __future__ import annotations

import json

from tts_erp_v2.proxy.miaoshou import client as ms_client
from tts_erp_v2.proxy.miaoshou.client import MiaoshouErpClient


def test_call_erp_sends_hmac_headers(monkeypatch):
    captured: dict = {}

    def fake_post(url, body_bytes, timeout, headers=None):
        captured["url"] = url
        captured["headers"] = headers or {}
        captured["body"] = body_bytes
        return json.dumps({"result": "success", "code": "0", "data": {"ok": True}})

    monkeypatch.setattr(ms_client, "safe_http_post_json", fake_post)

    client = MiaoshouErpClient(app_id="TEST_AK", app_secret="TEST_SK")
    out = client._call_erp(path="/api/foo", body={"page": 1})

    assert out["result"] == "success"
    headers = captured["headers"]
    assert headers.get("x-app-key") == "TEST_AK"
    assert headers.get("x-timestamp", "").isdigit()
    sign = headers.get("x-sign", "")
    assert sign and sign == sign.lower() and len(sign) == 64  # lowercase sha256 hex


def test_call_erp_headers_include_extra_and_safe_default(monkeypatch):
    captured: dict = {}

    def fake_post(url, body_bytes, timeout, headers=None):
        captured["headers"] = headers or {}
        return json.dumps({"result": "success", "code": "0", "data": []})

    monkeypatch.setattr(ms_client, "safe_http_post_json", fake_post)
    client = MiaoshouErpClient(app_id="TEST_AK", app_secret="TEST_SK")
    client._call_erp(path="/api/bar", extra_headers={"x-custom": "1"})
    assert captured["headers"]["x-custom"] == "1"
    assert "x-sign" in captured["headers"]
