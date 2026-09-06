"""has_data_cache 单元测试（假时钟，无 DB）。

覆盖：get miss / put 后命中 / 空集合命中 / put 替换 / TTL 过期驱逐 /
mark_present write-through（已加载桶 +1、未加载 no-op、过期 no-op、幂等）/
scope 隔离 / reset / sweep。TTL 用假单调钟推进，不 sleep。

first-party 模块按仓库测试惯例在函数内 import（见 tests/analytics/
test_repository.py）—— 避免模块级 first-party import 触发 isort 段歧义。
"""

from __future__ import annotations

import pytest

SELLER = "TEST_seller-cache"
ADVERTISER = "TEST_adv-cache"
CAMPAIGN = "TEST_campaign-cache"
EP = "/oec_ads/shopping/v1/oec/stat/post_product_list"
DAY = "2026-08-23"
OTHER_DAY = "2026-08-24"


class _FakeClock:
    """monotonic() 返回可推进的假时钟。"""

    def __init__(self) -> None:
        self.now = 0.0

    def monotonic(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


@pytest.fixture(autouse=True)
def _isolate(monkeypatch):
    from tts_erp_v2.analytics import has_data_cache

    has_data_cache.reset()
    fake = _FakeClock()
    monkeypatch.setattr(has_data_cache, "time", fake)
    yield fake
    has_data_cache.reset()


# ─── get / put ───────────────────────────────────────────────────────


def test_get_returns_none_before_any_put():
    from tts_erp_v2.analytics import has_data_cache

    assert has_data_cache.get(SELLER, ADVERTISER, CAMPAIGN) is None


def test_put_then_get_returns_pairs():
    from tts_erp_v2.analytics import has_data_cache

    has_data_cache.put(
        SELLER, ADVERTISER, CAMPAIGN, [(EP, DAY), (EP, OTHER_DAY)]
    )
    pairs = has_data_cache.get(SELLER, ADVERTISER, CAMPAIGN)
    assert pairs is not None
    assert pairs == {(EP, DAY), (EP, OTHER_DAY)}


def test_empty_put_is_hit_not_miss():
    """空集合 = 已加载确无数据（hasData false），与 None（未加载）区分。"""
    from tts_erp_v2.analytics import has_data_cache

    has_data_cache.put(SELLER, ADVERTISER, CAMPAIGN, [])
    pairs = has_data_cache.get(SELLER, ADVERTISER, CAMPAIGN)
    assert pairs is not None
    assert pairs == frozenset()


def test_put_replaces_previous_pairs():
    from tts_erp_v2.analytics import has_data_cache

    has_data_cache.put(SELLER, ADVERTISER, CAMPAIGN, [(EP, DAY)])
    has_data_cache.put(SELLER, ADVERTISER, CAMPAIGN, [(EP, OTHER_DAY)])
    pairs = has_data_cache.get(SELLER, ADVERTISER, CAMPAIGN)
    assert pairs == {(EP, OTHER_DAY)}


def test_scope_isolation_same_campaign_different_seller():
    """campaign_id 只在 scope 内唯一 —— key 必须含 seller/advertiser。"""
    from tts_erp_v2.analytics import has_data_cache

    has_data_cache.put(SELLER, ADVERTISER, CAMPAIGN, [(EP, DAY)])
    assert has_data_cache.get("TEST_seller-other", ADVERTISER, CAMPAIGN) is None
    assert has_data_cache.get(SELLER, "TEST_adv-other", CAMPAIGN) is None


# ─── TTL ─────────────────────────────────────────────────────────────


def test_ttl_hit_within_window_and_miss_after(_isolate):
    from tts_erp_v2.analytics import has_data_cache

    _isolate.advance(100.0)
    has_data_cache.put(SELLER, ADVERTISER, CAMPAIGN, [(EP, DAY)])
    _isolate.advance(has_data_cache.CACHE_TTL_S - 1)
    assert has_data_cache.get(SELLER, ADVERTISER, CAMPAIGN) is not None
    _isolate.advance(2)  # 越过 TTL
    assert has_data_cache.get(SELLER, ADVERTISER, CAMPAIGN) is None


# ─── mark_present（write-through）────────────────────────────────────


def test_mark_present_adds_pair_to_loaded_bucket():
    from tts_erp_v2.analytics import has_data_cache

    has_data_cache.put(SELLER, ADVERTISER, CAMPAIGN, [(EP, DAY)])
    updated = has_data_cache.mark_present(
        SELLER, ADVERTISER, CAMPAIGN, EP, OTHER_DAY
    )
    assert updated is True
    pairs = has_data_cache.get(SELLER, ADVERTISER, CAMPAIGN)
    assert pairs == {(EP, DAY), (EP, OTHER_DAY)}


def test_mark_present_idempotent():
    from tts_erp_v2.analytics import has_data_cache

    has_data_cache.put(SELLER, ADVERTISER, CAMPAIGN, [(EP, DAY)])
    assert has_data_cache.mark_present(SELLER, ADVERTISER, CAMPAIGN, EP, DAY) is True
    assert has_data_cache.mark_present(SELLER, ADVERTISER, CAMPAIGN, EP, DAY) is True


def test_mark_present_noop_when_bucket_not_loaded():
    """未加载桶上 mark = no-op（禁止建半桶）：下次 GET 回源全量重载。"""
    from tts_erp_v2.analytics import has_data_cache

    assert has_data_cache.mark_present(SELLER, ADVERTISER, CAMPAIGN, EP, DAY) is False
    assert has_data_cache.get(SELLER, ADVERTISER, CAMPAIGN) is None


def test_mark_present_noop_when_bucket_expired(_isolate):
    from tts_erp_v2.analytics import has_data_cache

    has_data_cache.put(SELLER, ADVERTISER, CAMPAIGN, [(EP, DAY)])
    _isolate.advance(has_data_cache.CACHE_TTL_S + 1)
    assert (
        has_data_cache.mark_present(SELLER, ADVERTISER, CAMPAIGN, EP, OTHER_DAY)
        is False
    )
    assert has_data_cache.get(SELLER, ADVERTISER, CAMPAIGN) is None


# ─── reset / sweep ───────────────────────────────────────────────────


def test_reset_clears_all_buckets():
    from tts_erp_v2.analytics import has_data_cache

    has_data_cache.put(SELLER, ADVERTISER, CAMPAIGN, [(EP, DAY)])
    has_data_cache.reset()
    assert has_data_cache.get(SELLER, ADVERTISER, CAMPAIGN) is None


def test_sweep_removes_expired_on_put(_isolate):
    from tts_erp_v2.analytics import has_data_cache

    has_data_cache.put(SELLER, ADVERTISER, CAMPAIGN, [(EP, DAY)])
    _isolate.advance(has_data_cache.CACHE_TTL_S + 1)
    # 另一个桶 put 触发全扫，旧桶应被逐出
    has_data_cache.put(SELLER, ADVERTISER, "TEST_campaign-2", [(EP, DAY)])
    assert has_data_cache.get(SELLER, ADVERTISER, CAMPAIGN) is None
    assert has_data_cache.get(SELLER, ADVERTISER, "TEST_campaign-2") is not None
