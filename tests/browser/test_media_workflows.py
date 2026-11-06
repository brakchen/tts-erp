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


def confirmation(page: Page) -> Locator:
    """Destructive operations use the prototype's content page, not a modal."""
    region = main(page).get_by_role("region", name="操作确认", exact=True)
    expect(region).to_be_visible()
    expect(dialog(page)).to_have_count(0)
    return region


def button(scope: Page | Locator, name: str) -> Locator:
    return scope.get_by_role("button", name=name, exact=True)


def text(scope: Page | Locator, value: str) -> None:
    expect(scope.get_by_text(value, exact=True).filter(visible=True).first).to_be_visible()


def main(page: Page) -> Locator:
    return page.get_by_role("main")


def heading(page: Page, title: str) -> None:
    expect(main(page).get_by_role("heading", name=title, exact=True)).to_be_visible()
    expect(page.get_by_role("banner")).to_have_count(1)
    expect(page.get_by_role("main")).to_have_count(1)
    expect(dialog(page)).to_have_count(0)


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
    main(page).locator('input[type="file"]').set_input_files({
        "name": name, "mimeType": "image/png", "buffer": png(),
    })
    preview = main(page).get_by_role("img", name="本地图片预览 " + name, exact=True)
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
    heading(page, name)


def open_human(page: Page, name: str) -> None:
    button(main(page), "查看档案 " + name).click()
    heading(page, name)


def open_reference(page: Page, name: str) -> None:
    refs = main(page).get_by_role("region", name="引用素材", exact=True)
    expect(refs.locator("img, video")).to_have_count(0)
    control = refs.get_by_role("link", name="查看素材 " + name, exact=True).or_(
        refs.get_by_role("button", name="查看素材 " + name, exact=True),
    )
    expect(control).to_have_count(1)
    control.click()
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
    button(main(page), "添加素材 " + name if from_list else "添加素材").click()
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
    button(main(page), "移除引用 " + name).click()
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
    for video in main(page).locator("video:visible").all():
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
    expect(page.get_by_role("banner")).to_have_count(1)
    expect(page.get_by_role("main")).to_have_count(1)
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


def assert_reference_header(page: Page, title: str, action: str | None = None) -> None:
    """The accepted reference has one object title, not a panel title plus target."""
    header = main(page).locator("[data-media-header]")
    expect(header).to_have_count(1)
    title_element = header.get_by_role("heading", name=title, exact=True, level=1)
    title_box, header_box = bounds(title_element), bounds(header)
    assert title_box["x"] >= header_box["x"] - 1
    size = float(title_element.evaluate("el => parseFloat(getComputedStyle(el).fontSize)"))
    assert 22 <= size <= 30, size
    if action:
        action_element = header.get_by_role("button", name=action, exact=True).or_(
            header.get_by_role("link", name=action, exact=True),
        )
        action_box = bounds(action_element)
        if header_box["width"] >= 560:
            assert action_box["x"] >= title_box["x"] + title_box["width"] - 1
            assert abs(action_box["x"] + action_box["width"] - header_box["x"] - header_box["width"]) <= 4


def assert_reference_grid(page: Page, count: int) -> None:
    """Reference CSS: four records/page, 178px minimum cards, 10px gap, compact previews."""
    grid = main(page).locator("[data-media-grid]")
    cards = grid.locator(":scope > *")
    expect(cards).to_have_count(count)
    area = bounds(grid)
    columns = min(count, max(1, int((area["width"] + 10) // 188)))
    rectangles = [bounds(cards.nth(index)) for index in range(count)]
    first_row = [box for box in rectangles if abs(box["y"] - rectangles[0]["y"]) <= 2]
    assert len(first_row) == columns, {"area": area, "cards": rectangles, "expectedColumns": columns}
    if columns > 1:
        for left, right in zip(first_row, first_row[1:]):
            assert abs(left["width"] - right["width"]) <= 2
            assert 8 <= right["x"] - left["x"] - left["width"] <= 12
    for index in range(count):
        visible_preview = cards.nth(index).locator("img:visible, video:visible").first
        assert bounds(visible_preview)["height"] <= 82, "Card previews must retain the reference's compact frame"


def assert_reference_picker(page: Page, name: str, count: int) -> None:
    assert_reference_header(page, "为「" + name + "」添加素材", "上传新素材")
    filters = main(page).locator("form").filter(has=page.get_by_role("textbox", name="搜索素材", exact=True))
    toolbar = main(page).locator("[data-picker-toolbar]")
    grid = main(page).locator("[data-media-grid]")
    filter_box, toolbar_box, grid_box = bounds(filters), bounds(toolbar), bounds(grid)
    assert filter_box["y"] + filter_box["height"] <= toolbar_box["y"] + 2
    assert toolbar_box["y"] + toolbar_box["height"] <= grid_box["y"] + 2
    hide_box = bounds(toolbar.get_by_role("checkbox", name="隐藏已选内容", exact=True))
    confirm_box = bounds(button(toolbar, f"确认添加（{count}）"))
    assert confirm_box["x"] >= hide_box["x"]
    assert abs(confirm_box["x"] + confirm_box["width"] - toolbar_box["x"] - toolbar_box["width"]) <= 4
    expect(main(page).get_by_role("button", name=re.compile(r"^确认添加[（(]"))).to_have_count(1)
    expect(main(page).get_by_text(re.compile("^目标数字人："))).to_have_count(0)
    expect(main(page).get_by_text(re.compile("^隐藏的是此数字人已关联"))).to_have_count(0)


def assert_reference_form(page: Page, title: str, labels: tuple[str, ...], submit: str) -> None:
    assert_reference_header(page, title)
    for label in labels:
        expect(main(page).get_by_role("textbox", name=label, exact=True)).to_be_visible()
    submit_box = bounds(button(main(page), submit))
    cancel_box = bounds(button(main(page), "取消"))
    assert abs(submit_box["y"] - cancel_box["y"]) <= 2
    assert submit_box["x"] + submit_box["width"] <= cancel_box["x"] + 2
    expect(button(main(page), "取消")).to_have_count(1)


def test_picker_preserves_selection_and_uses_server_exclusion(media_run: MediaRun) -> None:
    """Existing references are excluded before count/paging; new checks stay visible."""
    run, page, gate = media_run, media_run.page, media_run.gate
    run.goto("media-assets")
    existing = [run.seed_asset(f"already_{index}.png") for index in range(2)]
    candidates = [run.seed_asset(f"candidate_{index:02}.png") for index in range(5)]
    first_name, first_id = candidates[-1]
    assert run.api("PATCH", f"{ASSETS}/{first_id}", {"description": run.name("description_only")})[0] == 200
    human, human_id = run.seed_human("picker_target")
    for _name, asset_id in existing:
        run.link(human_id, asset_id)
    run.goto("digital-humans")
    start = len(gate.requests)
    picker(page, human, from_list=True)
    expect(main(page).get_by_role("checkbox")).to_have_count(5)  # Four cards + hide-existing switch.
    initial = gate.asset_pages[-1]
    assert initial["total"] == 5 and initial["limit"] == 4 and len(initial["items"]) == 4
    text(main(page), "已隐藏 2 项已添加素材")
    assert gate.associated_pages[-1]["total"] == 2 and gate.associated_pages[-1]["limit"] == 1
    assert not {row["asset_id"] for row in initial["items"]} & {aid for _, aid in existing}
    first_read = next(item for item in gate.requests[start:] if item.method == "GET" and item.path == ASSETS
                      and "digitalHumanId" not in item.query)
    assert first_read.query["excludeDigitalHumanId"] == [str(human_id)]
    text(main(page), "共 5 项 · 第 1 页")

    search(page, run.name("description_only"))
    expect(main(page).get_by_role("checkbox", name=first_name, exact=True)).to_be_visible()
    set_checkbox(page, first_name, True, keyboard=True)
    # The switch hides server-side references, never this unsaved local selection.
    expect(main(page).get_by_role("checkbox", name=first_name, exact=True)).to_be_checked()
    search(page, "")
    pagination = main(page).get_by_role("navigation", name="可选素材分页", exact=True)
    with page.expect_response(lambda response: response.request.method == "GET" and response.url.split("?")[0].endswith(ASSETS)
                              and "offset=4" in response.url):
        button(pagination, "下一页").click()
    expect(button(pagination, "下一页")).to_be_disabled()
    last_page = gate.asset_pages[-1]
    assert last_page["total"] == 5 and last_page["offset"] == 4 and len(last_page["items"]) == 1
    second = last_page["items"][0]
    second_name, second_id = second["filename"], second["asset_id"]
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
    heading(page, "上传并添加到「" + person + "」")
    expect(main(page).get_by_text("目标数字人：" + person, exact=True)).to_have_count(0)
    button(main(page), "取消").click()
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
        button(main(page), "开始上传").click()
    expect(main(page).get_by_role("progressbar", name="传输进度", exact=True)).to_be_visible()
    assert gate.store.held_put is not None and gate.store.puts == before_puts
    assert_layout(page, tmp_path, "upload_sending")
    with page.expect_request(lambda req: req.method == "POST" and req.url.endswith("/confirm")):
        gate.store.release_put()
    expect(main(page).get_by_text(re.compile("正在确认入库")).first).to_be_visible()
    expect(main(page).get_by_role("progressbar", name="传输完成", exact=True)).to_have_attribute("aria-valuenow", "100")
    uploaded_id, = gate.assets - before_assets
    confirm_path = f"{ASSETS}/{uploaded_id}/confirm"
    assert gate.store.puts == before_puts + 1 and gate.calls["POST", attach_path] == 0
    with db() as session:
        assert session.get(MediaAsset, uploaded_id).status == "awaiting_upload"
    assert_layout(page, tmp_path, "upload_confirming")
    gate.release(status=503)
    expect(button(main(page), "重试入库")).to_be_visible()
    expect(main(page).get_by_role("alert")).to_be_visible()
    assert_layout(page, tmp_path, "upload_confirm_error")
    gate.fail("POST", attach_path, 500)
    button(main(page), "重试入库").click()
    button(main(page), "重试添加").wait_for()
    with db() as session:
        assert session.get(MediaAsset, uploaded_id).status == "ready"
    assert run.links(human_id) == set()
    expect(main(page).get_by_role("alert")).to_be_visible()
    assert_layout(page, tmp_path, "upload_attach_error")
    button(main(page), "重试添加").click()
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


def test_tag_editor_upload_pending_input_failed_save_and_shared_chips(media_run: MediaRun) -> None:
    run, page, gate = media_run, media_run.page, media_run.gate
    run.goto("media-assets")
    existing_tag = run.name("existing tag")
    run.seed_asset("tag_candidate.png", tags=[existing_tag])
    search(page, run.name("unmatched_filter"))
    filename = run.name("tagged_upload.png")
    comma_tag = run.name("comma,stays_one_" + "长标签" * 25)
    assert len(comma_tag) > 64
    pending_tag = run.name("upload_pending")
    button(main(page), "上传素材").click()
    heading(page, "上传素材")
    cancelled_upload = run.name("cancel_upload")
    main(page).get_by_role("textbox", name="新增标签", exact=True).fill(cancelled_upload)
    button(main(page), "添加标签").click()
    button(main(page), "取消").click()
    button(dialog(page), "确认放弃").click()
    assert cancelled_upload not in tags(run)
    button(main(page), "上传素材").click()
    expect(main(page).get_by_role("row", name=cancelled_upload, exact=True)).to_have_count(0)
    pick_file(run, filename)
    existing = main(page).get_by_role("row", name=existing_tag, exact=True)
    existing.click()
    expect(existing).to_have_attribute("aria-selected", "true")
    entry = main(page).get_by_role("textbox", name="新增标签", exact=True)
    button(main(page), "添加标签").click()
    text(main(page), "请输入标签名称。")
    entry.fill(existing_tag.upper())
    button(main(page), "添加标签").click()
    expect(main(page).get_by_role("row", name=existing_tag.upper(), exact=True)).to_have_count(0)
    expect(existing).to_have_attribute("aria-selected", "true")
    text(main(page), "已选 1 个 · 可多选，再次点击取消")
    text(main(page), "已选中现有标签「" + existing_tag + "」。")
    before = gate.calls["POST", ASSETS + "/upload-url"]
    entry.fill("  " + comma_tag + "  ")
    entry.press("Enter")
    expect(entry).to_have_value("")
    expect(main(page).get_by_role("row", name=comma_tag, exact=True)).to_have_attribute("aria-selected", "true")
    assert gate.calls["POST", ASSETS + "/upload-url"] == before
    assert comma_tag not in tags(run)
    entry.fill(" " + comma_tag + " ")
    button(main(page), "添加标签").click()
    expect(main(page).get_by_role("row", name=comma_tag, exact=True)).to_have_count(1)
    entry.fill("  " + existing_tag.replace("existing tag", "existing\u00a0 \u3000tag") + "  ")
    button(main(page), "添加标签").click()
    expect(main(page).get_by_role("row", name=existing_tag, exact=True)).to_have_count(1)
    expect(existing).to_have_attribute("aria-selected", "true")
    entry.fill("  " + pending_tag + "  ")  # Save/upload must consume an input not explicitly added.
    gate.fail("POST", ASSETS + "/upload-url", 400)
    button(main(page), "开始上传").click()
    expect(main(page).get_by_role("alert")).to_be_visible()
    expect(entry).to_have_value("  " + pending_tag + "  ")
    expect(existing).to_have_attribute("aria-selected", "true")
    assert pending_tag not in tags(run)
    button(main(page), "开始上传").click()
    heading(page, filename)
    expect(main(page).get_by_text("已清除筛选并返回第一页。", exact=False)).to_be_visible()
    asset_id = run.asset_id(filename)
    expected = {existing_tag, comma_tag, pending_tag}
    with db() as session:
        stored = session.get(MediaAsset, asset_id)
        assert set(stored.tags) == expected and len(stored.tags) == 3
    for tag in expected:
        text(main(page), tag)
    assert expected <= set(tags(run))
    ticket_request = [item for item in gate.requests if item.path == ASSETS + "/upload-url"][-1]
    assert set(ticket_request.body["tags"]) == expected
    button(main(page), "返回素材库").click()
    expect(main(page).get_by_role("textbox", name="搜索素材", exact=True)).to_have_value("")
    button(main(page), "查看 " + filename).wait_for()
    open_asset(page, filename)

    button(main(page), "编辑资料").click()
    heading(page, "编辑素材资料")
    cancelled = run.name("cancelled_candidate")
    entry.fill(cancelled)
    entry.press("Enter")
    button(main(page), "取消").click()
    button(dialog(page), "确认放弃").click()
    heading(page, filename)
    assert cancelled not in tags(run)
    button(main(page), "编辑资料").click()
    expect(main(page).get_by_role("row", name=cancelled, exact=True)).to_have_count(0)
    renamed = run.name("tagged_renamed.png")
    note = run.name("shared_description")
    final_tag = run.name("edit_pending")
    main(page).get_by_label("素材名称", exact=True).fill(renamed)
    main(page).get_by_label("素材说明", exact=True).fill(note)
    entry.fill(" " + final_tag + " ")
    gate.fail("PATCH", f"{ASSETS}/{asset_id}", 500)
    button(main(page), "保存资料").click()
    expect(main(page).get_by_role("alert")).to_be_visible()
    expect(entry).to_have_value(" " + final_tag + " ")
    expect(main(page).get_by_label("素材名称", exact=True)).to_have_value(renamed)
    assert final_tag not in tags(run) and run.asset_id(filename) == asset_id
    button(main(page), "保存资料").click()
    heading(page, renamed)
    expected.add(final_tag)
    with db() as session:
        stored = session.get(MediaAsset, asset_id)
        assert (stored.filename, stored.description) == (renamed, note)
        assert set(stored.tags) == expected and len(stored.tags) == 4
    person, human_id = run.seed_human("tag_reference")
    run.link(human_id, asset_id)
    run.goto(f"digital-humans?human={human_id}")
    heading(page, person)
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
    run.goto(f"media-assets?asset={asset_id}")
    heading(page, filename)
    button(main(page), "从素材库删除").click()
    expect(button(confirmation(page), "确认删除")).to_be_disabled()
    for person in (first, second):
        expect(confirmation(page).get_by_role("link", name=person, exact=True)).to_be_visible()
    expect(confirmation(page).get_by_text(re.compile("请先解除全部引用"))).to_be_visible()
    # UI preflight and the authoritative endpoint must agree, including archived references.
    status, body = run.api("DELETE", f"{ASSETS}/{asset_id}")
    assert status == 409 and body["detail"] == "ASSET_IN_USE"
    assert_preserved(run, asset_id, {first_id, second_id}, objects)
    button(confirmation(page), "取消").click()

    run.goto(f"digital-humans?human={first_id}")
    heading(page, first)
    remove_reference(page, filename)
    assert run.links(first_id) == set() and run.links(second_id) == {asset_id}
    run.goto(f"media-assets?asset={asset_id}")
    button(main(page), "从素材库删除").click()
    expect(button(confirmation(page), "确认删除")).to_be_disabled()
    expect(confirmation(page).get_by_role("link", name=second, exact=True)).to_be_visible()
    assert_preserved(run, asset_id, {second_id}, objects)
    button(confirmation(page), "取消").click()

    # Deleting a linked human must retain both the asset and the other human's reference.
    run.link(first_id, asset_id)
    run.goto(f"digital-humans?human={first_id}")
    button(main(page), "删除档案").click()
    button(confirmation(page), "确认删除").click()
    expect(dialog(page)).to_have_count(0)
    expect(button(main(page), "新建数字人")).to_be_visible()
    expect(button(main(page), "查看档案 " + first)).to_have_count(0)
    with db() as session:
        assert session.get(DigitalHuman, first_id).deleted_at is not None
        assert session.get(MediaAsset, asset_id).deleted_at is None
    assert run.links(first_id) == set() and run.links(second_id) == {asset_id}
    assert gate.store.objects == objects and gate.store.removes == 0

    run.goto(f"digital-humans?human={second_id}")
    heading(page, second)
    remove_reference(page, filename)
    assert run.links(second_id) == set()
    button(main(page), "恢复档案").click()
    heading(page, second)
    expect(button(main(page), "归档档案")).to_be_visible()
    button(main(page), "返回数字人列表").click()
    heading(page, "数字人列表")
    expect(button(main(page), "启用中")).to_have_attribute("aria-pressed", "true")
    with db() as session:
        assert session.get(DigitalHuman, second_id).status == "active"
    run.goto(f"media-assets?asset={asset_id}")
    button(main(page), "从素材库删除").click()
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
    _filename, asset_id = run.seed_asset("delete_race.png")
    person, human_id = run.seed_human("late_reference")
    objects = dict(gate.store.objects)
    run.goto(f"media-assets?asset={asset_id}")
    button(main(page), "从素材库删除").click()
    expect(button(confirmation(page), "确认删除")).to_be_enabled()
    run.link(human_id, asset_id)  # Genuine API/SQL change while the original confirmation remains open.
    button(confirmation(page), "确认删除").click()
    expect(confirmation(page).get_by_role("alert")).to_contain_text("引用")
    expect(confirmation(page).get_by_role("link", name=person, exact=True)).to_be_visible()
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
    gate.fail("GET", re.compile(rf"{HUMANS}/[1-9][0-9]*"), 503)
    button(main(page), "创建档案").click()
    button(main(page), "重试数字人档案").wait_for()
    expect(main(page).get_by_text(re.compile("档案已创建")).first).to_be_visible()
    button(main(page), "重试数字人档案").click()
    heading(page, person)
    human_id = run.human_id(person)
    assert gate.calls["POST", HUMANS] == before_creates + 1
    picker(page, person)
    choose(page, filename)
    assert run.links(human_id) == set()
    button(main(page), "取消").click()
    button(dialog(page), "确认放弃").click()
    heading(page, person)
    assert run.links(human_id) == set() and gate.calls["POST", f"{HUMANS}/{human_id}/assets"] == 0
    button(main(page), "编辑档案").click()
    draft = run.name("discard_me")
    main(page).get_by_label("数字人名称", exact=True).fill(draft)
    button(main(page), "取消").click()
    button(dialog(page), "继续处理").click()
    expect(main(page).get_by_label("数字人名称", exact=True)).to_have_value(draft)
    button(main(page), "取消").click()
    button(dialog(page), "确认放弃").click()
    heading(page, person)
    button(main(page), "返回数字人列表").click()
    open_human(page, other_name)
    expect(main(page).get_by_text(draft, exact=True)).to_have_count(0)
    picker(page, other_name)
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
    heading(page, "全局素材库")
    image_ready(page, filename)
    text(main(page), chip)
    text(main(page), "1 个数字人引用")
    assert run.gate.asset_pages[-1]["items"][0]["usageCount"] == 1
    assert_layout(page, tmp_path, "media_library")
    open_asset(page, filename)
    image_ready(page, filename)
    expect(main(page).get_by_role("textbox", name="搜索素材", exact=True)).to_have_count(0)
    expect(main(page).get_by_role("link", name=person, exact=True)).to_be_visible()
    assert_layout(page, tmp_path, "asset_detail")
    main(page).get_by_role("link", name=person, exact=True).click()
    heading(page, person)
    text(main(page), chip)
    refs = main(page).get_by_role("region", name="引用素材", exact=True)
    text(refs, "引用素材 · 1 项")
    expect(refs.locator("img, video")).to_have_count(0)
    assert_layout(page, tmp_path, "human_detail")
    button(main(page), "返回数字人列表").click()
    picker(page, person, from_list=True)
    text(main(page), "本次新增 0 项")
    expect(button(main(page), "确认添加（0）")).to_be_disabled()
    expect(main(page).get_by_role("checkbox", name=filename, exact=True)).to_have_count(0)
    set_checkbox(page, "隐藏已选内容", False)
    expect(main(page).get_by_role("checkbox", name=filename, exact=True)).to_be_disabled()
    image_ready(page, filename, scope=open_picker_preview(page, filename))
    assert_layout(page, tmp_path, "asset_picker")


def seed_reference_content(run: MediaRun, tmp_path: Path) -> tuple[list[tuple[str, int]], list[tuple[str, int]], list[dict]]:
    """Reuse the accepted prototype's content, with real PNG/WebM bytes and owned names."""
    asset_specs = [
        {"id": 1, "name": "白底半身照", "type": "图片", "description": "用于档案头像与商品讲解开场", "tags": ["人像", "白底"]},
        {"id": 2, "name": "厨房台面背景", "type": "图片", "description": "暖色厨房场景", "tags": ["场景"]},
        {"id": 3, "name": "三秒开场口播", "type": "视频", "description": "竖屏开场动作示例", "tags": ["开场"]},
        {"id": 4, "name": "手持商品特写", "type": "视频", "description": "补充展示商品细节", "tags": ["特写"]},
        {"id": 5, "name": "侧脸参考照", "type": "图片", "description": "备用人像素材", "tags": ["人像"]},
        {"id": 6, "name": "收尾致谢", "type": "视频", "description": "短片结束片段", "tags": ["收尾"]},
    ]
    people_specs = [
        {"id": 1, "name": "林溪", "description": "家居好物讲解", "archived": False, "links": [1, 3]},
        {"id": 2, "name": "周晴", "description": "厨房用品体验", "archived": False, "links": [2, 3]},
        {"id": 3, "name": "苏宁", "description": "旧版样片档案", "archived": True, "links": [4]},
    ]
    clip = (Path(__file__).resolve().parents[1] / "fixtures/media/TEST_preview.webm").read_bytes()
    assets, humans = {}, {}
    # API order is newest first; reverse creation keeps the reference's display order.
    for spec in reversed(asset_specs):
        video = spec["type"] == "视频"
        assets[spec["id"]] = run.seed_asset(
            spec["name"], tags=spec["tags"], description=spec["description"],
            content_type="video/webm" if video else "image/png", payload=clip if video else png(),
        )
    for spec in reversed(people_specs):
        humans[spec["id"]] = run.seed_human(spec["name"], description=spec["description"])
        if spec["archived"]:
            assert run.api("PATCH", f"{HUMANS}/{humans[spec['id']][1]}", {"status": "archived"})[0] == 200
        for reference in spec["links"]:
            run.link(humans[spec["id"]][1], assets[reference][1])
    # Root renders a copy of the immutable prototype with these same data values.
    # Reference ids are local 1..N; no real database identity is needed for rendering.
    viewport = run.page.viewport_size
    assert viewport is not None
    (tmp_path / "reference-dataset.json").write_text(json.dumps({
        "marker": run.marker, "width": viewport["width"], "height": viewport["height"],
        "contentWidth": bounds(main(run.page).locator("[data-media-header]"))["width"],
        "tagCatalog": tags(run),
        "assets": [{**spec, "name": assets[spec["id"]][0]} for spec in asset_specs],
        "people": [{**spec, "name": humans[spec["id"]][0]} for spec in people_specs],
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    return [assets[index] for index in range(1, 7)], [humans[index] for index in range(1, 4)], asset_specs


@pytest.mark.parametrize("media_run", [("admin", width, "/tts", "fidelity") for width in (375, 1007, 1440)], indirect=True)
def test_media_pages_match_confirmed_prototype_structure(media_run: MediaRun, tmp_path: Path) -> None:
    run, page, gate = media_run, media_run.page, media_run.gate
    run.goto("media-assets")
    assets, humans, specs = seed_reference_content(run, tmp_path)
    filename, asset_id = assets[0]
    person, human_id = humans[0]
    run.goto("media-assets")
    assert_reference_header(page, "全局素材库", "上传素材")
    expect(main(page).get_by_role("heading", name="素材列表", exact=True)).to_have_count(0)
    expect(main(page).get_by_text("筛选标签", exact=True)).to_have_count(0)
    expect(button(main(page), "清除筛选")).to_have_count(0)
    assert_reference_grid(page, 4)
    footer = main(page).get_by_role("navigation", name="素材分页", exact=True)
    text(footer, "共 6 项 · 第 1 页")
    expect(button(footer, "上一页")).to_be_disabled()
    expect(button(footer, "下一页")).to_be_enabled()
    for spec in specs[:4]:
        expect(main(page).get_by_text(spec["description"], exact=True)).not_to_be_visible()
    assert_layout(page, tmp_path, "fidelity_library")

    open_asset(page, filename)
    assert_reference_header(page, filename, "返回素材库")
    details = main(page).get_by_role("heading", name="素材资料", exact=True).locator("xpath=ancestor::section[1]")
    uses = main(page).get_by_role("heading", name="哪些数字人在用", exact=True).locator("xpath=ancestor::section[1]")
    assert bounds(details)["y"] + bounds(details)["height"] <= bounds(uses)["y"] + 2
    expect(button(details, "编辑资料")).to_be_visible()
    expect(button(details, "从素材库删除")).to_be_visible()
    image_ready(page, filename, scope=details)
    assert_layout(page, tmp_path, "fidelity_asset_detail")
    button(details, "编辑资料").click()
    assert_reference_form(page, "编辑素材资料", ("素材名称", "素材说明", "新增标签"), "保存资料")
    text(main(page), "已选 2 个 · 可多选，再次点击取消")
    assert_layout(page, tmp_path, "fidelity_asset_edit")
    button(main(page), "取消").click()

    run.goto(f"media-assets?asset={assets[2][1]}")
    heading(page, assets[2][0])
    shared_by = main(page).get_by_role("region", name="哪些数字人在用", exact=True)
    expect(shared_by.get_by_role("link")).to_have_text([humans[0][0], humans[1][0]])
    video_ready(page, assets[2][0])
    assert_layout(page, tmp_path, "fidelity_shared_asset_detail")
    shared_by.get_by_role("link", name=person, exact=True).click()
    assert_reference_header(page, person, "返回数字人列表")
    refs = main(page).get_by_role("region", name="引用素材", exact=True)
    text(refs, "引用素材 · 2 项")
    reference_rows = refs.locator('ul[aria-label="已引用素材"] > li')
    expect(reference_rows).to_have_count(2)
    expect(reference_rows.locator(":scope > strong")).to_have_text([assets[0][0], assets[2][0]])
    expect(refs.locator("img, video")).to_have_count(0)
    assert_layout(page, tmp_path, "fidelity_human_detail")
    button(main(page), "编辑档案").click()
    assert_reference_form(page, "编辑档案", ("数字人名称", "人设说明"), "保存资料")
    assert_layout(page, tmp_path, "fidelity_human_edit")
    button(main(page), "取消").click()

    picker(page, person)
    assert_reference_picker(page, person, 0)
    assert_reference_grid(page, 4)
    picker_footer = main(page).get_by_role("navigation", name="可选素材分页", exact=True)
    text(picker_footer, "共 4 项 · 第 1 页")
    expect(button(picker_footer, "上一页")).to_be_disabled()
    expect(button(picker_footer, "下一页")).to_be_disabled()
    assert_layout(page, tmp_path, "fidelity_picker")
    preview = open_picker_preview(page, assets[1][0])
    image_ready(page, assets[1][0], scope=preview)
    text(preview, specs[1]["description"])
    assert_layout(page, tmp_path, "fidelity_picker_preview")
    preview.locator("summary").click()
    expect(preview).to_have_js_property("open", False)
    set_checkbox(page, assets[4][0], True)
    expect(button(main(page), "取消勾选 " + assets[4][0])).to_be_visible()
    assert_reference_picker(page, person, 1)
    assert_layout(page, tmp_path, "fidelity_picker_selected")
    button(main(page), "取消").click()
    button(dialog(page), "确认放弃").click()
    assert run.links(human_id) == {assets[0][1], assets[2][1]}

    picker(page, person)
    set_checkbox(page, assets[4][0], True)
    submit_selection(page, 1)
    heading(page, person)
    ordered_rows = main(page).locator('ul[aria-label="已引用素材"] > li > strong')
    expect(ordered_rows).to_have_text([assets[0][0], assets[2][0], assets[4][0]])
    assert_layout(page, tmp_path, "fidelity_human_after_add")
    run.link(human_id, assets[2][1])  # Replaying an existing link must not move its row.
    run.goto(f"digital-humans?human={human_id}")
    heading(page, person)
    expect(main(page).locator('ul[aria-label="已引用素材"] > li > strong')).to_have_text(
        [assets[0][0], assets[2][0], assets[4][0]],
    )
    assert run.links(human_id) == {assets[0][1], assets[2][1], assets[4][1]}
    remove_reference(page, assets[4][0])
    expect(main(page).locator('ul[aria-label="已引用素材"] > li > strong')).to_have_text([assets[0][0], assets[2][0]])

    button(main(page), "归档档案").click()
    assert_reference_header(page, "归档「" + person + "」")
    confirm = confirmation(page)
    assert gate.calls["PATCH", f"{HUMANS}/{human_id}"] == 0
    assert_layout(page, tmp_path, "fidelity_archive_confirmation")
    button(confirm, "确认归档").click()
    heading(page, "数字人列表")
    expect(button(main(page), "已归档")).to_have_attribute("aria-pressed", "true")
    expect(button(main(page), "添加素材 " + person)).to_have_count(0)
    assert_layout(page, tmp_path, "fidelity_archived_list")
    button(main(page), "恢复档案 " + person).click()
    heading(page, person)
    assert run.links(human_id) == {assets[0][1], assets[2][1]}
    button(main(page), "返回数字人列表").click()
    assert_reference_header(page, "数字人列表", "新建数字人")
    expect(button(main(page), "启用中")).to_have_attribute("aria-pressed", "true")
    expect(main(page).get_by_role("textbox", name="搜索数字人", exact=True)).to_have_count(0)
    assert_layout(page, tmp_path, "fidelity_active_list")
    button(main(page), "新建数字人").click()
    assert_reference_form(page, "新建数字人", ("数字人名称", "人设说明"), "创建档案")
    assert_layout(page, tmp_path, "fidelity_human_create")
    button(main(page), "取消").click()
    open_human(page, person)
    button(main(page), "删除档案").click()
    confirmation(page)
    assert_layout(page, tmp_path, "fidelity_delete_human_confirmation")
    button(confirmation(page), "取消").click()
    button(main(page), "移除引用 " + filename).click()
    confirmation(page)
    assert_layout(page, tmp_path, "fidelity_unlink_confirmation")
    button(confirmation(page), "取消").click()

    picker(page, person)
    button(main(page), "上传新素材").click()
    assert_reference_form(page, "上传并添加到「" + person + "」", ("素材名称", "新增标签"), "开始上传")
    assert_layout(page, tmp_path, "fidelity_target_upload_empty")
    pick_file(run, run.name("本地预览.png"))
    assert_layout(page, tmp_path, "fidelity_target_upload_preview")
    button(main(page), "取消").click()
    button(dialog(page), "确认放弃").click()
    button(main(page), "取消").click()
    heading(page, person)
    open_reference(page, assets[2][0])
    assert not gate.allows("GET", "/v2/pages/media-assets", {"asset": [str(asset_id)], "fromHuman": ["0"]}, None)
    button(main(page), "从素材库删除").click()
    expect(button(confirmation(page), "确认删除")).to_be_disabled()
    assert_layout(page, tmp_path, "fidelity_referenced_delete_confirmation")
    button(confirmation(page), "取消").click()
    run.goto("media-assets")
    button(main(page), "上传素材").click()
    assert_reference_form(page, "上传素材", ("素材名称", "新增标签"), "开始上传")
    assert_layout(page, tmp_path, "fidelity_library_upload_empty")
    before = gate.calls["POST", ASSETS + "/upload-url"]
    pick_file(run, run.name("独立本地预览.png"))
    assert_layout(page, tmp_path, "fidelity_library_upload_preview")
    assert gate.calls["POST", ASSETS + "/upload-url"] == before
    button(main(page), "取消").click()
    button(dialog(page), "确认放弃").click()


def test_video_local_preview_upload_and_shared_metadata_match(media_run: MediaRun, tmp_path: Path) -> None:
    """A legal VP8 fixture must decode natively before and after the real upload flow."""
    run, page, gate = media_run, media_run.page, media_run.gate
    fixture = Path(__file__).resolve().parents[1] / "fixtures" / "media" / "TEST_preview.webm"
    payload = fixture.read_bytes()
    filename = run.name("metadata_clip.webm")
    run.goto("media-assets")
    button(main(page), "上传素材").click()
    heading(page, "上传素材")
    main(page).locator('input[type="file"]').set_input_files({
        "name": filename, "mimeType": "video/webm", "buffer": payload,
    })
    local_metadata = video_ready(page, filename, local=True)
    assert (local_metadata["width"], local_metadata["height"]) == (320, 180)
    assert local_metadata["duration"] == pytest.approx(1.0, abs=0.05)
    assert gate.calls["POST", ASSETS + "/upload-url"] == gate.store.puts == 0
    assert gate.assets == set() and gate.store.objects == {}
    assert_layout(page, tmp_path, "local_video_preview")

    button(main(page), "开始上传").click()
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
    button(main(page), "返回素材库").click()
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
    heading(page, person)
    refs = main(page).get_by_role("region", name="引用素材", exact=True)
    text(refs, "引用素材 · 1 项")
    expect(refs.locator("img, video")).to_have_count(0)
    assert run.links(human_id) == {asset_id} and gate.calls["POST", attach_path] == 1
    assert_layout(page, tmp_path, "shared_video_detail")

    open_reference(page, filename)
    assert video_ready(page, filename) == local_metadata
    status, metadata = run.api("GET", f"{ASSETS}/{asset_id}")
    assert status == 200 and isinstance(metadata, dict) and metadata["usageCount"] == 1
    expect(main(page).get_by_role("link", name=person, exact=True)).to_be_visible()
    assert gate.store.objects == {object_key: ("video/webm", payload)} and gate.store.removes == 0


@pytest.mark.parametrize("media_run", [("viewer", 1440, "")], indirect=True)
def test_viewer_media_permission_denial(media_run: MediaRun) -> None:
    """The existing readonly role lacks page grants and cannot enter either workflow."""
    for slug in ("media-assets", "digital-humans"):
        response = media_run.goto(slug)
        assert response is not None and response.status == 403
    assert media_run.gate.calls["GET", ASSETS] == media_run.gate.calls["GET", HUMANS] == 0
