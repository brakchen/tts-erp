"""Run sequential phases through fresh isolated wrappers and retain their evidence.

Reuses the repository's owned_process and pytest report hooks rather than a
second test runner. The parent never imports pytest or opens a database.
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import time
from collections import Counter
from contextlib import suppress
from dataclasses import asdict
from pathlib import Path
from typing import Any

from scripts._run_selected_tests import (
    ROOT,
    SCENARIOS,
    Phase,
    _matches,
    committed_plan,
    phase_plan,
)
from tests.support.owned_process import owned_process

HEARTBEAT_SECONDS = 20
CLEANUP_GRACE_SECONDS = 30


def _write(path: Path, value: dict[str, Any]) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2), encoding="utf-8")
    temporary.replace(path)


def _read(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else None
    except (OSError, ValueError):
        return None


def _heartbeat(phase: str, progress: Path, log: Path, started: float) -> None:
    value = _read(progress) or {}
    print(
        f"[test-progress] phase={phase} stage={value.get('stage', 'isolated_setup')} "
        f"completed={value.get('completed', 0)}/{value.get('collected', '?')} "
        f"current={value.get('current') or '-'} elapsed={time.monotonic() - started:.0f}s log={log}",
        flush=True,
    )


def _execute(args: list[str], env: dict[str, str], phase: str, log: Path, progress: Path) -> int:
    started = time.monotonic()
    with log.open("w", encoding="utf-8") as output, owned_process(
        args, cwd=ROOT, env=env, stdout=output, stderr=subprocess.STDOUT,
    ) as process:
        try:
            while True:
                _heartbeat(phase, progress, log, started)
                try:
                    return process.wait(timeout=HEARTBEAT_SECONDS)
                except subprocess.TimeoutExpired:
                    continue
        finally:
            if process.poll() is None:
                # Pytest finalizers and Bash EXIT cleanup need time before the
                # existing process owner applies its final TERM/KILL fence.
                for signum in (signal.SIGINT, signal.SIGTERM):
                    with suppress(ProcessLookupError):
                        os.killpg(process.pid, signum)
                    try:
                        process.wait(timeout=CLEANUP_GRACE_SECONDS)
                        break
                    except subprocess.TimeoutExpired:
                        _heartbeat(phase, progress, log, started)


def _strings(value: Any) -> bool:
    return isinstance(value, list) and all(isinstance(entry, str) for entry in value)


def _valid_report(value: dict[str, Any] | None, phase: Phase) -> bool:
    return bool(value is not None and value.get("phase") == phase.name
                and value.get("required") == phase.required
                and type(value.get("status")) is int and value["status"] in range(6)
                and type(value.get("ran")) is int and value["ran"] >= 0
                and all(_strings(value.get(key)) for key in ("failed", "missing", "nodes", "collectedNodes")))


def aggregate(phases: list[Phase], results: dict[str, dict[str, Any]]) -> dict[str, Any]:
    missing: set[str] = set()
    failed: set[str] = set()
    seen: set[str] = set()
    duplicates: set[str] = set()
    ran = 0
    for phase in phases:
        result = results.get(phase.name, {})
        value = result.get("report")
        if not _valid_report(value, phase):
            missing.update([f"phase:{phase.name}", *phase.required])
            continue
        missing.update(value["missing"])
        missing.update(target for target in phase.required if not any(_matches(node, target) for node in value["nodes"]))
        failed.update(value["failed"])
        ran += value["ran"]
        collected = set(value["collectedNodes"])
        duplicates.update(node for node, count in Counter(value["collectedNodes"]).items() if count > 1)
        duplicates.update(seen & collected)
        seen.update(collected)
        if (not set(value["nodes"]) <= collected or not set(value["failed"]) <= set(value["nodes"])
                or value["ran"] > len(value["nodes"])):
            missing.add(f"phase:{phase.name}:invalid-nodes")
        code = result.get("exitCode")
        if code not in {0, 1} or value["status"] != code or bool(value["failed"]) != bool(code):
            missing.add(f"phase:{phase.name}:incomplete")
        if not value["nodes"]:
            missing.add(f"phase:{phase.name}:empty")
        component = result.get("components")
        if result.get("componentsRequired") and (
            not isinstance(component, dict) or component.get("schemaVersion") != 1
            or component.get("complete") is not True or component.get("status") not in {"passed", "failed"}
        ):
            missing.add("frontend:component-report")
    missing.update(f"duplicate:{node}" for node in duplicates)
    if not phases:
        missing.add("selection:empty")
    elif not ran:
        missing.add("selection:zero-executions")
    return {
        "status": 2 if missing else int(bool(failed)), "ran": ran,
        "failed": sorted(failed), "missing": sorted(missing),
        "phases": [{"name": phase.name, "required": phase.required, **results.get(phase.name, {})}
                   for phase in phases],
    }


def _component_summary(value: dict[str, Any] | None) -> None:
    if value is None:
        return
    print(f"[test-components] status={value.get('status')} complete={value.get('complete')}", flush=True)
    for failure in value.get("failedTests", []):
        if isinstance(failure, dict):
            print(f"[test-components] FAILED {failure.get('file')} :: {failure.get('name')}", flush=True)
    for command in value.get("rerunCommands", []):
        print(f"[test-components] rerun: {command}", flush=True)


def run(base: str, head: str, report: str, scenarios: list[str], scope: str, refresh: bool = False) -> int:
    if os.environ.get("TTS_ERP_TEST_RUN_PROOF") or os.environ.get("TTS_ERP_TEST_RUN_ID"):
        raise ValueError("phase orchestration must start outside an existing isolated run")
    if os.environ.get("TTS_ERP_TEST_DB_NAME"):
        raise ValueError("phase orchestration requires generated per-phase database names")
    base, head, plan = committed_plan(base, head, scenarios, scope)
    phases = phase_plan(plan, scope)
    if plan.refresh_template and not refresh:
        raise ValueError("schema changes require --refresh-template")
    destination = Path(report).resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    print(json.dumps({"base": base, "head": head, "scope": scope, "phases": [asdict(p) for p in phases]}, indent=2),
          flush=True)
    results: dict[str, dict[str, Any]] = {}
    started = time.monotonic()
    refreshed = False

    def save() -> dict[str, Any]:
        value = aggregate(phases, results)
        value.update(base=base, head=head, scope=scope, elapsedSeconds=round(time.monotonic() - started, 3))
        _write(destination, value)
        return value

    save()  # Even cancellation before the first child records every missing phase.
    try:
        for index, phase in enumerate(phases):
            child_report = destination.parent / f"{phase.name}.json"
            log = destination.parent / f"{phase.name}.log"
            progress = child_report.with_suffix(".progress.json")
            components = destination.parent / f"{phase.name}.components.json"
            # Never accept a previous invocation's successful report as evidence.
            for artifact in (child_report, progress, components):
                artifact.unlink(missing_ok=True)
            env = os.environ.copy()
            for name in ("TTS_ERP_TEST_PREPARED_RELEASE", "TTS_ERP_TEST_REFRESHED_SELECTION_HEAD",
                         "TTS_ERP_COMPONENT_REPORT", "TTS_ERP_TEST_PROGRESS"):
                env.pop(name, None)
            env.update(TTS_ERP_TEST_PHASE=phase.name, TTS_ERP_TEST_PROGRESS=str(progress))
            if refreshed:
                env["TTS_ERP_TEST_REFRESHED_SELECTION_HEAD"] = head
            components_required = phase.name == "frontend" and plan.frontend
            if components_required:
                env["TTS_ERP_COMPONENT_REPORT"] = str(components)
            args = ["bash", "scripts/test_isolated.sh"]
            if refresh and index == 0:
                args.append("--refresh-template")
            args.extend(["selected", "--scope", scope, "--base", base, "--head", head,
                         "--report", str(child_report), "--phase", phase.name])
            for scenario in scenarios:
                args.extend(["--scenario", scenario])
            phase_started = time.monotonic()
            result: dict[str, Any] = {
                "log": str(log), "progress": str(progress), "reportPath": str(child_report),
                "componentsRequired": components_required,
            }
            results[phase.name] = result
            try:
                result["exitCode"] = _execute(args, env, phase.name, log, progress)
            finally:
                result["elapsedSeconds"] = round(time.monotonic() - phase_started, 3)
                result["report"] = _read(child_report)
                if components_required:
                    result["components"] = _read(components)
                save()
            print(f"[test-phase] {phase.name} exit={result['exitCode']} elapsed={result['elapsedSeconds']}s log={log}",
                  flush=True)
            _component_summary(result.get("components"))
            value = result["report"]
            if _valid_report(value, phase):
                for node in value["failed"]:
                    print(f"FAILED {node}", flush=True)
            # A missing report means the wrapper/collection never established
            # usable execution evidence. Cleanup failure is always terminal.
            if not _valid_report(value, phase) or result["exitCode"] in {70, 130, 143}:
                break
            if refresh and index == 0:
                refreshed = True
    finally:
        value = save()
    return value["status"]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", required=True)
    parser.add_argument("--head", required=True)
    parser.add_argument("--scope", choices=("auto", "fast", "frontend", "backend"), default="auto")
    parser.add_argument("--report", required=True)
    parser.add_argument("--scenario", choices=sorted(SCENARIOS), action="append", default=[])
    parser.add_argument("--refresh-template", action="store_true")
    args = parser.parse_args()
    signal.signal(signal.SIGTERM, lambda *_: (_ for _ in ()).throw(KeyboardInterrupt()))
    try:
        return run(args.base, args.head, args.report, args.scenario, args.scope, args.refresh_template)
    except KeyboardInterrupt:
        return 130
    except (ValueError, OSError, subprocess.SubprocessError) as exc:
        print(f"test phases: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
