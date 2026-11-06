"""Prove partition and aggregate failures without invoking a real test database."""
from __future__ import annotations

import contextlib
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts import _test_browser_parallel as parallel
from tests.support.owned_process import owned_process

FILES = ["tests/browser/test_frontend_ad_daily_filters.py", "tests/browser/test_frontend_dashboard_layout.py"]


def project(tmp_path, monkeypatch):
    for name in FILES:
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch()
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    (scripts / "test_isolated.sh").write_text(
        "set -eu\n"
        "test \"$1\" = browser\n"
        "printf '%s\\n' \"$@\" >> TEST_executed_$BASHPID\n"
        "case \"$*\" in *dashboard*) exit \"${TEST_DASHBOARD_EXIT:-0}\";; esac\n",
    )
    monkeypatch.setattr(parallel, "ROOT", tmp_path)
    monkeypatch.setattr(parallel, "prepare_frontend", lambda root: contextlib.nullcontext(SimpleNamespace(cached=True)))


def test_partition_keeps_modules_and_rejects_unknown_overlap_and_duplicates(tmp_path, monkeypatch):
    project(tmp_path, monkeypatch)
    nodes = [FILES[0] + "::test_a", FILES[1], FILES[0] + "::test_b"]
    groups = parallel.partition(nodes, 2)
    assert groups == [[nodes[0], nodes[2]], [nodes[1]]]
    for invalid in [[], [FILES[0], FILES[0]], [FILES[0], nodes[0]], ["tests/browser/test_media_workflows.py"], ["--collect-only"]]:
        with pytest.raises(ValueError):
            parallel.partition(invalid, 2)


@pytest.mark.parametrize("exit_code", [0, 1, 2, 5, 70])
def test_each_target_runs_once_and_any_child_failure_propagates(tmp_path, monkeypatch, exit_code):
    project(tmp_path, monkeypatch)
    monkeypatch.setenv("TEST_DASHBOARD_EXIT", str(exit_code))
    assert parallel.run(FILES, 2) == (0 if exit_code == 0 else 1)
    outputs = sorted(tmp_path.glob("TEST_executed_*"))
    assert len(outputs) == 2
    observed = [path.read_text().splitlines() for path in outputs]
    assert sorted(observed) == sorted([["browser", name] for name in FILES])


def test_termination_allows_children_to_run_cleanup_traps(tmp_path, monkeypatch):
    project(tmp_path, monkeypatch)
    (tmp_path / "scripts/test_isolated.sh").write_text(
        "set -eu\ntrap 'touch TEST_cleaned_$$; exit 130' INT TERM\n"
        "touch TEST_ready_$$\nsleep 60\n",
    )
    code = (
        "import contextlib, sys; from pathlib import Path; from types import SimpleNamespace;"
        "from scripts import _test_browser_parallel as p;"
        "p.ROOT=Path(sys.argv[1]);"
        "p.prepare_frontend=lambda root: contextlib.nullcontext(SimpleNamespace(cached=True));"
        "sys.argv=['parallel','--workers','2','browser',*sys.argv[2:]];"
        "sys.exit(p.main())"
    )
    output = tmp_path / "TEST_coordinator.log"
    with output.open("w") as log, owned_process(
        [sys.executable, "-c", code, str(tmp_path), *FILES], cwd=tmp_path,
        env={**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[2])},
        stdout=log, stderr=subprocess.STDOUT,
    ) as process:
        deadline = time.monotonic() + 10
        while len(list(tmp_path.glob("TEST_ready_*"))) < 2:
            assert process.poll() is None, output.read_text()
            assert time.monotonic() < deadline, output.read_text()
            time.sleep(0.02)
        os.kill(process.pid, signal.SIGTERM)
        assert process.wait(timeout=20) == 130, output.read_text()
    ready = {path.name.removeprefix("TEST_ready_") for path in tmp_path.glob("TEST_ready_*")}
    cleaned = {path.name.removeprefix("TEST_cleaned_") for path in tmp_path.glob("TEST_cleaned_*")}
    assert ready == cleaned and len(cleaned) == 2


def test_explicit_database_override_is_rejected_before_starting_workers(tmp_path, monkeypatch):
    project(tmp_path, monkeypatch)
    monkeypatch.setenv("TTS_ERP_TEST_DB_NAME", "tts_erp_test_TEST_fixed")
    with pytest.raises(ValueError, match="generated per-worker"):
        parallel.run(FILES, 2)
    assert not list(tmp_path.glob("TEST_executed_*"))


def test_prewarm_lease_is_released_before_children_revalidate_changed_inputs(tmp_path, monkeypatch):
    project(tmp_path, monkeypatch)
    import fcntl

    lock_path = tmp_path / "TEST_build.lock"

    @contextlib.contextmanager
    def warmup(root):
        with lock_path.open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_SH)
            yield SimpleNamespace(cached=True)

    monkeypatch.setattr(parallel, "prepare_frontend", warmup)
    # Represents the EX rebuild needed after a child loads differing .env.test
    # or a source save. An inherited parent SH lease would make flock fail.
    (tmp_path / "scripts/test_isolated.sh").write_text(
        "set -eu\nflock -x -w 2 TEST_build.lock -c 'echo TEST_rebuilt'\n",
    )
    assert parallel.run(FILES, 2) == 0


def test_second_worker_launch_failure_cleans_the_first_wrapper(tmp_path, monkeypatch):
    project(tmp_path, monkeypatch)
    (tmp_path / "scripts/test_isolated.sh").write_text(
        "set -eu\ntrap 'touch TEST_launch_cleaned; exit 130' INT TERM\n"
        "touch TEST_launch_ready\nsleep 60\n",
    )
    original = parallel.owned_process
    calls = 0

    @contextlib.contextmanager
    def launch(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("TEST_second_launch_failed")
        with original(*args, **kwargs) as process:
            deadline = time.monotonic() + 5
            while not (tmp_path / "TEST_launch_ready").exists():
                assert time.monotonic() < deadline
                time.sleep(0.02)
            yield process

    monkeypatch.setattr(parallel, "owned_process", launch)
    with pytest.raises(OSError, match="TEST_second_launch_failed"):
        parallel.run(FILES, 2)
    assert (tmp_path / "TEST_launch_cleaned").exists()
