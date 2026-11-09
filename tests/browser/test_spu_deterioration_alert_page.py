"""PRD v3 consumer acceptance: actual requests, flat facts, recovery and keyboard.

The static fixture supplies no business evidence. Real HTTP failures are injected
with Playwright routing; backend scope/precision/deduplication need API/E2E tests.

P6 retired the Jinja template that used to own this page
(``tts_erp_v2/templates/pages/spu-profit-deterioration.html``, removed in
``f72a3089``) and the React implementation in
``frontend/src/pages/spu-profit-deterioration/`` took over. React never rendered
``#alert-rows`` / ``#total-spus`` / ``#alert-status``; it renders a comparison
table, a full-scope statistics region, per-SPU detail regions and inline
status/error paragraphs. The business facts under test are unchanged — only the
selectors now name the DOM the React page actually ships.
"""

from __future__ import annotations

import copy
import re
import time
from collections.abc import Callable
from datetime import date, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

import pytest
from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import expect

pytestmark = [pytest.mark.domain_browser, pytest.mark.requires_browser]

PAGE = "/v2/pages/spu-profit-deterioration"
API = "/v2/analytics/spu-profit-deterioration"
ANCHOR = "2026-10-03"
CALCULATED = "2026-10-07T02:00:00+00:00"

# --- React DOM contract (frontend/src/pages/spu-profit-deterioration/) -------
TABLE = 'table[aria-label="SPU 两期盈利对比"]'
ROWS = f"{TABLE} tbody tr"
SCROLL = '[role="region"][aria-label="SPU 对比表格滚动区域"]'
SCOPE = 'section[aria-label="已应用对比范围"]'
STATS = 'section[aria-label="完整分析范围统计"]'
FILTERS = 'form[aria-label="对比范围"]'
NAV = 'nav[aria-label="主导航"]'

#: The retired template's ``[data-value]`` keys, mapped to the React metric
#: label plus the period column that carries the value.
METRIC_KEYS = {
    "previousRoi": ("ROI（比率）", "上期"),
    "currentRoi": ("ROI（比率）", "本期"),
    "previousNetProfitCny": ("净利润（CNY）", "上期"),
    "currentNetProfitCny": ("净利润（CNY）", "本期"),
    "previousSpendCny": ("广告消耗（CNY）", "上期"),
    "currentSpendCny": ("广告消耗（CNY）", "本期"),
    "previousOrderCount": ("订单数", "上期"),
    "currentOrderCount": ("订单数", "本期"),
}


def _row(page: Any, spu_pk: str) -> Any:
    """One React comparison row.

    ``Table.Row`` renders a React-Aria collection id, so the row is identified
    by the public ARIA link to the detail region it owns (``detail-<spuPk>``).
    """
    return page.locator(f'{ROWS}:has([aria-controls="detail-{spu_pk}"])')


def _metric(scope: Any, label: str) -> Any:
    """One React ``Metric`` block: <div><strong>label</strong><div>上期 …</div>…</div>."""
    return scope.locator("strong", has_text=label).first.locator("xpath=..")


def _metric_value(scope: Any, label: str, period: str) -> Any:
    """The ``上期``/``本期`` value span of one React metric block."""
    return _metric(scope, label).locator(
        f"xpath=./div[starts-with(., '{period}')]/span"
    ).first


def _metric_display(scope: Any, label: str, kind: str) -> Any:
    """The ``展示差额``/``展示变化率`` paragraph of one React metric block."""
    return _metric(scope, label).locator(f"xpath=.//p[starts-with(., '{kind}')]")


def _stat(page: Any, label: str) -> Any:
    """One value of the React ``完整分析范围统计`` definition list."""
    return page.locator(f'{STATS} dl > div').filter(has_text=label).locator("dd")


def _select(page: Any, label: str) -> Any:
    """The HeroUI select trigger of one filter."""
    return page.get_by_role("button", name=re.compile(re.escape(label)))


def _choose(page: Any, label: str, option: str) -> None:
    """Pick one option of a HeroUI select (the draft only changes on apply)."""
    _select(page, label).click()
    page.get_by_role("option", name=option, exact=True).click()


def _apply(page: Any) -> None:
    """Apply the draft filters. The page only queries after this click."""
    page.get_by_role("button", name="应用筛选").click()


def _apply_and_read(page: Any) -> None:
    """Apply the draft filters and wait for the comparison read they trigger."""
    with page.expect_response(lambda response: API in response.url):
        page.get_by_role("button", name="应用筛选").click()
    # The pending read is over once the page leaves its loading state.
    expect(page.get_by_text("正在加载当前范围…")).to_have_count(0)


def _wait_until(page: Any, predicate: Callable[[], bool], timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        page.wait_for_timeout(50)
    assert predicate()


def _deliver_late(route: Any, **fulfill: Any) -> None:
    """Deliver a superseded read's answer after the page already moved on.

    The React owner aborts the in-flight request when a newer scope wins
    (``index.tsx``: ``controller.abort()`` in the effect cleanup), so the
    late answer can no longer reach the view. Playwright may report that
    abort either as a route that is already handled or as a request that
    failed; both mean "the late answer was dropped", which is exactly the
    behaviour under test. Any other error is a real failure and must
    propagate instead of being swallowed.
    """
    try:
        route.fulfill(**fulfill)
    except PlaywrightError as error:
        message = str(error).lower()
        assert any(
            marker in message
            for marker in ("already handled", "request failed", "aborted", "closed")
        ), f"迟到的响应投递失败，且不是请求已被丢弃: {error}"


def _periods(anchor: str, window: int) -> tuple[dict[str, str], dict[str, str]]:
    """Server-owned comparison windows: 7 days and 3 days ending at the anchor."""
    try:
        end = date.fromisoformat(anchor)
    except ValueError:
        end = date.fromisoformat(ANCHOR)
    if window == 7:
        current = (end - timedelta(days=6), end)
        previous = (end - timedelta(days=13), end - timedelta(days=7))
    else:
        current = (end - timedelta(days=2), end)
        previous = (end - timedelta(days=5), end - timedelta(days=3))
    return (
        {"startDate": current[0].isoformat(), "endDate": current[1].isoformat()},
        {"startDate": previous[0].isoformat(), "endDate": previous[1].isoformat()},
    )


def _item(**overrides: Any) -> dict[str, Any]:
    window = int(overrides.get("windowDays", 7))
    anchor = str(overrides.get("anchorDate", ANCHOR))
    shop = overrides.get("shopPk", 7)
    spu_id = overrides.get("spuId", "TEST-BIZ-SPU-1001")
    current, _previous = _periods(anchor, window)
    item = {
        "shopPk": shop,
        "spuPk": 1001,
        "spuId": spu_id,
        "windowDays": window,
        "anchorDate": anchor,
        "previousRoi": "2",
        "currentRoi": "2.5",
        "previousNetProfitCny": "100",
        "currentNetProfitCny": "80",
        "previousSpendCny": "50",
        "currentSpendCny": "40",
        "previousOrderCount": 2,
        "currentOrderCount": 2,
        "previousNoOrderAdSpend": False,
        "currentNoOrderAdSpend": False,
        "previousRoiUnavailableReason": None,
        "currentRoiUnavailableReason": None,
        "warnings": ["含未结算平台费估算；历史估值可能随成本和汇率更新"],
        "basisCalculatedAt": CALCULATED,
        "drilldown": {
            "period": "current",
            "startDate": current["startDate"],
            "endDate": current["endDate"],
            "pageUrl": (
                f"/v2/pages/spu-roi?shop_pk={shop}&spu_ids={spu_id}"
                f"&w_start={current['startDate']}&w_end={current['endDate']}"
            ),
        },
    }
    item.update(overrides)
    return item


def _payload(
    query: dict[str, list[str]], items: list[dict[str, Any]] | None = None
) -> dict[str, Any]:
    window = int(query.get("window_days", ["7"])[0])
    anchor = query.get("anchor_date", [ANCHOR])[0]
    current, previous = _periods(anchor, window)
    return {
        "items": copy.deepcopy(
            items
            if items is not None
            else [_item(windowDays=window, anchorDate=anchor)]
        ),
        "total": 2,
        # Intentionally not row sums or pagination total.
        "totals": {
            "analysisSpuCount": 12,
            "currentOrderCount": 3 if window == 7 else 1,
            "previousOrderCount": 4 if window == 7 else 2,
            "unavailableReason": None,
        },
        "meta": {
            "requestId": "TEST-request",
            "shopPk": 7,
            "anchorDate": anchor,
            "latestAvailableDate": ANCHOR,
            "windowDays": window,
            "reportingTimezone": "Asia/Ho_Chi_Minh",
            "dataAsOf": ANCHOR,
            "calculatedAt": CALCULATED,
            "stale": True,
            "periods": {"current": current, "previous": previous},
            "coverage": {
                "status": "limited",
                "basis": "TEST_observed_sources_and_inferred_operational_cutoff",
                "sources": {
                    "tiktok.orders": {
                        "status": "observed",
                        "runId": 41,
                        "startedAt": "2026-10-02T02:00:00+00:00",
                        "finishedAt": "2026-10-02T03:00:00+00:00",
                        "operationalCutoff": "2026-10-01",
                        "reportedFailures": 0,
                    }
                },
                "advertising": {
                    "endpoint": "/TEST/ad",
                    "startDate": previous["startDate"],
                    "endDate": anchor,
                    "missingDates": [],
                    "observedScopes": [
                        {
                            "advertiserId": "TEST_ADV",
                            "campaignId": "TEST_CAMPAIGN",
                            "days": [anchor],
                        }
                    ],
                },
                "limitations": ["TEST 覆盖限制：来源覆盖有限，不证明采集完整"],
            },
            "warnings": ["来源覆盖有限，不证明采集完整"],
            "activityBypassed": "spu_ids" in query,
            "scopeDescription": "精确 SPU 调查，活动排除不生效"
            if "spu_ids" in query
            else "固定近7天活动范围，保留无单投放",
        },
    }


class ComparisonSite:
    def __init__(self, renderer: Any) -> None:
        self.context = renderer._browser.new_context(
            viewport={"width": 1440, "height": 1100}
        )
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
        self.shops = [
            {"id": 7, "account_name": "TEST 店铺 7", "shop_id": "749486486860415"},
            {"id": 8, "account_name": "TEST 店铺 8", "shop_id": "749486486860416"},
        ]
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
            route.fulfill(
                status=422,
                json={"detail": {"message": error, "requestId": "TEST-invalid"}},
            )
        elif self.respond:
            self.respond(route, query)
        else:
            route.fulfill(json=_payload(query, self.items))

    def open(
        self,
        query: str = "",
        *,
        prefix: str = "",
        width: int = 1440,
        hold_request: bool = False,
    ) -> Any:
        self.page.set_viewport_size({"width": width, "height": 1100})
        # A deliberately held fetch cannot reach networkidle before the race is exercised.
        self.page.goto(
            self.base + prefix + PAGE + query,
            wait_until="load" if hold_request else "networkidle",
        )
        return self.page

    def ready(self) -> None:
        expect(_row(self.page, "1001")).to_have_count(1)
        assert not self.violations


@pytest.fixture
def site(browser_renderer: Any) -> ComparisonSite:
    return ComparisonSite(browser_renderer)


def test_default_valid_activity_only_7_3_no_writes_and_shared_route(
    site: ComparisonSite,
) -> None:
    page = site.open()
    site.ready()
    assert site.requests[0]["activity"] == ["recent"]
    assert site.requests[0]["window_days"] == ["7"]
    assert (
        "anchor_date" not in site.requests[0]
    )  # backend chooses latest, not yesterday
    # The comparison window offers exactly the 7/3 day pair and nothing else.
    window = _select(page, "比较窗口")
    expect(window).to_contain_text("7 天主参考")
    window.click()
    expect(page.get_by_role("option", name="7 天主参考", exact=True)).to_be_visible()
    expect(page.get_by_role("option", name="3 天近期变化", exact=True)).to_be_visible()
    page.keyboard.press("Escape")
    # The retired threshold-alert filters stay gone in the React page.
    assert (
        page.locator(
            "#btn-settings, #settings-drawer, #filter-severity, #filter-state, #filter-sample"
        ).count()
        == 0
    )
    assert (
        page.locator(FILTERS)
        .get_by_role("button", name=re.compile(r"设置|严重度|严重|状态|样本|阈值"))
        .count()
        == 0
    )
    entry = page.locator(f'{NAV} a[href$="/v2/pages/spu-profit-deterioration"]')
    expect(entry).to_have_attribute("aria-current", "page")
    expect(entry).to_contain_text("利润劣化告警")
    _choose(page, "活动范围", "全部本店 SPU 调查")
    _apply_and_read(page)
    site.ready()
    expect(page.get_by_text("正在加载当前范围…")).to_have_count(0)
    assert site.requests[-1]["activity"] == ["all"]
    assert "activity=all" in page.url
    page.reload(wait_until="networkidle")
    expect(_select(page, "活动范围")).to_contain_text("全部本店 SPU 调查")
    assert site.requests[-1]["activity"] == ["all"]


def test_stale_dates_periods_warnings_and_backend_totals(site: ComparisonSite) -> None:
    page = site.open("?shop_pk=7&limit=1")
    site.ready()
    anchor = page.get_by_label("截至日（店铺当地日）", exact=True)
    expect(anchor).to_have_attribute("max", ANCHOR)
    expect(anchor).to_have_value(ANCHOR)
    freshness = page.locator(f'{SCOPE} p[role="status"]')
    expect(freshness).to_contain_text("数据未更新")
    expect(freshness).to_contain_text(ANCHOR)
    periods = page.locator(SCOPE).locator("p", has_text="上期")
    expect(periods).to_contain_text("2026-09-20")
    expect(periods).to_contain_text("2026-09-26")
    expect(periods).to_contain_text("2026-09-27")
    expect(periods).to_contain_text("Asia/Ho_Chi_Minh")
    expect(page.locator(SCOPE)).to_contain_text("来源覆盖有限")
    expect(_stat(page, "分析 SPU 数")).to_have_text("12")
    expect(_stat(page, "本期订单数")).to_have_text("3")
    expect(_stat(page, "上期订单数")).to_have_text("4")
    page.get_by_role("button", name="下一页").click()
    expect(page.get_by_text(re.compile("第 2 页"))).to_be_visible()
    assert site.requests[-1]["offset"] == ["1"]
    expect(_stat(page, "本期订单数")).to_have_text("3")
    _choose(page, "比较窗口", "3 天近期变化")
    _apply_and_read(page)
    site.ready()
    periods = page.locator(SCOPE).locator("p", has_text="上期")
    expect(periods).to_contain_text("2026-09-28")
    expect(periods).to_contain_text("2026-10-01")
    expect(page.locator(SCOPE).locator("p", has_text="短期波动")).to_be_visible()
    assert site.requests[-1]["offset"] == ["0"]
    expect(_stat(page, "本期订单数")).to_have_text("1")
    expect(_stat(page, "分析 SPU 数")).to_have_text("12")


@pytest.mark.parametrize(
    "query,control,value",
    [
        ("?spu_ids=abc", "spu", "abc"),
        ("?spu_ids=1001,abc", "spu", "1001,abc"),
        ("?spu_ids=", "spu", ""),
        ("?window_days=5", None, None),
        ("?window_days=3&window_days=7", None, None),
        ("?shop_pk=999", "shop", "999"),
        ("?anchor_date=not-a-date", "anchor", "not-a-date"),
        ("?activity=undefined", None, None),
        ("?layer=confirmation", None, None),
        ("?limit=0", None, None),
    ],
)
def test_invalid_url_keeps_raw_scope_and_blocks_query(
    site: ComparisonSite, query: str, control: str | None, value: str | None
) -> None:
    page = site.open(query)
    expect(page.get_by_role("alert")).to_have_count(1)
    assert page.url.endswith(query)
    assert not site.requests
    if control == "spu":
        expect(page.get_by_label("精确 SPU 内部编号", exact=True)).to_have_value(value)
    elif control == "anchor":
        expect(page.get_by_label("截至日（店铺当地日）", exact=True)).to_have_value(value)
    elif control == "shop":
        expect(_select(page, "店铺")).to_contain_text("999")
    # An unusable scope publishes no totals at all — never a zero or a stale count.
    expect(page.locator(STATS)).to_have_count(0)


@pytest.mark.parametrize("unknown", ["spu_id=1001", "token=TEST-sensitive-value"])
def test_unknown_query_preserves_raw_and_blocks_scope_and_credential_forwarding(
    site: ComparisonSite, unknown: str
) -> None:
    forwarded: list[str] = []
    site.page.on(
        "request",
        lambda request: (
            forwarded.append(request.url)
            if "/v2/analytics/" in request.url or "/v2/commerce/" in request.url
            else None
        ),
    )
    query = f"?shop_pk=7&{unknown}"
    page = site.open(query)
    alert = page.get_by_role("alert")
    expect(alert).to_have_count(1)
    expect(alert).to_contain_text("不支持的查询参数")
    expect(alert).not_to_contain_text("TEST-sensitive-value")
    assert page.url.endswith(query)
    assert not site.requests and not forwarded
    page.get_by_role("button", name="刷新 / 重试").click()
    expect(alert).to_have_count(1)
    assert page.url.endswith(query)
    assert not site.requests and not forwarded
    expect(page.locator(STATS)).to_have_count(0)


def test_invalid_edited_spu_kept_and_exact_investigation_bypasses_activity(
    site: ComparisonSite,
) -> None:
    page = site.open()
    site.ready()
    calls = len(site.requests)
    spu = page.get_by_label("精确 SPU 内部编号", exact=True)
    spu.fill("1001,abc")
    spu.press("Tab")
    expect(spu).to_have_value("1001,abc")
    assert len(site.requests) == calls
    expect(page.get_by_text("当前输入尚未应用")).to_be_visible()
    _apply(page)
    expect(page.get_by_role("alert")).to_contain_text("SPU 输入无效")
    assert len(site.requests) == calls
    # The rejected draft never becomes the applied scope; the page keeps
    # showing the applied one and labels it as such.
    expect(page.get_by_text("当前输入尚未应用")).to_be_visible()
    expect(page.locator(SCOPE).locator("p", has_text="活动排除不生效")).to_have_count(0)
    assert "1001%2Cabc" not in page.url
    spu.fill("1001")
    spu.press("Tab")
    _apply_and_read(page)
    site.ready()
    assert site.requests[-1]["spu_ids"] == ["1001"]
    expect(page.locator(SCOPE).locator("p", has_text="活动排除不生效")).to_be_visible()


def test_dates_error_preserves_raw_and_reads_backend_max_then_historical(
    site: ComparisonSite,
) -> None:
    def respond(route: Any, query: dict[str, list[str]]) -> None:
        if query.get("anchor_date", [""])[0] > ANCHOR:
            route.fulfill(
                status=422,
                json={
                    "detail": {
                        "code": "DATE_UNAVAILABLE",
                        "message": "日期超过最新可用日期 2026-10-03",
                        "latestAvailableDate": ANCHOR,
                        "requestId": "TEST-date",
                    }
                },
            )
        else:
            route.fulfill(json=_payload(query))

    site.respond = respond
    page = site.open("?shop_pk=7&anchor_date=2026-10-07")
    alert = page.get_by_role("alert")
    expect(alert).to_contain_text("HTTP 422")
    expect(alert).to_contain_text("TEST-date")
    expect(alert).not_to_contain_text("[object Object]")
    expect(alert).not_to_contain_text("日期超过最新可用日期 2026-10-03")
    anchor = page.get_by_label("截至日（店铺当地日）", exact=True)
    expect(anchor).to_have_value("2026-10-07")
    expect(anchor).to_have_attribute("max", ANCHOR)
    expect(page.get_by_text("最新可用日期：2026-10-03")).to_be_visible()
    assert "anchor_date=2026-10-07" in page.url
    with page.expect_response(lambda response: API in response.url):
        page.get_by_role("button", name="刷新 / 重试").click()
    expect(alert).to_contain_text("HTTP 422")
    expect(anchor).to_have_value("2026-10-07")
    expect(anchor).to_have_attribute("max", ANCHOR)
    # The learned maximum is enforced locally: no further query is sent.
    calls = len(site.requests)
    _apply(page)
    validation = page.get_by_role("alert").filter(has_text="日期超过最新可用日期")
    expect(validation).to_contain_text("日期超过最新可用日期 2026-10-03")
    assert len(site.requests) == calls
    anchor.fill("2026-10-02")
    anchor.press("Tab")
    _apply_and_read(page)
    site.ready()
    assert site.requests[-1]["anchor_date"] == ["2026-10-02"]


@pytest.mark.parametrize(
    "previous,current,delta,pct",
    [
        ("2", "2.5", "+0.5", "+25%"),
        ("-0.5", "-0.2", "+0.3", "—"),
        ("0", "0", "0", "—"),
        (None, "-0.2", "—", "—"),
    ],
)
def test_original_roi_display_only_changes_and_independent_profit(
    site: ComparisonSite, previous: str | None, current: str, delta: str, pct: str
) -> None:
    site.items = [
        _item(
            previousRoi=previous,
            currentRoi=current,
            previousRoiUnavailableReason="上期广告费为零，ROI不可计算"
            if previous is None
            else None,
        )
    ]
    page = site.open()
    site.ready()
    row = _row(page, "1001")
    expect(_metric_value(row, "ROI（比率）", "上期")).to_have_text(
        previous if previous is not None else "—"
    )
    expect(_metric_value(row, "ROI（比率）", "本期")).to_have_text(current)
    expect(_metric_display(row, "ROI（比率）", "展示差额")).to_have_text(
        f"展示差额 {delta}"
    )
    expect(_metric_display(row, "ROI（比率）", "展示变化率")).to_have_text(
        f"展示变化率 {pct}"
    )
    expect(_metric_display(row, "净利润（CNY）", "展示差额")).to_have_text(
        "展示差额 −20 CNY"
    )
    expect(_metric_display(row, "净利润（CNY）", "展示变化率")).to_have_text(
        "展示变化率 −20%"
    )
    if previous is None:
        expect(_metric(row, "ROI（比率）")).to_contain_text("上期广告费为零")
    expect(row).to_contain_text("未结算平台费估算")


@pytest.mark.parametrize(
    "previous,current",
    [
        ("9007199254740992.01", "9007199254740992.02"),
        ("90071992547409.91", "90071992547409.92"),
        ("-9007199254740992.02", "-9007199254740992.01"),
    ],
)
def test_unsafe_display_precision_keeps_raw_values_without_false_flat_change(
    site: ComparisonSite, previous: str, current: str
) -> None:
    site.items = [
        _item(
            previousRoi=previous,
            currentRoi=current,
            previousNetProfitCny=previous,
            currentNetProfitCny=current,
        )
    ]
    page = site.open()
    site.ready()
    row = _row(page, "1001")
    for label in ("ROI（比率）", "净利润（CNY）"):
        expect(_metric_value(row, label, "上期")).to_have_text(previous)
        expect(_metric_value(row, label, "本期")).to_have_text(current)
        expect(_metric_display(row, label, "展示差额")).to_have_text("展示差额 —")
        expect(_metric_display(row, label, "展示变化率")).to_have_text("展示变化率 —")
    expect(row).to_contain_text("展示精度不足")
    expect(row).not_to_contain_text("持平")
    expect(_stat(page, "分析 SPU 数")).to_have_text("12")


def test_period_facts_are_backend_owned_zero_negative_and_null_distinct(
    site: ComparisonSite,
) -> None:
    site.items = [
        _item(
            currentOrderCount=0,
            currentSpendCny="100",
            currentNetProfitCny="-100",
            currentRoi="-0.2",
            currentNoOrderAdSpend=True,
        ),
        _item(
            spuPk=1002,
            spuId="TEST-prior-only",
            previousNoOrderAdSpend=True,
            previousOrderCount=0,
            currentOrderCount=None,
            currentNoOrderAdSpend=None,
        ),
        # No client reconstruction even if rounded display looks like zero.
        _item(
            spuPk=1003,
            spuId="TEST-no-inference",
            currentOrderCount=0,
            currentSpendCny="100",
            currentNoOrderAdSpend=False,
        ),
    ]
    page = site.open()
    site.ready()
    row = _row(page, "1001")
    expect(row).to_contain_text("本期：无单但有广告费")
    expect(_metric_value(row, "订单数", "本期")).to_have_text("0")
    expect(_metric_value(row, "净利润（CNY）", "本期")).to_have_text("-100")
    expect(_metric_value(row, "ROI（比率）", "本期")).to_have_text("-0.2")
    prior = _row(page, "1002")
    expect(prior).to_contain_text("上期：无单但有广告费")
    expect(prior).not_to_contain_text("本期：无单但有广告费")
    expect(prior).to_contain_text("本期订单数事实缺失")
    expect(_metric_value(prior, "订单数", "本期")).to_have_text("—")
    # The client never infers the no-order/ad-spend fact from a rounded display.
    expect(_row(page, "1003")).not_to_contain_text("无单但有广告费")
    _choose(page, "比较窗口", "3 天近期变化")
    _apply_and_read(page)
    site.ready()
    expect(page.get_by_text("正在加载当前范围…")).to_have_count(0)
    assert page.locator(ROWS).count() == 3


@pytest.mark.parametrize(
    "status,label",
    [(401, "会话已失效"), (403, "无权限"), (422, "参数"), (503, "不可用")],
)
def test_real_http_failure_clears_all_old_results_and_retry(
    site: ComparisonSite, status: int, label: str
) -> None:
    page = site.open()
    site.ready()
    trigger = page.get_by_role("button", name="查看对比明细 TEST-BIZ-SPU-1001")
    trigger.click()
    detail = page.locator("#detail-1001")
    expect(detail).to_be_visible()
    held: list[Any] = []
    site.respond = lambda route, query: held.append(route)
    _choose(page, "比较窗口", "3 天近期变化")
    _apply(page)
    expect(page.get_by_text("正在加载当前范围…")).to_be_visible()
    expect(page.locator(ROWS)).to_have_count(0)
    expect(detail).to_have_count(0)
    expect(page.locator(STATS)).to_have_count(0)
    expect(page.locator(SCOPE)).to_have_count(0)
    _wait_until(page, lambda: len(held) == 1)
    assert len(held) == 1
    held[0].fulfill(
        status=status,
        json={"detail": {"message": "TEST读取失败", "requestId": "TEST-http-failure"}},
    )
    if status == 401:
        # An expired session belongs to the session owner: the shell hands the
        # operator to the login page instead of keeping stale results around.
        expect(page).to_have_url(re.compile(r"/v2/auth/login\?next="))
        return
    alert = page.get_by_role("alert")
    expect(alert).to_contain_text(label)
    expect(alert).to_contain_text(f"HTTP {status}")
    if status in (422, 503):
        expect(alert).to_contain_text("TEST-http-failure")
    expect(page.locator(ROWS)).to_have_count(0)
    expect(page.locator(STATS)).to_have_count(0)
    site.respond = None
    page.get_by_role("button", name="刷新 / 重试").click()
    site.ready()


@pytest.mark.parametrize("late_status,b_status", [(200, 200), (503, 200), (200, 503)])
def test_latest_wins_data_response_and_error(
    site: ComparisonSite, late_status: int, b_status: int
) -> None:
    held: list[Any] = []

    def respond(route: Any, query: dict[str, list[str]]) -> None:
        if query.get("window_days") == ["7"]:
            held.append(route)
        elif b_status == 503:
            route.fulfill(
                status=503,
                json={"detail": {"message": "TEST B失败", "requestId": "TEST-B"}},
            )
        else:
            payload = _payload(query, [_item(spuId="TEST-B-latest", windowDays=3)])
            route.fulfill(json=payload)

    site.respond = respond
    page = site.open(hold_request=True)
    expect(page.get_by_text("正在加载当前范围…")).to_be_visible()
    _wait_until(page, lambda: len(held) == 1)
    assert len(held) == 1
    _choose(page, "比较窗口", "3 天近期变化")
    _apply(page)
    if b_status == 200:
        expect(page.locator(ROWS)).to_contain_text("TEST-B-latest")
    else:
        expect(page.get_by_role("alert")).to_contain_text("数据不可用")
        expect(page.get_by_role("alert")).to_contain_text("TEST-B")
    # React aborts the superseded read, so the late answer can no longer land;
    # delivering it anyway must not touch the scope that won.
    _deliver_late(
        held[0],
        status=late_status,
        json=_payload({"window_days": ["7"]}, [_item(spuId="TEST-A-late")])
        if late_status == 200
        else {"detail": "TEST A失败"},
    )
    # Wait until fetch+JSON+render microtasks finish without arbitrary sleeps.
    page.evaluate(
        "() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))"
    )
    # The late answer must not appear anywhere on the page. When the winning
    # scope failed there is no table at all, so assert on the document text
    # rather than on the (absent) row locator.
    expect(page.locator("body")).not_to_contain_text("TEST-A-late")
    if b_status == 200:
        expect(page.locator(ROWS)).to_contain_text("TEST-B-latest")
    else:
        expect(page.get_by_role("alert")).to_contain_text("数据不可用")
        expect(page.locator(STATS)).to_have_count(0)


def test_latest_wins_shop_initialization_does_not_revert_edited_scope(
    site: ComparisonSite,
) -> None:
    held: list[Any] = []

    def shops(route: Any) -> None:
        if not held:
            held.append(route)
        else:
            route.fulfill(json=site.shops)

    site.shop_respond = shops
    page = site.open("?shop_pk=7", hold_request=True)
    expect(page.get_by_text("正在加载当前范围…")).to_be_visible()
    _wait_until(page, lambda: len(held) == 1)
    assert len(held) == 1
    spu = page.get_by_label("精确 SPU 内部编号", exact=True)
    spu.fill("1001")
    spu.press("Tab")
    _apply_and_read(page)
    site.ready()
    assert site.requests[-1]["spu_ids"] == ["1001"]
    # React aborts the superseded shop read; the late shop list cannot land.
    _deliver_late(
        held[0],
        json=[{"id": 9, "account_name": "TEST old shops", "shop_id": "749486486860417"}],
    )
    page.evaluate(
        "() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))"
    )
    expect(_select(page, "店铺")).to_contain_text("TEST 店铺 7")
    expect(_select(page, "店铺")).not_to_contain_text("TEST old shops")
    expect(spu).to_have_value("1001")
    assert site.requests[-1]["spu_ids"] == ["1001"]


@pytest.mark.parametrize(
    "missing",
    [
        "total",
        "latestAvailableDate",
        "anchorDate",
        "windowDays",
        "dataAsOf",
        "currentStart",
        "currentEnd",
        "previousStart",
        "previousEnd",
        "currentOrderCount",
        "factFlag",
    ],
)
def test_malformed_success_is_unavailable_not_partial_results(
    site: ComparisonSite, missing: str
) -> None:
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
    expect(page.get_by_role("alert")).to_contain_text("数据格式不符合约定")
    expect(page.locator(ROWS)).to_have_count(0)
    expect(page.locator(STATS)).to_have_count(0)
    expect(page.locator(SCOPE)).to_have_count(0)
    expect(page.get_by_role("main")).not_to_contain_text("undefined")
    anchor = page.get_by_label("截至日（店铺当地日）", exact=True)
    expect(anchor).not_to_have_attribute("max", ANCHOR)
    expect(anchor).to_have_value("")
    assert "anchor_date" not in page.url  # no invented date/cap fallback


def test_shop_switch_clears_bound_and_ignores_late_old_shop_date_error(
    site: ComparisonSite,
) -> None:
    page = site.open("?shop_pk=7")
    site.ready()
    anchor = page.get_by_label("截至日（店铺当地日）", exact=True)
    expect(anchor).to_have_attribute("max", ANCHOR)
    held: list[Any] = []

    def respond(route: Any, query: dict[str, list[str]]) -> None:
        if query["shop_pk"] == ["7"]:
            held.append(route)
        else:
            payload = _payload(query, [_item(shopPk=8)])
            payload["meta"].update(shopPk=8, latestAvailableDate="2026-10-05")
            route.fulfill(json=payload)

    site.respond = respond
    page.get_by_role("button", name="刷新 / 重试").click()
    expect(page.get_by_text("正在加载当前范围…")).to_be_visible()
    _wait_until(page, lambda: len(held) == 1)
    assert len(held) == 1
    _choose(page, "店铺", "TEST 店铺 8")
    _apply_and_read(page)
    site.ready()
    expect(anchor).to_have_attribute("max", "2026-10-05")
    # The late old-shop answer belongs to an aborted read; it must not touch
    # the current shop's bound date nor surface an error.
    _deliver_late(
        held[0],
        status=422,
        json={
            "detail": {
                "message": "TEST旧店铺日期错误",
                "latestAvailableDate": "2026-10-01",
            }
        },
    )
    page.evaluate(
        "() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))"
    )
    expect(anchor).to_have_attribute("max", "2026-10-05")
    expect(page.get_by_role("alert")).to_have_count(0)
    anchor.fill("2026-10-04")
    anchor.press("Tab")
    _apply_and_read(page)
    site.ready()
    assert site.requests[-1]["anchor_date"] == ["2026-10-04"]


def test_empty_and_unavailable_totals_not_fake_zero(site: ComparisonSite) -> None:
    def respond(route: Any, query: dict[str, list[str]]) -> None:
        payload = _payload(query, [])
        payload["total"] = 0
        payload["totals"] = {
            "analysisSpuCount": None,
            "previousOrderCount": None,
            "currentOrderCount": None,
            "unavailableReason": "TEST统计不可用",
        }
        route.fulfill(json=payload)

    site.respond = respond
    page = site.open()
    empty = page.get_by_text(re.compile("当前条件无匹配 SPU"))
    expect(empty).to_be_visible()
    expect(empty).to_contain_text("调整")
    expect(page.locator(STATS)).to_contain_text("TEST统计不可用")
    expect(_stat(page, "分析 SPU 数")).to_have_text("—")
    expect(_stat(page, "本期订单数")).to_have_text("—")


@pytest.mark.parametrize("width,prefix", [(390, ""), (1440, "/tts"), (2560, "")])
def test_keyboard_detail_link_focus_return_and_responsive_fields(
    site: ComparisonSite, width: int, prefix: str
) -> None:
    site.items = [
        _item(currentOrderCount=0, currentNoOrderAdSpend=True, currentRoi="-0.2")
    ]
    page = site.open("?shop_pk=7&spu_ids=1001", prefix=prefix, width=width)
    site.ready()
    container = page.locator(TABLE)
    expect(container).to_be_visible()
    for label, period in METRIC_KEYS.values():
        expect(_metric_value(container, label, period)).to_be_visible()
    expect(container).to_contain_text("本期：无单但有广告费")
    trigger = container.get_by_role("button", name="查看对比明细 TEST-BIZ-SPU-1001")
    trigger.focus()
    page.keyboard.press("Enter")
    detail = page.locator("#detail-1001")
    expect(detail).to_be_visible()
    expect(page.locator("#detail-title-1001")).to_be_focused()
    expect(detail).to_contain_text(CALCULATED)
    expect(detail).to_contain_text("2026-09-20")
    page.keyboard.press("Tab")
    expect(page.get_by_role("button", name="关闭 SPU 明细")).to_be_focused()
    page.keyboard.press("Enter")
    expect(detail).to_have_count(0)
    expect(trigger).to_be_focused()
    page.keyboard.press("Tab")
    link = container.get_by_role("link", name="本期盈利详情（2026-09-27 至 2026-10-03）")
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
    summary_link = detail.get_by_role("link", name="本期盈利详情（2026-09-27 至 2026-10-03）")
    expect(summary_link).to_be_focused()
    expect(summary_link).to_have_attribute("href", href)
    expect(summary_link).to_contain_text("本期盈利详情（2026-09-27 至 2026-10-03）")
    expect(summary_link).not_to_contain_text("两期完整日期范围")
    expect(detail).to_contain_text("上期 2026-09-20 至 2026-09-26")
    page.keyboard.press("Escape")
    expect(detail).to_have_count(0)
    expect(trigger).to_be_focused()
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth"), (
        "whole page must not overflow"
    )
    screenshots = Path(".pi/subagents/recovery/logs/screenshots")
    screenshots.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(screenshots / f"comparison-{width}.png"), full_page=True)


@pytest.mark.parametrize("width", [390, 1440])
def test_shared_layout_fixture_renders_comparison_data_not_only_error_shell(
    browser_renderer: Any, width: int
) -> None:
    """The shared overflow/font gate must actually exercise populated v3 UI."""
    page = browser_renderer.open(PAGE, width=width)
    expect(page.get_by_role("alert")).to_have_count(0)
    container = page.locator(TABLE)
    expect(container).to_be_visible()
    expect(container).to_contain_text("TEST_RENDER_COMPARE_")
    expect(container).to_contain_text("本期：无单但有广告费")
    expect(_stat(page, "分析 SPU 数")).to_have_text("3")
    expect(page.get_by_label("截至日（店铺当地日）", exact=True)).to_have_attribute(
        "max", ANCHOR
    )


@pytest.mark.parametrize("width", [390, 1440, 2560])
def test_long_missing_reasons_keep_columns_balanced_and_notes_expandable(
    site: ComparisonSite, width: int
) -> None:
    reason = "TEST：售后和财务存在未解决解析关联问题，相关事实不可确认。" * 6
    site.items = [_item(previousRoi=None, previousRoiUnavailableReason=reason)]

    def respond(route: Any, query: dict[str, list[str]]) -> None:
        payload = _payload(query, site.items)
        payload["meta"]["warnings"] = [f"TEST 来源限制 {index}" for index in range(20)]
        route.fulfill(json=payload)

    site.respond = respond
    page = site.open(prefix="/tts", width=width)
    site.ready()
    # The applied scope and its data notes stay above the statistics block.
    assert page.locator(SCOPE).bounding_box()["y"] < page.locator(STATS).bounding_box()["y"]
    # Every server limitation reaches the operator.
    warnings = page.locator(SCOPE).locator("p", has_text="TEST 来源限制")
    expect(warnings).to_have_count(20)
    expect(warnings.nth(0)).to_have_text("TEST 来源限制 0")
    expect(warnings.nth(19)).to_have_text("TEST 来源限制 19")
    # The expandable coverage notes stay collapsed until the operator asks.
    trigger = page.get_by_role("button", name=re.compile("数据覆盖依据与限制"))
    expect(trigger).to_be_visible()
    limitation = page.get_by_text("TEST 覆盖限制", exact=False)
    expect(limitation).to_be_hidden()
    trigger.focus()
    page.keyboard.press("Enter")
    expect(limitation).to_be_visible()
    page.keyboard.press("Enter")
    expect(limitation).to_be_hidden()

    if width >= 992:
        proportions = page.locator(SCROLL).evaluate(
            "el => [...el.querySelectorAll('thead th')].map(th => "
            "th.getBoundingClientRect().width / el.querySelector('table').getBoundingClientRect().width)"
        )
        # The retired template calibrated 7%–26%. The React table is wider per
        # column (``min-w-48``/``min-w-64`` on the cells) and its container
        # caps the table at 1614px, so at 2560px a long missing reason pushes
        # the ROI column to ~35% while every other column stays above 7%
        # (measured at 2560px: 15.0/35.2/12.4/7.9/11.9/17.6%; at 1440px:
        # 19.2/16.2/16.2/10.8/16.2/21.5%). The floor still rejects a crushed
        # column; the ceiling follows the React layout.
        assert all(0.07 <= value <= 0.36 for value in proportions), proportions
        # The first four filters share one row; their controls are aligned.
        controls = page.locator(FILTERS).evaluate(
            """el => [...el.children].filter(c => c.tagName !== 'TEMPLATE').slice(0, 4)
                 .map(c => {
                   const control = c.querySelector('button.select__trigger, input');
                   const r = control.getBoundingClientRect();
                   return [r.top, r.bottom];
                 })"""
        )
        assert max(box[0] for box in controls) - min(box[0] for box in controls) <= 1, controls
        assert max(box[1] for box in controls) - min(box[1] for box in controls) <= 1, controls
    else:
        # One comparison table serves every width; narrow screens keep it
        # reachable through the labelled scroll region.
        expect(page.locator(TABLE)).to_be_visible()
        expect(page.locator(SCROLL)).to_be_visible()
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
