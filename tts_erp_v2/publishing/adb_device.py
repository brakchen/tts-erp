"""Constrained ADB adapter; no arbitrary shell command is exposed."""

from __future__ import annotations

import asyncio
import re
from pathlib import Path


class DeviceUnavailable(RuntimeError):
    pass


class DeviceLocked(DeviceUnavailable):
    pass


class ManagedAlbumNotEmpty(DeviceUnavailable):
    pass


class AdbDevice:
    _MEDIA_URI = "content://media/external/file"
    _MEDIA_POLL_ATTEMPTS = 10
    _MEDIA_POLL_SECONDS = 0.2

    def __init__(self, adb_binary: str = "adb", *, album: str = "TTSERP") -> None:
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", album):
            raise ValueError("INVALID_PUBLISH_ALBUM")
        self.adb_binary = adb_binary
        self.album = album

    @property
    def album_directory(self) -> str:
        return f"/sdcard/Movies/{self.album}"

    def device_path(self, task_id: object) -> str:
        return f"{self.album_directory}/tts_erp_{task_id}.mp4"

    def _require_managed_path(self, device_path: str) -> None:
        prefix = f"{self.album_directory}/tts_erp_"
        suffix = ".mp4"
        identifier = (
            device_path[len(prefix) : -len(suffix)]
            if device_path.startswith(prefix) and device_path.endswith(suffix)
            else ""
        )
        if (
            not identifier
            or len(identifier) > 64
            or any(
                character not in "0123456789abcdefABCDEF-" for character in identifier
            )
        ):
            raise ValueError("refusing to operate on an unmanaged device path")

    def _media_path_args(self, device_path: str) -> tuple[str, ...]:
        self._require_managed_path(device_path)
        return ("--where", "_data=?", "--bind", f"_data:s:{device_path}")

    @staticmethod
    def _media_query_has_rows(output: str) -> bool:
        return any(line.strip().startswith("Row:") for line in output.splitlines())

    async def _query_filesystem_path(self, serial: str, device_path: str) -> str:
        self._require_managed_path(device_path)
        return await self._run(
            "-s",
            serial,
            "shell",
            "find",
            device_path,
            "-maxdepth",
            "0",
            "-type",
            "f",
            "-print",
        )

    async def _query_media_path(self, serial: str, device_path: str) -> str:
        return await self._run(
            "-s",
            serial,
            "shell",
            "content",
            "query",
            "--uri",
            self._MEDIA_URI,
            "--projection",
            "_data",
            *self._media_path_args(device_path),
        )

    async def _run(self, *args: str, timeout: float = 30) -> str:
        process = await asyncio.create_subprocess_exec(
            self.adb_binary,
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=timeout)
        if process.returncode:
            raise DeviceUnavailable(stderr.decode(errors="replace").strip()[:500])
        return stdout.decode(errors="replace")

    async def check_device(self, serial: str) -> None:
        output = await self._run("-s", serial, "get-state")
        if output.strip() != "device":
            raise DeviceUnavailable("目标设备未在线")
        lock_state = await self._run("-s", serial, "shell", "dumpsys", "window")
        if re.search(
            r"(?:mDreamingLockscreen|mShowingLockscreen|isStatusBarKeyguard)=true",
            lock_state,
        ):
            raise DeviceLocked("目标设备仍处于锁屏状态")

    async def check_package(self, serial: str, package: str) -> None:
        output = await self._run("-s", serial, "shell", "pm", "path", package)
        if "package:" not in output:
            raise DeviceUnavailable("TikTok 未安装")

    async def ensure_album_empty(self, serial: str) -> None:
        """Refuse staging while any managed video remains in the album."""
        await self._run("-s", serial, "shell", "mkdir", "-p", self.album_directory)
        output = await self._run(
            "-s",
            serial,
            "shell",
            "find",
            self.album_directory,
            "-maxdepth",
            "1",
            "-type",
            "f",
            "-name",
            "tts_erp_*.mp4",
            "-print",
        )
        paths = [line.strip() for line in output.splitlines() if line.strip()]
        media_output = await self._run(
            "-s",
            serial,
            "shell",
            "content",
            "query",
            "--uri",
            self._MEDIA_URI,
            "--projection",
            "_data",
            "--where",
            "_data LIKE ?",
            "--bind",
            f"_data:s:{self.album_directory}/tts_erp_%.mp4",
        )
        if paths or self._media_query_has_rows(media_output):
            raise ManagedAlbumNotEmpty("managed video residue requires cleanup")

    async def stage_video(
        self, serial: str, local_path: Path, device_path: str
    ) -> None:
        await self._run("-s", serial, "shell", "mkdir", "-p", self.album_directory)
        await self._run("-s", serial, "push", str(local_path), device_path, timeout=180)
        size = await self._run("-s", serial, "shell", "stat", "-c", "%s", device_path)
        if int(size.strip() or "-1") != local_path.stat().st_size:
            raise DeviceUnavailable("设备视频大小校验失败")
        # am broadcast is a fixed command with a server-generated path.
        await self._run(
            "-s",
            serial,
            "shell",
            "am",
            "broadcast",
            "-a",
            "android.intent.action.MEDIA_SCANNER_SCAN_FILE",
            "-d",
            f"file://{device_path}",
        )

    async def verify_media_visible(self, serial: str, device_path: str) -> None:
        output = await self._run(
            "-s",
            serial,
            "shell",
            "content",
            "query",
            "--uri",
            "content://media/external/file",
            "--projection",
            "_data",
        )
        if device_path not in output:
            raise DeviceUnavailable("视频尚未出现在 TTSERP 相册")

    async def remove_staged_video(self, serial: str, device_path: str) -> None:
        """Remove and verify one exact ttsERP-managed filesystem/catalog entry."""
        self._require_managed_path(device_path)
        await self._run("-s", serial, "shell", "rm", "-f", device_path)
        await self._run(
            "-s",
            serial,
            "shell",
            "content",
            "delete",
            "--uri",
            self._MEDIA_URI,
            *self._media_path_args(device_path),
        )
        await self._run(
            "-s",
            serial,
            "shell",
            "am",
            "broadcast",
            "-a",
            "android.intent.action.MEDIA_SCANNER_SCAN_FILE",
            "-d",
            f"file://{device_path}",
        )
        for attempt in range(self._MEDIA_POLL_ATTEMPTS):
            filesystem_output = await self._query_filesystem_path(serial, device_path)
            media_output = await self._query_media_path(serial, device_path)
            if not filesystem_output.strip() and not self._media_query_has_rows(
                media_output
            ):
                return
            if attempt + 1 < self._MEDIA_POLL_ATTEMPTS:
                await asyncio.sleep(self._MEDIA_POLL_SECONDS)
        raise DeviceUnavailable("设备相册仍保留目标视频记录")
