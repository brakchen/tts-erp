from __future__ import annotations

import re
from dataclasses import fields
from pathlib import Path

from tts_erp_v2.analytics.spu_profitability import SortField, SpuProfitability


def _profitability_columns() -> list[dict[str, str | None]]:
    """Parse the React table's sole current-metric declaration."""
    source = (
        Path(__file__).resolve().parents[2]
        / "frontend/src/pages/spu-profitability/ProfitabilityTable.tsx"
    ).read_text(encoding="utf-8")
    definitions = source.split("export const columns = [", 1)[1].split("] as const", 1)[0]
    columns: list[dict[str, str | None]] = [
        {"columnId": "product", "field": "spu_id", "title": "商品", "sortField": None}
    ]
    columns.extend(
        {"columnId": field, "field": field, "title": title, "sortField": field}
        for title, field in re.findall(r"\['([^']+)', '([a-z0-9_]+)'\]", definitions)
    )
    assert len(columns) > 1, "Current React metric columns were not found"
    return columns


def test_every_profitability_metric_column_declares_supported_sort_metadata() -> None:
    """新增指标列必须声明 sortField，公共 kernel 才能自动绑定排序。"""
    columns = _profitability_columns()
    metric_columns = [c for c in columns if c["columnId"] != "product"]
    assert metric_columns

    supported = {member.value for member in SortField}
    row_fields = {field.name for field in fields(SpuProfitability)}
    for column in metric_columns:
        sort_field = column["sortField"]
        assert sort_field, f"{column['title']} 缺少 sortField"
        assert sort_field in supported, f"{column['title']} 未加入 SortField"
        assert sort_field in row_fields, f"{column['title']} 不是盈利行字段"


def test_all_current_profitability_metrics_are_sortable() -> None:
    columns = _profitability_columns()
    by_column = {c["columnId"]: c["sortField"] for c in columns}
    assert by_column == {
        "product": None,
        "spend": "spend",
        "ad_system_actual_roi": "ad_system_actual_roi",
        "ad_system_breakeven_roi": "ad_system_breakeven_roi",
        "effective_sales": "effective_sales",
        "total_orders": "total_orders",
        "effective_order_count": "effective_order_count",
        "cancel_rate": "cancel_rate",
        "full_loss_rate": "full_loss_rate",
        "net_profit": "net_profit",
    }
