"""Independent acceptance of the built media pages against the current clone.

Browser navigation, forms, previews and request ordering are real. ERP HTTP is
intercepted and forwarded into the actual app, session auth and SQL; object
storage is a test-only byte store. No deployed service is contacted.
"""
from __future__ import annotations

import json
import re
import struct
import zlib
from collections.abc import Iterator
from contextlib import AbstractContextManager
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from playwright.sync_api import Locator, Page, Response, expect
from render_support import overflow_report
from sqlalchemy import delete, or_, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from tests.support.browser_runtime import borrowed_browser
from tests.support.frontend_p2_binding import (
    checked_session,
    current_binding,
    validate_application,
)
from tests.support.frontend_p2_browser import fill_sensitive
from tests.support.media_browser import (
    APP,
    ASSETS,
    HUMANS,
    LOGIN,
    ME,
    MediaBrowserBridge,
    MediaBytes,
)
from tts_erp_v2.accounts import service
from tts_erp_v2.accounts.models import Role, User
from tts_erp_v2.api.v2 import media
from tts_erp_v2.app import build_app
from tts_erp_v2.db.base import get_engine, get_session_factory
from tts_erp_v2.db.models.media import DigitalHuman, DigitalHumanAsset, MediaAsset

pytestmark = [pytest.mark.domain_browser, pytest.mark.requires_browser]


def png() -> bytes:
    def chunk(kind: bytes, payload: bytes) -> bytes:
        return struct.pack("!I", len(payload)) + kind + payload + struct.pack("!I", zlib.crc32(kind + payload))

    left = b"\x12\x74\xb4" * 128
    right = b"\xf5\xb5\x3a" * 128
    rows = (b"\x00" + left + right) * 64 + (b"\x00" + right + left) * 64
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack("!IIBBBBB", 256, 128, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(rows))
        + chunk(b"IEND", b"")
    )


def db() -> AbstractContextManager[Session]:
    _, binding = current_binding()
    return checked_session(binding, get_engine(), get_session_factory())


@dataclass
class MediaRun:
    page: Page
    gate: MediaBrowserBridge
    marker: str

    def name(self, suffix: str) -> str:
        return self.marker + "_" + suffix

    def goto(self, slug: str) -> Response | None:
        return self.page.goto(f"{APP}{self.gate.prefix}/v2/pages/{slug}")

    def asset_id(self, name: str) -> int:
        with db() as session:
            return session.execute(select(MediaAsset.id).where(MediaAsset.filename == name)).scalar_one()

    def human_id(self, name: str) -> int:
        with db() as session:
            return session.execute(select(DigitalHuman.id).where(DigitalHuman.name == name)).scalar_one()

    def links(self, human_id: int) -> set[int]:
        with db() as session:
            return set(session.scalars(
                select(DigitalHumanAsset.asset_id)
                .join(MediaAsset, MediaAsset.id == DigitalHumanAsset.asset_id)
                .where(DigitalHumanAsset.digital_human_id == human_id, MediaAsset.deleted_at.is_(None))
            ))

    def api(self, method: str, path: str, payload: dict | None = None) -> tuple[int, object]:
        """Arrange owned data through the same authenticated, gated app as the UI."""
        result = self.page.evaluate("""async ({method, path, payload}) => {
            const response = await fetch(path, {
                method, credentials: 'same-origin',
                headers: {'Content-Type': 'application/json', 'X-Requested-With': 'tts-erp'},
                ...(payload === null ? {} : {body: JSON.stringify(payload)}),
            });
            const value = await response.text();
            return {status: response.status, body: value ? JSON.parse(value) : null};
        }""", {"method": method, "path": self.gate.prefix + path, "payload": payload})
        return result["status"], result["body"]

    def seed_asset(
        self, suffix: str, *, tags: list[str] | None = None, description: str = "",
        content_type: str = "image/png", payload: bytes | None = None,
    ) -> tuple[str, int]:
        """Fixtures still issue a real upload ticket, PUT bytes and confirm them."""
        name = self.name(suffix)
        data = png() if payload is None else payload
        status, ticket = self.api("POST", ASSETS + "/upload-url", {
            "filename": name, "contentType": content_type, "sizeBytes": len(data), "tags": tags or [],
        })
        assert status == 201 and isinstance(ticket, dict), ticket
        put_status = self.page.evaluate("""async ({url, headers, payload}) => {
            const response = await fetch(url, {
                method: 'PUT', credentials: 'omit', headers, body: new Uint8Array(payload),
            });
            return response.status;
        }""", {"url": ticket["upload_url"], "headers": ticket["required_headers"], "payload": list(data)})
        assert put_status == 204
        asset_id = ticket["asset_id"]
        assert self.api("POST", f"{ASSETS}/{asset_id}/confirm")[0] == 200
        if description:
            assert self.api("PATCH", f"{ASSETS}/{asset_id}", {"description": description})[0] == 200
        return name, asset_id

    def seed_human(self, suffix: str, *, description: str = "TEST_persona_description") -> tuple[str, int]:
        name = self.name(suffix)
        status, human = self.api("POST", HUMANS, {"name": name, "description": description})
        assert status == 201 and isinstance(human, dict), human
        return name, human["digital_human_id"]

    def link(self, human_id: int, asset_id: int) -> None:
        assert self.api("POST", f"{HUMANS}/{human_id}/assets", {"assetId": asset_id})[0] == 200


def clear_owned(gate: MediaBrowserBridge, user_id: int, username: str) -> None:
    """Discover only this case's uniquely named rows, including partial writes."""
    prefix = gate.marker + "_"
    with db() as session:
        assets = set(session.scalars(select(MediaAsset.id).where(MediaAsset.filename.startswith(prefix, autoescape=True))))
        humans = set(session.scalars(select(DigitalHuman.id).where(DigitalHuman.name.startswith(prefix, autoescape=True))))
        assert gate.assets <= assets and gate.humans <= humans
        links = session.execute(
            select(DigitalHumanAsset.digital_human_id, DigitalHumanAsset.asset_id).where(
                or_(DigitalHumanAsset.digital_human_id.in_(humans), DigitalHumanAsset.asset_id.in_(assets)),
            )
        ).all()
        assert all(human in humans and asset in assets for human, asset in links), "TEST relation escaped its fixture"
        session.execute(delete(DigitalHumanAsset).where(
            DigitalHumanAsset.digital_human_id.in_(humans), DigitalHumanAsset.asset_id.in_(assets),
        ))
        session.execute(delete(DigitalHuman).where(DigitalHuman.id.in_(humans)))
        session.execute(delete(MediaAsset).where(MediaAsset.id.in_(assets)))
        session.execute(delete(User).where(User.id == user_id, User.username == username))
        session.commit()
    service.clear_context_cache()


@pytest.fixture
def media_run(request, frontend_build, monkeypatch) -> Iterator[MediaRun]:
    parameters = getattr(request, "param", ("admin", 1440, ""))
    role, width, prefix = parameters[:3]
    value, binding = current_binding()
    monkeypatch.setenv("TTS_ERP_DB_URL", value)
    monkeypatch.setenv("TTS_ERP_AUTH_MODE", "enforce")
    monkeypatch.setenv("TTS_ERP_SESSION_SECURE", "1")
    monkeypatch.setenv("TTS_ERP_EXTERNAL_PREFIX", prefix)
    monkeypatch.delenv("TTS_ERP_FRONTEND_DEV_ORIGIN", raising=False)
    fixture_id = uuid4()
    # Compact reference content still has a random case owner. Long-name pressure
    # cases keep the full UUID; never use a shared TEST_ cleanup namespace.
    marker = "TEST_" + fixture_id.hex[:8] if parameters[3:] == ("fidelity",) else "TEST_media_" + fixture_id.hex
    client_ip = f"127.{fixture_id.bytes[0]}.{fixture_id.bytes[1]}.{fixture_id.bytes[2] % 254 + 1}"
    username = "test_media_" + uuid4().hex[:16]
    password = "TEST_Media_9!" + uuid4().hex
    store = MediaBytes()
    monkeypatch.setattr(media, "get_store", lambda: store)
    app = build_app()
    validate_application(binding, get_engine(), get_session_factory())
    with db() as session:
        # No filtering of real API responses and no visibility into foreign rows.
        assert session.scalar(select(MediaAsset.id).where(MediaAsset.deleted_at.is_(None)).limit(1)) is None
        assert session.scalar(select(DigitalHuman.id).where(DigitalHuman.deleted_at.is_(None)).limit(1)) is None
        assert session.get(Role, role) is not None, f"TEST clone is missing the builtin {role} role"
        try:
            user_id = service.create_user(
                session, username=username, password=password, display_name=marker, roles=[role], actor=marker,
            ).id
        except (ValueError, SQLAlchemyError) as error:
            pytest.fail(f"TEST media account setup failed ({type(error).__name__})", pytrace=False)
    client = TestClient(
        app, base_url=APP, follow_redirects=False, raise_server_exceptions=False, client=(client_ip, 50000),
    )
    gate = MediaBrowserBridge(client, store, marker, username, prefix=prefix)
    try:
        with borrowed_browser() as browser:
            context = browser.new_context(viewport={"width": width, "height": 1000}, service_workers="block")
            try:
                context.route("**/*", gate.handle)
                context.route_web_socket("**/*", lambda socket: socket.close())
                page = context.new_page()
                page.goto(f"{APP}{prefix}{LOGIN}?next={prefix}{ME}")
                page.get_by_label("用户名", exact=True).fill(username)
                fill_sensitive(page.get_by_label("密码", exact=True), password)
                page.get_by_role("button", name="登录", exact=True).click()
                page.wait_for_url(APP + prefix + ME)
                identity = json.loads(page.locator("body").inner_text())
                assert identity.get("authenticated") is True and identity.get("username") == username
                yield MediaRun(page, gate, marker)
                assert gate.refusals == []
                assert gate.fail_next is None and gate.held is None and gate.hold_next is None
                assert store.held_put is None and not store.hold_next_put
            finally:
                gate.closing = True
                context.close()
    finally:
        client.close()
        clear_owned(gate, user_id, username)


def dialog(page: Page) -> Locator:
    return page.locator('[role="dialog"]:visible, [role="alertdialog"]:visible').last


def upload_dialog(page: Page) -> Locator:
    return page.get_by_role("dialog", name=re.compile(r"^上传(?:新)?素材$"))


def confirmation(page: Page) -> Locator:
    region = page.get_by_role("alertdialog")
    expect(region).to_be_visible()
    return region


def button(scope: Page | Locator, name: str) -> Locator:
    return scope.get_by_role("button", name=name, exact=True)


def text(scope: Page | Locator, value: str) -> None:
    expect(scope.get_by_text(value, exact=True).filter(visible=True).first).to_be_visible()


def main(page: Page) -> Locator:
    return page.locator('main:visible, [role="dialog"]:visible, [role="alertdialog"]:visible').last


def heading(page: Page, title: str) -> None:
    expect(main(page).get_by_role("heading", name=title, exact=True)).to_be_visible()
    expect(page.get_by_role("banner", include_hidden=True)).to_have_count(1)
    expect(page.get_by_role("main", include_hidden=True)).to_have_count(1)


def search(page: Page, query: str) -> None:
    main(page).get_by_role("textbox", name="搜索素材", exact=True).fill(query)
    with page.expect_response(lambda response: response.request.method == "GET" and response.url.split("?")[0].endswith(ASSETS)):
        button(main(page), "搜索").click()


def kind(page: Page, label: str) -> None:
    main(page).get_by_role("button", name=re.compile("素材类型")).click()
    page.get_by_role("option", name=label, exact=True).click()
    with page.expect_response(lambda response: response.request.method == "GET" and response.url.split("?")[0].endswith(ASSETS)):
        button(main(page), "搜索").click()


def pick_file(run: MediaRun, name: str) -> None:
    page, gate = run.page, run.gate
    tickets_before = gate.calls["POST", ASSETS + "/upload-url"]
    puts_before = gate.store.puts
    with page.expect_file_chooser() as chooser:
        button(upload_dialog(page), "上传文件").click()
    chooser.value.set_files({
        "name": name, "mimeType": "image/png", "buffer": png(),
    })
    preview = upload_dialog(page).get_by_role("img", name="本地图片预览 " + name, exact=True)
    expect(preview).to_be_visible()
    expect(preview).to_have_attribute("src", re.compile(r"^blob:"))
    page.wait_for_function(
        "img => img.complete && img.naturalWidth === 256 && img.naturalHeight === 128",
        arg=preview.element_handle(),
    )
    assert gate.calls["POST", ASSETS + "/upload-url"] == tickets_before
    assert gate.store.puts == puts_before


def image_ready(page: Page, name: str, *, scope: Locator | None = None) -> None:
    image = (scope or main(page)).get_by_role("img", name="图片预览 " + name, exact=True).first
    expect(image).to_be_visible()
    page.wait_for_function(
        "img => img.complete && img.naturalWidth === 256 && img.naturalHeight === 128",
        arg=image.element_handle(),
    )


def video_ready(page: Page, name: str, *, local: bool = False, scope: Locator | None = None) -> dict[str, int | float]:
    label = ("本地视频预览 " if local else "视频预览 ") + name
    video = (scope or main(page)).get_by_label(label, exact=True)
    expect(video).to_be_visible()
    if local:
        expect(video).to_have_attribute("src", re.compile(r"^blob:"))
    page.wait_for_function("""video => video.readyState >= HTMLMediaElement.HAVE_CURRENT_DATA
        && video.videoWidth > 0 && video.videoHeight > 0
        && Number.isFinite(video.duration) && video.duration > 0""", arg=video.element_handle())
    return video.evaluate("video => ({width: video.videoWidth, height: video.videoHeight, duration: video.duration})")


def open_asset(page: Page, name: str) -> None:
    button(main(page), "查看 " + name).click()
    expect(dialog(page)).to_be_visible()
    heading(page, name)


def asset_action(page: Page, action: str, name: str) -> None:
    if dialog(page).count():
        button(dialog(page), "关闭").click()
        expect(dialog(page)).to_have_count(0)
    button(main(page), action + " " + name).click()


def human_card(page: Page, name: str) -> Locator:
    return page.locator("main").get_by_role("list", name="数字人档案").get_by_role("listitem").filter(
        has=page.get_by_role("heading", name=name, exact=True),
    )


def open_human(page: Page, name: str) -> None:
    human_card(page, name).get_by_role("button", name=re.compile(r"^管理素材 ·")).click()
    heading(page, name + "的素材")


def open_reference(page: Page, name: str) -> None:
    refs = main(page).get_by_role("list", name="关联素材", exact=True)
    button(refs, "查看素材 " + name).click()
    expect(dialog(page)).to_be_visible()
    heading(page, name)


def open_picker_preview(page: Page, name: str) -> Locator:
    card = main(page).locator("[data-media-grid] > *").filter(
        has=page.get_by_role("checkbox", name=name, exact=True),
    )
    expect(card).to_have_count(1)
    details = card.locator("details")
    expect(details).to_have_count(1)
    expect(details).to_have_js_property("open", False)
    expect(details.locator("summary")).to_have_text("查看预览")
    details.locator("summary").click()
    expect(details).to_have_js_property("open", True)
    return details


def picker(page: Page, name: str, *, from_list: bool = False) -> None:
    button(human_card(page, name) if from_list else main(page), "＋ 添加素材").click()
    expect(dialog(page)).to_be_visible()
    heading(page, "为「" + name + "」添加素材")
    expect(main(page).get_by_text("目标数字人：" + name, exact=True)).to_have_count(0)
    expect(main(page).get_by_role("checkbox", name="隐藏已选内容", exact=True)).to_be_checked()


def set_checkbox(page: Page, name: str, selected: bool, *, keyboard: bool = False) -> None:
    checkbox = main(page).get_by_role("checkbox", name=name, exact=True)
    expect(checkbox).to_be_visible()
    expect(checkbox).to_be_enabled()
    if checkbox.is_checked() != selected:
        if keyboard:
            checkbox.focus()
            expect(checkbox).to_be_focused()
            checkbox.press("Space")
        else:
            # HeroUI Checkbox.Content is the visible label; its SVG overlaps the native input.
            label = main(page).locator("label").filter(has=page.get_by_role("checkbox", name=name, exact=True))
            expect(label).to_be_visible()
            label.click()
    if selected:
        expect(checkbox).to_be_checked()
    else:
        expect(checkbox).not_to_be_checked()


def choose(page: Page, name: str) -> None:
    search(page, name)
    set_checkbox(page, name, True)


def submit_selection(page: Page, count: int) -> None:
    text(main(page), f"本次新增 {count} 项")
    button(main(page), f"确认添加（{count}）").click()


def remove_reference(page: Page, name: str) -> None:
    button(main(page), "移除关联 " + name).click()
    button(confirmation(page), "确认移除").click()
    expect(dialog(page)).to_have_count(0)
    expect(main(page).get_by_text(name, exact=True)).to_have_count(0)


def tags(run: MediaRun) -> list[str]:
    status, result = run.api("GET", ASSETS + "/tags")
    assert status == 200 and isinstance(result, list)
    assert all(isinstance(tag, str) for tag in result)
    return result


def assert_preserved(run: MediaRun, asset_id: int, human_ids: set[int], objects: dict) -> None:
    with db() as session:
        asset = session.get(MediaAsset, asset_id)
        assert asset is not None and asset.deleted_at is None and asset.status == "ready"
        assert set(session.scalars(select(DigitalHumanAsset.digital_human_id).where(
            DigitalHumanAsset.asset_id == asset_id,
        ))) == human_ids
    assert run.gate.store.objects == objects
    assert run.gate.store.removes == 0


def assert_layout(page: Page, tmp_path: Path, label: str) -> None:
    # Full-page capture must start at the same viewport origin as the reference;
    # otherwise Chromium paints the sticky app header across a scrolled page.
    page.evaluate("window.scrollTo(0, 0)")
    page.wait_for_function("window.scrollY === 0")
    media_metadata = []
    # Every visible video contains legal VP8 bytes. Full previews must actually
    # play and pause before capture: a decoded first frame alone can still
    # leave Chromium's native initial loading overlay in the screenshot.
    scope = upload_dialog(page) if upload_dialog(page).count() else main(page)
    for video in scope.locator("video:visible").all():
        page.wait_for_function("""video => video.readyState >= HTMLMediaElement.HAVE_CURRENT_DATA
            && video.videoWidth > 0 && video.videoHeight > 0
            && Number.isFinite(video.duration) && video.duration > 0""", arg=video.element_handle())
        if video.evaluate("video => video.controls"):
            video.evaluate("video => video.play()")
            page.wait_for_function("video => video.currentTime > 0 && !video.seeking", arg=video.element_handle())
            video.evaluate("video => video.pause()")
            page.wait_for_function("video => video.paused && !video.seeking", arg=video.element_handle())
            page.evaluate("() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))")
        media_metadata.append(video.evaluate("""video => ({
            label: video.getAttribute('aria-label'), readyState: video.readyState,
            width: video.videoWidth, height: video.videoHeight, duration: video.duration,
            controls: video.controls, preload: video.preload,
            currentTime: video.currentTime, paused: video.paused, networkState: video.networkState,
        })"""))
    dimensions = overflow_report(page)
    assert dimensions["scrollWidth"] <= dimensions["clientWidth"], dimensions
    expect(page.get_by_role("banner", include_hidden=True)).to_have_count(1)
    expect(page.get_by_role("main", include_hidden=True)).to_have_count(1)
    viewport = page.viewport_size
    assert viewport is not None
    (tmp_path / f"TEST_{label}_{viewport['width']}.media.json").write_text(
        json.dumps(media_metadata, ensure_ascii=False, indent=2), encoding="utf-8",
    )
    page.screenshot(path=tmp_path / f"TEST_{label}_{viewport['width']}.png", animations="disabled", full_page=True)


def bounds(locator: Locator) -> dict[str, float]:
    expect(locator).to_be_visible()
    value = locator.bounding_box()
    assert value is not None
    return value


def choose_status(page: Page, label: str) -> None:
    page.locator("main").get_by_role("button", name=re.compile("状态")).click()
    page.get_by_role("option", name=label, exact=True).click()


def test_picker_preserves_selection_and_uses_server_exclusion(media_run: MediaRun) -> None:
    """Existing links are excluded from the cursor and draft picks survive continuation."""
    run, page, gate = media_run, media_run.page, media_run.gate
    run.goto("media-assets")
    existing = [run.seed_asset(f"already_{index}.png") for index in range(2)]
    candidates = [run.seed_asset(f"candidate_{index:02}.png") for index in range(13)]
    first_name, first_id = candidates[-1]
    assert run.api("PATCH", f"{ASSETS}/{first_id}", {"description": run.name("description_only")})[0] == 200
    human, human_id = run.seed_human("picker_target")
    for _name, asset_id in existing:
        run.link(human_id, asset_id)
    run.goto("digital-humans")
    start = len(gate.requests)
    first_page = len(gate.asset_pages)
    picker(page, human, from_list=True)
    text(main(page), "已隐藏 2 项已添加素材")
    initial = gate.asset_pages[first_page]
    assert initial["total"] == 13 and initial["limit"] == 12 and len(initial["items"]) == 12
    text(main(page), "已隐藏 2 项已添加素材")
    assert gate.associated_pages[-1]["total"] == 2 and gate.associated_pages[-1]["limit"] == 1
    assert not {row["asset_id"] for row in initial["items"]} & {aid for _, aid in existing}
    first_read = next(item for item in gate.requests[start:] if item.method == "GET" and item.path == ASSETS
                      and "digitalHumanId" not in item.query)
    assert first_read.query["excludeDigitalHumanId"] == [str(human_id)]

    search(page, run.name("description_only"))
    expect(main(page).get_by_role("checkbox", name=first_name, exact=True)).to_be_visible()
    set_checkbox(page, first_name, True, keyboard=True)
    # The switch hides server-side references, never this unsaved local selection.
    expect(main(page).get_by_role("checkbox", name=first_name, exact=True)).to_be_checked()
    search(page, "")
    second_name, second_id = candidates[0]
    results = main(page).get_by_role("region", name="可选素材结果")
    expect(results.get_by_role("checkbox", name=first_name, exact=True)).to_be_visible()
    results.evaluate("element => element.scrollTop = element.scrollHeight")
    expect(results.get_by_role("checkbox", name=second_name, exact=True)).to_be_visible()
    assert any(item.query.get("cursor", [""])[0] for item in gate.requests[start:] if item.path == ASSETS)
    assert second_id != first_id
    set_checkbox(page, second_name, True)
    kind(page, "视频")
    text(main(page), "本次新增 2 项")
    expect(main(page).get_by_role("checkbox", name=first_name, exact=True)).to_have_count(0)
    kind(page, "图片")
    choose(page, first_name)

    set_checkbox(page, "隐藏已选内容", False)
    search(page, existing[0][0])
    linked = main(page).get_by_role("checkbox", name=existing[0][0], exact=True)
    expect(linked).to_be_checked()
    expect(linked).to_be_disabled()
    text(main(page), "已添加至「" + human + "」")
    assert "excludeDigitalHumanId" not in [item for item in gate.requests if item.path == ASSETS][-1].query
    set_checkbox(page, "隐藏已选内容", True, keyboard=True)
    choose(page, first_name)
    text(main(page), "本次新增 2 项")
    path = f"{HUMANS}/{human_id}/assets"
    assert gate.calls["POST", path] == 2  # only the two fixture-owned existing references
    assert run.links(human_id) == {asset_id for _, asset_id in existing}
    before = len(gate.requests)
    gate.fail("POST", path, 500, after=1)
    submit_selection(page, 2)
    text(main(page), "本次新增 1 项")
    expect(main(page).get_by_role("alert")).to_be_visible()
    assert run.links(human_id) == {asset_id for _, asset_id in existing} | {first_id}
    assert gate.calls["POST", path] == 4
    submit_selection(page, 1)
    heading(page, human)
    assert run.links(human_id) == {asset_id for _, asset_id in existing} | {first_id, second_id}
    attempts = [item.body["assetId"] for item in gate.requests[before:] if item.method == "POST" and item.path == path]
    assert list(map(int, attempts)) == [first_id, second_id, second_id]


@pytest.mark.parametrize("media_run", [("admin", 375, "/tts")], indirect=True)
def test_picker_upload_keeps_pending_assets_and_retries_the_same_ticket(media_run: MediaRun, tmp_path: Path) -> None:
    run, page, gate = media_run, media_run.page, media_run.gate
    run.goto("media-assets")
    first, aid = run.seed_asset("pending_A.png")
    second, bid = run.seed_asset("pending_B.png")
    person, human_id = run.seed_human("upload_target")
    _other, other_id = run.seed_human("other_target")
    run.goto("digital-humans")
    picker(page, person, from_list=True)
    choose(page, first)
    choose(page, second)
    kind(page, "图片")
    text(main(page), "本次新增 2 项")
    button(main(page), "上传新素材").click()
    upload = upload_dialog(page)
    text(upload, "上传后添加到「" + person + "」。")
    expect(page.locator("main h1")).to_contain_text("档案")
    button(upload, "取消").click()
    heading(page, "为「" + person + "」添加素材")
    expect(main(page).get_by_role("textbox", name="搜索素材", exact=True)).to_have_value(second)
    text(main(page), "本次新增 2 项")
    button(main(page), "上传新素材").click()
    uploaded = run.name("uploaded_C.png")
    pick_file(run, uploaded)
    before_assets = set(gate.assets)
    before_tickets = gate.calls["POST", ASSETS + "/upload-url"]
    before_puts = gate.store.puts
    attach_path = f"{HUMANS}/{human_id}/assets"
    gate.store.hold_put()
    gate.hold("POST", re.compile(rf"{ASSETS}/[1-9][0-9]*/confirm"))
    with page.expect_request(lambda req: req.method == "PUT"):
        button(upload, "开始上传").click()
    expect(upload.get_by_role("progressbar", name="传输进度", exact=True)).to_be_visible()
    assert gate.store.held_put is not None and gate.store.puts == before_puts
    assert_layout(page, tmp_path, "upload_sending")
    with page.expect_request(lambda req: req.method == "POST" and req.url.endswith("/confirm")):
        gate.store.release_put()
    expect(upload.get_by_text(re.compile("正在确认入库")).first).to_be_visible()
    expect(upload.get_by_role("progressbar", name="传输完成", exact=True)).to_have_attribute("aria-valuenow", "100")
    uploaded_id, = gate.assets - before_assets
    confirm_path = f"{ASSETS}/{uploaded_id}/confirm"
    assert gate.store.puts == before_puts + 1 and gate.calls["POST", attach_path] == 0
    with db() as session:
        assert session.get(MediaAsset, uploaded_id).status == "awaiting_upload"
    assert_layout(page, tmp_path, "upload_confirming")
    gate.release(status=503)
    expect(button(upload, "重试入库")).to_be_visible()
    expect(upload.get_by_role("alert")).to_be_visible()
    assert_layout(page, tmp_path, "upload_confirm_error")
    gate.fail("POST", attach_path, 500)
    button(upload, "重试入库").click()
    button(upload, "重试添加").wait_for()
    with db() as session:
        assert session.get(MediaAsset, uploaded_id).status == "ready"
    assert run.links(human_id) == set()
    expect(upload.get_by_role("alert")).to_be_visible()
    assert_layout(page, tmp_path, "upload_attach_error")
    button(upload, "重试添加").click()
    heading(page, "为「" + person + "」添加素材")
    text(main(page), "本次新增 2 项")
    expect(main(page).get_by_role("textbox", name="搜索素材", exact=True)).to_have_value(second)
    expect(main(page).get_by_role("checkbox", name=second, exact=True)).to_be_checked()
    choose(page, first)
    assert run.links(human_id) == {uploaded_id} and run.links(other_id) == set()
    assert gate.calls["POST", ASSETS + "/upload-url"] == before_tickets + 1
    assert gate.store.puts == before_puts + 1
    assert gate.calls["POST", confirm_path] == gate.calls["POST", attach_path] == 2
    submit_selection(page, 2)
    heading(page, person)
    assert run.links(human_id) == {aid, bid, uploaded_id} and run.links(other_id) == set()


def test_tag_editor_upload_selection_failed_save_and_shared_chips(media_run: MediaRun) -> None:
    run, page, gate = media_run, media_run.page, media_run.gate
    run.goto("media-assets")
    existing_tag = run.name("existing tag")
    run.seed_asset("tag_candidate.png", tags=[existing_tag])
    keyword = run.name("unmatched_filter")
    search(page, keyword)
    filename = run.name("tagged_upload.png")
    comma_tag = run.name("comma,stays_one_" + "长标签" * 25)
    assert len(comma_tag) > 64
    pending_tag = run.name("upload_pending")
    button(main(page), "上传素材").click()
    upload = upload_dialog(page)
    expect(upload.get_by_role("checkbox")).to_have_count(0)
    button(upload, "选择标签").click()
    tag_search = upload.get_by_role("searchbox", name="搜索标签", exact=True)
    cancelled_upload = run.name("cancel_upload")
    tag_search.fill(cancelled_upload)
    button(upload, "新建「" + cancelled_upload + "」").click()
    button(upload, "取消").click()
    button(dialog(page), "确认放弃").click()
    assert cancelled_upload not in tags(run)

    button(main(page), "上传素材").click()
    pick_file(run, filename)
    button(upload, "选择标签").click()
    existing = upload.get_by_role("checkbox", name=existing_tag, exact=True)
    expect(existing).to_be_visible()
    expect(upload.get_by_role("checkbox", name=cancelled_upload, exact=True)).to_have_count(0)
    tag_search.fill("  " + existing_tag.upper().replace("EXISTING TAG", "EXISTING\u00a0 \u3000TAG") + "  ")
    expect(existing).to_be_visible()
    expect(upload.get_by_role("button", name=re.compile("^新建"))).to_have_count(0)
    tag_search.press("Enter")
    expect(existing).to_be_checked()
    before = gate.calls["POST", ASSETS + "/upload-url"]
    tag_search.fill("  " + comma_tag + "  ")
    tag_search.press("Enter")
    expect(tag_search).to_have_value("")
    expect(upload.get_by_role("checkbox", name=comma_tag, exact=True)).to_be_checked()
    assert gate.calls["POST", ASSETS + "/upload-url"] == before
    assert comma_tag not in tags(run)
    button(upload, "移除标签 " + existing_tag).click()
    expect(existing).not_to_be_checked()
    existing.press("Space")
    expect(existing).to_be_checked()
    tag_search.fill("  " + pending_tag + "  ")
    gate.fail("POST", ASSETS + "/upload-url", 400)
    button(upload, "开始上传").click()
    expect(upload.get_by_role("alert")).to_be_visible()
    expect(tag_search).to_have_value("  " + pending_tag + "  ")
    expect(button(upload, "移除标签 " + existing_tag)).to_be_visible()
    assert pending_tag not in tags(run)
    button(upload, "开始上传").click()
    expect(button(page.locator("main"), "上传素材")).to_be_visible()
    expect(main(page).get_by_role("textbox", name="搜索素材", exact=True)).to_have_value(keyword)
    button(main(page), "查看刚上传的素材").click()
    heading(page, filename)
    asset_id = run.asset_id(filename)
    expected = {existing_tag, comma_tag}
    with db() as session:
        stored = session.get(MediaAsset, asset_id)
        assert set(stored.tags) == expected and len(stored.tags) == 2
    for tag in expected:
        text(main(page), tag)
    assert expected <= set(tags(run))
    ticket_request = [item for item in gate.requests if item.path == ASSETS + "/upload-url"][-1]
    assert set(ticket_request.body["tags"]) == expected

    button(dialog(page), "关闭").click()
    search(page, filename)
    asset_action(page, "编辑", filename)
    heading(page, "编辑素材")
    cancelled = run.name("cancelled_candidate")
    entry = main(page).get_by_role("textbox", name="新增标签", exact=True)
    main(page).get_by_role("textbox", name="新增标签", exact=True).fill(cancelled)
    entry.press("Enter")
    button(main(page), "取消").click()
    button(dialog(page), "确认放弃").click()
    expect(dialog(page)).to_have_count(0)
    assert cancelled not in tags(run)
    asset_action(page, "编辑", filename)
    expect(main(page).get_by_role("row", name=cancelled, exact=True)).to_have_count(0)
    renamed = run.name("tagged_renamed.png")
    note = run.name("shared_description")
    final_tag = run.name("edit_pending")
    main(page).get_by_label("素材名称", exact=True).fill(renamed)
    main(page).get_by_label("素材说明", exact=True).fill(note)
    main(page).get_by_role("textbox", name="新增标签", exact=True).fill(" " + final_tag + " ")
    gate.fail("PATCH", f"{ASSETS}/{asset_id}", 500)
    button(main(page), "保存资料").click()
    expect(main(page).get_by_role("alert")).to_be_visible()
    expect(main(page).get_by_role("textbox", name="新增标签", exact=True)).to_have_value(" " + final_tag + " ")
    expect(main(page).get_by_label("素材名称", exact=True)).to_have_value(renamed)
    assert final_tag not in tags(run) and run.asset_id(filename) == asset_id
    button(main(page), "保存资料").click()
    text(page.locator("main"), "素材资料已保存。")
    expected.add(final_tag)
    with db() as session:
        stored = session.get(MediaAsset, asset_id)
        assert (stored.filename, stored.description) == (renamed, note)
        assert set(stored.tags) == expected and len(stored.tags) == 3
    person, human_id = run.seed_human("tag_reference")
    run.link(human_id, asset_id)
    run.goto(f"digital-humans?human={human_id}")
    heading(page, person + "的素材")
    for tag in expected:
        text(main(page), tag)
    run.goto("media-assets")
    search(page, final_tag)
    button(main(page), "查看 " + renamed).wait_for()
    assert gate.asset_pages[-1]["total"] == 1
    assert [item["asset_id"] for item in gate.asset_pages[-1]["items"]] == [asset_id]
    assert final_tag not in renamed and final_tag not in note


def test_references_block_global_deletion_including_archived_humans(media_run: MediaRun) -> None:
    run, page, gate = media_run, media_run.page, media_run.gate
    run.goto("media-assets")
    filename, asset_id = run.seed_asset("shared.png")
    first, first_id = run.seed_human("active_reference")
    second, second_id = run.seed_human("archived_reference")
    run.link(first_id, asset_id)
    run.link(second_id, asset_id)
    assert run.api("PATCH", f"{HUMANS}/{second_id}", {"status": "archived"})[0] == 200
    objects = dict(gate.store.objects)
    run.goto("media-assets")
    asset_action(page, "删除", filename)
    expect(button(confirmation(page), "确认删除")).to_be_disabled()
    expect(confirmation(page).get_by_text(re.compile("请解除关联后再删除"))).to_be_visible()
    # UI preflight and the authoritative endpoint must agree, including archived references.
    status, body = run.api("DELETE", f"{ASSETS}/{asset_id}")
    assert status == 409 and body["detail"] == "ASSET_IN_USE"
    assert_preserved(run, asset_id, {first_id, second_id}, objects)
    button(confirmation(page), "取消").click()

    run.goto(f"digital-humans?human={first_id}")
    heading(page, first + "的素材")
    remove_reference(page, filename)
    assert run.links(first_id) == set() and run.links(second_id) == {asset_id}
    run.goto("media-assets")
    asset_action(page, "删除", filename)
    expect(button(confirmation(page), "确认删除")).to_be_disabled()
    expect(confirmation(page).get_by_text(re.compile("请解除关联后再删除"))).to_be_visible()
    assert_preserved(run, asset_id, {second_id}, objects)
    button(confirmation(page), "取消").click()

    # Deleting a linked human must retain both the asset and the other human's reference.
    run.link(first_id, asset_id)
    run.goto("digital-humans")
    button(human_card(page, first), "删除 " + first).click()
    button(confirmation(page), "确认删除").click()
    expect(dialog(page)).to_have_count(0)
    expect(button(main(page), "新建数字人")).to_be_visible()
    expect(human_card(page, first)).to_have_count(0)
    with db() as session:
        assert session.get(DigitalHuman, first_id).deleted_at is not None
        assert session.get(MediaAsset, asset_id).deleted_at is None
    assert run.links(first_id) == set() and run.links(second_id) == {asset_id}
    assert gate.store.objects == objects and gate.store.removes == 0

    run.goto(f"digital-humans?human={second_id}")
    heading(page, second + "的素材")
    remove_reference(page, filename)
    assert run.links(second_id) == set()
    button(main(page), "← 返回").click()
    choose_status(page, "已归档")
    button(human_card(page, second), "恢复 " + second).click()
    expect(main(page).get_by_text(re.compile("档案已恢复"))).to_be_visible()
    choose_status(page, "启用中")
    expect(human_card(page, second)).to_be_visible()
    with db() as session:
        assert session.get(DigitalHuman, second_id).status == "active"
    run.goto("media-assets")
    asset_action(page, "删除", filename)
    expect(button(confirmation(page), "确认删除")).to_be_enabled()
    button(confirmation(page), "确认删除").click()
    expect(dialog(page)).to_have_count(0)
    expect(button(main(page), "上传素材")).to_be_visible()
    expect(button(main(page), "查看 " + filename)).to_have_count(0)
    with db() as session:
        assert session.get(MediaAsset, asset_id).deleted_at is not None
    assert gate.store.objects == {} and gate.store.removes == 1
    assert run.links(second_id) == set()


def test_new_reference_after_delete_preflight_is_rejected_without_byte_removal(media_run: MediaRun) -> None:
    run, page, gate = media_run, media_run.page, media_run.gate
    run.goto("media-assets")
    filename, asset_id = run.seed_asset("delete_race.png")
    _person, human_id = run.seed_human("late_reference")
    objects = dict(gate.store.objects)
    run.goto("media-assets")
    asset_action(page, "删除", filename)
    expect(button(confirmation(page), "确认删除")).to_be_enabled()
    run.link(human_id, asset_id)  # Genuine API/SQL change while the original confirmation remains open.
    button(confirmation(page), "确认删除").click()
    expect(confirmation(page).get_by_role("alert")).to_contain_text("引用")
    expect(confirmation(page).get_by_text(re.compile("请解除关联后再删除"))).to_be_visible()
    expect(button(confirmation(page), "确认删除")).to_be_disabled()
    assert ("DELETE", f"{ASSETS}/{asset_id}", 409) in gate.responses
    assert_preserved(run, asset_id, {human_id}, objects)


def test_read_preview_and_draft_recovery_keeps_objects_isolated(media_run: MediaRun) -> None:
    run, page, gate = media_run, media_run.page, media_run.gate
    run.goto("media-assets")
    filename, asset_id = run.seed_asset("recover_preview.png")
    gate.fail("GET", ASSETS, 503)
    run.goto("media-assets")
    button(main(page), "重试素材列表").click()
    button(main(page), "查看 " + filename).wait_for()
    image_ready(page, filename)
    gate.fail("GET", f"{ASSETS}/{asset_id}", 503)
    button(main(page), "查看 " + filename).click()
    button(main(page), "重试素材详情").wait_for()
    gate.store.break_next_preview = True
    button(main(page), "重试素材详情").click()
    heading(page, filename)
    text(main(page), "暂时无法预览此文件。")
    button(main(page), "重新获取预览").click()
    image_ready(page, filename)

    run.goto("digital-humans")
    other_name, other_id = run.seed_human("other_persona")
    run.goto("digital-humans")
    before_creates = gate.calls["POST", HUMANS]
    person = run.name("created_in_ui")
    button(main(page), "新建数字人").click()
    heading(page, "新建数字人")
    main(page).get_by_label("数字人名称", exact=True).fill(person)
    main(page).get_by_label("人设说明", exact=True).fill("TEST_persona_description")
    gate.fail("GET", HUMANS, 503)
    button(main(page), "创建档案").click()
    button(main(page), "重试档案列表").wait_for()
    expect(main(page).get_by_text(re.compile("档案已创建")).first).to_be_visible()
    button(main(page), "重试档案列表").click()
    heading(page, person)
    human_id = run.human_id(person)
    assert gate.calls["POST", HUMANS] == before_creates + 1
    picker(page, person, from_list=True)
    choose(page, filename)
    assert run.links(human_id) == set()
    button(main(page), "取消").click()
    button(dialog(page), "确认放弃").click()
    heading(page, person)
    assert run.links(human_id) == set() and gate.calls["POST", f"{HUMANS}/{human_id}/assets"] == 0
    button(human_card(page, person), "编辑 " + person).click()
    draft = run.name("discard_me")
    main(page).get_by_label("数字人名称", exact=True).fill(draft)
    button(main(page), "取消").click()
    button(dialog(page), "继续处理").click()
    expect(main(page).get_by_label("数字人名称", exact=True)).to_have_value(draft)
    button(main(page), "取消").click()
    button(dialog(page), "确认放弃").click()
    heading(page, person)
    expect(main(page).get_by_text(draft, exact=True)).to_have_count(0)
    picker(page, other_name, from_list=True)
    expect(main(page).get_by_role("checkbox", name=filename, exact=True)).not_to_be_checked()
    text(main(page), "本次新增 0 项")
    assert run.links(other_id) == set() and run.human_id(person) == human_id
    assert gate.calls["PATCH", f"{HUMANS}/{human_id}"] == 0


@pytest.mark.parametrize("media_run", [("admin", width, "/tts") for width in (375, 1440, 2560)], indirect=True)
def test_media_long_content_remains_readable_at_supported_widths(media_run: MediaRun, tmp_path: Path) -> None:
    run, page = media_run, media_run.page
    run.goto("media-assets")
    chip = run.name("visible_chip")
    filename, asset_id = run.seed_asset("a_long_filename_" + "preview_" * 8 + ".png", tags=[chip])
    person, human_id = run.seed_human("layout_persona_" + "long_name_" * 6)
    run.link(human_id, asset_id)
    run.goto("media-assets")
    image_ready(page, filename)
    text(main(page), chip)
    expect(main(page).get_by_text("1 个数字人引用", exact=True)).to_have_count(0)
    card = main(page).get_by_role("list", name="素材列表").locator(":scope > li").first
    name_box = bounds(card.get_by_role("heading", name=filename, exact=True))
    kind_box = bounds(card.get_by_text("图片", exact=True))
    assert abs((name_box["y"] + name_box["height"] / 2) - (kind_box["y"] + kind_box["height"] / 2)) < 2
    assert_layout(page, tmp_path, "media_library")
    open_asset(page, filename)
    image_ready(page, filename)
    assert_layout(page, tmp_path, "asset_detail")
    run.goto("digital-humans")
    expect(human_card(page, person)).to_be_visible()
    assert_layout(page, tmp_path, "digital_human_cards")
    open_human(page, person)
    text(main(page), chip)
    image_ready(page, filename)
    assert_layout(page, tmp_path, "human_material_cards")
    button(main(page), "← 返回").click()
    picker(page, person, from_list=True)
    text(main(page), "本次新增 0 项")
    expect(button(main(page), "确认添加（0）")).to_be_disabled()
    expect(main(page).get_by_role("checkbox", name=filename, exact=True)).to_have_count(0)
    set_checkbox(page, "隐藏已选内容", False)
    expect(main(page).get_by_role("checkbox", name=filename, exact=True)).to_be_disabled()
    image_ready(page, filename, scope=open_picker_preview(page, filename))
    assert_layout(page, tmp_path, "asset_picker")


@pytest.mark.parametrize("media_run", [("admin", width, "/tts", "fidelity") for width in (375, 1007, 1440)], indirect=True)
def test_media_pages_match_confirmed_prototype_structure(media_run: MediaRun, tmp_path: Path) -> None:
    run, page, gate = media_run, media_run.page, media_run.gate
    run.goto("media-assets")
    assets = [run.seed_asset(f"卡片素材_{index:02}.png", tags=[run.name("样例标签")]) for index in range(13)]
    run.goto("media-assets")
    root = page.locator("main")
    expect(root.get_by_role("heading", name="全局素材库", exact=True)).to_have_count(0)
    expect(button(root, "上传素材")).to_be_visible()
    cards = root.get_by_role("list", name="素材列表").locator(":scope > li")
    expect(cards.first).to_be_visible()
    while cards.count() < len(assets):
        tail = root.get_by_role("button", name="加载更多", exact=True)
        if tail.count():
            tail.evaluate("element => element.scrollIntoView({block: 'end'})")
        expect(cards).to_have_count(len(assets))
    assert any(request.query.get("cursor", [""])[0] for request in gate.requests if request.path == ASSETS)
    assert len(cards.get_by_role("heading").all_text_contents()) == len(assets)
    assert len(set(cards.get_by_role("heading").all_text_contents())) == len(assets)
    expect(button(root, "下一页")).to_have_count(0)
    oldest, oldest_id = assets[0]
    asset_action(page, "编辑", oldest)
    heading(page, "编辑素材")
    main(page).get_by_label("素材说明", exact=True).fill(run.name("最近编辑"))
    button(main(page), "保存资料").click()
    expect(cards.first.get_by_role("heading", name=oldest, exact=True)).to_be_visible()
    assert_layout(page, tmp_path, "recent_first_library")

    run.goto("digital-humans")
    person = run.name("林溪")
    button(root, "新建数字人").click()
    create = page.get_by_role("dialog", name="新建数字人", exact=True)
    create.get_by_label("数字人名称", exact=True).fill(person)
    photo = run.name("形象初稿.png")
    with page.expect_file_chooser() as chooser:
        button(create, "选择照片").click()
    chooser.value.set_files({"name": photo, "mimeType": "image/png", "buffer": png()})
    expect(create.get_by_role("img", name="形象照片预览", exact=True)).to_be_visible()
    assert_layout(page, tmp_path, "create_with_portrait")
    button(create, "创建档案").click()
    card = human_card(page, person)
    portrait = button(card, "更换" + person + "的形象照片")
    expect(portrait).to_be_visible()
    image = portrait.locator("img")
    expect(image).to_be_visible()
    page.wait_for_function("img => img.complete && img.naturalWidth === 256", arg=image.element_handle())
    original = image.get_attribute("src")
    assert "human=" not in page.url
    human_id = run.human_id(person)
    with db() as session:
        assert session.get(DigitalHuman, human_id).portrait_asset_id == run.asset_id(photo)
    assert_layout(page, tmp_path, "human_portrait_card")

    replacement = run.name("形象新版.png")
    for save in (False, True):
        portrait.click()
        edit = page.get_by_role("dialog", name="更换形象照片", exact=True)
        with page.expect_file_chooser() as chooser:
            button(edit, "更换照片").click()
        chooser.value.set_files({"name": replacement, "mimeType": "image/png", "buffer": png()})
        expect(edit.get_by_role("img", name="形象照片预览", exact=True)).to_be_visible()
        button(edit, "保存照片" if save else "取消").click()
        if not save:
            expect(image).to_have_attribute("src", original)
    expect(root.get_by_text("形象照片已更新。", exact=True)).to_be_visible()
    expect(image).not_to_have_attribute("src", original)
    with db() as session:
        assert session.get(DigitalHuman, human_id).portrait_asset_id == run.asset_id(replacement)
    assert run.api("GET", ASSETS + "?keyword=" + run.marker)[1]["total"] == len(assets)

    picker(page, person, from_list=True)
    for filename, _ in assets[:2]:
        choose(page, filename)
    submit_selection(page, 2)
    expect(dialog(page)).to_have_count(0)
    open_human(page, person)
    linked = root.get_by_role("list", name="关联素材", exact=True)
    expect(linked.locator(":scope > li")).to_have_count(2)
    image_ready(page, oldest, scope=linked)
    assert_layout(page, tmp_path, "linked_asset_cards")
    set_checkbox(page, "全选", True)
    button(root, "批量移除").click()
    button(confirmation(page), "确认移除").click()
    text(root, "当前没有关联素材。")
    assert run.links(human_id) == set()
    assert gate.calls["POST", f"{HUMANS}/{human_id}/assets/batch-remove"] == 1
    assert run.api("GET", f"{ASSETS}/{oldest_id}")[0] == 200
    breadcrumb = root.get_by_role("navigation", name="面包屑")
    button(breadcrumb, "数字人").click()
    expect(human_card(page, person)).to_be_visible()


def test_video_local_preview_upload_and_shared_metadata_match(media_run: MediaRun, tmp_path: Path) -> None:
    """A legal VP8 fixture must decode natively before and after the real upload flow."""
    run, page, gate = media_run, media_run.page, media_run.gate
    fixture = Path(__file__).resolve().parents[1] / "fixtures" / "media" / "TEST_preview.webm"
    payload = fixture.read_bytes()
    filename = run.name("metadata_clip.webm")
    run.goto("media-assets")
    button(main(page), "上传素材").click()
    upload = upload_dialog(page)
    with page.expect_file_chooser() as chooser:
        button(upload, "上传文件").click()
    chooser.value.set_files({
        "name": filename, "mimeType": "video/webm", "buffer": payload,
    })
    local_metadata = video_ready(page, filename, local=True, scope=upload)
    assert (local_metadata["width"], local_metadata["height"]) == (320, 180)
    assert local_metadata["duration"] == pytest.approx(1.0, abs=0.05)
    assert gate.calls["POST", ASSETS + "/upload-url"] == gate.store.puts == 0
    assert gate.assets == set() and gate.store.objects == {}
    assert_layout(page, tmp_path, "local_video_preview")

    button(upload, "开始上传").click()
    expect(button(page.locator("main"), "上传素材")).to_be_visible()
    button(main(page), "查看刚上传的素材").click()
    heading(page, filename)
    assert video_ready(page, filename) == local_metadata
    asset_id = run.asset_id(filename)
    with db() as session:
        asset = session.get(MediaAsset, asset_id)
        assert (asset.kind, asset.content_type, asset.size_bytes, asset.status) == ("video", "video/webm", len(payload), "ready")
        object_key = asset.object_key
    assert gate.store.objects == {object_key: ("video/webm", payload)}
    assert gate.calls["POST", ASSETS + "/upload-url"] == gate.store.puts == 1
    assert gate.calls["POST", f"{ASSETS}/{asset_id}/confirm"] == 1
    button(main(page), "关闭").click()
    assert video_ready(page, filename) == local_metadata
    assert gate.asset_pages[-1]["items"][0]["asset_id"] == asset_id
    assert gate.asset_pages[-1]["items"][0]["usageCount"] == 0

    person, human_id = run.seed_human("video_target")
    run.goto("digital-humans")
    picker(page, person, from_list=True)
    kind(page, "视频")
    assert video_ready(page, filename, scope=open_picker_preview(page, filename)) == local_metadata
    set_checkbox(page, filename, True)
    attach_path = f"{HUMANS}/{human_id}/assets"
    assert gate.calls["POST", attach_path] == 0 and run.links(human_id) == set()
    submit_selection(page, 1)
    open_human(page, person)
    refs = main(page).get_by_role("list", name="关联素材", exact=True)
    assert video_ready(page, filename, scope=refs) == local_metadata
    assert run.links(human_id) == {asset_id} and gate.calls["POST", attach_path] == 1
    assert_layout(page, tmp_path, "shared_video_detail")

    open_reference(page, filename)
    assert video_ready(page, filename) == local_metadata
    status, metadata = run.api("GET", f"{ASSETS}/{asset_id}")
    assert status == 200 and isinstance(metadata, dict) and metadata["usageCount"] == 1
    assert gate.store.objects == {object_key: ("video/webm", payload)} and gate.store.removes == 0


@pytest.mark.parametrize("media_run", [("admin", width, "", "fidelity") for width in (375, 1007, 2560)], indirect=True)
def test_upload_file_control_is_visible_and_opens_native_picker(media_run: MediaRun, tmp_path: Path) -> None:
    run, page, gate = media_run, media_run.page, media_run.gate
    if page.viewport_size["width"] == 2560:
        page.set_viewport_size({"width": 2560, "height": 1215})
    run.goto("media-assets")
    button(main(page), "上传素材").click()
    upload = upload_dialog(page)
    expect(page.locator("main h1")).to_have_count(0)
    control = button(upload, "上传文件")
    name_field = upload.get_by_role("textbox", name="素材名称", exact=True)
    expect(name_field).to_be_focused()
    expect(control).to_be_visible()
    expect(upload.locator('input[type="file"]')).not_to_be_visible()
    assert bounds(name_field)["y"] + bounds(name_field)["height"] < bounds(control)["y"]
    expect(upload.get_by_role("searchbox", name="搜索标签")).to_have_count(0)
    assert_layout(page, tmp_path, "file_control_empty")
    display_name = run.name("只修改素材显示名称")
    name_field.fill(display_name)
    pick_file(run, run.name("本地素材.png"))
    expect(name_field).to_have_value(display_name)
    name = run.name("原始文件名_" + "长名称保持可读_" * 12 + ".png")
    current = button(upload, "更换文件")
    current.focus()
    current.press("Tab")
    page.keyboard.press("Shift+Tab")
    expect(current).to_be_focused()
    assert_layout(page, tmp_path, "file_control_keyboard_focus")
    with page.expect_file_chooser() as chooser:
        current.press("Enter")
    chooser.value.set_files({"name": name, "mimeType": "image/png", "buffer": png()})
    expect(current).to_be_visible()
    text(upload, name)
    expect(name_field).to_have_value(display_name)
    assert gate.assets == set() and gate.store.puts == 0
    assert_layout(page, tmp_path, "file_control_selected_long_name")
    gate.store.hold_put()
    with page.expect_request(lambda request: request.method == "PUT"):
        button(upload, "开始上传").click()
    expect(current).to_be_disabled()
    expect(upload.locator('input[type="file"]')).to_be_disabled()
    assert_layout(page, tmp_path, "file_control_disabled")
    gate.store.release_put()
    expect(button(page.locator("main"), "上传素材")).to_be_visible()
    text(main(page), "已入库「" + display_name + "」。")
    assert gate.store.puts == 1 and gate.calls["POST", ASSETS + "/upload-url"] == 1


@pytest.mark.parametrize("media_run", [("viewer", 1440, "")], indirect=True)
def test_viewer_media_permission_denial(media_run: MediaRun) -> None:
    """The existing readonly role lacks page grants and cannot enter either workflow."""
    for slug in ("media-assets", "digital-humans"):
        response = media_run.goto(slug)
        assert response is not None and response.status == 403
    assert media_run.gate.calls["GET", ASSETS] == media_run.gate.calls["GET", HUMANS] == 0
