"""Entry point for the independent, one-task publishing worker."""

from __future__ import annotations

import asyncio
import logging
import os
import socket
from pathlib import Path
from uuid import UUID, uuid4

from sqlalchemy import select, text

from tts_erp_v2.db.base import get_session_factory
from tts_erp_v2.db.models.publishing import VideoPublishTask
from tts_erp_v2.publishing.adb_device import AdbDevice, DeviceLocked, DeviceUnavailable
from tts_erp_v2.publishing.artemis_client import ArtemisClient
from tts_erp_v2.publishing.dispatcher import (
    PublishDependencies,
    dispatch_one,
    recover_active,
)
from tts_erp_v2.publishing.object_store import MinioVideoStore
from tts_erp_v2.publishing.repository import schedule_spool_reconciliation
from tts_erp_v2.storage.minio_client import MinioClient

logger = logging.getLogger(__name__)


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
            deps,
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
    session_factory,
    instance_id: str,
    interval_seconds: float,
    deps: PublishDependencies,
) -> None:
    while True:
        device_status, device_message = await _probe_device_readiness(deps)
        await asyncio.to_thread(
            _write_heartbeat,
            session_factory,
            instance_id,
            "ready",
            device_status,
            device_message,
        )
        await asyncio.sleep(interval_seconds)


async def _probe_device_readiness(
    deps: PublishDependencies,
) -> tuple[str, str]:
    serial = os.environ.get("ARTEMIS_DEVICE_SERIAL", "").strip()
    if not serial:
        return "unknown", "未配置设备序列号"

    def cleanup_is_busy() -> bool:
        with deps.session_factory() as session:
            return bool(
                session.scalar(
                    text("""
                    SELECT EXISTS (
                        SELECT 1 FROM publishing.video_publish_tasks
                        WHERE cleanup_intent <> 'none'
                          AND device_cleanup_status IN ('pending','failed')
                          AND (
                            device_cleanup_next_attempt_at IS NULL
                            OR device_cleanup_next_attempt_at <= clock_timestamp()
                            OR (cleanup_lease_owner IS NOT NULL
                                AND cleanup_lease_expires_at > clock_timestamp())
                          )
                    )
                    """)
                )
            )

    try:
        cleaning = await asyncio.to_thread(cleanup_is_busy)
    except Exception:  # noqa: BLE001 - never persist database diagnostics
        return "unknown", "发布数据库状态不可用"
    if cleaning:
        return "busy", "当前正在执行或等待设备清理"
    try:
        await deps.adb.check_device(serial)
    except DeviceLocked:
        return "locked", "设备在线但仍处于锁屏状态"
    except DeviceUnavailable:
        return "offline", "设备离线或 ADB 探测失败"
    except Exception:  # noqa: BLE001 - readiness text never persists raw diagnostics
        return "unknown", "设备探测返回未知错误"
    try:
        await deps.adb.check_package(
            serial, os.environ.get("ARTEMIS_APP_PACKAGE", "com.zhiliaoapp.musically")
        )
    except Exception:  # noqa: BLE001 - never persist package probe diagnostics
        return "unknown", "TikTok 应用不可用或包探测失败"
    try:
        await deps.artemis.check_available()
    except Exception:  # noqa: BLE001 - never persist endpoint/token diagnostics
        return "unknown", "Artemis 服务不可用"
    try:
        await asyncio.to_thread(deps.store.check_available)
    except Exception:  # noqa: BLE001 - never persist object-store diagnostics
        return "unknown", "视频对象存储不可用"

    def publish_is_busy() -> bool:
        with deps.session_factory() as session:
            return (
                session.scalar(
                    select(VideoPublishTask.id)
                    .where(VideoPublishTask.status == "running")
                    .limit(1)
                )
                is not None
            )

    try:
        running = await asyncio.to_thread(publish_is_busy)
    except Exception:  # noqa: BLE001 - never persist database diagnostics
        return "unknown", "发布数据库状态不可用"
    if running:
        return "busy", "设备在线且已解锁，当前正在执行发布或核验"
    return "ready", "设备、TikTok、Artemis 与对象存储均可用且当前空闲"


def _cleanup_orphan_spool(session_factory, spool_dir: Path) -> int:
    """Reconcile tracked terminal residue; never delete from the startup scan."""
    root = spool_dir.resolve()
    scheduled = 0
    for entry in root.iterdir():
        if not entry.is_dir() or entry.resolve().parent != root:
            continue
        try:
            task_id = UUID(entry.name)
        except ValueError:
            logger.warning("preserving unrecognized publish spool directory")
            continue
        with session_factory() as session:
            known = session.scalar(
                select(VideoPublishTask.id).where(VideoPublishTask.public_id == task_id)
            )
        if known is None:
            logger.warning("preserving untracked publish spool directory %s", task_id)
            continue
        if schedule_spool_reconciliation(session_factory, task_id):
            scheduled += 1
    if scheduled:
        logger.info("scheduled %d tracked spool residue cleanup task(s)", scheduled)
    return scheduled


def _write_heartbeat(
    session_factory,
    instance_id: str,
    state: str,
    device_status: str = "unknown",
    device_message: str | None = None,
) -> None:
    with session_factory() as session:
        session.execute(
            text(
                """
            INSERT INTO publishing.worker_heartbeats
              (instance_id, hostname, pid, status, device_status, device_message,
               started_at, heartbeat_at)
            VALUES (:id, :host, :pid, :status, :device_status, :device_message,
                    clock_timestamp(), clock_timestamp())
            ON CONFLICT (instance_id) DO UPDATE SET
              status = EXCLUDED.status,
              device_status = EXCLUDED.device_status,
              device_message = EXCLUDED.device_message,
              heartbeat_at = EXCLUDED.heartbeat_at,
              updated_at = now()
        """
            ),
            {
                "id": instance_id,
                "host": socket.gethostname(),
                "pid": os.getpid(),
                "status": state,
                "device_status": device_status,
                "device_message": device_message,
            },
        )
        session.commit()


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
