"""Scheduler operator control tests."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from tts_erp_v2.db.models.integration import SyncCursor
from tts_erp_v2.sync_worker import scheduler
from tts_erp_v2.sync_worker.scheduler import (
    CONTROL_DISABLED,
    CONTROL_ENABLED,
    SCHEDULER_CONTROL_JOB,
    JobSpec,
    _make_executor,
    get_job_control_values,
    is_job_enabled,
    set_job_enabled,
)

pytestmark = [pytest.mark.domain_sync]


def test_set_job_enabled_persists_control(db_session) -> None:
    set_job_enabled(db_session, "tiktok.orders", False)
    assert is_job_enabled(db_session, "tiktok.orders") is False
    controls = get_job_control_values(db_session)
    assert controls["tiktok.orders"] == CONTROL_DISABLED

    set_job_enabled(db_session, "tiktok.orders", True)
    assert is_job_enabled(db_session, "tiktok.orders") is True
    controls = get_job_control_values(db_session)
    assert controls["tiktok.orders"] == CONTROL_ENABLED


def test_is_job_enabled_defaults_true_without_control(db_session) -> None:
    db_session.query(SyncCursor).filter_by(
        job_name=SCHEDULER_CONTROL_JOB,
        scope="tiktok.logistics",
    ).delete()
    assert is_job_enabled(db_session, "tiktok.logistics") is True


def test_set_job_enabled_rejects_unknown_job(db_session) -> None:
    with pytest.raises(KeyError):
        set_job_enabled(db_session, "unknown.job", False)


def test_make_executor_skips_disabled_tiktok_job(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    spec = JobSpec(
        job_name="tiktok.orders",
        module_path="tts_erp_v2.jobs.tiktok.orders",
        interval_seconds=600,
        is_tiktok=True,
    )
    factory = MagicMock()
    monkeypatch.setattr(scheduler, "_is_job_enabled_in_factory", lambda _sf, _j: False)
    spawned: list[str] = []
    monkeypatch.setattr(
        scheduler,
        "_run_scheduled_job_child",
        lambda name: spawned.append(name),
    )

    _make_executor(spec, factory)()
    assert spawned == []


def test_make_executor_skips_disabled_system_job(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    spec = JobSpec(
        job_name="reporting.profit_daily",
        module_path="tts_erp_v2.jobs.reporting",
        interval_seconds=3600,
        is_tiktok=False,
        entrypoint="run_profit_daily",
    )
    factory = MagicMock()
    monkeypatch.setattr(scheduler, "_is_job_enabled_in_factory", lambda _sf, _j: False)
    spawned: list[str] = []
    monkeypatch.setattr(
        scheduler,
        "_run_scheduled_job_child",
        lambda name: spawned.append(name),
    )

    _make_executor(spec, factory)()
    assert spawned == []


def test_manual_run_ignores_disabled_control(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, str | None]] = []
    monkeypatch.setattr(
        "tts_erp_v2.db.base.get_session_factory",
        lambda: MagicMock(),
    )
    monkeypatch.setattr(
        scheduler,
        "_run_system_job",
        lambda spec, _sf: calls.append((spec.job_name, None)),
    )

    scheduler.run_scheduled_job_once("reporting.profit_daily")
    assert calls == [("reporting.profit_daily", None)]


# ─── A killed child must not leave its sync_jobs row 'running' forever ───
#
# 2026-11-10: the OOM killer SIGKILLed the one-shot child, so
# jobs.runner.run_job's failure branch never ran and the row stayed
# status='running' — the job looked stuck, was never reported as failed and
# never alerted. The parent knows the child died abnormally, so it writes the
# verdict the child could not.


def test_close_orphaned_running_row_marks_the_stuck_row_failed(db_engine) -> None:
    """数量断言：恰好一行 running 被结成 failed，并带上退出码。"""
    from sqlalchemy.orm import sessionmaker

    from tts_erp_v2.db.models.integration import SyncJob

    factory = sessionmaker(bind=db_engine, join_transaction_mode="create_savepoint")
    with factory() as session, session.begin():
        row = SyncJob(job_name="TEST_tiktok.finance", status="running")
        session.add(row)
        session.flush()
        row_id = row.id

    scheduler._close_orphaned_running_row("TEST_tiktok.finance", -9, factory)

    with factory() as session:
        closed = session.get(SyncJob, row_id)
        assert closed is not None
        assert closed.status == "failed"
        assert closed.finished_at is not None
        assert "-9" in (closed.error_message or "")


def test_close_orphaned_running_row_leaves_finished_rows_alone(db_engine) -> None:
    """数量断言：已结束的行走不到，只有一行 running 会被改。"""
    from sqlalchemy.orm import sessionmaker

    from tts_erp_v2.db.models.integration import SyncJob

    factory = sessionmaker(bind=db_engine, join_transaction_mode="create_savepoint")
    with factory() as session, session.begin():
        succeeded = SyncJob(job_name="TEST_tiktok.finance", status="succeeded")
        running = SyncJob(job_name="TEST_tiktok.finance", status="running")
        session.add_all([succeeded, running])
        session.flush()
        succeeded_id = succeeded.id
        running_id = running.id

    scheduler._close_orphaned_running_row("TEST_tiktok.finance", -9, factory)

    with factory() as session:
        assert session.get(SyncJob, succeeded_id).status == "succeeded"
        assert session.get(SyncJob, running_id).status == "failed"
