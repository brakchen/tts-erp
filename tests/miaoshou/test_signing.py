"""miaoshou_signing 单元测试（不依赖网络）."""

from __future__ import annotations

import json

import pytest

from miaoshou.miaoshou_signing import (  # type: ignore[reportMissingImports]
    build_envelope,
    build_sign,
    md5_upper,
    now_ms,
)


def test_md5_upper_basic():
    # 已知向量：md5("hello") = 5d41402abc4b2a76b9719d911017c592
    assert md5_upper("hello") == "5D41402ABC4B2A76B9719D911017C592"
    assert len(md5_upper("anything")) == 32
    assert md5_upper("test") == md5_upper("test")  # 确定性


def test_build_sign_changes_with_secret():
    sig_a = build_sign({"k": "v"}, "secret-A")
    sig_b = build_sign({"k": "v"}, "secret-B")
    assert sig_a != sig_b
    assert sig_a.isupper() and len(sig_a) == 32


def test_build_sign_doc_824327_vector():
    """Apifox doc-824327 验证向量：
    busData = base64({"orderNo": "P5151145027"}) = eyJvcmRlck5vIjogIlA1MTUxMTQ1MDI3In0=
    sign = MD5(busData + companySecret).upper()

    锁定向量（实测）：
    MD5(base64({"orderNo": "P5151145027"}) + "TEST-SECRET") = 809DF04F69DB79334AA1A796082BD58E
    """
    sig = build_sign({"orderNo": "P5151145027"}, "TEST-SECRET")
    expected = "809DF04F69DB79334AA1A796082BD58E"  # 锁定向量（实测）
    assert sig == expected, (
        f"签名向量偏移！\n  实测: {sig}\n  锁定: {expected}\n"
        f"  busData = {__import__('base64').b64encode(json.dumps({'orderNo': 'P5151145027'}, ensure_ascii=False).encode()).decode()}"
    )


def test_build_envelope_structure():
    env = build_envelope(
        {"orderNo": "X"}, company_secret="S", license_id="L", timestamp_ms=1700000000000
    )
    assert env["licenseId"] == "L"
    assert env["companySecret"] == "S"
    assert env["timestamp"] == 1700000000000
    assert env["sign"] == md5_upper(env["busData"] + "S")
    # busData base64 解码后等于 business_params
    import base64

    assert json.loads(base64.b64decode(env["busData"]).decode("utf-8")) == {
        "orderNo": "X"
    }


def test_build_envelope_auto_timestamp():
    t1 = now_ms()
    env = build_envelope({"x": 1}, company_secret="S", license_id="L")
    t2 = now_ms()
    assert t1 <= env["timestamp"] <= t2


def test_now_ms_monotonic():
    a = now_ms()
    b = now_ms()
    assert a <= b
    assert isinstance(a, int)


@pytest.mark.parametrize(
    "params",
    [
        {"orderNo": "X"},
        {"orderNo": "X", "amount": 100},
        {"嵌套": {"中文": "字段"}, "列表": [1, 2, 3]},
    ],
)
def test_sign_roundtrip_chinese(params):
    """中文 / 嵌套 / 列表场景下，busData 解码 = 原 JSON。"""
    import base64

    sig = build_sign(params, "secret")
    env = build_envelope(
        params, company_secret="secret", license_id="L", timestamp_ms=1
    )
    assert env["sign"] == sig
    decoded = json.loads(base64.b64decode(env["busData"]).decode("utf-8"))
    assert decoded == params
