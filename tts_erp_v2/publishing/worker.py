"""Entry point for the independent, one-task publishing worker."""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
import socket
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

from sqlalchemy import func, select, text

from tts_erp_v2.api.deps import require_destructive_script_guard
from tts_erp_v2.db.base import get_session_factory
from tts_erp_v2.db.models.publishing import VideoPublishTask
from tts_erp_v2.publishing.adb_device import AdbDevice
from tts_erp_v2.publishing.artemis_client import ArtemisClient
from tts_erp_v2.publishing.dispatcher import (
    PublishDependencies,
    dispatch_one,
    recover_active,
)
from tts_erp_v2.publishing.object_store import MinioVideoStore
from tts_erp_v2.storage.minio_client import MinioClient

logger = logging.getLogger(__name__)
_TERMINAL_STATUSES = {"succeeded", "failed", "needs_review", "cancelled"}


async def run() -> None:
    instance_id = str(uuid4())
    session_factory = get_session_factory()
    await asyncio.to_thread(_write_heartbeat, session_factory, instance_id, "starting")
    store = MinioVideoStore(MinioClient.from_env())
    deps = PublishDependencies(
        session_factory=session_factory,
        store=store,
        adb=AdbDevice(
            os.environ.get("ADB_BINARY", "adb"),
            album=os.environ.get("TIKTOK_PUBLISH_ALBUM", "TTSERP"),
        ),
        artemis=ArtemisClient(
            os.environ["ARTEMIS_BASE_URL"], token=os.environ.get("ARTEMIS_TOKEN")
        ),
        spool_dir=Path(os.environ["TTS_ERP_PUBLISH_SPOOL_DIR"]).expanduser(),
        instance_id=instance_id,
        max_attempts=int(os.environ.get("TIKTOK_PUBLISH_MAX_ATTEMPTS", "3")),
        lease_seconds=int(os.environ.get("PUBLISH_TASK_LEASE_SECONDS", "30")),
        poll_seconds=float(os.environ.get("PUBLISH_POLL_INTERVAL_SECONDS", "2")),
        artemis_profile=os.environ.get("ARTEMIS_PROFILE", "pro"),
        artemis_verification_level=os.environ.get(
            "ARTEMIS_VERIFICATION_LEVEL", "strict"
        ),
    )
    deps.spool_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    await asyncio.to_thread(_cleanup_orphan_spool, session_factory, deps.spool_dir)
    heartbeat_task = asyncio.create_task(
        _heartbeat_loop(
            session_factory,
            instance_id,
            float(os.environ.get("PUBLISH_WORKER_HEARTBEAT_SECONDS", "5")),
        ),
        name="publish-heartbeat",
    )
    try:
        await recover_active(deps)
        while True:
            await dispatch_one(deps)
            await asyncio.sleep(deps.poll_seconds)
    finally:
        heartbeat_task.cancel()
        await asyncio.gather(heartbeat_task, return_exceptions=True)
        await asyncio.to_thread(
            _write_heartbeat, session_factory, instance_id, "stopping"
        )


async def _heartbeat_loop(
    session_factory, instance_id: str, interval_seconds: float
) -> None:
    while True:
        await asyncio.to_thread(_write_heartbeat, session_factory, instance_id, "ready")
        await asyncio.sleep(interval_seconds)


def _cleanup_orphan_spool(session_factory, spool_dir: Path) -> int:
    """Delete only UUID task directories proven terminal and older than 24h."""
    root = spool_dir.resolve()
    with session_factory() as session:
        database_now = session.scalar(select(func.now()))
        if database_now is None:
            return 0
        cutoff = database_now - timedelta(hours=24)
        removable: list[Path] = []
        for entry in root.iterdir():
            if not entry.is_dir() or entry.resolve().parent != root:
                continue
            try:
                task_id = UUID(entry.name)
            except ValueError:
                logger.warning("preserving unrecognized publish spool directory")
                continue
            row = session.execute(
                select(
                    VideoPublishTask.status,
                    VideoPublishTask.completed_at,
                    VideoPublishTask.created_at,
                ).where(VideoPublishTask.public_id == task_id)
            ).one_or_none()
            task_age = row.completed_at or row.created_at if row else None
            if (
                row
                and task_age is not None
                and row.status in _TERMINAL_STATUSES
                and task_age <= cutoff
            ):
                removable.append(entry)
    if not removable:
        return 0
    try:
        require_destructive_script_guard(
            script_name="video_publish.cleanup_orphan_spool",
            confirmation=True,
            dangerous=True,
        )
    except SystemExit:
        logger.error("orphan spool cleanup blocked by destructive guard")
        return 0
    for entry in removable:
        shutil.rmtree(entry)
    logger.info("removed %d proven terminal orphan spool directories", len(removable))
    return len(removable)


def _write_heartbeat(session_factory, instance_id: str, state: str) -> None:
    now = datetime.now(UTC)
    with session_factory() as session:
        session.execute(
            text(
                """
            INSERT INTO publishing.worker_heartbeats
              (instance_id, hostname, pid, status, started_at, heartbeat_at)
            VALUES (:id, :host, :pid, :status, :now, :now)
            ON CONFLICT (instance_id) DO UPDATE SET
              status = EXCLUDED.status, heartbeat_at = EXCLUDED.heartbeat_at,
              updated_at = now()
        """
            ),
            {
                "id": instance_id,
                "host": socket.gethostname(),
                "pid": os.getpid(),
                "status": state,
                "now": now,
            },
        )
        session.commit()


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
