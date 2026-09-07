"""In-process live-row cache for GET /v2/analytics/sync/cursor (coverage mode).

设计（2026-09-07，tech-doc/analytics/range-aggregate-history-sync.md §5.2）——
为什么是「campaign → (endpoint, kind, 区间, capturedAt) live 行集」：

- v3 coverage 请求对同一批 (scope × campaign × endpoint × kind) 重复检查 live 行
  是否存在、区间是否匹配（决定 history 是否已 settled、today 是否要刷新）。
- 只缓存 **live 行**（kind history/today）——这是 /cursor coverage 模式的回答对象。
  legacy daily 行折叠（物理 DELETE）只发生在 kind='daily'，live 行集不受影响，
  ⇒ 无 stale-true（红线论证保持成立）。
- miss 时一条 SQL 只拉 live 5 元组（endpoint, kind, day_start, day_end,
  captured_at），不碰 request/response JSONB blob，灌入后纯内存 frozenset 判断。

一致性：进程内 dict + threading.Lock（uvicorn 单进程）；key 含
seller_id/advertiser_id —— campaign_id 只在 scope 内唯一，防多店铺撞 id。
TTL 由 loaded_at 单调钟判定（10 min）；桶只增不删、惰性驱逐 + put 时全扫。

写路径 mark_present：live upsert（inserted/updated）后把该行 upsert 进桶
（同 (endpoint, kind) 替换，history 推进/今天快照都正确反映）；stale_ignored
**不** mark（行未变）。桶未加载时 no-op（下次 GET 回源全量重载，结果必对）。

⚠️ 依赖不变量（红线，review Finding-2/3 更新版）：
- **live 行只 upsert 不删** 是本缓存无 stale-true 的 load-bearing 前提。物理删除
  仅允许 kind='daily' legacy 折叠（repository._fold_daily），不进 live 桶；
  **禁止**任何对 history/today 行的运维/手工 DELETE，否则最长 10min stale-true →
  插件跳过本应抓取的区间。
- **uvicorn 单进程**（ExecStart 无 --workers）是 load-bearing 假设：多 worker 下
  mark_present 只更新本 worker 桶 → 跨 worker stale-false 至多 TTL 窗口（仍无
  stale-true、自愈），但「命中免 DB」收益打折。
"""

from __future__ import annotations

import threading
import time
from collections.abc import Iterable

CACHE_TTL_S = 600  # 10 分钟；桶过期后下次 get 视为 miss，从 DB 全量重载

# live 行 5 元组：(endpoint, kind, day_start.isoformat(), day_end.isoformat(),
# captured_at.isoformat())。capturedAt 供 coverage 响应直接回显。
LiveRow = tuple[str, str, str, str, str]

_CacheKey = tuple[str, str, str]


class _Bucket:
    __slots__ = ("loaded_at", "rows")

    def __init__(self, rows: frozenset[LiveRow]) -> None:
        self.loaded_at = time.monotonic()
        self.rows = rows


_buckets: dict[_CacheKey, _Bucket] = {}
_lock = threading.Lock()


def get(
    seller_id: str,
    advertiser_id: str,
    campaign_id: str,
) -> frozenset[LiveRow] | None:
    """Return the cached live-row set, or None on miss/expiry.

    ``frozenset()``（空集合）与 None 是两种状态：前者表示「已加载、确无 live 行」，
    后者表示「未加载/过期，需回源」。
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
        return bucket.rows


def put(
    seller_id: str,
    advertiser_id: str,
    campaign_id: str,
    rows: Iterable[LiveRow],
) -> None:
    """Seed/replace the bucket for (scope, campaign) with fresh DB live rows."""
    key = (seller_id, advertiser_id, campaign_id)
    normalized = frozenset(rows)
    with _lock:
        _buckets[key] = _Bucket(normalized)
        _sweep_expired_locked()


def mark_present(
    seller_id: str,
    advertiser_id: str,
    campaign_id: str,
    row: LiveRow,
) -> bool:
    """Write-through: live upsert 成功落库后把该行 upsert 进桶（同 endpoint+kind 替换）。

    Returns True when a **loaded** bucket was updated; False when no bucket
    exists (no-op — 下次 GET 从 DB 全量回源，已含此行，结果必对)。
    """
    key = (seller_id, advertiser_id, campaign_id)
    endpoint, kind = row[0], row[1]
    with _lock:
        bucket = _buckets.get(key)
        if bucket is None or time.monotonic() - bucket.loaded_at >= CACHE_TTL_S:
            return False
        keep = {r for r in bucket.rows if not (r[0] == endpoint and r[1] == kind)}
        if row not in bucket.rows:
            bucket.rows = frozenset(keep | {row})
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
    "LiveRow",
    "get",
    "mark_present",
    "put",
    "reset",
]
