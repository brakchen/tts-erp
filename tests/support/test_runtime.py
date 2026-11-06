"""Prepare assets from retained pytest items and expose execution costs.

Uses pytest's public collection/report hooks; outcome reporting remains owned by
selected_test_report. No database connection or test selection is performed here.
"""

from __future__ import annotations

import os
import subprocess
import time
from collections import defaultdict
from contextlib import ExitStack
from pathlib import Path

import pytest

from scripts._test_frontend import prepare_frontend

_started = 0.0
_collection_started = 0.0
_phases: dict[str, float] = defaultdict(float)


def record_phase(name: str, seconds: float) -> None:
    _phases[name] += seconds


def pytest_sessionstart(session: pytest.Session) -> None:
    global _started
    _started = time.monotonic()
    _phases.clear()


@pytest.hookimpl(hookwrapper=True)
def pytest_collection(session: pytest.Session):
    global _collection_started
    _collection_started = time.monotonic()
    yield


def pytest_collection_finish(session: pytest.Session) -> None:
    _phases["collection"] += time.monotonic() - _collection_started
    # Failed/empty collection must never start npm or mask the collection error.
    if session.testsfailed or not session.items or session.config.option.collectonly:
        return
    needs_build = os.environ.get("TTS_ERP_TEST_SELECTED_NO_BUILD") == "0" or any(
        item.get_closest_marker("requires_frontend_build")
        or "frontend_build" in item.fixturenames
        or item.nodeid.startswith("tests/browser/")
        for item in session.items
    )
    prepared = os.environ.get("TTS_ERP_TEST_PREPARED_RELEASE")
    if needs_build and not prepared:
        try:
            resources = ExitStack()
            session.config.add_cleanup(resources.close)
            result = resources.enter_context(prepare_frontend(Path(session.config.rootpath)))
            _phases["frontend_lock"] += result.wait_seconds
            _phases["frontend_build"] += result.build_seconds
            _phases["frontend_cache_check"] += result.cache_seconds
            print(f"[test-build] cache={'hit' if result.cached else 'miss'} release={result.release_id}", flush=True)
            os.environ["TTS_ERP_TEST_PREPARED_RELEASE"] = result.release_id
        except (OSError, ValueError, subprocess.SubprocessError) as exc:
            raise pytest.UsageError("Current frontend build failed; inspect build output") from exc
    # Reuse the existing API fixture's test-only manifest for build-free runs.
    os.environ["TTS_ERP_TEST_SELECTED_NO_BUILD"] = "0" if needs_build or prepared else "1"


def pytest_runtest_logreport(report: pytest.TestReport) -> None:
    _phases[report.when] += report.duration


def pytest_terminal_summary(terminalreporter) -> None:
    terminalreporter.section("test phase timings (seconds)")
    for name in ("collection", "frontend_lock", "frontend_cache_check", "frontend_build", "setup", "call", "teardown",
                 "browser_start", "browser_navigation", "browser_ready", "browser_cleanup"):
        terminalreporter.write_line(f"[test-timing] {name}={_phases[name]:.3f}s")
    terminalreporter.write_line(f"[test-timing] pytest_total={time.monotonic() - _started:.3f}s")
