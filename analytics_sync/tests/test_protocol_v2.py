"""Tests for protocolVersion 2 (page-completeness-aware cursor).

Covers the acceptance matrix from the v2 spec:
1.  single-page day (expectedPageCount=1) advances the cursor
2.  multi-page day, only page 1 uploaded → cursor does NOT advance
3.  multi-page day, all pages uploaded → cursor advances
4.  out-of-order pages → no advance until the set is complete
5.  duplicate re-upload of any page → "duplicate" status, still counts
6.  expectedPageCount conflict (in-batch and cross-batch) → rejected
7.  rejected / missing page → cursor does not advance
8.  consecutive days: a missing earlier day blocks a later complete day
9.  v1 client compatibility (implicit expectedPageCount=1)
10. 401 / 403 SCOPE_DENIED / 429 / 413 (covered in test_auth.py,
    test_scope.py, test_rate_limit.py, test_errors.py — here we only add
    the v2-specific auth smoke)
11. concurrent duplicate of the same page and concurrent last-page races
"""
from __future__ import annotations

import threading
from datetime import date, datetime, timezone

import psycopg

from analytics_sync.domain import (
    Record,
    Scope,
    StorageKey,
    compute_idempotency_key,
)
from analytics_sync.pg_repositories import PgAnalyticsRepository

# ─── Helpers ─────────────────────────────────────────────────────────


def _make_v2_record(seller_id, advertiser_id, storage_key, campaign_id, day, page,
                    expected_page_count, **overrides):
    idem = compute_idempotency_key(
        seller_id=seller_id,
        advertiser_id=advertiser_id,
        storage_key=storage_key,
        campaign_id=campaign_id,
        day=day,
        page=page,
    )
    skey_str = storage_key.value if hasattr(storage_key, "value") else storage_key
    base = {
        "idempotencyKey": idem,
        "sourceRecordId": "uuid-" + idem[:8],
        "storageKey": skey_str,
        "campaignId": campaign_id,
        "day": day.isoformat() if hasattr(day, "isoformat") else day,
        "page": page,
        "expectedPageCount": expected_page_count,
        "endpoint": "/oec_ads/shopping/v1/oec/stat/post_product_list",
        "method": "POST",
        "requestBody": {"campaign_id": campaign_id},
        "response": {"data": []},
        "source": "background_poll",
        "capturedAt": datetime(2026, 8, day.day, 3, 0, 0, tzinfo=timezone.utc).isoformat(),
        "schemaVersion": 2,
    }
    base.update(overrides)
    return base


def _post_v2(client, token, seller_id, records, advertiser_id="adv-1"):
    return client.post(
        "/v1/analytics/sync/batches",
        json={
            "protocolVersion": 2,
            "scope": {"sellerId": seller_id, "advertiserId": advertiser_id},
            "records": records,
        },
        headers={"Authorization": f"Bearer {token}"},
    )


def _cursor(fastapi_client, token, seller, advertiser="adv-1"):
    resp = fastapi_client.get(
        f"/v1/analytics/sync/cursor?sellerId={seller}&advertiserId={advertiser}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    return resp.json()["data"]["items"]


def _cursor_for(fastapi_client, token, seller, storage_key, campaign_id, advertiser="adv-1"):
    """Return the cursor item for the given unit, failing the test if absent."""
    items = _cursor(fastapi_client, token, seller, advertiser)
    for it in items:
        if it["storageKey"] == storage_key and it["campaignId"] == campaign_id:
            return it
    raise AssertionError(
        f"no cursor item for {storage_key}/{campaign_id}; items={items}"
    )


# ─── 1. single-page day advances cursor ──────────────────────────────


def test_v2_single_page_day_advances_cursor(fastapi_client, sync_token):
    seller = "TEST_v2_single"
    rec = _make_v2_record(seller, "adv-1", StorageKey.PRODUCT_ANALYSES, "c-1",
                          date(2026, 8, 27), 1, 1)
    resp = _post_v2(fastapi_client, sync_token, seller, [rec])
    assert resp.status_code == 200
    body = resp.json()
    assert body["data"]["accepted"][0]["status"] == "inserted"
    assert body["data"]["rejected"] == []

    item = _cursor_for(fastapi_client, sync_token, seller, "productAnalyses", "c-1")
    assert item is not None
    assert item["latestCompletedDay"] == "2026-08-27"
    assert item["nextRequiredDay"] == "2026-08-28"


# ─── 2. multi-page day, page 1 only → no advance ─────────────────────


def test_v2_page1_of_3_does_not_advance_cursor(fastapi_client, sync_token):
    seller = "TEST_v2_p1only"
    rec = _make_v2_record(seller, "adv-1", StorageKey.PRODUCT_ANALYSES, "c-1",
                          date(2026, 8, 27), 1, 3)
    resp = _post_v2(fastapi_client, sync_token, seller, [rec])
    assert resp.status_code == 200
    assert resp.json()["data"]["accepted"][0]["status"] == "inserted"

    item = _cursor_for(fastapi_client, sync_token, seller, "productAnalyses", "c-1")
    assert item is not None
    assert item["latestCompletedDay"] is None
    # The day with missing pages is the next required day.
    assert item["nextRequiredDay"] == "2026-08-27"


# ─── 3. all pages uploaded → advance ─────────────────────────────────


def test_v2_all_3_pages_advance_cursor(fastapi_client, sync_token):
    seller = "TEST_v2_all3"
    records = [
        _make_v2_record(seller, "adv-1", StorageKey.SESSION_ANALYSES, "c-1",
                        date(2026, 8, 26), p, 3)
        for p in (1, 2, 3)
    ]
    resp = _post_v2(fastapi_client, sync_token, seller, records)
    assert resp.status_code == 200
    assert len(resp.json()["data"]["accepted"]) == 3

    item = _cursor_for(fastapi_client, sync_token, seller, "sessionAnalyses", "c-1")
    assert item["latestCompletedDay"] == "2026-08-26"
    assert item["nextRequiredDay"] == "2026-08-27"


# ─── 4. out-of-order pages → advance only when complete ──────────────


def test_v2_out_of_order_pages(fastapi_client, sync_token):
    seller = "TEST_v2_ooo"
    day = date(2026, 8, 25)

    # Page 2 first — day incomplete.
    resp = _post_v2(fastapi_client, sync_token, seller, [
        _make_v2_record(seller, "adv-1", StorageKey.CAMPAIGN_CHANGE_LOGS, "c-1", day, 2, 3),
    ])
    assert resp.status_code == 200
    item = _cursor_for(fastapi_client, sync_token, seller, "campaignChangeLogs", "c-1")
    assert item["latestCompletedDay"] is None
    assert item["nextRequiredDay"] == "2026-08-25"

    # Page 3 — still incomplete (page 1 missing).
    resp = _post_v2(fastapi_client, sync_token, seller, [
        _make_v2_record(seller, "adv-1", StorageKey.CAMPAIGN_CHANGE_LOGS, "c-1", day, 3, 3),
    ])
    assert resp.status_code == 200
    item = _cursor_for(fastapi_client, sync_token, seller, "campaignChangeLogs", "c-1")
    assert item["latestCompletedDay"] is None

    # Page 1 — completes the day.
    resp = _post_v2(fastapi_client, sync_token, seller, [
        _make_v2_record(seller, "adv-1", StorageKey.CAMPAIGN_CHANGE_LOGS, "c-1", day, 1, 3),
    ])
    assert resp.status_code == 200
    item = _cursor_for(fastapi_client, sync_token, seller, "campaignChangeLogs", "c-1")
    assert item["latestCompletedDay"] == "2026-08-25"
    assert item["nextRequiredDay"] == "2026-08-26"


# ─── 5. duplicate re-upload counts toward completeness ────────────────


def test_v2_duplicate_pages_participate_in_completeness(fastapi_client, sync_token):
    seller = "TEST_v2_duppages"
    day = date(2026, 8, 24)
    pages = [
        _make_v2_record(seller, "adv-1", StorageKey.PRODUCT_ANALYSES, "c-1", day, p, 2)
        for p in (1, 2)
    ]

    # First upload: both pages.
    resp = _post_v2(fastapi_client, sync_token, seller, pages)
    assert resp.status_code == 200
    assert all(a["status"] == "inserted" for a in resp.json()["data"]["accepted"])

    # Re-upload page 1 only → duplicate, day still complete.
    resp = _post_v2(fastapi_client, sync_token, seller, [pages[0]])
    assert resp.status_code == 200
    assert resp.json()["data"]["accepted"][0]["status"] == "duplicate"

    item = _cursor_for(fastapi_client, sync_token, seller, "productAnalyses", "c-1")
    assert item["latestCompletedDay"] == "2026-08-24"
    assert item["nextRequiredDay"] == "2026-08-25"


# ─── 6. expectedPageCount conflicts ───────────────────────────────────


def test_v2_in_batch_page_count_conflict_rejected(fastapi_client, sync_token):
    """Two records for the same daily unit with different expectedPageCount
    in ONE batch: the second is rejected PAGE_COUNT_CONFLICT, cursor stays."""
    seller = "TEST_v2_conflict_batch"
    day = date(2026, 8, 23)
    r1 = _make_v2_record(seller, "adv-1", StorageKey.PRODUCT_ANALYSES, "c-1", day, 1, 3)
    r2 = _make_v2_record(seller, "adv-1", StorageKey.PRODUCT_ANALYSES, "c-1", day, 2, 2)

    resp = _post_v2(fastapi_client, sync_token, seller, [r1, r2])
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["data"]["accepted"]) == 1
    assert len(body["data"]["rejected"]) == 1
    rej = body["data"]["rejected"][0]
    assert rej["code"] == "PAGE_COUNT_CONFLICT"
    assert rej["retryable"] is False
    assert rej["idempotencyKey"] == r2["idempotencyKey"]

    item = _cursor_for(fastapi_client, sync_token, seller, "productAnalyses", "c-1")
    assert item["latestCompletedDay"] is None


def test_v2_cross_batch_page_count_conflict_rejected(fastapi_client, sync_token):
    """Day already persisted with expectedPageCount=3; a later batch claiming
    2 pages for the same day is rejected and the cursor does not move."""
    seller = "TEST_v2_conflict_xbatch"
    day = date(2026, 8, 22)

    resp = _post_v2(fastapi_client, sync_token, seller, [
        _make_v2_record(seller, "adv-1", StorageKey.PRODUCT_ANALYSES, "c-1", day, 1, 3),
    ])
    assert resp.status_code == 200

    # New batch with a different expectedPageCount for the same unit+day.
    resp = _post_v2(fastapi_client, sync_token, seller, [
        _make_v2_record(seller, "adv-1", StorageKey.PRODUCT_ANALYSES, "c-1", day, 2, 2),
    ])
    assert resp.status_code == 200
    body = resp.json()
    assert body["data"]["accepted"] == []
    assert len(body["data"]["rejected"]) == 1
    rej = body["data"]["rejected"][0]
    assert rej["code"] == "PAGE_COUNT_CONFLICT"
    assert rej["retryable"] is False

    item = _cursor_for(fastapi_client, sync_token, seller, "productAnalyses", "c-1")
    assert item["latestCompletedDay"] is None
    assert item["nextRequiredDay"] == "2026-08-22"


# ─── 7. rejected / missing page → no advance ─────────────────────────


def test_v2_rejected_page_does_not_advance(fastapi_client, sync_token):
    """Page with a bad idempotencyKey is rejected; remaining pages do not
    complete the day."""
    seller = "TEST_v2_reject"
    day = date(2026, 8, 21)
    good = _make_v2_record(seller, "adv-1", StorageKey.PRODUCT_ANALYSES, "c-1", day, 1, 2)
    bad = _make_v2_record(seller, "adv-1", StorageKey.PRODUCT_ANALYSES, "c-1", day, 2, 2)
    bad["idempotencyKey"] = "f" * 64  # breaks canonical-key check

    resp = _post_v2(fastapi_client, sync_token, seller, [good, bad])
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["data"]["accepted"]) == 1
    assert len(body["data"]["rejected"]) == 1
    assert body["data"]["rejected"][0]["code"] == "SCHEMA_INVALID"

    item = _cursor_for(fastapi_client, sync_token, seller, "productAnalyses", "c-1")
    assert item["latestCompletedDay"] is None
    assert item["nextRequiredDay"] == "2026-08-21"


def test_v2_missing_middle_page_blocks_advance(fastapi_client, sync_token):
    """Pages 1 and 3 of 3 uploaded; page 2 missing → day incomplete."""
    seller = "TEST_v2_gap_page"
    day = date(2026, 8, 20)
    resp = _post_v2(fastapi_client, sync_token, seller, [
        _make_v2_record(seller, "adv-1", StorageKey.SESSION_ANALYSES, "c-1", day, 1, 3),
        _make_v2_record(seller, "adv-1", StorageKey.SESSION_ANALYSES, "c-1", day, 3, 3),
    ])
    assert resp.status_code == 200
    item = _cursor_for(fastapi_client, sync_token, seller, "sessionAnalyses", "c-1")
    assert item["latestCompletedDay"] is None
    assert item["nextRequiredDay"] == "2026-08-20"


def test_v2_page_exceeding_expected_rejected(fastapi_client, sync_token):
    seller = "TEST_v2_page_range"
    rec = _make_v2_record(seller, "adv-1", StorageKey.PRODUCT_ANALYSES, "c-1",
                          date(2026, 8, 20), 4, 3)
    resp = _post_v2(fastapi_client, sync_token, seller, [rec])
    assert resp.status_code == 200
    body = resp.json()
    assert body["data"]["accepted"] == []
    assert body["data"]["rejected"][0]["code"] == "SCHEMA_INVALID"
    assert body["data"]["rejected"][0]["retryable"] is False


def test_v2_missing_expected_page_count_rejected(fastapi_client, sync_token):
    """protocolVersion=2 without expectedPageCount → per-record SCHEMA_INVALID."""
    seller = "TEST_v2_no_epc"
    rec = _make_v2_record(seller, "adv-1", StorageKey.PRODUCT_ANALYSES, "c-1",
                          date(2026, 8, 20), 1, 1)
    del rec["expectedPageCount"]
    resp = _post_v2(fastapi_client, sync_token, seller, [rec])
    assert resp.status_code == 200
    body = resp.json()
    assert body["data"]["accepted"] == []
    assert body["data"]["rejected"][0]["code"] == "SCHEMA_INVALID"


# ─── 8. consecutive days: missing earlier day blocks later one ────────


def test_v2_earlier_missing_day_blocks_later_complete_day(fastapi_client, sync_token):
    """Day 20 complete, day 21 incomplete (1 of 2 pages), day 22 complete.
    Cursor must stop at 20 and nextRequiredDay must be 21 — never 23."""
    seller = "TEST_v2_daygap"
    resp = _post_v2(fastapi_client, sync_token, seller, [
        _make_v2_record(seller, "adv-1", StorageKey.PRODUCT_ANALYSES, "c-1",
                        date(2026, 8, 20), 1, 1),
        _make_v2_record(seller, "adv-1", StorageKey.PRODUCT_ANALYSES, "c-1",
                        date(2026, 8, 21), 1, 2),  # incomplete: page 2 missing
        _make_v2_record(seller, "adv-1", StorageKey.PRODUCT_ANALYSES, "c-1",
                        date(2026, 8, 22), 1, 1),
    ])
    assert resp.status_code == 200
    assert len(resp.json()["data"]["accepted"]) == 3

    item = _cursor_for(fastapi_client, sync_token, seller, "productAnalyses", "c-1")
    assert item["latestCompletedDay"] == "2026-08-20"
    assert item["nextRequiredDay"] == "2026-08-21"

    # Completing day 21 lets the cursor roll through 22 as well.
    resp = _post_v2(fastapi_client, sync_token, seller, [
        _make_v2_record(seller, "adv-1", StorageKey.PRODUCT_ANALYSES, "c-1",
                        date(2026, 8, 21), 2, 2),
    ])
    assert resp.status_code == 200
    item = _cursor_for(fastapi_client, sync_token, seller, "productAnalyses", "c-1")
    assert item["latestCompletedDay"] == "2026-08-22"
    assert item["nextRequiredDay"] == "2026-08-23"


# ─── 9. v1 compatibility ──────────────────────────────────────────────


def test_v1_client_still_accepted_and_advances(fastapi_client, sync_token):
    """v1 request (no expectedPageCount) is treated as a single-page day and
    advances the cursor exactly like before."""
    seller = "TEST_v2_v1compat"
    idem = compute_idempotency_key(
        seller_id=seller, advertiser_id="adv-1",
        storage_key=StorageKey.PRODUCT_ANALYSES, campaign_id="c-1",
        day=date(2026, 8, 19), page=1,
    )
    resp = fastapi_client.post(
        "/v1/analytics/sync/batches",
        json={
            "protocolVersion": 1,
            "scope": {"sellerId": seller, "advertiserId": "adv-1"},
            "records": [{
                "idempotencyKey": idem,
                "storageKey": "productAnalyses",
                "campaignId": "c-1",
                "day": "2026-08-19",
                "page": 1,
                "endpoint": "/test",
                "method": "POST",
                "response": {"data": []},
                "source": "background_poll",
                "capturedAt": "2026-08-19T03:00:00.000Z",
                "schemaVersion": 1,
            }],
        },
        headers={"Authorization": f"Bearer {sync_token}"},
    )
    assert resp.status_code == 200
    assert resp.json()["data"]["accepted"][0]["status"] == "inserted"

    item = _cursor_for(fastapi_client, sync_token, seller, "productAnalyses", "c-1")
    assert item["latestCompletedDay"] == "2026-08-19"
    assert item["nextRequiredDay"] == "2026-08-20"


def test_v1_record_does_not_falsely_complete_v2_day(fastapi_client, sync_token):
    """A v1 record for a day that v2 declared as 3-page must NOT flip the
    day to complete. v1's implicit expectedPageCount=1 conflicts with the
    stored 3 → rejected, cursor unmoved."""
    seller = "TEST_v2_v1_shield"
    day = date(2026, 8, 18)

    # v2 client declares 3 pages, uploads page 1 only.
    resp = _post_v2(fastapi_client, sync_token, seller, [
        _make_v2_record(seller, "adv-1", StorageKey.PRODUCT_ANALYSES, "c-1", day, 1, 3),
    ])
    assert resp.status_code == 200

    # v1 client uploads its (single-page) view of the same day.
    idem = compute_idempotency_key(
        seller_id=seller, advertiser_id="adv-1",
        storage_key=StorageKey.PRODUCT_ANALYSES, campaign_id="c-1",
        day=day, page=1,
    )
    resp = fastapi_client.post(
        "/v1/analytics/sync/batches",
        json={
            "protocolVersion": 1,
            "scope": {"sellerId": seller, "advertiserId": "adv-1"},
            "records": [{
                "idempotencyKey": idem,
                "storageKey": "productAnalyses",
                "campaignId": "c-1",
                "day": day.isoformat(),
                "page": 1,
                "endpoint": "/test",
                "method": "POST",
                "response": {"data": []},
                "source": "background_poll",
                "capturedAt": "2026-08-18T03:00:00.000Z",
                "schemaVersion": 1,
            }],
        },
        headers={"Authorization": f"Bearer {sync_token}"},
    )
    assert resp.status_code == 200
    # v1's implicit expectedPageCount=1 conflicts with the stored 3.
    # The record is either a duplicate of the page-1 v2 row (same canonical
    # key) or rejected — either way the day must remain incomplete.
    item = _cursor_for(fastapi_client, sync_token, seller, "productAnalyses", "c-1")
    assert item["latestCompletedDay"] is None
    assert item["nextRequiredDay"] == "2026-08-18"


def test_unsupported_protocol_version_3_rejected(fastapi_client, sync_token):
    rec = _make_v2_record("TEST_v2_pv3", "adv-1", StorageKey.PRODUCT_ANALYSES, "c-1",
                          date(2026, 8, 18), 1, 1)
    resp = fastapi_client.post(
        "/v1/analytics/sync/batches",
        json={
            "protocolVersion": 3,
            "scope": {"sellerId": "TEST_v2_pv3", "advertiserId": "adv-1"},
            "records": [rec],
        },
        headers={"Authorization": f"Bearer {sync_token}"},
    )
    assert resp.status_code == 400
    assert resp.json()["code"] == "UNSUPPORTED_PROTOCOL_VERSION"


# ─── 10. v2 auth smoke (full 401/403/413/429 matrix lives in the
#         existing test_auth / test_scope / test_errors / test_rate_limit
#         files and is protocol-agnostic) ─────────────────────────────


def test_v2_missing_token_401(fastapi_client):
    rec = _make_v2_record("TEST_v2_auth", "adv-1", StorageKey.PRODUCT_ANALYSES, "c-1",
                          date(2026, 8, 18), 1, 1)
    resp = fastapi_client.post(
        "/v1/analytics/sync/batches",
        json={
            "protocolVersion": 2,
            "scope": {"sellerId": "TEST_v2_auth", "advertiserId": "adv-1"},
            "records": [rec],
        },
    )
    assert resp.status_code == 401


def test_v2_scope_denied_403(fastapi_client, seller_scoped_token):
    rec = _make_v2_record("TEST_other_seller", "adv-1", StorageKey.PRODUCT_ANALYSES, "c-1",
                          date(2026, 8, 18), 1, 1)
    resp = fastapi_client.post(
        "/v1/analytics/sync/batches",
        json={
            "protocolVersion": 2,
            "scope": {"sellerId": "TEST_other_seller", "advertiserId": "adv-1"},
            "records": [rec],
        },
        headers={"Authorization": f"Bearer {seller_scoped_token}"},
    )
    assert resp.status_code == 403
    assert resp.json()["code"] == "SCOPE_DENIED"


# ─── 11. concurrency: duplicate same page + racing last page ─────────


def _repo_record(seller, adv, skey, camp, day, page, expected):
    idem = compute_idempotency_key(
        seller_id=seller, advertiser_id=adv,
        storage_key=skey, campaign_id=camp, day=day, page=page,
    )
    return Record(
        idempotency_key=idem,
        source_record_id="uuid-" + idem[:8],
        storage_key=skey,
        campaign_id=camp,
        day=day,
        page=page,
        endpoint="/test",
        method="POST",
        request_body=None,
        response={"data": []},
        source="background_poll",
        captured_at=datetime(2026, 8, day.day, 3, 0, 0, tzinfo=timezone.utc),
        schema_version=2,
        expected_page_count=expected,
        protocol_version=2,
    )


def test_v2_concurrent_same_page_exactly_one_insert(db_url):
    """N threads race the same (day, page=1 of 3): one inserted, rest
    duplicate; the day must NOT be complete afterwards."""
    seller = "TEST_v2_conc_same"
    day = date(2026, 8, 17)
    rec = _repo_record(seller, "adv-1", StorageKey.PRODUCT_ANALYSES, "c-1", day, 1, 3)
    scope = Scope(seller_id=seller, advertiser_id="adv-1", shop_name=None)
    kwargs = {"today_in_shop_tz": date(2026, 8, 27), "bootstrap_day": day}

    n_threads = 6
    results: list[str] = []
    lock = threading.Lock()
    barrier = threading.Barrier(n_threads)

    def worker():
        try:
            r = PgAnalyticsRepository().upsert_records(
                scope, [rec], request_id=f"req-{threading.get_ident()}", **kwargs
            )
            with lock:
                results.append(r.accepted[0].status)
        finally:
            barrier.wait()

    threads = [threading.Thread(target=worker) for _ in range(n_threads)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)

    assert results.count("inserted") == 1
    assert results.count("duplicate") == n_threads - 1

    # Day has only page 1 of 3 → incomplete.
    with psycopg.connect(db_url) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT is_complete FROM analytics_daily_completeness "
            "WHERE seller_id = %s AND storage_key = %s AND campaign_id = %s AND day = %s",
            (seller, "productAnalyses", "c-1", day),
        )
        row = cur.fetchone()
    assert row is not None
    assert row[0] is False


def test_v2_concurrent_last_page_completion(db_url):
    """Pages 1,2 already stored. N threads race to upload page 3 (last one).
    Exactly one inserts; afterwards the day is complete and the cursor
    advanced exactly to that day."""
    seller = "TEST_v2_conc_last"
    day = date(2026, 8, 16)
    scope = Scope(seller_id=seller, advertiser_id="adv-1", shop_name=None)
    kwargs = {"today_in_shop_tz": date(2026, 8, 27), "bootstrap_day": day}
    repo = PgAnalyticsRepository()

    # Seed pages 1 and 2.
    for p in (1, 2):
        r = repo.upsert_records(
            scope,
            [_repo_record(seller, "adv-1", StorageKey.SESSION_ANALYSES, "c-1", day, p, 3)],
            request_id=f"seed-{p}",
            **kwargs,
        )
        assert r.accepted[0].status == "inserted"

    last = _repo_record(seller, "adv-1", StorageKey.SESSION_ANALYSES, "c-1", day, 3, 3)
    n_threads = 6
    results: list[str] = []
    lock = threading.Lock()
    barrier = threading.Barrier(n_threads)

    def worker():
        try:
            r = PgAnalyticsRepository().upsert_records(
                scope, [last], request_id=f"req-{threading.get_ident()}", **kwargs
            )
            with lock:
                results.append(r.accepted[0].status)
        finally:
            barrier.wait()

    threads = [threading.Thread(target=worker) for _ in range(n_threads)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)

    assert results.count("inserted") == 1
    assert results.count("duplicate") == n_threads - 1

    with psycopg.connect(db_url) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT is_complete FROM analytics_daily_completeness "
            "WHERE seller_id = %s AND storage_key = %s AND campaign_id = %s AND day = %s",
            (seller, "sessionAnalyses", "c-1", day),
        )
        completeness = cur.fetchone()
        cur.execute(
            "SELECT latest_completed_day FROM analytics_cursors "
            "WHERE seller_id = %s AND storage_key = %s AND campaign_id = %s",
            (seller, "sessionAnalyses", "c-1"),
        )
        cursor_row = cur.fetchone()
    assert completeness is not None and completeness[0] is True
    assert cursor_row is not None and cursor_row[0] == day
