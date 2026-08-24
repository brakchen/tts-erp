"""Wave 3 Slice 5 — verify OAUTH_RECEIVER_URL is fully removed.

After Slice 5:

* The string ``OAUTH_RECEIVER_URL`` must NOT appear anywhere in
  production ``.py`` files under ``tdd/`` (this test file's own
  references are excluded by filter).
* The legacy ``OAuthReceiverTokenProvider`` class is deleted from
  ``token_provider.py``.
* The legacy ``_plain_http = PlainHttpClient(timeout=10)`` instance
  is gone (it was only used to construct the legacy provider).
* The ``urllib.request.urlopen`` call in ``tts_erp_fastapi.py`` is
  dropped.

If any external script still imports ``OAuthReceiverTokenProvider``,
they'll get an ImportError — that's intentional, that's the cleanup
this slice is delivering.
"""
from __future__ import annotations

from pathlib import Path

import pytest

TDD_DIR = Path(__file__).resolve().parent
THIS_FILE = Path(__file__).resolve()


def _production_python_files() -> list[Path]:
    """Production .py files under tdd/, excluding test files, __pycache__,
    and .bak. "Production" = does not start with ``test_``."""
    files: list[Path] = []
    for p in TDD_DIR.rglob("*.py"):
        if "__pycache__" in p.parts:
            continue
        if p.name.endswith(".bak"):
            continue
        if p.name.startswith("test_"):
            continue
        files.append(p)
    return sorted(files)


def _all_python_files() -> list[Path]:
    """All .py files under tdd/, excluding __pycache__ and .bak."""
    files: list[Path] = []
    for p in TDD_DIR.rglob("*.py"):
        if "__pycache__" in p.parts:
            continue
        if p.name.endswith(".bak"):
            continue
        files.append(p)
    return sorted(files)


class TestOauthReceiverUrlRemoved:
    """Slice 5: the legacy OAUTH_RECEIVER_URL is gone from the merged app."""

    def test_oa_uath_receiver_url_not_in_production_code(self):
        """No production .py file (non-test) may reference the legacy
        env-var name. The references in this test file are excluded by
        the file filter."""
        offenders: list[str] = []
        for p in _production_python_files():
            text = p.read_text(encoding="utf-8")
            for lineno, line in enumerate(text.splitlines(), start=1):
                if "OAUTH_RECEIVER_URL" in line:
                    offenders.append(f"{p.name}:{lineno}: {line.strip()}")
        assert not offenders, (
            "OAUTH_RECEIVER_URL still referenced in production code:\n"
            + "\n".join(offenders)
        )

    def test_legacy_provider_class_deleted(self):
        """OAuthReceiverTokenProvider must be gone from token_provider.

        Uses importlib to reload the module freshly — guards against
        any cached bytecode hanging onto the legacy class.
        """
        # Snapshot modules that depend on token_provider so we can
        # restore them after popping token_provider from sys.modules.
        # Otherwise downstream tests like ``test_token_provider_is_local``
        # would re-import token_provider and end up with a fresh module
        # whose ``LocalTokenProvider`` is a *different* class object
        # than the one already bound into ``tts_erp_fastapi._token_provider``,
        # breaking the isinstance check via class identity mismatch.
        import sys as _sys
        dependents = [
            name
            for name, mod in list(_sys.modules.items())
            if mod is not None
            and getattr(mod, "__file__", None)
            and "tts-erp" in (mod.__file__ or "")
        ]
        _sys.modules.pop("token_provider", None)
        # Force the dependents to re-import on next access too — but
        # ONLY token_provider, not the dependents themselves. Restoring
        # the dependents lets them keep their existing module reference
        # (which is fine; they only *call* LocalTokenProvider, they
        # don't isinstance against a specific class instance).
        for name in dependents:
            _sys.modules.pop(name, None)

        with pytest.raises(ImportError) as exc:
            from token_provider import OAuthReceiverTokenProvider  # noqa: F401, F821

        # The error message should mention the missing name so users
        # who hit this in production get a clear hint.
        assert "OAuthReceiverTokenProvider" in str(exc.value), (
            f"ImportError message didn't name the missing class: {exc.value}"
        )

    def test_plain_http_instance_removed_from_tts_erp_fastapi(self):
        """_plain_http = PlainHttpClient(timeout=10) was only used by
        the legacy provider. Slice 5 removes it."""
        src = (TDD_DIR / "tts_erp_fastapi.py").read_text(encoding="utf-8")
        assert "_plain_http" not in src, (
            "_plain_http still present in tts_erp_fastapi.py — "
            "should be removed in Slice 5."
        )

    def test_no_urllib_request_urlopen_in_tts_erp_fastapi(self):
        """urllib.request.urlopen was the legacy path to oauth-receiver.
        After Slice 5 it's gone from production code."""
        src = (TDD_DIR / "tts_erp_fastapi.py").read_text(encoding="utf-8")
        assert "urllib.request.urlopen" not in src, (
            "urllib.request.urlopen still used in tts_erp_fastapi.py — "
            "Slice 5 should drop the legacy HTTP path."
        )

    def test_no_plain_httpclient_in_tts_erp_fastapi_imports(self):
        """PlainHttpClient was the legacy HTTP client. Slice 5 drops it
        from the import line (TikTokHttpClient is still used)."""
        src = (TDD_DIR / "tts_erp_fastapi.py").read_text(encoding="utf-8")
        assert "PlainHttpClient" not in src, (
            "PlainHttpClient still imported in tts_erp_fastapi.py — "
            "Slice 5 should drop it from the import."
        )


class TestSlice5Smoke:
    """Smoke: the merged app still loads and the oauth routes still work."""

    def test_app_still_loads(self):
        from fastapi import FastAPI

        from tts_erp_fastapi import app
        assert isinstance(app, FastAPI)

    def test_token_provider_is_local(self):
        from token_provider import LocalTokenProvider
        from tts_erp_fastapi import _token_provider
        assert isinstance(_token_provider, LocalTokenProvider)

    def test_local_token_provider_still_works(self, monkeypatch):
        """Smoke: LocalTokenProvider.get(shop_id) returns creds."""
        import oauth_receiver_core
        from tts_erp_fastapi import _token_provider

        monkeypatch.setattr(
            oauth_receiver_core,
            "db_load_token",
            lambda shop_id, provider: {
                "access_token": "tok-slice5",  # noqa: S105  (test fixture, not a real token)
                "shop_cipher": "cipher-slice5",
                "shop_region": "US",
                "shop_id": shop_id,
            },
        )

        creds = _token_provider.get("shop-slice5")
        assert creds.access_token == "tok-slice5"
        assert creds.shop_cipher == "cipher-slice5"
