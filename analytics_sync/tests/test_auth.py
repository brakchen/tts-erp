"""Tests for sync-token auth middleware (Bearer / X-Sync-Token)."""
from __future__ import annotations
import hashlib
import psycopg
import secrets


def test_missing_token_returns_401(fastapi_client):
    resp = fastapi_client.get("/v1/analytics/sync/cursor?sellerId=x&advertiserId=y")
    assert resp.status_code == 401
    body = resp.json()
    assert "missing" in body["detail"].lower()


def test_invalid_token_returns_401(fastapi_client):
    resp = fastapi_client.get(
        "/v1/analytics/sync/cursor?sellerId=x&advertiserId=y",
        headers={"Authorization": "Bearer anlsync_invalid_xxxxxxxxxxxxxxxxxxxxxx"},
    )
    assert resp.status_code == 401
    body = resp.json()
    assert "invalid" in body["detail"].lower() or "expired" in body["detail"].lower()


def test_disabled_token_returns_401(fastapi_client, db_url):
    """A token that exists in DB but is enabled=false must be rejected."""
    plaintext = f"anlsync_TEST_{secrets.token_urlsafe(16)}"
    h = hashlib.sha256(plaintext.encode()).hexdigest()
    with psycopg.connect(db_url) as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO security.api_keys (key_prefix, key_hash, name, role, status) "
            "VALUES (%s, %s, %s, 'readwrite', 'disabled')",
            (plaintext[:16], h, "TEST_disabled"),
        )
        conn.commit()

    resp = fastapi_client.get(
        "/v1/analytics/sync/cursor?sellerId=x&advertiserId=y",
        headers={"Authorization": f"Bearer {plaintext}"},
    )
    assert resp.status_code == 401


def test_valid_token_passes_through(fastapi_client, sync_token):
    """A valid token grants access to the cursor endpoint."""
    resp = fastapi_client.get(
        "/v1/analytics/sync/cursor?sellerId=TEST_auth&advertiserId=adv-1",
        headers={"Authorization": f"Bearer {sync_token}"},
    )
    assert resp.status_code == 200


def test_x_sync_token_header_works(fastapi_client, sync_token):
    """The X-Sync-Token header is also accepted (per auth.py)."""
    resp = fastapi_client.get(
        "/v1/analytics/sync/cursor?sellerId=TEST_auth_hdr&advertiserId=adv-1",
        headers={"X-Sync-Token": sync_token},
    )
    assert resp.status_code == 200


def test_healthz_does_not_require_token(fastapi_client):
    """The /healthz endpoint is exempt from auth."""
    resp = fastapi_client.get("/healthz")
    assert resp.status_code == 200


def test_endpoints_does_not_require_token(fastapi_client):
    resp = fastapi_client.get("/endpoints")
    assert resp.status_code == 200
