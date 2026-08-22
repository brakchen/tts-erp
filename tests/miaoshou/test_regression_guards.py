"""回归守卫：把已确认的 Pydantic 行为锁住防回归."""

from __future__ import annotations

from miaoshou.endpoints.collection_box import CommonCollectBoxDetail
from miaoshou.endpoints.shop import Shop


def test_shop_int_fields_coerce_strings():
    """🔵 regression guard: Shop.isCb/isCnsc 接受 "0"/"1" 字符串（Pydantic v2 自动转）."""
    s = Shop.model_validate(
        {
            "shopId": 1,
            "site": "VN",
            "platform": "tiktok",
            "isCb": "0",
            "isCnsc": "1",
        }
    )
    assert s.isCb == 0
    assert isinstance(s.isCb, int)
    assert s.isCnsc == 1
    assert isinstance(s.isCnsc, int)


def test_shop_int_fields_coerce_booleans():
    """🔵 Shop.isCb/isCnsc 接受 True/False ↔ 1/0."""
    s = Shop.model_validate(
        {
            "shopId": 1,
            "site": "VN",
            "platform": "tiktok",
            "isCb": True,
            "isCnsc": False,
        }
    )
    assert s.isCb == 1
    assert s.isCnsc == 0


def test_collect_box_detail_string_fields_coerced_to_int():
    """🔵 CommonCollectBoxDetail.isCb/isCnsc（int | None）接受 "0"/"1" string（pydantic v2 自动转）."""
    d = CommonCollectBoxDetail.model_validate(
        {
            "commonCollectBoxDetailId": 1,
            "shopId": 100,
            "isCb": "1",
            "isCnsc": "0",
        }
    )
    # Pydantic v2 默认 strict mode off：自动 coerce string → int
    assert d.isCb == 1
    assert isinstance(d.isCb, int)
    assert d.isCnsc == 0


def test_signing_known_vector_official_doc():
    """🔵 regression guard: 官方文档 5.2 Python 示例签名向量."""
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
    assert actual == expected
    assert actual == "e5184ec50310347f408b9aa933b9690e858a536f5ce15bbda2fd40c97285feb7"
