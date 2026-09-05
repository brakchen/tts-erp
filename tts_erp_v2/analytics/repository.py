"""Analytics 存储层（v2 dump architecture + 2026-09-05 reorg,SQLAlchemy）。

2026-09-02 从 v2 cursor/batches 重构为 dump architecture（tech-doc/analytics/
dump-architecture.md）：
- 单事务写 ad_raw + ad_records + ad_daily_completeness（已废）
- ad_raw 是 source-of-truth
- 删除 fetch_cursor_page / upsert_records / _recompute_cursors 等老逻辑
- 新增 upsert_dump / has_data（ad_raw 存在性检查）

2026-09-05 reorg（tech-doc/analytics/reorg-plan.md 决策 #1-#4）：
- 删 ad_records / ad_daily_completeness 后,upsert_dump 缩为**单表写**
  (只 INSERT ad_raw, ON CONFLICT 5 元组 DO UPDATE, RETURNING xmax=0
  判 inserted/duplicate)。
- 删 ad_audit_log 后,审计职责迁出本模块（见 tts_erp_v2/api/v2/analytics.py
  的 logger 单行日志）。
- 删 ad_shop_timezones 后,fetch_timezone / SQL_UPSERT|GET|SEED|REPAIR_TIMEZONE
  全部移除；_today_in_tz / DEFAULT_TIMEZONE 也无调用方,domain 一并清理。

SQL 以模块级 text() 常量书写（v2 惯例,见 api/v2/reporting.py 的
SQL_COST_SNAPSHOTS 模式）。表全部 schema 限定为 analytics.ad_*。
"""

from __future__ import annotations

import json
from datetime import date

from sqlalchemy import text
from sqlalchemy.orm import Session

from .domain import (
    DumpPayload,
    DumpResult,
    HasDataResult,
    StorageKey,
    compute_idempotency_key,
)

# ─── endpoint → storage_key 映射（server-side 单点定义）────────────────
# 4 路径 1:1 映射（post_campaign_list 是 discovery,不走 dump 协议,不在此表）:
# - /oec_ads/.../post_product_list      → productAnalyses
# - /oec_ads/.../post_session_list      → sessionAnalyses
# - /oec_ads/.../campaign_opt_log_list  → campaignChangeLogs
# plugin dump 协议不传 storageKey（消除 client 端 enum 知识）,
# server 端用 STORAGE_KEY_BY_PATH[endpoint] 推导。
STORAGE_KEY_BY_PATH: dict[str, StorageKey] = {
    "/oec_ads/shopping/v1/oec/stat/post_product_list": StorageKey.PRODUCT_ANALYSES,
    "/oec_ads/shopping/v1/oec/stat/post_session_list": StorageKey.SESSION_ANALYSES,
    "/oec_ads/shopping/v1/oec/stat/campaign_opt_log_list": StorageKey.CAMPAIGN_CHANGE_LOGS,
}


# ─── SQL 常量（模块级,无插值）────────────────────────────────────────

# ad_raw INSERT（source-of-truth,immutable raw dump）
# xmax=0 判 inserted / duplicate: xmax=0 表示新插,否则是 update
SQL_INSERT_RAW = """
INSERT INTO analytics.ad_raw (
    idempotency_key, seller_id, advertiser_id, endpoint, method,
    day, campaign_id, request, response, captured_at, source, request_id,
    protocol_version, schema_version
) VALUES (
    :idempotency_key, :seller_id, :advertiser_id, :endpoint, :method,
    :day, :campaign_id,
    CAST(:request AS JSONB), CAST(:response AS JSONB),
    :captured_at, :source, :request_id, :protocol_version, :schema_version
)
ON CONFLICT (seller_id, advertiser_id, endpoint, day, campaign_id) DO UPDATE
    SET request        = EXCLUDED.request,
        response       = EXCLUDED.response,
        captured_at     = EXCLUDED.captured_at,
        idempotency_key = EXCLUDED.idempotency_key,
        received_at     = now()
RETURNING (xmax = 0) AS was_inserted
"""

# has-data 检查（GET /cursor has-data 模式）
SQL_HAS_DATA = """
SELECT 1 FROM analytics.ad_raw
WHERE seller_id = :seller_id AND advertiser_id = :advertiser_id
  AND endpoint = :endpoint AND day = :day
  AND (CAST(:campaign_id AS text) IS NULL OR campaign_id = :campaign_id)
LIMIT 1
"""


# ─── dump upsert（1 表 1 事务）────────────────────────────────────────


def upsert_dump(
    sess: Session,
    dump: DumpPayload,
    request_id: str | None,
) -> DumpResult:
    """Insert one dump in the **single** source-of-truth table (ad_raw).

    2026-09-05 reorg 后由"1 事务写 3 张表"缩为"1 事务写 1 张表"——
    ad_records / ad_daily_completeness 已删（见 tech-doc/analytics/
    reorg-plan.md 决策 #1-#2），无派生表可写。

    - ON CONFLICT 5 元组 DO UPDATE, RETURNING xmax=0 判 inserted/duplicate
    - page 隐式 = 1（dump 协议下一天一 dump）,幂等键 6 字段 SHA-256
      (seller, advertiser, storage_key, campaign_id, day, page=1),
      与 v2 batches 协议字节兼容。

    注意 ad_raw 是 upsert 语义（ON CONFLICT DO UPDATE）,技术上不是
    append-only 日志——未来若要 append-only 改造另案,不在本 reorg 范围。
    """
    # 1. 算 idempotency_key (page 隐式 = 1)
    idem_key = compute_idempotency_key(
        seller_id=dump.seller_id,
        advertiser_id=dump.advertiser_id,
        storage_key=dump.storage_key,
        campaign_id=dump.campaign_id,
        day=dump.day,
        page=1,
    )

    # 2. INSERT ad_raw (source-of-truth, immutable raw dump)
    # pi-lens-ignore: python-sql-injection
    was_inserted = sess.execute(
        text(SQL_INSERT_RAW),
        {
            "idempotency_key": idem_key,
            "seller_id": dump.seller_id,
            "advertiser_id": dump.advertiser_id,
            "endpoint": dump.endpoint,
            "method": dump.method,
            "day": dump.day,
            "campaign_id": dump.campaign_id,
            "request": json.dumps(dump.request, ensure_ascii=False),
            "response": json.dumps(dump.response, ensure_ascii=False),
            "captured_at": dump.captured_at,
            "source": dump.source,
            "request_id": request_id,
            "protocol_version": dump.protocol_version,
            "schema_version": dump.schema_version,
        },
    ).scalar()

    sess.commit()
    return DumpResult(
        idempotency_key=idem_key,
        status="inserted" if was_inserted else "duplicate",
    )


# ─── has-data 检查（GET /cursor has-data 模式）──────────────────────


def has_data(
    sess: Session,
    *,
    seller_id: str,
    advertiser_id: str,
    endpoint: str,
    day: date,
    campaign_id: str | None = None,
) -> HasDataResult:
    """存在性查询:ad_raw 有没有该 (scope, endpoint, day[, campaign_id]) 的行。

    Plugin 用作防 TikTok 风控的预检闸:hasData=true → 跳过（不打 TikTok）。
    返回的 storage_key 由 endpoint 推导（不在 DB,纯逻辑计算）。
    """
    storage_key = STORAGE_KEY_BY_PATH.get(endpoint)
    if storage_key is None:
        raise ValueError(f"unknown endpoint: {endpoint}")

    # pi-lens-ignore: python-sql-injection
    row = sess.execute(
        text(SQL_HAS_DATA),
        {
            "seller_id": seller_id,
            "advertiser_id": advertiser_id,
            "endpoint": endpoint,
            "day": day,
            "campaign_id": campaign_id,
        },
    ).scalar()

    return HasDataResult(
        day=day,
        endpoint=endpoint,
        storage_key=storage_key,
        has_data=row is not None,
        campaign_id=campaign_id,
    )


__all__ = [
    "STORAGE_KEY_BY_PATH",
    "SQL_HAS_DATA",
    "SQL_INSERT_RAW",
    "has_data",
    "upsert_dump",
]
