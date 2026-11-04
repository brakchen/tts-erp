"""Real process groups prove cleanup without touching unrelated processes."""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from collections.abc import Callable
from contextlib import nullcontext, suppress
from pathlib import Path

import pytest

from tests.support import owned_process as owner

pytestmark = [pytest.mark.domain_frontend, pytest.mark.layer_unit]


class _InjectedFailure(RuntimeError):
    pass


def _running(pid: int) -> bool:
    try:
        state = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[0]
    except FileNotFoundError:
        return False
    return state != "Z"


def _wait_for(predicate: Callable[[], bool]) -> None:
    deadline = time.monotonic() + 5
    while not predicate():
        assert time.monotonic() < deadline, "TEST process condition did not settle"
        time.sleep(0.01)


def test_success_preserves_output_environment_and_caller_file(tmp_path):
    target = tmp_path / "TEST_output.log"
    with target.open("w") as output:
        with owner.owned_process(
            [sys.executable, "-c", "import os; print(os.environ['TEST_OWNED_VALUE']); print(os.getcwd())"],
            cwd=tmp_path, env={**os.environ, "TEST_OWNED_VALUE": "TEST_value"},
            stdout=output, stderr=subprocess.PIPE,
        ) as process:
            _stdout, stderr = process.communicate(timeout=5)
            assert process.returncode == 0 and stderr == ""
            error_pipe = process.stderr
        assert not output.closed
        assert error_pipe is not None and error_pipe.closed
    assert target.read_text().splitlines() == ["TEST_value", str(tmp_path)]


@pytest.mark.parametrize("exit_kind", ["leader-exited", "exception", "timeout"])
def test_descendants_are_stopped_after_leader_exit_or_failure_and_other_session_survives(
    tmp_path, monkeypatch, exit_kind,
):
    monkeypatch.setattr(owner, "_TERMINATE_GRACE_SECONDS", 0.1)
    marker = tmp_path / "TEST_grandchild.pid"
    child_code = (
        "import os,signal,sys; from pathlib import Path; "
        "signal.signal(signal.SIGTERM,signal.SIG_IGN); "
        "os.close(1); os.close(2); "
        "Path(sys.argv[1]).write_text(str(os.getpid())); signal.pause()"
    )
    leader_code = (
        "import signal,subprocess,sys; "
        f"subprocess.Popen([sys.executable,'-c',{child_code!r},sys.argv[1]]); "
        + ("sys.exit(0)" if exit_kind == "leader-exited" else "signal.pause()")
    )
    sentinel = subprocess.Popen(
        [sys.executable, "-c", "import signal; signal.pause()"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True,
    )
    process = None
    try:
        expected_error = {
            "leader-exited": nullcontext(),
            "exception": pytest.raises(_InjectedFailure, match="TEST injected"),
            "timeout": pytest.raises(subprocess.TimeoutExpired),
        }[exit_kind]
        with (
            expected_error,
            owner.owned_process(
                [sys.executable, "-c", leader_code, str(marker)],
                cwd=tmp_path, env=dict(os.environ),
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            ) as process,
        ):
            _wait_for(lambda: marker.exists() and bool(marker.read_text()))
            descendant = int(marker.read_text())
            assert os.getpgid(descendant) == process.pid
            assert os.getpgid(sentinel.pid) == sentinel.pid != process.pid
            pipes = (process.stdout, process.stderr)
            if exit_kind == "leader-exited":
                assert process.wait(timeout=5) == 0
                assert _running(descendant)
            elif exit_kind == "exception":
                raise _InjectedFailure("TEST injected")
            else:
                process.communicate(timeout=0.05)
        _wait_for(lambda: not _running(descendant))
        assert process.returncode is not None
        assert all(pipe is not None and pipe.closed for pipe in pipes)
        assert sentinel.poll() is None
    finally:
        # Keep a failing cleanup regression from leaking its deliberately stubborn child.
        if process is not None:
            with suppress(ProcessLookupError):
                os.killpg(process.pid, signal.SIGKILL)
        sentinel.terminate()
        sentinel.wait(timeout=5)
