"""Compiled shops preview: canned GET only; actual auth/persistence is tested separately."""
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
SHOP = {"id": 41, "platform": "tiktok", "shop_id": "90071992547409931234567", "account_name": "TEST_long_name_" * 10,
        "region": None, "seller_type": None, "status": "active", "opened_date": None, "credential_id": None,
        "service_id": "TEST_service", "app_credentials_configured": True}


@pytest.fixture(params=["readonly", "readwrite"])
def shops_preview(frontend_build, tmp_path, request):
    role = request.param
    for prefix in ("", "/tts"):
        scope = {"type": "http", "root_path": prefix, "path": "/v2/pages/shops", "headers": [],
                 "access_grant": AccessGrant(mode=AuthMode.ENFORCE, role=Role(role))}
        response = frontend_pages.render_frontend_page(Request(scope), "shops")
        (tmp_path / f"{'tts-' if prefix else ''}shops.html").write_bytes(bytes(response.body))
    with browser_conftest._serve_pages(tmp_path) as url:
        for renderer in browser_conftest._browser_renderer(url):
            yield renderer, role


@pytest.mark.parametrize("prefix", ["", "/tts"])
@pytest.mark.parametrize("width", [375, 1440])
def test_native_shops_read_and_dialog_cancel(shops_preview, prefix, width):
    renderer, role = shops_preview
    page = renderer.open(f"{prefix}/v2/pages/shops", width=width)
    calls = []

    def read_shops(route):
        calls.append(route.request.method)
        route.fulfill(json=[SHOP], headers={"X-Total-Count": "502"})

    page.route("**/v2/commerce/channel-accounts?*", read_shops)
    page.reload(wait_until="networkidle")
    page.get_by_role("grid", name="店铺接入列表").get_by_text(SHOP["shop_id"], exact=True).wait_for()
    assert page.get_by_text("已登记 502 家店铺", exact=False).is_visible()
    search = page.get_by_role("searchbox", name="店铺搜索", exact=True)
    search_box = search.bounding_box()
    assert search_box is not None and search_box["height"] >= 40, search_box
    filters_box = page.get_by_role("form", name="店铺筛选").bounding_box()
    assert filters_box is not None and filters_box["width"] <= width, filters_box
    name_box = page.get_by_text(SHOP["account_name"], exact=True).bounding_box()
    assert name_box is not None and 160 <= name_box["width"] <= 256, name_box
    row_box = page.get_by_role("row").filter(has=page.get_by_text(SHOP["shop_id"], exact=True)).bounding_box()
    assert row_box is not None and row_box["height"] <= 400, row_box
    report = overflow_report(page)
    assert report["scrollWidth"] <= report["clientWidth"] + 1, report
    assert not report["offenders"], report
    assert calls == ["GET"]
    scroll = page.get_by_role("region", name="店铺表格")
    scroll.evaluate("el => { el.scrollLeft = 0; }")
    identity_box = page.get_by_text(SHOP["shop_id"], exact=True).bounding_box()
    scroll_box = scroll.bounding_box()
    assert identity_box is not None and scroll_box is not None
    assert identity_box["x"] >= scroll_box["x"] and identity_box["x"] + identity_box["width"] <= scroll_box["x"] + scroll_box["width"]
    if width == 375:
        assert scroll.evaluate("el => el.scrollWidth > el.clientWidth")
    if role == "readonly":
        page.get_by_role("button", name="获取授权链接", exact=True).scroll_into_view_if_needed()
        for name in ("手工补录", "编辑信息", "配置 App", "获取授权链接"):
            assert page.get_by_role("button", name=name, exact=True).is_disabled()
    else:
        trigger = page.get_by_role("button", name="配置 App", exact=True)
        invoking_row = page.get_by_role("row").filter(has=page.get_by_text(SHOP["shop_id"], exact=True))
        trigger.focus()
        page.keyboard.press("Enter")
        dialog = page.get_by_role("dialog")
        dialog.get_by_text("配置仅用于当前店铺", exact=False).wait_for()
        secret = dialog.get_by_label("App Secret", exact=True)
        assert secret.get_attribute("type") == "password"
        # Layout sizes avoid sampling two different frames of the modal animation.
        sizes = page.evaluate(
            "([service, secret, dialog]) => ({service: service.clientWidth, secret: secret.clientWidth, "
            "height: secret.clientHeight, dialog: dialog.clientWidth, scroll: dialog.scrollWidth})",
            [dialog.get_by_label("Service ID", exact=True).element_handle(), secret.element_handle(), dialog.element_handle()],
        )
        assert abs(sizes["service"] - sizes["secret"]) <= 1, sizes
        assert sizes["service"] <= sizes["dialog"] - 32, sizes
        assert sizes["scroll"] <= sizes["dialog"] + 1, sizes
        assert sizes["height"] >= 40, sizes
        secret.fill("TEST_cancel_only")
        page.keyboard.press("Escape")
        dialog.wait_for(state="hidden")
        # React Aria's native grid is one tab stop: keyboard restoration may target
        # the exact invoking row (useGridCell onFocus), not its nested button.
        # Assert that logical context rather than replacing the library's focus owner.
        page.wait_for_function("nodes => document.activeElement === nodes[0] || document.activeElement === nodes[1]",
                               arg=[trigger.element_handle(), invoking_row.element_handle()], timeout=5000)
        trigger.click()
        dialog.get_by_label("App Secret", exact=True).wait_for()
        assert dialog.get_by_label("App Secret", exact=True).input_value() == ""
        dialog.get_by_role("button", name="取消", exact=True).click()
        dialog.wait_for(state="hidden")
        assert calls == ["GET"]
    assert "shops" in frontend_pages.MIGRATED_PAGE_IDS
    scroll.evaluate("el => { el.scrollLeft = 0; }")
    artifacts = ROOT / "frontend/.cache/screenshots"
    artifacts.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(artifacts / f"TEST_shops_{role}_{width}_{prefix.strip('/') or 'direct'}.png"), full_page=True)
