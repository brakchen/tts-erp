"""Comparison drill-down dates must reach the profitability query unchanged (AC-016)."""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Any, cast
from urllib.parse import parse_qs, urlencode, urlsplit
from zoneinfo import ZoneInfo

import pytest

import conftest as browser_conftest

pytestmark = [pytest.mark.domain_browser, pytest.mark.requires_browser]

SPU_ID = "TEST_COMPARISON_BUSINESS_SPU"
PREFERENCES_KEY = "tts-erp:spu-roi:preferences:v1"
SAVED_START = "2026-08-01"
SAVED_END = "2026-08-07"


def _open_target(
    browser_renderer: Any,
    monkeypatch: pytest.MonkeyPatch,
    dates: dict[str, str],
    *,
    saved: bool = False,
    reject_dates: bool = False,
) -> tuple[Any, list[dict[str, list[str]]]]:
    original = cast(Callable[..., Any], cast(Any, browser_conftest)._mock_payload)

    def mock_payload(request_path: str, query: str = "") -> Any:
        if request_path.endswith("/v2/commerce/channel-product-options"):
            return [{"spu_id": SPU_ID, "title": "TEST comparison SPU"}]
        return original(request_path, query)

    monkeypatch.setattr(browser_conftest, "_mock_payload", mock_payload)
    page = browser_renderer.open("/v2/pages/spu-roi?shop_pk=7")
    page.wait_for_load_state("networkidle")
    preferences = (
        {"datesTouched": True, "wStart": SAVED_START, "wEnd": SAVED_END}
        if saved
        else None
    )
    page.evaluate(
        """([key, saved]) => {
            localStorage.removeItem(key);
            if (saved) localStorage.setItem(key, JSON.stringify(saved));
        }""",
        [PREFERENCES_KEY, preferences],
    )
    queries: list[dict[str, list[str]]] = []

    def analytics_route(route: Any) -> None:
        queries.append(parse_qs(urlsplit(route.request.url).query))
        if reject_dates:
            # Model the existing API's invalid-date response, not a new UI policy.
            route.fulfill(
                status=422,
                content_type="application/json",
                body=json.dumps({"detail": "TEST invalid date range"}),
            )
        else:
            route.continue_()

    page.route("**/v2/analytics/spu-roi?*", analytics_route)
    target = page.url.split("?", 1)[0] + "?" + urlencode(
        {"shop_pk": "7", "spu_ids": SPU_ID, **dates}
    )
    with page.expect_response(lambda response: "/v2/analytics/spu-roi?" in response.url):
        page.goto(target)
    page.locator("#filter-w-start").wait_for(state="visible")
    return page, queries


@pytest.mark.parametrize("saved", [False, True], ids=["fresh", "saved-preferences"])
def test_explicit_url_dates_and_business_spu_reach_query(
    browser_renderer: Any, monkeypatch: pytest.MonkeyPatch, saved: bool
) -> None:
    page, queries = _open_target(
        browser_renderer,
        monkeypatch,
        {"w_start": "2026-09-24", "w_end": "2026-09-30"},
        saved=saved,
    )
    assert queries
    for query in queries:
        assert query["shop_pk"] == ["7"]
        assert query["spu_ids"] == [SPU_ID]
        assert query["w_start"] == ["2026-09-24"]
        assert query["w_end"] == ["2026-09-30"]
    assert page.locator("#filter-w-start").input_value() == "2026-09-24"
    assert page.locator("#filter-w-end").input_value() == "2026-09-30"


@pytest.mark.parametrize("saved", [False, True], ids=["automatic-t-1", "saved-preferences"])
def test_without_url_dates_preserves_normal_date_behavior(
    browser_renderer: Any, monkeypatch: pytest.MonkeyPatch, saved: bool
) -> None:
    page, queries = _open_target(browser_renderer, monkeypatch, {}, saved=saved)
    yesterday = (datetime.now(ZoneInfo("Asia/Ho_Chi_Minh")) - timedelta(days=1)).date().isoformat()
    expected_start = SAVED_START if saved else yesterday
    expected_end = SAVED_END if saved else yesterday
    assert page.locator("#filter-w-start").input_value() == expected_start
    assert page.locator("#filter-w-end").input_value() == expected_end
    assert queries
    for query in queries:
        assert query["w_start"] == [expected_start]
        assert query["w_end"] == [expected_end]


@pytest.mark.parametrize(
    "dates",
    [
        {"w_start": "not-a-date", "w_end": "2026-09-30"},
        {"w_start": "2026-02-30", "w_end": "2026-03-07"},
        {"w_start": "2026-10-01", "w_end": "2026-09-30"},
    ],
    ids=["malformed", "invalid-calendar-date", "inverted"],
)
def test_invalid_url_dates_reach_existing_api_validation_without_widening(
    browser_renderer: Any, monkeypatch: pytest.MonkeyPatch, dates: dict[str, str]
) -> None:
    page, queries = _open_target(
        browser_renderer, monkeypatch, dates, saved=True, reject_dates=True
    )
    page.wait_for_function(
        "document.querySelector('#rows').textContent.includes('加载失败')"
    )
    assert queries
    for query in queries:
        assert query["shop_pk"] == ["7"]
        assert query["spu_ids"] == [SPU_ID]
        assert query["w_start"] == [dates["w_start"]]
        assert query["w_end"] == [dates["w_end"]]
