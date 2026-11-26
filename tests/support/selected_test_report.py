"""Report actual selected pytest outcomes to the parent baseline driver."""

from __future__ import annotations

import json
import os
from collections.abc import Iterator
from pathlib import Path

import pytest
from _pytest.terminal import TerminalReporter

_collected: list[str] = []
_declared_marks: dict[str, list[str]] = {}
_allowed_skips: dict[str, dict[str, object]] = {}


def _matches(node: str, target: str) -> bool:
    return node == target or node.startswith((f"{target}::", f"{target}[", f"{target}/"))


def _phase(item: pytest.Item) -> str:
    if (item.nodeid.startswith(("tests/browser/", "tests/e2e/"))
            or item.get_closest_marker("requires_frontend_build")
            or item.get_closest_marker("requires_browser")
            or "frontend_build" in item.fixturenames):
        return "browser"
    return "frontend" if item.nodeid.startswith("tests/frontend/") else "backend"


@pytest.hookimpl(hookwrapper=True, tryfirst=True)
def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> Iterator[None]:
    # Run after marker selection and directory conftests have finished. Pytest's
    # public collection hook preserves the same SAFE_MARKS policy in all phases.
    yield
    live = json.loads(os.environ["TTS_ERP_TEST_SELECTED_LIVE"])
    phase = os.environ["TTS_ERP_TEST_PHASE"]
    deselected = [
        item
        for item in items
        if _phase(item) != phase or (
            live is not None and item.get_closest_marker("requires_frontend_build")
            and not any(_matches(item.nodeid, target) for target in live)
        )
    ]
    if deselected:
        config.hook.pytest_deselected(items=deselected)
        rejected = set(deselected)
        items[:] = [item for item in items if item not in rejected]
    _collected[:] = [item.nodeid for item in items]
    _declared_marks.clear()
    _allowed_skips.clear()
    _declared_marks.update({
        item.nodeid: sorted({mark.name for mark in item.iter_markers() if mark.name in {"skip", "skipif"}})
        for item in items
    })


@pytest.hookimpl(hookwrapper=True, tryfirst=True)
def pytest_runtest_makereport(item: pytest.Item, call: pytest.CallInfo[None]) -> Iterator[None]:
    outcome = yield
    report = outcome.get_result()
    # Pytest's own skip-mark setup raises skip.Exception with this evidence.
    # Observe the actual decision instead of evaluating skipif expressions a
    # second time. A false skipif followed by pytest.skip() is not an allowance.
    # Pytest 9.0.2 skip-mark protocol (MIT):
    # https://github.com/pytest-dev/pytest/blob/9.0.2/src/_pytest/skipping.py
    if (report.when == "setup" and report.skipped and not hasattr(report, "wasxfail")
            and _declared_marks.get(item.nodeid) and call.excinfo is not None
            and isinstance(call.excinfo.value, pytest.skip.Exception)
            and getattr(call.excinfo.value, "_use_item_location", False)):
        _allowed_skips[item.nodeid] = {
            "nodeid": item.nodeid, "markers": _declared_marks[item.nodeid],
            "reason": str(call.excinfo.value),
        }


def pytest_terminal_summary(terminalreporter: TerminalReporter, exitstatus: int) -> None:
    stats = terminalreporter.stats
    ran = {
        report.nodeid
        for outcome in ("passed", "failed", "xfailed", "xpassed")
        for report in stats.get(outcome, ())
        if getattr(report, "when", None) == "call"
    }
    failed = {
        report.nodeid
        for outcome in ("failed", "error")
        for report in stats.get(outcome, ())
        if getattr(report, "when", None) in {"setup", "call", "teardown"}
    }
    terminal = {
        report.nodeid: outcome
        for outcome in ("passed", "xfailed", "xpassed", "skipped", "failed", "error")
        for report in stats.get(outcome, ())
        if (getattr(report, "when", None) == "call"
            or (getattr(report, "when", None) in {"setup", "teardown"}
                and outcome in {"failed", "error", "skipped", "xfailed"}))
    }
    required = json.loads(os.environ["TTS_ERP_TEST_SELECTION_REQUIRED"])
    evidence = ran | failed | {node for node, outcome in terminal.items() if outcome == "xfailed"}
    missing = [target for target in required if not any(
        _matches(node, target) for node in evidence | (set(_allowed_skips) if "::" not in target else set())
    )]
    missing.extend(f"unreported:{node}" for node in _collected if node not in terminal)
    missing.extend(f"skipped:{node}" for node, outcome in terminal.items()
                   if outcome == "skipped" and (
                       node not in _allowed_skips
                       or any("::" in target and _matches(node, target) for target in required)
                   ))
    if os.environ["TTS_ERP_TEST_SELECTION_BACKEND_ALL"] == "1" and not (ran | failed):
        missing.append("backend")
    progress = {}
    progress_path = os.environ.get("TTS_ERP_TEST_PROGRESS")
    if progress_path:
        try:
            progress = json.loads(Path(progress_path).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            pass
    Path(os.environ["TTS_ERP_TEST_SELECTION_REPORT"]).write_text(
        json.dumps({
            "status": int(exitstatus), "ran": len(ran), "failed": sorted(failed), "missing": missing,
            "phase": os.environ["TTS_ERP_TEST_PHASE"], "required": required,
            "nodes": sorted(ran | failed), "collectedNodes": _collected,
            "terminalOutcomes": terminal,
            "allowedSkips": [_allowed_skips[node] for node in sorted(_allowed_skips)],
            "timings": progress.get("timings", {}),
        }),
        encoding="utf-8",
    )
