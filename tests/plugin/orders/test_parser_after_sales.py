"""plugin.after_sales + after_sale_items 解析器测试。

lane feat/after-sales-table (2026-09-13)：response 结构基于
tech-doc/order-domain-business-rules.md §3 描述（无 chrome 端真实样本，
0 hit 数据），用 mock response 跑 upsert 路径覆盖。
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import text

pytestmark = [pytest.mark.domain_plugin, pytest.mark.layer_integration]

SHOP = "TEST_shop-after-sales"
LOG_PREFIX = "TEST_log-"


@pytest.fixture(autouse=True)
def _cleanup(db_engine):
    yield
    with db_engine.begin() as conn:
        conn.execute(
            text(
                "DELETE FROM plugin.after_sale_items WHERE shop_id LIKE 'TEST_%' OR shop_id = :sid"
            ),
            {"sid": SHOP},
        )
        conn.execute(
            text(
                "DELETE FROM plugin.after_sales WHERE shop_id LIKE 'TEST_%' OR shop_id = :sid"
            ),
            {"sid": SHOP},
        )


def test_parse_after_sales_full_response(db_engine, db_session):
    """完整 /return_refund/.../cancellations/search 响应：1 个 cancel + 2 个 line items。"""
    from tts_erp_v2.plugin.orders.parser import parse_after_sales_response

    captured_at = datetime.now(UTC)

    response_body = {
        "code": 0,
        "message": "success",
        "data": {
            "cancellations": [
                {
                    "cancel_id": "TEST-cancel-001",
                    "cancel_type": "BUYER_CANCEL",
                    "cancel_status": "CANCELLATION_REQUEST_COMPLETE",
                    "order_id": "TEST-order-001",
                    "reason": "不想要了",
                    "request_time": 1789293723000,
                    "complete_time": 1789293723500,
                    "cancel_line_items": [
                        {
                            "id": "TEST-line-001",
                            "order_line_item_id": "TEST-orderline-001",
                            "sku_id": "TEST-sku-001",
                            "product_id": "TEST-prod-001",
                            "quantity": 1,
                            "refund_amount": {"amount": "577523", "currency": "VND"},
                        },
                        {
                            "id": "TEST-line-002",
                            "order_line_item_id": "TEST-orderline-002",
                            "sku_id": "TEST-sku-002",
                            "product_id": "TEST-prod-002",
                            "quantity": 2,
                            "refund_amount": {"amount": "1100000", "currency": "VND"},
                        },
                    ],
                }
            ]
        },
    }

    rows = parse_after_sales_response(
        db_session,
        shop_id=SHOP,
        response_body=response_body,
        captured_at=captured_at,
    )
    db_session.flush()
    db_session.commit()

    # 1 cancel header + 2 line items = 3 rows
    assert rows == 3, f"expected 3 rows (1 header + 2 items), got {rows}"

    # db_session 用 savepoint 模式，独立 db_engine 看不到；用 db_session 自己查
    cancel = db_session.execute(
        text(
            "SELECT cancel_id, cancel_type, cancel_status, main_order_id, reason, "
            "       request_time, complete_time, raw_payload "
            "FROM plugin.after_sales WHERE shop_id = :sid"
        ),
        {"sid": SHOP},
    ).fetchone()
    assert cancel is not None
    assert cancel[0] == "TEST-cancel-001"
    assert cancel[1] == "BUYER_CANCEL"
    assert cancel[2] == "CANCELLATION_REQUEST_COMPLETE"
    assert cancel[3] == "TEST-order-001"
    assert cancel[4] == "不想要了"
    assert cancel[5] is not None
    assert cancel[6] is not None
    assert cancel[7] is not None

    items = db_session.execute(
        text(
            "SELECT line_item_id, order_line_item_id, sku_id, product_id, quantity, "
            "       refund_amount, currency "
            "FROM plugin.after_sale_items WHERE shop_id = :sid ORDER BY line_item_id"
        ),
        {"sid": SHOP},
    ).fetchall()
    assert len(items) == 2
    assert items[0][0] == "TEST-line-001"
    assert items[0][1] == "TEST-orderline-001"
    assert items[0][5] is not None
    assert items[0][6] == "VND"
    assert items[1][0] == "TEST-line-002"
    from decimal import Decimal
    assert items[1][4] == Decimal("2")  # quantity 2 (Numeric(20,4) → Decimal)


def test_parse_after_sales_no_line_items(db_engine, db_session):
    """cancel_line_items 缺失时：只写 header，0 line items。"""
    from tts_erp_v2.plugin.orders.parser import parse_after_sales_response

    response_body = {
        "code": 0,
        "data": {
            "cancellations": [
                {
                    "cancel_id": "TEST-cancel-no-items",
                    "cancel_type": "CANCEL",
                    "cancel_status": "CANCELLATION_REQUEST_COMPLETE",
                    "order_id": "TEST-order-002",
                    "cancel_line_items": [],
                }
            ]
        },
    }
    rows = parse_after_sales_response(
        db_session,
        shop_id=SHOP,
        response_body=response_body,
        captured_at=datetime.now(UTC),
    )
    db_session.flush()
    db_session.commit()
    assert rows == 1, f"expected 1 row (header only), got {rows}"


def test_parse_after_sales_missing_cancel_id_is_skipped(
    db_engine, db_session
):
    """cancel_id 缺失的记录跳过，warnings 但不抛异常。"""
    from tts_erp_v2.plugin.orders.parser import parse_after_sales_response

    response_body = {
        "code": 0,
        "data": {
            "cancellations": [
                {"cancel_type": "BUYER_CANCEL", "cancel_status": "PENDING"},  # 无 cancel_id
                {
                    "cancel_id": "TEST-cancel-good",
                    "cancel_type": "CANCEL",
                    "cancel_status": "CANCELLATION_REQUEST_COMPLETE",
                    "order_id": "TEST-order-003",
                },
            ]
        },
    }
    rows = parse_after_sales_response(
        db_session,
        shop_id=SHOP,
        response_body=response_body,
        captured_at=datetime.now(UTC),
    )
    db_session.flush()
    db_session.commit()
    # 1 个有效 cancel = 1 行
    assert rows == 1


def test_parse_after_sales_upsert_idempotent(db_engine, db_session):
    """同 cancel_id 二次解析：update 不 insert，rows_written 仍报 1（仅 1 header）。"""
    from tts_erp_v2.plugin.orders.parser import parse_after_sales_response

    response_body = {
        "code": 0,
        "data": {
            "cancellations": [
                {
                    "cancel_id": "TEST-cancel-idem",
                    "cancel_type": "BUYER_CANCEL",
                    "cancel_status": "PENDING",
                    "order_id": "TEST-order-004",
                }
            ]
        },
    }
    # 第 1 次：PENDING
    rows1 = parse_after_sales_response(
        db_session,
        shop_id=SHOP,
        response_body=response_body,
        captured_at=datetime.now(UTC),
    )
    db_session.commit()

    # 改 status 重跑：COMPLETE
    response_body["data"]["cancellations"][0]["cancel_status"] = "CANCELLATION_REQUEST_COMPLETE"
    db_session.expire_all()  # 让 PG INSERT ... ON CONFLICT 重新评估
    rows2 = parse_after_sales_response(
        db_session,
        shop_id=SHOP,
        response_body=response_body,
        captured_at=datetime.now(UTC),
    )
    db_session.flush()
    db_session.commit()

    # 都是 1 行（header），但 status 已更新
    assert rows1 == 1
    assert rows2 == 1
    result = db_session.execute(
        text(
            "SELECT cancel_status, updated_at FROM plugin.after_sales "
            "WHERE shop_id = :sid"
        ),
        {"sid": SHOP},
    ).fetchone()
    assert result[0] == "CANCELLATION_REQUEST_COMPLETE"
