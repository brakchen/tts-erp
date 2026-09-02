"""Analytics 领域类型（v2，dump architecture）。

纯领域层 —— 无 I/O、无框架、无 DB。定义流经本服务的全部值对象形状。

2026-09-02 v2 dump 化（tech-doc/analytics/dump-architecture.md）：
- ``Record`` 去掉 ``page`` / ``expected_page_count`` 字段（dump 1 天 1 行，page 隐式 = 1）
- 删除 ``CursorEntry`` / ``CursorPage``（cursor 协议 work-list 模式不再适用）
- 新增 ``DumpPayload``（plugin dump 入口）/ ``DumpResult``（idempotency_key + status）
- 新增 ``HasDataResult``（GET /cursor has-data 模式的响应）
- 保留 ``Scope`` / ``StorageKey`` / ``AcceptedRecord`` / ``RejectedRecord`` 类型
  （虽然 v2 dump 协议是单 record 模式，但 ``BatchResult`` 仍供 audit/未来扩展）

Layering:
    domain.py            (本文件 —— 仅类型)
    ↓
    repository.py        (SQLAlchemy 存储：ad_raw + ad_records + ad_daily_completeness)
    ↓
    api/v2/analytics.py  (FastAPI handlers: /dumps + /cursor has-data)

⚠️ 协议契约（dump architecture 锁定）：
- dump 字段单 object 不可 list
- ad_raw 5 元组 unique (seller_id, advertiser_id, endpoint, day, campaign_id)
- 幂等键 6 字段 SHA-256（page 隐式 = 1）
- 所有 endpoint→storageKey 1:1 映射在 server 端常量 (STORAGE_KEY_BY_PATH)
- ad_raw 与 ad_records / ad_daily_completeness 无 FK，逻辑链接靠 shared 5 元组 key
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import date, datetime
from enum import Enum
from typing import Any


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


# Sentinel pattern: server-side compute of the canonical idempotency key
# must produce the exact same hex string the plugin sent. Trimming rules
# below are part of the protocol; do NOT change without bumping the
# protocol version.
#
# dump architecture: page 隐式 = 1,仍走 6 字段 SHA-256。DumpPayload 转换时固定传
# page=1,所有 dump 的 idempotency_key 算法与旧 v2 batches 协议字节兼容。
@dataclass(frozen=True)
class Scope:
    """seller/advertiser pair from the plugin's request scope block."""

    seller_id: str
    advertiser_id: str
    shop_name: str | None = None


@dataclass(frozen=True)
class DumpPayload:
    """One dump = one (scope, endpoint, day, campaign_id) row.

    Plugin dump 协议输入。
    - ``request`` 完整 HTTP 交换(URL + headers + body)
    - ``response`` 完整 HTTP 交换(status + headers + body)
    - ``page`` 隐式 = 1(dump architecture 下一天一 dump,page 维度消失)
    - ``storage_key`` 由 server 端 STORAGE_KEY_BY_PATH 从 endpoint 推导,
      不来自 plugin 端(消除客户端 enum 知识)
    - ``expected_page_count`` 完全删除(若需要由 server 端从 response 抽)
    """

    seller_id: str
    advertiser_id: str
    endpoint: str
    method: str
    day: date
    campaign_id: str
    request: dict[str, Any]
    response: dict[str, Any]
    captured_at: datetime
    storage_key: StorageKey  # server-derived
    request_id: str | None = None
    source: str = "tiktok-shop-data-sync"
    protocol_version: int = 2
    schema_version: int = 1


@dataclass(frozen=True)
class DumpResult:
    """Output of upsert_dump: idempotency_key + status."""

    idempotency_key: str
    status: str  # "inserted" | "duplicate"


@dataclass(frozen=True)
class HasDataResult:
    """Output of has_data (GET /cursor has-data 模式):storageKey + bool.

    日后 plugin 端用来做"这天的这个 endpoint 是否已经 dump 过了"的预检闸,
    避免重复打 TikTok 触发风控。
    """

    day: date
    endpoint: str
    storage_key: StorageKey
    has_data: bool
    campaign_id: str | None = None  # only present if queried


# 保留 AcceptedRecord / RejectedRecord / BatchResult 供未来 /batches 类型兼容或
# 单 dump 内部 per-field rejected 时使用(目前 dump 协议是整 dump accepted/whole
# 失败二选一,但保留类型未来扩展)
@dataclass(frozen=True)
class AcceptedRecord:
    idempotency_key: str
    status: str  # "inserted" | "duplicate"


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


# ─── Canonical JSON for idempotency key ──────────────────────────────
# Per protocol §2: keys must be sorted, UTF-8, no insignificant
# whitespace, exact string values after trimming. We canonicalize the
# five fields explicitly rather than running json.dumps with sort_keys,
# because we also need to coerce page to int and day to ISO string.


def canonical_json_for_key(
    *,
    seller_id: str,
    advertiser_id: str,
    storage_key: StorageKey | str,
    campaign_id: str,
    day: date | str,
    page: int | str,
) -> str:
    """Return the canonical JSON string used as input to sha256.

    `page` is coerced to int (so `1` and `"1"` produce the same hash).
    `day` is coerced to ISO `YYYY-MM-DD` if a `date` object is passed.
    String fields are stripped. Keys are sorted (ASCII); separators are
    `(",", ":")` to remove insignificant whitespace; non-ASCII characters
    are passed through verbatim (ensure_ascii=False).
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
    """sha256 hex digest of canonical_json_for_key(...).

    `page` accepts both int and str (e.g. 1 vs "1"); `int()` is applied
    inside canonical_json_for_key before hashing so the two are
    interchangeable.

    dump architecture 下调用方固定传 page=1（dump 1 天 1 行），
    与 v2 batches 协议字节兼容 —— 同一 (5 fields, page=1) 输入产生
    同一哈希。
    """
    payload = canonical_json_for_key(
        seller_id=seller_id,
        advertiser_id=advertiser_id,
        storage_key=storage_key,
        campaign_id=campaign_id,
        day=day,
        page=page,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
