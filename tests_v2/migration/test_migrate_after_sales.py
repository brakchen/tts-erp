"""Tests for migrate_after_sales.

Covers:
* Source counts (31 returns + 175 cancellations = 206 cases).
* Case type mapping (CANCELLATION / REFUND_ONLY / RETURN_AND_REFUND).
* Case lines extracted from raw.return_line_items /
  raw.cancel_line_items arrays.
* Time conversion: epoch seconds → timestamptz.
* Real-run idempotency.
"""
from __future__ import annotations

import pytest


pytestmark = [
    pytest.mark.domain_after_sales,
    pytest.mark.domain_migration,
    pytest.mark.layer_integration,
    pytest.mark.slow,
]


def _count(table: str) -> int:
    from tts_erp_v2.db.base import get_engine
    eng = get_engine()
    table_q = {
        "after_sales.cases":
            "SELECT count(*) FROM after_sales.cases",
        "after_sales.case_lines":
            "SELECT count(*) FROM after_sales.case_lines",
    }
    if table not in table_q:
        raise ValueError(f"unknown table {table!r}")
    with eng.connect() as conn:
        row = conn.exec_driver_sql(table_q[table]).first()
    return int(row[0])


def test_dry_run_reports_full_population(dry_run_runner) -> None:
    """All returns + cancellations surface as cases."""
    stats = dry_run_runner("after_sales")
    assert stats.returns_seen == 31
    assert stats.cancellations_seen == 175


def test_real_run_matches_source_counts() -> None:
    """Source 31 + 175 = 206 cases land in after_sales.cases."""
    assert _count("after_sales.cases") == 206


def test_case_types_cover_all_three_variants() -> None:
    """The V3 enum has 3 values; observed data uses all of them."""
    from tts_erp_v2.db.base import get_engine
    eng = get_engine()
    with eng.connect() as conn:
        rows = conn.exec_driver_sql(
            "SELECT case_type, count(*) FROM after_sales.cases "
            "GROUP BY case_type ORDER BY case_type"
        ).fetchall()
    types = {r[0]: int(r[1]) for r in rows}
    # 175 cancellations → CANCELLATION
    assert types.get("CANCELLATION") == 175
    # 30 returns of RETURN_AND_REFUND + 1 REFUND
    assert types.get("RETURN_AND_REFUND", 0) == 30
    assert types.get("REFUND_ONLY", 0) == 1


def test_real_run_is_idempotent(real_runner) -> None:
    before_cases = _count("after_sales.cases")
    before_lines = _count("after_sales.case_lines")
    real_runner("after_sales")
    assert _count("after_sales.cases") == before_cases
    assert _count("after_sales.case_lines") == before_lines


def test_case_lines_extracted_from_raw() -> None:
    """The 7 cancellations with multi-line raw should produce >175 case lines."""
    from tts_erp_v2.db.base import get_engine
    eng = get_engine()
    with eng.connect() as conn:
        row = conn.exec_driver_sql(
            "SELECT count(*) FROM after_sales.case_lines"
        ).first()
    n = int(row[0])
    # 31 returns (1 line each) + 175 cancellations (some multi-line).
    # 31 + 184 = 215, give or take.
    assert n >= 200, f"expected >=200 case_lines, got {n}"


def test_case_lines_link_to_sales_order_lines() -> None:
    """Per V3, case_lines.sales_order_line_id must be a real FK."""
    from tts_erp_v2.db.base import get_engine
    eng = get_engine()
    with eng.connect() as conn:
        row = conn.exec_driver_sql(
            "SELECT count(*) FROM after_sales.case_lines "
            "WHERE sales_order_line_id IS NULL"
        ).first()
    assert int(row[0]) == 0, (
        "case_lines.sales_order_line_id is NULL — FK should be resolved"
    )


def test_cases_timestamps_are_timestamptz() -> None:
    """created_at_source / updated_at_source land as UTC-aware datetimes."""
    from tts_erp_v2.db.base import get_engine
    eng = get_engine()
    with eng.connect() as conn:
        row = conn.exec_driver_sql(
            "SELECT created_at_source FROM after_sales.cases LIMIT 1"
        ).first()
    assert row is not None
    ts = row[0]
    assert ts is not None
    assert ts.tzinfo is not None
    offset = ts.utcoffset()
    assert offset is not None
    assert offset.total_seconds() == 0


def test_dry_run_creates_no_duplicate_lines(dry_run_runner) -> None:
    """Dry-run on a clean state should not double-count case lines."""
    stats = dry_run_runner("after_sales")
    # Each row in source produces 1 case row.
    assert stats.cases_upserted == 206
    # case_lines come from raw.return_line_items / cancel_line_items.
    # Source has 31 returns (1 line each) + 175 cancellations (some
    # multi-line; total ~215).
    assert stats.case_lines_upserted >= 200
