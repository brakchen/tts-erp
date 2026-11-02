"""Browser renderer contract for nested SPU price statistics.

This is component evidence only: the renderer uses the compiled shared React page, profiles,
Tabulator vendor asset, and nested response shape, while the target API is mocked by
the browser fixture. Full-stack API/E2E evidence remains a separate lane.
"""

from __future__ import annotations

import copy
import time
from collections.abc import Callable
from typing import Any, cast
from urllib.parse import parse_qs

import pytest
from playwright.sync_api import expect

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


def _nested_payload(
    original: Callable[..., Any],
    stats: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    payload = copy.deepcopy(original("/v2/analytics/spu-roi"))
    stats = copy.deepcopy(stats or _price_stats())
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


def _open_with_supported_payload(
    frontend_browser_renderer,
    monkeypatch,
    path: str,
    stats: dict[str, dict[str, Any]] | None = None,
):
    original = cast(Callable[..., Any], browser_conftest._mock_payload)

    def mock_payload(request_path: str, query: str = ""):
        if request_path.endswith("/v2/commerce/channel-product-options"):
            return [{"spu_id": "TEST_SPU_000", "title": "测试 SPU"}]
        if request_path.endswith("/v2/analytics/spu-roi"):
            return _nested_payload(original, stats)
        return original(request_path, query)

    monkeypatch.setattr(browser_conftest, "_mock_payload", mock_payload)
    page = frontend_browser_renderer.open(path)
    shop_button = page.locator("#shop-modal-list button").first
    if shop_button.is_visible():
        shop_button.click()
    if "/focused-spus" not in path:
        page.locator("#price-summary").wait_for(state="visible")
    page.locator(".tabulator-row").first.wait_for(state="visible")
    return page


def test_price_content_is_only_shown_on_standard_roi_profile(frontend_browser_renderer, monkeypatch):
    for path in ("/v2/pages/spu-roi", "/v2/pages/focused-spus"):
        page = _open_with_supported_payload(frontend_browser_renderer, monkeypatch, path)
        if "/focused-spus" in path:
            expect(page.locator("#price-summary")).to_have_count(0)
            for metric in _METRICS:
                for kind in ("mean", "median"):
                    expect(page.locator(
                        f'.tabulator-header [tabulator-field="priceStats.{metric}.{kind}"]'
                    )).to_be_hidden()
            for label in ("采购价", "销售价", "实付价"):
                expect(page.locator(".tabulator-header").get_by_text(label, exact=True)).to_be_hidden()
            expect(page.locator("#sum-roi")).to_be_visible()
            assert page.locator(".tabulator-row").count() >= 1
            continue
        assert "采购价" in page.locator("#price-summary-purchase").inner_text()
        assert "37.0000" in page.locator("#price-summary-purchase").inner_text()
        assert "≈40.0000" in page.locator("#price-summary-purchase").inner_text()
        expect(page.get_by_label("成本", exact=True).locator("#price-summary-purchase")).to_be_visible()
        expect(page.get_by_label("销售", exact=True).locator("#price-summary")).to_be_visible()
        assert "0.0000" in page.locator(".tabulator-row").first.inner_text()
        assert page.locator('.tabulator-header [tabulator-field="priceStats.purchase.mean"]').count() == 1
        assert page.locator('.tabulator-header [tabulator-field="priceStats.originalSale.median"]').count() == 1
        assert page.locator('.tabulator-header [tabulator-field="priceStats.paid.mean"]').count() == 1
        assert "加载中…" not in page.locator("#price-summary-status").inner_text()


def test_null_and_empty_price_values_never_render_as_zero(frontend_browser_renderer, monkeypatch):
    for path, empty_value, status in (
        ("/v2/pages/spu-roi", None, "no_samples"),
        ("/v2/pages/spu-roi", "", "partial"),
    ):
        stats = _price_stats()
        for metric in _METRICS:
            stats[metric]["mean"] = empty_value
            stats[metric]["median"] = empty_value
            stats[metric]["status"] = status
        page = _open_with_supported_payload(frontend_browser_renderer, monkeypatch, path, stats)
        for metric in _METRICS:
            for kind in ("mean", "median"):
                summary = page.locator(f"#price-{metric}-{kind}")
                table = page.locator(
                    f'.tabulator-row .tabulator-cell[tabulator-field="priceStats.{metric}.{kind}"]'
                ).first
                assert summary.inner_text() == "—"
                assert table.inner_text() == "—"
        expected_status = "无样本" if status == "no_samples" else "部分覆盖"
        assert expected_status in page.locator("#price-summary").inner_text()


def test_each_price_header_sends_its_server_sort_id(frontend_browser_renderer, monkeypatch):
    page = _open_with_supported_payload(frontend_browser_renderer, monkeypatch, "/v2/pages/spu-roi")
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


def test_price_tooltip_is_keyboard_openable_and_restores_focus(frontend_browser_renderer, monkeypatch):
    page = _open_with_supported_payload(frontend_browser_renderer, monkeypatch, "/v2/pages/spu-roi")
    button = page.locator('[data-price-tip="purchase"]')
    button.focus()
    page.keyboard.press("Enter")
    page.get_by_role("dialog", name="采购价统计口径").wait_for(state="visible")
    text = page.locator("#ops-tip").inner_text()
    assert "TikTok" not in text
    assert "ROI 当前有效成本" in text
    assert "计算时间：2026-10-06T00:00:00+00:00" in text
    page.keyboard.press("Escape")
    page.get_by_role("dialog", name="采购价统计口径").wait_for(state="hidden")
    expect(button).to_be_focused()


def test_price_tooltip_survives_lazy_drill_cycles(frontend_browser_renderer, monkeypatch):
    page = _open_with_supported_payload(frontend_browser_renderer, monkeypatch, "/v2/pages/spu-roi")
    row = page.locator(".tabulator-row").first
    for _ in range(2):
        row.locator('.tabulator-cell[tabulator-field="priceStats.purchase.mean"]').click()
        page.wait_for_timeout(80)
        drill = page.locator(".op-drill-row").last
        drill.wait_for(state="visible")
        drill.locator('[data-tab="orders"]').click()
        page.wait_for_timeout(120)
        button = page.locator('[data-price-tip="purchase"]')
        button.click()
        page.get_by_role("dialog", name="采购价统计口径").wait_for(state="visible")
        page.get_by_role("button", name="关闭价格说明").click()
        page.get_by_role("dialog", name="采购价统计口径").wait_for(state="hidden")
        # Role hiding may precede Presence teardown. Wait for the real FocusScope
        # return before keyboard reopening instead of forcing focus during exit.
        expect(button).to_be_focused()
        page.keyboard.press("Enter")
        page.get_by_role("dialog", name="采购价统计口径").wait_for(state="visible")
        page.keyboard.press("Escape")
        page.get_by_role("dialog", name="采购价统计口径").wait_for(state="hidden")
        expect(button).to_be_focused()
        # Modal 的退出动画和焦点返回由 HeroUI 完成；最终仍须回到原触发器。
        # 每轮显式收起明细，防止上一轮残留的面板撑过下一轮可见性断言。
        if page.locator(".op-drill-row").count():
            page.get_by_role("button", name="收起商品详情").click()
        page.locator(".op-drill-row").wait_for(state="detached")
        page.wait_for_timeout(40)


def test_price_error_can_retry_to_supported_payload(frontend_browser_renderer, monkeypatch):
    original = cast(Callable[..., Any], browser_conftest._mock_payload)
    attempts = 0

    def mock_payload(request_path: str, query: str = ""):
        nonlocal attempts
        if request_path.endswith("/v2/commerce/channel-product-options"):
            return [{"spu_id": "TEST_SPU_000", "title": "测试 SPU"}]
        if request_path.endswith("/v2/analytics/spu-roi"):
            attempts += 1
            if attempts == 1:
                return None
            return _nested_payload(original)
        return original(request_path, query)

    monkeypatch.setattr(browser_conftest, "_mock_payload", mock_payload)
    page = frontend_browser_renderer.open("/v2/pages/spu-roi")
    shop_button = page.locator("#shop-modal-list button").first
    if shop_button.is_visible():
        shop_button.click()
    page.locator("#retry-link").wait_for(state="visible")
    assert page.locator("#price-summary").is_hidden()
    assert page.locator("#retry-link").is_visible()
    page.locator("#retry-link").click()
    page.locator("#price-summary").wait_for(state="visible")
    assert page.locator("#price-purchase-mean").inner_text() == "≈37.0000"
    assert "加载中…" not in page.locator("#price-summary-status").inner_text()
    assert attempts >= 2


def test_rapid_date_requests_keep_latest_price_payload(frontend_browser_renderer, monkeypatch):
    original = cast(Callable[..., Any], browser_conftest._mock_payload)

    def mock_payload(request_path: str, query: str = ""):
        if request_path.endswith("/v2/commerce/channel-product-options"):
            return [{"spu_id": "TEST_SPU_000", "title": "测试 SPU"}]
        if request_path.endswith("/v2/analytics/spu-roi"):
            payload = _nested_payload(original)
            start = parse_qs(query).get("w_start", [""])[0]
            if start == "2026-09-10":
                time.sleep(0.20)
                value = "10.0000"
            elif start == "2026-09-11":
                time.sleep(0.01)
                value = "11.0000"
            else:
                value = "37.0000"
            payload["totals"]["priceStats"]["purchase"]["mean"] = value
            for item in payload["items"]:
                item["priceStats"]["purchase"]["mean"] = value
            return payload
        return original(request_path, query)

    monkeypatch.setattr(browser_conftest, "_mock_payload", mock_payload)
    page = frontend_browser_renderer.open("/v2/pages/spu-roi")
    shop_button = page.locator("#shop-modal-list button").first
    if shop_button.is_visible():
        shop_button.click()
    page.locator("#price-summary").wait_for(state="visible")
    page.evaluate(
        """() => {
          const input = document.querySelector('#filter-w-start');
          for (const value of ['2026-09-10', '2026-09-11']) {
            Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set.call(input, value);
            input.dispatchEvent(new Event('change', {bubbles: true}));
          }
        }"""
    )
    page.wait_for_timeout(550)
    assert page.locator("#price-purchase-mean").inner_text() == "≈11.0000"


def test_rapid_shop_requests_keep_latest_price_payload(frontend_browser_renderer, monkeypatch):
    original = cast(Callable[..., Any], browser_conftest._mock_payload)

    def mock_payload(request_path: str, query: str = ""):
        if request_path.endswith("/v2/commerce/channel-accounts"):
            return [
                {"id": 7, "platform": "tiktok", "shop_id": "TEST_SHOP_7", "account_name": "QA 店 7", "region": "VN", "status": "active"},
                {"id": 8, "platform": "tiktok", "shop_id": "TEST_SHOP_8", "account_name": "QA 店 8", "region": "VN", "status": "active"},
            ]
        if request_path.endswith("/v2/commerce/channel-product-options"):
            return [{"spu_id": "TEST_SPU_000", "title": "测试 SPU"}]
        if request_path.endswith("/v2/analytics/spu-roi"):
            payload = _nested_payload(original)
            shop_pk = parse_qs(query).get("shop_pk", [""])[0]
            if shop_pk == "7":
                time.sleep(0.20)
                value = "7.0000"
            elif shop_pk == "8":
                time.sleep(0.01)
                value = "8.0000"
            else:
                value = "37.0000"
            payload["totals"]["priceStats"]["purchase"]["mean"] = value
            for item in payload["items"]:
                item["priceStats"]["purchase"]["mean"] = value
            return payload
        return original(request_path, query)

    monkeypatch.setattr(browser_conftest, "_mock_payload", mock_payload)
    page = frontend_browser_renderer.open("/v2/pages/spu-roi")
    buttons = page.locator("#shop-modal-list button")
    buttons.nth(0).click()
    buttons.nth(1).evaluate("button => button.click()")
    page.locator("#price-summary").wait_for(state="visible")
    page.wait_for_timeout(550)
    assert page.locator("#price-purchase-mean").inner_text() == "≈8.0000"


def test_legacy_payload_hides_price_capability_without_fake_zeroes(frontend_browser_renderer):
    page = frontend_browser_renderer.open("/v2/pages/spu-roi")
    shop_button = page.locator("#shop-modal-list button").first
    if shop_button.is_visible():
        shop_button.click()
    page.wait_for_timeout(500)
    assert page.locator("#price-summary").is_hidden()
    assert page.locator('.tabulator-header [tabulator-field="priceStats.purchase.mean"]').is_hidden()


def test_mobile_price_table_scrolls_without_document_overflow(frontend_browser_renderer, monkeypatch):
    page = _open_with_supported_payload(frontend_browser_renderer, monkeypatch, "/v2/pages/spu-roi")
    page.set_viewport_size({"width": 390, "height": 844})
    page.wait_for_timeout(100)
    report = page.evaluate(
        """() => ({
          documentOverflow: document.documentElement.scrollWidth > document.documentElement.clientWidth,
          tableOverflow: document.querySelector('.tabulator-tableholder').scrollWidth > document.querySelector('.tabulator-tableholder').clientWidth,
          productFrozen: Boolean(document.querySelector('.tabulator-cell.tabulator-frozen')),
        })"""
    )
    assert report == {"documentOverflow": False, "tableOverflow": True, "productFrozen": True}


# ── "加载中…" 闪现回归（2026-10-06 用户反馈"闪现一下就不见了"）─────────────
#
# 现场：`#price-summary` 默认 hidden。`renderPriceLoading()` 曾在每次请求开始时
# 无条件 unhide 并写 "加载中…"，响应不带 nested `priceStats` 时
# `renderPriceStats()` 立刻 `hidden = true`，于是 box 闪一下就没。
#
# 加载态只能在 capability 已知为 true 时出现。通过 fetch 采样和 DOM 时间线验证；
# React effect 发请求后异步提交 loading，不能再假定请求与 DOM 更新在同一调用栈。
_PRICE_FLASH_PROBE_JS = """(() => {
  const probe = window.__priceFlashProbe = {
    requestSnapshots: [],  // 每个 spu-roi 请求发出瞬间(响应到达前)的 DOM
    snapshots: [],         // box 每次 DOM 变更后的采样(时间线)
    responses: 0,          // 已到达的 spu-roi 响应数
  };
  const snapshot = () => {
    const box = document.getElementById('price-summary');
    const status = document.getElementById('price-summary-status');
    const cell = document.getElementById('price-purchase-mean');
    return {
      hidden: box ? box.hidden : null,
      status: status ? status.textContent : null,
      purchaseMean: cell ? cell.textContent : null,
    };
  };
  const nativeFetch = window.fetch.bind(window);
  window.fetch = function (input, init) {
    const url = typeof input === 'string' ? input : (input && input.url) || '';
    const isPriceRequest = url.indexOf('/v2/analytics/spu-roi') >= 0;
    if (isPriceRequest) probe.requestSnapshots.push(snapshot());
    const response = nativeFetch(input, init);
    if (isPriceRequest) response.then(() => { probe.responses += 1; }, () => {});
    return response;
  };
  const observe = () => {
    const box = document.getElementById('price-summary');
    if (box) probe.snapshots.push(snapshot());
  };
  new MutationObserver(observe).observe(document, {
    attributes: true, subtree: true, childList: true, characterData: true,
  });
})();
"""


def _conftest_mock_payload() -> Callable[..., Any]:
    """conftest 的 canned 载荷：本层共用但其名带下划线，集中一处取用。"""
    return cast(Callable[..., Any], vars(browser_conftest)["_mock_payload"])


def test_first_load_keeps_price_summary_hidden_without_price_stats(frontend_browser_renderer):
    """响应无 priceStats 时：响应到达前 box 就保持 hidden，全程不出现 "加载中…"。"""
    page = frontend_browser_renderer.open("/v2/pages/spu-roi?shop_pk=7")
    # add_init_script 只对之后的导航生效，重载一次让探针在文档脚本之前装好。
    page.add_init_script(_PRICE_FLASH_PROBE_JS)
    page.reload(wait_until="domcontentloaded")
    page.wait_for_function(
        "() => window.__priceFlashProbe && window.__priceFlashProbe.responses >= 1"
    )
    page.wait_for_timeout(250)  # 让响应后的渲染跑完，扩大观察窗口

    probe = page.evaluate("() => window.__priceFlashProbe")
    assert probe["requestSnapshots"], "页面没发 spu-roi 请求，探针没覆盖到加载态"
    inflight = probe["requestSnapshots"][0]
    assert inflight["hidden"] is True, inflight
    assert probe["snapshots"], "探针没采到任何变更，断言窗口没覆盖到加载态"
    assert all(snap["hidden"] is True for snap in probe["snapshots"]), probe["snapshots"]
    assert page.locator("#price-summary").is_hidden()


def test_supported_price_payload_shows_loading_then_values_on_next_load(
    frontend_browser_renderer, monkeypatch
):
    """已知支持后再次加载（切窗口）：先短暂显示 "加载中…"，响应后出现数值。"""
    original = _conftest_mock_payload()

    def mock_payload(request_path: str, query: str = ""):
        if request_path.endswith("/v2/commerce/channel-product-options"):
            return [{"spu_id": "TEST_SPU_000", "title": "测试 SPU"}]
        if request_path.endswith("/v2/analytics/spu-roi"):
            payload = _nested_payload(original)
            start = parse_qs(query).get("w_start", [""])[0]
            value = "11.0000" if start == "2026-09-11" else "37.0000"
            payload["totals"]["priceStats"]["purchase"]["mean"] = value
            for item in payload["items"]:
                item["priceStats"]["purchase"]["mean"] = value
            return payload
        return original(request_path, query)

    monkeypatch.setattr(browser_conftest, "_mock_payload", mock_payload)
    page = frontend_browser_renderer.open("/v2/pages/spu-roi?shop_pk=7")
    page.locator("#price-summary").wait_for(state="visible")
    assert page.locator("#price-purchase-mean").inner_text() == "≈37.0000"

    page.add_init_script(_PRICE_FLASH_PROBE_JS)
    page.reload(wait_until="domcontentloaded")
    page.wait_for_function(
        "() => window.__priceFlashProbe && window.__priceFlashProbe.responses >= 1"
    )
    page.locator("#price-summary").wait_for(state="visible")

    page.evaluate(
        """() => {
          const input = document.querySelector('#filter-w-start');
          Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set.call(input, '2026-09-11');
          input.dispatchEvent(new Event('change', {bubbles: true}));
        }"""
    )
    page.wait_for_function(
        "() => document.getElementById('price-purchase-mean').textContent === '≈11.0000'"
    )

    probe = page.evaluate("() => window.__priceFlashProbe")
    assert len(probe["requestSnapshots"]) >= 2, probe["requestSnapshots"]
    # React commits loading after the effect starts fetch; observe the DOM timeline,
    # not the old imperative kernel's same-stack fetch snapshot.
    assert any(not snap["hidden"] and snap["status"] == "加载中…" for snap in probe["snapshots"])
    timeline = probe["snapshots"]
    loading_at = max(i for i, s in enumerate(timeline) if s["status"] == "加载中…")
    value_at = max(i for i, s in enumerate(timeline) if s["purchaseMean"] == "≈11.0000")
    assert loading_at < value_at, timeline
    assert page.locator("#price-purchase-mean").inner_text() == "≈11.0000"


# ── 已知 capability=false 后的一次 5xx 不得让价格 box 冒出来 ───────────────────
#
# 现场：legacy 部署(响应不含 nested `priceStats`)下 `state.priceCapability` 落为
# false，`#price-summary` 已隐藏。此后一次加载失败会走 `renderError()` →
# `renderPriceMessage()`，那里无条件 `box.hidden = false`，box 以"加载失败 · …"
# 冒出来；下一通用响应再被 `renderPriceStats()` 隐藏——与
# `renderPriceLoading()` 写下的"未知或已知不支持：不显示 box"直接冲突。
#
# 失败信息与 retry 入口保留在表体(`#retry-link`)，不丢设计 §6.3 的 error + retry。
# 探针通过 `add_init_script` 拦 `window.fetch`：`/v2/analytics/spu-roi` 首通放行
# (真实 200 无 priceStats)，`arm` 后一律回 503——DOM 真实断言，不 grep 源码。
_PRICE_CAPABILITY_FALSE_5XX_JS = """(() => {
  const nativeFetch = window.fetch.bind(window);
  const track = window.__priceCapabilityFalse = { calls: [], arm: false, injected: 0 };
  window.fetch = function (input, init) {
    const url = typeof input === 'string' ? input : (input && input.url) || '';
    if (url.indexOf('/v2/analytics/spu-roi') < 0) return nativeFetch(input, init);
    track.calls.push(url);
    if (!track.arm) return nativeFetch(input, init);
    track.injected += 1;
    return Promise.resolve(new Response(
      JSON.stringify({ detail: 'TEST_price_upstream_503' }),
      { status: 503, headers: { 'Content-Type': 'application/json' } },
    ));
  };
})();
"""


def test_capability_false_with_5xx_keeps_price_summary_hidden(frontend_browser_renderer):
    """已知 capability=false 后再吃一次 5xx：价格 box 保持 hidden，不写"加载失败"。"""
    page = frontend_browser_renderer.open("/v2/pages/spu-roi?shop_pk=7")
    page.add_init_script(_PRICE_CAPABILITY_FALSE_5XX_JS)
    # add_init_script 只对之后的导航生效，重载一次让探针在文档脚本之前装好。
    page.reload(wait_until="domcontentloaded")
    # 首次 200(真实 legacy 载荷，无 priceStats)渲染完 → capability 落为 false。
    page.locator(".tabulator-row").first.wait_for(state="visible")
    assert page.locator("#price-summary").is_hidden()
    assert page.evaluate("() => window.__priceCapabilityFalse.calls.length") >= 1

    page.evaluate("() => { window.__priceCapabilityFalse.arm = true; }")
    page.evaluate(
        """() => {
          const input = document.querySelector('#filter-w-start');
          Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set.call(input, '2026-09-11');
          input.dispatchEvent(new Event('change', {bubbles: true}));
        }"""
    )
    # 表体出现错误占位 + retry 入口 = 5xx 真的走到了 renderError()，用例非空转。
    page.locator("#retry-link").wait_for(state="visible")
    assert page.evaluate("() => window.__priceCapabilityFalse.injected") >= 1
    assert "503" in page.evaluate("() => document.body.textContent")

    assert page.locator("#price-summary").is_hidden()


# ── drill 行点击：去重只认原生事件身份，不得吞掉合法快速 toggle ────────────────
#
# 现场：`rowClick` 曾用「同一 spu_pk + 300ms」拦 Tabulator 的重复派发，结果把用户
# 「点关闭 → 立刻点开同一行」的合法快速 toggle 一起吞掉（第二、三次点击被丢弃，
# drill 关不掉也重不开）。修复换成原生事件身份（`WeakSet` 持有已处理事件，不依赖
# 时钟分辨率）：只有同一原生事件对象被 frozen/main 两侧 row 重复派发时才忽略，
# 用户级连点（各自是不同事件对象）一律照办。
_DRILL_CLICK_HELPERS_JS = """(() => {
  window.__drillClicks = [];
  window.__drillClickRow = () => {
    const row = document.querySelector('.tabulator-row');
    window.__drillClicks.push(Date.now());
    row.click();
  };
})();
"""


def test_drill_same_row_close_then_reopen_works(frontend_browser_renderer, monkeypatch):
    """关闭后 <300ms 内重开同一行：drill 必须能重开（300ms 防重不得吞合法 toggle）。"""
    page = _open_with_supported_payload(frontend_browser_renderer, monkeypatch, "/v2/pages/spu-roi")
    page.locator(".tabulator-row").first.wait_for(state="visible")
    page.evaluate(_DRILL_CLICK_HELPERS_JS)

    page.evaluate("() => window.__drillClickRow()")  # 打开
    drill = page.locator(".op-drill-row")
    drill.wait_for(state="visible")
    assert drill.locator(".op-drill-body").inner_text().strip() != ""
    page.wait_for_timeout(20)

    page.evaluate("() => window.__drillClickRow()")  # 同一行再点一次 = 关闭
    assert page.locator(".op-drill-row").count() == 0
    page.wait_for_timeout(20)

    page.evaluate("() => window.__drillClickRow()")  # 立即重开
    drill = page.locator(".op-drill-row")
    drill.wait_for(state="visible")
    assert drill.locator(".op-drill-body").inner_text().strip() != ""

    # 三次点击确实都落在 300ms 内：用例覆盖的就是「用户级快速 toggle」。
    marks = page.evaluate("() => window.__drillClicks")
    assert len(marks) == 3, marks
    assert marks[2] - marks[0] < 300, marks


def test_drill_tabulator_frozen_row_duplicate_event_still_deduplicated(
    frontend_browser_renderer, monkeypatch
):
    """同一原生 click 被重复派发两次（frozen+main 双发）：drill 只开一次，不被关掉。"""
    page = _open_with_supported_payload(frontend_browser_renderer, monkeypatch, "/v2/pages/spu-roi")
    page.locator(".tabulator-row").first.wait_for(state="visible")
    observed = page.evaluate(
        """() => {
          const row = document.querySelector('.tabulator-row');
          let nativeSeen = 0;
          row.addEventListener('click', () => { nativeSeen += 1; });
          const ev = new MouseEvent('click', {bubbles: true});
          row.dispatchEvent(ev);   // frozen 侧派发
          row.dispatchEvent(ev);   // main 侧派发：同一原生事件对象再来一次
          const drill = document.querySelector('.op-drill-row');
          return {
            nativeSeen: nativeSeen,
            drills: document.querySelectorAll('.op-drill-row').length,
            bodyText: drill?.querySelector('.op-drill-body')?.innerText.trim() || '',
          };
        }"""
    )
    # 原生事件确实投递了两次：没有这条断言，用例可能在「事件根本没到」上空转。
    assert observed["nativeSeen"] == 2, observed
    # 去重生效：第二次派发既没有重复插入 drill，也没有把它当成"再点一次"关掉。
    assert observed["drills"] == 1, observed
    page.locator(".op-drill-row .op-drill-body").wait_for(state="visible")
    assert page.locator(".op-drill-row .op-drill-body").inner_text().strip()
    assert page.locator(".op-drill-row").count() == 1


def test_drill_dedup_follows_event_identity_not_reused_time_stamp(frontend_browser_renderer, monkeypatch):
    """timeStamp 被强行复用时，去重只认原生事件身份，不认时钟值。

    本用例刻意不区分守卫实现（WeakSet 身份守卫与旧的 timeStamp + 50ms 兜底窗口都会过），
    价值在于钉住语义边界：
      * 同一原生事件立即再派发（frozen/main 双发路径）：必须去重，drill 仍只有一份；
      * 换一个事件对象、timeStamp 强行复用成同一个值、间隔 ≥100ms 再派发：不得去重，
        同一行再点一次必须照常 toggle（最坏情况下 Firefox resistFingerprinting 把
        timeStamp 粗化到 100ms，用户两次点击就会拿到同一个 timeStamp）。
    """
    page = _open_with_supported_payload(frontend_browser_renderer, monkeypatch, "/v2/pages/spu-roi")
    page.locator(".tabulator-row").first.wait_for(state="visible")

    immediate = page.evaluate(
        """() => {
          const row = document.querySelector('.tabulator-row');
          let nativeSeen = 0;
          row.addEventListener('click', () => { nativeSeen += 1; });
          const ev = new MouseEvent('click', {bubbles: true});
          Object.defineProperty(ev, 'timeStamp', {value: 1234.5});
          row.dispatchEvent(ev);
          row.dispatchEvent(ev);  // 立即再派发：同一 timeStamp + 同一事件对象
          return {
            nativeSeen: nativeSeen,
            drillRowCount: document.querySelectorAll('.op-drill-row').length,
          };
        }"""
    )
    # 事件确实投递了两次：否则本条断言可能只是在"事件没到"上空转。
    assert immediate["nativeSeen"] == 2, immediate
    assert immediate["drillRowCount"] == 1, immediate

    page.wait_for_timeout(150)  # 远超旧实现的 50ms 同帧兜底窗口
    after_gap = page.evaluate(
        """() => {
          const row = document.querySelector('.tabulator-row');
          const ev = new MouseEvent('click', {bubbles: true});
          Object.defineProperty(ev, 'timeStamp', {value: 1234.5});
          row.dispatchEvent(ev);
          return document.querySelectorAll('.op-drill-row').length;
        }"""
    )
    # 复用同一 timeStamp 的新事件必须被当成新点击：同一行 toggle 关闭会得到 0；
    # 唯一不允许的结果是仍为 1（被吞掉，drill 数原地不动）。
    assert after_gap != 1, after_gap
