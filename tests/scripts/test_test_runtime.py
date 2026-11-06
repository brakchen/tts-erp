"""Exercise build decisions after real pytest collection and deselection."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

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
        "printf '%s' '{\"releaseId\":\"TEST_current\"}' > tts_erp_v2/static/frontend/release.json\n",
        encoding="utf-8",
    )
    (tmp_path / "test_sample.py").write_text(
        "import os\nimport pytest\n"
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
