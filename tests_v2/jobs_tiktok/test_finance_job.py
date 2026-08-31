"""TDD tests for jobs.tiktok.finance — payouts + statements + components."""
from __future__ import annotations

from typing import Any

import pytest
from sqlalchemy import select

from tts_erp_v2.db.models import (
    ChannelAccount,
    Credentials,
    Payout,
    RawRecord,
    SettlementComponent,
    SettlementStatement,
    SettlementTransaction,
    SyncIssue,
)
from tts_erp_v2.jobs.tiktok import finance as finance_job
from tts_erp_v2.sync_worker.job_runner import run_with_sync_job


pytestmark = [pytest.mark.domain_finance, pytest.mark.layer_integration]


class FakeProxy:
    """Routes by endpoint suffix. payout page drives statements → transactions cascade."""

    def __init__(
        self,
        *,
        payouts_pages,
        statements_pages,
        transactions_pages,
    ):
        self.payouts_pages = list(payouts_pages)
        self.statements_pages = list(statements_pages)
        self.transactions_pages = list(transactions_pages)
        self.calls: list[tuple[str, str]] = []

    def __call__(self, method: str, path: str, body=None):
        self.calls.append((method, path))
        if "payments/list" in path:
            return self.payouts_pages.pop(0) if self.payouts_pages else {"code": 0, "data": {"payments": []}}
        if "settlements/search" in path:
            return (
                self.statements_pages.pop(0)
                if self.statements_pages
                else {"code": 0, "data": {"statements": []}}
            )
        if "settlements/transactions" in path:
            return (
                self.transactions_pages.pop(0)
                if self.transactions_pages
                else {"code": 0, "data": {"transactions": []}}
            )
        return {"code": 404}


def _make_account(session) -> ChannelAccount:
    cred = Credentials(
        provider="tiktok",
        external_account_id="TEST_TT_FIN_SHOP",
        ciphertext=b"\x00" * 32,
    )
    session.add(cred)
    session.flush()
    acct = ChannelAccount(
        platform="tiktok",
        external_account_id="TEST_TT_FIN_SHOP",
        credential_id=cred.id,
        status="active",
    )
    session.add(acct)
    session.flush()
    return acct


def _payout_payload(pid: str, *, update_time: int = 1_700_001_000):
    return {
        "payment_id": pid,
        "payment_status": "SETTLED",
        "currency": "USD",
        "amount": "100.00",
        "update_time": update_time,
        "create_time": update_time - 1000,
    }


def _statement_payload(sid: str):
    return {
        "statement_id": sid,
        "statement_time": 1_700_001_000,
        "currency": "USD",
        "period": {"start_date": "2026-08-01", "end_date": "2026-08-31"},
    }


def _transaction_payload(tid: str, *, fee: str | None = "1.50"):
    tx = {"transaction_id": tid, "transaction_time": 1_700_001_000}
    if fee is not None:
        tx["fee"] = fee
    return tx


def test_finance_payouts_statements_transactions_components(db_session) -> None:
    account = _make_account(db_session)
    proxy = FakeProxy(
        payouts_pages=[
            {"code": 0, "data": {"payments": [_payout_payload("PAY1")]}}
        ],
        statements_pages=[
            {"code": 0, "data": {"statements": [_statement_payload("STM1")], "next_page_token": ""}}
        ],
        transactions_pages=[
            {
                "code": 0,
                "data": {
                    "transactions": [_transaction_payload("TX1", fee="2.50")],
                    "next_page_token": "",
                },
            }
        ],
    )
    _, result = run_with_sync_job(
        db_session,
        job_name="tiktok.finance",
        credential_id=account.credential_id,
        inner=finance_job.run,
        inner_kwargs={"proxy_call": proxy, "shop_id": account.external_account_id},
    )
    assert result.rows_inserted == 1  # one payout
    payout = db_session.execute(
        select(Payout).where(Payout.external_payout_id == "PAY1")
    ).scalar_one()
    assert payout.external_payout_id == "PAY1"
    stmt = db_session.execute(
        select(SettlementStatement).where(
            SettlementStatement.external_statement_id == "STM1"
        )
    ).scalar_one()
    assert stmt.external_statement_id == "STM1"
    txn = db_session.execute(
        select(SettlementTransaction).where(
            SettlementTransaction.external_transaction_id == "TX1"
        )
    ).scalar_one()
    assert txn.external_transaction_id == "TX1"
    components = db_session.execute(
        select(SettlementComponent).where(SettlementComponent.transaction_id == txn.id)
    ).scalars().all()
    assert len(components) == 1
    assert components[0].component_code == "fee"
    assert components[0].amount == pytest.approx(2.50)


def test_finance_zero_amount_component_not_written(db_session) -> None:
    """V3 rule: don't store zero-amount components (17x bloat)."""
    account = _make_account(db_session)
    proxy = FakeProxy(
        payouts_pages=[
            {"code": 0, "data": {"payments": [_payout_payload("PAY2")]}}
        ],
        statements_pages=[
            {"code": 0, "data": {"statements": [_statement_payload("STM2")], "next_page_token": ""}}
        ],
        transactions_pages=[
            {
                "code": 0,
                "data": {
                    "transactions": [_transaction_payload("TX2", fee="0")],
                    "next_page_token": "",
                },
            }
        ],
    )
    _, result = run_with_sync_job(
        db_session,
        job_name="tiktok.finance",
        credential_id=account.credential_id,
        inner=finance_job.run,
        inner_kwargs={"proxy_call": proxy, "shop_id": account.external_account_id},
    )
    assert result.rows_inserted == 1
    txn = db_session.execute(
        select(SettlementTransaction).where(
            SettlementTransaction.external_transaction_id == "TX2"
        )
    ).scalar_one()
    components = db_session.execute(
        select(SettlementComponent).where(SettlementComponent.transaction_id == txn.id)
    ).scalars().all()
    assert components == []  # no zero rows
