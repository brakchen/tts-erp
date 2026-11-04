"""Run frontend component tests only inside the canonical isolated pytest entry."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from tests.support.owned_process import owned_process

pytestmark = [pytest.mark.domain_frontend, pytest.mark.layer_unit]
ROOT = Path(__file__).resolve().parents[2]


def test_frontend_components(tmp_path: Path) -> None:
    frontend = ROOT / "frontend"
    assert (frontend / "node_modules" / "vitest").is_dir(), (
        "Frontend dependencies missing: run npm ci --prefix frontend before isolated tests"
    )
    args = ["npm", "run", "test:unit", "--"]
    planned = os.environ.get("TTS_ERP_TEST_VITEST_SELECTION")
    filters = json.loads(planned) if planned is not None else [os.environ.get("FRONTEND_TEST_FILTER")]
    for selection in filter(None, filters):
        candidate = Path(selection)
        assert not candidate.is_absolute() and ".." not in candidate.parts
        args.append(selection)
    with owned_process(
        args,
        cwd=frontend,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env={**os.environ, "CI": "1", "NODE_ENV": "test"},
    ) as process:
        stdout, stderr = process.communicate(timeout=180)
        output = stdout + stderr
        log = tmp_path / "TEST_components.log"
        log.write_text(output)
        if planned is not None:
            print(output)
        assert process.returncode == 0, f"Full component output: {log}\n{output[-24000:]}"
