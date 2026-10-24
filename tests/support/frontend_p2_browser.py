"""Current-clone browser egress only, not server authorization or rollout.

Public Playwright routing/fetch, no proxy, redirects or command retries:
https://playwright.dev/python/docs/api/class-route#route-fetch
No raw request/body/query/console, credentials, trace or screenshots are saved.
"""
from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from urllib.parse import parse_qs, urlsplit

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import Locator, Page, Route, sync_playwright

from tests.support.frontend_p2_accounts import SeedAccount
from tests.support.frontend_p2_binding import NONCE_HEADER, verify_attestation
from tests.support.frontend_p2_canary import (
    VerifiedDataProbe,
    _require_child,
    require_data_probe,
)

REFUSAL = "P2 browser request refused"
PAGES = {"/v2/pages/intercept-stats", "/v2/pages/shops", "/v2/pages/enum-map", "/v2/pages/dashboard"}


@dataclass
class BrowserFence:
    probe: VerifiedDataProbe = field(repr=False)
    username: str
    shop_id: str
    service_id: str
    enum_value: str
    shop_pks: set[int] = field(default_factory=set)
    enum_pks: set[int] = field(default_factory=set)
    calls: list[tuple[str, str, bool]] = field(default_factory=list)
    violations: list[str] = field(default_factory=list)
    drop_next: tuple[str, str] | None = None
    lost: int = 0
    closing: bool = False

    def __post_init__(self) -> None:
        require_data_probe(self.probe)
        if not (self.username.startswith("test_p2_") and self.shop_id.isdigit()
                and self.service_id.startswith("TEST_") and self.enum_value.startswith("TEST_")):
            raise ValueError(REFUSAL)

    def check_request(self, method: str, url: str, body: object) -> str:
        _require_child(self.probe.child)
        target, base = urlsplit(url), urlsplit(self.probe.child.base)
        if (target.scheme, target.hostname, target.port) != (base.scheme, base.hostname, base.port) or target.username or target.password or target.fragment:
            raise ValueError(REFUSAL)
        path = target.path
        if "%" in path or "\\" in path or any(part in {".", "..", ""} for part in path.split("/")[1:]):
            raise ValueError(REFUSAL)
        prefix = self.probe.prefix
        if prefix and not path.startswith(prefix + "/"):
            raise ValueError(REFUSAL)
        path = path[len(prefix):]
        query = parse_qs(target.query, keep_blank_values=True)
        if method == "GET":
            if not query and (path in PAGES or path in {"/v2/auth/me", "/v2/auth/login"}
                              or path.startswith(("/static/frontend/", "/static/fonts/"))
                              or path in {"/static/vendor/bootstrap.min.css", "/static/css/tokens.css", "/static/css/common.css"}):
                return path
            if path == "/v2/auth/login" and set(query) == {"next"} and query["next"] in ([prefix + page] for page in PAGES):
                return path
            if path == "/v2/intercept/requests/stats" and query == {"days": ["7"]}:
                return path
            if path == "/v2/commerce/channel-accounts" and query == {"platform": ["tiktok"], "limit": ["500"]}:
                return path
            if not query and path == "/v2/config/enum-map/list":
                return path
            if not query and path in {f"/v2/commerce/channel-accounts/{pk}" for pk in self.shop_pks}:
                return path
            if path == "/v2/oauth/tiktok/authorize" and query == {"format": ["json"], "service_id": [self.service_id]}:
                return path
        if method == "POST" and not query and path == "/v2/auth/login" and isinstance(body, dict) and body.get("username") == self.username:
            return path
        if method == "POST" and not query and path == "/v2/auth/logout":
            return path
        if not query and isinstance(body, dict):
            register = method == "POST" and path == "/v2/admin/shops/register"
            patch = method == "PATCH" and path in {f"/v2/admin/shops/{pk}" for pk in self.shop_pks}
            if register or patch:
                allowed = {"account_name", "region", "opened_date", "service_id", "app_key", "app_secret"}
                if register:
                    allowed |= {"platform", "shop_id"}
                safe = set(body) <= allowed
                safe = safe and (not register or body.get("shop_id") == self.shop_id and body.get("platform") == "tiktok")
                safe = safe and all(isinstance(body[key], str) and body[key].startswith("TEST_") for key in ("account_name", "app_key", "app_secret") if key in body)
                safe = safe and ("service_id" not in body or body["service_id"] == self.service_id)
                if safe:
                    return path
            if (method == "PUT" and path == "/v2/config/enum-map" and set(body) == {"enum_type", "enum_value", "label_zh", "sort_order"}
                    and body["enum_type"] == "order_status" and body["enum_value"] == self.enum_value
                    and isinstance(body["label_zh"], str) and body["label_zh"].startswith("TEST_")
                    and type(body["sort_order"]) is int):
                return path
        if method == "DELETE" and not query and path in {f"/v2/config/enum-map/{pk}" for pk in self.enum_pks}:
            return path
        raise ValueError(REFUSAL)

    def handle(self, route: Route) -> None:
        if self.closing:
            route.abort()
            return
        try:
            request = route.request
            path = self.check_request(request.method, request.url, request.post_data_json)
            self.calls.append((request.method, path, request.headers.get("x-requested-with") == "tts-erp"))
            response = route.fetch(max_redirects=0, max_retries=0, timeout=10_000)
            verify_attestation(self.probe.child.attestation, self.probe.child.binding,
                               nonce=self.probe.child.nonce, child_pid=self.probe.child.pid,
                               child_start=self.probe.child.start, http_nonce=response.headers.get(NONCE_HEADER))
            _require_child(self.probe.child)
            if self.drop_next == (request.method, path) and 200 <= response.status < 300:
                self.drop_next = None
                self.lost += 1
                route.abort("failed")  # Actual processing/commit already returned 2xx.
            else:
                route.fulfill(response=response)
        except (ValueError, TypeError, KeyError, AttributeError, OSError, PlaywrightError):
            self.violations.append(REFUSAL)
            route.abort("blockedbyclient")


def fill_sensitive(locator: Locator, value: str) -> None:
    """Use native fill, but never serialize its credential-bearing call log."""
    try:
        locator.fill(value)
    except PlaywrightError:
        raise ValueError("P2 sensitive input refused") from None


@contextmanager
def live_browser(
    probe: VerifiedDataProbe, account: SeedAccount, *, shop_id: str, service_id: str,
    enum_value: str, width: int,
) -> Iterator[tuple[Page, BrowserFence]]:
    gate = BrowserFence(probe, username=account.username, shop_id=shop_id,
                        service_id=service_id, enum_value=enum_value)
    # No skip/fallback for missing Chromium, persistent context or service worker.
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        try:
            context = browser.new_context(viewport={"width": width, "height": 1000}, service_workers="block")
            try:
                context.route("**/*", gate.handle)

                def reject_socket(socket) -> None:
                    gate.violations.append(REFUSAL)
                    socket.close()

                context.route_web_socket("**/*", reject_socket)
                page = context.new_page()
                yield page, gate
                page.wait_for_load_state("networkidle")
                if gate.violations:
                    raise ValueError(REFUSAL)
            finally:
                gate.closing = True
                context.close()
        finally:
            browser.close()
