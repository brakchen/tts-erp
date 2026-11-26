"""Keep Vitest outcomes and isolated rerun selections separate from terminal logs.

Uses the installed Vitest 5.0.5 JSON reporter (MIT), without another dependency:
https://vitest.dev/guide/reporters#json-reporter
https://github.com/vitest-dev/vitest/blob/v5.0.5/packages/vitest/src/node/reporters/json.ts
"""

from __future__ import annotations

import json
import math
import re
import shlex
from collections import Counter
from collections.abc import Mapping, Sequence
from pathlib import Path, PurePosixPath
from typing import Any

COMPONENT_NODE = "tests/frontend/test_components.py::test_frontend_components"
_TEST_STATES = {"passed", "failed", "skipped", "pending", "todo", "disabled"}


def component_selection(env: Mapping[str, str]) -> tuple[list[str], str | None]:
    """Reject malformed plans and option injection before starting npm."""
    planned = env.get("TTS_ERP_TEST_VITEST_SELECTION")
    filters = json.loads(planned) if planned is not None else [env.get("FRONTEND_TEST_FILTER", "")]
    if not isinstance(filters, list) or not all(isinstance(item, str) for item in filters):
        raise ValueError("component selection must be a JSON list of relative path filters")
    selections = list(dict.fromkeys(item for item in filters if item))
    for selection in selections:
        path = PurePosixPath(selection)
        if (
            path.is_absolute() or ".." in path.parts or selection.startswith("-")
            or "\\" in selection or ":" in selection or any(ord(char) < 32 for char in selection)
        ):
            raise ValueError("component selection must contain relative path filters, not options")
    # A canonical selection owns the complete assertion scope. Manual rerun
    # filters must not silently narrow a later baseline in the same shell.
    pattern = (env.get("FRONTEND_TEST_NAME_PATTERN") or None) if planned is None else None
    if pattern is not None and "\0" in pattern:
        raise ValueError("component test-name pattern cannot contain NUL")
    return selections, pattern


def expected_component_files(frontend: Path, selections: Sequence[str]) -> list[str]:
    """Conservative inventory for vite.config.ts's src/**/*.test.{ts,tsx}.

    This is a completeness fence, not a substitute collector: a disagreement
    with Vitest is incomplete, never evidence that omitted files passed.
    Vitest's positional filters are case-insensitive path substrings.
    """
    files = sorted(
        path.relative_to(frontend).as_posix()
        for suffix in ("*.test.ts", "*.test.tsx")
        for path in (frontend / "src").rglob(suffix)
        if path.is_file()
    )
    return [
        path for path in files
        if not selections or any(selection.removeprefix("./").lower() in path.lower() for selection in selections)
    ]


def _milliseconds(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
        raise ValueError("invalid Vitest duration")
    return value


def _parse_files(payload: Any, frontend: Path) -> list[dict[str, Any]]:
    if not isinstance(payload, dict) or not isinstance(payload.get("success"), bool):
        raise TypeError("invalid Vitest result envelope")
    results = payload.get("testResults")
    if not isinstance(results, list) or not results:
        raise ValueError("Vitest reported no test files")
    files = []
    for result in results:
        if not isinstance(result, dict) or not isinstance(result.get("name"), str):
            raise TypeError("invalid Vitest file result")
        path = Path(result["name"])
        if not path.is_absolute():
            path = frontend / path
        relative = path.resolve().relative_to(frontend.resolve()).as_posix()
        if not relative.startswith("src/") or not relative.endswith((".test.ts", ".test.tsx")):
            raise ValueError("Vitest result is outside component test sources")
        assertions = result.get("assertionResults")
        if result.get("status") not in {"passed", "failed"} or not isinstance(assertions, list):
            raise ValueError("invalid Vitest file status or assertions")
        message = result.get("message", "")
        if not isinstance(message, str):
            raise TypeError("invalid Vitest file failure message")
        tests = []
        for assertion in assertions:
            if not isinstance(assertion, dict) or assertion.get("status") not in _TEST_STATES:
                raise ValueError("invalid Vitest assertion status")
            ancestors, title = assertion.get("ancestorTitles"), assertion.get("title")
            full_name, messages = assertion.get("fullName"), assertion.get("failureMessages")
            if (
                not isinstance(ancestors, list) or not all(isinstance(name, str) for name in ancestors)
                or not isinstance(title, str) or not isinstance(full_name, str)
                or (messages is not None and (
                    not isinstance(messages, list) or not all(isinstance(item, str) for item in messages)
                ))
            ):
                raise ValueError("invalid Vitest assertion name or failure messages")
            # Vitest 5.0.5 JSON fullName uses spaces, but testNamePattern matches
            # fullTestName with ' > ' separators (runtime/runner/suite.ts).
            tests.append({
                "name": " > ".join([*ancestors, title]), "fullName": full_name,
                "status": assertion["status"], "durationMs": _milliseconds(assertion.get("duration")),
                "failureMessages": messages or [],
            })
        start, end = _milliseconds(result.get("startTime")), _milliseconds(result.get("endTime"))
        if start is None or end is None or end < start:
            raise ValueError("invalid Vitest file timing")
        files.append({
            "path": relative, "status": result["status"], "durationMs": end - start,
            "tests": tests, "failureMessage": message,
        })
    if len({file["path"] for file in files}) != len(files):
        raise ValueError("duplicate Vitest file results")
    counts = Counter(test["status"] for file in files for test in file["tests"])
    expected_counts = {
        "numTotalTests": sum(counts.values()), "numPassedTests": counts["passed"],
        "numFailedTests": counts["failed"], "numTodoTests": counts["todo"],
        "numPendingTests": counts["pending"] + counts["skipped"] + counts["disabled"],
    }
    if any(type(payload.get(key)) is not int or payload[key] != value for key, value in expected_counts.items()):
        raise ValueError("Vitest test counts disagree with assertion results")
    return files


def _exact_name_pattern(names: Sequence[str]) -> str:
    # Escape only JS RegExp syntax; Python re.escape has a different contract.
    escaped = [re.sub(r"[\\^$.*+?()[\]{}|]", lambda match: "\\" + match[0], name) for name in names]
    # Unlike $, the final lookahead does not also match before a final newline.
    return "^(?:" + "|".join(escaped) + r")(?![\s\S])"


def _rerun_command(file: dict[str, Any], all_files: Sequence[str]) -> str | None:
    path = file["path"]
    # Positional Vitest filters are substrings. Do not suggest a command that
    # would also select another file (e.g. foo.test.ts and foo.test.tsx).
    if [candidate for candidate in all_files if path.lower() in candidate.lower()] != [path]:
        return None
    failed_names = list(dict.fromkeys(test["name"] for test in file["tests"] if test["status"] == "failed"))
    # A suite/collection error may exist alongside failed assertions. Preserve
    # its file scope so the rerun cannot hide the lifecycle failure.
    pattern = _exact_name_pattern(failed_names) if failed_names and not file["failureMessage"] else ""
    if any("\0" in name for name in failed_names):
        return None
    try:
        component_selection({"FRONTEND_TEST_FILTER": path, "FRONTEND_TEST_NAME_PATTERN": pattern})
    except ValueError:
        return None
    return shlex.join([
        "env", "-u", "TTS_ERP_TEST_VITEST_SELECTION", "-u", "TTS_ERP_COMPONENT_REPORT",
        f"FRONTEND_TEST_FILTER={path}", f"FRONTEND_TEST_NAME_PATTERN={pattern}",
        "bash", "scripts/test_isolated.sh", "frontend", COMPONENT_NODE,
    ])


def build_component_report(
    raw_path: Path, *, frontend: Path, log: Path, selections: Sequence[str],
    test_name_pattern: str | None, expected_files: Sequence[str], exit_code: int | None,
    duration_ms: float, timed_out: bool = False, execution_error: str | None = None,
) -> dict[str, Any]:
    """Fail closed on absent/partial reports, while retaining every raw artifact."""
    errors = []
    files: list[dict[str, Any]] = []
    payload: dict[str, Any] = {}
    try:
        payload = json.loads(raw_path.read_text(encoding="utf-8"))
        files = _parse_files(payload, frontend)
    except (OSError, ValueError, TypeError) as exc:
        errors.append(f"Vitest report unavailable or invalid: {exc}")
    collected = sorted(file["path"] for file in files)
    if sorted(expected_files) != collected:
        errors.append("Vitest collected files disagree with the component source inventory")
    if not expected_files:
        errors.append("No component test files selected")
    if timed_out:
        errors.append("Component deadline exceeded; final JSON cannot certify run completion")
    if execution_error:
        errors.append(execution_error)
    if exit_code is None or exit_code < 0:
        errors.append("Component process did not finish normally")
    if any(test["status"] == "pending" for file in files for test in file["tests"]):
        errors.append("Vitest reported unfinished assertions")
    if any(file["status"] == "passed" and not file["tests"] for file in files):
        errors.append("Vitest reported an empty passing file; completion is unverified")
    if test_name_pattern and not any(test["status"] in {"passed", "failed"} for file in files for test in file["tests"]):
        errors.append("The component test-name pattern executed no assertions")
    failed_files = [
        file["path"] for file in files
        if file["status"] == "failed" or any(test["status"] == "failed" for test in file["tests"])
    ]
    failed_tests = [
        {"file": file["path"], "name": test["name"]}
        for file in files for test in file["tests"] if test["status"] == "failed"
    ]
    complete = not errors
    passed = complete and exit_code == 0 and payload.get("success") is True and not failed_files
    reruns = []
    if complete and not passed:
        all_files = expected_component_files(frontend, [])
        for file in files:
            if file["path"] in failed_files:
                command = _rerun_command(file, all_files)
                if command is not None:
                    reruns.append(command)
    return {
        "schemaVersion": 1, "status": "passed" if passed else "failed" if complete else "incomplete",
        "complete": complete, "timedOut": timed_out, "exitCode": exit_code, "durationMs": duration_ms,
        "selections": list(selections), "testNamePattern": test_name_pattern,
        "expectedFiles": list(expected_files), "collectedFiles": collected,
        "files": files, "failedFiles": failed_files, "failedTests": failed_tests,
        "rerunCommands": reruns, "errors": errors, "artifacts": {"vitestJson": str(raw_path), "log": str(log)},
    }


def write_component_report(path: Path, report: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def component_summary(report: dict[str, Any], path: Path) -> str:
    counts = Counter(test["status"] for file in report["files"] for test in file["tests"])
    return (
        f"[test-components] {report['status']}; files={len(report['collectedFiles'])}/{len(report['expectedFiles'])} "
        f"tests={sum(counts.values())} passed={counts['passed']} failed={counts['failed']} "
        f"duration={report['durationMs'] / 1000:.2f}s; report={path}; log={report['artifacts']['log']}"
    )
