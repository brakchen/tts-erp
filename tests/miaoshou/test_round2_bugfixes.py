"""Round 2 bugfixes — TDD 测试 for from_env + 4xx 错误区分."""

from __future__ import annotations

import io
import urllib.error
from http.client import HTTPMessage
from unittest.mock import patch

import pytest

from miaoshou.miaoshou_client import MiaoshouApiError
from miaoshou.miaoshou_erp_client import MiaoshouErpClient

# ===== bug 1: from_env 接受空字符串 =====


def test_from_env_rejects_empty_app_id():
    from unittest.mock import patch

    with patch.dict(
        "os.environ",
        {
            "MIAOSHOU_LICENSE_ID": "",
            "MIAOSHOU_COMPANY_SECRET": "secret",
            "MIAOSHOU_HTTP_TIMEOUT": "30",
        },
        clear=True,
    ), pytest.raises(RuntimeError, match="缺少"):
        MiaoshouErpClient.from_env()


def test_from_env_rejects_empty_app_secret():
    with patch.dict(
        "os.environ",
        {
            "MIAOSHOU_LICENSE_ID": "ak_xxx",
            "MIAOSHOU_COMPANY_SECRET": "",
            "MIAOSHOU_HTTP_TIMEOUT": "30",
        },
        clear=True,
    ), pytest.raises(RuntimeError, match="缺少"):
        MiaoshouErpClient.from_env()


def test_from_env_rejects_whitespace_only():
    """纯空白字符也应该被拒绝."""
    with patch.dict(
        "os.environ",
        {
            "MIAOSHOU_LICENSE_ID": "   ",
            "MIAOSHOU_COMPANY_SECRET": "\t\n",
            "MIAOSHOU_HTTP_TIMEOUT": "30",
        },
        clear=True,
    ), pytest.raises(RuntimeError, match="缺少"):
        MiaoshouErpClient.from_env()


# ===== bug 2: 4xx vs 5xx 区分 =====


def test_call_erp_4xx_returns_business_code():
    """4xx 也应提取 JSON body 里的业务 code，不只是 500."""
    client = MiaoshouErpClient(app_id="L", app_secret="S")
    err400 = urllib.error.HTTPError(
        url="x",
        code=400,
        msg="bad",
        hdrs=HTTPMessage(),
        fp=io.BytesIO(b'{"result":"fail","code":"paramInvalid","reason":"param err"}'),
    )
    with patch("urllib.request.urlopen", side_effect=err400), pytest.raises(MiaoshouApiError) as exc_info:
            client._call_erp(path="/test", body={})
    # 业务码是 paramInvalid，不是 HTTP 400
    assert exc_info.value.code == "paramInvalid"


def test_call_erp_5xx_returns_business_code():
    """5xx 同样提取业务 code."""
    client = MiaoshouErpClient(app_id="L", app_secret="S")
    err500 = urllib.error.HTTPError(
        url="x",
        code=500,
        msg="err",
        hdrs=HTTPMessage(),
        fp=io.BytesIO(b'{"result":"fail","code":"signInvalid","reason":"sign err"}'),
    )
    with patch("urllib.request.urlopen", side_effect=err500), pytest.raises(MiaoshouApiError) as exc_info:
            client._call_erp(path="/test", body={})
    assert exc_info.value.code == "signInvalid"


def test_call_erp_4xx_distinguished_from_5xx_in_message():
    """4xx vs 5xx 错误消息应区分，便于 debug."""
    client = MiaoshouErpClient(app_id="L", app_secret="S")

    for http_code, expected_marker in [(400, "HTTP 400"), (500, "HTTP 500")]:
        err = urllib.error.HTTPError(
            url="x",
            code=http_code,
            msg="err",
            hdrs=HTTPMessage(),
            fp=io.BytesIO(b'{"result":"fail","code":"err","reason":"x"}'),
        )
        with patch("urllib.request.urlopen", side_effect=err) as _, pytest.raises(MiaoshouApiError) as exc_info:
                client._call_erp(path="/test", body={})
        assert expected_marker in exc_info.value.message
