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

    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack("!IIBBBBB", 1, 1, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(b"\x00\x12\x74\xb4"))
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
    marker = "TEST_media_" + uuid4().hex
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
        app, base_url=APP, follow_redirects=False, raise_server_exceptions=False, client=("127.0.0.27", 50000),
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
    expect(scope.get_by_text(value, exact=True).first).to_be_visible()


def heading(page: Page, name: str) -> None:
    expect(dialog(page).get_by_role("heading", name=re.compile(re.escape(name) + r"$")).first).to_be_visible()


def close(page: Page) -> None:
    button(dialog(page), "关闭窗口").click()


def search(scope: Page | Locator, label: str, query: str) -> None:
    scope.get_by_role("textbox", name=label, exact=True).fill(query)
    button(scope, "搜索").click()


def pick_file(page: Page, name: str) -> None:
    dialog(page).locator('input[type="file"]').set_input_files({
        "name": name, "mimeType": "image/png", "buffer": png(),
    })


def create_persona(page: Page, name: str, *, detail_fails: bool = False) -> None:
    button(page, "新建数字人").click()
    dialog(page).get_by_label("名称", exact=True).fill(name)
    dialog(page).get_by_label("人设说明", exact=True).fill("TEST_persona_description")
    button(dialog(page), "创建数字人").click()
    if detail_fails:
        button(dialog(page), "重试数字人详情").wait_for()
        expect(dialog(page).get_by_text(re.compile("已创建数字人")).first).to_be_visible()
        button(dialog(page), "重试数字人详情").click()
    heading(page, name)
    button(dialog(page), "选择已有资产").wait_for()


def open_record(page: Page, name: str) -> None:
    button(page, "查看 " + name).click()
    heading(page, name)


def attach(page: Page, name: str) -> None:
    button(dialog(page), "选择已有资产").click()
    search(dialog(page), "搜索资产", name)
    button(dialog(page), "预览 " + name).click()
    expect(dialog(page).get_by_role("img").first).to_be_visible()
    button(dialog(page), "添加 " + name).click()
    expect(dialog(page).get_by_text("已添加", exact=True).first).to_be_visible()
    close(page)
    text(dialog(page), name)


def test_shared_asset_and_persona_lifecycle(media_run: MediaRun, tmp_path: Path) -> None:
    """Standalone upload, search, share, edit, detach, archive and both deletes."""
    page, gate = media_run.page, media_run.gate
    original = media_run.name("original.png")
    renamed = media_run.name("renamed.png")
    first = media_run.name("persona_A")
    second = media_run.name("persona_B")

    media_run.goto("media-assets")
    text(page, "资产库还没有文件。")
    search(page, "搜索资产", media_run.name("unmatched"))
    text(page, "没有符合当前筛选条件的资产。")
    button(page, "上传资产").click()
    pick_file(page, original)
    assert gate.calls["POST", ASSETS + "/upload-url"] == gate.store.puts == 0
    gate.fail("GET", re.compile(rf"{ASSETS}/[1-9][0-9]*"), 503)
    button(dialog(page), "开始上传").click()
    button(dialog(page), "重试资产详情").wait_for()
    expect(dialog(page).get_by_text(re.compile("已入库")).first).to_be_visible()
    with db() as session:
        asset = session.scalar(select(MediaAsset).where(MediaAsset.filename == original))
        assert asset is not None and asset.status == "ready"
    button(dialog(page), "重试资产详情").click()
    heading(page, original)
    image = dialog(page).get_by_role("img").first
    expect(image).to_be_visible()
    page.wait_for_function("img => img.complete && img.naturalWidth === 1", arg=image.element_handle())
    assert gate.calls["POST", ASSETS + "/upload-url"] == gate.store.puts == 1
    close(page)
    expect(page.get_by_text("已清除筛选并返回第一页。", exact=False)).to_be_visible()
    button(page, "查看 " + original).wait_for()
    search(page, "搜索资产", original)
    open_record(page, original)
    close(page)

    media_run.goto("digital-humans")
    search(page, "搜索数字人", media_run.name("unmatched"))
    gate.fail("GET", re.compile(rf"{HUMANS}/[1-9][0-9]*"), 503)
    create_persona(page, first, detail_fails=True)
    assert gate.calls["POST", HUMANS] == 1
    attach(page, original)
    aid, first_id = media_run.asset_id(original), media_run.human_id(first)
    assert media_run.links(first_id) == {aid}
    close(page)
    search(page, "搜索数字人", "")
    create_persona(page, second)
    attach(page, original)
    second_id = media_run.human_id(second)
    assert media_run.links(second_id) == {aid}

    media_run.goto("media-assets")
    search(page, "搜索资产", original)
    open_record(page, original)
    text(dialog(page), first)
    text(dialog(page), second)
    for width in (1440, 2560):
        page.set_viewport_size({"width": width, "height": 1000})
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
        expect(button(dialog(page), "编辑资料")).to_be_visible()
        page.screenshot(path=tmp_path / f"TEST_asset_detail_{width}.png")
    dialog(page).get_by_role("link", name=first, exact=True).click()
    heading(page, first)
    text(dialog(page), original)
    media_run.goto("media-assets")
    open_record(page, original)
    button(dialog(page), "编辑资料").click()
    dialog(page).get_by_label("显示名称", exact=True).fill(renamed)
    dialog(page).get_by_label("说明", exact=True).fill("TEST_shared_note")
    dialog(page).get_by_label(re.compile(r"^标签")).fill("TEST_shared_tag")
    gate.fail("PATCH", f"{ASSETS}/{aid}", 500)
    button(dialog(page), "保存资料").click()
    expect(dialog(page).get_by_role("alert").filter(has_text=re.compile("未能确认操作结果"))).to_be_visible()
    expect(dialog(page).get_by_label("显示名称", exact=True)).to_have_value(renamed)
    expect(dialog(page).get_by_label("说明", exact=True)).to_have_value("TEST_shared_note")
    assert media_run.asset_id(original) == aid
    button(dialog(page), "保存资料").click()
    heading(page, renamed)
    text(dialog(page), "TEST_shared_tag")
    assert media_run.asset_id(renamed) == aid
    close(page)
    search(page, "搜索资产", renamed)
    button(page, "查看 " + renamed).wait_for()

    media_run.goto("digital-humans")
    open_record(page, first)
    text(dialog(page), renamed)
    button(dialog(page), "从当前数字人移除 " + renamed).click()
    button(dialog(page), "确认移除").click()
    expect(dialog(page).get_by_text(renamed, exact=True)).to_have_count(0)
    assert media_run.links(first_id) == set() and media_run.links(second_id) == {aid}
    close(page)
    open_record(page, second)
    text(dialog(page), renamed)
    button(dialog(page), "归档数字人").click()
    button(dialog(page), "恢复为启用").wait_for()
    with db() as session:
        assert session.get(DigitalHuman, second_id).status == "archived"
    assert media_run.links(second_id) == {aid}
    close(page)
    expect(button(page, "查看 " + second)).to_have_count(0)
    page.get_by_role("button", name=re.compile("档案状态")).click()
    page.get_by_role("option", name="已归档", exact=True).click()
    open_record(page, second)
    text(dialog(page), renamed)
    button(dialog(page), "恢复为启用").click()
    button(dialog(page), "归档数字人").wait_for()
    with db() as session:
        assert session.get(DigitalHuman, second_id).status == "active"
    close(page)
    page.get_by_role("button", name=re.compile("档案状态")).click()
    page.get_by_role("option", name="启用中", exact=True).click()

    # Deleting an actual linked persona leaves its asset globally available.
    open_record(page, first)
    attach(page, renamed)
    button(dialog(page), "删除数字人").click()
    button(dialog(page), "确认删除").click()
    expect(dialog(page)).to_have_count(0)
    expect(button(page, "查看 " + first)).to_have_count(0)
    with db() as session:
        assert session.get(DigitalHuman, first_id).deleted_at is not None
        assert session.get(MediaAsset, aid).deleted_at is None

    media_run.goto("media-assets")
    open_record(page, renamed)
    text(dialog(page), second)
    button(dialog(page), "从资产库删除").click()
    expect(dialog(page).get_by_text(re.compile(re.escape(second))).first).to_be_visible()
    button(dialog(page), "确认删除").click()
    expect(dialog(page)).to_have_count(0)
    expect(button(page, "查看 " + renamed)).to_have_count(0)
    with db() as session:
        assert session.get(MediaAsset, aid).deleted_at is not None
    media_run.goto("digital-humans")
    open_record(page, second)
    expect(dialog(page).get_by_text(renamed, exact=True)).to_have_count(0)
    assert media_run.links(second_id) == set()


@pytest.mark.parametrize("media_run", [("admin", 375, "/tts")], indirect=True)
def test_confirm_and_attach_recovery_on_the_original_target(media_run: MediaRun, tmp_path: Path) -> None:
    page, gate = media_run.page, media_run.gate
    person = media_run.name("upload_target")
    asset = media_run.name("attached.png")
    media_run.goto("digital-humans")
    create_persona(page, person)
    page.screenshot(path=tmp_path / "TEST_digital_human_375.png")
    dimensions = overflow_report(page)
    assert dimensions["scrollWidth"] <= dimensions["clientWidth"], dimensions
    expect(button(dialog(page), "上传新资产")).to_be_visible()
    hid = media_run.human_id(person)
    attach_path = f"{HUMANS}/{hid}/assets"
    button(dialog(page), "上传新资产").click()
    pick_file(page, asset)
    gate.hold("POST", re.compile(rf"{ASSETS}/[1-9][0-9]*/confirm"))
    with page.expect_request(lambda req: req.method == "POST" and req.url.endswith("/confirm")):
        button(dialog(page), "开始上传并添加").click()
    expect(dialog(page).get_by_text(re.compile("正在确认入库")).first).to_be_visible()
    aid = next(iter(gate.assets))
    confirm_path = f"{ASSETS}/{aid}/confirm"
    assert gate.store.puts == 1 and gate.calls["POST", attach_path] == 0
    with db() as session:
        assert session.get(MediaAsset, aid).status == "awaiting_upload"
    gate.release(status=503)
    gate.fail("POST", attach_path, 500)
    button(dialog(page), "重试入库确认").click()
    button(dialog(page), "重试添加资产").wait_for()
    with db() as session:
        assert session.get(MediaAsset, aid).status == "ready"
    assert media_run.links(hid) == set()
    button(dialog(page), "重试添加资产").click()
    text(dialog(page), asset)
    assert gate.calls["POST", ASSETS + "/upload-url"] == gate.store.puts == 1
    assert gate.calls["POST", confirm_path] == 2
    assert gate.calls["POST", attach_path] == 2
    assert media_run.links(hid) == {aid}
    gate.fail("GET", ASSETS, 500)
    media_run.goto("media-assets")
    button(page, "重试资产列表").click()
    gate.store.break_next_preview = True
    open_record(page, asset)
    text(dialog(page), person)
    text(dialog(page), "暂时无法预览此文件。")
    button(dialog(page), "重新获取预览").click()
    image = dialog(page).get_by_role("img").first
    expect(image).to_be_visible()
    page.wait_for_function("img => img.complete && img.naturalWidth === 1", arg=image.element_handle())

    media_run.goto("digital-humans")
    other = media_run.name("other_persona")
    create_persona(page, other)
    close(page)
    open_record(page, person)
    button(dialog(page), "编辑资料").click()
    draft = media_run.name("discard_me")
    dialog(page).get_by_label("名称", exact=True).fill(draft)
    close(page)
    button(dialog(page), "继续处理").click()
    expect(dialog(page).get_by_label("名称", exact=True)).to_have_value(draft)
    close(page)
    button(dialog(page), "确认放弃").click()
    open_record(page, other)
    assert gate.calls["PATCH", f"{HUMANS}/{hid}"] == 0
    assert media_run.human_id(person) == hid


@pytest.mark.parametrize("media_run", [("viewer", 1440, "")], indirect=True)
def test_viewer_media_permission_denial(media_run: MediaRun) -> None:
    """A real session without the media page grants is denied at page entry."""
    for slug in ("media-assets", "digital-humans"):
        response = media_run.goto(slug)
        assert response is not None and response.status == 403
    assert media_run.gate.calls["GET", ASSETS] == media_run.gate.calls["GET", HUMANS] == 0
