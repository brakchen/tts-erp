"""Report actual selected pytest outcomes to the parent baseline driver."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from _pytest.terminal import TerminalReporter


def _matches(node: str, target: str) -> bool:
    return node == target or node.startswith((f"{target}::", f"{target}[", f"{target}/"))


@pytest.hookimpl(trylast=True)
def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    live = json.loads(os.environ["TTS_ERP_TEST_SELECTED_LIVE"])
    if live is None:
        return
    deselected = [
        item
        for item in items
        if item.get_closest_marker("requires_frontend_build")
        and not any(_matches(item.nodeid, target) for target in live)
    ]
    if deselected:
        config.hook.pytest_deselected(items=deselected)
        rejected = set(deselected)
        items[:] = [item for item in items if item not in rejected]


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
    if os.environ["TTS_ERP_TEST_SELECTION_BACKEND_ALL"] == "1" and not any(
        node.startswith("tests/") and not node.startswith(("tests/frontend/", "tests/browser/"))
        and not any(_matches(node, target) for target in required)
        for node in ran | failed
    ):
        missing.append("backend")
    Path(os.environ["TTS_ERP_TEST_SELECTION_REPORT"]).write_text(
        json.dumps({"status": int(exitstatus), "ran": len(ran), "failed": sorted(failed), "missing": missing}),
        encoding="utf-8",
    )
