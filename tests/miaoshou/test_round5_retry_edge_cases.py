"""Round 5 — retry 边界条件 bugfix."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

from miaoshou.miaoshou_erp_client import MiaoshouErpClient


def test_max_retries_zero_still_makes_one_request():
    """🔴 bug: max_retries=0 时 range(1,1) 是空 → 循环不执行 → raw 永远 None → 崩."""
    client = MiaoshouErpClient(app_id="L", app_secret="S", max_retries=0)

    fake_resp = MagicMock()
    fake_resp.read.return_value = b'{"result":"success","code":"0","data":{}}'
    fake_resp.__enter__ = lambda s: s
    fake_resp.__exit__ = lambda s, *a: None

    call_count = {"n": 0}

    def fake_urlopen(req, timeout=None):
        call_count["n"] += 1
        return fake_resp

    with patch("urllib.request.urlopen", side_effect=fake_urlopen):
        result = client._call_erp(path="/test", body={})

    assert call_count["n"] >= 1, f"max_retries=0 应至少调 1 次，实际 {call_count['n']} 次"
    assert result["result"] == "success"


def test_max_retries_one_makes_exactly_one_request_success():
    """✅ 边界: max_retries=1 + 成功路径 → 只发 1 次."""
    client = MiaoshouErpClient(app_id="L", app_secret="S", max_retries=1)

    fake_resp = MagicMock()
    fake_resp.read.return_value = b'{"result":"success","code":"0","data":{}}'
    fake_resp.__enter__ = lambda s: s
    fake_resp.__exit__ = lambda s, *a: None

    call_count = {"n": 0}

    def fake_urlopen(req, timeout=None):
        call_count["n"] += 1
        return fake_resp

    with patch("urllib.request.urlopen", side_effect=fake_urlopen):
        client._call_erp(path="/test", body={})
    assert call_count["n"] == 1


def test_max_retries_three_success_makes_one_request():
    """✅ 边界: max_retries=3 + 成功路径 → 只发 1 次（无重试）."""
    client = MiaoshouErpClient(app_id="L", app_secret="S", max_retries=3, retry_backoff=0)

    fake_resp = MagicMock()
    fake_resp.read.return_value = b'{"result":"success","code":"0","data":{}}'
    fake_resp.__enter__ = lambda s: s
    fake_resp.__exit__ = lambda s, *a: None

    call_count = {"n": 0}

    def fake_urlopen(req, timeout=None):
        call_count["n"] += 1
        return fake_resp

    with patch("urllib.request.urlopen", side_effect=fake_urlopen):
        client._call_erp(path="/test", body={})
    assert call_count["n"] == 1
