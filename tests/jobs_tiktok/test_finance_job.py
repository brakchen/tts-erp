"""TDD tests for jobs.tiktok.finance — payouts + statements + components.

Contract under test (Lane 2 refactor, 2026-08-31)
------------------------------------------------
* ``payouts`` and ``statements`` are TWO independent sub-jobs sharing
  one ``run(session, *, proxy_call, shop_id)`` entry point. They
  maintain SEPARATE watermarks under ``tiktok.finance.payouts`` and
  ``tiktok.finance.statements`` respectively.
* ``statements`` is no longer nested inside the payout loop. Each
  statement row is upserted once, with ``payout_id`` resolved via the
  upstream ``payment_id`` field — never fanned out by iterating
  payouts. This fixes the 24× replication bug (1080 statements vs 45
  real).
* Statements whose upstream payload lacks ``payment_id`` are surfaced
  as ``SyncIssue(issue_type='STATEMENT_PAYMENT_ID_MISSING')`` and
  skipped — the cursor does NOT advance past them.
* Upstream failures on the statements / transactions walk are
  re-raised (no silent ``raw_statements=[]`` / ``raw_txns=[]``).
  ``run_with_sync_job`` will mark the run 'failed' and the watermark
  stays put.
* Late statements (whose owning payout no longer updates) still get
  picked up because the statements cursor advances independently.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from tts_erp_v2.db.models import (
    ChannelAccount,
    Credentials,
    Payout,
    SettlementComponent,
    SettlementStatement,
    SettlementTransaction,
    SyncCursor,
    SyncIssue,
    SyncJob,
)
from tts_erp_v2.jobs.tiktok import finance as finance_job
from tts_erp_v2.sync_worker.job_runner import run_with_sync_job

pytestmark = [pytest.mark.domain_finance, pytest.mark.layer_integration]


# ─── Test helpers ───────────────────────────────────────────────────


class FakeProxy:
    """Routes by endpoint path. Three independent feeds, one per sub-job.

    The fake records every call so tests can assert which body keys were
    sent on which call (e.g. that statements DO get a ``statement_time``
    filter even when payouts haven't moved).
    """

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
        self.calls: list[tuple[str, str, dict | None]] = []

    def __call__(self, method: str, path: str, body=None):
        self.calls.append((method, path, body))
        if "/finance/202309/payments" in path and "statements" not in path:
            return (
                self.payouts_pages.pop(0)
                if self.payouts_pages
                else {"code": 0, "data": {"payments": []}}
            )
        if path.rstrip("/").endswith("/finance/202309/statements"):
            return (
                self.statements_pages.pop(0)
                if self.statements_pages
                else {"code": 0, "data": {"statements": []}}
            )
        if "/statement_transactions" in path:
            return (
                self.transactions_pages.pop(0)
                if self.transactions_pages
                else {"code": 0, "data": {"statement_transactions": []}}
            )
        return {"code": 404, "message": f"unmocked path: {path}"}


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


def _statement_payload(
    sid: str, *, payment_id: str | None = None, statement_time: int = 1_700_001_000
):
    out = {
        "id": sid,
        "statement_time": statement_time,
        "currency": "USD",
        "period": {"start_date": "2026-08-01", "end_date": "2026-08-31"},
    }
    if payment_id is not None:
        out["payment_id"] = payment_id
    return out


def _transaction_payload(tid: str, *, fee: str | None = "1.50"):
    tx = {"transaction_id": tid, "transaction_time": 1_700_001_000}
    if fee is not None:
        tx["fee"] = fee
    return tx


def _cursor_value(session, *, job_name: str, scope: str) -> int | None:
    row = session.execute(
        select(SyncCursor).where(
            SyncCursor.job_name == job_name,
            SyncCursor.scope == scope,
        )
    ).scalar_one_or_none()
    return row.cursor_epoch_ms if row else None


def _test_account_statements(
    session, account: ChannelAccount
) -> list[SettlementStatement]:
    """Return SettlementStatements whose payout belongs to ``account``.

    The shared ``db_session`` fixture joins an outer rollback
    transaction, so reads see committed production data (1080 replicated
    SettlementStatement rows from the audit we're fixing). Tests must
    scope all ``finance.*`` row assertions to the per-test
    ChannelAccount to avoid leaking prod rows into ``[]`` checks.
    """
    return list(
        session.execute(
            select(SettlementStatement)
            .join(Payout, SettlementStatement.payout_id == Payout.id)
            .where(Payout.channel_account_id == account.id)
        )
        .scalars()
        .all()
    )


def _test_account_transactions(
    session, account: ChannelAccount
) -> list[SettlementTransaction]:
    """Return SettlementTransactions under test-account statements.

    See ``_test_account_statements`` for the scoping rationale.
    """
    return list(
        session.execute(
            select(SettlementTransaction)
            .join(
                SettlementStatement,
                SettlementTransaction.settlement_statement_id == SettlementStatement.id,
            )
            .join(Payout, SettlementStatement.payout_id == Payout.id)
            .where(Payout.channel_account_id == account.id)
        )
        .scalars()
        .all()
    )


# ─── Existing happy-path coverage (updated to new endpoints) ───────


def test_finance_payouts_statements_transactions_components(db_session) -> None:
    account = _make_account(db_session)
    proxy = FakeProxy(
        payouts_pages=[{"code": 0, "data": {"payments": [_payout_payload("PAY1")]}}],
        statements_pages=[
            {
                "code": 0,
                "data": {
                    "statements": [_statement_payload("STM1", payment_id="PAY1")],
                    "next_page_token": "",
                },
            }
        ],
        transactions_pages=[
            {
                "code": 0,
                "data": {
                    "statement_transactions": [_transaction_payload("TX1", fee="2.50")],
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
    assert (
        result.rows_inserted == 2
    )  # 1 payout + 1 statement (orchestrator sums sub-jobs)
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
    components = (
        db_session.execute(
            select(SettlementComponent).where(
                SettlementComponent.transaction_id == txn.id
            )
        )
        .scalars()
        .all()
    )
    assert len(components) == 1
    assert components[0].component_code == "fee"
    assert components[0].amount == pytest.approx(2.50)


def test_finance_zero_amount_component_not_written(db_session) -> None:
    """V3 rule: don't store zero-amount components (17x bloat)."""
    account = _make_account(db_session)
    proxy = FakeProxy(
        payouts_pages=[{"code": 0, "data": {"payments": [_payout_payload("PAY2")]}}],
        statements_pages=[
            {
                "code": 0,
                "data": {
                    "statements": [_statement_payload("STM2", payment_id="PAY2")],
                    "next_page_token": "",
                },
            }
        ],
        transactions_pages=[
            {
                "code": 0,
                "data": {
                    "statement_transactions": [_transaction_payload("TX2", fee="0")],
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
    assert (
        result.rows_inserted == 2
    )  # 1 payout + 1 statement (orchestrator sums sub-jobs)
    txn = db_session.execute(
        select(SettlementTransaction).where(
            SettlementTransaction.external_transaction_id == "TX2"
        )
    ).scalar_one()
    components = (
        db_session.execute(
            select(SettlementComponent).where(
                SettlementComponent.transaction_id == txn.id
            )
        )
        .scalars()
        .all()
    )
    assert components == []  # no zero rows


# ─── Lane 2 regression tests ──────────────────────────────────────


def test_finance_statements_have_own_cursor(db_session) -> None:
    """The two sub-jobs write to distinct ``sync_cursors`` rows."""
    account = _make_account(db_session)
    proxy = FakeProxy(
        payouts_pages=[{"code": 0, "data": {"payments": [_payout_payload("PAY3")]}}],
        statements_pages=[
            {
                "code": 0,
                "data": {
                    "statements": [_statement_payload("STM3", payment_id="PAY3")],
                    "next_page_token": "",
                },
            }
        ],
        transactions_pages=[
            {
                "code": 0,
                "data": {
                    "statement_transactions": [_transaction_payload("TX3", fee="1.00")],
                    "next_page_token": "",
                },
            }
        ],
    )
    run_with_sync_job(
        db_session,
        job_name="tiktok.finance",
        credential_id=account.credential_id,
        inner=finance_job.run,
        inner_kwargs={"proxy_call": proxy, "shop_id": account.external_account_id},
    )
    payouts_cursor = _cursor_value(
        db_session, job_name="tiktok.finance.payouts", scope=account.external_account_id
    )
    stmts_cursor = _cursor_value(
        db_session,
        job_name="tiktok.finance.statements",
        scope=account.external_account_id,
    )
    assert payouts_cursor is not None and payouts_cursor > 0
    assert stmts_cursor is not None and stmts_cursor > 0
    # Both cursors must use the same (statement_time / update_time) source
    # so a 2nd run with no new data is a no-op for both.
    assert payouts_cursor == stmts_cursor


def test_finance_statement_attaches_only_to_own_payout(db_session) -> None:
    """One statement with payment_id=PAY-A must attach to PAY-A only.

    Locks the 24× replication bug: pre-refactor, every payout iterated
    ``/finance/202309/statements`` (GET with body=payment_id dropped by
    proxy_call) → same statement upserted once per payout → 24× fan-out.
    Post-refactor, statements is a top-level pull that resolves its own
    payout_id via the upstream ``payment_id`` field.
    """
    account = _make_account(db_session)
    # Three payouts, only one shares a payment_id with the statement.
    proxy = FakeProxy(
        payouts_pages=[
            {
                "code": 0,
                "data": {
                    "payments": [
                        _payout_payload("PAY_A", update_time=1_700_001_000),
                        _payout_payload("PAY_B", update_time=1_700_001_100),
                        _payout_payload("PAY_C", update_time=1_700_001_200),
                    ],
                },
            }
        ],
        statements_pages=[
            {
                "code": 0,
                "data": {
                    "statements": [_statement_payload("STM_A", payment_id="PAY_A")],
                    "next_page_token": "",
                },
            }
        ],
        transactions_pages=[
            {
                "code": 0,
                "data": {
                    "statement_transactions": [
                        _transaction_payload("TX_A", fee="1.00")
                    ],
                    "next_page_token": "",
                },
            }
        ],
    )
    run_with_sync_job(
        db_session,
        job_name="tiktok.finance",
        credential_id=account.credential_id,
        inner=finance_job.run,
        inner_kwargs={"proxy_call": proxy, "shop_id": account.external_account_id},
    )
    payout_a = db_session.execute(
        select(Payout).where(Payout.external_payout_id == "PAY_A")
    ).scalar_one()
    payout_b = db_session.execute(
        select(Payout).where(Payout.external_payout_id == "PAY_B")
    ).scalar_one()
    payout_c = db_session.execute(
        select(Payout).where(Payout.external_payout_id == "PAY_C")
    ).scalar_one()
    # ONE settlement_statement row, attached to PAY_A. Scoped to the test
    # account so production's 1080 replicated rows don't pollute the count.
    stmts = _test_account_statements(db_session, account)
    assert len(stmts) == 1, f"expected 1 statement (got {len(stmts)} — replication bug)"
    assert stmts[0].external_statement_id == "STM_A"
    assert stmts[0].payout_id == payout_a.id
    # No statement ever touched PAY_B / PAY_C.
    assert payout_b.id != stmts[0].payout_id
    assert payout_c.id != stmts[0].payout_id


def test_finance_statement_without_payment_id_skipped_with_issue(db_session) -> None:
    """A statement payload lacking ``payment_id`` must NOT be silently
    attached. Surface as ``SyncIssue(STATEMENT_PAYMENT_ID_MISSING)`` and
    skip — the statements cursor MUST NOT advance past it (otherwise a
    later batch with the real payment_id would be re-fetched, and an
    early termination would lose visibility).
    """
    account = _make_account(db_session)
    proxy = FakeProxy(
        payouts_pages=[{"code": 0, "data": {"payments": [_payout_payload("PAY_X")]}}],
        statements_pages=[
            {
                "code": 0,
                "data": {
                    "statements": [_statement_payload("STM_X", payment_id=None)],
                    "next_page_token": "",
                },
            }
        ],
        transactions_pages=[],  # never called
    )
    run_with_sync_job(
        db_session,
        job_name="tiktok.finance",
        credential_id=account.credential_id,
        inner=finance_job.run,
        inner_kwargs={"proxy_call": proxy, "shop_id": account.external_account_id},
    )
    # No statement row should exist (skip, not silent attach). Scoped to
    # the test account so production's 1080 replicated rows don't pollute.
    assert _test_account_statements(db_session, account) == []
    # SyncIssue recorded. Filter by (job_name, external_id) — prod has 23
    # STATEMENT_PAYMENT_ID_MISSING rows for other jobs that would inflate
    # the count.
    issues = (
        db_session.execute(
            select(SyncIssue).where(
                SyncIssue.issue_type == "STATEMENT_PAYMENT_ID_MISSING",
                SyncIssue.job_name == "tiktok.finance",
                SyncIssue.external_id == "STM_X",
            )
        )
        .scalars()
        .all()
    )
    assert len(issues) == 1
    assert issues[0].external_id == "STM_X"
    # Cursor MUST NOT have advanced past the missing-payment_id row, so
    # the next tick re-fetches it (TikTok might add payment_id later, or
    # we might back-fill).
    stmts_cursor = _cursor_value(
        db_session,
        job_name="tiktok.finance.statements",
        scope=account.external_account_id,
    )
    assert stmts_cursor is None, (
        "statements cursor advanced past a row we couldn't ingest; "
        "next tick would skip it forever"
    )


def test_finance_upstream_failure_on_statements_raises(db_session) -> None:
    """Statements 5xx must NOT be silently swallowed as ``[]``.

    Pre-refactor: ``except UpstreamJobError: raw_statements=[]`` meant
    a 500 on the statements endpoint advanced the payouts cursor but
    never pulled statements → silent data loss. Post-refactor: the
    exception propagates and ``run_with_sync_job`` marks the run failed.
    """
    account = _make_account(db_session)
    proxy = FakeProxy(
        payouts_pages=[{"code": 0, "data": {"payments": [_payout_payload("PAY_E")]}}],
        statements_pages=[
            {"code": 50000, "message": "internal_error", "data": {}},
        ],
        transactions_pages=[],
    )
    with pytest.raises(finance_job.UpstreamJobError):
        run_with_sync_job(
            db_session,
            job_name="tiktok.finance",
            credential_id=account.credential_id,
            inner=finance_job.run,
            inner_kwargs={"proxy_call": proxy, "shop_id": account.external_account_id},
        )
    # sync_jobs row was committed before the re-raise — fetch it now.
    sync_row = db_session.execute(
        select(SyncJob)
        .where(
            SyncJob.job_name == "tiktok.finance",
            SyncJob.credential_id == account.credential_id,
        )
        .order_by(SyncJob.started_at.desc())
        .limit(1)
    ).scalar_one()
    assert sync_row.status == "failed"
    # The payout WAS ingested (it lives in its own sub-tick), but the
    # statements cursor must not have advanced.
    payout = db_session.execute(
        select(Payout).where(Payout.external_payout_id == "PAY_E")
    ).scalar_one()
    assert payout.id is not None
    assert (
        _cursor_value(
            db_session,
            job_name="tiktok.finance.statements",
            scope=account.external_account_id,
        )
        is None
    )


def test_finance_upstream_failure_on_transactions_raises(db_session) -> None:
    """Transactions 5xx must NOT be silently swallowed as ``[]`` either."""
    account = _make_account(db_session)
    proxy = FakeProxy(
        payouts_pages=[{"code": 0, "data": {"payments": [_payout_payload("PAY_F")]}}],
        statements_pages=[
            {
                "code": 0,
                "data": {
                    "statements": [_statement_payload("STM_F", payment_id="PAY_F")],
                    "next_page_token": "",
                },
            }
        ],
        transactions_pages=[
            {"code": 50000, "message": "internal_error", "data": {}},
        ],
    )
    with pytest.raises(finance_job.UpstreamJobError):
        run_with_sync_job(
            db_session,
            job_name="tiktok.finance",
            credential_id=account.credential_id,
            inner=finance_job.run,
            inner_kwargs={"proxy_call": proxy, "shop_id": account.external_account_id},
        )
    # sync_jobs row was committed before the re-raise — fetch it now.
    sync_row = db_session.execute(
        select(SyncJob)
        .where(
            SyncJob.job_name == "tiktok.finance",
            SyncJob.credential_id == account.credential_id,
        )
        .order_by(SyncJob.started_at.desc())
        .limit(1)
    ).scalar_one()
    assert sync_row.status == "failed"
    # Statement was ingested, transaction was NOT.
    assert (
        db_session.execute(
            select(SettlementStatement).where(
                SettlementStatement.external_statement_id == "STM_F"
            )
        )
        .scalar_one()
        .id
        is not None
    )
    assert _test_account_transactions(db_session, account) == []


def test_finance_late_statement_advances_independently(db_session) -> None:
    """A statement whose owning payout NO LONGER UPDATES still gets pulled.

    Locks the watermark-split requirement: pre-refactor, statements
    shared the payouts ``update_time`` cursor. Once a payout settled
    and stopped bumping ``update_time``, its late-arriving statement
    would be skipped forever. Post-refactor, ``tiktok.finance.statements``
    has its own cursor advancing on ``statement_time``.
    """
    account = _make_account(db_session)
    # Tick 1: payouts has PAY_L, statements has STM_L with payment_id=PAY_L
    # (both at t=1_700_001_000).
    proxy1 = FakeProxy(
        payouts_pages=[
            {
                "code": 0,
                "data": {
                    "payments": [_payout_payload("PAY_L", update_time=1_700_001_000)]
                },
            }
        ],
        statements_pages=[
            {
                "code": 0,
                "data": {
                    "statements": [
                        _statement_payload(
                            "STM_L", payment_id="PAY_L", statement_time=1_700_001_000
                        )
                    ],
                    "next_page_token": "",
                },
            }
        ],
        transactions_pages=[
            {
                "code": 0,
                "data": {
                    "statement_transactions": [
                        _transaction_payload("TX_L", fee="1.00")
                    ],
                    "next_page_token": "",
                },
            }
        ],
    )
    run_with_sync_job(
        db_session,
        job_name="tiktok.finance",
        credential_id=account.credential_id,
        inner=finance_job.run,
        inner_kwargs={"proxy_call": proxy1, "shop_id": account.external_account_id},
    )
    payouts_cursor_after_tick1 = _cursor_value(
        db_session, job_name="tiktok.finance.payouts", scope=account.external_account_id
    )
    stmts_cursor_after_tick1 = _cursor_value(
        db_session,
        job_name="tiktok.finance.statements",
        scope=account.external_account_id,
    )
    assert payouts_cursor_after_tick1 == stmts_cursor_after_tick1 == 1_700_001_000_000

    # Tick 2: payouts EMPTY (no updates), but a NEW statement arrives
    # for the same payout (statement_time=1_700_005_000 — late).
    proxy2 = FakeProxy(
        payouts_pages=[
            {"code": 0, "data": {"payments": []}},
        ],
        statements_pages=[
            {
                "code": 0,
                "data": {
                    "statements": [
                        _statement_payload(
                            "STM_L_LATE",
                            payment_id="PAY_L",
                            statement_time=1_700_005_000,
                        )
                    ],
                    "next_page_token": "",
                },
            }
        ],
        transactions_pages=[
            {
                "code": 0,
                "data": {
                    "statement_transactions": [
                        _transaction_payload("TX_L_LATE", fee="0.50")
                    ],
                    "next_page_token": "",
                },
            }
        ],
    )
    run_with_sync_job(
        db_session,
        job_name="tiktok.finance",
        credential_id=account.credential_id,
        inner=finance_job.run,
        inner_kwargs={"proxy_call": proxy2, "shop_id": account.external_account_id},
    )
    # The new statement was ingested — the payouts cursor staying put
    # didn't matter, because statements has its own cursor.
    late = db_session.execute(
        select(SettlementStatement).where(
            SettlementStatement.external_statement_id == "STM_L_LATE"
        )
    ).scalar_one()
    assert late.id is not None
    # Payouts cursor unchanged (no new payout updates).
    payouts_cursor_after_tick2 = _cursor_value(
        db_session,
        job_name="tiktok.finance.payouts",
        scope=account.external_account_id,
    )
    assert payouts_cursor_after_tick2 == payouts_cursor_after_tick1
    # Statements cursor advanced to the late statement_time.
    stmts_cursor_after_tick2 = _cursor_value(
        db_session,
        job_name="tiktok.finance.statements",
        scope=account.external_account_id,
    )
    assert stmts_cursor_after_tick2 is not None
    assert payouts_cursor_after_tick1 is not None
    assert stmts_cursor_after_tick2 > payouts_cursor_after_tick1


def test_finance_statement_without_known_payout_raises_sync_issue(db_session) -> None:
    """Statement's ``payment_id`` references a payout NOT in the DB
    (e.g. payout sync hasn't run yet, or payout was deleted). Must NOT
    silently attach (FK would 500) — surface as a SyncIssue.
    """
    account = _make_account(db_session)
    # No payouts returned at all, but a statement references PAY_GHOST.
    proxy = FakeProxy(
        payouts_pages=[{"code": 0, "data": {"payments": []}}],
        statements_pages=[
            {
                "code": 0,
                "data": {
                    "statements": [_statement_payload("STM_G", payment_id="PAY_GHOST")],
                    "next_page_token": "",
                },
            }
        ],
        transactions_pages=[],
    )
    run_with_sync_job(
        db_session,
        job_name="tiktok.finance",
        credential_id=account.credential_id,
        inner=finance_job.run,
        inner_kwargs={"proxy_call": proxy, "shop_id": account.external_account_id},
    )
    issues = (
        db_session.execute(
            select(SyncIssue).where(
                SyncIssue.issue_type == "STATEMENT_PAYOUT_UNRESOLVED"
            )
        )
        .scalars()
        .all()
    )
    assert len(issues) == 1
    assert issues[0].external_id == "STM_G"
    assert issues[0].details["payment_id"] == "PAY_GHOST"
    # Statement NOT ingested. Scoped to the test account (see helper).
    assert _test_account_statements(db_session, account) == []


def test_finance_old_legacy_cursor_name_not_written(db_session) -> None:
    """No row in sync_cursors under the legacy ``tiktok.finance`` name.

    (The old code shared one cursor across payouts + statements. After
    the split, the umbrella ``tiktok.finance`` job_name no longer writes
    a cursor — it just registers ``sync_jobs`` rows.)
    """
    account = _make_account(db_session)
    proxy = FakeProxy(
        payouts_pages=[{"code": 0, "data": {"payments": [_payout_payload("PAY_N")]}}],
        statements_pages=[
            {
                "code": 0,
                "data": {
                    "statements": [_statement_payload("STM_N", payment_id="PAY_N")],
                    "next_page_token": "",
                },
            }
        ],
        transactions_pages=[
            {
                "code": 0,
                "data": {
                    "statement_transactions": [
                        _transaction_payload("TX_N", fee="1.00")
                    ],
                    "next_page_token": "",
                },
            }
        ],
    )
    run_with_sync_job(
        db_session,
        job_name="tiktok.finance",
        credential_id=account.credential_id,
        inner=finance_job.run,
        inner_kwargs={"proxy_call": proxy, "shop_id": account.external_account_id},
    )
    legacy = (
        db_session.execute(
            select(SyncCursor).where(SyncCursor.job_name == "tiktok.finance")
        )
        .scalars()
        .all()
    )
    assert legacy == []
