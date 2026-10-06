"""SPU 价格统计真实全栈 E2E：冷启 API + 隔离 PostgreSQL + 真实 Chromium。

覆盖 `docs/design/spu-price-statistics.md` §8.1 gate 4：真实启动临时 API、真实
浏览器、isolated DB，并把价格六指标在页面上的呈现作为断言对象。支撑代码在
`tests/support/spu_price_stats_live.py`（造数、oracle、uvicorn 冷启、浏览器会话）。

三层分工与本层的边界：

- `tests/browser/test_spu_price_columns.py`：mock 载荷的渲染契约（组件证据）；
- `tests/e2e/`：打常驻 :9877 的形状冒烟（fast 排除）；
- **本文件**：接真 API（本进程冷启的 uvicorn）+ 真库（`scripts/test_isolated.sh`
  克隆的库）+ 真 Chromium，断言页面上真实算出来的价格文本、状态文案与行序。

运行方式（私有模板，避免共享模板被别的 lane stamp 成未合并的 revision）：

    TTS_ERP_TEST_NO_DOTENV=1 \\
      TTS_ERP_TEST_TEMPLATE_DB=tts_erp_test_template_price_e2e \\
      bash scripts/test_isolated.sh \\
      --template-db tts_erp_test_template_price_e2e \\
      fast tests/browser/test_spu_price_stats_live.py

本 module 同时带 `domain_e2e` 标记（design §9.1），所以 `test_isolated.sh e2e
tests/browser/test_spu_price_stats_live.py` 也能选中它（对 `fast` 无影响）。

鉴权跑两条 leg（design §8.1 gate 2）：真实 `POST /v2/auth/login` 的 TEST 用户
cookie 会话，以及原有的 readonly API key 会话。浏览器、playwright 或 chromium
不可用是**失败**（不是 skip）：这层证据不允许静默降级成「未运行」。

期望值全部由 oracle 独立算出（件数加权、原生币种→CNY 换算），不读 API 响应，
因此 API 与页面同时算错也会被抓到。
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from support.spu_price_stats_live import (
    DAY_D1,
    DAY_D2,
    DAY_EMPTY_END,
    DAY_EMPTY_START,
    PRICE_FIELDS,
    SPU_A1,
    SPU_A2,
    SPU_B1,
    SPU_B2,
    SPU_B3,
    LiveApi,
    LiveBrowser,
    PriceScene,
    focused_spus_path,
    is_test_shaped,
    live_api_server,
    log_tail,
    priced_test_database,
    spu_roi_path,
)

pytestmark = [
    pytest.mark.domain_browser,
    # `domain_e2e` 让 design §9.1 的 `e2e` 命令也能选中本 module（`fast` 用
    # `-m "not slow and not requires_service"`，不受影响）。
    pytest.mark.domain_e2e,
    pytest.mark.requires_browser,
]

PRICE_GROUP_TITLES = ("采购价", "销售价", "实付价")


@pytest.fixture(scope="module")
def price_scene(db_url: str) -> Iterator[PriceScene]:
    """真实隔离库里的价格场景（两家店 + 汇率快照 + 只读 key）。

    缺 `TTS_ERP_DB_URL_TEST` 直接失败而不是 skip：这层证据不允许静默降级成
    「未运行」。
    """
    assert db_url, "TTS_ERP_DB_URL_TEST 未设置：请用 bash scripts/test_isolated.sh 运行"
    assert is_test_shaped(db_url), f"拒绝在非测试库上造价格数据: {db_url}"
    with priced_test_database(db_url) as scene:
        yield scene


@pytest.fixture(scope="module")
def live_api(price_scene: PriceScene, db_url: str) -> Iterator[LiveApi]:
    """冷启的临时 uvicorn（真实 FastAPI + 真实路由 + 真实库）。"""
    with live_api_server(db_url) as api:
        yield api


# 浏览器/playwright 不可用 = 本层证据缺失，必须失败（design §8.1 gate 5 第 4 条）。
_BROWSER_UNAVAILABLE = (
    "playwright/chromium 不可用：本层是真实浏览器全栈 E2E 证据，不允许跳过"
)


def _launch_chromium() -> tuple[Any, Any]:
    """启动 headless chromium；playwright/chromium 不可用即硬失败（不是 skip）。"""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:  # pragma: no cover - 环境相关
        raise RuntimeError(f"{_BROWSER_UNAVAILABLE}（playwright 导入失败: {exc}）") from exc
    playwright = sync_playwright().start()
    try:
        browser: Any = playwright.chromium.launch(headless=True)
    except Exception as exc:  # pragma: no cover - 环境相关
        playwright.stop()
        raise RuntimeError(f"{_BROWSER_UNAVAILABLE}（chromium 启动失败: {exc}）") from exc
    return playwright, browser


@pytest.fixture()
def live(
    live_api: LiveApi, price_scene: PriceScene, request: pytest.FixtureRequest
) -> Iterator[LiveBrowser]:
    """真实 Chromium 会话。

    作用域是 function（与 `tests/browser/conftest.py` 同因）：Playwright 存活
    期间会占着主线程事件循环，跨用例复用会污染后续 async 用例。

    用例失败时把 uvicorn 日志尾巴 + 每个页面截图写进临时证据目录并打印路径。
    """
    playwright, browser = _launch_chromium()
    session = LiveBrowser(browser, live_api.base, price_scene)
    try:
        yield session
    finally:
        if _call_failed(request):
            _dump_failure_evidence(session, live_api)
        session.close()
        browser.close()
        playwright.stop()


def _call_failed(request: pytest.FixtureRequest) -> bool:
    """用例（call 阶段）是否失败；report 由 tests/browser/conftest.py 挂钩子记录。"""
    report = getattr(request.node, "rep_call", None)
    return bool(report is not None and report.failed)


def _dump_failure_evidence(session: LiveBrowser, live_api: LiveApi) -> None:
    """失败留证：uvicorn 日志尾巴 + 页面截图，落到临时目录并打印路径。"""
    tail_path = live_api.evidence_dir / "uvicorn-tail.log"
    tail_path.write_text(log_tail(live_api.log_path), encoding="utf-8")
    shots = session.screenshot_all(live_api.evidence_dir)
    print(
        f"\n[live-e2e] 失败证据目录 {live_api.evidence_dir}\n"
        f"[live-e2e]   uvicorn 日志: {tail_path}\n"
        f"[live-e2e]   截图: {[str(path) for path in shots]}",
        flush=True,
    )


# ─── 1. 两页六指标 + 价格分组列 ──────────────────────────────────────


def test_spu_roi_price_summary_and_columns_render_real_weighted_stats(
    live: LiveBrowser, price_scene: PriceScene
) -> None:
    """spu-roi：六指标 + 分组列 + 行内价格 = 真实库里件数加权的价格统计。

    两条鉴权 leg（design §8.1 gate 2）：先真实 `POST /v2/auth/login` 的 TEST 用户
    cookie 会话，再是原有的 readonly API key 会话；两条必须渲染同一组真实价格。
    """
    shop = price_scene.shop_a
    expected = shop.stats((DAY_D1,))
    # oracle 自检：设计 §7.1 加权样例（行均值会得到 25.0000，非加权实现会被抓）
    assert expected.cells() == {
        "purchase.mean": "≈37.0000",
        "purchase.median": "≈40.0000",
        "originalSale.mean": "47.0000",
        "originalSale.median": "50.0000",
        "paid.mean": "42.3000",
        "paid.median": "45.0000",
    }

    # ── leg 1：真实登录 cookie 会话（无 Authorization 头）──
    session_page = live.open(spu_roi_path(shop.pk), auth="session")
    session_page.set_window(DAY_D1, DAY_D1)
    session_page.wait_price_state(expected.cells(), expected.summary_status())
    login = live.last_login
    assert login is not None, "浏览器未走真实 /v2/auth/login"
    assert login["username"] == price_scene.login_username, login
    assert login["role"] == "readonly", login  # viewer 角色
    assert "page:spu-roi" in login["pages"], login
    # 页面自己再问一次 /v2/auth/me：证明 DOM 下的会话真的凭 cookie 成立
    assert session_page.auth_identity() == price_scene.login_username
    assert session_page.summary_status() == expected.summary_status()
    assert session_page.price_cells() == expected.cells()
    assert session_page.row_cell(SPU_A1, "priceStats.purchase.mean") == "10.0000"
    for field in PRICE_FIELDS:
        assert session_page.price_field_count(field) == 1, field

    # ── leg 2：原 readonly API key 会话（下面的断言保持原样）──
    page = live.open(spu_roi_path(shop.pk))
    page.set_window(DAY_D1, DAY_D1)
    page.wait_price_state(expected.cells(), expected.summary_status())

    assert page.summary_status() == expected.summary_status()
    header = page.header_text()
    for title in PRICE_GROUP_TITLES:
        assert title in header, header
    for field in PRICE_FIELDS:
        assert page.price_field_count(field) == 1, field

    page.wait_rows((SPU_A1, SPU_A2))
    assert page.row_cell(SPU_A1, "priceStats.purchase.mean") == "10.0000"
    # A2 无人工成本 → K1(40 CNY) 估算样本，页面上必须带 ≈ 而不是伪装成实测
    assert page.row_cell(SPU_A2, "priceStats.purchase.mean") == "≈40.0000"
    # 200000 VND 原生价 × (6.5/26000) = 50 CNY：价格确实走了同快照换算
    assert page.row_cell(SPU_A2, "priceStats.originalSale.mean") == "50.0000"

    # 两条 leg 必须看到同一组真实价格（cookie 会话不降级、不旁路）
    assert session_page.price_cells() == page.price_cells()

    urls = page.spu_roi_requests()
    assert urls, "页面没有真的请求过 /v2/analytics/spu-roi"
    assert any(
        f"shop_pk={shop.pk}" in url and f"w_start={DAY_D1}" in url for url in urls
    ), urls


def test_focused_spus_profile_matches_spu_roi_price_totals(
    live: LiveBrowser, price_scene: PriceScene
) -> None:
    """两页对同一店铺/窗口的价格口径一致（全历史：spu-roi「不限」 vs focused 默认）。"""
    shop = price_scene.shop_a
    expected = shop.stats((DAY_D1, DAY_D2))
    assert expected.cells() == {
        "purchase.mean": "≈34.5455",
        "purchase.median": "≈40.0000",
        "originalSale.mean": "122.7273",
        "originalSale.median": "50.0000",
        "paid.mean": "111.1818",
        "paid.median": "45.0000",
    }

    roi = live.open(spu_roi_path(shop.pk))
    roi.apply_all_history_preset()
    roi.wait_price_state(expected.cells(), expected.summary_status())

    focused = live.open(focused_spus_path(shop.pk))
    focused.wait_price_state(expected.cells(), expected.summary_status())

    assert focused.price_cells() == roi.price_cells()
    assert focused.summary_status() == roi.summary_status()
    for field in PRICE_FIELDS:
        assert focused.price_field_count(field) == 1, field

    roi.wait_rows((SPU_A1, SPU_A2))
    focused.wait_rows((SPU_A1, SPU_A2))
    for spu_id in (SPU_A1, SPU_A2):
        for field in ("priceStats.purchase.median", "priceStats.paid.mean"):
            assert focused.row_cell(spu_id, field) == roi.row_cell(spu_id, field), (
                spu_id,
                field,
            )


# ─── 2. 排序（服务端重取 + 行序真实变化 + 键盘可达）──────────────────


def test_price_header_sorts_on_server_and_reorders_rows(
    live: LiveBrowser, price_scene: PriceScene
) -> None:
    """键盘点价格表头 → 服务端 sort=purchasePriceMean → 行序真的重排。

    默认序（roi_real 无效 → spu_pk 升序）是 [A2, A1]，与价格升序 [A1, A2]
    相反：这样 asc/desc 两向都证明行序真的翻了，不会退化成默认行序。
    """
    shop = price_scene.shop_a
    expected = shop.stats((DAY_D1,))
    page = live.open(spu_roi_path(shop.pk))
    page.set_window(DAY_D1, DAY_D1)
    page.wait_price_state(expected.cells(), expected.summary_status())

    default_order = page.row_order((SPU_A1, SPU_A2))
    assert default_order == [SPU_A2, SPU_A1], (
        "默认行序必须与价格升序相反，否则下面的 asc 断言会被默认序满足",
        default_order,
    )

    header = page.price_header("priceStats.purchase.mean")
    header.focus()
    page.page.keyboard.press("Enter")  # 表头 tabindex=0 + Enter（无障碍路径）
    page.wait_sort_note("平均值 ↑")
    page.wait_row_order((SPU_A1, SPU_A2))
    assert header.get_attribute("aria-sort") == "ascending"
    assert page.wait_request(
        lambda url: "sort=purchasePriceMean" in url and "order=asc" in url
    )
    assert page.row_order((SPU_A1, SPU_A2)) == [SPU_A1, SPU_A2]

    header.focus()
    page.page.keyboard.press("Enter")
    page.wait_sort_note("平均值 ↓")
    page.wait_row_order((SPU_A2, SPU_A1))
    assert header.get_attribute("aria-sort") == "descending"
    assert page.wait_request(
        lambda url: "sort=purchasePriceMean" in url and "order=desc" in url
    )
    assert page.row_order((SPU_A2, SPU_A1)) == [SPU_A2, SPU_A1]


# ─── 3. 切店铺 / 切日期窗口重取 ──────────────────────────────────────


def test_shop_switch_refetches_price_stats(
    live: LiveBrowser, price_scene: PriceScene
) -> None:
    """切店铺：价格重取到另一家店的真实统计，且状态文案随之改变。"""
    shop_a = price_scene.shop_a
    shop_b = price_scene.shop_b
    window = (DAY_D1,)
    page = live.open(spu_roi_path(shop_a.pk))
    page.set_window(DAY_D1, DAY_D1)
    page.wait_price_state(
        shop_a.stats(window).cells(), shop_a.stats(window).summary_status()
    )

    page.select_shop(shop_b.pk)
    expected_b = shop_b.stats(window)
    page.wait_price_state(expected_b.cells(), expected_b.summary_status())
    assert expected_b.summary_status() != shop_a.stats(window).summary_status()
    assert page.summary_status() == expected_b.summary_status()
    assert page.wait_request(lambda url: f"shop_pk={shop_b.pk}" in url)
    page.wait_rows((SPU_B1, SPU_B2, SPU_B3))


def test_date_window_switch_refetches_price_stats(
    live: LiveBrowser, price_scene: PriceScene
) -> None:
    """切日期窗口：后一天的数据、以及无事实窗口的 no_samples 都能取到。"""
    shop = price_scene.shop_a
    page = live.open(spu_roi_path(shop.pk))

    second_day = shop.stats((DAY_D2,))
    page.set_window(DAY_D2, DAY_D2)
    page.wait_price_state(second_day.cells(), second_day.summary_status())
    assert second_day.cells()["purchase.mean"] == "10.0000"  # 有实测成本 → 无 ≈

    first_day = shop.stats((DAY_D1,))
    page.set_window(DAY_D1, DAY_D1)
    page.wait_price_state(first_day.cells(), first_day.summary_status())

    empty = shop.stats((DAY_EMPTY_START, DAY_EMPTY_END))
    page.set_window(DAY_EMPTY_START, DAY_EMPTY_END)
    page.wait_price_state(empty.cells(), empty.summary_status())
    assert set(empty.cells().values()) == {"—"}
    assert "无样本" in page.summary_status()
    assert page.wait_request(
        lambda url: f"w_start={DAY_EMPTY_START}" in url and f"w_end={DAY_EMPTY_END}" in url
    )


# ─── 4. 空样本 / 部分覆盖不得伪装成 0 ────────────────────────────────


def test_partial_and_missing_price_samples_render_dash_not_zero(
    live: LiveBrowser, price_scene: PriceScene
) -> None:
    """无观察 → 六格 `—`；只有 paid 有价 → originalSale 两格 `—`（绝不 0.0000）。"""
    shop_b = price_scene.shop_b
    window = (DAY_D1,)
    expected = shop_b.stats(window)
    page = live.open(spu_roi_path(shop_b.pk))
    page.set_window(DAY_D1, DAY_D1)
    page.wait_price_state(expected.cells(), expected.summary_status())

    assert page.summary_status() == "已加载 · 采购价：完整；销售价：部分覆盖；实付价：完整"

    page.wait_rows((SPU_B1, SPU_B2, SPU_B3))
    no_observation = {field: page.row_cell(SPU_B1, field) for field in PRICE_FIELDS}
    assert set(no_observation.values()) == {"—"}, no_observation

    partial = shop_b.stats(window, SPU_B3)
    assert partial.metric("originalSale").status == "no_samples"
    assert page.row_cell(SPU_B3, "priceStats.originalSale.mean") == "—"
    assert page.row_cell(SPU_B3, "priceStats.originalSale.median") == "—"
    assert page.row_cell(SPU_B3, "priceStats.paid.mean") == "70.0000"


# ─── 5. 价格 tooltip（口径 + 同一读快照的 FX/成本基准）─────────────────


def test_price_tooltip_keyboard_opens_and_closes_with_real_basis(
    live: LiveBrowser, price_scene: PriceScene
) -> None:
    """Enter 打开/Escape 关闭价格 tooltip，文案来自真实响应 meta。"""
    shop = price_scene.shop_a
    expected = shop.stats((DAY_D1,))
    page = live.open(spu_roi_path(shop.pk))
    page.set_window(DAY_D1, DAY_D1)
    page.wait_price_state(expected.cells(), expected.summary_status())

    button = page.price_tip("purchase")
    button.focus()
    page.page.keyboard.press("Enter")
    assert button.get_attribute("aria-expanded") == "true"
    text = page.page.locator("#ops-tip").inner_text()
    assert "来源：ROI 当前有效成本" in text
    assert "TikTok" not in text  # 采购价权威是成本基准，不是 TikTok line item
    assert "覆盖：complete，10/10 件" in text
    assert f"汇率快照：{price_scene.fx_snapshot_id}" in text
    assert "计算时间：20" in text

    page.page.keyboard.press("Escape")
    assert button.get_attribute("aria-expanded") == "false"
    assert page.page.evaluate(
        "document.activeElement === document.querySelector('[data-price-tip=\"purchase\"]')"
    )

    original = page.price_tip("originalSale")
    original.focus()
    page.page.keyboard.press("Enter")
    original_text = page.page.locator("#ops-tip").inner_text()
    assert "来源：TikTok line_items.original_price" in original_text
    assert "覆盖：complete，10/10 件" in original_text
    page.page.keyboard.press("Escape")
    assert original.get_attribute("aria-expanded") == "false"


# ─── 6. 失败态与重试 ────────────────────────────────────────────────


def test_failed_price_request_shows_error_then_retry_recovers(
    live: LiveBrowser, price_scene: PriceScene
) -> None:
    """价格请求 500 → 错误态 + 重试链接；解除故障后重试拿到真实价格。"""
    shop = price_scene.shop_a
    expected = shop.stats((DAY_D1,))
    page = live.open(spu_roi_path(shop.pk))
    page.wait_summary_status("已加载")

    page.fail_spu_roi("E2E 注入故障")
    page.set_window(DAY_D1, DAY_D1)
    page.wait_summary_status("加载失败 · E2E 注入故障")
    assert set(page.price_cells().values()) == {"—"}
    assert page.retry_link().is_visible()

    page.clear_routes()
    page.retry_link().click()
    page.wait_price_state(expected.cells(), expected.summary_status())


# ─── 7. 移动端横向滚动 ──────────────────────────────────────────────


def test_mobile_price_table_scrolls_without_document_overflow(
    live: LiveBrowser, price_scene: PriceScene
) -> None:
    """390×844：价格仍在页面上真实呈现，表格自身横滚、文档不溢出。"""
    shop = price_scene.shop_a
    expected = shop.stats((DAY_D1,))
    page = live.open(spu_roi_path(shop.pk), width=390, height=844)
    page.set_window(DAY_D1, DAY_D1)
    page.wait_price_state(expected.cells(), expected.summary_status())

    report = page.mobile_layout()
    assert report == {
        "documentOverflow": False,
        "tableOverflow": True,
        "productFrozen": True,
    }
    assert page.summary_status() == expected.summary_status()
    for field in PRICE_FIELDS:
        assert page.price_field_count(field) == 1, field
