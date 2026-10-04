"""Entry point for the independent, one-task publishing worker."""

from __future__ import annotations

import asyncio
import os
import socket
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from sqlalchemy import text

from tts_erp_v2.db.base import get_session_factory
from tts_erp_v2.publishing.adb_device import AdbDevice
from tts_erp_v2.publishing.artemis_client import ArtemisClient
from tts_erp_v2.publishing.dispatcher import (
    PublishDependencies,
    dispatch_one,
    recover_active,
)
from tts_erp_v2.publishing.object_store import MinioVideoStore
from tts_erp_v2.storage.minio_client import MinioClient


async def run() -> None:
    instance_id = str(uuid4())
    session_factory = get_session_factory()
    _write_heartbeat(session_factory, instance_id, "starting")
    store = MinioVideoStore(MinioClient.from_env())
    deps = PublishDependencies(
        session_factory=session_factory,
        store=store,
        adb=AdbDevice(os.environ.get("ADB_BINARY", "adb")),
        artemis=ArtemisClient(
            os.environ["ARTEMIS_BASE_URL"], token=os.environ.get("ARTEMIS_TOKEN")
        ),
        spool_dir=Path(os.environ["TTS_ERP_PUBLISH_SPOOL_DIR"]).expanduser(),
        instance_id=instance_id,
        max_attempts=int(os.environ.get("TIKTOK_PUBLISH_MAX_ATTEMPTS", "3")),
        lease_seconds=int(os.environ.get("PUBLISH_TASK_LEASE_SECONDS", "30")),
    )
    deps.spool_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    heartbeat_task = asyncio.create_task(
        _heartbeat_loop(session_factory, instance_id), name="publish-heartbeat"
    )
    try:
        await recover_active(deps)
        while True:
            outcome = await dispatch_one(deps)
            if outcome == "no_task":
                await asyncio.sleep(2)
    finally:
        heartbeat_task.cancel()
        await asyncio.gather(heartbeat_task, return_exceptions=True)
        _write_heartbeat(session_factory, instance_id, "stopping")


async def _heartbeat_loop(session_factory, instance_id: str) -> None:
    while True:
        _write_heartbeat(session_factory, instance_id, "ready")
        await asyncio.sleep(5)


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
