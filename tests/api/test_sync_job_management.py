"""Scheduler management API/page tests."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import delete
from sqlalchemy.orm import Session

from tts_erp_v2.api.v2 import admin as admin_module
from tts_erp_v2.db.models.integration import SyncCursor, SyncJob
from tts_erp_v2.sync_worker.scheduler import (
    CONTROL_DISABLED,
    JOBS,
    SCHEDULER_CONTROL_JOB,
)

pytestmark = [pytest.mark.domain_api, pytest.mark.layer_integration]

TEST_JOB = "tiktok.orders"


@pytest.fixture()
def clean_job_control(db_engine) -> Iterator[None]:
    with Session(db_engine) as sess:
        sess.execute(
            delete(SyncCursor).where(
                SyncCursor.job_name == SCHEDULER_CONTROL_JOB,
                SyncCursor.scope == TEST_JOB,
            )
        )
        sess.commit()
    yield
    with Session(db_engine) as sess:
        sess.execute(
            delete(SyncCursor).where(
                SyncCursor.job_name == SCHEDULER_CONTROL_JOB,
                SyncCursor.scope == TEST_JOB,
            )
        )
        sess.commit()


def test_sync_jobs_requires_auth(api_client) -> None:
    r = api_client.get("/v2/sync/jobs")
    assert r.status_code == 401, r.text


def test_sync_jobs_readonly_returns_definitions(api_client, readonly_key) -> None:
    r = api_client.get(
        "/v2/sync/jobs",
        headers={"Authorization": f"Bearer {readonly_key}"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    by_name = {j["job_name"]: j for j in body["jobs"]}
    assert set(JOBS).issubset(by_name)
    assert by_name["tiktok.orders"]["scope_type"] == "tiktok_shop"
    assert by_name["reporting.profit_daily"]["scope_type"] == "system"
    assert isinstance(body["tiktok_shops"], list)


def test_sync_jobs_reflects_disabled_control(
    api_client, readonly_key, db_engine, clean_job_control
) -> None:
    with Session(db_engine) as sess:
        sess.add(
            SyncCursor(
                job_name=SCHEDULER_CONTROL_JOB,
                scope=TEST_JOB,
                cursor_value=CONTROL_DISABLED,
            )
        )
        sess.commit()

    r = api_client.get(
        "/v2/sync/jobs",
        headers={"Authorization": f"Bearer {readonly_key}"},
    )
    assert r.status_code == 200, r.text
    by_name = {j["job_name"]: j for j in r.json()["jobs"]}
    assert by_name[TEST_JOB]["enabled"] is False


def test_admin_can_toggle_job(api_client, admin_key, db_engine, clean_job_control) -> None:
    r = api_client.patch(
        f"/v2/admin/sync-jobs/{TEST_JOB}/enabled",
        headers={"Authorization": f"Bearer {admin_key}", "X-Requested-With": "tts-erp"},
        json={"enabled": False},
    )
    assert r.status_code == 200, r.text
    assert r.json()["enabled"] is False

    with Session(db_engine) as sess:
        value = sess.query(SyncCursor.cursor_value).filter_by(
            job_name=SCHEDULER_CONTROL_JOB,
            scope=TEST_JOB,
        ).scalar()
    assert value == CONTROL_DISABLED


def test_readwrite_cannot_toggle_job(api_client, readwrite_key) -> None:
    r = api_client.patch(
        f"/v2/admin/sync-jobs/{TEST_JOB}/enabled",
        headers={"Authorization": f"Bearer {readwrite_key}", "X-Requested-With": "tts-erp"},
        json={"enabled": False},
    )
    assert r.status_code == 403, r.text


def test_trigger_queues_background_task(
    api_client, admin_key, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[tuple[str, str | None]] = []

    def fake_run(job_name: str, *, shop_id: str | None = None) -> None:
        calls.append((job_name, shop_id))

    monkeypatch.setattr(admin_module, "run_scheduled_job_once", fake_run)
    r = api_client.post(
        "/v2/admin/sync-jobs/reporting.profit_daily/trigger",
        headers={"Authorization": f"Bearer {admin_key}", "X-Requested-With": "tts-erp"},
        json={},
    )
    assert r.status_code == 200, r.text
    assert r.json()["accepted"] is True
    assert calls == [("reporting.profit_daily", None)]


def test_trigger_rejects_shop_for_system_job(api_client, admin_key) -> None:
    r = api_client.post(
        "/v2/admin/sync-jobs/reporting.profit_daily/trigger",
        headers={"Authorization": f"Bearer {admin_key}", "X-Requested-With": "tts-erp"},
        json={"shop_id": "123"},
    )
    assert r.status_code == 422, r.text


def test_sync_jobs_page_html(api_client, readonly_key) -> None:
    r = api_client.get(
        "/v2/sync/jobs/page",
        headers={"Authorization": f"Bearer {readonly_key}"},
    )
    assert r.status_code == 200, r.text
    assert "定时任务管理" in r.text
    assert "sync-jobs.js" in r.text


def test_sync_jobs_includes_latest_status(api_client, readonly_key, db_engine) -> None:
    job_name = "TEST_sync_jobs_page_status"
    now = datetime.now(UTC)
    with Session(db_engine) as sess:
        sess.execute(delete(SyncJob).where(SyncJob.job_name == job_name))
        sess.add(
            SyncJob(
                job_name=job_name,
                status="succeeded",
                started_at=now - timedelta(minutes=1),
                finished_at=now,
            )
        )
        sess.commit()
    try:
        r = api_client.get(
            "/v2/sync/status",
            headers={"Authorization": f"Bearer {readonly_key}"},
        )
        assert r.status_code == 200, r.text
        by_name = {j["job_name"]: j for j in r.json()["jobs"]}
        assert by_name[job_name]["last_status"] == "succeeded"
    finally:
        with Session(db_engine) as sess:
            sess.execute(delete(SyncJob).where(SyncJob.job_name == job_name))
            sess.commit()
