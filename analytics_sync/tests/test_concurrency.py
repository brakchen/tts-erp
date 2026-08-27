"""Tests for concurrent duplicate-upload behavior.

The protocol promises:
- The unique index on idempotency_key makes a concurrent insert race
  safe: exactly one wins (status=inserted), all others see
  status=duplicate.
- The cursor advance is atomic per (scope, storageKey, campaignId): the
  winning insert sets latest_completed_day, duplicates do not regress.
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


def _make_record(seller, adv, skey, camp, day, page, expected_page_count=1):
    idem = compute_idempotency_key(
        seller_id=seller,
        advertiser_id=adv,
        storage_key=skey,
        campaign_id=camp,
        day=day,
        page=page,
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
        schema_version=1,
        expected_page_count=expected_page_count,
    )


def test_concurrent_duplicate_writes_have_exactly_one_insert(db_url):
    """N threads racing to insert the same idempotency_key: exactly one wins."""
    seller = "TEST_concurrent"
    day = date(2026, 8, 23)
    rec = _make_record(seller, "adv-1", StorageKey.PRODUCT_ANALYSES, "c-1", day, 1)
    scope = Scope(seller_id=seller, advertiser_id="adv-1", shop_name=None)
    # bootstrap_day == record day so a single complete day advances the cursor.
    kwargs = {"today_in_shop_tz": date(2026, 8, 27), "bootstrap_day": day}

    n_threads = 8
    results: list[tuple[bool, str]] = []
    lock = threading.Lock()
    barrier = threading.Barrier(n_threads)

    def worker():
        # Each thread opens its own connection (matches production path
        # — upsert_records opens its own connection per call).
        try:
            r = PgAnalyticsRepository().upsert_records(
                scope, [rec], request_id=f"req-{threading.get_ident()}", **kwargs
            )
            with lock:
                results.append((True, r.accepted[0].status))
        except Exception as exc:
            with lock:
                results.append((False, repr(exc)))
        barrier.wait()

    threads = [threading.Thread(target=worker) for _ in range(n_threads)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)

    successes = [r for r in results if r[0]]
    statuses = [r[1] for r in successes]
    assert len(successes) == n_threads, f"all threads must succeed; got {results}"
    # Exactly one inserted, the rest duplicate.
    assert statuses.count("inserted") == 1, f"expected 1 inserted, got {statuses}"
    assert statuses.count("duplicate") == n_threads - 1, (
        f"expected {n_threads - 1} duplicates, got {statuses}"
    )

    # Verify cursor advanced exactly once.
    with psycopg.connect(db_url) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT latest_completed_day FROM analytics_cursors WHERE seller_id = %s",
            (seller,),
        )
        row = cur.fetchone()
    assert row is not None
    assert row[0] == date(2026, 8, 23)


def test_concurrent_distinct_records_all_insert(db_url):
    """N threads, distinct idempotency keys → all N succeed as inserted."""
    seller = "TEST_concurrent_distinct"
    scope = Scope(seller_id=seller, advertiser_id="adv-1", shop_name=None)
    records = [
        _make_record(
            seller,
            "adv-1",
            StorageKey.SESSION_ANALYSES,
            "c-distinct",
            date(2026, 8, 22),
            p,
        )
        for p in range(1, 9)
    ]
    kwargs = {"today_in_shop_tz": date(2026, 8, 27), "bootstrap_day": date(2026, 8, 22)}

    n_threads = len(records)
    results: list[str] = []
    lock = threading.Lock()
    barrier = threading.Barrier(n_threads)

    def worker(rec):
        try:
            r = PgAnalyticsRepository().upsert_records(
                scope, [rec], request_id=f"req-{threading.get_ident()}", **kwargs
            )
            with lock:
                results.append(r.accepted[0].status)
        finally:
            barrier.wait()

    threads = [threading.Thread(target=worker, args=(rec,)) for rec in records]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)

    assert len(results) == n_threads
    assert all(s == "inserted" for s in results), results
