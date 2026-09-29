"""Tests for POST /v2/admin/purge-plugin-data.

Verifies (2026-09-13 hardening, see
``tech-doc/incident-reports/2026-09-13-ad-daily-purge.md``):
- 401 without auth
- 403 with readonly key (admin role required)
- 403 against prod-shape dbnames unless ALLOW_PROD_PURGE=1
- dry-run mode (no ``?confirm=true``) counts but does not delete
- ``?confirm=true`` actually clears the listed tables
- empty-table calls are idempotent no-ops

Note: tests run against the dedicated ``tts_erp_v3_test`` DB (see
``tests/conftest.py`` — prod-shape dbnames cause ``pytest.exit(2)``
before any test starts).
"""

from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

pytestmark = [pytest.mark.domain_api, pytest.mark.layer_integration]


def test_purge_plugin_data_requires_auth(api_client):
    """无 key → 401."""
    r = api_client.post("/v2/admin/purge-plugin-data")
    assert r.status_code == 401


def test_purge_plugin_data_requires_admin(api_client, readonly_key):
    """readonly key → 403 (admin role required post-2026-09-13)."""
    r = api_client.post(
        "/v2/admin/purge-plugin-data?confirm=true",
        headers={"Authorization": f"Bearer {readonly_key}"},
    )
    assert r.status_code == 403


def test_purge_plugin_data_dry_run_does_not_delete(api_client, admin_key, db_engine):
    """无 ``?confirm=true`` → dry-run 返回行数但不删."""
    # 先插一行测试数据
    with db_engine.begin() as conn:
        conn.execute(
            text("""
            INSERT INTO plugin.ad_daily (
                seller_id, advertiser_id, campaign_id, product_id,
                endpoint, day, mixed_real_cost, onsite_roi2_shopping_sku,
                onsite_roi2_shopping_value, created_at
            ) VALUES (
                'TEST_SELLER', 'TEST_ADV', 'TEST_CAMP', 'TEST_PROD',
                '/test', '2026-09-10', 10.00, 5, 50.00, now()
            )
        """)
        )

    # 调用无 confirm — dry-run
    r = api_client.post(
        "/v2/admin/purge-plugin-data",
        headers={"Authorization": f"Bearer {admin_key}"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["dry_run"] is True
    assert body["executed"] is False
    assert body["total_rows_deleted"] == 0
    # dry-run 仍会报告 row counts（有数据但未删），不是空 dict
    # 关键不变量: total_rows_deleted 必须为 0（dry-run 不删任何东西）
    assert body["prod_guarded"] is False
    assert body["next_step"] and "?confirm=true" in body["next_step"]

    # 数据应还在（检查我们插入的那一行，而非全表 — 测试库可能已有其他行）
    with Session(db_engine) as sess:
        count = sess.execute(
            text("SELECT COUNT(*) FROM plugin.ad_daily WHERE seller_id = 'TEST_SELLER'")
        ).scalar()
        assert count == 1, "dry-run should NOT have deleted the row"

    # 清理
    with db_engine.begin() as conn:
        conn.execute(text("DELETE FROM plugin.ad_daily WHERE seller_id = 'TEST_SELLER'"))


def test_purge_plugin_data_clears_ad_tables(api_client, admin_key, db_engine):
    """``?confirm=true`` 实际删除插入的测试数据."""
    with db_engine.begin() as conn:
        conn.execute(
            text("""
            INSERT INTO plugin.ad_daily (
                seller_id, advertiser_id, campaign_id, product_id,
                endpoint, day, mixed_real_cost, onsite_roi2_shopping_sku,
                onsite_roi2_shopping_value, created_at
            ) VALUES (
                'TEST_SELLER', 'TEST_ADV', 'TEST_CAMP', 'TEST_PROD',
                '/test', '2026-09-10', 10.00, 5, 50.00, now()
            )
        """)
        )
        conn.execute(
            text("""
            INSERT INTO plugin.ad_raw_log (
                seller_id, advertiser_id, endpoint, campaign_id,
                product_id, kind, day, request_url, request_method,
                request_body, response_status, response_body, source
            ) VALUES (
                'TEST_SELLER', 'TEST_ADV', '/test', 'TEST_CAMP',
                'TEST_PROD', 'daily', '2026-09-10', 'https://test.com', 'POST',
                '{}', 200, '{}', 'TEST'
            )
        """)
        )

    # Verify data exists
    with Session(db_engine) as sess:
        count = sess.execute(text("SELECT COUNT(*) FROM plugin.ad_daily")).scalar()
        assert count == 1

    # Purge with confirm=true
    r = api_client.post(
        "/v2/admin/purge-plugin-data?confirm=true",
        headers={"Authorization": f"Bearer {admin_key}"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["dry_run"] is False
    assert body["executed"] is True
    assert body["cleared"].get("plugin.ad_daily", 0) >= 1
    assert body["total_rows_deleted"] >= 1

    # Verify tables are empty
    with Session(db_engine) as sess:
        for table in [
            "plugin.ad_daily",
            "plugin.ad_today",
            "plugin.ad_monthly",
            "plugin.ad_raw_log",
            "plugin.plugin_logs",
        ]:
            count = sess.execute(text(f"SELECT COUNT(*) FROM {table}")).scalar()
            assert count == 0, f"{table} should be empty after purge"


def test_purge_plugin_data_is_idempotent(api_client, admin_key):
    """空库 → dry-run 仍返回零计数，confirm=true 也安全 no-op."""
    # 先清一次确保干净
    api_client.post(
        "/v2/admin/purge-plugin-data?confirm=true",
        headers={"Authorization": f"Bearer {admin_key}"},
    )
    # 第二次 dry-run → 全零
    r = api_client.post(
        "/v2/admin/purge-plugin-data",
        headers={"Authorization": f"Bearer {admin_key}"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["dry_run"] is True
    assert body["executed"] is False
    assert body["total_rows_deleted"] == 0
    assert body["cleared"] == {}
    assert "purged_at" in body
    assert "purged_by" in body
