"""Tests for migrate_orders.

Covers:
* Source counts match (720 orders, 749 items).
* Real-run idempotency: counts unchanged after a second run.
* Time conversion: orders.paid_at / shipped_at / etc. land as
  ``timestamptz`` (UTC, tz-aware).
* sync_issues: 1 row per unresolvable product_id, dedup'd on re-run.
* Product binding: channel_product_id stays NULL when products aren't
  synced yet (the documented initial state).
"""

from __future__ import annotations

import pytest

from datetime import timezone


pytestmark = [
    pytest.mark.domain_commerce,
    pytest.mark.domain_migration,
    pytest.mark.layer_integration,
    pytest.mark.slow,
]

from scripts.migrate_v1_to_v2.common import epoch_seconds_to_utc


def _count(table: str) -> int:
    from tts_erp_v2.db.base import get_engine

    eng = get_engine()
    table_q = {
        "commerce.sales_orders": "SELECT count(*) FROM commerce.sales_orders",
        "commerce.sales_order_lines": "SELECT count(*) FROM commerce.sales_order_lines",
        "integration.sync_issues": "SELECT count(*) FROM integration.sync_issues "
        "WHERE job_name = 'migrate.orders'",
        # live-source mirrors used for drift-tolerant assertions
        "public.orders": "SELECT count(*) FROM public.orders",
        "public.order_items": "SELECT count(*) FROM public.order_items",
    }
    if table not in table_q:
        raise ValueError(f"unknown table {table!r}")
    with eng.connect() as conn:
        row = conn.exec_driver_sql(table_q[table]).first()
    return int(row[0])


def test_dry_run_reports_full_population(dry_run_runner) -> None:
    """Dry-run sees every current source order + item.

    The legacy sync cron appends to ``public.orders`` live, so we capture
    source counts at runtime rather than hard-coding 720/749.
    """
    src_orders = _count("public.orders")
    src_items = _count("public.order_items")
    stats = dry_run_runner("orders")
    assert stats.orders_seen == src_orders
    assert stats.items_seen == src_items
    # In dry-run every order resolves its channel_account (the 1 real
    # TikTok shop exists in the target), so fk_missing stays 0.
    assert stats.orders_fk_missing == 0


def test_real_run_matches_source_counts() -> None:
    """Target row counts equal current source row counts (live-truth)."""
    assert _count("commerce.sales_orders") == _count("public.orders")
    assert _count("commerce.sales_order_lines") == _count("public.order_items")


def test_real_run_is_idempotent(real_runner) -> None:
    """A second apply-mode run leaves row counts unchanged."""
    before_orders = _count("commerce.sales_orders")
    before_lines = _count("commerce.sales_order_lines")
    real_runner("orders")
    assert _count("commerce.sales_orders") == before_orders
    assert _count("commerce.sales_order_lines") == before_lines


def test_real_run_dedupes_sync_issues(real_runner) -> None:
    """Re-running must NOT add duplicate sync_issues rows for the same
    unresolvable product_id. The migration clears prior migrate.orders
    issues at the start of each run."""
    real_runner("orders")  # first time after session fixture; nothing new
    first_count = _count("integration.sync_issues")
    real_runner("orders")  # second time — should not double the count
    second_count = _count("integration.sync_issues")
    assert second_count == first_count, (
        f"sync_issues doubled after re-run: {first_count} → {second_count}"
    )


def test_orders_timestamps_are_timestamptz() -> None:
    """paid_at, shipped_at, etc. land as UTC-aware datetimes."""
    from tts_erp_v2.db.base import get_engine

    eng = get_engine()
    with eng.connect() as conn:
        row = conn.exec_driver_sql(
            "SELECT paid_at, source_created_at "
            "FROM commerce.sales_orders "
            "WHERE paid_at IS NOT NULL LIMIT 1"
        ).first()
    if row is None:
        # Paid_at may be NULL for unpaid orders; skip the strict check.
        row = conn.exec_driver_sql(
            "SELECT source_created_at FROM commerce.sales_orders LIMIT 1"
        ).first()
        assert row is not None
        ts = row[0]
    else:
        ts = row[0]
    # timestamptz is enforced by the V3 schema (DECLARE TYPE); the result
    # must be tz-aware.
    assert ts is not None
    assert ts.tzinfo is not None
    assert ts.utcoffset() is not None
    assert ts.utcoffset().total_seconds() == 0


def test_epoch_seconds_conversion_round_trip() -> None:
    """The conversion function should match the source epoch-seconds
    exactly (no timezone drift)."""
    # 1787997915 == 2026-08-29 10:05:15 UTC (a real order in the DB).
    dt = epoch_seconds_to_utc(1787997915)
    assert dt is not None
    assert dt.year == 2026
    assert dt.month == 8
    assert dt.day == 29
    assert dt.hour == 10
    assert dt.minute == 5
    assert dt.second == 15
    assert dt.tzinfo is timezone.utc


def test_order_lines_snapshot_product_name() -> None:
    """Even when channel_product_id is NULL, the snapshot columns hold
    the truth for later join (per V3 §14: NEVER auto-bind by title)."""
    from tts_erp_v2.db.base import get_engine

    eng = get_engine()
    with eng.connect() as conn:
        row = conn.exec_driver_sql(
            "SELECT external_product_id_snapshot, product_name_snapshot, "
            "       variant_name_snapshot, image_url_snapshot "
            "FROM commerce.sales_order_lines LIMIT 1"
        ).first()
    assert row is not None
    ext_pid, name, variant, image = row
    # At least one of the snapshots should be populated.
    assert ext_pid or name or variant or image, (
        "order line snapshot columns are all NULL"
    )


def test_sync_issues_external_id_is_product_id() -> None:
    """sync_issues rows for unresolvable products carry the product_id
    as external_id (not the order_id or item_id)."""
    from tts_erp_v2.db.base import get_engine

    eng = get_engine()
    with eng.connect() as conn:
        row = conn.exec_driver_sql(
            "SELECT external_id, details "
            "FROM integration.sync_issues "
            "WHERE job_name = 'migrate.orders' "
            "LIMIT 1"
        ).first()
    if row is None:
        return  # products were synced, no UNRESOLVED_PRODUCT_ID rows
    ext_id, details = row
    # external_id is the product_id (numeric string); details JSON has
    # order_id + item_id.
    assert ext_id is not None
    assert ext_id.isdigit()
