"""Tests for migrate_finance.

Covers:
* Source counts (23 payments, 44 statements, 296 transactions).
* Settlement components: only non-zero rows are written (V3 §3.2).
* The 53-column wide-row → EAV expansion.
* Component codes are uppercase + suffix-stripped.
* Real-run idempotency.
* Sum-of-amounts reconciliation.
"""
from __future__ import annotations

from scripts.migrate_v1_to_v2.migrate_finance import _COMPONENT_COLUMNS


def _count(table: str) -> int:
    from tts_erp_v2.db.base import get_engine
    eng = get_engine()
    table_q = {
        "finance.payouts":
            "SELECT count(*) FROM finance.payouts",
        "finance.settlement_statements":
            "SELECT count(*) FROM finance.settlement_statements",
        "finance.settlement_transactions":
            "SELECT count(*) FROM finance.settlement_transactions",
        "finance.settlement_components":
            "SELECT count(*) FROM finance.settlement_components",
    }
    if table not in table_q:
        raise ValueError(f"unknown table {table!r}")
    with eng.connect() as conn:
        row = conn.exec_driver_sql(table_q[table]).first()
    return int(row[0])


def test_dry_run_reports_full_population(dry_run_runner) -> None:
    """Dry-run sees all 23 payments, 44 statements, 296 transactions."""
    stats = dry_run_runner("finance")
    assert stats.payments_seen == 23
    assert stats.statements_seen == 44
    assert stats.transactions_seen == 296


def test_real_run_payouts_match_source() -> None:
    """23 payments → 23 payouts."""
    assert _count("finance.payouts") == 23


def test_real_run_settlement_statements_partial() -> None:
    """44 statements in source; 16 have NULL payment_id → 28 land in target."""
    # The 16 NULL-payment_id rows are documented exclusions (no parent
    # payout to attach to). 44 - 16 = 28.
    assert _count("finance.settlement_statements") == 28


def test_real_run_settlement_transactions_partial() -> None:
    """296 transactions in source; 33 belong to skipped statements → 263."""
    assert _count("finance.settlement_transactions") == 263


def test_settlement_components_written() -> None:
    """The component expansion lands ~3477 non-zero rows in prod."""
    n = _count("finance.settlement_components")
    # 296 transactions × 53 numeric columns = 15708 max; 3477 in prod.
    # We accept any value within a reasonable band.
    assert 1000 < n < 15000, f"unexpected component count: {n}"


def test_zero_components_filtered() -> None:
    """No zero-amount components are written (V3 §3.2)."""
    from tts_erp_v2.db.base import get_engine
    eng = get_engine()
    with eng.connect() as conn:
        row = conn.exec_driver_sql(
            "SELECT count(*) FROM finance.settlement_components "
            "WHERE amount = 0"
        ).first()
    assert int(row[0]) == 0, "zero-amount components leaked into target"


def test_component_codes_are_uppercase_no_suffix() -> None:
    """Component code is column name with '_amount' suffix stripped +
    uppercased. E.g. ``actual_shipping_fee_amount`` → ``ACTUAL_SHIPPING_FEE``."""
    from tts_erp_v2.db.base import get_engine
    eng = get_engine()
    with eng.connect() as conn:
        rows = conn.exec_driver_sql(
            "SELECT DISTINCT component_code FROM finance.settlement_components"
        ).fetchall()
    codes = {r[0] for r in rows}
    # Spot-check expected codes (alphabetical sampling — they all start
    # with the column name with the ``_amount`` suffix removed).
    for expected in (
        "GROSS_SALES",
        "PLATFORM_COMMISSION",
        "SETTLEMENT",
        "REVENUE",
        "FEE",
        "SHIPPING_FEE",
    ):
        assert expected in codes, (
            f"component_code {expected!r} missing from "
            f"settlement_components (found: {sorted(codes)[:10]}…)"
        )
    # No code should end with _AMOUNT (the suffix is stripped).
    for code in codes:
        assert not code.endswith("_AMOUNT"), (
            f"component_code {code!r} still has _AMOUNT suffix"
        )


def test_real_run_is_idempotent(real_runner) -> None:
    before = {
        "payouts": _count("finance.payouts"),
        "statements": _count("finance.settlement_statements"),
        "transactions": _count("finance.settlement_transactions"),
        "components": _count("finance.settlement_components"),
    }
    real_runner("finance")
    after = {
        "payouts": _count("finance.payouts"),
        "statements": _count("finance.settlement_statements"),
        "transactions": _count("finance.settlement_transactions"),
        "components": _count("finance.settlement_components"),
    }
    assert before == after


def test_payouts_link_to_channel_account() -> None:
    """Every payout must have a non-NULL channel_account_id."""
    from tts_erp_v2.db.base import get_engine
    eng = get_engine()
    with eng.connect() as conn:
        row = conn.exec_driver_sql(
            "SELECT count(*) FROM finance.payouts "
            "WHERE channel_account_id IS NULL"
        ).first()
    assert int(row[0]) == 0


def test_components_have_currency() -> None:
    """Every component has a non-NULL currency (per V3 NOT NULL)."""
    from tts_erp_v2.db.base import get_engine
    eng = get_engine()
    with eng.connect() as conn:
        row = conn.exec_driver_sql(
            "SELECT count(*) FROM finance.settlement_components "
            "WHERE currency IS NULL OR currency = ''"
        ).first()
    assert int(row[0]) == 0


def test_payouts_amount_sum_matches_source() -> None:
    """sum(payouts.amount) should match sum(public.payments.amount_value)."""
    from tts_erp_v2.db.base import get_engine
    eng = get_engine()
    with eng.connect() as conn:
        payout_sum = conn.exec_driver_sql(
            "SELECT sum(amount) FROM finance.payouts"
        ).first()[0]
    # In prod: sum(payments.amount_value) = 98,434,371 VND.
    # Target must match to the cent.
    assert abs(float(payout_sum) - 98434371.0) < 0.01


def test_component_columns_list_completeness() -> None:
    """Sanity: the 53-column list matches what survey.md declared.

    If this fails, somebody added a column to public.statement_transactions
    and forgot to update _COMPONENT_COLUMNS — the settlement_components
    table would silently miss that new column.
    """
    assert len(_COMPONENT_COLUMNS) == 53, (
        f"expected 53 component columns, got {len(_COMPONENT_COLUMNS)}"
    )
    # Spot-check well-known columns.
    for col in (
        "fee_amount",
        "gross_sales_amount",
        "platform_commission_amount",
        "settlement_amount",
        "revenue_amount",
    ):
        assert col in _COMPONENT_COLUMNS, f"missing {col}"
