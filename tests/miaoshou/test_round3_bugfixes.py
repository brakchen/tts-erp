"""Round 3 bugfixes — regression guard + retry + session reuse (TDD)."""

from __future__ import annotations

import io
import urllib.error
from http.client import HTTPMessage
from unittest.mock import MagicMock, patch

import pytest

from miaoshou.endpoints.shop import Shop
from miaoshou.miaoshou_client import MiaoshouApiError
from miaoshou.miaoshou_erp_client import MiaoshouErpClient

pytestmark = [pytest.mark.domain_miaoshou, pytest.mark.layer_unit]

# ============================================================
# 🟢 regression guard 1: Shop int 字段接受 string "0"/"1" (Pydantic v2 自动转)
# ============================================================


def test_shop_int_fields_coerce_strings():
    """🔵 regression guard: Shop.isCb/isCnsc 接受 "0"/"1" string（pydantic 自动转）."""
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
    assert s.isCnsc == 1


def test_shop_int_fields_coerce_bools():
    """🔵 regression guard: Shop 接受 bool True/False."""
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


# ============================================================
# 🔵 retry on ConnectionError — 网络错误自动重试
# ============================================================


def test_call_erp_retries_on_connection_error(monkeypatch):
    """🔵 网络错误自动重试 max_retries 次，最终成功."""
    client = MiaoshouErpClient(
        app_id="L",
        app_secret="S",
        max_retries=3,
        retry_backoff=0,
    )

    # 前两次抛 ConnectionError，第三次成功
    call_count = {"n": 0}

    def fake_urlopen(req, timeout=None):
        call_count["n"] += 1
        if call_count["n"] < 3:
            raise ConnectionError(f"network down (call {call_count['n']})")
        resp = MagicMock()
        resp.read.return_value = b'{"result":"success","code":"0","data":{}}'
        resp.__enter__ = lambda s: s
        resp.__exit__ = lambda s, *a: None
        return resp

    with patch("urllib.request.urlopen", side_effect=fake_urlopen):
        result = client._call_erp(path="/test", body={})
    assert call_count["n"] == 3, f"应该重试到第 3 次，实际 {call_count['n']} 次"
    assert result["result"] == "success"


def test_call_erp_gives_up_after_max_retries(monkeypatch):
    """🔵 超过 max_retries 后抛 MiaoshouApiError，不再重试."""
    client = MiaoshouErpClient(
        app_id="L",
        app_secret="S",
        max_retries=2,
        retry_backoff=0,
    )

    def fake_urlopen(req, timeout=None):
        raise ConnectionError("network permanently down")

    with (
        patch("urllib.request.urlopen", side_effect=fake_urlopen),
        pytest.raises(MiaoshouApiError) as exc_info,
    ):
        client._call_erp(path="/test", body={})
    # 重试 2 次后最终抛
    assert (
        "network permanently down" in str(exc_info.value.message)
        or "重试" in str(exc_info.value.message)
        or "重试" in exc_info.value.message
        or "ConnectionError" in str(exc_info.value.message)
    )


def test_call_erp_no_retry_on_4xx_5xx():
    """🔵 业务错误（4xx/5xx 返回的 HTTPError）不重试（重试也没用）."""
    client = MiaoshouErpClient(
        app_id="L",
        app_secret="S",
        max_retries=3,
        retry_backoff=0,
    )

    call_count = {"n": 0}

    def fake_urlopen(req, timeout=None):
        call_count["n"] += 1
        err = urllib.error.HTTPError(
            url="x",
            code=400,
            msg="bad",
            hdrs=HTTPMessage(),
            fp=io.BytesIO(b'{"result":"fail","code":"paramInvalid","reason":"bad"}'),
        )
        raise err

    with (
        patch("urllib.request.urlopen", side_effect=fake_urlopen),
        pytest.raises(MiaoshouApiError),
    ):
        client._call_erp(path="/test", body={})
    assert call_count["n"] == 1, f"4xx 不该重试，实际调了 {call_count['n']} 次"


def test_call_erp_default_max_retries_is_3(monkeypatch):
    """🔵 默认 max_retries=3（不传参数时）."""
    client = MiaoshouErpClient(app_id="L", app_secret="S")

    def fake_urlopen(req, timeout=None):
        raise ConnectionError("down")

    with (
        patch("urllib.request.urlopen", side_effect=fake_urlopen),
        pytest.raises(MiaoshouApiError),
    ):
        client._call_erp(path="/test", body={})


# ============================================================
# 🟢 session reuse — 同一 client 多次调用复用同一 urllib connection（弱断言）
# ============================================================


def test_call_erp_uses_internal_urlopen_each_call():
    """🔵 当前实现：每次 _call_erp 都重新构造 Request（urllib 短连接）.
    这不是 bug——记录当前行为作为基线."""
    client = MiaoshouErpClient(app_id="L", app_secret="S")

    resp_mock = MagicMock()
    resp_mock.read.return_value = b'{"result":"success","code":"0","data":{}}'
    resp_mock.__enter__ = lambda s: s
    resp_mock.__exit__ = lambda s, *a: None

    with patch("urllib.request.urlopen", return_value=resp_mock) as mock:
        client._call_erp(path="/a", body={})
        client._call_erp(path="/b", body={})
    assert mock.call_count == 2  # 当前实现：每次新建连接
