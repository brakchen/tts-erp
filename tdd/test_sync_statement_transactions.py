"""TDD: statement_transactions 落库逻辑（persist_statement_transaction）。

数据源：GET /finance/202309/statements/{statement_id}/statement_transactions
→ data.statement_transactions[]（58 字段/条，金额字段是字符串数字、费用为负值）。

这是替代 Excel financial_lines + fee_lines 的接口数据源。
"""
from __future__ import annotations

import psycopg
import pytest

import tts_erp

SHOP_ID = "TEST_SHOP_STMT_TXN"
STMT_ID = "TEST_STMT_TXN_001"

# 真实响应的最小形态（其余 40+ 金额字段缺失 → 应落 NULL）
TXN = {
    "id": "TEST_TXN_001",
    "order_id": "TEST_ORDER_001",
    "order_create_time": 1786048444,
    "type": "ORDER",
    "currency": "VND",
    "customer_payment_amount": "524744",
    "platform_commission_amount": "-18674.59",
    "iva_vat_amount": "-1110.25",
    "actual_shipping_fee_amount": "-46600",
    "settlement_amount": "470360",
    "fee_amount": "-34600",
    "revenue_amount": "524744",
    "net_sales_amount": "524744",
    "seller_discount_amount": "-356495",
}


@pytest.fixture(autouse=True)
def _cleanup(db_url: str):
    """persist_* 自己开连接 commit，事务回滚隔离不住，测试后按 sentinel 清掉。"""
    yield
    conn = psycopg.connect(db_url)
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM statement_transactions WHERE txn_id LIKE 'TEST_TXN_%'")
        conn.commit()
    finally:
        conn.close()


def _fetch(db_url: str, txn_id: str) -> dict | None:
    conn = psycopg.connect(db_url)
    try:
        with conn.cursor(row_factory=psycopg.rows.dict_row) as cur:
            cur.execute("SELECT * FROM statement_transactions WHERE txn_id = %s", (txn_id,))
            row = cur.fetchone()
    finally:
        conn.close()
    return dict(row) if row else None


def test_persist_insert_full_row(db_url: str):
    n = tts_erp.persist_statement_transaction(SHOP_ID, STMT_ID, TXN)
    assert n is True

    row = _fetch(db_url, "TEST_TXN_001")
    assert row is not None
    assert row["statement_id"] == STMT_ID
    assert row["shop_id"] == SHOP_ID
    assert row["order_id"] == "TEST_ORDER_001"
    assert row["order_create_time"] == 1786048444
    assert row["type"] == "ORDER"
    assert row["currency"] == "VND"
    # 字符串数字 → NUMERIC
    assert float(row["customer_payment_amount"]) == 524744.0
    assert float(row["platform_commission_amount"]) == -18674.59
    assert float(row["iva_vat_amount"]) == -1110.25
    # 未提供的金额字段 → NULL（不是 0）
    assert row["pit_amount"] is None
    assert row["transaction_fee_amount"] is None
    # raw 兜底
    assert row["raw"]["id"] == "TEST_TXN_001"


def test_persist_idempotent_upsert(db_url: str):
    assert tts_erp.persist_statement_transaction(SHOP_ID, STMT_ID, TXN) is True
    updated = dict(TXN, settlement_amount="999")
    assert tts_erp.persist_statement_transaction(SHOP_ID, STMT_ID, updated) is True

    row = _fetch(db_url, "TEST_TXN_001")
    assert float(row["settlement_amount"]) == 999.0


def test_persist_skips_txn_without_id(db_url: str):
    assert tts_erp.persist_statement_transaction(SHOP_ID, STMT_ID, {"order_id": "X"}) is False
    assert _fetch(db_url, "X") is None


def test_persist_tolerates_non_numeric_amount(db_url: str):
    bad = dict(TXN, id="TEST_TXN_002", customer_payment_amount="N/A")
    assert tts_erp.persist_statement_transaction(SHOP_ID, STMT_ID, bad) is True
    row = _fetch(db_url, "TEST_TXN_002")
    assert row["customer_payment_amount"] is None


# ─── business 层（fake http/repo，模式同 test_sync_payments.py）────────

from typing import Any  # noqa: E402

from domain import Creds  # noqa: E402


class FakeHttpClient:
    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = []

    def request(self, method, path, *, body=None, extra_params=None, timeout=30):
        self.calls.append({"method": method, "path": path,
                           "extra_params": dict(extra_params or {})})
        if not self._responses:
            raise AssertionError(f"FakeHttpClient exhausted: {method} {path}")
        return self._responses.pop(0)


class FakeStmtTxnRepository:
    def __init__(self, statement_ids=()):
        self.txns: list[tuple[str, str, dict]] = []
        self.statement_ids = list(statement_ids)
        self.list_calls = []

    def upsert(self, shop_id: str, statement_id: str, txn_raw: dict[str, Any]) -> bool:
        if not txn_raw.get("id"):
            return False
        self.txns.append((shop_id, statement_id, dict(txn_raw)))
        return True

    def list_statement_ids(self, shop_id: str, *, statement_time_ge=None,
                           statement_time_lt=None, limit: int = 1000) -> list[str]:
        self.list_calls.append({"ge": statement_time_ge, "lt": statement_time_lt})
        return list(self.statement_ids)


@pytest.fixture()
def creds():
    return Creds(access_token="tok", shop_cipher="cipher", region="VN", shop_id="shop-1")


def _txn(tid: str) -> dict:
    return {"id": tid, "order_id": f"O-{tid}", "settlement_amount": "100"}


def test_biz_explicit_statement_ids(creds):
    http = FakeHttpClient([
        {"code": 0, "data": {"statement_transactions": [_txn("t1"), _txn("t2")]}},
        {"code": 0, "data": {"statement_transactions": [_txn("t3")]}},
    ])
    repo = FakeStmtTxnRepository()
    from tts_business import sync_statement_transactions

    result = sync_statement_transactions(
        creds, {"shop_id": creds.shop_id, "statement_ids": ["S1", "S2"]},
        http=http, repo=repo)

    assert result.ok and result.saved == 3 and result.pages == 2
    assert http.calls[0]["path"] == "/finance/202309/statements/S1/statement_transactions"
    assert http.calls[1]["path"] == "/finance/202309/statements/S2/statement_transactions"
    assert http.calls[0]["extra_params"]["sort_field"] == "order_create_time"
    # repo.list_statement_ids 不该被调用（显式给了 ids）
    assert repo.list_calls == []
    assert [t[1] for t in repo.txns] == ["S1", "S1", "S2"]


def test_biz_window_uses_list_statement_ids(creds):
    http = FakeHttpClient([
        {"code": 0, "data": {"statement_transactions": [_txn("t1")]}},
    ])
    repo = FakeStmtTxnRepository(statement_ids=["S9"])
    from tts_business import sync_statement_transactions

    result = sync_statement_transactions(
        creds, {"shop_id": creds.shop_id, "statement_time_ge": 111, "statement_time_lt": 222},
        http=http, repo=repo)

    assert result.ok and result.saved == 1
    assert repo.list_calls == [{"ge": 111, "lt": 222}]


def test_biz_pagination_within_statement(creds):
    http = FakeHttpClient([
        {"code": 0, "data": {"statement_transactions": [_txn("t1")], "next_page_token": "nt"}},
        {"code": 0, "data": {"statement_transactions": [_txn("t2")]}},
    ])
    repo = FakeStmtTxnRepository()
    from tts_business import sync_statement_transactions

    result = sync_statement_transactions(
        creds, {"shop_id": creds.shop_id, "statement_ids": ["S1"]}, http=http, repo=repo)

    assert result.ok and result.saved == 2 and result.pages == 2
    assert http.calls[1]["extra_params"]["page_token"] == "nt"


def test_biz_statement_error_continues_others(creds):
    http = FakeHttpClient([
        {"code": 106001, "message": "invalid sign"},
        {"code": 0, "data": {"statement_transactions": [_txn("t1")]}},
    ])
    repo = FakeStmtTxnRepository()
    from tts_business import sync_statement_transactions

    result = sync_statement_transactions(
        creds, {"shop_id": creds.shop_id, "statement_ids": ["BAD", "S1"]},
        http=http, repo=repo)

    # 部分失败不整体报错：成功的照常落库，error 记录细节
    assert result.saved == 1
    assert [t[1] for t in repo.txns] == ["S1"]


def test_biz_all_failed_reports_error(creds):
    http = FakeHttpClient([{"code": 106001, "message": "invalid sign"}])
    repo = FakeStmtTxnRepository()
    from tts_business import sync_statement_transactions

    result = sync_statement_transactions(
        creds, {"shop_id": creds.shop_id, "statement_ids": ["BAD"]},
        http=http, repo=repo)

    assert not result.ok and result.saved == 0
