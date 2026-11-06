"""Run frontend component tests only inside the canonical isolated pytest entry."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

pytestmark = [pytest.mark.domain_frontend, pytest.mark.layer_unit]
ROOT = Path(__file__).resolve().parents[2]


def test_frontend_components() -> None:
    frontend = ROOT / "frontend"
    assert (frontend / "node_modules" / "vitest").is_dir(), (
        "Frontend dependencies missing: run npm ci --prefix frontend before isolated tests"
    )
    args = ["npm", "run", "test:unit", "--"]
    selection = os.environ.get("FRONTEND_TEST_FILTER")
    if selection:
        candidate = Path(selection)
        assert not candidate.is_absolute() and ".." not in candidate.parts
        args.append(selection)
    result = subprocess.run(
        args,
        cwd=frontend,
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
        env={**os.environ, "CI": "1", "NODE_ENV": "test"},
    )
    assert result.returncode == 0, (result.stdout + result.stderr)[-24000:]
