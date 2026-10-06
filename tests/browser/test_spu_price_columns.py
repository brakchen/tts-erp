"""Browser renderer contract for nested SPU price statistics.

This is component evidence only: the renderer uses the real shared kernel, profiles,
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
    browser_renderer,
    monkeypatch,
    path: str,
    stats: dict[str, dict[str, Any]] | None = None,
):
    original = cast(Callable[..., Any], browser_conftest._mock_payload)

    def mock_payload(request_path: str, query: str = ""):
        if request_path.endswith("/v2/commerce/channel-product-options"):
            return {"items": [{"spu_id": "TEST_SPU_000", "title": "测试 SPU"}], "queryable": True}
        if request_path.endswith("/v2/analytics/spu-roi"):
            return _nested_payload(original, stats)
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
        assert "加载中…" not in page.locator("#price-summary-status").inner_text()


def test_null_and_empty_price_values_never_render_as_zero(browser_renderer, monkeypatch):
    for path, empty_value, status in (
        ("/v2/pages/spu-roi", None, "no_samples"),
        ("/v2/pages/focused-spus", "", "partial"),
    ):
        stats = _price_stats()
        for metric in _METRICS:
            stats[metric]["mean"] = empty_value
            stats[metric]["median"] = empty_value
            stats[metric]["status"] = status
        page = _open_with_supported_payload(browser_renderer, monkeypatch, path, stats)
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


def test_price_tooltip_survives_lazy_drill_cycles(browser_renderer, monkeypatch):
    page = _open_with_supported_payload(browser_renderer, monkeypatch, "/v2/pages/spu-roi")
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
        assert button.get_attribute("aria-expanded") == "true"
        button.click()
        assert button.get_attribute("aria-expanded") == "false"
        button.focus()
        page.keyboard.press("Enter")
        assert button.get_attribute("aria-expanded") == "true"
        page.keyboard.press("Escape")
        assert button.get_attribute("aria-expanded") == "false"
        assert page.evaluate("document.activeElement === document.querySelector('[data-price-tip=\\\"purchase\\\"]')")
        row.click()
        page.wait_for_timeout(40)


def test_price_error_can_retry_to_supported_payload(browser_renderer, monkeypatch):
    original = cast(Callable[..., Any], browser_conftest._mock_payload)
    attempts = 0

    def mock_payload(request_path: str, query: str = ""):
        nonlocal attempts
        if request_path.endswith("/v2/commerce/channel-product-options"):
            return {"items": [{"spu_id": "TEST_SPU_000", "title": "测试 SPU"}], "queryable": True}
        if request_path.endswith("/v2/analytics/spu-roi"):
            attempts += 1
            if attempts == 1:
                return None
            return _nested_payload(original)
        return original(request_path, query)

    monkeypatch.setattr(browser_conftest, "_mock_payload", mock_payload)
    page = browser_renderer.open("/v2/pages/spu-roi")
    shop_button = page.locator("#shop-modal-list button").first
    if shop_button.is_visible():
        shop_button.click()
    page.locator("#retry-link").wait_for(state="visible")
    assert "加载失败" in page.locator("#price-summary-status").inner_text()
    page.locator("#retry-link").click()
    page.locator("#price-summary").wait_for(state="visible")
    assert page.locator("#price-purchase-mean").inner_text() == "≈37.0000"
    assert "加载中…" not in page.locator("#price-summary-status").inner_text()
    assert attempts >= 2


def test_rapid_date_requests_keep_latest_price_payload(browser_renderer, monkeypatch):
    original = cast(Callable[..., Any], browser_conftest._mock_payload)

    def mock_payload(request_path: str, query: str = ""):
        if request_path.endswith("/v2/commerce/channel-product-options"):
            return {"items": [{"spu_id": "TEST_SPU_000", "title": "测试 SPU"}], "queryable": True}
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
    page = browser_renderer.open("/v2/pages/spu-roi")
    shop_button = page.locator("#shop-modal-list button").first
    if shop_button.is_visible():
        shop_button.click()
    page.locator("#price-summary").wait_for(state="visible")
    page.evaluate(
        """() => {
          const input = document.querySelector('#filter-w-start');
          for (const value of ['2026-09-10', '2026-09-11']) {
            input.value = value;
            input.dispatchEvent(new Event('change', {bubbles: true}));
          }
        }"""
    )
    page.wait_for_timeout(550)
    assert page.locator("#price-purchase-mean").inner_text() == "≈11.0000"


def test_rapid_shop_requests_keep_latest_price_payload(browser_renderer, monkeypatch):
    original = cast(Callable[..., Any], browser_conftest._mock_payload)

    def mock_payload(request_path: str, query: str = ""):
        if request_path.endswith("/v2/commerce/channel-accounts"):
            return [
                {"id": 7, "platform": "tiktok", "shop_id": "TEST_SHOP_7", "account_name": "QA 店 7", "region": "VN", "status": "active"},
                {"id": 8, "platform": "tiktok", "shop_id": "TEST_SHOP_8", "account_name": "QA 店 8", "region": "VN", "status": "active"},
            ]
        if request_path.endswith("/v2/commerce/channel-product-options"):
            return {"items": [{"spu_id": "TEST_SPU_000", "title": "测试 SPU"}], "queryable": True}
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
    page = browser_renderer.open("/v2/pages/spu-roi")
    buttons = page.locator("#shop-modal-list button")
    buttons.nth(0).click()
    buttons.nth(1).evaluate("button => button.click()")
    page.locator("#price-summary").wait_for(state="visible")
    page.wait_for_timeout(550)
    assert page.locator("#price-purchase-mean").inner_text() == "≈8.0000"


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


# ── "加载中…" 闪现回归（2026-10-06 用户反馈"闪现一下就不见了"）─────────────
#
# 现场：`#price-summary` 默认 hidden。`renderPriceLoading()` 曾在每次请求开始时
# 无条件 unhide 并写 "加载中…"，响应不带 nested `priceStats` 时
# `renderPriceStats()` 立刻 `hidden = true`，于是 box 闪一下就没。
#
# 加载态只能在 capability 已知为 true 时出现。断言取**响应到达前**的真实 DOM：
# `load()` 在同一个同步块里先调 `renderPriceLoading()` 再发 spu-roi 请求，所以
# fetch 被调用的那一瞬间就是加载态定论点——由 `add_init_script` 注入的探针采样，
# 不 grep 源码。
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
  window.addEventListener('DOMContentLoaded', () => {
    const box = document.getElementById('price-summary');
    if (!box) return;
    new MutationObserver(() => probe.snapshots.push(snapshot())).observe(box, {
      attributes: true, subtree: true, childList: true, characterData: true,
    });
  });
})();
"""


def _conftest_mock_payload() -> Callable[..., Any]:
    """conftest 的 canned 载荷：本层共用但其名带下划线，集中一处取用。"""
    return cast(Callable[..., Any], vars(browser_conftest)["_mock_payload"])


def test_first_load_keeps_price_summary_hidden_without_price_stats(browser_renderer):
    """响应无 priceStats 时：响应到达前 box 就保持 hidden，全程不出现 "加载中…"。"""
    page = browser_renderer.open("/v2/pages/spu-roi?shop_pk=7")
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
    assert inflight["status"] == "", inflight
    assert "加载中…" not in (inflight["purchaseMean"] or "")
    assert probe["snapshots"], "探针没采到任何变更，断言窗口没覆盖到加载态"
    assert all(snap["hidden"] is True for snap in probe["snapshots"]), probe["snapshots"]
    assert page.locator("#price-summary").is_hidden()
    assert "加载中…" not in page.evaluate(
        "() => document.getElementById('price-summary').textContent"
    )


def test_supported_price_payload_shows_loading_then_values_on_next_load(
    browser_renderer, monkeypatch
):
    """已知支持后再次加载（切窗口）：先短暂显示 "加载中…"，响应后出现数值。"""
    original = _conftest_mock_payload()

    def mock_payload(request_path: str, query: str = ""):
        if request_path.endswith("/v2/commerce/channel-product-options"):
            return {"items": [{"spu_id": "TEST_SPU_000", "title": "测试 SPU"}], "queryable": True}
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
    page = browser_renderer.open("/v2/pages/spu-roi?shop_pk=7")
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
          input.value = '2026-09-11';
          input.dispatchEvent(new Event('change', {bubbles: true}));
        }"""
    )
    page.wait_for_function(
        "() => document.getElementById('price-purchase-mean').textContent === '≈11.0000'"
    )

    probe = page.evaluate("() => window.__priceFlashProbe")
    assert len(probe["requestSnapshots"]) >= 2, probe["requestSnapshots"]
    inflight = probe["requestSnapshots"][-1]
    assert inflight["hidden"] is False
    assert inflight["status"] == "加载中…"
    assert inflight["purchaseMean"] == "加载中…"
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


def test_capability_false_with_5xx_keeps_price_summary_hidden(browser_renderer):
    """已知 capability=false 后再吃一次 5xx：价格 box 保持 hidden，不写"加载失败"。"""
    page = browser_renderer.open("/v2/pages/spu-roi?shop_pk=7")
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
          input.value = '2026-09-11';
          input.dispatchEvent(new Event('change', {bubbles: true}));
        }"""
    )
    # 表体出现错误占位 + retry 入口 = 5xx 真的走到了 renderError()，用例非空转。
    page.locator("#retry-link").wait_for(state="visible")
    assert page.evaluate("() => window.__priceCapabilityFalse.injected") >= 1
    assert "TEST_price_upstream_503" in page.evaluate("() => document.body.textContent")

    assert page.locator("#price-summary").is_hidden()
    assert page.evaluate(
        "() => document.getElementById('price-summary-status').textContent"
    ) == ""
    assert "加载失败" not in page.evaluate(
        "() => document.getElementById('price-summary').textContent"
    )
    assert "加载失败" not in page.evaluate(
        "() => document.getElementById('price-purchase-mean').textContent"
    )
