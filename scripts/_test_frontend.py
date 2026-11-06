"""Content-validated test builds with a shared consumption lease."""
from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import signal
import subprocess
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from tests.support.owned_process import owned_process

BUILD_TIMEOUT_SECONDS = 240


def _build(root: Path) -> None:
    def terminate(_signum, _frame):
        raise KeyboardInterrupt

    previous = signal.signal(signal.SIGTERM, terminate)
    try:
        with owned_process(
            ["bash", "scripts/build_frontend.sh"], cwd=root, env=os.environ.copy(),
            stdout=None, stderr=None,
        ) as process:
            code = process.wait(timeout=BUILD_TIMEOUT_SECONDS)
            if code:
                raise subprocess.CalledProcessError(code, process.args)
    finally:
        signal.signal(signal.SIGTERM, previous)


@dataclass(frozen=True)
class FrontendBuild:
    release_id: str
    wait_seconds: float
    build_seconds: float
    cache_seconds: float = 0.0
    cached: bool = False


def _files(root: Path, excluded: set[str]) -> list[Path]:
    result: list[Path] = []
    for directory, dirs, names in os.walk(root):
        dirs[:] = sorted(name for name in dirs if name not in excluded)
        if any((Path(directory) / name).is_symlink() for name in dirs):
            raise ValueError("Frontend cache cannot traverse symlink directories")
        result.extend(Path(directory) / name for name in names)
    return result


def _digest(paths: list[Path], root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(paths):
        if path.is_symlink() or not path.is_file():
            raise ValueError("Frontend cache requires regular files")
        data = path.read_bytes()
        digest.update(f"{path.relative_to(root).as_posix()}\0{len(data)}\0".encode())
        digest.update(data)
    return digest.hexdigest()


def input_key(root: Path) -> str:
    frontend = root / "frontend"
    paths = _files(frontend, {"node_modules", "dist", ".cache"}) if frontend.exists() else []
    paths.append(root / "scripts/build_frontend.sh")
    # npm ci replaces this installation marker. Hand-editing node_modules is
    # unsupported: reinstall rather than sharing writable dependency installs.
    installed = frontend / "node_modules/.package-lock.json"
    installation = None
    if installed.exists():
        paths.append(installed)
        info = installed.stat()
        installation = (info.st_ino, info.st_mtime_ns)
    guard = frontend / "scripts/check-runtime.mjs"
    node = subprocess.check_output(
        ["node", str(guard)] if guard.exists() else ["node", "--version"], text=True,
    ).strip()
    npm = subprocess.check_output(["npm", "--version"], text=True).strip()
    environment = {name: value for name, value in os.environ.items() if
                   name.startswith(("VITE_", "NODE_", "npm_config_", "NPM_CONFIG_"))
                   or name in {"CI", "TZ", "SOURCE_DATE_EPOCH"}}
    inputs = (_digest(paths, root), Path(__file__).read_bytes().hex(), node, npm, installation, environment)
    return hashlib.sha256(json.dumps(inputs, sort_keys=True).encode()).hexdigest()


def _artifact(root: Path) -> tuple[str, str]:
    target = root / "tts_erp_v2/static/frontend"
    release = json.loads((target / "release.json").read_text(encoding="utf-8"))["releaseId"]
    if not isinstance(release, str) or not re.fullmatch(r"[A-Za-z0-9_-]+", release):
        raise ValueError("Invalid frontend release")
    directory = target / release
    if directory.is_symlink() or not directory.is_dir():
        raise ValueError("Frontend release directory missing")
    paths = _files(directory, set())
    if not paths:
        raise ValueError("Empty frontend release")
    return release, _digest(paths, target)


@contextmanager
def prepare_frontend(root: Path) -> Iterator[FrontendBuild]:
    lock_path = Path(subprocess.check_output(
        ["git", "rev-parse", "--git-path", "tts-test-frontend.lock"], cwd=root, text=True,
    ).strip())
    if not lock_path.is_absolute():
        lock_path = root / lock_path
    cache = lock_path.with_suffix(".json")
    started = time.monotonic()
    with lock_path.open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_SH)
        wait_seconds = time.monotonic() - started
        check_started = time.monotonic()
        key = input_key(root)

        def hit() -> str | None:
            try:
                record = json.loads(cache.read_text(encoding="utf-8"))
                release, digest = _artifact(root)
                if record == {"key": key, "release": release, "digest": digest}:
                    return release
            except (OSError, ValueError, KeyError, TypeError):
                pass
            return None

        release = hit()
        cache_seconds = time.monotonic() - check_started
        build_seconds = 0.0
        cached = release is not None
        if release is None:
            # Release SH before EX to avoid competing reader-upgrade deadlocks.
            fcntl.flock(lock, fcntl.LOCK_UN)
            started = time.monotonic()
            fcntl.flock(lock, fcntl.LOCK_EX)
            wait_seconds += time.monotonic() - started
            key = input_key(root)
            release = hit()
            cached = release is not None
            if release is None:
                cache.unlink(missing_ok=True)
                started = time.monotonic()
                _build(root)
                build_seconds = time.monotonic() - started
                if input_key(root) != key:
                    raise ValueError("Frontend inputs changed during build; rerun tests")
                release, digest = _artifact(root)
                temporary = cache.with_name(f"{cache.name}.{os.getpid()}.tmp")
                try:
                    temporary.write_text(json.dumps({"key": key, "release": release, "digest": digest}), encoding="utf-8")
                    temporary.replace(cache)
                finally:
                    temporary.unlink(missing_ok=True)
            fcntl.flock(lock, fcntl.LOCK_SH)
        yield FrontendBuild(release, wait_seconds, build_seconds, cache_seconds, cached)
