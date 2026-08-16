"""TDD test suite for TikTokHttpClient.

TikTokHttpClient is the production implementation of HttpClient protocol.
It delegates to tts_signing.tiktok_request which handles HMAC signing.
We test that:
- It builds a credential-bound HttpClient
- It passes through all request kwargs (method, path, body, extra_params, timeout)
- It returns the parsed JSON dict unchanged
"""
from __future__ import annotations

from unittest.mock import patch

import pytest


class FakeTiktokRequest:
    """Records calls and returns canned responses."""

    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = []

    def __call__(self, method, api_host, path, access_token, app_key, app_secret,
                 body=None, extra_params=None, timeout=30):
        self.calls.append({
            "method": method,
            "api_host": api_host,
            "path": path,
            "access_token": access_token,
            "app_key": app_key,
            "app_secret": app_secret,
            "body": body,
            "extra_params": extra_params,
            "timeout": timeout,
        })
        if not self._responses:
            raise AssertionError(f"tiktok_request exhausted on call #{len(self.calls)}: {method} {path}")
        return self._responses.pop(0)


class TestTikTokHttpClient:
    def test_post_pass_through(self):
        fake = FakeTiktokRequest([{"code": 0, "data": {"ok": 1}}])
        with patch("http_client.tiktok_request", fake):
            from http_client import TikTokHttpClient
            cli = TikTokHttpClient(
                api_host="https://api.tiktok.com",
                app_key="ak", app_secret="sec",
                get_access_token=lambda: "tok-123",
            )
            result = cli.request(
                "POST", "/order/202309/orders/search",
                body={"shop_id": "X"}, extra_params={"shop_cipher": "c"},
                timeout=60,
            )

        assert result == {"code": 0, "data": {"ok": 1}}
        assert len(fake.calls) == 1
        call = fake.calls[0]
        assert call["method"] == "POST"
        assert call["api_host"] == "https://api.tiktok.com"
        assert call["path"] == "/order/202309/orders/search"
        assert call["access_token"] == "tok-123"
        assert call["app_key"] == "ak"
        assert call["app_secret"] == "sec"
        assert call["body"] == {"shop_id": "X"}
        assert call["extra_params"] == {"shop_cipher": "c"}
        assert call["timeout"] == 60

    def test_get_with_no_body(self):
        fake = FakeTiktokRequest([{"code": 0, "data": {"payments": []}}])
        with patch("http_client.tiktok_request", fake):
            from http_client import TikTokHttpClient
            cli = TikTokHttpClient(
                api_host="https://api.tiktok.com",
                app_key="ak", app_secret="sec",
                get_access_token=lambda: "tok",
            )
            cli.request("GET", "/finance/202309/payments", extra_params={"shop_cipher": "c"})

        assert fake.calls[0]["method"] == "GET"
        assert fake.calls[0]["body"] is None

    def test_token_fetcher_called_per_request(self):
        # get_access_token is called every request (token may rotate)
        fake = FakeTiktokRequest([
            {"code": 0, "data": {}},
            {"code": 0, "data": {}},
        ])
        token_calls = []

        def get_token():
            token_calls.append(1)
            return f"tok-{len(token_calls)}"

        with patch("http_client.tiktok_request", fake):
            from http_client import TikTokHttpClient
            cli = TikTokHttpClient(
                api_host="h", app_key="k", app_secret="s",
                get_access_token=get_token,
            )
            cli.request("GET", "/x")
            cli.request("GET", "/y")

        assert len(token_calls) == 2  # called for each request
        assert fake.calls[0]["access_token"] == "tok-1"
        assert fake.calls[1]["access_token"] == "tok-2"

    def test_default_timeout_30(self):
        fake = FakeTiktokRequest([{"code": 0}])
        with patch("http_client.tiktok_request", fake):
            from http_client import TikTokHttpClient
            cli = TikTokHttpClient(
                api_host="h", app_key="k", app_secret="s",
                get_access_token=lambda: "t",
            )
            cli.request("GET", "/x")

        assert fake.calls[0]["timeout"] == 30

    def test_response_returned_unchanged(self):
        # Even error responses should pass through unmodified
        fake = FakeTiktokRequest([{"code": 401, "message": "auth fail", "_error": True}])
        with patch("http_client.tiktok_request", fake):
            from http_client import TikTokHttpClient
            cli = TikTokHttpClient(
                api_host="h", app_key="k", app_secret="s",
                get_access_token=lambda: "t",
            )
            result = cli.request("GET", "/x")

        assert result == {"code": 401, "message": "auth fail", "_error": True}
