"""Coverage lift for ``tts_erp_v2/analytics/repository.py``.

Targets the previously-uncovered function bodies (lines 322-344, 390-391,
409-414, 425, 429 per the 2026-09-03 coverage report):

- ``upsert_dump`` — insert (was_inserted=True) + duplicate (was_inserted=False)
- ``has_data`` — empty + with-data + unknown endpoint (raises ValueError)
- ``fetch_timezone`` — cached value, missing-row seed, invalid-TZ repair
- ``write_audit`` — happy path + exception swallowed path (best-effort)
- ``purge_expired`` — records + audit delete counts
- ``_add_days`` / ``_subtract_days`` — cross-month / cross-year rollover

Layering: this file imports the repository module directly (not through
the HTTP API). The tests are unit-style on the function surface —
SQLAlchemy ``text()`` SQL is executed against the real dev database
(rolled-back savepoint per test, same pattern as other v2 suites).

Data isolation: TEST_/-prefixed seller/advertiser/campaign ids so the
autouse cleanup can wipe them at teardown. The
``tests_v2/api/conftest.py::_isolate_state`` does NOT cover analytics
tables — this file adds its own autouse wipe scoped to
``analytics.ad_*`` rows whose seller_id starts with ``TEST_``.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from unittest.mock import MagicMock

import pytest
from sqlalchemy import text

pytestmark = [pytest.mark.domain_api, pytest.mark.layer_integration]


# ---------------------------------------------------------------------------
# Cleanup
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _wipe_analytics_rows(db_engine):
    """Wipe analytics.ad_* rows this file touches, before AND after each test.

    The shared api/conftest.py autouse doesn't know about analytics. We
    wipe per-test so the savepoint-rollback isolation works for these
    rows too.
    """
    _wipe(db_engine)
    yield
    _wipe(db_engine)


def _wipe(db_engine) -> None:
    """Delete analytics rows whose seller_id starts with ``TEST_``.

    Delete order: child → parent (records + audit + daily_completeness
    first, then raw, then shop_timezones last).
    """
    with db_engine.begin() as conn:
        # pi-lens-ignore: python-sql-injection — literal SQL, LIKE prefix is constant
        conn.execute(
            text(
                "DELETE FROM analytics.ad_records "
                "WHERE seller_id LIKE 'TEST_%'"
            )
        )
        # pi-lens-ignore: python-sql-injection — literal SQL
        conn.execute(
            text(
                "DELETE FROM analytics.ad_daily_completeness "
                "WHERE seller_id LIKE 'TEST_%'"
            )
        )
        # pi-lens-ignore: python-sql-injection — literal SQL
        conn.execute(
            text(
                "DELETE FROM analytics.ad_raw WHERE seller_id LIKE 'TEST_%'"
            )
        )
        # pi-lens-ignore: python-sql-injection — literal SQL
        conn.execute(
            text(
                "DELETE FROM analytics.ad_shop_timezones "
                "WHERE seller_id LIKE 'TEST_%'"
            )
        )
        # audit: scope by request_id so we don't disturb prod rows.
        # pi-lens-ignore: python-sql-injection — literal SQL
        conn.execute(
            text(
                "DELETE FROM analytics.ad_audit_log "
                "WHERE request_id LIKE 'TEST_%' "
                "OR path LIKE '%TEST_repo_audit%'"
            )
        )


# ---------------------------------------------------------------------------
# upsert_dump
# ---------------------------------------------------------------------------


def _make_dump(seller: str = "TEST_repo_seller"):
    """Build a minimal DumpPayload for the repository.

    Avoids going through the FastAPI layer — directly constructs the
    dataclass so tests don't depend on the request schema validation.
    """
    from tts_erp_v2.analytics.domain import (
        DEFAULT_TIMEZONE,
        DumpPayload,
        StorageKey,
        compute_idempotency_key,
    )

    day = date(2026, 8, 23)
    return DumpPayload(
        seller_id=seller,
        advertiser_id="TEST_repo_adv",
        endpoint="/oec_ads/shopping/v1/oec/stat/post_product_list",
        method="POST",
        day=day,
        campaign_id="TEST_repo_campaign",
        request={"url": "https://tiktok.test/oec", "body": {}},
        response={"status": 200, "body": {"data": {"rows": []}}},
        captured_at=datetime(2026, 8, 23, 0, 0, 0, tzinfo=timezone.utc),
        storage_key=StorageKey.PRODUCT_ANALYSES,
        request_id="TEST_repo_req-1",
        source="tiktok-shop-data-sync",
        protocol_version=2,
        schema_version=1,
    ), DEFAULT_TIMEZONE, compute_idempotency_key(
        seller_id=seller,
        advertiser_id="TEST_repo_adv",
        storage_key=StorageKey.PRODUCT_ANALYSES,
        campaign_id="TEST_repo_campaign",
        day=day,
        page=1,
    )


def test_upsert_dump_first_call_inserts(db_session):
    """Lines 322-344 (upsert_dump first call): was_inserted=True, all 3 tables.

    After upsert_dump:
    - ad_raw has 1 row with the expected idempotency_key + payload.
    - ad_records has 1 row, body-only fields populated from the dump.
    - ad_daily_completeness has 1 row.
    """
    from tts_erp_v2.analytics import repository

    dump, _tz, expected_idem = _make_dump()
    result = repository.upsert_dump(db_session, dump, request_id=dump.request_id)

    assert result.status == "inserted"
    assert result.idempotency_key == expected_idem
    assert len(result.idempotency_key) == 64  # SHA-256 hex

    # Verify ad_raw
    raw_row = db_session.execute(
        text(
            "SELECT idempotency_key, seller_id, advertiser_id, endpoint, "
            "method, day, campaign_id FROM analytics.ad_raw "
            "WHERE seller_id = :s"
        ),
        {"s": dump.seller_id},
    ).first()
    assert raw_row is not None
    assert raw_row.idempotency_key == expected_idem
    assert raw_row.seller_id == "TEST_repo_seller"
    assert raw_row.endpoint == dump.endpoint
    assert raw_row.day == dump.day

    # Verify ad_records (derived, body-only)
    rec_row = db_session.execute(
        text(
            "SELECT idempotency_key, storage_key, request_body, response_data "
            "FROM analytics.ad_records WHERE seller_id = :s"
        ),
        {"s": dump.seller_id},
    ).first()
    assert rec_row is not None
    assert rec_row.storage_key == "productAnalyses"
    # request_body is just the "body" subset of dump.request (per the
    # repository's SQL_INSERT_RECORD_DERIVED params dict), not the full
    # request envelope.
    assert rec_row.request_body == {}
    assert rec_row.response_data == {"data": {"rows": []}}

    # Verify ad_daily_completeness
    comp_row = db_session.execute(
        text(
            "SELECT seller_id, storage_key, day FROM analytics.ad_daily_completeness "
            "WHERE seller_id = :s"
        ),
        {"s": dump.seller_id},
    ).first()
    assert comp_row is not None
    assert comp_row.storage_key == "productAnalyses"
    assert comp_row.day == dump.day


def test_upsert_dump_replay_is_duplicate(db_session):
    """Replay the same dump: status='duplicate', same idempotency_key.

    Pinned because the protocol's contract is byte-stable: re-running
    the plugin's POST /dumps with the same payload must yield the same
    idempotency_key (so retries are deduped at the storage layer).
    """
    from tts_erp_v2.analytics import repository

    dump, _tz, expected_idem = _make_dump()
    r1 = repository.upsert_dump(db_session, dump, request_id=dump.request_id)
    # Need a fresh session because upsert_dump commits internally.
    r2 = repository.upsert_dump(db_session, dump, request_id=dump.request_id)

    assert r1.status == "inserted"
    assert r2.status == "duplicate"
    assert r1.idempotency_key == r2.idempotency_key == expected_idem


def test_upsert_dump_different_seller_gets_distinct_key(db_session):
    """Different seller → different idempotency_key (5-tuple uniqueness)."""
    from tts_erp_v2.analytics import repository

    dump_a, _tz_a, key_a = _make_dump(seller="TEST_repo_a")
    dump_b, _tz_b, key_b = _make_dump(seller="TEST_repo_b")
    assert key_a != key_b
    repository.upsert_dump(db_session, dump_a, request_id="TEST_a")
    repository.upsert_dump(db_session, dump_b, request_id="TEST_b")


# ---------------------------------------------------------------------------
# has_data
# ---------------------------------------------------------------------------


def test_has_data_false_before_dump(db_session):
    """Lines 322-344 (has_data): no rows yet → has_data=False, storageKey derived."""
    from tts_erp_v2.analytics import repository

    result = repository.has_data(
        db_session,
        seller_id="TEST_repo_hasdata_empty",
        advertiser_id="TEST_repo_adv",
        endpoint="/oec_ads/shopping/v1/oec/stat/post_product_list",
        day=date(2099, 1, 1),  # future date — no rows
    )
    assert result.has_data is False
    assert result.storage_key.value == "productAnalyses"
    assert result.day == date(2099, 1, 1)


def test_has_data_true_after_dump(db_session):
    """After upsert_dump, has_data=True for the same (scope, endpoint, day)."""
    from tts_erp_v2.analytics import repository

    dump, _tz, _key = _make_dump(seller="TEST_repo_hasdata_set")
    repository.upsert_dump(db_session, dump, request_id="TEST_hd")

    result = repository.has_data(
        db_session,
        seller_id=dump.seller_id,
        advertiser_id=dump.advertiser_id,
        endpoint=dump.endpoint,
        day=dump.day,
    )
    assert result.has_data is True
    assert result.storage_key.value == "productAnalyses"


def test_has_data_unknown_endpoint_raises(db_session):
    """has_data with an endpoint outside the 3-path allowlist → ValueError.

    This is the server-side guard rail against plugin typos. Without it
    the SQL would still execute (returning has_data=False), but the
    storageKey would be unknown and break downstream consumers.
    """
    from tts_erp_v2.analytics import repository

    with pytest.raises(ValueError) as exc_info:
        repository.has_data(
            db_session,
            seller_id="TEST_repo_bad",
            advertiser_id="TEST_repo_adv",
            endpoint="/oec_ads/UNKNOWN/path",
            day=date(2026, 8, 23),
        )
    assert "unknown endpoint" in str(exc_info.value)


# ---------------------------------------------------------------------------
# fetch_timezone
# ---------------------------------------------------------------------------


def test_fetch_timezone_seeds_default_for_missing_row(db_session, db_engine):
    """Lines 322-344: missing seller_id row → seed with Asia/Shanghai.

    The lazy-seed side-effect commits a row to ad_shop_timezones on
    first read, so the next call returns the same default without
    re-seeding. We verify the return value (the DB-side state is
    covered by the api-level /v2/analytics contract tests, which use
    a non-savepoint session).
    """
    from tts_erp_v2.analytics import repository

    seller = "TEST_repo_tz_seed"
    tz1 = repository.fetch_timezone(db_session, seller_id=seller)
    assert tz1 == "Asia/Shanghai"

    # Second call should NOT re-insert (ON CONFLICT DO NOTHING) — the
    # returned value comes from the seeded row, not a re-seed.
    tz2 = repository.fetch_timezone(db_session, seller_id=seller)
    assert tz2 == "Asia/Shanghai"


def test_fetch_timezone_returns_existing_valid_value(db_session, db_engine):
    """Pre-existing valid IANA tz → returned as-is (no re-seed, no repair)."""
    from tts_erp_v2.analytics import repository

    seller = "TEST_repo_tz_existing"
    with db_engine.begin() as conn:
        # pi-lens-ignore: python-sql-injection — literal SQL, only :s bound
        conn.execute(
            text(
                "INSERT INTO analytics.ad_shop_timezones "
                "(seller_id, advertiser_id, timezone) "
                "VALUES (:s, 'TEST_adv', 'UTC')"
            ),
            {"s": seller},
        )
    tz = repository.fetch_timezone(db_session, seller_id=seller)
    assert tz == "UTC"


def test_fetch_timezone_repairs_invalid_value(db_session, db_engine):
    """Invalid IANA tz in DB → repaired to Asia/Shanghai, returns default.

    Regression guard for the production crash on 2026-08-19 where a
    hand-edited row contained 'GMT+8' (which ZoneInfo rejects). The
    handler now repairs the row in-place and falls back to the default.

    Note: the repair happens in the same session that fetch_timezone
    is called on; we verify the in-place repair by reading back via
    the same session (a separate db_engine connection cannot see
    uncommitted savepoint writes).
    """
    from tts_erp_v2.analytics import repository

    seller = "TEST_repo_tz_bad"
    with db_engine.begin() as conn:
        # pi-lens-ignore: python-sql-injection — literal SQL, only :s bound
        conn.execute(
            text(
                "INSERT INTO analytics.ad_shop_timezones "
                "(seller_id, advertiser_id, timezone) "
                "VALUES (:s, 'TEST_adv', 'GMT+8_invalid')"
            ),
            {"s": seller},
        )
    tz = repository.fetch_timezone(db_session, seller_id=seller)
    assert tz == "Asia/Shanghai"

    # The repaired row is visible to the same session. (savepoint
    # commit made it visible to subsequent SELECTs on db_session.)
    row = db_session.execute(
        text(
            "SELECT timezone FROM analytics.ad_shop_timezones "
            "WHERE seller_id = :s"
        ),
        {"s": seller},
    ).first()
    assert row is not None
    assert row.timezone == "Asia/Shanghai"


# ---------------------------------------------------------------------------
# write_audit
# ---------------------------------------------------------------------------


def test_write_audit_inserts_row(db_engine):
    """Lines 390-391 happy path: audit row lands in ad_audit_log.

    write_audit uses an INDEPENDENT engine connection (best-effort
    audit) so this works outside the test's savepoint. The row is
    visible immediately after the call returns.
    """
    from tts_erp_v2.analytics import repository

    repository.write_audit(
        request_id="TEST_repo_audit_ok",
        endpoint="dumps",
        method="POST",
        path="/v2/analytics/sync/dumps",
        status=200,
        key_prefix="ttserp_rw_",
        records_in=1,
        records_ok=1,
        records_rej=0,
        engine=db_engine,
    )

    with db_engine.begin() as conn:
        # pi-lens-ignore: python-sql-injection — literal SQL
        row = conn.execute(
            text(
                "SELECT endpoint, method, status, records_in, records_ok "
                "FROM analytics.ad_audit_log WHERE request_id = :r"
            ),
            {"r": "TEST_repo_audit_ok"},
        ).first()
    assert row is not None
    assert row.endpoint == "dumps"
    assert row.method == "POST"
    assert row.status == 200
    assert row.records_in == 1
    assert row.records_ok == 1


def test_write_audit_failure_is_swallowed(capsys):
    """Lines 390-391: a broken engine → write_audit prints to stderr, doesn't raise.

    The contract is best-effort: audit failures must NEVER bubble up
    into the request handler. We verify both branches:
    1. No exception propagates (the call returns cleanly).
    2. A diagnostic line lands on stderr mentioning the failure.
    """
    from tts_erp_v2.analytics import repository

    # Pass an engine whose .begin() raises — simulates a downed DB.
    broken_engine = MagicMock(name="broken-engine")
    broken_engine.begin.side_effect = RuntimeError("simulated DB outage")

    repository.write_audit(
        request_id="TEST_repo_audit_fail",
        endpoint="dumps",
        method="POST",
        path="/x",
        status=200,
        key_prefix=None,
        engine=broken_engine,
    )  # must not raise

    err = capsys.readouterr().err
    assert "[analytics-sync] audit write failed" in err
    assert "simulated DB outage" in err


# ---------------------------------------------------------------------------
# purge_expired
# ---------------------------------------------------------------------------


def test_purge_expired_returns_row_counts(db_session, db_engine):
    """Lines 409-414: returns dict {records_deleted, audit_deleted} with row counts.

    Seed 1 audit row with a very old created_at (older than 30 days),
    seed 1 records row with old received_at, then run with default
    retention. Both should be deleted; the counters reflect the seeded
    rows PLUS any production rows that match the retention window.

    Note on commit semantics: ``purge_expired`` does not call
    ``session.commit()`` itself (the function's docstring claims it
    does, but the implementation only executes the DELETE statements).
    In production the analytics.retention job runs ``purge_expired``
    inside the ``run_job`` context manager which owns the transaction
    boundary — but the function itself leaves commit to the caller.
    We verify the function-level contract (DELETE executed, rowcounts
    returned) by reading counts via the same session the function
    ran on; cross-connection verification would need an explicit
    commit which we don't want to fake.
    """
    from tts_erp_v2.analytics import repository

    # Seed: 1 old audit row + 1 old records row.
    very_old = datetime(2020, 1, 1, tzinfo=timezone.utc)
    with db_engine.begin() as conn:
        # pi-lens-ignore: python-sql-injection — literal SQL, only :t bound
        conn.execute(
            text(
                "INSERT INTO analytics.ad_audit_log "
                "(request_id, endpoint, method, path, status, created_at) "
                "VALUES ('TEST_repo_purge_audit', 'dumps', 'POST', '/x', 200, :t)"
            ),
            {"t": very_old},
        )
        # ad_records schema requires response_data, source, captured_at,
        # schema_version, protocol_version — see schema_tts_erp.sql.
        # pi-lens-ignore: python-sql-injection — literal SQL, only :t bound
        conn.execute(
            text(
                "INSERT INTO analytics.ad_records "
                "(idempotency_key, seller_id, advertiser_id, storage_key, "
                " campaign_id, day, endpoint, method, request_body, "
                " response_data, source, captured_at, "
                " schema_version, protocol_version, received_at) "
                "VALUES ('TEST_repo_purge_rec_key', 'TEST_repo_purge', "
                " 'TEST_adv', 'productAnalyses', 'TEST_camp', "
                " '2020-01-01', '/oec_ads/test', 'POST', '{}'::jsonb, "
                " '{}'::jsonb, 'test', :t, 1, 1, :t)"
            ),
            {"t": very_old},
        )

    result = repository.purge_expired(db_session)
    assert "records_deleted" in result
    assert "audit_deleted" in result
    # Counters must be non-negative integers. The actual numbers depend
    # on production rows that match the retention window — we only
    # assert that the function returns a well-formed payload.
    assert isinstance(result["records_deleted"], int)
    assert isinstance(result["audit_deleted"], int)
    assert result["records_deleted"] >= 1
    assert result["audit_deleted"] >= 1


def test_purge_expired_respects_custom_retention(db_session, db_engine):
    """Custom records_days / audit_days — verify the make_interval param plumbs."""
    from tts_erp_v2.analytics import repository

    # Insert one fresh audit row (created_at=now); with audit_days=30 it
    # would NOT be purged; with audit_days=0 it WOULD be purged.
    with db_engine.begin() as conn:
        # pi-lens-ignore: python-sql-injection — literal SQL
        conn.execute(
            text(
                "INSERT INTO analytics.ad_audit_log "
                "(request_id, endpoint, method, path, status) "
                "VALUES ('TEST_repo_purge_fresh', 'dumps', 'POST', '/x', 200)"
            )
        )

    # audit_days=0 → make_interval(days => 0) → keep <= now, delete > now
    # so the fresh row WILL be purged.
    result = repository.purge_expired(db_session, audit_days=0, records_days=0)
    assert result["audit_deleted"] >= 1


# ---------------------------------------------------------------------------
# _add_days / _subtract_days
# ---------------------------------------------------------------------------


def test_add_days_within_month():
    """Line 425: simple in-month addition."""
    from tts_erp_v2.analytics.repository import _add_days

    assert _add_days(date(2026, 8, 10), 5) == date(2026, 8, 15)


def test_add_days_crosses_month_boundary():
    """Line 425: cross-month rollover (Aug 31 → Sep 1)."""
    from tts_erp_v2.analytics.repository import _add_days

    assert _add_days(date(2026, 8, 31), 1) == date(2026, 9, 1)


def test_add_days_crosses_year_boundary():
    """Line 425: cross-year rollover (Dec 31 → Jan 1)."""
    from tts_erp_v2.analytics.repository import _add_days

    assert _add_days(date(2026, 12, 31), 1) == date(2027, 1, 1)


def test_add_days_negative():
    """Line 425: negative offset (subtract via add)."""
    from tts_erp_v2.analytics.repository import _add_days

    assert _add_days(date(2026, 8, 10), -5) == date(2026, 8, 5)


def test_add_days_zero():
    """Line 425: zero offset is identity."""
    from tts_erp_v2.analytics.repository import _add_days

    assert _add_days(date(2026, 8, 10), 0) == date(2026, 8, 10)


def test_subtract_days_positive():
    """Line 429: _subtract_days is the negative counterpart of _add_days."""
    from tts_erp_v2.analytics.repository import _subtract_days

    assert _subtract_days(date(2026, 8, 15), 5) == date(2026, 8, 10)
    # Cross-month subtraction
    assert _subtract_days(date(2026, 9, 1), 1) == date(2026, 8, 31)
    # Cross-year subtraction
    assert _subtract_days(date(2027, 1, 1), 1) == date(2026, 12, 31)


# ---------------------------------------------------------------------------
# STORAGE_KEY_BY_PATH (module-level constant — smoke check)
# ---------------------------------------------------------------------------


def test_storage_key_by_path_covers_three_endpoints():
    """The 3-path allowlist must contain every dump endpoint mapping.

    Regression guard: a typo in the path would silently disable
    has_data for that endpoint and break the Chrome extension's
    pre-flight check.
    """
    from tts_erp_v2.analytics.domain import StorageKey
    from tts_erp_v2.analytics.repository import STORAGE_KEY_BY_PATH

    assert (
        STORAGE_KEY_BY_PATH[
            "/oec_ads/shopping/v1/oec/stat/post_product_list"
        ]
        == StorageKey.PRODUCT_ANALYSES
    )
    assert (
        STORAGE_KEY_BY_PATH[
            "/oec_ads/shopping/v1/oec/stat/post_session_list"
        ]
        == StorageKey.SESSION_ANALYSES
    )
    assert (
        STORAGE_KEY_BY_PATH[
            "/oec_ads/shopping/v1/oec/stat/campaign_opt_log_list"
        ]
        == StorageKey.CAMPAIGN_CHANGE_LOGS
    )
    # Total of 3 entries — no surprise additions.
    assert len(STORAGE_KEY_BY_PATH) == 3
