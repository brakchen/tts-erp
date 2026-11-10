"""Paging is streamed across the tiktok jobs, and a looping upstream fails the tick.

2026-11-10 production incident (see docs/handoff/): the sync worker was
OOM-killed 20 times in 30 days, all inside tts-erp-sync.service.
tiktok.finance had not completed a tick since 2026-11-06 04:38 because its
one-shot child grew to ~1.9 GB anon-rss against MemoryMax=2G and the kernel
SIGKILLed it mid-walk; the sync_jobs row then stayed status='running'
forever, so the stall was invisible.

Two defects, both fixed:

* the page walk collected every page into one list (``collected.extend``) and
  only released it when the walk ended — a long window had to fit in memory
  at once. It is now a generator: one page is alive at a time.
* a pagination that never advances (the upstream hands back a
  ``next_page_token`` we already walked) used to run forever. It now raises
  ``UpstreamJobError`` — one failed tick, visible, watermark untouched.

The same shape existed in four jobs (finance / orders / products /
after_sales), so all four are covered here.
"""

from __future__ import annotations

import pytest

pytestmark = [
    pytest.mark.domain_finance,
    pytest.mark.domain_commerce,
    pytest.mark.domain_after_sales,
    pytest.mark.layer_unit,
]


# ─── Paging is streamed; a looping upstream fails the tick (2026-11-10 P0) ───
#
# Production incident: the sync worker was OOM-killed 20 times in 30 days, all
# of them inside tts-erp-sync.service. tiktok.finance had not completed a tick
# since 2026-11-06 04:38: its one-shot child grew to ~1.9 GB anon-rss against
# MemoryMax=2G, the kernel SIGKILLed it, and the sync_jobs row stayed
# status='running' — the death was invisible.
#
# Two defects, covered here:
#   * the page walk collected every page into one list and only released it at
#     the end, so a long window had to fit in memory at once;
#   * a pagination that never advances ran forever.
#
# Assertion classes are kept apart: location (the shape of the walk),
# quantity (exact item/call counts), branch coverage (each branch).

from tts_erp_v2.jobs.tiktok import after_sales as after_sales_job
from tts_erp_v2.jobs.tiktok import finance as finance_job
from tts_erp_v2.jobs.tiktok import orders as orders_job
from tts_erp_v2.jobs.tiktok import products as products_job

_WALK_CASES = [
    pytest.param(
        finance_job,
        "payments",
        {"endpoint": finance_job.PAYOUTS_ENDPOINT, "items_key": "payments"},
        id="finance",
    ),
    pytest.param(orders_job, "orders", {}, id="orders"),
    pytest.param(products_job, "products", {}, id="products"),
    pytest.param(
        after_sales_job,
        "return_orders",
        {"endpoint": after_sales_job.RETURNS_ENDPOINT},
        id="after_sales",
    ),
]


class _PageProxy:
    """Serves fixed pages and records every call."""

    def __init__(self, pages):
        self.pages = list(pages)
        self.calls: list[dict | None] = []

    def __call__(self, method, path, body=None):
        self.calls.append(body)
        return self.pages.pop(0) if self.pages else {"code": 0, "data": {}}


def _walk(module, proxy, extra):
    return module._walk_pages(proxy, base_body={"page_size": 50}, **extra)


def _page(items_key, ids, next_token=None):
    data = {items_key: [{"id": i} for i in ids]}
    if next_token:
        data["next_page_token"] = next_token
    return {"code": 0, "data": data}


@pytest.mark.parametrize("module,items_key,extra", _WALK_CASES)
def test_walk_pages_is_a_generator_not_a_list_builder(module, items_key, extra) -> None:
    """位置断言：分页是生成器，不再把整轮结果累积进一个 list。"""
    import inspect

    assert inspect.isgeneratorfunction(module._walk_pages)
    # the walk must not keep a local accumulator any more (the old
    # ``collected`` list that held every item of every page)
    assert "collected" not in module._walk_pages.__code__.co_varnames


@pytest.mark.parametrize("module,items_key,extra", _WALK_CASES)
def test_walk_pages_yields_exactly_every_item_across_pages(
    module, items_key, extra
) -> None:
    """数量断言：跨页取到的条目数与上游调用次数都精确。"""
    proxy = _PageProxy(
        [
            _page(items_key, ["1", "2"], "t1"),
            _page(items_key, ["3"], "t2"),
            _page(items_key, ["4", "5"]),
        ]
    )
    items = list(_walk(module, proxy, extra))
    assert [i["id"] for i in items] == ["1", "2", "3", "4", "5"]
    assert len(proxy.calls) == 3


@pytest.mark.parametrize("module,items_key,extra", _WALK_CASES)
def test_walk_pages_single_page_needs_exactly_one_call(
    module, items_key, extra
) -> None:
    """分支覆盖：单页只调上游一次。"""
    proxy = _PageProxy([_page(items_key, ["1"])])
    items = list(_walk(module, proxy, extra))
    assert len(items) == 1
    assert len(proxy.calls) == 1


@pytest.mark.parametrize("module,items_key,extra", _WALK_CASES)
def test_walk_pages_repeated_token_fails_the_tick(module, items_key, extra) -> None:
    """分支覆盖：上游重复 next_page_token（分页不前进）⇒ 抛错，不再无限跑。"""
    proxy = _PageProxy(
        [
            _page(items_key, ["1"], "same"),
            _page(items_key, ["2"], "same"),
            _page(items_key, ["3"], "same"),
        ]
    )
    with pytest.raises(module.UpstreamJobError) as exc:
        list(_walk(module, proxy, extra))
    assert "did not advance" in str(exc.value)


@pytest.mark.parametrize("module,items_key,extra", _WALK_CASES)
def test_walk_pages_non_zero_code_still_fails_the_tick(
    module, items_key, extra
) -> None:
    """分支覆盖：上游非零 code 仍然抛 UpstreamJobError（契约不变）。"""
    proxy = _PageProxy([{"code": 500, "message": "boom"}])
    with pytest.raises(module.UpstreamJobError):
        list(_walk(module, proxy, extra))
