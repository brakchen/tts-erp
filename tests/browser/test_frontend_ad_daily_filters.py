"""Compiled filter layout: labels above full-width, consistently sized controls."""

from __future__ import annotations

import re

import pytest
from playwright.sync_api import expect

pytestmark = [pytest.mark.domain_browser, pytest.mark.requires_browser]


@pytest.mark.parametrize("width", [375, 900, 1440, 2560])
def test_ad_filters_fit_and_align(frontend_browser_renderer, width):
    page = frontend_browser_renderer.open("/v2/pages/ad-daily", width=width)
    form = page.get_by_role("form", name="广告筛选")
    expect(form.get_by_role("button", name="应用筛选")).to_be_enabled()
    controls = [form.get_by_label(label, exact=True) for label in [
        "店铺（外部 Seller ID）", "广告账户", "数据接口", "开始日期", "结束日期", "计划 / 商品 ID 搜索",
    ]]
    boxes = [control.bounding_box() for control in controls]
    assert all(box is not None for box in boxes)
    columns = 1 if width == 375 else 2 if width == 900 else 3
    assert len({round(box["x"]) for box in boxes}) == columns
    assert max(box["width"] for box in boxes) - min(box["width"] for box in boxes) < 2
    assert all(43 <= box["height"] <= 45 for box in boxes)
    for control in controls:
        layout = control.evaluate("""el => {
            const field = el.closest('label, [data-slot="textfield"]');
            const label = field.querySelector('span, label');
            return {labelBottom: label.getBoundingClientRect().bottom,
                    inputTop: el.getBoundingClientRect().top,
                    fieldWidth: field.getBoundingClientRect().width,
                    inputWidth: el.getBoundingClientRect().width};
        }""")
        assert layout["labelBottom"] <= layout["inputTop"]
        assert abs(layout["fieldWidth"] - layout["inputWidth"]) < 2
    assert page.evaluate("document.documentElement.scrollWidth <= document.documentElement.clientWidth + 1")


def test_ad_filters_submit_by_enter_and_reset(frontend_browser_renderer):
    page = frontend_browser_renderer.open("/v2/pages/ad-daily", width=1440)
    form = page.get_by_role("form", name="广告筛选")
    expect(form.get_by_role("button", name="应用筛选")).to_be_enabled()
    search = form.get_by_role("searchbox", name="计划 / 商品 ID 搜索")
    search.fill(" TEST_1877 ")
    search.press("Enter")
    expect(page).to_have_url(re.compile(r"[?&]q=TEST_1877(?:&|$)"))
    expect(form.get_by_role("button", name="重置筛选")).to_be_enabled()
    form.get_by_role("button", name="重置筛选").click()
    expect(search).to_have_value("")
    expect(form.get_by_label("每页行数")).to_have_value("50")
    expect(page).not_to_have_url(re.compile(r"[?&]q="))
