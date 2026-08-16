"""TDD test suite for OAuthReceiverTokenProvider.

Production TokenProvider that calls oauth-receiver /token/<id>?reveal=1
to fetch access_token + shop_cipher + shop_region for a given shop.
"""
from __future__ import annotations

import pytest

from domain import TokenError


class FakeHttp:
    """Plain http client fake. Records calls; replays canned responses."""

    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = []

    def request(self, method, url, *, body=None, extra_params=None, timeout=None):
        self.calls.append({
            "method": method, "url": url, "body": body,
            "extra_params": extra_params, "timeout": timeout,
        })
        if not self._responses:
            raise AssertionError(f"FakeHttp exhausted on call #{len(self.calls)}: {method} {url}")
        return self._responses.pop(0)


class TestOAuthReceiverTokenProvider:
    def test_get_creds_calls_reveal_endpoint(self):
        http = FakeHttp([{
            "access_token": "tok-abc",
            "shop_cipher": "cipher-xyz",
            "shop_region": "VN",
            "shop_id": "shop-1",
        }])
        from token_provider import OAuthReceiverTokenProvider
        tp = OAuthReceiverTokenProvider(base_url="http://oauth:9876", http=http)

        creds = tp.get("shop-1")

        assert creds.access_token == "tok-abc"
        assert creds.shop_cipher == "cipher-xyz"
        assert creds.region == "VN"
        assert creds.shop_id == "shop-1"
        # Verify the URL that was called
        assert http.calls[0]["method"] == "GET"
        assert "http://oauth:9876/token/shop-1" in http.calls[0]["url"]
        assert "reveal=1" in http.calls[0]["url"]

    def test_shop_id_is_url_escaped(self):
        # shop IDs with special chars must be URL-encoded
        http = FakeHttp([{
            "access_token": "t", "shop_cipher": "c", "shop_region": "VN",
        }])
        from token_provider import OAuthReceiverTokenProvider
        tp = OAuthReceiverTokenProvider(base_url="http://oauth:9876", http=http)

        tp.get("shop/with/slashes")

        # / must be %2F-encoded to not break path
        assert "shop%2Fwith%2Fslashes" in http.calls[0]["url"]

    def test_raises_token_error_on_http_error(self):
        http = FakeHttp([{
            "_error": True, "_http_status": 404, "_body": "shop not found",
        }])
        from token_provider import OAuthReceiverTokenProvider
        tp = OAuthReceiverTokenProvider(base_url="http://oauth:9876", http=http)

        with pytest.raises(TokenError) as exc:
            tp.get("missing-shop")

        assert exc.value.status == 502  # we map to 502 to caller

    def test_raises_token_error_on_missing_access_token(self):
        http = FakeHttp([{"shop_cipher": "c", "shop_region": "VN"}])  # no access_token
        from token_provider import OAuthReceiverTokenProvider
        tp = OAuthReceiverTokenProvider(base_url="http://oauth:9876", http=http)

        with pytest.raises(TokenError) as exc:
            tp.get("shop-x")

        assert "access_token" in str(exc.value)

    def test_raises_token_error_on_missing_shop_cipher(self):
        http = FakeHttp([{"access_token": "t", "shop_region": "VN"}])  # no shop_cipher
        from token_provider import OAuthReceiverTokenProvider
        tp = OAuthReceiverTokenProvider(base_url="http://oauth:9876", http=http)

        with pytest.raises(TokenError) as exc:
            tp.get("shop-x")

        assert "shop_cipher" in str(exc.value)

    def test_region_default_empty_when_missing(self):
        http = FakeHttp([{"access_token": "t", "shop_cipher": "c"}])
        from token_provider import OAuthReceiverTokenProvider
        tp = OAuthReceiverTokenProvider(base_url="http://oauth:9876", http=http)

        creds = tp.get("shop-x")

        assert creds.region == ""

    def test_trailing_slash_in_base_url_handled(self):
        # If user passes base_url with trailing /, should not produce //
        http = FakeHttp([{"access_token": "t", "shop_cipher": "c"}])
        from token_provider import OAuthReceiverTokenProvider
        tp = OAuthReceiverTokenProvider(base_url="http://oauth:9876/", http=http)

        tp.get("shop-x")

        # URL should be http://oauth:9876/token/shop-x, not //token
        assert "//token" not in http.calls[0]["url"]
        assert http.calls[0]["url"].count("://") == 1  # scheme only
