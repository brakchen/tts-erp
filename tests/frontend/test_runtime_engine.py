"""Engine mismatches fail at the frontend command boundary, before any build."""
from __future__ import annotations

import subprocess
from pathlib import Path


def test_engine_guard_uses_the_declared_range_including_prereleases():
    frontend = Path(__file__).resolve().parents[2] / "frontend"
    result = subprocess.run([
        "node", "--input-type=module", "-e",
        "import assert from 'node:assert/strict';"
        "import { checkRuntime } from './scripts/check-runtime.mjs';"
        "for (const v of ['v22.22.1', 'v23.0.0', 'v22.22.2-rc.0']) "
        "assert.throws(() => checkRuntime(v), /Frontend requires Node/);"
        "assert.equal(checkRuntime('v22.22.2'), 'v22.22.2');"
        "assert.equal(checkRuntime(), process.version);",
    ], cwd=frontend, capture_output=True, text=True, timeout=10, check=False)
    assert result.returncode == 0, result.stdout + result.stderr
