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
            text("DELETE FROM plugin.ad_daily WHERE seller_id = :s"), {"s": SELLER}
        )
        conn.execute(
            text("DELETE FROM plugin.ad_monthly WHERE seller_id = :s"), {"s": SELLER}
        )
        conn.execute(
            text("DELETE FROM plugin.ad_today WHERE seller_id = :s"), {"s": SELLER}
        )
        conn.execute(
            text("DELETE FROM plugin.ad_raw_log WHERE seller_id = :s"), {"s": SELLER}
        )
    yield
    with db_engine.begin() as conn:
        conn.execute(
            text("DELETE FROM plugin.ad_daily WHERE seller_id = :s"), {"s": SELLER}
        )
        conn.execute(
            text("DELETE FROM plugin.ad_monthly WHERE seller_id = :s"), {"s": SELLER}
        )
        conn.execute(
            text("DELETE FROM plugin.ad_today WHERE seller_id = :s"), {"s": SELLER}
        )
        conn.execute(
            text("DELETE FROM plugin.ad_raw_log WHERE seller_id = :s"), {"s": SELLER}
        )


def _insert_ad_daily(
    db_engine,
    *,
    day: str,
    campaign_id: str = CAMPAIGN_1,
    product_id: str = "TEST_PROD_1",
) -> None:
    """Insert one row into plugin.ad_daily for testing."""
    with db_engine.begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO plugin.ad_daily (
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
    """Insert one row into plugin.ad_monthly for testing."""
    with db_engine.begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO plugin.ad_monthly (
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


def test_coverage_requested_campaign_ids_includes_empty_campaigns(
    api_client, readwrite_key, db_engine
):
    """插件带 expected campaign IDs 时，零覆盖计划也必须返回空 entry。"""
    _insert_ad_daily(db_engine, day="2026-09-03", campaign_id=CAMPAIGN_1)

    r = _coverage_get(
        api_client,
        readwrite_key,
        sellerId=SELLER,
        advertiserId=ADVERTISER,
        endpoint=ENDPOINT,
        kind="daily",
        startDay="2026-09-01",
        endDay="2026-09-10",
        campaignId=[CAMPAIGN_1, CAMPAIGN_2],
    )
    assert r.status_code == 200, r.text
    data = r.json()["data"]
    assert set(data["campaigns"]) == {CAMPAIGN_1, CAMPAIGN_2}
    assert data["campaigns"][CAMPAIGN_1]["coveredPeriods"] == ["2026-09-03"]
    assert data["campaigns"][CAMPAIGN_2] == {
        "coveredPeriods": [],
        "totalCovered": 0,
    }
    assert data["pagination"] == {
        "page": 1,
        "pageSize": 500,
        "totalCampaigns": 2,
        "totalPages": 1,
        "hasMore": False,
    }


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


# ─── §8.2 分页（2026-09-11 加）─────────────────────────────────────
# 修 /coverage 无分页/上限隐患 #3：服务端加 page/pageSize + pagination 元数据；
# 客户端 fetchBatchCoverage 自动迭代合并。

# PAGER_SELLER 跟前面 SELLER 隔离，避免 fixture 串扰

# PAGER_SELLER 跟前面 SELLER 隔离，避免 fixture 串扰
PAGER_SELLER = "TEST_seller-cov-pager"
PAGER_ADVERTISER = "TEST_adv-cov-pager"
PAGER_ENDPOINT = "/oec_ads/shopping/v1/oec/stat/post_product_list"
PAGER_CAMPAIGN_PREFIX = "TEST_campaign-cov-pager-"


def _seed_many_campaigns(db_engine, *, n_campaigns: int, days: list[str]) -> None:
    """Insert n_campaigns campaigns × days, each campaign covers all days."""
    with db_engine.begin() as conn:
        for i in range(n_campaigns):
            cid = f"{PAGER_CAMPAIGN_PREFIX}{i:04d}"
            for d in days:
                conn.execute(
                    text(
                        """
                        INSERT INTO plugin.ad_daily (
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
                        "seller": PAGER_SELLER,
                        "adv": PAGER_ADVERTISER,
                        "campaign": cid,
                        "product": f"PROD_{i}",
                        "ep": PAGER_ENDPOINT,
                        "day": d,
                    },
                )


@pytest.fixture(autouse=True)
def _cleanup_pager(db_engine):
    with db_engine.begin() as conn:
        conn.execute(
            text("DELETE FROM plugin.ad_daily WHERE seller_id = :s"),
            {"s": PAGER_SELLER},
        )
        conn.execute(
            text("DELETE FROM plugin.ad_monthly WHERE seller_id = :s"),
            {"s": PAGER_SELLER},
        )
        conn.execute(
            text("DELETE FROM plugin.ad_today WHERE seller_id = :s"),
            {"s": PAGER_SELLER},
        )
        conn.execute(
            text("DELETE FROM plugin.ad_raw_log WHERE seller_id = :s"),
            {"s": PAGER_SELLER},
        )
    yield
    with db_engine.begin() as conn:
        conn.execute(
            text("DELETE FROM plugin.ad_daily WHERE seller_id = :s"),
            {"s": PAGER_SELLER},
        )
        conn.execute(
            text("DELETE FROM plugin.ad_monthly WHERE seller_id = :s"),
            {"s": PAGER_SELLER},
        )
        conn.execute(
            text("DELETE FROM plugin.ad_today WHERE seller_id = :s"),
            {"s": PAGER_SELLER},
        )
        conn.execute(
            text("DELETE FROM plugin.ad_raw_log WHERE seller_id = :s"),
            {"s": PAGER_SELLER},
        )


def _params(page=None, pageSize=None, **extra):
    p = {
        "sellerId": PAGER_SELLER,
        "advertiserId": PAGER_ADVERTISER,
        "endpoint": PAGER_ENDPOINT,
        "kind": "daily",
        "startDay": "2026-09-01",
        "endDay": "2026-09-05",
    }
    if page is not None:
        p["page"] = page
    if pageSize is not None:
        p["pageSize"] = pageSize
    p.update(extra)
    return p


# ─── 分页功能 ───


def test_coverage_pagination_default(api_client, readwrite_key, db_engine):
    """不传 page/pageSize → 默认 page=1, pageSize=500，返回所有 campaign + pagination 元数据。"""
    _seed_many_campaigns(db_engine, n_campaigns=3, days=["2026-09-01", "2026-09-03"])
    r = _coverage_get(api_client, readwrite_key, **_params())
    assert r.status_code == 200, r.text
    data = r.json()["data"]
    assert "pagination" in data
    p = data["pagination"]
    assert p["page"] == 1
    assert p["pageSize"] == 500
    assert p["totalCampaigns"] == 3
    assert p["totalPages"] == 1
    assert p["hasMore"] is False
    assert len(data["campaigns"]) == 3


def test_coverage_requested_campaign_ids_page_two_keeps_empty_entries(
    api_client, readwrite_key, db_engine
):
    """expected campaign 分页在第二页仍返回零覆盖计划，不被 SQL offset 二次跳过。"""
    requested = ["TEST_requested-campaign-1", "TEST_requested-campaign-2", "TEST_requested-campaign-3"]
    r = _coverage_get(
        api_client,
        readwrite_key,
        **_params(page=2, pageSize=2, campaignId=requested),
    )
    assert r.status_code == 200, r.text
    data = r.json()["data"]
    assert data["campaigns"] == {
        requested[2]: {"coveredPeriods": [], "totalCovered": 0},
    }
    assert data["pagination"]["totalCampaigns"] == 3
    assert data["pagination"]["totalPages"] == 2
    assert data["pagination"]["hasMore"] is False


def test_coverage_pagination_basic(api_client, readwrite_key, db_engine):
    """300 campaigns, pageSize=100 → 3 页，hasMore 一致。"""
    _seed_many_campaigns(db_engine, n_campaigns=300, days=["2026-09-01"])
    # page 1
    r1 = _coverage_get(api_client, readwrite_key, **_params(page=1, pageSize=100))
    d1 = r1.json()["data"]
    assert d1["pagination"] == {
        "page": 1,
        "pageSize": 100,
        "totalCampaigns": 300,
        "totalPages": 3,
        "hasMore": True,
    }
    assert len(d1["campaigns"]) == 100
    # page 2
    r2 = _coverage_get(api_client, readwrite_key, **_params(page=2, pageSize=100))
    d2 = r2.json()["data"]
    assert d2["pagination"]["page"] == 2
    assert d2["pagination"]["hasMore"] is True
    assert len(d2["campaigns"]) == 100
    # page 3
    r3 = _coverage_get(api_client, readwrite_key, **_params(page=3, pageSize=100))
    d3 = r3.json()["data"]
    assert d3["pagination"]["page"] == 3
    assert d3["pagination"]["hasMore"] is False
    assert len(d3["campaigns"]) == 100
    # 三页 campaign_id 互不重叠、合并为 300 个
    all_cids = (
        set(d1["campaigns"].keys())
        | set(d2["campaigns"].keys())
        | set(d3["campaigns"].keys())
    )
    assert len(all_cids) == 300


def test_coverage_pagination_last_page_partial(api_client, readwrite_key, db_engine):
    """250 campaigns, pageSize=100 → page 1=100, page 2=100, page 3=50, hasMore=False。"""
    _seed_many_campaigns(db_engine, n_campaigns=250, days=["2026-09-01"])
    r1 = _coverage_get(api_client, readwrite_key, **_params(page=1, pageSize=100))
    r2 = _coverage_get(api_client, readwrite_key, **_params(page=2, pageSize=100))
    r3 = _coverage_get(api_client, readwrite_key, **_params(page=3, pageSize=100))
    assert len(r1.json()["data"]["campaigns"]) == 100
    assert len(r2.json()["data"]["campaigns"]) == 100
    assert len(r3.json()["data"]["campaigns"]) == 50
    assert r3.json()["data"]["pagination"]["hasMore"] is False


def test_coverage_pagination_beyond_end(api_client, readwrite_key, db_engine):
    """请求超出 totalPages 的 page → campaigns={}, hasMore=False（不 4xx）。"""
    _seed_many_campaigns(db_engine, n_campaigns=10, days=["2026-09-01"])
    r = _coverage_get(api_client, readwrite_key, **_params(page=99, pageSize=5))
    assert r.status_code == 200
    data = r.json()["data"]
    assert data["campaigns"] == {}
    assert data["pagination"]["totalCampaigns"] == 10
    assert data["pagination"]["totalPages"] == 2
    assert data["pagination"]["hasMore"] is False


def test_coverage_pagination_pagesize_too_large(api_client, readwrite_key, db_engine):
    """pageSize > 1000 → 400 SCHEMA_INVALID。"""
    r = _coverage_get(api_client, readwrite_key, **_params(page=1, pageSize=5000))
    assert r.status_code == 400
    assert r.json()["code"] == "SCHEMA_INVALID"
    assert "pageSize" in r.json()["message"]


def test_coverage_pagination_page_invalid(api_client, readwrite_key, db_engine):
    """page=0 或负数 → 400 SCHEMA_INVALID。"""
    for bad in (0, -1):
        r = _coverage_get(api_client, readwrite_key, **_params(page=bad, pageSize=10))
        assert r.status_code == 400, r.text
        assert r.json()["code"] == "SCHEMA_INVALID"


def test_coverage_pagination_pagesize_zero(api_client, readwrite_key, db_engine):
    """pageSize=0 → 400。"""
    r = _coverage_get(api_client, readwrite_key, **_params(page=1, pageSize=0))
    assert r.status_code == 400


def test_coverage_pagination_empty_result(api_client, readwrite_key, db_engine):
    """空库 → campaigns={}, totalCampaigns=0, totalPages=0, hasMore=False。"""
    r = _coverage_get(api_client, readwrite_key, **_params(page=1, pageSize=50))
    assert r.status_code == 200
    data = r.json()["data"]
    assert data["campaigns"] == {}
    p = data["pagination"]
    assert p["totalCampaigns"] == 0
    assert p["totalPages"] == 0
    assert p["hasMore"] is False
    assert p["page"] == 1
    assert p["pageSize"] == 50


def test_coverage_pagination_single_page(api_client, readwrite_key, db_engine):
    """3 campaigns < pageSize=50 → 单页，totalPages=1, hasMore=False。"""
    _seed_many_campaigns(db_engine, n_campaigns=3, days=["2026-09-01"])
    r = _coverage_get(api_client, readwrite_key, **_params(page=1, pageSize=50))
    data = r.json()["data"]
    assert data["pagination"]["totalPages"] == 1
    assert data["pagination"]["hasMore"] is False
    assert len(data["campaigns"]) == 3


def test_coverage_pagination_stable_order(api_client, readwrite_key, db_engine):
    """多次分页请求必须返回稳定的 campaign_id 顺序（同一 page 多次）。"""
    _seed_many_campaigns(db_engine, n_campaigns=20, days=["2026-09-01"])
    r1 = _coverage_get(api_client, readwrite_key, **_params(page=1, pageSize=7))
    r2 = _coverage_get(api_client, readwrite_key, **_params(page=1, pageSize=7))
    cids1 = list(r1.json()["data"]["campaigns"].keys())
    cids2 = list(r2.json()["data"]["campaigns"].keys())
    assert cids1 == cids2
    # page 2 不会跟 page 1 重叠
    r3 = _coverage_get(api_client, readwrite_key, **_params(page=2, pageSize=7))
    cids3 = set(r3.json()["data"]["campaigns"].keys())
    assert not (set(cids1) & cids3)
