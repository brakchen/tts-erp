"""Browser user-path contract for the SPU profit-deterioration alert page.

本层只服务 ``fast``：本地静态服务 + canned 只读载荷（``tests/browser/conftest.py``），
不需要 :9877。断言的是「页面/抽屉行为契约」，不是数值正确性（数值属于 API 层）。

强警告要求（docs/design/spu-profit-deterioration-alert.md §6.3）在这里被固定为
可执行断言：每行/卡片/横幅必须同时有文案 + 图标 + 行处理，
样本不足与数据不可用绝不能长得像健康稳定行。
"""

from __future__ import annotations

import copy
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any, cast

import pytest

import conftest as browser_conftest

pytestmark = [pytest.mark.domain_browser, pytest.mark.requires_browser]

ALERT_PAGE = "/v2/pages/spu-profit-deterioration"
ALERT_API = "/v2/analytics/spu-profit-deterioration"
CONFIG_ITEM = "/v2/config/runtime/items/analytics.spu_profit_deterioration_alert.v1"

_ORIGINAL_RENDER_PAGES = browser_conftest._render_pages  # type: ignore[attr-defined]


def _render_pages_with_alert_page(out: Path) -> None:
    """渲染 conftest 的页面清单，外加本 lane 新增的告警页。

    ``tests/browser/conftest.py::_render_pages`` 的页面表不在本 lane 的文件归属内，
    所以这里只包一层（不改那个文件）：导入本模块发生在 pytest 收集阶段，
    早于 session 级 ``site_url`` fixture 首次渲染。
    """
    _ORIGINAL_RENDER_PAGES(out)
    from tts_erp_v2.api.v2.pages import _render_page

    rendered = _render_page(
        "spu-profit-deterioration.html", current_page="spu-profit-deterioration"
    )
    (out / "spu-profit-deterioration.html").write_bytes(rendered.body)


browser_conftest._render_pages = _render_pages_with_alert_page  # type: ignore[attr-defined]

_ANCHOR = "2026-10-03"
_CALCULATED_AT = "2026-10-03T02:00:00+00:00"


def _thresholds() -> dict[str, Any]:
    def block(w: int, ad_orders: int) -> dict[str, Any]:
        return {
            "roiAbsDelta": f"0.{w}0",
            "roiRelativeDecline": f"0.{w}0",
            "netProfitDecline": f"0.{w}0",
            "minSpendCny": "100",
            "minOrders": 3,
            "minAdOrders": ad_orders,
        }

    return {
        "enabled": True,
        "maturityDays": 7,
        "fast": {
            days: {"warning": block(2, 0), "critical": block(4, 0)} for days in ("1", "3", "7")
        },
        "confirmation": {
            days: {"warning": block(1, 0), "critical": block(3, 0)} for days in ("1", "3", "7")
        },
    }


def _item(**overrides: Any) -> dict[str, Any]:
    item: dict[str, Any] = {
        "shopPk": 7,
        "spuPk": 1001,
        "windowDays": 3,
        "layer": "fast",
        "severity": "warning",
        "state": "roi_deterioration",
        "sampleStatus": "sufficient",
        "previousRoi": "1.1200",
        "currentRoi": "0.8400",
        "roiDecline": "0.2500",
        "previousNetProfitCny": "420.0000",
        "currentNetProfitCny": "290.0000",
        "netProfitDecline": "0.3095",
        "previousSpendCny": "500.0000",
        "currentSpendCny": "510.0000",
        "previousOrderCount": 18,
        "currentOrderCount": 16,
        "previousAdOrderCount": 12,
        "currentAdOrderCount": 11,
        "anchorDate": _ANCHOR,
        "basisCalculatedAt": _CALCULATED_AT,
        "configSource": "runtime_config",
        "configVersion": 3,
        "provisionalLabel": None,
        "warningCode": "ROI_AND_NET_PROFIT_DETERIORATED",
        "warningText": "实际 ROI 与净利润比较恶化；请查看利润构成与订单/售后证据。",
        "drilldown": {
            "profitabilityUrl": "/v2/analytics/spu-roi?shop_pk=7&spu_pk=1001",
            "pageUrl": "/v2/pages/spu-roi",
        },
    }
    item.update(overrides)
    return item


def _payload(items: list[dict[str, Any]], **overrides: Any) -> dict[str, Any]:
    meta: dict[str, Any] = {
        "requestId": "TEST-request-id",
        "enabled": True,
        "anchorDate": _ANCHOR,
        "batchThrough": _ANCHOR,
        "maturityDays": 7,
        "calculatedAt": _CALCULATED_AT,
        "stale": False,
        "coverage": {"materialized": True, "stale": False, "missingWindowCount": 0},
        "config": {
            "key": "analytics.spu_profit_deterioration_alert.v1",
            "source": "runtime_config",
            "version": 3,
            "updatedAt": _CALCULATED_AT,
            "updatedBy": "config-publisher",
            "validation": "passed",
        },
        "effectiveConfig": {
            "source": "runtime_config",
            "version": 3,
            "updatedAt": _CALCULATED_AT,
            "updatedBy": "config-publisher",
            "validation": "passed",
            "enabled": True,
            "maturityDays": 7,
            "thresholds": _thresholds(),
            "payloadHash": "TESTpayloadhash",
            "provisionalLabel": None,
            "drawer": {
                "mode": "published_effective_readonly_safe",
                "canEdit": False,
                "draftIncluded": False,
                "rolloutIncluded": False,
                "secretsIncluded": False,
            },
        },
    }
    meta.update(overrides)
    severity_counts = {
        "warningCount": sum(item["severity"] == "warning" for item in items),
        "criticalCount": sum(item["severity"] == "critical" for item in items),
        "insufficientSampleCount": sum(item["sampleStatus"] != "sufficient" for item in items),
        "shopSpuCount": len({item["spuPk"] for item in items}),
    }
    return {
        "items": items,
        "total": len(items),
        "totals": severity_counts,
        "meta": meta,
    }


class _Mock:
    """可编程的 canned 载荷：默认 admin 会话 + 一条 warning 行。"""

    def __init__(self) -> None:
        self.role = "admin"
        self.items: list[dict[str, Any]] = [_item()]
        self.meta_overrides: dict[str, Any] = {}
        self.item_detail: dict[str, Any] | None = {
            "configKey": "analytics.spu_profit_deterioration_alert.v1",
            "displayName": "SPU 利润劣化告警（回测暂定）",
            "publishedVersion": 3,
            "hasDraft": True,
            "draftVersion": 2,
            "draftPayload": _thresholds(),
            "publishedPayload": _thresholds(),
            "draftRollout": [],
            "publishedRollout": [],
            "jsonSchema": {},
        }
        self.slow_window_days: str | None = None
        self.malformed = False
        self.first_load_gate: threading.Event | None = None
        self.alert_calls = 0

    def payload(self, query: str) -> dict[str, Any]:
        if self.malformed:
            return {"detail": "materialized alert snapshot is unavailable"}
        return copy.deepcopy(_payload(self.items, **self.meta_overrides))


def _patch_mock(monkeypatch: pytest.MonkeyPatch, mock: _Mock) -> None:
    original = cast(
        Callable[..., Any], browser_conftest._mock_payload  # type: ignore[attr-defined]
    )

    def mock_payload(path: str, query: str = "") -> Any:
        if path.endswith(ALERT_API):
            # 可选的 gate：把首个告警请求挂住，直到用例自己 set()。
            # ``Renderer.open()`` 会等 networkidle（8s 上限），因此用例会看到
            # “请求仍在飞行中”的确定性现场，而不是跟 sleep 赛跑。
            if mock.first_load_gate is not None and mock.alert_calls == 0:
                mock.alert_calls += 1
                mock.first_load_gate.wait(timeout=30)
            if mock.slow_window_days and f"window_days={mock.slow_window_days}" in query:
                import time

                time.sleep(2.0)
            return mock.payload(query)
        if path.endswith("/v2/config/runtime/items/analytics.spu_profit_deterioration_alert.v1"):
            return mock.item_detail if mock.item_detail is not None else {"detail": "not found"}
        if path.endswith("/v2/auth/me"):
            return {
                "authenticated": True,
                "username": "TEST_operator",
                "displayName": "TEST 运营",
                "role": mock.role,
                "pages": ["page:spu-profit-deterioration"],
            }
        return original(path, query)

    monkeypatch.setattr(browser_conftest, "_mock_payload", mock_payload)


def _open(
    browser_renderer: Any,
    mock: _Mock,
    monkeypatch: pytest.MonkeyPatch,
    *,
    width: int = 1440,
    expect_rows: bool = True,
) -> Any:
    _patch_mock(monkeypatch, mock)
    page = browser_renderer.open(ALERT_PAGE, width=width)
    if expect_rows:
        page.wait_for_selector("#alert-rows tr[data-kind]", timeout=10_000)
    else:
        page.wait_for_selector("#alert-status:not([data-kind='loading'])", timeout=10_000)
    return page


def test_page_renders_sidebar_entry_active_and_warning_banner(
    browser_renderer: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    page = _open(browser_renderer, _Mock(), monkeypatch, width=1440)

    assert page.locator("h1.op-title").inner_text() == "利润劣化告警"
    entry = page.locator('#sidebar a[href$="/v2/pages/spu-profit-deterioration"]')
    assert entry.count() == 1
    assert entry.get_attribute("aria-current") == "page"
    assert "active" in (entry.get_attribute("class") or "")
    assert "利润劣化告警" in entry.inner_text()

    banner = page.locator("#alert-banner")
    assert banner.is_visible()
    assert banner.get_attribute("data-kind") == "warning"
    assert "告警" in banner.inner_text()
    assert banner.locator(".alert-banner__icon").inner_text() == BANNER_ICONS_WARNING
    provisional = page.locator("#alert-banner-provisional")
    assert provisional.is_visible() is False

    status = page.locator("#alert-status")
    assert status.get_attribute("aria-live") == "polite"
    assert page.locator("#alert-rows tr[data-kind='warning']").is_visible()
    assert page.locator("#total-warning").inner_text() == "1"
    assert page.locator("#total-spus").inner_text() == "1"


BANNER_ICONS_WARNING = "!"


def test_settings_button_opens_drawer_with_keyboard_and_aria(
    browser_renderer: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    page = _open(browser_renderer, _Mock(), monkeypatch)

    button = page.locator("#btn-settings")
    assert button.inner_text() == "阈值设置"
    assert button.get_attribute("aria-haspopup") == "dialog"
    assert button.get_attribute("aria-expanded") == "false"
    drawer = page.locator("#settings-drawer")
    assert drawer.is_visible() is False

    # 键盘激活（Playwright 的 press 会先聚焦元素）。
    button.press("Enter")
    page.wait_for_selector("#settings-drawer[open]", timeout=5_000)
    assert drawer.get_attribute("role") is None  # <dialog> 自带 dialog 语义
    assert drawer.evaluate("el => el.tagName") == "DIALOG"
    assert button.get_attribute("aria-expanded") == "true"
    assert page.evaluate("() => document.getElementById('settings-drawer').open === true")
    assert drawer.get_attribute("aria-labelledby") == "drawer-title"
    # 焦点进入抽屉内部（原生 showModal 的焦点行为）。
    assert page.evaluate(
        "() => document.getElementById('settings-drawer').contains(document.activeElement)"
    )

    page.keyboard.press("Escape")
    page.wait_for_timeout(200)
    assert page.evaluate("() => document.getElementById('settings-drawer').open === false")
    assert button.get_attribute("aria-expanded") == "false"


def test_warning_rows_cards_and_banner_use_text_icon_and_treatment(
    browser_renderer: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    mock = _Mock()
    mock.items = [
        _item(spuPk=1001, severity="critical", state="profit_to_loss"),
        _item(spuPk=1002, severity="warning"),
        _item(
            spuPk=1003,
            severity="none",
            state="sample_insufficient",
            sampleStatus="sample_insufficient",
            currentRoi=None,
            roiDecline=None,
            netProfitDecline=None,
            previousRoi=None,
            warningCode="SAMPLE_INSUFFICIENT",
            warningText=None,
        ),
        _item(
            spuPk=1004,
            severity="none",
            state="unavailable",
            sampleStatus="unavailable",
            currentRoi=None,
            previousRoi=None,
            warningCode="DATA_STALE",
            warningText=None,
        ),
        _item(spuPk=1005, severity="none", state="stable"),
    ]
    page = _open(browser_renderer, mock, monkeypatch, width=1440)

    critical = page.locator("#alert-rows tr[data-kind='critical']").first
    warning = page.locator("#alert-rows tr[data-kind='warning']").first
    sample = page.locator("#alert-rows tr[data-kind='sample']").first
    unavailable = page.locator("#alert-rows tr[data-kind='unavailable']").first
    stable = page.locator("#alert-rows tr[data-kind='stable']").first

    for row, label in (
        (critical, "严重告警"),
        (warning, "告警"),
        (sample, "样本不足，未触发告警"),
        (unavailable, "数据不可用"),
    ):
        assert row.is_visible()
        icon = row.locator(".alert-icon").first
        assert icon.get_attribute("role") == "img"
        assert icon.get_attribute("aria-label") == label
        assert icon.inner_text().strip() != ""
        # 行处理：彩色只是辅助，非颜色信号是左边框样式 + 纯色浅底
        # （斜纹已移除——密集行噪点太重，见 e85d616 与 css 头注）。
        treatment = row.evaluate(
            "el => { const cs = getComputedStyle(el.firstElementChild);"
            " const bg = getComputedStyle(el);"
            " return {width: cs.borderLeftWidth, style: cs.borderLeftStyle,"
            " color: bg.backgroundColor, image: bg.backgroundImage}; }"
        )
        assert treatment["width"] != "0px"
        assert treatment["image"] == "none"
        assert treatment["color"] not in ("rgba(0, 0, 0, 0)", "transparent")
        assert label in row.inner_text()

    assert critical.locator(".alert-badge").first.inner_text().startswith("严重告警")
    assert warning.locator(".alert-badge").first.inner_text().startswith("告警")
    assert sample.locator(".alert-badge").first.inner_text().startswith("样本不足")
    assert sample.evaluate(
        "el => getComputedStyle(el.firstElementChild).borderLeftStyle"
    ) == "dashed"
    assert unavailable.evaluate(
        "el => getComputedStyle(el.firstElementChild).borderLeftStyle"
    ) == "dotted"
    # 样本不足 / 不可用绝不能与健康稳定行同形。
    assert sample.get_attribute("data-sample") == "sample_insufficient"
    assert stable.locator(".alert-badge").first.inner_text().startswith("无告警")
    sample_treatment = sample.evaluate("el => getComputedStyle(el).backgroundColor")
    stable_treatment = stable.evaluate("el => getComputedStyle(el).backgroundColor")
    assert sample_treatment != stable_treatment

    # 空值必须显示为「—」，不能变成 0。
    null_cell = sample.locator(".is-null[data-null='1']").first
    assert null_cell.inner_text().strip() == "—"
    assert "0" not in sample.locator("td").nth(6).inner_text()

    banner = page.locator("#alert-banner")
    assert banner.get_attribute("data-kind") == "critical"
    assert "严重告警" in banner.inner_text()
    assert banner.locator(".alert-banner__icon").inner_text().strip() != "!"

    # 窄屏卡片视图：同一套文案 + 图标 + 卡片处理。
    mobile = browser_renderer.open(ALERT_PAGE, width=390)
    mobile.wait_for_selector("#alert-cards .alert-card[data-kind]", timeout=10_000)
    sample_card = mobile.locator("#alert-cards .alert-card[data-kind='sample']").first
    assert sample_card.is_visible()
    assert "样本不足，未触发告警" in sample_card.inner_text()
    assert sample_card.locator(".alert-icon").first.get_attribute("aria-label") == "样本不足，未触发告警"
    assert sample_card.evaluate(
        "el => getComputedStyle(el).borderLeftStyle"
    ) == "dashed"
    assert mobile.locator("#alert-table-wrap").is_visible() is False


def test_filters_are_sent_to_the_api_and_round_trip_through_the_url(
    browser_renderer: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    page = _open(browser_renderer, _Mock(), monkeypatch)
    requests: list[str] = []
    page.on("request", lambda request: requests.append(request.url))

    page.locator("#filter-window-days button[data-window-days='7']").click()
    page.wait_for_timeout(150)
    page.locator("#filter-severity").select_option("warning")
    page.wait_for_timeout(150)
    page.locator("#filter-anchor-date").fill(_ANCHOR)
    page.wait_for_timeout(400)

    alert_requests = [url for url in requests if ALERT_API in url]
    assert alert_requests
    last = alert_requests[-1]
    for expected in (
        "shop_pk=7",
        "window_days=7",
        "layer=confirmation",
        "severity=warning",
        "sample=sufficient",
        f"anchor_date={_ANCHOR}",
    ):
        assert expected in last, (expected, last)

    # 同一份筛选写回 URL，并在页面上可见（刷新后仍可复现）。
    assert "window_days=7" in page.url
    assert "sample=sufficient" in page.url
    assert page.locator("#filter-echo").inner_text().count("window_days=7") == 1
    assert page.locator("#filter-window-days button[aria-pressed='true']").inner_text().strip() == "7 天"
    # owner 2026-10-07：样本下拉框已移除，页面默认只查 sufficient（可判定行）。
    assert page.locator("#filter-sample").count() == 0
    # owner 2026-10-07：层级下拉框已移除（业务理解不了 fast/confirmation），
    # 页面默认只查 confirmation（确认层）；快层走 ?layer=fast 排查，见下方专项测试。
    assert page.locator("#filter-layer").count() == 0


def test_layer_filter_is_still_reachable_via_url(
    browser_renderer: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """URL ?layer= 保留为排查入口：显式给 fast 时必须透传到 API 并写回 URL。"""
    _patch_mock(monkeypatch, _Mock())
    page = browser_renderer.open(f"{ALERT_PAGE}?layer=fast")
    page.wait_for_selector("#alert-rows tr[data-kind]", timeout=10_000)
    requests: list[str] = []
    page.on("request", lambda request: requests.append(request.url))

    page.locator("#btn-refresh").click()
    page.wait_for_timeout(400)

    alert_requests = [url for url in requests if ALERT_API in url]
    assert alert_requests
    assert "layer=fast" in alert_requests[-1], alert_requests[-1]
    assert "layer=fast" in page.url


def test_sample_filter_is_still_reachable_via_url(
    browser_renderer: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """URL ?sample= 保留为排查入口：显式给 unavailable 时必须透传到 API 并写回 URL。"""
    _patch_mock(monkeypatch, _Mock())
    page = browser_renderer.open(f"{ALERT_PAGE}?sample=unavailable")
    page.wait_for_selector("#alert-rows tr[data-kind]", timeout=10_000)
    requests: list[str] = []
    page.on("request", lambda request: requests.append(request.url))

    page.locator("#btn-refresh").click()
    page.wait_for_timeout(400)

    alert_requests = [url for url in requests if ALERT_API in url]
    assert alert_requests
    assert "sample=unavailable" in alert_requests[-1], alert_requests[-1]
    assert "sample=unavailable" in page.url


def test_loading_state_is_announced_while_refetching(
    browser_renderer: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    mock = _Mock()
    mock.slow_window_days = "3"
    page = _open(browser_renderer, mock, monkeypatch)

    page.locator("#filter-window-days button[data-window-days='3']").click()
    loading = page.evaluate(
        "() => ({live: document.getElementById('alert-status').getAttribute('aria-live'),"
        " text: document.getElementById('alert-status').textContent,"
        " kind: document.getElementById('alert-status').dataset.kind,"
        " rows: document.getElementById('alert-rows').textContent})"
    )
    assert loading["live"] == "polite"
    assert "正在加载告警" in loading["text"]
    assert loading["kind"] == "loading"
    assert "正在加载告警" in loading["rows"]
    page.wait_for_selector("#alert-rows tr[data-kind]", timeout=10_000)


def test_empty_sample_unavailable_stale_and_disabled_states_are_readable(
    browser_renderer: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    empty = _Mock()
    empty.items = []
    page = _open(browser_renderer, empty, monkeypatch, expect_rows=False)
    assert page.locator("#alert-rows tr[data-kind]").count() == 0
    assert "没有达到阈值的告警" in page.locator("#alert-status").inner_text()
    assert page.locator("#alert-status").get_attribute("data-kind") == "ok"
    assert "已检查" in page.locator("#alert-banner").inner_text()

    sample_only = _Mock()
    sample_only.items = [
        _item(
            severity="none",
            state="sample_insufficient",
            sampleStatus="sample_insufficient",
            currentRoi=None,
            previousRoi=None,
        )
    ]
    sample_only_page = _open(browser_renderer, sample_only, monkeypatch)
    assert sample_only_page.locator("#alert-banner").get_attribute("data-kind") == "sample"
    assert "样本不足" in sample_only_page.locator("#alert-status").inner_text()
    assert "样本不足，未触发告警" in sample_only_page.locator("#alert-rows").inner_text()

    unavailable_only = _Mock()
    unavailable_only.items = [
        _item(
            severity="none",
            state="unavailable",
            sampleStatus="unavailable",
            currentRoi=None,
            previousRoi=None,
        )
    ]
    unavailable_page = _open(browser_renderer, unavailable_only, monkeypatch)
    assert unavailable_page.locator("#alert-banner").get_attribute("data-kind") == "unavailable"
    assert "数据不可用" in unavailable_page.locator("#alert-status").inner_text()

    stale = _Mock()
    stale.meta_overrides = {"stale": True, "coverage": {"materialized": True, "stale": True}}
    stale_page = _open(browser_renderer, stale, monkeypatch, expect_rows=False)
    assert stale_page.locator("#alert-status").get_attribute("data-kind") == "stale"
    assert "快照已过期" in stale_page.locator("#alert-status").inner_text()

    disabled = _Mock()
    disabled.items = []
    disabled.meta_overrides = {"enabled": False}
    disabled_page = _open(browser_renderer, disabled, monkeypatch, expect_rows=False)
    assert disabled_page.locator("#alert-status").get_attribute("data-kind") == "disabled"
    assert "告警已停用" in disabled_page.locator("#alert-status").inner_text()
    assert disabled_page.locator("#alert-banner").get_attribute("data-kind") == "disabled"


def test_error_state_reports_contract_failure_and_retry(
    browser_renderer: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    mock = _Mock()
    mock.malformed = True
    _patch_mock(monkeypatch, mock)
    page = browser_renderer.open(ALERT_PAGE)
    page.wait_for_selector("#alert-status[data-kind='error']", timeout=10_000)

    status_text = page.locator("#alert-status").inner_text()
    assert "加载失败" in status_text
    assert "契约错误" in status_text
    assert "刷新" in status_text
    assert page.locator("#alert-banner").get_attribute("data-kind") == "error"
    assert page.locator("#alert-rows .op-error").is_visible()
    # 卡片列表在 ≥lg 断点由 Bootstrap 工具类隐藏；文案仍须下发到卡片容器。
    cards = page.locator("#alert-cards .op-error")
    assert cards.count() == 1
    assert "加载失败" in cards.inner_text()


def test_drilldown_links_use_server_supplied_urls(
    browser_renderer: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    page = _open(browser_renderer, _Mock(), monkeypatch)

    drill = page.locator("#alert-rows a[data-role='drilldown']").first
    assert drill.is_visible()
    assert "查看利润详情" in drill.inner_text()
    assert drill.get_attribute("href") == "/v2/pages/spu-roi?shop_pk=7&spu_pk=1001"
    raw = page.locator("#alert-rows a[data-role='drilldown-json']").first
    assert raw.is_visible()
    assert raw.get_attribute("href") == "/v2/analytics/spu-roi?shop_pk=7&spu_pk=1001"
    assert "alert-drill--secondary" in (raw.get_attribute("class") or "")


def test_drilldown_falls_back_to_json_when_server_omits_page_url(
    browser_renderer: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """服务端不下发 pageUrl 时主 CTA 退回 JSON 端点，而不是给一个死链。"""
    mock = _Mock()
    mock.items = [_item(drilldown={"profitabilityUrl": "/v2/analytics/spu-roi?shop_pk=7&spu_pk=1001"})]
    page = _open(browser_renderer, mock, monkeypatch)

    drill = page.locator("#alert-rows a[data-role='drilldown']").first
    assert drill.get_attribute("href") == "/v2/analytics/spu-roi?shop_pk=7&spu_pk=1001"
    assert drill.get_attribute("data-role-target") == "json"
    assert page.locator("#alert-rows a[data-role='drilldown-json']").count() == 0


def test_spu_scope_filter_sends_ids_round_trips_and_never_widens_silently(
    browser_renderer: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    page = _open(browser_renderer, _Mock(), monkeypatch)
    requests: list[str] = []
    page.on("request", lambda request: requests.append(request.url))

    page.locator("#filter-spu-ids").fill("1001, 1002")
    page.locator("#filter-spu-ids").press("Enter")
    page.wait_for_timeout(300)

    alert_requests = [url for url in requests if ALERT_API in url]
    assert alert_requests
    last = alert_requests[-1]
    # API 契约：可重复的 spu_ids=<内部 spu_pk>
    assert "spu_ids=1001" in last and "spu_ids=1002" in last, last
    # URL 回写用逗号串（与盈利页同一习惯），刷新后仍能复现。
    assert "spu_ids=1001%2C1002" in page.url, page.url
    assert page.locator("#filter-spu-ids").input_value() == "1001,1002"
    assert "spu_ids=1001%2C1002" in page.locator("#filter-echo").inner_text()

    # 超过 100 上限：不发请求、不静默放宽为全 SPU，保持原 scope 并写明原因。
    before = len(requests)
    page.locator("#filter-spu-ids").fill(",".join(str(1000 + i) for i in range(101)))
    page.locator("#filter-spu-ids").press("Enter")
    page.wait_for_timeout(200)
    assert len(requests) == before
    feedback = page.locator("#filter-spu-feedback")
    assert "最多 100 个" in feedback.inner_text()
    assert feedback.get_attribute("data-kind") == "error"
    assert page.locator("#filter-spu-ids").input_value() == "1001,1002"

    # 非正整数同样被拒：服务端 spu_ids 是 int，页面不发送未知值。
    page.locator("#filter-spu-ids").fill("1001,abc")
    page.locator("#filter-spu-ids").press("Enter")
    page.wait_for_timeout(200)
    assert len(requests) == before
    assert "正整数" in page.locator("#filter-spu-feedback").inner_text()
    assert page.locator("#filter-spu-ids").input_value() == "1001,1002"


def test_state_header_filter_covers_documented_enum_and_is_sent_as_state_param(
    browser_renderer: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """状态下拉已移除；点击「状态」列表头循环筛选，合法值只读 data-state-values。"""
    page = _open(browser_renderer, _Mock(), monkeypatch)
    button = page.locator("#filter-state")
    values = button.get_attribute("data-state-values")
    assert values is not None
    options = ["all"] + [v.strip() for v in values.split(",") if v.strip()]
    assert options == [
        "all",
        "profit_to_loss",
        "loss_expanding",
        "loss_to_profit",
        "roi_deterioration",
        "net_profit_deterioration",
        "stable",
        "sample_insufficient",
        "unavailable",
    ]
    # B-04：两个死状态已被删除。JS 的合法值只从 data-state-values 读
    # （alertStateValues()），所以模板是唯一来源；这里点名断言防止死状态被塞回。
    assert "roi_recovery" not in options
    assert "recovery" not in options
    assert len(options) == 9, options

    # 点击循环：all → profit_to_loss → loss_expanding（第 2 次点击）。
    requests: list[str] = []
    page.on("request", lambda request: requests.append(request.url))
    button.click()
    button.click()
    page.wait_for_timeout(200)

    last = [url for url in requests if ALERT_API in url][-1]
    assert "state=loss_expanding" in last, last
    assert "state=loss_expanding" in page.url, page.url
    assert button.inner_text() == "状态：loss_expanding"
    assert button.get_attribute("aria-pressed") == "true"

    # 再点 7 次回到 all，state 参数消失。
    for _ in range(7):
        button.click()
    page.wait_for_timeout(200)
    last = [url for url in requests if ALERT_API in url][-1]
    assert "state=" not in last, last
    assert "state=" not in page.url, page.url
    assert button.inner_text() == "状态"
    assert button.get_attribute("aria-pressed") == "false"


def test_row_click_opens_summary_card_with_server_supplied_values(
    browser_renderer: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    page = _open(browser_renderer, _Mock(), monkeypatch)
    assert page.locator("#alert-summary").is_hidden()

    page.locator("#alert-rows tr[data-kind]").first.click()
    page.wait_for_selector("#alert-summary:not([hidden])", timeout=5_000)

    summary = page.locator("#alert-summary")
    text = summary.inner_text()
    for expected in (
        "shop_pk #7",
        "SPU #1001",
        "3 天",
        "roi_deterioration",
        "warning",
        "sufficient",
        _ANCHOR,
        _CALCULATED_AT,
        "runtime_config v3",
        "ROI_AND_NET_PROFIT_DETERIORATED",
    ):
        assert expected in text, (expected, text)
    for label in (
        "上期 ROI",
        "本期 ROI",
        "ROI 降幅",
        "上期净利润(CNY)",
        "本期净利润(CNY)",
        "净利润降幅",
        "上期消耗(CNY)",
        "本期消耗(CNY)",
        "上期订单数",
        "本期订单数",
        "上期广告订单数",
        "本期广告订单数",
    ):
        assert label in text, label
    assert summary.locator("dd").count() == 12
    # 值是照抄服务端的 Decimal wire string，浏览器不重算。
    assert "1.1200" in text and "0.8400" in text and "0.2500" in text
    assert summary.get_attribute("data-spu-pk") == "1001"
    assert summary.get_attribute("data-shop-pk") == "7"
    assert (
        page.locator("#alert-rows tr[data-kind]").first.get_attribute("aria-expanded")
        == "true"
    )
    # 同一行再次点击 / 关闭按钮都收起面板。
    page.locator("#btn-summary-close").click()
    assert page.locator("#alert-summary").is_hidden()
    assert (
        page.locator("#alert-rows tr[data-kind]").first.get_attribute("aria-expanded")
        == "false"
    )

    # 键盘用户可用行内按钮展开（表格语义不靠 role=button 破坏）。
    page.locator("#alert-rows [data-role='row-summary']").first.click()
    page.wait_for_selector("#alert-summary:not([hidden])", timeout=5_000)
    assert "SPU #1001" in page.locator("#alert-summary").inner_text()


def test_freshness_prompt_reports_snapshot_time_and_announced_update(
    browser_renderer: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    mock = _Mock()
    page = _open(browser_renderer, mock, monkeypatch)

    fresh = page.locator("#alert-freshness")
    assert fresh.get_attribute("aria-live") == "polite"
    assert fresh.get_attribute("data-kind") == "stable"
    first = fresh.inner_text()
    assert _ANCHOR in first and _CALCULATED_AT in first, first

    # 新的物化结果（basisCalculatedAt / meta.calculatedAt 前移）→ 提示「已更新」。
    later = "2026-10-04T02:00:00+00:00"
    mock.items = [_item(basisCalculatedAt=later)]
    mock.meta_overrides = {"calculatedAt": later}
    page.locator("#btn-refresh").click()
    page.wait_for_selector("#alert-freshness[data-kind='updated']", timeout=10_000)
    updated = fresh.inner_text()
    assert "快照已更新" in updated
    assert later in updated


def test_drawer_opened_before_first_payload_renders_tables_when_data_lands(
    browser_renderer: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """抽屉竞态：首个载荷还在路上就打开抽屉，数据到达后必须补上阈值表格。"""
    mock = _Mock()
    mock.first_load_gate = threading.Event()
    _patch_mock(monkeypatch, mock)
    # open() 内部等 networkidle（8s 上限），返回时首个告警请求仍被 gate 挂住。
    page = browser_renderer.open(ALERT_PAGE)
    assert mock.alert_calls == 1

    page.locator("#btn-settings").click()
    page.wait_for_selector("#settings-drawer[open]", timeout=5_000)
    # 请求仍在飞行中：抽屉打开了但还没有权威数据源。
    assert page.locator("#drawer-thresholds .op-threshold-input").count() == 0
    assert page.evaluate(
        "() => document.getElementById('settings-drawer').dataset.rendered"
    ) != "1"

    mock.first_load_gate.set()
    page.wait_for_selector("#drawer-thresholds .op-threshold-input", timeout=10_000)
    assert page.locator("#drawer-thresholds .op-threshold-group").count() == 12
    assert page.evaluate(
        "() => document.getElementById('settings-drawer').dataset.rendered"
    ) == "1"
    assert page.locator("#drawer-thresholds #drawer-enabled").is_checked()
    assert "maturityDays" not in page.locator("#drawer-form-status").inner_text()


def test_readonly_session_sees_disabled_inputs_and_runtime_link(
    browser_renderer: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    mock = _Mock()
    mock.role = "readonly"
    _patch_mock(monkeypatch, mock)
    page = browser_renderer.open(ALERT_PAGE)
    page.wait_for_selector("#alert-rows tr[data-kind]", timeout=10_000)
    requests: list[str] = []
    page.on("request", lambda request: requests.append(request.url))

    page.locator("#btn-settings").click()
    page.wait_for_selector("#settings-drawer[open]", timeout=5_000)

    assert page.locator("#drawer-readonly-note").is_visible()
    assert (
        page.locator("#drawer-readonly-note a").get_attribute("href")
        == "../../v2/pages/runtime-configs"
    )
    inputs = page.locator("#drawer-thresholds .op-threshold-input")
    assert inputs.count() > 0
    assert page.evaluate(
        "() => [...document.querySelectorAll('#drawer-thresholds .op-threshold-input')]"
        ".every(el => el.disabled)"
    )
    assert page.locator("#drawer-actions").is_visible() is False
    assert page.locator("#btn-draft-save").is_disabled()
    assert page.locator("#btn-publish").is_disabled()
    # readonly 不调用 readwrite-only 的运行配置端点。
    assert not [url for url in requests if "/v2/config/runtime/items" in url]
    assert "只读会话" in page.locator("#drawer-form-status").inner_text()


def test_drawer_projects_effective_config_and_seed_fallback_label(
    browser_renderer: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    page = _open(browser_renderer, _Mock(), monkeypatch)
    page.locator("#btn-settings").click()
    page.wait_for_selector("#settings-drawer[open]", timeout=5_000)

    meta_text = page.locator("#drawer-meta").inner_text()
    assert "source runtime_config v3" in meta_text
    assert "updatedAt" in meta_text and "updatedBy config-publisher" in meta_text
    hash_text = page.locator("#drawer-payload-hash").inner_text()
    assert "payloadHash TESTpayloadhash" in hash_text
    assert "draftIncluded false" in hash_text
    assert "rolloutIncluded false" in hash_text
    assert "secretsIncluded false" in hash_text
    assert page.locator("#drawer-provisional").is_visible() is False
    # 已发布版本：载入回测暂定不可用，且说明 seed 只在 fallback 时可见。
    assert page.locator("#btn-load-backtest").get_attribute("aria-disabled") == "true"
    assert "seed_fallback" in (page.locator("#btn-load-backtest").get_attribute("title") or "")
    # 6 组（2 层 × 3 窗口）× 2 档 × 6 字段的表单来自服务端 payload。
    group = page.locator("#drawer-thresholds .op-threshold-group").first
    assert group.locator(".op-threshold-input").count() == 6
    assert page.locator("#drawer-thresholds .op-threshold-group").count() == 12
    assert (
        page.locator("#drawer-thresholds [data-path='fast.1.warning.roiAbsDelta']").input_value()
        == "0.20"
    )
    assert page.locator("#drawer-thresholds #drawer-enabled").is_checked()
    assert page.locator("#drawer-thresholds .op-threshold-global").inner_text().count("7") >= 1

    seed = _Mock()
    seed.items = [
        _item(configSource="seed_fallback", configVersion=None, provisionalLabel="回测暂定")
    ]
    effective = dict(seed.meta_overrides)
    effective["effectiveConfig"] = {
        **copy.deepcopy(_payload([])["meta"]["effectiveConfig"]),
        "source": "seed_fallback",
        "version": None,
        "provisionalLabel": "回测暂定",
    }
    seed.meta_overrides = effective
    seed_page = _open(browser_renderer, seed, monkeypatch)
    assert seed_page.locator("#alert-banner-provisional").inner_text() == "回测暂定"
    assert seed_page.locator("#alert-banner-provisional").is_visible()
    assert "回测暂定" in seed_page.locator("#alert-rows tr[data-kind]").first.inner_text()
    seed_page.locator("#btn-settings").click()
    seed_page.wait_for_selector("#settings-drawer[open]", timeout=5_000)
    assert seed_page.locator("#drawer-provisional").inner_text() == "回测暂定"
    load_button = seed_page.locator("#btn-load-backtest")
    assert load_button.get_attribute("aria-disabled") == "false"
    assert "二次确认" in (load_button.get_attribute("title") or "")


def test_drawer_validation_shows_field_level_errors(
    browser_renderer: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    page = _open(browser_renderer, _Mock(), monkeypatch)
    page.locator("#btn-settings").click()
    page.wait_for_selector("#settings-drawer[open]", timeout=5_000)
    page.wait_for_function(
        "() => document.getElementById('btn-draft-save').disabled === false"
    )

    path = "fast.1.warning.roiAbsDelta"
    page.locator(f"#drawer-thresholds [data-path='{path}']").fill("-1")
    page.locator("#btn-draft-save").click()
    page.wait_for_timeout(200)

    error = page.locator("#err-fast-1-warning-roiAbsDelta")
    assert error.inner_text().strip() != ""
    assert "ROI 绝对差" in error.inner_text()
    assert page.locator(f"#drawer-thresholds [data-path='{path}']").get_attribute("aria-invalid") == "true"
    assert "表单校验未通过" in page.locator("#drawer-form-status").inner_text()
    # 校验失败不得发写请求（Renderer 收尾也会断言本层只读）。

    # 重置只把表单恢复为「已发布 payload」，不覆盖运行时权威（无写请求）。
    page.locator("#btn-reset").click()
    page.wait_for_timeout(200)
    restored = page.locator(f"#drawer-thresholds [data-path='{path}']")
    assert restored.input_value() == "0.20"
    assert restored.get_attribute("aria-invalid") is None
    assert "已发布 payload" in page.locator("#drawer-form-status").inner_text()
    browser_renderer.assert_read_only()
