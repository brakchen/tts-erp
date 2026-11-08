"""Exercise current compiled React documents, with isolated canned GET APIs."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from render_support import overflow_report

from tests.support.page_readiness import wait_canned_page
from tts_erp_v2.accounts.pages import PAGES

pytestmark = [pytest.mark.domain_browser, pytest.mark.requires_browser]


@pytest.mark.parametrize("prefix", ["", "/tts"])
def test_navigation_drawer_theme_and_focus(frontend_browser_renderer, prefix):
    page = frontend_browser_renderer.open(f"{prefix}/v2/pages/intercept-requests", width=390)
    trigger = page.get_by_role("button", name="打开主导航")
    trigger.focus()
    page.keyboard.press("Enter")
    dialog = page.get_by_role("dialog")
    dialog.wait_for()
    colors = page.evaluate("""() => {
        const probe = document.createElement('span');
        probe.style.backgroundColor = 'var(--accent-deep)';
        probe.style.color = 'var(--navigation-foreground)';
        document.body.append(probe);
        const style = getComputedStyle(probe);
        const result = { background: style.backgroundColor, foreground: style.color };
        probe.style.color = 'var(--navigation-muted)';
        result.muted = getComputedStyle(probe).color;
        probe.remove();
        return result;
    }""")
    assert dialog.evaluate("el => getComputedStyle(el).backgroundColor") == colors["background"]
    assert dialog.get_by_role("heading", name="主导航").evaluate(
        "el => getComputedStyle(el).color"
    ) == colors["foreground"]
    assert dialog.get_by_role("button", name="关闭主导航").evaluate(
        "el => getComputedStyle(el).color"
    ) == colors["foreground"]
    assert dialog.locator(".drawer__body").evaluate(
        "el => getComputedStyle(el).color"
    ) == colors["muted"]
    page.wait_for_function("el => el.contains(document.activeElement)", arg=dialog.element_handle())
    for _ in range(3):
        page.keyboard.press("Tab")
        assert dialog.evaluate("el => el.contains(document.activeElement)")
    dialog.get_by_role("link").last.focus()
    page.keyboard.press("Tab")
    assert dialog.evaluate("el => el.contains(document.activeElement)")
    page.keyboard.press("Escape")
    dialog.wait_for(state="hidden")
    page.wait_for_function("el => document.activeElement === el", arg=trigger.element_handle())


@pytest.mark.parametrize("prefix", ["", "/tts"])
@pytest.mark.parametrize("width", [375, 1440, 2560])
@pytest.mark.parametrize("slug,title", [
    (page.page_id, page.label) for page in PAGES
    if page.page_id not in {"spu-roi", "focused-spus"}
])
def test_pilot_uses_shared_shell(frontend_browser_renderer, tmp_path, prefix, width, slug, title):
    page = frontend_browser_renderer.open(f"{prefix}/v2/pages/{slug}", width=width)
    page.get_by_role("heading", level=1, name=title, exact=True).wait_for(timeout=10_000)
    if slug == "runninghub":
        page.get_by_text("plus · 并发 5 · 已校验版本", exact=True).wait_for()
        page.get_by_role("checkbox", name="TEST_RENDER_HUMAN · 模板 A · TEST_ren · 0 段", exact=True).wait_for()
        page.get_by_text("没有就绪图片。", exact=False).wait_for()
    else:
        wait_canned_page(page, slug)
    assert page.locator("#frontend-startup").count() == 0
    if slug == "spu-profit-deterioration":
        # Exercise populated comparison layout, not just its loading/error shell.
        page.get_by_text("TEST_RENDER_COMPARE_0", exact=True).wait_for(timeout=10_000)
    if slug == "users":
        tabs = page.get_by_role("tablist", name="管理页签")
        assert tabs.bounding_box()["width"] <= 400
        assert page.get_by_role("tab", name="角色权限").evaluate("""el => {
            const walker = document.createTreeWalker(el, NodeFilter.SHOW_TEXT);
            let node;
            while ((node = walker.nextNode())) {
                if (!node.textContent.trim()) continue;
                const range = document.createRange();
                range.selectNodeContents(node);
                if (range.getClientRects().length > 1) return false;
            }
            return true;
        }""")
    if slug == "shops":
        filters = page.get_by_role("form", name="店铺筛选")
        bounds = filters.bounding_box()
        controls = filters.locator("input:visible, button:visible")
        assert controls.count() >= 6  # Search, three selectors, search/reset actions.
        for control in controls.all():
            box = control.bounding_box()
            assert box["height"] >= 32
            assert box["x"] >= bounds["x"] - 1
            assert box["x"] + box["width"] <= bounds["x"] + bounds["width"] + 1
    if width < 1024:
        trigger = page.get_by_role("button", name="打开主导航")
        trigger.click()
        page.get_by_role("navigation", name="主导航").wait_for()
        page.keyboard.press("Escape")
        page.get_by_role("dialog").wait_for(state="hidden")
        page.wait_for_function("el => document.activeElement === el", arg=trigger.element_handle(), timeout=5_000)
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
    page.get_by_role("navigation", name="主导航").get_by_role(
        "link", name="店铺注册", exact=True
    ).click()
    page.wait_for_url("**/tts/v2/pages/shops")
    page.get_by_role("heading", level=1, name="店铺注册", exact=True).wait_for()
    assert page.locator("#frontend-root").count() == 1
    page.get_by_role("navigation", name="主导航").get_by_role(
        "link", name="采购工作台", exact=True
    ).click()
    page.wait_for_url("**/tts/v2/pages/manual-costs")
    page.get_by_role("heading", level=1, name="采购工作台", exact=True).wait_for()
    assert page.locator("#frontend-root").count() == 1
    assert page.locator('link[href*="bootstrap.min.css"]').count() == 0


@pytest.mark.parametrize("prefix", ["", "/tts"])
@pytest.mark.parametrize("failed", ["entry", "eager"])
def test_initial_module_failure_keeps_accessible_fallback(frontend_browser_renderer, prefix, failed):
    page = frontend_browser_renderer.open(f"{prefix}/v2/pages/dashboard")
    page.get_by_role("heading", level=1, name="控制台", exact=True).wait_for()
    assets = Path(__file__).resolve().parents[2] / "tts_erp_v2/static/frontend"
    release = json.loads((assets / "release.json").read_text())["releaseId"]
    manifest = json.loads((assets / release / ".vite/manifest.json").read_text())
    entry = manifest["src/main.tsx"]
    chunk = entry["file"] if failed == "entry" else manifest[entry["imports"][0]]["file"]
    page.route("**/" + chunk, lambda route: route.abort())
    page.reload(wait_until="networkidle")
    fallback = page.locator("#frontend-startup")
    assert fallback.is_visible()
    assert "资源可能加载失败" in fallback.inner_text()
    assert fallback.get_by_role("link", name="刷新页面").get_attribute("href") == ""
    assert page.get_by_role("heading", level=1, name="控制台", exact=True).count() == 0


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
