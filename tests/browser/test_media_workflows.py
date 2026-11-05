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
from playwright.sync_api import Locator, Page, Response, expect, sync_playwright
from render_support import overflow_report
from sqlalchemy import delete, or_, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from tests.support.frontend_p2_binding import checked_session, current_binding, validate_application
from tests.support.frontend_p2_browser import fill_sensitive
from tests.support.media_browser import APP, ASSETS, HUMANS, LOGIN, ME, MediaBrowserBridge, MediaBytes
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

    def seed_asset(self, suffix: str, *, tags: list[str] | None = None, description: str = "") -> tuple[str, int]:
        """Fixtures still issue a real upload ticket, PUT bytes and confirm them."""
        name = self.name(suffix)
        status, ticket = self.api("POST", ASSETS + "/upload-url", {
            "filename": name, "contentType": "image/png", "sizeBytes": len(png()), "tags": tags or [],
        })
        assert status == 201 and isinstance(ticket, dict), ticket
        put_status = self.page.evaluate("""async ({url, headers, payload}) => {
            const response = await fetch(url, {
                method: 'PUT', credentials: 'omit', headers, body: new Uint8Array(payload),
            });
            return response.status;
        }""", {"url": ticket["upload_url"], "headers": ticket["required_headers"], "payload": list(png())})
        assert put_status == 204
        asset_id = ticket["asset_id"]
        assert self.api("POST", f"{ASSETS}/{asset_id}/confirm")[0] == 200
        if description:
            assert self.api("PATCH", f"{ASSETS}/{asset_id}", {"description": description})[0] == 200
        return name, asset_id

    def seed_human(self, suffix: str) -> tuple[str, int]:
        name = self.name(suffix)
        status, human = self.api("POST", HUMANS, {"name": name, "description": "TEST_persona_description"})
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
    role, width, prefix = getattr(request, "param", ("admin", 1440, ""))
    value, binding = current_binding()
    monkeypatch.setenv("TTS_ERP_DB_URL", value)
    monkeypatch.setenv("TTS_ERP_AUTH_MODE", "enforce")
    monkeypatch.setenv("TTS_ERP_SESSION_SECURE", "1")
    monkeypatch.setenv("TTS_ERP_EXTERNAL_PREFIX", prefix)
    monkeypatch.delenv("TTS_ERP_FRONTEND_DEV_ORIGIN", raising=False)
    fixture_id = uuid4()
    marker = "TEST_media_" + fixture_id.hex
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
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            try:
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
                finally:
                    gate.closing = True
                    context.close()
            finally:
                browser.close()
    finally:
        client.close()
        clear_owned(gate, user_id, username)


def dialog(page: Page) -> Locator:
    return page.locator('[role="dialog"]:visible, [role="alertdialog"]:visible').last


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
    with page.expect_response(lambda response: response.request.method == "GET" and response.url.split("?")[0].endswith(ASSETS)):
        page.get_by_role("option", name=label, exact=True).click()


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


def image_ready(page: Page, name: str) -> None:
    image = main(page).get_by_role("img", name="图片预览 " + name, exact=True).first
    expect(image).to_be_visible()
    page.wait_for_function(
        "img => img.complete && img.naturalWidth === 256 && img.naturalHeight === 128",
        arg=image.element_handle(),
    )


def video_ready(page: Page, name: str, *, local: bool = False) -> dict[str, int | float]:
    label = ("本地视频预览 " if local else "视频预览 ") + name
    video = main(page).get_by_label(label, exact=True)
    expect(video).to_be_visible()
    if local:
        expect(video).to_have_attribute("src", re.compile(r"^blob:"))
    page.wait_for_function("""video => video.readyState >= HTMLMediaElement.HAVE_METADATA
        && video.videoWidth > 0 && video.videoHeight > 0
        && Number.isFinite(video.duration) && video.duration > 0""", arg=video.element_handle())
    return video.evaluate("video => ({width: video.videoWidth, height: video.videoHeight, duration: video.duration})")


def open_asset(page: Page, name: str) -> None:
    button(main(page), "查看 " + name).click()
    heading(page, "素材详情 · " + name)


def open_human(page: Page, name: str) -> None:
    button(main(page), "查看档案 " + name).click()
    heading(page, "数字人档案 · " + name)


def picker(page: Page, name: str, *, from_list: bool = False) -> None:
    button(main(page), "添加素材 " + name if from_list else "选择已有素材").click()
    heading(page, "选择已有素材 · " + name)
    text(main(page), "目标数字人：" + name)
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
    button(main(page), f"确认添加({count})").click()


def remove_reference(page: Page, name: str) -> None:
    button(main(page), "从当前数字人移除 " + name).click()
    button(dialog(page), "确认移除").click()
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
    dimensions = overflow_report(page)
    assert dimensions["scrollWidth"] <= dimensions["clientWidth"], dimensions
    expect(page.get_by_role("banner")).to_have_count(1)
    expect(page.get_by_role("main")).to_have_count(1)
    viewport = page.viewport_size
    assert viewport is not None
    page.screenshot(path=tmp_path / f"TEST_{label}_{viewport['width']}.png", animations="disabled")


def test_picker_preserves_selection_and_uses_server_exclusion(media_run: MediaRun) -> None:
    """Existing references are excluded before count/paging; new checks stay visible."""
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
    picker(page, human, from_list=True)
    expect(main(page).get_by_role("checkbox")).to_have_count(13)  # 12 cards + hide-existing switch
    initial = gate.asset_pages[-1]
    assert initial["total"] == 13 and len(initial["items"]) == 12
    assert not {row["asset_id"] for row in initial["items"]} & {aid for _, aid in existing}
    first_read = next(item for item in gate.requests[start:] if item.method == "GET" and item.path == ASSETS)
    assert first_read.query["excludeDigitalHumanId"] == [str(human_id)]
    text(main(page), "共 13 项可选素材")

    search(page, run.name("description_only"))
    expect(main(page).get_by_role("checkbox", name=first_name, exact=True)).to_be_visible()
    set_checkbox(page, first_name, True, keyboard=True)
    # The switch hides server-side references, never this unsaved local selection.
    expect(main(page).get_by_role("checkbox", name=first_name, exact=True)).to_be_checked()
    search(page, "")
    pagination = main(page).get_by_role("navigation", name="可选素材分页", exact=True)
    with page.expect_response(lambda response: response.request.method == "GET" and response.url.split("?")[0].endswith(ASSETS)
                              and "offset=12" in response.url):
        button(pagination, "下一页").click()
    expect(button(pagination, "下一页")).to_be_disabled()
    last_page = gate.asset_pages[-1]
    assert last_page["total"] == 13 and last_page["offset"] == 12 and len(last_page["items"]) == 1
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
    heading(page, "数字人档案 · " + human)
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
    heading(page, "上传素材并添加到 " + person)
    text(main(page), "目标数字人：" + person)
    button(main(page), "返回选择素材").click()
    heading(page, "选择已有素材 · " + person)
    expect(main(page).get_by_role("textbox", name="搜索素材", exact=True)).to_have_value(second)
    text(main(page), "本次新增 2 项")
    button(main(page), "上传新素材").click()
    uploaded = run.name("uploaded_C.png")
    pick_file(run, uploaded)
    before_assets = set(gate.assets)
    before_tickets = gate.calls["POST", ASSETS + "/upload-url"]
    before_puts = gate.store.puts
    attach_path = f"{HUMANS}/{human_id}/assets"
    gate.hold("POST", re.compile(rf"{ASSETS}/[1-9][0-9]*/confirm"))
    with page.expect_request(lambda req: req.method == "POST" and req.url.endswith("/confirm")):
        button(main(page), "开始上传并添加").click()
    expect(main(page).get_by_text(re.compile("正在确认入库")).first).to_be_visible()
    uploaded_id, = gate.assets - before_assets
    confirm_path = f"{ASSETS}/{uploaded_id}/confirm"
    assert gate.store.puts == before_puts + 1 and gate.calls["POST", attach_path] == 0
    with db() as session:
        assert session.get(MediaAsset, uploaded_id).status == "awaiting_upload"
    assert_layout(page, tmp_path, "upload_confirming")
    gate.release(status=503)
    gate.fail("POST", attach_path, 500)
    button(main(page), "重试入库确认").click()
    button(main(page), "重试添加资产").wait_for()
    with db() as session:
        assert session.get(MediaAsset, uploaded_id).status == "ready"
    assert run.links(human_id) == set()
    button(main(page), "重试添加资产").click()
    heading(page, "选择已有素材 · " + person)
    text(main(page), "本次新增 2 项")
    expect(main(page).get_by_role("textbox", name="搜索素材", exact=True)).to_have_value(second)
    expect(main(page).get_by_role("checkbox", name=second, exact=True)).to_be_checked()
    choose(page, first)
    assert run.links(human_id) == {uploaded_id} and run.links(other_id) == set()
    assert gate.calls["POST", ASSETS + "/upload-url"] == before_tickets + 1
    assert gate.store.puts == before_puts + 1
    assert gate.calls["POST", confirm_path] == gate.calls["POST", attach_path] == 2
    submit_selection(page, 2)
    heading(page, "数字人档案 · " + person)
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
    button(main(page), "取消上传").click()
    button(dialog(page), "确认放弃").click()
    assert cancelled_upload not in tags(run)
    button(main(page), "上传素材").click()
    expect(main(page).get_by_role("row", name=cancelled_upload, exact=True)).to_have_count(0)
    pick_file(run, filename)
    existing = main(page).get_by_role("row", name=existing_tag, exact=True)
    existing.click()
    expect(existing).to_have_attribute("aria-selected", "true")
    entry = main(page).get_by_role("textbox", name="新增标签", exact=True)
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
    heading(page, "素材详情 · " + filename)
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
    heading(page, "编辑素材 · " + filename)
    cancelled = run.name("cancelled_candidate")
    entry.fill(cancelled)
    entry.press("Enter")
    button(main(page), "取消编辑").click()
    button(dialog(page), "确认放弃").click()
    heading(page, "素材详情 · " + filename)
    assert cancelled not in tags(run)
    button(main(page), "编辑资料").click()
    expect(main(page).get_by_role("row", name=cancelled, exact=True)).to_have_count(0)
    renamed = run.name("tagged_renamed.png")
    note = run.name("shared_description")
    final_tag = run.name("edit_pending")
    main(page).get_by_label("显示名称", exact=True).fill(renamed)
    main(page).get_by_label("说明", exact=True).fill(note)
    entry.fill(" " + final_tag + " ")
    gate.fail("PATCH", f"{ASSETS}/{asset_id}", 500)
    button(main(page), "保存资料").click()
    expect(main(page).get_by_role("alert")).to_be_visible()
    expect(entry).to_have_value(" " + final_tag + " ")
    expect(main(page).get_by_label("显示名称", exact=True)).to_have_value(renamed)
    assert final_tag not in tags(run) and run.asset_id(filename) == asset_id
    button(main(page), "保存资料").click()
    heading(page, "素材详情 · " + renamed)
    expected.add(final_tag)
    with db() as session:
        stored = session.get(MediaAsset, asset_id)
        assert (stored.filename, stored.description) == (renamed, note)
        assert set(stored.tags) == expected and len(stored.tags) == 4
    person, human_id = run.seed_human("tag_reference")
    run.link(human_id, asset_id)
    run.goto(f"digital-humans?human={human_id}")
    heading(page, "数字人档案 · " + person)
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
    heading(page, "素材详情 · " + filename)
    button(main(page), "从素材库删除").click()
    expect(button(dialog(page), "确认删除")).to_be_disabled()
    for person in (first, second):
        expect(dialog(page).get_by_role("link", name=person, exact=True)).to_be_visible()
    expect(dialog(page).get_by_text(re.compile("请先解除全部引用"))).to_be_visible()
    # UI preflight and the authoritative endpoint must agree, including archived references.
    status, body = run.api("DELETE", f"{ASSETS}/{asset_id}")
    assert status == 409 and body["detail"] == "ASSET_IN_USE"
    assert_preserved(run, asset_id, {first_id, second_id}, objects)
    button(dialog(page), "取消").click()

    run.goto(f"digital-humans?human={first_id}")
    heading(page, "数字人档案 · " + first)
    remove_reference(page, filename)
    assert run.links(first_id) == set() and run.links(second_id) == {asset_id}
    run.goto(f"media-assets?asset={asset_id}")
    button(main(page), "从素材库删除").click()
    expect(button(dialog(page), "确认删除")).to_be_disabled()
    expect(dialog(page).get_by_role("link", name=second, exact=True)).to_be_visible()
    assert_preserved(run, asset_id, {second_id}, objects)
    button(dialog(page), "取消").click()

    # Deleting a linked human must retain both the asset and the other human's reference.
    run.link(first_id, asset_id)
    run.goto(f"digital-humans?human={first_id}")
    button(main(page), "删除数字人").click()
    button(dialog(page), "确认删除").click()
    expect(dialog(page)).to_have_count(0)
    expect(button(main(page), "新建数字人")).to_be_visible()
    expect(button(main(page), "查看档案 " + first)).to_have_count(0)
    with db() as session:
        assert session.get(DigitalHuman, first_id).deleted_at is not None
        assert session.get(MediaAsset, asset_id).deleted_at is None
    assert run.links(first_id) == set() and run.links(second_id) == {asset_id}
    assert gate.store.objects == objects and gate.store.removes == 0

    run.goto(f"digital-humans?human={second_id}")
    heading(page, "数字人档案 · " + second)
    remove_reference(page, filename)
    assert run.links(second_id) == set()
    button(main(page), "恢复为启用").click()
    button(main(page), "归档数字人").wait_for()
    with db() as session:
        assert session.get(DigitalHuman, second_id).status == "active"
    run.goto(f"media-assets?asset={asset_id}")
    button(main(page), "从素材库删除").click()
    expect(button(dialog(page), "确认删除")).to_be_enabled()
    button(dialog(page), "确认删除").click()
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
    person, human_id = run.seed_human("late_reference")
    objects = dict(gate.store.objects)
    run.goto(f"media-assets?asset={asset_id}")
    button(main(page), "从素材库删除").click()
    expect(button(dialog(page), "确认删除")).to_be_enabled()
    run.link(human_id, asset_id)  # Genuine API/SQL change while the original confirmation remains open.
    button(dialog(page), "确认删除").click()
    expect(dialog(page).get_by_role("alert")).to_contain_text("引用")
    expect(dialog(page).get_by_role("link", name=person, exact=True)).to_be_visible()
    expect(button(dialog(page), "确认删除")).to_be_disabled()
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
    heading(page, "素材详情 · " + filename)
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
    main(page).get_by_label("名称", exact=True).fill(person)
    main(page).get_by_label("人设说明", exact=True).fill("TEST_persona_description")
    gate.fail("GET", re.compile(rf"{HUMANS}/[1-9][0-9]*"), 503)
    button(main(page), "创建数字人").click()
    button(main(page), "重试数字人档案").wait_for()
    expect(main(page).get_by_text(re.compile("已创建数字人")).first).to_be_visible()
    button(main(page), "重试数字人档案").click()
    heading(page, "数字人档案 · " + person)
    human_id = run.human_id(person)
    assert gate.calls["POST", HUMANS] == before_creates + 1
    picker(page, person)
    choose(page, filename)
    assert run.links(human_id) == set()
    button(main(page), "取消").click()
    button(dialog(page), "确认放弃").click()
    heading(page, "数字人档案 · " + person)
    assert run.links(human_id) == set() and gate.calls["POST", f"{HUMANS}/{human_id}/assets"] == 0
    button(main(page), "编辑资料").click()
    draft = run.name("discard_me")
    main(page).get_by_label("名称", exact=True).fill(draft)
    button(main(page), "返回档案").click()
    button(dialog(page), "继续处理").click()
    expect(main(page).get_by_label("名称", exact=True)).to_have_value(draft)
    button(main(page), "返回档案").click()
    button(dialog(page), "确认放弃").click()
    heading(page, "数字人档案 · " + person)
    button(main(page), "返回数字人列表").click()
    open_human(page, other_name)
    expect(main(page).get_by_text(draft, exact=True)).to_have_count(0)
    picker(page, other_name)
    expect(main(page).get_by_role("checkbox", name=filename, exact=True)).not_to_be_checked()
    text(main(page), "本次新增 0 项")
    assert run.links(other_id) == set() and run.human_id(person) == human_id
    assert gate.calls["PATCH", f"{HUMANS}/{human_id}"] == 0


@pytest.mark.parametrize("media_run", [("admin", width, "/tts") for width in (375, 1440, 2560)], indirect=True)
def test_media_pages_share_shell_and_render_real_previews_at_supported_widths(media_run: MediaRun, tmp_path: Path) -> None:
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
    heading(page, "数字人档案 · " + person)
    text(main(page), chip)
    image_ready(page, filename)
    assert_layout(page, tmp_path, "human_detail")
    button(main(page), "返回数字人列表").click()
    picker(page, person, from_list=True)
    text(main(page), "本次新增 0 项")
    expect(button(main(page), "确认添加(0)")).to_be_disabled()
    expect(main(page).get_by_role("checkbox", name=filename, exact=True)).to_have_count(0)
    set_checkbox(page, "隐藏已选内容", False)
    expect(main(page).get_by_role("checkbox", name=filename, exact=True)).to_be_disabled()
    image_ready(page, filename)
    assert_layout(page, tmp_path, "asset_picker")


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
    heading(page, "素材详情 · " + filename)
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
    assert video_ready(page, filename) == local_metadata
    set_checkbox(page, filename, True)
    attach_path = f"{HUMANS}/{human_id}/assets"
    assert gate.calls["POST", attach_path] == 0 and run.links(human_id) == set()
    submit_selection(page, 1)
    heading(page, "数字人档案 · " + person)
    assert video_ready(page, filename) == local_metadata
    text(main(page).get_by_role("region", name="引用素材", exact=True), "1 个数字人引用")
    assert run.links(human_id) == {asset_id} and gate.calls["POST", attach_path] == 1
    assert_layout(page, tmp_path, "shared_video_detail")

    run.goto(f"media-assets?asset={asset_id}")
    heading(page, "素材详情 · " + filename)
    assert video_ready(page, filename) == local_metadata
    text(main(page), "1 个数字人引用")
    expect(main(page).get_by_role("link", name=person, exact=True)).to_be_visible()
    assert gate.store.objects == {object_key: ("video/webm", payload)} and gate.store.removes == 0


@pytest.mark.parametrize("media_run", [("viewer", 1440, "")], indirect=True)
def test_viewer_media_permission_denial(media_run: MediaRun) -> None:
    """The existing readonly role lacks page grants and cannot enter either workflow."""
    for slug in ("media-assets", "digital-humans"):
        response = media_run.goto(slug)
        assert response is not None and response.status == 403
    assert media_run.gate.calls["GET", ASSETS] == media_run.gate.calls["GET", HUMANS] == 0
