"""Tests for the rate limiter.

Per-token sliding window, default 100 req/min. Exceeding returns 429
with Retry-After. Anonymous traffic is bucketed by IP.
"""
from __future__ import annotations

import os
import time

import pytest
from fastapi.testclient import TestClient

from analytics_sync import rate_limit as rl


@pytest.fixture(autouse=True)
def _tight_rate_limit(monkeypatch):
    """Force a low limit so tests don't need to fire 100+ requests."""
    monkeypatch.setenv("ANALYTICS_SYNC_RATE_LIMIT_PER_MIN", "5")
    rl.reset_buckets()


def test_under_limit_passes_through(fastapi_client, sync_token):
    headers = {"Authorization": f"Bearer {sync_token}"}
    for _ in range(5):
        resp = fastapi_client.get(
            "/v1/analytics/sync/cursor?sellerId=TEST_rl&advertiserId=adv",
            headers=headers,
        )
        assert resp.status_code == 200, resp.text


def test_over_limit_returns_429_with_retry_after(fastapi_client, sync_token):
    headers = {"Authorization": f"Bearer {sync_token}"}
    # Burn the budget.
    for _ in range(5):
        fastapi_client.get(
            "/v1/analytics/sync/cursor?sellerId=TEST_rl&advertiserId=adv",
            headers=headers,
        )
    # Next one is rate-limited.
    resp = fastapi_client.get(
        "/v1/analytics/sync/cursor?sellerId=TEST_rl&advertiserId=adv",
        headers=headers,
    )
    assert resp.status_code == 429
    body = resp.json()
    assert body["code"] == "RATE_LIMITED"
    assert body["retryable"] is True
    assert resp.headers["retry-after"] is not None
    assert int(resp.headers["retry-after"]) >= 1


def test_different_tokens_have_independent_buckets(fastapi_client, db_url):
    """Token A's quota exhaustion does not affect token B."""
    # Mint two tokens via direct DB inserts.
    import hashlib, secrets
    from analytics_sync.auth import clear_cache
    clear_cache()

    def mint_token(label: str) -> str:
        plaintext = f"anlsync_TEST_{secrets.token_urlsafe(16)}"
        h = hashlib.sha256(plaintext.encode()).hexdigest()
        import psycopg
        with psycopg.connect(db_url) as conn, conn.cursor() as cur:
            cur.execute(
                "INSERT INTO analytics_sync_tokens (key_prefix, key_hash, name, enabled) "
                "VALUES (%s, %s, %s, true)",
                (plaintext[:16], h, f"TEST_rl_{label}"),
            )
            conn.commit()
        return plaintext

    token_a = mint_token("A")
    token_b = mint_token("B")
    clear_cache()

    # Exhaust token A.
    for _ in range(5):
        fastapi_client.get(
            "/v1/analytics/sync/cursor?sellerId=TEST_rl&advertiserId=adv",
            headers={"Authorization": f"Bearer {token_a}"},
        )
    resp = fastapi_client.get(
        "/v1/analytics/sync/cursor?sellerId=TEST_rl&advertiserId=adv",
        headers={"Authorization": f"Bearer {token_a}"},
    )
    assert resp.status_code == 429

    # Token B still has full budget.
    resp = fastapi_client.get(
        "/v1/analytics/sync/cursor?sellerId=TEST_rl&advertiserId=adv",
        headers={"Authorization": f"Bearer {token_b}"},
    )
    assert resp.status_code == 200


def test_healthz_is_exempt_from_rate_limit(fastapi_client, sync_token):
    """Health checks don't burn quota — they're cheap and frequent."""
    headers = {"Authorization": f"Bearer {sync_token}"}
    # First exhaust the budget via cursor.
    for _ in range(5):
        fastapi_client.get(
            "/v1/analytics/sync/cursor?sellerId=TEST_rl&advertiserId=adv",
            headers=headers,
        )
    # /healthz still passes.
    for _ in range(20):
        resp = fastapi_client.get("/healthz", headers=headers)
        assert resp.status_code == 200


def test_allow_function_basic():
    """Unit test on the bucket primitive directly."""
    rl.reset_buckets()
    for i in range(5):
        ok, retry = rl.allow("test-key")
        assert ok is True
        assert retry == 0
    ok, retry = rl.allow("test-key")
    assert ok is False
    assert retry >= 1
