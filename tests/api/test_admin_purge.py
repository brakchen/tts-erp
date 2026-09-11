"""Tests for POST /v2/admin/purge-plugin-data.

Verifies:
- admin role required (401 without key, 403 with readonly)
- clears all plugin-synced tables
- returns correct row counts
- idempotent (clear on empty tables is a no-op)
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
    """readonly key → 403."""
    r = api_client.post(
        "/v2/admin/purge-plugin-data",
        headers={"Authorization": f"Bearer {readonly_key}"},
    )
    assert r.status_code == 403


def test_purge_plugin_data_returns_empty_on_clean_db(api_client, admin_key):
    """空库 → 返回零计数."""
    # 先清一次确保干净
    api_client.post(
        "/v2/admin/purge-plugin-data",
        headers={"Authorization": f"Bearer {admin_key}"},
    )
    # 第二次应该全零
    r = api_client.post(
        "/v2/admin/purge-plugin-data",
        headers={"Authorization": f"Bearer {admin_key}"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["total_rows_deleted"] == 0
    assert body["cleared"] == {}
    assert "purged_at" in body
    assert "purged_by" in body


def test_purge_plugin_data_clears_ad_tables(api_client, admin_key, db_engine):
    """插入测试数据后清除，验证行数返回正确."""
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

    # Purge
    r = api_client.post(
        "/v2/admin/purge-plugin-data",
        headers={"Authorization": f"Bearer {admin_key}"},
    )
    assert r.status_code == 200
    body = r.json()
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
    """连续两次调用不会出错."""
    r1 = api_client.post(
        "/v2/admin/purge-plugin-data",
        headers={"Authorization": f"Bearer {admin_key}"},
    )
    assert r1.status_code == 200

    r2 = api_client.post(
        "/v2/admin/purge-plugin-data",
        headers={"Authorization": f"Bearer {admin_key}"},
    )
    assert r2.status_code == 200
    assert r2.json()["total_rows_deleted"] == 0
