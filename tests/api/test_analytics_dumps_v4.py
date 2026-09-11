"""HTTP 契约测试：POST /v2/analytics/sync/dumps protocol v4。

覆盖（tech-doc/analytics/daily-sync-with-coverage.md §8.1）：
- v4 daily 写入 ad_daily + ad_raw_log
- v4 today 写入 ad_today（覆盖）
- v4 monthly 写入 ad_monthly
- 重复写入 daily → ON CONFLICT DO NOTHING（inserted=0）
- 重复写入 today → ON CONFLICT DO UPDATE（值更新）
- 缺 rows → 400
- kind=daily 缺 day → 400
- kind=monthly 缺 yearMonth → 400
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text

pytestmark = [pytest.mark.domain_api, pytest.mark.layer_integration]

SELLER = "TEST_seller-dv4"
ADVERTISER = "TEST_adv-dv4"
CAMPAIGN = "TEST_campaign-dv4"
ENDPOINT = "/oec_ads/shopping/v1/oec/stat/post_product_list"


@pytest.fixture(autouse=True)
def _cleanup(db_engine):
    """Wipe TEST_ data from all analytics tables this test touches."""
    with db_engine.begin() as conn:
        for stmt in (
            "DELETE FROM analytics.ad_daily WHERE seller_id = :s",
            "DELETE FROM analytics.ad_today WHERE seller_id = :s",
            "DELETE FROM analytics.ad_monthly WHERE seller_id = :s",
            "DELETE FROM analytics.ad_raw_log WHERE seller_id = :s",
        ):
            # pi-lens-ignore: python-sql-injection
            conn.execute(text(stmt), {"s": SELLER})
    yield
    with db_engine.begin() as conn:
        for stmt in (
            "DELETE FROM analytics.ad_daily WHERE seller_id = :s",
            "DELETE FROM analytics.ad_today WHERE seller_id = :s",
            "DELETE FROM analytics.ad_monthly WHERE seller_id = :s",
            "DELETE FROM analytics.ad_raw_log WHERE seller_id = :s",
        ):
            # pi-lens-ignore: python-sql-injection
            conn.execute(text(stmt), {"s": SELLER})


def _dump_body_v4(
    *,
    kind: str = "daily",
    day: str = "2026-09-08",
    year_month: str | None = None,
    campaign: str = CAMPAIGN,
    rows: list[dict] | None = None,
    endpoint: str = ENDPOINT,
):
    """Build a protocol v4 dump body."""
    if rows is None:
        rows = [
            {
                "product_id": "TEST_PROD_1",
                "mixed_real_cost": "150.50",
                "onsite_roi2_shopping_sku": 10,
                "onsite_roi2_shopping_value": "2000.00",
                "onsite_mixed_real_roi2_shopping": "13.29",
            }
        ]
    dump: dict = {
        "kind": kind,
        "endpoint": endpoint,
        "campaignId": campaign,
        "method": "POST",
        "rows": rows,
        "request": {"url": "http://tiktok.test/", "body": {}},
        "response": {"status": 200, "body": {"data": {"table": rows}}},
        "createdAt": "2026-09-09T02:00:00.000Z",
    }
    if kind == "monthly":
        dump["yearMonth"] = year_month or "2026-08"
    else:
        dump["day"] = day
    return {
        "protocolVersion": 4,
        "requestId": str(uuid.uuid4()),
        "scope": {"sellerId": SELLER, "advertiserId": ADVERTISER},
        "dump": dump,
    }


def _post(api_client, key, body):
    """Helper: POST /v2/analytics/sync/dumps."""
    return api_client.post(
        "/v2/analytics/sync/dumps",
        headers={"Authorization": f"Bearer {key}"},
        json=body,
    )


def _ad_daily_count(db_engine) -> int:
    with db_engine.connect() as conn:
        # pi-lens-ignore: python-sql-injection
        return conn.execute(
            text("SELECT count(*) FROM analytics.ad_daily WHERE seller_id = :s"),
            {"s": SELLER},
        ).scalar()


def _ad_today_count(db_engine) -> int:
    with db_engine.connect() as conn:
        # pi-lens-ignore: python-sql-injection
        return conn.execute(
            text("SELECT count(*) FROM analytics.ad_today WHERE seller_id = :s"),
            {"s": SELLER},
        ).scalar()


def _ad_monthly_count(db_engine) -> int:
    with db_engine.connect() as conn:
        # pi-lens-ignore: python-sql-injection
        return conn.execute(
            text("SELECT count(*) FROM analytics.ad_monthly WHERE seller_id = :s"),
            {"s": SELLER},
        ).scalar()


def _ad_raw_log_count(db_engine) -> int:
    with db_engine.connect() as conn:
        # pi-lens-ignore: python-sql-injection
        return conn.execute(
            text("SELECT count(*) FROM analytics.ad_raw_log WHERE seller_id = :s"),
            {"s": SELLER},
        ).scalar()


# ─── v4 dump 写入测试 ───────────────────────────────────────────────


def test_dumps_v4_daily(api_client, readwrite_key, db_engine):
    """POST /dumps v4 kind=daily 写入 ad_daily + ad_raw_log。"""
    r = _post(api_client, readwrite_key, _dump_body_v4(kind="daily", day="2026-09-08"))
    assert r.status_code == 200, r.text
    data = r.json()["data"]
    assert data["kind"] == "daily"
    assert data["day"] == "2026-09-08"
    assert data["rowCount"] == 1
    assert data["inserted"] == 1
    assert data["duplicates"] == 0
    assert _ad_daily_count(db_engine) == 1
    assert _ad_raw_log_count(db_engine) == 1


def test_dumps_v4_today(api_client, readwrite_key, db_engine):
    """POST /dumps v4 kind=today 写入 ad_today（覆盖）。"""
    r = _post(api_client, readwrite_key, _dump_body_v4(kind="today", day="2026-09-09"))
    assert r.status_code == 200, r.text
    data = r.json()["data"]
    assert data["kind"] == "today"
    assert data["day"] == "2026-09-09"
    assert data["rowCount"] == 1
    assert data["inserted"] == 1
    assert _ad_today_count(db_engine) == 1
    assert _ad_raw_log_count(db_engine) == 1


def test_dumps_v4_monthly(api_client, readwrite_key, db_engine):
    """POST /dumps v4 kind=monthly 写入 ad_monthly。"""
    r = _post(
        api_client, readwrite_key, _dump_body_v4(kind="monthly", year_month="2026-08")
    )
    assert r.status_code == 200, r.text
    data = r.json()["data"]
    assert data["kind"] == "monthly"
    assert data["yearMonth"] == "2026-08"
    assert data["rowCount"] == 1
    assert data["inserted"] == 1
    assert data["duplicates"] == 0
    assert _ad_monthly_count(db_engine) == 1
    assert _ad_raw_log_count(db_engine) == 1


def test_dumps_v4_duplicate_daily(api_client, readwrite_key, db_engine):
    """重复写入 daily → ON CONFLICT DO NOTHING（inserted=0）。"""
    body = _dump_body_v4(kind="daily", day="2026-09-08")
    r1 = _post(api_client, readwrite_key, body)
    assert r1.status_code == 200
    assert r1.json()["data"]["inserted"] == 1

    # 同 (seller, adv, campaign, product, endpoint, day) 再次写入
    r2 = _post(api_client, readwrite_key, body)
    assert r2.status_code == 200
    data2 = r2.json()["data"]
    assert data2["inserted"] == 0
    assert data2["duplicates"] == 1
    assert _ad_daily_count(db_engine) == 1  # 仍只有 1 行


def test_dumps_v4_today_overwrite(api_client, readwrite_key, db_engine):
    """重复写入 today → ON CONFLICT DO UPDATE（值更新）。"""
    rows_v1 = [
        {
            "product_id": "TEST_PROD_1",
            "mixed_real_cost": "100.00",
            "onsite_roi2_shopping_sku": 5,
            "onsite_roi2_shopping_value": "1000.00",
            "onsite_mixed_real_roi2_shopping": "10.00",
        }
    ]
    rows_v2 = [
        {
            "product_id": "TEST_PROD_1",
            "mixed_real_cost": "200.00",
            "onsite_roi2_shopping_sku": 15,
            "onsite_roi2_shopping_value": "3000.00",
            "onsite_mixed_real_roi2_shopping": "20.00",
        }
    ]
    r1 = _post(
        api_client,
        readwrite_key,
        _dump_body_v4(kind="today", day="2026-09-09", rows=rows_v1),
    )
    assert r1.status_code == 200
    assert r1.json()["data"]["inserted"] == 1
    assert _ad_today_count(db_engine) == 1

    r2 = _post(
        api_client,
        readwrite_key,
        _dump_body_v4(kind="today", day="2026-09-09", rows=rows_v2),
    )
    assert r2.status_code == 200
    assert _ad_today_count(db_engine) == 1  # still 1 row

    # Verify the value was updated
    from decimal import Decimal

    with db_engine.connect() as conn:
        # pi-lens-ignore: python-sql-injection
        cost = conn.execute(
            text(
                "SELECT mixed_real_cost FROM analytics.ad_today "
                "WHERE seller_id = :s AND day = '2026-09-09'"
            ),
            {"s": SELLER},
        ).scalar()
    assert Decimal(str(cost)) == Decimal("200.00")


# ─── v4 schema 校验 ────────────────────────────────────────────────


def test_dumps_v4_missing_rows(api_client, readwrite_key, db_engine):
    """缺 rows → 400 SCHEMA_INVALID。"""
    body = _dump_body_v4(kind="daily", day="2026-09-08")
    del body["dump"]["rows"]
    r = _post(api_client, readwrite_key, body)
    assert r.status_code == 400
    assert r.json()["code"] == "SCHEMA_INVALID"


def test_dumps_v4_missing_day(api_client, readwrite_key, db_engine):
    """kind=daily 缺 day → 400 SCHEMA_INVALID。"""
    body = _dump_body_v4(kind="daily", day="2026-09-08")
    del body["dump"]["day"]
    r = _post(api_client, readwrite_key, body)
    assert r.status_code == 400
    assert r.json()["code"] == "SCHEMA_INVALID"


def test_dumps_v4_missing_year_month(api_client, readwrite_key, db_engine):
    """kind=monthly 缺 yearMonth → 400 SCHEMA_INVALID。"""
    body = _dump_body_v4(kind="monthly", year_month="2026-08")
    del body["dump"]["yearMonth"]
    r = _post(api_client, readwrite_key, body)
    assert r.status_code == 400
    assert r.json()["code"] == "SCHEMA_INVALID"


# ─── api-managed 守卫：data_source='api' 的店铺停止插件写入 ──────────


def _seed_seller_data_source(db_engine, data_source: str) -> None:
    """给 SELLER 种一行 shops（指定 data_source）；conftest TEST_ wipe 清理。"""
    with db_engine.begin() as conn:
        # pi-lens-ignore: python-sql-injection — 字面量 SQL + 绑定参数
        conn.execute(
            text(
                "INSERT INTO commerce.shops "
                "(platform, shop_id, account_name, status, data_source) "
                "VALUES ('tiktok', :s, 'TEST shop', 'active', :ds) "
                "ON CONFLICT (platform, shop_id) DO UPDATE SET data_source = :ds"
            ),
            {"s": SELLER, "ds": data_source},
        )


def test_dumps_v4_api_managed_seller_is_ignored(api_client, readwrite_key, db_engine):
    """data_source='api' 的店铺：广告 dump 静默忽略（200 + api_managed），
    ad_daily / ad_raw_log 都不写 —— 防广告域 API 与插件双写。"""
    _seed_seller_data_source(db_engine, "api")
    r = _post(api_client, readwrite_key, _dump_body_v4())
    assert r.status_code == 200, r.text
    assert r.json()["data"]["status"] == "api_managed"
    with db_engine.connect() as conn:
        # pi-lens-ignore: python-sql-injection — 字面量 SQL + 绑定参数
        n_daily = conn.execute(
            text("SELECT count(*) FROM analytics.ad_daily WHERE seller_id = :s"),
            {"s": SELLER},
        ).scalar_one()
        # pi-lens-ignore: python-sql-injection — 字面量 SQL + 绑定参数
        n_raw = conn.execute(
            text("SELECT count(*) FROM analytics.ad_raw_log WHERE seller_id = :s"),
            {"s": SELLER},
        ).scalar_one()
    assert n_daily == 0, "api_managed 店铺不得写 ad_daily"
    assert n_raw == 0, "api_managed 店铺不得写 ad_raw_log"


def test_dumps_v4_plugin_registered_seller_still_written(
    api_client, readwrite_key, db_engine
):
    """对照组：同店 data_source='plugin'（人工注册）时 dump 照常写入。"""
    _seed_seller_data_source(db_engine, "plugin")
    r = _post(api_client, readwrite_key, _dump_body_v4())
    assert r.status_code == 200, r.text
    assert r.json()["data"]["inserted"] >= 1


# ─── campaign-level endpoint：rows 没有 product_id，不写结构化表 ─────


CAMPAIGN_CHANGE_LOG_ENDPOINT = (
    "/oec_ads/shopping/v1/oec/stat/campaign_opt_log_list"
)


def _campaign_change_log_rows() -> list[dict]:
    """模拟 campaign_opt_log_list 真实响应：rows 是 campaign-level 变更事件，
    没有 product_id 字段（这是真值，campaign-change-log endpoint 永远没 product_id）。"""
    return [
        {
            "change_id": "evt-001",
            "campaign_id": CAMPAIGN,
            "change_type": "BUDGET",
            "old_value": "100.00",
            "new_value": "150.00",
            "modified_at": "2026-09-08T12:34:56Z",
        },
        {
            "change_id": "evt-002",
            "campaign_id": CAMPAIGN,
            "change_type": "ROI",
            "old_value": "1.5",
            "new_value": "1.8",
            "modified_at": "2026-09-08T12:35:00Z",
        },
    ]


def test_dumps_v4_campaign_change_log_only_archives(
    api_client, readwrite_key, db_engine
):
    """campaign_opt_log_list 是 campaign-level endpoint，rows 没有 product_id：
    - HTTP 200（不是 500）
    - ad_daily / ad_today / ad_monthly 全 0 行（不要污染 product 级结构化表）
    - ad_raw_log 写 1 行（rows 完整保留在 response.body 存档）
    - response.kind = "campaign_level"（明确告诉插件这是 campaign-level）"""
    body = _dump_body_v4(
        kind="daily",
        day="2026-09-08",
        endpoint=CAMPAIGN_CHANGE_LOG_ENDPOINT,
        rows=_campaign_change_log_rows(),
    )
    r = _post(api_client, readwrite_key, body)
    assert r.status_code == 200, r.text
    data = r.json()["data"]
    assert data["status"] == "campaign_level", data
    assert data["rowCount"] == len(_campaign_change_log_rows())
    assert data["inserted"] == 0  # 结构化表无新增

    assert _ad_daily_count(db_engine) == 0
    assert _ad_today_count(db_engine) == 0
    assert _ad_monthly_count(db_engine) == 0
    assert _ad_raw_log_count(db_engine) == 1

    # ad_raw_log 里 product_id 是 NULL（不是 ''、不是 ''campaign'' 这种 sentinel）
    with db_engine.connect() as conn:
        # pi-lens-ignore: python-sql-injection
        product_id, response_body = conn.execute(
            text(
                "SELECT product_id, response_body::text "
                "FROM analytics.ad_raw_log WHERE seller_id = :s"
            ),
            {"s": SELLER},
        ).first()
    assert product_id is None
    # response.body 完整保留原始 rows（不丢字段）—— service 把
    # payload.dump.response 整体存入 response_body 列，所以结构是
    # {status, body: {data: {table: rows}}}。
    import json
    archived = json.loads(response_body)
    assert archived["body"]["data"]["table"][0]["change_id"] == "evt-001"
    assert archived["body"]["data"]["table"][1]["change_type"] == "ROI"
    assert archived["status"] == 200


def test_dumps_v4_monthly_campaign_change_log_only_archives(
    api_client, readwrite_key, db_engine
):
    """campaign_opt_log_list + kind=monthly 同样：rows 不进 ad_monthly，只进 ad_raw_log。"""
    body = _dump_body_v4(
        kind="monthly",
        year_month="2026-08",
        endpoint=CAMPAIGN_CHANGE_LOG_ENDPOINT,
        rows=_campaign_change_log_rows(),
    )
    r = _post(api_client, readwrite_key, body)
    assert r.status_code == 200, r.text
    data = r.json()["data"]
    assert data["status"] == "campaign_level", data
    assert data["inserted"] == 0
    assert _ad_monthly_count(db_engine) == 0
    assert _ad_raw_log_count(db_engine) == 1


def test_dumps_v4_today_campaign_change_log_only_archives(
    api_client, readwrite_key, db_engine
):
    """campaign_opt_log_list + kind=today 同样：rows 不进 ad_today，只进 ad_raw_log。"""
    body = _dump_body_v4(
        kind="today",
        day="2026-09-09",
        endpoint=CAMPAIGN_CHANGE_LOG_ENDPOINT,
        rows=_campaign_change_log_rows(),
    )
    r = _post(api_client, readwrite_key, body)
    assert r.status_code == 200, r.text
    data = r.json()["data"]
    assert data["status"] == "campaign_level", data
    assert data["inserted"] == 0
    assert _ad_today_count(db_engine) == 0
    assert _ad_raw_log_count(db_engine) == 1
