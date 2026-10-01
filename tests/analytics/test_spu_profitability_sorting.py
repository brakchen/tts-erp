from __future__ import annotations

import re
from dataclasses import fields
from pathlib import Path

from tts_erp_v2.analytics.spu_profitability import SortField, SpuProfitability


def _profitability_columns() -> list[dict[str, str | None]]:
    """Parse COLUMN_DEFS — the single source of truth for the table.

    Tabulator migration (feat/spu-table-tabulator): the header row no longer
    lives in the HTML template; columns are declared in JS and the kernel
    binds server-side sorting from ``sortField``.
    """
    source = (
        Path(__file__).resolve().parents[2]
        / "tts_erp_v2"
        / "static"
        / "js"
        / "spu-profitability-page.js"
    ).read_text(encoding="utf-8")
    columns: list[dict[str, str | None]] = []
    for match in re.finditer(
        r'\{\s*columnId: "([^"]+)",\s*field: "([^"]+)",\s*title: "([^"]+)",'
        r'\s*sortField: ("([^"]+)"|null)',
        source,
    ):
        columns.append(
            {
                "columnId": match.group(1),
                "field": match.group(2),
                "title": match.group(3),
                "sortField": match.group(5),
            }
        )
    assert columns, "COLUMN_DEFS not found in spu-profitability-page.js"
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
        "ad-actual-roi": "ad_system_actual_roi",
        "ad-breakeven-roi": "ad_system_breakeven_roi",
        "effective-sales": "effective_sales",
        "total-orders": "total_orders",
        "effective-orders": "effective_order_count",
        "cancel-rate": "cancel_rate",
        "full-loss-rate": "full_loss_rate",
        "net-profit": "net_profit",
    }
