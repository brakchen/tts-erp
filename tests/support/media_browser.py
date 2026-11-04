"""Compiled media UI + canonical app/SQL, with intercepted HTTP and fake storage.

The browser never reaches a real HTTP listener or object store. This is an
interaction and persistence check, not evidence about MinIO or native CORS.
"""
from __future__ import annotations

import re
from re import Pattern
from collections import Counter
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

from fastapi.testclient import TestClient
from playwright.sync_api import Route

from tts_erp_v2.media.object_store import ObjectNotFound

APP = "https://media-erp.test"
STORE = "https://media-objects.test"
ASSETS = "/v2/media/assets"
HUMANS = "/v2/media/digital-humans"
PAGES = {"/v2/pages/media-assets", "/v2/pages/digital-humans"}
ME = "/v2/auth/me"
LOGIN = "/v2/auth/login"


class MediaBytes:
    """In-memory object payloads keyed by the test app's opaque capabilities."""

    default_expiry = timedelta(minutes=15)

    def __init__(self) -> None:
        self.tickets: dict[str, tuple[str, str]] = {}
        self.previews: dict[str, str] = {}
        self.objects: dict[str, tuple[str, bytes]] = {}
        self.puts = 0
        self.break_next_preview = False

    def bucket(self) -> str:
        return "TEST_media"

    def presign_put(self, key: str, content_type: str) -> str:
        path = "/TEST_put/" + uuid4().hex
        self.tickets[path] = (key, content_type)
        return STORE + path

    def presign_get(self, key: str) -> tuple[str, datetime]:
        path = "/TEST_get/" + uuid4().hex
        self.previews[path] = key
        return STORE + path, datetime.now(UTC) + self.default_expiry

    def public_url_or_none(self, key: str) -> None:
        return None

    def stat(self, key: str) -> dict[str, str | int]:
        item = self.objects.get(key)
        if item is None:
            raise ObjectNotFound("TEST_object_missing")
        content_type, payload = item
        return {"size": len(payload), "content_type": content_type}

    def remove(self, key: str) -> None:
        self.objects.pop(key, None)

    def handle(self, route: Route, path: str) -> bool:
        request = route.request
        headers = request.all_headers()
        if {"cookie", "authorization", "x-api-key", "x-requested-with"} & headers.keys():
            return False
        ticket = self.tickets.get(path)
        if ticket is not None and request.method in {"OPTIONS", "PUT"}:
            if headers.get("origin") != APP:
                return False
            cors = {
                "access-control-allow-origin": APP,
                "access-control-allow-methods": "PUT",
                "access-control-allow-headers": "content-type",
            }
            if request.method == "PUT":
                key, content_type = ticket
                payload = request.post_data_buffer
                if headers.get("content-type") != content_type or not payload or len(payload) > 2**20:
                    return False
                self.objects[key] = (content_type, payload)
                self.puts += 1
            route.fulfill(status=204, headers=cors)
            return True
        if request.method == "GET" and path in self.previews:
            item = self.objects.get(self.previews[path])
            if self.break_next_preview or item is None:
                self.break_next_preview = False
                route.fulfill(status=403)
            else:
                content_type, payload = item
                route.fulfill(status=200, content_type=content_type, body=payload)
            return True
        return False


class MediaBrowserBridge:
    """An exact-purpose gate for current-run media tests.

    IDs are learned only from successful create responses in this fixture.
    Forwarded cookie state is exclusively the browser's own HttpOnly cookie.
    """

    def __init__(
        self, client: TestClient, store: MediaBytes, marker: str, username: str, *, prefix: str = "",
    ) -> None:
        self.client = client
        self.store = store
        self.marker = marker
        self.username = username
        self.prefix = prefix
        self.assets: set[int] = set()
        self.humans: set[int] = set()
        self.calls: Counter[tuple[str, str]] = Counter()
        self.refusals: list[str] = []
        self.fail_next: tuple[str, str, int] | None = None
        self.hold_next: tuple[str, Pattern[str]] | None = None
        self.held: tuple[Route, str, str] | None = None
        self.closing = False

    def fail(self, method: str, path: str, status: int) -> None:
        assert self.fail_next is None
        self.fail_next = method, path, status

    def hold(self, method: str, path: Pattern[str]) -> None:
        assert self.hold_next is None and self.held is None
        self.hold_next = method, path

    def release(self, *, status: int | None = None) -> None:
        assert self.held is not None
        route, path, target = self.held
        self.held = None
        if status is None:
            self.forward(route, path, target)
        else:
            route.fulfill(status=status, json={"detail": "TEST_injected_failure"})

    def _owned_name(self, value: object) -> bool:
        return isinstance(value, str) and value.startswith(self.marker + "_")

    def _owned_id(self, value: object, ids: set[int]) -> bool:
        if type(value) is int:
            return value in ids
        return isinstance(value, str) and bool(re.fullmatch(r"[1-9][0-9]{0,18}", value)) and int(value) in ids

    def allows(self, method: str, path: str, query: dict, body: object) -> bool:
        if method == "GET" and path in PAGES:
            id_name, ids = ("asset", self.assets) if path.endswith("media-assets") else ("human", self.humans)
            return not query or (
                set(query) == {id_name} and len(query[id_name]) == 1 and self._owned_id(query[id_name][0], ids)
            )
        if method == "GET" and path == LOGIN:
            return not query or (
                set(query) == {"next"} and query["next"] in ([self.prefix + p] for p in PAGES | {ME})
            )
        if method == "GET" and not query and (
            path == ME or path.startswith(("/static/frontend/", "/static/fonts/"))
            or path == "/static/favicon.svg"
        ):
            return True
        if method == "POST" and not query and path == LOGIN:
            return isinstance(body, dict) and body.get("username") == self.username
        if method == "GET" and path in {ASSETS, HUMANS}:
            if set(query) - {"keyword", "kind", "limit", "offset", "status", "tag", "digitalHumanId", "excludeDigitalHumanId"}:
                return False
            return all(
                len(values) == 1 and (key not in {"digitalHumanId", "excludeDigitalHumanId"}
                or self._owned_id(values[0], self.humans))
                for key, values in query.items()
            )
        if query:
            return False
        if method == "POST" and isinstance(body, dict):
            if path == ASSETS + "/upload-url":
                return self._owned_name(body.get("filename"))
            if path == HUMANS:
                return self._owned_name(body.get("name"))
        asset = re.fullmatch(rf"{ASSETS}/([1-9][0-9]{{0,18}})(/confirm)?", path)
        if asset and self._owned_id(asset[1], self.assets):
            if asset[2]:
                return method == "POST"
            return method in {"GET", "DELETE"} or (
                method == "PATCH" and isinstance(body, dict)
                and ("filename" not in body or self._owned_name(body["filename"]))
            )
        human = re.fullmatch(rf"{HUMANS}/([1-9][0-9]{{0,18}})(/assets(?:/([1-9][0-9]{{0,18}}))?)?", path)
        if not human or not self._owned_id(human[1], self.humans):
            return False
        if human[3]:
            return method == "DELETE" and self._owned_id(human[3], self.assets)
        if human[2]:
            return method == "POST" and isinstance(body, dict) and self._owned_id(body.get("assetId"), self.assets)
        return method in {"GET", "DELETE"} or (
            method == "PATCH" and isinstance(body, dict)
            and ("name" not in body or self._owned_name(body["name"]))
        )

    def forward(self, route: Route, path: str, target: str) -> None:
        request = route.request
        headers = {
            name: value for name, value in request.all_headers().items()
            if name in {"accept", "content-type", "cookie", "user-agent", "x-requested-with"}
        }
        self.client.cookies.clear()
        try:
            response = self.client.request(
                request.method, target, headers=headers, content=request.post_data_buffer,
            )
        finally:
            self.client.cookies.clear()
        if response.status_code == 201 and request.method == "POST":
            if path == ASSETS + "/upload-url":
                self.assets.add(response.json()["asset_id"])
            elif path == HUMANS:
                self.humans.add(response.json()["digital_human_id"])
        route.fulfill(
            status=response.status_code,
            headers={
                name: value for name, value in response.headers.items()
                if name not in {"content-length", "content-encoding", "transfer-encoding"}
            },
            body=response.content,
        )

    def handle(self, route: Route) -> None:
        if self.closing:
            route.abort()
            return
        request = route.request
        parsed = urlsplit(request.url)
        origin = f"{parsed.scheme}://{parsed.netloc}"
        if origin == STORE and not parsed.query and self.store.handle(route, parsed.path):
            return
        if origin == APP and parsed.path.startswith(self.prefix + "/") and "%" not in parsed.path:
            path = parsed.path[len(self.prefix):]
            query = parse_qs(parsed.query, keep_blank_values=True)
            body = request.post_data_json if request.post_data_buffer else None
            if self.allows(request.method, path, query, body):
                self.calls[request.method, path] += 1
                if self.fail_next and self.fail_next[:2] == (request.method, path):
                    _method, _path, code = self.fail_next
                    self.fail_next = None
                    route.fulfill(status=code, json={"detail": "TEST_injected_failure"})
                    return
                target = parsed.path + ("?" + parsed.query if parsed.query else "")
                if self.hold_next and self.hold_next[0] == request.method and self.hold_next[1].fullmatch(path):
                    self.hold_next = None
                    self.held = route, path, target
                    return
                self.forward(route, path, target)
                return
            self.refusals.append(f"{request.method} {path}")
        else:
            self.refusals.append("unexpected browser origin")
        route.abort("blockedbyclient")
