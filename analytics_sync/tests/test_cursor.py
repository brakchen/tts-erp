"""Tests for the cursor endpoint (protocol §4)."""
from __future__ import annotations

import os
from datetime import date

import psycopg
import pytest


def test_cursor_returns_empty_list_when_no_records(fastapi_client, sync_token):
    headers = {"Authorization": f"Bearer {sync_token}"}
    resp = fastapi_client.get(
        "/v1/analytics/sync/cursor?sellerId=TEST_empty_cursor&advertiserId=adv-1",
        headers=headers,
    )
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["timezone"] == "Asia/Shanghai"
    assert data["items"] == []
    assert data["nextCursor"] is None


def test_cursor_returns_latest_completed_day(fastapi_client, sync_token, db_url):
    headers = {"Authorization": f"Bearer {sync_token}"}
    seller = "TEST_cursor_latest"

    # Seed a cursor row directly so we don't have to round-trip through batch.
    with psycopg.connect(db_url) as conn, conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO analytics_cursors (seller_id, advertiser_id, storage_key, campaign_id, latest_completed_day)
            VALUES (%s, %s, %s, %s, %s)
            """,
            (seller, "adv-1", "productAnalyses", "c-1", date(2026, 8, 22)),
        )
        conn.commit()

    resp = fastapi_client.get(
        f"/v1/analytics/sync/cursor?sellerId={seller}&advertiserId=adv-1",
        headers=headers,
    )
    assert resp.status_code == 200
    items = resp.json()["data"]["items"]
    assert len(items) == 1
    assert items[0]["latestCompletedDay"] == "2026-08-22"
    assert items[0]["nextRequiredDay"] == "2026-08-23"


def test_cursor_next_required_day_is_authoritative(fastapi_client, sync_token, db_url):
    """nextRequiredDay = latest_completed + 1, regardless of when the
    client asks. This is the protocol's authoritative value."""
    headers = {"Authorization": f"Bearer {sync_token}"}
    seller = "TEST_authoritative"

    with psycopg.connect(db_url) as conn, conn.cursor() as cur:
        # 3 cursors at different states.
        cur.execute(
            """
            INSERT INTO analytics_cursors (seller_id, advertiser_id, storage_key, campaign_id, latest_completed_day)
            VALUES
                (%s, 'adv-1', 'productAnalyses', 'c-1', '2026-08-10'),
                (%s, 'adv-1', 'sessionAnalyses',  'c-1', '2026-08-22'),
                (%s, 'adv-1', 'campaignChangeLogs', 'c-1', NULL)
            """,
            (seller, seller, seller),
        )
        conn.commit()

    resp = fastapi_client.get(
        f"/v1/analytics/sync/cursor?sellerId={seller}&advertiserId=adv-1",
        headers=headers,
    )
    items = {it["storageKey"]: it for it in resp.json()["data"]["items"]}

    # Latest = 2026-08-10 → next = 2026-08-11.
    assert items["productAnalyses"]["latestCompletedDay"] == "2026-08-10"
    assert items["productAnalyses"]["nextRequiredDay"] == "2026-08-11"

    # Latest = 2026-08-22 → next = 2026-08-23.
    assert items["sessionAnalyses"]["latestCompletedDay"] == "2026-08-22"
    assert items["sessionAnalyses"]["nextRequiredDay"] == "2026-08-23"

    # NULL → bootstrap: today − 30 days in shop TZ.
    assert items["campaignChangeLogs"]["latestCompletedDay"] is None
    bootstrap_day = items["campaignChangeLogs"]["nextRequiredDay"]
    # Verify it's roughly today − 30 days.
    from datetime import datetime, timedelta
    from zoneinfo import ZoneInfo
    today = datetime.now(ZoneInfo("Asia/Shanghai")).date()
    expected = (today - timedelta(days=30)).isoformat()
    assert bootstrap_day == expected


def test_cursor_filter_by_storage_key(fastapi_client, sync_token, db_url):
    headers = {"Authorization": f"Bearer {sync_token}"}
    seller = "TEST_filter_storage"

    with psycopg.connect(db_url) as conn, conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO analytics_cursors (seller_id, advertiser_id, storage_key, campaign_id, latest_completed_day)
            VALUES
                (%s, 'adv-1', 'productAnalyses',   'c-1', '2026-08-22'),
                (%s, 'adv-1', 'sessionAnalyses',   'c-1', '2026-08-21'),
                (%s, 'adv-1', 'campaignChangeLogs','c-1', '2026-08-20')
            """,
            (seller, seller, seller),
        )
        conn.commit()

    resp = fastapi_client.get(
        f"/v1/analytics/sync/cursor?sellerId={seller}&advertiserId=adv-1&storageKey=sessionAnalyses",
        headers=headers,
    )
    items = resp.json()["data"]["items"]
    assert len(items) == 1
    assert items[0]["storageKey"] == "sessionAnalyses"


def test_cursor_filter_by_campaign_id(fastapi_client, sync_token, db_url):
    headers = {"Authorization": f"Bearer {sync_token}"}
    seller = "TEST_filter_campaign"

    with psycopg.connect(db_url) as conn, conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO analytics_cursors (seller_id, advertiser_id, storage_key, campaign_id, latest_completed_day)
            VALUES
                (%s, 'adv-1', 'productAnalyses', 'c-1', '2026-08-22'),
                (%s, 'adv-1', 'productAnalyses', 'c-2', '2026-08-21')
            """,
            (seller, seller),
        )
        conn.commit()

    resp = fastapi_client.get(
        f"/v1/analytics/sync/cursor?sellerId={seller}&advertiserId=adv-1&campaignId=c-2",
        headers=headers,
    )
    items = resp.json()["data"]["items"]
    assert len(items) == 1
    assert items[0]["campaignId"] == "c-2"


def test_cursor_timezone_returned_per_shop(fastapi_client, sync_token, db_url):
    """The server returns the canonical IANA timezone per shop, defaulting
    to Asia/Shanghai when no row exists."""
    headers = {"Authorization": f"Bearer {sync_token}"}
    seller = "TEST_tz"
    with psycopg.connect(db_url) as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO analytics_shop_timezones (seller_id, advertiser_id, timezone) "
            "VALUES (%s, 'adv-1', 'America/New_York')",
            (seller,),
        )
        conn.commit()

    resp = fastapi_client.get(
        f"/v1/analytics/sync/cursor?sellerId={seller}&advertiserId=adv-1",
        headers=headers,
    )
    assert resp.json()["data"]["timezone"] == "America/New_York"
