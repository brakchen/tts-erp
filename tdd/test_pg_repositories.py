"""TDD test suite for Pg*Repository implementations.

These wrap the existing persist_* functions in tts_erp.py (which contain
the SQL we don't want to retest here). The tests verify the wrapper
contract:
- upsert() returns True on successful write
- upsert() returns False when ID is missing
- The row actually lands in PG

Cleanup: conftest._cleanup_test_shop_ids deletes all shop_id LIKE 'TEST_%'
rows at session end.
"""
from __future__ import annotations

import pytest

from domain import Creds


SENTINEL = "TEST_PG_REPO"


class TestPgOrderRepository:
    def test_upsert_returns_true_with_valid_id(self, db_cursor):
        from pg_repositories import PgOrderRepository
        repo = PgOrderRepository()
        ok = repo.upsert(SENTINEL + "_ORD_OK", {
            "id": "test-order-1",
            "status": "AWAITING_SHIPMENT",
            "create_time": 1_700_000_000,
        })
        assert ok is True

        db_cursor.execute(
            "SELECT order_id, shop_id, order_status_name FROM orders WHERE order_id = %s",
            ("test-order-1",),
        )
        row = db_cursor.fetchone()
        assert row is not None
        assert row[0] == "test-order-1"
        assert row[1] == SENTINEL + "_ORD_OK"
        assert row[2] == "AWAITING_SHIPMENT"

    def test_upsert_returns_false_when_id_missing(self):
        from pg_repositories import PgOrderRepository
        repo = PgOrderRepository()
        ok = repo.upsert(SENTINEL + "_ORD_NOID", {
            "status": "PENDING",
            # no "id" or "order_id" key
        })
        assert ok is False

    def test_upsert_persists_line_items(self, db_cursor):
        from pg_repositories import PgOrderRepository
        repo = PgOrderRepository()
        repo.upsert(SENTINEL + "_ORD_ITEMS", {
            "id": "test-order-with-items",
            "status": "AWAITING_SHIPMENT",
            "line_items": [
                {"id": "li-1", "sku_id": "SKU-A", "quantity": 2, "sale_price": "10.00"},
                {"id": "li-2", "sku_id": "SKU-B", "quantity": 1, "sale_price": "20.00"},
            ],
        })

        db_cursor.execute(
            "SELECT count(*) FROM order_items WHERE order_id = %s",
            ("test-order-with-items",),
        )
        assert db_cursor.fetchone()[0] == 2

    def test_upsert_is_idempotent(self, db_cursor):
        from pg_repositories import PgOrderRepository
        repo = PgOrderRepository()
        for i in range(3):
            ok = repo.upsert(SENTINEL + "_ORD_IDEMP", {
                "id": "test-order-idemp",
                "status": f"STATUS-{i}",
            })
            assert ok is True
        # Same row, only one entry
        db_cursor.execute(
            "SELECT count(*) FROM orders WHERE order_id = %s",
            ("test-order-idemp",),
        )
        assert db_cursor.fetchone()[0] == 1
        # Last write wins
        db_cursor.execute(
            "SELECT order_status_name FROM orders WHERE order_id = %s",
            ("test-order-idemp",),
        )
        assert db_cursor.fetchone()[0] == "STATUS-2"


class TestPgPaymentRepository:
    def test_upsert_returns_true_with_valid_id(self, db_cursor):
        from pg_repositories import PgPaymentRepository
        repo = PgPaymentRepository()
        ok = repo.upsert(SENTINEL + "_PAY_OK", {
            "id": "test-pay-1",
            "amount": "100.00",
            "currency": "VND",
        })
        assert ok is True
        db_cursor.execute(
            "SELECT payment_id, currency FROM payments WHERE payment_id = %s",
            ("test-pay-1",),
        )
        row = db_cursor.fetchone()
        assert row[0] == "test-pay-1"

    def test_upsert_returns_false_when_id_missing(self):
        from pg_repositories import PgPaymentRepository
        repo = PgPaymentRepository()
        ok = repo.upsert(SENTINEL + "_PAY_NOID", {"amount": "0"})
        assert ok is False


class TestPgStatementRepository:
    def test_upsert_returns_true(self, db_cursor):
        from pg_repositories import PgStatementRepository
        repo = PgStatementRepository()
        ok = repo.upsert(SENTINEL + "_ST_OK", {
            "id": "test-stmt-1",
            "currency": "VND",
        })
        assert ok is True
        db_cursor.execute(
            "SELECT statement_id FROM statements WHERE statement_id = %s",
            ("test-stmt-1",),
        )
        assert db_cursor.fetchone() is not None

    def test_upsert_returns_false_when_id_missing(self):
        from pg_repositories import PgStatementRepository
        repo = PgStatementRepository()
        ok = repo.upsert(SENTINEL + "_ST_NOID", {"amount": "0"})
        assert ok is False


class TestPgReturnRepository:
    def test_upsert_returns_true(self, db_cursor):
        from pg_repositories import PgReturnRepository
        repo = PgReturnRepository()
        ok = repo.upsert(SENTINEL + "_RET_OK", {
            "id": "test-ret-1",
            "status": "AWAITING_RETURN",
        })
        assert ok is True
        db_cursor.execute(
            "SELECT return_id FROM returns WHERE return_id = %s",
            ("test-ret-1",),
        )
        assert db_cursor.fetchone() is not None

    def test_upsert_returns_false_when_id_missing(self):
        from pg_repositories import PgReturnRepository
        repo = PgReturnRepository()
        ok = repo.upsert(SENTINEL + "_RET_NOID", {"status": "PENDING"})
        assert ok is False


class TestPgCancellationRepository:
    def test_upsert_returns_true(self, db_cursor):
        from pg_repositories import PgCancellationRepository
        repo = PgCancellationRepository()
        ok = repo.upsert(SENTINEL + "_CAN_OK", {
            "id": "test-can-1",
            "status": "PENDING",
        })
        assert ok is True
        db_cursor.execute(
            "SELECT cancel_id FROM cancellations WHERE cancel_id = %s",
            ("test-can-1",),
        )
        assert db_cursor.fetchone() is not None

    def test_upsert_returns_false_when_id_missing(self):
        from pg_repositories import PgCancellationRepository
        repo = PgCancellationRepository()
        ok = repo.upsert(SENTINEL + "_CAN_NOID", {"status": "PENDING"})
        assert ok is False


class TestFactory:
    def test_make_pg_repos_returns_all_repos(self):
        from pg_repositories import make_pg_repos
        repos = make_pg_repos("postgresql://test")
        # Check all 5 repo types are present
        assert hasattr(repos["orders"], "upsert")
        assert hasattr(repos["payments"], "upsert")
        assert hasattr(repos["statements"], "upsert")
        assert hasattr(repos["returns"], "upsert")
        assert hasattr(repos["cancellations"], "upsert")
