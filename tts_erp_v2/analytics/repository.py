"""Analytics 存储层（v4 daily-sync-with-coverage, raw SQL）。

SQL 以模块级 text() 常量书写。表全部 schema 限定为 analytics.ad_*。
"""

from __future__ import annotations

import json
from datetime import date, datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

# ─── daily-sync-with-coverage 结构化写入 SQL ─────────────────────────
# tech-doc/analytics/daily-sync-with-coverage.md §5.1 / §5.3

SQL_COVERAGE_DAILY = """
SELECT campaign_id, array_agg(DISTINCT day ORDER BY day) AS days
FROM analytics.ad_daily
WHERE seller_id = :seller_id AND advertiser_id = :advertiser_id
  AND endpoint = :endpoint
  AND day BETWEEN :start_day AND :end_day
GROUP BY campaign_id
ORDER BY campaign_id
LIMIT :page_size OFFSET :offset
"""

SQL_COVERAGE_DAILY_COUNT = """
SELECT count(DISTINCT campaign_id) AS total
FROM analytics.ad_daily
WHERE seller_id = :seller_id AND advertiser_id = :advertiser_id
  AND endpoint = :endpoint
  AND day BETWEEN :start_day AND :end_day
"""

SQL_COVERAGE_MONTHLY = """
SELECT campaign_id, array_agg(DISTINCT year_month ORDER BY year_month) AS months
FROM analytics.ad_monthly
WHERE seller_id = :seller_id AND advertiser_id = :advertiser_id
  AND endpoint = :endpoint
  AND year_month BETWEEN :start_month AND :end_month
GROUP BY campaign_id
ORDER BY campaign_id
LIMIT :page_size OFFSET :offset
"""

SQL_COVERAGE_MONTHLY_COUNT = """
SELECT count(DISTINCT campaign_id) AS total
FROM analytics.ad_monthly
WHERE seller_id = :seller_id AND advertiser_id = :advertiser_id
  AND endpoint = :endpoint
  AND year_month BETWEEN :start_month AND :end_month
"""

SQL_UPSERT_DAILY_ROW = """
INSERT INTO analytics.ad_daily (
    seller_id, advertiser_id, campaign_id, product_id, endpoint, day,
    mixed_real_cost, onsite_roi2_shopping_sku, onsite_roi2_shopping_value,
    onsite_mixed_real_roi2_shopping, metrics_extra, created_at
) VALUES (
    :seller_id, :advertiser_id, :campaign_id, :product_id, :endpoint, :day,
    :mixed_real_cost, :onsite_roi2_shopping_sku, :onsite_roi2_shopping_value,
    :onsite_mixed_real_roi2_shopping, CAST(:metrics_extra AS JSONB), :created_at
)
ON CONFLICT ON CONSTRAINT uq_ad_daily DO NOTHING
RETURNING id
"""

SQL_UPSERT_TODAY_ROW = """
INSERT INTO analytics.ad_today (
    seller_id, advertiser_id, campaign_id, product_id, endpoint, day,
    mixed_real_cost, onsite_roi2_shopping_sku, onsite_roi2_shopping_value,
    onsite_mixed_real_roi2_shopping, metrics_extra, created_at
) VALUES (
    :seller_id, :advertiser_id, :campaign_id, :product_id, :endpoint, :day,
    :mixed_real_cost, :onsite_roi2_shopping_sku, :onsite_roi2_shopping_value,
    :onsite_mixed_real_roi2_shopping, CAST(:metrics_extra AS JSONB), :created_at
)
ON CONFLICT ON CONSTRAINT uq_ad_today DO UPDATE SET
    mixed_real_cost = EXCLUDED.mixed_real_cost,
    onsite_roi2_shopping_sku = EXCLUDED.onsite_roi2_shopping_sku,
    onsite_roi2_shopping_value = EXCLUDED.onsite_roi2_shopping_value,
    onsite_mixed_real_roi2_shopping = EXCLUDED.onsite_mixed_real_roi2_shopping,
    metrics_extra = EXCLUDED.metrics_extra,
    updated_at = now()
"""

SQL_UPSERT_MONTHLY_ROW = """
INSERT INTO analytics.ad_monthly (
    seller_id, advertiser_id, campaign_id, product_id, endpoint, year_month,
    mixed_real_cost, onsite_roi2_shopping_sku, onsite_roi2_shopping_value,
    onsite_mixed_real_roi2_shopping, metrics_extra, created_at
) VALUES (
    :seller_id, :advertiser_id, :campaign_id, :product_id, :endpoint, :year_month,
    :mixed_real_cost, :onsite_roi2_shopping_sku, :onsite_roi2_shopping_value,
    :onsite_mixed_real_roi2_shopping, CAST(:metrics_extra AS JSONB), :created_at
)
ON CONFLICT ON CONSTRAINT uq_ad_monthly DO NOTHING
RETURNING id
"""

SQL_INSERT_RAW_LOG = """
INSERT INTO analytics.ad_raw_log (
    seller_id, advertiser_id, endpoint, campaign_id, product_id,
    kind, day, year_month,
    request_url, request_method, request_body, response_status, response_body,
    created_at, request_id, source
) VALUES (
    :seller_id, :advertiser_id, :endpoint, :campaign_id, :product_id,
    :kind, :day, :year_month,
    :request_url, :request_method, CAST(:request_body AS JSONB), :response_status, CAST(:response_body AS JSONB),
    :created_at, :request_id, :source
)
"""

SQL_INSERT_PLUGIN_LOG = """
INSERT INTO analytics.plugin_logs (
    seller_id, advertiser_id, plugin_version, level, message, context, occurred_at
) VALUES (
    :seller_id, :advertiser_id, :plugin_version, :level, :message,
    CAST(:context AS JSONB), :occurred_at
)
"""

# ─── 核心指标字段（写入时预解析到结构化列）──────────────────────────
_CORE_METRIC_KEYS: frozenset[str] = frozenset(
    {
        "product_id",
        "mixed_real_cost",
        "onsite_roi2_shopping_sku",
        "onsite_roi2_shopping_value",
        "onsite_mixed_real_roi2_shopping",
    }
)


# ─── Coverage 查询 ────────────────────────────────────────────────────


def get_coverage_daily(
    sess: Session,
    *,
    seller_id: str,
    advertiser_id: str,
    endpoint: str,
    start_day: date,
    end_day: date,
    page: int = 1,
    page_size: int = 500,
) -> tuple[dict[str, list[str]], int]:
    """返回 ({campaign_id: [day1, day2, ...]}, totalCampaigns) 的元组。

    2026-09-11 加分页（隐患 #3）：page/page_size 取一段 campaign；totalCampaigns 是
    整个查询窗口内的总 campaign 数（不随 page 变）。sort 用 ORDER BY campaign_id
    保证分页结果稳定。
    """
    offset = (page - 1) * page_size
    # pi-lens-ignore: python-sql-injection — LIMIT/OFFSET 走 :page_size/:offset 参数化
    rows = sess.execute(
        text(SQL_COVERAGE_DAILY),
        {
            "seller_id": seller_id,
            "advertiser_id": advertiser_id,
            "endpoint": endpoint,
            "start_day": start_day,
            "end_day": end_day,
            "page_size": page_size,
            "offset": offset,
        },
    ).all()
    # pi-lens-ignore: python-sql-injection — COUNT() 参数化
    total_row = sess.execute(
        text(SQL_COVERAGE_DAILY_COUNT),
        {
            "seller_id": seller_id,
            "advertiser_id": advertiser_id,
            "endpoint": endpoint,
            "start_day": start_day,
            "end_day": end_day,
        },
    ).first()
    # 防御型: COUNT() 总是 integer；但万一返回 None（不应发生），fallback 到 0
    try:
        total = int(total_row[0]) if total_row is not None else 0
    except (TypeError, ValueError):
        total = 0
    return {row[0]: [d.isoformat() for d in row[1]] for row in rows}, total


def get_coverage_monthly(
    sess: Session,
    *,
    seller_id: str,
    advertiser_id: str,
    endpoint: str,
    start_month: str,
    end_month: str,
    page: int = 1,
    page_size: int = 500,
) -> tuple[dict[str, list[str]], int]:
    """返回 ({campaign_id: ['2026-01', ...]}, totalCampaigns) 的元组。"""
    offset = (page - 1) * page_size
    # pi-lens-ignore: python-sql-injection
    rows = sess.execute(
        text(SQL_COVERAGE_MONTHLY),
        {
            "seller_id": seller_id,
            "advertiser_id": advertiser_id,
            "endpoint": endpoint,
            "start_month": start_month,
            "end_month": end_month,
            "page_size": page_size,
            "offset": offset,
        },
    ).all()
    # pi-lens-ignore: python-sql-injection — COUNT() 参数化
    total_row = sess.execute(
        text(SQL_COVERAGE_MONTHLY_COUNT),
        {
            "seller_id": seller_id,
            "advertiser_id": advertiser_id,
            "endpoint": endpoint,
            "start_month": start_month,
            "end_month": end_month,
        },
    ).first()
    try:
        total = int(total_row[0]) if total_row is not None else 0
    except (TypeError, ValueError):
        total = 0
    return {row[0]: list(row[1]) for row in rows}, total


# ─── 结构化写入函数 ──────────────────────────────────────────────────
# tech-doc/analytics/daily-sync-with-coverage.md §5.3


def upsert_daily_rows(
    sess: Session,
    *,
    seller_id: str,
    advertiser_id: str,
    endpoint: str,
    campaign_id: str,
    day: date,
    rows: list[dict[str, Any]],
    request_url: str,
    request_body: dict[str, Any] | None,
    response_status: int | None,
    response_body: dict[str, Any] | None,
    created_at: datetime,
    request_id: str | None,
    source: str | None,
) -> int:
    """解析 rows → INSERT ad_daily + ad_raw_log，单事务。返回 inserted 计数。"""
    first_product_id = rows[0]["product_id"] if rows else None
    inserted = 0

    for row in rows:
        product_id = row["product_id"]
        mixed_real_cost = row.get("mixed_real_cost")
        onsite_roi2_shopping_sku = row.get("onsite_roi2_shopping_sku")
        onsite_roi2_shopping_value = row.get("onsite_roi2_shopping_value")
        onsite_mixed_real_roi2_shopping = row.get("onsite_mixed_real_roi2_shopping")

        metrics_extra = {k: v for k, v in row.items() if k not in _CORE_METRIC_KEYS}

        # pi-lens-ignore: python-sql-injection
        result = sess.execute(
            text(SQL_UPSERT_DAILY_ROW),
            {
                "seller_id": seller_id,
                "advertiser_id": advertiser_id,
                "campaign_id": campaign_id,
                "product_id": product_id,
                "endpoint": endpoint,
                "day": day,
                "mixed_real_cost": mixed_real_cost,
                "onsite_roi2_shopping_sku": onsite_roi2_shopping_sku,
                "onsite_roi2_shopping_value": onsite_roi2_shopping_value,
                "onsite_mixed_real_roi2_shopping": onsite_mixed_real_roi2_shopping,
                "metrics_extra": json.dumps(metrics_extra, ensure_ascii=False),
                "created_at": created_at,
            },
        )
        if result.rowcount > 0:
            inserted += 1

    # INSERT ad_raw_log
    # pi-lens-ignore: python-sql-injection
    sess.execute(
        text(SQL_INSERT_RAW_LOG),
        {
            "seller_id": seller_id,
            "advertiser_id": advertiser_id,
            "endpoint": endpoint,
            "campaign_id": campaign_id,
            "product_id": first_product_id,
            "kind": "daily",
            "day": day,
            "year_month": None,
            "request_url": request_url,
            "request_method": "POST",
            "request_body": json.dumps(request_body, ensure_ascii=False),
            "response_status": response_status,
            "response_body": json.dumps(response_body, ensure_ascii=False),
            "created_at": created_at,
            "request_id": request_id,
            "source": source,
        },
    )

    sess.commit()
    return inserted


def upsert_today_rows(
    sess: Session,
    *,
    seller_id: str,
    advertiser_id: str,
    endpoint: str,
    campaign_id: str,
    day: date,
    rows: list[dict[str, Any]],
    request_url: str,
    request_body: dict[str, Any] | None,
    response_status: int | None,
    response_body: dict[str, Any] | None,
    created_at: datetime,
    request_id: str | None,
    source: str | None,
) -> int:
    """解析 rows → INSERT ad_today（ON CONFLICT DO UPDATE）+ ad_raw_log，单事务。"""
    first_product_id = rows[0]["product_id"] if rows else None
    inserted = 0

    for row in rows:
        product_id = row["product_id"]
        mixed_real_cost = row.get("mixed_real_cost")
        onsite_roi2_shopping_sku = row.get("onsite_roi2_shopping_sku")
        onsite_roi2_shopping_value = row.get("onsite_roi2_shopping_value")
        onsite_mixed_real_roi2_shopping = row.get("onsite_mixed_real_roi2_shopping")

        metrics_extra = {k: v for k, v in row.items() if k not in _CORE_METRIC_KEYS}

        # pi-lens-ignore: python-sql-injection
        sess.execute(
            text(SQL_UPSERT_TODAY_ROW),
            {
                "seller_id": seller_id,
                "advertiser_id": advertiser_id,
                "campaign_id": campaign_id,
                "product_id": product_id,
                "endpoint": endpoint,
                "day": day,
                "mixed_real_cost": mixed_real_cost,
                "onsite_roi2_shopping_sku": onsite_roi2_shopping_sku,
                "onsite_roi2_shopping_value": onsite_roi2_shopping_value,
                "onsite_mixed_real_roi2_shopping": onsite_mixed_real_roi2_shopping,
                "metrics_extra": json.dumps(metrics_extra, ensure_ascii=False),
                "created_at": created_at,
            },
        )
        inserted += 1

    # INSERT ad_raw_log
    # pi-lens-ignore: python-sql-injection
    sess.execute(
        text(SQL_INSERT_RAW_LOG),
        {
            "seller_id": seller_id,
            "advertiser_id": advertiser_id,
            "endpoint": endpoint,
            "campaign_id": campaign_id,
            "product_id": first_product_id,
            "kind": "today",
            "day": day,
            "year_month": None,
            "request_url": request_url,
            "request_method": "POST",
            "request_body": json.dumps(request_body, ensure_ascii=False),
            "response_status": response_status,
            "response_body": json.dumps(response_body, ensure_ascii=False),
            "created_at": created_at,
            "request_id": request_id,
            "source": source,
        },
    )

    sess.commit()
    return inserted


def upsert_monthly_rows(
    sess: Session,
    *,
    seller_id: str,
    advertiser_id: str,
    endpoint: str,
    campaign_id: str,
    year_month: str,
    rows: list[dict[str, Any]],
    request_url: str,
    request_body: dict[str, Any] | None,
    response_status: int | None,
    response_body: dict[str, Any] | None,
    created_at: datetime,
    request_id: str | None,
    source: str | None,
) -> int:
    """解析 rows → INSERT ad_monthly + ad_raw_log，单事务。返回 inserted 计数。"""
    first_product_id = rows[0]["product_id"] if rows else None
    inserted = 0

    for row in rows:
        product_id = row["product_id"]
        mixed_real_cost = row.get("mixed_real_cost")
        onsite_roi2_shopping_sku = row.get("onsite_roi2_shopping_sku")
        onsite_roi2_shopping_value = row.get("onsite_roi2_shopping_value")
        onsite_mixed_real_roi2_shopping = row.get("onsite_mixed_real_roi2_shopping")

        metrics_extra = {k: v for k, v in row.items() if k not in _CORE_METRIC_KEYS}

        # pi-lens-ignore: python-sql-injection
        result = sess.execute(
            text(SQL_UPSERT_MONTHLY_ROW),
            {
                "seller_id": seller_id,
                "advertiser_id": advertiser_id,
                "campaign_id": campaign_id,
                "product_id": product_id,
                "endpoint": endpoint,
                "year_month": year_month,
                "mixed_real_cost": mixed_real_cost,
                "onsite_roi2_shopping_sku": onsite_roi2_shopping_sku,
                "onsite_roi2_shopping_value": onsite_roi2_shopping_value,
                "onsite_mixed_real_roi2_shopping": onsite_mixed_real_roi2_shopping,
                "metrics_extra": json.dumps(metrics_extra, ensure_ascii=False),
                "created_at": created_at,
            },
        )
        if result.rowcount > 0:
            inserted += 1

    # INSERT ad_raw_log
    # pi-lens-ignore: python-sql-injection
    sess.execute(
        text(SQL_INSERT_RAW_LOG),
        {
            "seller_id": seller_id,
            "advertiser_id": advertiser_id,
            "endpoint": endpoint,
            "campaign_id": campaign_id,
            "product_id": first_product_id,
            "kind": "monthly",
            "day": None,
            "year_month": year_month,
            "request_url": request_url,
            "request_method": "POST",
            "request_body": json.dumps(request_body, ensure_ascii=False),
            "response_status": response_status,
            "response_body": json.dumps(response_body, ensure_ascii=False),
            "created_at": created_at,
            "request_id": request_id,
            "source": source,
        },
    )

    sess.commit()
    return inserted


# ─── Solidify (ad_today → ad_daily) ─────────────────────────────────


def solidify_yesterday_scope_pairs(
    sess: Session,
    yesterday: date,
) -> list[tuple[str, str]]:
    """Return distinct (seller_id, advertiser_id) pairs in ad_today for *yesterday*."""
    # pi-lens-ignore: python-sql-injection
    rows = sess.execute(
        text("""
            SELECT DISTINCT seller_id, advertiser_id
            FROM analytics.ad_today
            WHERE day = :yesterday
            ORDER BY seller_id, advertiser_id
        """),
        {"yesterday": yesterday},
    ).all()
    return [(row[0], row[1]) for row in rows]


def solidify_yesterday(
    sess: Session,
    *,
    seller_id: str,
    advertiser_id: str,
    yesterday: date,
) -> None:
    """ad_today 昨天数据 → ad_daily（固化），然后清空 ad_today。"""
    # pi-lens-ignore: python-sql-injection
    sess.execute(
        text("""
            INSERT INTO analytics.ad_daily (
                seller_id, advertiser_id, campaign_id, product_id, endpoint, day,
                mixed_real_cost, onsite_roi2_shopping_sku, onsite_roi2_shopping_value,
                onsite_mixed_real_roi2_shopping, metrics_extra, created_at
            )
            SELECT seller_id, advertiser_id, campaign_id, product_id, endpoint, day,
                   mixed_real_cost, onsite_roi2_shopping_sku, onsite_roi2_shopping_value,
                   onsite_mixed_real_roi2_shopping, metrics_extra, created_at
            FROM analytics.ad_today
            WHERE seller_id = :seller_id AND advertiser_id = :advertiser_id
              AND day = :yesterday
            ON CONFLICT ON CONSTRAINT uq_ad_daily DO NOTHING
        """),
        {
            "seller_id": seller_id,
            "advertiser_id": advertiser_id,
            "yesterday": yesterday,
        },
    )

    # pi-lens-ignore: python-sql-injection
    sess.execute(
        text("""
            DELETE FROM analytics.ad_today
            WHERE seller_id = :seller_id AND advertiser_id = :advertiser_id
              AND day = :yesterday
        """),
        {
            "seller_id": seller_id,
            "advertiser_id": advertiser_id,
            "yesterday": yesterday,
        },
    )

    sess.commit()


# ─── Plugin logs ─────────────────────────────────────────────────────


def insert_plugin_logs(sess: Session, *, logs: list[dict[str, Any]]) -> int:
    """批量写入插件日志，返回 inserted 数。"""
    inserted = 0
    for log in logs:
        # pi-lens-ignore: python-sql-injection
        sess.execute(
            text(SQL_INSERT_PLUGIN_LOG),
            {
                "seller_id": log["seller_id"],
                "advertiser_id": log["advertiser_id"],
                "plugin_version": log.get("plugin_version", ""),
                "level": log.get("level", "info"),
                "message": log["message"],
                "context": json.dumps(log.get("context", {}), ensure_ascii=False),
                "occurred_at": log["occurred_at"],
            },
        )
        inserted += 1
    sess.commit()
    return inserted


__all__ = [
    "SQL_COVERAGE_DAILY",
    "SQL_COVERAGE_MONTHLY",
    "SQL_INSERT_PLUGIN_LOG",
    "SQL_INSERT_RAW_LOG",
    "SQL_UPSERT_DAILY_ROW",
    "SQL_UPSERT_MONTHLY_ROW",
    "SQL_UPSERT_TODAY_ROW",
    "get_coverage_daily",
    "get_coverage_monthly",
    "insert_plugin_logs",
    "solidify_yesterday",
    "solidify_yesterday_scope_pairs",
    "upsert_daily_rows",
    "upsert_monthly_rows",
    "upsert_today_rows",
]
