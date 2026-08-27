"""TDD test suite for tts_signing.py.

Covers:
- HMAC-SHA256 canonical string format (GET / POST variants)
- Parameter sort order (alphabetical)
- Body inclusion rules (raw JSON, not URL-encoded)
- build_signed_url adds app_key + timestamp + sign
- tiktok_request full HTTP call (mocked urllib)

This is a retrofit: tts_signing.py already exists. We're writing
these tests to lock down current behavior + document the contract.
New edge cases follow strict red-green-refactor: failing test first.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import urllib.error
from unittest.mock import MagicMock, patch

from tts_signing import build_signed_url, sign_request, tiktok_request

# ─── sign_request canonical format ────────────────────────────────────


class TestSignRequestGET:
    """GET requests: body=None, canonical = {secret}{path}{kv}{secret}"""

    def test_empty_params_uses_just_secret_path(self):
        # canonical = f"{secret}{path}{kv}{secret}" — secret directly concatenates to path's last char
        sig = sign_request("mysecret", "/foo", {}, body=None)
        # = "mysecret" + "/foo" + "" + "mysecret" = "mysecret/foomysecret"
        expected = hmac.new(b"mysecret", b"mysecret/foomysecret", hashlib.sha256).hexdigest()
        assert sig == expected

    def test_single_param_appended_alphabetically(self):
        # param key "app_key" inserted, canonical has key+value
        sig = sign_request("sec", "/p", {"app_key": "k1"}, body=None)
        # canonical = "sec/papp_keyk1sec"
        expected = hmac.new(b"sec", b"sec/papp_keyk1sec", hashlib.sha256).hexdigest()
        assert sig == expected

    def test_multiple_params_sorted_alphabetically(self):
        # 3 params, sorted: app_key < shop_cipher < timestamp
        sig = sign_request("S", "/p",
                           {"timestamp": "99", "shop_cipher": "C", "app_key": "K"},
                           body=None)
        # canonical = "S/papp_keyKshop_cipherCtimestamp99S"
        expected = hmac.new(b"S", b"S/papp_keyKshop_cipherCtimestamp99S", hashlib.sha256).hexdigest()
        assert sig == expected


class TestSignRequestPOST:
    """POST requests: canonical = {secret}{path}{kv}{body}{secret}"""

    def test_body_inserted_after_kv_before_trailing_secret(self):
        sig = sign_request("s", "/p", {"app_key": "K"}, body='{"a":1}')
        # canonical = "s/papp_keyK{"a":1}s"
        expected = hmac.new(b"s", b's/papp_keyK{"a":1}s', hashlib.sha256).hexdigest()
        assert sig == expected

    def test_body_not_url_encoded(self):
        # body is raw JSON, NOT percent-encoded
        sig = sign_request("s", "/p", {"app_key": "K"}, body='{"name": "张三"}')
        # 必须用 raw UTF-8 字节,不能 %E5%BC%A0 编码
        expected = hmac.new(
            b"s",
            b's/papp_keyK{"name": "\xe5\xbc\xa0\xe4\xb8\x89"}s',
            hashlib.sha256,
        ).hexdigest()
        assert sig == expected

    def test_empty_string_body_still_inserted(self):
        # POST with no body but body="" still includes "" between kv and secret
        sig = sign_request("s", "/p", {"app_key": "K"}, body="")
        expected = hmac.new(b"s", b"s/papp_keyKs", hashlib.sha256).hexdigest()
        assert sig == expected


class TestSignRequestKnownVectors:
    """Fixed canonical strings → fixed signature. If these break, the
    signature contract changed and TikTok will reject with 106001."""

    def test_known_vector_1_no_body(self):
        sig = sign_request("test_secret_123", "/order/202309/orders/search",
                           {"app_key": "ak", "timestamp": "1700000000",
                            "shop_cipher": "cipher_abc"})
        # Calculate expected from documented canonical:
        # sorted = app_key, shop_cipher, timestamp → "ak" + "cipher_abc" + "1700000000"
        # canonical = "test_secret_123/order/202309/orders/searchapp_keyakshop_ciphercipher_abctimestamp1700000000test_secret_123"
        canonical = (
            "test_secret_123/order/202309/orders/search"
            "app_keyak"
            "shop_ciphercipher_abc"
            "timestamp1700000000"
            "test_secret_123"
        )
        expected = hmac.new(b"test_secret_123", canonical.encode(), hashlib.sha256).hexdigest()
        assert sig == expected

    def test_known_vector_2_with_body(self):
        body = '{"order_status":"UNPAID","page_size":50}'
        sig = sign_request("S3cr3t!", "/finance/202309/statements",
                           {"app_key": "ak1", "timestamp": "1700000001",
                            "shop_cipher": "sc1"},
                           body=body)
        canonical = (
            "S3cr3t!"
            "/finance/202309/statements"
            "app_keyak1"
            "shop_ciphersc1"
            "timestamp1700000001"
            + body
            + "S3cr3t!"
        )
        expected = hmac.new(b"S3cr3t!", canonical.encode(), hashlib.sha256).hexdigest()
        assert sig == expected


# ─── build_signed_url ─────────────────────────────────────────────────


class TestBuildSignedUrl:
    def test_includes_app_key_timestamp_and_sign(self):
        url, ts = build_signed_url(
            "https://api.example.com", "/foo",
            app_key="mykey", app_secret="mysec",
        )
        # timestamp is integer seconds as string
        assert ts.isdigit()
        assert int(ts) > 1_700_000_000
        # URL must have these query params
        assert "app_key=mykey" in url
        assert f"timestamp={ts}" in url
        assert "sign=" in url

    def test_extra_params_merged_into_query(self):
        url, _ = build_signed_url(
            "https://api.example.com", "/foo",
            app_key="ak", app_secret="sec",
            extra_params={"shop_cipher": "abc", "page_size": "50"},
        )
        assert "shop_cipher=abc" in url
        assert "page_size=50" in url
        # app_key, timestamp, sign are still there
        assert "app_key=ak" in url
        assert "sign=" in url

    def test_sign_matches_independent_sign_request_call(self):
        # Cross-check: build_signed_url's sign is reproducible via sign_request
        ts = "1700000123"
        with patch("tts_signing.time.time", return_value=int(ts)):
            url, _ = build_signed_url(
                "https://api.example.com", "/x",
                app_key="k", app_secret="s",
                extra_params={"shop_cipher": "c"},
            )
        # Extract sign from URL
        from urllib.parse import parse_qs, urlparse
        qs = parse_qs(urlparse(url).query)
        sig = qs["sign"][0]
        # Recompute independently
        params = {"app_key": "k", "timestamp": ts, "shop_cipher": "c"}
        expected = sign_request("s", "/x", params, body=None)
        assert sig == expected


# ─── tiktok_request (mocked HTTP) ─────────────────────────────────────


class TestTiktokRequest:
    """Mock urllib to verify request shape (URL, headers, body)."""

    def test_get_request_builds_signed_url_with_extra_params(self):
        fake_response = MagicMock()
        fake_response.read.return_value = b'{"code": 0, "data": {"items": []}}'
        fake_response.__enter__ = lambda s: s
        fake_response.__exit__ = lambda s, *a: None

        with patch("tts_signing.urllib.request.urlopen", return_value=fake_response) as mock_urlopen:
            result = tiktok_request(
                "GET", "https://api.tiktok.com", "/finance/202309/statements",
                access_token="tok123",
                app_key="ak", app_secret="sec",
                extra_params={"shop_cipher": "sc", "page_size": "50"},
            )

        # Verify urlopen was called with a Request having signed URL
        assert mock_urlopen.called
        req = mock_urlopen.call_args[0][0]
        assert "shop_cipher=sc" in req.full_url
        assert "sign=" in req.full_url
        assert "app_key=ak" in req.full_url
        # x-tts-access-token header — urllib capitalize header names
        # (HTTP headers are case-insensitive; urllib stores as "X-tts-access-token")
        assert req.headers.get("X-tts-access-token") == "tok123"
        # No body for GET
        assert req.data is None
        # Result parsed
        assert result == {"code": 0, "data": {"items": []}}

    def test_post_request_signs_with_body(self):
        fake_response = MagicMock()
        fake_response.read.return_value = b'{"code": 0, "data": {"order_list": []}}'
        fake_response.__enter__ = lambda s: s
        fake_response.__exit__ = lambda s, *a: None

        with patch("tts_signing.urllib.request.urlopen", return_value=fake_response) as mock_urlopen:
            tiktok_request(
                "POST", "https://api.tiktok.com", "/order/202309/orders/search",
                access_token="tok",
                app_key="ak", app_secret="sec",
                body={"order_status": "UNPAID", "page_size": 50},
                extra_params={"shop_cipher": "sc"},
            )

        req = mock_urlopen.call_args[0][0]
        # Body must be JSON-encoded (ensure_ascii=False to preserve CJK)
        body_str = req.data.decode("utf-8")
        assert json.loads(body_str) == {"order_status": "UNPAID", "page_size": 50}
        # Content-Type
        assert "application/json" in req.headers.get("Content-type", "")

    def test_post_with_no_body_sends_empty_data(self):
        # return_refund search endpoints have body=None
        fake_response = MagicMock()
        fake_response.read.return_value = b'{"code": 0}'
        fake_response.__enter__ = lambda s: s
        fake_response.__exit__ = lambda s, *a: None

        with patch("tts_signing.urllib.request.urlopen", return_value=fake_response) as mock_urlopen:
            tiktok_request(
                "POST", "https://api.tiktok.com", "/return_refund/202309/returns/search",
                access_token="t", app_key="k", app_secret="s",
                body=None, extra_params={"shop_cipher": "c"},
            )
        req = mock_urlopen.call_args[0][0]
        # body=None → b"" sent
        assert req.data == b""

    def test_http_error_returns_parsed_body(self):
        import urllib.error
        err = urllib.error.HTTPError(
            "https://api.tiktok.com/foo", 401, "Unauthorized", {},  # type: ignore[arg-type] -- dict hdrs fine in practice; test fake
            MagicMock(read=MagicMock(return_value=b'{"code": 401, "message": "auth fail"}'))
        )
        with patch("tts_signing.urllib.request.urlopen", side_effect=err):
            result = tiktok_request(
                "GET", "https://api.tiktok.com", "/x",
                access_token="t", app_key="k", app_secret="s",
            )
        assert result == {"code": 401, "message": "auth fail"}

    def test_network_error_returns_code_minus_one(self):
        import urllib.error
        with patch("tts_signing.urllib.request.urlopen",
                   side_effect=urllib.error.URLError("connection refused")):
            result = tiktok_request(
                "GET", "https://api.tiktok.com", "/x",
                access_token="t", app_key="k", app_secret="s",
            )
        assert result["code"] == -1
        assert "connection refused" in result["message"]


# ─── Regression: TTS_DEBUG_SIGN env var emits canonical ───────────────


class TestDebugSignEnv:
    def test_tts_debug_sign_writes_canonical_to_stderr(self, capsys):
        import os
        os.environ["TTS_DEBUG_SIGN"] = "1"
        try:
            sign_request("sec", "/p", {"app_key": "K"}, body='{"x":1}')
        finally:
            del os.environ["TTS_DEBUG_SIGN"]
        captured = capsys.readouterr()
        assert "canonical=" in captured.err
        assert "sig=" in captured.err


class TestTiktokRequestRetry:
    """W3.2: bounded retry with backoff for 429 / 5xx HTTP errors and
    network failures. 4xx business errors must NOT be retried."""

    def _resp(self, payload: bytes):
        r = MagicMock()
        r.read.return_value = payload
        r.__enter__ = lambda s: s
        r.__exit__ = lambda s, *a: None
        return r

    def _http_error(self, code: int):
        e = urllib.error.HTTPError("u", code, "msg", None, None)  # type: ignore[arg-type] -- None hdrs fine for test fake
        e.read = lambda: json.dumps({"code": code, "message": f"HTTP {code}"}).encode()  # type: ignore[method-assign]
        return e

    def test_429_retried_then_succeeds(self):
        import tts_signing

        calls = {"n": 0}

        def side_effect(req, timeout=30):
            calls["n"] += 1
            if calls["n"] < 3:
                raise self._http_error(429)
            return self._resp(b'{"code": 0, "data": {}}')

        with (
            patch("tts_signing.urllib.request.urlopen", side_effect=side_effect),
            patch.object(tts_signing.time, "sleep", lambda s: None),
        ):
            result = tiktok_request(
                "GET", "https://api.tiktok.com", "/x",
                access_token="t", app_key="ak", app_secret="sec",
            )
        assert result["code"] == 0
        assert calls["n"] == 3

    def test_500_retried_then_gives_up_after_3(self):
        import tts_signing

        calls = {"n": 0}

        def side_effect(req, timeout=30):
            calls["n"] += 1
            raise self._http_error(500)

        with (
            patch("tts_signing.urllib.request.urlopen", side_effect=side_effect),
            patch.object(tts_signing.time, "sleep", lambda s: None),
        ):
            result = tiktok_request(
                "GET", "https://api.tiktok.com", "/x",
                access_token="t", app_key="ak", app_secret="sec",
            )
        assert result["code"] == 500  # last error returned, not raised
        assert calls["n"] == 3  # 1 initial + 2 retries

    def test_400_not_retried(self):
        calls = {"n": 0}

        def side_effect(req, timeout=30):
            calls["n"] += 1
            raise self._http_error(400)

        with patch("tts_signing.urllib.request.urlopen", side_effect=side_effect):
            result = tiktok_request(
                "GET", "https://api.tiktok.com", "/x",
                access_token="t", app_key="ak", app_secret="sec",
            )
        assert calls["n"] == 1

    def test_network_error_retried(self):
        import tts_signing

        calls = {"n": 0}

        def side_effect(req, timeout=30):
            calls["n"] += 1
            if calls["n"] == 1:
                raise urllib.error.URLError("conn reset")
            return self._resp(b'{"code": 0}')

        with (
            patch("tts_signing.urllib.request.urlopen", side_effect=side_effect),
            patch.object(tts_signing.time, "sleep", lambda s: None),
        ):
            result = tiktok_request(
                "GET", "https://api.tiktok.com", "/x",
                access_token="t", app_key="ak", app_secret="sec",
            )
        assert result["code"] == 0
        assert calls["n"] == 2
