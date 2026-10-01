"""Scheduler management API/page tests."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

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


def test_sync_jobs_requires_readwrite(api_client, readonly_key) -> None:
    r = api_client.get(
        "/v2/sync/jobs",
        headers={"Authorization": f"Bearer {readonly_key}"},
    )
    assert r.status_code == 403, r.text


def test_sync_jobs_readwrite_returns_definitions(api_client, readwrite_key) -> None:
    r = api_client.get(
        "/v2/sync/jobs",
        headers={"Authorization": f"Bearer {readwrite_key}"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    by_name = {j["job_name"]: j for j in body["jobs"]}
    assert set(JOBS).issubset(by_name)
    assert by_name["tiktok.orders"]["scope_type"] == "tiktok_shop"
    assert by_name["reporting.profit_daily"]["scope_type"] == "system"
    assert isinstance(body["tiktok_shops"], list)


def test_sync_jobs_reflects_disabled_control(
    api_client, readwrite_key, db_engine, clean_job_control
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
        headers={"Authorization": f"Bearer {readwrite_key}"},
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


def test_readonly_cannot_trigger_job(api_client, readonly_key) -> None:
    r = api_client.post(
        "/v2/admin/sync-jobs/reporting.profit_daily/trigger",
        headers={"Authorization": f"Bearer {readonly_key}", "X-Requested-With": "tts-erp"},
        json={},
    )
    assert r.status_code == 403, r.text


def test_readwrite_can_trigger_queues_background_task(
    api_client, readwrite_key, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[tuple[str, str | None]] = []

    def fake_run(job_name: str, *, shop_id: str | None = None) -> None:
        calls.append((job_name, shop_id))

    monkeypatch.setattr(admin_module, "run_scheduled_job_once", fake_run)
    r = api_client.post(
        "/v2/admin/sync-jobs/reporting.profit_daily/trigger",
        headers={"Authorization": f"Bearer {readwrite_key}", "X-Requested-With": "tts-erp"},
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


def test_legacy_sync_jobs_page_is_removed(api_client, readonly_key) -> None:
    r = api_client.get(
        "/v2/sync/jobs/page",
        headers={"Authorization": f"Bearer {readonly_key}"},
    )
    assert r.status_code == 404, r.text


def test_sync_jobs_js_uses_prefix_aware_api_paths() -> None:
    src = (
        Path(__file__).resolve().parents[2]
        / "tts_erp_v2"
        / "static"
        / "js"
        / "sync-jobs.js"
    ).read_text(encoding="utf-8")

    assert "const API = `${rootPrefix}/v2`;" in src
    assert "api('/sync/jobs')" in src
    assert "api('/auth/me')" in src
    assert "CSS.escape" not in src
    assert "state.canAdmin" in src
    assert "state.canTrigger" in src
    assert "disabled" in src
    assert "window.confirm" in src
    assert "window.alert" in src
    assert "data-label=\"任务\"" in src
    assert "is-busy" in src


def test_sync_jobs_css_has_mobile_card_layout_and_disabled_cursor() -> None:
    src = (
        Path(__file__).resolve().parents[2]
        / "tts_erp_v2"
        / "static"
        / "css"
        / "sync-jobs.css"
    ).read_text(encoding="utf-8")

    assert "button:disabled" in src
    assert "cursor: not-allowed" in src
    assert "button.is-busy:disabled" in src
    assert ".jobs-table thead { display: none; }" in src
    assert "content: attr(data-label)" in src
    assert ".scope-select { width: 100%;" in src


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
