"""Analytics 存储层（v2 dump architecture,SQLAlchemy）。

2026-09-02 从 v2 cursor/batches 重构为 dump architecture（tech-doc/analytics/
dump-architecture.md）：
- 单事务写 3 张表（ad_raw + ad_records + ad_daily_completeness）
- ad_raw 是 source-of-truth,其他 ad_* 表是派生
- 删除 fetch_cursor_page / upsert_records / _recompute_cursors 等老逻辑
- 新增 upsert_dump / has_data（ad_raw 存在性检查）

SQL 以模块级 text() 常量书写（v2 惯例,见 api/v2/reporting.py 的
SQL_COST_SNAPSHOTS 模式）。表全部 schema 限定为 analytics.ad_*。
write_audit 用独立 engine 连接提交（best-effort）。
fetch_timezone 懒写（缺行时补种 Asia/Shanghai）会就地 commit。
"""

from __future__ import annotations

import json
import sys
from datetime import date
from zoneinfo import ZoneInfo as _ZI

from sqlalchemy import text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from tts_erp_v2.db.base import get_engine

from .domain import (
    DEFAULT_TIMEZONE,
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

# ad_records INSERT（派生 - body only,无 page 列,无 expected_page_count 列）
# request_body/response_data 来自 dump.request.body / dump.response.body
# ad_records 只存 body 部分,不存 header/status(endpoint/method 单独存)
SQL_INSERT_RECORD_DERIVED = """
INSERT INTO analytics.ad_records (
    idempotency_key, source_record_id,
    seller_id, advertiser_id, storage_key, campaign_id,
    day, shop_name, endpoint, method, request_body, response_data,
    source, captured_at, schema_version, protocol_version, request_id
) VALUES (
    :idempotency_key, :source_record_id,
    :seller_id, :advertiser_id, :storage_key, :campaign_id,
    :day, :shop_name, :endpoint, :method,
    CAST(:request_body AS JSONB), CAST(:response_data AS JSONB),
    :source, :captured_at, :schema_version, :protocol_version, :request_id
)
ON CONFLICT (seller_id, advertiser_id, storage_key, campaign_id, day) DO UPDATE
    SET response_data   = EXCLUDED.response_data,
        request_body    = EXCLUDED.request_body,
        endpoint        = EXCLUDED.endpoint,
        method          = EXCLUDED.method,
        idempotency_key = EXCLUDED.idempotency_key
"""

# ad_daily_completeness UPSERT（dump 架构下 captured_at=now() 是唯一语义）
# is_complete / expected_page_count 概念被 hasData existence 替代
SQL_UPSERT_DAILY_COMPLETENESS_CAPTURED = """
INSERT INTO analytics.ad_daily_completeness (
    seller_id, advertiser_id, storage_key, campaign_id, day, captured_at
) VALUES (
    :seller_id, :advertiser_id, :storage_key, :campaign_id, :day, now()
)
ON CONFLICT (seller_id, advertiser_id, storage_key, campaign_id, day) DO UPDATE
    SET captured_at = now()
"""

# has-data 检查（GET /cursor has-data 模式）
SQL_HAS_DATA = """
SELECT 1 FROM analytics.ad_raw
WHERE seller_id = :seller_id AND advertiser_id = :advertiser_id
  AND endpoint = :endpoint AND day = :day
  AND (CAST(:campaign_id AS text) IS NULL OR campaign_id = :campaign_id)
LIMIT 1
"""

# 店铺时区 last-write-wins（dump 事务内尾部;保持 v2 行为）
SQL_UPSERT_TIMEZONE = """
INSERT INTO analytics.ad_shop_timezones (seller_id, advertiser_id, timezone)
VALUES (:seller_id, :advertiser_id, :timezone)
ON CONFLICT (seller_id) DO UPDATE
    SET advertiser_id = EXCLUDED.advertiser_id,
        updated_at = now()
"""

SQL_GET_TIMEZONE = (
    "SELECT timezone FROM analytics.ad_shop_timezones WHERE seller_id = :seller_id"
)

SQL_SEED_TIMEZONE = """
INSERT INTO analytics.ad_shop_timezones (seller_id, advertiser_id, timezone)
VALUES (:seller_id, '', :timezone)
ON CONFLICT (seller_id) DO NOTHING
"""

SQL_REPAIR_TIMEZONE = (
    "UPDATE analytics.ad_shop_timezones "
    "SET timezone = :timezone WHERE seller_id = :seller_id"
)

SQL_INSERT_AUDIT = """
INSERT INTO analytics.ad_audit_log (
    request_id, endpoint, method, path, status,
    key_prefix, records_in, records_ok, records_rej,
    error_code, error_message
)
VALUES (
    :request_id, :endpoint, :method, :path, :status,
    :key_prefix, :records_in, :records_ok, :records_rej,
    :error_code, :error_message
)
"""

# retention（analytics.retention job;records 90 天、audit 30 天;
# shop_timezones 永久保留。dump 架构下 cursors 已删,不再需要 cursor 清理）
SQL_PURGE_RECORDS = (
    "DELETE FROM analytics.ad_records "
    "WHERE received_at < now() - make_interval(days => :days)"
)
SQL_PURGE_AUDIT = (
    "DELETE FROM analytics.ad_audit_log "
    "WHERE created_at < now() - make_interval(days => :days)"
)


# ─── dump upsert（1 事务写 3 张表）────────────────────────────────────


def upsert_dump(
    sess: Session,
    dump: DumpPayload,
    request_id: str | None,
) -> DumpResult:
    """Insert one dump in 3 tables, single transaction.

    1. INSERT ad_raw (ON CONFLICT 5 元组 DO UPDATE,RETURNING xmax=0 判 inserted/duplicate)
    2. INSERT ad_records (ON CONFLICT 5 元组 DO UPDATE,派生 body only)
    3. UPSERT ad_daily_completeness (captured_at=now())
    4. sess.commit()

    dump 协议下 page 隐式 = 1,幂等键 6 字段 SHA-256（seller, advertiser,
    storage_key, campaign_id, day, page=1）。与 v2 batches 协议字节兼容。
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

    # 3. INSERT ad_records (派生 - body only,无 page 列,无 expected_page_count 列)
    # request_body 来自 dump.request.body,response_data 来自 dump.response.body
    # pi-lens-ignore: python-sql-injection
    sess.execute(
        text(SQL_INSERT_RECORD_DERIVED),
        {
            "idempotency_key": idem_key,
            "source_record_id": None,  # dump 协议无 sourceRecordId 概念
            "seller_id": dump.seller_id,
            "advertiser_id": dump.advertiser_id,
            "storage_key": dump.storage_key.value,
            "campaign_id": dump.campaign_id,
            "day": dump.day,
            "shop_name": None,  # dump 协议无 shop_name
            "endpoint": dump.endpoint,
            "method": dump.method,
            "request_body": json.dumps(
                dump.request.get("body", {}), ensure_ascii=False
            ),
            "response_data": json.dumps(
                dump.response.get("body", {}), ensure_ascii=False
            ),
            "source": dump.source,
            "captured_at": dump.captured_at,
            "schema_version": dump.schema_version,
            "protocol_version": dump.protocol_version,
            "request_id": request_id,
        },
    )

    # 4. UPSERT ad_daily_completeness (captured_at=now() 唯一语义)
    # pi-lens-ignore: python-sql-injection
    sess.execute(
        text(SQL_UPSERT_DAILY_COMPLETENESS_CAPTURED),
        {
            "seller_id": dump.seller_id,
            "advertiser_id": dump.advertiser_id,
            "storage_key": dump.storage_key.value,
            "campaign_id": dump.campaign_id,
            "day": dump.day,
        },
    )

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


# ─── 店铺时区（保持 v2 行为）───────────────────────────────────────


def fetch_timezone(sess: Session, seller_id: str) -> str:
    """返回该 seller 的权威 IANA 时区。

    缺行时以 Asia/Shanghai 补种（懒写会就地 commit）。若库里的值不是合法
    IANA 标识（脏行、手改、或未来部署改了列默认值），修复该行并回落
    Asia/Shanghai,而不是把垃圾串传给 ZoneInfo 构造器（会抛）。
    """
    default_tz = DEFAULT_TIMEZONE
    # pi-lens-ignore: python-sql-injection
    tz_str = sess.execute(text(SQL_GET_TIMEZONE), {"seller_id": seller_id}).scalar()
    if tz_str is None:
        # pi-lens-ignore: python-sql-injection
        sess.execute(
            text(SQL_SEED_TIMEZONE),
            {"seller_id": seller_id, "timezone": default_tz},
        )
        sess.commit()
        tz_str = default_tz
    # 校验：脏 TZ 会让 _today_in_tz 崩（ZoneInfo raises）。修复 + 回落。
    try:
        _ZI(tz_str)
    except Exception:
        # pi-lens-ignore: python-sql-injection
        sess.execute(
            text(SQL_REPAIR_TIMEZONE),
            {"seller_id": seller_id, "timezone": default_tz},
        )
        sess.commit()
        return default_tz
    return tz_str


# ─── 审计（独立连接,best-effort）─────────────────────────────────────


def write_audit(
    *,
    request_id: str | None,
    endpoint: str,
    method: str,
    path: str,
    status: int,
    key_prefix: str | None,
    records_in: int | None = None,
    records_ok: int | None = None,
    records_rej: int | None = None,
    error_code: str | None = None,
    error_message: str | None = None,
    engine: Engine | None = None,
) -> None:
    """追加一行审计。best-effort —— 失败只打 stderr,不外抛,
    审计问题永远不打断请求。

    dump architecture 改造后 endpoint 字段值:dumps / cursor。
    """
    try:
        eng = engine or get_engine()
        with eng.begin() as conn:
            # pi-lens-ignore: python-sql-injection
            conn.execute(
                text(SQL_INSERT_AUDIT),
                {
                    "request_id": request_id,
                    "endpoint": endpoint,
                    "method": method,
                    "path": path,
                    "status": status,
                    "key_prefix": key_prefix,
                    "records_in": records_in,
                    "records_ok": records_ok,
                    "records_rej": records_rej,
                    "error_code": error_code,
                    "error_message": error_message,
                },
            )
    except Exception as exc:  # best-effort：审计失败不外抛
        sys.stderr.write(f"[analytics-sync] audit write failed: {exc!r}\n")


# ─── retention（analytics.retention job 用）───────────────────────────


def purge_expired(
    sess: Session,
    *,
    records_days: int = 90,
    audit_days: int = 30,
) -> dict[str, int]:
    """删除过期数据并 commit。返回各表删除行数。

    records 按 received_at 保留 90 天,audit 按 created_at 保留 30 天;
    shop_timezones 永久保留（dump 架构下 cursors 已删,不再需要 cursor 清理）。
    """
    # pi-lens-ignore: python-sql-injection
    records_deleted = sess.execute(
        text(SQL_PURGE_RECORDS), {"days": records_days}
    ).rowcount
    # pi-lens-ignore: python-sql-injection
    audit_deleted = sess.execute(text(SQL_PURGE_AUDIT), {"days": audit_days}).rowcount
    return {"records_deleted": records_deleted, "audit_deleted": audit_deleted}


# ─── 日期工具（加性日期运算不需要 zoneinfo）───────────────────────────
# 协议契约是「店铺时区的今天」——调用方算好 today_in_shop_tz 传进来。
# 这些工具只需要跨月/跨年正确地加减天数。
# (不再用:dump 架构下 has-data 不需要 bootstrap_day)


def _add_days(d: date, n: int) -> date:
    """d 加 n 天（可正可负）。用 datetime 序数运算正确处理月/年 rollover。"""
    return date.fromordinal(d.toordinal() + n)


def _subtract_days(d: date, n: int) -> date:
    return _add_days(d, -n)
