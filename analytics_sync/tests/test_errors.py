"""Tests for error-path handling (5xx, body size, response too large)."""
from __future__ import annotations

import json

import pytest


def test_413_for_oversized_body(fastapi_client, sync_token):
    """A request body exceeding 2 MB returns 413 PAYLOAD_TOO_LARGE."""
    headers = {
        "Authorization": f"Bearer {sync_token}",
        "Content-Type": "application/json",
    }
    # Build a body that's > 2 MB.
    big = "x" * (3 * 1024 * 1024)
    resp = fastapi_client.post(
        "/v1/analytics/sync/batches",
        content=big.encode(),
        headers=headers,
    )
    assert resp.status_code == 413
    body = resp.json()
    assert body["code"] == "PAYLOAD_TOO_LARGE"
    assert body["retryable"] is False


def test_413_via_content_length_header(fastapi_client, sync_token):
    """A Content-Length header > 2 MB returns 413 without reading the body."""
    headers = {
        "Authorization": f"Bearer {sync_token}",
        "Content-Type": "application/json",
        "Content-Length": str(3 * 1024 * 1024),
    }
    resp = fastapi_client.post(
        "/v1/analytics/sync/batches",
        content=b'{"x":1}',  # body is small but Content-Length lies
        headers=headers,
    )
    assert resp.status_code == 413
    assert resp.json()["code"] == "PAYLOAD_TOO_LARGE"


def test_413_audit_log_written(db_url, fastapi_client, sync_token):
    """413 should write an audit row."""
    import psycopg
    headers = {
        "Authorization": f"Bearer {sync_token}",
        "Content-Type": "application/json",
    }
    big = "x" * (3 * 1024 * 1024)
    fastapi_client.post(
        "/v1/analytics/sync/batches",
        content=big.encode(),
        headers=headers,
    )
    with psycopg.connect(db_url) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT status, error_code FROM analytics_audit_log "
            "WHERE status = 413 ORDER BY id DESC LIMIT 1"
        )
        row = cur.fetchone()
    assert row is not None
    assert row[0] == 413
    assert row[1] == "PAYLOAD_TOO_LARGE"


def test_5xx_audit_log_on_unhandled_exception(fastapi_client, sync_token, monkeypatch):
    """A handler-level unhandled exception logs to audit and returns 500."""
    from analytics_sync import pg_repositories
    from analytics_sync.app import post_batches
    from datetime import date
    from analytics_sync.domain import StorageKey, compute_idempotency_key

    def boom(*args, **kwargs):
        raise RuntimeError("simulated DB outage")

    monkeypatch.setattr(pg_repositories.PgAnalyticsRepository, "upsert_records", boom)

    seller = "TEST_5xx"
    idem = compute_idempotency_key(
        seller_id=seller, advertiser_id="adv",
        storage_key=StorageKey.PRODUCT_ANALYSES, campaign_id="c-1",
        day=date(2026, 8, 23), page=1,
    )
    body = {
        "protocolVersion": 1,
        "scope": {"sellerId": seller, "advertiserId": "adv"},
        "records": [{
            "idempotencyKey": idem,
            "storageKey": "productAnalyses",
            "campaignId": "c-1",
            "day": "2026-08-23",
            "page": 1,
            "endpoint": "/",
            "method": "POST",
            "response": {},
            "source": "x",
            "capturedAt": "2026-08-23T00:00:00Z",
            "schemaVersion": 1,
        }],
    }
    resp = fastapi_client.post(
        "/v1/analytics/sync/batches",
        json=body,
        headers={"Authorization": f"Bearer {sync_token}"},
    )
    assert resp.status_code == 500
    body = resp.json()
    assert body["code"] == "INTERNAL_ERROR"
    assert body["retryable"] is True


def test_5xx_audit_log_records_error_code(db_url, fastapi_client, sync_token, monkeypatch):
    """The audit row for a 5xx must include the error_code."""
    from analytics_sync import pg_repositories

    def boom(*args, **kwargs):
        raise RuntimeError("simulated DB outage")

    monkeypatch.setattr(pg_repositories.PgAnalyticsRepository, "upsert_records", boom)

    from datetime import date
    from analytics_sync.domain import StorageKey, compute_idempotency_key
    seller = "TEST_5xx_audit"
    idem = compute_idempotency_key(
        seller_id=seller, advertiser_id="adv",
        storage_key=StorageKey.PRODUCT_ANALYSES, campaign_id="c-1",
        day=date(2026, 8, 23), page=1,
    )
    body = {
        "protocolVersion": 1,
        "scope": {"sellerId": seller, "advertiserId": "adv"},
        "records": [{
            "idempotencyKey": idem,
            "storageKey": "productAnalyses",
            "campaignId": "c-1",
            "day": "2026-08-23",
            "page": 1,
            "endpoint": "/",
            "method": "POST",
            "response": {},
            "source": "x",
            "capturedAt": "2026-08-23T00:00:00Z",
            "schemaVersion": 1,
        }],
    }
    fastapi_client.post(
        "/v1/analytics/sync/batches",
        json=body,
        headers={"Authorization": f"Bearer {sync_token}"},
    )

    import psycopg
    with psycopg.connect(db_url) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT status, error_code FROM analytics_audit_log "
            "WHERE key_prefix LIKE 'anlsync_TEST_%' AND status = 500 "
            "ORDER BY id DESC LIMIT 1"
        )
        row = cur.fetchone()
    assert row is not None
    assert row[0] == 500
    # Audit row carries the exception class name (server-side only —
    # the response body never echoes it).
    assert "RuntimeError" in (row[1] or "")


def test_error_response_does_not_echo_token(fastapi_client, sync_token):
    """The 401/403/413/500 envelopes must NOT contain the token or any
    client header."""
    bad_token = "anlsync_INVALID_TOKEN_VALUE_DO_NOT_LEAK"
    resp = fastapi_client.get(
        "/v1/analytics/sync/cursor?sellerId=x&advertiserId=y",
        headers={"Authorization": f"Bearer {bad_token}"},
    )
    text = resp.text
    assert bad_token not in text
    assert "Bearer " not in text
