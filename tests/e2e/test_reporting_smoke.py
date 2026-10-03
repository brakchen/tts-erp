"""Live 冒烟：报表侧只读端点（缺成本/手工成本/广告日/重点关注）。

补充 ``test_finance_smoke.py`` 未覆盖的 /v2/reporting/* 读接口，
断言以「结构 + 类型契约」为主，避免锁死随业务变化的数值。
``focused-spus`` 需要 shop_pk，走 conftest 的 ``first_channel_account`` 锚点。
"""

from __future__ import annotations

from conftest import request_json


def test_reporting_missing_cost_products_shape() -> None:
    """缺成本商品清单：items 数组 + 计数字段为 int。"""
    status, body = request_json("GET", "/v2/reporting/missing-cost-products")
    assert status == 200, body
    assert isinstance(body, dict), body
    assert isinstance(body.get("items"), list), body
    assert isinstance(body.get("total_missing_photo"), int), body


def test_reporting_manual_costs_pagination() -> None:
    """手工成本搜索：total 为全量 int、items 受 limit 约束。"""
    status, body = request_json("GET", "/v2/reporting/manual-costs?limit=5")
    assert status == 200, body
    assert isinstance(body.get("total"), int), body
    assert isinstance(body.get("items"), list), body
    assert len(body["items"]) <= 5, f"limit=5 应至多返回 5 行, 实际 {len(body['items'])}"


def test_reporting_ad_daily_shape() -> None:
    """广告日报列表：{items: [...] } 结构。"""
    status, body = request_json("GET", "/v2/reporting/ad-daily")
    assert status == 200, body
    assert isinstance(body, dict), body
    assert isinstance(body.get("items"), list), body


def test_reporting_ad_daily_options_shape() -> None:
    """广告日报筛选项：sellers 数组且每项带 seller_id/shop_name。"""
    status, body = request_json("GET", "/v2/reporting/ad-daily/options")
    assert status == 200, body
    assert isinstance(body.get("sellers"), list), body
    for seller in body["sellers"]:
        assert {"seller_id", "shop_name"} <= set(seller), seller


def test_reporting_focused_spus_shape(first_channel_account) -> None:
    """重点关注 SPU 列表：回显 shopPk + 分页字段契约。"""
    shop_pk = first_channel_account["id"]
    status, body = request_json("GET", f"/v2/reporting/focused-spus/{shop_pk}")
    assert status == 200, body
    assert isinstance(body, dict), body
    assert body.get("shopPk") == shop_pk, body
    assert isinstance(body.get("items"), list), body
    assert isinstance(body.get("total"), int), body
    assert isinstance(body.get("limit"), int) and isinstance(body.get("offset"), int), body


def test_focused_spus_unknown_shop_404() -> None:
    """不存在的 shop_pk → 404 + detail 文案。"""
    status, body = request_json("GET", "/v2/reporting/focused-spus/999999999")
    assert status == 404, body
    assert "not found" in str(body), body
