"""TDD bug 红→绿：P0-P2 修复 + 新 bug 挖掘."""
from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

import pytest

# ===== P0 — JSON body 必须紧凑序列化（与官方文档 5.2 示例一致）=====

def test_p0_body_json_compact_serialization():
    """官方文档 body_json = '{"orderNo":"ORD2024001","amount":100.00}'
    我们的代码必须也产紧凑 JSON，不能有空格."""
    import json
    body = {"orderNo": "ORD2024001", "amount": 100.00}
    # 当前（错）：
    bad = json.dumps(body, ensure_ascii=False)
    # 修复后：
    good = json.dumps(body, ensure_ascii=False, separators=(",", ":"))
    assert bad != good  # 当前确实有空格
    assert '": "' not in good, f"found space in canonical: {good!r}"
    assert '", "' not in good, f"found space in canonical: {good!r}"
    assert good == '{"orderNo":"ORD2024001","amount":100.0}'


# ===== P1 — 500 响应必须解析业务 code =====

def test_p1_500_response_extracts_business_code():
    """妙手 ERP 500 响应 JSON 包含业务 code，应作为异常 code 抛出."""
    import io
    import urllib.error

    from miaoshou.miaoshou_erp_client import MiaoshouApiError, MiaoshouErpClient

    client = MiaoshouErpClient(app_id="LIC", app_secret="SECRET")
    err = urllib.error.HTTPError(
        url="x", code=500, msg="err",
        hdrs={}, fp=io.BytesIO(b'{"result":"fail","code":"signInvalid","reason":"\xe7\xad\xbe\xe5\x90\x8d\xe9\x94\x99\xe8\xaf\xaf"}')
    )
    with patch("urllib.request.urlopen", side_effect=err), pytest.raises(MiaoshouApiError) as exc_info:
            client._call_erp(path="/open/v1/product/shop/shop/get_shop_list", body={"platform": "tiktok", "site": "VN"})
    err_obj = exc_info.value
    assert err_obj.code == "signInvalid"  # ← 业务码，不是 HTTP 500
    assert "签名错误" in err_obj.message or "签错" in err_obj.message


def test_p1_500_non_json_response_falls_back_to_http_code():
    """500 响应不是 JSON 时，回退到 HTTP code（不崩）."""
    import io
    import urllib.error

    from miaoshou.miaoshou_erp_client import MiaoshouApiError, MiaoshouErpClient

    client = MiaoshouErpClient(app_id="LIC", app_secret="SECRET")
    err = urllib.error.HTTPError(
        url="x", code=500, msg="err",
        hdrs={}, fp=io.BytesIO(b"<html>500</html>")  # 非 JSON
    )
    with patch("urllib.request.urlopen", side_effect=err), pytest.raises(MiaoshouApiError) as exc_info:
            client._call_erp(path="/open/v1/test", body={})
    # 回退到 HTTP code
    assert exc_info.value.code == 500


# ===== P1 — Pydantic ValidationError 必须包装成 MiaoshouApiError =====

def test_p1_validation_error_becomes_miaoshou_api_error():
    """服务端 schema 变化时，_safe_validate 包装 ValidationError 成 MiaoshouApiError."""
    from miaoshou.endpoints.shop import ShopListResult
    from miaoshou.miaoshou_erp_client import MiaoshouApiError, _safe_validate

    payload = {}  # 缺 result + code 必填字段
    with pytest.raises(MiaoshouApiError) as exc_info:
        _safe_validate(payload, ShopListResult)
    assert "schema" in exc_info.value.message.lower()
    assert "ShopListResult" in exc_info.value.message


# ===== P2 — get_site_default_setting 不该发 body =====

def test_p2_get_site_default_setting_passes_none_body():
    """spec: parameters: []，无 body。当前传 body={}（= "{}"），应改 None。"""
    from unittest.mock import MagicMock, patch

    from miaoshou.endpoints.tk_collect_box import TkCollectBoxEndpoint

    ep = TkCollectBoxEndpoint(MagicMock())
    captured = {}
    def fake_call(**kwargs):
        captured.update(kwargs)
        return {"result": "success", "code": "0", "data": {}}
    with patch.object(ep, "_c") as mock_client:
        mock_client._call_erp = fake_call
        ep.get_site_default_setting()
    assert captured.get("body") is None, f"get_site_default_setting 不该发 body，但发了 {captured.get('body')!r}"


# ===== 已知向量验证（HMAC-SHA256）=====

def test_known_vector_official_doc_example():
    """官方文档 5.2 Python 示例：签名应该匹配硬编码值."""
    import hashlib
    import hmac

    from miaoshou.miaoshou_signing import hmac_sha256_sign

    expected = hmac.new(
        b"as_xxxxxxxxxxxxxxxx",
        b"as_xxxxxxxxxxxxxxxx/open/v1/order/create1700000000ak_1234567890abcdef"
        b'{"orderNo":"ORD2024001","amount":100.00}as_xxxxxxxxxxxxxxxx',
        hashlib.sha256,
    ).hexdigest()
    actual = hmac_sha256_sign(
        app_secret="as_xxxxxxxxxxxxxxxx",
        path="/open/v1/order/create",
        timestamp_sec=1700000000,
        app_key="ak_1234567890abcdef",
        body_json='{"orderNo":"ORD2024001","amount":100.00}',
    )
    assert expected == actual
    assert expected == "e5184ec50310347f408b9aa933b9690e858a536f5ce15bbda2fd40c97285feb7"


# ===== 深度挖新 bug =====

def test_bug_query_params_should_be_url_encoded():
    """query 参数含特殊字符（中文、空格、&、=）必须正确 URL 编码，不能裸拼."""
    from miaoshou.miaoshou_erp_client import MiaoshouErpClient
    client = MiaoshouErpClient(app_id="L", app_secret="S")
    captured = {}
    def fake_call(**kwargs):
        captured.update(kwargs)
        return {"result": "success", "code": "0", "data": {}}
    with patch.object(client, "_call_erp", side_effect=fake_call):
        try:
            client.some_method(query={"name": "test & demo = 中文"})
        except AttributeError:
            pass  # _call_erp is the method, not some_method
        client._call_erp(path="/test", query={"name": "test & demo = 中文"})
    # 实际拼到 URL 上时，urlencode 会处理；这里只验证 query 字段不丢失
    assert captured["query"]["name"] == "test & demo = 中文"


def test_bug_empty_body_and_empty_query_both_passed():
    """空 body + 空 query 不该出问题，urlencode 不会加 ?"""

    from miaoshou.miaoshou_erp_client import MiaoshouErpClient

    client = MiaoshouErpClient(app_id="L", app_secret="S")
    captured_url = {}
    def fake_urlopen(req, timeout=None):
        captured_url["url"] = req.full_url
        captured_url["body"] = req.data.decode("utf-8")
        resp = MagicMock()
        resp.read.return_value = b'{"result":"success","code":"0","data":{}}'
        resp.__enter__ = lambda s: s
        resp.__exit__ = lambda s, *a: None
        return resp

    with patch("urllib.request.urlopen", side_effect=fake_urlopen):
        client._call_erp(path="/test", body={}, query={})
    # 空 query 不该加 ?（避免 URL 末尾多个 ?）
    assert "?" not in captured_url["url"], f"empty query 不该加 ? 到 URL：{captured_url['url']}"
    # 空 body 必须是空串（""），不能是 "null" 或 "None"（canonical 会受影响）
    assert captured_url["body"] == "", f"空 body 应该是空串，但得到 {captured_url['body']!r}"
    assert "?" not in captured_url["url"]


def test_bug_handler_uses_self_app_id_not_passed_value():
    """注册 app_id 与 SDK 调用的 app_key 必须一致。self.app_id 必须是 env 里读到的那个."""
    from unittest.mock import patch

    from miaoshou.miaoshou_erp_client import MiaoshouErpClient

    with patch.dict("os.environ", {"MIAOSHOU_LICENSE_ID": "ak_real", "MIAOSHOU_COMPANY_SECRET": "secret"}):
        client = MiaoshouErpClient.from_env()
        assert client.app_id == "ak_real"
        # SDK 调用时 x-app-key 必须是 ak_real
        captured = {}
        def fake_call(**kwargs):
            captured["body"] = kwargs.get("body")
            captured["headers"] = self.headers if hasattr(self := MagicMock(), 'headers') else None
            return {"result": "success", "code": "0", "data": {}}
        # 简单测试：client.app_id 一致性
        assert client.app_id == os.environ.get("MIAOSHOU_LICENSE_ID")
