"""Browser user-path contract for the projection sample control."""

from __future__ import annotations

import copy
import time
from collections.abc import Callable
from typing import Any, cast
from urllib.parse import parse_qs

import pytest

import conftest as browser_conftest

pytestmark = [pytest.mark.domain_browser, pytest.mark.requires_browser]


def test_projection_control_is_visible_and_switches_only_projection(browser_renderer, monkeypatch) -> None:
    original_mock_payload = cast(  # type: ignore[attr-defined]
        Callable[[str], Any], browser_conftest._mock_payload  # type: ignore[attr-defined]
    )

    def mock_payload(path: str, query: str = ""):
        if path.endswith("/v2/commerce/channel-product-options"):
            return {"items": [{"spu_id": "TEST_SPU_000", "title": "测试 SPU"}], "queryable": True}
        payload = original_mock_payload(path)
        if path.endswith("/v2/analytics/spu-roi"):
            lookback = int(parse_qs(query).get("projection_lookback_days", [30])[0])
            time.sleep(0.2 if lookback == 30 else 0.01)
            payload = copy.deepcopy(payload)
            payload["meta"]["projection"] = {
                "status": "available",
                "warnings": [],
                "as_of": "2026-10-08",
                "lookback_days": lookback,
                "maturity_lag_days": 7,
                "sample_start": "2026-07-03" if lookback == 90 else "2026-09-01",
                "sample_end": "2026-09-30" if lookback == 90 else "2026-09-30",
                "basis_order_count": lookback,
                "basis_full_loss_order_count": lookback // 10,
                "completed_full_loss_rate": "0.1000",
                "scope": "shop_pk=7",
            }
            payload["totals"]["projection_status"] = "available"
        return payload

    monkeypatch.setattr(browser_conftest, "_mock_payload", mock_payload)
    page = browser_renderer.open("/v2/pages/spu-roi")
    shop_button = page.locator("#shop-modal-list button").first
    if shop_button.is_visible():
        shop_button.click()
        page.wait_for_timeout(300)
    control = page.locator("#filter-projection-lookback-days")
    assert control.is_visible()
    assert control.input_value() == "30"
    assert page.locator("#projection-basis-card").is_visible()
    assert page.locator("#projection-sample-window").is_visible()

    reporting_start = page.locator("#filter-w-start").input_value()
    reporting_end = page.locator("#filter-w-end").input_value()
    assert control.is_enabled()
    requests: list[str] = []
    page.on("request", lambda request: requests.append(request.url))

    control.select_option("90")
    page.wait_for_timeout(500)
    page.wait_for_function(
        "document.querySelector('#filter-projection-lookback-days').disabled === false"
    )
    projection_requests = [url for url in requests if "/v2/analytics/spu-roi" in url]
    assert projection_requests
    assert any("projection_lookback_days=90" in url for url in projection_requests)
    assert page.locator("#filter-w-start").input_value() == reporting_start
    assert page.locator("#filter-w-end").input_value() == reporting_end
    card = page.locator("#projection-basis-card")
    window = card.locator(".op-projection-window")
    assert window.inner_text() == "预测样本窗口：2026-07-03 ~ 2026-09-30"
    assert page.locator("#projection-maturity-as-of").count() == 0
    assert page.locator("#projection-basis-counts").count() == 0
    assert page.locator("#projection-basis-status").count() == 0

    page.evaluate(
        """() => {
          const control = document.querySelector('#filter-projection-lookback-days');
          control.value = '30';
          control.dispatchEvent(new Event('change', {bubbles: true}));
          control.value = '90';
          control.dispatchEvent(new Event('change', {bubbles: true}));
        }"""
    )
    page.wait_for_timeout(700)
    assert window.inner_text() == "预测样本窗口：2026-07-03 ~ 2026-09-30"
