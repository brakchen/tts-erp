"""TDD contract tests: /v2/order-sync/* — Chrome 扩展订单/物流/结算数据同步。

端点契约：
1. POST /v2/order-sync/has-data — 批量查业务表存在性
2. POST /v2/order-sync/dumps — 接收 dump → inline 解析 → 写业务表 + plugin_logs 健康记录
3. POST /v2/order-sync/reconcile — 订单锚点与物流候选统一查询

auth 分类 = readwrite：匿名 401、readonly 403、readwrite 通过。

数据隔离：TEST_ 哨兵 shopId，autouse fixture 经 db_engine 直连清理。
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text

pytestmark = [pytest.mark.domain_api, pytest.mark.layer_integration]

SHOP_ID = "TEST_shop-001"
ORDER_ID_1 = "TEST_order-001"
ORDER_ID_2 = "TEST_order-002"
LOGISTICS_ORDER_ID = "TEST_logistics-order-001"
STATEMENT_ID_1 = "TEST_stmt-001"
STATEMENT_ID_2 = "TEST_stmt-002"

# FK 删除顺序：child → parent
_CLEANUP_SQLS = [
    "DELETE FROM plugin.tracking_events WHERE shop_id = :s",
    "DELETE FROM plugin.order_lines WHERE shop_id = :s",
    "DELETE FROM plugin.settlement_details WHERE shop_id = :s",
    "DELETE FROM plugin.settlements WHERE shop_id = :s",
    "DELETE FROM plugin.shipments WHERE shop_id = :s",
    "DELETE FROM plugin.orders WHERE shop_id = :s",
]


@pytest.fixture(autouse=True)
def _cleanup_plugin_order_rows(db_engine):
    """Setup + teardown 都清一遍。"""
    params = {"s": SHOP_ID}
    with db_engine.begin() as conn:  # noqa: python-sql-injection — 字面量 SQL
        for stmt in _CLEANUP_SQLS:
            conn.execute(text(stmt), params)
    yield
    with db_engine.begin() as conn:  # noqa: python-sql-injection — 字面量 SQL
        for stmt in _CLEANUP_SQLS:
            # pi-lens-ignore: python-sql-injection
            conn.execute(text(stmt), params)


# ─── Helpers ────────────────────────────────────────────────────────


def _dump_payload(
    domain: str,
    response_body: dict | None,
    *,
    main_order_id: str | None = None,
    endpoint: str = "/api/test",
    method: str = "GET",
) -> dict:
    """构建标准 dump 请求体。"""
    return {
        "protocolVersion": 1,
        "requestId": str(uuid.uuid4()),
        "scope": {"sellerId": SHOP_ID, "shopId": SHOP_ID},
        "dump": {
            "domain": domain,
            "endpoint": endpoint,
            "method": method,
            **({"mainOrderId": main_order_id} if main_order_id else {}),
            "request": {"params": {}, "body": None},
            "response": {"status": 200, "body": response_body},
            "createdAt": "2026-09-08T10:00:00.000Z",
        },
    }


def _order_response(order_ids: list[str], sku_count: int = 1) -> dict:
    """构造 order/list 响应体。"""
    orders = []
    for oid in order_ids:
        skus = [
            {
                "sku_id": f"{oid}_sku_{i}",
                "product_id": f"{oid}_prod_{i}",
                "product_name": f"Product {i}",
                "sku_name": f"Variant {i}",
                "quantity": 2,
                "sale_price": {"amount": "100000", "currency": "VND"},
                "sku_order_status": "DELIVERED",
            }
            for i in range(sku_count)
        ]
        orders.append(
            {
                "main_order_id": oid,
                "order_status_module": {"order_status": "DELIVERED"},
                "price_module": {
                    "payment": {"amount": "200000", "currency": "VND"},
                    "total_amount": {"amount": "200000", "currency": "VND"},
                },
                "sku_module": skus,
            }
        )
    return {"code": 0, "message": "success", "data": {"main_orders": orders}}


def _logistics_response(order_id: str, package_count: int = 1) -> dict:
    """构造 logistic_detail/list 响应体。"""
    packages = []
    for i in range(package_count):
        packages.append(
            {
                "main_order_id": order_id,
                "package_id": f"{order_id}_pkg_{i}",
                "tracking_no": f"TRACK_{order_id}_{i}",
                "logistic_supplier": "VNPost",
                "logistic_detail": {
                    "track_list": [
                        {"time": "2026-09-01T10:00:00Z", "track_status": "Picked up"},
                        {"time": "2026-09-02T15:00:00Z", "track_status": "Delivered"},
                    ]
                },
            }
        )
    return {"code": 0, "message": "success", "data": {"package_list": packages}}


def _statement_list_response(statement_ids: list[str]) -> dict:
    """构造 statement/list/detail 响应体。"""
    records = []
    for sid in statement_ids:
        records.append(
            {
                "statement_id": sid,
                "statement_version": 0,
                "bill_period": "2026-09-01~2026-09-07",
                "settlement_time": "2026-09-08T00:00:00Z",
                "settlement_id": f"settle_{sid}",
                "payment_id": f"pay_{sid}",
                "payment_status": 2,
                "statement_type": 1,
                "settle_amount": {"amount": "1000000", "currency": "VND"},
                "earning_amount": {"amount": "900000", "currency": "VND"},
                "fee_amount": {"amount": "100000", "currency": "VND"},
                "adjust_amount": {"amount": "0", "currency": "VND"},
                "payable_amount": {"amount": "900000", "currency": "VND"},
                "shipping_amount": {"amount": "50000", "currency": "VND"},
                "total_reserve_amount": {"amount": "0", "currency": "VND"},
            }
        )
    return {
        "code": 0,
        "message": "success",
        "data": {"statement_records": records},
    }


def _statement_transaction_response(sku_detail_id: str) -> dict:
    """构造 statement/transaction/detail 响应体。"""
    return {
        "code": 0,
        "message": "success",
        "data": {
            "sku_record": {
                "statement_sku_detail_id": sku_detail_id,
                "statement_id": STATEMENT_ID_1,
                "statement_version": 0,
                "trade_order_id": "TO-001",
                "sku_id": "SKU-001",
                "product_name": "Widget",
                "sku_name": "Blue",
                "quantity": 2,
                "settlement_status": 1,
                "placed_time": "2026-09-01T08:00:00Z",
                "settlement_amount": {"amount": "100000", "currency": "VND"},
                "earning_amount": {"amount": "90000", "currency": "VND"},
                "fees": {"amount": "10000", "currency": "VND"},
                "in_come": {
                    "amount": {"amount": "100000", "currency": "VND"},
                    "fee_list": [
                        {"type": "GROSS_SALES", "amount": {"amount": "100000", "currency": "VND"}},
                    ],
                },
                "out_come": {
                    "amount": {"amount": "10000", "currency": "VND"},
                    "fee_list": [
                        {
                            "type": "PLATFORM_COMMISSION",
                            "amount": {"amount": "8000", "currency": "VND"},
                            "sub_fees": [
                                {"type": "COMMISSION_TAX", "amount": {"amount": "2000", "currency": "VND"}},
                            ],
                        },
                    ],
                },
            }
        },
        "seller_web_cut_flow": True,
        "seller_app_cut_flow": False,
    }


def _after_sales_response(cancel_id: str = "TEST_cancel-001") -> dict:
    """构造 /return_refund/202309/cancellations/search 响应体。"""
    return {
        "code": 0,
        "message": "success",
        "data": {
            "cancellations": [
                {
                    "cancel_id": cancel_id,
                    "cancel_type": "BUYER_CANCEL",
                    "cancel_status": "CANCELLATION_REQUEST_COMPLETE",
                    "order_id": "TEST_order-001",
                    "reason": "Changed mind",
                    "request_time": "2026-09-15T10:00:00Z",
                    "complete_time": "2026-09-15T12:00:00Z",
                    "cancel_line_items": [
                        {
                            "id": "line_item_1",
                            "order_line_item_id": "SKU-001",
                            "sku_id": "SKU-001",
                            "product_id": "PROD-001",
                            "quantity": 1,
                            "refund_amount": {"amount": "100000", "currency": "VND"},
                        }
                    ],
                }
            ]
        },
    }


# ─── Auth: anonymous 401 ───────────────────────────────────────────


def test_has_data_anonymous_is_401(api_client):
    r = api_client.post(
        "/v2/order-sync/has-data",
        json={
            "scope": {"sellerId": SHOP_ID, "shopId": SHOP_ID},
            "domain": "orders",
            "ids": [ORDER_ID_1],
        },
    )
    assert r.status_code == 401


def test_dumps_anonymous_is_401(api_client):
    r = api_client.post(
        "/v2/order-sync/dumps",
        json=_dump_payload("orders", _order_response([ORDER_ID_1])),
    )
    assert r.status_code == 401


def test_reconcile_anonymous_is_401(api_client):
    r = api_client.post(
        "/v2/order-sync/reconcile",
        json={
            "protocolVersion": 1,
            "scope": {"sellerId": SHOP_ID, "shopId": SHOP_ID},
            "domains": ["orders"],
            "orders": {"anchorPositions": [0]},
        },
    )
    assert r.status_code == 401


# ─── Auth: readonly 403 ────────────────────────────────────────────


def test_has_data_readonly_is_403(api_client, readonly_key):
    r = api_client.post(
        "/v2/order-sync/has-data",
        headers={"Authorization": f"Bearer {readonly_key}"},
        json={
            "scope": {"sellerId": SHOP_ID, "shopId": SHOP_ID},
            "domain": "orders",
            "ids": [ORDER_ID_1],
        },
    )
    assert r.status_code == 403


def test_dumps_readonly_is_403(api_client, readonly_key):
    r = api_client.post(
        "/v2/order-sync/dumps",
        headers={"Authorization": f"Bearer {readonly_key}"},
        json=_dump_payload("orders", _order_response([ORDER_ID_1])),
    )
    assert r.status_code == 403


def test_reconcile_readonly_is_403(api_client, readonly_key):
    r = api_client.post(
        "/v2/order-sync/reconcile",
        headers={"Authorization": f"Bearer {readonly_key}"},
        json={
            "protocolVersion": 1,
            "scope": {"sellerId": SHOP_ID, "shopId": SHOP_ID},
            "domains": ["orders"],
            "orders": {"anchorPositions": [0]},
        },
    )
    assert r.status_code == 403


# ─── has-data: 空库查 covered=false ────────────────────────────────


def test_has_data_orders_empty_db_returns_all_false(api_client, readwrite_key):
    r = api_client.post(
        "/v2/order-sync/has-data",
        headers={"Authorization": f"Bearer {readwrite_key}"},
        json={
            "scope": {"sellerId": SHOP_ID, "shopId": SHOP_ID},
            "domain": "orders",
            "ids": [ORDER_ID_1, ORDER_ID_2],
        },
    )
    assert r.status_code == 200
    body = r.json()
    assert body["code"] == 0
    assert body["data"]["domain"] == "orders"
    assert body["data"]["covered"] == {ORDER_ID_1: False, ORDER_ID_2: False}


def test_has_data_logistics_empty_db_returns_all_false(api_client, readwrite_key):
    r = api_client.post(
        "/v2/order-sync/has-data",
        headers={"Authorization": f"Bearer {readwrite_key}"},
        json={
            "scope": {"sellerId": SHOP_ID, "shopId": SHOP_ID},
            "domain": "logistics",
            "ids": [LOGISTICS_ORDER_ID],
        },
    )
    assert r.status_code == 200
    assert r.json()["data"]["covered"] == {LOGISTICS_ORDER_ID: False}


def test_has_data_statements_empty_db_returns_all_false(api_client, readwrite_key):
    r = api_client.post(
        "/v2/order-sync/has-data",
        headers={"Authorization": f"Bearer {readwrite_key}"},
        json={
            "scope": {"sellerId": SHOP_ID, "shopId": SHOP_ID},
            "domain": "statements",
            "ids": [STATEMENT_ID_1],
        },
    )
    assert r.status_code == 200
    assert r.json()["data"]["covered"] == {STATEMENT_ID_1: False}


# ─── has-data: 插入后查 covered=true ───────────────────────────────


def test_has_data_orders_returns_true_after_dump(api_client, readwrite_key):
    # 先写入订单
    api_client.post(
        "/v2/order-sync/dumps",
        headers={"Authorization": f"Bearer {readwrite_key}"},
        json=_dump_payload(
            "orders",
            _order_response([ORDER_ID_1]),
            endpoint="/api/fulfillment/order/list",
            method="POST",
        ),
    )
    # 查 has-data
    r = api_client.post(
        "/v2/order-sync/has-data",
        headers={"Authorization": f"Bearer {readwrite_key}"},
        json={
            "scope": {"sellerId": SHOP_ID, "shopId": SHOP_ID},
            "domain": "orders",
            "ids": [ORDER_ID_1, ORDER_ID_2],
        },
    )
    body = r.json()
    assert body["data"]["covered"] == {ORDER_ID_1: True, ORDER_ID_2: False}


def test_has_data_logistics_returns_true_after_dump(api_client, readwrite_key):
    # 先写入物流
    api_client.post(
        "/v2/order-sync/dumps",
        headers={"Authorization": f"Bearer {readwrite_key}"},
        json=_dump_payload(
            "logistics",
            _logistics_response(LOGISTICS_ORDER_ID),
            main_order_id=LOGISTICS_ORDER_ID,
            endpoint="/api/v1/fulfillment/logistic_detail/list",
        ),
    )
    # 查 has-data
    r = api_client.post(
        "/v2/order-sync/has-data",
        headers={"Authorization": f"Bearer {readwrite_key}"},
        json={
            "scope": {"sellerId": SHOP_ID, "shopId": SHOP_ID},
            "domain": "logistics",
            "ids": [LOGISTICS_ORDER_ID],
        },
    )
    assert r.json()["data"]["covered"] == {LOGISTICS_ORDER_ID: True}


def test_has_data_statements_returns_true_after_dump(api_client, readwrite_key):
    # 先写入结算单
    api_client.post(
        "/v2/order-sync/dumps",
        headers={"Authorization": f"Bearer {readwrite_key}"},
        json=_dump_payload(
            "statements",
            _statement_list_response([STATEMENT_ID_1]),
            endpoint="/api/v1/pay/statement/list/detail",
        ),
    )
    # 查 has-data
    r = api_client.post(
        "/v2/order-sync/has-data",
        headers={"Authorization": f"Bearer {readwrite_key}"},
        json={
            "scope": {"sellerId": SHOP_ID, "shopId": SHOP_ID},
            "domain": "statements",
            "ids": [STATEMENT_ID_1, STATEMENT_ID_2],
        },
    )
    assert r.json()["data"]["covered"] == {STATEMENT_ID_1: True, STATEMENT_ID_2: False}


def test_has_data_statements_matches_requested_version(api_client, readwrite_key):
    # 先写入 statement_version=0。
    api_client.post(
        "/v2/order-sync/dumps",
        headers={"Authorization": f"Bearer {readwrite_key}"},
        json=_dump_payload(
            "statements",
            _statement_list_response([STATEMENT_ID_1]),
            endpoint="/api/v1/pay/statement/list/detail",
        ),
    )

    # 同一个 statement_id 的新版本不能被旧版本误报为已覆盖。
    r = api_client.post(
        "/v2/order-sync/has-data",
        headers={"Authorization": f"Bearer {readwrite_key}"},
        json={
            "scope": {"sellerId": SHOP_ID, "shopId": SHOP_ID},
            "domain": "statements",
            "ids": [STATEMENT_ID_1],
            "versions": {STATEMENT_ID_1: 1},
        },
    )
    assert r.status_code == 200
    assert r.json()["data"]["covered"] == {STATEMENT_ID_1: False}

    # 未携带版本号的旧客户端仍保留按 statement_id 的兼容语义。
    r = api_client.post(
        "/v2/order-sync/has-data",
        headers={"Authorization": f"Bearer {readwrite_key}"},
        json={
            "scope": {"sellerId": SHOP_ID, "shopId": SHOP_ID},
            "domain": "statements",
            "ids": [STATEMENT_ID_1],
        },
    )
    assert r.json()["data"]["covered"] == {STATEMENT_ID_1: True}

    # 同一 statement 的多个版本必须全部存在才算 covered。
    r = api_client.post(
        "/v2/order-sync/has-data",
        headers={"Authorization": f"Bearer {readwrite_key}"},
        json={
            "scope": {"sellerId": SHOP_ID, "shopId": SHOP_ID},
            "domain": "statements",
            "ids": [STATEMENT_ID_1],
            "versions": {STATEMENT_ID_1: [0, 1]},
        },
    )
    assert r.status_code == 200
    assert r.json()["data"]["covered"] == {STATEMENT_ID_1: False}


# ─── dumps: order inserted ─────────────────────────────────────────


def test_dumps_order_inserted(api_client, readwrite_key):
    r = api_client.post(
        "/v2/order-sync/dumps",
        headers={"Authorization": f"Bearer {readwrite_key}"},
        json=_dump_payload(
            "orders",
            _order_response([ORDER_ID_1], sku_count=2),
            endpoint="/api/fulfillment/order/list",
            method="POST",
        ),
    )
    assert r.status_code == 200
    body = r.json()
    assert body["code"] == 0
    assert body["message"] == "success"
    # AGENTS.md §2.5: 4 字段 envelope（data 可为空 dict）
    assert body["data"] == {}


def test_dumps_order_with_no_parsed_rows_is_not_reported_as_inserted(
    api_client, readwrite_key
):
    # AGENTS.md §2.5: 解析成功但 0 行 → 200 + empty data
    # （原 rowsWritten=0 hack 删除，HTTP code 不再作为 parse-error 信号）
    r = api_client.post(
        "/v2/order-sync/dumps",
        headers={"Authorization": f"Bearer {readwrite_key}"},
        json=_dump_payload(
            "orders",
            {"code": 0, "data": {"main_orders": []}},
            endpoint="/api/fulfillment/order/list",
            method="POST",
        ),
    )
    assert r.status_code == 200
    assert r.json()["code"] == 0
    assert r.json()["data"] == {}


def test_dumps_empty_response_returns_422(api_client, readwrite_key):
    """AGENTS.md §2.5: response.body=null → 422 EMPTY_RESPONSE_BODY（PERMANENT）。"""
    r = api_client.post(
        "/v2/order-sync/dumps",
        headers={"Authorization": f"Bearer {readwrite_key}"},
        json=_dump_payload(
            "orders",
            None,
            endpoint="/api/fulfillment/order/list",
            method="POST",
        ),
    )
    assert r.status_code == 422
    body = r.json()
    assert body["code"] == "EMPTY_RESPONSE_BODY"
    assert "plugin must not advance progress" in body["message"]


# ─── dumps: logistics inserted ─────────────────────────────────────


def test_dumps_logistics_inserted(api_client, readwrite_key):
    r = api_client.post(
        "/v2/order-sync/dumps",
        headers={"Authorization": f"Bearer {readwrite_key}"},
        json=_dump_payload(
            "logistics",
            _logistics_response(LOGISTICS_ORDER_ID, package_count=1),
            main_order_id=LOGISTICS_ORDER_ID,
            endpoint="/api/v1/fulfillment/logistic_detail/list",
        ),
    )
    assert r.status_code == 200
    body = r.json()
    assert body["code"] == 0
    assert body["message"] == "success"


# ─── dumps: statement list inserted ─────────────────────────────────


def test_dumps_statement_list_inserted(api_client, readwrite_key):
    r = api_client.post(
        "/v2/order-sync/dumps",
        headers={"Authorization": f"Bearer {readwrite_key}"},
        json=_dump_payload(
            "statements",
            _statement_list_response([STATEMENT_ID_1, STATEMENT_ID_2]),
            endpoint="/api/v1/pay/statement/list/detail",
        ),
    )
    assert r.status_code == 200
    body = r.json()
    assert body["code"] == 0
    assert body["message"] == "success"


def test_dumps_single_statement_object_inserted(api_client, readwrite_key):
    """插件逐 statement 上传的单对象 response.body 也必须写入结算表。"""
    record = _statement_list_response([STATEMENT_ID_1])["data"]["statement_records"][0]
    r = api_client.post(
        "/v2/order-sync/dumps",
        headers={"Authorization": f"Bearer {readwrite_key}"},
        json=_dump_payload(
            "statements",
            record,
            endpoint="/api/v1/pay/statement/list/detail",
        ),
    )
    assert r.status_code == 200
    assert r.json()["code"] == 0
    assert r.json()["message"] == "success"


# ─── dumps: statement transaction detail inserted ───────────────────


def test_dumps_statement_transaction_inserted(api_client, readwrite_key):
    r = api_client.post(
        "/v2/order-sync/dumps",
        headers={"Authorization": f"Bearer {readwrite_key}"},
        json=_dump_payload(
            "statements",
            _statement_transaction_response("TEST_ssd-001"),
            endpoint="/api/v1/pay/statement/transaction/detail",
        ),
    )
    assert r.status_code == 200
    body = r.json()
    assert body["code"] == 0
    assert body["message"] == "success"


# ─── dumps: after_sales inserted ────────────────────────────────────


def test_dumps_after_sales_inserted(api_client, readwrite_key):
    """after_sales 域接入：VALID_DOMAINS + 路由分支。"""
    r = api_client.post(
        "/v2/order-sync/dumps",
        headers={"Authorization": f"Bearer {readwrite_key}"},
        json=_dump_payload(
            "after_sales",
            _after_sales_response(),
            endpoint="/return_refund/202309/cancellations/search",
            method="POST",
        ),
    )
    assert r.status_code == 200
    body = r.json()
    assert body["code"] == 0
    assert body["message"] == "success"


def test_dumps_invalid_domain_returns_400(api_client, readwrite_key):
    """非法 domain 走 Pydantic V2 校验，返 400。"""
    r = api_client.post(
        "/v2/order-sync/dumps",
        headers={"Authorization": f"Bearer {readwrite_key}"},
        json=_dump_payload(
            "invalid_domain",
            {"code": 0, "data": {}},
            endpoint="/api/test",
            method="GET",
        ),
    )
    assert r.status_code == 400  # Pydantic V2 校验 + SCHEMA_INVALID


# ─── dumps: health counter 写入 plugin_logs ──────────────────────


def test_dumps_health_counter_logs_successful_dump(api_client, readwrite_key):
    """成功 dump 应写入 plugin_logs（level=info）。"""
    r = api_client.post(
        "/v2/order-sync/dumps",
        headers={"Authorization": f"Bearer {readwrite_key}"},
        json=_dump_payload(
            "orders",
            _order_response([ORDER_ID_1], sku_count=1),
            endpoint="/api/fulfillment/order/list",
            method="POST",
        ),
    )
    assert r.status_code == 200
    # plugin_logs 应有写入（level=info）
    from tts_erp_v2.db.base import get_session_factory
    Session = get_session_factory()
    with Session() as sess:
        row = sess.execute(
            __import__("sqlalchemy").text(
                "SELECT level, message, context FROM plugin.plugin_logs"
                " WHERE seller_id = :shop_id AND plugin_name = 'order-sync'"
                " ORDER BY occurred_at DESC LIMIT 1"
            ),
            {"shop_id": SHOP_ID},
        ).fetchone()
    assert row is not None, "plugin_logs 应有 order-sync 健康记录"
    assert row[0] == "info", "成功 dump 应是 info level"
    assert "domain=orders" in row[1]
    assert row[2]["rows_written"] >= 1


def test_dumps_health_counter_logs_failed_dump(api_client, readwrite_key):
    """AGENTS.md §2.5: parse_error dump 应写入 plugin_logs（level=warn，422）。"""
    # 用 logistics 域缺 mainOrderId 触发真 parse_error
    r = api_client.post(
        "/v2/order-sync/dumps",
        headers={"Authorization": f"Bearer {readwrite_key}"},
        json=_dump_payload(
            "logistics",
            _logistics_response("any"),
            # main_order_id=None → parse_error
            endpoint="/api/v1/fulfillment/logistic_detail/list",
        ),
    )
    assert r.status_code == 422
    from tts_erp_v2.db.base import get_session_factory
    Session = get_session_factory()
    with Session() as sess:
        row = sess.execute(
            __import__("sqlalchemy").text(
                "SELECT level, message, context FROM plugin.plugin_logs"
                " WHERE seller_id = :shop_id AND plugin_name = 'order-sync'"
                " ORDER BY occurred_at DESC LIMIT 1"
            ),
            {"shop_id": SHOP_ID},
        ).fetchone()
    assert row is not None, "plugin_logs 应有 order-sync 健康记录"
    assert row[0] == "warn", "parse_error dump 应是 warn level"
    assert "domain=logistics" in row[1]
    assert "parse_error=" in row[1]
    assert row[2]["rows_written"] == 0
    # spec §3.3 7 维度验证
    assert "captured_at" in row[2], "context 应含 captured_at 维度"


# ─── dumps: protocolVersion 必填 ──────────────────────────────────


def test_dumps_protocol_version_required(api_client, readwrite_key):
    """protocolVersion 缺失应返 400（Pydantic V2 校验 + SCHEMA_INVALID）。"""
    payload = _dump_payload(
        "orders",
        _order_response([ORDER_ID_1]),
        endpoint="/api/fulfillment/order/list",
        method="POST",
    )
    # 删除 protocolVersion 字段
    del payload["protocolVersion"]
    r = api_client.post(
        "/v2/order-sync/dumps",
        headers={"Authorization": f"Bearer {readwrite_key}"},
        json=payload,
    )
    assert r.status_code == 400  # Pydantic V2 校验 + SCHEMA_INVALID


def test_dumps_protocol_version_1_accepts(api_client, readwrite_key):
    """protocolVersion=1 应成功。"""
    payload = _dump_payload(
        "orders",
        _order_response([ORDER_ID_1]),
        endpoint="/api/fulfillment/order/list",
        method="POST",
    )
    assert payload["protocolVersion"] == 1
    r = api_client.post(
        "/v2/order-sync/dumps",
        headers={"Authorization": f"Bearer {readwrite_key}"},
        json=payload,
    )
    assert r.status_code == 200
    assert r.json()["code"] == 0
    assert r.json()["message"] == "success"


# ─── dumps: 幂等重放 ──────────────────────────────────────────────


def test_dumps_order_idempotent_replay(api_client, readwrite_key):
    """同 dump 重放：两次都返回 inserted（upsert 语义）。"""
    payload = _dump_payload(
        "orders",
        _order_response([ORDER_ID_1]),
        endpoint="/api/fulfillment/order/list",
        method="POST",
    )
    r1 = api_client.post(
        "/v2/order-sync/dumps",
        headers={"Authorization": f"Bearer {readwrite_key}"},
        json=payload,
    )
    r2 = api_client.post(
        "/v2/order-sync/dumps",
        headers={"Authorization": f"Bearer {readwrite_key}"},
        json=payload,
    )
    assert r1.json()["data"] == {}
    assert r2.json()["data"] == {}
    # 幂等：两次都返 200 + empty data（空 list + 空 data 不再当 parse_error）
    assert r1.status_code == 200
    assert r2.status_code == 200


# ─── dumps: 解析失败返回 422 PARSE_ERROR ────────────────────────────


def test_dumps_logistics_missing_main_order_id_returns_422(
    api_client, readwrite_key
):
    """AGENTS.md §2.5: logistics 域缺 mainOrderId → 422 PARSE_ERROR。"""
    r = api_client.post(
        "/v2/order-sync/dumps",
        headers={"Authorization": f"Bearer {readwrite_key}"},
        json=_dump_payload(
            "logistics",
            _logistics_response("any"),
            # main_order_id=None
            endpoint="/api/v1/fulfillment/logistic_detail/list",
        ),
    )
    assert r.status_code == 422
    body = r.json()
    assert body["code"] == "PARSE_ERROR"
    assert "mainOrderId is required" in body["message"]


def test_dumps_parse_failure_rolls_back_partial_business_rows(
    api_client, readwrite_key, db_engine, monkeypatch
):
    """解析器先写一行再报错时，业务表不可留下半个 dump。"""
    from datetime import UTC, datetime

    from tts_erp_v2.api.v2 import order_sync as order_sync_api
    from tts_erp_v2.plugin.orders.repository import upsert_order

    def _partially_write_then_fail(sess, **kwargs):
        upsert_order(
            sess,
            shop_id=kwargs["shop_id"],
            order_id=ORDER_ID_1,
        )
        raise RuntimeError("synthetic parser failure")

    monkeypatch.setattr(order_sync_api, "parse_order_response", _partially_write_then_fail)
    r = api_client.post(
        "/v2/order-sync/dumps",
        headers={"Authorization": f"Bearer {readwrite_key}"},
        json=_dump_payload("orders", _order_response([ORDER_ID_1])),
    )
    assert r.status_code == 422
    assert r.json()["code"] == "PARSE_ERROR"
    with db_engine.connect() as conn:
        order_count = conn.execute(
            text("SELECT count(*) FROM plugin.orders WHERE shop_id = :s"),
            {"s": SHOP_ID},
        ).scalar()
    assert order_count == 0
    # Phase 1: parse_error 不再落 raw_log，由 response JSON 验证（上一行 r.json()['data']['parseError']）


# ─── dumps: 400 schema invalid ─────────────────────────────────────


def test_dumps_400_on_invalid_domain(api_client, readwrite_key):
    r = api_client.post(
        "/v2/order-sync/dumps",
        headers={"Authorization": f"Bearer {readwrite_key}"},
        json=_dump_payload("invalid_domain", {}),
    )
    assert r.status_code == 400
    assert r.json()["code"] == "SCHEMA_INVALID"


def test_dumps_400_on_malformed_json(api_client, readwrite_key):
    r = api_client.post(
        "/v2/order-sync/dumps",
        headers={
            "Authorization": f"Bearer {readwrite_key}",
            "content-type": "application/json",
        },
        content=b"not-json",
    )
    assert r.status_code == 400
    assert r.json()["code"] == "MALFORMED_JSON"


# ─── reconcile: 空库返回空状态 ─────────────────────────────────────


def test_reconcile_empty_db_returns_empty_orders_and_logistics(
    api_client, readwrite_key
):
    r = api_client.post(
        "/v2/order-sync/reconcile",
        headers={"Authorization": f"Bearer {readwrite_key}"},
        json={
            "protocolVersion": 1,
            "scope": {"sellerId": SHOP_ID, "shopId": SHOP_ID},
            "domains": ["orders", "logistics"],
            "orders": {"anchorPositions": [0]},
            "logistics": {"limit": 10},
        },
    )
    assert r.status_code == 200
    body = r.json()
    assert body["code"] == 0
    assert body["data"]["orders"]["serverTotal"] == 0
    assert body["data"]["orders"]["anchors"] == []
    assert body["data"]["logistics"]["complete"] is True
    assert body["data"]["logistics"]["items"] == []
    assert body["data"]["logistics"]["nextCursor"] is None


# ─── reconcile: 订单锚点与物流终态 ─────────────────────────────────


def test_reconcile_returns_order_anchors_and_terminal_logistics(
    api_client, readwrite_key
):
    api_client.post(
        "/v2/order-sync/dumps",
        headers={"Authorization": f"Bearer {readwrite_key}"},
        json=_dump_payload(
            "orders",
            _order_response([ORDER_ID_1, ORDER_ID_2]),
            endpoint="/api/fulfillment/order/list",
            method="POST",
        ),
    )
    api_client.post(
        "/v2/order-sync/dumps",
        headers={"Authorization": f"Bearer {readwrite_key}"},
        json=_dump_payload(
            "logistics",
            _logistics_response(LOGISTICS_ORDER_ID),
            main_order_id=LOGISTICS_ORDER_ID,
            endpoint="/api/v1/fulfillment/logistic_detail/list",
        ),
    )

    r = api_client.post(
        "/v2/order-sync/reconcile",
        headers={"Authorization": f"Bearer {readwrite_key}"},
        json={
            "protocolVersion": 1,
            "scope": {"sellerId": SHOP_ID, "shopId": SHOP_ID},
            "domains": ["orders", "logistics"],
            "orders": {
                "sortInfo": "6",
                "anchorPositions": [0, 1, 99],
                "hotWindowSize": 40,
            },
            "logistics": {"limit": 10},
        },
    )
    assert r.status_code == 200
    body = r.json()
    orders = body["data"]["orders"]
    assert orders["serverTotal"] == 2
    assert [anchor["position"] for anchor in orders["anchors"]] == [0, 1]
    assert {anchor["orderId"] for anchor in orders["anchors"]} == {
        ORDER_ID_1,
        ORDER_ID_2,
    }
    assert orders["offsetSafe"] is False  # fixture deliberately has no order_time

    logistics = body["data"]["logistics"]
    terminal_item = next(
        item for item in logistics["items"] if item["orderId"] == LOGISTICS_ORDER_ID
    )
    assert terminal_item["isTerminal"] is True
    assert terminal_item["packageIds"] == [f"{LOGISTICS_ORDER_ID}_pkg_0"]
    assert terminal_item["terminalReason"] == "Delivered"


def test_reconcile_rejects_unknown_domain(api_client, readwrite_key):
    r = api_client.post(
        "/v2/order-sync/reconcile",
        headers={"Authorization": f"Bearer {readwrite_key}"},
        json={
            "protocolVersion": 1,
            "scope": {"sellerId": SHOP_ID, "shopId": SHOP_ID},
            "domains": ["statements"],
        },
    )
    assert r.status_code == 400
    assert r.json()["code"] == "SCHEMA_INVALID"


# ─── has-data: 400 invalid domain ──────────────────────────────────


def test_has_data_400_on_invalid_domain(api_client, readwrite_key):
    r = api_client.post(
        "/v2/order-sync/has-data",
        headers={"Authorization": f"Bearer {readwrite_key}"},
        json={
            "scope": {"sellerId": SHOP_ID, "shopId": SHOP_ID},
            "domain": "invalid",
            "ids": ["x"],
        },
    )
    assert r.status_code == 400
    assert r.json()["code"] == "SCHEMA_INVALID"
