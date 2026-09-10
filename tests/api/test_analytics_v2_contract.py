"""HTTP 契约测试：/v2/analytics/sync/* — analytics ingest 的 v4 契约。

背景：dump architecture 重构（tech-doc/analytics/dump-architecture.md）→
2026-09-05 reorg → v4 结构化 rows 协议
（tech-doc/analytics/daily-sync-with-coverage.md）。

本文件只锁**传输层/路由层**契约（持久化语义见 ``test_analytics_dumps_v4.py``，
coverage 语义见 ``test_analytics_coverage.py``）：

1. 路由：``/v2/analytics/sync/coverage``（GET, 批量覆盖）+ ``/v2/analytics/sync/dumps``（POST, 单 dump v4）
2. auth 分类 = readwrite：匿名 401、readonly 403、readwrite 通过
3. 旧 ``/v1/analytics/sync/*`` 路径 = 404
4. dumps v4 响应 envelope 形状固定 + ingest 日志行可观测

历史：v3 的 ``/cursor`` has-data 单点查询已由 ``/coverage`` 批量查询取代
（v3 区间聚合方案已搁置，见 range-aggregate-history-sync.md 的「已搁置」标注）。

数据隔离：TEST_ 哨兵 seller/advertiser，finally 块经 db_engine
直连清理（handler 内部 commit）。
"""

from __future__ import annotations

import json
import uuid

import pytest
from sqlalchemy import text

pytestmark = [pytest.mark.domain_api, pytest.mark.layer_integration]

SELLER = "TEST_seller-contract"
ADVERTISER = "TEST_adv-contract"
CAMPAIGN = "TEST_campaign-contract"
ENDPOINT = "/oec_ads/shopping/v1/oec/stat/post_product_list"
DAY = "2026-08-23"

_CLEANUP_SQL = (
    "DELETE FROM analytics.ad_daily WHERE seller_id = :s",
    "DELETE FROM analytics.ad_today WHERE seller_id = :s",
    "DELETE FROM analytics.ad_monthly WHERE seller_id = :s",
    "DELETE FROM analytics.ad_raw_log WHERE seller_id = :s",
)


@pytest.fixture(autouse=True)
def _cleanup_analytics_rows(db_engine):
    """Setup + teardown 都清一遍。"""
    params = {"s": SELLER}
    with db_engine.begin() as conn:
        for stmt in _CLEANUP_SQL:
            # pi-lens-ignore: python-sql-injection
            conn.execute(text(stmt), params)
    yield
    with db_engine.begin() as conn:
        for stmt in _CLEANUP_SQL:
            # pi-lens-ignore: python-sql-injection
            conn.execute(text(stmt), params)


def _dump_body_v4(request_id: str | None = None) -> dict:
    """最小合法 v4 dump body。"""
    return {
        "protocolVersion": 4,
        "requestId": request_id or str(uuid.uuid4()),
        "scope": {"sellerId": SELLER, "advertiserId": ADVERTISER},
        "dump": {
            "kind": "daily",
            "endpoint": ENDPOINT,
            "method": "POST",
            "day": DAY,
            "campaignId": CAMPAIGN,
            "rows": [{"product_id": "TEST_PROD_1", "mixed_real_cost": "1.00"}],
            "request": {"url": "http://tiktok.test/...", "body": {}},
            "response": {"status": 200, "body": {"data": {"table": []}}},
            "createdAt": "2026-08-23T00:00:00.000Z",
        },
    }


# ─── 路由 + auth ─────────────────────────────────────────────────────


def test_coverage_route_anonymous_is_401(api_client):
    assert (
        api_client.get(
            "/v2/analytics/sync/coverage",
            params={
                "sellerId": SELLER,
                "advertiserId": ADVERTISER,
                "endpoint": ENDPOINT,
                "kind": "daily",
                "startDay": DAY,
                "endDay": DAY,
            },
        ).status_code
        == 401
    )


def test_coverage_readonly_key_is_forbidden(api_client, readonly_key):
    r = api_client.get(
        "/v2/analytics/sync/coverage",
        headers={"Authorization": f"Bearer {readonly_key}"},
        params={
            "sellerId": SELLER,
            "advertiserId": ADVERTISER,
            "endpoint": ENDPOINT,
            "kind": "daily",
            "startDay": DAY,
            "endDay": DAY,
        },
    )
    assert r.status_code == 403


def test_coverage_readwrite_key_passes(api_client, readwrite_key):
    r = api_client.get(
        "/v2/analytics/sync/coverage",
        headers={"Authorization": f"Bearer {readwrite_key}"},
        params={
            "sellerId": SELLER,
            "advertiserId": ADVERTISER,
            "endpoint": ENDPOINT,
            "kind": "daily",
            "startDay": DAY,
            "endDay": DAY,
        },
    )
    assert r.status_code == 200
    body = r.json()
    assert body["code"] == 0
    data = body["data"]
    assert data["kind"] == "daily"
    assert data["endpoint"] == ENDPOINT
    assert data["storageKey"] == "productAnalyses"
    assert data["totalRequested"] == 1
    assert data["campaigns"] == {}


def test_dumps_v4_route_present(api_client, readwrite_key):
    r = api_client.post(
        "/v2/analytics/sync/dumps",
        headers={"Authorization": f"Bearer {readwrite_key}"},
        json=_dump_body_v4("req-test-dump-1"),
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["code"] == 0
    assert body["data"]["kind"] == "daily"
    assert body["data"]["inserted"] == 1


def test_v1_paths_are_gone(api_client, admin_key):
    for path in ["/v1/analytics/sync/cursor", "/v1/analytics/sync/batches"]:
        r = api_client.get(
            path,
            headers={"Authorization": f"Bearer {admin_key}"},
            params={"sellerId": SELLER, "advertiserId": ADVERTISER},
        )
        assert r.status_code == 404, f"expected 404 for {path}, got {r.status_code}"


# ─── envelope + 可观测性 ─────────────────────────────────────────────


def test_dumps_v4_response_is_json_serializable_envelope(api_client, readwrite_key):
    r = api_client.post(
        "/v2/analytics/sync/dumps",
        headers={"Authorization": f"Bearer {readwrite_key}"},
        json=_dump_body_v4("req-envelope-1"),
    )
    body = r.json()
    # dump 协议 envelope 字段固定
    assert set(body.keys()) == {"code", "requestId", "data"}
    assert set(body["data"].keys()) == {"kind", "rowCount", "inserted", "duplicates", "day"}
    json.dumps(body)


def test_dumps_v4_emits_ingest_log_line(api_client, readwrite_key, caplog):
    """每次 POST /dumps 写一条 ingest logger 单行 key=value（替代 audit_log）。

    2026-09-05 reorg 后 audit 职责从 ``analytics.ad_audit_log``（DB 表）
    迁到 ``tts_erp_v2.analytics.ingest`` logger（文件）。这里断言：
    - 一次成功 POST /dumps → caplog 抓到该 logger 一行 INFO log
    - log 字段包含 method=POST path=/v2/analytics/sync/dumps
      status=200 records_in=1 records_ok=1 records_rej=0
    """
    import logging

    caplog.set_level(logging.INFO, logger="tts_erp_v2.analytics.ingest")

    r = api_client.post(
        "/v2/analytics/sync/dumps",
        headers={"Authorization": f"Bearer {readwrite_key}"},
        json=_dump_body_v4(),
    )
    assert r.status_code == 200

    matches = [
        rec
        for rec in caplog.records
        if rec.name == "tts_erp_v2.analytics.ingest"
        and "method=POST" in rec.getMessage()
        and "path=/v2/analytics/sync/dumps" in rec.getMessage()
        and "status=200" in rec.getMessage()
    ]
    assert matches, (
        "ingest log line for dumps 200 missing; "
        f"records={[r.getMessage() for r in caplog.records]}"
    )
    msg = matches[-1].getMessage()
    assert "records_in=1" in msg
    assert "records_ok=1" in msg
    assert "records_rej=0" in msg
