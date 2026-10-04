from __future__ import annotations

import pytest

from tts_erp_v2.publishing.adb_device import AdbDevice
from tts_erp_v2.publishing.artemis_client import (
    ArtemisClient,
    ArtemisResult,
    ArtemisSessionNotFound,
)
from tts_erp_v2.publishing.domain import (
    DomainTransitionError,
    TaskStage,
    TaskStatus,
    transition_task,
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


def test_state_machine_rejects_direct_retry_from_ambiguous_task() -> None:
    task = type(
        "Task",
        (),
        {"status": TaskStatus.RUNNING.value, "stage": TaskStage.WAITING_ARTEMIS.value},
    )()
    with pytest.raises(DomainTransitionError):
        transition_task(task, "USER_RETRY")


def test_verify_prompt_forbids_publish_actions() -> None:
    prompt = build_verify_prompt(
        caption="hello", app_package="com.zhiliaoapp.musically"
    )
    assert "never click Publish" in prompt
    assert "upload flow" in prompt
