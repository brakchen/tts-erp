"""4 个 bug 修复 + 官方文档签名向量验证（TDD 测试）.

bug 修复历史：
  P0 — JSON body 用 compact separators (',', ':')
  P1 — HTTPError 500 解析 JSON body 提取业务 code
  P1 — model_validate 包成 MiaoshouApiError(500)
  P2 — get_site_default_setting 传 body=None

官方文档签名向量（apifox project 8149572 / 5.2 Python 示例）：
  app_secret + path + str(ts) + app_key + body + app_secret
  → HMAC-SHA256 → hex
  → expected: e5184ec50310347f408b9aa933b9690e858a536f5ce15bbda2fd40c97285feb7
"""

from __future__ import annotations

import pytest

pytestmark = [pytest.mark.domain_miaoshou, pytest.mark.layer_unit]

import io
import json
import urllib.error
from unittest.mock import patch

import pytest
from pydantic import BaseModel, ConfigDict

from miaoshou import MiaoshouErpClient
from miaoshou.miaoshou_client import MiaoshouApiError
from miaoshou.miaoshou_erp_client import _safe_validate
from miaoshou.miaoshou_signing import hmac_sha256_sign

# ============================================================
# 🔴 P0: body_json 必须用 compact separators
# ============================================================


def _captured_body_json(captured_request):
    """从 mock request 里把 body_json 解析出来."""
    raw = captured_request.data.decode("utf-8")
    return raw


def test_body_json_uses_compact_serializers():
    """🔴 P0 修复回归测试：body 不能含 ``": "`` 或 ``", "``.

    官方文档 5.2 Python 示例 body 是 ``{"orderNo":"ORD2024001","amount":100.00}``（无空格）。
    之前用 ``json.dumps(body)`` 会生成 ``{"orderNo": "ORD2024001", "amount": 100.0}``（带空格），
    导致 canonical 字符串跟服务端不一致 → 签错。
    """
    captured = {}

    class FakeResp:
        def read(self):
            return b'{"result":"success","code":"0","data":{}}'

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def fake_urlopen(req, timeout=None):
        captured["request"] = req
        return FakeResp()

    client = MiaoshouErpClient(
        app_id="LIC",
        app_secret="SECRET",
        base_url="http://test",
        timeout=5,
    )
    with patch(
        "miaoshou.miaoshou_erp_client.urllib.request.urlopen", side_effect=fake_urlopen
    ):
        client._call_erp(
            path="/open/v1/test",
            body={
                "orderNo": "ORD2024001",
                "amount": 100.00,
                "nested": {"a": 1, "b": 2},
            },
        )

    body_json = _captured_body_json(captured["request"])
    # 不能有任何空格： ": " 和 ", " 都不应出现
    assert '": "' not in body_json, f"body 含 '\": \"'，签名会失败：{body_json!r}"
    assert '", "' not in body_json, f"body 含 '\", \"'，签名会失败：{body_json!r}"
    # 还要序列化对
    parsed = json.loads(body_json)
    assert parsed["orderNo"] == "ORD2024001"
    assert parsed["amount"] == 100.00
    assert parsed["nested"]["a"] == 1


def test_body_json_empty_body_serializes_as_empty_string():
    """空 body 应该拼空串（不是 '{}'）—— 官方文档 §5.3 没 body 需拼空串."""
    captured = {}

    class FakeResp:
        def read(self):
            return b'{"result":"success","code":"0","data":{}}'

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def fake_urlopen(req, timeout=None):
        captured["request"] = req
        return FakeResp()

    client = MiaoshouErpClient(
        app_id="L", app_secret="S", base_url="http://test", timeout=5
    )
    with patch(
        "miaoshou.miaoshou_erp_client.urllib.request.urlopen", side_effect=fake_urlopen
    ):
        client._call_erp(path="/open/v1/test")  # no body

    body_json = _captured_body_json(captured["request"])
    assert body_json == "", f"空 body 应该是 '' 字符串，实际 {body_json!r}"


# ============================================================
# 🟠 P1: HTTPError 500 解析 JSON body 提取 code
# ============================================================


def _make_http_error(status, body):
    """构造 urllib HTTPError（headers 是 IO 兼容对象）."""
    return _HTTPError(
        url="http://test/open/v1/test",
        code=status,
        msg="Internal Error",
        hdrs={},
        body=body,
    )


class _HTTPError(urllib.error.HTTPError):
    """HTTPError 但支持 ``body=bytes`` 参数.

    标准 HTTPError 的 fp 是 file-like，需在构造后 write。直接用
    BytesIO 替代。
    """

    def __init__(self, url, code, msg, hdrs, body):
        super().__init__(url, code, msg, hdrs, io.BytesIO(body))
        self._raw_body = body

    def read(self, *args, **kw):  # type: ignore[override]
        return self._raw_body


def test_call_erp_500_response_extracts_business_code():
    """🟠 P1 修复回归：500 + JSON body 里 ``code=signInvalid`` → MiaoshouApiError.code=signInvalid."""
    err_body = json.dumps(
        {
            "result": "fail",
            "code": "signInvalid",
            "reason": "签名验证不通过",
        }
    ).encode("utf-8")

    client = MiaoshouErpClient(
        app_id="LIC",
        app_secret="SECRET",
        base_url="http://test",
        timeout=5,
    )
    with (
        patch(
            "miaoshou.miaoshou_erp_client.urllib.request.urlopen",
            side_effect=_make_http_error(500, err_body),
        ),
        pytest.raises(MiaoshouApiError) as exc_info,
    ):
        client._call_erp(path="/open/v1/test", body={"x": 1})

    err = exc_info.value
    # code 应该用 JSON 里 "signInvalid"，不是 HTTP 500
    assert err.code == "signInvalid", f"expected 'signInvalid', got {err.code!r}"
    # message 应含 reason
    assert "签名验证不通过" in err.message
    # message 应含 HTTP 状态码作 context
    assert "HTTP 500" in err.message
    assert "fail:" in err.message or "fail: " in err.message


def test_call_erp_500_response_non_json_body_falls_back_to_http_code():
    """500 + 非 JSON body（HTML 错误页等）→ 退回 HTTP code 500."""
    non_json_body = b"<html><body>500 Internal Server Error</body></html>"

    client = MiaoshouErpClient(
        app_id="LIC",
        app_secret="SECRET",
        base_url="http://test",
        timeout=5,
    )
    with (
        patch(
            "miaoshou.miaoshou_erp_client.urllib.request.urlopen",
            side_effect=_make_http_error(500, non_json_body),
        ),
        pytest.raises(MiaoshouApiError) as exc_info,
    ):
        client._call_erp(path="/open/v1/test")

    err = exc_info.value
    # JSON parse 失败，回退到 HTTP code
    assert err.code == 500
    # message 至少含 body 前缀
    assert "<html>" in err.message or "HTTP 500" in err.message


# ============================================================
# 🟠 P1: _safe_validate 包 ValidationError
# ============================================================


class _SampleModel(BaseModel):
    """测试用 schema——shopId 是必填，没默认值."""

    model_config = ConfigDict(extra="allow")
    shopId: int  # 必填
    site: str = ""


def test_safe_validate_opt_extra_field_keeps_extras():
    """合法 payload 直接返回 Pydantic 模型，extra='allow' 保留额外字段."""
    m = _safe_validate(
        {"shopId": 1, "site": "VN", "extraField": "ignored"}, _SampleModel
    )
    assert m.shopId == 1
    assert m.site == "VN"
    # extra="allow" 时额外字段会被保留（pydantic v2 通过 model_extra / __pydantic_extra__ 访问）
    extras = getattr(m, "model_extra", None) or getattr(m, "__pydantic_extra__", None)
    assert extras and extras.get("extraField") == "ignored"


def test_safe_validate_wraps_validation_error():
    """🟠 P1 修复回归：缺字段/类型错 → 抛 MiaoshouApiError(500)，不抛 ValidationError."""
    # shopId 缺 → ValidationError；现在应被包成 MiaoshouApiError
    with pytest.raises(MiaoshouApiError) as exc_info:
        _safe_validate({"site": "VN"}, _SampleModel)

    err = exc_info.value
    assert err.code == 500
    assert "_SampleModel" in err.message
    assert "shopId" in err.message or "validation" in err.message.lower()


def test_safe_validate_type_mismatch_wraps_validation_error():
    """shopId 类型错（传 str "123" 进来）→ ValidationError → 包成 MiaoshouApiError."""
    with pytest.raises(MiaoshouApiError) as exc_info:
        _safe_validate({"shopId": "not-an-int", "site": "VN"}, _SampleModel)
    assert exc_info.value.code == 500


def test_safe_validate_in_endpoint_returns_miaoshou_api_error_on_bad_schema():
    """端点级：服务端 schema 变化 → 用户拿 MiaoshouApiError 不是 ValidationError."""

    # 静态校验：所有 endpoint 方法都该用 _safe_validate 而不是裸 model_validate
    import subprocess

    result = subprocess.run(
        ["grep", "-rn", "model_validate(", "miaoshou/endpoints/"],
        check=False,
        capture_output=True,
        text=True,
    )
    bad_lines = [
        line
        for line in result.stdout.split("\n")
        if "model_validate" in line
        and "_safe_validate" not in line
        and "class " not in line
        and "Pydantic" not in line
    ]
    assert not bad_lines, "以下 endpoint 方法未用 _safe_validate：\n" + "\n".join(
        bad_lines
    )


# ============================================================
# 🟡 P2: get_site_default_setting 传 body=None
# ============================================================


def test_get_site_default_setting_passes_none_body():
    """🟡 P2 修复回归：get_site_default_setting 不该在 body 里塞 {}."""
    captured = {}

    class FakeResp:
        def read(self):
            return b'{"result":"success","code":"0","data":{}}'

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def fake_urlopen(req, timeout=None):
        captured["request"] = req
        return FakeResp()

    client = MiaoshouErpClient(
        app_id="LIC",
        app_secret="SECRET",
        base_url="http://test",
        timeout=5,
    )
    with patch(
        "miaoshou.miaoshou_erp_client.urllib.request.urlopen", side_effect=fake_urlopen
    ):
        client.tk_collect_box.get_site_default_setting()

    body_json = _captured_body_json(captured["request"])
    # spec 没要 body，应该是空字符串
    assert body_json == "", (
        f"get_site_default_setting 应不传 body，实际发了 {body_json!r}"
    )


# ============================================================
# 推荐: 官方文档签名向量验证
# ============================================================


def test_hmac_sha256_sign_matches_official_doc_example():
    """锁死官方文档 5.2 Python 示例的签名结果.

    canonical = appSecret + path + str(timestamp_sec) + appKey + bodyJson + appSecret
    signature = HMAC-SHA256(appSecret, canonical).hexdigest()

    Doc:  https://s.apifox.cn/fd54e57e-9b98-4c34-bada-306221c39e68 (项目 8149572 / §5.2)
    """
    app_secret = "as_xxxxxxxxxxxxxxxx"
    path = "/open/v1/order/create"
    timestamp_sec = 1700000000
    app_key = "ak_1234567890abcdef"
    body_json = '{"orderNo":"ORD2024001","amount":100.00}'

    actual = hmac_sha256_sign(
        app_secret=app_secret,
        path=path,
        timestamp_sec=timestamp_sec,
        app_key=app_key,
        body_json=body_json,
    )
    expected = "e5184ec50310347f408b9aa933b9690e858a536f5ce15bbda2fd40c97285feb7"
    assert actual == expected, f"签名向量偏移！\n  实测: {actual}\n  锁定: {expected}"


def test_hmac_sha256_sign_empty_body_matches_doc_convention():
    """官方 §5.3：POST 无 body 需拼空字符串 ``""``."""
    # canonical = appSecret + path + ts + appKey + "" + appSecret
    import hashlib
    import hmac

    canonical = "secret" + "/x" + "100" + "key" + "" + "secret"
    expected = hmac.new(
        b"secret", canonical.encode("utf-8"), hashlib.sha256
    ).hexdigest()
    actual = hmac_sha256_sign(
        app_secret="secret",
        path="/x",
        timestamp_sec=100,
        app_key="key",
        body_json="",
    )
    assert actual == expected


def test_check_erp_case_insensitive_result():
    """🔵 P3 修复回归：result 字段大小写不敏感."""
    from miaoshou.endpoints.collection_box import _check_erp
    # 大写 SUCCESS 也要通过
    _check_erp({"result": "SUCCESS", "code": "0", "data": {}})
    # 混合大小写也通过
    _check_erp({"result": "Success", "code": "0", "data": {}})
    # 小写依然通过（regression）
    _check_erp({"result": "success", "code": "0", "data": {}})

    # fail 大小写都抛
    from miaoshou.miaoshou_client import MiaoshouApiError
    for bad in ("fail", "FAIL", "Fail", ""):
        with pytest.raises(MiaoshouApiError):
            _check_erp({"result": bad, "code": "0", "data": {}})


def test_list_shops_pagination_loop():
    """🔵 修复: list_shops 自动分页循环拿全所有店铺."""
    from unittest.mock import MagicMock, patch

    from miaoshou.miaoshou_erp_client import MiaoshouErpClient
    client = MiaoshouErpClient(app_id="L", app_secret="S")
    # 模拟服务端：pageNo=1 返回满页(100 个)，pageNo=2 返回满页(50 个)，pageNo=3 返回空
    page1_shops = [{"shopId": i, "site": "VN", "platformShopName": f"s{i}", "platform": "tiktok"} for i in range(100)]
    page2_shops = [{"shopId": 100 + i, "site": "VN", "platformShopName": f"s{100+i}", "platform": "tiktok"} for i in range(50)]
    page1_resp = {"result": "success", "code": "0", "data": {"shopList": page1_shops, "total": 150}}
    page2_resp = {"result": "success", "code": "0", "data": {"shopList": page2_shops, "total": 150}}
    page3_resp = {"result": "success", "code": "0", "data": {"shopList": [], "total": 150}}
    responses = iter([page1_resp, page2_resp, page3_resp])

    def fake_urlopen(req, timeout=None):
        resp = MagicMock()
        resp.read.return_value = json.dumps(next(responses)).encode("utf-8")
        resp.__enter__ = lambda s: s
        resp.__exit__ = lambda s, *a: None
        return resp

    with patch("urllib.request.urlopen", side_effect=fake_urlopen):
        result = client.shops.list(platform="tiktok", site="VN", page_no=1, page_size=100)

    # 期望：分页循环拿全 150 个
    all_shops = result.data.shopList
    assert len(all_shops) == 150, f"分页循环失败，只拿到 {len(all_shops)} 个，应该是 150 个"
    assert all_shops[0].shopId == 0
    assert all_shops[99].shopId == 99
    assert all_shops[100].shopId == 100
    assert all_shops[149].shopId == 149


def test_call_erp_4xx_response_also_parses_business_code():
    """🔵 新 bug: 4xx 错误码（比如 401 / 403）也应该解析业务 code，不只 500."""
    import io
    import urllib.error
    from unittest.mock import patch

    from miaoshou.miaoshou_erp_client import MiaoshouApiError, MiaoshouErpClient

    client = MiaoshouErpClient(app_id="L", app_secret="S")
    err = urllib.error.HTTPError(
        url="x", code=401, msg="unauthorized",
        hdrs={}, fp=io.BytesIO(b'{"result":"fail","code":"appNoPermission","reason":"no permission"}')
    )
    with patch("urllib.request.urlopen", side_effect=err), pytest.raises(MiaoshouApiError) as exc_info:
            client._call_erp(path="/test", body={})
    # 业务码应是 appNoPermission（不是 HTTP 401）
    assert exc_info.value.code == "appNoPermission", f"4xx 应该解析业务码，得到 {exc_info.value.code!r}"
