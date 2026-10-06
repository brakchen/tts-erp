"""渲染回归（无服务）：多视口横向溢出 + 全站字体栈，进 `fast`。

与 `tests/e2e/test_ui_render_smoke.py` 是**同一套断言**（共享
``render_support``），差别只在数据来源：这里渲染本分支 Jinja + 本地静态服务
+ mock，不依赖 :9877，所以每次 `scripts/test_isolated.sh fast` 都会跑。

背景（2026-10-04）：广告日明细筛选条在 1440 把查询按钮顶出页面、
runtime-configs 的裸 ``<code>`` 命中 Bootstrap SFMono 栈 —— 两个问题当时的
`fast` 与 live e2e 都是绿的，因为没有任何一层打开过浏览器。
"""

from __future__ import annotations

import pytest
from render_support import (
    PAGE_SLUGS,
    font_stacks,
    normalize_stack,
    overflow_report,
    page_path,
)

pytestmark = [pytest.mark.domain_browser, pytest.mark.requires_browser]

# ``token_font_stacks`` 是 tests/browser/conftest.py 里的 fixture，由测试函数
# 同名参数注入（同 tests/e2e/test_ui_render_smoke.py 的写法），无需模块级 import。

# 共享渲染门槛的页面清单。``render_support.PAGE_SLUGS`` 由渲染回归 lane 拥有，
# 新页面在自己的 lane 里只做“加一项”的登记：告警页由此进入同一套横向溢出 +
# 字体栈断言，而不是另起一套只跑一次的检查。页面本体由 tests/browser/conftest.py
# 的 ``_render_pages`` 登记并提供 canned 只读载荷。
ALERT_SLUG = "spu-profit-deterioration"
GATE_SLUGS = [*PAGE_SLUGS, ALERT_SLUG]


def _assert_no_overflow(page, label: str) -> None:
    report = overflow_report(page)
    assert report["scrollWidth"] <= report["clientWidth"] + 1, (
        f"{label} 出现横向滚动条：scrollWidth={report['scrollWidth']} > "
        f"clientWidth={report['clientWidth']}"
    )
    assert not report["offenders"], (
        f"{label} 有未被 overflow 祖先裁剪的越界元素: {report['offenders']}"
    )


@pytest.mark.parametrize("slug", GATE_SLUGS)
def test_page_has_no_horizontal_overflow(browser_renderer, slug):
    """共享门槛清单里的页面在常见笔记本宽度（1440）不横向溢出。"""
    page = browser_renderer.open(page_path(slug))
    _assert_no_overflow(page, f"{slug}@1440")


@pytest.mark.parametrize("width", [1280, 1440, 1920])
def test_ad_daily_has_no_horizontal_overflow_at_any_viewport(browser_renderer, width):
    """广告日明细逐宽度断言：筛选条 8 列曾在 1280~1536 把查询按钮顶出视口。"""
    page = browser_renderer.open("/v2/pages/ad-daily", width=width)
    _assert_no_overflow(page, f"ad-daily@{width}")


@pytest.mark.parametrize("width", [390, 768, 1280, 1920])
def test_spu_deterioration_alert_has_no_horizontal_overflow_at_any_viewport(
    browser_renderer, width
):
    """告警页逐宽度断言：18 列表格 + 9 个筛选控件的组合条是最易溢出的新布局。"""
    page = browser_renderer.open(f"/v2/pages/{ALERT_SLUG}", width=width)
    # 等待信号必须与视口无关：< lg 断点下表格容器是 d-none（走卡片列表），
    # 所以用始终可见的状态面板，而不是只在宽屏可见的表格行。
    page.wait_for_selector(
        "#alert-status:not([data-kind='loading'])", timeout=10_000
    )
    _assert_no_overflow(page, f"{ALERT_SLUG}@{width}")


@pytest.mark.parametrize("slug", GATE_SLUGS)
def test_page_font_stacks_come_only_from_tokens(browser_renderer, token_font_stacks, slug):
    """页面上每一段文字的 computed font-family 必须是 tokens 的三套栈之一。"""
    page = browser_renderer.open(page_path(slug))
    stray = sorted({s for s in font_stacks(page) if normalize_stack(s) not in token_font_stacks})
    assert not stray, (
        f"{slug} 出现 tokens 之外的字体栈（vendor / 内联旁路，需 var(--sans|mono|serif)）: "
        f"{stray}"
    )
