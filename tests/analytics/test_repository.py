"""Tests for ``tts_erp_v2/analytics/repository.py`` — v4 daily-sync-with-coverage.

Tests v4 structured upsert functions (upsert_daily_rows, upsert_today_rows,
upsert_monthly_rows), coverage queries, solidify, and plugin_logs.
"""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest
from sqlalchemy import text

pytestmark = [pytest.mark.domain_api, pytest.mark.layer_integration]

_ENDPOINT = "/oec_ads/shopping/v1/oec/stat/post_product_list"
_SELLER = "TEST_repo_seller"
_ADV = "TEST_repo_adv"
_CAMPAIGN = "TEST_repo_campaign"
_SPU = "TEST_SPU_1"


@pytest.fixture(autouse=True)
def _wipe_analytics_rows(db_engine):
    _wipe(db_engine)
    yield
    _wipe(db_engine)


def _wipe(db_engine) -> None:
    with db_engine.begin() as conn:
        # pi-lens-ignore: python-sql-injection — literal SQL
        conn.execute(
            text("DELETE FROM analytics.ad_daily WHERE seller_id LIKE 'TEST_%'")
        )
        conn.execute(
            text("DELETE FROM analytics.ad_today WHERE seller_id LIKE 'TEST_%'")
        )
        conn.execute(
            text("DELETE FROM analytics.ad_monthly WHERE seller_id LIKE 'TEST_%'")
        )
        conn.execute(
            text("DELETE FROM analytics.ad_raw_log WHERE seller_id LIKE 'TEST_%'")
        )
        conn.execute(
            text("DELETE FROM analytics.plugin_logs WHERE seller_id LIKE 'TEST_%'")
        )


def _base_row(**overrides) -> dict:
    row = {
        "product_id": _SPU,
        "mixed_real_cost": 100.50,
        "onsite_roi2_shopping_sku": 5,
        "onsite_roi2_shopping_value": 250.75,
        "onsite_mixed_real_roi2_shopping": 2.5,
    }
    row.update(overrides)
    return row


# ---------------------------------------------------------------------------
# upsert_daily_rows
# ---------------------------------------------------------------------------


def test_upsert_daily_rows_inserts(db_session):
    """v4 daily rows 写入 ad_daily + ad_raw_log。"""
    from tts_erp_v2.analytics import repository

    rows = [_base_row()]
    inserted = repository.upsert_daily_rows(
        db_session,
        seller_id=_SELLER,
        advertiser_id=_ADV,
        endpoint=_ENDPOINT,
        campaign_id=_CAMPAIGN,
        day=date(2026, 9, 1),
        rows=rows,
        request_url="https://ads.tiktok.com/...",
        request_body={"url": "https://ads.tiktok.com/...", "body": {}},
        response_status=200,
        response_body={"status": 200, "body": {"data": {"rows": rows}}},
        created_at=datetime(2026, 9, 2, 12, 0, 0, tzinfo=UTC),
        request_id="TEST_req_daily1",
        source="tiktok-shop-data-sync",
    )
    assert inserted == 1

    # Verify ad_daily
    count = db_session.execute(
        text("SELECT count(*) FROM analytics.ad_daily WHERE seller_id = :s"),
        {"s": _SELLER},
    ).scalar()
    assert count == 1

    # Verify ad_raw_log
    log_count = db_session.execute(
        text(
            "SELECT count(*) FROM analytics.ad_raw_log WHERE seller_id = :s AND kind = 'daily'"
        ),
        {"s": _SELLER},
    ).scalar()
    assert log_count == 1


def test_upsert_daily_rows_idempotent(db_session):
    """v4 daily rows 幂等（ON CONFLICT DO NOTHING）。"""
    from tts_erp_v2.analytics import repository

    rows = [_base_row()]
    r1 = repository.upsert_daily_rows(
        sess=db_session,
        seller_id=_SELLER,
        advertiser_id=_ADV,
        endpoint=_ENDPOINT,
        campaign_id=_CAMPAIGN,
        day=date(2026, 9, 1),
        rows=rows,
        request_url="https://x/",
        request_body={},
        response_status=200,
        response_body={},
        created_at=datetime(2026, 9, 2, tzinfo=UTC),
        request_id="TEST_idem",
        source="t",
    )
    r2 = repository.upsert_daily_rows(
        sess=db_session,
        seller_id=_SELLER,
        advertiser_id=_ADV,
        endpoint=_ENDPOINT,
        campaign_id=_CAMPAIGN,
        day=date(2026, 9, 1),
        rows=rows,
        request_url="https://x/",
        request_body={},
        response_status=200,
        response_body={},
        created_at=datetime(2026, 9, 2, tzinfo=UTC),
        request_id="TEST_idem",
        source="t",
    )
    assert r1 == 1
    assert r2 == 0  # duplicate → nothing inserted

    count = db_session.execute(
        text("SELECT count(*) FROM analytics.ad_daily WHERE seller_id = :s"),
        {"s": _SELLER},
    ).scalar()
    assert count == 1


# ---------------------------------------------------------------------------
# upsert_today_rows
# ---------------------------------------------------------------------------


def test_upsert_today_rows_upserts(db_session):
    """v4 today rows 写入 ad_today（ON CONFLICT DO UPDATE）。"""
    from tts_erp_v2.analytics import repository

    rows = [_base_row()]
    repository.upsert_today_rows(
        db_session,
        seller_id=_SELLER,
        advertiser_id=_ADV,
        endpoint=_ENDPOINT,
        campaign_id=_CAMPAIGN,
        day=date(2026, 9, 10),
        rows=rows,
        request_url="https://x/",
        request_body={},
        response_status=200,
        response_body={},
        created_at=datetime(2026, 9, 10, 12, 0, 0, tzinfo=UTC),
        request_id="TEST_today1",
        source="t",
    )

    # Update same row with new cost → should update
    rows2 = [_base_row(mixed_real_cost=200.00)]
    repository.upsert_today_rows(
        db_session,
        seller_id=_SELLER,
        advertiser_id=_ADV,
        endpoint=_ENDPOINT,
        campaign_id=_CAMPAIGN,
        day=date(2026, 9, 10),
        rows=rows2,
        request_url="https://x/",
        request_body={},
        response_status=200,
        response_body={},
        created_at=datetime(2026, 9, 10, 12, 0, 30, tzinfo=UTC),
        request_id="TEST_today2",
        source="t",
    )

    count = db_session.execute(
        text("SELECT count(*) FROM analytics.ad_today WHERE seller_id = :s"),
        {"s": _SELLER},
    ).scalar()
    assert count == 1  # still one row (upsert)


# ---------------------------------------------------------------------------
# upsert_monthly_rows
# ---------------------------------------------------------------------------


def test_upsert_monthly_rows_inserts(db_session):
    """v4 monthly rows 写入 ad_monthly。"""
    from tts_erp_v2.analytics import repository

    rows = [_base_row()]
    inserted = repository.upsert_monthly_rows(
        db_session,
        seller_id=_SELLER,
        advertiser_id=_ADV,
        endpoint=_ENDPOINT,
        campaign_id=_CAMPAIGN,
        year_month="2026-09",
        rows=rows,
        request_url="https://x/",
        request_body={},
        response_status=200,
        response_body={},
        created_at=datetime(2026, 10, 1, tzinfo=UTC),
        request_id="TEST_monthly1",
        source="t",
    )
    assert inserted == 1

    count = db_session.execute(
        text("SELECT count(*) FROM analytics.ad_monthly WHERE seller_id = :s"),
        {"s": _SELLER},
    ).scalar()
    assert count == 1


# ---------------------------------------------------------------------------
# coverage queries
# ---------------------------------------------------------------------------


def test_get_coverage_daily_returns_map(db_session):
    """coverage daily 查询返回 {campaign_id: [days]}。"""
    from tts_erp_v2.analytics import repository

    repository.upsert_daily_rows(
        db_session,
        seller_id=_SELLER,
        advertiser_id=_ADV,
        endpoint=_ENDPOINT,
        campaign_id=_CAMPAIGN,
        day=date(2026, 9, 1),
        rows=[_base_row()],
        request_url="https://x/",
        request_body={},
        response_status=200,
        response_body={},
        created_at=datetime(2026, 9, 2, tzinfo=UTC),
        request_id="TEST_cov1",
        source="t",
    )
    repository.upsert_daily_rows(
        db_session,
        seller_id=_SELLER,
        advertiser_id=_ADV,
        endpoint=_ENDPOINT,
        campaign_id=_CAMPAIGN,
        day=date(2026, 9, 2),
        rows=[_base_row()],
        request_url="https://x/",
        request_body={},
        response_status=200,
        response_body={},
        created_at=datetime(2026, 9, 3, tzinfo=UTC),
        request_id="TEST_cov2",
        source="t",
    )

    result = repository.get_coverage_daily(
        db_session,
        seller_id=_SELLER,
        advertiser_id=_ADV,
        endpoint=_ENDPOINT,
        start_day=date(2026, 9, 1),
        end_day=date(2026, 9, 30),
    )
    assert _CAMPAIGN in result
    assert "2026-09-01" in result[_CAMPAIGN]
    assert "2026-09-02" in result[_CAMPAIGN]


def test_get_coverage_monthly_returns_map(db_session):
    """coverage monthly 查询返回 {campaign_id: [months]}。"""
    from tts_erp_v2.analytics import repository

    repository.upsert_monthly_rows(
        db_session,
        seller_id=_SELLER,
        advertiser_id=_ADV,
        endpoint=_ENDPOINT,
        campaign_id=_CAMPAIGN,
        year_month="2026-08",
        rows=[_base_row()],
        request_url="https://x/",
        request_body={},
        response_status=200,
        response_body={},
        created_at=datetime(2026, 9, 1, tzinfo=UTC),
        request_id="TEST_covm1",
        source="t",
    )

    result = repository.get_coverage_monthly(
        db_session,
        seller_id=_SELLER,
        advertiser_id=_ADV,
        endpoint=_ENDPOINT,
        start_month="2026-07",
        end_month="2026-12",
    )
    assert _CAMPAIGN in result
    assert "2026-08" in result[_CAMPAIGN]


# ---------------------------------------------------------------------------
# solidify
# ---------------------------------------------------------------------------


def test_solidify_yesterday_moves_today_to_daily(db_session):
    """solidify: ad_today 昨天 → ad_daily，然后清空 ad_today。"""
    from tts_erp_v2.analytics import repository

    # Insert today row for yesterday
    repository.upsert_today_rows(
        db_session,
        seller_id=_SELLER,
        advertiser_id=_ADV,
        endpoint=_ENDPOINT,
        campaign_id=_CAMPAIGN,
        day=date(2026, 9, 9),
        rows=[_base_row()],
        request_url="https://x/",
        request_body={},
        response_status=200,
        response_body={},
        created_at=datetime(2026, 9, 10, 1, 0, 0, tzinfo=UTC),
        request_id="TEST_sol1",
        source="t",
    )

    # Solidify
    pairs = repository.solidify_yesterday_scope_pairs(
        db_session, yesterday=date(2026, 9, 9)
    )
    assert (_SELLER, _ADV) in pairs

    repository.solidify_yesterday(
        db_session,
        seller_id=_SELLER,
        advertiser_id=_ADV,
        yesterday=date(2026, 9, 9),
    )

    # ad_today should be empty for that day
    today_count = db_session.execute(
        text(
            "SELECT count(*) FROM analytics.ad_today WHERE seller_id = :s AND day = :d"
        ),
        {"s": _SELLER, "d": date(2026, 9, 9)},
    ).scalar()
    assert today_count == 0

    # ad_daily should have the row
    daily_count = db_session.execute(
        text(
            "SELECT count(*) FROM analytics.ad_daily WHERE seller_id = :s AND day = :d"
        ),
        {"s": _SELLER, "d": date(2026, 9, 9)},
    ).scalar()
    assert daily_count == 1


# ---------------------------------------------------------------------------
# campaign-level endpoint 分流（fix/analytics-v4-campaign-rows）
# ---------------------------------------------------------------------------
# TikTok campaign_opt_log_list 返回 campaign-level 变更事件，rows 没有
# product_id，服务端 upsert_*_rows 入口走 archive-only 路径：只入 ad_raw_log，
# 不入 ad_daily / ad_today / ad_monthly。下游 spu_roi 读 ad_daily + ad_today
# 不会被污染。


_CAMPAIGN_LEVEL_ENDPOINT = "/oec_ads/shopping/v1/oec/stat/campaign_opt_log_list"


def _change_event_rows() -> list[dict]:
    return [
        {"change_id": "evt-001", "change_type": "BUDGET", "new_value": "150.00"},
        {"change_id": "evt-002", "change_type": "ROI", "new_value": "1.8"},
    ]


def test_is_product_level_endpoint():
    """白名单区分 product-level 与 campaign-level endpoint。"""
    from tts_erp_v2.analytics import repository

    assert repository.is_product_level_endpoint(
        "/oec_ads/shopping/v1/oec/stat/post_product_list"
    )
    assert repository.is_product_level_endpoint(
        "/oec_ads/shopping/v1/oec/stat/post_session_list"
    )
    # 不在白名单 = campaign-level（保守默认走 archive-only）
    assert not repository.is_product_level_endpoint(_CAMPAIGN_LEVEL_ENDPOINT)
    assert not repository.is_product_level_endpoint("/unknown/endpoint/v1/test")


def test_upsert_daily_rows_campaign_level_only_archives(db_session):
    """campaign-level endpoint:ad_daily 不写,ad_raw_log 写 1 行,product_id NULL。"""
    from tts_erp_v2.analytics import repository

    rows = _change_event_rows()
    inserted = repository.upsert_daily_rows(
        db_session,
        seller_id=_SELLER,
        advertiser_id=_ADV,
        endpoint=_CAMPAIGN_LEVEL_ENDPOINT,
        campaign_id=_CAMPAIGN,
        day=date(2026, 9, 1),
        rows=rows,
        request_url="https://ads.tiktok.com/...",
        request_body={"url": "https://ads.tiktok.com/...", "body": {}},
        response_status=200,
        response_body={
            "status": 200,
            "body": {"data": {"table": rows}},
        },
        created_at=datetime(2026, 9, 2, 12, 0, 0, tzinfo=UTC),
        request_id="TEST_req_camp_lvl_daily",
        source="tiktok-shop-data-sync",
    )
    assert inserted == 0

    # ad_daily 不写
    daily_count = db_session.execute(
        text("SELECT count(*) FROM analytics.ad_daily WHERE seller_id = :s"),
        {"s": _SELLER},
    ).scalar()
    assert daily_count == 0

    # ad_raw_log 写 1 行，product_id 为 NULL
    raw_row = db_session.execute(
        text(
            "SELECT product_id, kind, endpoint, campaign_id, response_body::text "
            "FROM analytics.ad_raw_log WHERE seller_id = :s"
        ),
        {"s": _SELLER},
    ).first()
    assert raw_row is not None
    assert raw_row[0] is None, "campaign-level rows 不应有 product_id"
    assert raw_row[1] == "daily"
    assert raw_row[2] == _CAMPAIGN_LEVEL_ENDPOINT
    assert raw_row[3] == _CAMPAIGN
    # response_body 完整保留原始 rows
    import json
    archived = json.loads(raw_row[4])
    assert archived["body"]["data"]["table"][0]["change_id"] == "evt-001"


def test_upsert_today_rows_campaign_level_only_archives(db_session):
    """campaign-level + kind=today:ad_today 不写,ad_raw_log 写 1 行。"""
    from tts_erp_v2.analytics import repository

    inserted = repository.upsert_today_rows(
        db_session,
        seller_id=_SELLER,
        advertiser_id=_ADV,
        endpoint=_CAMPAIGN_LEVEL_ENDPOINT,
        campaign_id=_CAMPAIGN,
        day=date(2026, 9, 9),
        rows=_change_event_rows(),
        request_url="https://x/",
        request_body={},
        response_status=200,
        response_body={"status": 200, "body": {"data": {"table": _change_event_rows()}}},
        created_at=datetime(2026, 9, 9, 1, 0, 0, tzinfo=UTC),
        request_id="TEST_req_camp_lvl_today",
        source="t",
    )
    assert inserted == 0

    today_count = db_session.execute(
        text("SELECT count(*) FROM analytics.ad_today WHERE seller_id = :s"),
        {"s": _SELLER},
    ).scalar()
    assert today_count == 0

    raw_count = db_session.execute(
        text(
            "SELECT count(*) FROM analytics.ad_raw_log "
            "WHERE seller_id = :s AND kind = 'today'"
        ),
        {"s": _SELLER},
    ).scalar()
    assert raw_count == 1


def test_upsert_monthly_rows_campaign_level_only_archives(db_session):
    """campaign-level + kind=monthly:ad_monthly 不写,ad_raw_log 写 1 行。"""
    from tts_erp_v2.analytics import repository

    inserted = repository.upsert_monthly_rows(
        db_session,
        seller_id=_SELLER,
        advertiser_id=_ADV,
        endpoint=_CAMPAIGN_LEVEL_ENDPOINT,
        campaign_id=_CAMPAIGN,
        year_month="2026-08",
        rows=_change_event_rows(),
        request_url="https://x/",
        request_body={},
        response_status=200,
        response_body={"status": 200, "body": {"data": {"table": _change_event_rows()}}},
        created_at=datetime(2026, 9, 1, 1, 0, 0, tzinfo=UTC),
        request_id="TEST_req_camp_lvl_monthly",
        source="t",
    )
    assert inserted == 0

    monthly_count = db_session.execute(
        text("SELECT count(*) FROM analytics.ad_monthly WHERE seller_id = :s"),
        {"s": _SELLER},
    ).scalar()
    assert monthly_count == 0

    raw_count = db_session.execute(
        text(
            "SELECT count(*) FROM analytics.ad_raw_log "
            "WHERE seller_id = :s AND kind = 'monthly' AND year_month = :ym"
        ),
        {"s": _SELLER, "ym": "2026-08"},
    ).scalar()
    assert raw_count == 1
