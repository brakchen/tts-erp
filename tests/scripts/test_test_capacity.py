"""Host admission is independent of clone identities and releases on exit."""

from __future__ import annotations

import os
import select
import signal
import subprocess
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
HELPER = ROOT / "scripts/_test_capacity.sh"


def _start(tmp_path: Path, *args: str, timeout: str = "10") -> subprocess.Popen[str]:
    source = tmp_path / "tests/unit/test_TEST_capacity.py"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.touch()
    return subprocess.Popen(
        ["bash", "-c", 'set -eu; source "$1"; shift; test_capacity_acquire "$@"; echo TEST_READY; read -r line',
         "TEST_capacity", str(HELPER), *args],
        env={**os.environ, "TTS_ERP_TEST_CAPACITY_LOCK": str(tmp_path / "capacity.lock"),
             "TTS_ERP_TEST_CAPACITY_TIMEOUT_S": timeout},
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, start_new_session=True, cwd=tmp_path,
    )


def _ready(process: subprocess.Popen[str], timeout: float = 5) -> str:
    # Read bytes, not TextIO buffering, so select sees each complete message.
    assert process.stdout is not None
    output = b""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if select.select([process.stdout], [], [], max(0, deadline - time.monotonic()))[0]:
            part = os.read(process.stdout.fileno(), 4096)
            if not part:
                break
            output += part
            if b"TEST_READY" in output:
                return output.decode()
    raise AssertionError(f"admission did not finish: {output.decode()}")


def _stop(process: subprocess.Popen[str]) -> None:
    if process.poll() is None:
        os.killpg(process.pid, signal.SIGTERM)
    process.wait(timeout=5)
    for stream in (process.stdout, process.stdin):
        if stream:
            stream.close()


@pytest.mark.parametrize("args", [(), ("fast",), ("fast", "--tb=short"), ("selected",),
                                  ("fast", "tests/frontend/test_components.py"),
                                  ("fast", "tests/fx/../frontend/test_components.py"),
                                  ("fast", "tests/unit/*.py")])
def test_full_and_unknown_commands_take_exclusive_capacity(tmp_path: Path, args: tuple[str, ...]) -> None:
    owner = _start(tmp_path, *args)
    follower = None
    try:
        assert "class=heavy" in _ready(owner)
        follower = _start(tmp_path, "fast", timeout="1")
        output, _ = follower.communicate(timeout=4)
        assert follower.returncode != 0
        assert "TEST_READY" not in output and "capacity timeout" in output
        _stop(owner)
        owner = _start(tmp_path, "fast")
        assert "TEST_READY" in _ready(owner)
    finally:
        _stop(owner)
        if follower:
            _stop(follower)


def test_two_light_selections_are_bounded_and_block_heavy_run(tmp_path: Path) -> None:
    args = ("fast", "tests/unit/test_TEST_capacity.py")
    first, second = _start(tmp_path, *args), _start(tmp_path, *args)
    third = None
    try:
        assert "class=light" in _ready(first)
        assert "class=light" in _ready(second)
        third = _start(tmp_path, *args, timeout="1")
        output, _ = third.communicate(timeout=4)
        assert third.returncode != 0 and "TEST_READY" not in output
        _stop(third)
        third = _start(tmp_path, "fast", timeout="1")
        output, _ = third.communicate(timeout=4)
        assert third.returncode != 0 and "TEST_READY" not in output
    finally:
        _stop(first)
        _stop(second)
        if third:
            _stop(third)


def test_invalid_timeout_fails_before_admission(tmp_path: Path) -> None:
    process = _start(tmp_path, "fast", timeout="bad")
    try:
        output, _ = process.communicate(timeout=3)
        assert process.returncode != 0 and "invalid timeout" in output
        assert not (tmp_path / "capacity.lock").exists()
    finally:
        _stop(process)
