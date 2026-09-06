"""In-process existence cache for GET /v2/analytics/sync/cursor (has-data).

设计（2026-09-06，tech-doc/analytics/dump-architecture.md「cursor has-data
缓存」小节）——为什么是「campaign → (endpoint, day) 集合」：

- cursor 请求 99.8% 带 campaignId（实测 251,397 / 251,861），是扩展扫历史时
  对同一批 (campaign × endpoint × day) 的重复存在性检查。
- hasData 对 (scope, endpoint, day, campaign) **恒定**（ad_raw 只 upsert 不
  删，无 stale-true）；单写者（唯一写者 = 扩展 POST /dumps）+ /dumps 成功
  后 write-through ⇒ stale-false 窗口收敛到 ~0。
- miss 时一条 SQL 只拉 DISTINCT (endpoint, day)（不碰 request/response
  JSONB blob），灌入后后续命中走纯内存 frozenset 成员检查，不碰 DB/session。

边界（与 has_data SQL 语义严格对齐）：
- 只缓存 campaign-scoped 请求。无 campaignId 的请求（实测 ~60/天）语义是
  「该 (scope, endpoint, day) 有没有任意行（不分 campaign）」，量级可忽略，
  直接走 repository.has_data 原 DB 路径，不缓存。
- campaign_id 列 NOT NULL（schema 约束），所以 key 恒为 str。

一致性：进程内 dict + threading.Lock（uvicorn 单进程）；key 含
seller_id/advertiser_id —— campaign_id 只在 scope 内唯一，防多店铺撞 id。
TTL 由 loaded_at 单调钟判定（10 min）；桶只增不删、惰性驱逐 + put 时全扫。
写路径 mark_present 在桶未加载时 no-op（下次 GET 全量重载，新行已落库，
结果必对）——**禁止**在未加载桶上建「半桶」。

⚠️ 依赖不变量（红线，review Finding-2/3）：
- **ad_raw 只 upsert 不删** 是本缓存无 stale-true 的 load-bearing 前提。任何
  未来对 ad_raw 的手工/运维 DELETE（合规删除、误操作、临时清数）会造成最长
  10min stale-true → 插件跳过本应抓取的 day。setup/analytics-sync.md 已写
  「ad_raw 永久保留」，动它之前先确认本缓存与插件语义。
- **uvicorn 单进程**（ExecStart 无 --workers）是 load-bearing 假设：多 worker
  下 mark_present 只更新本 worker 桶 → 跨 worker stale-false 至多 TTL 窗口
  （仍无 stale-true、自愈），但「命中免 DB」收益打折。加多 worker 前需改
  共享缓存或接受该窗口。

Tests: tests/analytics/test_has_data_cache.py（假时钟单测）+
tests/api/test_analytics_v2_cursor_cache.py（端点集成，_isolate_state 里
reset() 保证测试隔离）。
"""

from __future__ import annotations

import threading
import time
from collections.abc import Iterable

CACHE_TTL_S = 600  # 10 分钟；桶过期后下次 get 视为 miss，从 DB 全量重载

# 桶 value：frozenset[(endpoint 字符串, day.isoformat())]
_CacheKey = tuple[str, str, str]
_Pair = tuple[str, str]


class _Bucket:
    __slots__ = ("loaded_at", "pairs")

    def __init__(self, pairs: frozenset[_Pair]) -> None:
        self.loaded_at = time.monotonic()
        self.pairs = pairs


_buckets: dict[_CacheKey, _Bucket] = {}
_lock = threading.Lock()


def get(
    seller_id: str,
    advertiser_id: str,
    campaign_id: str,
) -> frozenset[_Pair] | None:
    """Return the cached (endpoint, day) set, or None on miss/expiry.

    ``frozenset()``（空集合）与 None 是两种状态：前者表示「已加载、确无
    数据」，后者表示「未加载/过期，需回源」。
    """
    key = (seller_id, advertiser_id, campaign_id)
    now = time.monotonic()
    with _lock:
        bucket = _buckets.get(key)
        if bucket is None:
            return None
        if now - bucket.loaded_at >= CACHE_TTL_S:
            _buckets.pop(key, None)
            return None
        return bucket.pairs


def put(
    seller_id: str,
    advertiser_id: str,
    campaign_id: str,
    pairs: Iterable[_Pair],
) -> None:
    """Seed/replace the bucket for (scope, campaign) with fresh DB rows."""
    key = (seller_id, advertiser_id, campaign_id)
    normalized = frozenset(pairs)
    with _lock:
        _buckets[key] = _Bucket(normalized)
        _sweep_expired_locked()


def mark_present(
    seller_id: str,
    advertiser_id: str,
    campaign_id: str,
    endpoint: str,
    day: str,
) -> bool:
    """Write-through: /dumps 成功落库后把 (endpoint, day) 标为存在。

    Returns True if a **loaded** bucket was updated (or already contained
    the pair); False when no bucket exists (no-op — 下次 GET 从 DB 全量
    重载，已含此行，结果必对)。
    """
    key = (seller_id, advertiser_id, campaign_id)
    with _lock:
        bucket = _buckets.get(key)
        if bucket is None or time.monotonic() - bucket.loaded_at >= CACHE_TTL_S:
            return False
        if (endpoint, day) not in bucket.pairs:
            bucket.pairs = frozenset(bucket.pairs | {(endpoint, day)})
        return True


def reset() -> None:
    """Clear every bucket. Tests call this per-test（_isolate_state）。"""
    with _lock:
        _buckets.clear()


def _sweep_expired_locked() -> None:
    """Drop expired buckets so memory stays bounded by *active* campaigns.

    只在 put 路径调用（持锁内）。桶数量 = 10 分钟内被查过的 campaign 数，
    量级几百，全扫成本可忽略。
    """
    now = time.monotonic()
    expired = [
        key
        for key, bucket in _buckets.items()
        if now - bucket.loaded_at >= CACHE_TTL_S
    ]
    for key in expired:
        _buckets.pop(key, None)


__all__ = [
    "CACHE_TTL_S",
    "get",
    "mark_present",
    "put",
    "reset",
]
