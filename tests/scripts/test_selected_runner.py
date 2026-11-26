"""Exercise selected phase collection through the installed pytest collector."""

from __future__ import annotations

import importlib
import json
import os
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from scripts._test_selection import COMPONENTS, Selection


def _collector_project(tmp_path: Path, monkeypatch, plan: Selection, sources: dict[str, str]):
    project = Path(__file__).resolve().parents[2]
    monkeypatch.syspath_prepend(str(project / "scripts"))
    runner = importlib.import_module("_run_selected_tests")
    monkeypatch.setattr(runner, "ROOT", tmp_path)
    monkeypatch.setattr(runner, "git", lambda *args: b"")
    monkeypatch.setattr(runner, "resolve_commit", lambda rev: "head" if rev == "HEAD" else rev)
    monkeypatch.setattr(runner, "select_commits", lambda *args: plan)
    monkeypatch.setattr(runner, "SCENARIOS", {"requested": "tests/api/test_backend.py::test_requested"})
    monkeypatch.setenv("PYTHONPATH", os.pathsep.join((str(project), os.environ.get("PYTHONPATH", ""))))
    for name, source in sources.items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source, encoding="utf-8")
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts/build_frontend.sh").write_text(
        "set -eu\nmkdir -p tts_erp_v2/static/frontend/TEST_collector\n"
        "echo TEST_asset > tts_erp_v2/static/frontend/TEST_collector/main.js\n"
        "printf '%s' '{\"releaseId\":\"TEST_collector\"}' > tts_erp_v2/static/frontend/release.json\n",
        encoding="utf-8",
    )
    return runner


def _sources() -> dict[str, str]:
    return {
        "tests/api/test_backend.py": (
            "import pytest\n"
            "def test_backend(): pass\n"
            "@pytest.mark.requires_frontend_build\ndef test_requested(): pass\n"
            "@pytest.mark.requires_frontend_build\ndef test_unrequested(): pass\n"
        ),
        COMPONENTS: "def test_components(): pass\n",
        "tests/frontend/test_contract.py": "def test_contract(): pass\n",
        "tests/browser/test_mock.py": "def test_mock(): pass\n",
        "tests/e2e/test_safe.py": (
            "import pytest\n"
            "def test_safe_e2e(): pass\n"
            "@pytest.mark.requires_service\ndef test_external_service(): raise AssertionError('excluded')\n"
        ),
        "tests/support/test_support_contract.py": "def test_support_contract(): pass\n",
        "tests/unit/test_excluded.py": (
            "import pytest\n@pytest.mark.slow\ndef test_slow(): raise AssertionError('excluded')\n"
            "@pytest.mark.domain_migration\ndef test_migration(): raise AssertionError('excluded')\n"
        ),
    }


@pytest.mark.parametrize("frontend,filters", [(True, []), (False, []), (True, ["src/pages/dashboard/"])])
def test_mixed_collection_preserves_requested_groups_once(tmp_path, monkeypatch, frontend, filters):
    plan = Selection(frontend=frontend, vitest=filters, backend_all=True, scenarios=["requested"],
                     backend=["tests/api/test_backend.py"], browser_mock=["tests/browser/test_mock.py"])
    runner = _collector_project(tmp_path, monkeypatch, plan, _sources())
    real_run = subprocess.run
    seen: list[str] = []
    for phase in runner.phase_plan(plan, "auto"):
        report = tmp_path / f"{phase.name}.json"
        junit = tmp_path / f"{phase.name}.xml"

        def execute(args, _junit=junit, **kwargs):
            if args[0] == "npm":
                return subprocess.CompletedProcess(args, 0)
            assert "-rP" not in args
            return real_run([*args, f"--junitxml={_junit}"], **kwargs)

        monkeypatch.setattr(runner.subprocess, "run", execute)
        assert runner.run("base", "head", str(report), [], "auto", phase.name) == 0
        result = json.loads(report.read_text(encoding="utf-8"))
        assert result["missing"] == result["failed"] == []
        assert result["phase"] == phase.name
        assert result["required"] == phase.required
        seen.extend(case.attrib["name"] for case in ET.parse(junit).findall(".//testcase"))
        progress = json.loads(report.with_suffix(".progress.json").read_text())
        assert progress["phase"] == phase.name
        assert progress["collected"] == progress["completed"] == result["ran"]
        assert progress["stage"] == "complete"
        assert progress["timings"]["pytest_total"] >= 0
    assert len(seen) == len(set(seen))
    assert seen.count("test_backend") == seen.count("test_requested") == seen.count("test_mock") == 1
    assert seen.count("test_components") == int(frontend)
    assert seen.count("test_contract") == int(frontend and not filters)
    assert "test_unrequested" not in seen
    assert "test_safe_e2e" not in seen
    assert "test_support_contract" not in seen  # Preserve auto backend_all's existing boundary.


def test_fast_phase_union_matches_safe_full_collection(tmp_path, monkeypatch):
    plan = Selection(frontend=True, backend_all=True, build=True)
    runner = _collector_project(tmp_path, monkeypatch, plan, _sources())
    real_run = subprocess.run
    seen: list[str] = []
    for phase in runner.phase_plan(plan, "fast"):
        report = tmp_path / f"{phase.name}.json"
        assert runner.run("base", "head", str(report), [], "fast", phase.name) == 0
        result = json.loads(report.read_text())
        assert result["missing"] == result["failed"] == []
        seen.extend(result["collectedNodes"])
    assert len(seen) == len(set(seen))
    env = {**os.environ, "PYTEST_ADDOPTS": ""}
    junit = tmp_path / "full.xml"
    result = real_run(
        [runner.sys.executable, "-m", "pytest", "-q", "-m", runner.SAFE_MARKS, "tests", f"--junitxml={junit}"],
        cwd=tmp_path, env=env, capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    names = {node.split("::")[-1] for node in seen}
    assert names == {case.attrib["name"] for case in ET.parse(junit).findall(".//testcase")}
    assert {"test_safe_e2e", "test_unrequested", "test_support_contract"} <= names
    assert not {"test_slow", "test_external_service", "test_migration"} & names


def test_explicit_service_target_stays_missing_after_safe_mark_selection(tmp_path, monkeypatch):
    plan = Selection(browser_mock=["tests/e2e/test_safe.py::test_external_service"])
    runner = _collector_project(tmp_path, monkeypatch, plan, _sources())
    report = tmp_path / "browser.json"
    assert runner.run("base", "head", str(report), [], "auto", "browser") == 5
    value = json.loads(report.read_text())
    assert value["ran"] == 0
    assert value["missing"] == plan.browser_mock


def test_unknown_or_empty_phase_fails_before_any_framework_launch(tmp_path, monkeypatch):
    runner = _collector_project(tmp_path, monkeypatch, Selection(), _sources())
    monkeypatch.setattr(runner.subprocess, "run", lambda *_args, **_kwargs: pytest.fail("must not launch pytest"))
    for phase in ("backend", "not-a-phase"):
        with pytest.raises(ValueError, match="not in the committed selection"):
            runner.run("base", "head", str(tmp_path / "result.json"), [], "auto", phase)


def test_canonical_selection_ignores_ambient_component_filters(tmp_path, monkeypatch):
    runner = _collector_project(tmp_path, monkeypatch, Selection(frontend=True), _sources())
    monkeypatch.setenv("FRONTEND_TEST_FILTER", "src/TEST_unrelated.test.tsx")
    monkeypatch.setenv("FRONTEND_TEST_NAME_PATTERN", "TEST_accidental_narrowing")
    captured = []

    def execute(args, **kwargs):
        captured.append(kwargs["env"])
        return subprocess.CompletedProcess(args, 0)

    monkeypatch.setattr(runner.subprocess, "run", execute)
    assert runner.run("base", "head", str(tmp_path / "report.json"), [], "frontend", "frontend") == 0
    assert captured
    assert all("FRONTEND_TEST_FILTER" not in env and "FRONTEND_TEST_NAME_PATTERN" not in env for env in captured)
    assert all(json.loads(env["TTS_ERP_TEST_VITEST_SELECTION"]) == [] for env in captured)


def test_phase_targets_deduplicate_parent_roots_and_explicit_children(tmp_path, monkeypatch):
    plan = Selection(backend=["tests/sync_worker", "tests/sync_worker/test_worker.py",
                              "tests/sync_worker/test_worker.py::test_example"])
    runner = _collector_project(tmp_path, monkeypatch, plan, _sources())
    [phase] = runner.phase_plan(plan, "auto")
    assert phase.targets == ["tests/sync_worker"]
    assert phase.required == plan.backend
