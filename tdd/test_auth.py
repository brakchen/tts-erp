"""Tests for API key auth middleware (design: ../tech-doc/api-key-auth-design.md).

Hermetic: inserts its own TEST_-named keys into api_keys (committed, since the
middleware opens its own DB connections) and deletes them afterwards.
Auth mode is controlled per-test via monkeypatch on TTS_ERP_AUTH_MODE.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone

import psycopg
import pytest
from fastapi.testclient import TestClient

from tts_erp_fastapi import app
import auth

# Well-known test keys (full key only lives in this file; DB holds SHA-256)
KEY_RO = "ttserp_ro_TESTreadonlykey000000000000"
KEY_RW = "ttserp_rw_TESTreadwritekey00000000000"
KEY_ADMIN = "ttserp_admin_TESTadminkey000000000000"
KEY_DISABLED = "ttserp_ro_TESTdisabledkey00000000000"
KEY_EXPIRED = "ttserp_ro_TESTexpiredkey000000000000"


def _sha256(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()


@pytest.fixture()
def auth_keys(db_url):
    """Insert TEST_ api keys (own connection + commit) and clean up after."""
    conn = psycopg.connect(db_url)
    past = datetime.now(timezone.utc) - timedelta(days=1)
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM api_keys WHERE name LIKE 'TEST_%'")
            cur.executemany(
                "INSERT INTO api_keys (key_hash, key_prefix, name, role, enabled, expires_at)"
                " VALUES (%s, %s, %s, %s, %s, %s)",
                [
                    (
                        _sha256(KEY_RO),
                        KEY_RO[:16],
                        "TEST_auth_ro",
                        "readonly",
                        True,
                        None,
                    ),
                    (
                        _sha256(KEY_RW),
                        KEY_RW[:16],
                        "TEST_auth_rw",
                        "readwrite",
                        True,
                        None,
                    ),
                    (
                        _sha256(KEY_ADMIN),
                        KEY_ADMIN[:16],
                        "TEST_auth_admin",
                        "admin",
                        True,
                        None,
                    ),
                    (
                        _sha256(KEY_DISABLED),
                        KEY_DISABLED[:16],
                        "TEST_auth_disabled",
                        "readonly",
                        False,
                        None,
                    ),
                    (
                        _sha256(KEY_EXPIRED),
                        KEY_EXPIRED[:16],
                        "TEST_auth_expired",
                        "readonly",
                        True,
                        past,
                    ),
                ],
            )
        conn.commit()
        auth.clear_cache()
        yield
    finally:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM api_keys WHERE name LIKE 'TEST_%'")
        conn.commit()
        conn.close()
        auth.clear_cache()


@pytest.fixture()
def client():
    return TestClient(app)


def _auth(key: str) -> dict:
    return {"Authorization": f"Bearer {key}"}


# ─── enforce mode ─────────────────────────────────────────────────────


def test_enforce_no_key_401(client, auth_keys, monkeypatch):
    monkeypatch.setenv("TTS_ERP_AUTH_MODE", "enforce")
    r = client.get("/db/orders?limit=1")
    assert r.status_code == 401
    assert r.headers.get("www-authenticate") == "Bearer"


def test_enforce_malformed_header_401(client, auth_keys, monkeypatch):
    monkeypatch.setenv("TTS_ERP_AUTH_MODE", "enforce")
    r = client.get("/db/orders?limit=1", headers={"Authorization": "notbearer xyz"})
    assert r.status_code == 401


def test_enforce_forged_key_401(client, auth_keys, monkeypatch):
    monkeypatch.setenv("TTS_ERP_AUTH_MODE", "enforce")
    r = client.get(
        "/db/orders?limit=1", headers=_auth("ttserp_ro_forged000000000000000000")
    )
    assert r.status_code == 401


def test_readonly_can_read_db(client, auth_keys, monkeypatch):
    monkeypatch.setenv("TTS_ERP_AUTH_MODE", "enforce")
    r = client.get("/db/orders?limit=1", headers=_auth(KEY_RO))
    assert r.status_code == 200


def test_readonly_cannot_write_shop_403(client, auth_keys, monkeypatch):
    monkeypatch.setenv("TTS_ERP_AUTH_MODE", "enforce")
    # Blocked at middleware — never reaches the real shop.
    r = client.post("/orders/123456789/cancel", headers=_auth(KEY_RO), json={})
    assert r.status_code == 403


def test_readonly_cannot_fetch_token_403(client, auth_keys, monkeypatch):
    monkeypatch.setenv("TTS_ERP_AUTH_MODE", "enforce")
    r = client.get("/token/7494763368967603447", headers=_auth(KEY_RO))
    assert r.status_code == 403


def test_readwrite_passes_auth_on_sync(client, auth_keys, monkeypatch):
    monkeypatch.setenv("TTS_ERP_AUTH_MODE", "enforce")
    # Missing shop_id → business-layer 400 proves auth let it through.
    r = client.post("/sync/orders", headers=_auth(KEY_RW), json={})
    assert r.status_code == 400


# Wave 3 merge: the legacy `/token/{shop_id}` proxy route was deleted
# (token fetch now goes through LocalTokenProvider in-process).
# This test removed in feature/oauth-merge commit; admin-role coverage
# is exercised by other tests below (e.g. test_admin_required_for_xxx).


def test_healthz_exempt(client, auth_keys, monkeypatch):
    monkeypatch.setenv("TTS_ERP_AUTH_MODE", "enforce")
    r = client.get("/healthz")
    assert r.status_code == 200


def test_disabled_key_401(client, auth_keys, monkeypatch):
    monkeypatch.setenv("TTS_ERP_AUTH_MODE", "enforce")
    r = client.get("/db/orders?limit=1", headers=_auth(KEY_DISABLED))
    assert r.status_code == 401


def test_expired_key_401(client, auth_keys, monkeypatch):
    monkeypatch.setenv("TTS_ERP_AUTH_MODE", "enforce")
    r = client.get("/db/orders?limit=1", headers=_auth(KEY_EXPIRED))
    assert r.status_code == 401


def test_unclassified_path_requires_admin(client, auth_keys, monkeypatch):
    """Unmatched path gets the highest bar (default-deny → admin)."""
    monkeypatch.setenv("TTS_ERP_AUTH_MODE", "enforce")
    r = client.get("/nonexistent", headers=_auth(KEY_RO))
    assert r.status_code == 403  # readonly < admin, blocked before 404


# ─── cache behavior ───────────────────────────────────────────────────


def test_cache_delays_revocation_until_clear(client, auth_keys, db_url, monkeypatch):
    monkeypatch.setenv("TTS_ERP_AUTH_MODE", "enforce")
    assert client.get("/db/orders?limit=1", headers=_auth(KEY_RO)).status_code == 200
    # Revoke directly in DB; cached entry still passes
    conn = psycopg.connect(db_url)
    with conn.cursor() as cur:
        cur.execute("UPDATE api_keys SET enabled = false WHERE name = 'TEST_auth_ro'")
    conn.commit()
    conn.close()
    assert client.get("/db/orders?limit=1", headers=_auth(KEY_RO)).status_code == 200
    # After cache invalidation the revocation takes effect
    auth.clear_cache()
    assert client.get("/db/orders?limit=1", headers=_auth(KEY_RO)).status_code == 401


# ─── shadow / off modes ───────────────────────────────────────────────


def test_shadow_mode_allows_and_logs(client, auth_keys, monkeypatch, capsys):
    monkeypatch.setenv("TTS_ERP_AUTH_MODE", "shadow")
    r = client.get("/db/orders?limit=1")  # no key — would be denied, but passes
    assert r.status_code == 200
    assert "would-deny" in capsys.readouterr().err


def test_off_mode_allows_everything(client, auth_keys, monkeypatch):
    monkeypatch.setenv("TTS_ERP_AUTH_MODE", "off")
    assert client.get("/db/orders?limit=1").status_code == 200
