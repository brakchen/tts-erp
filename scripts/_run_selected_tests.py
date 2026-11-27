"""Execute one committed selection phase inside the isolated test wrapper."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

if __package__:
    from ._test_selection import (
        COMPONENTS,
        ROOT,
        SCENARIOS,
        Selection,
        git,
        resolve_commit,
        select_commits,
    )
else:
    from _test_selection import (
        COMPONENTS,
        ROOT,
        SCENARIOS,
        Selection,
        git,
        resolve_commit,
        select_commits,
    )

PHASES = ("backend", "frontend", "browser")
BACKEND_EXCLUDED_DIRS = {"browser", "e2e", "fixtures", "frontend", "support", "integration", "__pycache__"}
SAFE_MARKS = "not slow and not requires_service and not domain_migration"


@dataclass
class Phase:
    name: str
    targets: list[str]
    required: list[str]
    backend_all: bool = False
    build: bool = False


def _matches(node: str, target: str) -> bool:
    return node == target or node.startswith((target + "/", target + "::", target + "["))


def _minimal_targets(targets: list[str]) -> list[str]:
    """Do not collect a module twice when its explicit scenario is also selected."""
    unique = sorted(set(targets))
    return [target for target in unique if not any(
        other != target and _matches(target, other) for other in unique
    )]


def phase_plan(plan: Selection, scope: str) -> list[Phase]:
    """Partition execution, retaining explicit requirements even under broad roots."""
    live = [SCENARIOS[name] for name in plan.scenarios]
    required = list(dict.fromkeys([*plan.backend, *plan.checks, *plan.browser_mock, *live]))
    if plan.frontend:
        required.append(COMPONENTS)
        if not plan.vitest:
            required.extend(path.relative_to(ROOT).as_posix()
                            for path in sorted((ROOT / "tests/frontend").glob("test_*.py")))
    groups = {name: Phase(name, [], []) for name in PHASES}
    for target in dict.fromkeys(required):
        phase = "browser" if target in live or target.startswith(("tests/browser/", "tests/e2e/")) else (
            "frontend" if target.startswith("tests/frontend/") else "backend"
        )
        groups[phase].targets.append(target)
        groups[phase].required.append(target)
    if scope == "fast":
        # Markers place real-API asset cases outside tests/browser/. The report
        # plugin partitions retained items after SAFE_MARKS, exactly once.
        groups["backend"].targets = ["tests"]
        groups["backend"].backend_all = True
        groups["frontend"].targets = ["tests/frontend"]
        groups["browser"].targets = ["tests"]
        groups["browser"].required = list(dict.fromkeys(["tests/browser", *live]))
    elif plan.backend_all:
        roots = sorted(path.relative_to(ROOT).as_posix() for path in (ROOT / "tests").iterdir()
                       if (path.is_dir() and path.name not in BACKEND_EXCLUDED_DIRS
                           and not path.name.startswith(".")) or path.match("test_*.py"))
        groups["backend"].targets.extend(roots)
        groups["backend"].backend_all = True
    for phase in groups.values():
        phase.targets = _minimal_targets(phase.targets)
        phase.build = phase.name == "browser" or (
            phase.name == "frontend" and plan.build and not groups["browser"].targets
        )
    return [phase for phase in groups.values() if phase.targets or phase.backend_all]


def committed_plan(base: str, head: str, scenarios: list[str], scope: str) -> tuple[str, str, Selection]:
    if git("status", "--porcelain", "-z", "--untracked-files=all"):
        raise ValueError("selected execution requires a clean committed worktree")
    base, head = resolve_commit(base), resolve_commit(head)
    if head != resolve_commit("HEAD"):
        raise ValueError("HEAD changed after test selection")
    git("merge-base", "--is-ancestor", base, head)
    return base, head, select_commits(base, head, scenarios, scope)


def run(base: str, head: str, report: str, scenarios: list[str], scope: str, phase: str) -> int:
    base, head, plan = committed_plan(base, head, scenarios, scope)
    phases = {entry.name: entry for entry in phase_plan(plan, scope)}
    if phase not in phases:
        raise ValueError(f"phase {phase} is not in the committed selection")
    selected = phases[phase]
    print(json.dumps({"base": base, "head": head, "scope": scope, "phase": asdict(selected)}, indent=2), flush=True)
    if not selected.targets:
        return 5
    if (plan.refresh_template and os.environ.get("TTS_ERP_TEST_REFRESHED_FOR_SELECTION") != "1"
            and os.environ.get("TTS_ERP_TEST_REFRESHED_SELECTION_HEAD") != head):
        raise ValueError("schema changes require --refresh-template")

    env = os.environ.copy()
    for name in ("TTS_ERP_TEST_PREPARED_RELEASE", "TTS_ERP_TEST_PROGRESS_OWNER",
                 "FRONTEND_TEST_FILTER", "FRONTEND_TEST_NAME_PATTERN"):
        env.pop(name, None)
    env.update(
        PYTEST_ADDOPTS="",
        TTS_ERP_TEST_SELECTION_REPORT=report,
        TTS_ERP_TEST_PHASE=phase,
        TTS_ERP_TEST_PROGRESS=str(Path(report).with_suffix(".progress.json")),
        TTS_ERP_TEST_VITEST_SELECTION=json.dumps(plan.vitest),
        TTS_ERP_TEST_SELECTED_NO_BUILD="0" if selected.build else "1",
        TTS_ERP_TEST_SELECTED_LIVE=json.dumps(None if scope == "fast" else [SCENARIOS[name] for name in plan.scenarios]),
        TTS_ERP_TEST_SELECTION_REQUIRED=json.dumps(selected.required),
        TTS_ERP_TEST_SELECTION_BACKEND_ALL="1" if selected.backend_all else "0",
    )
    args = [
        sys.executable, "-m", "pytest", "-q", "-p", "tests.support.selected_test_report",
        "-p", "tests.support.test_runtime", "--durations=20", "-p", "no:cacheprovider", "-m", SAFE_MARKS,
    ]
    if scope == "fast" and phase == "backend":
        args.extend(["--ignore=tests/frontend", "--ignore=tests/browser", "--ignore=tests/e2e"])
    if phase == "frontend" and plan.frontend and not plan.build:
        subprocess.run(["npm", "--prefix", "frontend", "run", "typecheck"], cwd=ROOT, env=env, check=True, timeout=120)
    return subprocess.run([*args, *selected.targets], cwd=ROOT, env=env, check=False).returncode


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", required=True)
    parser.add_argument("--scope", choices=("auto", "frontend", "backend", "fast"), default="auto")
    parser.add_argument("--head", required=True)
    parser.add_argument("--report", required=True)
    parser.add_argument("--phase", choices=PHASES, required=True)
    parser.add_argument("--scenario", choices=sorted(SCENARIOS), action="append", default=[])
    args = parser.parse_args()
    try:
        return run(args.base, args.head, args.report, args.scenario, args.scope, args.phase)
    except (ValueError, OSError, subprocess.SubprocessError) as exc:
        print(f"selected tests: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
