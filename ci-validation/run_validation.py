"""Run canonical isolated commands and retain exact-commit evidence."""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

CASES = (
    "tests/api/test_manual_costs_page_v2.py::test_console_js_forwards_the_catalogue_filters",
    "tests/api/test_optional_product_images.py::test_catalogue_dto_keeps_cost_facts_without_optional_storage",
    "tests/api/test_spu_deterioration_alert_api.py::test_negative_canonical_roi_is_not_hidden",
    "tests/api/test_spu_roi_api.py::test_spu_roi_math_single_spu_default_k1",
)
BASE_CASES = (*CASES,
    "tests/api/test_spu_roi_api.py::test_spu_roi_page_uses_bootstrap_responsive_layout",
    "tests/api/test_spu_roi_api.py::test_spu_roi_drill_summary_matches_actual_dashboard_metrics",
    "tests/api/test_spu_roi_api.py::test_spu_roi_pnl_cogs_rows_show_qty_times_unit_cost",
    "tests/api/test_spu_roi_api.py::test_spu_roi_page_no_old_columns",
    "tests/jobs_tiktok/test_spu_image_mirror_job.py::test_stream_upload_closes_downloaded_file",
    "tests/publishing/test_final_v18_findings.py::test_generated_schema_and_architecture_cover_publishing_head",
)


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


def main(results: Path, evidence: dict) -> int:
    target, base = os.environ["TARGET_SHA"], os.environ["BASE_SHA"]
    assert all(re.fullmatch(r"[0-9a-f]{40}", value) for value in (target, base))
    assert git("rev-parse", "HEAD") == target, "checkout must be the exact candidate"
    assert git("rev-parse", "HEAD^1") == base, "first parent must be the synchronized master"
    assert git("status", "--porcelain", "--untracked-files=all") == ""
    evidence.update(target_sha=target, base_sha=base, first_parent=base)

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
    candidate_xml = results / "candidate-four.junit.xml"
    rc = logged(["bash", "scripts/test_isolated.sh", "fast", *CASES, "--tb=short",
                 f"--junitxml={candidate_xml}"], results / "candidate-four.log")
    evidence["candidate_four_exit"] = rc
    assert rc == 0, "the four candidate regressions must all pass"
    check_junit(candidate_xml, cases=len(CASES), failures=0)
    print("Candidate passed all four repaired regression cases", flush=True)
    rc = logged(["bash", "scripts/test_baseline.sh", "--scope", "auto", "--base", base, "--plan"],
                results / "selection-plan.log")
    assert rc == 0, "selection plan must be valid"
    print("Starting strict auto gate on the exact prospective merge", flush=True)
    # Observe the canonical selector's own report without changing collection,
    # assertions, exit codes, fixtures or its temporary DB lifecycle.
    os.environ["PYTEST_PLUGINS"] = "ci_result_report"
    os.environ["CI_RESULT_DIR"] = str(results)
    os.environ["PYTHONPATH"] = os.pathsep.join(filter(None, (os.environ.get("PYTHONPATH"), str(Path(__file__).resolve().parent))))
    rc = logged(["bash", "scripts/test_baseline.sh", "--scope", "auto", "--base", base,
                 "--keep-log", str(results / "pytest.log")], results / "baseline-auto.log")
    evidence["baseline_exit"] = rc
    report = json.loads((results / "selected-report.json").read_text())
    evidence.update(selected_cases=report["ran"], selected_failures=report["failed"], missing=report["missing"])
    assert rc == 0 and report["status"] == 0 and report["ran"] > len(CASES) and not report["failed"] and not report["missing"], "full auto gate must execute and pass"
    assert git("rev-parse", "HEAD") == target and git("status", "--porcelain", "--untracked-files=all") == ""
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
