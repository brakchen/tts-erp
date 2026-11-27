"""Granular component evidence must never turn incomplete execution green."""

from __future__ import annotations

import json
import re
import shlex
import subprocess
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.frontend import test_components as adapter
from tests.support.component_report import (
    COMPONENT_NODE,
    build_component_report,
    component_selection,
    expected_component_files,
)

pytestmark = [pytest.mark.domain_frontend, pytest.mark.layer_unit]


def test_canonical_plan_ignores_ambient_manual_rerun_filters():
    selections, pattern = component_selection({
        "TTS_ERP_TEST_VITEST_SELECTION": '["src/pages/dashboard/"]',
        "FRONTEND_TEST_FILTER": "src/TEST_unrelated.test.ts",
        "FRONTEND_TEST_NAME_PATTERN": "TEST_only_one_assertion",
    })
    assert selections == ["src/pages/dashboard/"] and pattern is None


@pytest.fixture
def frontend(tmp_path: Path) -> Path:
    frontend = tmp_path / "TEST_frontend"
    (frontend / "src").mkdir(parents=True)
    (frontend / "node_modules/vitest").mkdir(parents=True)
    (frontend / "src/TEST_example.test.tsx").write_text("// TEST source\n", encoding="utf-8")
    return frontend


def _payload(frontend: Path, *, failed: bool = False) -> dict:
    status = "failed" if failed else "passed"
    return {
        "success": not failed, "numTotalTests": 1, "numPassedTests": int(not failed),
        "numFailedTests": int(failed), "numPendingTests": 0, "numTodoTests": 0,
        "testResults": [{
            "name": str(frontend / "src/TEST_example.test.tsx"), "status": status,
            "startTime": 1000, "endTime": 1025, "message": "",
            "assertionResults": [{
                "ancestorTitles": ["TEST suite"], "title": "TEST renders", "fullName": "TEST suite TEST renders",
                "status": status, "duration": 25, "failureMessages": ["TEST assertion failed"] if failed else [],
            }],
        }],
    }


def _report(frontend: Path, payload: dict | str | None, **overrides) -> dict:
    raw = frontend.parent / "TEST_results.vitest.json"
    if payload is not None:
        raw.write_text(payload if isinstance(payload, str) else json.dumps(payload), encoding="utf-8")
    return build_component_report(
        raw, frontend=frontend, log=frontend.parent / "TEST_results.log", selections=[],
        test_name_pattern=overrides.pop("test_name_pattern", None),
        expected_files=expected_component_files(frontend, []),
        exit_code=overrides.pop("exit_code", 0), duration_ms=30, **overrides,
    )


def test_passing_report_preserves_file_test_timing_and_never_requests_rerun(frontend):
    report = _report(frontend, _payload(frontend))
    assert report["status"] == "passed" and report["complete"]
    assert report["expectedFiles"] == report["collectedFiles"] == ["src/TEST_example.test.tsx"]
    assert report["files"][0]["durationMs"] == report["files"][0]["tests"][0]["durationMs"] == 25
    assert report["failedFiles"] == report["failedTests"] == report["rerunCommands"] == []


def test_failed_names_use_vitest_five_separator_exact_regex_and_shell_quoting(frontend):
    payload = _payload(frontend, failed=True)
    title = "TEST it's $(echo unsafe) `unsafe` [a].*?\\ value\n中文"
    assertion = payload["testResults"][0]["assertionResults"][0]
    assertion.update(title=title, fullName=f"TEST suite {title}")
    report = _report(frontend, payload, exit_code=1)
    assert report["status"] == "failed" and report["complete"]
    assert report["failedFiles"] == ["src/TEST_example.test.tsx"]
    assert report["failedTests"] == [{"file": "src/TEST_example.test.tsx", "name": f"TEST suite > {title}"}]
    assert report["files"][0]["tests"][0]["failureMessages"] == ["TEST assertion failed"]
    argv = shlex.split(report["rerunCommands"][0])
    assert argv[:5] == ["env", "-u", "TTS_ERP_TEST_VITEST_SELECTION", "-u", "TTS_ERP_COMPONENT_REPORT"]
    assert argv[-4:] == ["bash", "scripts/test_isolated.sh", "frontend", COMPONENT_NODE]
    env = dict(item.split("=", 1) for item in argv[5:-4])
    selections, pattern = component_selection(env)
    assert selections == ["src/TEST_example.test.tsx"]
    assert re.match(pattern, f"TEST suite > {title}")
    assert not re.match(pattern, f"TEST suite > {title}\n")
    assert not re.match(pattern, f"TEST suite > {title} extra")
    assert not re.match(pattern, f"OTHER suite > {title}")


def test_collection_failure_reruns_the_file_without_hiding_it_with_name_pattern(frontend):
    payload = _payload(frontend, failed=True)
    payload["testResults"][0].update(assertionResults=[], message="TEST import error")
    payload.update(numTotalTests=0, numFailedTests=0)
    report = _report(frontend, payload, exit_code=1)
    assert report["status"] == "failed" and report["failedTests"] == []
    assert "FRONTEND_TEST_NAME_PATTERN=" in shlex.split(report["rerunCommands"][0])


def test_quote_sensitive_file_and_multiple_failed_names_preserve_one_file_selection(frontend):
    original = frontend / "src/TEST_example.test.tsx"
    renamed = frontend / "src/TEST it's $(echo unsafe).test.tsx"
    original.rename(renamed)
    payload = _payload(frontend, failed=True)
    result = payload["testResults"][0]
    result["name"] = str(renamed)
    result["assertionResults"].append({
        **result["assertionResults"][0], "title": "TEST second (case)", "fullName": "TEST suite TEST second (case)",
    })
    payload.update(numTotalTests=2, numFailedTests=2)
    report = _report(frontend, payload, exit_code=1)
    argv = shlex.split(report["rerunCommands"][0])
    env = dict(item.split("=", 1) for item in argv[5:-4])
    selections, pattern = component_selection(env)
    assert selections == [renamed.relative_to(frontend).as_posix()]
    assert re.match(pattern, "TEST suite > TEST renders")
    assert re.match(pattern, "TEST suite > TEST second (case)")
    assert not re.match(pattern, "TEST suite > TEST second case")


@pytest.mark.parametrize("raw", [None, "{broken", "[]", "{}"])
def test_missing_or_malformed_report_cannot_pass_even_with_zero_exit(frontend, raw):
    report = _report(frontend, raw)
    assert report["status"] == "incomplete" and not report["complete"]
    assert report["files"] == report["rerunCommands"] == []
    assert report["errors"]


def test_timeout_does_not_certify_a_final_report_or_suggest_whole_suite_rerun(frontend):
    report = _report(frontend, _payload(frontend, failed=True), exit_code=None, timed_out=True)
    assert report["status"] == "incomplete" and report["timedOut"]
    assert report["rerunCommands"] == []


def test_missing_inventory_file_and_pending_tests_remain_incomplete(frontend):
    (frontend / "src/TEST_missing.test.ts").write_text("// TEST source\n", encoding="utf-8")
    payload = _payload(frontend)
    payload["testResults"][0]["assertionResults"][0]["status"] = "pending"
    payload.update(numPassedTests=0, numPendingTests=1)
    report = _report(frontend, payload)
    assert report["status"] == "incomplete"
    assert any("inventory" in error for error in report["errors"])
    assert any("unfinished" in error for error in report["errors"])


def test_name_pattern_matching_no_assertions_fails_closed(frontend):
    payload = _payload(frontend)
    payload["testResults"][0]["assertionResults"][0]["status"] = "skipped"
    payload.update(numPassedTests=0, numPendingTests=1)
    report = _report(frontend, payload, test_name_pattern="TEST missing")
    assert report["status"] == "incomplete"
    assert any("executed no assertions" in error for error in report["errors"])


def test_inconsistent_counts_and_external_file_cannot_pass(frontend):
    payload = _payload(frontend)
    payload["numPassedTests"] = 0
    assert _report(frontend, payload)["status"] == "incomplete"
    payload = _payload(frontend)
    payload["testResults"][0]["name"] = str(frontend.parent / "TEST_external.test.ts")
    assert _report(frontend, payload)["status"] == "incomplete"


def test_process_failure_is_not_overridden_by_passing_assertions(frontend):
    report = _report(frontend, _payload(frontend), exit_code=1)
    assert report["status"] == "failed"
    assert report["rerunCommands"] == []


def test_ambiguous_file_substring_does_not_produce_an_overbroad_rerun(frontend):
    payload = _payload(frontend, failed=True)
    (frontend / "src/nested/src").mkdir(parents=True)
    (frontend / "src/nested/src/TEST_example.test.tsx").write_text("// TEST source\n", encoding="utf-8")
    second = _payload(frontend)["testResults"][0]
    second["name"] = str(frontend / "src/nested/src/TEST_example.test.tsx")
    payload["testResults"].append(second)
    payload.update(numTotalTests=2, numPassedTests=1)
    report = _report(frontend, payload, exit_code=1)
    assert report["status"] == "failed"
    assert report["rerunCommands"] == []


@pytest.mark.parametrize("selection", ['"src/TEST.ts"', '[1]', '["--passWithNoTests"]', '["../src/"]', '["/tmp/src/"]'])
def test_invalid_selection_is_rejected_before_process_launch(selection):
    with pytest.raises(ValueError):
        component_selection({"TTS_ERP_TEST_VITEST_SELECTION": selection})


def test_explicit_plan_precedes_manual_filter_and_inventory_matches_case_insensitively(frontend):
    selections, pattern = component_selection({
        "TTS_ERP_TEST_VITEST_SELECTION": '["src/TEST_example.test.tsx"]',
        "FRONTEND_TEST_FILTER": "TEST_wrong", "FRONTEND_TEST_NAME_PATTERN": "TEST pattern",
    })
    assert selections == ["src/TEST_example.test.tsx"] and pattern is None
    assert expected_component_files(frontend, ["test_EXAMPLE"]) == selections


@pytest.mark.parametrize("mode,filtered,deadline", [
    ("pass", False, 360), ("pass", True, 180), ("timeout", True, 180), ("missing", False, 360),
])
def test_adapter_keeps_owned_cleanup_deadlines_and_persistent_concise_artifacts(
    frontend, monkeypatch, capsys, mode, filtered, deadline,
):
    root = frontend.parent
    monkeypatch.setattr(adapter, "ROOT", root)
    frontend.rename(root / "frontend")
    frontend = root / "frontend"
    report_path = root / "TEST_persistent/results.json"
    report_path.parent.mkdir()
    report_path.with_suffix(".vitest.json").write_text(json.dumps(_payload(frontend)), encoding="utf-8")
    monkeypatch.setenv("TTS_ERP_COMPONENT_REPORT", str(report_path))
    monkeypatch.delenv("TTS_ERP_TEST_VITEST_SELECTION", raising=False)
    monkeypatch.delenv("FRONTEND_TEST_NAME_PATTERN", raising=False)
    monkeypatch.setenv("FRONTEND_TEST_FILTER", "src/TEST_example.test.tsx" if filtered else "")
    observed = []

    @contextmanager
    def fake_owned_process(args, **kwargs):
        assert json.loads(report_path.read_text(encoding="utf-8"))["status"] == "incomplete"
        assert "--reporter=default" in args and "--reporter=json" in args
        assert kwargs["env"]["CI"] == "1" and kwargs["env"]["NODE_ENV"] == "test"
        assert kwargs["cwd"] == frontend and kwargs["stderr"] == subprocess.STDOUT

        def wait(timeout):
            observed.append(timeout)
            kwargs["stdout"].write("TEST full log retained\n" * 100)
            if mode == "timeout":
                raise subprocess.TimeoutExpired(args, timeout)
            if mode != "missing":
                output_path = next(item.split("=", 1)[1] for item in args if item.startswith("--outputFile.json="))
                Path(output_path).write_text(json.dumps(_payload(frontend)), encoding="utf-8")
            return 0

        try:
            yield SimpleNamespace(wait=wait)
        finally:
            observed.append("cleaned")

    monkeypatch.setattr(adapter, "owned_process", fake_owned_process)
    if mode == "timeout":
        with pytest.raises(subprocess.TimeoutExpired):
            adapter.test_frontend_components()
    elif mode == "missing":
        with pytest.raises(AssertionError, match="incomplete"):
            adapter.test_frontend_components()
    else:
        adapter.test_frontend_components()
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["status"] == ("passed" if mode == "pass" else "incomplete")
    assert observed == [deadline, "cleaned"]
    assert Path(report["artifacts"]["log"]).read_text(encoding="utf-8").count("TEST full log retained") == 100
    output = capsys.readouterr().out
    assert "[test-components]" in output and "TEST full log retained" not in output
