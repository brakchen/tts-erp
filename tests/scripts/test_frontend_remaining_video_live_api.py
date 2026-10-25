"""Video-publish real API acceptance against the owned isolated child.

Real authentication, CSRF, application sessions and SQL over the maintained
fixed collaborator profile: the upload bytes reach the existing controlled
loopback store and the device read is the fixed TEST device. No worker, job,
device, TikTok, callback or publication runs. The opaque TEST bytes prove the
HTTP storage hash only -- never playable media, native CORS, progress or abort
-- and the SQL facts prove the row state only, not object-store retention.
This is not native, visual, runtime or deployment acceptance.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterator
from contextlib import ExitStack
from dataclasses import dataclass
from datetime import UTC, datetime
from urllib.parse import urlsplit
from uuid import UUID, uuid4

import httpx
import pytest
from sqlalchemy import delete, select, text
from sqlalchemy.orm import Session

from tests.scripts import test_frontend_remaining_live_api as original
from tests.scripts.test_frontend_remaining_live_api import OwnedClone, login, owned
from tests.support.frontend_p2_binding import checked_session
from tests.support.frontend_p2_canary import require_data_probe
from tts_erp_v2.db.models.publishing import (
    PublishWorkerHeartbeat,
    VideoPublishAttempt,
    VideoPublishConfirmation,
    VideoPublishTask,
)
from tts_erp_v2.publishing.safe_values import mask_device_serial

# Direct alias of the original decorated clone fixture: same params, same
# owned child, probe, accounts, checked session, retained Popen and nonce.
clone = original.clone

pytestmark = [pytest.mark.domain_frontend, pytest.mark.layer_integration]
CSRF = {"X-Requested-With": "tts-erp"}
VIDEO_BYTES = 32
TEST_DEVICE = "TEST_device"


def _code(response: httpx.Response) -> str:
    """Handler error code; empty for a middleware denial that carries none."""
    detail = response.json().get("detail")
    if isinstance(detail, dict):
        code = detail.get("code")
        return code if isinstance(code, str) else ""
    return ""


def _flag(value: bool) -> bool:
    """Pass a comparison through as a bool, failing inside so no operand is echoed."""
    if not value:
        raise AssertionError("privacy flag failed")
    return True


def _session(clone: OwnedClone):
    """The original checked session; no second engine or factory is built."""
    return checked_session(clone.child.binding, clone.engine, clone.factory)


def _post(
    clone: OwnedClone,
    client: httpx.Client,
    path: str,
    body: dict,
    status: int,
    *,
    csrf: bool = True,
) -> httpx.Response:
    """One owned API call, checked against the retained receipt and nonce."""
    headers = CSRF if csrf else None
    return owned(clone, client.post(path, json=body, headers=headers), status)


def _create(
    clone: OwnedClone,
    client: httpx.Client,
    marker: str,
    client_request_id: str,
    *,
    filename: str,
    caption: str | None = None,
    size: int = VIDEO_BYTES,
    status: int = 201,
    csrf: bool = True,
) -> httpx.Response:
    """One owned create call, with the body the server's aliases name."""
    body = {
        "clientRequestId": client_request_id,
        "filename": filename,
        "contentType": "video/mp4",
        "sizeBytes": size,
        "caption": marker if caption is None else caption,
    }
    path = clone.prefix + "/v2/video-publish/tasks"
    return _post(clone, client, path, body, status, csrf=csrf)


def _states(clone: OwnedClone, client_request_ids: list[str]) -> dict[str, dict]:
    """Own-fact SQL projection: booleans instead of raw stored material."""
    with _session(clone) as session:
        clone.child.require_live()
        keys = VideoPublishTask.client_request_id.in_(client_request_ids)
        rows = session.scalars(select(VideoPublishTask).where(keys))
        return {
            str(row.client_request_id): {
                "public_id": str(row.public_id),
                "status": row.status,
                "stage": row.stage,
                "row_version": row.row_version,
                "caption": row.caption,
                "original_filename": row.original_filename,
                "content_type": row.content_type,
                "size_bytes": row.size_bytes,
                "object_stored": row.object_etag is not None,
                "object_uploaded": row.object_uploaded_at is not None,
                "object_deleted": row.object_deleted_at is not None,
                "queued": row.queued_at is not None,
                "completed": row.completed_at is not None,
                "created_by_user_id": row.created_by_user_id,
                "device_is_test": row.target_device_serial == TEST_DEVICE,
            }
            for row in rows
        }


@dataclass
class _Intent:
    """One owned create intent, known before the request is sent.

    A refusal that unexpectedly created a row returns no id, so this marker
    tuple is the real ownership proof for the cleanup.
    """

    client_request_id: str
    filename: str
    created_by_user_id: int
    public_id: str | None = None


def _intent(clone: OwnedClone, role: str, key: str, filename: str) -> _Intent:
    """One owned create intent of an original principal, recorded up front."""
    return _Intent(key, filename, clone.accounts[role].user_pk)


def _owned_rows(session: Session, intent: _Intent) -> list[VideoPublishTask]:
    """Exactly the rows one known intent owns, matched by its own marker."""
    query = select(VideoPublishTask).where(
        VideoPublishTask.client_request_id == intent.client_request_id,
        VideoPublishTask.original_filename == intent.filename,
        VideoPublishTask.created_by_user_id == intent.created_by_user_id,
    )
    return list(session.scalars(query))


def _purge(clone: OwnedClone, intents: list[_Intent]) -> None:
    """Delete only the rows this test owns, then prove fresh absence."""
    with _session(clone) as session:
        clone.child.require_live()
        pks: list[int] = []
        for intent in intents:
            for row in _owned_rows(session, intent):
                if intent.public_id is not None:
                    assert str(row.public_id) == intent.public_id
                pks.append(row.id)
        if pks:
            session.execute(
                delete(VideoPublishAttempt).where(VideoPublishAttempt.task_id.in_(pks))
            )
            session.execute(
                delete(VideoPublishConfirmation).where(
                    VideoPublishConfirmation.task_id.in_(pks)
                )
            )
            session.execute(
                delete(VideoPublishTask).where(VideoPublishTask.id.in_(pks))
            )
        session.commit()
    with _session(clone) as session:
        clone.child.require_live()
        for intent in intents:
            assert _owned_rows(session, intent) == []
        for model, owner in (
            (VideoPublishAttempt, VideoPublishAttempt.task_id),
            (VideoPublishConfirmation, VideoPublishConfirmation.task_id),
        ):
            assert session.scalar(select(model.id).where(owner.in_(pks))) is None


def _arm_worker(clone: OwnedClone, instance_id: str, *, fresh: bool) -> None:
    """Move only my own TEST readiness row in time; no worker process runs."""
    with _session(clone) as session:
        clone.child.require_live()
        if fresh:
            statement = text(
                "UPDATE publishing.worker_heartbeats SET heartbeat_at = now() "
                "WHERE instance_id = :instance_id"
            )
        else:
            statement = text(
                "UPDATE publishing.worker_heartbeats SET heartbeat_at = "
                "now() - interval '60 seconds' WHERE instance_id = :instance_id"
            )
        session.execute(statement, {"instance_id": instance_id})
        session.commit()


@pytest.fixture(scope="module")
def sessions(clone: OwnedClone) -> Iterator[dict[str, httpx.Client]]:
    """One real session per original owned principal; no minted credential."""
    with ExitStack() as stack:
        clients: dict[str, httpx.Client] = {}
        for role in ("admin", "viewer", "operator"):
            client = stack.enter_context(
                httpx.Client(base_url=clone.child.base, trust_env=False, timeout=5)
            )
            login(clone, client, role)
            clients[role] = client
        yield clients


@pytest.fixture(scope="module")
def worker(clone: OwnedClone) -> Iterator[str]:
    """Own unique TEST readiness row only; no worker, job or device runs."""
    instance_id = "TEST_video_worker_" + uuid4().hex
    with _session(clone) as session:
        clone.child.require_live()
        session.add(
            PublishWorkerHeartbeat(
                instance_id=instance_id,
                hostname="TEST_host",
                pid=4242,
                status="ready",
                device_status="ready",
                started_at=datetime.now(UTC),
                heartbeat_at=datetime.now(UTC),
            )
        )
        session.commit()
    try:
        yield instance_id
    finally:
        with _session(clone) as session:
            clone.child.require_live()
            session.execute(
                delete(PublishWorkerHeartbeat).where(
                    PublishWorkerHeartbeat.instance_id == instance_id
                )
            )
            session.commit()
        with _session(clone) as session:
            clone.child.require_live()
            assert (
                session.scalar(
                    select(PublishWorkerHeartbeat.instance_id).where(
                        PublishWorkerHeartbeat.instance_id == instance_id
                    )
                )
                is None
            )


def test_create_is_idempotent_per_client_request_id_and_never_duplicates(
    clone: OwnedClone,
    sessions: dict[str, httpx.Client],
    worker: str,
) -> None:
    """One clientRequestId owns one task row; a replay never adds a second."""
    require_data_probe(clone.probe)
    marker = "TEST_video_idem_" + uuid4().hex
    first_key, second_key = str(uuid4()), str(uuid4())
    filename = marker + ".mp4"
    account = clone.accounts["admin"]
    admin = sessions["admin"]
    first_intent = _intent(clone, "admin", first_key, filename)
    second_intent = _intent(clone, "admin", second_key, marker + "_second.mp4")
    intents = [first_intent, second_intent]
    try:
        _arm_worker(clone, worker, fresh=False)
        refused = _create(
            clone, admin, marker, first_key, filename=filename, status=503
        )
        assert _code(refused) == "PUBLISH_WORKER_UNAVAILABLE"
        assert _states(clone, [first_key]) == {}

        _arm_worker(clone, worker, fresh=True)
        created = _create(clone, admin, marker, first_key, filename=filename).json()
        first_intent.public_id = created["taskId"]
        assert created["idempotentReplay"] is False
        assert created["clientRequestId"] == first_key
        assert created["filename"] == filename
        assert created["status"] == "pending"
        assert created["stage"] == "awaiting_upload"
        assert created["createdBy"] == f"user:{account.user_pk}"
        assert "continue_upload" in created["allowedActions"]
        assert "cancel" in created["allowedActions"]
        assert created["upload"]["method"] == "PUT"
        assert created["upload"]["headers"] == {"Content-Type": "video/mp4"}
        target = urlsplit(created["upload"]["url"])
        assert target.scheme == "http"
        assert target.hostname == "127.0.0.1"
        assert target.port is not None and target.port != clone.child.port
        minted = target.path.startswith("/TEST_upload/")
        assert minted is True

        replay = _create(
            clone, admin, marker, first_key, filename=filename, status=200
        ).json()
        assert replay["idempotentReplay"] is True
        assert replay["taskId"] == created["taskId"]
        assert replay["rowVersion"] == created["rowVersion"] + 1
        assert _flag(replay["upload"]["url"] != created["upload"]["url"]) is True

        states = _states(clone, [first_key])
        assert list(states) == [first_key]
        state = states[first_key]
        assert state["public_id"] == created["taskId"]
        assert state["status"] == "pending"
        assert state["stage"] == "awaiting_upload"
        assert state["caption"] == marker
        assert state["original_filename"] == filename
        assert state["content_type"] == "video/mp4"
        assert state["size_bytes"] == VIDEO_BYTES
        assert state["object_stored"] is False
        assert state["object_uploaded"] is False
        assert state["created_by_user_id"] == account.user_pk
        assert state["device_is_test"] is True
        assert state["row_version"] == replay["rowVersion"]

        mismatch = _create(
            clone,
            admin,
            marker,
            first_key,
            filename=filename,
            caption=marker + "_other",
            status=409,
        )
        assert _code(mismatch) == "IDEMPOTENCY_PAYLOAD_MISMATCH"
        assert _states(clone, [first_key]) == states

        second = _create(
            clone, admin, marker, second_key, filename=marker + "_second.mp4"
        ).json()
        second_intent.public_id = second["taskId"]
        assert second["taskId"] != created["taskId"]
        both = _states(clone, [first_key, second_key])
        assert set(both) == {first_key, second_key}
        assert both[first_key] == state
        assert both[second_key]["caption"] == marker
    finally:
        _purge(clone, intents)


def test_stale_row_version_is_refused_without_changing_the_row(
    clone: OwnedClone,
    sessions: dict[str, httpx.Client],
    worker: str,
) -> None:
    """A wrong or missing rowVersion is a 409 and never a partial write."""
    require_data_probe(clone.probe)
    marker = "TEST_video_version_" + uuid4().hex
    key = str(uuid4())
    filename = marker + ".mp4"
    admin = sessions["admin"]
    tasks_path = clone.prefix + "/v2/video-publish/tasks"
    intent = _intent(clone, "admin", key, filename)
    try:
        _arm_worker(clone, worker, fresh=True)
        created = _create(clone, admin, marker, key, filename=filename).json()
        intent.public_id = created["taskId"]
        before = _states(clone, [key])[key]
        task_path = tasks_path + "/" + created["taskId"]
        for path, body in (
            (task_path + "/cancel", {"rowVersion": before["row_version"] + 7}),
            (task_path + "/cancel", {}),
            (task_path + "/confirm-upload", {"rowVersion": before["row_version"] + 7}),
            (task_path + "/confirm-upload", {}),
        ):
            refused = _post(clone, admin, path, body, 409)
            assert _code(refused) == "TASK_VERSION_CONFLICT"
            detail = refused.json()["detail"]
            assert detail["rowVersion"] == before["row_version"]
            assert "cancel" in detail["allowedActions"]
            assert _states(clone, [key])[key] == before
    finally:
        _purge(clone, [intent])


def test_cancel_before_upload_confirmation_cancels_the_unpublished_task(
    clone: OwnedClone,
    sessions: dict[str, httpx.Client],
    worker: str,
) -> None:
    """Cancelling an unpublished task is a server state change, not a delete."""
    require_data_probe(clone.probe)
    marker = "TEST_video_cancel_" + uuid4().hex
    key = str(uuid4())
    filename = marker + ".mp4"
    admin = sessions["admin"]
    tasks_path = clone.prefix + "/v2/video-publish/tasks"
    intent = _intent(clone, "admin", key, filename)
    try:
        _arm_worker(clone, worker, fresh=True)
        created = _create(clone, admin, marker, key, filename=filename).json()
        intent.public_id = created["taskId"]
        assert "cancel" in created["allowedActions"]
        cancelled = _post(
            clone,
            admin,
            tasks_path + "/" + created["taskId"] + "/cancel",
            {"rowVersion": created["rowVersion"]},
            200,
        ).json()
        assert cancelled["taskId"] == created["taskId"]
        assert cancelled["status"] == "cancelled"
        assert cancelled["statusLabel"] == "已取消"
        assert cancelled["stage"] == "done"
        assert cancelled["allowedActions"] == ["view"]
        assert cancelled["caption"] == marker
        state = _states(clone, [key])[key]
        assert state["status"] == "cancelled"
        assert state["stage"] == "done"
        assert state["completed"] is True
        assert state["row_version"] == created["rowVersion"] + 1
        # Nothing was ever uploaded here, so no object-retention claim is made.
        assert state["object_uploaded"] is False

        # The list filter is real in both directions for this owned task.
        cancelled_listing = owned(
            clone, admin.get(tasks_path, params={"status": "cancelled"}), 200
        ).json()
        cancelled_ids = {item["taskId"] for item in cancelled_listing["items"]}
        assert created["taskId"] in cancelled_ids

        again = _post(
            clone,
            admin,
            tasks_path + "/" + created["taskId"] + "/cancel",
            {"rowVersion": state["row_version"]},
            409,
        )
        assert _code(again) == "TASK_ACTION_NOT_ALLOWED"
        assert _states(clone, [key])[key] == state
    finally:
        _purge(clone, [intent])


def test_readonly_and_missing_csrf_are_refused_without_writing(
    clone: OwnedClone,
    sessions: dict[str, httpx.Client],
    worker: str,
) -> None:
    """A refused write leaves no task row, and the same payload then works."""
    require_data_probe(clone.probe)
    marker = "TEST_video_deny_" + uuid4().hex
    viewer_key, operator_key, admin_key = str(uuid4()), str(uuid4()), str(uuid4())
    admin, viewer = sessions["admin"], sessions["viewer"]
    operator = sessions["operator"]
    viewer_intent = _intent(clone, "viewer", viewer_key, marker + "_viewer.mp4")
    operator_intent = _intent(clone, "operator", operator_key, marker + "_operator.mp4")
    admin_intent = _intent(clone, "admin", admin_key, marker + "_admin.mp4")
    intents = [viewer_intent, operator_intent, admin_intent]
    try:
        _arm_worker(clone, worker, fresh=True)
        _create(
            clone,
            viewer,
            marker,
            viewer_key,
            filename=marker + "_viewer.mp4",
            status=403,
        )
        assert _states(clone, [viewer_key]) == {}

        _create(
            clone,
            operator,
            marker,
            operator_key,
            filename=marker + "_operator.mp4",
            status=403,
        )
        assert _states(clone, [operator_key]) == {}

        refused = _create(
            clone,
            admin,
            marker,
            admin_key,
            filename=marker + "_admin.mp4",
            status=403,
            csrf=False,
        )
        assert _code(refused) == "CSRF_HEADER_REQUIRED"
        assert _states(clone, [admin_key]) == {}

        created = _create(
            clone, admin, marker, admin_key, filename=marker + "_admin.mp4"
        ).json()
        admin_intent.public_id = created["taskId"]
        state = _states(clone, [admin_key])[admin_key]
        assert state["caption"] == marker
        assert state["original_filename"] == marker + "_admin.mp4"
        assert state["status"] == "pending"
    finally:
        _purge(clone, intents)


def test_unexpected_success_still_leaves_no_owned_row_behind(
    clone: OwnedClone,
    sessions: dict[str, httpx.Client],
    worker: str,
) -> None:
    """A create that succeeds when the harness expected a refusal stays owned."""
    require_data_probe(clone.probe)
    marker = "TEST_video_harness_" + uuid4().hex
    key = str(uuid4())
    filename = marker + "_harness.mp4"
    admin = sessions["admin"]
    intent = _intent(clone, "admin", key, filename)
    try:
        _arm_worker(clone, worker, fresh=True)
        # The real admin with a valid CSRF header IS allowed to create, while
        # this harness intentionally expects the refusal: the server commits
        # the row, but the response never carries a captured id.
        with pytest.raises(pytest.fail.Exception):
            _create(clone, admin, marker, key, filename=filename, status=403)
        states = _states(clone, [key])
        assert key in states
        assert states[key]["original_filename"] == filename
    finally:
        _purge(clone, [intent])
    assert _states(clone, [key]) == {}


def test_owned_task_detail_list_and_diagnostics_project_only_safe_fields(
    clone: OwnedClone,
    sessions: dict[str, httpx.Client],
    worker: str,
) -> None:
    """Detail, list and diagnostics expose the owned task and nothing raw."""
    require_data_probe(clone.probe)
    marker = "TEST_video_projection_" + uuid4().hex
    key = str(uuid4())
    filename = marker + ".mp4"
    account = clone.accounts["admin"]
    admin = sessions["admin"]
    tasks_path = clone.prefix + "/v2/video-publish/tasks"
    intent = _intent(clone, "admin", key, filename)
    try:
        _arm_worker(clone, worker, fresh=True)
        created = _create(clone, admin, marker, key, filename=filename).json()
        intent.public_id = created["taskId"]
        task_id = created["taskId"]
        task_path = tasks_path + "/" + task_id

        detail = owned(clone, admin.get(task_path), 200).json()
        assert _flag(detail["taskId"] == task_id) is True
        assert _flag(detail["clientRequestId"] == key) is True
        assert _flag(detail["filename"] == filename) is True
        assert _flag(detail["caption"] == marker) is True
        assert _flag(detail["status"] == "pending") is True
        assert _flag(detail["stage"] == "awaiting_upload") is True
        assert _flag(detail["attempts"] == []) is True
        assert _flag(detail["confirmation"] is None) is True
        assert _flag(detail["paramsLockedAt"] is None) is True
        assert _flag(detail["object"]["etag"] is None) is True
        assert detail["object"]["deletedAt"] is None
        assert detail["target"]["deviceSerialMasked"] == mask_device_serial(TEST_DEVICE)
        body = json.dumps(detail)
        assert _flag(TEST_DEVICE not in body) is True
        assert _flag(created["upload"]["url"] not in body) is True

        diagnostics = owned(
            clone, admin.get(task_path, params={"includeDiagnostics": "true"}), 200
        ).json()
        assert diagnostics["taskId"] == task_id
        assert _flag(diagnostics["attempts"] == []) is True
        assert _flag("promptSnapshot" not in diagnostics) is True
        assert _flag("artemisOutput" not in diagnostics) is True

        listing = owned(clone, admin.get(tasks_path, params={"limit": 100}), 200).json()
        rows = {row["taskId"]: row for row in listing["items"]}
        assert task_id in rows
        row = rows[task_id]
        assert row["clientRequestId"] == key
        assert row["captionPreview"] == marker
        assert row["status"] == "pending"
        assert row["stage"] == "awaiting_upload"
        assert row["createdBy"] == f"user:{account.user_pk}"
        assert "cancel" in row["allowedActions"]

        filtered = owned(
            clone, admin.get(tasks_path, params={"status": "cancelled"}), 200
        ).json()
        assert task_id not in {item["taskId"] for item in filtered["items"]}

        missing = owned(clone, admin.get(tasks_path + "/" + str(uuid4())), 404)
        assert _code(missing) == "TASK_NOT_FOUND"
    finally:
        _purge(clone, [intent])


def test_owned_upload_bytes_confirm_by_storage_hash_and_refuse_stale_versions(
    clone: OwnedClone,
    sessions: dict[str, httpx.Client],
    worker: str,
) -> None:
    """Opaque bytes reach the controlled store; only the HTTP hash is proven."""
    require_data_probe(clone.probe)
    marker = "TEST_video_upload_" + uuid4().hex
    key = str(uuid4())
    filename = marker + ".mp4"
    content = b"TEST_video_opaque_bytes_" + uuid4().hex.encode()
    admin = sessions["admin"]
    tasks_path = clone.prefix + "/v2/video-publish/tasks"
    intent = _intent(clone, "admin", key, filename)
    try:
        _arm_worker(clone, worker, fresh=True)
        created = _create(
            clone, admin, marker, key, filename=filename, size=len(content)
        ).json()
        intent.public_id = created["taskId"]
        task_path = tasks_path + "/" + created["taskId"]
        # A separate client: no ERP cookie, key or CSRF header reaches storage.
        with httpx.Client(trust_env=False, timeout=5) as storage:
            put = storage.put(
                created["upload"]["url"],
                content=content,
                headers={"Content-Type": "video/mp4", "Origin": clone.child.base},
            )
            assert put.status_code == 200

        refused = _post(
            clone,
            admin,
            task_path + "/confirm-upload",
            {"rowVersion": created["rowVersion"] + 3},
            409,
        )
        assert _code(refused) == "TASK_VERSION_CONFLICT"
        before = _states(clone, [key])[key]
        assert before["object_stored"] is False
        assert before["object_uploaded"] is False

        confirmed = _post(
            clone,
            admin,
            task_path + "/confirm-upload",
            {"rowVersion": created["rowVersion"]},
            200,
        ).json()
        assert confirmed["taskId"] == created["taskId"]
        assert confirmed["status"] == "pending"
        assert confirmed["stage"] == "queued"
        after = _states(clone, [key])[key]
        assert after["stage"] == "queued"
        assert after["queued"] is True
        assert after["object_stored"] is True
        assert after["object_uploaded"] is True
        assert after["row_version"] == created["rowVersion"] + 1

        with _session(clone) as session:
            clone.child.require_live()
            etag = session.scalar(
                select(VideoPublishTask.object_etag).where(
                    VideoPublishTask.public_id == UUID(created["taskId"])
                )
            )
        assert _flag(etag == hashlib.sha256(content).hexdigest()[:32]) is True

        cancelled = _post(
            clone,
            admin,
            task_path + "/cancel",
            {"rowVersion": after["row_version"]},
            200,
        ).json()
        assert cancelled["status"] == "cancelled"
        final = _states(clone, [key])[key]
        assert final["status"] == "cancelled"
        assert final["stage"] == "done"
        # The owned object was uploaded and observed; the cancel wrote no
        # object deletion, which is a row fact and not a store-retention claim.
        assert final["object_deleted"] is False
    finally:
        _purge(clone, [intent])
