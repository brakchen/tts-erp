"""Execute the committed selection inside the isolated test wrapper."""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import subprocess
import sys
from dataclasses import asdict
from pathlib import Path

from _test_selection import (
    COMPONENTS,
    LEGACY_FRONTEND,
    ROOT,
    SCENARIOS,
    changed_paths,
    git,
    resolve_commit,
    select,
)

BACKEND_EXCLUDED_DIRS = {"browser", "e2e", "fixtures", "frontend", "support", "integration", "__pycache__"}
SAFE_MARKS = "not slow and not requires_service and not domain_migration"


def run(base: str, report: str, scenarios: list[str], scope: str) -> int:
    if git("status", "--porcelain", "-z", "--untracked-files=all"):
        raise ValueError("selected execution requires a clean committed worktree")
    base, head = resolve_commit(base), resolve_commit("HEAD")
    git("merge-base", "--is-ancestor", base, head)
    plan = select(changed_paths(base, head), scenarios, scope=scope)
    print(json.dumps({"base": base, "head": head, **asdict(plan)}, indent=2), flush=True)
    live = [SCENARIOS[name] for name in plan.scenarios]
    targets = [*plan.backend, *plan.checks, *plan.browser_mock, *live]
    if plan.frontend:
        targets.append(COMPONENTS)
        if not plan.vitest:
            targets.extend((*LEGACY_FRONTEND, "tests/frontend/test_ad_daily_legacy.py"))
    required = list(dict.fromkeys(targets))
    if plan.backend_all:
        roots = [
            path.relative_to(ROOT).as_posix()
            for path in (ROOT / "tests").iterdir()
            if (path.is_dir() and path.name not in BACKEND_EXCLUDED_DIRS and not path.name.startswith("."))
            or path.match("test_*.py")
        ]
        targets = sorted(roots) + [
            target for target in required
            if not any(target == root or target.startswith((root + "/", root + "::")) for root in roots)
            or target in LEGACY_FRONTEND
        ]
    if not targets:
        return 5
    if plan.refresh_template and os.environ.get("TTS_ERP_TEST_REFRESHED_FOR_SELECTION") != "1":
        raise ValueError("schema changes require --refresh-template")

    env = os.environ.copy()
    env.update(
        PYTEST_ADDOPTS="",
        TTS_ERP_TEST_SELECTION_REPORT=report,
        TTS_ERP_TEST_VITEST_SELECTION=json.dumps(plan.vitest),
        TTS_ERP_TEST_SELECTED_NO_BUILD="0" if plan.build else "1",
        TTS_ERP_TEST_SELECTED_LIVE=json.dumps(live),
        TTS_ERP_TEST_SELECTION_REQUIRED=json.dumps(required),
        TTS_ERP_TEST_SELECTION_BACKEND_ALL="1" if plan.backend_all else "0",
    )
    args = [
        sys.executable, "-m", "pytest", "-q", "-rP", "-p", "tests.support.selected_test_report",
        "-p", "no:cacheprovider", "-m", SAFE_MARKS,
    ]
    if plan.backend_all:
        args.extend(f"--ignore={path}" for path in LEGACY_FRONTEND)
    if plan.build:
        lock_path = ROOT / Path(os.fsdecode(git("rev-parse", "--git-path", "tts-test-frontend.lock")).strip())
        with lock_path.open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            subprocess.run(["bash", "scripts/build_frontend.sh"], cwd=ROOT, env=env, check=True, timeout=240)
            release = ROOT / "tts_erp_v2/static/frontend/release.json"
            env["TTS_ERP_TEST_PREPARED_RELEASE"] = json.loads(release.read_text(encoding="utf-8"))["releaseId"]
            return subprocess.run([*args, *targets], cwd=ROOT, env=env, check=False).returncode
    if plan.frontend:
        subprocess.run(["npm", "--prefix", "frontend", "run", "typecheck"], cwd=ROOT, env=env, check=True, timeout=120)
    return subprocess.run([*args, *targets], cwd=ROOT, env=env, check=False).returncode


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", required=True)
    parser.add_argument("--scope", choices=("auto", "frontend", "backend"), default="auto")
    parser.add_argument("--report", required=True)
    parser.add_argument("--scenario", choices=sorted(SCENARIOS), action="append", default=[])
    args = parser.parse_args()
    try:
        return run(args.base, args.report, args.scenario, args.scope)
    except (ValueError, OSError, subprocess.SubprocessError) as exc:
        print(f"selected tests: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
