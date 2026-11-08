"""Exercise selected-run collection through the installed pytest collector."""

from __future__ import annotations

import importlib
import json
import os
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from scripts._test_selection import COMPONENTS, Selection


@pytest.mark.parametrize("frontend,filters", [
    (True, []),
    (False, []),
    (True, ["src/pages/dashboard/"]),
])
def test_mixed_collection_preserves_requested_groups_once(
    tmp_path: Path, monkeypatch, frontend: bool, filters: list[str],
) -> None:
    project = Path(__file__).resolve().parents[2]
    monkeypatch.syspath_prepend(str(project / "scripts"))
    runner = importlib.import_module("_run_selected_tests")
    monkeypatch.setattr(runner, "ROOT", tmp_path)
    monkeypatch.setattr(runner, "git", lambda *args: b"")
    monkeypatch.setattr(runner, "resolve_commit", lambda rev: "head" if rev == "HEAD" else rev)
    plan = Selection(frontend=frontend, vitest=filters, backend_all=True, scenarios=["requested"])
    monkeypatch.setattr(runner, "select_commits", lambda *args: plan)
    monkeypatch.setattr(runner, "SCENARIOS", {"requested": "tests/browser/test_live.py::test_requested"})
    monkeypatch.setenv("PYTHONPATH", os.pathsep.join((str(project), os.environ.get("PYTHONPATH", ""))))
    sources = {
        "tests/unit/test_backend.py": "def test_backend(): pass\n",
        COMPONENTS: "def test_components(): pass\n",
        "tests/browser/test_live.py": (
            "import pytest\n"
            "@pytest.mark.requires_frontend_build\ndef test_requested(): pass\n"
            "@pytest.mark.requires_frontend_build\ndef test_unrequested(): pass\n"
        ),
    }
    for name, source in sources.items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source, encoding="utf-8")
    report, junit = tmp_path / "report.json", tmp_path / "junit.xml"
    # The retained marker requests actual build evidence even in this synthetic
    # collector project. Its tiny builder must succeed before tests can execute.
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts/build_frontend.sh").write_text(
        "set -eu\nmkdir -p tts_erp_v2/static/frontend/TEST_collector\n"
        "echo TEST_asset > tts_erp_v2/static/frontend/TEST_collector/main.js\n"
        "printf '%s' '{\"releaseId\":\"TEST_collector\"}' > tts_erp_v2/static/frontend/release.json\n",
        encoding="utf-8",
    )
    real_run = subprocess.run

    def execute(args, **kwargs):
        if args[0] == "npm":
            return subprocess.CompletedProcess(args, 0)
        return real_run([*args, f"--junitxml={junit}"], **kwargs)

    monkeypatch.setattr(runner.subprocess, "run", execute)
    assert runner.run("base", "head", str(report), [], "auto") == 0
    result = json.loads(report.read_text(encoding="utf-8"))
    assert result["missing"] == result["failed"] == []
    cases = ET.parse(junit).findall(".//testcase")
    names = [(case.attrib["classname"], case.attrib["name"]) for case in cases]
    assert len(names) == len(set(names))
    assert sum(name == "test_backend" for _, name in names) == 1
    assert sum(name == "test_components" for _, name in names) == int(frontend)
    assert sum(name == "test_requested" for _, name in names) == 1
    assert not any(name == "test_unrequested" for _, name in names)
