"""Compiled auxiliary views via the existing inert static browser fixture.

The real renderers construct these anonymous documents. The shared static server
transports every saved document as 200; real 403/400/502 protocol assertions belong
to tests/api/test_frontend_auxiliary.py. No callback handler, live service, DB or
upstream authorization operation is executed by this browser layer.
"""
from __future__ import annotations

from pathlib import Path
from urllib.parse import urlsplit

import pytest
from render_support import overflow_report
from starlette.requests import Request

from tests.browser import conftest as browser_conftest
from tts_erp_v2.api.v2 import frontend_pages

pytestmark = [pytest.mark.domain_browser, pytest.mark.requires_browser]
ROOT = Path(__file__).resolve().parents[2]
AUTHORIZATION_URL = ("https://services.tiktokshop.com/open/authorize?"
    "service_id=TEST_SERVICE&state=TEST_single_use_authorization_state")
LONG_SHOP_ID = "TEST_9007199254740993123456789"
RESULT_TITLE = "TEST 店铺授权成功"
RESULT_MESSAGE = "TEST 已完成授权，同步任务将在下次运行时接管。"
SLUGS = ("auxiliary-onboard", "auxiliary-result", "auxiliary-forbidden")


def _documents(directory: Path) -> None:
    for prefix in ("", "/tts"):
        request = Request({"type": "http", "root_path": prefix,
            "path": "/v2/oauth/tiktok/onboard", "headers": []})
        documents = {
            "auxiliary-onboard": frontend_pages.render_oauth_onboard_page(request),
            "auxiliary-result": frontend_pages.render_message_page(prefix,
                title=RESULT_TITLE, message=RESULT_MESSAGE, tone="success", status_code=200,
                authorization={"url": AUTHORIZATION_URL, "expiresAt": "2026-11-02T16:00:00+00:00"},
                sections=[{"title": "TEST 店铺明细", "facts": [
                    {"label": "店铺 ID", "value": LONG_SHOP_ID},
                    {"label": "店铺名称", "value": "TEST_很长的卖家店铺名称" * 8},
                    {"label": "地区", "value": "VN"},
                    {"label": "授权范围", "value": "seller.order.read, seller.product.read"},
                ]}]),
            "auxiliary-forbidden": frontend_pages.render_message_page(prefix,
                title="无权访问该页面", message="TEST 403：当前账号没有此页面权限。",
                tone="danger", status_code=403, action="login"),
        }
        assert documents["auxiliary-forbidden"].status_code == 403
        for slug, response in documents.items():
            name = ("tts-" if prefix else "") + slug
            (directory / f"{name}.html").write_bytes(bytes(response.body))


@pytest.fixture()
def auxiliary_renderer(frontend_build: None, tmp_path: Path, monkeypatch):
    monkeypatch.delenv("TTS_ERP_FRONTEND_DEV_ORIGIN", raising=False)
    _documents(tmp_path)
    with browser_conftest._serve_pages(tmp_path) as url:
        yield from browser_conftest._browser_renderer(url)


def _assert_no_session_or_command_requests(page, recorded) -> None:
    # Covers the first load as well as events captured after Renderer.open.
    resources = page.evaluate("performance.getEntriesByType('resource').map(item => item.name)")
    for url in resources + [url for _method, url in recorded]:
        path = urlsplit(url).path
        if path.startswith("/tts/"):
            path = path[len("/tts"):]
        # Only this static document alias may be under /v2; no session probe,
        # callback replay, authorize request, or other business API is permitted.
        assert not path.startswith("/v2/") or path.startswith("/v2/pages/auxiliary-"), url
    assert all(method in {"GET", "HEAD", "OPTIONS"} for method, _url in recorded)


@pytest.mark.parametrize("prefix", ["", "/tts"])
@pytest.mark.parametrize("width", [375, 1440, 2560])
def test_compiled_auxiliary_facts_navigation_and_layout(auxiliary_renderer, prefix, width):
    screenshots = ROOT / "frontend/.cache/screenshots"
    screenshots.mkdir(parents=True, exist_ok=True)
    for slug in SLUGS:
        page = auxiliary_renderer.open(f"{prefix}/v2/pages/{slug}", width=width)
        recorded = []
        page.on("request", lambda request, captured=recorded: captured.append((request.method, request.url)))
        title = ("新店接入授权" if slug == "auxiliary-onboard" else RESULT_TITLE
            if slug == "auxiliary-result" else "无权访问该页面")
        # Initial semantic fallback uses h1. Waiting for h2 proves the current
        # compiled HeroUI React view mounted before assertions/screenshots.
        page.get_by_role("heading", name=title, level=2, exact=True).wait_for()
        assert page.get_by_role("navigation").count() == 0
        assert page.get_by_role("button", name="刷新页面", exact=True).count() == 0
        if slug == "auxiliary-onboard":
            assert page.get_by_text("只读账号可查看说明", exact=False).is_visible()
            assert page.get_by_role("link", name="前往店铺注册台").get_attribute("href") == f"{prefix}/v2/pages/shops"
            assert page.get_by_text(f"{prefix}/v2/oauth/tiktok/callback", exact=True).is_visible()
        elif slug == "auxiliary-result":
            assert page.get_by_text(RESULT_MESSAGE, exact=True).is_visible()
            assert page.get_by_text(LONG_SHOP_ID, exact=True).is_visible()
            link = page.get_by_role("link", name="打开授权页面", exact=True)
            assert link.get_attribute("href") == AUTHORIZATION_URL
            assert link.get_attribute("target") == "_blank"
            assert set(link.get_attribute("rel").split()) >= {"noopener", "noreferrer"}
            assert page.get_by_role("link", name="前往店铺注册台").get_attribute("href") == f"{prefix}/v2/pages/shops"
        else:
            assert page.get_by_text("TEST 403：当前账号没有此页面权限。", exact=True).is_visible()
            assert page.get_by_role("link", name="返回登录页").get_attribute("href") == f"{prefix}/v2/auth/login"
        _assert_no_session_or_command_requests(page, recorded)
        report = overflow_report(page)
        assert report["scrollWidth"] <= report["clientWidth"] + 1, report
        page.screenshot(path=str(screenshots / f"TEST_{slug}_{width}_{prefix.strip('/') or 'direct'}.png"), full_page=True)
        auxiliary_renderer.assert_read_only()


@pytest.mark.parametrize("prefix", ["", "/tts"])
def test_entry_download_failure_keeps_completed_server_result_without_callback_refresh(auxiliary_renderer, prefix):
    page = auxiliary_renderer.open(f"{prefix}/v2/pages/auxiliary-result", width=375)
    page.get_by_role("heading", name=RESULT_TITLE, level=2, exact=True).wait_for()
    recorded = []
    page.on("request", lambda request, captured=recorded: captured.append((request.method, request.url)))
    page.route("**/static/frontend/**/*.js", lambda route: route.abort())
    page.reload(wait_until="networkidle")
    # Module graph cannot start, so the real server-rendered facts remain.
    assert page.get_by_role("heading", name=RESULT_TITLE, level=1, exact=True).is_visible()
    assert page.get_by_text(RESULT_MESSAGE, exact=True).is_visible()
    assert page.get_by_text(LONG_SHOP_ID, exact=True).is_visible()
    assert page.get_by_role("link", name="打开授权页面").get_attribute("href") == AUTHORIZATION_URL
    assert page.get_by_role("link", name="前往店铺注册台").get_attribute("href") == f"{prefix}/v2/pages/shops"
    assert page.get_by_role("button", name="刷新页面").count() == 0
    _assert_no_session_or_command_requests(page, recorded)
    report = overflow_report(page)
    assert report["scrollWidth"] <= report["clientWidth"] + 1, report
    screenshots = ROOT / "frontend/.cache/screenshots"
    screenshots.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(screenshots / f"TEST_auxiliary-result_no-entry_375_{prefix.strip('/') or 'direct'}.png"), full_page=True)
    auxiliary_renderer.assert_read_only()
