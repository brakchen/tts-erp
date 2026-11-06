"""Exercise build decisions after real pytest collection and deselection."""

from __future__ import annotations

import fcntl
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

from scripts._test_frontend import input_key, prepare_frontend
from tests.support.owned_process import owned_process

ROOT = Path(__file__).resolve().parents[2]


def _project(tmp_path: Path) -> None:
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    (tmp_path / "pytest.ini").write_text(
        "[pytest]\nmarkers = requires_frontend_build: needs real assets\n", encoding="utf-8",
    )
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts/build_frontend.sh").write_text(
        "set -eu\n"
        "echo build >> TEST_builds\n"
        'exit_code="${TEST_BUILD_EXIT:-0}"\n'
        'if test "$exit_code" != 0; then exit "$exit_code"; fi\n'
        "mkdir -p tts_erp_v2/static/frontend\n"
        "mkdir -p tts_erp_v2/static/frontend/TEST_current\n"
        "echo TEST_asset > tts_erp_v2/static/frontend/TEST_current/main.js\n"
        "printf '%s' '{\"releaseId\":\"TEST_current\"}' > tts_erp_v2/static/frontend/release.json\n",
        encoding="utf-8",
    )
    (tmp_path / "test_sample.py").write_text(
        "import os\nimport fcntl\nfrom pathlib import Path\nimport pytest\n"
        "def test_plain(): pass\n"
        "@pytest.mark.requires_frontend_build\n"
        "def test_assets():\n"
        "    assert os.environ['TTS_ERP_TEST_PREPARED_RELEASE'] == 'TEST_current'\n"
        "    open('TEST_executed', 'w').close()\n"
        "@pytest.fixture\n"
        "def frontend_build(): return os.environ['TTS_ERP_TEST_PREPARED_RELEASE']\n"
        "def test_fixture(frontend_build): assert frontend_build == 'TEST_current'\n",
        encoding="utf-8",
    )
    with (tmp_path / "test_sample.py").open("a", encoding="utf-8") as sample:
        sample.write(
            "@pytest.mark.requires_frontend_build\n"
            "def test_release_is_locked():\n"
            "    with Path('.git/tts-test-frontend.lock').open('a') as lock:\n"
            "        fcntl.flock(lock, fcntl.LOCK_SH | fcntl.LOCK_NB)\n"
            "        with pytest.raises(BlockingIOError):\n"
            "            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)\n"
        )


def _run(tmp_path: Path, *args: str, build_exit: int = 0) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, "PYTHONPATH": str(ROOT), "PYTEST_ADDOPTS": "", "TEST_BUILD_EXIT": str(build_exit)}
    for name in ("TTS_ERP_TEST_PREPARED_RELEASE", "TTS_ERP_TEST_SELECTED_NO_BUILD"):
        env.pop(name, None)
    return subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "tests.support.test_runtime", "--durations=3", *args],
        cwd=tmp_path, env=env, text=True, capture_output=True, timeout=30, check=False,
    )


def test_deselected_asset_tests_do_not_build(tmp_path: Path) -> None:
    _project(tmp_path)
    result = _run(tmp_path, "-k", "plain")
    assert result.returncode == 0, result.stdout + result.stderr
    assert not (tmp_path / "TEST_builds").exists()
    assert "[test-timing] frontend_build=0.000s" in result.stdout
    assert "[test-timing] call=" in result.stdout


def test_marker_and_fixture_build_current_assets_once(tmp_path: Path) -> None:
    _project(tmp_path)
    result = _run(tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr
    assert (tmp_path / "TEST_builds").read_text().splitlines() == ["build"]
    assert (tmp_path / "TEST_executed").exists()
    with (tmp_path / ".git/tts-test-frontend.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)


def test_collection_only_does_not_build(tmp_path: Path) -> None:
    _project(tmp_path)
    result = _run(tmp_path, "--collect-only")
    assert result.returncode == 0, result.stdout + result.stderr
    assert not (tmp_path / "TEST_builds").exists()


def test_build_failure_stops_before_test_execution(tmp_path: Path) -> None:
    _project(tmp_path)
    result = _run(tmp_path, build_exit=7)
    assert result.returncode != 0
    assert "Current frontend build failed" in result.stderr
    assert not (tmp_path / "TEST_executed").exists()


@pytest.mark.parametrize("source,code", [("def test_broken(:", 2), ("", 5)])
def test_failed_or_empty_collection_does_not_build(tmp_path: Path, source: str, code: int) -> None:
    _project(tmp_path)
    (tmp_path / "test_sample.py").write_text(source, encoding="utf-8")
    result = _run(tmp_path)
    assert result.returncode == code, result.stdout + result.stderr
    assert not (tmp_path / "TEST_builds").exists()


def test_cache_revalidates_content_environment_installation_and_artifacts(tmp_path, monkeypatch):
    _project(tmp_path)
    source = tmp_path / "frontend/src/main.tsx"
    source.parent.mkdir(parents=True)
    source.write_text("TEST_first")
    with prepare_frontend(tmp_path) as cold:
        assert not cold.cached
    with prepare_frontend(tmp_path) as warm:
        assert warm.cached and warm.build_seconds == 0
    changes = [
        lambda: source.write_text("TEST_changed"),
        lambda: (source.parent.parent / "package-lock.json").write_text("TEST_lock"),
        lambda: monkeypatch.setenv("VITE_TEST_CACHE_INPUT", "TEST_environment"),
        lambda: (tmp_path / "tts_erp_v2/static/frontend/TEST_current/main.js").write_text("TEST_corrupt"),
        lambda: (tmp_path / ".git/tts-test-frontend.json").write_text("malformed"),
    ]
    for change in changes:
        change()
        with prepare_frontend(tmp_path) as rebuilt:
            assert not rebuilt.cached
        with prepare_frontend(tmp_path) as checked:
            assert checked.cached
    assert len((tmp_path / "TEST_builds").read_text().splitlines()) == 6
    installed = tmp_path / "frontend/node_modules/.package-lock.json"
    installed.parent.mkdir(parents=True)
    installed.write_text("TEST_installation")
    before = input_key(tmp_path)
    os.utime(installed, ns=(1, 1))
    assert input_key(tmp_path) != before


def test_failed_rebuild_does_not_accept_previous_release(tmp_path, monkeypatch):
    _project(tmp_path)
    with prepare_frontend(tmp_path):
        pass
    monkeypatch.setenv("VITE_TEST_CACHE_INPUT", "TEST_new")
    monkeypatch.setenv("TEST_BUILD_EXIT", "7")
    with pytest.raises(subprocess.CalledProcessError), prepare_frontend(tmp_path):
        pytest.fail("Failed rebuild must not yield a stale release")
    assert not (tmp_path / ".git/tts-test-frontend.json").exists()


def test_source_change_during_build_cannot_publish_cache(tmp_path):
    _project(tmp_path)
    script = tmp_path / "scripts/build_frontend.sh"
    with script.open("a") as output:
        output.write("echo '# TEST_mutation' >> scripts/build_frontend.sh\n")
    with pytest.raises(ValueError, match="inputs changed"), prepare_frontend(tmp_path):
        pytest.fail("A changing input is not valid cache evidence")
    assert not (tmp_path / ".git/tts-test-frontend.json").exists()


@pytest.mark.parametrize("mode", ["timeout", "terminate"])
def test_cancelled_build_cannot_leave_a_descendant_writer(tmp_path, monkeypatch, mode):
    from scripts import _test_frontend

    _project(tmp_path)
    (tmp_path / "scripts/build_frontend.sh").write_text(
        "set -eu\n"
        "(trap '' TERM; touch TEST_writer_started; sleep 1; echo TEST_stale > TEST_late_write) &\n"
        "wait\n",
    )
    if mode == "timeout":
        monkeypatch.setattr(_test_frontend, "BUILD_TIMEOUT_SECONDS", 0.2)
        with pytest.raises(subprocess.TimeoutExpired), prepare_frontend(tmp_path):
            pytest.fail("A timed-out build must not produce a lease")
        time.sleep(1)
        assert not (tmp_path / "TEST_late_write").exists()
    else:
        code = (
            "import sys; from pathlib import Path; from scripts._test_frontend import prepare_frontend;"
            "\nwith prepare_frontend(Path(sys.argv[1])): raise AssertionError('unexpected build completion')"
        )
        with (tmp_path / "TEST_build.log").open("w") as log, owned_process(
            [sys.executable, "-c", code, str(tmp_path)], cwd=tmp_path,
            env={**os.environ, "PYTHONPATH": str(ROOT)}, stdout=log, stderr=subprocess.STDOUT,
        ) as process:
            deadline = time.monotonic() + 10
            while not (tmp_path / "TEST_writer_started").exists():
                assert process.poll() is None
                assert time.monotonic() < deadline
                time.sleep(0.02)
            os.kill(process.pid, signal.SIGTERM)
            assert process.wait(timeout=10) != 0
            time.sleep(1)
            assert not (tmp_path / "TEST_late_write").exists()
    assert not (tmp_path / ".git/tts-test-frontend.json").exists()
    with (tmp_path / ".git/tts-test-frontend.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
