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
    selections = [selection for selection in filters if selection]
    for selection in selections:
        candidate = Path(selection)
        assert not candidate.is_absolute() and ".." not in candidate.parts
        args.append(selection)
    log = tmp_path / "TEST_components.log"
    # The full suite can exceed 180s with two jsdom workers on the shared test host.
    timeout = 180 if selections else 360
    try:
        with log.open("w", encoding="utf-8") as output_file, owned_process(
            args,
            cwd=frontend,
            stdout=output_file,
            stderr=subprocess.STDOUT,
            env={**os.environ, "CI": "1", "NODE_ENV": "test"},
        ) as process:
            process.wait(timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        partial = log.read_text(encoding="utf-8", errors="replace")
        exc.add_note(f"Partial component output: {log}\n{partial[-24000:]}")
        raise
    output = log.read_text(encoding="utf-8", errors="replace")
    if planned is not None:
        print(output)
    assert process.returncode == 0, f"Full component output: {log}\n{output[-24000:]}"
