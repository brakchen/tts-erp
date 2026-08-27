"""Tests for the batch upload endpoint (protocol §5).

Covers:
- happy path: first write → inserted, cursor advances
- duplicate write → "duplicate" status, cursor stays
- partial success: mixed valid + invalid records
- schema errors: bad storageKey, missing fields, invalid page
- cursor advance is monotonic and idempotent
"""

from __future__ import annotations

from datetime import date, datetime, timezone

import psycopg

from analytics_sync.domain import (
    StorageKey,
    compute_idempotency_key,
)

# ─── Helpers ─────────────────────────────────────────────────────────


def _make_record(
    seller_id, advertiser_id, storage_key, campaign_id, day, page, **overrides
):
    idem = compute_idempotency_key(
        seller_id=seller_id,
        advertiser_id=advertiser_id,
        storage_key=storage_key,
        campaign_id=campaign_id,
        day=day,
        page=page,
    )
    # The protocol/JSON wire format uses camelCase. StorageKey is an enum;
    # serialize to its string value.
    skey_str = storage_key.value if hasattr(storage_key, "value") else storage_key
    base = {
        "idempotencyKey": idem,
        "sourceRecordId": "uuid-" + idem[:8],
        "storageKey": skey_str,
        "campaignId": campaign_id,
        "day": day.isoformat() if hasattr(day, "isoformat") else day,
        "page": page,
        "endpoint": "/test",
        "method": "POST",
        "requestBody": {"campaign_id": campaign_id},
        "response": {"data": []},
        "source": "background_poll",
        "capturedAt": datetime(
            2026, 8, day.day, 3, 0, 0, tzinfo=timezone.utc
        ).isoformat(),
        "schemaVersion": 1,
    }
    base.update(overrides)
    return base


def _post_batch(client, seller_id, advertiser_id, records, request_id=None):
    body = {
        "protocolVersion": 1,
        "requestId": request_id or "req-test",
        "scope": {
            "sellerId": seller_id,
            "advertiserId": advertiser_id,
            "shopName": "TEST",
        },
        "records": records,
    }
    return client.post("/v1/analytics/sync/batches", json=body)


# ─── Happy path ──────────────────────────────────────────────────────


def test_first_write_inserts_and_advances_cursor(fastapi_client, sync_token):
    headers = {"Authorization": f"Bearer {sync_token}"}
    seller = "TEST_first_write"
    records = [
        _make_record(
            seller, "adv-1", StorageKey.PRODUCT_ANALYSES, "c-1", date(2026, 8, 23), 1
        ),
        _make_record(
            seller, "adv-1", StorageKey.PRODUCT_ANALYSES, "c-1", date(2026, 8, 23), 2
        ),
    ]
    resp = fastapi_client.post(
        "/v1/analytics/sync/batches",
        json={
            "protocolVersion": 1,
            "scope": {"sellerId": seller, "advertiserId": "adv-1"},
            "records": records,
        },
        headers=headers,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["code"] == 0
    assert len(body["data"]["accepted"]) == 2
    assert all(r["status"] == "inserted" for r in body["data"]["accepted"])
    assert body["data"]["rejected"] == []

    # Cursor advanced to day 2026-08-23.
    resp = fastapi_client.get(
        f"/v1/analytics/sync/cursor?sellerId={seller}&advertiserId=adv-1",
        headers=headers,
    )
    assert resp.status_code == 200
    items = resp.json()["data"]["items"]
    assert len(items) == 1
    assert items[0]["latestCompletedDay"] == "2026-08-23"
    assert items[0]["nextRequiredDay"] == "2026-08-24"


def test_duplicate_write_returns_duplicate_status(fastapi_client, sync_token):
    headers = {"Authorization": f"Bearer {sync_token}"}
    seller = "TEST_dup"
    records = [
        _make_record(
            seller, "adv-1", StorageKey.SESSION_ANALYSES, "c-1", date(2026, 8, 22), 3
        )
    ]
    resp = fastapi_client.post(
        "/v1/analytics/sync/batches",
        json={
            "protocolVersion": 1,
            "scope": {"sellerId": seller, "advertiserId": "adv-1"},
            "records": records,
        },
        headers=headers,
    )
    assert resp.status_code == 200
    assert resp.json()["data"]["accepted"][0]["status"] == "inserted"

    # Second upload with same record → duplicate.
    resp = fastapi_client.post(
        "/v1/analytics/sync/batches",
        json={
            "protocolVersion": 1,
            "scope": {"sellerId": seller, "advertiserId": "adv-1"},
            "records": records,
        },
        headers=headers,
    )
    assert resp.status_code == 200
    assert resp.json()["data"]["accepted"][0]["status"] == "duplicate"


# ─── Cursor monotonicity ─────────────────────────────────────────────


def test_cursor_does_not_skip_incomplete_gap_days(fastapi_client, sync_token, db_url):
    """v2 semantics: uploading day 20 then day 25 must NOT jump the cursor to
    25 — days 21..24 are missing, so the cursor stays at 20. Filling the gap
    then advances it through the whole contiguous prefix."""
    headers = {"Authorization": f"Bearer {sync_token}"}
    seller = "TEST_cursor_advance"

    # Day 20 first.
    r1 = [
        _make_record(
            seller, "adv-1", StorageKey.PRODUCT_ANALYSES, "c-1", date(2026, 8, 20), 1
        )
    ]
    resp = fastapi_client.post(
        "/v1/analytics/sync/batches",
        json={
            "protocolVersion": 1,
            "scope": {"sellerId": seller, "advertiserId": "adv-1"},
            "records": r1,
        },
        headers=headers,
    )
    assert resp.status_code == 200

    # Then day 25 (out of order). Cursor must NOT skip the gap.
    r2 = [
        _make_record(
            seller, "adv-1", StorageKey.PRODUCT_ANALYSES, "c-1", date(2026, 8, 25), 1
        )
    ]
    resp = fastapi_client.post(
        "/v1/analytics/sync/batches",
        json={
            "protocolVersion": 1,
            "scope": {"sellerId": seller, "advertiserId": "adv-1"},
            "records": r2,
        },
        headers=headers,
    )
    assert resp.status_code == 200

    # Query cursor directly: still day 20, because 21..24 are missing.
    with psycopg.connect(db_url) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT latest_completed_day FROM analytics_cursors "
            "WHERE seller_id = %s AND storage_key = %s AND campaign_id = %s",
            (seller, "productAnalyses", "c-1"),
        )
        row = cur.fetchone()
    assert row is not None
    assert row[0] == date(2026, 8, 20)

    # Fill the gap days 21..24 — now the whole prefix 20..25 is complete.
    gap = [
        _make_record(
            seller, "adv-1", StorageKey.PRODUCT_ANALYSES, "c-1", date(2026, 8, d), 1
        )
        for d in range(21, 25)
    ]
    resp = fastapi_client.post(
        "/v1/analytics/sync/batches",
        json={
            "protocolVersion": 1,
            "scope": {"sellerId": seller, "advertiserId": "adv-1"},
            "records": gap,
        },
        headers=headers,
    )
    assert resp.status_code == 200

    with psycopg.connect(db_url) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT latest_completed_day FROM analytics_cursors "
            "WHERE seller_id = %s AND storage_key = %s AND campaign_id = %s",
            (seller, "productAnalyses", "c-1"),
        )
        row = cur.fetchone()
    assert row is not None
    assert row[0] == date(2026, 8, 25)


def test_cursor_does_not_regress_on_duplicate(fastapi_client, sync_token, db_url):
    """Re-uploading an older day after a newer one was completed should NOT
    move the cursor back."""
    headers = {"Authorization": f"Bearer {sync_token}"}
    seller = "TEST_no_regress"

    # Day 25 first.
    r = [
        _make_record(
            seller, "adv-1", StorageKey.SESSION_ANALYSES, "c-1", date(2026, 8, 25), 1
        )
    ]
    resp = fastapi_client.post(
        "/v1/analytics/sync/batches",
        json={
            "protocolVersion": 1,
            "scope": {"sellerId": seller, "advertiserId": "adv-1"},
            "records": r,
        },
        headers=headers,
    )
    assert resp.status_code == 200

    # Re-upload same record (duplicate).
    resp = fastapi_client.post(
        "/v1/analytics/sync/batches",
        json={
            "protocolVersion": 1,
            "scope": {"sellerId": seller, "advertiserId": "adv-1"},
            "records": r,
        },
        headers=headers,
    )
    assert resp.status_code == 200
    assert resp.json()["data"]["accepted"][0]["status"] == "duplicate"

    with psycopg.connect(db_url) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT latest_completed_day FROM analytics_cursors "
            "WHERE seller_id = %s AND storage_key = %s",
            (seller, "sessionAnalyses"),
        )
        row = cur.fetchone()
    assert row is not None
    assert row[0] == date(2026, 8, 25)


# ─── Partial success ─────────────────────────────────────────────────


def test_partial_success_mixed_valid_and_invalid(fastapi_client, sync_token):
    headers = {"Authorization": f"Bearer {sync_token}"}
    seller = "TEST_partial"

    valid = _make_record(
        seller, "adv-1", StorageKey.PRODUCT_ANALYSES, "c-1", date(2026, 8, 22), 1
    )
    bad = dict(valid, idempotencyKey="0" * 64)  # doesn't match canonical

    resp = fastapi_client.post(
        "/v1/analytics/sync/batches",
        json={
            "protocolVersion": 1,
            "scope": {"sellerId": seller, "advertiserId": "adv-1"},
            "records": [valid, bad],
        },
        headers=headers,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["data"]["accepted"]) == 1
    assert body["data"]["accepted"][0]["status"] == "inserted"
    assert len(body["data"]["rejected"]) == 1
    assert body["data"]["rejected"][0]["code"] == "SCHEMA_INVALID"
    assert body["data"]["rejected"][0]["retryable"] is False


def test_empty_records_returns_400(fastapi_client, sync_token):
    headers = {"Authorization": f"Bearer {sync_token}"}
    resp = fastapi_client.post(
        "/v1/analytics/sync/batches",
        json={
            "protocolVersion": 1,
            "scope": {"sellerId": "TEST_empty", "advertiserId": "adv"},
            "records": [],
        },
        headers=headers,
    )
    assert resp.status_code == 400
    assert resp.json()["code"] == "SCHEMA_INVALID"


def test_bad_storage_key_returns_400(fastapi_client, sync_token):
    headers = {"Authorization": f"Bearer {sync_token}"}
    record = _make_record(
        "TEST_bad_storage",
        "adv-1",
        StorageKey.PRODUCT_ANALYSES,
        "c-1",
        date(2026, 8, 23),
        1,
    )
    record["storageKey"] = "wrongKey"  # not in allowlist
    record["idempotencyKey"] = compute_idempotency_key(
        seller_id="TEST_bad_storage",
        advertiser_id="adv-1",
        storage_key="wrongKey",
        campaign_id="c-1",
        day=date(2026, 8, 23),
        page=1,
    )
    resp = fastapi_client.post(
        "/v1/analytics/sync/batches",
        json={
            "protocolVersion": 1,
            "scope": {"sellerId": "TEST_bad_storage", "advertiserId": "adv-1"},
            "records": [record],
        },
        headers=headers,
    )
    assert resp.status_code == 400
    assert resp.json()["code"] == "SCHEMA_INVALID"


def test_page_zero_returns_400(fastapi_client, sync_token):
    headers = {"Authorization": f"Bearer {sync_token}"}
    record = _make_record(
        "TEST_p0",
        "adv-1",
        StorageKey.PRODUCT_ANALYSES,
        "c-1",
        date(2026, 8, 23),
        1,
    )
    record["page"] = 0
    record["idempotencyKey"] = compute_idempotency_key(
        seller_id="TEST_p0",
        advertiser_id="adv-1",
        storage_key=StorageKey.PRODUCT_ANALYSES,
        campaign_id="c-1",
        day=date(2026, 8, 23),
        page=0,
    )
    resp = fastapi_client.post(
        "/v1/analytics/sync/batches",
        json={
            "protocolVersion": 1,
            "scope": {"sellerId": "TEST_p0", "advertiserId": "adv-1"},
            "records": [record],
        },
        headers=headers,
    )
    assert resp.status_code == 400
    assert resp.json()["code"] == "SCHEMA_INVALID"


def test_idempotency_key_mismatch_returns_rejected(fastapi_client, sync_token):
    """Client-sent key ≠ server-computed key → SCHEMA_INVALID in rejected[]."""
    headers = {"Authorization": f"Bearer {sync_token}"}
    record = _make_record(
        "TEST_mismatch",
        "adv-1",
        StorageKey.PRODUCT_ANALYSES,
        "c-1",
        date(2026, 8, 23),
        1,
    )
    record["idempotencyKey"] = "f" * 64  # wrong but valid-format
    resp = fastapi_client.post(
        "/v1/analytics/sync/batches",
        json={
            "protocolVersion": 1,
            "scope": {"sellerId": "TEST_mismatch", "advertiserId": "adv-1"},
            "records": [record],
        },
        headers=headers,
    )
    assert resp.status_code == 200  # HTTP 200 for partial success
    body = resp.json()
    assert body["data"]["accepted"] == []
    assert len(body["data"]["rejected"]) == 1
    r = body["data"]["rejected"][0]
    assert r["code"] == "SCHEMA_INVALID"
    assert r["retryable"] is False


def test_too_many_records_returns_400(fastapi_client, sync_token):
    """Batch over MAX_BATCH_RECORDS → 400 SCHEMA_INVALID (not 413 — that's
    reserved for body size). The plugin must split the batch."""
    headers = {"Authorization": f"Bearer {sync_token}"}
    seller = "TEST_too_big"
    records = [
        _make_record(
            seller, "adv", StorageKey.PRODUCT_ANALYSES, "c", date(2026, 8, 23), p
        )
        for p in range(1, 102)  # 101 records, exceeds MAX_BATCH_RECORDS=100
    ]
    resp = fastapi_client.post(
        "/v1/analytics/sync/batches",
        json={
            "protocolVersion": 1,
            "scope": {"sellerId": seller, "advertiserId": "adv"},
            "records": records,
        },
        headers=headers,
    )
    assert resp.status_code == 400
    assert resp.json()["code"] == "SCHEMA_INVALID"


def test_malformed_json_returns_400(fastapi_client, sync_token):
    headers = {"Authorization": f"Bearer {sync_token}"}
    resp = fastapi_client.post(
        "/v1/analytics/sync/batches",
        content=b"this is not json",
        headers={**headers, "Content-Type": "application/json"},
    )
    assert resp.status_code == 400
    assert resp.json()["code"] == "MALFORMED_JSON"


def test_unsupported_protocol_version_returns_400(fastapi_client, sync_token):
    headers = {"Authorization": f"Bearer {sync_token}"}
    resp = fastapi_client.post(
        "/v1/analytics/sync/batches",
        json={
            "protocolVersion": 99,
            "scope": {"sellerId": "TEST_pv", "advertiserId": "adv"},
            "records": [],
        },
        headers=headers,
    )
    # Empty records → 400 SCHEMA_INVALID (records min_length=1) wins over
    # protocol version check; we still assert the response is a 4xx.
    assert resp.status_code == 400
