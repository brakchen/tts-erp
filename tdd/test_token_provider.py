"""TDD test suite for token providers.

Two providers live here:

* ``LocalTokenProvider`` — production. After Wave 3, tts-erp calls
  ``oauth_receiver_core.db_load_token`` in-process; no HTTP, no
  ``OAUTH_RECEIVER_URL`` env var.

* ``OAuthReceiverTokenProvider`` — legacy HTTP-based provider kept
  temporarily during the Wave 3 migration for any external scripts that
  may still import it; scheduled for removal in Slice 5.

The Wave 3 tests focus on ``LocalTokenProvider``. The legacy class is
still smoke-tested so callers that haven't migrated don't crash
unexpectedly.
"""
from __future__ import annotations

import pytest

from domain import Creds, TokenError

# ─── LocalTokenProvider ──────────────────────────────────────────────


class FakeCore:
    """In-memory stand-in for oauth_receiver_core used by LocalTokenProvider.

    The real LocalTokenProvider does ``import oauth_receiver_core`` at
    call time, so we patch ``oauth_receiver_core.db_load_token`` in tests
    rather than subclassing. This Fake is kept around for the few tests
    that exercise the module boundary directly.
    """

    def __init__(self, rows):
        # rows: dict[shop_id] -> dict (decrypted token row, or None for missing)
        self._rows = dict(rows)

    def db_load_token(self, shop_id, provider="tiktok"):
        row = self._rows.get(shop_id)
        if row is None:
            return None
        if provider != "tiktok":
            return None
        return dict(row)


class TestLocalTokenProvider:
    """Vertical slice 1: LocalTokenProvider reads from oauth_receiver_core."""

    def test_local_provider_returns_access_token_and_shop_cipher_from_db(self, monkeypatch):
        import oauth_receiver_core
        from token_provider import LocalTokenProvider

        monkeypatch.setattr(
            oauth_receiver_core,
            "db_load_token",
            lambda shop_id, provider: {
                "access_token": "tok-local",
                "shop_cipher": "cipher-local",
                "shop_region": "US",
                "shop_id": shop_id,
            },
        )

        tp = LocalTokenProvider()
        creds = tp.get("shop-local")

        assert isinstance(creds, Creds)
        assert creds.access_token == "tok-local"
        assert creds.shop_cipher == "cipher-local"
        assert creds.shop_id == "shop-local"
        assert creds.region == "US"

    def test_local_provider_returns_shop_region(self, monkeypatch):
        import oauth_receiver_core
        from token_provider import LocalTokenProvider

        monkeypatch.setattr(
            oauth_receiver_core,
            "db_load_token",
            lambda shop_id, provider: {
                "access_token": "t", "shop_cipher": "c", "shop_region": "VN",
            },
        )

        creds = LocalTokenProvider().get("shop-1")
        assert creds.region == "VN"

    def test_local_provider_region_default_empty_when_missing(self, monkeypatch):
        import oauth_receiver_core
        from token_provider import LocalTokenProvider

        monkeypatch.setattr(
            oauth_receiver_core,
            "db_load_token",
            lambda shop_id, provider: {"access_token": "t", "shop_cipher": "c"},
        )

        creds = LocalTokenProvider().get("shop-x")
        assert creds.region == ""

    def test_local_provider_raises_token_error_when_no_row(self, monkeypatch):
        import oauth_receiver_core
        from token_provider import LocalTokenProvider

        monkeypatch.setattr(
            oauth_receiver_core, "db_load_token", lambda shop_id, provider: None
        )

        with pytest.raises(TokenError) as exc:
            LocalTokenProvider().get("missing-shop")

        assert exc.value.status == 404
        assert "missing-shop" in str(exc.value)

    def test_local_provider_works_with_provider_arg_default_tiktok(self, monkeypatch):
        """LocalTokenProvider.get(shop_id) always asks oauth_receiver_core
        for provider='tiktok' (the only supported provider today). Verify
        the provider arg is forwarded so non-tiktok is never silently
        served from a tiktok row.
        """
        import oauth_receiver_core
        from token_provider import LocalTokenProvider

        captured = {}

        def fake_load(shop_id, provider):
            captured["shop_id"] = shop_id
            captured["provider"] = provider
            return {"access_token": "t", "shop_cipher": "c"}

        monkeypatch.setattr(oauth_receiver_core, "db_load_token", fake_load)

        LocalTokenProvider().get("shop-1")

        assert captured == {"shop_id": "shop-1", "provider": "tiktok"}

    def test_local_provider_does_not_call_http(self, monkeypatch):
        """LocalTokenProvider must be in-process only — no urllib,
        no http clients. This guards against regressions where someone
        re-introduces an HTTP fallback 'for safety'.
        """
        import oauth_receiver_core
        from token_provider import LocalTokenProvider

        def fail_if_http_called(*args, **kwargs):
            raise AssertionError("LocalTokenProvider must not perform HTTP I/O")

        # Patch urllib.request.urlopen (the only stdlib HTTP path that
        # the legacy provider used).
        import urllib.request as _urllib_request
        monkeypatch.setattr(_urllib_request, "urlopen", fail_if_http_called)
        monkeypatch.setattr(
            oauth_receiver_core,
            "db_load_token",
            lambda shop_id, provider: {"access_token": "t", "shop_cipher": "c"},
        )

        # Must not raise.
        creds = LocalTokenProvider().get("shop-1")
        assert creds.access_token == "t"

    def test_local_provider_constructor_takes_no_args(self):
        """LocalTokenProvider() has zero-arg constructor — there is no
        base_url, no http client, no config. If someone tries to pass
        them, the contract is broken.
        """
        import inspect

        from token_provider import LocalTokenProvider

        sig = inspect.signature(LocalTokenProvider.__init__)
        params = list(sig.parameters.keys())
        assert params == ["self"], (
            f"LocalTokenProvider.__init__ must have only `self` param, got {params}"
        )

    def test_local_provider_does_not_read_oa_uth_receiver_url_env(self, monkeypatch):
        """LocalTokenProvider must not depend on OAUTH_RECEIVER_URL env
        var at runtime. Patching the env var to a bogus value must not
        break the provider.
        """
        import oauth_receiver_core
        from token_provider import LocalTokenProvider

        monkeypatch.setenv("OAUTH_RECEIVER_URL", "http://does-not-exist.invalid:0")
        monkeypatch.setattr(
            oauth_receiver_core,
            "db_load_token",
            lambda shop_id, provider: {"access_token": "t", "shop_cipher": "c"},
        )

        creds = LocalTokenProvider().get("shop-x")
        assert creds.access_token == "t"


# ─── OAuthReceiverTokenProvider (legacy, kept until Slice 5) ──────────


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
    """Smoke tests for the legacy HTTP-based provider. These exist so
    scripts that haven't migrated don't break. Slice 5 deletes the class.
    """

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
        assert http.calls[0]["method"] == "GET"
        assert "http://oauth:9876/token/shop-1" in http.calls[0]["url"]
        assert "reveal=1" in http.calls[0]["url"]

    def test_shop_id_is_url_escaped(self):
        http = FakeHttp([{"access_token": "t", "shop_cipher": "c", "shop_region": "VN"}])
        from token_provider import OAuthReceiverTokenProvider
        tp = OAuthReceiverTokenProvider(base_url="http://oauth:9876", http=http)

        tp.get("shop/with/slashes")

        assert "shop%2Fwith%2Fslashes" in http.calls[0]["url"]

    def test_raises_token_error_on_http_error(self):
        http = FakeHttp([{"_error": True, "_http_status": 404, "_body": "shop not found"}])
        from token_provider import OAuthReceiverTokenProvider
        tp = OAuthReceiverTokenProvider(base_url="http://oauth:9876", http=http)

        with pytest.raises(TokenError) as exc:
            tp.get("missing-shop")
        assert exc.value.status == 502

    def test_raises_token_error_on_missing_access_token(self):
        http = FakeHttp([{"shop_cipher": "c", "shop_region": "VN"}])
        from token_provider import OAuthReceiverTokenProvider
        tp = OAuthReceiverTokenProvider(base_url="http://oauth:9876", http=http)

        with pytest.raises(TokenError) as exc:
            tp.get("shop-x")
        assert "access_token" in str(exc.value)

    def test_raises_token_error_on_missing_shop_cipher(self):
        http = FakeHttp([{"access_token": "t", "shop_region": "VN"}])
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
        http = FakeHttp([{"access_token": "t", "shop_cipher": "c"}])
        from token_provider import OAuthReceiverTokenProvider
        tp = OAuthReceiverTokenProvider(base_url="http://oauth:9876/", http=http)

        tp.get("shop-x")
        assert "//token" not in http.calls[0]["url"]
        assert http.calls[0]["url"].count("://") == 1
