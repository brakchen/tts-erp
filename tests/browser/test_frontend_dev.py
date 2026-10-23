"""Real local Vite/Fast Refresh handshake and one disposable HMR module update."""

from __future__ import annotations

import os
import socket
import subprocess
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

import pytest

pytestmark = [pytest.mark.domain_browser, pytest.mark.requires_browser]
ROOT = Path(__file__).resolve().parents[2]


def test_local_backend_page_supports_vite_hmr(frontend_browser_renderer, frontend_preview, tmp_path):
    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", 0))
        port = reservation.getsockname()[1]
    origin = f"http://127.0.0.1:{port}"
    probe_dir = ROOT / "frontend/.hmr-probe"
    probe_dir.mkdir(exist_ok=True)
    probe = probe_dir / f"TEST_{uuid.uuid4().hex}.ts"
    first = 'export const value = "TEST_first"; if (import.meta.hot) import.meta.hot.accept(module => { window.__TEST_frontendHmr = module.value; });'
    probe.write_text(first)
    with (tmp_path / "TEST_vite.log").open("w") as log:
        process = subprocess.Popen(
            ["npm", "run", "dev", "--", "--port", str(port)],
            cwd=ROOT / "frontend", stdout=log, stderr=subprocess.STDOUT,
            env={**os.environ, "NODE_ENV": "development"},
        )
        try:
            for _ in range(100):
                assert process.poll() is None, "local Vite process exited"
                try:
                    with urllib.request.urlopen(origin + "/@vite/client", timeout=1) as response:
                        if response.status == 200:
                            break
                except (urllib.error.URLError, TimeoutError):
                    time.sleep(0.1)
            else:
                pytest.fail("local Vite did not become ready")
            with frontend_preview(origin) as backend:
                page = frontend_browser_renderer.open("/v2/pages/dashboard")
                page.goto(backend + "/tts/v2/pages/dashboard", wait_until="networkidle")
                page.get_by_role("heading", level=1, name="控制台", exact=True).wait_for(timeout=20_000)
                assert page.evaluate("window.__vite_plugin_react_preamble_installed__") is True
                module_url = origin + "/.hmr-probe/" + probe.name
                page.evaluate("async url => { const module = await import(url); window.__TEST_frontendHmr = module.value; }", module_url)
                page.wait_for_function("window.__TEST_frontendHmr === 'TEST_first'")
                probe.write_text(first.replace("TEST_first", "TEST_second"))
                page.wait_for_function("window.__TEST_frontendHmr === 'TEST_second'", timeout=15_000)
        finally:
            process.terminate()
            try:
                process.wait(timeout=8)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
            probe.unlink(missing_ok=True)
            try:
                probe_dir.rmdir()
            except OSError:
                pass
