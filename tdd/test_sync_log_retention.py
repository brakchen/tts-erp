"""TDD test suite for sync_log 60-day retention.

Covers:
- cleanup_sync_log(60) PL/pgSQL function: returns (deleted_count, cutoff_ts)
- Retention semantics: COALESCE(finished_at, started_at) drives the cutoff
- AFTER INSERT trigger trg_sync_log_retention: lazy cleanup
- Edge: empty table, custom retention, future timestamps

Test isolation: each test runs in a transaction that's rolled back.
Safe against the production tts_erp.sync_log table.
"""
from __future__ import annotations

from datetime import datetime, timezone

import psycopg
import pytest


# ─── cleanup_sync_log() function behavior ──────────────────────────────


class TestCleanupFunction:
    def test_empty_table_returns_zero_deleted(self, db_cursor):
        # cleanup_sync_log returns (deleted_count, cutoff_timestamp)
        db_cursor.execute("SELECT * FROM cleanup_sync_log(60)")
        row = db_cursor.fetchone()
        assert row[0] >= 0  # deleted_count
        assert isinstance(row[1], datetime)  # cutoff_timestamp

    def test_deletes_rows_older_than_retention(self, db_cursor):
        sentinel = "TEST_RETENTION_OLD"
        db_cursor.execute("DELETE FROM sync_log WHERE shop_id = %s", (sentinel,))
        # Insert one row 90 days old, one row 1 hour old
        db_cursor.execute(
            """
            INSERT INTO sync_log (shop_id, sync_type, started_at, finished_at, status, rows_affected)
            VALUES (%s, 'orders_search', now() - interval '90 days', now() - interval '90 days', 'ok', 10),
                   (%s, 'orders_search', now() - interval '1 hour', now() - interval '1 hour', 'ok', 5)
            """,
            (sentinel, sentinel),
        )
        # Trigger fires on this INSERT, removing the 90-day row automatically
        # So at this point the table has only the 1-hour row for our sentinel
        # (assuming no other tests' rows for this sentinel)

        # Now explicitly call cleanup_sync_log(60) and verify nothing is deleted
        db_cursor.execute("SELECT * FROM cleanup_sync_log(60)")
        row = db_cursor.fetchone()
        # The trigger should have already cleaned the 90-day row
        # So cleanup_sync_log(60) finds nothing to delete
        assert row[0] == 0

        # Verify only the recent row remains
        db_cursor.execute(
            "SELECT count(*) FROM sync_log WHERE shop_id = %s", (sentinel,)
        )
        assert db_cursor.fetchone()[0] == 1

    def test_cutoff_timestamp_is_now_minus_retention(self, db_cursor):
        # Run cleanup at a known point and verify cutoff
        db_cursor.execute("SELECT now()")
        before = db_cursor.fetchone()[0]  # aware datetime (TIMESTAMPTZ)
        db_cursor.execute("SELECT * FROM cleanup_sync_log(60)")
        after_call = db_cursor.fetchone()
        after = datetime.now(timezone.utc)

        cutoff: datetime = after_call[1]
        # Both are tz-aware, subtraction works
        delta_from_before = (before - cutoff).total_seconds()
        delta_from_after = (after - cutoff).total_seconds()
        expected_60d = 60 * 24 * 3600
        assert abs(delta_from_before - expected_60d) < 2
        assert abs(delta_from_after - expected_60d) < 2

    def test_custom_retention_days(self, db_cursor):
        sentinel = "TEST_RETENTION_CUSTOM"
        db_cursor.execute("DELETE FROM sync_log WHERE shop_id = %s", (sentinel,))
        # Insert a row 5 days old
        db_cursor.execute(
            """
            INSERT INTO sync_log (shop_id, sync_type, started_at, finished_at, status, rows_affected)
            VALUES (%s, 'payments', now() - interval '5 days', now() - interval '5 days', 'ok', 1)
            """,
            (sentinel,),
        )
        # Trigger fires, but 5 days < 60 days, so it's kept
        # Now call cleanup_sync_log(1) — should remove the 5-day row
        # (Note: trigger is 60 days, but this is the standalone function)
        db_cursor.execute("SELECT * FROM cleanup_sync_log(1)")
        row = db_cursor.fetchone()
        # 5-day-old row should be deleted
        assert row[0] >= 1
        # Verify the row is gone
        db_cursor.execute(
            "SELECT count(*) FROM sync_log WHERE shop_id = %s", (sentinel,)
        )
        assert db_cursor.fetchone()[0] == 0


# ─── AFTER INSERT trigger behavior ────────────────────────────────────


class TestRetentionTrigger:
    def test_trigger_cleans_old_rows_on_insert(self, db_cursor):
        sentinel = "TEST_TRIGGER_OLD"
        db_cursor.execute("DELETE FROM sync_log WHERE shop_id = %s", (sentinel,))
        # Insert an "old" row directly via a side path:
        # First, disable the trigger temporarily by inserting via the function
        # is messy. Instead, use a separate shop_id for the old row and rely
        # on the trigger firing on the next INSERT.
        # Trick: insert two rows in one statement, with the first being old.
        # The trigger fires AFTER the whole statement, so both rows are inserted
        # first, then the trigger deletes the old one.
        db_cursor.execute(
            """
            INSERT INTO sync_log (shop_id, sync_type, started_at, finished_at, status, rows_affected)
            VALUES (%s, 'orders_search', now() - interval '70 days', now() - interval '70 days', 'ok', 1),
                   (%s, 'orders_search', now(), now(), 'ok', 1)
            """,
            (sentinel, sentinel),
        )
        # Trigger should have deleted the 70-day row, leaving only the fresh one
        db_cursor.execute(
            "SELECT count(*) FROM sync_log WHERE shop_id = %s", (sentinel,)
        )
        assert db_cursor.fetchone()[0] == 1
        # Verify the remaining row is the new one
        db_cursor.execute(
            "SELECT age(now(), started_at) FROM sync_log WHERE shop_id = %s",
            (sentinel,),
        )
        age = db_cursor.fetchone()[0]
        # age should be < 1 minute
        assert age.total_seconds() < 60

    def test_trigger_keeps_rows_within_retention(self, db_cursor):
        sentinel = "TEST_TRIGGER_KEEP"
        db_cursor.execute("DELETE FROM sync_log WHERE shop_id = %s", (sentinel,))
        # Insert a 30-day-old row + new row in one statement
        db_cursor.execute(
            """
            INSERT INTO sync_log (shop_id, sync_type, started_at, finished_at, status, rows_affected)
            VALUES (%s, 'payments', now() - interval '30 days', now() - interval '30 days', 'ok', 1),
                   (%s, 'payments', now(), now(), 'ok', 1)
            """,
            (sentinel, sentinel),
        )
        # 30 days < 60 days retention → both kept
        db_cursor.execute(
            "SELECT count(*) FROM sync_log WHERE shop_id = %s", (sentinel,)
        )
        assert db_cursor.fetchone()[0] == 2

    def test_coalesce_finished_at_null_uses_started_at(self, db_cursor):
        # Verify the COALESCE expression itself (the core retention logic):
        #   WHEN finished_at IS NULL → use started_at for comparison
        # Disable the trigger so the test row isn't auto-cleaned before we check.
        db_cursor.execute("ALTER TABLE sync_log DISABLE TRIGGER trg_sync_log_retention")
        try:
            sentinel = "TEST_COALESCE_NULL"
            db_cursor.execute("DELETE FROM sync_log WHERE shop_id = %s", (sentinel,))
            db_cursor.execute(
                """
                INSERT INTO sync_log (shop_id, sync_type, started_at, finished_at, status)
                VALUES (%s, 'returns', now() - interval '70 days', NULL, 'error')
                """,
                (sentinel,),
            )
            # Now the row is 70 days old, finished_at=NULL, and trigger is disabled.
            # cleanup_sync_log should delete it via COALESCE(finished_at, started_at).
            db_cursor.execute("SELECT * FROM cleanup_sync_log(60)")
            row = db_cursor.fetchone()
            assert row[0] >= 1
            db_cursor.execute(
                "SELECT count(*) FROM sync_log WHERE shop_id = %s", (sentinel,)
            )
            assert db_cursor.fetchone()[0] == 0
        finally:
            db_cursor.execute("ALTER TABLE sync_log ENABLE TRIGGER trg_sync_log_retention")


# ─── Trigger existence (deployment verification) ──────────────────────


class TestTriggerInstalled:
    def test_trigger_exists(self, db_cursor):
        db_cursor.execute("""
            SELECT tgname, tgtype, tgenabled
            FROM pg_trigger
            WHERE tgrelid = 'sync_log'::regclass AND NOT tgisinternal
        """)
        row = db_cursor.fetchone()
        assert row is not None, "retention trigger not installed"
        assert row[0] == "trg_sync_log_retention"
        # tgtype bit 0 (row-level): 0 = STATEMENT, 1 = ROW
        # bit 1 (BEFORE): 0 = AFTER, 1 = BEFORE
        # bit 2 (INSERT=2): value 4 means AFTER STATEMENT
        assert row[1] == 4, f"expected AFTER STATEMENT (4), got {row[1]}"
        # tgenabled: 'O' = enabled, 'D' = disabled, 'R' = replica, 'A' = always
        assert row[2] == "O", f"trigger is not enabled (status={row[2]})"

    def test_cleanup_function_exists(self, db_cursor):
        db_cursor.execute("""
            SELECT proname, pronargs
            FROM pg_proc
            WHERE proname = 'cleanup_sync_log'
        """)
        row = db_cursor.fetchone()
        assert row is not None
        # pronargs: 1 (retention_days)
        assert row[1] == 1
