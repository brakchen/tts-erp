from __future__ import annotations

import asyncio
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from uuid import UUID, uuid4

import pytest
from fastapi import HTTPException, Request, Response
from sqlalchemy import delete, func, select, text, update
from sqlalchemy.orm import Session, sessionmaker

from tts_erp_v2.access import (
    AccessEffect,
    AccessRequest,
    AuthMode,
    Credential,
    Role,
    _access,
)
from tts_erp_v2.api.v2 import video_publish as publish_api
from tts_erp_v2.db.models.publishing import VideoPublishAttempt, VideoPublishTask
from tts_erp_v2.publishing import dispatcher, worker
from tts_erp_v2.publishing.artemis_client import ArtemisResult
from tts_erp_v2.publishing.dispatcher import (
    PublishDependencies,
    _execute,
    _execute_cleanup,
)
from tts_erp_v2.publishing.repository import CleanupClaimRequest, claim_cleanup_work
from tts_erp_v2.publishing.submission import (
    CreateCommand,
    TaskConflict,
    cancel_task,
    create_upload_ticket,
    replace_upload,
    retry_task,
    upload_expires_at,
)
from tts_erp_v2.storage.minio_client import ObjectNotFound


def _task(*, status: str = "running", stage: str = "downloading") -> VideoPublishTask:
    return VideoPublishTask(
        public_id=uuid4(),
        client_request_id=uuid4(),
        created_by_user_id=2424,
        caption="TEST_v24_caption",
        original_filename="TEST_v24.mp4",
        object_filename="TEST_v24.mp4",
        content_type="video/mp4",
        size_bytes=4,
        object_bucket="tiktok-video",
        object_key=f"TEST/v24/{uuid4()}/video.mp4",
        object_etag="TEST-v24-etag",
        object_uploaded_at=datetime.now(UTC),
        status=status,
        stage=stage,
        stage_started_at=datetime.now(UTC),
        cleanup_intent="none",
        target_device_serial="TESTV24SERIAL",
        target_app_package="com.test.v24",
        lease_owner="TEST-v24-worker",
        lease_expires_at=datetime.now(UTC) + timedelta(minutes=5),
    )


def _delete_task(factory: sessionmaker[Session], task_id: UUID) -> None:
    with factory() as session:
        task_pk = session.scalar(
            select(VideoPublishTask.id).where(VideoPublishTask.public_id == task_id)
        )
        if task_pk is None:
            return
        session.execute(
            delete(VideoPublishAttempt).where(VideoPublishAttempt.task_id == task_pk)
        )
        session.execute(delete(VideoPublishTask).where(VideoPublishTask.id == task_pk))
        session.commit()


@pytest.mark.asyncio
async def test_execution_generation_is_persisted_before_download_side_effect(
    db_session: Session, tmp_path: Path
) -> None:
    task = _task()
    db_session.add(task)
    db_session.commit()
    destinations: list[Path] = []
    persisted_generations: list[UUID | None] = []

    class Store:
        def download(self, _key: str, destination: Path, _expected_etag: str) -> str:
            destinations.append(destination)
            with Session(bind=db_session.get_bind()) as observer:
                persisted = observer.get(VideoPublishTask, task.id)
                assert persisted is not None
                generation = cast(
                    UUID | None, getattr(persisted, "execution_generation", None)
                )
                persisted_generations.append(generation)
            raise RuntimeError("TEST stop after observing download identity")

    deps = cast(
        PublishDependencies,
        SimpleNamespace(
            session_factory=lambda: Session(
                bind=db_session.get_bind(), join_transaction_mode="create_savepoint"
            ),
            instance_id="TEST-v24-worker",
            lease_seconds=30,
            max_attempts=3,
            poll_seconds=0,
            store=Store(),
            adb=SimpleNamespace(),
            artemis=SimpleNamespace(),
            spool_dir=tmp_path,
            artemis_profile="TEST-profile",
            artemis_verification_level="TEST-verification",
        ),
    )

    await _execute(task.public_id, deps)

    assert len(destinations) == 1
    assert len(persisted_generations) == 1
    generation = persisted_generations[0]
    assert generation is not None
    assert destinations[0] == (
        tmp_path / str(task.public_id) / str(generation) / "video.mp4"
    )


@pytest.mark.asyncio
async def test_stale_spool_cleaner_cannot_address_new_execution_generation(
    db_session: Session, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    old_generation = uuid4()
    task = _task(status="cancelled", stage="done")
    task.lease_owner = None
    task.lease_expires_at = None
    task.execution_generation = old_generation
    old_path = tmp_path / str(task.public_id) / str(old_generation) / "video.mp4"
    old_path.parent.mkdir(parents=True)
    old_path.write_bytes(b"TEST-old")
    task.spool_path = str(old_path)
    task.cleanup_intent = "preserve_state"
    task.spool_cleanup_status = "pending"
    task.spool_cleanup_next_attempt_at = datetime.now(UTC) - timedelta(seconds=1)
    db_session.add(task)
    db_session.commit()
    task_pk = task.id
    task_public_id = task.public_id

    def factory() -> Session:
        return Session(
            bind=db_session.get_bind(), join_transaction_mode="create_savepoint"
        )

    stale = claim_cleanup_work(
        factory,
        CleanupClaimRequest("TEST-stale-spool", 1, "background", task_public_id),
    )
    assert stale is not None
    assert getattr(stale, "execution_generation", None) == old_generation

    started = threading.Event()
    release = threading.Event()
    removed_paths: list[Path] = []
    original_remove = dispatcher._remove_spool

    async def controlled_remove(path: Path) -> None:
        removed_paths.append(path)
        if len(removed_paths) == 1:
            started.set()
            await asyncio.to_thread(release.wait, 5)
            return
        await original_remove(path)

    monkeypatch.setattr(dispatcher, "_remove_spool", controlled_remove)
    monkeypatch.setattr(
        dispatcher, "require_destructive_script_guard", lambda **_kwargs: None
    )
    deps = cast(
        PublishDependencies,
        SimpleNamespace(
            session_factory=factory,
            instance_id="TEST-stale-spool",
            lease_seconds=1,
            spool_dir=tmp_path,
            store=SimpleNamespace(remove=lambda *_args: None),
            adb=SimpleNamespace(),
        ),
    )
    stale_run = asyncio.create_task(_execute_cleanup(stale, deps))
    assert await asyncio.to_thread(started.wait, 2)
    db_session.execute(
        text(
            "UPDATE publishing.video_publish_tasks "
            "SET cleanup_lease_expires_at=clock_timestamp()-interval '1 second' "
            "WHERE id=:id"
        ),
        {"id": task_pk},
    )
    db_session.commit()
    await asyncio.sleep(0.4)
    takeover = claim_cleanup_work(
        factory,
        CleanupClaimRequest("TEST-takeover-spool", 30, "background", task_public_id),
    )
    assert takeover is not None
    deps.instance_id = "TEST-takeover-spool"
    deps.lease_seconds = 30
    await _execute_cleanup(takeover, deps)

    new_generation = uuid4()
    new_path = tmp_path / str(task.public_id) / str(new_generation) / "video.mp4"
    new_path.parent.mkdir(parents=True)
    new_path.write_bytes(b"TEST-new")
    db_session.execute(
        update(VideoPublishTask)
        .where(VideoPublishTask.id == task_pk)
        .values(execution_generation=new_generation, spool_path=str(new_path))
    )
    db_session.commit()
    release.set()
    await stale_run

    assert removed_paths == [old_path, old_path]
    assert new_path.exists()


@pytest.mark.asyncio
async def test_stale_device_cleaner_cannot_address_new_execution_generation(
    db_session: Session, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    old_generation = uuid4()
    task = _task(status="cancelled", stage="done")
    task.lease_owner = None
    task.lease_expires_at = None
    task.execution_generation = old_generation
    old_path = f"/sdcard/Movies/TTSERP/tts_erp_{old_generation}.mp4"
    task.device_path = old_path
    task.cleanup_intent = "preserve_state"
    task.device_cleanup_status = "pending"
    task.device_cleanup_next_attempt_at = datetime.now(UTC) - timedelta(seconds=1)
    db_session.add(task)
    db_session.commit()
    task_pk = task.id
    task_public_id = task.public_id

    def factory() -> Session:
        return Session(
            bind=db_session.get_bind(), join_transaction_mode="create_savepoint"
        )

    stale = claim_cleanup_work(
        factory,
        CleanupClaimRequest("TEST-stale-device", 1, "device", task_public_id),
    )
    assert stale is not None
    assert getattr(stale, "execution_generation", None) == old_generation
    started = asyncio.Event()
    release = asyncio.Event()
    removed_paths: list[str] = []

    class Adb:
        async def remove_staged_video(self, _serial: str, path: str) -> None:
            removed_paths.append(path)
            if len(removed_paths) == 1:
                started.set()
                await release.wait()

    monkeypatch.setattr(
        dispatcher, "require_destructive_script_guard", lambda **_kwargs: None
    )
    deps = cast(
        PublishDependencies,
        SimpleNamespace(
            session_factory=factory,
            instance_id="TEST-stale-device",
            lease_seconds=1,
            spool_dir=tmp_path,
            store=SimpleNamespace(remove=lambda *_args: None),
            adb=Adb(),
        ),
    )
    stale_run = asyncio.create_task(_execute_cleanup(stale, deps))
    await asyncio.wait_for(started.wait(), timeout=2)
    db_session.execute(
        text(
            "UPDATE publishing.video_publish_tasks "
            "SET cleanup_lease_expires_at=clock_timestamp()-interval '1 second' "
            "WHERE id=:id"
        ),
        {"id": task_pk},
    )
    db_session.commit()
    await asyncio.sleep(0.4)
    takeover = claim_cleanup_work(
        factory,
        CleanupClaimRequest("TEST-takeover-device", 30, "device", task_public_id),
    )
    assert takeover is not None
    deps.instance_id = "TEST-takeover-device"
    deps.lease_seconds = 30
    await _execute_cleanup(takeover, deps)

    new_generation = uuid4()
    new_path = f"/sdcard/Movies/TTSERP/tts_erp_{new_generation}.mp4"
    db_session.execute(
        update(VideoPublishTask)
        .where(VideoPublishTask.id == task_pk)
        .values(execution_generation=new_generation, device_path=new_path)
    )
    db_session.commit()
    release.set()
    await stale_run

    assert removed_paths == [old_path, old_path]
    assert new_path not in removed_paths


def test_existing_ticket_issuance_serializes_with_cancellation(
    db_engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ARTEMIS_DEVICE_SERIAL", "TESTV24SERIAL")
    monkeypatch.setenv("TIKTOK_PUBLISH_MINIO_BUCKET", "tiktok-video")
    factory = sessionmaker(db_engine, expire_on_commit=False)
    task = _task(status="pending", stage="awaiting_upload")
    task.lease_owner = None
    task.lease_expires_at = None
    task.object_upload_expires_at = datetime.now(UTC) - timedelta(minutes=1)
    with factory() as session:
        session.add(task)
        session.commit()
    signing = threading.Event()
    release = threading.Event()

    class Store:
        bucket = "tiktok-video"
        default_expiry = timedelta(minutes=15)

        def presign_put(self, key: str, _content_type: str) -> str:
            signing.set()
            assert release.wait(timeout=5)
            return f"https://upload.invalid/{key}"

    command = CreateCommand(
        task.client_request_id,
        task.original_filename,
        task.content_type,
        task.size_bytes,
        task.caption,
        actor_user_id=task.created_by_user_id,
    )

    def replay_ticket():
        with factory() as session:
            return create_upload_ticket(session, command, cast(Any, Store()))

    def cancel():
        with factory() as session:
            return cancel_task(session, task.public_id, cast(Any, Store()))

    with ThreadPoolExecutor(max_workers=2) as pool:
        replay_future = pool.submit(replay_ticket)
        assert signing.wait(timeout=2)
        cancel_future = pool.submit(cancel)
        threading.Event().wait(0.2)
        assert not cancel_future.done()
        release.set()
        _replayed_task, url, replay = replay_future.result(timeout=5)
        cancelled = cancel_future.result(timeout=5)

    assert replay is True
    assert url
    assert cancelled.object_upload_expires_at is not None
    assert cancelled.object_cleanup_next_attempt_at is not None
    assert (
        cancelled.object_cleanup_next_attempt_at >= cancelled.object_upload_expires_at
    )
    _delete_task(factory, task.public_id)


@pytest.mark.asyncio
async def test_attempt_profile_and_verification_level_are_immutable_submit_snapshots(
    db_session: Session, tmp_path: Path
) -> None:
    task = _task(status="running", stage="dispatching_artemis")
    attempt = VideoPublishAttempt(
        sequence_no=1,
        kind="publish",
        artemis_session_id=uuid4(),
        status="created",
        prompt_version="TEST-v24",
        prompt_snapshot="TEST immutable request",
        device_serial="TESTV24SERIAL",
        target_app_package="com.test.snapshot",
        artemis_profile="TEST-snapshot-profile",
        artemis_verification_level="TEST-snapshot-verification",
    )
    task.attempts.append(attempt)
    db_session.add(task)
    db_session.commit()
    submitted: list[tuple[str, str]] = []

    class Artemis:
        async def submit(self, **kwargs):
            submitted.append((kwargs["profile"], kwargs["verification_level"]))
            return ArtemisResult(session_id=kwargs["session_id"], status="running")

    deps = cast(
        PublishDependencies,
        SimpleNamespace(
            session_factory=lambda: Session(
                bind=db_session.get_bind(), join_transaction_mode="create_savepoint"
            ),
            instance_id="TEST-v24-worker",
            lease_seconds=30,
            max_attempts=3,
            poll_seconds=0,
            store=SimpleNamespace(),
            adb=SimpleNamespace(),
            artemis=Artemis(),
            spool_dir=tmp_path,
            artemis_profile="TEST-mutated-profile",
            artemis_verification_level="TEST-mutated-verification",
        ),
    )

    await _execute(task.public_id, deps)

    assert submitted == [("TEST-snapshot-profile", "TEST-snapshot-verification")]


@pytest.mark.parametrize(
    ("role", "expected_effect"),
    [
        (Role.READWRITE, AccessEffect.DENY),
        (Role.ADMIN, AccessEffect.ALLOW),
    ],
)
def test_video_publish_api_keys_are_admin_only_by_default(
    monkeypatch: pytest.MonkeyPatch, role: Role, expected_effect: AccessEffect
) -> None:
    monkeypatch.delenv("TTS_ERP_VIDEO_PUBLISH_ALLOW_READWRITE_API_KEYS", raising=False)
    monkeypatch.setattr(
        _access,
        "authenticate_key",
        lambda _key: Credential("TEST-v24-key-hash", role),
    )

    decision = asyncio.run(
        _access.evaluate_access(
            AccessRequest(
                method="POST",
                route_path="/v2/video-publish/tasks",
                accepts_html=False,
                client_ip="127.0.0.1",
                api_key="TEST-v24-key",
            ),
            mode=AuthMode.ENFORCE,
        )
    )

    assert decision.effect is expected_effect
    if role is Role.READWRITE:
        assert decision.status == 403
        assert decision.detail == "requires admin"


@pytest.mark.asyncio
async def test_background_cleanup_progresses_while_publish_queue_remains_sustained(
    db_session: Session, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    generation = uuid4()
    residue = _task(status="succeeded", stage="done")
    residue.lease_owner = None
    residue.lease_expires_at = None
    residue.execution_generation = generation
    path = tmp_path / str(residue.public_id) / str(generation) / "video.mp4"
    path.parent.mkdir(parents=True)
    path.write_bytes(b"TEST-residue")
    residue.spool_path = str(path)
    residue.cleanup_intent = "preserve_state"
    residue.spool_cleanup_status = "pending"
    residue.spool_cleanup_next_attempt_at = datetime.now(UTC) - timedelta(seconds=1)
    queued = [_task(status="pending", stage="queued") for _ in range(3)]
    for task in queued:
        task.lease_owner = None
        task.lease_expires_at = None
        task.queued_at = datetime.now(UTC)
    db_session.add_all([residue, *queued])
    db_session.commit()

    def factory() -> Session:
        return Session(
            bind=db_session.get_bind(), join_transaction_mode="create_savepoint"
        )

    monkeypatch.setattr(
        dispatcher, "require_destructive_script_guard", lambda **_kwargs: None
    )
    deps = cast(
        PublishDependencies,
        SimpleNamespace(
            session_factory=factory,
            instance_id="TEST-v24-background",
            lease_seconds=30,
            spool_dir=tmp_path,
            store=SimpleNamespace(remove=lambda *_args: None),
            adb=SimpleNamespace(),
        ),
    )

    run_batch = cast(Any, getattr(dispatcher, "run_background_cleanup_batch", None))
    assert run_batch is not None
    processed = await run_batch(deps, limit=1)

    db_session.refresh(residue)
    assert processed == 1
    assert residue.spool_cleanup_status == "succeeded"
    assert not path.exists()
    assert not path.parent.parent.exists()
    assert db_session.scalar(
        select(func.count())
        .select_from(VideoPublishTask)
        .where(
            VideoPublishTask.status == "pending",
            VideoPublishTask.stage == "queued",
        )
    ) == len(queued)


@pytest.mark.asyncio
async def test_background_cleanup_loop_cancels_inflight_batch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    started = asyncio.Event()
    cancelled = asyncio.Event()

    async def blocking_batch(_deps, *, limit: int) -> int:
        assert limit == 1
        started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancelled.set()
            raise
        return 0

    monkeypatch.setattr(worker, "run_background_cleanup_batch", blocking_batch)
    deps = cast(
        PublishDependencies,
        SimpleNamespace(poll_seconds=0.01),
    )
    cleanup_loop = cast(Any, getattr(worker, "_background_cleanup_loop", None))
    assert cleanup_loop is not None
    loop_task = asyncio.create_task(
        cleanup_loop(deps, batch_size=1, interval_seconds=0.01)
    )
    await asyncio.wait_for(started.wait(), timeout=1)
    loop_task.cancel()
    await asyncio.gather(loop_task, return_exceptions=True)

    assert cancelled.is_set()


def _api_request() -> Request:
    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/v2/video-publish/tasks",
            "headers": [],
            "api_key_role": "admin",
            "api_key_hash": "TEST-v24-api-key",
            "auth_method": "bearer",
        }
    )


def test_create_requires_fresh_worker_only_for_genuinely_new_task(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ARTEMIS_DEVICE_SERIAL", "TESTV24SERIAL")
    monkeypatch.setenv("TIKTOK_PUBLISH_MINIO_BUCKET", "tiktok-video")
    request_id = uuid4()
    body = publish_api.CreateIn.model_validate(
        {
            "clientRequestId": str(request_id),
            "filename": "TEST-v24.mp4",
            "contentType": "video/mp4",
            "sizeBytes": 4,
            "caption": "TEST-v24 caption",
        }
    )
    presigned: list[str] = []
    store = SimpleNamespace(
        bucket="tiktok-video",
        default_expiry=timedelta(minutes=15),
        presign_put=lambda key, _content_type: (
            presigned.append(key) or f"https://upload.invalid/{key}"
        ),
    )

    with pytest.raises(HTTPException) as exc_info:
        publish_api.create_task(
            body,
            _api_request(),
            db_session,
            cast(Any, store),
            Response(),
        )

    assert exc_info.value.status_code == 503
    detail = cast(dict[str, Any], exc_info.value.detail)
    assert detail["code"] == "PUBLISH_WORKER_UNAVAILABLE"
    assert detail["retryable"] is True
    assert presigned == []

    existing = _task(status="pending", stage="awaiting_upload")
    existing.lease_owner = None
    existing.lease_expires_at = None
    existing.client_request_id = request_id
    existing.created_by_user_id = None
    existing.created_by_key_hash = "TEST-v24-api-key"
    existing.original_filename = body.filename
    existing.content_type = body.content_type
    existing.size_bytes = body.size_bytes
    existing.caption = body.caption
    db_session.add(existing)
    db_session.commit()

    payload = publish_api.create_task(
        body,
        _api_request(),
        db_session,
        cast(Any, store),
        Response(),
    )

    assert payload["idempotentReplay"] is True
    assert payload["taskId"] == str(existing.public_id)
    assert len(presigned) == 1


def test_active_testing_guidance_uses_only_isolated_runner() -> None:
    paths = [
        Path("docs/guides/test-domains.md"),
        Path("docs/guides/commands-reference.md"),
        Path("docs/guides/agent-testing.md"),
        Path("docs/guides/agent-safety.md"),
        Path("docs/reference/miaoshou-platform.md"),
        Path("docs/design/focused-spus.md"),
        Path("docs/design/order-dump-intake-module.md"),
        Path("docs/design/access-policy-module.md"),
        Path("docs/plans/dumps-tts-erp-refactor-proposal.md"),
        Path("docs/plans/plugin-arch-cleanup-plan.md"),
        *Path("docs/architecture/adr").glob("*.md"),
    ]
    for path in paths:
        source = path.read_text()
        assert "bash scripts/test.sh" not in source, path
        assert "`scripts/test.sh" not in source, path
        assert ".venv/bin/pytest" not in source, path
        assert "test.sh all" not in source, path
        assert "test.sh coverage" not in source, path
    assert "bash scripts/test_isolated.sh" in paths[0].read_text()


def test_v24_design_runbook_and_registry_describe_all_new_fences() -> None:
    design = Path("docs/design/tiktok-video-publish.md").read_text()
    runbook = Path("docs/ops/video-publish-runbook.md").read_text()
    registry = Path("docs/handoff/ACTIVE.md").read_text()
    external_api = Path("docs/api/external-api.md").read_text()

    assert "0066_publish_execution_fences" in design
    assert "execution_generation" in design
    assert "artemis_profile" in design
    assert "artemis_verification_level" in design
    assert "15 minutes completion grace" in design
    assert "0053`–`0066" in runbook
    assert "7 天 + 15 分钟 hold" in runbook
    assert "PUBLISH_BACKGROUND_CLEANUP_BATCH_SIZE" in runbook
    assert "TTS_ERP_VIDEO_PUBLISH_ALLOW_READWRITE_API_KEYS=1" in runbook
    assert "006[0-6]" in registry
    assert "PUBLISH_WORKER_UNAVAILABLE" in external_api
    assert "admin-tier API key by default" in external_api


def test_losing_create_ticket_signer_returns_structured_conflict(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    task = _task(status="cancelled", stage="done")
    task.lease_owner = None
    task.lease_expires_at = None
    task.created_by_user_id = None
    task.created_by_key_hash = "TEST-v24-api-key"
    db_session.add(task)
    db_session.commit()
    monkeypatch.setattr(
        publish_api,
        "create_upload_ticket",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            TaskConflict("TASK_ACTION_NOT_ALLOWED", task)
        ),
    )
    body = publish_api.CreateIn.model_validate(
        {
            "clientRequestId": str(task.client_request_id),
            "filename": task.original_filename,
            "contentType": task.content_type,
            "sizeBytes": task.size_bytes,
            "caption": task.caption,
        }
    )

    with pytest.raises(HTTPException) as exc_info:
        publish_api.create_task(
            body,
            _api_request(),
            db_session,
            cast(Any, SimpleNamespace()),
            Response(),
        )

    detail = cast(dict[str, Any], exc_info.value.detail)
    assert exc_info.value.status_code == 409
    assert detail["code"] == "TASK_ACTION_NOT_ALLOWED"
    assert detail["rowVersion"] == task.row_version
    assert "allowedActions" in detail


def test_ticket_ttl_above_migration_safety_bound_is_rejected() -> None:
    store = SimpleNamespace(default_expiry=timedelta(days=7, seconds=1))

    with pytest.raises(ValueError, match="UPLOAD_TTL_EXCEEDS_SAFE_MAX"):
        upload_expires_at(cast(Any, store), database_time=datetime.now(UTC))


@pytest.mark.asyncio
async def test_missing_retry_object_stays_selector_owned_until_expiry_grace(
    db_session: Session, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    task = _task(status="failed", stage="done")
    task.lease_owner = None
    task.lease_expires_at = None
    task.object_upload_expires_at = datetime.now(UTC) + timedelta(minutes=10)
    task.attempts.append(
        VideoPublishAttempt(
            sequence_no=1,
            kind="publish",
            artemis_session_id=uuid4(),
            status="failed",
            prompt_version="TEST-v24",
            prompt_snapshot="TEST-v24",
            device_serial="TESTV24SERIAL",
            target_app_package="com.test.v24",
            artemis_profile="TEST-profile",
            artemis_verification_level="TEST-verification",
            retry_safe=True,
        )
    )
    db_session.add(task)
    db_session.commit()
    store = SimpleNamespace(
        stat=lambda _key: (_ for _ in ()).throw(ObjectNotFound("TEST missing"))
    )

    with pytest.raises(ValueError, match="UPLOAD_REPLACEMENT_REQUIRED"):
        retry_task(db_session, task.public_id, cast(Any, store))

    db_session.refresh(task)
    assert task.object_deleted_at is None
    assert task.object_cleanup_status == "pending"
    assert task.cleanup_intent == "preserve_state"
    assert task.object_cleanup_next_attempt_at == task.object_upload_expires_at
    with pytest.raises(ValueError, match="OBJECT_CLEANUP_IN_PROGRESS"):
        replace_upload(db_session, task.public_id)

    retired_generation = task.object_generation
    db_session.execute(
        update(VideoPublishTask)
        .where(VideoPublishTask.id == task.id)
        .values(
            object_upload_expires_at=datetime.now(UTC) - timedelta(minutes=16),
            object_cleanup_next_attempt_at=datetime.now(UTC) - timedelta(minutes=16),
        )
    )
    db_session.commit()

    def factory() -> Session:
        return Session(
            bind=db_session.get_bind(), join_transaction_mode="create_savepoint"
        )

    removed: list[str] = []
    monkeypatch.setattr(
        dispatcher, "require_destructive_script_guard", lambda **_kwargs: None
    )
    cleanup_store = SimpleNamespace(remove=lambda key, _etag=None: removed.append(key))
    work = claim_cleanup_work(
        factory,
        CleanupClaimRequest("TEST-missing-object-cleaner", 30, "background"),
    )
    assert work is not None
    await _execute_cleanup(
        work,
        cast(
            PublishDependencies,
            SimpleNamespace(
                session_factory=factory,
                instance_id="TEST-missing-object-cleaner",
                lease_seconds=30,
                spool_dir=tmp_path,
                store=cleanup_store,
                adb=SimpleNamespace(),
            ),
        ),
    )
    db_session.expire_all()
    assert task.object_cleanup_status == "succeeded"
    assert task.object_deleted_at is not None
    assert removed == [task.object_key]

    replacement = replace_upload(db_session, task.public_id)
    assert replacement.object_generation != retired_generation


def test_concurrent_replays_persist_monotonic_maximum_ticket_expiry(
    db_engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ARTEMIS_DEVICE_SERIAL", "TESTV24SERIAL")
    monkeypatch.setenv("TIKTOK_PUBLISH_MINIO_BUCKET", "tiktok-video")
    factory = sessionmaker(db_engine, expire_on_commit=False)
    task = _task(status="pending", stage="awaiting_upload")
    task.lease_owner = None
    task.lease_expires_at = None
    task.object_upload_expires_at = datetime.now(UTC) - timedelta(minutes=1)
    with factory() as session:
        session.add(task)
        session.commit()
    short_signing = threading.Event()
    release_short = threading.Event()

    class Store:
        bucket = "tiktok-video"

        def __init__(self, expiry: timedelta, *, block: bool = False) -> None:
            self.default_expiry = expiry
            self.block = block

        def presign_put(self, key: str, _content_type: str) -> str:
            if self.block:
                short_signing.set()
                assert release_short.wait(timeout=5)
            return f"https://upload.invalid/{key}"

    command = CreateCommand(
        task.client_request_id,
        task.original_filename,
        task.content_type,
        task.size_bytes,
        task.caption,
        actor_user_id=task.created_by_user_id,
    )

    def replay(store: Store):
        with factory() as session:
            return create_upload_ticket(session, command, cast(Any, store))

    before = datetime.now(UTC)
    with ThreadPoolExecutor(max_workers=2) as pool:
        short = pool.submit(replay, Store(timedelta(minutes=5), block=True))
        assert short_signing.wait(timeout=2)
        long = pool.submit(replay, Store(timedelta(minutes=30)))
        threading.Event().wait(0.2)
        release_short.set()
        short.result(timeout=5)
        long.result(timeout=5)

    with factory() as session:
        persisted_expiry = session.scalar(
            select(VideoPublishTask.object_upload_expires_at).where(
                VideoPublishTask.id == task.id
            )
        )
    assert persisted_expiry is not None
    assert persisted_expiry >= before + timedelta(minutes=29)
    _delete_task(factory, task.public_id)


def test_object_cleanup_claim_enforces_ticket_expiry_completion_grace(
    db_session: Session,
) -> None:
    task = _task(status="cancelled", stage="done")
    task.lease_owner = None
    task.lease_expires_at = None
    task.cleanup_intent = "preserve_state"
    task.object_cleanup_status = "pending"
    task.object_cleanup_next_attempt_at = datetime.now(UTC) - timedelta(hours=1)
    task.object_upload_expires_at = datetime.now(UTC) - timedelta(seconds=1)
    db_session.add(task)
    db_session.commit()

    def factory() -> Session:
        return Session(
            bind=db_session.get_bind(), join_transaction_mode="create_savepoint"
        )

    assert (
        claim_cleanup_work(
            factory,
            CleanupClaimRequest("TEST-before-grace", 30, "background", task.public_id),
        )
        is None
    )
    db_session.execute(
        update(VideoPublishTask)
        .where(VideoPublishTask.id == task.id)
        .values(
            object_upload_expires_at=datetime.now(UTC) - timedelta(minutes=16),
            object_cleanup_next_attempt_at=datetime.now(UTC) - timedelta(minutes=16),
        )
    )
    db_session.commit()

    work = claim_cleanup_work(
        factory,
        CleanupClaimRequest("TEST-after-grace", 30, "background", task.public_id),
    )
    assert work is not None
    assert work.resources == ("object",)
