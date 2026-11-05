"""Exercise selected-run collection through the installed pytest collector."""

from __future__ import annotations

import importlib
import json
import os
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from scripts._test_selection import COMPONENTS, LEGACY_FRONTEND, Selection


@pytest.mark.parametrize("frontend,filters,legacy_count", [
    (True, [], 12),
    (False, [], 0),
    (True, ["src/pages/dashboard/"], 0),
])
def test_mixed_collection_preserves_requested_groups_once(
    tmp_path: Path, monkeypatch, frontend: bool, filters: list[str], legacy_count: int,
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
    for path in LEGACY_FRONTEND:
        sources[path] = "\n".join(f"def test_legacy_{index}(): pass" for index in range(4))
    for name, source in sources.items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source, encoding="utf-8")
    report, junit = tmp_path / "report.json", tmp_path / "junit.xml"
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
    assert sum(name.startswith("test_legacy_") for _, name in names) == legacy_count
    assert sum(name == "test_backend" for _, name in names) == 1
    assert sum(name == "test_components" for _, name in names) == int(frontend)
    assert sum(name == "test_requested" for _, name in names) == 1
    assert not any(name == "test_unrequested" for _, name in names)
