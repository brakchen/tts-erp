"""The upload entry remains accessible in the compiled shared workbench."""

from __future__ import annotations

import pytest
from playwright.sync_api import expect
from render_support import overflow_report

pytestmark = [pytest.mark.domain_browser, pytest.mark.requires_browser]


@pytest.mark.parametrize("prefix", ["", "/tts"])
@pytest.mark.parametrize("width", [375, 1440, 2560])
def test_upload_entry_layout(frontend_browser_renderer, tmp_path, prefix, width):
    page = frontend_browser_renderer.open(f"{prefix}/v2/pages/manual-costs", width=width)
    panel = page.get_by_role("region", name="文件导入采购价", exact=True)
    expect(panel.get_by_role("heading", name="从订单包裹文件导入采购价")).to_be_visible()
    expect(panel.get_by_label("采购价 Excel 文件")).to_be_visible()
    expect(panel.get_by_role("button", name="上传并预览")).to_be_visible()
    report = overflow_report(page)
    assert report["scrollWidth"] <= report["clientWidth"] + 1, report
    assert not report["offenders"], report
    for control in panel.locator("input, button").all():
        box = control.bounding_box()
        assert box is not None and box["width"] > 0 and box["height"] >= 30
        assert box["x"] >= 0 and box["x"] + box["width"] <= width + 1
    page.evaluate("document.fonts.ready")
    page.screenshot(path=str(tmp_path / f"TEST_cost_import_{width}.png"), full_page=True)
