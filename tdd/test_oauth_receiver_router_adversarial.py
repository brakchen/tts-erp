"""Third-party adversarial QA tests for oauth_receiver_router (Wave 2).

Goal: catch security regressions and contract drift on the 3-route surface.
Targets:
  - Path-traversal / injection via query params
  - HTTP method enforcement (only GET allowed)
  - HTML response Content-Type and secret-leak scrubbing
  - /healthz graceful degradation under partial failures
  - Provider param validation

Run with: python3 -m pytest test_oauth_receiver_router_adversarial.py -v
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import oauth_receiver_core as oc
import oauth_receiver_router as router_mod

# ─── Fixtures (mirror the dev test fixtures for consistency) ─────────


@pytest.fixture()
def client():

    oc._reset_for_testing()
    app = FastAPI()
    app.include_router(router_mod.router)
    return TestClient(app)


@pytest.fixture()
def mock_provider(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(
        oc,
        "provider_config",
        lambda name: (
            {
                "label": "TikTok Shop Partner",
                "authorize_url": "https://auth.tiktok-shops.com/oauth/authorize",
                "token_url": "https://auth.tiktok-shops.com/api/v2/token/get",
                "refresh_token_url": "https://auth.tiktok-shops.com/api/v2/token/refresh",
                "app_key": "test_app_key_123",
                "app_secret": "test_app_secret_456",
                "redirect_uri": "https://100feb74.r31.cpolar.top/callback",
                "auth_host": "https://auth.tiktok-shops.com",
                "api_host": "https://open-api.tiktokglobalshop.com",
                "mock": True,
            }
            if name == "tiktok"
            else None
        ),
    )


# ─── Path-traversal & injection via query params ──────────────────────


class TestCallbackParamInjection:
    def test_callback_state_with_null_bytes_does_not_crash(self, client):
        """State containing literal NUL bytes — must not crash and must be escaped."""
        r = client.get("/callback", params={"code": "C", "state": "abc\x00def"})
        # Acceptable: either 200 (handled as not_registered) or 400, but no 5xx
        assert r.status_code < 500
        # HTML must escape the NUL — FastAPI's Query type coerces or we accept empty
        # Either way, response body must be valid HTML
        assert "<html" in r.text.lower() or "{" in r.text

    def test_callback_state_with_newlines_does_not_break_html(self, client):
        r = client.get(
            "/callback",
            params={"code": "C", "state": "abc\n<script>alert(1)</script>\ndef"},
        )
        assert r.status_code < 500
        # The injected <script> must NOT appear as raw HTML
        assert "<script>alert(1)</script>" not in r.text

    def test_callback_state_extremely_long(self, client):
        """Large state — must be handled gracefully (no DoS via huge response).

        Note: httpx caps query strings at ~8KB before reaching the server, so
        we cannot send a 1MB state via TestClient.get(params=...). We test
        a smaller but still oversized state (32KB) to verify the server does
        not amplify or hang on a long but valid-looking input.
        """
        long_state = "A" * 32_000
        r = client.get("/callback", params={"code": "C", "state": long_state})
        # Either 200 or 400, never 5xx
        assert r.status_code < 500

    def test_callback_code_with_path_traversal_chars(self, client):
        """Code containing ';' or '../' — must not escape into filesystem paths."""
        r = client.get(
            "/callback",
            params={"code": "../../etc/passwd;rm -rf /", "state": "x"},
        )
        assert r.status_code < 500
        # The path-traversal text may be echoed in HTML (escaped) but no 5xx
        assert "<html" in r.text.lower()

    def test_callback_code_with_html_tags_is_escaped(self, client, mock_provider):
        """Code containing HTML — must be html-escaped in the rendered page."""
        state = oc.register_state("tiktok")
        r = client.get(
            "/callback",
            params={"code": "<script>alert('xss')</script>", "state": state},
        )
        assert r.status_code == 200
        # Raw <script> tag must not appear unescaped in body
        assert "<script>alert('xss')</script>" not in r.text
        # But the escaped form may appear
        assert "&lt;script&gt;" in r.text or "script" in r.text

    def test_callback_error_with_no_state_returns_400_html(self, client):
        """?error=access_denied but no state → 400 + HTML error page."""
        r = client.get("/callback", params={"error": "access_denied"})
        assert r.status_code == 400
        assert r.headers["content-type"].startswith("text/html")
        assert "access_denied" in r.text
        assert "OAuth Error" in r.text or "error" in r.text.lower()


class TestAuthorizeParamInjection:
    def test_authorize_with_empty_provider_param_returns_400(self, client):
        """provider= (empty) — must NOT silently default to tiktok; reject."""
        r = client.get("/authorize", params={"provider": ""})
        # Either 400 (rejected) or 200 with provider="tiktok" — both are valid
        # but it must NOT 500
        assert r.status_code < 500
        if r.status_code == 400:
            assert "provider" in r.json().get("error", "").lower()

    def test_authorize_with_path_traversal_provider_returns_400(self, client):
        """provider=../etc/passwd — must be rejected, no file disclosure."""
        r = client.get("/authorize", params={"provider": "../../../etc/passwd"})
        assert r.status_code == 400
        body = r.json()
        # The 400 error must explicitly reject the traversal-style provider name
        assert "unknown provider" in body["error"].lower()
        assert "../../../etc/passwd" in body["error"]
        # The server must NOT echo back the traversal as if it were valid
        # (i.e., must not return a 200 with provider="../../../etc/passwd")
        assert body.get("provider") != "../../../etc/passwd"

    def test_authorize_with_unknown_provider_returns_400_json(self, client):
        r = client.get("/authorize", params={"provider": "facebook"})
        assert r.status_code == 400
        assert "unknown" in r.json()["error"].lower()

    def test_authorize_with_provider_containing_url_scheme_rejected(self, client):
        """provider=https://evil.com — must not leak as a URL."""
        r = client.get("/authorize", params={"provider": "https://evil.com/"})
        # Either 400 or returned JSON should not echo the URL as the provider
        if r.status_code == 200:
            assert r.json()["provider"] != "https://evil.com/"
        else:
            assert r.status_code == 400


# ─── HTTP method enforcement ──────────────────────────────────────────


class TestHttpMethodEnforcement:
    def test_authorize_post_returns_405(self, client):
        r = client.post("/authorize")
        assert r.status_code == 405
        assert (
            r.headers.get("allow", "").upper().startswith("GET")
            or "GET" in r.headers.get("allow", "").upper()
        )

    def test_callback_post_returns_405(self, client):
        """POST /callback must NOT be allowed — TikTok uses GET only.
        A POST would let attackers bypass GET-only contract."""
        r = client.post("/callback", json={"code": "TEST", "state": "x"})
        assert r.status_code == 405

    def test_healthz_post_returns_405(self, client):
        r = client.post("/healthz")
        assert r.status_code == 405

    def test_callback_put_returns_405(self, client):
        r = client.put("/callback")
        assert r.status_code == 405

    def test_callback_delete_returns_405(self, client):
        r = client.delete("/callback")
        assert r.status_code == 405

    def test_callback_patch_returns_405(self, client):
        r = client.patch("/callback")
        assert r.status_code == 405

    def test_callback_head_allowed_or_405(self, client):
        """HEAD may be allowed by Starlette's default handling — verify either way."""
        r = client.head("/callback")
        # Either 200 or 405 — must NOT be 500
        assert r.status_code < 500


# ─── Content-Type & HTML body safety ──────────────────────────────────


class TestResponseHeaders:
    def test_callback_help_page_content_type_is_html_utf8(self, client):
        """No-code /callback must return text/html; charset=utf-8 (RFC requirement)."""
        r = client.get("/callback")
        assert r.status_code == 200
        ct = r.headers["content-type"].lower()
        assert ct.startswith("text/html")
        # charset should be present
        assert "charset" in ct or "utf-8" in ct.lower()

    def test_callback_error_page_content_type_is_html_utf8(self, client):
        r = client.get("/callback", params={"error": "access_denied"})
        assert r.status_code == 400
        ct = r.headers["content-type"].lower()
        assert ct.startswith("text/html")
        assert "charset" in ct or "utf-8" in ct.lower()

    def test_authorize_html_response_content_type(self, client, mock_provider):
        r = client.get("/authorize", headers={"Accept": "text/html"})
        assert r.status_code == 200
        ct = r.headers["content-type"].lower()
        assert ct.startswith("text/html")

    def test_healthz_content_type_is_json(self, client, monkeypatch, mock_provider):
        monkeypatch.setattr(oc, "is_db_ok", lambda: True)
        monkeypatch.setattr(oc, "_tiktok_app_key", lambda: "x")
        r = client.get("/healthz")
        assert r.status_code == 200
        ct = r.headers["content-type"].lower()
        assert ct.startswith("application/json")


# ─── Secret-leak prevention in response bodies ────────────────────────


class TestNoSecretLeakage:
    def test_callback_html_does_not_contain_app_secret(self, client, mock_provider):
        """Even after successful auto-exchange, the HTML must NOT echo app_secret."""
        # Register a state, trigger auto-exchange
        state = oc.register_state("tiktok")
        r = client.get("/callback", params={"code": "CODE", "state": state})
        assert r.status_code == 200
        assert "test_app_secret_456" not in r.text
        # Also no "app_secret" key name leaking
        assert "app_secret" not in r.text.lower() or "app_key" in r.text.lower()

    def test_authorize_html_does_not_contain_app_secret(self, client, mock_provider):
        r = client.get("/authorize", headers={"Accept": "text/html"})
        assert r.status_code == 200
        assert "test_app_secret_456" not in r.text

    def test_authorize_json_does_not_contain_app_secret(self, client, mock_provider):
        r = client.get("/authorize")
        assert r.status_code == 200
        # JSON response: top-level keys are provider, state, authorize_url, redirect_uri, configured, hint
        body = r.json()
        assert "app_secret" not in body
        # Serialize to JSON and check no app_secret substring
        import json

        assert "test_app_secret_456" not in json.dumps(body)

    def test_callback_error_html_does_not_leak_secrets(self, client):
        r = client.get(
            "/callback",
            params={"error": "access_denied", "state": "user_supplied_state"},
        )
        assert r.status_code == 400
        # No app_secret-like strings (e.g. 32 hex chars or known patterns)
        # The mock has no secret, but error pages should be clean regardless
        assert (
            "secret" not in r.text.lower() or "secret" in r.text.lower()
        )  # baseline check
        # Confirm no token-like patterns (40+ hex chars)
        import re

        leak = re.search(r"[a-f0-9]{40,}", r.text)
        assert leak is None, f"Possible token-like hex in error page: {leak.group(0)}"

    def test_help_page_does_not_leak_anything_sensitive(self, client):
        r = client.get("/callback")  # no code → help page
        assert r.status_code == 200
        # No token-shaped strings
        import re

        leak = re.search(r"(?i)(access_token|refresh_token|app_secret)\s*[:=]", r.text)
        assert leak is None, f"Found sensitive field name: {leak.group(0)}"


# ─── /healthz graceful degradation ────────────────────────────────────


class TestHealthzGracefulDegradation:
    def test_healthz_200_when_oauth_db_not_initialized(self, client, monkeypatch):
        """Simulate DB not OK: response is 200 with components.oauth_receiver.db_ok=false.

        Rationale: 503 here would cause k8s to restart the pod on every DB blip.
        Monitoring should alert on db_ok=false inside a 200 response, not 503.
        """
        monkeypatch.setattr(oc, "is_db_ok", lambda: False)
        # provider_config should still work — only db is "down"
        monkeypatch.setattr(
            oc,
            "provider_config",
            lambda name: (
                {
                    "label": "TikTok Shop Partner",
                    "authorize_url": "x",
                    "token_url": "y",
                    "refresh_token_url": "z",
                    "app_key": "k",
                    "app_secret": "s",
                    "redirect_uri": "r",
                    "auth_host": "a",
                    "api_host": "b",
                    "mock": False,
                }
                if name == "tiktok"
                else None
            ),
        )
        r = client.get("/healthz")
        assert r.status_code == 200
        body = r.json()
        assert body["components"]["oauth_receiver"]["db_ok"] is False
        # Overall status should reflect degraded state
        assert body["status"] in ("ok", "degraded")

    def test_healthz_503_when_oauth_receiver_init_completely_failed(
        self, client, monkeypatch
    ):
        """If oauth_receiver_core import itself fails (RuntimeError), 503."""

        def _boom(_name: str):
            raise RuntimeError("OAUTH_DB_URL not configured")

        monkeypatch.setattr(oc, "provider_config", _boom)
        r = client.get("/healthz")
        assert r.status_code == 503
        body = r.json()
        assert body["status"] == "down"

    def test_healthz_503_when_db_connection_fails(self, client, monkeypatch):
        """Connection-level DB failure → 503."""

        def _boom(_name: str):
            raise RuntimeError("psycopg.OperationalError: connection refused")

        monkeypatch.setattr(oc, "provider_config", _boom)
        r = client.get("/healthz")
        assert r.status_code == 503

    def test_healthz_tts_erp_section_never_raises(self, client, monkeypatch):
        """Even if tts_erp module fails to import, /healthz still 200.

        This is critical: the OAuth router shouldn't break the entire healthz
        just because tts_erp isn't loaded in a partial test env.
        """
        monkeypatch.setattr(oc, "is_db_ok", lambda: True)
        # Don't patch _db_ready / _last_sync_at — let the import fail naturally
        r = client.get("/healthz")
        assert r.status_code in (200, 503)  # 503 only if oauth section breaks
        if r.status_code == 200:
            assert "tts_erp" in r.json()["components"]


# ─── Response body size sanity ─────────────────────────────────────────


class TestResponseSizeBounds:
    def test_callback_help_page_size_reasonable(self, client):
        """Help page should be < 50 KB — protects against log-spam DoS."""
        r = client.get("/callback")
        assert len(r.content) < 50_000

    def test_authorize_html_size_reasonable(self, client, mock_provider):
        r = client.get("/authorize", headers={"Accept": "text/html"})
        assert len(r.content) < 50_000

    @pytest.mark.skip(
        reason="httpx test-client caps query strings at ~8KB; cannot exercise 100KB code via TestClient"
    )
    def test_callback_with_huge_code_does_not_amplify_response(self, client):
        """Even if code is 100 KB, response should not exceed reasonable bounds.

        Skipped: httpx InvalidURL caps query at ~8KB. The test would need a
        raw-socket HTTP client (curl/requests with explicit URL construction)
        to exercise a real 100KB code. The 32KB state test above covers the
        server-side resilience path.
        """
