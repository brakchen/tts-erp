"""Build current test assets through the shared worktree-local lock."""

from __future__ import annotations

import fcntl
import json
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class FrontendBuild:
    release_id: str
    wait_seconds: float
    build_seconds: float


def prepare_frontend(root: Path) -> FrontendBuild:
    lock_path = Path(subprocess.check_output(
        ["git", "rev-parse", "--git-path", "tts-test-frontend.lock"], cwd=root, text=True,
    ).strip())
    if not lock_path.is_absolute():
        lock_path = root / lock_path
    started = time.monotonic()
    with lock_path.open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        wait_seconds = time.monotonic() - started
        started = time.monotonic()
        subprocess.run(["bash", "scripts/build_frontend.sh"], cwd=root, check=True, timeout=240)
        build_seconds = time.monotonic() - started
        release = json.loads((root / "tts_erp_v2/static/frontend/release.json").read_text(encoding="utf-8"))
        return FrontendBuild(release["releaseId"], wait_seconds, build_seconds)
