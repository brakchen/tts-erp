"""Guard sequential wrapper dispatch, complete evidence and cancellation cleanup."""

from __future__ import annotations

import json
import os
import signal
import subprocess
from contextlib import contextmanager
from pathlib import Path

import pytest

from scripts import _test_phases as phases
from scripts._run_selected_tests import Phase
from scripts._test_selection import Selection


def _report(phase: Phase, *, status: int = 0, node: str | None = None) -> dict:
    node = node or f"{phase.targets[0]}::test_example"
    return {
        "phase": phase.name, "required": phase.required, "status": status, "ran": 1,
        "failed": [node] if status == 1 else [], "missing": [], "nodes": [node], "collectedNodes": [node],
    }


@pytest.fixture
def dispatch(monkeypatch, tmp_path):
    for name in ("TTS_ERP_TEST_RUN_PROOF", "TTS_ERP_TEST_RUN_ID", "TTS_ERP_TEST_DB_NAME"):
        monkeypatch.delenv(name, raising=False)
    selected = [Phase(name, [f"tests/{name}/test_example.py"], [f"tests/{name}/test_example.py"])
                for name in ("backend", "frontend", "browser")]
    plan = Selection(frontend=True)
    monkeypatch.setattr(phases, "committed_plan", lambda *_args: ("base", "head", plan))
    monkeypatch.setattr(phases, "phase_plan", lambda *_args: selected)
    return selected, plan, tmp_path / "report.json"


def test_dispatch_uses_new_wrapper_per_phase_and_refreshes_only_first(dispatch, monkeypatch):
    selected, plan, destination = dispatch
    plan.refresh_template = True
    calls = []
    monkeypatch.setenv("TTS_ERP_TEST_PREPARED_RELEASE", "TEST_stale")
    monkeypatch.setenv("TTS_ERP_TEST_REFRESHED_SELECTION_HEAD", "TEST_stale")

    def execute(args, env, name, log, progress):
        phase = next(entry for entry in selected if entry.name == name)
        calls.append((args, env))
        assert args[:2] == ["bash", "scripts/test_isolated.sh"]
        assert "pytest" not in args and "TTS_ERP_TEST_PREPARED_RELEASE" not in env
        assert env["TTS_ERP_TEST_PROGRESS"] == str(progress)
        report = Path(args[args.index("--report") + 1])
        report.write_text(json.dumps(_report(phase, status=int(name == "backend"))))
        if name == "frontend":
            Path(env["TTS_ERP_COMPONENT_REPORT"]).write_text(json.dumps({
                "schemaVersion": 1, "complete": True, "status": "passed", "failedTests": [], "rerunCommands": [],
            }))
        return int(name == "backend")

    monkeypatch.setattr(phases, "_execute", execute)
    assert phases.run("base", "head", str(destination), ["page-assets"], "auto", refresh=True) == 1
    assert len(calls) == 3  # Ordinary assertion failure does not suppress the remaining phases.
    assert ["--refresh-template" in args for args, _ in calls] == [True, False, False]
    assert "TTS_ERP_TEST_REFRESHED_SELECTION_HEAD" not in calls[0][1]
    assert all(env["TTS_ERP_TEST_REFRESHED_SELECTION_HEAD"] == "head" for _, env in calls[1:])
    assert all(args[-2:] == ["--scenario", "page-assets"] for args, _ in calls)
    result = json.loads(destination.read_text())
    assert result["ran"] == 3 and result["missing"] == []
    assert len(result["failed"]) == 1
    assert [phase["name"] for phase in result["phases"]] == [entry.name for entry in selected]
    assert all(phase["elapsedSeconds"] >= 0 for phase in result["phases"])


@pytest.mark.parametrize("failure", ["missing", "malformed", "cleanup", "cancelled"])
def test_infrastructure_failure_retains_never_started_phase_requirements(dispatch, monkeypatch, failure):
    selected, _plan, destination = dispatch
    calls = []
    # A prior successful file must not make a failed wrapper appear complete.
    (destination.parent / "backend.json").write_text(json.dumps(_report(selected[0])))

    def execute(args, _env, name, _log, _progress):
        calls.append(name)
        child = Path(args[args.index("--report") + 1])
        assert not child.exists()
        if failure == "malformed":
            child.write_text("[]")
        elif failure == "cleanup":
            child.write_text(json.dumps(_report(selected[0])))
            return 70
        elif failure == "cancelled":
            raise KeyboardInterrupt
        return 2

    monkeypatch.setattr(phases, "_execute", execute)
    if failure == "cancelled":
        with pytest.raises(KeyboardInterrupt):
            phases.run("base", "head", str(destination), [], "auto")
    else:
        assert phases.run("base", "head", str(destination), [], "auto") == 2
    assert calls == ["backend"]
    result = json.loads(destination.read_text())
    assert {"phase:frontend", "phase:browser", *selected[1].required, *selected[2].required} <= set(result["missing"])


@pytest.mark.parametrize("component", [None, {}, {"schemaVersion": 1, "status": "incomplete", "complete": False}])
def test_incomplete_component_evidence_cannot_be_green(component):
    phase = Phase("frontend", ["tests/frontend/test_components.py"], ["tests/frontend/test_components.py"])
    result = phases.aggregate([phase], {"frontend": {
        "report": _report(phase), "exitCode": 0, "componentsRequired": True, "components": component,
    }})
    assert result["status"] == 2
    assert result["missing"] == ["frontend:component-report"]


def test_duplicate_nodes_and_no_selection_fail_closed():
    first, second = Phase("backend", ["tests/api"], []), Phase("browser", ["tests/api"], [])
    node = "tests/api/test_assets.py::test_shared"
    result = phases.aggregate([first, second], {
        phase.name: {"report": _report(phase, node=node), "exitCode": 0} for phase in (first, second)
    })
    assert result["status"] == 2 and result["missing"] == [f"duplicate:{node}"]
    assert phases.aggregate([], {})["missing"] == ["selection:empty"]


def test_claimed_pass_with_zero_execution_fails_closed():
    phase = Phase("backend", ["tests/api"], ["tests/api"])
    value = {**_report(phase), "nodes": [], "collectedNodes": [], "ran": 0}
    result = phases.aggregate([phase], {"backend": {"report": value, "exitCode": 0}})
    assert result["status"] == 2 and "phase:backend:empty" in result["missing"]


def test_phase_heartbeat_shows_progress_without_streaming_full_logs(tmp_path, monkeypatch, capsys):
    progress, log = tmp_path / "progress.json", tmp_path / "phase.log"
    progress.write_text(json.dumps({"stage": "call", "current": "TEST_case", "completed": 2, "collected": 4}))

    class Process:
        calls = 0

        def wait(self, timeout):
            assert timeout <= 30
            self.calls += 1
            if self.calls == 1:
                raise subprocess.TimeoutExpired("TEST_command", timeout)
            return 0

        def poll(self):
            return 0

    @contextmanager
    def owner(*_args, **_kwargs):
        yield Process()

    monkeypatch.setattr(phases, "owned_process", owner)
    assert phases._execute(["TEST_command"], {}, "backend", log, progress) == 0
    output = capsys.readouterr().out
    assert output.count("[test-progress]") == 2
    assert "completed=2/4 current=TEST_case" in output and str(log) in output


def test_cancelled_phase_waits_for_wrapper_cleanup_before_owner_exits(tmp_path, monkeypatch):
    signals = []

    class Process:
        pid = 12345
        cleaned = False

        def wait(self, timeout):
            if not signals:
                raise KeyboardInterrupt
            assert timeout == phases.CLEANUP_GRACE_SECONDS
            self.cleaned = True
            return 130

        def poll(self):
            return 130 if self.cleaned else None

    process = Process()

    @contextmanager
    def owner(*_args, **_kwargs):
        try:
            yield process
        finally:
            assert process.cleaned

    monkeypatch.setattr(phases, "owned_process", owner)
    monkeypatch.setattr(os, "killpg", lambda pid, signum: signals.append((pid, signum)))
    with pytest.raises(KeyboardInterrupt):
        phases._execute(["TEST_command"], {}, "backend", tmp_path / "phase.log", tmp_path / "progress.json")
    assert signals == [(process.pid, signal.SIGINT)]


@pytest.mark.parametrize("variable", ["TTS_ERP_TEST_RUN_PROOF", "TTS_ERP_TEST_RUN_ID", "TTS_ERP_TEST_DB_NAME"])
def test_outer_refuses_inherited_run_identity(dispatch, monkeypatch, variable):
    _selected, _plan, destination = dispatch
    monkeypatch.setenv(variable, "TEST_existing")
    with pytest.raises(ValueError, match="existing isolated run|generated per-phase"):
        phases.run("base", "head", str(destination), [], "auto")
