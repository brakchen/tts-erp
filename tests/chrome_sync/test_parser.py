"""chrome_sync 解析层单测。

覆盖 parse_order_response / parse_logistics_response /
parse_statement_list_response / parse_statement_transaction_response /
flatten_fees。
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from tts_erp_v2.chrome_sync.parser import (
    flatten_fees,
    parse_logistics_response,
    parse_order_response,
    parse_statement_list_response,
    parse_statement_transaction_response,
)
from tts_erp_v2.chrome_sync.repository import write_raw_log
from tts_erp_v2.db.base import get_engine

pytestmark = [pytest.mark.domain_api, pytest.mark.layer_integration]

SHOP_ID = "TEST_parser-shop"

_CLEANUP_SQLS = [
    "DELETE FROM chrome_sync.tracking_events WHERE shop_id = :s",
    "DELETE FROM chrome_sync.order_lines WHERE shop_id = :s",
    "DELETE FROM chrome_sync.settlement_details WHERE shop_id = :s",
    "DELETE FROM chrome_sync.settlements WHERE shop_id = :s",
    "DELETE FROM chrome_sync.shipments WHERE shop_id = :s",
    "DELETE FROM chrome_sync.orders WHERE shop_id = :s",
    "DELETE FROM chrome_sync.raw_log WHERE shop_id = :s",
]


def _make_log_id(sess: Session) -> int:
    """创建一条 raw_log 并返回 log_id（供 FK 引用）。"""
    return write_raw_log(
        sess,
        domain="test",
        shop_id=SHOP_ID,
        endpoint="/test",
        captured_at=datetime.now(UTC),
        request_params=None,
        request_body=None,
        response_body={"test": True},
        parse_error=None,
        rows_written=0,
    )


@pytest.fixture(autouse=True)
def _cleanup(db_engine):
    params = {"s": SHOP_ID}
    with db_engine.begin() as conn:
        for stmt in _CLEANUP_SQLS:
            # noqa: python-sql-injection — 字面量 SQL, bind :s
            conn.execute(text(stmt), params)
    yield
    with db_engine.begin() as conn:
        for stmt in _CLEANUP_SQLS:
            # noqa: python-sql-injection — 字面量 SQL, bind :s
            conn.execute(text(stmt), params)


# ─── flatten_fees ───────────────────────────────────────────────────


class TestFlattenFees:
    def test_empty(self):
        assert flatten_fees(None) == []
        assert flatten_fees([]) == []

    def test_flat_list(self):
        fees = [
            {"type": "GROSS_SALES", "amount": {"amount": "100", "currency": "VND"}},
            {"type": "REFUND", "amount": {"amount": "0", "currency": "VND"}},
        ]
        result = flatten_fees(fees)
        assert len(result) == 2
        assert result[0]["code"] == "GROSS_SALES"
        assert result[0]["amount"] == "100"

    def test_nested_sub_fees(self):
        fees = [
            {
                "type": "PLATFORM_COMMISSION",
                "amount": {"amount": "8000", "currency": "VND"},
                "sub_fees": [
                    {"type": "COMMISSION_TAX", "amount": {"amount": "2000", "currency": "VND"}},
                ],
            },
        ]
        result = flatten_fees(fees)
        assert len(result) == 2
        assert result[0]["code"] == "PLATFORM_COMMISSION"
        assert result[1]["code"] == "COMMISSION_TAX"

    def test_deeply_nested(self):
        fees = [
            {
                "type": "A",
                "amount": {"amount": "1", "currency": "VND"},
                "sub_fees": [
                    {
                        "type": "B",
                        "amount": {"amount": "2", "currency": "VND"},
                        "sub_fees": [
                            {"type": "C", "amount": {"amount": "3", "currency": "VND"}},
                        ],
                    },
                ],
            },
        ]
        result = flatten_fees(fees)
        assert len(result) == 3
        assert [r["code"] for r in result] == ["A", "B", "C"]


# ─── parse_order_response ───────────────────────────────────────────


class TestParseOrderResponse:
    def _make_response(self, orders: list[dict]) -> dict:
        return {"code": 0, "data": {"main_orders": orders}}

    def test_basic_parse(self):
        eng = get_engine()
        with Session(eng) as sess:
            log_id = _make_log_id(sess)
            resp = self._make_response([
                {
                    "main_order_id": "TEST_ord-1",
                    "order_status_module": {"order_status": "DELIVERED"},
                    "price_module": {
                        "payment": {"amount": "299000", "currency": "VND"},
                        "total_amount": {"amount": "329000", "currency": "VND"},
                    },
                    "sku_module": [
                        {
                            "sku_id": "sku-1",
                            "product_id": "prod-1",
                            "product_name": "Widget",
                            "quantity": 2,
                            "sale_price": {"amount": "149500", "currency": "VND"},
                        },
                    ],
                }
            ])
            rows = parse_order_response(
                sess, log_id=log_id, shop_id=SHOP_ID,
                response_body=resp, captured_at=datetime.now(UTC),
            )
            sess.commit()
            assert rows == 2  # 1 order + 1 line

    def test_empty_main_orders(self):
        eng = get_engine()
        with Session(eng) as sess:
            log_id = _make_log_id(sess)
            rows = parse_order_response(
                sess, log_id=log_id, shop_id=SHOP_ID,
                response_body={"code": 0, "data": {"main_orders": []}},
                captured_at=datetime.now(UTC),
            )
            assert rows == 0

    def test_sku_dedup(self):
        """同 sku_id 出现两次只写一次。"""
        eng = get_engine()
        with Session(eng) as sess:
            log_id = _make_log_id(sess)
            resp = self._make_response([
                {
                    "main_order_id": "TEST_ord-dedup",
                    "sku_module": [
                        {"sku_id": "sku-dup", "product_id": "p1"},
                        {"sku_id": "sku-dup", "product_id": "p1"},
                    ],
                }
            ])
            rows = parse_order_response(
                sess, log_id=log_id, shop_id=SHOP_ID,
                response_body=resp, captured_at=datetime.now(UTC),
            )
            sess.commit()
            assert rows == 2  # 1 order + 1 line (deduped)


# ─── parse_logistics_response ──────────────────────────────────────


class TestParseLogisticsResponse:
    def test_multi_package(self):
        eng = get_engine()
        with Session(eng) as sess:
            log_id = _make_log_id(sess)
            resp = {
                "code": 0,
                "data": {
                    "package_list": [
                        {
                            "main_order_id": "TEST_ord-log-1",
                            "package_id": "pkg-1",
                            "tracking_no": "TN1",
                            "logistic_supplier": "VNPost",
                            "logistic_detail": {
                                "track_list": [
                                    {"time": "2026-09-01T10:00:00Z", "track_status": "Picked up"},
                                    {"time": "2026-09-02T15:00:00Z", "track_status": "Delivered"},
                                ]
                            },
                        },
                        {
                            "main_order_id": "TEST_ord-log-1",
                            "package_id": "pkg-2",
                            "tracking_no": "TN2",
                            "logistic_supplier": "GHN",
                            "logistic_detail": {
                                "track_list": [
                                    {"time": "2026-09-03T08:00:00Z", "track_status": "In transit"},
                                ]
                            },
                        },
                    ]
                },
            }
            rows = parse_logistics_response(
                sess, log_id=log_id, shop_id=SHOP_ID,
                order_id="TEST_ord-log-1",
                response_body=resp, captured_at=datetime.now(UTC),
            )
            sess.commit()
            assert rows == 5  # 2 shipments + 3 tracking events

    def test_empty_track_list(self):
        eng = get_engine()
        with Session(eng) as sess:
            log_id = _make_log_id(sess)
            resp = {
                "code": 0,
                "data": {
                    "package_list": [
                        {
                            "main_order_id": "TEST_ord-ntrl",
                            "package_id": "pkg-ntrl",
                            "logistic_detail": {"track_list": []},
                        }
                    ]
                },
            }
            rows = parse_logistics_response(
                sess, log_id=log_id, shop_id=SHOP_ID,
                order_id="TEST_ord-ntrl",
                response_body=resp, captured_at=datetime.now(UTC),
            )
            sess.commit()
            assert rows == 1  # 1 shipment, 0 events


# ─── parse_statement_list_response ─────────────────────────────────


class TestParseStatementListResponse:
    def test_basic(self):
        eng = get_engine()
        with Session(eng) as sess:
            log_id = _make_log_id(sess)
            resp = {
                "code": 0,
                "data": {
                    "statement_records": [
                        {
                            "statement_id": "TEST_stmt-1",
                            "statement_version": 0,
                            "bill_period": "2026-09-01~2026-09-07",
                            "settlement_time": "2026-09-08T00:00:00Z",
                            "payment_id": "pay-1",
                            "payment_status": 2,
                            "settle_amount": {"amount": "1000000", "currency": "VND"},
                            "earning_amount": {"amount": "900000", "currency": "VND"},
                            "fee_amount": {"amount": "100000", "currency": "VND"},
                            "adjust_amount": {"amount": "0", "currency": "VND"},
                            "payable_amount": {"amount": "900000", "currency": "VND"},
                            "shipping_amount": {"amount": "50000", "currency": "VND"},
                            "total_reserve_amount": {"amount": "0", "currency": "VND"},
                        }
                    ]
                },
            }
            rows = parse_statement_list_response(
                sess, log_id=log_id, shop_id=SHOP_ID,
                response_body=resp, captured_at=datetime.now(UTC),
            )
            sess.commit()
            assert rows == 1


# ─── parse_statement_transaction_response ──────────────────────────


class TestParseStatementTransactionResponse:
    def test_basic(self):
        eng = get_engine()
        with Session(eng) as sess:
            log_id = _make_log_id(sess)
            resp = {
                "code": 0,
                "data": {
                    "sku_record": {
                        "statement_sku_detail_id": "TEST_ssd-1",
                        "statement_id": "TEST_stmt-tx-1",
                        "statement_version": 0,
                        "trade_order_id": "TO-1",
                        "sku_id": "SKU-1",
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
            rows = parse_statement_transaction_response(
                sess, log_id=log_id, shop_id=SHOP_ID,
                response_body=resp, captured_at=datetime.now(UTC),
            )
            sess.commit()
            assert rows == 1


# ─── JSON 可序列化 ─────────────────────────────────────────────────


def test_flatten_fees_result_is_json_serializable():
    fees = [
        {"type": "A", "amount": {"amount": "1", "currency": "VND"}, "sub_fees": [
            {"type": "B", "amount": {"amount": "2", "currency": "VND"}},
        ]},
    ]
    result = flatten_fees(fees)
    json.dumps(result)  # must not raise
