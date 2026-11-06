"""Native runtime-config acceptance on the current owned ephemeral clone.

The test-local fence grants only this fixture's config key, auth and release
assets. It does not extend the shared P2 allowlist. No traces, videos, console
messages, credentials or raw HTTP bodies are recorded; screenshots contain only
the benign TEST config. The isolated wrapper owns all database teardown.
"""

from __future__ import annotations

import json
from tests.support.browser_runtime import borrowed_browser

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlsplit
from uuid import uuid4

import httpx
import pytest
from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import Locator, Page, Route, expect
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from tests.scripts.test_frontend_remaining_live_api import (
    CSRF,
    OwnedClone,
    login,
    owned,
)
from tests.scripts.test_frontend_remaining_live_api import clone as owned_clone
from tests.support.frontend_p2_binding import (
    NONCE_HEADER,
    checked_session,
    verify_attestation,
)
from tests.support.frontend_p2_browser import fill_sensitive
from tests.support.frontend_p2_canary import require_data_probe
from tts_erp_v2.db.models.config import RuntimeConfigItem, RuntimeConfigRevision

clone = owned_clone
pytestmark = [pytest.mark.domain_browser, pytest.mark.requires_browser]

SCREENSHOTS = Path(__file__).resolve().parents[2] / "logs/TEST_runtime_config/screenshots"
PAGE = "/v2/pages/runtime-configs"
ITEMS = "/v2/config/runtime/items"
BIG = 900719925474099312345
REFUSAL = "TEST runtime browser request refused"


@dataclass(frozen=True)
class ConfigTarget:
    key: str
    title: str

    @property
    def path(self) -> str:
        return ITEMS + "/" + self.key

    def payload(self, counter: int) -> dict[str, str | int]:
        return {"label": self.title, "counter": counter}


def config_state(clone: OwnedClone, target: ConfigTarget) -> tuple:
    """Independent verified SQL transaction, never an API DTO/identity map."""
    clone.child.require_live()
    with checked_session(clone.child.binding, clone.engine, clone.factory) as session:
        item = session.get(RuntimeConfigItem, target.key)
        if item is None or item.display_name != target.title:
            raise ValueError("TEST runtime fixture ownership refused")
        revisions = session.execute(
            select(
                RuntimeConfigRevision.version,
                RuntimeConfigRevision.payload,
                RuntimeConfigRevision.comment,
                RuntimeConfigRevision.created_at,
            )
            .where(RuntimeConfigRevision.config_key == target.key)
            .order_by(RuntimeConfigRevision.version)
        )
        result = (
            item.draft_version, item.published_version, item.draft_payload,
            item.updated_at, tuple(tuple(row) for row in revisions),
        )
    clone.child.require_live()
    return result


@pytest.fixture
def config_target(frontend_build: None, clone: OwnedClone) -> ConfigTarget:
    """Create and publish only a fresh TEST key through the real authenticated API."""
    require_data_probe(clone.probe)
    target = ConfigTarget("test_runtime_browser_" + uuid4().hex, "TEST runtime " + uuid4().hex)
    with httpx.Client(
        base_url=clone.child.base, trust_env=False, timeout=5,
        follow_redirects=False, max_redirects=0,
    ) as client:
        login(clone, client, "operator")
        require_data_probe(clone.probe)
        detail = owned(clone, client.post(
            clone.prefix + ITEMS,
            json={
                "configKey": target.key, "displayName": target.title,
                "jsonSchema": {"type": "object"}, "draftPayload": target.payload(BIG),
            }, headers=CSRF,
        ), 201).json()
        assert detail["configKey"] == target.key
        assert detail["draftVersion"] == 1
        require_data_probe(clone.probe)
        owned(clone, client.post(
            clone.prefix + target.path + "/publish",
            json={"expectedDraftVersion": 1, "comment": "TEST initial publication"},
            headers=CSRF,
        ), 200)
        owned(clone, client.post(clone.prefix + "/v2/auth/logout", headers=CSRF), 204)
    state = config_state(clone, target)
    assert state[:3] == (2, 1, None)
    assert len(state[4]) == 1 and state[4][0][1] == target.payload(BIG)
    return target


@dataclass
class RuntimeFence:
    clone: OwnedClone = field(repr=False)
    target: ConfigTarget
    calls: list[tuple[str, str]] = field(default_factory=list)
    responses: list[tuple[str, str, int]] = field(default_factory=list)
    sources: list[tuple[str, int | None]] = field(default_factory=list)
    violations: list[str] = field(default_factory=list)
    closing: bool = False

    def check(self, method: str, url: str, body: object) -> str:
        child = self.clone.child
        child.require_live()
        target, base = urlsplit(url), urlsplit(child.base)
        if (
            (target.scheme, target.hostname, target.port) != (base.scheme, base.hostname, base.port)
            or target.username or target.password or target.fragment
        ):
            raise ValueError(REFUSAL)
        path = target.path
        if "%" in path or "\\" in path or any(part in {"", ".", ".."} for part in path.split("/")[1:]):
            raise ValueError(REFUSAL)
        prefix = self.clone.prefix
        if prefix and not path.startswith(prefix + "/"):
            raise ValueError(REFUSAL)
        path = path[len(prefix):]
        query = parse_qs(target.query, keep_blank_values=True)
        if method == "GET":
            if not query and (
                path in {PAGE, "/v2/auth/me", "/v2/auth/login", ITEMS, self.target.path, self.target.path + "/revisions"}
                or path.startswith(("/static/frontend/", "/static/fonts/"))
                or path in {"/static/css/tokens.css", "/static/css/common.css", "/static/vendor/bootstrap.min.css"}
            ):
                return path
            if path == "/v2/auth/login" and query == {"next": [prefix + PAGE]}:
                return path
        # Fresh authenticated canary and original fixture row before every POST/PUT,
        # including read-only preview; a TEST-looking URL alone grants nothing.
        require_data_probe(self.clone.probe)
        config_state(self.clone, self.target)
        if method == "POST" and not query and path == "/v2/auth/login":
            account = self.clone.accounts["operator"]
            if isinstance(body, dict) and set(body) == {"username", "password", "next"} and body["username"] == account.username and body["password"] == account.password and body["next"] == prefix + PAGE:
                return path
        if method == "POST" and not query and path == "/v2/auth/logout" and body is None:
            return path
        if not query and isinstance(body, dict):
            draft = method == "PUT" and path == self.target.path + "/draft"
            review = method == "POST" and path == self.target.path + "/preview"
            publish = method == "POST" and path == self.target.path + "/publish"
            fields = {"expectedDraftVersion", "payload"} if draft else {
                "expectedDraftVersion", "expectedPublishedVersion", "payload", "sourceRevisionVersion",
            } | ({"comment"} if publish else set())
            if (draft or review or publish) and set(body) == fields:
                payload = body["payload"]
                safe_payload = isinstance(payload, dict) and set(payload) == {"label", "counter"} and payload.get("label") == self.target.title and type(payload.get("counter")) is int and payload["counter"] in {BIG, BIG + 1, BIG + 2}
                safe_versions = type(body["expectedDraftVersion"]) is int and body["expectedDraftVersion"] >= 0
                if not draft:
                    safe_versions = safe_versions and type(body["expectedPublishedVersion"]) is int and body["expectedPublishedVersion"] >= 1 and body["sourceRevisionVersion"] in (None, 1)
                safe_comment = not publish or body["comment"] in (None, "TEST atomic publication", "TEST historical publication")
                if safe_payload and safe_versions and safe_comment:
                    return path
        raise ValueError(REFUSAL)

    def handle(self, route: Route) -> None:
        if self.closing:
            route.abort()
            return
        try:
            request = route.request
            body = request.post_data_json
            path = self.check(request.method, request.url, body)
            if request.method != "GET" and path != "/v2/auth/login" and request.headers.get("x-requested-with") != "tts-erp":
                raise ValueError(REFUSAL)
            self.calls.append((request.method, path))
            response = route.fetch(max_redirects=0, max_retries=0, timeout=10_000)
            child = self.clone.child
            verify_attestation(
                child.attestation, child.binding, nonce=child.nonce,
                child_pid=child.pid, child_start=child.start,
                http_nonce=response.headers.get(NONCE_HEADER),
            )
            child.require_live()
            self.responses.append((request.method, path, response.status))
            if path in {self.target.path + "/preview", self.target.path + "/publish"}:
                self.sources.append((path.rsplit("/", 1)[1], body["sourceRevisionVersion"]))
            route.fulfill(response=response)
        except (ValueError, TypeError, KeyError, AttributeError, OSError, PlaywrightError, SQLAlchemyError):
            self.violations.append(REFUSAL)
            route.abort("blockedbyclient")

    def writes(self) -> list[tuple[str, str]]:
        return [(method, path) for method, path in self.calls if path.startswith(ITEMS) and (method, path) != ("POST", self.target.path + "/preview") and method != "GET"]


@contextmanager
def runtime_browser(clone: OwnedClone, target: ConfigTarget, width: int) -> Iterator[tuple[Page, RuntimeFence]]:
    require_data_probe(clone.probe)
    config_state(clone, target)
    gate = RuntimeFence(clone, target)
    # No reused context, service worker, external network, websocket or fallback.
    with borrowed_browser() as browser:
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
            assert not gate.violations, REFUSAL
        finally:
            gate.closing = True
            context.close()


def focused_view(page: Page, target: ConfigTarget, title: str) -> None:
    heading = page.get_by_role("heading", name=target.title + " / " + title, exact=True)
    expect(heading).to_be_visible()
    expect(heading).to_be_focused()


def editor_text(page: Page) -> Locator:
    section = page.get_by_role("region", name="配置内容", exact=True)
    section.get_by_text("无障碍 JSON 文本编辑", exact=True).click()
    text = section.get_by_label("配置内容 JSON 文本", exact=True)
    expect(text).to_be_editable()
    return text


def capture(page: Page, clone: OwnedClone, width: int, stage: str) -> None:
    page.evaluate("document.fonts.ready")
    assert page.evaluate("document.documentElement.scrollWidth <= document.documentElement.clientWidth"), "TEST runtime full-page horizontal overflow"
    SCREENSHOTS.mkdir(parents=True, exist_ok=True)
    label = "tts" if clone.prefix else "root"
    page.screenshot(path=str(SCREENSHOTS / f"TEST_runtime_{label}_{width}_{stage}.png"), full_page=True)


@pytest.mark.parametrize("width", [390, 1440, 2560])
def test_runtime_native_review_atomic_publish_stash_and_history(
    frontend_build: None, clone: OwnedClone, config_target: ConfigTarget, width: int,
) -> None:
    target = config_target
    initial = config_state(clone, target)
    with runtime_browser(clone, target, width) as (page, gate):
        url = clone.child.base + clone.prefix + "/v2/auth/login?" + urlencode({"next": clone.prefix + PAGE})
        response = page.goto(url, wait_until="networkidle")
        assert response is not None and response.status == 200
        expect(page.get_by_role("form", name="登录表单", exact=True)).to_be_visible()
        account = clone.accounts["operator"]
        page.get_by_label("用户名", exact=True).fill(account.username)
        fill_sensitive(page.get_by_label("密码", exact=True), account.password)
        page.get_by_label("密码", exact=True).press("Enter")
        page.wait_for_url(clone.child.base + clone.prefix + PAGE)
        expect(page.get_by_role("heading", name="配置列表", exact=True)).to_be_visible()
        expect(page.get_by_role("button", name="退出登录", exact=True)).to_be_visible()
        page.get_by_role("button", name="查看与修改 " + target.title, exact=True).click()
        focused_view(page, target, "配置概览")
        capture(page, clone, width, "overview")

        page.get_by_role("button", name="修改配置", exact=True).click()
        focused_view(page, target, "修改配置")
        editor_text(page).fill(json.dumps(target.payload(BIG + 1), ensure_ascii=False, indent=2))
        page.get_by_role("button", name="检查并发布", exact=True).click()
        focused_view(page, target, "检查发布")
        expect(page.get_by_role("heading", name="检查本次修改", exact=True)).to_be_visible()
        expect(page.get_by_label("增加和删除的内容", exact=True)).to_contain_text(str(BIG + 1))
        # Preview is read-only in both the network ledger and independent SQL.
        assert gate.writes() == []
        assert config_state(clone, target) == initial
        expect(page.get_by_label("配置内容 JSON 文本", exact=True)).to_have_count(0)
        page.get_by_text("查看当前生效内容和准备发布的完整内容", exact=True).click()
        candidate = page.locator("section").filter(has=page.get_by_role("heading", name="准备发布的内容", exact=True)).last
        expect(candidate.locator("pre")).to_contain_text(str(BIG + 1))
        capture(page, clone, width, "review")
        page.get_by_label("发布说明", exact=True).fill("TEST atomic publication")
        page.get_by_role("button", name="确认发布", exact=True).click()
        focused_view(page, target, "配置概览")
        expect(page.get_by_text("当前已发布 v2", exact=True)).to_be_visible()
        expect(page.get_by_role("button", name="修改配置", exact=True)).to_be_enabled()
        assert gate.writes() == [("POST", target.path + "/publish")]
        state = config_state(clone, target)
        assert state[:3] == (initial[0] + 1, 2, None)
        assert len(state[4]) == 2 and state[4][1][:3] == (2, target.payload(BIG + 1), "TEST atomic publication")
        assert type(state[4][1][1]["counter"]) is int

        # Stashing is optional and preserves the current published revision.
        page.get_by_role("button", name="修改配置", exact=True).click()
        focused_view(page, target, "修改配置")
        editor_text(page).fill(json.dumps(target.payload(BIG + 2), ensure_ascii=False))
        page.get_by_role("button", name="暂存草稿", exact=True).click()
        expect(page.get_by_text("当前正在编辑已保存、尚未发布的草稿。", exact=True)).to_be_visible()
        stashed = config_state(clone, target)
        assert stashed[:3] == (state[0] + 1, 2, target.payload(BIG + 2))
        assert stashed[4] == state[4]
        page.get_by_role("button", name="返回配置概览", exact=True).click()
        focused_view(page, target, "配置概览")
        live_content = page.get_by_role("region", name="当前生效内容", exact=True)
        live_content.get_by_text("无障碍 JSON 文本编辑", exact=True).click()
        published_text = live_content.get_by_label("当前生效内容 JSON 文本", exact=True)
        expect(published_text).not_to_be_editable()
        assert json.loads(published_text.input_value()) == target.payload(BIG + 1)

        page.get_by_role("button", name="查看发布历史", exact=True).click()
        focused_view(page, target, "发布历史")
        before_history_writes = gate.writes()
        page.get_by_role("button", name="使用 v1 内容", exact=True).click()
        focused_view(page, target, "修改配置")
        assert json.loads(editor_text(page).input_value()) == target.payload(BIG)
        assert gate.writes() == before_history_writes
        assert config_state(clone, target) == stashed
        page.get_by_role("button", name="检查并发布", exact=True).click()
        focused_view(page, target, "检查发布")
        assert gate.writes() == before_history_writes
        assert config_state(clone, target) == stashed
        assert gate.sources[-1] == ("preview", 1)
        capture(page, clone, width, "history_review")
        page.get_by_label("发布说明", exact=True).fill("TEST historical publication")
        page.get_by_role("button", name="确认发布", exact=True).click()
        focused_view(page, target, "配置概览")
        expect(page.get_by_text("当前已发布 v3", exact=True)).to_be_visible()
        expect(page.get_by_role("button", name="修改配置", exact=True)).to_be_enabled()
        final = config_state(clone, target)
        assert final[:3] == (stashed[0] + 1, 3, None)
        assert len(final[4]) == 3 and final[4][2][:3] == (3, target.payload(BIG), "TEST historical publication")
        assert final[4][:2] == state[4]
        assert gate.sources == [("preview", None), ("publish", None), ("preview", 1), ("publish", 1)]
        assert gate.writes() == [
            ("POST", target.path + "/publish"), ("PUT", target.path + "/draft"),
            ("POST", target.path + "/publish"),
        ]
        assert all(status == 200 for method, path, status in gate.responses if path.startswith(ITEMS))
        assert sum(method == "POST" and path == "/v2/auth/login" for method, path in gate.calls) == 1
        capture(page, clone, width, "published")
        page.get_by_role("button", name="退出登录", exact=True).click()
        page.wait_for_url(clone.child.base + clone.prefix + "/v2/auth/login")
