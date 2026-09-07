"""Analytics 领域类型（v2 + v3 range-aggregate, dump architecture）。

纯领域层 —— 无 I/O、无框架、无 DB。定义流经本服务的全部值对象形状。

2026-09-02 v2 dump 化（tech-doc/analytics/dump-architecture.md）：
- ``Record`` 去掉 ``page`` / ``expected_page_count`` 字段（dump 1 天 1 行,page 隐式 = 1）
- 新增 ``DumpPayload``（plugin dump 入口）/ ``DumpResult``（idempotency_key + status）
- 新增 ``HasDataResult``（GET /cursor has-data 模式的响应）

2026-09-07 range-aggregate（tech-doc/analytics/range-aggregate-history-sync.md）：
- ``DumpPayload`` 从单 ``day`` 升级为 ``kind`` + 区间 ``[day_start..day_end]``：
  - kind='history' 历史整段快照 [S..T-1]
  - kind='today'   今日快照 [T..T]
  - kind='daily'   legacy 逐日行（旧 v2 插件写入兼容）
- 新增 protocol v3 幂等键公式（6 字段含 kind/dayStart/dayEnd；v2 分支保留）
- 新增 ``LiveRowResult``（/cursor coverage 模式的 live 行状态）

⚠️ 协议契约（dump architecture 锁定 + v3 扩展）：
- dump 字段单 object 不可 list
- live 行 unique (seller_id, advertiser_id, endpoint, campaign_id, kind)
- v2 幂等键 6 字段 SHA-256（day+page=1）；v3 幂等键 6 字段 SHA-256（kind+dayStart+dayEnd）
- 所有 endpoint→storageKey 1:1 映射在 server 端常量 (STORAGE_KEY_BY_PATH)
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import date, datetime
from enum import Enum
from typing import Any

# kind 常量（与 DB CHECK 约束 / 插件协议对齐）
KIND_HISTORY = "history"
KIND_TODAY = "today"
KIND_DAILY = "daily"
LIVE_KINDS = (KIND_HISTORY, KIND_TODAY)


class StorageKey(str, Enum):
    """Allowlist of dataset identifiers (mirrors the Chrome extension).
    dump architecture 改造后：仅 3 个 dump 端点对应这 3 个 enum。
    discovery 端点 post_campaign_list 不 dump，单独流程。"""

    PRODUCT_ANALYSES = "productAnalyses"
    SESSION_ANALYSES = "sessionAnalyses"
    CAMPAIGN_CHANGE_LOGS = "campaignChangeLogs"


# Default IANA timezone for sellers without an explicit setting.
# Single source of truth — handlers and repository both import from here.
DEFAULT_TIMEZONE = "Asia/Shanghai"


@dataclass(frozen=True)
class Scope:
    """seller/advertiser pair from the plugin's request scope block."""

    seller_id: str
    advertiser_id: str
    shop_name: str | None = None


@dataclass(frozen=True)
class DumpPayload:
    """One dump = one (scope, endpoint, campaign, kind) row with an interval.

    Plugin dump 协议输入。
    - ``request`` 完整 HTTP 交换(URL + headers + body)
    - ``response`` 完整 HTTP 交换(status + headers + body)
    - ``page`` 隐式 = 1(dump architecture 下无 page 维度)
    - ``kind`` ∈ history / today / daily；``day_start``/``day_end`` 定义覆盖区间
      （daily 行 day_start == day_end == 单日）
    - ``storage_key`` 由 server 端 STORAGE_KEY_BY_PATH 从 endpoint 推导,
      不来自 plugin 端(消除客户端 enum 知识)
    """

    seller_id: str
    advertiser_id: str
    endpoint: str
    method: str
    kind: str
    day_start: date
    day_end: date
    campaign_id: str
    request: dict[str, Any]
    response: dict[str, Any]
    captured_at: datetime
    storage_key: StorageKey  # server-derived
    request_id: str | None = None
    source: str = "tiktok-shop-data-sync"
    protocol_version: int = 3
    schema_version: int = 2


@dataclass(frozen=True)
class DumpResult:
    """Output of upsert_dump: idempotency_key + status."""

    idempotency_key: str
    status: str  # inserted | updated | duplicate | stale_ignored


@dataclass(frozen=True)
class HasDataResult:
    """Output of legacy day-based has_data (GET /cursor, 无 kind):storageKey + bool.

    旧 v2 插件用"这个 (scope, endpoint, day[, campaignId]) 有没有数据"做防 TikTok
    风控预检闸。覆盖语义：daily 行 day_end=day，或 live 行区间含该 day。
    """

    day: date
    endpoint: str
    storage_key: StorageKey
    has_data: bool
    campaign_id: str | None = None  # only present if queried


@dataclass(frozen=True)
class LiveRowResult:
    """Output of coverage lookup (GET /cursor, kind=history|today).

    带 campaignId 时返回该 (scope,endpoint,campaign) 的 live 行状态（如有）。
    """

    endpoint: str
    storage_key: StorageKey
    campaign_id: str | None = None
    kind: str | None = None
    has_row: bool = False
    day_start: date | None = None
    day_end: date | None = None
    captured_at: datetime | None = None


# 保留 AcceptedRecord / RejectedRecord / BatchResult 供未来扩展使用
@dataclass(frozen=True)
class AcceptedRecord:
    idempotency_key: str
    status: str  # "inserted" | "updated" | "duplicate"


@dataclass(frozen=True)
class RejectedRecord:
    idempotency_key: str
    code: str  # SCHEMA_INVALID, IDEMPOTENCY_KEY_MISMATCH, etc.
    message: str
    retryable: bool


@dataclass(frozen=True)
class BatchResult:
    accepted: list[AcceptedRecord]
    rejected: list[RejectedRecord]


# ─── Canonical JSON for v2 idempotency key ───────────────────────────
# Per protocol §2: keys must be sorted, UTF-8, no insignificant
# whitespace, exact string values after trimming.


def canonical_json_for_key(
    *,
    seller_id: str,
    advertiser_id: str,
    storage_key: StorageKey | str,
    campaign_id: str,
    day: date | str,
    page: int | str,
) -> str:
    """Return the canonical JSON string used as input to sha256 (v2).

    `page` is coerced to int (so `1` and `"1"` produce the same hash).
    `day` is coerced to ISO `YYYY-MM-DD` if a `date` object is passed.
    """
    storage_key_str = (
        storage_key.value if isinstance(storage_key, StorageKey) else storage_key
    )
    day_str = day.isoformat() if isinstance(day, date) else day
    try:
        page_int = int(page)
    except (TypeError, ValueError) as exc:
        raise ValueError(
            f"page must be coercible to int (got {page!r}); "
            "see protocol §2 — page is a positive integer"
        ) from exc
    return json.dumps(
        {
            "sellerId": seller_id.strip(),
            "advertiserId": advertiser_id.strip(),
            "storageKey": storage_key_str,
            "campaignId": campaign_id.strip(),
            "day": day_str,
            "page": page_int,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def compute_idempotency_key(
    *,
    seller_id: str,
    advertiser_id: str,
    storage_key: StorageKey | str,
    campaign_id: str,
    day: date | str,
    page: int | str,
) -> str:
    """sha256 hex digest of canonical_json_for_key(...) (v2, page=1 legacy)."""
    payload = canonical_json_for_key(
        seller_id=seller_id,
        advertiser_id=advertiser_id,
        storage_key=storage_key,
        campaign_id=campaign_id,
        day=day,
        page=page,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


# ─── Canonical JSON for v3 idempotency key（kind + 区间）────────────────


def canonical_json_for_key_v3(
    *,
    seller_id: str,
    advertiser_id: str,
    storage_key: StorageKey | str,
    campaign_id: str,
    kind: str,
    day_start: date | str,
    day_end: date | str,
) -> str:
    """Return the canonical JSON string used as input to sha256 (v3).

    与 v2 同款规则（strip / ISO / sort_keys / 无空白 / UTF-8）。
    """
    storage_key_str = (
        storage_key.value if isinstance(storage_key, StorageKey) else storage_key
    )
    day_start_str = day_start.isoformat() if isinstance(day_start, date) else day_start
    day_end_str = day_end.isoformat() if isinstance(day_end, date) else day_end
    return json.dumps(
        {
            "sellerId": seller_id.strip(),
            "advertiserId": advertiser_id.strip(),
            "storageKey": storage_key_str,
            "campaignId": campaign_id.strip(),
            "kind": kind,
            "dayStart": day_start_str,
            "dayEnd": day_end_str,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def compute_idempotency_key_v3(
    *,
    seller_id: str,
    advertiser_id: str,
    storage_key: StorageKey | str,
    campaign_id: str,
    kind: str,
    day_start: date | str,
    day_end: date | str,
) -> str:
    """sha256 hex digest of canonical_json_for_key_v3(...)."""
    payload = canonical_json_for_key_v3(
        seller_id=seller_id,
        advertiser_id=advertiser_id,
        storage_key=storage_key,
        campaign_id=campaign_id,
        kind=kind,
        day_start=day_start,
        day_end=day_end,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


__all__ = [
    "DEFAULT_TIMEZONE",
    "KIND_DAILY",
    "KIND_HISTORY",
    "KIND_TODAY",
    "LIVE_KINDS",
    "DumpPayload",
    "DumpResult",
    "HasDataResult",
    "LiveRowResult",
    "Scope",
    "StorageKey",
    "canonical_json_for_key",
    "canonical_json_for_key_v3",
    "compute_idempotency_key",
    "compute_idempotency_key_v3",
]
