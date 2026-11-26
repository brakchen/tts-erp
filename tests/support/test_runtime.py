"""Prepare assets from retained pytest items and expose execution costs.

Uses pytest's public collection/report hooks; outcome reporting remains owned by
selected_test_report. No database connection or test selection is performed here.
"""

from __future__ import annotations

import json
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
_collected = 0
_completed = 0
_current = ""
_stage = "starting"
_progress_path: str | None = None


def _progress() -> None:
    if not _progress_path:
        return
    target = Path(_progress_path)
    temporary = target.with_suffix(target.suffix + f".{os.getpid()}.tmp")
    temporary.write_text(json.dumps({
        "phase": os.environ.get("TTS_ERP_TEST_PHASE", "pytest"), "stage": _stage,
        "current": _current, "collected": _collected, "completed": _completed,
        "elapsed": round(time.monotonic() - _started, 3), "timings": dict(_phases),
    }), encoding="utf-8")
    temporary.replace(target)


def record_phase(name: str, seconds: float) -> None:
    _phases[name] += seconds
    _progress()


def pytest_sessionstart(session: pytest.Session) -> None:
    global _started, _collected, _completed, _current, _stage, _progress_path
    _started = time.monotonic()
    _phases.clear()
    _collected = _completed = 0
    _current = ""
    _stage = "collection"
    # Synthetic pytest children used by infrastructure regressions inherit the
    # environment; they must not overwrite their owning phase's live progress.
    owner = os.environ.get("TTS_ERP_TEST_PROGRESS_OWNER")
    _progress_path = os.environ.get("TTS_ERP_TEST_PROGRESS") if owner in {None, str(os.getpid())} else None
    if _progress_path:
        os.environ["TTS_ERP_TEST_PROGRESS_OWNER"] = str(os.getpid())
    _progress()


@pytest.hookimpl(hookwrapper=True)
def pytest_collection(session: pytest.Session):
    global _collection_started
    _collection_started = time.monotonic()
    yield


def pytest_collection_finish(session: pytest.Session) -> None:
    global _collected, _stage
    _phases["collection"] += time.monotonic() - _collection_started
    _collected = len(session.items)
    _stage = "collected"
    _progress()
    # Failed/empty collection must never start npm or mask the collection error.
    if session.testsfailed or not session.items or session.config.option.collectonly:
        return
    needs_build = os.environ.get("TTS_ERP_TEST_SELECTED_NO_BUILD") == "0" or any(
        item.get_closest_marker("requires_frontend_build")
        or "frontend_build" in item.fixturenames
        # tests/e2e/ drives the real React bundle through a live API child, so
        # it needs the frontend build exactly like tests/browser/ does. Without
        # it the page never boots, no API request is ever sent and the test
        # can only time out waiting for a response.
        or item.nodeid.startswith(("tests/browser/", "tests/e2e/"))
        for item in session.items
    )
    prepared = os.environ.get("TTS_ERP_TEST_PREPARED_RELEASE")
    if needs_build and not prepared:
        _stage = "frontend_prepare"
        _progress()
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
    _stage = "running"
    _progress()


def pytest_runtest_logstart(nodeid: str, location) -> None:
    global _current, _stage
    _current, _stage = nodeid, "setup"
    _progress()


def pytest_runtest_call(item: pytest.Item) -> None:
    global _stage
    _stage = "call"
    _progress()


def pytest_runtest_teardown(item: pytest.Item, nextitem: pytest.Item | None) -> None:
    global _stage
    _stage = "teardown"
    _progress()


def pytest_runtest_logreport(report: pytest.TestReport) -> None:
    global _completed, _stage
    _phases[report.when] += report.duration
    _stage = report.when
    if report.when == "teardown":
        _completed += 1
    _progress()


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    global _stage
    _stage = "complete"
    _phases["pytest_total"] = time.monotonic() - _started
    _progress()


def pytest_terminal_summary(terminalreporter) -> None:
    terminalreporter.section("test phase timings (seconds)")
    for name in ("collection", "frontend_lock", "frontend_cache_check", "frontend_build", "setup", "call", "teardown",
                 "browser_start", "browser_navigation", "browser_ready", "browser_cleanup"):
        terminalreporter.write_line(f"[test-timing] {name}={_phases[name]:.3f}s")
    terminalreporter.write_line(f"[test-timing] pytest_total={time.monotonic() - _started:.3f}s")
