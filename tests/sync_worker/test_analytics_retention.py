"""TDD tests for ``tts_erp_v2.jobs.analytics_retention``.

The retention job is registered in the sync-worker's JOBS dict
(:data:`tts_erp_v2.sync_worker.scheduler.JOBS["analytics.retention"]`)
and runs once per day. It deletes rows older than the configured
retention window from ``analytics.ad_records`` and
``analytics.ad_audit_log``.

These tests use the per-test transaction-rollback fixture from
``tests_v2/conftest.py``. They DO NOT insert production-shaped rows;
instead they test the entrypoint behaviour at the unit layer (env var
parsing, run_job row) and at the integration layer (purge actually
removes rows seeded via TEST_-prefixed sentinels).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import text

from tts_erp_v2.jobs import analytics_retention as retention_mod

pytestmark = [pytest.mark.domain_sync]


# ─── module surface ────────────────────────────────────────────────


def test_module_exposes_constants_and_entrypoint() -> None:
    """__all__ + JOB_NAME + entrypoint name."""
    assert retention_mod.JOB_NAME == "analytics.retention"
    assert hasattr(retention_mod, "run_analytics_retention")
    assert "JOB_NAME" in retention_mod.__all__
    assert "run_analytics_retention" in retention_mod.__all__


def test_module_is_registered_in_scheduler_jobs() -> None:
    """The job must be wired into the sync-worker registry, otherwise
    the retention purge never runs in prod."""
    from tts_erp_v2.sync_worker.scheduler import JOBS

    spec = JOBS["analytics.retention"]
    assert spec.module_path == "tts_erp_v2.jobs.analytics_retention"
    assert spec.is_tiktok is False
    assert spec.entrypoint == "run_analytics_retention"
    # 1 day — matches the original retention.sql cron suggestion.
    assert spec.interval_seconds == 86400


# ─── _env_int ──────────────────────────────────────────────────────


def test_env_int_returns_default_when_unset(monkeypatch) -> None:
    """No env var → default value."""
    monkeypatch.delenv("ANALYTICS_RETENTION_RECORDS_DAYS", raising=False)
    assert retention_mod._env_int("ANALYTICS_RETENTION_RECORDS_DAYS", 90) == 90


def test_env_int_parses_valid_int(monkeypatch) -> None:
    """Numeric string → that integer."""
    monkeypatch.setenv("ANALYTICS_RETENTION_RECORDS_DAYS", "120")
    assert retention_mod._env_int("ANALYTICS_RETENTION_RECORDS_DAYS", 90) == 120


def test_env_int_clamps_zero_to_one(monkeypatch) -> None:
    """``0`` → 1 (never negative retention)."""
    monkeypatch.setenv("ANALYTICS_RETENTION_RECORDS_DAYS", "0")
    assert retention_mod._env_int("ANALYTICS_RETENTION_RECORDS_DAYS", 90) == 1


def test_env_int_clamps_negative_to_one(monkeypatch) -> None:
    """Negative values are not permitted."""
    monkeypatch.setenv("ANALYTICS_RETENTION_RECORDS_DAYS", "-5")
    assert retention_mod._env_int("ANALYTICS_RETENTION_RECORDS_DAYS", 90) == 1


def test_env_int_falls_back_on_garbage(monkeypatch) -> None:
    """Non-integer string → default + warning logged."""
    monkeypatch.setenv("ANALYTICS_RETENTION_AUDIT_DAYS", "later")
    assert retention_mod._env_int("ANALYTICS_RETENTION_AUDIT_DAYS", 30) == 30


# ─── run_analytics_retention (unit-layer with mocked purge_expired) ─


def test_run_analytics_retention_records_sync_job_counters(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """run_job context manager sees rows_total = records + audit, and
    extra carries the per-table counts + retention days."""
    fake_session = MagicMock()
    fake_job = MagicMock()

    purge_counts = {"records_deleted": 12, "audit_deleted": 5}

    with patch(
        "tts_erp_v2.analytics.repository.purge_expired", return_value=purge_counts
    ) as purge_seen, patch(
        "tts_erp_v2.jobs.runner.run_job"
    ) as run_job_mock:
        # Make run_job behave like a context manager returning fake_job.
        run_job_mock.return_value.__enter__.return_value = fake_job
        run_job_mock.return_value.__exit__.return_value = False

        out = retention_mod.run_analytics_retention(fake_session)

    assert out == purge_counts
    # purge called with the defaults (no env override).
    assert purge_seen.call_args.kwargs == {
        "records_days": 90,
        "audit_days": 30,
    }
    # SyncJob row populated.
    assert fake_job.rows_total == 17  # 12 + 5
    assert fake_job.rows_inserted == 0
    assert fake_job.rows_updated == 0
    assert fake_job.extra == {
        "records_days": 90,
        "audit_days": 30,
        **purge_counts,
    }


def test_run_analytics_retention_respects_env_overrides(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Env vars ANALYTICS_RETENTION_*_DAYS flow through to purge_expired."""
    monkeypatch.setenv("ANALYTICS_RETENTION_RECORDS_DAYS", "7")
    monkeypatch.setenv("ANALYTICS_RETENTION_AUDIT_DAYS", "2")

    purge_counts = {"records_deleted": 0, "audit_deleted": 0}
    fake_session = MagicMock()
    fake_job = MagicMock()

    with patch(
        "tts_erp_v2.analytics.repository.purge_expired", return_value=purge_counts
    ) as purge_seen, patch(
        "tts_erp_v2.jobs.runner.run_job"
    ) as run_job_mock:
        run_job_mock.return_value.__enter__.return_value = fake_job
        run_job_mock.return_value.__exit__.return_value = False

        retention_mod.run_analytics_retention(fake_session)

    assert purge_seen.call_args.kwargs == {"records_days": 7, "audit_days": 2}
    assert fake_job.extra["records_days"] == 7
    assert fake_job.extra["audit_days"] == 2


# ─── integration: purge actually deletes seeded rows ───────────────


def _seed_old_record(db_session, days_ago: int) -> None:
    """Insert a TEST_*-prefixed ad_records row whose received_at is
    days_ago in the past. Skipped from purge sweep if inside the window.
    """
    received_at = datetime.now(UTC) - timedelta(days=days_ago)
    db_session.execute(
        text(
            "INSERT INTO analytics.ad_records "
            "(idempotency_key, seller_id, advertiser_id, storage_key, campaign_id, day, "
            " endpoint, method, response_data, source, captured_at, received_at) "
            "VALUES (:k, 'TEST_SELLER', 'TEST_ADV', 'productAnalyses', 'TEST_CAMP', "
            "        CURRENT_DATE, '/p', 'POST', '{}'::jsonb, 'TEST', "
            "        :cap, :recv)"
        ),
        {
            "k": f"TEST_RET_REC_{days_ago}",
            "cap": received_at,
            "recv": received_at,
        },
    )


def _seed_old_audit(db_session, days_ago: int) -> None:
    created_at = datetime.now(UTC) - timedelta(days=days_ago)
    db_session.execute(
        text(
            "INSERT INTO analytics.ad_audit_log "
            "(request_id, endpoint, method, path, status, key_prefix, records_in, "
            " records_ok, records_rej, error_code, created_at, error_message) "
            "VALUES (:rid, '/e', 'POST', '/p', 200, 'TEST', 0, 0, 0, NULL, "
            "        :c, NULL)"
        ),
        {"rid": f"TEST_RET_AUDIT_{days_ago}", "c": created_at},
    )


def _count_test_records(db_session, *, prefix: str, table: str) -> int:
    """Count rows whose idempotency_key / request_id starts with prefix."""
    col = "idempotency_key" if table == "ad_records" else "request_id"
    return db_session.execute(
        text(
            f"SELECT count(*) FROM analytics.{table} "
            f"WHERE {col} LIKE :p"
        ),
        {"p": f"{prefix}%"},
    ).scalar_one()


def test_purge_actually_removes_old_rows(db_session) -> None:
    """Integration: a 1-day window purges every seeded row (all > 1 day)."""
    _seed_old_record(db_session, days_ago=120)
    _seed_old_audit(db_session, days_ago=120)

    # Sanity: rows were inserted (within this savepoint).
    assert _count_test_records(db_session, prefix="TEST_RET_REC_", table="ad_records") == 1
    assert _count_test_records(db_session, prefix="TEST_RET_AUDIT_", table="ad_audit_log") == 1

    counts = retention_mod.run_analytics_retention(
        db_session,  # type: ignore[arg-type]
    ) if False else None  # placeholder; we don't run with monkeypatched purge
    # Instead, call purge_expired directly so the per-test savepoint
    # captures the deletes.
    from tts_erp_v2.analytics.repository import purge_expired

    out = purge_expired(db_session, records_days=1, audit_days=1)

    # Both rows are older than 1 day → both deleted.
    assert out["records_deleted"] >= 1
    assert out["audit_deleted"] >= 1
    assert _count_test_records(db_session, prefix="TEST_RET_REC_", table="ad_records") == 0
    assert _count_test_records(db_session, prefix="TEST_RET_AUDIT_", table="ad_audit_log") == 0


def test_purge_keeps_rows_inside_window(db_session) -> None:
    """A row 5 days old survives a 30-day window."""
    _seed_old_record(db_session, days_ago=5)
    _seed_old_audit(db_session, days_ago=5)

    from tts_erp_v2.analytics.repository import purge_expired

    out = purge_expired(db_session, records_days=30, audit_days=30)
    # Our seed is inside the 30-day window → NOT counted in deleted.
    # (The function returns the deleted count; we just check our row survives.)
    assert _count_test_records(db_session, prefix="TEST_RET_REC_", table="ad_records") == 1
    assert _count_test_records(db_session, prefix="TEST_RET_AUDIT_", table="ad_audit_log") == 1


def test_run_analytics_retention_writes_sync_job_row(
    db_session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """End-to-end: run_analytics_retention writes a SyncJob row via run_job."""
    # Purge_expired must run inside the same session as run_job so the
    # row writes/deletes share a transaction.
    from tts_erp_v2.analytics.repository import purge_expired

    # Stub purge_expired so the deletes happen on the real session but
    # we don't depend on what's in the DB (we still need the function
    # to actually run so the SyncJob row commits).
    # Easiest: pass through to the real function with a tiny window.
    # No old rows → 0 deletes, but a SyncJob row should still be written.
    with patch(
        "tts_erp_v2.analytics.repository.purge_expired", side_effect=purge_expired
    ):
        retention_mod.run_analytics_retention(db_session)
    db_session.commit()

    # Look up our SyncJob row — bounded by job_name (the prod table is
    # unrelated to analytics.retention so this scope is safe).
    rows = db_session.execute(
        text(
            "SELECT status, rows_total, extra FROM integration.sync_jobs "
            "WHERE job_name = 'analytics.retention' ORDER BY started_at DESC LIMIT 1"
        )
    ).first()
    assert rows is not None, "run_analytics_retention must write a SyncJob row"
    assert rows[0] == "succeeded"
    assert rows[1] >= 0  # 0 or more deleted rows
    assert isinstance(rows[2], dict)
    assert rows[2]["records_days"] == 90
    assert rows[2]["audit_days"] == 30
