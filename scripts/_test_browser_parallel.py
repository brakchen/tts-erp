"""Bounded two-way adapter around the sole isolated test entry point."""
from __future__ import annotations

import argparse
import os
import signal
import subprocess
import sys
import tempfile
import time
from contextlib import ExitStack
from pathlib import Path

from scripts._test_frontend import prepare_frontend
from tests.support.owned_process import owned_process

ROOT = Path(__file__).resolve().parents[1]
# Reviewed canned/read-only consumers only. Whole modules stay together so
# module fixtures/Chromium reuse work. Writable workflows and Vite stay serial.
SAFE_FILES = frozenset(f"tests/browser/{name}.py" for name in (
    "test_frontend_ad_daily_filters", "test_frontend_dashboard_layout",
    "test_frontend_shops", "test_spu_numeric_sort", "test_spu_date_range",
    "test_spu_price_columns", "test_spu_projection", "test_render_smoke",
))


def partition(targets: list[str], workers: int) -> list[list[str]]:
    if not targets or len(set(targets)) != len(targets):
        raise ValueError("Supply distinct explicit browser file/node targets")
    modules: dict[str, list[str]] = {}
    for target in targets:
        module = target.split("::", 1)[0]
        if module not in SAFE_FILES or not (ROOT / module).is_file():
            raise ValueError("Parallel browser target is outside the reviewed safe file list")
        peers = modules.setdefault(module, [])
        if any(target == peer or target.startswith((peer + "::", peer + "["))
               or peer.startswith((target + "::", target + "[")) for peer in peers):
            raise ValueError("Overlapping file/node selections are not supported")
        peers.append(target)
    groups: list[list[str]] = [[] for _ in range(min(workers, len(modules)))]
    for index, nodes in enumerate(modules.values()):
        groups[index % len(groups)].extend(nodes)
    return groups


def run(targets: list[str], workers: int) -> int:
    groups = partition(targets, workers)
    if os.environ.get("TTS_ERP_TEST_DB_NAME"):
        raise ValueError("Parallel browser runs require generated per-worker database names; unset TTS_ERP_TEST_DB_NAME")
    started = time.monotonic()
    # Prewarm under EX before starting consumers; readers then share one lease.
    # There is no parent database. Each child invokes the full isolated wrapper.
    with prepare_frontend(ROOT) as result:
        print(f"[browser-parallel] workers={len(groups)} cache={'hit' if result.cached else 'miss'}", flush=True)
    # Children revalidate after loading .env.test. Release the warmup lease so
    # an input/environment change cannot deadlock a child needing an EX build.
    with ExitStack() as resources:
        directory = Path(resources.enter_context(tempfile.TemporaryDirectory(prefix="tts-browser-parallel-")))
        children = []
        for index, group in enumerate(groups):
            path = directory / f"worker-{index + 1}.log"
            output = resources.enter_context(path.open("w", encoding="utf-8"))
            process = resources.enter_context(owned_process(
                ["bash", "scripts/test_isolated.sh", "browser", *group], cwd=ROOT,
                env=os.environ.copy(), stdout=output, stderr=subprocess.STDOUT,
            ))
            children.append((process, path))
            print(f"[browser-parallel] worker={index + 1} targets={group}", flush=True)
        try:
            deadline = time.monotonic() + 1200
            while any(process.poll() is None for process, _ in children):
                if time.monotonic() > deadline:
                    raise TimeoutError("Parallel browser execution exceeded 1200s")
                time.sleep(0.1)
        finally:
            # Let pytest finalizers and shell EXIT traps clean contexts and DBs
            # before the existing owner applies its bounded TERM/KILL cleanup.
            for process, _ in children:
                if process.poll() is None:
                    os.killpg(process.pid, signal.SIGINT)
            for process, path in children:
                try:
                    process.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    pass
                print(f"\n[browser-parallel] {path.name} exit={process.returncode}", flush=True)
                print(path.read_text(encoding="utf-8", errors="replace"), flush=True)
        codes = [process.returncode for process, _ in children]
    print(f"[test-timing] browser_parallel_total={time.monotonic() - started:.3f}s", flush=True)
    return 0 if all(code == 0 for code in codes) else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workers", type=int, choices=(1, 2), required=True)
    parser.add_argument("scope", choices=("browser",))
    parser.add_argument("targets", nargs="+")
    args = parser.parse_args()
    # SIGTERM also reaches the resource/finalizer path. Never leave orphan runs.
    signal.signal(signal.SIGTERM, lambda *_: (_ for _ in ()).throw(KeyboardInterrupt()))
    try:
        return run(args.targets, args.workers)
    except KeyboardInterrupt:
        return 130
    except (ValueError, OSError, subprocess.SubprocessError, TimeoutError) as exc:
        print(f"Parallel browser tests: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
