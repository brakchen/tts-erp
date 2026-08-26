"""Tests for the /healthz oauth_receiver.token_count bug fix.

Root cause (2026-08-25 investigation):
    `token_count` was computed as `len(oc._token_history)` (an in-memory
    deque of recent token-exchange records) instead of the actual number
    of rows in `oauth_tokens`. After a service restart, the deque is
    empty, so healthz reported `token_count: 0` even when the DB held
    2+ shops. This silently broke monitoring.

Fix:
    Compute `token_count` from `oauth_receiver_core.db_count_shops()`
    which does `SELECT COUNT(*) FROM oauth_tokens`.

These tests pin the contract: token_count reflects DB rows, not the
in-memory deque. They use mocks to avoid coupling to the real DB.
"""

from __future__ import annotations

import oauth_receiver_core as oc
import oauth_receiver_router as router_mod
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


@pytest.fixture()
def client():
    """Fresh TestClient per test."""
    oc._reset_for_testing()
    app = FastAPI()
    app.include_router(router_mod.router)
    return TestClient(app)


@pytest.fixture()
def mock_provider(monkeypatch: pytest.MonkeyPatch):
    """Force provider_config to return a 'configured' tiktok cfg."""
    monkeypatch.setattr(
        oc,
        "provider_config",
        lambda name: (
            {
                "label": "TikTok Shop Partner",
                "authorize_url": "https://auth.tiktok-shops.com/oauth/authorize",
                "token_url": "https://auth.tiktok-shops.com/api/v2/token/get",
                "app_key": "test_app_key_123",
                "app_secret": "test_app_secret_456",
                "redirect_uri": "http://daqiang.nat100.top/callback",
                "auth_host": "https://auth.tiktok-shops.com",
                "api_host": "https://open-api.tiktokglobalshop.com",
                "mock": True,
            }
            if name == "tiktok"
            else None
        ),
    )


def _patch_oauth_section(monkeypatch, *, db_ok: bool, count: int):
    """Standard patches for _oauth_receiver_section's dependencies."""
    monkeypatch.setattr(oc, "is_db_ok", lambda: db_ok)
    monkeypatch.setattr(oc, "db_count_shops", lambda provider=None: count)
    monkeypatch.setattr(oc, "db_list_shops", lambda provider=None: [])


def test_token_count_reflects_db_not_in_memory_history(
    client, mock_provider, monkeypatch
):
    """Even if in-memory _token_history has many entries, token_count
    must come from DB. This is the headline regression guard."""
    _patch_oauth_section(monkeypatch, db_ok=True, count=2)
    # Force the in-memory deque to a different size; healthz must NOT
    # pick this up.
    oc._token_history.clear()
    for i in range(7):
        oc._append_token_history_for_test({"i": i, "ok": True})

    r = client.get("/healthz")
    assert r.status_code == 200
    oauth = r.json()["components"]["oauth_receiver"]
    assert oauth["token_count"] == 2, (
        f"healthz must report DB count (2), not in-memory history size "
        f"(7). Got: {oauth['token_count']}"
    )


def test_token_count_zero_when_db_has_no_shops(client, mock_provider, monkeypatch):
    """Empty DB → token_count=0 (and 200, not 503)."""
    _patch_oauth_section(monkeypatch, db_ok=True, count=0)
    r = client.get("/healthz")
    assert r.status_code == 200
    oauth = r.json()["components"]["oauth_receiver"]
    assert oauth["db_ok"] is True
    assert oauth["token_count"] == 0


def test_token_count_zero_when_db_unavailable(client, mock_provider, monkeypatch):
    """DB unavailable → db_ok=false, token_count=0 (graceful degrade, no 5xx)."""
    _patch_oauth_section(monkeypatch, db_ok=False, count=0)
    r = client.get("/healthz")
    assert r.status_code == 200
    oauth = r.json()["components"]["oauth_receiver"]
    assert oauth["db_ok"] is False
    assert oauth["token_count"] == 0


def test_active_states_still_reflects_in_memory_oauth_state(
    client, mock_provider, monkeypatch
):
    """active_states = pending OAuth CSRF state tokens (in-memory is correct
    here because state tokens are short-lived and not persisted)."""
    _patch_oauth_section(monkeypatch, db_ok=True, count=0)
    oc._reset_for_testing()
    oc.register_state("tiktok")
    oc.register_state("tiktok")
    oc.register_state("tiktok")

    r = client.get("/healthz")
    oauth = r.json()["components"]["oauth_receiver"]
    assert oauth["active_states"] == 3
    assert oauth["token_count"] == 0  # DB still empty
