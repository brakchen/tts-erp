"""TDD contract tests: /v2/analytics/sync/* — analytics ingest 的 dump architecture 契约。

背景：v2 切流后（commit cc04490）又经历 dump architecture 重构
（commit ab7fd22 + 8d25c94，tech-doc/analytics/dump-architecture.md）。
本文件锁定新契约：
1. 路由：/v2/analytics/sync/cursor（GET, has-data）+ /v2/analytics/sync/dumps（POST, 单 dump）
2. auth 分类 = readwrite：匿名 401、readonly 403、readwrite 通过
3. 旧 /v1/analytics/sync/* 路径 = 404
4. cursor has-data 模式：endpoint + day 5 元组查 ad_raw，返回 hasData bool
5. dumps 端到端：合法 dump → accepted/inserted；幂等重放 → duplicate
6. ad_raw 5 元组 unique 约束保证 dump 幂等

数据隔离：TEST_ 哨兵 seller/advertiser，finally 块经 db_engine
直连清理（handler 内部 commit）。
"""

from __future__ import annotations

import json
import uuid

import pytest
from sqlalchemy import text

pytestmark = [pytest.mark.domain_api, pytest.mark.layer_integration]

SELLER = "TEST_seller-dump"
ADVERTISER = "TEST_adv-dump"
CAMPAIGN = "TEST_campaign-dump"
ENDPOINT = "/oec_ads/shopping/v1/oec/stat/post_product_list"
DAY = "2026-08-23"

# dump architecture：dump 唯一性 (scope, endpoint, day, campaign_id)
# cursor has-data 查 ad_raw（不在 ad_cursors）
_CLEANUP_SQL = (
    "DELETE FROM analytics.ad_daily_completeness WHERE seller_id = :s",
    "DELETE FROM analytics.ad_records WHERE seller_id = :s",
    "DELETE FROM analytics.ad_raw WHERE seller_id = :s",
    "DELETE FROM analytics.ad_shop_timezones WHERE seller_id = :s",
    "DELETE FROM analytics.ad_audit_log WHERE path LIKE '%sellerId=TEST_%'",
)


@pytest.fixture(autouse=True)
def _cleanup_analytics_rows(db_engine):
    """Setup + teardown 都清一遍。"""
    params = {"s": SELLER}
    with db_engine.begin() as conn:
        for stmt in _CLEANUP_SQL:
            # noqa: python-sql-injection — 字面量 SQL tuple
            conn.execute(text(stmt), params)
    yield
    with db_engine.begin() as conn:
        for stmt in _CLEANUP_SQL:
            # noqa: python-sql-injection — 字面量 SQL tuple
            conn.execute(text(stmt), params)


# ─── 路由 + auth ─────────────────────────────────────────────────────

def test_v2_cursor_route_anonymous_is_401(api_client):
    assert api_client.get("/v2/analytics/sync/cursor", params={"sellerId": SELLER, "advertiserId": ADVERTISER, "endpoint": ENDPOINT, "day": DAY}).status_code == 401


def test_v2_cursor_readonly_key_is_forbidden(api_client, readonly_key):
    r = api_client.get(
        "/v2/analytics/sync/cursor",
        headers={"Authorization": f"Bearer {readonly_key}"},
        params={"sellerId": SELLER, "advertiserId": ADVERTISER, "endpoint": ENDPOINT, "day": DAY},
    )
    assert r.status_code == 403


def test_v2_cursor_readwrite_key_passes(api_client, readwrite_key):
    r = api_client.get(
        "/v2/analytics/sync/cursor",
        headers={"Authorization": f"Bearer {readwrite_key}"},
        params={"sellerId": SELLER, "advertiserId": ADVERTISER, "endpoint": ENDPOINT, "day": DAY},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["code"] == 0
    assert body["data"]["hasData"] is False
    assert body["data"]["storageKey"] == "productAnalyses"


def test_v2_dumps_route_present(api_client, readwrite_key):
    r = api_client.post(
        "/v2/analytics/sync/dumps",
        headers={"Authorization": f"Bearer {readwrite_key}"},
        json={
            "protocolVersion": 2, "requestId": "req-test-dump-1",
            "scope": {"sellerId": SELLER, "advertiserId": ADVERTISER},
            "dump": {
                "endpoint": ENDPOINT, "method": "POST",
                "day": DAY, "campaignId": CAMPAIGN,
                "request": {"url": "http://tiktok.test/..."},
                "response": {"status": 200, "body": {"data": {"rows": []}}},
                "capturedAt": "2026-08-23T00:00:00.000Z",
            },
        },
    )
    assert r.status_code == 200
    body = r.json()
    assert body["code"] == 0
    assert body["data"]["status"] == "inserted"
    assert len(body["data"]["idempotencyKey"]) == 64  # SHA-256 hex


def test_v1_paths_are_gone(api_client, admin_key):
    for path in ["/v1/analytics/sync/cursor", "/v1/analytics/sync/batches"]:
        r = api_client.get(path, headers={"Authorization": f"Bearer {admin_key}"}, params={"sellerId": SELLER, "advertiserId": ADVERTISER})
        assert r.status_code == 404, f"expected 404 for {path}, got {r.status_code}"


# ─── cursor has-data 行为 ───────────────────────────────────────────

def test_v2_cursor_has_data_returns_true_after_dump(api_client, readwrite_key, db_engine):
    """dump 1 次后，cursor has-data 应返 true。"""
    # 先 dump 1 次
    api_client.post(
        "/v2/analytics/sync/dumps",
        headers={"Authorization": f"Bearer {readwrite_key}"},
        json={
            "protocolVersion": 2, "requestId": str(uuid.uuid4()),
            "scope": {"sellerId": SELLER, "advertiserId": ADVERTISER},
            "dump": {
                "endpoint": ENDPOINT, "method": "POST",
                "day": DAY, "campaignId": CAMPAIGN,
                "request": {"url": "http://tiktok.test/..."},
                "response": {"status": 200, "body": {"data": {"rows": []}}},
                "capturedAt": "2026-08-23T00:00:00.000Z",
            },
        },
    )
    # 再查 has-data
    r = api_client.get(
        "/v2/analytics/sync/cursor",
        headers={"Authorization": f"Bearer {readwrite_key}"},
        params={"sellerId": SELLER, "advertiserId": ADVERTISER, "endpoint": ENDPOINT, "day": DAY, "campaignId": CAMPAIGN},
    )
    body = r.json()
    assert body["code"] == 0
    assert body["data"]["hasData"] is True
    assert body["data"]["storageKey"] == "productAnalyses"
    assert body["data"]["campaignId"] == CAMPAIGN


def test_v2_cursor_has_data_returns_false_before_dump(api_client, readwrite_key):
    """未 dump 时，cursor has-data 应返 false。"""
    r = api_client.get(
        "/v2/analytics/sync/cursor",
        headers={"Authorization": f"Bearer {readwrite_key}"},
        params={"sellerId": SELLER, "advertiserId": ADVERTISER, "endpoint": ENDPOINT, "day": "2099-01-01"},
    )
    body = r.json()
    assert body["code"] == 0
    assert body["data"]["hasData"] is False


def test_v2_cursor_400_on_unknown_endpoint(api_client, readwrite_key):
    r = api_client.get(
        "/v2/analytics/sync/cursor",
        headers={"Authorization": f"Bearer {readwrite_key}"},
        params={"sellerId": SELLER, "advertiserId": ADVERTISER, "endpoint": "/unknown/path", "day": DAY},
    )
    assert r.status_code == 400
    assert r.json()["code"] == "SCHEMA_INVALID"


# ─── /dumps 行为 ─────────────────────────────────────────────────────

def test_v2_dumps_insert_then_duplicate(api_client, readwrite_key, db_engine):
    """同 dump 重放 2 次：第 1 次 inserted，第 2 次 duplicate。"""
    payload = {
        "protocolVersion": 2, "requestId": str(uuid.uuid4()),
        "scope": {"sellerId": SELLER, "advertiserId": ADVERTISER},
        "dump": {
            "endpoint": ENDPOINT, "method": "POST",
            "day": DAY, "campaignId": CAMPAIGN,
            "request": {"url": "http://tiktok.test/..."},
            "response": {"status": 200, "body": {"data": {"rows": []}}},
            "capturedAt": "2026-08-23T00:00:00.000Z",
        },
    }
    r1 = api_client.post("/v2/analytics/sync/dumps", headers={"Authorization": f"Bearer {readwrite_key}"}, json=payload)
    r2 = api_client.post("/v2/analytics/sync/dumps", headers={"Authorization": f"Bearer {readwrite_key}"}, json=payload)
    assert r1.json()["data"]["status"] == "inserted"
    assert r2.json()["data"]["status"] == "duplicate"
    # idempotencyKey 必须一致
    assert r1.json()["data"]["idempotencyKey"] == r2.json()["data"]["idempotencyKey"]


def test_v2_dumps_400_on_unknown_endpoint(api_client, readwrite_key):
    """endpoint 不在 4 路径白名单 → 400 SCHEMA_INVALID。"""
    r = api_client.post(
        "/v2/analytics/sync/dumps",
        headers={"Authorization": f"Bearer {readwrite_key}"},
        json={
            "protocolVersion": 2, "requestId": "r",
            "scope": {"sellerId": SELLER, "advertiserId": ADVERTISER},
            "dump": {
                "endpoint": "/unknown/path", "method": "POST",
                "day": DAY, "campaignId": CAMPAIGN,
                "request": {"url": "x"},
                "response": {"status": 200, "body": {}},
                "capturedAt": "2026-08-23T00:00:00.000Z",
            },
        },
    )
    assert r.status_code == 400
    assert r.json()["code"] == "SCHEMA_INVALID"


def test_v2_dumps_audit_log_written(api_client, readwrite_key, db_engine):
    """每次 dump 写一条 audit_log（endpoint='dumps'）。"""
    api_client.post(
        "/v2/analytics/sync/dumps",
        headers={"Authorization": f"Bearer {readwrite_key}"},
        json={
            "protocolVersion": 2, "requestId": str(uuid.uuid4()),
            "scope": {"sellerId": SELLER, "advertiserId": ADVERTISER},
            "dump": {
                "endpoint": ENDPOINT, "method": "POST",
                "day": DAY, "campaignId": CAMPAIGN,
                "request": {"url": "http://tiktok.test/..."},
                "response": {"status": 200, "body": {"data": {"rows": []}}},
                "capturedAt": "2026-08-23T00:00:00.000Z",
            },
        },
    )
    with db_engine.begin() as conn:
        # noqa: python-sql-injection — 字面量 SQL
        row = conn.execute(
            text("SELECT endpoint, status, records_in FROM analytics.ad_audit_log WHERE endpoint = 'dumps' ORDER BY id DESC LIMIT 1"),
        ).first()
    assert row is not None
    assert row[0] == "dumps"
    assert row[1] == 200
    assert row[2] == 1


# ─── envelope ─────────────────────────────────────────────────────

def test_v2_dumps_response_is_json_serializable_envelope(api_client, readwrite_key):
    r = api_client.post(
        "/v2/analytics/sync/dumps",
        headers={"Authorization": f"Bearer {readwrite_key}"},
        json={
            "protocolVersion": 2, "requestId": "req-envelope-1",
            "scope": {"sellerId": SELLER, "advertiserId": ADVERTISER},
            "dump": {
                "endpoint": ENDPOINT, "method": "POST",
                "day": DAY, "campaignId": CAMPAIGN,
                "request": {"url": "http://tiktok.test/..."},
                "response": {"status": 200, "body": {}},
                "capturedAt": "2026-08-23T00:00:00.000Z",
            },
        },
    )
    body = r.json()
    # dump 协议 envelope 字段固定
    assert set(body.keys()) == {"code", "requestId", "data"}
    assert set(body["data"].keys()) == {"idempotencyKey", "status"}
    # idempotencyKey 是 64 字符 hex
    assert isinstance(body["data"]["idempotencyKey"], str)
    assert len(body["data"]["idempotencyKey"]) == 64
    # 整体可 JSON 序列化
    json.dumps(body)
