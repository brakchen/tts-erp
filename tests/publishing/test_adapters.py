import pytest

from tts_erp_v2.publishing.adb_device import AdbDevice
from tts_erp_v2.publishing.artemis_client import ArtemisResult
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
