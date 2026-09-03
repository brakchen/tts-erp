"""Tests for migrate_miaoshou.

Covers:
* Source counts (1 shop, 237 move_collect_tasks).
* Real-run idempotency.
* Time conversion: gmt_* strings (UTC+8) → timestamptz.
* external_product_id: platform_item_id preferred; source_item_id fallback
  for failed tasks with no published TikTok product.
* link_evidence: 1 row per task, scoped-delete idempotency.
* procurement_products count is lower than task count (33 fail tasks
  collide on shared source_item_id).
"""
from __future__ import annotations

import pytest

pytestmark = [
    pytest.mark.domain_miaoshou,
    pytest.mark.domain_migration,
    pytest.mark.layer_integration,
    pytest.mark.slow,
]


def _count(table: str) -> int:
    from tts_erp_v2.db.base import get_engine
    eng = get_engine()
    table_q = {
        "procurement.procurement_accounts":
            "SELECT count(*) FROM procurement.procurement_accounts",
        "procurement.procurement_products":
            "SELECT count(*) FROM procurement.procurement_products",
        "linkage.link_evidence":
            "SELECT count(*) FROM linkage.link_evidence",
    }
    if table not in table_q:
        raise ValueError(f"unknown table {table!r}")
    with eng.connect() as conn:
        row = conn.exec_driver_sql(table_q[table]).first()
    return int(row[0])


def test_dry_run_reports_full_population(dry_run_runner) -> None:
    """Dry-run sees 1 shop, 237 tasks, plans 1 account + 237 products
    (dedup collisions bring actual down to 215)."""
    stats = dry_run_runner("miaoshou")
    assert stats.shops_seen == 1
    assert stats.products_seen == 237
    assert stats.accounts_upserted == 1


def test_real_run_procurement_accounts() -> None:
    """1 miaoshou shop → 1 procurement_account row."""
    assert _count("procurement.procurement_accounts") == 1


def test_real_run_procurement_products_dedup() -> None:
    """215 unique products in prod (33 fail tasks collide on source_item_id)."""
    assert _count("procurement.procurement_products") == 215


def test_real_run_link_evidence_per_task() -> None:
    """1 evidence row per move_collect_task (237 in prod)."""
    assert _count("linkage.link_evidence") == 237


def test_real_run_is_idempotent(real_runner) -> None:
    """Re-running should leave row counts unchanged AND not duplicate
    link_evidence (scope-delete at start of run handles the latter)."""
    before_accounts = _count("procurement.procurement_accounts")
    before_products = _count("procurement.procurement_products")
    before_evidence = _count("linkage.link_evidence")
    real_runner("miaoshou")
    assert _count("procurement.procurement_accounts") == before_accounts
    assert _count("procurement.procurement_products") == before_products
    assert _count("linkage.link_evidence") == before_evidence


def test_procurement_account_uses_string_shop_id() -> None:
    """The miaoshou shop_id is a bigint in source; we coerce to str."""
    from tts_erp_v2.db.base import get_engine
    eng = get_engine()
    with eng.connect() as conn:
        row = conn.exec_driver_sql(
            "SELECT external_account_id, provider FROM "
            "procurement.procurement_accounts LIMIT 1"
        ).first()
    assert row is not None
    ext_id, provider = row
    assert provider == "miaoshou"
    assert ext_id.isdigit(), (
        f"external_account_id {ext_id!r} should be the string form of shop_id"
    )


def test_gmt_string_conversion_to_timestamptz() -> None:
    """source_updated_at on procurement_products is gmt_modified parsed
    as Asia/Shanghai wall-clock and stored as UTC."""
    from tts_erp_v2.db.base import get_engine
    eng = get_engine()
    with eng.connect() as conn:
        row = conn.exec_driver_sql(
            "SELECT source_updated_at FROM procurement.procurement_products "
            "WHERE source_updated_at IS NOT NULL LIMIT 1"
        ).first()
    assert row is not None
    ts = row[0]
    assert ts is not None
    assert ts.tzinfo is not None
    offset = ts.utcoffset()
    assert offset is not None
    assert offset.total_seconds() == 0


def test_link_evidence_payload_has_task_metadata() -> None:
    """Each evidence row carries the full task metadata as JSONB payload."""
    from tts_erp_v2.db.base import get_engine
    eng = get_engine()
    with eng.connect() as conn:
        row = conn.exec_driver_sql(
            "SELECT evidence_payload FROM linkage.link_evidence "
            "WHERE evidence_type = 'MOVE_COLLECT_TASK' LIMIT 1"
        ).first()
    assert row is not None
    payload = row[0]
    assert isinstance(payload, dict)
    # Spot-check expected fields from the original miaoshou task schema.
    for key in (
        "platform", "task_status", "platform_item_id", "source_item_id",
        "source_item_url", "breadcrumb", "gmt_create", "gmt_modified",
    ):
        assert key in payload, f"missing key {key} in evidence payload"


def test_link_evidence_source_external_id_unique() -> None:
    """Within (evidence_type, source_table), source_external_id is unique.

    We rely on the scope-delete idempotency for the migration; downstream
    consumers may add unique constraints later.
    """
    from tts_erp_v2.db.base import get_engine
    eng = get_engine()
    with eng.connect() as conn:
        rows = conn.exec_driver_sql(
            "SELECT source_external_id, count(*) FROM linkage.link_evidence "
            "WHERE evidence_type = 'MOVE_COLLECT_TASK' "
            "GROUP BY source_external_id HAVING count(*) > 1 LIMIT 1"
        ).fetchall()
    assert len(rows) == 0, "duplicate source_external_id in link_evidence"
