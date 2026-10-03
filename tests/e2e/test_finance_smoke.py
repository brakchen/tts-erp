"""Live 冒烟：财务/报表侧只读端点（原根目录 `test_e2e_finance.py`，v1 端点已退役）。

原脚本打的 `/finance/*`、`/db/*` 路由随 v1 退役已删除；这里改为校验现行
`/v2/reporting/*` 只读接口。需要运行中的 :9877 服务；默认跳过。
"""

from __future__ import annotations

from conftest import request_json


def test_reporting_coverage():
    """同步覆盖率报表（docs/design/daily-sync-with-coverage.md §8.1 的读侧）。"""
    status, body = request_json("GET", "/v2/reporting/coverage")
    assert status == 200, body


def test_reporting_profit_daily():
    """旧版粗略毛利日报（只读；新消费者禁止，见 docs/design/spu-profitability-module.md §5）。"""
    status, body = request_json("GET", "/v2/reporting/profit-daily")
    assert status == 200, body
    assert isinstance(body, list), body


def test_reporting_cost_snapshots():
    """成本快照只读列表。"""
    status, body = request_json("GET", "/v2/reporting/cost-snapshots")
    assert status == 200, body
    assert isinstance(body, list), body
