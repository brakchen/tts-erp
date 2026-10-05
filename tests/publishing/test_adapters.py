from __future__ import annotations

import httpx
import pytest

from tts_erp_v2.publishing import artemis_client as artemis_module
from tts_erp_v2.publishing.adb_device import AdbDevice, DeviceLocked
from tts_erp_v2.publishing.artemis_client import (
    ArtemisClient,
    ArtemisResult,
    ArtemisSessionNotFound,
)
from tts_erp_v2.publishing.prompt import build_verify_prompt


@pytest.mark.asyncio
async def test_adb_adapter_rejects_unmanaged_delete_path() -> None:
    with pytest.raises(ValueError):
        await AdbDevice().remove_staged_video("device", "/sdcard/DCIM/other.mp4")


def test_adb_adapter_uses_configured_album_path() -> None:
    device = AdbDevice(album="Campaign")
    assert device.album_directory == "/sdcard/Movies/Campaign"
    assert device.device_path("task") == "/sdcard/Movies/Campaign/tts_erp_task.mp4"


@pytest.mark.asyncio
async def test_adb_preflight_rejects_locked_device_without_admission(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    device = AdbDevice()

    async def fake_run(*args: str, timeout: float = 30) -> str:
        del timeout
        return (
            "device\n"
            if args[-1] == "get-state"
            else "mDreamingLockscreen=true\nmShowingLockscreen=true\n"
        )

    monkeypatch.setattr(device, "_run", fake_run)
    with pytest.raises(DeviceLocked, match="锁屏"):
        await device.check_device("TEST_device")


@pytest.mark.asyncio
async def test_artemis_404_falls_back_to_global_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = ArtemisClient("https://artemis.test")
    session_id = __import__("uuid").uuid4()
    calls: list[str] = []

    async def fake_request(method: str, path: str, **_kwargs):
        calls.append(path)
        if path.startswith("/api/sessions/"):
            raise ArtemisSessionNotFound("missing")
        return {"active_task": {"session_id": str(session_id), "status": "running"}}

    monkeypatch.setattr(client, "_request", fake_request)
    result = await client.get_task(session_id)
    assert result.status == "running"
    assert calls == [f"/api/sessions/{session_id}", "/api/status"]


@pytest.mark.asyncio
async def test_artemis_http_409_maps_to_typed_admission_rejection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeAsyncClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def request(self, method: str, path: str, **_kwargs):
            request = httpx.Request(method, f"https://artemis.test{path}")
            return httpx.Response(
                409,
                request=request,
                json={"code": "DEVICE_LOCKED"},
            )

    monkeypatch.setattr(
        artemis_module.httpx,
        "AsyncClient",
        lambda **_kwargs: FakeAsyncClient(),
    )
    with pytest.raises(artemis_module.ArtemisAdmissionRejected, match="DEVICE_LOCKED"):
        await ArtemisClient("https://artemis.test").submit(
            goal="TEST",
            session_id=__import__("uuid").uuid4(),
            device_serial="TEST_device",
            app_package="com.tiktok",
        )


def test_artemis_result_preserves_final_publish_observed() -> None:
    session_id = __import__("uuid").uuid4()
    result = ArtemisClient._result(
        session_id,
        {"status": "failed", "finalPublishObserved": True, "stepsCount": 4},
    )
    assert result.final_publish_observed is True
    assert result.steps_count == 4


def test_artemis_terminal_statuses_are_bounded() -> None:
    assert ArtemisResult(
        session_id=__import__("uuid").uuid4(), status="success"
    ).terminal
    assert not ArtemisResult(
        session_id=__import__("uuid").uuid4(), status="running"
    ).terminal


def test_verify_prompt_forbids_publish_actions() -> None:
    prompt = build_verify_prompt(
        caption="hello", app_package="com.zhiliaoapp.musically"
    )
    assert "never click Publish" in prompt
    assert "upload flow" in prompt
