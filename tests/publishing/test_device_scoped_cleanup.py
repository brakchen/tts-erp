"""Per-device cleanup ownership and the retired object resource.

02 §5.3/§6.1: one worker process serves one device, device cleanup blocks only
that device, and MinIO objects are never deleted so the ``object`` resource
neither stays claimable nor holds cleanup ownership open.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy.orm import Session

from tts_erp_v2.db.models.publishing import VideoPublishTask
from tts_erp_v2.publishing.domain import cleanup_retryable_resources
from tts_erp_v2.publishing.repository import (
    CleanupClaimRequest,
    claim_cleanup_work,
    finish_cleanup_work,
)

pytestmark = [pytest.mark.domain_publishing]

THIS_DEVICE = "TEST_device"
OTHER_DEVICE = "TEST_other_device"


def _factory(db_session: Session):
    def factory():
        return Session(
            bind=db_session.get_bind(), join_transaction_mode="create_savepoint"
        )

    return factory


def _cleanup_task(
    *, serial: str, device_status: str = "pending", spool_status: str = "succeeded"
) -> VideoPublishTask:
    task = VideoPublishTask(
        public_id=uuid4(),
        client_request_id=uuid4(),
        caption="TEST_scoped_cleanup",
        original_filename="TEST.mp4",
        content_type="video/mp4",
        size_bytes=4,
        object_bucket="tiktok-video",
        object_key=f"TEST/scoped/{uuid4()}.mp4",
        status="failed",
        stage="done",
        cleanup_intent="preserve_state",
        target_device_serial=serial,
        target_app_package="com.zhiliaoapp.musically",
        device_path=f"/sdcard/Movies/TTSERP/{uuid4()}.mp4",
        device_cleanup_status=device_status,
        device_cleanup_next_attempt_at=datetime.now(UTC) - timedelta(minutes=1),
        spool_cleanup_status=spool_status,
        spool_cleanup_next_attempt_at=datetime.now(UTC) - timedelta(minutes=1),
        object_cleanup_status="not_started",
    )
    return task


def _running_task(*, serial: str) -> VideoPublishTask:
    return VideoPublishTask(
        public_id=uuid4(),
        client_request_id=uuid4(),
        caption="TEST_scoped_running",
        original_filename="TEST-running.mp4",
        content_type="video/mp4",
        size_bytes=4,
        object_bucket="tiktok-video",
        object_key=f"TEST/scoped/{uuid4()}.mp4",
        status="running",
        stage="downloading",
        cleanup_intent="none",
        target_device_serial=serial,
        target_app_package="com.zhiliaoapp.musically",
        stage_started_at=datetime.now(UTC),
    )


def test_device_cleanup_claim_ignores_another_devices_running_task(
    db_session: Session,
) -> None:
    """A busy device elsewhere must not block this device's cleanup."""
    mine = _cleanup_task(serial=THIS_DEVICE)
    db_session.add_all([mine, _running_task(serial=OTHER_DEVICE)])
    db_session.commit()

    work = claim_cleanup_work(
        _factory(db_session),
        CleanupClaimRequest("TEST-scoped", 30, "device", THIS_DEVICE),
    )
    assert work is not None
    assert work.resources == ("device",)
    assert work.device_serial == THIS_DEVICE


def test_device_cleanup_claim_is_blocked_by_its_own_running_task(
    db_session: Session,
) -> None:
    mine = _cleanup_task(serial=THIS_DEVICE)
    db_session.add_all([mine, _running_task(serial=THIS_DEVICE)])
    db_session.commit()

    assert (
        claim_cleanup_work(
            _factory(db_session),
            CleanupClaimRequest("TEST-scoped", 30, "device", THIS_DEVICE),
        )
        is None
    )


def test_device_cleanup_claim_never_selects_another_devices_rows(
    db_session: Session,
) -> None:
    """The selector is device-scoped, so the other device's row is untouched."""
    theirs = _cleanup_task(serial=OTHER_DEVICE)
    db_session.add(theirs)
    db_session.commit()

    assert (
        claim_cleanup_work(
            _factory(db_session),
            CleanupClaimRequest("TEST-scoped", 30, "device", THIS_DEVICE),
        )
        is None
    )
    assert theirs.cleanup_lease_owner is None


def test_cleanup_claim_requires_a_device_serial(db_session: Session) -> None:
    db_session.add(_cleanup_task(serial=THIS_DEVICE))
    db_session.commit()

    with pytest.raises(ValueError, match="CLEANUP_DEVICE_SERIAL_REQUIRED"):
        claim_cleanup_work(
            _factory(db_session),
            CleanupClaimRequest("TEST-scoped", 30, "device", ""),
        )


def test_legacy_pending_object_row_is_never_claimable(db_session: Session) -> None:
    """A retired object status must never produce cleanup work.

    ``claim_cleanup_work`` ignores object entirely, so such a row is simply not
    claimable: the worker cannot spin on it, and nothing ever deletes the object.
    """
    task = _cleanup_task(
        serial=THIS_DEVICE, device_status="succeeded", spool_status="succeeded"
    )
    task.object_cleanup_status = "pending"
    db_session.add(task)
    db_session.commit()

    factory = _factory(db_session)
    for owner in ("TEST-scoped-1", "TEST-scoped-2"):
        assert (
            claim_cleanup_work(
                factory, CleanupClaimRequest(owner, 30, "device", THIS_DEVICE)
            )
            is None
        )
        assert (
            claim_cleanup_work(
                factory, CleanupClaimRequest(owner, 30, "background", THIS_DEVICE)
            )
            is None
        )
    db_session.expire_all()
    assert task.object_cleanup_status == "pending"
    assert task.object_deleted_at is None
    assert task.cleanup_lease_owner is None


def test_object_row_stays_owned_but_spool_cleanup_still_drains(
    db_session: Session,
) -> None:
    """Real cleanup still drains; only the retired object keeps ownership.

    ``video_publish_task_cleanup_owner_check`` forbids ``cleanup_intent='none'``
    while an object status is pending, so the row keeps its cleanup marker. The
    property under test is that the remaining real resource is executed once and
    is not re-claimed afterwards.
    """
    task = _cleanup_task(
        serial=THIS_DEVICE, device_status="succeeded", spool_status="pending"
    )
    task.object_cleanup_status = "pending"
    db_session.add(task)
    db_session.commit()

    factory = _factory(db_session)
    work = claim_cleanup_work(
        factory, CleanupClaimRequest("TEST-scoped", 30, "background", THIS_DEVICE)
    )
    assert work is not None
    assert work.resources == ("spool",)

    outcome = finish_cleanup_work(factory, work.token, {"spool": None})
    assert outcome.cleanup_intent == "preserve_state"

    assert (
        claim_cleanup_work(
            factory, CleanupClaimRequest("TEST-scoped-2", 30, "background", THIS_DEVICE)
        )
        is None
    )
    db_session.expire_all()
    assert task.spool_cleanup_status == "succeeded"
    assert task.object_deleted_at is None


def test_cleanup_retryable_resources_never_reports_object(db_session: Session) -> None:
    task = _cleanup_task(serial=THIS_DEVICE, device_status="failed")
    task.object_cleanup_status = "failed"
    db_session.add(task)
    db_session.commit()

    assert cleanup_retryable_resources(task) == ("device",)


def test_object_is_not_a_retryable_cleanup_resource(db_session: Session) -> None:
    from tts_erp_v2.publishing.repository import retry_cleanup_resources

    task = _cleanup_task(serial=THIS_DEVICE, device_status="failed")
    task.object_cleanup_status = "failed"
    db_session.add(task)
    db_session.flush()

    with pytest.raises(ValueError, match="CLEANUP_RESOURCE_NOT_RETRYABLE"):
        retry_cleanup_resources(db_session, task, resources=["object"])
