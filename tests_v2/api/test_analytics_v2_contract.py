"""TDD contract tests: /v2/analytics/sync/* — analytics_sync 的 v2 化落地契约。

背景：analytics ingest 链路从 public.analytics_* + 裸 psycopg + /v1 挂载
迁移为 analytics.ad_* + SQLAlchemy + /v2 挂载（方案：
tech-doc/analytics-v2-migration-plan.md）。协议字节级不变。

本文件锁定的契约：
1. ``/v2/analytics/sync/cursor`` / ``/v2/analytics/sync/batches`` 在 v2 app 注册。
2. auth 分类 = readwrite：匿名 401、readonly 403、readwrite 通过。
   （漏配分类规则时未知路径默认 admin → readwrite 也 403，本测试即红线。）
3. 旧 ``/v1/analytics/sync/*`` 路径随发布下线（admin key 打过去 = 404）。
4. cursor items 必须 echo sellerId/advertiserId（2026-08-30 协议事故回归点）。
5. batches 端到端：合法批次 → accepted/inserted，行落 analytics.ad_records；
   幂等重放 → duplicate；幂等键不匹配 → SCHEMA_INVALID。

数据隔离：全部用 TEST_ 哨兵 seller/advertiser，finally 块经 db_engine
直连清理（handler 内部 commit，行会逃出测试 savepoint）。
"""

from __future__ import annotations

import json

import pytest
from sqlalchemy import text

pytestmark = [pytest.mark.domain_api, pytest.mark.layer_integration]

SELLER = "TEST_seller-v2"
ADVERTISER = "TEST_adv-v2"
CAMPAIGN = "TEST_campaign-v2"
STORAGE_KEY = "productAnalyses"

_CURSOR_PARAMS = {"sellerId": SELLER, "advertiserId": ADVERTISER}

# 清理本文件写入的所有 TEST_ 行（handler 内 commit，必须独立于 savepoint）。
# 字面量逐条写（非循环变量），否则 pi-lens 判动态 SQL。
_CLEANUP_SQL = (
    "DELETE FROM analytics.ad_records WHERE seller_id = :s",
    "DELETE FROM analytics.ad_daily_pages WHERE seller_id = :s",
    "DELETE FROM analytics.ad_daily_completeness WHERE seller_id = :s",
    "DELETE FROM analytics.ad_cursors WHERE seller_id = :s",
    "DELETE FROM analytics.ad_shop_timezones WHERE seller_id = :s",
    "DELETE FROM analytics.ad_audit_log WHERE path LIKE '%sellerId=TEST_%'",
)


@pytest.fixture(autouse=True)
def _cleanup_analytics_rows(db_engine):
    """Setup + teardown 都清一遍，防上次运行残留。"""
    _wipe(db_engine)
    yield
    _wipe(db_engine)


def _wipe(db_engine) -> None:
    from sqlalchemy.exc import ProgrammingError

    try:
        with db_engine.begin() as conn:
            for stmt in _CLEANUP_SQL:
                # pi-lens-ignore: python-sql-injection
                conn.execute(text(stmt), {"s": SELLER})
    except ProgrammingError as exc:
        # 迁移未应用（红阶段）：analytics.ad_* 还不存在（SQLSTATE 42P01），无可清理。
        # SQLAlchemy 会把 psycopg 的 UndefinedTable 包成 ProgrammingError，按 orig 判。
        if getattr(getattr(exc, "orig", None), "sqlstate", None) != "42P01":
            raise


def _valid_batch(idem_key: str, day: str = "2026-09-01") -> dict:
    """protocolVersion 2 的合法单页批次。"""
    return {
        "protocolVersion": 2,
        "requestId": "TEST_req-v2-contract",
        "scope": {"sellerId": SELLER, "advertiserId": ADVERTISER, "shopName": "TEST店"},
        "records": [
            {
                "idempotencyKey": idem_key,
                "storageKey": STORAGE_KEY,
                "campaignId": CAMPAIGN,
                "day": day,
                "page": 1,
                "expectedPageCount": 1,
                "endpoint": "/api/campaign/analysis",
                "method": "GET",
                "requestBody": None,
                "response": {"code": 0, "data": {"list": []}},
                "source": "chrome-extension",
                "capturedAt": "2026-09-01T08:00:00Z",
                "schemaVersion": 1,
            }
        ],
    }


# ─── 路由 + auth 分类 ─────────────────────────────────────────────────


def test_v2_cursor_route_anonymous_is_401(api_client):
    r = api_client.get("/v2/analytics/sync/cursor", params=_CURSOR_PARAMS)
    assert r.status_code == 401, r.text
    assert "X-API-Key" in r.text


def test_v2_cursor_readonly_key_is_forbidden(api_client, readonly_key):
    """readonly 403 —— 若漏配 /v2 分类规则，未知路径默认 admin 也是 403，
    但结合下一个测试（readwrite 必须通过）才能区分两种 403。"""
    r = api_client.get(
        "/v2/analytics/sync/cursor",
        params=_CURSOR_PARAMS,
        headers={"Authorization": f"Bearer {readonly_key}"},
    )
    assert r.status_code == 403, r.text


def test_v2_cursor_readwrite_key_passes(api_client, readwrite_key):
    """readwrite 必须到达 handler —— 这是 auth 分类规则存在的直接证据。"""
    r = api_client.get(
        "/v2/analytics/sync/cursor",
        params=_CURSOR_PARAMS,
        headers={"Authorization": f"Bearer {readwrite_key}"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["code"] == 0
    assert body["requestId"]
    assert body["data"]["nextCursor"] is None
    assert body["data"]["timezone"]
    assert body["data"]["items"] == []


def test_v2_batches_route_present(api_client, readwrite_key):
    r = api_client.post(
        "/v2/analytics/sync/batches",
        json={"protocolVersion": 1, "scope": {}, "records": []},
        headers={"Authorization": f"Bearer {readwrite_key}"},
    )
    # 到达 handler（400 校验失败）即可；401/403/404 都是回归
    assert r.status_code == 400, r.text
    body = r.json()
    assert body["code"] == "SCHEMA_INVALID"
    assert "errors" in body  # Pydantic 消毒三元组
    assert body["retryable"] is False


def test_v1_paths_are_gone(api_client, admin_key):
    """旧 /v1/analytics/sync/* 随 v2 化下线。admin key 通过 auth 后应 404。"""
    r = api_client.get(
        "/v1/analytics/sync/cursor",
        params=_CURSOR_PARAMS,
        headers={"Authorization": f"Bearer {admin_key}"},
    )
    assert r.status_code == 404, r.text
    r = api_client.post(
        "/v1/analytics/sync/batches",
        json={},
        headers={"Authorization": f"Bearer {admin_key}"},
    )
    assert r.status_code == 404, r.text


# ─── cursor 协议契约 ──────────────────────────────────────────────────


def test_v2_cursor_items_echo_scope_fields(api_client, readwrite_key, db_engine):
    """items 必须 echo sellerId/advertiserId（插件 parseCursor 严格匹配）。"""
    with db_engine.begin() as conn:
        # pi-lens-ignore: python-sql-injection — 字面量 SQL + 绑定参数
        conn.execute(
            text(
                """
                INSERT INTO analytics.ad_cursors (
                    seller_id, advertiser_id, storage_key, campaign_id,
                    latest_completed_day, first_seen_day
                ) VALUES (:s, :a, :sk, :c, '2026-08-28', '2026-08-27')
                """
            ),
            {"s": SELLER, "a": ADVERTISER, "sk": STORAGE_KEY, "c": CAMPAIGN},
        )
    r = api_client.get(
        "/v2/analytics/sync/cursor",
        params={
            **_CURSOR_PARAMS,
            "storageKey": STORAGE_KEY,
            "campaignId": CAMPAIGN,
        },
        headers={"Authorization": f"Bearer {readwrite_key}"},
    )
    assert r.status_code == 200, r.text
    items = r.json()["data"]["items"]
    assert len(items) == 1, items
    item = items[0]
    assert item["sellerId"] == SELLER
    assert item["advertiserId"] == ADVERTISER
    assert item["storageKey"] == STORAGE_KEY
    assert item["campaignId"] == CAMPAIGN
    assert item["latestCompletedDay"] == "2026-08-28"
    assert item["nextRequiredDay"] == "2026-08-29"


# ─── batches 端到端契约 ───────────────────────────────────────────────


def _idem_key(day: str = "2026-09-01", page: int = 1) -> str:
    from tts_erp_v2.analytics.domain import compute_idempotency_key

    return compute_idempotency_key(
        seller_id=SELLER,
        advertiser_id=ADVERTISER,
        storage_key=STORAGE_KEY,
        campaign_id=CAMPAIGN,
        day=day,
        page=page,
    )


def test_v2_batches_insert_then_duplicate(api_client, readwrite_key, db_engine):
    """端到端：inserted → 行落 analytics.ad_records + cursor 推进 → 重放 duplicate。"""
    key = _idem_key()
    payload = _valid_batch(key)

    r1 = api_client.post(
        "/v2/analytics/sync/batches",
        json=payload,
        headers={"Authorization": f"Bearer {readwrite_key}"},
    )
    assert r1.status_code == 200, r1.text
    data = r1.json()["data"]
    assert data["accepted"] == [{"idempotencyKey": key, "status": "inserted"}]
    assert data["rejected"] == []

    with db_engine.begin() as conn:
        # pi-lens-ignore: python-sql-injection — 字面量 SQL + 绑定参数
        row = conn.execute(
            text(
                "SELECT storage_key, campaign_id, day, page, expected_page_count "
                "FROM analytics.ad_records WHERE idempotency_key = :k"
            ),
            {"k": key},
        ).one()
        assert (row[0], row[1], str(row[2]), row[3], row[4]) == (
            STORAGE_KEY,
            CAMPAIGN,
            "2026-09-01",
            1,
            1,
        )
        # 单页日应即刻 complete 并推进 cursor
        # pi-lens-ignore: python-sql-injection — 字面量 SQL + 绑定参数
        cur = conn.execute(
            text(
                "SELECT latest_completed_day FROM analytics.ad_cursors "
                "WHERE seller_id = :s AND advertiser_id = :a "
                "AND storage_key = :sk AND campaign_id = :c"
            ),
            {"s": SELLER, "a": ADVERTISER, "sk": STORAGE_KEY, "c": CAMPAIGN},
        ).one()
        assert str(cur[0]) == "2026-09-01"

    # 幂等重放
    r2 = api_client.post(
        "/v2/analytics/sync/batches",
        json=payload,
        headers={"Authorization": f"Bearer {readwrite_key}"},
    )
    assert r2.status_code == 200, r2.text
    data2 = r2.json()["data"]
    assert data2["accepted"] == [{"idempotencyKey": key, "status": "duplicate"}]
    assert data2["rejected"] == []


def test_v2_batches_idempotency_key_mismatch_rejected(api_client, readwrite_key):
    payload = _valid_batch("0" * 64)  # 合法形状但与 canonical 不符
    r = api_client.post(
        "/v2/analytics/sync/batches",
        json=payload,
        headers={"Authorization": f"Bearer {readwrite_key}"},
    )
    assert r.status_code == 200, r.text
    data = r.json()["data"]
    assert data["accepted"] == []
    assert len(data["rejected"]) == 1
    rej = data["rejected"][0]
    assert rej["code"] == "SCHEMA_INVALID"
    assert "idempotencyKey mismatch" in rej["message"]
    assert rej["retryable"] is False


def test_v2_batches_malformed_json(api_client, readwrite_key):
    r = api_client.post(
        "/v2/analytics/sync/batches",
        content=b"{not json",
        headers={
            "Authorization": f"Bearer {readwrite_key}",
            "Content-Type": "application/json",
        },
    )
    assert r.status_code == 400, r.text
    body = r.json()
    assert body["code"] == "MALFORMED_JSON"
    assert body["retryable"] is False


def test_v2_batches_audit_log_written(api_client, readwrite_key, db_engine):
    """每次请求（无论成败）落一行 analytics.ad_audit_log。"""
    key = _idem_key(day="2026-09-02")
    r = api_client.post(
        "/v2/analytics/sync/batches",
        json=_valid_batch(key, day="2026-09-02"),
        headers={"Authorization": f"Bearer {readwrite_key}"},
    )
    assert r.status_code == 200, r.text
    with db_engine.begin() as conn:
        # pi-lens-ignore: python-sql-injection — 字面量 SQL，request_id 为模块常量
        row = conn.execute(
            text(
                "SELECT endpoint, status, records_in, records_ok "
                "FROM analytics.ad_audit_log "
                "WHERE request_id = 'TEST_req-v2-contract' "
                "ORDER BY id DESC LIMIT 1"
            )
        ).one()
        assert row[0] == "batches"
        assert row[1] == 200
        assert row[2] == 1
        assert row[3] == 1
    # 清理 audit（不在 autouse 的 seller 维度里）
    with db_engine.begin() as conn:
        # pi-lens-ignore: python-sql-injection — 字面量 SQL，request_id 为模块常量
        conn.execute(
            text(
                "DELETE FROM analytics.ad_audit_log WHERE request_id = 'TEST_req-v2-contract'"
            )
        )


def test_v2_cursor_response_is_json_serializable_envelope(api_client, readwrite_key):
    """envelope 形状锁定：code/requestId/data{timezone,items,nextCursor}。"""
    r = api_client.get(
        "/v2/analytics/sync/cursor",
        params=_CURSOR_PARAMS,
        headers={"Authorization": f"Bearer {readwrite_key}"},
    )
    body = json.loads(r.text)
    assert set(body.keys()) == {"code", "requestId", "data"}
    assert set(body["data"].keys()) == {"timezone", "items", "nextCursor"}
