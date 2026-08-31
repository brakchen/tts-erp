"""Round 6 fix test — 直接 patch time.time 不通过 module."""
from __future__ import annotations

import time
from unittest.mock import MagicMock, patch

import pytest

from miaoshou.miaoshou_erp_client import MiaoshouErpClient

pytestmark = [pytest.mark.domain_miaoshou, pytest.mark.layer_unit]


def test_timestamp_called_per_attempt():
    """🔴 P0 bugfix: 每个 retry attempt 都重新生成 timestamp.

    直接 patch time.time（不是 patch time module），确保 mock 生效。
    """
    client = MiaoshouErpClient(app_id="L", app_secret="S", max_retries=3, retry_backoff=0)

    timestamps = [1700000000, 1700000001, 1700000002]
    call_count = {"n": 0}

    def fake_time():
        v = timestamps[call_count["n"]]
        call_count["n"] += 1
        return v

    fake_resp = MagicMock()
    fake_resp.read.return_value = b'{"result":"success","code":"0","data":{}}'
    fake_resp.__enter__ = lambda s: s
    fake_resp.__exit__ = lambda s, *a: None

    urlopen_calls = {"n": 0}
    def fake_urlopen(req, timeout=None):
        urlopen_calls["n"] += 1
        if urlopen_calls["n"] < 3:
            raise ConnectionError(f"blip {urlopen_calls['n']}")
        return fake_resp

    with patch.object(time, "time", fake_time), \
         patch("urllib.request.urlopen", side_effect=fake_urlopen):
        client._call_erp(path="/test", body={})

    # 期望：3 次 retry 每次都调 time.time()
    assert call_count["n"] == 3, f"time.time 应调 3 次，实际 {call_count['n']} 次"
    # urlopen 也应调 3 次（前 2 次 raise，第 3 次成功）
    assert urlopen_calls["n"] == 3, f"urlopen 应调 3 次，实际 {urlopen_calls['n']} 次"


def test_signature_uses_fresh_timestamp_each_retry():
    """🔴 P0 bugfix: 每次 retry signature 用新 timestamp（不是循环外缓存的旧值）.

    mock time.time() 返回不同值，验证每次 retry 的 signature 不同。
    """
    client = MiaoshouErpClient(app_id="L", app_secret="S", max_retries=3, retry_backoff=0)

    ts_seq = [1700000000, 1700000001, 1700000002]
    call_idx = {"i": 0}

    def fake_time():
        v = ts_seq[call_idx["i"]]
        call_idx["i"] += 1
        return v

    captured = []
    from miaoshou.miaoshou_signing import hmac_sha256_sign
    def fake_sign(*args, **kwargs):
        sig = hmac_sha256_sign(*args, **kwargs)
        captured.append(sig)
        return sig

    fake_resp = MagicMock()
    fake_resp.read.return_value = b'{"result":"success","code":"0","data":{}}'
    fake_resp.__enter__ = lambda s: s
    fake_resp.__exit__ = lambda s, *a: None

    call_n = {"n": 0}
    def fake_urlopen(req, timeout=None):
        call_n["n"] += 1
        if call_n["n"] < 3:
            raise ConnectionError(f"blip {call_n['n']}")
        return fake_resp

    with patch.object(time, "time", fake_time), \
         patch("miaoshou.miaoshou_erp_client.hmac_sha256_sign", side_effect=fake_sign), \
         patch("urllib.request.urlopen", side_effect=fake_urlopen):
        client._call_erp(path="/test", body={})

    # 3 次 retry 每次都调了签名
    assert len(captured) == 3, f"3 次 retry 应调 3 次签名，实际 {len(captured)} 次"
    # timestamp 每次不同 → signature 每次不同
    assert len(set(captured)) == 3, (
        f"3 次 retry 的 signature 应都不同（因 timestamp 变了），实际 {len(set(captured))} 个唯一值: {captured}"
    )


def test_loop_runs_three_times_with_max_retries_3():
    """✅ baseline: max_retries=3 + 全失败 → 跑 3 次后 break（耗尽 retries）."""
    client = MiaoshouErpClient(app_id="L", app_secret="S", max_retries=3, retry_backoff=0)

    calls = {"n": 0}
    def fake_urlopen(req, timeout=None):
        calls["n"] += 1
        raise ConnectionError(f"blip {calls['n']}")

    with patch.object(time, "time", lambda: 0), \
         patch("urllib.request.urlopen", side_effect=fake_urlopen):
        from miaoshou.miaoshou_client import MiaoshouApiError
        with pytest.raises(MiaoshouApiError) as exc_info:
            client._call_erp(path="/test", body={})
    # max_retries=3 → 跑 3 次后耗尽
    assert calls["n"] == 3, f"max_retries=3 应跑 3 次（耗尽），实际 {calls['n']} 次"
    assert "已重试 3 次" in str(exc_info.value)
