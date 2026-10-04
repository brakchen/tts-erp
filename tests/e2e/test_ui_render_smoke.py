"""Live 渲染冒烟：多视口横向溢出 + 全站字体栈（headless chromium）。

为什么需要浏览器用例（2026-10-04，lane `e2e-ui-render`）
----------------------------------------------------------
HTTP 形状冒烟看不见渲染结果。当天上线的两个缺陷在既有 e2e 与 `fast` 下**全绿**：

1. 广告日明细筛选条的 8 列固定轨道合计 1283px，1440 视口放不下，把
   「查询明细」按钮顶出页面（页面出现横向滚动）——只有真实视口量得出来；
2. `runtime-configs` 的裸 ``<code>`` 吃 Bootstrap reboot 的
   ``--bs-font-monospace``（SFMono 栈），全站出现第 4 套字体——只有
   computed font-family 才暴露。

因此这里断言的是**渲染态**：

- 每个页面在 1440 不产生横向滚动，且没有「未被 overflow 祖先裁剪」的越界元素；
- 广告日明细另在 1280/1366/1536/1920/2560 逐一断言（1440 是当初的爆点）；
- 每个页面所有含文字元素的 computed font-family 都必须来自 tokens.css 的
  三套栈（--sans/--mono/--serif），vendor 或内联旁路一律失败。

运行：``bash scripts/test_isolated.sh e2e``（需 :9877 在跑，见 conftest）。
"""

from __future__ import annotations

from typing import Any

import pytest
from conftest import (
    PAGE_SLUGS,
    normalize_stack,
    page_path,
    request_json,
)

# 裁剪感知的越界扫描（与 scripts/probe_ui_layout_audit.js 同口径）：
# 被 overflow 祖先裁掉的不计（表格横滚是设计行为），只抓真正顶出视口的元素。
_OVERFLOW_JS = """() => {
  const de = document.documentElement;
  const offenders = [];
  for (const el of document.querySelectorAll('body *')) {
    const r = el.getBoundingClientRect();
    if (r.width === 0 || r.height === 0) continue;
    if (r.right <= de.clientWidth + 2 || r.left >= de.clientWidth) continue;
    if (getComputedStyle(el).position === 'fixed') continue;
    let p = el.parentElement, clipped = false, nested = false;
    while (p && p !== document.body) {
      const cs = getComputedStyle(p);
      if (/(hidden|auto|scroll|clip)/.test(cs.overflowX)) { clipped = true; break; }
      if (p.getBoundingClientRect().right > de.clientWidth + 2) { nested = true; break; }
      p = p.parentElement;
    }
    if (clipped || nested) continue;
    offenders.push(
      el.tagName.toLowerCase() + '.' + String(el.className).split(' ').slice(0, 2).join('.')
      + '(right=' + Math.round(r.right) + ')'
    );
  }
  return { scrollWidth: de.scrollWidth, clientWidth: de.clientWidth, offenders };
}"""

_FONT_STACKS_JS = """() => {
  const out = new Set();
  const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_ELEMENT);
  let node = walker.currentNode;
  while (node) {
    const hasText = Array.from(node.childNodes)
      .some((c) => c.nodeType === 3 && c.textContent.trim());
    if (hasText) {
      const cs = getComputedStyle(node);
      if (cs.display !== 'none' && cs.visibility !== 'hidden') out.add(cs.fontFamily);
    }
    node = walker.nextNode();
  }
  return [...out];
}"""


@pytest.fixture(scope="module")
def shop_pk() -> int | None:
    """第一个店铺主键；没有店铺时返回 None（数据页退化为空态，不断言跳过）。"""
    status, body = request_json("GET", "/v2/commerce/channel-accounts")
    if status != 200 or not isinstance(body, list) or not body:
        return None
    return body[0].get("id")


def _assert_no_overflow(page: Any, label: str) -> None:
    report = page.evaluate(_OVERFLOW_JS)
    assert report["scrollWidth"] <= report["clientWidth"] + 1, (
        f"{label} 出现横向滚动条：scrollWidth={report['scrollWidth']} > "
        f"clientWidth={report['clientWidth']}"
    )
    assert not report["offenders"], (
        f"{label} 有未被 overflow 祖先裁剪的越界元素: {report['offenders']}"
    )


@pytest.mark.parametrize("slug", PAGE_SLUGS)
def test_page_has_no_horizontal_overflow(renderer, shop_pk, slug):
    """13 个页面在常见笔记本宽度（1440）不横向溢出。"""
    page = renderer.open(page_path(slug, shop_pk))
    _assert_no_overflow(page, f"{slug}@1440")


@pytest.mark.parametrize("width", [1280, 1366, 1536, 1920, 2560])
def test_ad_daily_has_no_horizontal_overflow_at_any_viewport(renderer, width):
    """广告日明细逐宽度断言：筛选条 8 列曾在 1280~1536 把查询按钮顶出视口。"""
    page = renderer.open("/v2/pages/ad-daily", width=width)
    _assert_no_overflow(page, f"ad-daily@{width}")


@pytest.mark.parametrize("slug", PAGE_SLUGS)
def test_page_font_stacks_come_only_from_tokens(renderer, token_font_stacks, slug):
    """页面上每一段文字的 computed font-family 必须是 tokens 的三套栈之一。"""
    page = renderer.open(page_path(slug))
    stacks = page.evaluate(_FONT_STACKS_JS)
    stray = sorted({s for s in stacks if normalize_stack(s) not in token_font_stacks})
    assert not stray, (
        f"{slug} 出现 tokens 之外的字体栈（vendor / 内联旁路，需 var(--sans|mono|serif)）: "
        f"{stray}"
    )
