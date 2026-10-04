"""渲染态断言的**单一来源**——browser 层（`tests/browser/`，无服务）与
live e2e 层（`tests/e2e/`，需 :9877）共用这里的 JS 片段与 Renderer。

为什么抽出来（2026-10-04，lane `render-gates`）
------------------------------------------------
两类渲染回归（多视口横向溢出、computed 字体栈旁路）在 HTTP 形状冒烟下全绿，
只能靠真实浏览器断言。断言逻辑写两份必然漂移——巡检 mock 的旧契约事故
（finding f-6001d47e-db2）就是同一类病，所以：

- ``OVERFLOW_JS`` / ``FONT_STACKS_JS`` 在这里定义一次；
- 两层测试都 ``from render_support import ...``（``tests/conftest.py`` 存在，
  pytest 会把 ``tests/`` 放进 ``sys.path``）；
- 两层的差异只在**数据来源**：browser 层渲染本分支的 Jinja + 本地静态服务 +
  mock（不需要服务，进 `fast`）；live 层打真服务 + 真数据。
"""

from __future__ import annotations

import re
from typing import Any

# 全站 13 个页面 slug（与 tts_erp_v2.accounts.pages.PAGES 一一对应）
PAGE_SLUGS = [
    "dashboard",
    "focused-spus",
    "spu-roi",
    "ad-daily",
    "manual-costs",
    "shops",
    "enum-map",
    "runtime-configs",
    "sync-jobs",
    "users",
    "intercept-configs",
    "intercept-requests",
    "intercept-stats",
]

# 主表带数据才有布局意义的页面（需 shop_pk 才渲染数据行）
_DATA_DRIVEN_SLUGS = frozenset({"spu-roi", "focused-spus"})

# 裁剪感知的越界扫描（与 scripts/probe_ui_layout_audit.js 同口径）：
# 被 overflow 祖先裁掉的不计（表格横滚是设计行为），只抓真正顶出视口的元素。
OVERFLOW_JS = """() => {
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

FONT_STACKS_JS = """() => {
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


def page_path(slug: str, shop_pk: int | None = None) -> str:
    """页面路径；只有数据驱动页会拼 ``?shop_pk=``。"""
    if slug in _DATA_DRIVEN_SLUGS and shop_pk:
        return f"/v2/pages/{slug}?shop_pk={shop_pk}"
    return f"/v2/pages/{slug}"


def normalize_stack(value: str) -> str:
    """去掉引号与空白，让 CSS 源文本与 computed font-family 可直接比较。"""
    return re.sub(r"[\s'\"]+", "", value)


def token_font_stacks(css_text: str) -> set[str]:
    """从 tokens.css 源文本解析 --sans/--mono/--serif 三套栈（归一化）。"""
    stacks = set()
    for name in ("sans", "mono", "serif"):
        match = re.search(rf"--{name}:\s*([^;]+);", css_text)
        assert match is not None, f"tokens.css 未声明 --{name}"
        stacks.add(normalize_stack(match.group(1)))
    assert len(stacks) == 3, f"三套栈必须互不相同: {stacks}"
    return stacks


def overflow_report(page: Any) -> dict[str, Any]:
    """跑 OVERFLOW_JS，返回 scrollWidth / clientWidth / 越界元素清单。"""
    return page.evaluate(OVERFLOW_JS)


def font_stacks(page: Any) -> list[str]:
    """页面上每段可见文字的 computed font-family（去重）。"""
    return page.evaluate(FONT_STACKS_JS)


# 页面加载期就会发的「语义只读」POST：有请求体、但只做预览不落库。
# 只读约束拦的是写操作，这类端点进白名单（新增时必须确认它不写库）。
READ_ONLY_POST_SUFFIXES = (
    "/v2/config/runtime/schema/preview",  # runtime-configs 预览 schema，无持久化
)


class Renderer:
    """headless chromium 页面渲染器。

    - 可选 ``Authorization: Bearer``（live 层需要；本地静态服务不需要）；
    - 记录所有非 GET/HEAD/OPTIONS 请求，收尾断言 e2e/browser 层**只读**；
    - 打开下一页前先自检上一页没发写请求，越界越早暴露越好定位。
    """

    def __init__(self, browser: Any, base: str, key: str | None = None) -> None:
        self._browser = browser
        self._base = base.rstrip("/")
        self._key = key
        self._contexts: list[Any] = []
        self.write_requests: list[str] = []

    def open(self, path: str, *, width: int = 1440, height: int = 1200) -> Any:
        self.assert_read_only()
        headers = {"Authorization": f"Bearer {self._key}"} if self._key else None
        context = self._browser.new_context(viewport={"width": width, "height": height})
        if headers:
            context.set_extra_http_headers(headers)
        page = context.new_page()
        page.on("request", self._record)
        response = page.goto(
            self._base + path, wait_until="domcontentloaded", timeout=30_000
        )
        try:
            # 有轮询的页面（sync-jobs）不会 idle，等不到就算了，不因此失败
            page.wait_for_load_state("networkidle", timeout=8_000)
        except Exception:  # noqa: BLE001 - 等待超时不是用例失败
            pass
        status = response.status if response is not None else 0
        assert status == 200, f"{path} 未渲染成功：HTTP {status}"
        self._contexts.append(context)
        return page

    def _record(self, request: Any) -> None:
        if request.method in {"GET", "HEAD", "OPTIONS"}:
            return
        path = request.url.split("?", 1)[0]
        if request.method == "POST" and path.endswith(READ_ONLY_POST_SUFFIXES):
            return
        self.write_requests.append(f"{request.method} {request.url}")

    def assert_read_only(self) -> None:
        assert not self.write_requests, (
            f"渲染冒烟必须只读，却发出了写请求: {self.write_requests}"
        )

    def close(self) -> None:
        for context in self._contexts:
            try:
                context.close()
            except Exception:  # noqa: BLE001 - 收尾尽力而为
                pass
        self._contexts.clear()
        self.assert_read_only()
