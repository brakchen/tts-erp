"""Runtime configuration precedence, validation, and live reload tests."""

from __future__ import annotations

import logging
import time
from pathlib import Path

import pytest
from cryptography.fernet import Fernet, InvalidToken

from tts_erp_v2 import runtime_settings as runtime_config

pytestmark = [pytest.mark.domain_api, pytest.mark.layer_unit]


@pytest.fixture(autouse=True)
def _isolate_runtime_settings(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    dotenv = tmp_path / "local.env"
    runtime = tmp_path / "runtime.env"
    monkeypatch.setenv(runtime_config.DOTENV_FILE_VAR, str(dotenv))
    monkeypatch.setenv(runtime_config.RUNTIME_ENV_FILE_VAR, str(runtime))
    runtime_config.reset_for_testing()
    yield dotenv, runtime
    runtime_config.reset_for_testing()


def _write(path: Path, **values: str) -> None:
    path.write_text(
        "".join(f"{key}={value}\n" for key, value in values.items()),
        encoding="utf-8",
    )


def test_precedence_is_dotenv_then_runtime_file_then_process_env(
    _isolate_runtime_settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dotenv, runtime = _isolate_runtime_settings
    monkeypatch.setenv("TEST_PROCESS_ONLY", "process")
    monkeypatch.setenv("TEST_SHARED", "process")
    _write(
        runtime,
        TEST_RUNTIME_ONLY="runtime",
        TEST_SHARED="runtime",
    )
    _write(dotenv, TEST_DOTENV_ONLY="dotenv", TEST_SHARED="dotenv")

    assert runtime_config.get_setting("TEST_DOTENV_ONLY") == "dotenv"
    assert runtime_config.get_setting("TEST_RUNTIME_ONLY") == "runtime"
    assert runtime_config.get_setting("TEST_PROCESS_ONLY") == "process"
    assert runtime_config.get_setting("TEST_SHARED") == "dotenv"


def test_explicit_empty_dotenv_value_blocks_lower_priority_secret(
    _isolate_runtime_settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dotenv, runtime = _isolate_runtime_settings
    monkeypatch.setenv("TEST_SECRET", "process-secret")
    _write(runtime, TEST_SECRET="runtime-secret")
    _write(dotenv, TEST_SECRET="")

    assert runtime_config.get_setting("TEST_SECRET") == ""
    with pytest.raises(runtime_config.MissingRequiredSettings):
        runtime_config.require_setting("TEST_SECRET")


def test_runtime_file_change_is_visible_without_process_restart(
    _isolate_runtime_settings,
) -> None:
    _dotenv, runtime = _isolate_runtime_settings
    _write(runtime, TEST_ROTATING_SECRET="before")
    before_revision = runtime_config.config_revision()
    assert runtime_config.get_setting("TEST_ROTATING_SECRET") == "before"

    _write(runtime, TEST_ROTATING_SECRET="after-with-different-size")

    assert runtime_config.get_setting("TEST_ROTATING_SECRET") == (
        "after-with-different-size"
    )
    assert runtime_config.config_revision() > before_revision


def test_missing_required_settings_identifies_names_without_values(
    _isolate_runtime_settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _dotenv, runtime = _isolate_runtime_settings
    monkeypatch.delenv("TTS_ERP_DB_URL", raising=False)
    monkeypatch.delenv("TTS_ERP_FERNET_KEY", raising=False)
    _write(runtime, TTS_ERP_DB_URL="")

    with pytest.raises(runtime_config.MissingRequiredSettings) as raised:
        runtime_config.validate_required_settings("TEST service")

    message = str(raised.value)
    assert "TTS_ERP_DB_URL" in message
    assert "TTS_ERP_FERNET_KEY" in message
    assert "postgresql" not in message


def test_optional_setting_logs_missing_once_and_recovery(
    _isolate_runtime_settings,
    caplog: pytest.LogCaptureFixture,
) -> None:
    _dotenv, runtime = _isolate_runtime_settings
    caplog.set_level(logging.INFO, logger="tts_erp_v2.runtime_settings")
    _write(runtime, OPTIONAL_TEST_KEY="")

    assert (
        runtime_config.optional_setting(
            "OPTIONAL_TEST_KEY",
            feature="TEST downstream",
        )
        is None
    )
    runtime_config.optional_setting(
        "OPTIONAL_TEST_KEY",
        feature="TEST downstream",
    )
    missing_records = [
        record
        for record in caplog.records
        if "optional configuration unavailable" in record.getMessage()
    ]
    assert len(missing_records) == 1

    _write(runtime, OPTIONAL_TEST_KEY="available-now")
    assert (
        runtime_config.optional_setting(
            "OPTIONAL_TEST_KEY",
            feature="TEST downstream",
        )
        == "available-now"
    )
    assert any(
        "optional configuration recovered" in record.getMessage()
        for record in caplog.records
    )


def test_fernet_key_rotation_is_used_on_next_operation(
    _isolate_runtime_settings,
) -> None:
    from tts_erp_v2.proxy import token_service

    _dotenv, runtime = _isolate_runtime_settings
    first_key = Fernet.generate_key().decode("ascii")
    second_key = Fernet.generate_key().decode("ascii")
    _write(runtime, TTS_ERP_FERNET_KEY=first_key)
    token_service._reset_for_testing()
    first_ciphertext = token_service.encrypt("first")
    assert Fernet(first_key.encode()).decrypt(first_ciphertext) == b"first"

    _write(runtime, TTS_ERP_FERNET_KEY=second_key)
    # Some filesystems can reuse identical inode/size/mtime metadata for two
    # same-length writes. The provider guarantees a bounded 250 ms recheck.
    time.sleep(0.3)
    second_ciphertext = token_service.encrypt("second")
    assert Fernet(second_key.encode()).decrypt(second_ciphertext) == b"second"
    with pytest.raises(InvalidToken):
        Fernet(first_key.encode()).decrypt(second_ciphertext)


def test_build_app_refuses_to_start_without_strong_dependencies(
    _isolate_runtime_settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tts_erp_v2.app import build_app

    _dotenv, runtime = _isolate_runtime_settings
    monkeypatch.delenv("TTS_ERP_DB_URL", raising=False)
    monkeypatch.delenv("TTS_ERP_FERNET_KEY", raising=False)
    _write(runtime, TTS_ERP_DB_URL="", TTS_ERP_FERNET_KEY="")

    with pytest.raises(runtime_config.MissingRequiredSettings):
        build_app()


def test_server_bind_settings_come_from_reloadable_file(
    _isolate_runtime_settings,
) -> None:
    from tts_erp_v2.server import bind_settings

    _dotenv, runtime = _isolate_runtime_settings
    _write(runtime, TTS_ERP_HOST="0.0.0.0", TTS_ERP_PORT="19877")

    assert bind_settings() == ("0.0.0.0", 19877)

    _write(runtime, TTS_ERP_HOST="127.0.0.1", TTS_ERP_PORT="19878")
    assert bind_settings() == ("127.0.0.1", 19878)


@pytest.mark.parametrize("raw_port", ["not-a-port", "0", "65536"])
def test_server_rejects_invalid_port(
    _isolate_runtime_settings,
    raw_port: str,
) -> None:
    from tts_erp_v2.server import bind_settings

    _dotenv, runtime = _isolate_runtime_settings
    _write(runtime, TTS_ERP_PORT=raw_port)

    with pytest.raises(RuntimeError, match="TTS_ERP_PORT"):
        bind_settings()
