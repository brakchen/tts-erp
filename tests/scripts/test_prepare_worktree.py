"""Preparation respects the selected scope and verifies private installs."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _checkout(tmp_path: Path) -> tuple[Path, dict[str, str]]:
    checkout, main = tmp_path / "checkout", tmp_path / "main"
    (checkout / "scripts/envsetup").mkdir(parents=True)
    for name in ("prepare-worktree.sh", "test-runtime.sh"):
        shutil.copyfile(ROOT / "scripts/envsetup" / name, checkout / "scripts/envsetup" / name)
    subprocess.run(["git", "init", "-q", str(checkout)], check=True)
    (main / ".venv/bin").mkdir(parents=True)
    (main / ".venv/bin/python").symlink_to(sys.executable)
    (main / ".env.test").write_text("# TEST fixture only; no database connection\n")
    (checkout / "frontend/scripts").mkdir(parents=True)
    (checkout / "frontend/package.json").write_text('{"name":"TEST_prepare"}')
    (checkout / "frontend/package-lock.json").write_text('{"lockfileVersion":3}')
    (checkout / "frontend/scripts/check-runtime.mjs").write_text("// TEST runtime double")
    tools = tmp_path / "tools"
    tools.mkdir()
    for name, source in {
        "node": '#!/bin/bash\necho v22.23.2\n',
        "npm": '#!/bin/bash\nif [[ "$1" == --version ]]; then echo 10.9.4; exit; fi\n'
               'echo installed >> "$TEST_INSTALL_TRACE"\nmkdir -p node_modules/vitest node_modules/semver\n',
    }.items():
        (tools / name).write_text(source)
        (tools / name).chmod(0o755)
    env = {**os.environ, "TTS_ERP_MAIN_CHECKOUT": str(main), "TTS_ERP_TEST_NODE_BIN": str(tools),
           "TEST_INSTALL_TRACE": str(tmp_path / "installs"), "PATH": f"{tools}:{os.environ['PATH']}"}
    return checkout, env


def _run(checkout: Path, env: dict[str, str], *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["bash", "scripts/envsetup/prepare-worktree.sh", *args], cwd=checkout,
                          env=env, capture_output=True, text=True, timeout=20, check=False)


def test_backend_preparation_needs_no_node_or_npm(tmp_path: Path) -> None:
    checkout, env = _checkout(tmp_path)
    env["TTS_ERP_TEST_NODE_BIN"] = "/TEST_missing_runtime"
    result = _run(checkout, env, "--scope", "backend")
    assert result.returncode == 0, result.stdout + result.stderr
    assert (checkout / ".venv/bin/python").exists()
    assert (checkout / ".env.test").exists()
    assert not (tmp_path / "installs").exists()


def test_dry_run_and_check_do_not_create_environment(tmp_path: Path) -> None:
    checkout, env = _checkout(tmp_path)
    result = _run(checkout, env, "--scope", "backend", "--dry-run")
    assert result.returncode == 0
    assert not (checkout / ".venv").exists() and not (checkout / ".env.test").exists()
    result = _run(checkout, env, "--scope", "backend", "--check")
    assert result.returncode != 0
    assert not (checkout / ".venv").exists() and not (checkout / ".env.test").exists()


def test_private_install_fingerprint_reuses_and_invalidates(tmp_path: Path) -> None:
    checkout, env = _checkout(tmp_path)
    for _ in range(2):
        result = _run(checkout, env, "--scope", "frontend")
        assert result.returncode == 0, result.stdout + result.stderr
    trace = tmp_path / "installs"
    assert trace.read_text().splitlines() == ["installed"]
    (checkout / "frontend/package-lock.json").write_text('{"lockfileVersion":3,"TEST_changed":true}')
    check = _run(checkout, env, "--scope", "frontend", "--check")
    assert check.returncode != 0 and trace.read_text().splitlines() == ["installed"]
    result = _run(checkout, env, "--scope", "frontend")
    assert result.returncode == 0, result.stdout + result.stderr
    assert len(trace.read_text().splitlines()) == 2


def test_shared_node_modules_are_rejected(tmp_path: Path) -> None:
    checkout, env = _checkout(tmp_path)
    foreign = tmp_path / "foreign_install"
    foreign.mkdir()
    (checkout / "frontend/node_modules").symlink_to(foreign, target_is_directory=True)
    result = _run(checkout, env, "--scope", "frontend")
    assert result.returncode != 0
    assert not (tmp_path / "installs").exists()


def test_auto_requires_an_explicit_base_before_mutation(tmp_path: Path) -> None:
    checkout, env = _checkout(tmp_path)
    result = _run(checkout, env, "--scope", "auto")
    assert result.returncode != 0
    assert not (checkout / ".venv").exists()
