"""Prove partition and aggregate failures without invoking a real test database."""
from __future__ import annotations

import contextlib
from types import SimpleNamespace

import pytest

from scripts import _test_browser_parallel as parallel

FILES = ["tests/browser/test_frontend_ad_daily_filters.py", "tests/browser/test_frontend_dashboard_layout.py"]


def project(tmp_path, monkeypatch):
    for name in FILES:
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch()
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    (scripts / "test_isolated.sh").write_text(
        "set -eu\n"
        "test \"$1\" = browser\n"
        "printf '%s\\n' \"$@\" >> TEST_executed_$BASHPID\n"
        "case \"$*\" in *dashboard*) exit \"${TEST_DASHBOARD_EXIT:-0}\";; esac\n",
    )
    monkeypatch.setattr(parallel, "ROOT", tmp_path)
    monkeypatch.setattr(parallel, "prepare_frontend", lambda root: contextlib.nullcontext(SimpleNamespace(cached=True)))


def test_partition_keeps_modules_and_rejects_unknown_overlap_and_duplicates(tmp_path, monkeypatch):
    project(tmp_path, monkeypatch)
    nodes = [FILES[0] + "::test_a", FILES[1], FILES[0] + "::test_b"]
    groups = parallel.partition(nodes, 2)
    assert groups == [[nodes[0], nodes[2]], [nodes[1]]]
    for invalid in [[], [FILES[0], FILES[0]], [FILES[0], nodes[0]], ["tests/browser/test_media_workflows.py"], ["--collect-only"]]:
        with pytest.raises(ValueError):
            parallel.partition(invalid, 2)


@pytest.mark.parametrize("exit_code", [0, 1, 2, 5, 70])
def test_each_target_runs_once_and_any_child_failure_propagates(tmp_path, monkeypatch, exit_code):
    project(tmp_path, monkeypatch)
    monkeypatch.setenv("TEST_DASHBOARD_EXIT", str(exit_code))
    assert parallel.run(FILES, 2) == (0 if exit_code == 0 else 1)
    outputs = sorted(tmp_path.glob("TEST_executed_*"))
    assert len(outputs) == 2
    observed = [path.read_text().splitlines() for path in outputs]
    assert sorted(observed) == sorted([["browser", name] for name in FILES])
