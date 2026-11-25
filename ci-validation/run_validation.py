"""Run canonical isolated commands and retain exact-commit evidence."""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

BASE_CASES = (
    "tests/api/test_manual_costs_page_v2.py::test_console_js_forwards_the_catalogue_filters",
    "tests/api/test_optional_product_images.py::test_catalogue_dto_keeps_cost_facts_without_optional_storage",
    "tests/api/test_spu_deterioration_alert_api.py::test_negative_canonical_roi_is_not_hidden",
    "tests/api/test_spu_roi_api.py::test_spu_roi_math_single_spu_default_k1",
    "tests/api/test_spu_roi_api.py::test_spu_roi_page_uses_bootstrap_responsive_layout",
    "tests/api/test_spu_roi_api.py::test_spu_roi_drill_summary_matches_actual_dashboard_metrics",
    "tests/api/test_spu_roi_api.py::test_spu_roi_pnl_cogs_rows_show_qty_times_unit_cost",
    "tests/api/test_spu_roi_api.py::test_spu_roi_page_no_old_columns",
    "tests/jobs_tiktok/test_spu_image_mirror_job.py::test_stream_upload_closes_downloaded_file",
    "tests/publishing/test_final_v18_findings.py::test_generated_schema_and_architecture_cover_publishing_head",
    "tests/sync_worker/test_main_module.py::test_main_module_runs_main_when_invoked_as_script",
    "tests/sync_worker/test_spu_deterioration_alert_job.py::test_real_canonical_queries_batch_more_than_500_scope",
)
ENVIRONMENT_CASES = (
    "tests/db/test_time_fields_convention.py::test_critical_columns_have_comments",
    "tests/scripts/test_browser_runtime.py::test_canned_layout_waits_for_data_and_refuses_an_error_shell",
    "tests/scripts/test_browser_runtime.py::test_real_browser_contexts_and_mixed_async_modules",
    "tests/scripts/test_test_runtime.py::test_build_failure_stops_before_test_execution",
    "tests/scripts/test_test_runtime.py::test_collection_only_does_not_build",
    "tests/scripts/test_test_runtime.py::test_deselected_asset_tests_do_not_build",
    "tests/scripts/test_test_runtime.py::test_failed_or_empty_collection_does_not_build[-5]",
    "tests/scripts/test_test_runtime.py::test_failed_or_empty_collection_does_not_build[def test_broken(:-2]",
    "tests/scripts/test_test_runtime.py::test_marker_and_fixture_build_current_assets_once",
)
CASES = (*BASE_CASES, *ENVIRONMENT_CASES)


def git(*arguments: str) -> str:
    return subprocess.check_output(["git", *arguments], text=True).strip()


def logged(command: list[str], path: Path) -> int:
    with path.open("w") as output:
        return subprocess.run(command, stdout=output, stderr=subprocess.STDOUT).returncode


def check_junit(path: Path, *, cases: int, failures: int) -> None:
    items = ET.parse(path).getroot().findall(".//testcase")
    assert len(items) == cases, "all explicit cases must execute"
    assert sum(item.find("failure") is not None for item in items) == failures
    assert not any(item.find("error") is not None or item.find("skipped") is not None for item in items)


def failure_ids(output: str, raw_output: str, log_notice: str) -> tuple[list[str], list[str]]:
    """Read reported IDs; retain spaces in parametrized canonical test IDs."""
    canonical: list[str] = []
    lines = output.rstrip().splitlines()
    if lines and lines[-1] == log_notice:
        for index in range(len(lines) - 2, -1, -1):
            if lines[index] == "[test-baseline] new failures:":
                candidates = lines[index + 1:-1]
                if candidates and all(line.startswith("tests/") and "::" in line for line in candidates):
                    canonical = sorted(set(candidates))
                break
    plain = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", output + "\n" + raw_output)
    pytest_ids = sorted(set(re.findall(r"^(?:FAILED|ERROR)\s+(tests/.+?)(?: - [^\n]*)?$", plain, re.M)))
    return canonical, pytest_ids


def main(results: Path, evidence: dict) -> int:
    assert len(BASE_CASES) == len(set(BASE_CASES)), "base selectors must be unique"
    assert len(CASES) == len(set(CASES)), "candidate selectors must be unique"
    target, base = os.environ["TARGET_SHA"], os.environ["BASE_SHA"]
    assert all(re.fullmatch(r"[0-9a-f]{40}", value) for value in (target, base))
    assert git("rev-parse", "HEAD") == target, "checkout must be the exact candidate"
    assert git("rev-parse", "HEAD^1") == base, "first parent must be the synchronized master"
    assert git("status", "--porcelain", "--untracked-files=all") == ""
    evidence.update(target_sha=target, base_sha=base, first_parent=base)
    baseline_text = git("show", f"{base}:docs/testing/baseline-fast.txt")
    known_baseline_ids = [line for line in baseline_text.splitlines() if line.startswith("tests/")]
    evidence["committed_baseline_known_count"] = len(known_baseline_ids)
    assert not known_baseline_ids, "success summary requires a zero-failure committed baseline"

    subprocess.run(["git", "switch", "--detach", base], check=True)
    assert git("rev-parse", "HEAD") == base
    base_xml = results / "base-known.junit.xml"
    rc = logged(["bash", "scripts/test_isolated.sh", "--refresh-template", "fast", *BASE_CASES,
                 "--tb=short", f"--junitxml={base_xml}"], results / "base-known.log")
    evidence["base_known_exit"] = rc
    assert rc == 1, "base must reproduce the documented stale-test failures"
    check_junit(base_xml, cases=len(BASE_CASES), failures=len(BASE_CASES))
    expected_ids = set(BASE_CASES)
    failed_ids = set(re.findall(r"^FAILED\s+(\S+)", (results / "base-known.log").read_text(), re.M))
    assert failed_ids == expected_ids, "base failed IDs must exactly match the documented set"
    evidence.update(base_known_count=len(BASE_CASES), base_known_failures=sorted(failed_ids))
    print(f"Base reproduced all {len(BASE_CASES)} expected failures", flush=True)

    subprocess.run(["git", "switch", "--detach", target], check=True)
    assert git("rev-parse", "HEAD") == target
    assert git("status", "--porcelain", "--untracked-files=all") == ""
    candidate_xml = results / "candidate-repaired.junit.xml"
    rc = logged(["bash", "scripts/test_isolated.sh", "fast", *CASES, "--tb=short",
                 f"--junitxml={candidate_xml}"], results / "candidate-repaired.log")
    evidence["candidate_repaired_exit"] = rc
    assert rc == 0, "the targeted candidate regressions must all pass"
    check_junit(candidate_xml, cases=len(CASES), failures=0)
    evidence["candidate_repaired_count"] = len(CASES)
    print(f"Candidate passed all {len(CASES)} repaired regression cases", flush=True)
    rc = logged(["bash", "scripts/test_baseline.sh", "--scope", "auto", "--base", base, "--plan"],
                results / "selection-plan.log")
    assert rc == 0, "selection plan must be valid"
    print("Starting strict auto gate on the exact prospective merge", flush=True)
    rc = logged(["bash", "scripts/test_baseline.sh", "--refresh-template", "--scope", "auto", "--base", base,
                 "--keep-log", str(results / "pytest.log")], results / "baseline-auto.log")
    evidence["baseline_exit"] = rc
    assert git("rev-parse", "HEAD") == target and git("status", "--porcelain", "--untracked-files=all") == ""
    output = (results / "baseline-auto.log").read_text(encoding="utf-8")
    expected_log_notice = f"[test-baseline] raw pytest output: {results / 'pytest.log'}"
    if rc != 0:
        raw_log = results / "pytest.log"
        raw_output = raw_log.read_text(encoding="utf-8") if raw_log.is_file() else ""
        canonical_ids, pytest_ids = failure_ids(output, raw_output, expected_log_notice)
        evidence.update(
            selected_cases=None, reported_failures=canonical_ids or pytest_ids,
            canonical_new_failures=canonical_ids, pytest_summary_failures=pytest_ids,
            missing=None, summary_source="failed canonical driver; count and completeness unknown",
        )
        return rc
    # The checked driver validates its real report before printing this final
    # success marker. With an empty committed baseline, it means no failures,
    # no missing required cases, and a nonempty execution; no plugin is needed.
    lines = output.rstrip().splitlines()
    assert lines and lines[-1] == expected_log_notice, "canonical driver trailer is missing"
    marker = re.fullmatch(
        r"\[test-baseline\] (\d+) selected cases executed; no new failures", lines[-2]
    ) if len(lines) >= 2 else None
    assert marker is not None, "canonical driver success marker is missing"
    selected_cases = int(marker.group(1))
    assert selected_cases > 1000, "full auto gate must execute its broad selection"
    evidence.update(
        selected_cases=selected_cases, selected_failures=[], missing=[],
        summary_source="successful canonical driver final marker and zero known baseline failures",
    )
    evidence["ok"] = True
    return 0


if __name__ == "__main__":
    results = Path(os.environ["RUNNER_TEMP"]) / "ad-metric-validation-results"
    results.mkdir(exist_ok=True)
    evidence = {"ok": False, "run_id": os.environ.get("GITHUB_RUN_ID"), "workflow_commit": os.environ.get("GITHUB_SHA")}
    status = 1
    try:
        status = main(results, evidence)
    except Exception as error:
        evidence["error_type"] = type(error).__name__
        if isinstance(error, AssertionError):
            evidence["error"] = str(error)  # These are fixed local assertion messages.
        print("CI validation did not pass; inspect the bounded result artifact", file=sys.stderr)
    finally:
        evidence["exit_status"] = status
        (results / "result.json").write_text(json.dumps(evidence, indent=2))
    raise SystemExit(status)
