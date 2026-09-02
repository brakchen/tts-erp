"""Analytics 领域类型（v2）。

纯领域层 —— 无 I/O、无框架、无 DB。定义流经本服务的全部值对象形状。

2026-09-02 从 ``analytics_sync/domain.py`` 零逻辑平移（v2 化，
tech-doc/analytics-v2-migration-plan.md）。本模块是 Chrome extension
协议契约的一部分：幂等键推导、裁剪规则、envelope 形状全都字节级锁定，
改动必须升 protocolVersion。

Layering:
    domain.py            (本文件 —— 仅类型)
    ↓
    repository.py        (SQLAlchemy 存储)
    ↓
    api/v2/analytics.py  (FastAPI handlers)

注：旧 ``AnalyticsRepository`` Protocol（为 standalone 服务的测试替身而设）
随 v2 化移除 —— handler 直接调 repository 模块函数 + 请求级 session，
测试走真实 DB（tests_v2 事务回滚隔离）。
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import date, datetime
from enum import Enum
from typing import Any


class StorageKey(str, Enum):
    """Allowlist of dataset identifiers (mirrors the Chrome extension)."""

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
@dataclass(frozen=True)
class Scope:
    """seller/advertiser pair from the plugin's request scope block."""

    seller_id: str
    advertiser_id: str
    shop_name: str | None = None


@dataclass(frozen=True)
class Record:
    """A single analytics record as it arrives in the batch payload.

    `idempotency_key` is computed by the server from the canonical fields
    (see canonical_json_for_key). The client also sends its own value
    which we verify matches; mismatch → SCHEMA_INVALID.
    """

    idempotency_key: str
    source_record_id: str | None
    storage_key: StorageKey
    campaign_id: str
    day: date
    page: int
    endpoint: str
    method: str
    request_body: dict[str, Any] | None
    response: dict[str, Any]
    source: str
    captured_at: datetime
    schema_version: int = 1
    expected_page_count: int | None = None
    protocol_version: int = 1


@dataclass(frozen=True)
class CursorEntry:
    """One row of the cursor response — the per-(scope, dataset, campaign)
    state of the latest persisted day."""

    storage_key: StorageKey
    campaign_id: str
    latest_completed_day: date | None
    next_required_day: date


@dataclass(frozen=True)
class CursorPage:
    """Cursor endpoint response payload (data field only)."""

    timezone: str
    items: list[CursorEntry]
    next_cursor: str | None


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
