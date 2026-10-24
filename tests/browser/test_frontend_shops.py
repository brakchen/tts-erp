"""Unactivated shops preview: real compiled HeroUI, canned GET only; not persistence/auth acceptance."""
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
    page.get_by_role("grid", name="已注册店铺").get_by_text(SHOP["shop_id"], exact=True).wait_for()
    assert page.get_by_text("已注册总数：502", exact=False).is_visible()
    name_box = page.get_by_text(SHOP["account_name"], exact=True).bounding_box()
    assert name_box is not None and name_box["width"] <= 256, name_box
    report = overflow_report(page)
    assert report["scrollWidth"] <= report["clientWidth"] + 1, report
    assert not report["offenders"], report
    assert calls == ["GET"]
    if role == "readonly":
        for name in ("注册店铺", "编辑元信息", "配置 App", "获取授权链接"):
            assert page.get_by_role("button", name=name, exact=True).is_disabled()
    else:
        trigger = page.get_by_role("button", name="配置 App", exact=True)
        invoking_row = page.get_by_role("row").filter(has=page.get_by_text(SHOP["shop_id"], exact=True))
        trigger.focus()
        page.keyboard.press("Enter")
        dialog = page.get_by_role("dialog")
        dialog.get_by_text("同一 Service ID", exact=False).wait_for()
        secret = dialog.get_by_label("App Secret", exact=True)
        assert secret.get_attribute("type") == "password"
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
    assert "shops" not in frontend_pages.MIGRATED_PAGE_IDS
    artifacts = ROOT / "frontend/.cache/screenshots"
    artifacts.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(artifacts / f"TEST_shops_{role}_{width}_{prefix.strip('/') or 'direct'}.png"), full_page=True)
