"""Agent 3 (Wave 3) verification probes — batch 3A + the deadlock question.

Read-only with respect to product code. Every probe asserts the contract that
MUST hold after the Wave 3 batch-3A fix (allow retry, keep the downstream gate
effective). A failure here is evidence of a defect; it never documents buggy
behaviour as correct.

Naming: probes are prefixed ``test_a3w3_`` so a reviewer can filter them.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

pytestmark = [pytest.mark.domain_publishing]

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from tts_erp_v2.api import deps
from tts_erp_v2.api.v2 import video_publish
from tts_erp_v2.db.models.publishing import (
    PublishWorkerHeartbeat,
    VideoPublishAttempt,
    VideoPublishTask,
)
from tts_erp_v2.publishing.domain import allowed_actions
from tts_erp_v2.publishing.repository import (
    CleanupClaimRequest,
    claim_cleanup_work,
    claim_one,
    finish_cleanup_work,
    has_pending_device_cleanup,
)

OWNER_KEY = "key-a"


class _PresentStore:
    """Object store where the uploaded object is present."""

    bucket = "tiktok-video"

    def stat(self, key: str) -> dict:
        return {"size": 4, "etag": "TEST_etag"}

    def presign_put(self, key: str, content_type: str) -> str:
        return f"https://upload.test/{key}"


def _app(db_session: Session, store: _PresentStore) -> FastAPI:
    app = FastAPI()
    app.include_router(video_publish.router)

    @app.middleware("http")
    async def _auth(request: Request, call_next):
        request.scope["auth_method"] = "bearer"
        request.scope["api_key_role"] = "readwrite"
        request.scope["api_key_hash"] = OWNER_KEY
        return await call_next(request)

    app.dependency_overrides[deps.get_session] = lambda: db_session
    app.dependency_overrides[video_publish.get_session] = lambda: db_session
    app.dependency_overrides[video_publish.get_store] = lambda: store
    return app


@pytest.fixture()
def client(db_session: Session):
    db_session.add(
        PublishWorkerHeartbeat(
            instance_id="TEST-a3w3-worker",
            hostname="TEST-a3w3-host",
            pid=2424,
            status="ready",
            device_status="ready",
            started_at=datetime.now(UTC),
            heartbeat_at=datetime.now(UTC),
        )
    )
    db_session.commit()
    with TestClient(_app(db_session, _PresentStore()), raise_server_exceptions=False) as c:
        yield c


def _factory(db_session: Session):
    def factory():
        return Session(
            bind=db_session.get_bind(), join_transaction_mode="create_savepoint"
        )

    return factory


def _seed_failed(
    db_session: Session,
    *,
    device_cleanup_status: str = "not_started",
    object_cleanup_status: str = "not_started",
    spool_cleanup_status: str = "not_started",
    publish_budget_used: int = 1,
    object_uploaded: bool = True,
    object_deleted: bool = False,
    retry_safe: bool = True,
) -> VideoPublishTask:
    """A terminal failed task that is otherwise retryable."""
    now = datetime.now(UTC)
    task = VideoPublishTask(
        public_id=uuid4(),
        client_request_id=uuid4(),
        created_by_key_hash=OWNER_KEY,
        caption="TEST_caption",
        original_filename="TEST_a3w3_video.mp4",
        content_type="video/mp4",
        size_bytes=4,
        object_bucket="tiktok-video",
        object_key=f"TEST/a3w3/{uuid4()}.mp4",
        status="failed",
        stage="done",
        # video_publish_task_cleanup_owner_check: 'none' is only legal while every
        # *_cleanup_status is outside pending/failed. A terminal task holding
        # outstanding cleanup must carry 'preserve_state'.
        cleanup_intent=(
            "preserve_state"
            if any(
                s in {"pending", "failed"}
                for s in (
                    device_cleanup_status,
                    object_cleanup_status,
                    spool_cleanup_status,
                )
            )
            else "none"
        ),
        target_device_serial="TEST_device",
        target_app_package="com.zhiliaoapp.musically",
        device_path="/sdcard/Movies/TEST/a3w3.mp4",
        device_cleanup_status=device_cleanup_status,
        device_cleanup_error=(
            "TEST_device_cleanup_failed" if device_cleanup_status == "failed" else None
        ),
        object_cleanup_status=object_cleanup_status,
        spool_cleanup_status=spool_cleanup_status,
        attempt_count=max(publish_budget_used, 1),
        publish_budget_used=publish_budget_used,
        queued_at=now - timedelta(minutes=5),
        object_uploaded_at=now - timedelta(minutes=4) if object_uploaded else None,
        object_deleted_at=now - timedelta(minutes=1) if object_deleted else None,
        object_etag="TEST_etag",
    )
    db_session.add(task)
    db_session.flush()
    db_session.add(
        VideoPublishAttempt(
            task_id=task.id,
            sequence_no=1,
            kind="publish",
            status="failed",
            artemis_session_id=uuid4(),
            prompt_version="TEST",
            prompt_snapshot="TEST",
            device_serial="TEST_device",
            artemis_profile="TEST",
            artemis_verification_level="TEST_strict",
            retry_safe=retry_safe,
        )
    )
    db_session.commit()
    return task


def _reload(db_session: Session, public_id) -> VideoPublishTask:
    db_session.rollback()
    fresh = db_session.scalar(
        select(VideoPublishTask).where(VideoPublishTask.public_id == public_id)
    )
    assert fresh is not None, "task row disappeared"
    return fresh


def _detail(response) -> str:
    try:
        return str(response.json().get("detail"))
    except ValueError:
        return response.text[:400]


def _retry(client: TestClient, task: VideoPublishTask, row_version: int):
    return client.post(
        f"/v2/video-publish/tasks/{task.public_id}/retry",
        json={"rowVersion": row_version},
        headers={"X-Test-Key": OWNER_KEY},
    )


# ───────────────────────── batch 3A: the retry contract ─────────────────────────
# Contract under test (design §10.7 "failed(可重试) → 重试原任务"):
# a failed task whose *device* cleanup failed can be retried; the downstream
# claim/dispatch gates keep it out of execution until cleanup resolves.


def test_a3w3_a_retry_succeeds_when_device_cleanup_failed(
    client: TestClient, db_session: Session
):
    task = _seed_failed(db_session, device_cleanup_status="failed")
    response = _retry(client, task, task.row_version)
    assert response.status_code == 200, (
        f"expected 200, got {response.status_code}: {_detail(response)}"
    )
    after = _reload(db_session, task.public_id)
    assert after.status == "pending", f"status={after.status}"
    assert after.stage == "queued", f"stage={after.stage}"


def test_a3w3_b_requeued_task_keeps_cleanup_intent_requeue_publish(
    client: TestClient, db_session: Session
):
    """(b) The gate must still SEE the task after it is requeued.

    ``has_pending_device_cleanup`` keys off ``cleanup_intent != 'none'``. If the
    requeue wiped the intent to 'none', the task would become claimable while the
    device is still dirty.
    """
    task = _seed_failed(db_session, device_cleanup_status="failed")
    response = _retry(client, task, task.row_version)
    assert response.status_code == 200, (
        f"expected 200, got {response.status_code}: {_detail(response)}"
    )
    after = _reload(db_session, task.public_id)
    assert after.cleanup_intent == "requeue_publish", (
        f"cleanup_intent={after.cleanup_intent!r}; 'none' would erase the "
        "gate's precondition and let a dirty device publish"
    )


def test_a3w3_c_allowed_actions_still_offers_retry(
    client: TestClient, db_session: Session
):
    """(c) The retry button must be visible in this state."""
    task = _seed_failed(db_session, device_cleanup_status="failed")
    after = _reload(db_session, task.public_id)
    names = {action.name for action in allowed_actions(after)}
    assert "RETRY" in names, f"allowed_actions={sorted(names)}"


def test_a3w3_d_gate_still_blocks_claim_after_requeue(
    client: TestClient, db_session: Session
):
    """(d) CORE GUARD. After the requeue the claim gate must refuse the task.

    If ``claim_one`` returns a task here, the device is dirty and a new video
    would still be pushed to it -- that is the real vulnerability this batch
    is about.
    """
    task = _seed_failed(db_session, device_cleanup_status="failed")
    response = _retry(client, task, task.row_version)
    assert response.status_code == 200, (
        f"expected 200, got {response.status_code}: {_detail(response)}"
    )
    after = _reload(db_session, task.public_id)
    assert has_pending_device_cleanup(db_session) is True, (
        "has_pending_device_cleanup() went False: the gate is blind to the task"
    )
    claimed = claim_one(db_session, "TEST-a3w3-worker")
    assert claimed is None, (
        f"claim_one returned {getattr(claimed, 'public_id', claimed)!r}: the dirty "
        "device would receive a new publish"
    )


def test_a3w3_d2_gate_blocks_claim_for_manual_requeue_state(
    db_session: Session
):
    """(d) gate behaviour on the post-fix state, independent of the HTTP path.

    Constructed directly so the guard is verified even if the endpoint regresses.
    """
    task = _seed_failed(db_session, device_cleanup_status="failed")
    fresh = _reload(db_session, task.public_id)
    fresh.status = "pending"
    fresh.stage = "queued"
    fresh.cleanup_intent = "requeue_publish"
    fresh.device_cleanup_status = "failed"
    db_session.commit()
    assert has_pending_device_cleanup(db_session) is True
    assert claim_one(db_session, "TEST-a3w3-worker") is None


def test_a3w3_f_requeue_publish_branch_satisfies_db_check(
    client: TestClient, db_session: Session
):
    """(f) The persisted row must satisfy video_publish_task_cleanup_owner_check.

    The commit in retry_task would raise IntegrityError (→409) otherwise, so a
    200 here already proves the CHECK accepted the branch; assert it explicitly
    by reading the row back in a fresh session.
    """
    task = _seed_failed(db_session, device_cleanup_status="failed")
    response = _retry(client, task, task.row_version)
    assert response.status_code == 200, (
        f"expected 200, got {response.status_code}: {_detail(response)}"
    )
    with _factory(db_session)() as verify_session:
        row = verify_session.scalar(
            select(VideoPublishTask).where(VideoPublishTask.public_id == task.public_id)
        )
        assert row is not None
        verify_session.commit()  # re-validates every CHECK constraint
        assert (row.status, row.stage, row.cleanup_intent) == (
            "pending",
            "queued",
            "requeue_publish",
        )


# ───────────────────── batch 3A: existing behaviour must not regress ───────────


@pytest.mark.parametrize("object_cleanup_status", ["pending", "failed"])
def test_a3w3_e1_object_cleanup_still_blocks_retry(
    client: TestClient, db_session: Session, object_cleanup_status: str
):
    task = _seed_failed(
        db_session, object_cleanup_status=object_cleanup_status
    )
    response = _retry(client, task, task.row_version)
    assert response.status_code == 409, (
        f"expected 409 for object_cleanup_status={object_cleanup_status}, "
        f"got {response.status_code}: {_detail(response)}"
    )
    assert "OBJECT_CLEANUP_IN_PROGRESS" in _detail(response), _detail(response)


def test_a3w3_e2_retry_budget_exhausted_still_blocks(
    client: TestClient, db_session: Session
):
    task = _seed_failed(db_session, publish_budget_used=3)
    response = _retry(client, task, task.row_version)
    assert response.status_code == 409, (
        f"expected 409, got {response.status_code}: {_detail(response)}"
    )
    assert "RETRY_BUDGET_EXHAUSTED" in _detail(response), _detail(response)


def test_a3w3_e3_deleted_object_still_requires_replacement(
    client: TestClient, db_session: Session
):
    task = _seed_failed(db_session, object_deleted=True)
    response = _retry(client, task, task.row_version)
    assert response.status_code == 409, (
        f"expected 409, got {response.status_code}: {_detail(response)}"
    )
    assert "UPLOAD_REPLACEMENT_REQUIRED" in _detail(response), _detail(response)


def test_a3w3_e4_replace_upload_keeps_its_own_cleanup_gate(
    client: TestClient, db_session: Session
):
    """replace_upload has its own gate; it must NOT be relaxed by 3A."""
    task = _seed_failed(
        db_session, device_cleanup_status="failed", object_deleted=True
    )
    response = client.post(
        f"/v2/video-publish/tasks/{task.public_id}/replace-upload",
        json={"rowVersion": task.row_version},
        headers={"X-Test-Key": OWNER_KEY},
    )
    assert response.status_code == 409, (
        f"expected 409, got {response.status_code}: {_detail(response)}"
    )
    assert "CLEANUP_REQUIRED" in _detail(response), _detail(response)


@pytest.mark.parametrize(
    ("status", "stage", "expect"),
    [
        ("pending", "awaiting_upload", {"CONTINUE_UPLOAD", "CANCEL", "VIEW"}),
        ("pending", "queued", {"CANCEL", "VIEW"}),
        ("pending", "waiting_device", {"CANCEL", "VIEW"}),
        ("running", "verifying", {"VIEW"}),
        ("succeeded", "done", {"VIEW"}),
        ("cancelled", "done", {"VIEW"}),
    ],
)
def test_a3w3_e5_allowed_actions_state_matrix(
    db_session: Session, status: str, stage: str, expect: set[str]
):
    now = datetime.now(UTC)
    task = VideoPublishTask(
        public_id=uuid4(),
        client_request_id=uuid4(),
        created_by_key_hash=OWNER_KEY,
        caption="TEST_caption",
        original_filename="TEST_a3w3_video.mp4",
        content_type="video/mp4",
        size_bytes=4,
        object_bucket="tiktok-video",
        object_key=f"TEST/a3w3/{uuid4()}.mp4",
        object_generation=uuid4(),
        status=status,
        stage=stage,
        cleanup_intent="none",
        target_device_serial="TEST_device",
        target_app_package="com.zhiliaoapp.musically",
        device_path="/sdcard/Movies/TEST/a3w3.mp4",
        attempt_count=1,
        publish_budget_used=1,
        queued_at=now - timedelta(minutes=5),
    )
    db_session.add(task)
    db_session.commit()
    fresh = _reload(db_session, task.public_id)
    names = {action.name for action in allowed_actions(fresh)}
    assert names == expect, f"status={status} stage={stage} -> {sorted(names)}"


# ──────────────────────────── deadlock question ────────────────────────────────
# Scenario from the brief: A fails device cleanup → retry A → A becomes
# pending/queued + requeue_publish. Can A still be claimed? If nobody repairs
# the cleanup, does A ever leave the queue?


def test_a3w3_deadlock_requeued_task_is_released_after_cleanup_succeeds(
    db_session: Session
):
    """Empirical answer: the requeued task is released once cleanup succeeds.

    Steps mirror the brief exactly and drive the REAL background cleanup claim
    path (``_background_cleanup_loop`` → claim_cleanup_work(scope='device') →
    finish_cleanup_work), so this is the production compensation route, not a
    hand-written state poke.
    """
    task = _seed_failed(db_session, device_cleanup_status="failed")
    fresh = _reload(db_session, task.public_id)
    fresh.status = "pending"
    fresh.stage = "queued"
    fresh.cleanup_intent = "requeue_publish"
    fresh.device_cleanup_status = "failed"
    fresh.device_cleanup_next_attempt_at = None  # due immediately
    db_session.commit()

    # 1. Gate holds: not claimable while device cleanup is failed.
    assert has_pending_device_cleanup(db_session) is True
    assert claim_one(db_session, "TEST-a3w3-worker") is None

    # 2. Background cleanup picks the row up on its own (no operator action).
    work = claim_cleanup_work(
        _factory(db_session), CleanupClaimRequest("cleanup-worker", 30, "device")
    )
    assert work is not None, "background cleanup never claimed the requeued task"
    assert work.resources == ("device",), f"resources={work.resources}"

    # 3. Cleanup succeeds → intent cleared, gate opens.
    outcome = finish_cleanup_work(_factory(db_session), work.token, {"device": None})
    assert outcome.cleanup_intent == "none", (
        f"cleanup_intent stayed {outcome.cleanup_intent!r}: the task would never "
        "leave the queue"
    )
    assert (outcome.task_status, outcome.task_stage) == ("pending", "queued"), (
        f"status={outcome.task_status} stage={outcome.task_stage}"
    )

    # 4. Now, and only now, the task is claimable.
    assert has_pending_device_cleanup(db_session) is False
    claimed = claim_one(db_session, "TEST-a3w3-worker")
    assert claimed is not None, "task never became claimable after cleanup succeeded"
    assert claimed.public_id == task.public_id


def test_a3w3_deadlock_cleanup_failure_requeues_to_waiting_device(
    db_session: Session
):
    """If cleanup keeps failing, the task parks in waiting_device (not lost)."""
    task = _seed_failed(db_session, device_cleanup_status="failed")
    fresh = _reload(db_session, task.public_id)
    fresh.status = "pending"
    fresh.stage = "queued"
    fresh.cleanup_intent = "requeue_publish"
    fresh.device_cleanup_status = "failed"
    fresh.device_cleanup_next_attempt_at = None
    db_session.commit()

    work = claim_cleanup_work(
        _factory(db_session), CleanupClaimRequest("cleanup-worker", 30, "device")
    )
    assert work is not None
    outcome = finish_cleanup_work(
        _factory(db_session), work.token, {"device": "TEST_adb_offline"}
    )
    assert (outcome.task_status, outcome.task_stage) == (
        "pending",
        "waiting_device",
    ), f"status={outcome.task_status} stage={outcome.task_stage}"
    assert outcome.cleanup_intent == "requeue_publish", outcome.cleanup_intent
    assert claim_one(db_session, "TEST-a3w3-worker") is None