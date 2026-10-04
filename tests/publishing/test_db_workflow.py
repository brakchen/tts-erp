from datetime import UTC, datetime, timedelta
from uuid import uuid4

from tts_erp_v2.db.models.publishing import VideoPublishTask
from tts_erp_v2.publishing.domain import TaskStage, TaskStatus
from tts_erp_v2.publishing.repository import _lease_task, claim_one


def _task() -> VideoPublishTask:
    return VideoPublishTask(
        public_id=uuid4(),
        client_request_id=uuid4(),
        caption="TEST_caption",
        original_filename="TEST_video.mp4",
        content_type="video/mp4",
        size_bytes=4,
        object_bucket="tiktok-video",
        object_key=f"TEST/{uuid4()}.mp4",
        status=TaskStatus.PENDING.value,
        stage=TaskStage.QUEUED.value,
        target_device_serial="TEST_device",
        target_app_package="com.tiktok",
        queued_at=datetime.now(UTC),
    )


def test_global_claim_and_expired_lease_takeover(db_session) -> None:
    first = _task()
    second = _task()
    db_session.add_all([first, second])
    db_session.flush()

    claimed = claim_one(db_session, "worker-a")
    assert claimed is not None
    assert claimed.public_id == first.public_id
    db_session.commit()

    assert claim_one(db_session, "worker-b") is None

    first.lease_expires_at = datetime.now(UTC) - timedelta(seconds=1)
    db_session.commit()
    takeover = _lease_task(db_session, "worker-b", 30)
    assert takeover is not None
    assert takeover.public_id == first.public_id
    assert takeover.lease_owner == "worker-b"
    db_session.rollback()
