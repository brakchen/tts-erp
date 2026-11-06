"""Entry point for the independent, one-task publishing worker.

One process serves exactly one device (02 §6.1): ``ARTEMIS_DEVICE_SERIAL`` is
read once here and every claim, cleanup and readiness query below is scoped to
that serial. A second device needs a second worker process.
"""

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
    run_background_cleanup_batch,
)
from tts_erp_v2.publishing.object_store import video_store_from_env
from tts_erp_v2.publishing.repository import schedule_spool_reconciliation
from tts_erp_v2.runtime_settings import (
    MissingRequiredSettings,
    config_revision,
    get_setting,
    log_optional_settings_status,
    require_setting,
    validate_required_settings,
)
from tts_erp_v2.storage.minio_client import MinioConfigError

logger = logging.getLogger(__name__)
_CONFIG_RETRY_SECONDS = 5.0


def _build_dependencies(session_factory, instance_id: str) -> PublishDependencies:
    store = video_store_from_env()
    device_serial = get_setting("ARTEMIS_DEVICE_SERIAL", "").strip()
    if not device_serial:
        logger.error(
            "ARTEMIS_DEVICE_SERIAL is not configured; this worker would claim "
            "nothing. The worker remains alive and will retry configuration."
        )
    return PublishDependencies(
        session_factory=session_factory,
        store=store,
        adb=AdbDevice(
            get_setting("ADB_BINARY", "adb"),
            album=get_setting("TIKTOK_PUBLISH_ALBUM", "TTSERP"),
        ),
        artemis=ArtemisClient(
            require_setting("ARTEMIS_BASE_URL"), token=get_setting("ARTEMIS_TOKEN")
        ),
        spool_dir=Path(require_setting("TTS_ERP_PUBLISH_SPOOL_DIR")).expanduser(),
        instance_id=instance_id,
        device_serial=device_serial,
        lease_seconds=int(get_setting("PUBLISH_TASK_LEASE_SECONDS", "30")),
        poll_seconds=float(get_setting("PUBLISH_POLL_INTERVAL_SECONDS", "2")),
        artemis_profile=get_setting("ARTEMIS_PROFILE", "pro"),
        artemis_verification_level=get_setting("ARTEMIS_VERIFICATION_LEVEL", "strict"),
    )


async def _serve_config_generation(
    deps: PublishDependencies,
    *,
    revision: int,
) -> None:
    deps.spool_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    await asyncio.to_thread(
        _cleanup_orphan_spool,
        deps.session_factory,
        deps.spool_dir,
    )
    heartbeat_task = asyncio.create_task(
        _heartbeat_loop(
            deps.session_factory,
            deps.instance_id,
            float(get_setting("PUBLISH_WORKER_HEARTBEAT_SECONDS", "5")),
            deps,
        ),
        name="publish-heartbeat",
    )
    background_cleanup_task = asyncio.create_task(
        _background_cleanup_loop(
            deps,
            batch_size=int(get_setting("PUBLISH_BACKGROUND_CLEANUP_BATCH_SIZE", "1")),
            interval_seconds=float(
                get_setting("PUBLISH_BACKGROUND_CLEANUP_INTERVAL_SECONDS", "1")
            ),
        ),
        name="publish-background-cleanup",
    )
    try:
        await recover_active(deps)
        while config_revision() == revision:
            await dispatch_one(deps)
            await asyncio.sleep(deps.poll_seconds)
    finally:
        heartbeat_task.cancel()
        background_cleanup_task.cancel()
        await asyncio.gather(
            heartbeat_task, background_cleanup_task, return_exceptions=True
        )


async def run() -> None:
    validate_required_settings("tts-erp publish-worker")
    instance_id = str(uuid4())
    session_factory = get_session_factory()
    await asyncio.to_thread(_write_heartbeat, session_factory, instance_id, "starting")
    try:
        while True:
            log_optional_settings_status()
            try:
                deps = _build_dependencies(session_factory, instance_id)
            except (MissingRequiredSettings, MinioConfigError, ValueError) as exc:
                logger.error(
                    "publish worker downstream configuration unavailable (%s); "
                    "retrying in %.1fs without exiting",
                    type(exc).__name__,
                    _CONFIG_RETRY_SECONDS,
                )
                await asyncio.sleep(_CONFIG_RETRY_SECONDS)
                continue
            revision = config_revision()
            await _serve_config_generation(deps, revision=revision)
            logger.info("publish worker configuration changed; rebuilding clients")
    finally:
        await asyncio.to_thread(
            _write_heartbeat,
            session_factory,
            instance_id,
            "stopping",
        )


async def _background_cleanup_loop(
    deps: PublishDependencies,
    *,
    batch_size: int,
    interval_seconds: float,
) -> None:
    """Continuously drain bounded spool/object work independently of publishing."""
    while True:
        try:
            await run_background_cleanup_batch(deps, limit=max(1, batch_size))
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - keep cleanup service alive after transient faults
            logger.warning("background publish cleanup iteration failed")
        await asyncio.sleep(max(0.05, interval_seconds))


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
    serial = deps.device_serial
    if not serial:
        return "unknown", "未配置设备序列号"

    def cleanup_is_busy() -> bool:
        # Only this worker's own device: a dirty device elsewhere must not make
        # this device look busy (02 §6.1).
        with deps.session_factory() as session:
            return bool(
                session.scalar(
                    select(VideoPublishTask.id)
                    .where(
                        VideoPublishTask.target_device_serial == serial,
                        VideoPublishTask.cleanup_intent != "none",
                        VideoPublishTask.device_cleanup_status.in_(
                            ["pending", "failed"]
                        ),
                    )
                    .limit(1)
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
            serial, get_setting("ARTEMIS_APP_PACKAGE", "com.zhiliaoapp.musically")
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
                    .where(
                        VideoPublishTask.status == "running",
                        VideoPublishTask.target_device_serial == serial,
                    )
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
