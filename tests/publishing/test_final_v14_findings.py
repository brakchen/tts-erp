from __future__ import annotations

import importlib.util
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from uuid import uuid4

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from fastapi import HTTPException, Request, Response
from minio.error import S3Error
from sqlalchemy import event, func, inspect, select, text
from sqlalchemy.orm import Session

from tts_erp_v2.access import AccessGrant, AuthMode, Role
from tts_erp_v2.api.v2 import video_publish as api
from tts_erp_v2.db.models.publishing import (
    PublishWorkerHeartbeat,
    VideoPublishAttempt,
    VideoPublishTask,
)
from tts_erp_v2.publishing.adb_device import AdbDevice, ManagedAlbumNotEmpty
from tts_erp_v2.publishing.diagnostics import sanitize_text
from tts_erp_v2.publishing.dispatcher import (
    PublishDependencies,
    dispatch_one,
    run_background_cleanup_batch,
)
from tts_erp_v2.publishing.domain import CleanupIntent
from tts_erp_v2.publishing.object_store import (
    MinioVideoStore,
    ObjectVersionMismatch,
    VideoObjectStore,
)
from tts_erp_v2.publishing.prompt import build_verify_prompt
from tts_erp_v2.publishing.repository import (
    OperationalFailure,
    PublishLeaseToken,
    commit_publish_transition,
)
from tts_erp_v2.publishing.submission import CreateCommand, create_upload_ticket
from tts_erp_v2.publishing.worker import _probe_device_readiness
from tts_erp_v2.storage.minio_client import ObjectNotFound

ROOT = Path(__file__).parents[2]
MIGRATION_0062 = ROOT / "alembic/versions/0062_publish_attempt_identity.py"


def _request() -> Request:
    grant = AccessGrant(
        mode=AuthMode.ENFORCE,
        role=Role.READWRITE,
        scopes=("page:video-publish",),
        auth_method="cookie",
        bypass=True,
    )
    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/v2/video-publish",
            "headers": [
                (b"x-requested-with", b"tts-erp"),
                (b"x-request-id", b"TEST-v14-request"),
            ],
            "auth_method": "cookie",
            "access_grant": grant,
        }
    )


def _task(*, status: str = "failed", stage: str = "done") -> VideoPublishTask:
    return VideoPublishTask(
        public_id=uuid4(),
        client_request_id=uuid4(),
        caption="TEST caption",
        original_filename="TEST video 视频.mp4",
        object_filename="TEST_video_.mp4",
        content_type="video/mp4",
        size_bytes=4,
        object_bucket="tiktok-video",
        object_key=f"TEST/{uuid4()}.mp4",
        object_etag="TEST-etag-1",
        status=status,
        stage=stage,
        cleanup_intent=CleanupIntent.NONE.value,
        target_device_serial="TEST_device",
        target_app_package="com.tiktok",
        queued_at=datetime.now(UTC),
    )


def _load_0062():
    spec = importlib.util.spec_from_file_location("migration_0062_test", MIGRATION_0062)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_0062_backfills_object_filename_without_overwriting_original_basename(
    db_engine,
) -> None:
    migration = _load_0062()
    with db_engine.connect() as conn:
        transaction = conn.begin()
        try:
            migration.__dict__["op"] = Operations(MigrationContext.configure(conn))
            migration.downgrade()
            task_id = conn.scalar(
                text("""
                INSERT INTO publishing.video_publish_tasks (
                  client_request_id, caption, original_filename, content_type,
                  size_bytes, object_bucket, object_key, status, stage,
                  target_device_serial, target_app_package
                ) VALUES (:request_id, 'TEST', '测试 video 01.mp4', 'video/mp4', 4,
                  'tiktok-video', :key, 'failed', 'done', 'TEST_device', 'com.tiktok')
                RETURNING id
                """),
                {
                    "request_id": uuid4(),
                    "key": f"video-publish/TEST/{uuid4()}/video_01.mp4",
                },
            )
            migration.upgrade()
            # pi-lens-ignore: python-sql-injection
            row = conn.execute(
                text("""
                SELECT original_filename, object_filename
                FROM publishing.video_publish_tasks WHERE id=:id
                """),
                {"id": task_id},
            ).one()
            assert row == ("测试 video 01.mp4", "video_01.mp4")
        finally:
            transaction.rollback()


def test_0062_attempt_identity_is_immutable_but_lifecycle_is_mutable(db_engine) -> None:
    migration = _load_0062()
    with db_engine.connect() as conn:
        transaction = conn.begin()
        try:
            migration.__dict__["op"] = Operations(MigrationContext.configure(conn))
            assert migration.revision == "0062_publish_attempt_identity"
            task_ids = []
            for suffix in ("a", "b"):
                task_ids.append(
                    conn.scalar(
                        text("""
                        INSERT INTO publishing.video_publish_tasks (
                          client_request_id, caption, original_filename, object_filename,
                          content_type, size_bytes, object_bucket, object_key, status,
                          stage, target_device_serial, target_app_package
                        ) VALUES (:request_id, 'TEST', 'TEST video.mp4', 'TEST_video.mp4',
                          'video/mp4', 4, 'tiktok-video', :key, 'failed', 'done',
                          'TEST_device', 'com.tiktok') RETURNING id
                        """),
                        {"request_id": uuid4(), "key": f"TEST/{suffix}-{uuid4()}.mp4"},
                    )
                )
            publish_id = conn.scalar(
                text("""
                INSERT INTO publishing.video_publish_attempts (
                  task_id, sequence_no, kind, artemis_session_id, status,
                  prompt_version, prompt_snapshot, device_serial
                ) VALUES (:task_id, 1, 'publish', :session_id, 'failed',
                  'TEST', 'TEST', 'TEST_device') RETURNING id
                """),
                {"task_id": task_ids[0], "session_id": uuid4()},
            )
            verify_id = conn.scalar(
                text("""
                INSERT INTO publishing.video_publish_attempts (
                  task_id, sequence_no, kind, related_attempt_id, artemis_session_id,
                  status, prompt_version, prompt_snapshot, device_serial
                ) VALUES (:task_id, 2, 'verify', :publish_id, :session_id, 'created',
                  'TEST', 'TEST', 'TEST_device') RETURNING id
                """),
                {
                    "task_id": task_ids[0],
                    "publish_id": publish_id,
                    "session_id": uuid4(),
                },
            )
            mutations = [
                (
                    "UPDATE publishing.video_publish_attempts SET task_id=:value WHERE id=:id",
                    task_ids[1],
                    publish_id,
                ),
                (
                    "UPDATE publishing.video_publish_attempts SET sequence_no=:value WHERE id=:id",
                    3,
                    publish_id,
                ),
                (
                    "UPDATE publishing.video_publish_attempts SET artemis_session_id=:value WHERE id=:id",
                    uuid4(),
                    publish_id,
                ),
                (
                    "UPDATE publishing.video_publish_attempts SET kind='verify', related_attempt_id=:value WHERE id=:id",
                    publish_id,
                    publish_id,
                ),
                (
                    "UPDATE publishing.video_publish_attempts SET related_attempt_id=NULL WHERE id=:id",
                    None,
                    verify_id,
                ),
            ]
            for statement, value, attempt_id in mutations:
                savepoint = conn.begin_nested()
                with pytest.raises(Exception, match="identity fields are immutable"):
                    # pi-lens-ignore: python-sql-injection
                    conn.execute(text(statement), {"value": value, "id": attempt_id})
                savepoint.rollback()
            # pi-lens-ignore: python-sql-injection
            conn.execute(
                text("""
                UPDATE publishing.video_publish_attempts
                SET status='success', artemis_output='{"verdict":"published"}'::jsonb,
                    retry_safe=false, finished_at=clock_timestamp()
                WHERE id=:id
                """),
                {"id": verify_id},
            )
            assert (
                conn.scalar(
                    text(
                        "SELECT status FROM publishing.video_publish_attempts WHERE id=:id"
                    ),
                    {"id": verify_id},
                )
                == "success"
            )
            migration.__dict__["op"] = Operations(MigrationContext.configure(conn))
            savepoint = conn.begin_nested()
            with pytest.raises(Exception, match="0062 downgrade refused"):
                migration.downgrade()
            savepoint.rollback()
        finally:
            transaction.rollback()


def test_0062_live_schema_has_identity_trigger_and_object_filename(
    db_session: Session,
) -> None:
    triggers = (
        db_session.execute(
            text("""
        SELECT tgname FROM pg_trigger
        WHERE tgrelid='publishing.video_publish_attempts'::regclass
          AND NOT tgisinternal
        """)
        )
        .scalars()
        .all()
    )
    assert "trg_video_publish_attempt_identity" in triggers
    columns = {
        row["name"]
        for row in inspect(db_session.get_bind()).get_columns(
            "video_publish_tasks", schema="publishing"
        )
    }
    assert "object_filename" in columns


def test_plain_text_credentials_are_redacted_and_bounded() -> None:
    raw = (
        "password=TEST_PASSWORD_VALUE\nCookie: session=TEST_COOKIE_VALUE; theme=dark\n"
        "token: TEST_TOKEN_VALUE\napi_key = TEST_API_KEY_VALUE\n"
        "Authorization: Basic TEST_AUTH_VALUE\nsecret='TEST_SECRET_VALUE'\n"
        "session_id: TEST_SESSION_VALUE\n" + "x" * 5000
    )
    cleaned = sanitize_text(raw)
    for secret in (
        "TEST_PASSWORD_VALUE",
        "TEST_COOKIE_VALUE",
        "TEST_TOKEN_VALUE",
        "TEST_API_KEY_VALUE",
        "TEST_AUTH_VALUE",
        "TEST_SECRET_VALUE",
        "TEST_SESSION_VALUE",
    ):
        assert secret not in cleaned
    assert cleaned.count("[REDACTED]") >= 7
    assert len(cleaned) <= 2000


def test_plain_text_credentials_are_redacted_at_persistence_and_response(
    db_session: Session,
) -> None:
    sensitive_value = "TEST_PASSWORD_PERSISTED"
    task = _task(status="running", stage="downloading")
    task.lease_owner = "TEST-worker"
    task.lease_expires_at = datetime.now(UTC) + timedelta(minutes=1)
    db_session.add(task)
    db_session.flush()
    db_session.commit()

    def factory():
        return Session(
            # pi-lens-ignore: python-sql-injection
            bind=db_session.get_bind(),
            join_transaction_mode="create_savepoint",
        )

    commit_publish_transition(
        factory,
        PublishLeaseToken(task.public_id, "TEST-worker", task.row_version),
        OperationalFailure(
            action="failed",
            code="TEST_FAILURE",
            max_attempts=3,
            message=f"password={sensitive_value}" + "x" * 5000,
        ),
    )
    db_session.expire_all()
    assert sensitive_value not in (task.last_error_message or "")
    assert "[REDACTED]" in (task.last_error_message or "")
    assert len(task.last_error_message or "") <= 2000
    payload = api._snapshot(
        task, summary_attempts=[], publish_attempt_count=0, verify_attempt_count=0
    )
    assert sensitive_value not in str(payload)
    assert "[REDACTED]" in str(payload)


class _TicketStore:
    bucket = "tiktok-video"
    default_expiry = timedelta(minutes=15)

    def __init__(self, *, fail_presign: bool = False) -> None:
        self.fail_presign = fail_presign

    def presign_put(self, key: str, content_type: str) -> str:
        del key, content_type
        if self.fail_presign:
            raise RuntimeError("password=TEST_secret")
        return "https://upload.test/signed"


def test_original_browser_basename_is_preserved_separately_from_object_filename(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ARTEMIS_DEVICE_SERIAL", "TEST_device")
    monkeypatch.setenv("TIKTOK_PUBLISH_MINIO_BUCKET", "tiktok-video")
    task, _url, _replay = create_upload_ticket(
        db_session,
        CreateCommand(uuid4(), "测试 video 01.mp4", "video/mp4", 4, "TEST caption"),
        cast(VideoObjectStore, _TicketStore()),
    )
    assert task.original_filename == "测试 video 01.mp4"
    assert task.object_filename == "video_01.mp4"
    assert task.object_key.endswith("/video_01.mp4")
    snapshot = api._snapshot(
        task, summary_attempts=[], publish_attempt_count=0, verify_attempt_count=0
    )
    assert snapshot["filename"] == "测试 video 01.mp4"
    assert snapshot["objectFilename"] == "video_01.mp4"


def test_presign_failure_is_structured_and_keeps_awaiting_upload(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ARTEMIS_DEVICE_SERIAL", "TEST_device")
    monkeypatch.setenv("TIKTOK_PUBLISH_MINIO_BUCKET", "tiktok-video")
    monkeypatch.setattr(api, "_fresh_ready_worker", lambda _session: object())
    body = api.CreateIn(
        clientRequestId=uuid4(),
        filename="TEST video.mp4",
        contentType="video/mp4",
        sizeBytes=4,
        caption="TEST caption",
    )
    with pytest.raises(HTTPException) as exc_info:
        api.create_task(
            body,
            _request(),
            db_session,
            cast(VideoObjectStore, _TicketStore(fail_presign=True)),
            Response(),
        )
    assert exc_info.value.status_code == 503
    assert exc_info.value.detail == {
        "code": "OBJECT_STORE_UNAVAILABLE",
        "message": "OBJECT_STORE_UNAVAILABLE",
        "retryable": True,
        "requestId": "TEST-v14-request",
    }
    task = db_session.scalar(
        select(VideoPublishTask).where(
            VideoPublishTask.client_request_id == body.client_request_id
        )
    )
    assert task is not None
    assert (task.status, task.stage) == ("pending", "awaiting_upload")


def test_idempotent_replay_presign_failure_is_structured(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ARTEMIS_DEVICE_SERIAL", "TEST_device")
    monkeypatch.setenv("TIKTOK_PUBLISH_MINIO_BUCKET", "tiktok-video")
    request_id = uuid4()
    command = CreateCommand(
        request_id, "TEST replay.mp4", "video/mp4", 4, "TEST caption"
    )
    create_upload_ticket(db_session, command, cast(VideoObjectStore, _TicketStore()))
    with pytest.raises(HTTPException) as exc_info:
        api.create_task(
            api.CreateIn(
                clientRequestId=request_id,
                filename=command.filename,
                contentType=command.content_type,
                sizeBytes=command.size_bytes,
                caption=command.caption,
            ),
            _request(),
            db_session,
            cast(VideoObjectStore, _TicketStore(fail_presign=True)),
            Response(),
        )
    assert exc_info.value.status_code == 503
    detail = cast(dict[str, Any], exc_info.value.detail)
    assert detail["code"] == "OBJECT_STORE_UNAVAILABLE"
    assert detail["retryable"] is True
    assert detail["requestId"] == "TEST-v14-request"
    task = db_session.scalar(
        select(VideoPublishTask).where(VideoPublishTask.client_request_id == request_id)
    )
    assert task is not None
    assert (task.status, task.stage) == ("pending", "awaiting_upload")


def test_refresh_presign_failure_is_structured(db_session: Session) -> None:
    task = _task(status="pending", stage="awaiting_upload")
    db_session.add(task)
    db_session.flush()
    with pytest.raises(HTTPException) as exc_info:
        api.refresh_upload_url(
            task.public_id,
            _request(),
            db_session,
            cast(VideoObjectStore, _TicketStore(fail_presign=True)),
        )
    assert exc_info.value.status_code == 503
    detail = cast(dict[str, Any], exc_info.value.detail)
    assert detail["code"] == "OBJECT_STORE_UNAVAILABLE"
    assert detail["retryable"] is True
    assert detail["requestId"] == "TEST-v14-request"
    assert detail["rowVersion"] == task.row_version


class _Body:
    def __init__(self, chunks: list[bytes]) -> None:
        self.chunks = chunks

    def stream(self, _size: int):
        yield from self.chunks

    def close(self) -> None:
        pass

    def release_conn(self) -> None:
        pass


def _s3_error(code: str) -> S3Error:
    return S3Error(code, code, "TEST", "TEST", None, None)  # type: ignore[arg-type]


@pytest.mark.parametrize("etag", ["plain-etag", "abc-5"])
def test_download_uses_if_match_for_plain_and_multipart_etags(
    tmp_path: Path, etag: str
) -> None:
    calls: list[dict[str, str]] = []

    def get_object(_bucket, _key, *, request_headers=None):
        calls.append(cast(dict[str, str], request_headers))
        return _Body([b"DIFF"])

    store = MinioVideoStore(
        cast(
            Any,
            SimpleNamespace(
                bucket="tiktok-video", _sdk=SimpleNamespace(get_object=get_object)
            ),
        )
    )
    destination = tmp_path / "video.mp4"
    store.download("TEST/key", destination, etag)
    assert calls == [{"If-Match": f'"{etag}"'}]
    assert destination.read_bytes() == b"DIFF"


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (_s3_error("PreconditionFailed"), ObjectVersionMismatch),
        (_s3_error("NoSuchKey"), ObjectNotFound),
    ],
)
def test_conditional_download_fails_closed_on_replacement_or_missing(
    tmp_path: Path, error: Exception, expected: type[Exception]
) -> None:
    sdk = SimpleNamespace(
        get_object=lambda *_args, **_kwargs: (_ for _ in ()).throw(error)
    )
    store = MinioVideoStore(cast(Any, SimpleNamespace(bucket="tiktok-video", _sdk=sdk)))
    with pytest.raises(expected):
        store.download("TEST/key", tmp_path / "video.mp4", "confirmed-etag")
    assert not (tmp_path / "video.mp4").exists()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("error", "code", "object_cleanup_required"),
    [
        (
            ObjectVersionMismatch("CONFIRMED_OBJECT_REPLACED"),
            "CONFIRMED_OBJECT_REPLACED",
            True,
        ),
        (ObjectNotFound("TEST/key"), "CONFIRMED_OBJECT_MISSING", True),
    ],
)
async def test_dispatch_exposes_replacement_after_confirmed_object_recovery(
    db_session: Session,
    tmp_path: Path,
    error: Exception,
    code: str,
    object_cleanup_required: bool,
) -> None:
    class Store:
        def download(self, _key, _destination, _expected_etag):
            raise error

        def remove(self, _key, _expected_etag=None):
            return None

    class Adb:
        def device_path(self, _task_id):
            raise AssertionError("device staging must not begin")

    task = _task(status="pending", stage="queued")
    task.object_uploaded_at = datetime.now(UTC)
    db_session.add(task)
    db_session.flush()
    db_session.commit()
    deps = cast(
        PublishDependencies,
        SimpleNamespace(
            session_factory=lambda: Session(
                # pi-lens-ignore: python-sql-injection
                bind=db_session.get_bind(),
                join_transaction_mode="create_savepoint",
            ),
            instance_id="TEST-v14-worker",
            lease_seconds=30,
            max_attempts=3,
            store=Store(),
            adb=Adb(),
            spool_dir=tmp_path,
        ),
    )
    assert await dispatch_one(deps) == "processed"
    db_session.expire_all()
    assert (task.status, task.stage, task.last_error_code) == ("failed", "done", code)
    assert task.attempt_count == 0
    first_actions = api._snapshot(task, summary_attempts=[])["allowedActions"]
    assert task.spool_cleanup_status == "pending"
    assert task.object_cleanup_status == (
        "pending" if object_cleanup_required else "succeeded"
    )
    assert "replace_upload" not in first_actions
    assert await run_background_cleanup_batch(deps, limit=1) == 1
    db_session.expire_all()
    assert task.object_deleted_at is not None
    assert (
        "replace_upload" in api._snapshot(task, summary_attempts=[])["allowedActions"]
    )
    assert (
        db_session.scalar(
            select(func.count())
            .select_from(VideoPublishAttempt)
            .where(VideoPublishAttempt.task_id == task.id)
        )
        == 0
    )


@pytest.mark.asyncio
async def test_download_size_mismatch_keeps_tracked_spool_cleanup(
    db_session: Session, tmp_path: Path
) -> None:
    class Store:
        def download(self, _key, destination, _expected_etag):
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(b"BAD")
            return "TEST-sha256"

    class Adb:
        def device_path(self, _task_id):
            raise AssertionError("device preflight must not begin")

    task = _task(status="pending", stage="queued")
    task.object_uploaded_at = datetime.now(UTC)
    db_session.add(task)
    db_session.commit()
    deps = cast(
        PublishDependencies,
        SimpleNamespace(
            session_factory=lambda: Session(
                bind=db_session.get_bind(), join_transaction_mode="create_savepoint"
            ),
            instance_id="TEST-v18-size-worker",
            lease_seconds=30,
            max_attempts=3,
            store=Store(),
            adb=Adb(),
            spool_dir=tmp_path,
        ),
    )
    assert await dispatch_one(deps) == "processed"
    db_session.expire_all()
    assert (task.status, task.stage) == ("pending", "queued")
    assert task.last_error_code == "WORKER_OPERATION_FAILED"
    assert task.spool_cleanup_status == "pending"
    assert task.cleanup_intent == "requeue_publish"


@pytest.mark.asyncio
async def test_album_residue_stops_workflow_before_artemis_attempt(
    db_session: Session, tmp_path: Path
) -> None:
    class Store:
        def download(self, _key, destination, _expected_etag):
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(b"TEST")
            return "TEST-sha256"

    class Adb:
        def device_path(self, _task_id):
            return "/sdcard/Movies/TTSERP/tts_erp_TEST.mp4"

        async def check_device(self, _serial):
            pass

        async def check_package(self, _serial, _package):
            pass

        async def ensure_album_empty(self, _serial):
            raise ManagedAlbumNotEmpty("managed video residue requires cleanup")

    class Artemis:
        async def submit(self, **_kwargs):
            raise AssertionError("Artemis attempt must not be created")

    task = _task(status="pending", stage="queued")
    task.object_uploaded_at = datetime.now(UTC)
    db_session.add(task)
    db_session.flush()
    db_session.commit()
    deps = cast(
        PublishDependencies,
        SimpleNamespace(
            session_factory=lambda: Session(
                # pi-lens-ignore: python-sql-injection
                bind=db_session.get_bind(),
                join_transaction_mode="create_savepoint",
            ),
            instance_id="TEST-v14-album-worker",
            lease_seconds=30,
            max_attempts=3,
            store=Store(),
            adb=Adb(),
            artemis=Artemis(),
            spool_dir=tmp_path,
        ),
    )
    assert await dispatch_one(deps) == "processed"
    db_session.expire_all()
    assert (task.status, task.stage) == ("pending", "waiting_device"), (
        task.last_error_code,
        task.last_error_message,
        task.spool_cleanup_status,
        task.cleanup_intent,
    )
    assert task.spool_cleanup_status == "pending"
    assert task.cleanup_intent == "requeue_publish"
    assert task.attempt_count == 0
    assert (
        db_session.scalar(
            select(func.count())
            .select_from(VideoPublishAttempt)
            .where(VideoPublishAttempt.task_id == task.id)
        )
        == 0
    )


def test_verify_prompt_contains_disambiguating_media_and_time_instructions() -> None:
    prompt = build_verify_prompt(
        caption="same caption",
        app_package="com.tiktok",
        source_filename="测试 video.mp4",
        object_identity="etag:abc-5 sha256:deadbeef",
        expected_publish_after="2026-10-05T10:00:00+00:00",
        expected_publish_before="2026-10-05T10:15:00+00:00",
    )
    assert "测试 video.mp4" in prompt
    assert "etag:abc-5" in prompt
    assert "2026-10-05T10:00:00+00:00" in prompt
    assert "newest" in prompt.lower()
    assert "thumbnail" in prompt.lower()
    assert '"published|not_published|inconclusive"' in prompt


@pytest.mark.asyncio
async def test_adb_album_preflight_rejects_any_managed_residue(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    device = AdbDevice()
    calls: list[tuple[str, ...]] = []

    async def fake_run(*args: str, timeout: float = 30) -> str:
        del timeout
        calls.append(args)
        return "/sdcard/Movies/TTSERP/tts_erp_stale.mp4\n" if "find" in args else ""

    monkeypatch.setattr(device, "_run", fake_run)
    with pytest.raises(ManagedAlbumNotEmpty, match="managed video"):
        await device.ensure_album_empty("TEST_device")
    assert any("find" in call for call in calls)


@pytest.mark.asyncio
@pytest.mark.parametrize("cleanup_mode", ["due", "leased"])
async def test_readiness_reports_due_or_leased_cleanup_busy_before_dependency_probes(
    db_session: Session, monkeypatch: pytest.MonkeyPatch, cleanup_mode: str
) -> None:
    monkeypatch.setenv("ARTEMIS_DEVICE_SERIAL", "TEST_device")
    calls: list[str] = []

    class Adb:
        async def check_device(self, _serial):
            calls.append("device")

        async def check_package(self, _serial, _package):
            calls.append("package")

    class Artemis:
        async def check_available(self):
            calls.append("artemis")

    class Store:
        def check_available(self):
            calls.append("minio")

    cleanup = _task(status="failed", stage="done")
    cleanup.cleanup_intent = "preserve_state"
    cleanup.device_cleanup_status = "pending"
    cleanup.device_cleanup_next_attempt_at = (
        datetime.now(UTC) - timedelta(seconds=1)
        if cleanup_mode == "due"
        else datetime.now(UTC) + timedelta(hours=1)
    )
    if cleanup_mode == "leased":
        cleanup.cleanup_lease_owner = "TEST-cleaner"
        cleanup.cleanup_lease_expires_at = datetime.now(UTC) + timedelta(minutes=1)
    db_session.add(cleanup)
    db_session.flush()
    db_session.commit()
    deps = SimpleNamespace(
        adb=Adb(),
        artemis=Artemis(),
        store=Store(),
        session_factory=lambda: Session(
            # pi-lens-ignore: python-sql-injection
            bind=db_session.get_bind(),
            join_transaction_mode="create_savepoint",
        ),
    )
    status_value, message = await _probe_device_readiness(cast(Any, deps))
    assert status_value == "busy"
    assert "清理" in message
    assert calls == []


@pytest.mark.asyncio
async def test_full_readiness_probe_checks_package_artemis_and_minio(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ARTEMIS_DEVICE_SERIAL", "TEST_device")
    calls: list[str] = []

    class Adb:
        async def check_device(self, _serial):
            calls.append("device")

        async def check_package(self, _serial, _package):
            calls.append("package")

    class Artemis:
        async def check_available(self):
            calls.append("artemis")

    class Store:
        def check_available(self):
            calls.append("minio")

    deps = SimpleNamespace(
        adb=Adb(),
        artemis=Artemis(),
        store=Store(),
        session_factory=lambda: Session(
            # pi-lens-ignore: python-sql-injection
            bind=db_session.get_bind(),
            join_transaction_mode="create_savepoint",
        ),
    )
    status_value, message = await _probe_device_readiness(cast(Any, deps))
    assert status_value == "ready"
    assert "均可用" in message
    assert calls == ["device", "package", "artemis", "minio"]


def test_offline_or_locked_readiness_does_not_block_creation(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ARTEMIS_DEVICE_SERIAL", "TEST_device")
    now = datetime.now(UTC)
    heartbeat = PublishWorkerHeartbeat(
        instance_id="TEST-worker-v14",
        hostname="TEST-host",
        pid=1,
        status="ready",
        device_status="locked",
        device_message="设备锁屏",
        started_at=now,
        heartbeat_at=now,
    )
    db_session.add(heartbeat)
    db_session.flush()
    payload = api.config(_request(), db_session)
    assert payload["device"]["status"] == "locked"
    assert payload["canWrite"] is True
    assert payload["writeBlockReason"] is None


def test_list_and_current_attempt_summary_queries_are_bounded(
    db_session: Session,
) -> None:
    for index in range(4):
        task = _task()
        task.object_key = f"TEST/summary-{index}-{uuid4()}.mp4"
        task.client_request_id = uuid4()
        for sequence in range(1, 6):
            task.attempts.append(
                VideoPublishAttempt(
                    sequence_no=sequence,
                    kind="publish",
                    status="failed",
                    artemis_session_id=uuid4(),
                    prompt_version="TEST",
                    prompt_snapshot="TEST",
                    device_serial="TEST_device",
                )
            )
        task.attempt_count = 5
        task.publish_budget_used = 3
        db_session.add(task)
    db_session.flush()
    attempt_selects = 0

    def count_attempt_selects(_conn, _cursor, statement, _parameters, _context, _many):
        nonlocal attempt_selects
        if (
            statement.lstrip().upper().startswith("SELECT")
            and "video_publish_attempts" in statement
        ):
            attempt_selects += 1

    event.listen(db_session.get_bind(), "before_cursor_execute", count_attempt_selects)
    try:
        api.list_tasks(
            _request(),
            db_session,
            Response(),
            status_filter=None,
            limit=30,
            cursor=None,
        )
        assert attempt_selects == 2
        attempt_selects = 0
        api.current(_request(), db_session, Response())
        assert attempt_selects <= 2
    finally:
        event.remove(
            db_session.get_bind(), "before_cursor_execute", count_attempt_selects
        )


def test_frontend_resets_dialogs_handles_escape_and_cleans_drawer_on_close() -> None:
    source = (ROOT / "tts_erp_v2/static/js/video-publish.js").read_text()
    assert "function showConfirmationDialog(dialog)" in source
    assert 'dialog.returnValue = "cancel"' in source
    assert 'dialog.addEventListener("cancel", onCancel)' in source
    assert 'dialog.removeEventListener("cancel", onCancel)' in source
    assert '$("publish-task-drawer").addEventListener("close"' in source
    close_section = source[
        source.index('$("publish-task-drawer").addEventListener("close"') :
    ]
    assert "state.detailTaskId = null" in close_section
    assert "state.detailChannel.controller?.abort()" in close_section
    assert "clearTimeout(state.detailTimer)" in close_section
    assert "opener?.focus?.()" in close_section
    assert "writeBlockReason" in source


def test_registry_and_docs_cover_v14_contracts() -> None:
    registry = (ROOT / "docs/handoff/ACTIVE.md").read_text()
    design = (ROOT / "docs/design/tiktok-video-publish.md").read_text()
    runbook = (ROOT / "docs/ops/video-publish-runbook.md").read_text()
    architecture = (ROOT / "docs/architecture/architecture-overview.md").read_text()
    for path in (
        "alembic/env.py",
        "scripts/systemd/tts-erp-publish.service",
        "tts_erp_v2/access/",
        "tts_erp_v2/accounts/pages.py",
        "tts_erp_v2/api/v2/pages.py",
        "tts_erp_v2/app.py",
        "tts_erp_v2/db/base.py",
        "tts_erp_v2/db/models/",
    ):
        assert path in registry
    assert "created_by_user_id" in design and "非 FK" in design
    assert "created_by_key_hash" in design
    assert "publish_budget_used" in design
    assert "cleanup_lease_owner" in design
    assert "stage_started_at" in design
    assert "device_status" in design
    assert "13 个非 public 业务 schema / 71 张业务表" in architecture
    assert (
        "bash scripts/test_isolated.sh --refresh-template fast tests/publishing"
        in runbook
    )
    assert "systemctl --user daemon-reload" in runbook
    assert "systemctl --user enable --now tts-erp-publish.service" in runbook
