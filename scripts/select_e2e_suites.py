#!/usr/bin/env python3
"""根据 Git diff 选择受影响的 E2E 测试 suite 和 tier。

用法：
  python3 scripts/select_e2e_suites.py <base_ref>
  python3 scripts/select_e2e_suites.py origin/master
  python3 scripts/select_e2e_suites.py origin/master --tier core
  python3 scripts/select_e2e_suites.py origin/master --tier all

输出格式（每行一个条目）：
  suite_name tier
  例：spu-roi core

无匹配时输出空（exit 0），不报错。
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SUITES_FILE = REPO / "tests" / "e2e" / "suites.json"


def git_diff_names(base_ref: str) -> list[str]:
    """Return list of changed file paths between base_ref and HEAD."""
    result = subprocess.run(
        ["git", "diff", "--name-only", f"{base_ref}..HEAD", "--", "."],
        capture_output=True, text=True, cwd=REPO,
    )
    if result.returncode != 0:
        # fallback: maybe base_ref is not reachable; try merge-base
        result = subprocess.run(
            ["git", "diff", "--name-only", base_ref, "--", "."],
            capture_output=True, text=True, cwd=REPO,
        )
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def load_suites() -> dict:
    with SUITES_FILE.open() as f:
        return json.load(f)


def match_files(patterns: list[str], changed: list[str]) -> bool:
    """Check if any changed file matches any pattern (prefix match)."""
    for path in changed:
        for pat in patterns:
            if path.startswith(pat) or path == pat.rstrip("/"):
                return True
    return False


def main() -> int:
    parser = argparse.ArgumentParser(description="Select affected E2E suites")
    parser.add_argument("base_ref", help="Git ref to diff against (e.g. origin/master)")
    parser.add_argument("--tier", default="core", choices=["core", "all"],
                        help="Which tier to select (default: core)")
    parser.add_argument("--format", dest="fmt", default="text",
                        choices=["text", "json", "playwright-grep"],
                        help="Output format")
    args = parser.parse_args()

    suites = load_suites()
    changed = git_diff_names(args.base_ref)

    if not changed:
        return 0

    # If tests/e2e/ itself changed, run all affected suites at requested tier
    e2e_infra_changed = any(p.startswith("tests/e2e/") or p.startswith("scripts/test_e2e")
                           or p.startswith("scripts/select_e2e") or p == "playwright.config.js"
                           or p == "package.json" or p == "package-lock.json"
                           for p in changed)

    affected: set[str] = set()

    for name, cfg in suites.items():
        if name == "spu-profitability-shared" or name == "auth-shared":
            # These are meta-suites that only affect other suites
            if match_files(cfg.get("watchPaths", []), changed):
                for target in cfg.get("affectedSuites", []):
                    affected.add(target)
        else:
            if match_files(cfg.get("watchPaths", []), changed):
                affected.add(name)

    if e2e_infra_changed:
        # Run all page suites that have specs
        for name, cfg in suites.items():
            if "specDir" in cfg:
                affected.add(name)

    # Output
    results = sorted(affected)
    if args.fmt == "json":
        print(json.dumps([{"suite": s, "tier": args.tier} for s in results]))
    elif args.fmt == "playwright-grep":
        # Output grep patterns for playwright
        for s in results:
            print(f"@page:{s}")
    else:
        for s in results:
            print(f"{s} {args.tier}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())