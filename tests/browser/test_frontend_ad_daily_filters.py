"""Compiled filter layout: labels beside compact, consistently sized controls."""

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
    page.evaluate("document.fonts.ready")
    controls = [form.get_by_role("button", name=re.compile(re.escape(label))) for label in [
        "店铺（外部 Seller ID）", "广告账户", "数据接口",
    ]] + [form.get_by_label(label, exact=True) for label in [
        "开始日期", "结束日期", "计划 / 商品 ID 搜索",
    ]]
    columns = 1 if width == 375 else 2 if width == 900 else 6 if width == 2560 else 3
    fields = []
    for control in controls:
        layout = control.evaluate("""el => {
            const field = el.closest('.ad-filter-field').getBoundingClientRect();
            const label = el.closest('.ad-filter-field').querySelector(':scope > .label').getBoundingClientRect();
            const input = el.getBoundingClientRect();
            return {fieldLeft: field.left, fieldRight: field.right,
                    labelRight: label.right, inputLeft: input.left, inputRight: input.right,
                    labelCenter: label.top + label.height / 2,
                    inputCenter: input.top + input.height / 2, inputHeight: input.height};
        }""")
        fields.append(round(layout["fieldLeft"]))
        assert layout["labelRight"] <= layout["inputLeft"]
        assert abs(layout["labelCenter"] - layout["inputCenter"]) < 2
        assert abs(layout["fieldRight"] - layout["inputRight"]) < 2
        assert 31 <= layout["inputHeight"] <= 33
    assert len(set(fields)) == columns
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
    expect(form.get_by_role("button", name=re.compile("每页行数"))).to_contain_text("50 条 / 页")
    expect(page).not_to_have_url(re.compile(r"[?&]q="))


def test_ad_details_escape_restores_the_invoking_button(frontend_browser_renderer):
    page = frontend_browser_renderer.open("/v2/pages/ad-daily", width=1440)
    trigger = page.get_by_role("button", name=re.compile(r"^更多详情 ")).first
    trigger.click()
    heading = page.get_by_role("heading", name=re.compile(r"^记录详情 "))
    expect(heading).to_be_focused()
    page.keyboard.press("Escape")
    expect(heading).to_have_count(0)
    expect(trigger).to_be_focused()
    page.keyboard.press("Enter")
    expect(heading).to_be_focused()
