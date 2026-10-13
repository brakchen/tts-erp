"""Manual-confirmation contract: the only path to succeeded (02 §7.4).

Also covers the three rules that changed with it: unlimited manual retry
(§7.1), the cancel boundary that `params_locked_at` closes (§6.5), and
per-device claiming (§16.1).

Read-only with respect to product code: these tests assert the confirmed
contract and never document retired behaviour as correct.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from tts_erp_v2.db.models.publishing import (
    VideoPublishAttempt,
    VideoPublishConfirmation,
    VideoPublishTask,
)
from tts_erp_v2.publishing.domain import AttemptStatus, TaskStage, TaskStatus
from tts_erp_v2.publishing.repository import (
    ConfirmationActor,
    claim_one,
    confirm_published,
    database_now,
    release_lease,
)
from tts_erp_v2.publishing.submission import cancel_task, retry_task

pytestmark = [pytest.mark.domain_publishing]


def _task(
    *,
    status: str = TaskStatus.PENDING.value,
    stage: str = TaskStage.QUEUED.value,
    device_serial: str = "TEST_device",
    device_cleanup_status: str = "not_started",
    object_uploaded: bool = False,
    params_locked_at: datetime | None = None,
) -> VideoPublishTask:
    terminal = status in {
        TaskStatus.SUCCEEDED.value,
        TaskStatus.FAILED.value,
        TaskStatus.NEEDS_REVIEW.value,
        TaskStatus.CANCELLED.value,
    }
    return VideoPublishTask(
        public_id=uuid4(),
        client_request_id=uuid4(),
        caption="TEST_caption",
        original_filename="TEST_video.mp4",
        content_type="video/mp4",
        size_bytes=4,
        object_bucket="tiktok-video",
        object_key=f"TEST/{uuid4()}.mp4",
        object_etag="TEST-etag",
        object_uploaded_at=datetime.now(UTC) if object_uploaded else None,
        status=status,
        stage=TaskStage.DONE.value if terminal else stage,
        target_device_serial=device_serial,
        target_app_package="com.tiktok",
        device_cleanup_status=device_cleanup_status,
        spool_cleanup_status="succeeded",
        object_cleanup_status="succeeded",
        cleanup_intent=(
            "preserve_state"
            if device_cleanup_status in {"pending", "failed"}
            else "none"
        ),
        params_locked_at=params_locked_at,
        last_error_code="TEST_AMBIGUOUS" if terminal else None,
        last_error_message="TEST_AMBIGUOUS" if terminal else None,
        completed_at=datetime.now(UTC) if terminal else None,
        queued_at=datetime.now(UTC),
    )


def _factory(db_session: Session):
    """A real factory: confirm_published opens and commits its own session."""

    def factory() -> Session:
        return Session(
            bind=db_session.get_bind(), join_transaction_mode="create_savepoint"
        )

    return factory


def _terminal_attempt(task: VideoPublishTask, *, status: str) -> VideoPublishAttempt:
    attempt = VideoPublishAttempt(
        sequence_no=1,
        kind="publish",
        status=status,
        artemis_session_id=uuid4(),
        prompt_version="TEST",
        prompt_snapshot="TEST",
        device_serial=task.target_device_serial,
        artemis_profile="TEST_pro",
        artemis_verification_level="TEST_strict",
    )
    task.attempts.append(attempt)
    return attempt


def _confirmations(db_session: Session, task: VideoPublishTask) -> list:
    return list(
        db_session.scalars(
            select(VideoPublishConfirmation).where(
                VideoPublishConfirmation.task_id == task.id
            )
        )
    )


def test_confirm_published_writes_the_audit_row_and_succeeds(
    db_session: Session,
) -> None:
    task = _task(status=TaskStatus.NEEDS_REVIEW.value)
    _terminal_attempt(task, status=AttemptStatus.SUCCESS.value)
    db_session.add(task)
    db_session.flush()
    before = database_now(db_session)

    outcome = confirm_published(
        _factory(db_session),
        task.public_id,
        actor=ConfirmationActor(user_id=4242, key_hash="TEST_key_hash"),
        request_id=uuid4(),
    )

    db_session.expire_all()
    assert outcome.replayed is False
    assert outcome.task_status == TaskStatus.SUCCEEDED.value
    assert task.status == TaskStatus.SUCCEEDED.value
    assert task.stage == TaskStage.DONE.value
    assert task.confirmed_by_user_id == 4242
    assert task.confirmed_by_key_hash == "TEST_key_hash"
    # The click time is database time, not the caller's clock.
    assert task.confirmed_at is not None and task.confirmed_at >= before
    assert task.last_error_code is None
    assert task.last_error_message is None
    assert task.row_version > 1

    rows = _confirmations(db_session, task)
    assert len(rows) == 1
    assert rows[0].action == "confirm_published"
    assert rows[0].from_status == TaskStatus.NEEDS_REVIEW.value
    assert rows[0].to_status == TaskStatus.SUCCEEDED.value
    assert rows[0].operator_user_id == 4242
    assert rows[0].created_at == task.confirmed_at


def test_confirm_published_is_idempotent(db_session: Session) -> None:
    task = _task(status=TaskStatus.NEEDS_REVIEW.value)
    _terminal_attempt(task, status=AttemptStatus.SUCCESS.value)
    db_session.add(task)
    db_session.flush()

    first = confirm_published(
        _factory(db_session),
        task.public_id,
        actor=ConfirmationActor(user_id=1, key_hash="TEST_first"),
        request_id=uuid4(),
    )
    db_session.expire_all()
    version_after_first = task.row_version

    second = confirm_published(
        _factory(db_session),
        task.public_id,
        actor=ConfirmationActor(user_id=2, key_hash="TEST_second"),
        request_id=uuid4(),
    )

    db_session.expire_all()
    assert first.replayed is False
    assert second.replayed is True
    assert second.confirmed_at == first.confirmed_at
    assert second.confirmed_by_user_id == 1
    assert second.confirmed_by_key_hash == "TEST_first"
    # A replay must not bump the row version again.
    assert task.row_version == version_after_first
    assert len(_confirmations(db_session, task)) == 1


def test_concurrent_confirmation_keeps_the_first_writer(db_session: Session) -> None:
    """Two operators clicking at once: the unique index keeps the first row."""
    task = _task(status=TaskStatus.NEEDS_REVIEW.value)
    _terminal_attempt(task, status=AttemptStatus.SUCCESS.value)
    db_session.add(task)
    db_session.flush()

    # The racing writer committed its audit row first (02 §16.2 step 6).
    winner_created = database_now(db_session) - timedelta(seconds=30)
    db_session.add(
        VideoPublishConfirmation(
            task_id=task.id,
            action="confirm_published",
            from_status=TaskStatus.NEEDS_REVIEW.value,
            to_status=TaskStatus.SUCCEEDED.value,
            operator_user_id=900,
            operator_key_hash="TEST_winner",
            request_id=uuid4(),
            created_at=winner_created,
        )
    )
    db_session.flush()

    outcome = confirm_published(
        _factory(db_session),
        task.public_id,
        actor=ConfirmationActor(user_id=1, key_hash="TEST_loser"),
        request_id=uuid4(),
    )

    db_session.expire_all()
    assert outcome.replayed is True
    assert outcome.confirmed_by_user_id == 900
    assert outcome.confirmed_at == winner_created
    rows = _confirmations(db_session, task)
    assert len(rows) == 1
    assert rows[0].operator_user_id == 900


def test_confirm_published_keeps_outstanding_cleanup_visible(
    db_session: Session,
) -> None:
    """A confirmed task may still hold an unresolved device cleanup."""
    task = _task(status=TaskStatus.NEEDS_REVIEW.value, device_cleanup_status="pending")
    _terminal_attempt(task, status=AttemptStatus.SUCCESS.value)
    db_session.add(task)
    db_session.flush()

    outcome = confirm_published(
        _factory(db_session),
        task.public_id,
        actor=ConfirmationActor(user_id=7, key_hash="TEST_key"),
        request_id=uuid4(),
    )

    db_session.expire_all()
    assert outcome.task_status == TaskStatus.SUCCEEDED.value
    assert task.status == TaskStatus.SUCCEEDED.value
    assert task.cleanup_intent == "preserve_state"
    assert task.device_cleanup_status == "pending"


def test_confirm_published_rejects_states_that_are_not_decidable(
    db_session: Session,
) -> None:
    running = _task(status=TaskStatus.RUNNING.value, stage=TaskStage.DOWNLOADING.value)
    db_session.add(running)
    db_session.flush()
    with pytest.raises(ValueError, match="TASK_ACTION_NOT_ALLOWED"):
        confirm_published(
            _factory(db_session),
            running.public_id,
            actor=ConfirmationActor(user_id=1, key_hash="TEST_key"),
            request_id=uuid4(),
        )

    busy = _task(status=TaskStatus.NEEDS_REVIEW.value)
    _terminal_attempt(busy, status=AttemptStatus.RUNNING.value)
    db_session.add(busy)
    db_session.flush()
    with pytest.raises(ValueError, match="TASK_ACTION_NOT_ALLOWED"):
        confirm_published(
            _factory(db_session),
            busy.public_id,
            actor=ConfirmationActor(user_id=1, key_hash="TEST_key"),
            request_id=uuid4(),
        )

    queued = _task(status=TaskStatus.PENDING.value)
    db_session.add(queued)
    db_session.flush()
    with pytest.raises(ValueError, match="TASK_ACTION_NOT_ALLOWED"):
        confirm_published(
            _factory(db_session),
            queued.public_id,
            actor=ConfirmationActor(user_id=1, key_hash="TEST_key"),
            request_id=uuid4(),
        )


def test_confirm_published_requires_the_matching_row_version(
    db_session: Session,
) -> None:
    task = _task(status=TaskStatus.FAILED.value)
    _terminal_attempt(task, status=AttemptStatus.FAILED.value)
    db_session.add(task)
    db_session.flush()
    with pytest.raises(ValueError, match="ROW_VERSION_CONFLICT"):
        confirm_published(
            _factory(db_session),
            task.public_id,
            actor=ConfirmationActor(user_id=1, key_hash="TEST_key"),
            request_id=uuid4(),
            row_version=task.row_version + 99,
        )


def test_failed_task_can_be_retried_without_any_attempt_limit(
    db_session: Session,
) -> None:
    """02 §7.1: unlimited manual retries, history kept only for display."""
    task = _task(status=TaskStatus.FAILED.value, object_uploaded=True)
    _terminal_attempt(task, status=AttemptStatus.FAILED.value)
    db_session.add(task)
    db_session.flush()

    for round_no in range(5):
        task = retry_task(db_session, task.public_id)
        db_session.expire_all()
        assert task.status == TaskStatus.PENDING.value
        assert task.stage == TaskStage.QUEUED.value
        assert task.last_error_code is None
        assert task.last_error_message is None
        assert task.completed_at is None
        assert task.queued_at is not None

        attempt = _terminal_attempt(task, status=AttemptStatus.FAILED.value)
        attempt.sequence_no = round_no + 2
        db_session.add(task)
        db_session.flush()
        task.status = TaskStatus.FAILED.value
        task.stage = TaskStage.DONE.value
        task.completed_at = datetime.now(UTC)
        task.last_error_code = "TEST_ROUND_FAILED"
        db_session.flush()

    # Every round is preserved as audit history and never gated a retry.
    assert task.attempt_count == 0
    assert len(task.attempts) == 6
    assert task.status == TaskStatus.FAILED.value


def test_retry_is_refused_while_an_attempt_is_still_active(
    db_session: Session,
) -> None:
    task = _task(status=TaskStatus.FAILED.value, object_uploaded=True)
    _terminal_attempt(task, status=AttemptStatus.RUNNING.value)
    db_session.add(task)
    db_session.flush()

    with pytest.raises(ValueError, match="TASK_ACTION_NOT_ALLOWED"):
        retry_task(db_session, task.public_id)


def test_claim_is_per_device_and_lets_other_devices_proceed(
    db_session: Session,
) -> None:
    """02 §16.1: one running slot per device, not one slot globally."""
    first = _task()
    second = _task(device_serial="TEST_other_device")
    db_session.add_all([first, second])
    db_session.flush()

    claimed = claim_one(db_session, "worker-a", "TEST_device")
    assert claimed is not None
    assert claimed.public_id == first.public_id

    # Same device is blocked; a different device publishes in parallel.
    assert claim_one(db_session, "worker-b", "TEST_device") is None
    other = claim_one(db_session, "worker-c", "TEST_other_device")
    assert other is not None
    assert other.public_id == second.public_id


def test_device_cleanup_gate_only_blocks_its_own_device(
    db_session: Session,
) -> None:
    residue = _task(status=TaskStatus.FAILED.value, device_cleanup_status="failed")
    clean = _task(device_serial="TEST_other_device")
    db_session.add_all([residue, clean])
    db_session.flush()

    assert claim_one(db_session, "worker-a", "TEST_device") is None
    other = claim_one(db_session, "worker-b", "TEST_other_device")
    assert other is not None
    assert other.public_id == clean.public_id


def test_cancel_is_refused_once_artemis_admitted_the_publish(
    db_session: Session,
) -> None:
    """02 §6.5: a submitted task can no longer be cancelled."""
    task = _task(
        status=TaskStatus.RUNNING.value,
        stage=TaskStage.DISPATCHING_ARTEMIS.value,
        params_locked_at=datetime.now(UTC),
    )
    task.lease_owner = "worker-a"
    task.lease_expires_at = datetime.now(UTC) + timedelta(minutes=1)
    db_session.add(task)
    db_session.flush()

    with pytest.raises(ValueError, match="TASK_ALREADY_SUBMITTED"):
        cancel_task(db_session, task.public_id, None)  # type: ignore[arg-type]
    db_session.expire_all()
    assert task.status == TaskStatus.RUNNING.value
    assert task.cancel_requested is False


def test_cancel_during_admission_returns_task_submitting(db_session: Session) -> None:
    """02 §6.5: while the admission request is in flight the API must say
    TASK_SUBMITTING instead of setting a flag the worker would never read."""
    task = _task(
        status=TaskStatus.RUNNING.value,
        stage=TaskStage.DISPATCHING_ARTEMIS.value,
    )
    task.lease_owner = "worker-a"
    task.lease_expires_at = datetime.now(UTC) + timedelta(minutes=1)
    db_session.add(task)
    db_session.flush()

    with pytest.raises(ValueError, match="TASK_SUBMITTING"):
        cancel_task(db_session, task.public_id, None)  # type: ignore[arg-type]
    db_session.expire_all()
    assert task.status == TaskStatus.RUNNING.value
    assert task.cancel_requested is False


def test_cancel_of_a_running_round_sets_the_stop_flag(db_session: Session) -> None:
    task = _task(status=TaskStatus.RUNNING.value, stage=TaskStage.STAGING_DEVICE.value)
    task.lease_owner = "worker-a"
    task.lease_expires_at = datetime.now(UTC) + timedelta(minutes=1)
    task.device_cleanup_status = "pending"
    db_session.add(task)
    db_session.flush()

    cancel_task(db_session, task.public_id, None)  # type: ignore[arg-type]
    db_session.expire_all()
    # The worker stops at the next stage boundary; the round is not closed yet.
    assert task.status == TaskStatus.RUNNING.value
    assert task.cancel_requested is True
    assert task.stage == TaskStage.STAGING_DEVICE.value


def test_cancel_closes_a_queued_task_and_keeps_the_object(
    db_session: Session,
) -> None:
    task = _task(object_uploaded=True)
    task.object_upload_expires_at = datetime.now(UTC) + timedelta(minutes=10)
    db_session.add(task)
    db_session.flush()

    cancel_task(db_session, task.public_id, None)  # type: ignore[arg-type]
    db_session.expire_all()
    assert task.status == TaskStatus.CANCELLED.value
    assert task.stage == TaskStage.DONE.value
    assert task.completed_at is not None
    assert task.object_cleanup_status == "succeeded"
    release_lease(task)
