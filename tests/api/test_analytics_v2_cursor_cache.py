"""cursor has-data 进程内存缓存（has_data_cache.py）的端点行为测试。

覆盖（与 has_data_cache.py 单测互补，这里走完整 HTTP + middleware）：
1. 缓存命中不碰 DB/session —— 命中后把 repository loader monkeypatch 成
   会 raise，GET 仍正常返回（证明没回源）。
2. miss 回源灌桶 —— 首次 GET（空库）返回 false，之后 loader 已废仍能
   GET（空集合也是命中）。
3. /dumps 成功 write-through —— 灌了空桶后再 POST dump，同 (endpoint,day)
   立刻 true（无 TTL 等待、无 DB 重载）。
4. 先 POST 后 GET（桶未加载）—— 走全量回源，不建半桶。
5. 无 campaignId 请求不缓存 —— 每发必走 has_data DB 路径。
6. scope 隔离 —— 同 campaign 不同 seller 不共享桶。

数据隔离：TEST_ 哨兵 + autouse 直连删除 ad_raw（清理模式同
tests/analytics/test_repository.py）；_isolate_state（tests/api/conftest）
负责每测 reset 进程缓存。
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest
from sqlalchemy import text

pytestmark = [pytest.mark.domain_api, pytest.mark.layer_integration]

SELLER = "TEST_seller-cursor-cache"
ADVERTISER = "TEST_adv-cursor-cache"
CAMPAIGN = "TEST_campaign-cursor-cache"
ENDPOINT = "/oec_ads/shopping/v1/oec/stat/post_product_list"
DAY = "2026-08-23"
DAY_2 = "2026-08-24"


@pytest.fixture(autouse=True)
def _cleanup_analytics_rows(db_engine):
    """Setup + teardown 都清一遍本文件哨兵 seller 的 ad_raw 行。"""
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


def _get_cursor(api_client, key, *, seller=SELLER, campaign: str | None = CAMPAIGN, day=DAY):
    params = {
        "sellerId": seller,
        "advertiserId": ADVERTISER,
        "endpoint": ENDPOINT,
        "day": day,
    }
    if campaign is not None:
        params["campaignId"] = campaign
    return api_client.get(
        "/v2/analytics/sync/cursor",
        headers={"Authorization": f"Bearer {key}"},
        params=params,
    )


def _post_dump(api_client, key, *, campaign=CAMPAIGN, day=DAY):
    return api_client.post(
        "/v2/analytics/sync/dumps",
        headers={"Authorization": f"Bearer {key}"},
        json={
            "protocolVersion": 2,
            "requestId": str(uuid.uuid4()),
            "scope": {"sellerId": SELLER, "advertiserId": ADVERTISER},
            "dump": {
                "endpoint": ENDPOINT,
                "method": "POST",
                "day": day,
                "campaignId": campaign,
                "request": {"url": "http://tiktok.test/..."},
                "response": {"status": 200, "body": {"data": {"rows": []}}},
                "capturedAt": "2026-08-23T00:00:00.000Z",
            },
        },
    )


# ─── 命中 / miss 路径 ───────────────────────────────────────────────


def test_cursor_cache_hit_does_not_touch_db(api_client, readwrite_key, monkeypatch):
    """种子桶后 loader 会 raise —— 仍返回正确 hasData，证明命中免 DB。"""
    from tts_erp_v2.analytics import has_data_cache
    from tts_erp_v2.api.v2 import analytics as analytics_module

    has_data_cache.put(SELLER, ADVERTISER, CAMPAIGN, [(ENDPOINT, DAY)])
    monkeypatch.setattr(
        analytics_module,
        "load_campaign_pairs",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("should not hit DB")),
    )
    r = _get_cursor(api_client, readwrite_key)
    assert r.status_code == 200
    assert r.json()["data"]["hasData"] is True
    # 桶里没有的 day → false（仍由缓存回答，不回源）
    r = _get_cursor(api_client, readwrite_key, day=DAY_2)
    assert r.status_code == 200
    assert r.json()["data"]["hasData"] is False


def test_cursor_miss_seeds_empty_bucket(api_client, readwrite_key, monkeypatch):
    """空库首次 GET = miss → 回源灌空桶；之后 loader 已废仍可 GET。"""
    from tts_erp_v2.api.v2 import analytics as analytics_module

    r = _get_cursor(api_client, readwrite_key)
    assert r.status_code == 200
    assert r.json()["data"]["hasData"] is False

    monkeypatch.setattr(
        analytics_module,
        "load_campaign_pairs",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("should be cached now")),
    )
    r = _get_cursor(api_client, readwrite_key)
    assert r.status_code == 200
    assert r.json()["data"]["hasData"] is False


# ─── write-through（/dumps）─────────────────────────────────────────


def test_dumps_write_through_flips_cursor_immediately(
    api_client, readwrite_key, monkeypatch
):
    """空桶（hasData false）→ POST dump → 同 (endpoint, day) 立刻 true，
    且 loader 已废也能命中 —— 证明是 mark_present 而不是回源。"""
    from tts_erp_v2.analytics import has_data_cache
    from tts_erp_v2.api.v2 import analytics as analytics_module

    has_data_cache.put(SELLER, ADVERTISER, CAMPAIGN, [])  # 已加载空桶
    r = _get_cursor(api_client, readwrite_key)
    assert r.json()["data"]["hasData"] is False

    r = _post_dump(api_client, readwrite_key)
    assert r.status_code == 200
    assert r.json()["data"]["status"] == "inserted"

    monkeypatch.setattr(
        analytics_module,
        "load_campaign_pairs",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("should be cached now")),
    )
    r = _get_cursor(api_client, readwrite_key)
    assert r.json()["data"]["hasData"] is True
    # 同一 campaign 没 dump 过的 day 仍 false（缓存精确性）
    r = _get_cursor(api_client, readwrite_key, day=DAY_2)
    assert r.json()["data"]["hasData"] is False


def test_dumps_before_any_get_no_half_bucket(api_client, readwrite_key):
    """桶未加载时先 POST：mark no-op，随后 GET 全量回源（含新行）→ true。"""
    r = _post_dump(api_client, readwrite_key)
    assert r.status_code == 200
    r = _get_cursor(api_client, readwrite_key)
    assert r.json()["data"]["hasData"] is True
    r = _get_cursor(api_client, readwrite_key, day=DAY_2)
    assert r.json()["data"]["hasData"] is False


# ─── 不缓存路径 / 隔离 ──────────────────────────────────────────────


def test_cursor_without_campaign_id_never_cached(
    api_client, readwrite_key, monkeypatch
):
    """无 campaignId 每发必走 has_data DB 路径（不命中 campaign 桶）。"""
    from tts_erp_v2.analytics import has_data_cache
    from tts_erp_v2.api.v2 import analytics as analytics_module

    calls = {"n": 0}

    def fake_has_data(sess, **kwargs):
        calls["n"] += 1
        return SimpleNamespace(has_data=False)

    monkeypatch.setattr(analytics_module, "has_data", fake_has_data)
    # 缓存里先塞同 campaign 桶 —— 无 campaignId 请求不该命中它
    has_data_cache.put(SELLER, ADVERTISER, CAMPAIGN, [(ENDPOINT, DAY)])

    r1 = _get_cursor(api_client, readwrite_key, campaign=None)  # no campaignId
    assert r1.status_code == 200
    assert r1.json()["data"]["hasData"] is False
    r2 = _get_cursor(api_client, readwrite_key, campaign=None)
    assert r2.status_code == 200
    assert calls["n"] == 2  # 两次都走 DB，未缓存


def test_cursor_cache_scope_isolated_per_seller(api_client, readwrite_key, monkeypatch):
    """同 campaign_id 不同 seller 不共享桶（key 含 scope）。"""
    from tts_erp_v2.analytics import has_data_cache
    from tts_erp_v2.api.v2 import analytics as analytics_module

    has_data_cache.put(SELLER, ADVERTISER, CAMPAIGN, [(ENDPOINT, DAY)])
    seen = {}

    def fake_loader(sess, *, seller_id, advertiser_id, campaign_id):
        seen["seller"] = seller_id
        return frozenset()

    monkeypatch.setattr(analytics_module, "load_campaign_pairs", fake_loader)
    other_seller = "TEST_seller-cursor-cache-other"
    r = _get_cursor(api_client, readwrite_key, seller=other_seller)
    assert r.status_code == 200
    assert r.json()["data"]["hasData"] is False
    # 不同 seller 命中不了 SELLER 的桶 → 回源（fake_loader 被调且拿到它）
    assert seen.get("seller") == other_seller
