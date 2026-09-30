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


def test_make_executor_skips_disabled_tiktok_job(monkeypatch: pytest.MonkeyPatch) -> None:
    spec = JobSpec(
        job_name="tiktok.orders",
        module_path="tts_erp_v2.jobs.tiktok.orders",
        interval_seconds=600,
        is_tiktok=True,
    )
    factory = MagicMock()
    monkeypatch.setattr(scheduler, "_is_job_enabled_in_factory", lambda _sf, _j: False)
    run_calls: list[str] = []
    monkeypatch.setattr(
        scheduler,
        "_run_tiktok_job",
        lambda _spec, _sf: run_calls.append(_spec.job_name),
    )

    _make_executor(spec, factory)()
    assert run_calls == []


def test_make_executor_skips_disabled_system_job(monkeypatch: pytest.MonkeyPatch) -> None:
    spec = JobSpec(
        job_name="reporting.profit_daily",
        module_path="tts_erp_v2.jobs.reporting",
        interval_seconds=3600,
        is_tiktok=False,
        entrypoint="run_profit_daily",
    )
    factory = MagicMock()
    monkeypatch.setattr(scheduler, "_is_job_enabled_in_factory", lambda _sf, _j: False)
    run_calls: list[str] = []
    monkeypatch.setattr(
        scheduler,
        "_run_system_job",
        lambda _spec, _sf: run_calls.append(_spec.job_name),
    )

    _make_executor(spec, factory)()
    assert run_calls == []


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
