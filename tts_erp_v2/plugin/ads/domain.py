"""Analytics 领域类型（v4 daily-sync-with-coverage）。

纯领域层 —— 无 I/O、无框架、无 DB。定义流经本服务的全部值对象形状。

2026-09-10 daily-sync-with-coverage（tech-doc/analytics/daily-sync-with-coverage.md）：
- v4 协议：插件按 coverage diff 决策后，按 day/monthly/today 粒度上传结构化 rows。
- Server 端写入 ad_today / ad_daily / ad_monthly 三张结构化表。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

# kind 常量（与 DB CHECK 约束 / 插件协议对齐）
KIND_DAILY = "daily"


class StorageKey(str, Enum):
    """Allowlist of dataset identifiers (mirrors the Chrome extension)."""

    PRODUCT_ANALYSES = "productAnalyses"
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


__all__ = [
    "DEFAULT_TIMEZONE",
    "KIND_DAILY",
    "Scope",
    "StorageKey",
]
