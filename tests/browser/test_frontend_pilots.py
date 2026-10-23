"""Exercise current compiled React documents, with isolated canned GET APIs."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from render_support import overflow_report

pytestmark = [pytest.mark.domain_browser, pytest.mark.requires_browser]


@pytest.mark.parametrize("prefix", ["", "/tts"])
@pytest.mark.parametrize("width", [375, 1440])
@pytest.mark.parametrize("slug,title", [("dashboard", "控制台"), ("intercept-requests", "拦截记录")])
def test_pilot_uses_shared_shell(frontend_browser_renderer, tmp_path, prefix, width, slug, title):
    page = frontend_browser_renderer.open(f"{prefix}/v2/pages/{slug}", width=width)
    page.get_by_role("heading", level=1, name=title, exact=True).wait_for(timeout=10_000)
    if width < 1024:
        trigger = page.get_by_role("button", name="打开主导航")
        trigger.click()
        page.get_by_role("navigation", name="主导航").wait_for()
        page.keyboard.press("Escape")
        page.get_by_role("dialog").wait_for(state="hidden")
        assert trigger.evaluate("el => document.activeElement === el")
    else:
        navigation = page.get_by_role("navigation", name="主导航")
        assert navigation.count() == 1
        assert navigation.locator('[aria-current="page"]').get_attribute("href") == f"{prefix}/v2/pages/{slug}"
    report = overflow_report(page)
    assert report["scrollWidth"] <= report["clientWidth"] + 1, report
    assert not report["offenders"], report
    resources = page.evaluate("performance.getEntriesByType('resource').map(x => x.name)")
    assert not any("bootstrap.min" in url or "/static/js/" in url for url in resources)
    assert all(url.startswith(frontend_browser_renderer._base) for url in resources)
    artifacts = Path(__file__).resolve().parents[2] / "frontend/.cache/screenshots"
    artifacts.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(artifacts / f"TEST_{slug}_{width}_{prefix.strip('/') or 'direct'}.png"), full_page=True)


def test_request_query_detail_and_pagination(frontend_browser_renderer):
    page = frontend_browser_renderer.open("/tts/v2/pages/intercept-requests")
    page.get_by_role("button", name="查看详情").first.wait_for()
    page.get_by_role("button", name="查看详情").first.click()
    dialog = page.get_by_role("dialog")
    dialog.get_by_text("TEST_detail", exact=False).wait_for()
    assert dialog.get_by_text("TEST_request_0", exact=True).is_visible()
    dialog.get_by_role("button", name="关闭详情").click()
    dialog.wait_for(state="hidden")
    with page.expect_request(lambda request: "/v2/intercept/requests?" in request.url and "offset=100" in request.url):
        page.get_by_role("button", name="下一页").click()
    page.get_by_text("/TEST/TEST_request_100", exact=True).wait_for()
    page.get_by_role("textbox", name="域名", exact=True).fill("TEST_no_match")
    page.get_by_role("button", name="查询", exact=True).click()
    page.get_by_text("暂无记录", exact=True).wait_for()


def test_dashboard_and_new_old_navigation(frontend_browser_renderer):
    page = frontend_browser_renderer.open("/tts/v2/pages/dashboard")
    page.get_by_text("API 正常", exact=False).wait_for()
    assert page.get_by_text("健康状态未知（接口未提供）", exact=True).count() == 3
    metric = page.get_by_role("region", name="SPU 总数", exact=True)
    metric.get_by_text("1234", exact=True).wait_for()
    navigation = page.get_by_role("navigation", name="主导航")
    navigation.get_by_role("link", name="拦截记录", exact=True).click()
    page.get_by_role("heading", level=1, name="拦截记录", exact=True).wait_for()
    page.get_by_role("navigation", name="主导航").get_by_role("link", name="店铺注册", exact=True).click()
    page.wait_for_url("**/tts/v2/pages/shops")
    assert page.locator("#frontend-root").count() == 0
    assert page.locator('link[href*="bootstrap.min.css"]').count() == 1


def test_lazy_chunk_failure_is_visible(frontend_browser_renderer):
    page = frontend_browser_renderer.open("/v2/pages/dashboard")
    page.get_by_role("heading", level=1, name="控制台", exact=True).wait_for()
    assets = Path(__file__).resolve().parents[2] / "tts_erp_v2/static/frontend"
    release = json.loads((assets / "release.json").read_text())["releaseId"]
    manifest = json.loads((assets / release / ".vite/manifest.json").read_text())
    chunk = manifest["src/pages/dashboard/index.tsx"]["file"]
    page.route("**/" + chunk, lambda route: route.abort())
    page.reload(wait_until="domcontentloaded")
    page.get_by_text("页面加载失败", exact=True).wait_for(timeout=10_000)
