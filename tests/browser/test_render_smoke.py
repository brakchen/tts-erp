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
from conftest import token_font_stacks  # noqa: F401 - 供本模块断言使用
from render_support import PAGE_SLUGS, font_stacks, normalize_stack, overflow_report, page_path

pytestmark = [pytest.mark.domain_browser, pytest.mark.requires_browser]


def _assert_no_overflow(page, label: str) -> None:
    report = overflow_report(page)
    assert report["scrollWidth"] <= report["clientWidth"] + 1, (
        f"{label} 出现横向滚动条：scrollWidth={report['scrollWidth']} > "
        f"clientWidth={report['clientWidth']}"
    )
    assert not report["offenders"], (
        f"{label} 有未被 overflow 祖先裁剪的越界元素: {report['offenders']}"
    )


@pytest.mark.parametrize("slug", PAGE_SLUGS)
def test_page_has_no_horizontal_overflow(browser_renderer, slug):
    """13 个页面在常见笔记本宽度（1440）不横向溢出。"""
    page = browser_renderer.open(page_path(slug))
    _assert_no_overflow(page, f"{slug}@1440")


@pytest.mark.parametrize("width", [1280, 1440, 1920])
def test_ad_daily_has_no_horizontal_overflow_at_any_viewport(browser_renderer, width):
    """广告日明细逐宽度断言：筛选条 8 列曾在 1280~1536 把查询按钮顶出视口。"""
    page = browser_renderer.open("/v2/pages/ad-daily", width=width)
    _assert_no_overflow(page, f"ad-daily@{width}")


@pytest.mark.parametrize("slug", PAGE_SLUGS)
def test_page_font_stacks_come_only_from_tokens(browser_renderer, token_font_stacks, slug):
    """页面上每一段文字的 computed font-family 必须是 tokens 的三套栈之一。"""
    page = browser_renderer.open(page_path(slug))
    stray = sorted({s for s in font_stacks(page) if normalize_stack(s) not in token_font_stacks})
    assert not stray, (
        f"{slug} 出现 tokens 之外的字体栈（vendor / 内联旁路，需 var(--sans|mono|serif)）: "
        f"{stray}"
    )
