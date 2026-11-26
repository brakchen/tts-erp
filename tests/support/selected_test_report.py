"""Report actual selected pytest outcomes to the parent baseline driver."""

from __future__ import annotations

import json
import os
from collections.abc import Iterator
from pathlib import Path

import pytest
from _pytest.terminal import TerminalReporter

_collected: list[str] = []


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
    required = json.loads(os.environ["TTS_ERP_TEST_SELECTION_REQUIRED"])
    missing = [target for target in required if not any(_matches(node, target) for node in ran | failed)]
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
            "timings": progress.get("timings", {}),
        }),
        encoding="utf-8",
    )
