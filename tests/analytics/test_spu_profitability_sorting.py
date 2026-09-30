from __future__ import annotations

import re
from dataclasses import fields
from pathlib import Path

from tts_erp_v2.analytics.spu_profitability import SortField, SpuProfitability


def _profitability_headers() -> list[dict[str, str]]:
    source = (
        Path(__file__).resolve().parents[2]
        / "tts_erp_v2"
        / "api"
        / "v2"
        / "pages.py"
    ).read_text(encoding="utf-8")
    tables = re.findall(
        r'(?s)<table class="[^"]*op-table[^"]*".*?</table>',
        source,
    )
    table = next(
        (candidate for candidate in tables if 'data-column-id="product"' in candidate),
        None,
    )
    assert table, "找不到 SPU 盈利主表"
    headers: list[dict[str, str]] = []
    for attrs, label in re.findall(r"(?s)<th\b([^>]*)>(.*?)</th>", table):
        parsed = dict(re.findall(r'([\w-]+)="([^"]*)"', attrs))
        parsed["label"] = re.sub(r"<[^>]+>", "", label).strip()
        headers.append(parsed)
    return headers


def test_every_profitability_metric_column_declares_supported_sort_metadata() -> None:
    """新增指标列必须声明 data-sort，公共 kernel 才能自动绑定排序。"""
    headers = _profitability_headers()
    metric_headers = [
        header
        for header in headers
        if header.get("data-column-id") not in {None, "product"}
    ]
    assert metric_headers

    supported = {member.value for member in SortField}
    row_fields = {field.name for field in fields(SpuProfitability)}
    for header in metric_headers:
        sort_field = header.get("data-sort")
        assert sort_field, f"{header['label']} 缺少 data-sort"
        assert sort_field in supported, f"{header['label']} 未加入 SortField"
        assert sort_field in row_fields, f"{header['label']} 不是盈利行字段"


def test_all_current_profitability_metrics_are_sortable() -> None:
    headers = _profitability_headers()
    by_column = {
        header["data-column-id"]: header.get("data-sort")
        for header in headers
        if "data-column-id" in header
    }
    assert by_column == {
        "product": None,
        "spend": "spend",
        "ad-actual-roi": "ad_system_actual_roi",
        "ad-breakeven-roi": "ad_system_breakeven_roi",
        "effective-sales": "effective_sales",
        "total-orders": "total_orders",
        "effective-orders": "effective_order_count",
        "cancel-rate": "cancel_rate",
        "full-loss-rate": "full_loss_rate",
        "net-profit": "net_profit",
    }
