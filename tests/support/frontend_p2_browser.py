"""Current-clone browser egress only, not server authorization or rollout.

Public Playwright routing/fetch, no proxy, redirects or command retries:
https://playwright.dev/python/docs/api/class-route#route-fetch
No raw request/body/query/console, credentials, trace or screenshots are saved.
"""
from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
import re
from typing import Literal
from uuid import UUID
from urllib.parse import parse_qs, urlsplit

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import Locator, Page, Route, sync_playwright

from tests.support.frontend_p2_accounts import SeedAccount
from tests.support.frontend_p2_binding import NONCE_HEADER, verify_attestation
from tests.support.frontend_p2_canary import (
    VerifiedDataProbe,
    _require_child,
    require_data_probe,
    _bound_json,
)

from tests.support.frontend_remaining_targets import (
    REMAINING_PAGES, STORE_PATH, ManualTarget, TaskTarget, RemainingTargets, remaining_request, manual_pk_operand,
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
    invalid_region_next: bool = False
    mutated: int = 0
    error_next: tuple[str, str, int, int] | None = None
    responses: list[tuple[str, str, int]] = field(default_factory=list)
    injected: list[tuple[str, str, int]] = field(default_factory=list)
    remaining: RemainingTargets | None = field(default=None, repr=False)
    _store_origin: str | None = field(default=None, repr=False)
    _tickets: dict[str, tuple[str, str, int]] = field(default_factory=dict, repr=False)
    _tasks: dict[str, TaskTarget] = field(default_factory=dict, repr=False)
    _products: tuple[ManualTarget, ...] = field(default=(), repr=False)
    _observations: dict = field(default_factory=dict, repr=False)
    native_stages: dict[str, int] = field(default_factory=lambda: {
        "entry": 0, "bound": 0, "header_requested": 0,
        "header_read": 0, "accepted": 0, "continued": 0,
    }, repr=False)

    def __post_init__(self) -> None:
        require_data_probe(self.probe)
        if not (self.username.startswith("test_p2_") and self.shop_id.isdigit()
                and self.service_id.startswith("TEST_") and self.enum_value.startswith("TEST_")):
            raise ValueError(REFUSAL)

    def _profile_metadata(self) -> None:
        child = self.probe.child
        if child.collaborator_profile != "remaining-console-v1":
            raise ValueError(REFUSAL)
        data = _bound_json(child, self.probe.prefix + STORE_PATH, {NONCE_HEADER: child.nonce})
        counts = {"optionsCount", "putCount", "completePuts", "abortedPuts", "bytesReceived", "lastSize"}
        flags = {"paused", "credentialsReceived"}
        if not isinstance(data, dict) or set(data) != counts | flags | {"profile", "origin", "lastSha256"} or data["profile"] != "remaining-console-v1":
            raise ValueError(REFUSAL)
        if any(type(data[key]) is not int or data[key] < 0 for key in counts) or any(type(data[key]) is not bool for key in flags):
            raise ValueError(REFUSAL)
        if not isinstance(data["lastSha256"], str) or not re.fullmatch(r"(?:[a-f0-9]{64})?", data["lastSha256"]):
            raise ValueError(REFUSAL)
        origin = data["origin"]
        parsed = urlsplit(origin) if isinstance(origin, str) else None
        if (not parsed or parsed.scheme != "http" or parsed.hostname != "127.0.0.1"
                or not parsed.port or parsed.port == child.port or origin != f"http://127.0.0.1:{parsed.port}"
                or self._store_origin not in (None, origin)):
            raise ValueError(REFUSAL)
        self._store_origin = origin
        self._observations = {key: data[key] for key in counts | flags | {"lastSha256"}}

    def _validate_remaining(self, method: str = "", path: str = "", body: object = None, query: dict | None = None) -> None:
        t = self.remaining
        if not isinstance(t, RemainingTargets):
            raise ValueError(REFUSAL)
        value, binding = require_data_probe(self.probe)
        # Actual original-nonce-bound fixed metadata, never caller dataclass state.
        self._profile_metadata()
        from sqlalchemy import create_engine, select
        from sqlalchemy.orm import sessionmaker
        from tests.support.frontend_p2_binding import checked_session
        from tts_erp_v2.accounts.models import User
        from tts_erp_v2.db.models.commerce import ChannelAccount, ChannelProduct
        from tts_erp_v2.db.models.publishing import VideoPublishTask

        engine = create_engine(value)
        try:
            with checked_session(binding, engine, sessionmaker(bind=engine)) as session:
                shops = list(session.scalars(select(ChannelAccount).where(ChannelAccount.id.in_(t.shop_pks))))
                products = list(session.scalars(select(ChannelProduct).where(ChannelProduct.id.in_(t.product_pks))))
                if ({row.id for row in shops} != t.shop_pks or any(row.platform != "tiktok" or row.account_name != t.marker for row in shops)
                        or {row.id for row in products} != t.product_pks or any(row.shop_pk not in t.shop_pks or row.title != t.marker for row in products)):
                    raise ValueError(REFUSAL)
                self._products = tuple(ManualTarget(row.id, row.shop_pk, row.spu_id) for row in products)
                if method == "POST" and path == "/v2/reporting/manual-costs":
                    if not isinstance(body, dict) or not isinstance(body.get("spu_id"), str):
                        raise ValueError(REFUSAL)
                    operands = {key: manual_pk_operand(body[key]) for key in ("spu_pk", "shop_pk") if key in body}
                    if any(value is None for value in operands.values()):
                        raise ValueError(REFUSAL)
                    candidates = list(session.scalars(select(ChannelProduct).where(ChannelProduct.spu_id == body["spu_id"])))
                    # Each explicit operand restricts the same SQL row, not flat sets.
                    candidates = [row for row in candidates if ("spu_pk" not in operands or row.id == operands["spu_pk"])
                                  and ("shop_pk" not in operands or row.shop_pk == operands["shop_pk"])]
                    if not candidates or any(ManualTarget(row.id, row.shop_pk, row.spu_id) not in self._products for row in candidates):
                        raise ValueError(REFUSAL)
                if method == "GET" and path in {"/v2/commerce/channel-products", "/v2/reporting/missing-cost-products", "/v2/reporting/manual-costs"}:
                    scopes = (query or {}).get("shop_pk", [])
                    if len(scopes) != 1 or not scopes[0].isdigit() or int(scopes[0]) not in t.shop_pks:
                        raise ValueError(REFUSAL)
                    visible = set(session.scalars(select(ChannelProduct.id).where(ChannelProduct.shop_pk == int(scopes[0]))))
                    if not visible <= t.product_pks:
                        raise ValueError(REFUSAL)
                actor = session.scalar(select(User.id).where(User.username == self.username))
                tasks = list(session.scalars(select(VideoPublishTask).where(VideoPublishTask.public_id.in_([UUID(x) for x in t.task_ids]))))
                if {str(row.public_id) for row in tasks} != t.task_ids or any(actor is None or not self.username.startswith("test_p2_") or row.created_by_user_id != actor
                        or row.original_filename != t.marker + ".mp4" or row.caption != t.marker for row in tasks):
                    raise ValueError(REFUSAL)
                self._tasks = {str(row.public_id): TaskTarget(str(row.public_id), str(row.client_request_id), row.size_bytes,
                               row.original_filename, row.caption, row.stage) for row in tasks}
                if method == "GET" and path in {"/v2/video-publish/tasks", "/v2/video-publish/tasks/current"}:
                    # Global reads are finite UI requirements, never response-filtered.
                    visible = {str(value) for value in session.scalars(select(VideoPublishTask.public_id))}
                    if not visible <= t.task_ids:
                        raise ValueError(REFUSAL)
                _require_child(self.probe.child)
        finally:
            engine.dispose()

    def observe_store(self) -> dict:
        self._validate_remaining()
        return dict(self._observations)

    def store_control(self, page: Page, action: Literal["pause", "release"]) -> dict:
        if action not in ("pause", "release"):
            raise ValueError(REFUSAL)
        self.observe_store()
        result = page.evaluate("""async path => {
            const response = await fetch(path, {method: 'POST', body: '{}', headers: {'Content-Type': 'application/json'}});
            return {status: response.status, ok: response.ok};
        }""", self.probe.prefix + STORE_PATH + "/" + action)
        if result != {"status": 200, "ok": True}:
            raise ValueError(REFUSAL)
        return self.observe_store()

    def _native_upload(self, route: Route) -> bool:
        request = route.request
        if request.url not in self._tickets:
            return False
        self.native_stages["entry"] += 1
        self._validate_remaining()
        self.native_stages["bound"] += 1
        task_id, client_id, size = self._tickets[request.url]
        task = self._tasks.get(task_id)
        target = urlsplit(request.url)
        if (task is None or task.client_id != client_id or task.size != size or task.stage != "awaiting_upload"
                or request.redirected_from is not None or f"{target.scheme}://{target.netloc}" != self._store_origin or request.method not in {"PUT", "OPTIONS"}):
            raise ValueError(REFUSAL)
        self.native_stages["header_requested"] += 1
        headers = request.all_headers()
        self.native_stages["header_read"] += 1
        if any(key in headers for key in ("cookie", "authorization", "x-api-key", "x-requested-with", "x-csrf-token")):
            raise ValueError(REFUSAL)
        if headers.get("origin") != self.probe.child.base:
            raise ValueError(REFUSAL)
        if request.method == "PUT" and (headers.get("content-type") != "video/mp4" or not 0 < size <= 32 * 1024 * 1024
                or headers.get("content-length") != str(size) or "transfer-encoding" in headers):
            raise ValueError(REFUSAL)
        if request.method == "OPTIONS" and (headers.get("access-control-request-method") != "PUT" or
                set(part.strip().lower() for part in headers.get("access-control-request-headers", "").split(",") if part.strip()) - {"content-type"}):
            raise ValueError(REFUSAL)
        self.native_stages["accepted"] += 1
        route.continue_()  # Native browser owns CORS/streaming/progress/abort.
        self.native_stages["continued"] += 1
        return True

    def _learn_response(self, method: str, path: str, body: object, response) -> None:
        if self.remaining is None or not 200 <= response.status < 300 or method != "POST":
            return
        t = self.remaining
        create = path == "/v2/video-publish/tasks"
        reissue = path.startswith("/v2/video-publish/tasks/") and path.endswith("/upload-url")
        if create or reissue:
            data = response.json()
            task_id, client_id = data["taskId"], data["clientRequestId"]
            if (str(UUID(task_id)) != task_id or str(UUID(client_id)) != client_id or not isinstance(body, dict)
                    or create and client_id != body["clientRequestId"]
                    or reissue and (task_id != path.split("/")[4] or task_id not in self._tasks or self._tasks[task_id].client_id != client_id)):
                raise ValueError(REFUSAL)
            self.remaining = replace(t, task_ids=t.task_ids | {task_id})
            self._validate_remaining()
            task = self._tasks[task_id]
            if task.client_id != client_id or (create and (task.size != body["sizeBytes"] or task.filename != body["filename"] or task.caption != body["caption"])):
                raise ValueError(REFUSAL)
            upload = data.get("upload")
            if upload is not None:
                url = upload["url"]
                parsed = urlsplit(url)
                if (f"{parsed.scheme}://{parsed.netloc}" != self._store_origin or parsed.query or parsed.fragment
                        or not re.fullmatch(r"/TEST_upload/[A-Za-z0-9_-]{32}", parsed.path)
                        or upload.get("method") != "PUT" or upload.get("headers") != {"Content-Type": "video/mp4"}):
                    raise ValueError(REFUSAL)
                self._tickets = {key: value for key, value in self._tickets.items() if value[0] != task_id}
                self._tickets[url] = (task_id, client_id, task.size)
        elif path.startswith("/v2/video-publish/tasks/") and path.endswith(("/cancel", "/confirm-upload")):
            task_id = path.split("/")[4]
            self._tickets = {key: value for key, value in self._tickets.items() if value[0] != task_id}

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
        if self.remaining is not None and not path.startswith("/static/"):
            self._validate_remaining(method, path, body, query)
            if remaining_request(self.remaining, method, path, query, body):
                return path
            if method == "GET" and path == "/v2/auth/login" and query.get("next", []) in ([prefix + page] for page in REMAINING_PAGES) and set(query) == {"next"}:
                return path
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
                services = {self.service_id, self.service_id + "_conflict"} if register else {self.service_id}
                safe = safe and ("service_id" not in body or body["service_id"] in services)
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

    def inject_next(self, method: str, path: str, status: int, *, retry_after: int = 0) -> None:
        read = method == "GET" and path in {"/v2/commerce/channel-accounts", "/v2/config/enum-map/list"} and status == 429 and retry_after > 0
        write = status == 500 and retry_after == 0 and (method == "PUT" and path == "/v2/config/enum-map" or method == "PATCH" and path in {f"/v2/admin/shops/{pk}" for pk in self.shop_pks})
        if not (read or write) or self.error_next is not None:
            raise ValueError(REFUSAL)
        self.error_next = (method, path, status, retry_after)

    def request_body(self, method: str, path: str, body: object) -> object:
        if self.invalid_region_next and method == "POST" and path == "/v2/admin/shops/register":
            if not isinstance(body, dict):
                raise ValueError(REFUSAL)
            body = {**body, "region": "TEST_region_exceeds_16"}
            self.check_request(method, self.probe.child.base + self.probe.prefix + path, body)
            self.invalid_region_next = False
            self.mutated += 1
        return body

    def handle(self, route: Route) -> None:
        if self.closing:
            route.abort()
            return
        try:
            request = route.request
            if self._native_upload(route):
                return
            original = request.post_data_json
            path = self.check_request(request.method, request.url, original)
            body = self.request_body(request.method, path, original)
            wire_body = request.post_data if body is original else json.dumps(body, ensure_ascii=False)
            self.calls.append((request.method, path, request.headers.get("x-requested-with") == "tts-erp"))
            options = {}
            if path.startswith(STORE_PATH):
                options["headers"] = {**request.all_headers(), NONCE_HEADER: self.probe.child.nonce}
            response = route.fetch(post_data=wire_body, max_redirects=0, max_retries=0, timeout=10_000, **options)
            verify_attestation(self.probe.child.attestation, self.probe.child.binding,
                               nonce=self.probe.child.nonce, child_pid=self.probe.child.pid,
                               child_start=self.probe.child.start, http_nonce=response.headers.get(NONCE_HEADER))
            _require_child(self.probe.child)
            self.responses.append((request.method, path, response.status))
            self._learn_response(request.method, path, body, response)
            if self.error_next and self.error_next[:2] == (request.method, path) and 200 <= response.status < 300:
                _method, _path, status, retry = self.error_next
                self.error_next = None
                self.injected.append((request.method, path, status))
                headers = {NONCE_HEADER: self.probe.child.nonce, "content-type": "application/json"}
                if retry:
                    headers["retry-after"] = str(retry)
                route.fulfill(status=status, headers=headers, body='{"detail":"TEST_wire_failure"}')
            elif self.drop_next == (request.method, path) and 200 <= response.status < 300:
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
    enum_value: str, width: int, remaining: RemainingTargets | None = None,
) -> Iterator[tuple[Page, BrowserFence]]:
    gate = BrowserFence(probe, username=account.username, shop_id=shop_id,
                        service_id=service_id, enum_value=enum_value, remaining=remaining)
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
