"""cursor live-row 内存缓存（has_data_cache.py）的端点行为测试（v3 coverage）。

覆盖：
1. coverage 命中不碰 DB/session —— 命中后把 repository loader monkeypatch 成
   会 raise，GET 仍正常返回（证明没回源）。
2. miss 回源灌空桶 —— 首次 GET（空库）返回 hasRow=false，之后 loader 已废仍可 GET。
3. /dumps v3 live upsert write-through —— 灌空桶后 POST history dump，
   同 (endpoint, kind) 立刻 hasRow=true 且区间正确（无 TTL 等待、无 DB 重载）。
4. stale 写入（capturedAt 更旧 → stale_ignored）不替换桶内 live 行区间。
5. kind coverage 无 campaignId → 400 SCHEMA_INVALID。
6. scope 隔离 —— 同 campaign 不同 seller 不共享桶。
7. legacy has-data（无 kind + day）不缓存 —— 每发必走 DB。

数据隔离：TEST_ 哨兵 + autouse 直连删除 ad_raw。
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text

pytestmark = [pytest.mark.domain_api, pytest.mark.layer_integration]

SELLER = "TEST_seller-cursor-cache"
ADVERTISER = "TEST_adv-cursor-cache"
CAMPAIGN = "TEST_campaign-cursor-cache"
ENDPOINT = "/oec_ads/shopping/v1/oec/stat/post_product_list"
DS = "2026-07-01"
DE = "2026-09-05"
DAY = "2026-08-23"

_HISTORY_TUPLE = (ENDPOINT, "history", DS, DE, "2026-09-05T12:00:00+00:00")
_TODAY_TUPLE = (ENDPOINT, "today", "2026-09-10", "2026-09-10",
                "2026-09-10T01:00:00+00:00")


@pytest.fixture(autouse=True)
def _cleanup_analytics_rows(db_engine):
    with db_engine.begin() as conn:
        # pi-lens-ignore: python-sql-injection — literal SQL, bound param only
        conn.execute(
            text("DELETE FROM analytics.ad_raw WHERE seller_id = :s"),
            {"s": SELLER},
        )
    yield
    with db_engine.begin() as conn:
        # pi-lens-ignore: python-sql-injection — literal SQL, bound param only
        conn.execute(
            text("DELETE FROM analytics.ad_raw WHERE seller_id = :s"),
            {"s": SELLER},
        )


def _coverage_get(api_client, key, *, seller=SELLER, campaign: str | None = CAMPAIGN,
                  kind="history"):
    params = {
        "sellerId": seller,
        "advertiserId": ADVERTISER,
        "endpoint": ENDPOINT,
        "kind": kind,
    }
    if campaign is not None:
        params["campaignId"] = campaign
    return api_client.get(
        "/v2/analytics/sync/cursor",
        headers={"Authorization": f"Bearer {key}"},
        params=params,
    )


def _post_history_dump(api_client, key, *, campaign=CAMPAIGN, day_start=DS,
                       day_end=DE, captured_at="2026-09-10T02:00:00.000Z"):
    return api_client.post(
        "/v2/analytics/sync/dumps",
        headers={"Authorization": f"Bearer {key}"},
        json={
            "protocolVersion": 3,
            "requestId": str(uuid.uuid4()),
            "scope": {"sellerId": SELLER, "advertiserId": ADVERTISER},
            "dump": {
                "endpoint": ENDPOINT,
                "method": "POST",
                "day": day_end,
                "kind": "history",
                "dayStart": day_start,
                "dayEnd": day_end,
                "campaignId": campaign,
                "request": {"url": "http://tiktok.test/..."},
                "response": {"status": 200, "body": {"data": {"rows": []}}},
                "capturedAt": captured_at,
            },
        },
    )


# ─── coverage 命中 / miss ────────────────────────────────────────────


def test_coverage_hit_does_not_touch_db(api_client, readwrite_key, monkeypatch):
    """种子桶后 loader 会 raise —— 仍返回正确 live 行状态，证明命中免 DB。"""
    from tts_erp_v2.analytics import has_data_cache
    from tts_erp_v2.api.v2 import analytics as analytics_module

    has_data_cache.put(SELLER, ADVERTISER, CAMPAIGN, [_HISTORY_TUPLE])
    monkeypatch.setattr(
        analytics_module,
        "load_campaign_live_rows",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("should not hit DB")),
    )
    r = _coverage_get(api_client, readwrite_key)
    assert r.status_code == 200
    body = r.json()["data"]
    assert body["hasRow"] is True
    assert body["kind"] == "history"
    assert body["dayStart"] == DS
    assert body["dayEnd"] == DE
    # 桶里没有 today 行 → hasRow false（仍由缓存回答，不回源）
    r2 = _coverage_get(api_client, readwrite_key, kind="today")
    assert r2.status_code == 200
    assert r2.json()["data"]["hasRow"] is False


def test_coverage_miss_seeds_empty_bucket(api_client, readwrite_key, monkeypatch):
    """空库首次 GET = miss → 回源灌空桶；之后 loader 已废仍可 GET。"""
    from tts_erp_v2.api.v2 import analytics as analytics_module

    r = _coverage_get(api_client, readwrite_key)
    assert r.status_code == 200
    assert r.json()["data"]["hasRow"] is False

    monkeypatch.setattr(
        analytics_module,
        "load_campaign_live_rows",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("should be cached now")),
    )
    r = _coverage_get(api_client, readwrite_key)
    assert r.status_code == 200
    assert r.json()["data"]["hasRow"] is False


# ─── write-through（/dumps v3）──────────────────────────────────────


def test_dumps_write_through_flips_coverage_immediately(
    api_client, readwrite_key, monkeypatch
):
    """空桶（hasRow false）→ POST history dump → 同 (endpoint,kind) 立刻 hasRow
    true 且区间正确，且 loader 已废也能命中 —— 证明是 mark_present 而非回源。"""
    from tts_erp_v2.analytics import has_data_cache
    from tts_erp_v2.api.v2 import analytics as analytics_module

    has_data_cache.put(SELLER, ADVERTISER, CAMPAIGN, [])
    r = _coverage_get(api_client, readwrite_key)
    assert r.json()["data"]["hasRow"] is False

    r = _post_history_dump(api_client, readwrite_key)
    assert r.status_code == 200
    assert r.json()["data"]["status"] == "inserted"

    monkeypatch.setattr(
        analytics_module,
        "load_campaign_live_rows",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("should be cached now")),
    )
    r = _coverage_get(api_client, readwrite_key)
    assert r.status_code == 200
    data = r.json()["data"]
    assert data["hasRow"] is True
    assert data["dayStart"] == DS
    assert data["dayEnd"] == DE
    # 同一 campaign 没写过的 today kind 仍 false
    r2 = _coverage_get(api_client, readwrite_key, kind="today")
    assert r2.json()["data"]["hasRow"] is False


def test_dumps_before_any_get_no_half_bucket(api_client, readwrite_key):
    """桶未加载时先 POST：mark no-op，随后 GET 回源全量重载（含新行）→ hasRow。"""
    r = _post_history_dump(api_client, readwrite_key)
    assert r.status_code == 200
    r = _coverage_get(api_client, readwrite_key)
    assert r.json()["data"]["hasRow"] is True
    r2 = _coverage_get(api_client, readwrite_key, kind="today")
    assert r2.json()["data"]["hasRow"] is False


def test_stale_dump_does_not_replace_bucket_row(
    api_client, readwrite_key, monkeypatch
):
    """stale 写入（capturedAt 更旧）→ stale_ignored；桶内 live 行区间不被回退。"""
    from tts_erp_v2.analytics import has_data_cache
    from tts_erp_v2.api.v2 import analytics as analytics_module

    has_data_cache.put(SELLER, ADVERTISER, CAMPAIGN, [])  # 已加载空桶
    # 1) 新快照写入（inserted + write-through）
    r = _post_history_dump(api_client, readwrite_key, day_start=DS, day_end=DE,
                           captured_at="2026-09-10T02:00:00.000Z")
    assert r.json()["data"]["status"] == "inserted"

    # 2) 更旧 capturedAt 的同 kind 不同区间 → 守卫拒绝（stale_ignored）
    r = _post_history_dump(
        api_client, readwrite_key, day_start=DS, day_end="2026-08-01",
        captured_at="2026-08-02T00:00:00.000Z",
    )
    assert r.status_code == 200
    assert r.json()["data"]["status"] == "stale_ignored"

    # 3) 桶内 live 行仍为最新区间（loader 已废也能命中，证明未回退）
    monkeypatch.setattr(
        analytics_module,
        "load_campaign_live_rows",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("should be cached now")),
    )
    r = _coverage_get(api_client, readwrite_key)
    data = r.json()["data"]
    assert data["hasRow"] is True
    assert data["dayEnd"] == DE  # 未被 stale 写入替换


# ─── 不缓存 / 隔离 / 参数校验 ───────────────────────────────────────


def test_coverage_without_campaign_id_rejected(api_client, readwrite_key):
    """kind coverage 必须带 campaignId → 400 SCHEMA_INVALID。"""
    r = _coverage_get(api_client, readwrite_key, campaign=None)
    assert r.status_code == 400
    assert r.json()["code"] == "SCHEMA_INVALID"


def test_legacy_has_data_never_cached(api_client, readwrite_key, monkeypatch):
    """无 kind（legacy day 查询）每发必走 has_data DB 路径。"""
    from tts_erp_v2.analytics import has_data_cache
    from tts_erp_v2.api.v2 import analytics as analytics_module

    calls = {"n": 0}

    def fake_has_data(sess, **kwargs):
        calls["n"] += 1
        from types import SimpleNamespace

        return SimpleNamespace(has_data=False)

    monkeypatch.setattr(analytics_module, "has_data", fake_has_data)
    # 缓存里先塞同 campaign 桶 —— legacy 请求不该命中它
    has_data_cache.put(SELLER, ADVERTISER, CAMPAIGN, [_HISTORY_TUPLE])

    for _ in range(2):
        r = api_client.get(
            "/v2/analytics/sync/cursor",
            headers={"Authorization": f"Bearer {readwrite_key}"},
            params={
                "sellerId": SELLER,
                "advertiserId": ADVERTISER,
                "endpoint": ENDPOINT,
                "day": DAY,
            },
        )
        assert r.status_code == 200
        assert r.json()["data"]["hasData"] is False
    assert calls["n"] == 2  # 两次都走 DB，未缓存


def test_coverage_cache_scope_isolated_per_seller(api_client, readwrite_key, monkeypatch):
    """同 campaign_id 不同 seller 不共享桶（key 含 scope）。"""
    from tts_erp_v2.analytics import has_data_cache
    from tts_erp_v2.api.v2 import analytics as analytics_module

    has_data_cache.put(SELLER, ADVERTISER, CAMPAIGN, [_HISTORY_TUPLE])
    seen = {}

    def fake_loader(sess, *, seller_id, advertiser_id, campaign_id):
        seen["seller"] = seller_id
        return frozenset()

    monkeypatch.setattr(analytics_module, "load_campaign_live_rows", fake_loader)
    other_seller = "TEST_seller-cursor-cache-other"
    r = _coverage_get(api_client, readwrite_key, seller=other_seller)
    assert r.status_code == 200
    assert r.json()["data"]["hasRow"] is False
    # 不同 seller 命中不了 SELLER 的桶 → 回源（fake_loader 被调且拿到它）
    assert seen.get("seller") == other_seller
