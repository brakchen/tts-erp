"""Run frontend component tests only inside the canonical isolated pytest entry."""

from __future__ import annotations

import os
import subprocess
import tempfile
import time
from pathlib import Path

import pytest

from tests.support.component_report import (
    build_component_report,
    component_selection,
    component_summary,
    expected_component_files,
    write_component_report,
)
from tests.support.owned_process import owned_process

pytestmark = [pytest.mark.domain_frontend, pytest.mark.layer_unit]
ROOT = Path(__file__).resolve().parents[2]


def test_frontend_components() -> None:
    frontend = ROOT / "frontend"
    destination = os.environ.get("TTS_ERP_COMPONENT_REPORT")
    if destination:
        report_path = Path(destination).resolve()
    else:
        # Pytest rotates its tmp_path after a few runs, exactly when repeated
        # narrow reruns need earlier failure evidence. Keep manual runs too.
        storage = Path(subprocess.run(
            ["git", "rev-parse", "--path-format=absolute", "--git-path", "test-runs"],
            cwd=ROOT, check=True, capture_output=True, text=True,
        ).stdout.strip())
        storage.mkdir(parents=True, exist_ok=True)
        report_path = Path(tempfile.mkdtemp(prefix="components.", dir=storage)) / "components.json"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    raw_path = report_path.with_suffix(".vitest.json")
    log = report_path.with_suffix(".log")
    # A reused destination must never certify this run from an earlier JSON.
    raw_path.unlink(missing_ok=True)
    log.write_text("", encoding="utf-8")
    selections, pattern, expected = [], None, []
    exit_code, timed_out, execution_error = None, False, None
    started = time.monotonic()
    try:
        selections, pattern = component_selection(os.environ)
        expected = expected_component_files(frontend, selections)
        write_component_report(report_path, build_component_report(
            raw_path, frontend=frontend, log=log, selections=selections, test_name_pattern=pattern,
            expected_files=expected, exit_code=None, duration_ms=0,
            execution_error="Component execution has not finished",
        ))
        assert (frontend / "node_modules" / "vitest").is_dir(), (
            "Frontend dependencies missing: run npm ci --prefix frontend before isolated tests"
        )
        args = [
            "npm", "run", "test:unit", "--", "--reporter=default", "--reporter=json",
            f"--outputFile.json={raw_path}", *selections,
        ]
        if pattern:
            args.append(f"--testNamePattern={pattern}")
        # The full suite can exceed 180s with two jsdom workers on the shared test host.
        timeout = 180 if selections or pattern else 360
        with log.open("w", encoding="utf-8") as output_file, owned_process(
            args,
            cwd=frontend,
            stdout=output_file,
            stderr=subprocess.STDOUT,
            env={**os.environ, "CI": "1", "NODE_ENV": "test"},
        ) as process:
            exit_code = process.wait(timeout=timeout)
    except BaseException as exc:
        timed_out = isinstance(exc, subprocess.TimeoutExpired)
        execution_error = f"Component execution interrupted: {type(exc).__name__}"
        exc.add_note(f"Component report: {report_path}; full component output: {log}")
        raise
    finally:
        report = build_component_report(
            raw_path, frontend=frontend, log=log, selections=selections, test_name_pattern=pattern,
            expected_files=expected, exit_code=exit_code, duration_ms=(time.monotonic() - started) * 1000,
            timed_out=timed_out, execution_error=execution_error,
        )
        write_component_report(report_path, report)
        print(component_summary(report, report_path))
    details = "\n".join([*report["errors"], *report["rerunCommands"]])
    assert report["status"] == "passed", f"{component_summary(report, report_path)}\n{details}"
