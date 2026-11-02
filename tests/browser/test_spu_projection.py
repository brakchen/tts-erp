"""Browser user-path contract for the projection sample control."""

from __future__ import annotations

import copy
import time
from collections.abc import Callable
from datetime import date, timedelta
from threading import Event
from typing import Any, cast
from urllib.parse import parse_qs

import pytest
from playwright.sync_api import expect

import conftest as browser_conftest

pytestmark = [pytest.mark.domain_browser, pytest.mark.requires_browser]


def test_projection_control_is_visible_and_switches_only_projection(frontend_browser_renderer, monkeypatch) -> None:
    original_mock_payload = cast(  # type: ignore[attr-defined]
        Callable[[str], Any], browser_conftest._mock_payload  # type: ignore[attr-defined]
    )

    old_started = Event()
    release_old = Event()
    old_returned = Event()
    race = {"enabled": False}
    next_start = ""

    def mock_payload(path: str, query: str = ""):
        if path.endswith("/v2/commerce/channel-product-options"):
            return [{"spu_id": "TEST_SPU_000", "title": "测试 SPU"}]
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
                "sample_end": "2026-09-30",
                "basis_order_count": lookback,
                "basis_full_loss_order_count": lookback // 10,
                "completed_full_loss_rate": "0.1000",
                "scope": "shop_pk=7",
            }
            payload["totals"]["projection_status"] = "available"
            if race["enabled"] and lookback == 30:
                if parse_qs(query).get("w_start") == [next_start]:
                    # A distinct returned snapshot identifies the new query.
                    payload["meta"]["projection"]["sample_start"] = "2026-09-02"
                else:
                    old_started.set()
                    assert release_old.wait(timeout=10), "old scoped response was not released"
                    old_returned.set()
        return payload

    monkeypatch.setattr(browser_conftest, "_mock_payload", mock_payload)
    page = frontend_browser_renderer.open("/v2/pages/spu-roi")
    shop_button = page.locator("#shop-modal-list button").first
    if shop_button.is_visible():
        shop_button.click()
        page.wait_for_timeout(300)
    control = page.locator("#filter-projection-lookback-days")
    assert control.is_visible()
    expect(control).to_contain_text("30 天")
    expect(page.locator("#projection-basis-card")).to_be_visible()
    page.locator("#projection-basis-card").get_by_role("button", name="预测依据", exact=True).click()
    assert page.locator("#projection-sample-window").is_visible()

    reporting_start = page.locator("#filter-w-start").input_value()
    reporting_end = page.locator("#filter-w-end").input_value()
    next_start = (date.fromisoformat(reporting_start) - timedelta(days=1)).isoformat()
    assert control.is_enabled()
    requests: list[str] = []
    page.on("request", lambda request: requests.append(request.url))

    control.click()
    page.get_by_role("option", name="90 天", exact=True).click()
    page.wait_for_timeout(500)
    expect(control).to_be_enabled()
    projection_requests = [url for url in requests if "/v2/analytics/spu-roi" in url]
    assert projection_requests
    assert any("projection_lookback_days=90" in url for url in projection_requests)
    assert page.locator("#filter-w-start").input_value() == reporting_start
    assert page.locator("#filter-w-end").input_value() == reporting_end
    card = page.locator("#projection-basis-card")
    window = card.locator(".op-projection-window")
    assert window.inner_text() == "样本：2026-07-03 ~ 2026-09-30"
    title = card.locator(".op-counter-group-label")
    assert title.inner_text() == "预测依据"
    assert card.locator(".op-projection-window").count() == 1
    assert page.locator("#projection-maturity-as-of").count() == 0
    assert page.locator("#projection-basis-counts").count() == 0
    assert page.locator("#projection-basis-status").count() == 0

    # The sample menu is disabled while its read is pending. Keep that gate and
    # use the still-editable reporting date to replace the query through real UI.
    # The old server handler remains held until the new range has rendered.
    race["enabled"] = True
    try:
        control.click()
        page.get_by_role("option", name="30 天", exact=True).click()
        assert old_started.wait(timeout=3), "the 30-day request never started"
        expect(control).to_be_disabled()
        with page.expect_request(
            lambda request: "/v2/analytics/spu-roi?" in request.url
            and f"w_start={next_start}" in request.url
            and "projection_lookback_days=30" in request.url
        ):
            page.locator("#filter-w-start").fill(next_start)
        expect(window).to_have_text("样本：2026-09-02 ~ 2026-09-30")
        expect(control).to_be_enabled()
        expect(control).to_contain_text("30 天")
        expect(page.locator("#filter-w-start")).to_have_value(next_start)
        expect(page.locator("#filter-w-end")).to_have_value(reporting_end)
    finally:
        release_old.set()
    assert old_returned.wait(timeout=3), "the old scoped handler did not finish"
    # Allow the old response attempt to reach the browser after the new one.
    page.wait_for_timeout(250)
    expect(window).to_have_text("样本：2026-09-02 ~ 2026-09-30")
    expect(page.locator("#filter-w-end")).to_have_value(reporting_end)
