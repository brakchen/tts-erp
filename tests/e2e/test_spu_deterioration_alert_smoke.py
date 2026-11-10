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

from tests.support.frontend_p2_live import live_api_server
from tts_erp_v2.db.base import Base

# Reuse the maintained SQL acceptance scene without cloning its seed logic.
pytest_plugins = ["tests.api.test_spu_deterioration_alert_api"]
pytestmark = [pytest.mark.domain_e2e, pytest.mark.requires_browser]

PAGE = "/v2/pages/spu-profit-deterioration"
API = "/v2/analytics/spu-profit-deterioration"


@pytest.fixture()
def comparison_live(
    db_url, db_engine, comparison_scope, ua_user_factory, ua_role_factory
):
    """Use the maintained child/HTTP/clone binding proof, not a guessed port."""
    role = ua_role_factory(
        "test_ua_compare_reader",
        api_tier="readonly",
        permissions=("page:spu-profit-deterioration", "page:spu-roi"),
    )
    user = ua_user_factory("test_ua_compare_e2e", roles=(role,))
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


# --- React DOM contract (frontend/src/pages/spu-profit-deterioration/) -------
# P6 retired the Jinja template that owned this page and React took over, so
# the legacy ids (``#alert-rows`` / ``#total-spus`` / ``[data-value=...]``)
# no longer exist. The business facts under test are unchanged — only the
# selectors now name the DOM the React page actually ships.
TABLE = 'table[aria-label="SPU 两期盈利对比"]'
ROWS = f"{TABLE} tbody tr"
SCOPE = 'section[aria-label="已应用对比范围"]'
STATS = 'section[aria-label="完整分析范围统计"]'


def _stat(page: Any, label: str) -> Any:
    """One value of the React ``完整分析范围统计`` definition list."""
    return page.locator(f'{STATS} dl > div').filter(has_text=label).locator("dd")


def _metric(row: Any, label: str, period: str) -> Any:
    """One React metric block's value span.

    React renders ``<div><strong>label</strong><div>上期 <span>…</span></div>
    <div>本期 <span>…</span></div></div>`` — the retired template's
    ``[data-value="currentOrderCount"]`` key maps to the label plus the
    period column that carries the value.
    """
    return (
        row.locator("strong", has_text=label).first.locator("xpath=..")
        .locator(f"xpath=./div[starts-with(., '{period}')]/span").first
    )


def _choose(page: Any, label: str, option: str) -> None:
    """Pick one option of a HeroUI select (the draft only changes on apply)."""
    page.get_by_role("button", name=re.compile(re.escape(label))).click()
    page.get_by_role("option", name=option, exact=True).click()


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
        anchor_input = page.get_by_label("截至日（店铺当地日）", exact=True)
        expect(anchor_input).to_have_value(anchor)
        expect(anchor_input).to_have_attribute("max", anchor)
        # The readiness contract's anchor must hold on the REAL page — real
        # API child, real React bundle. The canned fixture path is not
        # evidence: the old helper passed by matching fixture text.
        expect(page.get_by_role("grid", name="SPU 两期盈利对比")).to_have_count(1)
        expect(_stat(page, "分析 SPU 数")).to_have_text("4")
        expect(_stat(page, "本期订单数")).to_have_text("1")
        expect(_stat(page, "上期订单数")).to_have_text("1")
        expect(page.locator(f'{SCOPE} p[role="status"]')).to_contain_text("数据未更新")
        container = page.locator(TABLE)
        expect(container).to_contain_text("TEST_COMPARE_3")
        expect(container).to_contain_text("无单但有广告费")
        # Independent oracle, scoped to the actual ad-only record (not another
        # row that happens to contain the same amount).
        ad_only = page.locator(ROWS).filter(has_text="TEST_COMPARE_3")
        expect(_metric(ad_only, "订单数", "本期")).to_have_text("0")
        expect(_metric(ad_only, "广告消耗（CNY）", "本期")).to_have_text(
            re.compile(r"49(?:\.0+)?$")
        )
        expect(_metric(ad_only, "净利润（CNY）", "本期")).to_have_text(
            re.compile(r"[-−]49(?:\.0+)?$")
        )
        # Canonical ROI excludes ad spend from its numerator; the net profit
        # above is negative while zero revenue/return loss yields an ROI of 0.
        expect(_metric(ad_only, "ROI（比率）", "本期")).to_have_text(
            re.compile(r"0(?:\.0+)?$")
        )
        # The retired threshold-alert filters stay gone in the React page.
        assert (
            page.locator(
                "#btn-settings, #settings-drawer, #filter-severity, #filter-state, #filter-sample"
            ).count()
            == 0
        )
        with page.expect_response(lambda response: _comparison_response(response, 3)) as short:
            # The window lives in a select whose draft only reaches the server
            # when the filters are applied.
            _choose(page, "比较窗口", "3 天近期变化")
            page.get_by_role("button", name="应用筛选").click()
        assert short.value.status == 200
        assert short.value.json()["meta"]["latestAvailableDate"] == anchor
        expect(_stat(page, "分析 SPU 数")).to_have_text("4")
        expect(_stat(page, "上期订单数")).to_have_text("0")
        expect(_metric(ad_only, "净利润（CNY）", "本期")).to_have_text(
            re.compile(r"[-−]21(?:\.0+)?$")
        )
        expect(page.get_by_label("截至日（店铺当地日）", exact=True)).to_have_value(anchor)
        # Re-reading never invents a later source-data day.
        with page.expect_response(_comparison_response) as refreshed:
            page.get_by_role("button", name="刷新 / 重试").click()
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


def test_real_page_permission_and_existing_readonly_api_tier_are_distinct(
    comparison_live, chromium, ua_user_factory
):
    """Page denial stays distinct from the established global readonly API tier."""
    child, scope, _user, _key = comparison_live
    denied = ua_user_factory("test_ua_compare_denied")
    with chromium.new_context() as context:
        login = context.request.post(
            child.base + "/v2/auth/login",
            data={"username": denied.username, "password": denied.password},
        )
        assert login.status == 200
        page = context.request.get(child.base + PAGE)
        assert page.status == 403
        assert "TEST_COMPARE_3" not in page.text()
        # accounts.pages.required_page_permission intentionally leaves these
        # data APIs under api_tier; _build_context defaults to readonly. This
        # slice preserves that existing policy rather than inventing page gates.
        data = context.request.get(
            child.base + API + f"?shop_pk={scope['shop']}"
        )
        assert data.status == 200
        assert data.json()["meta"]["shopPk"] == scope["shop"]


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
        row = page.locator(ROWS).filter(has_text="TEST_COMPARE_3")
        button = row.get_by_role("button", name="查看对比明细").first
        button.focus()
        button.press("Enter")
        # The retired ``#alert-summary`` panel is the per-SPU detail region now.
        detail = page.locator('section[role="region"][aria-labelledby^="detail-title-"]')
        expect(detail).to_be_visible()
        expect(detail.get_by_role("heading")).to_contain_text("TEST_COMPARE_3")
        link = detail.locator('a[href*="/v2/pages/spu-roi"]').first
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
        # Back/forward cache may restore the validated DOM without another GET.
        page.go_back(wait_until="domcontentloaded")
        # The retired ``#filter-shop`` input became a select without a value
        # attribute; the applied scope line states the shop instead.
        scope_line = page.locator(
            f'{SCOPE} p', has_text=re.compile(rf"店铺\s*{scope['shop']}\b")
        ).first
        expect(scope_line).to_be_visible()
        expect(page.get_by_label("截至日（店铺当地日）", exact=True)).to_have_value(
            scope["anchor"].isoformat()
        )
    finally:
        context.close()
