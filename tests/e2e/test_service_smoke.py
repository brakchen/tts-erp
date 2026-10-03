"""Live 冒烟：服务健康、端点清单、汇率快照（原根目录 `test_e2e.py`，v1 端点已退役）。

只读检查，需要运行中的 :9877 服务；默认跳过，见 `tests/e2e/conftest.py` 模块说明。
"""

from __future__ import annotations

from conftest import request_json


def test_healthz():
    status, body = request_json("GET", "/healthz")
    assert status == 200, body
    assert isinstance(body, dict) and body.get("status") in ("ok", "up"), body


def test_endpoints_inventory():
    """GET /endpoints 是活的端点清单（docs/api/external-api.md 的权威来源）。"""
    status, body = request_json("GET", "/endpoints")
    assert status == 200, body
    assert isinstance(body, dict) and body, body
    # v1 /shops /token /db/* /finance/* /sync/* 已删除；清单里应是 v2 端点。
    flat = str(body)
    for stale in ('"/shops"', '"/db/', '"/finance/', '"/sync/'):
        assert stale not in flat, f"端点清单出现已退役 v1 路径: {stale}"


def test_fx_latest_rates():
    """汇率快照只读端点（docs/design/fx-exchange-rates.md）。"""
    status, body = request_json("GET", "/v2/fx/latest")
    assert status == 200, body
    assert isinstance(body, dict), body
