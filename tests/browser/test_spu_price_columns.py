"""Browser renderer contract for nested SPU price statistics.

This is component evidence only: the renderer uses the real shared kernel, profiles,
Tabulator vendor asset, and nested response shape, while the target API is mocked by
the browser fixture. Full-stack API/E2E evidence remains a separate lane.
"""

from __future__ import annotations

import copy
from collections.abc import Callable
from typing import Any, cast

import pytest

import conftest as browser_conftest

pytestmark = [pytest.mark.domain_browser, pytest.mark.requires_browser]


_METRICS = ("purchase", "originalSale", "paid")
_SORTS = (
    "purchasePriceMean",
    "purchasePriceMedian",
    "originalSalePriceMean",
    "originalSalePriceMedian",
    "paidPriceMean",
    "paidPriceMedian",
)


def _price_stats() -> dict[str, dict[str, Any]]:
    return {
        "purchase": {
            "mean": "37.0000",
            "median": "40.0000",
            "eligibleQuantity": 10,
            "observedQuantity": 10,
            "missingQuantity": 0,
            "invalidQuantity": 0,
            "coverageRatio": "1.0000",
            "status": "complete",
            "source": "roi_unit_cost",
            "estimated": True,
        },
        "originalSale": {
            "mean": "55.0000",
            "median": "60.0000",
            "eligibleQuantity": 10,
            "observedQuantity": 9,
            "missingQuantity": 1,
            "invalidQuantity": 0,
            "coverageRatio": "0.9000",
            "status": "partial",
            "source": "tiktok_line_item_original_price",
            "estimated": False,
        },
        "paid": {
            "mean": "0.0000",
            "median": None,
            "eligibleQuantity": 10,
            "observedQuantity": 9,
            "missingQuantity": 1,
            "invalidQuantity": 0,
            "coverageRatio": "0.9000",
            "status": "partial",
            "source": "tiktok_line_item_sale_price",
            "estimated": False,
        },
    }


def _nested_payload(original: Callable[..., Any]) -> dict[str, Any]:
    payload = copy.deepcopy(original("/v2/analytics/spu-roi"))
    stats = _price_stats()
    payload["totals"]["priceStats"] = stats
    payload.setdefault("meta", {}).update(
        {
            "calculatedAt": "2026-10-06T00:00:00+00:00",
            "priceFx": {"snapshotId": 901},
            "priceCost": {"basisFingerprint": "sha256:test-cost-map"},
        }
    )
    for item in payload.get("items", []):
        item["priceStats"] = copy.deepcopy(stats)
    return payload


def _open_with_supported_payload(browser_renderer, monkeypatch, path: str):
    original = cast(Callable[..., Any], browser_conftest._mock_payload)

    def mock_payload(request_path: str, query: str = ""):
        if request_path.endswith("/v2/commerce/channel-product-options"):
            return {"items": [{"spu_id": "TEST_SPU_000", "title": "测试 SPU"}], "queryable": True}
        if request_path.endswith("/v2/analytics/spu-roi"):
            return _nested_payload(original)
        return original(request_path, query)

    monkeypatch.setattr(browser_conftest, "_mock_payload", mock_payload)
    page = browser_renderer.open(path)
    shop_button = page.locator("#shop-modal-list button").first
    if shop_button.is_visible():
        shop_button.click()
    page.locator("#price-summary").wait_for(state="visible")
    page.locator(".tabulator-row").first.wait_for(state="visible")
    return page


def test_nested_price_summary_and_grouped_columns_render_on_both_profiles(browser_renderer, monkeypatch):
    for path in ("/v2/pages/spu-roi", "/v2/pages/focused-spus"):
        page = _open_with_supported_payload(browser_renderer, monkeypatch, path)
        assert "采购价" in page.locator("#price-summary").inner_text()
        assert "37.0000" in page.locator("#price-summary").inner_text()
        assert "≈40.0000" in page.locator("#price-summary").inner_text()
        assert "0.0000" in page.locator(".tabulator-row").first.inner_text()
        assert page.locator('.tabulator-header [tabulator-field="priceStats.purchase.mean"]').count() == 1
        assert page.locator('.tabulator-header [tabulator-field="priceStats.originalSale.median"]').count() == 1
        assert page.locator('.tabulator-header [tabulator-field="priceStats.paid.mean"]').count() == 1


def test_each_price_header_sends_its_server_sort_id(browser_renderer, monkeypatch):
    page = _open_with_supported_payload(browser_renderer, monkeypatch, "/v2/pages/spu-roi")
    requests: list[str] = []
    page.on("request", lambda request: requests.append(request.url))
    for field, sort_id in zip(
        (
            "priceStats.purchase.mean",
            "priceStats.purchase.median",
            "priceStats.originalSale.mean",
            "priceStats.originalSale.median",
            "priceStats.paid.mean",
            "priceStats.paid.median",
        ),
        _SORTS,
    ):
        page.locator(f'.tabulator-header [tabulator-field="{field}"]').click()
        page.wait_for_timeout(80)
        assert any(f"sort={sort_id}" in url for url in requests), (field, sort_id, requests)


def test_price_tooltip_is_keyboard_openable_and_restores_focus(browser_renderer, monkeypatch):
    page = _open_with_supported_payload(browser_renderer, monkeypatch, "/v2/pages/spu-roi")
    button = page.locator('[data-price-tip="purchase"]')
    button.focus()
    page.keyboard.press("Enter")
    assert button.get_attribute("aria-expanded") == "true"
    text = page.locator("#ops-tip").inner_text()
    assert "TikTok" not in text
    assert "ROI 当前有效成本" in text
    assert "计算时间：2026-10-06T00:00:00+00:00" in text
    page.keyboard.press("Escape")
    assert button.get_attribute("aria-expanded") == "false"
    assert page.evaluate("document.activeElement === document.querySelector('[data-price-tip=\\\"purchase\\\"]')")


def test_legacy_payload_hides_price_capability_without_fake_zeroes(browser_renderer):
    page = browser_renderer.open("/v2/pages/spu-roi")
    shop_button = page.locator("#shop-modal-list button").first
    if shop_button.is_visible():
        shop_button.click()
    page.wait_for_timeout(500)
    assert page.locator("#price-summary").is_hidden()
    assert page.locator('.tabulator-header [tabulator-field="priceStats.purchase.mean"]').is_hidden()


def test_mobile_price_table_scrolls_without_document_overflow(browser_renderer, monkeypatch):
    page = _open_with_supported_payload(browser_renderer, monkeypatch, "/v2/pages/spu-roi")
    page.set_viewport_size({"width": 390, "height": 844})
    page.wait_for_timeout(100)
    report = page.evaluate(
        """() => ({
          documentOverflow: document.documentElement.scrollWidth > document.documentElement.clientWidth,
          tableOverflow: document.querySelector('.op-table-wrap').scrollWidth > document.querySelector('.op-table-wrap').clientWidth,
          productFrozen: Boolean(document.querySelector('.tabulator-cell.tabulator-frozen')),
        })"""
    )
    assert report == {"documentOverflow": False, "tableOverflow": True, "productFrozen": True}
