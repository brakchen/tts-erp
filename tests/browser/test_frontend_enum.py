"""Compiled enum preview. GET-only, not actual auth or persistence acceptance."""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import Request
from render_support import overflow_report

from tests.browser import conftest as browser_conftest
from tts_erp_v2.access import AccessGrant, AuthMode, Role
from tts_erp_v2.api.v2 import frontend_pages

pytestmark = [pytest.mark.domain_browser, pytest.mark.requires_browser]
ROOT = Path(__file__).resolve().parents[2]
ROW = {"id": 41, "enum_type": "order_status", "enum_value": "TEST_ENGLISH_" * 9,
       "label_zh": "TEST_标签_" * 32, "sort_order": 9}
ROWS = [ROW, {**ROW, "id": 42, "enum_type": "__proto__", "enum_value": "TEST_UNKNOWN"}]


@pytest.fixture(params=["readonly", "admin"])
def enum_preview(frontend_build, tmp_path, request):
    role = request.param
    for prefix in ("", "/tts"):
        scope = {"type": "http", "root_path": prefix, "path": "/v2/pages/enum-map", "headers": [],
                 "access_grant": AccessGrant(mode=AuthMode.ENFORCE, role=Role(role))}
        response = frontend_pages.render_frontend_page(Request(scope), "enum-map")
        (tmp_path / f"{'tts-' if prefix else ''}enum-map.html").write_bytes(bytes(response.body))
    with browser_conftest._serve_pages(tmp_path) as url:
        for renderer in browser_conftest._browser_renderer(url):
            yield renderer, role


@pytest.mark.parametrize("prefix", ["", "/tts"])
@pytest.mark.parametrize("width", [375, 768, 1440])
def test_native_enum_categories_and_cancel(enum_preview, prefix, width):
    renderer, role = enum_preview
    page = renderer.open(f"{prefix}/v2/pages/enum-map", width=width)
    calls = []

    def read_list(route):
        calls.append(route.request.method)
        assert route.request.method == "GET"
        route.fulfill(json=ROWS)

    page.route("**/v2/config/enum-map/list", read_list)
    page.reload(wait_until="networkidle")
    grid = page.get_by_role("grid", name="枚举映射")
    grid.get_by_text(ROW["enum_value"], exact=True).wait_for()
    row = page.get_by_role("row").filter(has=page.get_by_text(ROW["enum_value"], exact=True))
    row_box = row.bounding_box()
    label_box = grid.get_by_text(ROW["label_zh"], exact=True).bounding_box()
    assert row_box is not None and row_box["height"] <= 400, row_box
    assert label_box is not None and label_box["width"] >= 200, label_box
    report = overflow_report(page)
    assert report["scrollWidth"] <= report["clientWidth"] + 1 and not report["offenders"], report
    scroll = page.get_by_role("region", name="枚举表格")
    if width == 375:
        assert scroll.evaluate("el => el.scrollWidth > el.clientWidth")
    assert calls == ["GET"]
    if role == "readonly":
        assert page.get_by_role("button", name="新增映射", exact=True).is_disabled()
        assert page.get_by_role("button", name="编辑映射", exact=True).count() == 0
        assert page.get_by_role("button", name="删除映射", exact=True).count() == 0
    else:
        trigger = page.get_by_role("button", name="编辑映射", exact=True)
        trigger.scroll_into_view_if_needed()
        trigger.focus()
        page.keyboard.press("Enter")
        dialog = page.get_by_role("dialog")
        dialog.get_by_role("heading", name="编辑映射", exact=True).wait_for()
        assert dialog.get_by_label("英文值", exact=True).get_attribute("readonly") is not None
        dialog.get_by_label("中文标签", exact=True).fill("TEST_cancel_draft")
        page.keyboard.press("Escape")
        dialog.wait_for(state="hidden")
        page.wait_for_function("nodes => document.activeElement === nodes[0] || document.activeElement === nodes[1]",
                               arg=[trigger.element_handle(), row.element_handle()], timeout=5000)
        trigger.click()
        assert dialog.get_by_label("中文标签", exact=True).input_value() == ROW["label_zh"]
        dialog.get_by_role("button", name="取消", exact=True).click()
        dialog.wait_for(state="hidden")
        page.get_by_role("button", name="删除映射", exact=True).click()
        dialog.get_by_role("heading", name="确认删除映射", exact=True).wait_for()
        assert dialog.get_by_text(f"确认删除 {ROW['enum_type']} / {ROW['enum_value']}？", exact=True).is_visible()
        dialog.get_by_role("button", name="取消", exact=True).click()
        dialog.wait_for(state="hidden")
        assert calls == ["GET"]
    scroll.evaluate("el => { el.scrollLeft = 0; }")
    identity_box = grid.get_by_text(ROW["enum_value"], exact=True).bounding_box()
    scroll_box = scroll.bounding_box()
    assert identity_box is not None and scroll_box is not None
    assert identity_box["x"] >= scroll_box["x"] and identity_box["x"] + identity_box["width"] <= scroll_box["x"] + scroll_box["width"]
    artifacts = ROOT / "frontend/.cache/screenshots"
    artifacts.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(artifacts / f"TEST_enum_{role}_{width}_{prefix.strip('/') or 'direct'}.png"), full_page=True)
    selector = page.get_by_role("button", name="订单状态（order_status） 分类", exact=True)
    selector.focus()
    page.keyboard.press("Enter")
    page.get_by_role("option", name="__proto__", exact=True).click()
    grid.get_by_text("TEST_UNKNOWN", exact=True).wait_for()
    page.get_by_role("button", name="__proto__ 分类", exact=True).click()
    page.get_by_role("option", name="售后类型（case_type）", exact=True).click()
    grid.get_by_text("该分类暂无映射", exact=True).wait_for()
    assert calls == ["GET"]
    assert "enum-map" in frontend_pages.MIGRATED_PAGE_IDS
