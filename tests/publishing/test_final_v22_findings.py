from __future__ import annotations

import asyncio
import subprocess
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from uuid import uuid4

import pytest
from fastapi import Request
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from tts_erp_v2.access import AccessGrant, AuthMode, Role
from tts_erp_v2.api.v2 import video_publish as api
from tts_erp_v2.db.models.publishing import VideoPublishAttempt, VideoPublishTask
from tts_erp_v2.publishing.artemis_client import ArtemisResult
from tts_erp_v2.publishing.dispatcher import (
    PublishDependencies,
    _execute,
    _execute_cleanup,
    recover_active,
)
from tts_erp_v2.publishing.object_store import (
    MinioVideoStore,
    ObjectVersionMismatch,
    VideoObjectStore,
)
from tts_erp_v2.publishing.repository import (
    CleanupClaimRequest,
    claim_cleanup_work,
)
from tts_erp_v2.publishing.submission import (
    CreateCommand,
    cancel_task,
    create_upload_ticket,
    replace_upload,
)

ROOT = Path(__file__).parents[2]


def _request() -> Request:
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/v2/video-publish/metrics",
            "headers": [],
            "access_grant": AccessGrant(
                mode=AuthMode.ENFORCE,
                role=Role.ADMIN,
                scopes=("page:video-publish",),
                auth_method="cookie",
                bypass=False,
            ),
            "auth_method": "cookie",
            "user_id": 2222,
        }
    )


def _task(*, status: str = "pending", stage: str = "queued") -> VideoPublishTask:
    return VideoPublishTask(
        public_id=uuid4(),
        client_request_id=uuid4(),
        created_by_user_id=2222,
        caption="TEST_v22_caption",
        original_filename="TEST_v22.mp4",
        object_filename="TEST_v22.mp4",
        content_type="video/mp4",
        size_bytes=4,
        object_bucket="tiktok-video",
        object_key=f"TEST/v22/{uuid4()}/video.mp4",
        object_etag="TEST-v22-etag",
        object_uploaded_at=datetime.now(UTC),
        status=status,
        stage=stage,
        cleanup_intent="none",
        target_device_serial="TESTV22SERIAL",
        target_app_package="com.test.mutable",
        queued_at=datetime.now(UTC) if status == "pending" else None,
        stage_started_at=datetime.now(UTC),
    )


class _TicketStore:
    bucket = "tiktok-video"
    default_expiry = timedelta(minutes=15)

    def __init__(self) -> None:
        self.keys: list[str] = []

    def presign_put(self, key: str, _content_type: str) -> str:
        self.keys.append(key)
        return f"https://upload.invalid/{key}"

    def stat(self, _key: str) -> dict:
        return {"size": 4, "content_type": "video/mp4", "etag": "TEST-etag"}

    def download(self, _key: str, _destination: Path, _expected_etag: str) -> str:
        return "TEST-sha256"

    def check_available(self) -> None:
        return None

    def remove(self, _key: str, _expected_etag: str | None = None) -> None:
        return None


@pytest.mark.asyncio
async def test_process_kill_part_residue_is_removed_by_selector_cleanup(
    db_session: Session, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    task = _task(status="cancelled", stage="done")
    final = tmp_path / str(task.public_id) / "video.mp4"
    part = final.with_suffix(".mp4.part")
    part.parent.mkdir(parents=True)
    part.write_bytes(b"TEST-crash-residue")
    task.spool_path = str(final)
    task.cleanup_intent = "preserve_state"
    task.spool_cleanup_status = "pending"
    task.spool_cleanup_next_attempt_at = datetime.now(UTC) - timedelta(seconds=1)
    db_session.add(task)
    db_session.commit()

    monkeypatch.setenv("TTS_ERP_ALLOW_DESTRUCTIVE", "1")

    def factory() -> Session:
        return Session(
            bind=db_session.get_bind(), join_transaction_mode="create_savepoint"
        )

    work = claim_cleanup_work(
        factory,
        CleanupClaimRequest(
            owner="TEST-v22-cleaner", lease_seconds=30, scope="background"
        ),
    )
    assert work is not None
    deps = cast(
        PublishDependencies,
        SimpleNamespace(
            session_factory=factory,
            instance_id="TEST-v22-cleaner",
            lease_seconds=30,
            spool_dir=tmp_path,
            store=SimpleNamespace(remove=lambda *_args, **_kwargs: None),
            adb=SimpleNamespace(),
        ),
    )

    await _execute_cleanup(work, deps)

    db_session.expire_all()
    assert not final.exists()
    assert not part.exists()
    assert task.spool_cleanup_status == "succeeded"


def test_object_cleanup_refuses_a_different_confirmed_etag() -> None:
    removed: list[str] = []
    client = SimpleNamespace(
        bucket="tiktok-video",
        stat=lambda _key: {"etag": "new-generation-etag"},
        remove=lambda key: removed.append(key),
    )
    store = MinioVideoStore(cast(Any, client))

    with pytest.raises(ObjectVersionMismatch, match="VERSION_MISMATCH"):
        store.remove("TEST/retired-generation.mp4", "retired-etag")

    assert removed == []


def test_upload_replacement_allocates_a_new_generation_and_key(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ARTEMIS_DEVICE_SERIAL", "TESTV22SERIAL")
    monkeypatch.setenv("TIKTOK_PUBLISH_MINIO_BUCKET", "tiktok-video")
    store = _TicketStore()
    created, _url, _replay = create_upload_ticket(
        db_session,
        CreateCommand(uuid4(), "TEST generation.mp4", "video/mp4", 4, "TEST caption"),
        cast(VideoObjectStore, store),
    )
    old_generation = created.object_generation
    old_key = created.object_key
    assert created.object_upload_expires_at is not None
    assert str(old_generation) in old_key

    created.status = "failed"
    created.stage = "done"
    created.object_deleted_at = datetime.now(UTC)
    created.object_cleanup_status = "succeeded"
    db_session.commit()

    replaced = replace_upload(db_session, created.public_id)

    assert replaced.object_generation != old_generation
    assert replaced.object_key != old_key
    assert str(replaced.object_generation) in replaced.object_key
    assert replaced.object_upload_expires_at is None


def test_cancel_defers_object_cleanup_until_persisted_put_expiry(
    db_session: Session,
) -> None:
    task = _task(status="pending", stage="awaiting_upload")
    task.object_upload_expires_at = datetime.now(UTC) + timedelta(minutes=10)
    db_session.add(task)
    db_session.commit()

    cancelled = cancel_task(
        db_session, task.public_id, cast(VideoObjectStore, SimpleNamespace())
    )

    assert (
        cancelled.object_cleanup_next_attempt_at == cancelled.object_upload_expires_at
    )

    def factory() -> Session:
        return Session(
            bind=db_session.get_bind(), join_transaction_mode="create_savepoint"
        )

    assert (
        claim_cleanup_work(
            factory,
            CleanupClaimRequest(
                owner="TEST-too-early", lease_seconds=30, scope="background"
            ),
        )
        is None
    )

    db_session.execute(
        text(
            "UPDATE publishing.video_publish_tasks "
            "SET object_upload_expires_at = clock_timestamp() - interval '1 second', "
            "object_cleanup_next_attempt_at = clock_timestamp() - interval '1 second' "
            "WHERE id = :task_id"
        ),
        {"task_id": task.id},
    )
    db_session.commit()
    due = claim_cleanup_work(
        factory,
        CleanupClaimRequest(
            owner="TEST-after-expiry", lease_seconds=30, scope="background"
        ),
    )
    assert due is not None
    assert due.object_key == task.object_key


def test_cleanup_work_keeps_retired_generation_identity(
    db_session: Session,
) -> None:
    task = _task(status="cancelled", stage="done")
    retired_key = task.object_key
    task.object_upload_expires_at = datetime.now(UTC) - timedelta(seconds=1)
    task.cleanup_intent = "preserve_state"
    task.object_cleanup_status = "pending"
    task.object_cleanup_next_attempt_at = datetime.now(UTC) - timedelta(seconds=1)
    db_session.add(task)
    db_session.commit()
    retired_generation = task.object_generation

    def factory() -> Session:
        return Session(
            bind=db_session.get_bind(), join_transaction_mode="create_savepoint"
        )

    work = claim_cleanup_work(
        factory,
        CleanupClaimRequest(
            owner="TEST-generation-cleaner", lease_seconds=30, scope="background"
        ),
    )

    assert work is not None
    assert work.object_generation == retired_generation
    assert work.object_key == retired_key
    assert work.object_etag == "TEST-v22-etag"


@pytest.mark.asyncio
async def test_stale_cleaner_can_only_delete_its_retired_generation(
    db_session: Session, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    task = _task(status="failed", stage="done")
    task.object_upload_expires_at = datetime.now(UTC) - timedelta(seconds=1)
    task.cleanup_intent = "preserve_state"
    task.object_cleanup_status = "pending"
    task.object_cleanup_next_attempt_at = datetime.now(UTC) - timedelta(seconds=1)
    db_session.add(task)
    db_session.commit()
    retired_key = task.object_key

    def factory() -> Session:
        return Session(
            bind=db_session.get_bind(), join_transaction_mode="create_savepoint"
        )

    stale_work = claim_cleanup_work(
        factory,
        CleanupClaimRequest(owner="TEST-stale", lease_seconds=1, scope="background"),
    )
    assert stale_work is not None
    started = threading.Event()
    release = threading.Event()
    removed: list[str] = []

    class Store:
        def remove(self, key: str, _expected_etag: str | None = None) -> None:
            removed.append(key)
            if len(removed) == 1:
                started.set()
                assert release.wait(timeout=5)

    monkeypatch.setattr(
        "tts_erp_v2.publishing.dispatcher.require_destructive_script_guard",
        lambda **_kwargs: None,
    )
    stale_deps = cast(
        PublishDependencies,
        SimpleNamespace(
            session_factory=factory,
            instance_id="TEST-stale",
            lease_seconds=1,
            spool_dir=tmp_path,
            store=Store(),
            adb=SimpleNamespace(),
        ),
    )
    stale_execution = asyncio.create_task(_execute_cleanup(stale_work, stale_deps))
    assert await asyncio.to_thread(started.wait, 2)
    db_session.execute(
        text(
            "UPDATE publishing.video_publish_tasks "
            "SET cleanup_lease_expires_at = clock_timestamp() - interval '1 second' "
            "WHERE id = :task_id"
        ),
        {"task_id": task.id},
    )
    db_session.commit()
    await asyncio.sleep(0.4)

    takeover = claim_cleanup_work(
        factory,
        CleanupClaimRequest(
            owner="TEST-takeover", lease_seconds=30, scope="background"
        ),
    )
    assert takeover is not None
    takeover_deps = cast(
        PublishDependencies,
        SimpleNamespace(
            session_factory=factory,
            instance_id="TEST-takeover",
            lease_seconds=30,
            spool_dir=tmp_path,
            store=stale_deps.store,
            adb=SimpleNamespace(),
        ),
    )
    await _execute_cleanup(takeover, takeover_deps)
    db_session.expire_all()
    replacement = replace_upload(db_session, task.public_id)
    replacement_key = replacement.object_key

    release.set()
    await stale_execution

    assert removed == [retired_key, retired_key]
    assert replacement_key != retired_key
    assert replacement_key not in removed


def _active_attempt(task: VideoPublishTask, *, package: str) -> VideoPublishAttempt:
    return VideoPublishAttempt(
        sequence_no=1,
        kind="publish",
        artemis_session_id=uuid4(),
        status="created",
        prompt_version="TEST-v22",
        prompt_snapshot="TEST immutable prompt",
        device_serial="TESTV22SERIAL",
        device_path="/sdcard/Movies/TTSERP/tts_erp_test.mp4",
        target_app_package=package,
        started_at=datetime.now(UTC),
    )


@pytest.mark.asyncio
async def test_dispatch_uses_immutable_attempt_package_snapshot(
    db_session: Session, tmp_path: Path
) -> None:
    task = _task(status="running", stage="dispatching_artemis")
    task.lease_owner = "TEST-v22-worker"
    task.lease_expires_at = datetime.now(UTC) + timedelta(minutes=5)
    attempt = _active_attempt(task, package="com.test.snapshot")
    task.attempts.append(attempt)
    db_session.add(task)
    db_session.commit()
    submitted: list[str] = []

    class Artemis:
        async def submit(self, **kwargs):
            submitted.append(kwargs["app_package"])
            return ArtemisResult(session_id=kwargs["session_id"], status="running")

    deps = cast(
        PublishDependencies,
        SimpleNamespace(
            session_factory=lambda: Session(
                bind=db_session.get_bind(), join_transaction_mode="create_savepoint"
            ),
            instance_id="TEST-v22-worker",
            lease_seconds=30,
            max_attempts=3,
            poll_seconds=0,
            store=SimpleNamespace(),
            adb=SimpleNamespace(),
            artemis=Artemis(),
            spool_dir=tmp_path,
            artemis_profile="pro",
            artemis_verification_level="strict",
        ),
    )

    await _execute(task.public_id, deps)

    assert submitted == ["com.test.snapshot"]


@pytest.mark.asyncio
async def test_unexpected_post_creation_exception_preserves_same_session_recovery(
    db_session: Session, tmp_path: Path
) -> None:
    task = _task(status="running", stage="dispatching_artemis")
    task.lease_owner = "TEST-v22-crashing-worker"
    task.lease_expires_at = datetime.now(UTC) + timedelta(minutes=5)
    attempt = _active_attempt(task, package="com.test.snapshot")
    task.attempts.append(attempt)
    db_session.add(task)
    db_session.commit()
    session_id = attempt.artemis_session_id

    class Artemis:
        def __init__(self) -> None:
            self.crash = True
            self.queried: list[object] = []

        async def submit(self, **_kwargs):
            if self.crash:
                self.crash = False
                raise RuntimeError("TEST injected post-attempt exception")
            raise AssertionError("recovery must query, not create a new session")

        async def get_task(self, recovered_session_id):
            self.queried.append(recovered_session_id)
            return ArtemisResult(session_id=recovered_session_id, status="running")

    artemis = Artemis()
    deps = cast(
        PublishDependencies,
        SimpleNamespace(
            session_factory=lambda: Session(
                bind=db_session.get_bind(), join_transaction_mode="create_savepoint"
            ),
            instance_id="TEST-v22-crashing-worker",
            lease_seconds=30,
            max_attempts=3,
            poll_seconds=0,
            store=SimpleNamespace(),
            adb=SimpleNamespace(),
            artemis=artemis,
            spool_dir=tmp_path,
            artemis_profile="pro",
            artemis_verification_level="strict",
        ),
    )

    await _execute(task.public_id, deps)
    db_session.expire_all()
    assert task.status == "running"
    assert task.stage == "waiting_artemis"
    assert task.lease_owner is None
    assert attempt.status == "unknown"

    assert await recover_active(deps) == "recovered"
    assert artemis.queried == [session_id]
    db_session.expire_all()
    assert (
        db_session.scalar(
            select(VideoPublishAttempt.artemis_session_id).where(
                VideoPublishAttempt.task_id == task.id
            )
        )
        == session_id
    )
    assert (
        db_session.scalar(
            select(func.count())
            .select_from(VideoPublishAttempt)
            .where(VideoPublishAttempt.task_id == task.id)
        )
        == 1
    )


def test_attempt_submission_snapshot_fields_are_immutable_in_postgresql(
    db_session: Session,
) -> None:
    task = _task(status="running", stage="waiting_artemis")
    task.lease_owner = "TEST-v22-immutability"
    task.lease_expires_at = datetime.now(UTC) + timedelta(minutes=5)
    attempt = _active_attempt(task, package="com.test.snapshot")
    task.attempts.append(attempt)
    db_session.add(task)
    db_session.commit()

    mutations = (
        (
            "UPDATE publishing.video_publish_attempts SET prompt_version=:value WHERE id=:attempt_id",
            "TEST-mutated-version",
        ),
        (
            "UPDATE publishing.video_publish_attempts SET prompt_snapshot=:value WHERE id=:attempt_id",
            "TEST mutated prompt",
        ),
        (
            "UPDATE publishing.video_publish_attempts SET device_serial=:value WHERE id=:attempt_id",
            "TESTMUTATEDSERIAL",
        ),
        (
            "UPDATE publishing.video_publish_attempts SET device_path=:value WHERE id=:attempt_id",
            "/sdcard/Movies/TTSERP/tts_erp_mutated.mp4",
        ),
        (
            "UPDATE publishing.video_publish_attempts SET target_app_package=:value WHERE id=:attempt_id",
            "com.test.mutated",
        ),
    )
    for statement, value in mutations:
        with pytest.raises(Exception, match="identity fields are immutable"):
            db_session.execute(
                text(statement),
                {"value": value, "attempt_id": attempt.id},
            )
            db_session.commit()
        db_session.rollback()


def test_metrics_include_all_owner_scoped_pending_cleanup_gauges(
    db_session: Session,
) -> None:
    task = _task(status="cancelled", stage="done")
    task.cleanup_intent = "preserve_state"
    task.spool_cleanup_status = "pending"
    task.object_cleanup_status = "pending"
    db_session.add(task)
    db_session.commit()

    payload = api.metrics(_request(), db_session)

    assert payload["cleanup"]["spoolPending"] == 1
    assert payload["cleanup"]["objectPending"] == 1


def test_v22_docs_and_layout_match_shipped_contracts() -> None:
    design = (ROOT / "docs/design/tiktok-video-publish.md").read_text()
    architecture = (ROOT / "docs/architecture/architecture-overview.md").read_text()
    runbook = (ROOT / "docs/ops/video-publish-runbook.md").read_text()

    assert "`CONFIRMED_ABSENT` | pending/queued" not in design
    assert "明确未发布 → 新建 publish attempt" not in design
    assert "明确未发布 → 服务端按重试预算自动排队" not in design
    assert "`CONFIRMED_ABSENT` | needs_review/done" in design
    for line in design.splitlines():
        if (
            "CONFIRMED_ABSENT" in line
            or "not_published" in line
            or "明确未发布" in line
        ):
            assert "释放 lease并重试 publish" not in line
            assert "新建 publish attempt" not in line
            assert "服务端按重试预算自动排队" not in line
    for module in ("diagnostics.py", "observability.py", "safe_values.py"):
        assert module in design
    assert "artemis-client==" not in design
    assert "tests/api/test_video_publish.py" not in design
    assert "tests/browser/test_video_publish_page.py" not in design
    assert "0053`–`0065" in runbook
    assert "bash scripts/test_isolated.sh" in architecture
    assert "scripts/test.sh" not in architecture
    assert "tts_erp_v3_test" not in architecture


def test_install_test_deps_help_stops_at_the_comment_header() -> None:
    result = subprocess.run(
        ["bash", "scripts/envsetup/install-test-deps.sh", "--help"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    assert "set -euo pipefail" not in result.stdout
    assert "usage()" not in result.stdout
    assert "bash scripts/envsetup/install-test-deps.sh --check" in result.stdout
