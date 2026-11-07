"""HTTP 契约测试：POST /v2/analytics/sync/dumps protocol v4。

覆盖（docs/design/daily-sync-with-coverage.md §8.1）：
- v4 daily 写入 ad_daily + ad_raw_log
- v4 today 写入 ad_today（覆盖）
- monthly dump 被拒绝
- 重复写入 daily → ON CONFLICT DO NOTHING（inserted=0）
- 重复写入 today → ON CONFLICT DO UPDATE（值更新）
- 缺 rows → 400
- kind=daily 缺 day → 400
- kind=monthly → 400
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

pytestmark = [pytest.mark.domain_api, pytest.mark.layer_integration]

SELLER = "TEST_seller-dv4"
ADVERTISER = "TEST_adv-dv4"
CAMPAIGN = "TEST_campaign-dv4"
ENDPOINT = "/oec_ads/shopping/v1/oec/stat/post_product_list"


@pytest.fixture(autouse=True)
def _cleanup(db_engine):
    """Wipe TEST_ data from all analytics tables this test touches."""
    statements = (
        "DELETE FROM plugin.ad_daily WHERE seller_id = :s",
        "DELETE FROM plugin.ad_today WHERE seller_id = :s",
        "DELETE FROM plugin.ad_raw_log WHERE seller_id = :s",
        "DELETE FROM plugin.campaign_opt_logs WHERE seller_id = :s",
    )
    with db_engine.begin() as conn:
        for stmt in statements:
            # pi-lens-ignore: python-sql-injection
            conn.execute(text(stmt), {"s": SELLER})
    yield
    with db_engine.begin() as conn:
        for stmt in statements:
            # pi-lens-ignore: python-sql-injection
            conn.execute(text(stmt), {"s": SELLER})


def _dump_body_v4(
    *,
    kind: str = "daily",
    day: str = "2026-09-08",
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
            text("SELECT count(*) FROM plugin.ad_daily WHERE seller_id = :s"),
            {"s": SELLER},
        ).scalar()


def _ad_today_count(db_engine) -> int:
    with db_engine.connect() as conn:
        # pi-lens-ignore: python-sql-injection
        return conn.execute(
            text("SELECT count(*) FROM plugin.ad_today WHERE seller_id = :s"),
            {"s": SELLER},
        ).scalar()


def _ad_raw_log_count(db_engine) -> int:
    with db_engine.connect() as conn:
        # pi-lens-ignore: python-sql-injection
        return conn.execute(
            text("SELECT count(*) FROM plugin.ad_raw_log WHERE seller_id = :s"),
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


def test_dumps_v4_daily_correction_updates_existing_natural_key(
    api_client, readwrite_key, db_engine
):
    """天级数据同自然键重传时，后到的校准值必须覆盖旧值。"""
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
        _dump_body_v4(kind="daily", day="2026-09-08", rows=rows_v1),
    )
    assert r1.status_code == 200
    assert r1.json()["data"]["inserted"] == 1

    r2 = _post(
        api_client,
        readwrite_key,
        _dump_body_v4(kind="daily", day="2026-09-08", rows=rows_v2),
    )
    assert r2.status_code == 200
    data2 = r2.json()["data"]
    assert data2["inserted"] == 1
    assert data2["duplicates"] == 0
    assert _ad_daily_count(db_engine) == 1  # 仍只有 1 行
    with db_engine.connect() as conn:
        cost = conn.execute(
            text(
                "SELECT mixed_real_cost FROM plugin.ad_daily WHERE seller_id = :s AND day = '2026-09-08'"
            ),
            {"s": SELLER},
        ).scalar()
    assert Decimal(str(cost)) == Decimal("200.00")


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
                "SELECT mixed_real_cost FROM plugin.ad_today "
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


def test_dumps_v4_rejects_monthly(api_client, readwrite_key):
    """Monthly dump is retired and rejected at the wire boundary."""
    body = _dump_body_v4(kind="monthly")
    r = _post(api_client, readwrite_key, body)
    assert r.status_code == 400
    assert r.json()["code"] == "SCHEMA_INVALID"


# ─── campaign-level endpoint：rows 没有 product_id，不写结构化表 ─────


CAMPAIGN_CHANGE_LOG_ENDPOINT = "/oec_ads/shopping/v1/oec/stat/campaign_opt_log_list"


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
    - ad_daily / ad_today 全 0 行（不要污染 product 级结构化表）
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
    assert _ad_raw_log_count(db_engine) == 1

    # ad_raw_log 里 product_id 是 NULL（不是 ''、不是 ''campaign'' 这种 sentinel）
    with db_engine.connect() as conn:
        # pi-lens-ignore: python-sql-injection
        product_id, response_body = conn.execute(
            text(
                "SELECT product_id, response_body::text "
                "FROM plugin.ad_raw_log WHERE seller_id = :s"
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


@pytest.mark.parametrize(
    "failed_write", ["upsert_campaign_opt_logs", "upsert_daily_rows"]
)
def test_campaign_change_log_failure_rolls_back_both_writes(
    api_client, readwrite_key, db_engine, monkeypatch, failed_write
):
    from tts_erp_v2.plugin.ads import repository

    body = _dump_body_v4(endpoint=CAMPAIGN_CHANGE_LOG_ENDPOINT, rows=[])
    body["dump"]["response"]["body"]["data"]["logs"] = [
        {
            "id": "TEST_atomic_campaign_log",
            "object_id": CAMPAIGN,
            "opt_time": "2026-09-08 15:40:46",
        }
    ]

    def fail(*args, **kwargs):
        raise RuntimeError("simulated persistence failure")

    def log_count():
        with db_engine.connect() as conn:
            return conn.execute(
                text(
                    "SELECT count(*) FROM plugin.campaign_opt_logs WHERE seller_id = :s"
                ),
                {"s": SELLER},
            ).scalar_one()

    with monkeypatch.context() as patch:
        patch.setattr(repository, failed_write, fail)
        r = _post(api_client, readwrite_key, body)
        assert r.status_code == 500, r.text
        assert _ad_raw_log_count(db_engine) == 0
        assert log_count() == 0

    r = _post(api_client, readwrite_key, body)
    assert r.status_code == 200, r.text
    assert _ad_raw_log_count(db_engine) == 1
    assert log_count() == 1


@pytest.mark.parametrize(
    "invalid",
    [
        None,
        {},
        [{}],
        [
            {"id": "TEST_valid_before_invalid", "opt_time": "2026-09-08 15:40:46"},
            {"id": "TEST_invalid", "opt_time": "invalid"},
        ],
    ],
)
def test_campaign_change_log_rejects_invalid_rows_without_coverage(
    api_client, readwrite_key, db_engine, invalid
):
    body = _dump_body_v4(endpoint=CAMPAIGN_CHANGE_LOG_ENDPOINT, rows=[])
    body["dump"]["response"]["body"]["data"]["logs"] = invalid
    r = _post(api_client, readwrite_key, body)

    assert r.status_code == 422, r.text
    assert r.json()["code"] == "INVALID_CAMPAIGN_CHANGE_LOG"
    assert _ad_raw_log_count(db_engine) == 0
    with db_engine.connect() as conn:
        assert (
            conn.execute(
                text(
                    "SELECT count(*) FROM plugin.campaign_opt_logs WHERE seller_id = :s"
                ),
                {"s": SELLER},
            ).scalar_one()
            == 0
        )


def test_campaign_change_log_requires_native_logs_or_a_legacy_table(
    api_client, readwrite_key, db_engine
):
    body = _dump_body_v4(endpoint=CAMPAIGN_CHANGE_LOG_ENDPOINT, rows=[])
    body["dump"]["response"]["body"]["data"] = {}
    r = _post(api_client, readwrite_key, body)

    assert r.status_code == 422, r.text
    assert _ad_raw_log_count(db_engine) == 0


def test_campaign_change_log_cannot_overwrite_another_scope(
    api_client, readwrite_key, db_engine
):
    from tts_erp_v2.plugin.ads import repository

    log = {
        "id": "TEST_scope_campaign_log",
        "object_id": CAMPAIGN,
        "opt_time": "2026-09-08 15:40:46",
    }
    # The API request uses its own session, so commit the fixture to this clone.
    with Session(db_engine) as session:
        repository.upsert_campaign_opt_logs(
            session,
            seller_id=SELLER,
            advertiser_id="TEST_original_advertiser",
            logs=[{**log, "user": "original"}],
        )
    body = _dump_body_v4(endpoint=CAMPAIGN_CHANGE_LOG_ENDPOINT, rows=[])
    body["dump"]["response"]["body"]["data"]["logs"] = [{**log, "user": "replacement"}]
    r = _post(api_client, readwrite_key, body)

    assert r.status_code == 422, r.text
    assert _ad_raw_log_count(db_engine) == 0
    with db_engine.connect() as conn:
        assert (
            conn.execute(
                text('SELECT "user" FROM plugin.campaign_opt_logs WHERE log_id = :id'),
                {"id": log["id"]},
            ).scalar_one()
            == "original"
        )


@pytest.mark.parametrize("requested", [False, True])
def test_campaign_change_log_coverage_recovers_an_old_partial_archive(
    api_client, readwrite_key, db_engine, requested
):
    from tts_erp_v2.plugin.ads import repository

    body = _dump_body_v4(endpoint=CAMPAIGN_CHANGE_LOG_ENDPOINT, rows=[])
    dump = body["dump"]
    dump["response"]["body"]["data"]["logs"] = [
        {
            "id": "TEST_old_partial",
            "object_id": CAMPAIGN,
            "opt_time": "2026-09-08 15:40:46",
        }
    ]
    with Session(db_engine) as session:
        repository.upsert_daily_rows(
            session,
            seller_id=SELLER,
            advertiser_id=ADVERTISER,
            endpoint=CAMPAIGN_CHANGE_LOG_ENDPOINT,
            campaign_id=CAMPAIGN,
            day=date.fromisoformat(dump["day"]),
            rows=[],
            request_url=dump["request"]["url"],
            request_body=dump["request"],
            response_status=200,
            response_body=dump["response"],
            created_at=datetime(2026, 9, 9, 2, tzinfo=UTC),
            request_id="TEST_old_partial_archive",
            source="tiktok-shop-data-sync",
        )
    assert _ad_raw_log_count(db_engine) == 1

    def coverage():
        r = api_client.get(
            "/v2/analytics/sync/coverage",
            headers={"Authorization": f"Bearer {readwrite_key}"},
            params={
                "sellerId": SELLER,
                "advertiserId": ADVERTISER,
                "endpoint": CAMPAIGN_CHANGE_LOG_ENDPOINT,
                "kind": "daily",
                "startDay": dump["day"],
                "endDay": dump["day"],
                **({"campaignId": CAMPAIGN} if requested else {}),
            },
        )
        assert r.status_code == 200, r.text
        return r.json()["data"]

    assert coverage()["campaigns"].get(CAMPAIGN, {}).get("coveredPeriods", []) == []
    r = _post(api_client, readwrite_key, body)
    assert r.status_code == 200, r.text
    assert coverage()["campaigns"][CAMPAIGN]["coveredPeriods"] == [dump["day"]]


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
