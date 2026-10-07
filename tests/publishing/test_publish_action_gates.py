"""Agent 3 verification probes for Wave 1 (P1-1 + retry regression net).

These are *verification artifacts*, not product tests: every probe asserts the
contract that MUST hold after the Wave 1 fix. A failure here is evidence of a
defect; it never documents the buggy behaviour as correct.

Scope: read-only with respect to product code. Probes only build rows and call
``GET/POST /v2/video-publish/tasks/{id}`` through the real router.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

pytestmark = [pytest.mark.domain_publishing]
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from tts_erp_v2.api import deps
from tts_erp_v2.api.v2 import video_publish
from tts_erp_v2.db.models.publishing import (
    PublishWorkerHeartbeat,
    VideoPublishAttempt,
    VideoPublishTask,
)
from tts_erp_v2.publishing import submission as submission_module
from tts_erp_v2.publishing.domain import allowed_actions
from tts_erp_v2.publishing.repository import claim_one, has_pending_device_cleanup

# Owner key hash used by the "key-a" caller; "key-b" is a foreign actor.
OWNER_KEY = "key-a"
FOREIGN_KEY = "key-b"


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
        if request.headers.get("X-Test-Cookie"):
            request.scope["auth_method"] = "cookie"
            request.scope["api_key_role"] = "readwrite"
            request.scope["user_id"] = 1
        else:
            request.scope["auth_method"] = "bearer"
            request.scope["api_key_role"] = "readwrite"
            request.scope["api_key_hash"] = request.headers.get("X-Test-Key", OWNER_KEY)
        return await call_next(request)

    app.dependency_overrides[deps.get_session] = lambda: db_session
    app.dependency_overrides[video_publish.get_session] = lambda: db_session
    app.dependency_overrides[video_publish.get_store] = lambda: store
    return app


def _heartbeat(db_session: Session) -> None:
    db_session.add(
        PublishWorkerHeartbeat(
            instance_id="TEST-a3-worker",
            hostname="TEST-a3-host",
            pid=2424,
            status="ready",
            device_status="ready",
            started_at=datetime.now(UTC),
            heartbeat_at=datetime.now(UTC),
        )
    )
    db_session.commit()


@pytest.fixture()
def client(db_session: Session):
    _heartbeat(db_session)
    with TestClient(_app(db_session, _PresentStore()), raise_server_exceptions=False) as c:
        yield c


@pytest.fixture()
def strict_client(db_session: Session):
    """Client that re-raises server-side exceptions (to name the exception type)."""
    _heartbeat(db_session)
    with TestClient(_app(db_session, _PresentStore()), raise_server_exceptions=True) as c:
        yield c


def _seed(
    db_session: Session,
    *,
    status: str = "failed",
    stage: str = "done",
    cleanup_intent: str = "none",
    device_cleanup_status: str = "not_started",
    object_cleanup_status: str = "not_started",
    spool_cleanup_status: str = "not_started",
    publish_budget_used: int = 1,
    retry_safe: bool | None = True,
    object_uploaded: bool = True,
    object_deleted: bool = False,
    owner_key: str = OWNER_KEY,
    cleanup_lease_owner: str | None = None,
) -> VideoPublishTask:
    now = datetime.now(UTC)
    task = VideoPublishTask(
        public_id=uuid4(),
        client_request_id=uuid4(),
        created_by_key_hash=owner_key,
        caption="TEST_caption",
        original_filename="TEST_a3_video.mp4",
        content_type="video/mp4",
        size_bytes=4,
        object_bucket="tiktok-video",
        object_key=f"TEST/a3/{uuid4()}.mp4",
        status=status,
        stage=stage,
        cleanup_intent=cleanup_intent,
        target_device_serial="TEST_device",
        target_app_package="com.zhiliaoapp.musically",
        device_path="/sdcard/Movies/TEST/a3.mp4",
        device_cleanup_status=device_cleanup_status,
        object_cleanup_status=object_cleanup_status,
        spool_cleanup_status=spool_cleanup_status,
        device_cleanup_error=(
            "TEST_device_cleanup_failed" if device_cleanup_status == "failed" else None
        ),
        cleanup_lease_owner=cleanup_lease_owner,
        # video_publish_task_budget_check: publish_budget_used <= attempt_count
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


def _post(
    client: TestClient,
    task: VideoPublishTask,
    *,
    row_version,
    key: str | None = None,
    headers: dict | None = None,
):
    hdrs = dict(headers or {})
    if key is not None:
        hdrs["X-Test-Key"] = key
    return client.post(
        f"/v2/video-publish/tasks/{task.public_id}/retry",
        json={"rowVersion": row_version},
        headers=hdrs,
    )


def _explain(label: str, response) -> str:
    try:
        detail = response.json().get("detail")
    except ValueError:  # pragma: no cover - diagnostics only
        detail = None
    return (
        f"\n[probe:{label}]\n"
        f"  status_code = {response.status_code}\n"
        f"  detail      = {detail!r}\n"
        f"  body        = {response.text[:600]}"
    )


def _row_state(db_session: Session, task_id) -> str:
    """Read the persisted row back; rollback first because a 500 leaves the
    session in PendingRollbackError state."""
    db_session.rollback()
    fresh = db_session.scalar(
        select(VideoPublishTask).where(VideoPublishTask.public_id == task_id)
    )
    if fresh is None:
        return "row missing"
    return (
        f"status={fresh.status} stage={fresh.stage} "
        f"cleanup_intent={fresh.cleanup_intent} "
        f"device_cleanup_status={fresh.device_cleanup_status} "
        f"object_cleanup_status={fresh.object_cleanup_status} "
        f"row_version={fresh.row_version}"
    )


def _row_snapshot(db_session: Session, task_id) -> dict:
    """Full before/after comparable snapshot of the task row."""
    db_session.rollback()
    fresh = db_session.scalar(
        select(VideoPublishTask).where(VideoPublishTask.public_id == task_id)
    )
    if fresh is None:
        return {"__missing__": True}
    return {
        "status": fresh.status,
        "stage": fresh.stage,
        "cleanup_intent": fresh.cleanup_intent,
        "device_cleanup_status": fresh.device_cleanup_status,
        "spool_cleanup_status": fresh.spool_cleanup_status,
        "object_cleanup_status": fresh.object_cleanup_status,
        "row_version": fresh.row_version,
    }


def _p1_1_task(db_session: Session) -> VideoPublishTask:
    """The exact P1-1 trigger state from the task brief."""
    return _seed(
        db_session,
        status="failed",
        stage="done",
        cleanup_intent="preserve_state",
        device_cleanup_status="failed",
        object_cleanup_status="not_started",
        spool_cleanup_status="not_started",
        publish_budget_used=1,
        retry_safe=True,
        object_uploaded=True,
        object_deleted=False,
    )


# ── P1-1 (a) ORIGINAL BUG REPRODUCTION ───────────────────────────────────────
# This test documents the PRE-FIX symptom. It asserts the old HTTP 500, which
# no longer happens after the Wave 1 fix, so it is a non-strict xfail: post-fix
# it reports XPASS instead of failing. It exists purely as the "原问题" evidence
# and is deliberately kept separate from the contract assertion below.
@pytest.mark.xfail(
    strict=False,
    reason=(
        "P1-1 原问题复现：修复前该状态 retry 必然 500"
        "（IntegrityError/CheckViolation on video_publish_task_cleanup_owner_check）。"
        "修复后应为 409，故此处 XPASS。"
    ),
)
def test_p1_1_repro_pre_fix_retry_500(client, db_session: Session) -> None:
    task = _p1_1_task(db_session)
    response = _post(client, task, row_version=task.row_version)
    print(f"\n[probe:P1-1-repro] status={response.status_code} body={response.text[:200]}")
    assert response.status_code == 500, _explain("P1-1-repro", response)


def test_p1_1_repro_pre_fix_unhandled_exception(strict_client, db_session: Session) -> None:
    """Names the exception that escaped the retry handler pre-fix.

    Post-fix nothing escapes, so this asserts the *absence* of an unhandled
    server-side exception — the same probe is the pre-fix evidence because it
    reports the escaped exception type in its failure message.
    """
    task = _p1_1_task(db_session)
    try:
        response = _post(strict_client, task, row_version=task.row_version)
    except Exception as exc:  # the evidence we want to record
        raise AssertionError(
            f"\n[probe:P1-1-exception] unhandled {type(exc).__module__}.{type(exc).__name__}: "
            f"{str(exc)[:400]}\n  row: {_row_state(db_session, task.public_id)}"
        ) from exc
    print(f"\n[probe:P1-1-exception] no exception; status={response.status_code}")
    assert response.status_code < 500, _explain("P1-1-exception", response)


# ── P1-1 (b) POST-FIX CONTRACT (must pass) ──────────────────────────────────
def test_p1_1_retry_contract_requeues_when_device_cleanup_unresolved(
    client, db_session: Session
) -> None:
    """Batch 3A contract (owner-ruled): retry is ALLOWED while device cleanup is
    unresolved; the readiness gate downstream holds the task, not the API.

    ``docs/design`` §10.7 lists ``failed（可重试）| 重试原任务`` with no cleanup
    carve-out, and §14 step 8 states device-cleanup failure forms a readiness
    gate *before the next staging* — a runtime gate, not an API refusal. The
    Wave 1 two-step ('retry is impossible until retry_cleanup succeeds') is
    withdrawn.
    """
    task = _p1_1_task(db_session)
    public_id = task.public_id
    before = _row_snapshot(db_session, public_id)
    assert not before.get("__missing__")

    response = _post(client, task, row_version=before["row_version"])
    print(f"\n[probe:3A-retry] status={response.status_code} body={response.text[:300]}")

    assert response.status_code == 200, _explain("3A-retry", response)
    body = response.json()
    assert body["status"] == "pending", _explain("3A-retry", response)
    assert body["stage"] == "queued", _explain("3A-retry", response)

    after = _row_snapshot(db_session, public_id)
    print(f"[probe:3A-retry] before={before}\n[probe:3A-retry] after={after}")
    # The requeue must NOT erase the device-cleanup-outstanding marker, and it
    # must not fake a clean device: the readiness gate keys off both.
    assert after["cleanup_intent"] == "requeue_publish", (
        "requeue must keep the cleanup-gated marker; writing 'none' would hide "
        f"the dirty device from the gate: {after}"
    )
    assert after["device_cleanup_status"] == "failed", (
        f"retry must not silently mark the device clean: {after}"
    )


def test_p1_1_retry_cleanup_still_available_same_state(client, db_session: Session) -> None:
    """Control: the operator keeps BOTH exits while cleanup is failing.

    Under 3A the publish task is allowed to be re-queued *and* the cleanup
    command stays available; neither command blocks the other.
    """
    task = _p1_1_task(db_session)
    public_id = task.public_id

    listing = client.get(f"/v2/video-publish/tasks/{public_id}")
    assert listing.status_code == 200, _explain("P1-1-outlet-list", listing)
    offered = listing.json()["allowedActions"]
    print(f"\n[probe:P1-1-outlet] allowedActions={sorted(offered)}")
    assert "retry" in offered, sorted(offered)
    assert "retry_cleanup" in offered, (
        f"operator must keep an exit while cleanup is failing: {sorted(offered)}"
    )

    response = client.post(
        f"/v2/video-publish/tasks/{public_id}/cleanup/retry",
        json={"rowVersion": task.row_version, "resources": ["device"]},
    )
    assert response.status_code == 200, _explain("P1-1-outlet", response)
    after = _row_snapshot(db_session, public_id)
    print(f"[probe:P1-1-outlet] device_cleanup_status={after['device_cleanup_status']}")
    assert after["device_cleanup_status"] != "failed", (
        f"retry_cleanup must clear the failed device cleanup: {after}"
    )


# ── Regression net: retry success path ──────────────────────────────────────
def test_retry_success_path_requeues_clean_failed_task(client, db_session: Session) -> None:
    task = _seed(db_session, status="failed", stage="done", cleanup_intent="none")
    response = _post(client, task, row_version=task.row_version)
    assert response.status_code == 200, _explain("R-retry-success", response)
    body = response.json()
    assert body["status"] == "pending", _explain("R-retry-success", response)
    assert body["stage"] == "queued", _explain("R-retry-success", response)
    assert body["queuePosition"] == 1, _explain("R-retry-success", response)
    assert "retry" not in body["allowedActions"], _explain("R-retry-success", response)


# ── Regression net: retry guard rails ───────────────────────────────────────
@pytest.mark.parametrize("cleanup_status", ["pending", "failed"])
def test_retry_blocked_by_object_cleanup(
    client, db_session: Session, cleanup_status: str
) -> None:
    task = _seed(
        db_session,
        cleanup_intent="preserve_state",
        object_cleanup_status=cleanup_status,
    )
    response = _post(client, task, row_version=task.row_version)
    assert response.status_code == 409, _explain(f"R-cleanup-{cleanup_status}", response)
    assert response.json()["detail"]["code"] == "OBJECT_CLEANUP_IN_PROGRESS", _explain(
        f"R-cleanup-{cleanup_status}", response
    )


def test_retry_blocked_by_live_cleanup_lease(client, db_session: Session) -> None:
    task = _seed(
        db_session,
        cleanup_intent="preserve_state",
        cleanup_lease_owner="TEST-cleaner",
    )
    response = _post(client, task, row_version=task.row_version)
    assert response.status_code == 409, _explain("R-lease", response)
    assert response.json()["detail"]["code"] in {
        "OBJECT_CLEANUP_IN_PROGRESS",
        "CLEANUP_REQUIRED",
    }, _explain("R-lease", response)


def test_retry_blocked_when_budget_exhausted(client, db_session: Session) -> None:
    task = _seed(db_session, cleanup_intent="none", publish_budget_used=3)
    response = _post(client, task, row_version=task.row_version)
    assert response.status_code == 409, _explain("R-budget", response)
    assert response.json()["detail"]["code"] == "RETRY_BUDGET_EXHAUSTED", _explain(
        "R-budget", response
    )


def test_retry_requires_replacement_when_object_deleted(client, db_session: Session) -> None:
    task = _seed(db_session, cleanup_intent="none", object_deleted=True)
    response = _post(client, task, row_version=task.row_version)
    assert response.status_code == 409, _explain("R-deleted", response)
    assert response.json()["detail"]["code"] == "UPLOAD_REPLACEMENT_REQUIRED", _explain(
        "R-deleted", response
    )


def test_retry_rejects_unsafe_latest_attempt(client, db_session: Session) -> None:
    task = _seed(db_session, cleanup_intent="none", retry_safe=False)
    response = _post(client, task, row_version=task.row_version)
    assert response.status_code == 409, _explain("R-unsafe", response)
    assert response.json()["detail"]["code"] == "TASK_RETRY_NOT_SAFE", _explain(
        "R-unsafe", response
    )


def test_retry_rejects_non_failed_status(client, db_session: Session) -> None:
    task = _seed(db_session, status="needs_review", stage="done", cleanup_intent="none")
    response = _post(client, task, row_version=task.row_version)
    assert response.status_code == 409, _explain("R-status", response)
    assert response.json()["detail"]["code"] == "TASK_ACTION_NOT_ALLOWED", _explain(
        "R-status", response
    )


# ── Regression net: transport / auth guards ────────────────────────────────
def test_retry_requires_csrf_header_for_cookie_auth(client, db_session: Session) -> None:
    task = _seed(db_session, cleanup_intent="none")
    response = client.post(
        f"/v2/video-publish/tasks/{task.public_id}/retry",
        json={"rowVersion": task.row_version},
        headers={"X-Test-Cookie": "1"},
    )
    assert response.status_code == 403, _explain("R-csrf", response)
    assert response.json()["detail"]["code"] == "CSRF_HEADER_REQUIRED", _explain(
        "R-csrf", response
    )


def test_retry_hides_foreign_task(client, db_session: Session) -> None:
    task = _seed(db_session, cleanup_intent="none", owner_key=OWNER_KEY)
    response = _post(client, task, row_version=task.row_version, key=FOREIGN_KEY)
    assert response.status_code == 404, _explain("R-foreign", response)
    assert response.json()["detail"]["code"] == "TASK_NOT_FOUND", _explain("R-foreign", response)


def test_retry_rejects_stale_row_version(client, db_session: Session) -> None:
    task = _seed(db_session, cleanup_intent="none")
    response = _post(client, task, row_version=task.row_version + 99)
    assert response.status_code == 409, _explain("R-version", response)
    assert response.json()["detail"]["code"] == "TASK_VERSION_CONFLICT", _explain(
        "R-version", response
    )


# ── Regression net: allowedActions matrix (6 contracted states) ────────────
def test_allowed_actions_matrix(client, db_session: Session) -> None:
    """Freeze the allowedActions set for the six contracted task states."""
    cases: list[tuple[str, dict, set[str]]] = [
        (
            "pending/awaiting_upload",
            {"status": "pending", "stage": "awaiting_upload", "cleanup_intent": "none"},
            {"view", "continue_upload", "cancel", "copy_artemis_id"},
        ),
        (
            "pending/queued",
            {"status": "pending", "stage": "queued", "cleanup_intent": "none"},
            {"view", "cancel", "copy_artemis_id"},
        ),
        (
            "failed",
            {"status": "failed", "stage": "done", "cleanup_intent": "none"},
            {"view", "retry", "copy_artemis_id"},
        ),
        (
            "needs_review",
            {"status": "needs_review", "stage": "done", "cleanup_intent": "none"},
            {"view", "verify", "copy_artemis_id"},
        ),
        (
            "succeeded",
            {"status": "succeeded", "stage": "done", "cleanup_intent": "none"},
            {"view", "copy_artemis_id"},
        ),
        (
            "cancelled",
            {
                "status": "cancelled",
                "stage": "done",
                "cleanup_intent": "preserve_state",
                "object_cleanup_status": "failed",
            },
            {"view", "retry_cleanup", "copy_artemis_id"},
        ),
    ]
    failures: list[str] = []
    for label, kwargs, expected in cases:
        task = _seed(db_session, **kwargs)
        response = client.get(f"/v2/video-publish/tasks/{task.public_id}")
        if response.status_code != 200:
            failures.append(f"{label}: HTTP {response.status_code} {response.text[:200]}")
            continue
        actual = set(response.json()["allowedActions"])
        if actual != expected:
            failures.append(
                f"{label}: expected={sorted(expected)} actual={sorted(actual)}"
            )
    assert not failures, "\n[probe:R-actions-matrix]\n  " + "\n  ".join(failures)


def test_retry_action_still_offered_while_device_cleanup_failed(
    db_session: Session,
) -> None:
    """Batch 3A: retry stays VISIBLE while device cleanup is unresolved.

    object_cleanup_status='pending' alone still suppresses retry, so this probe
    isolates the DEVICE-cleanup case.
    """
    task = _seed(
        db_session,
        cleanup_intent="preserve_state",
        device_cleanup_status="failed",
        object_cleanup_status="not_started",
    )
    actions = {item.value for item in allowed_actions(task)}
    print(f"\n[probe:actions-device-failed] {sorted(actions)}")
    assert "retry" in actions, (
        f"retry must stay offered so the operator is not forced into a two-step "
        f"retry_cleanup -> retry dance: {sorted(actions)}"
    )
    assert "replace_upload" not in actions, sorted(actions)
    # The cleanup exit stays available too: cleanup_retryable_resources surfaces
    # terminal-failed resources.
    assert "retry_cleanup" in actions, sorted(actions)


# ── Owner-ordered correction: the IntegrityError fallback must not masquerade ──
def test_integrity_error_fallback_uses_a_dedicated_code(
    client, db_session: Session, monkeypatch
) -> None:
    """Supervisor ruling: the IntegrityError fallback in ``retry`` is defence in
    depth, so it must NOT report ``CLEANUP_REQUIRED`` — that code would blame
    cleanup for any future constraint conflict and mislead triage.

    The guard added to ``retry_task`` makes this path unreachable in normal
    operation, so the probe forces it by making ``queue_task`` raise.
    """
    task = _seed(db_session, cleanup_intent="none")

    def boom(*_a, **_k):
        raise IntegrityError("UPDATE publishing.video_publish_tasks", {}, Exception("probe"))

    monkeypatch.setattr(submission_module, "queue_task", boom)
    response = _post(client, task, row_version=task.row_version)
    assert response.status_code == 409, _explain("R-fallback", response)
    code = response.json()["detail"]["code"]
    print(f"\n[probe:R-fallback] status={response.status_code} code={code}")
    assert code != "CLEANUP_REQUIRED", (
        f"fallback must not reuse CLEANUP_REQUIRED (got {code!r}); "
        "a non-cleanup constraint conflict would be misreported as a cleanup problem"
    )


# ═══════════════════════════════════════════════════════════════════════════
# Wave 2 probes (Agent 3). These assert the contract that MUST hold after the
# Wave 2 fix. A failure here is evidence of a defect, never a record of buggy
# behaviour as correct.
# ═══════════════════════════════════════════════════════════════════════════


def _seed_running(db_session: Session, **kw) -> VideoPublishTask:
    """A task the worker holds: status=running, stage deep in the pipeline."""
    return _seed(db_session, status="running", stage="verifying", cleanup_intent="none", **kw)


def test_p1_5_current_prefers_running_over_older_cleaning_task(
    client, db_session: Session
) -> None:
    """P1-5: ``/tasks/current`` must return the *running* task even when an older
    cleaning task exists. The rail is the page's "what is happening now" widget;
    returning the cleaning row puts the wrong filename/stage/artemis id on screen.
    """
    # seeded first => lower surrogate id => sorts first under ``ORDER BY id``
    cleaning = _seed(
        db_session,
        status="succeeded",
        stage="done",
        cleanup_intent="preserve_state",
        device_cleanup_status="pending",
    )
    running = _seed_running(db_session)
    db_session.commit()

    assert cleaning.id < running.id, (
        "probe precondition: the cleaning row must sort before the running row "
        "for this regression to be reachable"
    )

    response = client.get("/v2/video-publish/tasks/current")
    assert response.status_code == 200, _explain("P1-5-current", response)
    body = response.json()
    returned = body["task"]
    print(
        f"\n[probe:P1-5-current] cleaning.id={cleaning.id} running.id={running.id} "
        f"returned={returned['taskId'] if returned else None} "
        f"status={returned['status'] if returned else None}"
    )
    assert returned is not None, "expected a current task"
    assert returned["status"] == "running", (
        f"/tasks/current returned a {returned['status']!r} task while a running "
        "task existed; the rail would show the wrong task "
        f"(expected {running.public_id}, got {returned['taskId']})"
    )
    assert returned["taskId"] == str(running.public_id), _explain("P1-5-current", response)


def test_p1_5_current_returns_cleaning_when_nothing_runs(
    client, db_session: Session
) -> None:
    """Regression guard for the P1-5 fix: a lone cleaning task must still surface,
    otherwise the fix would simply break the cleaning rail.
    """
    cleaning = _seed(
        db_session,
        status="succeeded",
        stage="done",
        cleanup_intent="preserve_state",
        device_cleanup_status="failed",
    )
    db_session.commit()
    response = client.get("/v2/video-publish/tasks/current")
    assert response.status_code == 200, _explain("P1-5-cleaning-only", response)
    task = response.json()["task"]
    assert task is not None, "a lone cleaning task must still be reported"
    assert task["taskId"] == str(cleaning.public_id), _explain("P1-5-cleaning-only", response)


def test_p2_21_operational_stage_started_at_is_never_in_the_future(
    client, db_session: Session
) -> None:
    """P2-21: during cleanup backoff the snapshot reports
    ``device_cleanup_next_attempt_at`` as ``operationalStageStartedAt``. That is a
    *next retry* time, not a stage-start time, and the rail renders
    ``max(0, now - startedAt)`` => it pins to "已耗时 0 秒" forever.
    """
    now = datetime.now(UTC)
    task = _seed(
        db_session,
        status="succeeded",
        stage="done",
        cleanup_intent="preserve_state",
        device_cleanup_status="failed",
    )
    task.device_cleanup_next_attempt_at = now + timedelta(minutes=30)
    task.stage_started_at = now - timedelta(minutes=10)
    db_session.commit()

    response = client.get("/v2/video-publish/tasks/current")
    assert response.status_code == 200, _explain("P2-21-future", response)
    body = response.json()
    task_body = body["task"]
    started = task_body.get("operationalStageStartedAt")
    print(
        f"\n[probe:P2-21-future] operationalStage={task_body.get('operationalStage')!r} "
        f"operationalStageStartedAt={started!r} stageStartedAt={task_body.get('stageStartedAt')!r} "
        f"next_attempt={now + timedelta(minutes=30)!r}"
    )
    assert started is not None
    parsed = datetime.fromisoformat(started)
    assert parsed <= datetime.now(UTC), (
        "operationalStageStartedAt is a FUTURE timestamp "
        f"({started}); the rail would clamp to '已耗时 0 秒' for the whole backoff "
        "window. Backoff must not be reported as stage-start time."
    )


# ═══════════════════════════════════════════════════════════════════════════
# Batch 3A — the P1-1 ruling is WITHDRAWN.
#
# The readiness gate was never missing: it lives at three layers
#   1. repository.has_pending_device_cleanup()  — global pre-claim probe
#   2. repository.claim_one()                  — refuses to lease anything
#   3. dispatcher (DEVICE_CLEANUP_BLOCKED)     — before STAGING_DEVICE
# Wave 1 added an API-level gate on top of those and turned §10.7
# "failed（可重试）→ 重试原任务" into a mandatory two-step. That is withdrawn.
#
# The load-bearing invariant that replaces it: ``queue_task`` must NOT write
# cleanup_intent='none', because that erases this task's own
# "device cleanup unresolved" marker and the gate can no longer see it.
# ═══════════════════════════════════════════════════════════════════════════


def test_retry_then_gate_still_blocks_publishing(db_session: Session) -> None:
    """THE core guardrail: a re-queued task with an unresolved device cleanup must
    remain invisible to the publish worker.

    If ``queue_task`` wrote cleanup_intent='none' the marker would be erased,
    ``has_pending_device_cleanup`` would go blind, and a second video would be
    staged onto a phone that still holds the previous one — the exact §14 step 8
    hazard. Without this probe the whole 3A change is unguarded.
    """
    task = _seed(
        db_session,
        status="failed",
        stage="done",
        cleanup_intent="preserve_state",
        device_cleanup_status="failed",
        object_cleanup_status="not_started",
        spool_cleanup_status="not_started",
    )
    from tts_erp_v2.publishing.submission import retry_task

    retry_task(db_session, task.public_id)
    db_session.commit()
    db_session.expire_all()

    requeued = db_session.scalar(
        select(VideoPublishTask).where(VideoPublishTask.public_id == task.public_id)
    )
    print(
        f"\n[probe:3A-gate] status={requeued.status} stage={requeued.stage} "
        f"cleanup_intent={requeued.cleanup_intent} "
        f"device_cleanup_status={requeued.device_cleanup_status}"
    )

    # 1) the marker survives the requeue
    assert requeued.status == "pending" and requeued.stage == "queued"
    assert requeued.cleanup_intent != "none", (
        "queue_task erased the cleanup marker; the readiness gate is now blind "
        "to this task"
    )

    # 2) the global pre-claim probe still sees it
    db_session.rollback()
    assert has_pending_device_cleanup(db_session) is True, (
        "has_pending_device_cleanup went blind to a task whose device cleanup is "
        "still failed"
    )

    # 3) and the worker refuses to lease anything at all
    claimed = claim_one(db_session, "TEST-a3-gate-worker")
    print(f"[probe:3A-gate] claim_one -> {claimed!r}")
    assert claimed is None, (
        "claim_one leased a task while its device cleanup is unresolved; the "
        "readiness gate is not actually wired"
    )


def test_retry_healthy_path_is_claimable(db_session: Session) -> None:
    """Anti-deadlock control: a cleanly failed task re-queued with NOTHING to
    clean must be claimable by the worker.

    This is the reason queue_task cannot write 'requeue_publish'
    unconditionally: claim_one only leases rows whose cleanup_intent is 'none'
    and whose device cleanup is not pending/failed. Marking a clean task as
    cleanup-gated would strand it in the queue forever.
    """
    task = _seed(
        db_session,
        status="failed",
        stage="done",
        cleanup_intent="none",
        device_cleanup_status="not_started",
        object_cleanup_status="not_started",
        spool_cleanup_status="not_started",
    )
    from tts_erp_v2.publishing.submission import retry_task

    retry_task(db_session, task.public_id)
    db_session.commit()
    db_session.expire_all()

    requeued = db_session.scalar(
        select(VideoPublishTask).where(VideoPublishTask.public_id == task.public_id)
    )
    print(
        f"\n[probe:3A-clean] cleanup_intent={requeued.cleanup_intent} "
        f"device_cleanup_status={requeued.device_cleanup_status}"
    )
    assert requeued.cleanup_intent == "none", (
        "a task with no outstanding cleanup must stay claimable; marking it "
        "'requeue_publish' would deadlock it in the queue forever"
    )
    db_session.rollback()
    assert has_pending_device_cleanup(db_session) is False
    claimed = claim_one(db_session, "TEST-a3-clean-worker")
    print(f"[probe:3A-clean] claim_one -> {getattr(claimed, 'public_id', None)!r}")
    assert claimed is not None, "clean re-queued task must be claimable"
    assert claimed.public_id == task.public_id


def test_requeue_publish_satisfies_db_check_constraint(db_session: Session) -> None:
    """The 'requeue_publish' branch of video_publish_task_cleanup_owner_check
    requires status='pending', stage IN ('queued','waiting_device') and
    object_cleanup_status NOT IN ('pending','failed'). Prove the real write
    lands — a plain in-memory assignment would not catch a CHECK violation,
    because the ORM only emits it at flush time.
    """
    from sqlalchemy.exc import IntegrityError as _IntegrityError

    task = _seed(
        db_session,
        status="failed",
        stage="done",
        cleanup_intent="preserve_state",
        device_cleanup_status="failed",
        object_cleanup_status="not_started",
        spool_cleanup_status="not_started",
    )
    from tts_erp_v2.publishing.submission import retry_task

    retry_task(db_session, task.public_id)
    try:
        db_session.commit()
    except _IntegrityError as exc:  # pragma: no cover - failure evidence
        db_session.rollback()
        raise AssertionError(
            "retry on an unresolved device cleanup violates the DB CHECK "
            f"constraint: {str(exc)[:400]}"
        ) from exc

    db_session.expire_all()
    requeued = db_session.scalar(
        select(VideoPublishTask).where(VideoPublishTask.public_id == task.public_id)
    )
    print(
        f"\n[probe:3A-check] committed status={requeued.status} stage={requeued.stage} "
        f"cleanup_intent={requeued.cleanup_intent} "
        f"object_cleanup_status={requeued.object_cleanup_status}"
    )
    assert requeued.cleanup_intent == "requeue_publish"
    assert requeued.object_cleanup_status not in {"pending", "failed"}, (
        "object_cleanup must be clear; the DB CHECK for 'requeue_publish' "
        "forbids pending/failed object cleanup"
    )


def test_retry_rejected_when_object_cleanup_pending_still_holds(
    client, db_session: Session
) -> None:
    """Regression net: 3A must NOT reopen the object dimension.

    Object deletion (retention) must finish before retry mutates input, so a
    pending/failed object cleanup still blocks retry with 409.
    """
    for cleanup_status in ("pending", "failed"):
        task = _seed(
            db_session,
            cleanup_intent="preserve_state",
            device_cleanup_status="not_started",
            object_cleanup_status=cleanup_status,
        )
        response = _post(client, task, row_version=task.row_version)
        assert response.status_code == 409, _explain(f"3A-obj-{cleanup_status}", response)
        assert response.json()["detail"]["code"] == "OBJECT_CLEANUP_IN_PROGRESS", (
            _explain(f"3A-obj-{cleanup_status}", response)
        )
