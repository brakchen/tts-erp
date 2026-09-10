"""HTTP 契约测试：GET /v2/analytics/sync/coverage 批量覆盖查询。

覆盖（tech-doc/analytics/daily-sync-with-coverage.md §8.1）：
- 空库 → coveredPeriods=[], totalCovered=0
- 插入 ad_daily 行 → 对应日期在 coveredPeriods 中
- 插入 ad_monthly 行 → 对应月份在 coveredPeriods 中
- 范围外的日期/月份不返回
- 不同 campaign 数据互不影响
- 无权限 → 403
- 非法 endpoint → 400
- startDay > endDay → 400
"""

from __future__ import annotations

import pytest
from sqlalchemy import text

pytestmark = [pytest.mark.domain_api, pytest.mark.layer_integration]

SELLER = "TEST_seller-cov"
ADVERTISER = "TEST_adv-cov"
CAMPAIGN_1 = "TEST_campaign-cov-1"
CAMPAIGN_2 = "TEST_campaign-cov-2"
ENDPOINT = "/oec_ads/shopping/v1/oec/stat/post_product_list"


@pytest.fixture(autouse=True)
def _cleanup(db_engine):
    """Wipe TEST_ data from all analytics tables this test touches."""
    with db_engine.begin() as conn:
        conn.execute(
            text("DELETE FROM analytics.ad_daily WHERE seller_id = :s"), {"s": SELLER}
        )
        conn.execute(
            text("DELETE FROM analytics.ad_monthly WHERE seller_id = :s"), {"s": SELLER}
        )
        conn.execute(
            text("DELETE FROM analytics.ad_today WHERE seller_id = :s"), {"s": SELLER}
        )
        conn.execute(
            text("DELETE FROM analytics.ad_raw_log WHERE seller_id = :s"), {"s": SELLER}
        )
    yield
    with db_engine.begin() as conn:
        conn.execute(
            text("DELETE FROM analytics.ad_daily WHERE seller_id = :s"), {"s": SELLER}
        )
        conn.execute(
            text("DELETE FROM analytics.ad_monthly WHERE seller_id = :s"), {"s": SELLER}
        )
        conn.execute(
            text("DELETE FROM analytics.ad_today WHERE seller_id = :s"), {"s": SELLER}
        )
        conn.execute(
            text("DELETE FROM analytics.ad_raw_log WHERE seller_id = :s"), {"s": SELLER}
        )


def _insert_ad_daily(
    db_engine,
    *,
    day: str,
    campaign_id: str = CAMPAIGN_1,
    product_id: str = "TEST_PROD_1",
) -> None:
    """Insert one row into analytics.ad_daily for testing."""
    with db_engine.begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO analytics.ad_daily (
                    seller_id, advertiser_id, campaign_id, product_id, endpoint, day,
                    mixed_real_cost, onsite_roi2_shopping_sku, onsite_roi2_shopping_value,
                    onsite_mixed_real_roi2_shopping, metrics_extra, created_at
                ) VALUES (
                    :seller, :adv, :campaign, :product, :ep, CAST(:day AS date),
                    100.00, 10, 2000.00, 20.00, '{}', now()
                )
                ON CONFLICT ON CONSTRAINT uq_ad_daily DO NOTHING
                """
            ),
            {
                "seller": SELLER,
                "adv": ADVERTISER,
                "campaign": campaign_id,
                "product": product_id,
                "ep": ENDPOINT,
                "day": day,
            },
        )


def _insert_ad_monthly(
    db_engine,
    *,
    year_month: str,
    campaign_id: str = CAMPAIGN_1,
    product_id: str = "TEST_PROD_1",
) -> None:
    """Insert one row into analytics.ad_monthly for testing."""
    with db_engine.begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO analytics.ad_monthly (
                    seller_id, advertiser_id, campaign_id, product_id, endpoint, year_month,
                    mixed_real_cost, onsite_roi2_shopping_sku, onsite_roi2_shopping_value,
                    onsite_mixed_real_roi2_shopping, metrics_extra, created_at
                ) VALUES (
                    :seller, :adv, :campaign, :product, :ep, :ym,
                    3000.00, 300, 60000.00, 20.00, '{}', now()
                )
                ON CONFLICT ON CONSTRAINT uq_ad_monthly DO NOTHING
                """
            ),
            {
                "seller": SELLER,
                "adv": ADVERTISER,
                "campaign": campaign_id,
                "product": product_id,
                "ep": ENDPOINT,
                "ym": year_month,
            },
        )


def _coverage_get(api_client, key, **params):
    """Helper: GET /v2/analytics/sync/coverage with given params."""
    return api_client.get(
        "/v2/analytics/sync/coverage",
        headers={"Authorization": f"Bearer {key}"},
        params=params,
    )


# ─── §8.1 测试用例 ─────────────────────────────────────────────────


def test_coverage_daily_empty(api_client, readwrite_key, db_engine):
    """空库，kind=daily → campaigns={}, totalRequested 正确。"""
    r = _coverage_get(
        api_client,
        readwrite_key,
        sellerId=SELLER,
        advertiserId=ADVERTISER,
        endpoint=ENDPOINT,
        kind="daily",
        startDay="2026-09-01",
        endDay="2026-09-10",
    )
    assert r.status_code == 200, r.text
    data = r.json()["data"]
    assert data["kind"] == "daily"
    assert data["startDay"] == "2026-09-01"
    assert data["endDay"] == "2026-09-10"
    assert data["totalRequested"] == 10
    assert data["campaigns"] == {}


def test_coverage_daily_with_data(api_client, readwrite_key, db_engine):
    """插入 ad_daily 行后，对应日期在 coveredPeriods 中。"""
    _insert_ad_daily(db_engine, day="2026-09-03")
    _insert_ad_daily(db_engine, day="2026-09-05")
    _insert_ad_daily(db_engine, day="2026-09-07")

    r = _coverage_get(
        api_client,
        readwrite_key,
        sellerId=SELLER,
        advertiserId=ADVERTISER,
        endpoint=ENDPOINT,
        kind="daily",
        startDay="2026-09-01",
        endDay="2026-09-10",
    )
    assert r.status_code == 200, r.text
    data = r.json()["data"]
    campaigns = data["campaigns"]
    assert CAMPAIGN_1 in campaigns
    cov = campaigns[CAMPAIGN_1]
    assert cov["totalCovered"] == 3
    assert cov["coveredPeriods"] == ["2026-09-03", "2026-09-05", "2026-09-07"]


def test_coverage_monthly_with_data(api_client, readwrite_key, db_engine):
    """插入 ad_monthly 行后，对应月份在 coveredPeriods 中。"""
    _insert_ad_monthly(db_engine, year_month="2026-06")
    _insert_ad_monthly(db_engine, year_month="2026-07")
    _insert_ad_monthly(db_engine, year_month="2026-08")

    r = _coverage_get(
        api_client,
        readwrite_key,
        sellerId=SELLER,
        advertiserId=ADVERTISER,
        endpoint=ENDPOINT,
        kind="monthly",
        startMonth="2026-01",
        endMonth="2026-09",
    )
    assert r.status_code == 200, r.text
    data = r.json()["data"]
    assert data["kind"] == "monthly"
    assert data["startMonth"] == "2026-01"
    assert data["endMonth"] == "2026-09"
    assert data["totalRequested"] == 9
    campaigns = data["campaigns"]
    assert CAMPAIGN_1 in campaigns
    cov = campaigns[CAMPAIGN_1]
    assert cov["totalCovered"] == 3
    assert cov["coveredPeriods"] == ["2026-06", "2026-07", "2026-08"]


def test_coverage_out_of_range(api_client, readwrite_key, db_engine):
    """范围外的日期/月份不返回。"""
    _insert_ad_daily(db_engine, day="2026-09-05")
    _insert_ad_monthly(db_engine, year_month="2026-08")

    # daily: 查询 2026-08-01~2026-08-31，不包含 09-05
    r_daily = _coverage_get(
        api_client,
        readwrite_key,
        sellerId=SELLER,
        advertiserId=ADVERTISER,
        endpoint=ENDPOINT,
        kind="daily",
        startDay="2026-08-01",
        endDay="2026-08-31",
    )
    assert r_daily.status_code == 200
    assert r_daily.json()["data"]["campaigns"] == {}

    # monthly: 查询 2026-01~2026-06，不包含 2026-08
    r_monthly = _coverage_get(
        api_client,
        readwrite_key,
        sellerId=SELLER,
        advertiserId=ADVERTISER,
        endpoint=ENDPOINT,
        kind="monthly",
        startMonth="2026-01",
        endMonth="2026-06",
    )
    assert r_monthly.status_code == 200
    assert r_monthly.json()["data"]["campaigns"] == {}


def test_coverage_campaign_isolation(api_client, readwrite_key, db_engine):
    """不同 campaign 的数据互不影响。"""
    _insert_ad_daily(db_engine, day="2026-09-05", campaign_id=CAMPAIGN_1)
    _insert_ad_daily(db_engine, day="2026-09-06", campaign_id=CAMPAIGN_2)

    r = _coverage_get(
        api_client,
        readwrite_key,
        sellerId=SELLER,
        advertiserId=ADVERTISER,
        endpoint=ENDPOINT,
        kind="daily",
        startDay="2026-09-01",
        endDay="2026-09-10",
    )
    assert r.status_code == 200, r.text
    campaigns = r.json()["data"]["campaigns"]
    assert campaigns[CAMPAIGN_1]["coveredPeriods"] == ["2026-09-05"]
    assert campaigns[CAMPAIGN_1]["totalCovered"] == 1
    assert campaigns[CAMPAIGN_2]["coveredPeriods"] == ["2026-09-06"]
    assert campaigns[CAMPAIGN_2]["totalCovered"] == 1


def test_coverage_scope_denied(api_client, readonly_key, db_engine):
    """无 readwrite 权限 → 403（coverage 端点要求 readwrite 角色）。"""
    r = _coverage_get(
        api_client,
        readonly_key,
        sellerId=SELLER,
        advertiserId=ADVERTISER,
        endpoint=ENDPOINT,
        kind="daily",
        startDay="2026-09-01",
        endDay="2026-09-10",
    )
    assert r.status_code == 403


def test_coverage_unknown_endpoint(api_client, readwrite_key, db_engine):
    """非法 endpoint → 400 SCHEMA_INVALID。"""
    r = _coverage_get(
        api_client,
        readwrite_key,
        sellerId=SELLER,
        advertiserId=ADVERTISER,
        endpoint="/invalid/endpoint/does/not/exist",
        kind="daily",
        startDay="2026-09-01",
        endDay="2026-09-10",
    )
    assert r.status_code == 400
    assert r.json()["code"] == "SCHEMA_INVALID"
    assert "unknown endpoint" in r.json()["message"]


def test_coverage_start_after_end(api_client, readwrite_key, db_engine):
    """startDay > endDay → 400 SCHEMA_INVALID。"""
    r = _coverage_get(
        api_client,
        readwrite_key,
        sellerId=SELLER,
        advertiserId=ADVERTISER,
        endpoint=ENDPOINT,
        kind="daily",
        startDay="2026-09-10",
        endDay="2026-09-01",
    )
    assert r.status_code == 400
    assert r.json()["code"] == "SCHEMA_INVALID"
    assert "startDay" in r.json()["message"]


def test_coverage_monthly_start_after_end(api_client, readwrite_key, db_engine):
    """startMonth > endMonth → 400 SCHEMA_INVALID。"""
    r = _coverage_get(
        api_client,
        readwrite_key,
        sellerId=SELLER,
        advertiserId=ADVERTISER,
        endpoint=ENDPOINT,
        kind="monthly",
        startMonth="2026-09",
        endMonth="2026-01",
    )
    assert r.status_code == 400
    assert r.json()["code"] == "SCHEMA_INVALID"
    assert "startMonth" in r.json()["message"]
