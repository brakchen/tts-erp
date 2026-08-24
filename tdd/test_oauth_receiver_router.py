"""Tests for oauth_receiver_router — thin FastAPI router (Wave 2).

The router exposes ONLY 3 endpoints:
  GET /authorize   — browser-initiated OAuth flow
  GET /callback    — TikTok OAuth redirect (must work, public contract)
  GET /healthz     — merged health check

All business logic lives in oauth_receiver_core; the router is glue.
"""

from __future__ import annotations

import time

import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

import oauth_receiver_core as oc
import oauth_receiver_router as router_mod

# ─── Test fixtures ─────────────────────────────────────────────────────


@pytest.fixture()
def client():
    """Fresh TestClient + clean module state per test.

    Mount the router on a real FastAPI() app because TestClient(APIRouter)
    fails on fastapi>=0.139 with 'fastapi_middleware_astack not found' —
    known bug where the middleware setup happens in app.build_middleware_stack
    which only runs on the top-level FastAPI instance, not on bare routers.
    """
    from fastapi import FastAPI  # local import: see fixture docstring

    oc._reset_for_testing()
    app = FastAPI()
    app.include_router(router_mod.router)
    return TestClient(app)


@pytest.fixture()
def mock_provider(monkeypatch: pytest.MonkeyPatch):
    """Force provider_config to return a 'configured, mock mode' tiktok cfg."""
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
                "mock": True,  # forces mock-mode token response
            }
            if name == "tiktok"
            else None
        ),
    )


# ─── Route registration (structural) ──────────────────────────────────


def test_router_registers_exactly_three_routes():
    """Wave 2 contract: router exposes ONLY /authorize, /callback, /healthz."""
    paths = sorted(getattr(r, "path", "") for r in router_mod.router.routes)
    assert paths == ["/authorize", "/callback", "/healthz"]


def test_router_routes_are_all_get():
    for r in router_mod.router.routes:
        # Filter to APIRoute — Mount/WebSocketRoute don't have .methods
        if not isinstance(r, APIRoute):
            continue
        assert r.methods == {"GET"}, (
            f"non-GET route registered: {getattr(r, 'path', '?')}"
        )


# ─── Slice 1: GET /authorize ──────────────────────────────────────────


def test_authorize_happy_path_returns_json(client, mock_provider):
    """JSON response with authorize_url + state."""
    r = client.get("/authorize")
    assert r.status_code == 200
    body = r.json()
    assert body["provider"] == "tiktok"
    assert body["state"]  # auto-generated, non-empty
    assert body["authorize_url"].startswith(
        "https://auth.tiktok-shops.com/oauth/authorize?"
    )
    assert body["redirect_uri"] == "https://100feb74.r31.cpolar.top/callback"
    assert body["configured"] is True
    assert "hint" in body
    # authorize_url must include the registered state for CSRF pairing
    assert f"state={body['state']}" in body["authorize_url"]


def test_authorize_with_explicit_state_reuses_it(client, mock_provider):
    """If caller supplies state, register_state must reuse it (not regenerate)."""
    r = client.get("/authorize", params={"state": "user_supplied_state_abc"})
    assert r.status_code == 200
    assert r.json()["state"] == "user_supplied_state_abc"
    # And it must be registered server-side for /callback to match
    assert "user_supplied_state_abc" in oc._states


def test_authorize_browser_accept_returns_html(client, mock_provider):
    """Browser with text/html Accept header gets the HTML landing page."""
    r = client.get("/authorize", headers={"Accept": "text/html,application/xhtml+xml"})
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/html")
    body = r.text
    assert "Authorize with" in body
    assert "state" in body.lower()
    assert "Open in browser" in body or "authorize_url" in body.lower()
    # No JSON braces leaked
    assert '"authorize_url"' not in body


def test_authorize_unknown_provider_returns_400(client):
    """provider=google is not configured → 400."""
    r = client.get("/authorize", params={"provider": "google"})
    assert r.status_code == 400
    body = r.json()
    assert "unknown provider" in body["error"].lower()
    assert "google" in body["error"]


def test_authorize_default_provider_is_tiktok(client, mock_provider):
    """When provider is omitted, default to tiktok."""
    r = client.get("/authorize")
    assert r.status_code == 200
    assert r.json()["provider"] == "tiktok"


# ─── Slice 2: GET /callback ───────────────────────────────────────────


def test_callback_no_code_returns_help_page_html(client):
    """?code missing → render help/status page (HTML, not JSON)."""
    r = client.get("/callback")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/html")
    # Help page has the status/help content
    assert (
        "Authorize" in r.text or "help" in r.text.lower() or "status" in r.text.lower()
    )


def test_callback_error_param_returns_400_html(client):
    """?error=access_denied → 400 + error HTML page."""
    r = client.get("/callback", params={"error": "access_denied", "state": "abc"})
    assert r.status_code == 400
    assert r.headers["content-type"].startswith("text/html")
    assert "access_denied" in r.text
    assert "OAuth Error" in r.text or "error" in r.text.lower()


def test_callback_code_with_matched_state_returns_success_html(client, mock_provider):
    """code + state that matches registered state → success HTML page."""
    # Register a state via /authorize first
    ar = client.get("/authorize")
    state = ar.json()["state"]

    # Now hit /callback with that state + a code
    r = client.get(
        "/callback",
        params={"code": "TEST_AUTH_CODE_123", "state": state},
    )
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/html")
    assert "TEST_AUTH_CODE_123" in r.text or "Code Captured" in r.text
    assert "matched" in r.text  # state validation status reported
    # State should be consumed (single-use)
    assert state not in oc._states


def test_callback_code_with_unregistered_state_still_renders_success(
    client, mock_provider
):
    """code without registered state → handle_callback returns state_status=not_registered,
    but the UX still shows success (TikTok callback is browser-visible)."""
    r = client.get(
        "/callback",
        params={"code": "TEST_CODE_NO_STATE", "state": "never_registered"},
    )
    assert r.status_code == 200
    assert "not_registered" in r.text  # UX discloses the CSRF status


def test_callback_expired_state_is_rejected_not_matched(client, mock_provider):
    """MEDIUM fix from WAVE1_QA_REPORT.md:
    A state whose ts + TTL < now must NOT be popped and must NOT trigger exchange.
    Tests both the helper (handle_callback with stale state) AND the route.
    """
    # Pre-register a state, then artificially age it past TTL
    state = oc.register_state("tiktok")
    oc._states[state]["ts"] = time.time() - 9999  # ancient

    # Hit /callback — the router (or handle_callback itself) must reject
    r = client.get(
        "/callback",
        params={"code": "STALE_CODE", "state": state},
    )
    # The request still completes (TikTok already redirected the browser),
    # but the rendered page must NOT auto-exchange and must report "expired"
    assert r.status_code == 200
    assert "expired" in r.text
    assert "STALE_CODE" not in r.text.split("expir")[0] or "expired" in r.text
    # The state must NOT have been popped (we want it visible for forensics)
    assert state in oc._states


def test_callback_rejects_expired_state_in_core_handle_callback(client, mock_provider):
    """Direct unit test of the core helper: handle_callback must mark expired
    states as state_status='expired' instead of 'matched'."""
    state = oc.register_state("tiktok")
    oc._states[state]["ts"] = time.time() - 9999

    result = oc.handle_callback(
        code="CODE",
        state=state,
        provider="tiktok",
        registered_states=oc._states,
    )
    assert result["handled"] is True
    assert result["kind"] == "token"
    assert result["state_status"] == "expired"
    # No auto-exchange should have happened
    assert result["token_result"] is None
    # State should NOT have been popped
    assert state in oc._states


def test_callback_fresh_state_still_works(client, mock_provider):
    """Sanity: a state that's within TTL must still match and auto-exchange."""
    state = oc.register_state("tiktok")
    # Just registered — ts is now, well within TTL
    r = client.get(
        "/callback",
        params={"code": "FRESH_CODE", "state": state},
    )
    assert r.status_code == 200
    assert "matched" in r.text
    # State consumed
    assert state not in oc._states


# ─── Slice 3: GET /healthz (merged) ───────────────────────────────────


def test_healthz_returns_200_and_components(client, mock_provider, monkeypatch):
    """Healthz returns the merged shape per merge-design.md §3.3."""
    monkeypatch.setattr(oc, "is_db_ok", lambda: True)
    monkeypatch.setattr(oc, "db_list_shops", lambda provider=None: [])
    # /healthz response uses providers from provider_config, which mock_provider
    # patches — but healthz has its own iteration. Provide a stub.
    monkeypatch.setattr(
        oc,
        "_tiktok_app_key",
        lambda: "test_app_key",
    )
    r = client.get("/healthz")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert "ts" in body
    assert "version" in body
    assert "components" in body
    assert "oauth_receiver" in body["components"]
    assert "tts_erp" in body["components"]
    assert "miaoshou" in body["components"]


def test_healthz_oauth_receiver_section_reports_db_ok_and_token_count(
    client, mock_provider, monkeypatch
):
    """The oauth_receiver section must surface db_ok + token_count."""
    # Register some state + inject a fake history entry to bump counters
    oc.register_state("tiktok")
    monkeypatch.setattr(oc, "is_db_ok", lambda: True)
    monkeypatch.setattr(oc, "db_list_shops", lambda provider=None: [])
    monkeypatch.setattr(oc, "_tiktok_app_key", lambda: "test_app_key")

    r = client.get("/healthz")
    assert r.status_code == 200
    oauth = r.json()["components"]["oauth_receiver"]
    assert oauth["db_ok"] is True
    assert oauth["active_states"] == 1
    assert oauth["token_count"] >= 0
    assert "providers" in oauth
    assert "tiktok" in oauth["providers"]


def test_healthz_returns_200_even_when_oauth_db_unavailable(
    client, mock_provider, monkeypatch
):
    """Degraded mode: oauth_receiver.db_ok=false but /healthz still 200.
    Caller (k8s/monitoring) can parse components.oauth_receiver.db_ok."""
    monkeypatch.setattr(oc, "is_db_ok", lambda: False)
    # When DB not OK, db_list_shops still returns []
    r = client.get("/healthz")
    assert r.status_code == 200
    body = r.json()
    assert body["components"]["oauth_receiver"]["db_ok"] is False
    # Overall status is still "ok" (or "degraded") — NOT a 503
    assert body["status"] in ("ok", "degraded")


def test_healthz_503_when_core_init_failed(client, monkeypatch):
    """If oauth_receiver_core.db_init would raise (no DB URL etc.), /healthz
    must surface this as a 503 so monitoring can alert."""

    # Simulate "core init failed" by making provider_config raise
    def _boom(_name: str):
        raise RuntimeError("OAUTH_DB_URL not configured")

    monkeypatch.setattr(oc, "provider_config", _boom)

    r = client.get("/healthz")
    assert r.status_code == 503
    body = r.json()
    assert body["status"] == "down"
    assert (
        "oauth_receiver" in body.get("error", "").lower()
        or "init" in body.get("error", "").lower()
    )


def test_healthz_503_when_db_unreachable(client, monkeypatch):
    """Connection-level DB failure → 503 (matches merge-design §3.3)."""

    def _boom(_name: str):
        raise RuntimeError("psycopg.OperationalError: could not connect")

    monkeypatch.setattr(oc, "provider_config", _boom)

    r = client.get("/healthz")
    assert r.status_code == 503


# ─── /healthz tts_erp.db_ok — regression for missing _db_ready() bug ──
# The original implementation imported `from tts_erp import _db_ready`
# but that symbol never existed; `except Exception` swallowed the
# ImportError and `db_ok` was permanently False. These tests pin the
# contract: db_ok MUST reflect actual DB connectivity (probed in-process).

def test_healthz_tts_erp_db_ok_true_when_psycopg_connects(
    client, monkeypatch
):
    """Regression: db_ok must be True when the probe succeeds."""
    class _FakeCursor:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def execute(self, *a, **k): pass
        def fetchone(self): return (1,)
    class _FakeConn:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def cursor(self): return _FakeCursor()
    monkeypatch.setattr(router_mod.psycopg, "connect", lambda *a, **k: _FakeConn())
    monkeypatch.setenv("TTS_ERP_DB_URL", "postgresql://x:x@127.0.0.1:5432/x")

    r = client.get("/healthz")
    assert r.status_code == 200
    assert r.json()["components"]["tts_erp"]["db_ok"] is True


def test_healthz_tts_erp_db_ok_false_when_psycopg_raises(
    client, monkeypatch
):
    """When the probe fails, db_ok is False — must NOT crash."""
    def _fail(*a, **k):
        raise RuntimeError("connection refused")
    monkeypatch.setattr(router_mod.psycopg, "connect", _fail)
    monkeypatch.setenv("TTS_ERP_DB_URL", "postgresql://x:x@127.0.0.1:9/x")

    r = client.get("/healthz")
    assert r.status_code == 200
    assert r.json()["components"]["tts_erp"]["db_ok"] is False


def test_healthz_tts_erp_db_ok_false_when_TTS_ERP_DB_URL_unset(
    client, monkeypatch
):
    """If TTS_ERP_DB_URL env var is missing, db_ok is False (no crash)."""
    monkeypatch.delenv("TTS_ERP_DB_URL", raising=False)

    r = client.get("/healthz")
    assert r.status_code == 200
    assert r.json()["components"]["tts_erp"]["db_ok"] is False
