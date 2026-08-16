"""TDD test suite for PlainHttpClient.

PlainHttpClient is the production HttpClient for internal services
(oauth-receiver). Unlike TikTokHttpClient, no HMAC signing — just
plain JSON-over-HTTP GET/POST.

Out of scope (TDD-not-needed):
- urllib.request.urlopen itself (stdlib, not our code)
- json.loads error handling beyond what we deliberately raise
"""
from __future__ import annotations

import json
from unittest.mock import patch, MagicMock

import pytest

from http_client import PlainHttpClient


# ─── Helpers ─────────────────────────────────────────────────────────


def _mock_response(payload: dict, status: int = 200):
    """Build a context-manager mock that returns JSON payload."""
    body = json.dumps(payload).encode("utf-8")
    resp = MagicMock()
    resp.read.return_value = body
    resp.__enter__ = lambda s: s
    resp.__exit__ = lambda s, *a: None
    return resp


# ─── Request shape ──────────────────────────────────────────────────


class TestPlainHttpClientRequestShape:
    def test_get_request_no_body(self):
        fake = _mock_response({"ok": 1})
        with patch("http_client.urllib.request.urlopen", return_value=fake) as mock:
            cli = PlainHttpClient()
            result = cli.request("GET", "http://oauth:9876/tokens/shops")

        assert result == {"ok": 1}
        # Verify urlopen was called with a Request
        assert mock.called
        req = mock.call_args[0][0]
        # Method
        assert req.method == "GET"
        # No body for GET
        assert req.data is None
        # URL preserved
        assert req.full_url == "http://oauth:9876/tokens/shops"
        # Accept header
        assert "application/json" in req.headers.get("Accept", "")

    def test_post_with_json_body(self):
        fake = _mock_response({"code": 0})
        with patch("http_client.urllib.request.urlopen", return_value=fake) as mock:
            cli = PlainHttpClient()
            cli.request("POST", "http://oauth:9876/foo", body={"x": 1})

        req = mock.call_args[0][0]
        assert req.method == "POST"
        # Body is JSON-encoded bytes
        assert json.loads(req.data.decode("utf-8")) == {"x": 1}
        # Content-Type set when body present
        assert "application/json" in req.headers.get("Content-type", "")

    def test_no_content_type_when_no_body(self):
        fake = _mock_response({})
        with patch("http_client.urllib.request.urlopen", return_value=fake) as mock:
            cli = PlainHttpClient()
            cli.request("GET", "http://x")

        req = mock.call_args[0][0]
        # No body → no Content-Type
        assert "Content-type" not in req.headers


# ─── extra_params URL encoding ──────────────────────────────────────


class TestExtraParamsEncoding:
    def test_extra_params_appended_with_question_mark(self):
        fake = _mock_response({})
        with patch("http_client.urllib.request.urlopen", return_value=fake) as mock:
            cli = PlainHttpClient()
            cli.request(
                "GET", "http://oauth:9876/token/abc",
                extra_params={"reveal": "1"},
            )
        url = mock.call_args[0][0].full_url
        assert url == "http://oauth:9876/token/abc?reveal=1"

    def test_extra_params_appended_with_ampersand_if_url_has_query(self):
        fake = _mock_response({})
        with patch("http_client.urllib.request.urlopen", return_value=fake) as mock:
            cli = PlainHttpClient()
            cli.request(
                "GET", "http://oauth:9876/x?a=1",
                extra_params={"b": "2"},
            )
        url = mock.call_args[0][0].full_url
        assert url == "http://oauth:9876/x?a=1&b=2"

    def test_no_extra_params_url_unchanged(self):
        fake = _mock_response({})
        with patch("http_client.urllib.request.urlopen", return_value=fake) as mock:
            cli = PlainHttpClient()
            cli.request("GET", "http://oauth:9876/shops")
        assert mock.call_args[0][0].full_url == "http://oauth:9876/shops"

    def test_multiple_extra_params_url_encoded(self):
        fake = _mock_response({})
        with patch("http_client.urllib.request.urlopen", return_value=fake) as mock:
            cli = PlainHttpClient()
            cli.request(
                "GET", "http://x/y",
                extra_params={"a": "hello world", "b": "special/chars"},
            )
        url = mock.call_args[0][0].full_url
        # Spaces and slashes should be URL-encoded
        assert "hello%20world" in url or "hello+world" in url
        assert "special%2Fchars" in url


# ─── Timeout handling ──────────────────────────────────────────────


class TestTimeout:
    def test_default_timeout_10(self):
        fake = _mock_response({})
        with patch("http_client.urllib.request.urlopen", return_value=fake) as mock:
            cli = PlainHttpClient()
            cli.request("GET", "http://x")

        assert mock.call_args.kwargs.get("timeout") == 10

    def test_custom_constructor_timeout(self):
        fake = _mock_response({})
        with patch("http_client.urllib.request.urlopen", return_value=fake) as mock:
            cli = PlainHttpClient(timeout=30)
            cli.request("GET", "http://x")

        assert mock.call_args.kwargs.get("timeout") == 30

    def test_per_request_timeout_overrides_constructor(self):
        fake = _mock_response({})
        with patch("http_client.urllib.request.urlopen", return_value=fake) as mock:
            cli = PlainHttpClient(timeout=10)
            cli.request("GET", "http://x", timeout=60)

        assert mock.call_args.kwargs.get("timeout") == 60


# ─── Error handling ────────────────────────────────────────────────


class TestErrors:
    def test_http_error_returns_error_dict(self):
        import urllib.error
        err = urllib.error.HTTPError(
            "http://oauth:9876/missing", 404, "Not Found", {},
            MagicMock(read=MagicMock(return_value=b'{"detail":"not found"}')),
        )
        with patch("http_client.urllib.request.urlopen", side_effect=err):
            cli = PlainHttpClient()
            result = cli.request("GET", "http://oauth:9876/missing")

        assert result["_error"] is True
        assert result["_http_status"] == 404
        assert "not found" in result["_body"]

    def test_http_error_truncates_long_body(self):
        import urllib.error
        long_body = "x" * 1000
        err = urllib.error.HTTPError(
            "http://x", 500, "Internal", {},
            MagicMock(read=MagicMock(return_value=long_body.encode())),
        )
        with patch("http_client.urllib.request.urlopen", side_effect=err):
            cli = PlainHttpClient()
            result = cli.request("GET", "http://x")

        # Truncated to 500 chars
        assert len(result["_body"]) == 500

    def test_url_error_returns_error_dict(self):
        import urllib.error
        with patch("http_client.urllib.request.urlopen",
                   side_effect=urllib.error.URLError("connection refused")):
            cli = PlainHttpClient()
            result = cli.request("GET", "http://oauth:9876")

        assert result["_error"] is True
        assert "connection refused" in result["_reason"]

    def test_generic_exception_returns_error_dict(self):
        with patch("http_client.urllib.request.urlopen",
                   side_effect=RuntimeError("boom")):
            cli = PlainHttpClient()
            result = cli.request("GET", "http://x")

        assert result["_error"] is True
        assert "RuntimeError" in result["_reason"]
        assert "boom" in result["_reason"]
