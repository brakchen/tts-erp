"""Real two-session outbox lease fencing; all rows are TEST_-owned."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from tts_erp_v2.db.models.plugin_status import StatusInstance, StatusNotificationOutbox
from tts_erp_v2.db.models.security import ApiKey
from tts_erp_v2.plugin.status import repository, scheduler

pytestmark = [pytest.mark.domain_api, pytest.mark.layer_integration]
NOW = datetime.now(UTC)


@pytest.fixture
def outbox_row(db_engine):
    instance_id = uuid.uuid4()
    row_id = uuid.uuid4()
    with Session(db_engine) as session:
        key = ApiKey(
            name=f"TEST_outbox_lease_{instance_id}",
            key_hash=uuid.uuid4().hex + uuid.uuid4().hex,
            key_prefix="TEST_outbox_lease",
            role="readonly",
            purpose="plugin_status",
        )
        session.add(key)
        session.flush()
        key_id = key.id
        session.add(
            StatusInstance(
                id=instance_id,
                plugin_id="ads-monitor",
                status_api_key_id=key_id,
                scope={"kind": "shop", "sellerId": "TEST_LEASE_SELLER"},
                capabilities=[],
            )
        )
        session.flush()
        session.add(
            StatusNotificationOutbox(
                id=row_id,
                instance_id=instance_id,
                incident_key="TEST_lease",
                notification_kind="episode_requires_user",
                dedupe_key=f"TEST_{row_id}",
                payload={"reasonCode": "LOGIN_REQUIRED"},
                next_attempt_at=NOW,
            )
        )
        session.commit()
    try:
        yield row_id
    finally:
        with Session(db_engine) as session:
            session.execute(
                delete(StatusNotificationOutbox).where(
                    StatusNotificationOutbox.instance_id == instance_id
                )
            )
            session.execute(
                delete(StatusInstance).where(StatusInstance.id == instance_id)
            )
            session.execute(delete(ApiKey).where(ApiKey.id == key_id))
            session.commit()


def settle(session: Session, claim: Any, outcome: str, now: datetime):
    if outcome == "delivered":
        return repository.mark_delivered(session, claim, now=now)
    if outcome == "retry":
        return repository.mark_retry(
            session,
            claim,
            now=now,
            next_attempt_at=now + timedelta(seconds=30),
            error_code="TEST_RETRY",
        )
    return repository.mark_pending_manual(
        session, claim, error_code="TEST_MANUAL", now=now
    )


@pytest.mark.parametrize("outcome", ["delivered", "retry", "pending_manual"])
def test_expired_worker_cannot_overwrite_a_new_claim(db_engine, outbox_row, outcome):
    later = NOW + timedelta(seconds=61)
    with Session(db_engine) as old, Session(db_engine) as replacement:
        claim = repository.claim_due_notifications(
            old, now=NOW, limit=1, lease_seconds=60
        )[0]
        old.commit()
        assert repository.reclaim_expired_leases(replacement, now=later) == 1
        new_claim = repository.claim_due_notifications(
            replacement, now=later, limit=1, lease_seconds=60
        )[0]
        new_lease = new_claim.lease_until
        replacement.commit()
        settle(old, claim, outcome, later)
        old.commit()
    with Session(db_engine) as check:
        row = check.get(StatusNotificationOutbox, outbox_row)
        assert row is not None
        assert row.state == "claimed", "stale worker overwrote replacement ownership"
        assert row.lease_until == new_lease
        assert row.attempt_count == 0
        assert row.delivered_at is None
        assert row.last_error_code is None


@pytest.mark.parametrize("outcome", ["delivered", "retry", "pending_manual"])
def test_current_worker_can_settle_its_own_claim(
    db_engine, outbox_row, outcome, monkeypatch
):
    # Suite collection may precede this test by minutes. All three settlement
    # outcomes must use the same injected instant, not a second live clock.
    monkeypatch.setattr(
        repository, "datetime", SimpleNamespace(now=lambda tz: NOW + timedelta(days=1))
    )
    with Session(db_engine) as session:
        claim = repository.claim_due_notifications(
            session, now=NOW, limit=1, lease_seconds=60
        )[0]
        session.commit()
        settle(session, claim, outcome, NOW + timedelta(seconds=1))
        session.commit()
    with Session(db_engine) as check:
        row = check.get(StatusNotificationOutbox, outbox_row)
        assert row is not None
        assert (
            row.state
            == {
                "delivered": "delivered",
                "retry": "pending",
                "pending_manual": "pending_manual",
            }[outcome]
        )
        assert row.lease_until is None
        assert row.attempt_count == (1 if outcome == "retry" else 0)


@pytest.mark.parametrize("lease_seconds", [0, -1])
def test_nonpositive_lease_cannot_break_claim_identity(
    db_engine, outbox_row, lease_seconds
):
    with Session(db_engine) as session:
        with pytest.raises(ValueError, match="lease must be positive"):
            repository.claim_due_notifications(
                session, now=NOW, limit=1, lease_seconds=lease_seconds
            )
        session.rollback()
    with Session(db_engine) as check:
        row = check.get(StatusNotificationOutbox, outbox_row)
        assert row is not None
        assert row.state == "pending"
        assert row.lease_until is None


def test_reclaim_refreshes_a_retained_pending_identity(db_engine, outbox_row):
    later = NOW + timedelta(seconds=61)
    with (
        Session(db_engine, expire_on_commit=False) as watcher,
        Session(db_engine) as worker,
    ):
        cached = watcher.get(StatusNotificationOutbox, outbox_row)
        assert cached is not None
        assert cached.state == "pending" and cached.lease_until is None
        assert (
            len(
                repository.claim_due_notifications(
                    worker, now=NOW, limit=1, lease_seconds=60
                )
            )
            == 1
        )
        worker.commit()
        # Keep a strong reference to the stale identity-map object while the
        # database row is claimed. The locking query must refresh authority.
        assert cached.state == "pending" and cached.lease_until is None
        assert repository.reclaim_expired_leases(watcher, now=later) == 1
        watcher.commit()
        with Session(db_engine) as independent:
            persisted = independent.get(StatusNotificationOutbox, outbox_row)
            assert persisted is not None
            assert persisted.state == "pending", (
                "reclaim reported a write it never made"
            )
            assert persisted.lease_until is None
        successor = repository.claim_due_notifications(
            watcher, now=later, limit=1, lease_seconds=60
        )
        assert len(successor) == 1
        assert successor[0].lease_until == later + timedelta(seconds=60)
        watcher.commit()


def test_sweep_claims_only_the_message_it_is_about_to_send(db_engine, outbox_row):
    second_id = uuid.uuid4()
    with Session(db_engine) as session:
        first = session.get(StatusNotificationOutbox, outbox_row)
        assert first is not None
        session.add(
            StatusNotificationOutbox(
                id=second_id,
                instance_id=first.instance_id,
                incident_key="TEST_second",
                notification_kind="episode_requires_user",
                dedupe_key=f"TEST_{second_id}",
                payload={},
                next_attempt_at=NOW + timedelta(microseconds=1),
            )
        )
        session.commit()
    waiting_states = []
    transaction_states = []
    with Session(db_engine) as sender_session:

        def send(message):
            transaction_states.append(sender_session.in_transaction())
            with Session(db_engine) as observer:
                row = observer.get(StatusNotificationOutbox, second_id)
                assert row is not None
                waiting_states.append(row.state)
            return "delivered"

        result = scheduler.run_scheduled(
            sender_session, now=NOW + timedelta(seconds=1), sender=send, limit=2
        )
    assert waiting_states == ["pending", "claimed"], (
        "waiting messages were leased before their network turn"
    )
    assert transaction_states == [False, False]
    assert result["delivered"] == 2


@pytest.mark.parametrize(
    "outcome,counter",
    [
        ("delivered", "delivered"),
        ("retry", "retrying"),
        ("pending_manual", "pendingManual"),
    ],
)
def test_sweep_does_not_count_a_stale_result(db_engine, outbox_row, outcome, counter):
    later = NOW + timedelta(seconds=61)

    def send(message):
        with Session(db_engine) as replacement:
            assert repository.reclaim_expired_leases(replacement, now=later) == 1
            claim = repository.claim_due_notifications(
                replacement, now=later, limit=1, lease_seconds=60
            )[0]
            replacement.commit()
            repository.mark_delivered(
                replacement, claim, now=later + timedelta(seconds=1)
            )
            replacement.commit()
        return outcome

    with Session(db_engine) as session:
        result = scheduler.run_scheduled(session, now=NOW, sender=send)
    assert result[counter] == 0, "rejected stale settlement was counted as current"
    with Session(db_engine) as check:
        row = check.get(StatusNotificationOutbox, outbox_row)
        assert row is not None
        assert row.state == "delivered"
        assert row.delivered_at == later + timedelta(seconds=1)


@pytest.mark.parametrize("expire_before_send", [False, True])
def test_real_clock_expiry_never_starts_or_commits_an_expired_delivery(
    db_engine, outbox_row, monkeypatch, expire_before_send
):
    clock = [NOW]
    monkeypatch.setattr(scheduler, "datetime", SimpleNamespace(now=lambda tz: clock[0]))
    render = scheduler.notifications.render_message
    sent = []

    def render_then_delay(**kwargs):
        message = render(**kwargs)
        if expire_before_send:
            clock[0] += timedelta(seconds=61)
        return message

    monkeypatch.setattr(scheduler.notifications, "render_message", render_then_delay)

    def send(message):
        sent.append(message)
        clock[0] += timedelta(seconds=61)
        return "delivered"

    with Session(db_engine) as session:
        result = scheduler.run_scheduled(session, sender=send, limit=1)
    assert len(sent) == (0 if expire_before_send else 1)
    assert result["delivered"] == 0
    with Session(db_engine) as check:
        row = check.get(StatusNotificationOutbox, outbox_row)
        assert row is not None
        assert row.state == "claimed"
        assert row.delivered_at is None


def test_worker_registry_and_real_system_tick_deliver_the_domain_job(
    db_engine, outbox_row, monkeypatch
):
    from tts_erp_v2.db.models.integration import SyncJob
    from tts_erp_v2.sync_worker import scheduler as worker

    assert scheduler.JOB_NAME in worker.JOBS, (
        "lifecycle job is absent from the actual worker"
    )
    spec = worker.JOBS[scheduler.JOB_NAME]
    assert spec.module_path == scheduler.JOB_MODULE_PATH
    assert spec.interval_seconds == 60
    assert spec.is_tiktok is False
    assert spec.needs_token_registry is False
    assert spec.entrypoint == "run_worker_job"
    # No real secret or HTTP; the real sender class parks an unconfigured notice.
    monkeypatch.setattr(
        scheduler.notifications, "resolve_webhook_url", lambda session: None
    )
    transaction_states = []
    resolve_sender = scheduler._resolve_sender

    def observe_sender_boundary(session, sender):
        deliver = resolve_sender(session, sender)

        def deliver_observed(message):
            transaction_states.append(session.in_transaction())
            return deliver(message)

        return deliver_observed

    monkeypatch.setattr(scheduler, "_resolve_sender", observe_sender_boundary)
    with Session(db_engine) as session:
        before = set(
            session.scalars(
                select(SyncJob.id).where(SyncJob.job_name == scheduler.JOB_NAME)
            )
        )
    try:
        worker.run_scheduled_job_once(scheduler.JOB_NAME)
        assert transaction_states == [False], (
            "audit wrapper held a transaction across send"
        )
        with Session(db_engine) as check:
            jobs = list(
                check.scalars(
                    select(SyncJob).where(SyncJob.job_name == scheduler.JOB_NAME)
                )
            )
            created = [job for job in jobs if job.id not in before]
            assert len(created) == 1
            assert created[0].status == "succeeded"
            assert created[0].finished_at is not None
            assert created[0].rows_total == 1
            assert created[0].extra is not None
            assert created[0].extra["pendingManual"] == 1
            row = check.get(StatusNotificationOutbox, outbox_row)
            assert row is not None
            assert row.state == "pending_manual"
    finally:
        with Session(db_engine) as session:
            session.execute(
                delete(SyncJob).where(
                    SyncJob.job_name == scheduler.JOB_NAME, SyncJob.id.not_in(before)
                )
            )
            session.commit()
