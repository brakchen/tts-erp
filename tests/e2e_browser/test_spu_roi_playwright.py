"""Playwright E2E 测试编排层。

此模块在 scripts/test_isolated.sh e2e 下运行：
1. 使用隔离测试 DB（由 test_isolated.sh 克隆模板并设置 TTS_ERP_DB_URL_TEST）
2. seed TEST_E2E_* 数据
3. 启动临时 uvicorn（TTS_ERP_AUTH_MODE=enforce）
4. 运行 Playwright 测试（通过 subprocess 调用 npx playwright test）
5. 清理

直接运行：
  TTS_ERP_DB_URL_TEST=<url> python3 -m pytest tests/e2e_browser/ -x -q \
    -o "e2e_playwright_args=--project=spu-roi --grep=@tier:core"
"""
from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
from collections.abc import Generator
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "tests" / "support"))

from spu_roi_e2e_seed import cleanup, seed_all  # noqa: E402


def _find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _wait_for_server(port: int, timeout: float = 30.0) -> bool:
    """Wait until the server responds to /healthz."""
    import urllib.request

    deadline = time.monotonic() + timeout
    url = f"http://127.0.0.1:{port}/healthz"
    while time.monotonic() < deadline:
        try:
            req = urllib.request.Request(url)
            with urllib.request.urlopen(req, timeout=2) as r:  # noqa: S310 — localhost only
                if r.status == 200:
                    return True
        except Exception:
            time.sleep(0.3)
    return False


@pytest.fixture(scope="session")
def db_url() -> str:
    """Get test DB URL from environment (set by test_isolated.sh)."""
    url = os.environ.get("TTS_ERP_DB_URL_TEST")
    if not url:
        pytest.skip("TTS_ERP_DB_URL_TEST not set; run via scripts/test_e2e.sh")
    return url


@pytest.fixture(scope="session")
def db_engine(db_url: str) -> Generator:
    """Create SQLAlchemy engine for the test DB."""
    from sqlalchemy import create_engine

    engine = create_engine(db_url, pool_pre_ping=True)
    yield engine
    engine.dispose()


@pytest.fixture(scope="session")
def seed_info(db_engine) -> Generator:
    """Seed test data and return seed info."""
    info = seed_all(db_engine)
    yield info
    cleanup(db_engine)


@pytest.fixture(scope="session")
def uvicorn_server(db_url: str, seed_info: dict):
    """Start a temporary uvicorn server with TTS_ERP_AUTH_MODE=enforce."""
    port = _find_free_port()
    env = os.environ.copy()
    env.update({
        "TTS_ERP_DB_URL": db_url,
        "TTS_ERP_AUTH_MODE": "enforce",
        "TTS_ERP_SESSION_SECRET": "TEST_E2E_SESSION_SECRET_FOR_BROWSER_TESTS_ONLY",
        "TTS_ERP_SESSION_SECURE": "0",
        "TTS_ERP_ACCESS_LOG": "0",
    })

    proc = subprocess.Popen(
        [
            sys.executable, "-m", "uvicorn",
            "tts_erp_v2.app:build_app",
            "--factory",
            "--host", "127.0.0.1",
            "--port", str(port),
            "--log-level", "warning",
            "--no-access-log",
        ],
        env=env,
        cwd=str(REPO_ROOT),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    try:
        if not _wait_for_server(port, timeout=30):
            proc.terminate()
            try:
                _stdout, stderr_bytes = proc.communicate(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                _stdout, stderr_bytes = proc.communicate(timeout=5)
            stderr = stderr_bytes.decode(errors="replace") if stderr_bytes else ""
            raise RuntimeError(f"uvicorn failed to start on port {port}:\n{stderr[:2000]}")
    except Exception:
        # Re-raise after cleanup (RuntimeError from above, or any other error)
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)
        raise

    yield {
        "port": port,
        "base_url": f"http://127.0.0.1:{port}",
        "key": seed_info["key_plaintext"],
        "shop_pk": seed_info["shop_pk"],
        "shop_id": seed_info["shop_id"],
        "shop2_pk": seed_info["shop2_pk"],
        "shop2_id": seed_info["shop2_id"],
        "spu_count": seed_info["spu_count"],
    }

    proc.terminate()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=5)


@pytest.fixture(scope="session")
def playwright_args() -> list[str]:
    """Get Playwright CLI args from environment (set by test_e2e.sh)."""
    return os.environ.get("E2E_PLAYWRIGHT_ARGS", "").split()


@pytest.mark.domain_e2e       # excluded from `fast` by marker filter
@pytest.mark.requires_service   # needs live uvicorn
@pytest.mark.slow               # browser startup + network
class TestSpuRoiPlaywright:
    """Run Playwright E2E tests against a live temporary server."""

    def test_spu_roi_e2e(self, uvicorn_server: dict, monkeypatch):
        """Execute Playwright test suite for SPU ROI page."""
        # Build Playwright args from E2E_PLAYWRIGHT_ARGS env var (set by test_e2e.sh)
        pw_args = os.environ.get("E2E_PLAYWRIGHT_ARGS", "")
        if not pw_args:
            # Default to spu-roi core
            pw_args = "--project=spu-roi --grep=@tier:core"
        args_list = pw_args.split()

        env = os.environ.copy()
        env["E2E_BASE_URL"] = uvicorn_server["base_url"]
        env["E2E_API_KEY"] = uvicorn_server["key"]
        env["E2E_SHOP_PK"] = str(uvicorn_server["shop_pk"])
        env["E2E_SHOP_ID"] = uvicorn_server["shop_id"]
        env["E2E_SHOP2_PK"] = str(uvicorn_server["shop2_pk"])
        env["E2E_SHOP2_ID"] = uvicorn_server["shop2_id"]

        cmd = [
            "npx", "playwright", "test",
            *args_list,
            "--reporter=list",
        ]

        result = subprocess.run(
            cmd,
            cwd=str(REPO_ROOT),
            env=env,
            capture_output=True,
            text=True,
            timeout=300,
        )

        # Print output for visibility
        if result.stdout:
            print(result.stdout, file=sys.stderr)
        if result.stderr:
            print(result.stderr, file=sys.stderr)

        assert result.returncode == 0, (
            f"Playwright tests failed (exit code {result.returncode}).\n"
            f"STDOUT:\n{result.stdout[-3000:]}\n"
            f"STDERR:\n{result.stderr[-2000:]}"
        )
