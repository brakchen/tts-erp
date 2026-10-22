"""Focused-SPUs / SPU ROI 表头排序必须保持服务端数字序。

回归：Tabulator 默认 sortMode=local。load() 先 clearData 再 setSort，空表
findSorter 会把数字列缓存成 string sorter；replaceData 后「总单量 ↓」变成
9, 8, 63 这种字典序，与排序指示和后端 ORDER BY 都不一致。
"""

from __future__ import annotations

import copy
from collections.abc import Callable
from pathlib import Path
from typing import Any, cast
from urllib.parse import parse_qs

import pytest

import conftest as browser_conftest

pytestmark = [pytest.mark.domain_browser, pytest.mark.requires_browser]

_KERNEL = (
    Path(__file__).resolve().parents[2]
    / "tts_erp_v2"
    / "static"
    / "js"
    / "spu-profitability-page.js"
)

# 数字降序 63,50,9,8,6；字符串降序 9,8,63,6,50。两组必须能区分。
_ORDER_COUNTS = (9, 8, 63, 50, 6)


def test_profitability_kernel_uses_remote_sort_mode() -> None:
    src = _KERNEL.read_text(encoding="utf-8")
    assert 'sortMode: "remote"' in src


def _item(base: dict[str, Any], index: int, total_orders: int) -> dict[str, Any]:
    row = copy.deepcopy(base)
    row["spu_pk"] = 4100 + index
    row["spu_id"] = f"TEST_SORT_{total_orders:03d}"
    row["title"] = f"TEST sort fixture {total_orders}"
    row["total_orders"] = total_orders
    row["effective_order_count"] = max(total_orders - 1, 0)
    return row


def _ordered_items(base: dict[str, Any], query: str) -> list[dict[str, Any]]:
    items = [_item(base, i, count) for i, count in enumerate(_ORDER_COUNTS)]
    params = parse_qs(query)
    sort = (params.get("sort") or [""])[0]
    order = (params.get("order") or ["desc"])[0]
    if sort != "total_orders":
        return items
    reverse = order != "asc"
    return sorted(items, key=lambda row: int(row["total_orders"]), reverse=reverse)


def _open_page(browser_renderer: Any, monkeypatch: pytest.MonkeyPatch, path: str) -> Any:
    original = cast(Callable[..., Any], browser_conftest._mock_payload)

    def mock_payload(request_path: str, query: str = "") -> Any:
        if request_path.endswith("/v2/analytics/spu-roi"):
            payload = copy.deepcopy(original("/v2/analytics/spu-roi"))
            base = payload["items"][0]
            items = _ordered_items(base, query)
            payload["items"] = items
            payload["total"] = len(items)
            payload["totals"]["total_orders"] = sum(_ORDER_COUNTS)
            return payload
        return original(request_path, query)

    monkeypatch.setattr(browser_conftest, "_mock_payload", mock_payload)
    page = browser_renderer.open(path)
    shop_button = page.locator("#shop-modal-list button").first
    if shop_button.is_visible():
        shop_button.click()
    page.locator(".tabulator-row").first.wait_for(state="visible")
    return page


def _visible_total_orders(page: Any) -> list[int]:
    texts = page.locator(
        '.tabulator-row .tabulator-cell[tabulator-field="total_orders"]'
    ).all_inner_texts()
    return [int(text) for text in texts if text.strip() and text.strip() != "—"]


@pytest.mark.parametrize("path", ("/v2/pages/focused-spus", "/v2/pages/spu-roi"))
def test_total_orders_header_keeps_numeric_server_order(
    browser_renderer: Any, monkeypatch: pytest.MonkeyPatch, path: str
) -> None:
    page = _open_page(browser_renderer, monkeypatch, path)
    # Replay load(): clearData → setSort → replaceData(server numeric desc).
    # Local string sorter (empty-table findSorter) rewrites 63,50,9 into 9,8,63.
    page.evaluate(
        """(counts) => {
          const table = window.Tabulator.findTable('#rows')[0];
          const base = table.getData()[0];
          const items = counts.map((n, i) => Object.assign({}, base, {
            spu_pk: 5100 + i,
            spu_id: 'TEST_SORT_' + n,
            title: 'TEST sort fixture ' + n,
            total_orders: n,
          }));
          table.clearData();
          table.setSort('total_orders', 'desc');
          return table.replaceData(items);
        }""",
        sorted(_ORDER_COUNTS, reverse=True),
    )
    page.locator(".tabulator-row").first.wait_for(state="visible")
    values = _visible_total_orders(page)
    assert values == sorted(_ORDER_COUNTS, reverse=True), values
