"""Reuse Chromium within one synchronous test module, never across modules."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
import time
from typing import Any

from tests.support.test_runtime import record_phase


class BrowserLaunchError(RuntimeError):
    """Chromium is unavailable; driver startup failures remain real errors."""


class ModuleBrowser:
    def __init__(self) -> None:
        self.playwright: Any = None
        self.browser: Any = None

    def get(self) -> Any:
        if self.browser is None:
            from playwright.sync_api import sync_playwright

            started = time.monotonic()
            self.playwright = sync_playwright().start()
            try:
                self.browser = self.playwright.chromium.launch(headless=True)
            except Exception as exc:
                self.close()
                raise BrowserLaunchError("Chromium launch failed") from exc
            except BaseException:
                self.close()
                raise
            finally:
                record_phase("browser_start", time.monotonic() - started)
        return self.browser

    def close(self) -> None:
        started = time.monotonic()
        browser, playwright = self.browser, self.playwright
        self.browser = self.playwright = None
        try:
            if browser is not None:
                browser.close()
        finally:
            if playwright is not None:
                playwright.stop()
            record_phase("browser_cleanup", time.monotonic() - started)


_current: ModuleBrowser | None = None


def current_module_browser() -> ModuleBrowser | None:
    return _current


@contextmanager
def borrowed_browser() -> Iterator[Any]:
    """Full-stack consumers keep hard failures and fresh per-case contexts."""
    runtime = current_module_browser() or ModuleBrowser()
    browser = runtime.get()
    try:
        yield browser
    finally:
        if current_module_browser() is not runtime or browser.contexts:
            runtime.close()


@contextmanager
def module_browser_scope() -> Iterator[ModuleBrowser]:
    # conftest can be loaded both by pytest and by a qualified Python import.
    # Bind lifecycle state here so both consumers see the same canonical owner.
    global _current
    assert _current is None, "browser module lifecycles must not overlap"
    runtime = ModuleBrowser()
    _current = runtime
    try:
        yield runtime
    finally:
        try:
            runtime.close()
        finally:
            _current = None
