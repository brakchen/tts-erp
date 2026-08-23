"""Tests for cross-shop isolation.

Records and cursors are scoped by (sellerId, advertiserId). Operations
on shop A must NEVER affect shop B's state, even when both use the same
campaign_id or storage_key.
"""
from __future__ import annotations

from datetime import date, datetime, timezone

import psycopg
import pytest

from analytics_sync.domain import StorageKey, compute_idempotency_key


def _make_record_dict(seller, adv, skey, camp, day, page):
    """Build a record payload in JSON wire format (camelCase, ISO date)."""
    idem = compute_idempotency_key(
        seller_id=seller, advertiser_id=adv,
        storage_key=skey, campaign_id=camp,
        day=day, page=page,
    )
    return {
        "idempotencyKey": idem,
        "sourceRecordId": "uuid-" + idem[:8],
        "storageKey": skey.value if hasattr(skey, "value") else skey,
        "campaignId": camp,
        "day": day.isoformat() if hasattr(day, "isoformat") else day,
        "page": page,
        "endpoint": "/test",
        "method": "POST",
        "requestBody": None,
        "response": {"data": []},
        "source": "background_poll",
        "capturedAt": datetime(2026, 8, day.day, 3, 0, 0, tzinfo=timezone.utc).isoformat(),
        "schemaVersion": 1,
    }


def test_cursor_isolated_by_seller(fastapi_client, sync_token):
    """Uploading records for seller A must not appear in seller B's cursor."""
    headers = {"Authorization": f"Bearer {sync_token}"}
    seller_a = "TEST_iso_A"
    seller_b = "TEST_iso_B"

    # Upload for seller A only.
    body = {
        "protocolVersion": 1,
        "scope": {"sellerId": seller_a, "advertiserId": "adv-1"},
        "records": [
            _make_record_dict(seller_a, "adv-1", StorageKey.PRODUCT_ANALYSES, "shared-camp", date(2026, 8, 23), 1)
        ],
    }
    resp = fastapi_client.post("/v1/analytics/sync/batches", json=body, headers=headers)
    assert resp.status_code == 200
    assert resp.json()["data"]["accepted"][0]["status"] == "inserted"

    # Seller B's cursor is empty.
    resp = fastapi_client.get(
        f"/v1/analytics/sync/cursor?sellerId={seller_b}&advertiserId=adv-1",
        headers=headers,
    )
    assert resp.status_code == 200
    assert resp.json()["data"]["items"] == []

    # Seller A's cursor has the record.
    resp = fastapi_client.get(
        f"/v1/analytics/sync/cursor?sellerId={seller_a}&advertiserId=adv-1",
        headers=headers,
    )
    items = resp.json()["data"]["items"]
    assert len(items) == 1
    assert items[0]["latestCompletedDay"] == "2026-08-23"


def test_same_campaign_id_distinct_shops(db_url, fastapi_client, sync_token):
    """Two sellers using the same campaign_id → independent cursor rows."""
    headers = {"Authorization": f"Bearer {sync_token}"}
    seller_a = "TEST_iso_camp_A"
    seller_b = "TEST_iso_camp_B"
    shared_camp = "shared-campaign-id"

    # Upload for both sellers on the same campaign_id but different days.
    body_a = {
        "protocolVersion": 1,
        "scope": {"sellerId": seller_a, "advertiserId": "adv-1"},
        "records": [
            _make_record_dict(seller_a, "adv-1", StorageKey.PRODUCT_ANALYSES, shared_camp, date(2026, 8, 20), 1)
        ],
    }
    body_b = {
        "protocolVersion": 1,
        "scope": {"sellerId": seller_b, "advertiserId": "adv-1"},
        "records": [
            _make_record_dict(seller_b, "adv-1", StorageKey.PRODUCT_ANALYSES, shared_camp, date(2026, 8, 25), 1)
        ],
    }
    assert fastapi_client.post("/v1/analytics/sync/batches", json=body_a, headers=headers).status_code == 200
    assert fastapi_client.post("/v1/analytics/sync/batches", json=body_b, headers=headers).status_code == 200

    # Each shop has its own cursor row.
    with psycopg.connect(db_url) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT seller_id, latest_completed_day FROM analytics_cursors "
            "WHERE seller_id IN (%s, %s) AND campaign_id = %s ORDER BY seller_id",
            (seller_a, seller_b, shared_camp),
        )
        rows = cur.fetchall()
    assert rows == [
        (seller_a, date(2026, 8, 20)),
        (seller_b, date(2026, 8, 25)),
    ]


def test_advertiser_isolation(db_url, fastapi_client, sync_token):
    """Same seller_id but different advertiser_id → independent cursors."""
    headers = {"Authorization": f"Bearer {sync_token}"}
    seller = "TEST_iso_adv"

    for adv, day in [("adv-1", date(2026, 8, 20)), ("adv-2", date(2026, 8, 22))]:
        body = {
            "protocolVersion": 1,
            "scope": {"sellerId": seller, "advertiserId": adv},
            "records": [
                _make_record_dict(seller, adv, StorageKey.PRODUCT_ANALYSES, "c-1", day, 1)
            ],
        }
        assert fastapi_client.post("/v1/analytics/sync/batches", json=body, headers=headers).status_code == 200

    with psycopg.connect(db_url) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT advertiser_id, latest_completed_day FROM analytics_cursors "
            "WHERE seller_id = %s ORDER BY advertiser_id",
            (seller,),
        )
        rows = cur.fetchall()
    assert rows == [
        ("adv-1", date(2026, 8, 20)),
        ("adv-2", date(2026, 8, 22)),
    ]
