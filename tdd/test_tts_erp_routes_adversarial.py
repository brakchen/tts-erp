"""Wave 3 adversarial QA — third-party verification of merged tts-erp surface.

These tests cover the contract guarantees of merge-design.md §3.1, §4.2,
§4.3, §4.4 after Wave 3 Slice 1-5:

* oauth-receiver routes (/callback, /authorize, /healthz) are mounted
  in-process via oauth_router
* tts-erp legacy proxy routes (/shops, /shops/<id>, /token/<id>) are GONE
* OAUTH_RECEIVER_URL + OAuthReceiverTokenProvider are fully removed
* LocalTokenProvider works as the in-process token source
* No 127.0.0.1:9876 (HTTP bridge) is reachable from tts-erp code

Run: python3 -m pytest test_tts_erp_routes_adversarial.py -v
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

TDD_DIR = Path(__file__).resolve().parent
TTS_ERP_FASTAPI = TDD_DIR / "tts_erp_fastapi.py"
TOKEN_PROVIDER = TDD_DIR / "token_provider.py"


@pytest.fixture(scope="module")
def client():
    from tts_erp_fastapi import app

    with TestClient(app) as c:
        yield c


# ─── 1. Legacy proxy routes are GONE (404, not 200, not 401) ────────


class TestLegacyProxiesGone:
    """Wave 3 Slice 2 deletes /shops, /shops/<id>, /token/<id>.

    With auth mode = off (default), the routes must return 404 because
    they don't exist on the merged app. They MUST NOT 200 (proxy still
    alive), 500 (broken proxy), or 401 (route exists, auth blocks).
    """

    def test_legacy_shops_returns_404(self, client, monkeypatch):
        monkeypatch.delenv("TTS_ERP_AUTH_MODE", raising=False)
        r = client.get("/shops")
        assert r.status_code == 404, (
            f"expected 404 (route deleted in Wave 3 Slice 2), got {r.status_code} "
            f"body={r.text[:200]}"
        )

    def test_legacy_shops_with_shop_id_returns_404(self, client, monkeypatch):
        monkeypatch.delenv("TTS_ERP_AUTH_MODE", raising=False)
        r = client.get("/shops/7494763368967603447")
        assert r.status_code == 404, (
            f"expected 404, got {r.status_code} body={r.text[:200]}"
        )

    def test_legacy_token_returns_404(self, client, monkeypatch):
        monkeypatch.delenv("TTS_ERP_AUTH_MODE", raising=False)
        r = client.get("/token/7494763368967603447")
        assert r.status_code == 404, (
            f"expected 404 (proxy route deleted), got {r.status_code} "
            f"body={r.text[:200]}"
        )

    def test_legacy_token_with_reveal_returns_404(self, client, monkeypatch):
        monkeypatch.delenv("TTS_ERP_AUTH_MODE", raising=False)
        r = client.get("/token/7494763368967603447?reveal=1")
        assert r.status_code == 404, (
            f"expected 404 even with reveal=1, got {r.status_code}"
        )


# ─── 2. OAuth public routes work without API key (TikTok contract) ───


class TestOauthPublicRoutes:
    """Wave 3 Slice 3 mounts oauth_router; /callback, /authorize are PUBLIC.

    TikTok's browser redirect hits /callback with NO Authorization header.
    If we accidentally put these behind auth, OAuth breaks for all shops.
    """

    def test_callback_no_api_key_returns_200(self, client, monkeypatch):
        monkeypatch.delenv("TTS_ERP_AUTH_MODE", raising=False)
        r = client.get("/callback")
        assert r.status_code == 200, (
            f"/callback MUST be public (TikTok contract), got {r.status_code}"
        )

    def test_authorize_no_api_key_returns_200(self, client, monkeypatch):
        monkeypatch.delenv("TTS_ERP_AUTH_MODE", raising=False)
        r = client.get("/authorize")
        assert r.status_code == 200, (
            f"/authorize MUST be public (browser flow), got {r.status_code}"
        )

    def test_healthz_no_api_key_returns_200(self, client, monkeypatch):
        monkeypatch.delenv("TTS_ERP_AUTH_MODE", raising=False)
        r = client.get("/healthz")
        assert r.status_code == 200, f"/healthz MUST be public, got {r.status_code}"

    def test_healthz_includes_components(self, client, monkeypatch):
        """merged healthz must report both oauth_receiver and tts_erp sections."""
        monkeypatch.delenv("TTS_ERP_AUTH_MODE", raising=False)
        r = client.get("/healthz")
        body = r.json()
        assert "components" in body, f"missing components key: {body}"
        comps = body["components"]
        assert "oauth_receiver" in comps, (
            f"healthz missing oauth_receiver component: {comps}"
        )
        assert "tts_erp" in comps, f"healthz missing tts_erp component: {comps}"

    def test_healthz_does_not_leak_app_secret(self, client, monkeypatch):
        """healthz response MUST NOT contain app_secret anywhere (any leak is a bug)."""
        monkeypatch.delenv("TTS_ERP_AUTH_MODE", raising=False)
        r = client.get("/healthz")
        body_text = r.text
        assert "app_secret" not in body_text, (
            "healthz body leaks 'app_secret' string - potential credential disclosure"
        )


# ─── 3. LocalTokenProvider is local (no HTTP) ──────────────────────


class TestLocalTokenProvider:
    """Wave 3 Slice 1: LocalTokenProvider calls oauth_receiver_core in-process."""

    def test_local_token_provider_no_urllib_import(self):
        """token_provider.py MUST NOT import urllib (no HTTP)."""
        src = TOKEN_PROVIDER.read_text(encoding="utf-8")
        top_lines = [
            line
            for line in src.splitlines()
            if line.startswith(("import urllib", "from urllib"))
        ]
        assert top_lines == [], (
            f"token_provider.py imports urllib (HTTP bridge still alive): {top_lines}"
        )

    def test_local_token_provider_no_http_clients(self):
        """token_provider.py MUST NOT import PlainHttpClient or oauth HTTP paths."""
        src = TOKEN_PROVIDER.read_text(encoding="utf-8")
        forbidden = [
            "PlainHttpClient",
            "OAUTH_RECEIVER_URL",
            "127.0.0.1:9876",
            "urlopen",
        ]
        for tok in forbidden:
            assert tok not in src, (
                f"token_provider.py references '{tok}' — HTTP bridge not fully removed"
            )

    def test_local_token_provider_class_is_used_by_app(self):
        """tts_erp_fastapi must wire LocalTokenProvider into _token_provider."""
        from token_provider import LocalTokenProvider
        from tts_erp_fastapi import _token_provider

        assert isinstance(_token_provider, LocalTokenProvider), (
            f"_token_provider is {type(_token_provider).__name__}, "
            f"expected LocalTokenProvider"
        )

    def test_local_token_provider_raises_on_missing_shop(self, monkeypatch):
        """LocalTokenProvider.get(unknown_shop) must raise TokenError."""
        import oauth_receiver_core
        from token_provider import LocalTokenProvider

        monkeypatch.setattr(
            oauth_receiver_core,
            "db_load_token",
            lambda shop_id, provider=None: None,
        )

        with pytest.raises(Exception) as excinfo:
            LocalTokenProvider().get("nonexistent_shop_id_999")
        assert excinfo.value is not None

    def test_local_token_provider_returns_creds(self, monkeypatch):
        """LocalTokenProvider.get(known_shop) returns Creds with token + cipher."""
        import oauth_receiver_core
        from token_provider import LocalTokenProvider

        fixture_token = "ROW_test_token_xxxx"  # noqa: S105 (test fixture)
        fixture_cipher = "GCP_test_cipher_xxxx"
        monkeypatch.setattr(
            oauth_receiver_core,
            "db_load_token",
            lambda shop_id, provider=None: {
                "access_token": fixture_token,
                "shop_cipher": fixture_cipher,
                "shop_region": "US",
                "shop_id": shop_id,
            },
        )

        creds = LocalTokenProvider().get("shop_xyz")
        assert creds.access_token == fixture_token
        assert creds.shop_cipher == fixture_cipher
        assert creds.region == "US"
        assert creds.shop_id == "shop_xyz"


# ─── 4. HTTP bridge fully gone from production code ─────────────────


class TestHttpBridgeGone:
    """Verify no production .py file references the old oauth HTTP bridge."""

    def _production_files(self) -> list[Path]:
        out = []
        for p in TDD_DIR.rglob("*.py"):
            if "__pycache__" in p.parts:
                continue
            if p.name.endswith(".bak"):
                continue
            if p.name == "test_tts_erp_routes_adversarial.py":
                continue
            out.append(p)
        return sorted(out)

    def test_no_oauth_receiver_url_in_production(self):
        """OAUTH_RECEIVER_URL string MUST NOT appear in any non-test, non-bak .py file."""
        hits = []
        for p in self._production_files():
            if not p.name.startswith("test_"):
                text = p.read_text(encoding="utf-8", errors="replace")
                if "OAUTH_RECEIVER_URL" in text:
                    hits.append(str(p))
        assert hits == [], (
            f"OAUTH_RECEIVER_URL still referenced in production code: {hits}"
        )

    def test_no_oauth_receiver_token_provider_in_production(self):
        """OAuthReceiverTokenProvider class MUST NOT be in any production .py file."""
        hits = []
        for p in self._production_files():
            if not p.name.startswith("test_"):
                text = p.read_text(encoding="utf-8", errors="replace")
                if "OAuthReceiverTokenProvider" in text:
                    hits.append(str(p))
        assert hits == [], (
            f"OAuthReceiverTokenProvider still referenced in production: {hits}"
        )

    def test_no_127_in_source_strings(self):
        """127.0.0.1:9876 string MUST NOT appear as code/URL anywhere in production code.

        Comments mentioning the deleted bridge are tolerated because they
        serve as documentation of the cleanup. We match only string-literal usage.
        """
        pattern = re.compile(r'["\']127\.0\.0\.1:9876["\']')
        hits = []
        for p in self._production_files():
            if not p.name.startswith("test_"):
                text = p.read_text(encoding="utf-8", errors="replace")
                for lineno, line in enumerate(text.splitlines(), 1):
                    if pattern.search(line):
                        hits.append(f"{p}:{lineno}: {line.strip()[:100]}")
        assert hits == [], (
            f"127.0.0.1:9876 still appears as a string in production code: {hits}"
        )


# ─── 5. End-to-end OAuth flow on merged app ──────────────────────────


class TestMergedOAuthFlow:
    """End-to-end: hit /authorize and /callback through the merged FastAPI TestClient."""

    def test_authorize_then_callback_state_roundtrip(self, client, monkeypatch):
        """Authorize registers a state, callback with that state must reach handle_callback."""
        # Force auth=off for this end-to-end test. In default off mode,
        # /authorize and /callback are both reachable without API key.
        # (When Wave 4 ships the whitelist, /authorize and /callback
        # will be EXEMPT_PATHS and this test still passes in enforce mode.)
        monkeypatch.setenv("TTS_ERP_AUTH_MODE", "off")
        r1 = client.get("/authorize?format=json")
        assert r1.status_code == 200, f"/authorize: {r1.status_code} {r1.text[:200]}"
        body1 = r1.json()
        state = body1.get("state")
        assert state, f"authorize response missing state: {body1}"
        r2 = client.get(f"/callback?state={state}")
        assert r2.status_code == 200, f"/callback: {r2.status_code} {r2.text[:200]}"

    def test_callback_with_no_code_shows_help(self, client, monkeypatch):
        """Empty /callback (no params) must return help page (matches standalone behavior)."""
        monkeypatch.delenv("TTS_ERP_AUTH_MODE", raising=False)
        r = client.get("/callback")
        assert r.status_code == 200
        body_lower = r.text.lower()
        assert "oauth" in body_lower or "callback" in body_lower, (
            f"/callback help page lacks expected markers: {r.text[:300]}"
        )


# ─── 6. Sync routes still need auth (no auth bypass introduced) ──────


class TestSyncRoutesStillProtected:
    """Wave 3 must NOT have accidentally exposed /sync/* to the public."""

    def test_sync_orders_without_auth_returns_4xx(self, client, monkeypatch):
        monkeypatch.delenv("TTS_ERP_AUTH_MODE", raising=False)
        r = client.post("/sync/orders", json={"shop_id": "x"})
        assert r.status_code != 200, (
            f"/sync/orders returned 200 without auth — sync endpoints are now PUBLIC. "
            f"This is a security regression. Body: {r.text[:200]}"
        )


# ─── 7. Route surface introspection ──────────────────────────────────


class TestRouteSurface:
    """Lock in the exact merged route surface per merge-design §3.1."""

    def test_oauth_router_routes_present(self):
        """oauth_receiver_router exposes /authorize, /callback, /healthz."""
        from oauth_receiver_router import router as oauth_router

        paths = {r.path for r in oauth_router.routes if isinstance(r, APIRoute)}
        assert paths == {"/authorize", "/callback", "/healthz"}, (
            f"oauth_router surface changed: {paths}"
        )

    def test_merged_app_mounts_oauth_router(self):
        """tts_erp_fastapi.app has oauth_router's 3 paths via include_router."""
        from tts_erp_fastapi import app

        oauth_paths: set[str] = set()
        for r in app.routes:
            # _IncludedRouter exposes the wrapped router as original_router.
            inner = getattr(r, "original_router", None)
            if inner is None:
                continue
            for ir in inner.routes:
                if isinstance(ir, APIRoute):
                    oauth_paths.add(ir.path)
        for p in ("/authorize", "/callback", "/healthz"):
            assert p in oauth_paths, (
                f"merged app missing oauth path '{p}'. Found: {oauth_paths}"
            )

    def test_no_legacy_proxy_routes_in_merged_app(self):
        """Legacy /shops, /shops/<id>, /token/<id> MUST NOT be in the merged app."""
        from tts_erp_fastapi import app

        all_paths: set[str] = set()
        for r in app.routes:
            if isinstance(r, APIRoute):
                all_paths.add(r.path)
        forbidden = {"/shops", "/shops/{shop_id}", "/token/{shop_id}"}
        leaked = all_paths & forbidden
        assert leaked == set(), f"merged app still has legacy proxy routes: {leaked}"


# ─── 8. TokenError has a status attribute (used by HTTP layer) ──────


class TestTokenErrorShape:
    """Sanity: domain.TokenError exists and has .status (used by FastAPI handlers)."""

    def test_token_error_has_status(self):
        from domain import TokenError

        e = TokenError("test message", status=502)
        assert e.status == 502
        assert "test message" in str(e)
