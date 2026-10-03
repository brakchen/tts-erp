"""Live 冒烟：commerce 只读端点（店铺/商品/订单 + 分页/404/422 契约）。

全部为 GET 只读，覆盖 docs/api/external-api.md 的 commerce 段：
列表分页参数、详情/聚合、按外部 shop_id 反查、以及错误路径的
404/422 响应形态。链式用例依赖 conftest 的 ``first_channel_account`` /
``first_sales_order`` 锚点，live 库无数据时自动跳过。
"""

from __future__ import annotations

from conftest import request_json


def test_channel_accounts_list_shape() -> None:
    """店铺列表：200 数组，且每行具备契约必填字段。"""
    status, body = request_json("GET", "/v2/commerce/channel-accounts")
    assert status == 200, body
    assert isinstance(body, list), body
    for acct in body:
        assert {"id", "platform", "shop_id", "account_name", "status"} <= set(acct), acct


def test_channel_account_detail(first_channel_account) -> None:
    """详情：id 对齐，关键字段与列表一致。"""
    pk = first_channel_account["id"]
    status, body = request_json("GET", f"/v2/commerce/channel-accounts/{pk}")
    assert status == 200, body
    assert body["id"] == pk, body
    assert body["shop_id"] == first_channel_account["shop_id"], body
    assert body["platform"] == first_channel_account["platform"], body


def test_channel_account_by_external_roundtrip(first_channel_account) -> None:
    """按上游 shop_id 反查 → 应回到同一个 shop_pk（外部映射契约）。"""
    status, body = request_json(
        "GET",
        f"/v2/commerce/channel-accounts/by-external/{first_channel_account['shop_id']}",
    )
    assert status == 200, body
    assert body["id"] == first_channel_account["id"], body


def test_channel_account_order_stats(first_channel_account) -> None:
    """单店订单聚合：结构 + 类型契约。"""
    pk = first_channel_account["id"]
    status, body = request_json("GET", f"/v2/commerce/channel-accounts/{pk}/order-stats")
    assert status == 200, body
    assert body["shop_pk"] == pk, body
    assert isinstance(body["distinct_orders"], int) and body["distinct_orders"] >= 0, body
    assert isinstance(body["total_payment_amount"], str), body


def test_unknown_channel_account_404() -> None:
    """不存在的 shop_pk → 404 + detail 文案。"""
    status, body = request_json("GET", "/v2/commerce/channel-accounts/999999999")
    assert status == 404, body
    assert "not found" in str(body), body


def test_unknown_by_external_404() -> None:
    """不存在的上游 shop_id → 404。"""
    status, body = request_json("GET", "/v2/commerce/channel-accounts/by-external/0000000")
    assert status == 404, body
    assert "not found" in str(body), body


def test_channel_products_pagination() -> None:
    """商品列表：limit 生效（≤2 行）+ 行字段契约。"""
    status, body = request_json("GET", "/v2/commerce/channel-products?limit=2")
    assert status == 200, body
    assert isinstance(body, list), body
    assert len(body) <= 2, f"limit=2 应至多返回 2 行, 实际 {len(body)}"
    for product in body:
        assert {"id", "shop_pk", "spu_id", "title"} <= set(product), product


def test_sales_orders_pagination() -> None:
    """订单列表：limit 生效 + 行字段契约。"""
    status, body = request_json("GET", "/v2/commerce/sales-orders?limit=2")
    assert status == 200, body
    assert isinstance(body, list), body
    assert len(body) <= 2, f"limit=2 应至多返回 2 行, 实际 {len(body)}"
    for order in body:
        assert {"id", "shop_pk", "order_id", "status", "currency"} <= set(order), order


def test_sales_order_lines(first_sales_order) -> None:
    """订单行：全部行属于该订单。"""
    order_pk = first_sales_order["id"]
    status, body = request_json("GET", f"/v2/commerce/sales-orders/{order_pk}/lines")
    assert status == 200, body
    assert isinstance(body, list), body
    for line in body:
        assert line["order_pk"] == order_pk, line


def test_unknown_sales_order_404() -> None:
    """不存在的 order_pk → 404 + detail 文案。"""
    status, body = request_json("GET", "/v2/commerce/sales-orders/999999999")
    assert status == 404, body
    assert "not found" in str(body), body


def test_invalid_pagination_limit_422() -> None:
    """limit=0 违反 ge=1 校验 → 422 + pydantic detail 列表。"""
    status, body = request_json("GET", "/v2/commerce/channel-products?limit=0")
    assert status == 422, body
    assert isinstance(body, dict) and isinstance(body.get("detail"), list), body
