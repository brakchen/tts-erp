"""TDD contract tests: /v2/order-sync/* — Chrome 扩展订单/物流/结算数据同步。

端点契约：
1. POST /v2/order-sync/has-data — 批量查业务表存在性
2. POST /v2/order-sync/dumps — 接收 dump → inline 解析 → 写业务表 + raw_log
3. GET  /v2/order-sync/synced-ids — 查询已同步 id 列表

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
    "DELETE FROM plugin.raw_log WHERE shop_id = :s",
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
    response_body: dict,
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


def test_synced_ids_anonymous_is_401(api_client):
    r = api_client.get(
        "/v2/order-sync/synced-ids",
        params={"shopId": SHOP_ID, "domain": "orders"},
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


def test_synced_ids_readonly_is_403(api_client, readonly_key):
    r = api_client.get(
        "/v2/order-sync/synced-ids",
        headers={"Authorization": f"Bearer {readonly_key}"},
        params={"shopId": SHOP_ID, "domain": "orders"},
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
    assert body["data"]["status"] == "inserted"
    assert body["data"]["rowsWritten"] == 3  # 1 order + 2 lines
    assert body["data"]["logId"] > 0


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
    assert body["data"]["status"] == "inserted"
    assert body["data"]["rowsWritten"] == 3  # 1 shipment + 2 tracking events


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
    assert body["data"]["status"] == "inserted"
    assert body["data"]["rowsWritten"] == 2


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
    assert body["data"]["status"] == "inserted"
    assert body["data"]["rowsWritten"] == 1


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
    assert r1.json()["data"]["status"] == "inserted"
    assert r2.json()["data"]["status"] == "inserted"
    # logId 不同（raw_log 每次都写）
    assert r1.json()["data"]["logId"] != r2.json()["data"]["logId"]


# ─── dumps: 解析失败返回 parse_error ──────────────────────────────


def test_dumps_logistics_missing_main_order_id_returns_parse_error(
    api_client, readwrite_key
):
    """logistics 域缺 mainOrderId → 解析失败。"""
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
    assert r.status_code == 200
    body = r.json()
    assert body["data"]["status"] == "parse_error"
    assert "mainOrderId is required" in body["data"]["parseError"]
    assert body["data"]["rowsWritten"] == 0


# ─── dumps: 400 schema invalid ─────────────────────────────────────


def test_dumps_400_on_invalid_domain(api_client, readwrite_key):
    r = api_client.post(
        "/v2/order-sync/dumps",
        headers={"Authorization": f"Bearer {readwrite_key}"},
        json=_dump_payload("invalid_domain", {}),
    )
    assert r.status_code == 400
    assert r.json()["code"] == "SCHEMA_INVALID"


# ─── api-managed 守卫：data_source='api' 的店铺停止插件写入 ──────────


def _seed_shop_data_source(db_engine, data_source: str) -> None:
    """给 TEST_shop-001 种一行 shops（指定 data_source）。

    conftest 的 TEST_ shops wipe 负责清理。ON CONFLICT 兼容同一测试
    会话里已存在的行。"""
    with db_engine.begin() as conn:
        # pi-lens-ignore: python-sql-injection — 字面量 SQL + 绑定参数
        conn.execute(
            text(
                "INSERT INTO commerce.shops "
                "(platform, shop_id, account_name, status, data_source) "
                "VALUES ('tiktok', :s, 'TEST shop', 'active', :ds) "
                "ON CONFLICT (platform, shop_id) DO UPDATE SET data_source = :ds"
            ),
            {"s": SHOP_ID, "ds": data_source},
        )


def test_dumps_api_managed_shop_is_ignored(api_client, readwrite_key, db_engine):
    """data_source='api' 的店铺：插件 dump 静默忽略（200 + api_managed），
    raw_log / 业务表都不写 —— 防订单/物流/结算域 API 与插件双写。"""
    _seed_shop_data_source(db_engine, "api")
    r = api_client.post(
        "/v2/order-sync/dumps",
        headers={"Authorization": f"Bearer {readwrite_key}"},
        json=_dump_payload(
            "orders",
            _order_response([ORDER_ID_1]),
            endpoint="/api/fulfillment/order/list",
            method="POST",
        ),
    )
    assert r.status_code == 200, r.text
    assert r.json()["data"]["status"] == "api_managed"
    with db_engine.connect() as conn:
        # pi-lens-ignore: python-sql-injection — 字面量 SQL + 绑定参数
        n_log = conn.execute(
            text("SELECT count(*) FROM plugin.raw_log WHERE shop_id = :s"),
            {"s": SHOP_ID},
        ).scalar_one()
        # pi-lens-ignore: python-sql-injection — 字面量 SQL + 绑定参数
        n_order = conn.execute(
            text("SELECT count(*) FROM plugin.orders WHERE shop_id = :s"),
            {"s": SHOP_ID},
        ).scalar_one()
    assert n_log == 0, "api_managed 店铺不得写 raw_log"
    assert n_order == 0, "api_managed 店铺不得写业务表"


def test_dumps_plugin_registered_shop_still_written(
    api_client, readwrite_key, db_engine
):
    """对照组：同店 data_source='plugin'（人工注册）时 dump 照常写入。"""
    _seed_shop_data_source(db_engine, "plugin")
    r = api_client.post(
        "/v2/order-sync/dumps",
        headers={"Authorization": f"Bearer {readwrite_key}"},
        json=_dump_payload(
            "orders",
            _order_response([ORDER_ID_1]),
            endpoint="/api/fulfillment/order/list",
            method="POST",
        ),
    )
    assert r.status_code == 200, r.text
    assert r.json()["data"]["status"] == "inserted"


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


# ─── synced-ids: 空库空列表 ────────────────────────────────────────


def test_synced_ids_empty_db_returns_empty(api_client, readwrite_key):
    r = api_client.get(
        "/v2/order-sync/synced-ids",
        headers={"Authorization": f"Bearer {readwrite_key}"},
        params={"shopId": SHOP_ID, "domain": "orders"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["code"] == 0
    assert body["data"]["ids"] == []
    assert body["data"]["total"] == 0


# ─── synced-ids: 有数据返回 ────────────────────────────────────────


def test_synced_ids_returns_inserted_orders(api_client, readwrite_key):
    # 写入2个订单
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
    r = api_client.get(
        "/v2/order-sync/synced-ids",
        headers={"Authorization": f"Bearer {readwrite_key}"},
        params={"shopId": SHOP_ID, "domain": "orders"},
    )
    body = r.json()
    assert body["data"]["total"] == 2
    assert set(body["data"]["ids"]) == {ORDER_ID_1, ORDER_ID_2}


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
