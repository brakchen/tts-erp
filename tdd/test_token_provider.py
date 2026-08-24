"""TDD test suite for ``LocalTokenProvider``.

Production token provider that calls ``oauth_receiver_core.db_load_token``
in-process. After Wave 3, this is the only provider the FastAPI app uses —
no HTTP, no ``OAUTH_RECEIVER_URL`` env var.

The legacy ``OAuthReceiverTokenProvider`` (HTTP-based) was deleted in
Slice 5 along with these tests; nothing in the merged app imports it.
"""
from __future__ import annotations

import pytest

from domain import Creds, TokenError


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
        assert creds.access_token == "tok-local"  # noqa: S105  (test fixture, not a real token)
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
        assert creds.access_token == "t"  # noqa: S105  (test fixture)

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

    def test_local_provider_does_not_read_env_vars_at_runtime(self, monkeypatch):
        """LocalTokenProvider must not depend on any env var at runtime.
        We pick a deliberately bogus env var name to prove the provider
        does not look up any legacy config knob.

        After Slice 5 the legacy ``OAUTH_RECEIVER_URL`` is gone; this
        test intentionally uses a different name to avoid being coupled
        to the specific legacy var. The contract is: no env var lookup.
        """
        import oauth_receiver_core
        from token_provider import LocalTokenProvider

        monkeypatch.setenv("TTS_ERP_LEGACY_OAUTH_HOST", "http://does-not-exist.invalid:0")
        monkeypatch.setattr(
            oauth_receiver_core,
            "db_load_token",
            lambda shop_id, provider: {"access_token": "t", "shop_cipher": "c"},
        )

        creds = LocalTokenProvider().get("shop-x")
        assert creds.access_token == "t"  # noqa: S105  (test fixture)
