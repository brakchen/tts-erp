"""GET /v2/sync/status contract tests (readonly; integration.sync_jobs reads).

红灯规则（2026-09-28 用户需求）：最近一次同步时间落后 ≥2 个周期 →
severity="crit"。

共享 DB 注意事项：测试库可能被真实 sync worker 写入真实 job 的
sync_jobs 行，所以集成断言只针对 TEST_-prefixed 的额外 job 行
（周期未知 → severity="unknown"），红灯/黄灯逻辑用 _build_status
纯函数单测覆盖，不依赖共享库时序。
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import delete
from sqlalchemy.orm import Session

from tts_erp_v2.api.v2.sync_status import _build_status
from tts_erp_v2.db.models.integration import SyncJob
from tts_erp_v2.sync_worker.scheduler import JOBS

TEST_JOB = "TEST_sync_status_probe"

pytestmark = [pytest.mark.domain_api, pytest.mark.layer_integration]


@pytest.fixture()
def seeded_probe_job(db_engine) -> Iterator[None]:
    """Insert a TEST_-prefixed sync_jobs row with a REAL commit so the app
    under test (own connections) can see it; wipe it afterwards."""
    now = datetime.now(UTC)
    with Session(db_engine) as sess:
        sess.execute(delete(SyncJob).where(SyncJob.job_name == TEST_JOB))
        sess.add(
            SyncJob(
                job_name=TEST_JOB,
                status="succeeded",
                started_at=now - timedelta(minutes=5),
                finished_at=now - timedelta(minutes=4),
                rows_total=3,
                rows_inserted=3,
            )
        )
        sess.commit()
    yield
    with Session(db_engine) as sess:
        sess.execute(delete(SyncJob).where(SyncJob.job_name == TEST_JOB))
        sess.commit()


# ---------- 纯函数：红灯/黄灯判定 ----------


def _row(started_at: datetime, status: str = "succeeded") -> SyncJob:
    return SyncJob(
        job_name="probe",
        status=status,
        started_at=started_at,
        finished_at=started_at + timedelta(seconds=10),
    )


def test_build_status_ok_within_one_cycle() -> None:
    now = datetime.now(UTC)
    out = _build_status("j", 600, _row(now - timedelta(minutes=5)), now)
    assert out.severity == "ok"
    assert out.cycles_late == pytest.approx(0.5)
    assert out.next_expected_at == now - timedelta(minutes=5) + timedelta(seconds=600)
    assert out.lag_seconds == pytest.approx(300.0)


def test_build_status_warn_past_one_cycle() -> None:
    now = datetime.now(UTC)
    out = _build_status("j", 600, _row(now - timedelta(minutes=15)), now)
    assert out.severity == "warn"
    assert out.cycles_late == pytest.approx(1.5)


def test_build_status_crit_past_two_cycles() -> None:
    """用户需求核心：落后 ≥2 个周期标红。"""
    now = datetime.now(UTC)
    out = _build_status("j", 600, _row(now - timedelta(minutes=25)), now)
    assert out.severity == "crit"
    assert out.cycles_late == pytest.approx(2.5)
    assert out.last_status == "succeeded"


def test_build_status_crit_boundary_exactly_two_cycles() -> None:
    now = datetime.now(UTC)
    out = _build_status("j", 600, _row(now - timedelta(minutes=20)), now)
    assert out.severity == "crit"


def test_build_status_unknown_never_ran() -> None:
    now = datetime.now(UTC)
    out = _build_status("j", 600, None, now)
    assert out.severity == "unknown"
    assert out.last_run_at is None
    assert out.next_expected_at is None
    assert out.lag_seconds is None


def test_build_status_unknown_no_interval() -> None:
    """注册表外的 job（历史残留）无周期 → 不参与红灯判定。"""
    now = datetime.now(UTC)
    out = _build_status("j", None, _row(now - timedelta(days=7)), now)
    assert out.severity == "unknown"
    assert out.interval_seconds is None
    assert out.next_expected_at is None
    assert out.last_run_at is not None


def test_build_status_naive_timestamp_treated_as_utc() -> None:
    """sync_jobs 列是 TIMESTAMP WITHOUT TIME ZONE（UTC 语义）；
    naive 值必须按 UTC 解释，否则 lag 会错一个时区。"""
    now = datetime.now(UTC)
    naive = (now - timedelta(minutes=5)).replace(tzinfo=None)
    out = _build_status("j", 600, _row(naive), now)
    assert out.severity == "ok"
    assert out.cycles_late == pytest.approx(0.5, abs=0.05)


# ---------- API 契约 ----------


def test_status_requires_auth(api_client) -> None:
    r = api_client.get("/v2/sync/status")
    assert r.status_code == 401, r.text


def test_status_readonly_key_returns_registry(
    api_client, readonly_key, db_engine
) -> None:
    r = api_client.get(
        "/v2/sync/status",
        headers={"Authorization": f"Bearer {readonly_key}"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["server_time"]
    by_name = {j["job_name"]: j for j in body["jobs"]}
    # 注册表里的每个周期作业都必须出现（即使从未运行）。
    for name, spec in JOBS.items():
        assert name in by_name, f"missing registry job {name}"
        entry = by_name[name]
        assert entry["interval_seconds"] == spec.interval_seconds
        assert entry["severity"] in {"ok", "warn", "crit", "unknown"}


def test_status_shows_seeded_extra_job_as_unknown(
    api_client, readonly_key, db_engine, seeded_probe_job
) -> None:
    """注册表外的 sync_jobs 行也要展示（周期未知，severity=unknown）。"""
    r = api_client.get(
        "/v2/sync/status",
        headers={"Authorization": f"Bearer {readonly_key}"},
    )
    assert r.status_code == 200, r.text
    by_name = {j["job_name"]: j for j in r.json()["jobs"]}
    probe = by_name[TEST_JOB]
    assert probe["interval_seconds"] is None
    assert probe["severity"] == "unknown"
    assert probe["last_status"] == "succeeded"
    # sync_jobs 列是 naive-UTC；响应必须带时区标记（+00:00 或 Z），
    # 浏览器才不会按本地时间错解。
    assert probe["last_run_at"].endswith(("+00:00", "Z"))
    assert probe["last_finished_at"].endswith(("+00:00", "Z"))


def test_status_latest_row_wins_per_job(
    api_client, readonly_key, db_engine, seeded_probe_job
) -> None:
    """同一 job_name 多行（tiktok 按 shop 扇出）只保留最新一次。"""
    now = datetime.now(UTC)
    with Session(db_engine) as sess:
        sess.add(
            SyncJob(
                job_name=TEST_JOB,
                status="failed",
                started_at=now - timedelta(hours=2),
                finished_at=now - timedelta(hours=2),
                error_message="TEST_ stale failure",
            )
        )
        sess.commit()
    r = api_client.get(
        "/v2/sync/status",
        headers={"Authorization": f"Bearer {readonly_key}"},
    )
    assert r.status_code == 200, r.text
    entries = [j for j in r.json()["jobs"] if j["job_name"] == TEST_JOB]
    assert len(entries) == 1
    # fixture 种的是 5 分钟前的 succeeded；2 小时前的 failed 不应覆盖它。
    assert entries[0]["last_status"] == "succeeded"
