"""Prove module reuse releases Playwright before neighboring async tests."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from tests.render_support import Renderer
from tests.support.browser_runtime import BrowserLaunchError, ModuleBrowser

ROOT = Path(__file__).resolve().parents[2]


def test_reuses_browser_and_releases_driver_even_if_browser_close_fails(monkeypatch) -> None:
    import playwright.sync_api

    start = MagicMock()
    monkeypatch.setattr(playwright.sync_api, "sync_playwright", start)
    runtime = ModuleBrowser()
    browser = runtime.get()
    assert runtime.get() is browser
    start.return_value.start.return_value.chromium.launch.assert_called_once()
    browser.close.side_effect = RuntimeError("TEST_close_failure")
    with pytest.raises(RuntimeError, match="TEST_close_failure"):
        runtime.close()
    start.return_value.start.return_value.stop.assert_called_once()
    assert runtime.browser is runtime.playwright is None
    runtime.close()


def test_launch_failure_releases_driver(monkeypatch) -> None:
    import playwright.sync_api

    start = MagicMock()
    monkeypatch.setattr(playwright.sync_api, "sync_playwright", start)
    start.return_value.start.return_value.chromium.launch.side_effect = RuntimeError("TEST_launch_failure")
    runtime = ModuleBrowser()
    with pytest.raises(BrowserLaunchError, match="Chromium launch failed"):
        runtime.get()
    start.return_value.start.return_value.stop.assert_called_once()
    assert runtime.browser is runtime.playwright is None


def test_driver_startup_error_is_not_classified_as_missing_chromium(monkeypatch) -> None:
    import playwright.sync_api

    start = MagicMock()
    monkeypatch.setattr(playwright.sync_api, "sync_playwright", start)
    start.return_value.start.side_effect = RuntimeError("TEST_event_loop_conflict")
    with pytest.raises(RuntimeError, match="TEST_event_loop_conflict") as result:
        ModuleBrowser().get()
    assert not isinstance(result.value, BrowserLaunchError)


def test_failed_navigation_context_is_still_closed() -> None:
    browser = MagicMock()
    context = browser.new_context.return_value
    context.new_page.return_value.goto.side_effect = RuntimeError("TEST_navigation_failure")
    renderer = Renderer(browser, "http://127.0.0.1")
    with pytest.raises(RuntimeError, match="TEST_navigation_failure"):
        renderer.open("/TEST_page")
    renderer.close()
    context.close.assert_called_once()


def test_real_browser_contexts_and_mixed_async_modules(tmp_path: Path) -> None:
    browser_dir = tmp_path / "tests/browser"
    browser_dir.mkdir(parents=True)
    (browser_dir / "conftest.py").write_text(
        (ROOT / "tests/browser/conftest.py").read_text(encoding="utf-8"), encoding="utf-8",
    )
    (tmp_path / "pytest.ini").write_text("[pytest]\nasyncio_mode = auto\n", encoding="utf-8")
    (browser_dir / "test_a_sync.py").write_text(
        "import asyncio\n"
        "TEST_browsers = []\n"
        "def test_first(_module_browser_runtime):\n"
        "    browser = _module_browser_runtime.get()\n"
        "    TEST_browsers.append(browser)\n"
        "    with browser.new_context() as context:\n"
        "        context.add_cookies([{'name': 'TEST_cookie', 'value': 'secret', 'url': 'http://example.test'}])\n"
        "        assert context.cookies()\n"
        "def test_second(_module_browser_runtime):\n"
        "    browser = _module_browser_runtime.get()\n"
        "    assert browser is TEST_browsers[0]\n"
        "    assert browser.contexts == []\n"
        "    with browser.new_context() as context: assert context.cookies() == []\n"
        "async def test_async_between():\n"
        "    await asyncio.sleep(0)\n"
        "def test_relaunch(_module_browser_runtime):\n"
        "    assert _module_browser_runtime.get() is not TEST_browsers[0]\n",
        encoding="utf-8",
    )
    (browser_dir / "test_b_async.py").write_text(
        "import asyncio\nasync def test_next_module(): await asyncio.sleep(0)\n", encoding="utf-8",
    )
    env = {**os.environ, "PYTHONPATH": os.pathsep.join((str(ROOT), str(ROOT / "tests"))), "PYTEST_ADDOPTS": ""}
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "tests"], cwd=tmp_path, env=env,
        text=True, capture_output=True, timeout=45, check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "5 passed" in result.stdout and "skipped" not in result.stdout
