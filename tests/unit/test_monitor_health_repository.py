from __future__ import annotations

import importlib.util
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Event
from typing import cast
from uuid import uuid4

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import delete, inspect, select
from sqlalchemy.orm import Session

pytestmark = pytest.mark.layer_integration
BASE = datetime(2026, 1, 1, tzinfo=UTC)
MIGRATION = Path("alembic/versions/0070_monitor_health.py")


@pytest.fixture
def health_schema(db_engine):
    assert MIGRATION.exists(), "additive current-health migration is required"
    spec = importlib.util.spec_from_file_location("monitor_health_migration", MIGRATION)
    assert spec and spec.loader
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    assert migration.down_revision == "0069_video_publish_confirmation"
    with db_engine.begin() as connection:
        if not inspect(connection).has_table("monitor_health", schema="plugin"):
            with Operations.context(MigrationContext.configure(connection)):
                migration.upgrade()
    return db_engine


@pytest.fixture
def shops(health_schema):
    from tts_erp_v2.db.models.monitor_health import MonitorHealth

    ids = ["TEST_" + uuid4().hex, "TEST_" + uuid4().hex]
    yield ids
    with Session(health_schema) as session:
        session.execute(delete(MonitorHealth).where(MonitorHealth.shop_id.in_(ids)))
        session.commit()


def test_migration_is_one_current_state_table(health_schema):
    columns = {
        column["name"]
        for column in inspect(health_schema).get_columns(
            "monitor_health", schema="plugin"
        )
    }
    assert columns == {"shop_id", "shop_name", "state"}
    assert inspect(health_schema).get_pk_constraint("monitor_health", schema="plugin")[
        "constrained_columns"
    ] == ["shop_id"]


def test_true_shop_id_same_name_and_restart_state(health_schema, shops):
    from tts_erp_v2.db.models.monitor_health import MonitorHealth
    from tts_erp_v2.plugin.monitor_health.repository import ingest

    with Session(health_schema) as session:
        for shop in shops:
            ingest(session, shop, "TEST_same_name", "heartbeat", "running", now=BASE)
        session.commit()
    with Session(health_schema) as session:
        ingest(
            session,
            shops[0],
            "TEST_renamed",
            "heartbeat",
            "stopped",
            now=BASE + timedelta(seconds=1),
        )
        session.commit()
    with Session(health_schema) as session:
        first, second = [session.get(MonitorHealth, shop) for shop in shops]
        assert first and second
        assert first.shop_name == "TEST_renamed" and not first.state["enabled"]
        assert second.shop_name == "TEST_same_name" and second.state["enabled"]


def test_notification_failure_retry_success_and_recovery(
    health_schema, shops, monkeypatch
):
    from tts_erp_v2.plugin.monitor_health import job
    from tts_erp_v2.plugin.monitor_health.repository import ingest

    monkeypatch.setattr(job, "webhook", lambda session: "TEST_url")
    calls = []

    def broken(url, lines):
        calls.append(lines)
        raise RuntimeError("TEST_send_failure")

    monkeypatch.setattr(job, "send", broken)
    with Session(health_schema) as session:
        ingest(session, shops[0], "TEST_shop", "heartbeat", "running", now=BASE)
        session.commit()
        with pytest.raises(RuntimeError, match="TEST_send_failure"):
            job.run(session, now=BASE + timedelta(seconds=180))
        session.rollback()
    monkeypatch.setattr(
        job, "send", lambda url, batches: calls.append(batches) or len(batches)
    )
    for time in (195, 210):
        with Session(health_schema) as session:
            job.run(session, now=BASE + timedelta(seconds=time))
            session.commit()
    assert len(calls) == 2  # one failed attempt, one successful digest
    with Session(health_schema) as session:
        ingest(
            session,
            shops[0],
            "TEST_shop",
            "heartbeat",
            "running",
            now=BASE + timedelta(seconds=211),
        )
        session.commit()
        job.run(session, now=BASE + timedelta(seconds=212))
        session.commit()
        job.run(session, now=BASE + timedelta(seconds=213))
        session.commit()
    assert len(calls) == 3
    assert "heartbeat:recovery" in str(calls[-1])
    assert "scan:recovery" not in str(calls[-1])


def test_parallel_scans_no_duplicate_and_intake_not_overwritten(
    health_schema, shops, monkeypatch
):
    from tts_erp_v2.db.models.monitor_health import MonitorHealth
    from tts_erp_v2.plugin.monitor_health import job
    from tts_erp_v2.plugin.monitor_health.repository import ingest

    monkeypatch.setattr(job, "webhook", lambda session: "TEST_url")
    sending, release, intake_started = Event(), Event(), Event()
    sent = []

    def send(url, lines):
        sent.append(lines)
        sending.set()
        assert release.wait(5)
        return len(lines)

    monkeypatch.setattr(job, "send", send)
    with Session(health_schema) as session:
        ingest(session, shops[0], "TEST_shop", "heartbeat", "running", now=BASE)
        session.commit()

    def scan():
        with Session(health_schema) as session:
            job.run(session, now=BASE + timedelta(seconds=180))
            session.commit()

    def intake():
        with Session(health_schema) as session:
            intake_started.set()
            ingest(
                session,
                shops[0],
                "TEST_new",
                "heartbeat",
                "running",
                now=BASE + timedelta(seconds=181),
            )
            session.commit()

    with ThreadPoolExecutor(max_workers=3) as pool:
        first = pool.submit(scan)
        try:
            assert sending.wait(5)
            pool.submit(scan).result(timeout=5)  # SKIP LOCKED, no duplicate send
            arriving = pool.submit(intake)
            assert intake_started.wait(5)
        finally:
            release.set()
        first.result(timeout=5)
        arriving.result(timeout=5)
    assert len(sent) == 1
    with Session(health_schema) as session:
        row = session.scalars(
            select(MonitorHealth).where(MonitorHealth.shop_id == shops[0])
        ).one()
        assert row.shop_name == "TEST_new"
        assert row.state["heartbeat_at"] == (BASE + timedelta(seconds=181)).timestamp()
        assert not row.state["faults"]["heartbeat"]["active"]
        assert row.state["faults"]["scan"]["notified"]


def test_arrival_order_not_lock_or_commit_order(health_schema, shops):
    from tts_erp_v2.db.models.monitor_health import MonitorHealth
    from tts_erp_v2.plugin.monitor_health.repository import ingest

    with Session(health_schema) as session:
        ingest(
            session,
            shops[0],
            "TEST_new",
            "heartbeat",
            "running",
            now=BASE + timedelta(seconds=20),
        )
        session.commit()
        ingest(
            session,
            shops[0],
            "TEST_old",
            "heartbeat",
            "stopped",
            now=BASE + timedelta(seconds=10),
        )
        session.commit()
        row = session.get(MonitorHealth, shops[0])
        assert row and row.state["enabled"] and row.shop_name == "TEST_new"


def test_200_valid_stopped_shops_all_notified_in_first_scan_before_180(monkeypatch):
    import json
    from time import monotonic
    from types import SimpleNamespace

    import httpx

    from tts_erp_v2.plugin.monitor_health import job, notifications
    from tts_erp_v2.plugin.monitor_health.domain import receive

    rows = [
        SimpleNamespace(
            shop_id=f"TEST_{i:03}_" + "x" * 119,
            shop_name="店" * 40,
            state=receive(None, "heartbeat", "stopped", 0),
        )
        for i in range(200)
    ]
    assert all(len(row.shop_id) == 128 for row in rows)
    monkeypatch.setattr(job, "locked_current", lambda session: rows)
    monkeypatch.setattr(
        job,
        "webhook",
        lambda session: "https://open.feishu.cn/open-apis/bot/v2/hook/TEST_fake",
    )
    sent = []

    def handle(request):
        sent.append(json.loads(request.content)["content"]["text"].splitlines())
        return httpx.Response(200, json={"code": 0})

    monkeypatch.setattr(
        job,
        "send",
        lambda url, batches: notifications.send_many(
            url, batches, transport=httpx.MockTransport(handle)
        ),
    )
    session = cast(Session, SimpleNamespace(flush=lambda: None))
    started = monotonic()
    result = job.run(session, now=datetime.fromtimestamp(165, UTC))
    assert monotonic() - started <= 10
    assert 165 + monotonic() - started <= 180
    assert len(sent) == 4
    assert result["sent"] == 200 and result["pending"] == 0
    assert not result["degraded"] and not result["deadlineAtRisk"]
    assert all(len("\n".join(lines).encode("utf-8")) <= 16 * 1024 for lines in sent)
    assert (
        sum(fault["notified"] for row in rows for fault in row.state["faults"].values())
        == 200
    )
    job.run(session, now=datetime.fromtimestamp(180, UTC))
    assert sum(len(lines) for lines in sent) == 200


def test_below_capacity_keeps_offline_delivery_budget(monkeypatch):
    from types import SimpleNamespace

    from tts_erp_v2.plugin.monitor_health import job
    from tts_erp_v2.plugin.monitor_health.domain import receive

    rows = [
        SimpleNamespace(
            shop_id="TEST_shop",
            shop_name="",
            state=receive(None, "heartbeat", "running", 0),
        )
    ]
    monkeypatch.setattr(job, "locked_current", lambda session: rows)
    monkeypatch.setattr(job, "webhook", lambda session: "TEST_url")
    sent = []
    monkeypatch.setattr(
        job, "send", lambda url, batches: sent.extend(batches) or len(batches)
    )
    result = job.run(
        cast(Session, SimpleNamespace(flush=lambda: None)),
        now=datetime.fromtimestamp(165, UTC),
    )
    assert result["sent"] == 1 and not result["pending"]
    assert not result["degraded"] and not result["deadlineAtRisk"]
    assert len(sent) == 1


def test_actual_transport_timeout_never_acknowledges(monkeypatch):
    import asyncio
    from types import SimpleNamespace

    import httpx

    from tts_erp_v2.plugin.monitor_health import job, notifications
    from tts_erp_v2.plugin.monitor_health.domain import receive

    rows = [
        SimpleNamespace(
            shop_id="TEST_shop",
            shop_name="",
            state=receive(None, "heartbeat", "running", 0),
        )
    ]
    monkeypatch.setattr(job, "locked_current", lambda session: rows)
    url = "https://open.feishu.cn/open-apis/bot/v2/hook/TEST_fake"
    monkeypatch.setattr(job, "webhook", lambda session: url)
    monkeypatch.setattr(notifications, "REQUEST_TIMEOUT_SECONDS", 0.01)

    async def stall(request):
        await asyncio.sleep(1)
        return httpx.Response(200, json={"code": 0})

    monkeypatch.setattr(
        job,
        "send",
        lambda url, batches: notifications.send_many(
            url, batches, transport=httpx.MockTransport(stall)
        ),
    )
    with pytest.raises(RuntimeError, match="monitor health"):
        job.run(
            cast(Session, SimpleNamespace(flush=lambda: None)),
            now=datetime.fromtimestamp(180, UTC),
        )
    assert all(not fault["notified"] for fault in rows[0].state["faults"].values())


def test_partial_chunk_failure_commits_only_accepted_prefix_and_retries_remainder(
    health_schema, shops, monkeypatch, caplog
):
    import json
    import logging

    import httpx

    from tts_erp_v2.db.models.monitor_health import MonitorHealth
    from tts_erp_v2.plugin.monitor_health import job, notifications
    from tts_erp_v2.plugin.monitor_health.repository import ingest

    monkeypatch.setattr(job, "DIGEST_BYTES", 250)
    url = "https://open.feishu.cn/open-apis/bot/v2/hook/TEST_fake"
    monkeypatch.setattr(job, "webhook", lambda session: url)
    calls = []

    def handle(request):
        calls.append(json.loads(request.content)["content"]["text"])
        return httpx.Response(200, json={"code": 1 if len(calls) == 2 else 0})

    monkeypatch.setattr(
        job,
        "send",
        lambda url, batches: notifications.send_many(
            url, batches, transport=httpx.MockTransport(handle)
        ),
    )
    caplog.set_level(logging.ERROR)
    with Session(health_schema) as session:
        for i, shop in enumerate(shops):
            ingest(
                session,
                shop,
                "店" * 40,
                "heartbeat",
                "stopped",
                now=BASE + timedelta(seconds=i),
            )
        session.commit()
        result = job.run(session, now=BASE + timedelta(seconds=165))
        assert result["sent"] == 1 and result["pending"] == 1 and result["degraded"]
        assert result["deadlineAtRisk"]
        assert result["deliveryError"] and shops[0] in calls[0]
        session.commit()  # Existing system runner commits a returned partial result.
    assert "dispatch degraded" in caplog.text and "TEST_fake" not in caplog.text
    with Session(health_schema) as session:
        rows = [session.get(MonitorHealth, shop) for shop in shops]
        assert rows[0] is not None and rows[1] is not None
        assert rows[0].state["faults"]["heartbeat"]["notified"]
        assert not rows[1].state["faults"]["heartbeat"]["notified"]
        result = job.run(session, now=BASE + timedelta(seconds=166))
        session.commit()
        assert result["sent"] == 1 and result["pending"] == 0 and not result["degraded"]
        job.run(session, now=BASE + timedelta(seconds=167))
        session.commit()
    assert len(calls) == 3 and shops[1] in calls[2] and shops[0] not in calls[2]


def test_persisted_recurrence_and_failed_recovery_delivery(
    health_schema, shops, monkeypatch
):
    from tts_erp_v2.plugin.monitor_health import job
    from tts_erp_v2.plugin.monitor_health.repository import ingest

    monkeypatch.setattr(job, "webhook", lambda session: "TEST_url")
    sent = []
    monkeypatch.setattr(
        job, "send", lambda url, batches: sent.extend(batches) or len(batches)
    )

    def report(session, step, status, time):
        ingest(
            session,
            shops[0],
            "TEST_shop",
            step,
            status,
            now=BASE + timedelta(seconds=time),
        )
        session.commit()

    with Session(health_schema) as session:
        report(session, "heartbeat", "running", 0)
        report(session, "heartbeat", "running", 179)
        job.run(session, now=BASE + timedelta(seconds=180))
        session.commit()
        report(session, "scan", "progress", 181)
        report(session, "scan", "failed", 182)

    def fail(url, batches):
        raise RuntimeError("TEST_recovery_send_failure")

    monkeypatch.setattr(job, "send", fail)
    with Session(health_schema) as session:
        with pytest.raises(RuntimeError, match="TEST_recovery"):
            job.run(session, now=BASE + timedelta(seconds=183))
        session.rollback()
    monkeypatch.setattr(
        job, "send", lambda url, batches: sent.extend(batches) or len(batches)
    )
    with Session(health_schema) as session:
        report(session, "heartbeat", "running", 360)
        job.run(session, now=BASE + timedelta(seconds=361))
        session.commit()
        job.run(session, now=BASE + timedelta(seconds=362))
        session.commit()
        report(session, "scan", "progress", 363)
        job.run(session, now=BASE + timedelta(seconds=364))
        session.commit()
    assert len(sent) == 3
    assert ["scan:recovery" in line for line in sent[1]] == [True, False]
    assert "scan:fault" in sent[1][1]
    assert "scan:recovery" in sent[2][0]


@pytest.mark.parametrize("accepted", [-1, 3])
def test_invalid_accepted_prefix_never_acknowledged(monkeypatch, accepted):
    from types import SimpleNamespace

    from tts_erp_v2.plugin.monitor_health import job, notifications
    from tts_erp_v2.plugin.monitor_health.domain import receive

    row = SimpleNamespace(
        shop_id="TEST_shop",
        shop_name="",
        state=receive(None, "heartbeat", "stopped", 0),
    )
    monkeypatch.setattr(job, "locked_current", lambda session: [row])
    monkeypatch.setattr(job, "webhook", lambda session: "TEST_url")

    def invalid(url, batches):
        raise notifications.DeliveryError(accepted)

    monkeypatch.setattr(job, "send", invalid)
    with pytest.raises(RuntimeError, match="invalid accepted batch count"):
        job.run(
            cast(Session, SimpleNamespace(flush=lambda: None)),
            now=datetime.fromtimestamp(165, UTC),
        )
    assert not row.state["faults"]["heartbeat"]["notified"]
