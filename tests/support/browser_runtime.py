"""Reuse Chromium within one synchronous test module, never across modules."""

from __future__ import annotations

from typing import Any


class BrowserLaunchError(RuntimeError):
    """Chromium is unavailable; driver startup failures remain real errors."""


class ModuleBrowser:
    def __init__(self) -> None:
        self.playwright: Any = None
        self.browser: Any = None

    def get(self) -> Any:
        if self.browser is None:
            from playwright.sync_api import sync_playwright

            self.playwright = sync_playwright().start()
            try:
                self.browser = self.playwright.chromium.launch(headless=True)
            except Exception as exc:
                self.close()
                raise BrowserLaunchError("Chromium launch failed") from exc
            except BaseException:
                self.close()
                raise
        return self.browser

    def close(self) -> None:
        browser, playwright = self.browser, self.playwright
        self.browser = self.playwright = None
        try:
            if browser is not None:
                browser.close()
        finally:
            if playwright is not None:
                playwright.stop()
