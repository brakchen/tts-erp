from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Barrier
from uuid import uuid4

import pytest
from sqlalchemy import delete, func, select, update
from sqlalchemy.orm import Session, sessionmaker

from tts_erp_v2.db.models.publishing import VideoPublishAttempt, VideoPublishTask
from tts_erp_v2.publishing.repository import (
    AttemptObservation,
    CleanupClaimRequest,
    LeaseLost,
    ObserveAttempt,
    PublishLeaseToken,
    claim_cleanup_work,
    commit_publish_transition,
    finish_cleanup_work,
)


def _task(*, stage: str = "waiting_artemis") -> VideoPublishTask:
    task_id = uuid4()
    return VideoPublishTask(
        public_id=task_id,
        client_request_id=uuid4(),
        caption="TEST_owner_seam",
        original_filename="TEST_video.mp4",
        content_type="video/mp4",
        size_bytes=4,
        object_bucket="tiktok-video",
        object_key=f"TEST/owner-seam/{task_id}.mp4",
        status="running" if stage != "done" else "succeeded",
        stage=stage,
        target_device_serial="TEST_device",
        target_app_package="com.tiktok",
        device_path="/sdcard/Movies/TEST/video.mp4",
        device_cleanup_status="pending" if stage == "done" else "not_started",
        spool_cleanup_status="pending" if stage == "done" else "not_started",
        object_cleanup_status="pending" if stage == "done" else "not_started",
        cleanup_intent="finalize_success" if stage == "done" else "none",
        lease_owner="worker-a" if stage != "done" else None,
        lease_expires_at=(
            datetime.now(UTC) + timedelta(minutes=5) if stage != "done" else None
        ),
        row_version=7,
    )


def _factory(db_engine) -> sessionmaker[Session]:
    return sessionmaker(db_engine, expire_on_commit=False)


def _delete_task(factory: sessionmaker[Session], task_id) -> None:
    with factory() as session:
        session.execute(
            delete(VideoPublishAttempt).where(
                VideoPublishAttempt.task_id
                == select(VideoPublishTask.id)
                .where(VideoPublishTask.public_id == task_id)
                .scalar_subquery()
            )
        )
        session.execute(
            delete(VideoPublishTask).where(VideoPublishTask.public_id == task_id)
        )
        session.commit()


@pytest.mark.parametrize(
    "observation",
    [
        AttemptObservation(status="running", terminal=False),
        AttemptObservation(status="success", terminal=True),
        AttemptObservation(status="success", terminal=True, verdict="published"),
        AttemptObservation(status="success", terminal=True, verdict="not_published"),
        AttemptObservation(status="success", terminal=True, verdict="inconclusive"),
    ],
)
def test_stale_publish_token_cannot_commit_any_observation(
    db_engine, observation: AttemptObservation
) -> None:
    factory = _factory(db_engine)
    task = _task(stage="verifying" if observation.verdict else "waiting_artemis")
    with factory() as session:
        session.add(task)
        session.flush()
        related = None
        if observation.verdict:
            related = VideoPublishAttempt(
                task_id=task.id,
                sequence_no=1,
                kind="publish",
                status="failed",
                artemis_session_id=uuid4(),
                prompt_version="TEST",
                prompt_snapshot="TEST",
                device_serial="TEST_device",
            )
            session.add(related)
            session.flush()
        attempt = VideoPublishAttempt(
            task_id=task.id,
            sequence_no=2 if related else 1,
            kind="verify" if observation.verdict else "publish",
            related_attempt_id=related.id if related else None,
            status="running",
            artemis_session_id=uuid4(),
            prompt_version="TEST",
            prompt_snapshot="TEST",
            device_serial="TEST_device",
        )
        session.add(attempt)
        session.commit()
        task_id = task.public_id
        attempt_id = attempt.id
        token = PublishLeaseToken(
            task_id=task_id,
            lease_owner="worker-a",
            row_version=task.row_version,
            attempt_id=attempt_id,
            expected_attempt_status="running",
        )
    try:
        with factory() as session:
            session.execute(
                update(VideoPublishTask)
                .where(VideoPublishTask.public_id == task_id)
                .values(lease_owner="worker-b", row_version=8)
            )
            session.commit()
        with pytest.raises(LeaseLost):
            commit_publish_transition(
                factory,
                token,
                ObserveAttempt(observation=observation, max_attempts=3),
            )
        with factory() as session:
            persisted = session.get(VideoPublishAttempt, attempt_id)
            assert persisted is not None
            assert persisted.status == "running"
            owner, version = session.execute(
                select(
                    VideoPublishTask.lease_owner, VideoPublishTask.row_version
                ).where(VideoPublishTask.public_id == task_id)
            ).one()
            assert (owner, version) == ("worker-b", 8)
    finally:
        _delete_task(factory, task_id)


def test_attempt_failure_rolls_back_task_transition_atomically(
    db_engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tts_erp_v2.publishing import repository

    factory = _factory(db_engine)
    task = _task()
    attempt = VideoPublishAttempt(
        sequence_no=1,
        kind="publish",
        status="running",
        artemis_session_id=uuid4(),
        prompt_version="TEST",
        prompt_snapshot="TEST",
        device_serial="TEST_device",
    )
    task.attempts.append(attempt)
    with factory() as session:
        session.add(task)
        session.commit()
        task_id = task.public_id
        attempt_id = attempt.id
        version = task.row_version
    token = PublishLeaseToken(
        task_id=task_id,
        lease_owner="worker-a",
        row_version=version,
        attempt_id=attempt_id,
        expected_attempt_status="running",
    )
    monkeypatch.setattr(
        repository,
        "_new_verify_attempt",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("TEST_INSERT")),
    )
    try:
        with pytest.raises(RuntimeError, match="TEST_INSERT"):
            commit_publish_transition(
                factory,
                token,
                ObserveAttempt(
                    observation=AttemptObservation(
                        status="failed", terminal=True, steps_count=2
                    ),
                    max_attempts=3,
                ),
            )
        with factory() as session:
            persisted_task = session.scalar(
                select(VideoPublishTask).where(VideoPublishTask.public_id == task_id)
            )
            persisted_attempt = session.get(VideoPublishAttempt, attempt_id)
            assert persisted_task is not None and persisted_attempt is not None
            assert persisted_task.row_version == version
            assert persisted_task.stage == "waiting_artemis"
            assert persisted_attempt.status == "running"
    finally:
        _delete_task(factory, task_id)


@pytest.mark.parametrize("exact_requests", [(True, True), (True, False)])
def test_concurrent_cleanup_claim_has_one_winner(
    db_engine, exact_requests: tuple[bool, bool]
) -> None:
    factory = _factory(db_engine)
    task = _task(stage="done")
    with factory() as session:
        session.add(task)
        session.commit()
        task_id = task.public_id
    barrier = Barrier(2)

    def claim(request: tuple[str, bool]):
        owner, exact = request
        barrier.wait()
        return claim_cleanup_work(
            factory,
            CleanupClaimRequest(
                owner=owner,
                lease_seconds=30,
                scope="device",
                task_id=task_id if exact else None,
            ),
        )

    requests = tuple(
        (owner, exact)
        for owner, exact in zip(("cleanup-a", "cleanup-b"), exact_requests, strict=True)
    )
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            outcomes = list(pool.map(claim, requests))
        winners = [outcome for outcome in outcomes if outcome is not None]
        assert len(winners) == 1
        assert winners[0].resources == ("device",)
        with factory() as session:
            owner = session.scalar(
                select(VideoPublishTask.cleanup_lease_owner).where(
                    VideoPublishTask.public_id == task_id
                )
            )
            assert owner == winners[0].token.owner
    finally:
        _delete_task(factory, task_id)


def test_cleanup_completion_after_takeover_cannot_write(db_engine) -> None:
    factory = _factory(db_engine)
    task = _task(stage="done")
    task.spool_cleanup_status = "succeeded"
    task.object_cleanup_status = "succeeded"
    with factory() as session:
        session.add(task)
        session.commit()
        task_id = task.public_id
    work = claim_cleanup_work(
        factory,
        CleanupClaimRequest("cleanup-a", 30, "device", task_id),
    )
    assert work is not None
    try:
        with factory() as session:
            session.execute(
                update(VideoPublishTask)
                .where(VideoPublishTask.public_id == task_id)
                .values(
                    cleanup_lease_owner="cleanup-b",
                    cleanup_lease_expires_at=func.clock_timestamp()
                    + timedelta(minutes=1),
                    row_version=work.token.row_version + 1,
                )
            )
            session.commit()
        with pytest.raises(LeaseLost):
            finish_cleanup_work(factory, work.token, {"device": None})
        with factory() as session:
            status, owner = session.execute(
                select(
                    VideoPublishTask.device_cleanup_status,
                    VideoPublishTask.cleanup_lease_owner,
                ).where(VideoPublishTask.public_id == task_id)
            ).one()
            assert (status, owner) == ("pending", "cleanup-b")
    finally:
        _delete_task(factory, task_id)


def test_cleanup_claim_blocks_live_owner_and_allows_database_expiry(db_engine) -> None:
    factory = _factory(db_engine)
    task = _task(stage="done")
    task.cleanup_lease_owner = "cleanup-a"
    task.cleanup_lease_expires_at = datetime.now(UTC) + timedelta(minutes=5)
    with factory() as session:
        session.add(task)
        session.commit()
        task_id = task.public_id
    try:
        assert (
            claim_cleanup_work(
                factory,
                CleanupClaimRequest("cleanup-b", 30, "device", task_id),
            )
            is None
        )
        with factory() as session:
            session.execute(
                update(VideoPublishTask)
                .where(VideoPublishTask.public_id == task_id)
                .values(
                    cleanup_lease_expires_at=func.clock_timestamp()
                    - timedelta(seconds=1)
                )
            )
            session.commit()
        work = claim_cleanup_work(
            factory,
            CleanupClaimRequest("cleanup-b", 30, "device", task_id),
        )
        assert work is not None
        assert work.token.owner == "cleanup-b"
    finally:
        _delete_task(factory, task_id)


def test_cleanup_claim_returns_only_database_time_due_resources(
    db_engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tts_erp_v2.publishing import repository

    factory = _factory(db_engine)
    task = _task(stage="done")
    task.device_cleanup_status = "succeeded"
    task.spool_cleanup_status = "pending"
    task.spool_cleanup_next_attempt_at = datetime.now(UTC) - timedelta(minutes=1)
    task.object_cleanup_status = "pending"
    task.object_cleanup_next_attempt_at = datetime.now(UTC) + timedelta(hours=1)
    with factory() as session:
        session.add(task)
        session.commit()
        task_id = task.public_id

    class SkewedDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2099, 1, 1, tzinfo=tz or UTC)

    monkeypatch.setattr(repository, "datetime", SkewedDateTime)
    try:
        work = claim_cleanup_work(
            factory,
            CleanupClaimRequest("cleanup-due", 30, "background", task_id),
        )
        assert work is not None
        assert work.resources == ("spool",)
    finally:
        _delete_task(factory, task_id)


def test_cleanup_retry_deadline_uses_database_time(
    db_engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tts_erp_v2.publishing import repository

    factory = _factory(db_engine)
    task = _task(stage="done")
    task.spool_cleanup_status = "succeeded"
    task.object_cleanup_status = "succeeded"
    with factory() as session:
        session.add(task)
        session.commit()
        task_id = task.public_id
    work = claim_cleanup_work(
        factory,
        CleanupClaimRequest("cleanup-clock", 30, "device", task_id),
    )
    assert work is not None

    class SkewedDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2099, 1, 1, tzinfo=tz or UTC)

    monkeypatch.setattr(repository, "datetime", SkewedDateTime)
    try:
        with factory() as session:
            before = session.scalar(select(func.clock_timestamp()))
        finish_cleanup_work(factory, work.token, {"device": "TEST_BUSY"})
        with factory() as session:
            retry_at, after = session.execute(
                select(
                    VideoPublishTask.device_cleanup_next_attempt_at,
                    func.clock_timestamp(),
                ).where(VideoPublishTask.public_id == task_id)
            ).one()
        assert before is not None and after is not None and retry_at is not None
        assert before + timedelta(seconds=5) <= retry_at <= after + timedelta(seconds=5)
    finally:
        _delete_task(factory, task_id)
