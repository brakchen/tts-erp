"""SPU ROI 日期输入不得在编辑中途把自己刚填的值清掉。

回归：type=date 先改日再改年时，Chrome 会在仍聚焦时抛出空 change；
起始晚于截止时旧逻辑还会 e.target.value=""，把刚输入的日/整段日期抹掉。
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, cast

import pytest

import conftest as browser_conftest

pytestmark = [pytest.mark.domain_browser, pytest.mark.requires_browser]


def _open_spu_roi(browser_renderer: Any, monkeypatch: pytest.MonkeyPatch) -> Any:
    original = cast(Callable[..., Any], browser_conftest._mock_payload)

    def mock_payload(request_path: str, query: str = "") -> Any:
        if request_path.endswith("/v2/commerce/channel-product-options"):
            return {
                "items": [{"spu_id": "TEST_SPU_000", "title": "测试 SPU"}],
                "queryable": True,
            }
        return original(request_path, query)

    monkeypatch.setattr(browser_conftest, "_mock_payload", mock_payload)
    page = browser_renderer.open("/v2/pages/spu-roi")
    shop_button = page.locator("#shop-modal-list button").first
    if shop_button.is_visible():
        shop_button.click()
    page.locator("#filter-w-start").wait_for(state="visible")
    page.locator("#filter-w-end").wait_for(state="visible")
    return page


def test_inverted_range_keeps_the_date_just_typed(
    browser_renderer: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    page = _open_spu_roi(browser_renderer, monkeypatch)
    bounds = page.evaluate(
        """() => {
          const end = document.querySelector('#filter-w-end');
          const max = end.max || '2026-10-22';
          const [y, m, d] = max.split('-').map(Number);
          const later = new Date(Date.UTC(y, m - 1, d - 2));
          const earlier = new Date(Date.UTC(y, m - 1, d - 12));
          const iso = (dt) => dt.toISOString().slice(0, 10);
          return { earlier: iso(earlier), later: iso(later), max };
        }"""
    )
    page.evaluate(
        """({ earlier, later }) => {
          const start = document.querySelector('#filter-w-start');
          const end = document.querySelector('#filter-w-end');
          end.value = earlier;
          end.dispatchEvent(new Event('change', {bubbles: true}));
          start.value = later;
          start.dispatchEvent(new Event('change', {bubbles: true}));
        }""",
        bounds,
    )
    assert page.locator("#filter-w-start").input_value() == bounds["later"]
    assert page.locator("#filter-w-end").input_value() == bounds["earlier"]
    assert "起始日期不能晚于截止日期" in page.locator("#rows").inner_text()


def test_empty_change_while_focused_does_not_commit_clear(
    browser_renderer: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    page = _open_spu_roi(browser_renderer, monkeypatch)
    requests: list[str] = []
    page.on("request", lambda request: requests.append(request.url))
    page.evaluate(
        """() => {
          const start = document.querySelector('#filter-w-start');
          const end = document.querySelector('#filter-w-end');
          end.value = '2026-10-21';
          end.dispatchEvent(new Event('change', {bubbles: true}));
          start.value = '2026-09-01';
          start.dispatchEvent(new Event('change', {bubbles: true}));
        }"""
    )
    page.wait_for_timeout(80)
    before = len(requests)
    page.evaluate(
        """() => {
          const start = document.querySelector('#filter-w-start');
          start.focus();
          start.value = '';
          start.dispatchEvent(new Event('change', {bubbles: true}));
        }"""
    )
    page.wait_for_timeout(80)
    new_urls = requests[before:]
    assert not any("/v2/analytics/spu-roi" in url for url in new_urls), new_urls
    page.evaluate(
        """() => {
          const start = document.querySelector('#filter-w-start');
          start.blur();
        }"""
    )
    page.wait_for_timeout(80)
    after_blur = [url for url in requests[before:] if "/v2/analytics/spu-roi" in url]
    assert after_blur, requests[before:]
    assert any("w_start=" not in url.split("?")[-1] for url in after_blur), after_blur


def test_date_inputs_expose_min_max_bounds(
    browser_renderer: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    page = _open_spu_roi(browser_renderer, monkeypatch)
    start = page.locator("#filter-w-start")
    end = page.locator("#filter-w-end")
    assert start.get_attribute("min") == "2000-01-01"
    assert end.get_attribute("min") == "2000-01-01"
    start_max = start.get_attribute("max") or ""
    end_max = end.get_attribute("max") or ""
    assert start_max == "2099-12-31"
    assert end_max == "2099-12-31"


def test_out_of_range_year_restores_previous_date(
    browser_renderer: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    page = _open_spu_roi(browser_renderer, monkeypatch)
    page.evaluate(
        """() => {
          const end = document.querySelector('#filter-w-end');
          end.value = '2026-10-21';
          end.dispatchEvent(new Event('change', {bubbles: true}));
        }"""
    )
    assert page.locator("#filter-w-end").input_value() == "2026-10-21"
    page.evaluate(
        """() => {
          const end = document.querySelector('#filter-w-end');
          end.value = '2100-01-01';
          end.dispatchEvent(new Event('change', {bubbles: true}));
        }"""
    )
    assert page.locator("#filter-w-end").input_value() == "2026-10-21"
    assert "日期无效" in page.locator("#rows").inner_text()
