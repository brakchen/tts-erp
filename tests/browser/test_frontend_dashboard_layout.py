"""Compiled dashboard visual contracts, with canned TEST GET data only."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from render_support import overflow_report

pytestmark = [pytest.mark.domain_browser, pytest.mark.requires_browser]


@pytest.mark.parametrize("prefix", ["", "/tts"])
@pytest.mark.parametrize("width", [375, 1440, 2560])
def test_dashboard_has_readable_visual_hierarchy(frontend_browser_renderer, prefix, width):
    page = frontend_browser_renderer.open(f"{prefix}/v2/pages/dashboard", width=width)
    page.set_viewport_size({"width": width, "height": 1271 if width == 2560 else 900})
    main = page.get_by_role("main")
    metric = main.get_by_role("region", name="SPU 总数", exact=True)
    value = metric.get_by_text("1234", exact=True)
    value.wait_for()
    page.evaluate("document.fonts.ready")
    slugs = ["spu-roi", "ad-daily", "manual-costs", "shops", "runtime-configs", "intercept-configs"]
    links = [main.locator(f'a[href="{prefix}/v2/pages/{slug}"]').first for slug in slugs]
    boxes = [link.bounding_box() for link in links]
    assert all(box is not None for box in boxes)
    rows = {round(box["y"]) for box in boxes}
    value_size = value.evaluate("el => parseFloat(getComputedStyle(el).fontSize)")
    hint_size = metric.get_by_text("已跟踪的 SPU 数量", exact=True).evaluate(
        "el => parseFloat(getComputedStyle(el).fontSize)"
    )
    parent_layout = links[0].evaluate(
        "el => ({display: getComputedStyle(el.parentElement).display, "
        "direction: getComputedStyle(el.parentElement).flexDirection, "
        "columnWidth: parseFloat(getComputedStyle(el.parentElement).gridTemplateColumns)})"
    )
    artifacts = Path(__file__).resolve().parents[2] / "frontend/.cache/screenshots"
    artifacts.mkdir(parents=True, exist_ok=True)
    page.screenshot(
        path=str(artifacts / f"TEST_dashboard_hierarchy_{width}_{prefix.strip('/') or 'direct'}.png"),
        full_page=True,
    )
    observed = {"rows": len(rows), "valueFont": value_size, "hintFont": hint_size,
                "minimumTargetHeight": min(box["height"] for box in boxes), "parent": parent_layout}
    print("TEST dashboard layout:", json.dumps(observed))
    assert len(rows) <= (3 if width < 1024 else 2), observed
    assert all(box["height"] >= 36 for box in boxes), observed
    assert all(box["width"] >= parent_layout["columnWidth"] * 0.95 for box in boxes), observed
    assert value_size >= hint_size * 1.6, observed
    links[0].focus()
    page.keyboard.press("Tab")
    assert links[1].evaluate("el => document.activeElement === el")

    shops = main.get_by_role("grid", name="已注册店铺", exact=True)
    shops.wait_for()
    for title in ["店铺", "店铺 ID", "地区", "平台"]:
        assert shops.get_by_role("columnheader", name=title, exact=True).is_visible()
    report = overflow_report(page)
    assert report["scrollWidth"] <= report["clientWidth"] + 1, report
    assert not report["offenders"], report
