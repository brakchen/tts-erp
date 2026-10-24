"""Current-worktree SPU comparison journey: isolated DB + cold API + Chromium.

Never targets a resident :9877 service or production data. Run only through
``bash scripts/test_isolated.sh e2e tests/e2e/test_spu_deterioration_alert_smoke.py``.
Missing browser/runtime prerequisites fail rather than produce an all-skip gate.
The API fixture supplies known facts; assertions below are independent expected
business counts/amounts, not values copied from the API into the expected DOM.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterator
from datetime import timedelta
from typing import Any
from urllib.parse import parse_qs, urlencode, urlsplit

import pytest
from sqlalchemy import delete, insert

from tests.api.test_spu_deterioration_alert_api import (
    comparison_scope as comparison_scope,
)
from tests.support.frontend_p2_live import live_api_server
from tts_erp_v2.db.base import Base

pytestmark = [pytest.mark.domain_e2e, pytest.mark.requires_browser]

PAGE = "/v2/pages/spu-profit-deterioration"
API = "/v2/analytics/spu-profit-deterioration"


@pytest.fixture()
def comparison_live(db_url, db_engine, comparison_scope, ua_user_factory):
    """Use the maintained child/HTTP/clone binding proof, not a guessed port."""
    user = ua_user_factory("test_ua_compare_e2e", roles=("viewer",))
    key = "ttserp_readonly_TEST_COMPARE_E2E"
    digest = hashlib.sha256(key.encode()).hexdigest()
    table = Base.metadata.tables["security.api_keys"]
    with db_engine.begin() as connection:
        connection.execute(
            insert(table).values(
                key_hash=digest,
                key_prefix=key[:16],
                name="TEST_COMPARE_E2E_READONLY",
                role="readonly",
                status="active",
            )
        )
    try:
        with live_api_server(db_url) as child:
            yield child, comparison_scope, user, key
    finally:
        with db_engine.begin() as connection:
            connection.execute(delete(table).where(table.c.key_hash == digest))


@pytest.fixture()
def chromium() -> Iterator[Any]:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        try:
            yield browser
        finally:
            browser.close()


def _comparison_response(response: Any, window: int | None = None) -> bool:
    parsed = urlsplit(response.url)
    return parsed.path.endswith(API) and (
        window is None
        or parse_qs(parsed.query).get("window_days") == [str(window)]
    )


@pytest.mark.parametrize("auth,width", [("session", 1440), ("api_key", 390)])
def test_real_comparison_journey(comparison_live, chromium, tmp_path, auth, width):
    """AC-001/009/013/014/022: real values and stale readability."""
    from playwright.sync_api import expect

    child, scope, user, key = comparison_live
    child.require_live()
    context = chromium.new_context(viewport={"width": width, "height": 1000})
    if auth == "session":
        login = context.request.post(
            child.base + "/v2/auth/login",
            data={"username": user.username, "password": user.password},
        )
        assert login.status == 200
        assert any(c["name"] == "tts_erp_session" for c in context.cookies())
    else:
        context.set_extra_http_headers({"Authorization": f"Bearer {key}"})
    page = context.new_page()
    writes: list[str] = []
    page.on("request", lambda request: writes.append(request.method)
            if request.method not in {"GET", "HEAD"} else None)
    query = urlencode({"shop_pk": scope["shop"]})
    try:
        with page.expect_response(_comparison_response) as loaded:
            page.goto(child.base + "/tts" + PAGE + "?" + query)
        assert loaded.value.status == 200, loaded.value.text()
        payload = loaded.value.json()
        assert payload["totals"] == {
            "analysisSpuCount": 4,
            "previousOrderCount": 1,
            "currentOrderCount": 1,
            "unavailableReason": None,
        }
        anchor = scope["anchor"].isoformat()
        expect(page.locator("#filter-anchor-date")).to_have_value(anchor)
        expect(page.locator("#filter-anchor-date")).to_have_attribute("max", anchor)
        expect(page.locator("#total-spus")).to_have_text("4")
        expect(page.locator("#total-current-orders")).to_have_text("1")
        expect(page.locator("#total-previous-orders")).to_have_text("1")
        expect(page.locator("#alert-freshness")).to_contain_text("数据未更新")
        container = page.locator("#alert-rows" if width > 1000 else "#alert-cards")
        expect(container).to_contain_text("TEST_COMPARE_3")
        expect(container).to_contain_text("无单但有广告费")
        # Known ad-only SPU: seven daily USD1 spends, USD/CNY=7; no orders.
        expect(container).to_contain_text(re.compile(r"[-−]49(?:\.0+)?"))
        assert page.locator("#btn-settings, #filter-severity, #filter-state").count() == 0
        with page.expect_response(lambda response: _comparison_response(response, 3)) as short:
            page.locator('[data-window-days="3"]').click()
        assert short.value.status == 200
        assert short.value.json()["meta"]["latestAvailableDate"] == anchor
        expect(page.locator("#total-spus")).to_have_text("4")
        expect(page.locator("#total-previous-orders")).to_have_text("0")
        expect(container).to_contain_text(re.compile(r"[-−]21(?:\.0+)?"))
        expect(page.locator("#filter-anchor-date")).to_have_value(anchor)
        # Re-reading never invents a later source-data day.
        with page.expect_response(_comparison_response) as refreshed:
            page.locator("#btn-refresh").click()
        assert refreshed.value.status == 200
        assert refreshed.value.json()["meta"]["dataAsOf"] == anchor
        page.screenshot(path=str(tmp_path / f"comparison-{auth}-{width}.png"), full_page=True)
        assert writes == [], writes
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1")
    finally:
        context.close()


def test_real_http_auth_date_and_legacy_filter_boundaries(comparison_live, chromium):
    """No live-service fallback, silent date clamping or anonymous business data."""
    child, scope, _user, key = comparison_live
    with chromium.new_context() as context:
        anonymous = context.request.get(child.base + API + f"?shop_pk={scope['shop']}")
        assert anonymous.status == 401
        context.set_extra_http_headers({"Authorization": f"Bearer {key}"})
        missing = context.request.get(child.base + API)
        assert missing.status == 422
        tomorrow = scope["anchor"] + timedelta(days=1)
        future = context.request.get(
            child.base + API + "?" + urlencode({"shop_pk": scope["shop"], "anchor_date": tomorrow.isoformat()})
        )
        assert future.status == 422
        assert scope["anchor"].isoformat() in future.text()
        for name in ("layer", "severity", "state", "sample"):
            response = context.request.get(
                child.base + API + "?" + urlencode({"shop_pk": scope["shop"], name: "all"})
            )
            assert response.status == 422, name


def test_real_keyboard_drilldown_preserves_business_spu_and_dates(
    comparison_live, chromium
):
    """AC-016/017: follow the actual target, not merely inspect a plausible href."""
    from playwright.sync_api import expect

    child, scope, _user, key = comparison_live
    context = chromium.new_context(
        viewport={"width": 1440, "height": 1000},
        extra_http_headers={"Authorization": f"Bearer {key}"},
    )
    page = context.new_page()
    try:
        source = child.base + PAGE + "?" + urlencode({"shop_pk": scope["shop"]})
        with page.expect_response(_comparison_response):
            page.goto(source)
        row = page.locator("#alert-rows tr").filter(has_text="TEST_COMPARE_3")
        button = row.get_by_role("button").first
        button.focus()
        button.press("Enter")
        expect(page.locator("#alert-summary")).to_be_visible()
        link = page.locator('#alert-summary a[href*="/v2/pages/spu-roi"]').first
        expect(link).to_be_visible()
        link.focus()
        with page.expect_response(
            lambda response: urlsplit(response.url).path.endswith("/v2/analytics/spu-roi")
            and parse_qs(urlsplit(response.url).query).get("spu_ids") == ["TEST_COMPARE_3"]
        ) as details:
            link.press("Enter")
        assert details.value.status == 200
        details_query = parse_qs(urlsplit(details.value.url).query)
        assert details_query["shop_pk"] == [str(scope["shop"])]
        assert details_query["w_start"] == [
            (scope["anchor"] - timedelta(days=6)).isoformat()
        ]
        assert details_query["w_end"] == [scope["anchor"].isoformat()]
        expect(page.locator("#filter-w-start")).to_have_value(details_query["w_start"][0])
        expect(page.locator("#filter-w-end")).to_have_value(details_query["w_end"][0])
        with page.expect_response(_comparison_response):
            page.go_back()
        expect(page.locator("#filter-shop")).to_have_value(str(scope["shop"]))
        expect(page.locator("#filter-anchor-date")).to_have_value(scope["anchor"].isoformat())
    finally:
        context.close()
