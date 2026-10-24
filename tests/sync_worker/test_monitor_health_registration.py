"""Monitor intake and scanner must be reachable through canonical registries."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from tts_erp_v2.db import models
from tts_erp_v2.sync_worker import scheduler

pytestmark = [pytest.mark.domain_sync, pytest.mark.layer_unit]
JOB = "plugin.monitor_health"
MODULE = "tts_erp_v2.plugin.monitor_health.job"


def test_monitor_model_is_registered_and_exported():
    assert "MonitorHealth" in models.__all__
    assert models.MonitorHealth.__table__.fullname == "plugin.monitor_health"


def test_health_job_is_a_fifteen_second_system_job():
    assert JOB in scheduler.JOBS
    spec = scheduler.JOBS[JOB]
    assert spec.job_name == JOB
    assert spec.module_path == MODULE
    assert spec.interval_seconds == 15
    assert spec.is_tiktok is False
    assert spec.entrypoint == "run"
    assert spec.needs_token_registry is False


def test_health_trigger_has_no_random_delay_but_preserves_other_jobs():
    sched = scheduler.build_scheduler(session_factory=MagicMock(), jitter_seconds=42)
    jobs = {job.id: job for job in sched.get_jobs()}
    assert JOB in jobs
    assert jobs[JOB].trigger.interval.total_seconds() == 15
    assert jobs[JOB].trigger.jitter == 0
    for name, job in jobs.items():
        if name != JOB:
            assert job.trigger.jitter == 42


def test_system_dispatch_commits_partial_delivery_acknowledgements(monkeypatch):
    assert JOB in scheduler.JOBS
    session = MagicMock()
    factory = MagicMock(return_value=session)
    entrypoint = MagicMock(
        return_value={"sent": 1, "pending": 1, "degraded": True, "deliveryError": True}
    )
    importer = MagicMock(return_value=SimpleNamespace(run=entrypoint))
    monkeypatch.setattr(scheduler.importlib, "import_module", importer)
    scheduler._run_system_job(scheduler.JOBS[JOB], factory)
    importer.assert_called_once_with(MODULE)
    entrypoint.assert_called_once_with(session)
    session.commit.assert_called_once_with()
    session.rollback.assert_not_called()
    session.close.assert_called_once_with()
