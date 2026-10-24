"""PRD v3 consumer acceptance: actual requests, flat facts, recovery and keyboard.

The static fixture supplies no business evidence. Real HTTP failures are injected
with Playwright routing; backend scope/precision/deduplication need API/E2E tests.
"""
from __future__ import annotations

import copy
from collections.abc import Callable
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

import pytest
from playwright.sync_api import expect

pytestmark = [pytest.mark.domain_browser, pytest.mark.requires_browser]

PAGE = "/v2/pages/spu-profit-deterioration"
API = "/v2/analytics/spu-profit-deterioration"
ANCHOR = "2026-10-03"
CALCULATED = "2026-10-07T02:00:00+00:00"


def _item(**overrides: Any) -> dict[str, Any]:
    item = {
        "shopPk": 7, "spuPk": 1001, "spuId": "TEST-BIZ-SPU-1001",
        "windowDays": 7, "anchorDate": ANCHOR,
        "previousRoi": "2", "currentRoi": "2.5",
        "previousNetProfitCny": "100", "currentNetProfitCny": "80",
        "previousSpendCny": "50", "currentSpendCny": "40",
        "previousOrderCount": 2, "currentOrderCount": 2,
        "previousNoOrderAdSpend": False, "currentNoOrderAdSpend": False,
        "previousRoiUnavailableReason": None, "currentRoiUnavailableReason": None,
        "warnings": ["含未结算平台费估算；历史估值可能随成本和汇率更新"],
        "basisCalculatedAt": CALCULATED,
        "drilldown": {"period": "current", "pageUrl": "/v2/pages/spu-roi?shop_pk=7&spu_ids=TEST-BIZ-SPU-1001&w_start=2026-09-27&w_end=2026-10-03"},
    }
    item.update(overrides)
    return item


def _payload(query: dict[str, list[str]], items: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    window = int(query.get("window_days", ["7"])[0])
    anchor = query.get("anchor_date", [ANCHOR])[0]
    return {
        "items": copy.deepcopy(items if items is not None else [_item(windowDays=window, anchorDate=anchor)]),
        "total": 2,
        # Intentionally not row sums or pagination total.
        "totals": {"analysisSpuCount": 12, "currentOrderCount": 3 if window == 7 else 1,
                   "previousOrderCount": 4 if window == 7 else 2, "unavailableReason": None},
        "meta": {
            "requestId": "TEST-request", "shopPk": 7, "anchorDate": anchor,
            "latestAvailableDate": ANCHOR, "windowDays": window,
            "reportingTimezone": "Asia/Ho_Chi_Minh", "dataAsOf": ANCHOR,
            "calculatedAt": CALCULATED, "stale": True,
            "periods": {"current": {"startDate": "2026-09-27" if window == 7 else "2026-10-01", "endDate": anchor},
                        "previous": {"startDate": "2026-09-20" if window == 7 else "2026-09-28", "endDate": "2026-09-26" if window == 7 else "2026-09-30"}},
            "coverage": {"status": "limited", "reason": "来源覆盖有限，不证明采集完整"},
            "warnings": ["来源覆盖有限，不证明采集完整"],
            "activityBypassed": "spu_ids" in query,
            "scopeDescription": "精确 SPU 调查，活动排除不生效" if "spu_ids" in query else "固定近7天活动范围，保留无单投放",
        },
    }


class ComparisonSite:
    def __init__(self, renderer: Any) -> None:
        self.context = renderer._browser.new_context(viewport={"width": 1440, "height": 1100})
        renderer._contexts.append(self.context)
        self.page = self.context.new_page()
        self.page.set_default_timeout(3000)
        self.page.on("request", renderer._record)
        self.base = renderer._base
        self.requests: list[dict[str, list[str]]] = []
        self.violations: list[str] = []
        self.items: list[dict[str, Any]] | None = None
        self.respond: Callable[[Any, dict[str, list[str]]], None] | None = None
        self.shop_respond: Callable[[Any], None] | None = None
        self.shops = [{"id": 7, "account_name": "TEST 店铺 7"}, {"id": 8, "account_name": "TEST 店铺 8"}]
        self.context.route("**/v2/analytics/spu-profit-deterioration?*", self._data)
        self.context.route("**/v2/commerce/channel-accounts?*", self._shops)

    def _shops(self, route: Any) -> None:
        if self.shop_respond:
            self.shop_respond(route)
        else:
            route.fulfill(json=self.shops)

    def _data(self, route: Any) -> None:
        query = parse_qs(urlsplit(route.request.url).query, keep_blank_values=True)
        self.requests.append(query)
        error = ""
        if query.get("activity") not in (["recent"], ["all"]):
            error = "activity 必须为 recent/all"
        if query.get("window_days") not in (["7"], ["3"]):
            error = "window_days 必须为3/7"
        if any(key in query for key in ("layer", "sample", "severity", "state")):
            error = "不接受旧告警筛选"
        if error:
            self.violations.append(error)
            route.fulfill(status=422, json={"detail": {"message": error, "requestId": "TEST-invalid"}})
        elif self.respond:
            self.respond(route, query)
        else:
            route.fulfill(json=_payload(query, self.items))

    def open(self, query: str = "", *, prefix: str = "", width: int = 1440, hold_request: bool = False) -> Any:
        self.page.set_viewport_size({"width": width, "height": 1100})
        # A deliberately held fetch cannot reach networkidle before the race is exercised.
        self.page.goto(self.base + prefix + PAGE + query, wait_until="load" if hold_request else "networkidle")
        return self.page

    def ready(self) -> None:
        expect(self.page.locator("#alert-rows [data-spu-pk='1001']")).to_have_count(1)
        assert not self.violations


@pytest.fixture
def site(browser_renderer: Any) -> ComparisonSite:
    return ComparisonSite(browser_renderer)


def test_default_valid_activity_only_7_3_no_writes_and_shared_route(site: ComparisonSite) -> None:
    page = site.open()
    site.ready()
    assert site.requests[0]["activity"] == ["recent"]
    assert site.requests[0]["window_days"] == ["7"]
    assert "anchor_date" not in site.requests[0]  # backend chooses latest, not yesterday
    assert page.locator("[data-window-days]").evaluate_all("els => els.map(e => e.dataset.windowDays)") == ["7", "3"]
    assert page.locator("#btn-settings, #settings-drawer, #filter-severity, #filter-state, #filter-sample").count() == 0
    entry = page.locator('#sidebar a[href$="/v2/pages/spu-profit-deterioration"]')
    expect(entry).to_have_attribute("aria-current", "page")
    expect(entry).to_contain_text("利润劣化告警")
    page.locator("#filter-activity").uncheck()
    expect(page.locator("#alert-status")).not_to_have_attribute("data-kind", "loading")
    assert site.requests[-1]["activity"] == ["all"]
    assert "activity=all" in page.url
    page.reload(wait_until="networkidle")
    expect(page.locator("#filter-activity")).not_to_be_checked()
    assert site.requests[-1]["activity"] == ["all"]


def test_stale_dates_periods_warnings_and_backend_totals(site: ComparisonSite) -> None:
    page = site.open("?shop_pk=7&limit=1")
    site.ready()
    expect(page.locator("#filter-anchor-date")).to_have_attribute("max", ANCHOR)
    expect(page.locator("#filter-anchor-date")).to_have_value(ANCHOR)
    expect(page.locator("#alert-freshness")).to_contain_text("数据未更新")
    expect(page.locator("#alert-freshness")).to_contain_text(ANCHOR)
    expect(page.locator("#filter-echo")).to_contain_text("2026-09-20")
    expect(page.locator("#filter-echo")).to_contain_text("2026-09-26")
    expect(page.locator("#filter-echo")).to_contain_text("2026-09-27")
    expect(page.locator("#filter-echo")).to_contain_text("Asia/Ho_Chi_Minh")
    expect(page.locator("#alert-banner")).to_contain_text("来源覆盖有限")
    expect(page.locator("#total-spus")).to_have_text("12")
    expect(page.locator("#total-current-orders")).to_have_text("3")
    expect(page.locator("#total-previous-orders")).to_have_text("4")
    page.locator("#alert-pager-next").click()
    expect(page.locator("#alert-pager-label")).to_contain_text("2")
    assert site.requests[-1]["offset"] == ["1"]
    expect(page.locator("#total-current-orders")).to_have_text("3")
    page.locator('[data-window-days="3"]').click()
    expect(page.locator("#filter-echo")).to_contain_text("2026-09-28")
    expect(page.locator("#filter-echo")).to_contain_text("2026-10-01")
    expect(page.locator("#window-note")).to_contain_text("短期波动")
    assert site.requests[-1]["offset"] == ["0"]
    expect(page.locator("#total-current-orders")).to_have_text("1")
    expect(page.locator("#total-spus")).to_have_text("12")


@pytest.mark.parametrize("query,control,value", [
    ("?spu_ids=abc", "#filter-spu-ids", "abc"),
    ("?spu_ids=1001,abc", "#filter-spu-ids", "1001,abc"),
    ("?spu_ids=", "#filter-spu-ids", ""),
    ("?window_days=5", None, None),
    ("?window_days=3&window_days=7", None, None),
    ("?shop_pk=999", "#filter-shop", "999"),
    ("?anchor_date=not-a-date", "#filter-anchor-date", "not-a-date"),
    ("?activity=undefined", None, None),
    ("?layer=confirmation", None, None),
    ("?limit=0", None, None),
])
def test_invalid_url_keeps_raw_scope_and_blocks_query(site: ComparisonSite, query: str, control: str | None, value: str | None) -> None:
    page = site.open(query)
    expect(page.locator("#alert-status")).to_have_attribute("data-kind", "error")
    assert page.url.endswith(query)
    assert not site.requests
    if control:
        expect(page.locator(control)).to_have_value(value)
    expect(page.locator("#total-spus")).to_have_text("—")


def test_invalid_edited_spu_kept_and_exact_investigation_bypasses_activity(site: ComparisonSite) -> None:
    page = site.open()
    site.ready()
    calls = len(site.requests)
    page.locator("#filter-spu-ids").fill("1001,abc")
    page.locator("#filter-spu-ids").press("Tab")
    expect(page.locator("#alert-status")).to_have_attribute("data-kind", "error")
    expect(page.locator("#filter-spu-ids")).to_have_value("1001,abc")
    assert len(site.requests) == calls
    expect(page.locator("#alert-rows")).to_be_empty()
    page.locator("#filter-spu-ids").fill("1001")
    page.locator("#filter-spu-ids").press("Tab")
    site.ready()
    assert site.requests[-1]["spu_ids"] == ["1001"]
    expect(page.locator("#filter-echo")).to_contain_text("活动排除不生效")


def test_dates_error_preserves_raw_and_reads_backend_max_then_historical(site: ComparisonSite) -> None:
    def respond(route: Any, query: dict[str, list[str]]) -> None:
        if query.get("anchor_date", [""])[0] > ANCHOR:
            route.fulfill(status=422, json={"detail": {"code": "DATE_UNAVAILABLE", "message": "日期超过最新可用日期 2026-10-03", "latestAvailableDate": ANCHOR, "requestId": "TEST-date"}})
        else:
            route.fulfill(json=_payload(query))
    site.respond = respond
    page = site.open("?shop_pk=7&anchor_date=2026-10-07")
    expect(page.locator("#alert-status")).to_contain_text("2026-10-03")
    expect(page.locator("#alert-status")).to_contain_text("TEST-date")
    expect(page.locator("#alert-status")).not_to_contain_text("[object Object]")
    expect(page.locator("#filter-anchor-date")).to_have_value("2026-10-07")
    expect(page.locator("#filter-anchor-date")).to_have_attribute("max", ANCHOR)
    assert "anchor_date=2026-10-07" in page.url
    calls = len(site.requests)
    page.locator("#btn-refresh").click()
    expect(page.locator("#alert-status")).to_have_attribute("data-kind", "error")
    assert len(site.requests) == calls  # known max also checked locally
    page.locator("#filter-anchor-date").fill("2026-10-02")
    page.locator("#filter-anchor-date").press("Tab")
    site.ready()
    assert site.requests[-1]["anchor_date"] == ["2026-10-02"]


@pytest.mark.parametrize("previous,current,delta,pct", [
    ("2", "2.5", "+0.5", "+25%"),
    ("-0.5", "-0.2", "+0.3", "—"),
    ("0", "0", "0", "—"),
    (None, "-0.2", "—", "—"),
])
def test_original_roi_display_only_changes_and_independent_profit(site: ComparisonSite, previous: str | None, current: str, delta: str, pct: str) -> None:
    site.items = [_item(previousRoi=previous, currentRoi=current, previousRoiUnavailableReason="上期广告费为零，ROI不可计算" if previous is None else None)]
    page = site.open()
    site.ready()
    row = page.locator("#alert-rows tr")
    expect(row.locator('[data-value="previousRoi"]')).to_have_text(previous if previous is not None else "—")
    expect(row.locator('[data-value="currentRoi"]')).to_have_text(current)
    expect(row.locator('[data-display="roi-difference"]')).to_have_text(delta)
    expect(row.locator('[data-display="roi-percent"]')).to_have_text(pct)
    expect(row.locator('[data-display="profit-difference"]')).to_have_text("−20 CNY")
    expect(row.locator('[data-display="profit-percent"]')).to_have_text("−20%")
    if previous is None:
        expect(row).to_contain_text("上期广告费为零")
    expect(row).to_contain_text("未结算平台费估算")


def test_period_facts_are_backend_owned_zero_negative_and_null_distinct(site: ComparisonSite) -> None:
    site.items = [
        _item(currentOrderCount=0, currentSpendCny="100", currentNetProfitCny="-100", currentRoi="-0.2", currentNoOrderAdSpend=True),
        _item(spuPk=1002, spuId="TEST-prior-only", previousNoOrderAdSpend=True, previousOrderCount=0, currentOrderCount=None, currentNoOrderAdSpend=None),
        # No client reconstruction even if rounded display looks like zero.
        _item(spuPk=1003, spuId="TEST-no-inference", currentOrderCount=0, currentSpendCny="100", currentNoOrderAdSpend=False),
    ]
    page = site.open()
    site.ready()
    row = page.locator('#alert-rows [data-spu-pk="1001"]')
    expect(row).to_contain_text("本期：无单但有广告费")
    expect(row.locator('[data-value="currentOrderCount"]')).to_have_text("0")
    expect(row.locator('[data-value="currentNetProfitCny"]')).to_have_text("-100")
    expect(row.locator('[data-value="currentRoi"]')).to_have_text("-0.2")
    prior = page.locator('#alert-rows [data-spu-pk="1002"]')
    expect(prior).to_contain_text("上期：无单但有广告费")
    expect(prior).not_to_contain_text("本期：无单但有广告费")
    expect(prior).to_contain_text("本期订单事实缺失")
    expect(prior.locator('[data-value="currentOrderCount"]')).to_have_text("—")
    expect(page.locator('#alert-rows [data-spu-pk="1003"] .no-order-fact')).to_have_count(0)
    page.locator('[data-window-days="3"]').click()
    expect(page.locator("#alert-status")).not_to_have_attribute("data-kind", "loading")
    assert page.locator("#alert-rows tr").count() == 3


@pytest.mark.parametrize("status,label", [(401, "未登录"), (403, "无权限"), (422, "参数"), (503, "不可用")])
def test_real_http_failure_clears_all_old_results_and_retry(site: ComparisonSite, status: int, label: str) -> None:
    page = site.open()
    site.ready()
    page.locator("#alert-rows .summary-trigger").click()
    expect(page.locator("#alert-summary")).to_be_visible()
    held: list[Any] = []
    site.respond = lambda route, query: held.append(route)
    page.locator('[data-window-days="3"]').click()
    expect(page.locator("#alert-status")).to_have_attribute("data-kind", "loading")
    expect(page.locator("#alert-rows")).to_be_empty()
    expect(page.locator("#alert-cards")).to_be_empty()
    expect(page.locator("#alert-summary")).to_be_hidden()
    expect(page.locator("#total-spus")).to_have_text("—")
    expect(page.locator("#total-current-orders")).to_have_text("—")
    expect(page.locator("#filter-echo")).not_to_contain_text("2026-09-20")
    assert len(held) == 1
    held[0].fulfill(status=status, json={"detail": {"message": "TEST读取失败", "requestId": "TEST-http-failure"}})
    expect(page.locator("#alert-status")).to_contain_text(label)
    expect(page.locator("#alert-status")).to_contain_text("TEST-http-failure")
    expect(page.locator("#alert-rows")).to_be_empty()
    expect(page.locator("#total-spus")).to_have_text("—")
    site.respond = None
    page.locator("#btn-refresh").click()
    site.ready()


@pytest.mark.parametrize("late_status,b_status", [(200, 200), (503, 200), (200, 503)])
def test_latest_wins_data_response_and_error(site: ComparisonSite, late_status: int, b_status: int) -> None:
    held: list[Any] = []
    def respond(route: Any, query: dict[str, list[str]]) -> None:
        if query.get("window_days") == ["7"]:
            held.append(route)
        elif b_status == 503:
            route.fulfill(status=503, json={"detail": {"message": "TEST B失败", "requestId": "TEST-B"}})
        else:
            payload = _payload(query, [_item(spuId="TEST-B-latest", windowDays=3)])
            route.fulfill(json=payload)
    site.respond = respond
    page = site.open(hold_request=True)
    expect(page.locator("#alert-status")).to_have_attribute("data-kind", "loading")
    assert len(held) == 1
    page.locator('[data-window-days="3"]').click()
    if b_status == 200:
        expect(page.locator("#alert-rows")).to_contain_text("TEST-B-latest")
    else:
        expect(page.locator("#alert-status")).to_contain_text("TEST B失败")
    with page.expect_response(lambda response: API in response.url and "window_days=7" in response.url):
        held[0].fulfill(status=late_status, json=_payload({"window_days": ["7"]}, [_item(spuId="TEST-A-late")]) if late_status == 200 else {"detail": "TEST A失败"})
    # Wait until fetch+JSON+render microtasks finish without arbitrary sleeps.
    page.evaluate("() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))")
    expect(page.locator("#alert-rows")).not_to_contain_text("TEST-A-late")
    if b_status == 200:
        expect(page.locator("#alert-rows")).to_contain_text("TEST-B-latest")
    else:
        expect(page.locator("#alert-status")).to_contain_text("TEST B失败")
        expect(page.locator("#total-spus")).to_have_text("—")


def test_latest_wins_shop_initialization_does_not_revert_edited_scope(site: ComparisonSite) -> None:
    held: list[Any] = []
    def shops(route: Any) -> None:
        if not held:
            held.append(route)
        else:
            route.fulfill(json=site.shops)
    site.shop_respond = shops
    page = site.open("?shop_pk=7", hold_request=True)
    expect(page.locator("#alert-status")).to_have_attribute("data-kind", "loading")
    assert len(held) == 1
    page.locator("#filter-spu-ids").fill("1001")
    page.locator("#filter-spu-ids").press("Tab")
    site.ready()
    assert site.requests[-1]["spu_ids"] == ["1001"]
    with page.expect_response(lambda response: "/commerce/channel-accounts" in response.url):
        held[0].fulfill(json=[{"id": 9, "account_name": "TEST old shops"}])
    page.evaluate("() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))")
    expect(page.locator("#filter-shop")).to_have_value("7")
    expect(page.locator("#filter-spu-ids")).to_have_value("1001")
    assert site.requests[-1]["spu_ids"] == ["1001"]


@pytest.mark.parametrize("missing", [
    "total", "latestAvailableDate", "anchorDate", "windowDays", "dataAsOf",
    "currentStart", "currentEnd", "previousStart", "previousEnd", "currentOrderCount", "factFlag",
])
def test_malformed_success_is_unavailable_not_partial_results(site: ComparisonSite, missing: str) -> None:
    def respond(route: Any, query: dict[str, list[str]]) -> None:
        payload = _payload(query)
        if missing == "total":
            del payload["total"]
        elif missing in ("latestAvailableDate", "anchorDate", "windowDays", "dataAsOf"):
            del payload["meta"][missing]
        elif missing in ("currentStart", "currentEnd", "previousStart", "previousEnd"):
            period = "current" if missing.startswith("current") else "previous"
            date_key = "startDate" if missing.endswith("Start") else "endDate"
            del payload["meta"]["periods"][period][date_key]
        elif missing == "currentOrderCount":
            del payload["totals"]["currentOrderCount"]
        else:
            del payload["items"][0]["currentNoOrderAdSpend"]
        route.fulfill(json=payload)
    site.respond = respond
    page = site.open()
    expect(page.locator("#alert-status")).to_have_attribute("data-kind", "error")
    expect(page.locator("#alert-rows")).to_be_empty()
    expect(page.locator("#total-spus")).to_have_text("—")
    expect(page.locator("#filter-echo")).not_to_contain_text("undefined")
    expect(page.locator("#filter-anchor-date")).not_to_have_attribute("max", ANCHOR)
    expect(page.locator("#filter-anchor-date")).to_have_value("")
    assert "anchor_date" not in page.url  # no invented date/cap fallback


def test_shop_switch_clears_bound_and_ignores_late_old_shop_date_error(site: ComparisonSite) -> None:
    page = site.open("?shop_pk=7")
    site.ready()
    expect(page.locator("#filter-anchor-date")).to_have_attribute("max", ANCHOR)
    held: list[Any] = []
    def respond(route: Any, query: dict[str, list[str]]) -> None:
        if query["shop_pk"] == ["7"]:
            held.append(route)
        else:
            payload = _payload(query, [_item(shopPk=8)])
            payload["meta"].update(shopPk=8, latestAvailableDate="2026-10-05")
            route.fulfill(json=payload)
    site.respond = respond
    page.locator("#btn-refresh").click()
    expect(page.locator("#alert-status")).to_have_attribute("data-kind", "loading")
    assert len(held) == 1
    page.locator("#filter-shop").select_option("8")
    site.ready()
    expect(page.locator("#filter-anchor-date")).to_have_attribute("max", "2026-10-05")
    with page.expect_response(lambda response: API in response.url and "shop_pk=7" in response.url):
        held[0].fulfill(status=422, json={"detail": {"message": "TEST旧店铺日期错误", "latestAvailableDate": "2026-10-01"}})
    page.evaluate("() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))")
    expect(page.locator("#filter-anchor-date")).to_have_attribute("max", "2026-10-05")
    expect(page.locator("#alert-status")).not_to_contain_text("TEST旧店铺日期错误")
    page.locator("#filter-anchor-date").fill("2026-10-04")
    page.locator("#filter-anchor-date").press("Tab")
    site.ready()
    assert site.requests[-1]["anchor_date"] == ["2026-10-04"]


def test_empty_and_unavailable_totals_not_fake_zero(site: ComparisonSite) -> None:
    def respond(route: Any, query: dict[str, list[str]]) -> None:
        payload = _payload(query, [])
        payload["total"] = 0
        payload["totals"] = {"analysisSpuCount": None, "previousOrderCount": None, "currentOrderCount": None, "unavailableReason": "TEST统计不可用"}
        route.fulfill(json=payload)
    site.respond = respond
    page = site.open()
    expect(page.locator("#alert-status")).to_contain_text("无匹配")
    expect(page.locator("#alert-status")).to_contain_text("调整")
    expect(page.locator("#totals-note")).to_contain_text("TEST统计不可用")
    expect(page.locator("#total-spus")).to_have_text("—")
    expect(page.locator("#total-current-orders")).to_have_text("—")


@pytest.mark.parametrize("width,prefix", [(390, ""), (1440, "/tts"), (2560, "")])
def test_keyboard_detail_link_focus_return_and_responsive_fields(site: ComparisonSite, width: int, prefix: str) -> None:
    site.items = [_item(currentOrderCount=0, currentNoOrderAdSpend=True, currentRoi="-0.2")]
    page = site.open("?shop_pk=7&spu_ids=1001", prefix=prefix, width=width)
    site.ready()
    container = page.locator("#alert-cards" if width < 992 else "#alert-rows")
    expect(container).to_be_visible()
    for key in ("previousRoi", "currentRoi", "previousNetProfitCny", "currentNetProfitCny", "previousSpendCny", "currentSpendCny", "previousOrderCount", "currentOrderCount"):
        expect(container.locator(f'[data-value="{key}"]')).to_be_visible()
    expect(container).to_contain_text("本期：无单但有广告费")
    trigger = container.locator(".summary-trigger")
    trigger.focus()
    page.keyboard.press("Enter")
    summary = page.locator("#alert-summary")
    expect(summary).to_be_visible()
    expect(page.locator("#alert-summary-title")).to_be_focused()
    expect(summary).to_contain_text(CALCULATED)
    expect(summary).to_contain_text("2026-09-20")
    page.keyboard.press("Tab")
    expect(page.locator("#btn-summary-close")).to_be_focused()
    page.keyboard.press("Enter")
    expect(summary).to_be_hidden()
    expect(trigger).to_be_focused()
    page.keyboard.press("Tab")
    link = container.locator("a.profitability-link")
    expect(link).to_be_focused()
    href = link.get_attribute("href")
    assert href == prefix + _item()["drilldown"]["pageUrl"]
    assert "TEST-BIZ-SPU-1001" in href and "w_start=2026-09-27" in href
    expect(link).to_have_text("本期盈利详情（2026-09-27 至 2026-10-03）")
    assert link.evaluate("el => el.tagName") == "A"
    assert link.get_attribute("target") in (None, "_self")
    # The parent-owned cold E2E exercises the real target and browser history;
    # a fake destination here would not prove business-ID/date consumption.
    trigger.focus()
    page.keyboard.press("Enter")
    page.keyboard.press("Tab")
    page.keyboard.press("Tab")
    summary_link = summary.locator("a.profitability-link")
    expect(summary_link).to_be_focused()
    expect(summary_link).to_have_attribute("href", href)
    expect(summary_link).to_contain_text("本期盈利详情（2026-09-27 至 2026-10-03）")
    expect(summary_link).not_to_contain_text("两期完整日期范围")
    expect(summary).to_contain_text("上期 2026-09-20 至 2026-09-26")
    page.keyboard.press("Escape")
    expect(summary).to_be_hidden()
    expect(trigger).to_be_focused()
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth"), "whole page must not overflow"
    screenshots = Path(".pi/subagents/recovery/logs/screenshots")
    screenshots.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(screenshots / f"comparison-{width}.png"), full_page=True)
