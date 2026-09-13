"""Analytics 存储层（v4 daily-sync-with-coverage, raw SQL）。

SQL 以模块级 text() 常量书写。表全部 schema 限定为 analytics.ad_*。
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import bindparam, text
from sqlalchemy.orm import Session

# ─── daily-sync-with-coverage 结构化写入 SQL ─────────────────────────
# tech-doc/analytics/daily-sync-with-coverage.md §5.1 / §5.3

SQL_COVERAGE_DAILY = """
SELECT campaign_id, array_agg(DISTINCT day ORDER BY day) AS days
FROM plugin.ad_daily
WHERE seller_id = :seller_id AND advertiser_id = :advertiser_id
  AND endpoint = :endpoint
  AND day BETWEEN :start_day AND :end_day
GROUP BY campaign_id
ORDER BY campaign_id
LIMIT :page_size OFFSET :offset
"""

SQL_COVERAGE_DAILY_COUNT = """
SELECT count(DISTINCT campaign_id) AS total
FROM plugin.ad_daily
WHERE seller_id = :seller_id AND advertiser_id = :advertiser_id
  AND endpoint = :endpoint
  AND day BETWEEN :start_day AND :end_day
"""

SQL_COVERAGE_DAILY_RAW = """
SELECT campaign_id, array_agg(DISTINCT day ORDER BY day) AS days
FROM plugin.ad_raw_log
WHERE seller_id = :seller_id AND advertiser_id = :advertiser_id
  AND endpoint = :endpoint AND kind = 'daily'
  AND day BETWEEN :start_day AND :end_day
GROUP BY campaign_id
ORDER BY campaign_id
LIMIT :page_size OFFSET :offset
"""

SQL_COVERAGE_DAILY_RAW_COUNT = """
SELECT count(DISTINCT campaign_id) AS total
FROM plugin.ad_raw_log
WHERE seller_id = :seller_id AND advertiser_id = :advertiser_id
  AND endpoint = :endpoint AND kind = 'daily'
  AND day BETWEEN :start_day AND :end_day
"""

SQL_COVERAGE_MONTHLY = """
SELECT campaign_id, array_agg(DISTINCT year_month ORDER BY year_month) AS months
FROM plugin.ad_monthly
WHERE seller_id = :seller_id AND advertiser_id = :advertiser_id
  AND endpoint = :endpoint
  AND year_month BETWEEN :start_month AND :end_month
GROUP BY campaign_id
ORDER BY campaign_id
LIMIT :page_size OFFSET :offset
"""

SQL_COVERAGE_MONTHLY_COUNT = """
SELECT count(DISTINCT campaign_id) AS total
FROM plugin.ad_monthly
WHERE seller_id = :seller_id AND advertiser_id = :advertiser_id
  AND endpoint = :endpoint
  AND year_month BETWEEN :start_month AND :end_month
"""

SQL_COVERAGE_MONTHLY_RAW = """
SELECT campaign_id, array_agg(DISTINCT year_month ORDER BY year_month) AS months
FROM plugin.ad_raw_log
WHERE seller_id = :seller_id AND advertiser_id = :advertiser_id
  AND endpoint = :endpoint AND kind = 'monthly'
  AND year_month BETWEEN :start_month AND :end_month
GROUP BY campaign_id
ORDER BY campaign_id
LIMIT :page_size OFFSET :offset
"""

SQL_COVERAGE_MONTHLY_RAW_COUNT = """
SELECT count(DISTINCT campaign_id) AS total
FROM plugin.ad_raw_log
WHERE seller_id = :seller_id AND advertiser_id = :advertiser_id
  AND endpoint = :endpoint AND kind = 'monthly'
  AND year_month BETWEEN :start_month AND :end_month
"""

SQL_UPSERT_DAILY_ROW = """
INSERT INTO plugin.ad_daily (
    seller_id, advertiser_id, campaign_id, product_id, endpoint, day,
    mixed_real_cost, onsite_roi2_shopping_sku, onsite_roi2_shopping_value,
    onsite_mixed_real_roi2_shopping, metrics_extra, created_at
) VALUES (
    :seller_id, :advertiser_id, :campaign_id, :product_id, :endpoint, :day,
    :mixed_real_cost, :onsite_roi2_shopping_sku, :onsite_roi2_shopping_value,
    :onsite_mixed_real_roi2_shopping, CAST(:metrics_extra AS JSONB), :created_at
)
ON CONFLICT ON CONSTRAINT uq_ad_daily DO UPDATE SET
    mixed_real_cost = EXCLUDED.mixed_real_cost,
    onsite_roi2_shopping_sku = EXCLUDED.onsite_roi2_shopping_sku,
    onsite_roi2_shopping_value = EXCLUDED.onsite_roi2_shopping_value,
    onsite_mixed_real_roi2_shopping = EXCLUDED.onsite_mixed_real_roi2_shopping,
    metrics_extra = EXCLUDED.metrics_extra,
    updated_at = now()
RETURNING id
"""

SQL_UPSERT_TODAY_ROW = """
INSERT INTO plugin.ad_today (
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
INSERT INTO plugin.ad_monthly (
    seller_id, advertiser_id, campaign_id, product_id, endpoint, year_month,
    mixed_real_cost, onsite_roi2_shopping_sku, onsite_roi2_shopping_value,
    onsite_mixed_real_roi2_shopping, metrics_extra, created_at
) VALUES (
    :seller_id, :advertiser_id, :campaign_id, :product_id, :endpoint, :year_month,
    :mixed_real_cost, :onsite_roi2_shopping_sku, :onsite_roi2_shopping_value,
    :onsite_mixed_real_roi2_shopping, CAST(:metrics_extra AS JSONB), :created_at
)
ON CONFLICT ON CONSTRAINT uq_ad_monthly DO UPDATE SET
    mixed_real_cost = EXCLUDED.mixed_real_cost,
    onsite_roi2_shopping_sku = EXCLUDED.onsite_roi2_shopping_sku,
    onsite_roi2_shopping_value = EXCLUDED.onsite_roi2_shopping_value,
    onsite_mixed_real_roi2_shopping = EXCLUDED.onsite_mixed_real_roi2_shopping,
    metrics_extra = EXCLUDED.metrics_extra,
    updated_at = now()
RETURNING id
"""

SQL_INSERT_RAW_LOG = """
INSERT INTO plugin.ad_raw_log (
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
INSERT INTO plugin.plugin_logs (
    seller_id, advertiser_id, plugin_version, plugin_name, level, message, context, occurred_at
) VALUES (
    :seller_id, :advertiser_id, :plugin_version, :plugin_name, :level, :message,
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

# ─── Endpoint 分层白名单（2026-09-11 fix/analytics-v4-campaign-rows）──────────────
# v4 dump 协议假设每行都有 product_id（ad_daily/ad_today/ad_monthly 的主键之一）。
# 但部分 TikTok endpoint 是 campaign-level 粒度，rows 永远是 campaign 变更事件
# （如 change_id / change_type），压根没有 product_id —— 硬走结构化表会 KeyError。
#
# 解决：在 upsert_*_rows 入口判断 endpoint：白名单内 = product-level 写结构化表，
# 不在白名单 = campaign-level 只写 ad_raw_log（rows 原样存进 response.body 备查）。
# 新增 endpoint 默认走 product-level，保守暴露 KeyError 让开发者知道要补白名单
# （AGENTS.md §6 「fail loud」）。
#
# 配套：chrome-plugins/ads-data-sync/entrypoints/background.ts 的
# executeSingle*DumpV4 在 campaign-level endpoint 时 dump.rows=[]，
# 这样 wires 上 rows 就是空的，rows 完整性靠 response.body 存档保证。
_PRODUCT_LEVEL_ENDPOINTS: frozenset[str] = frozenset(
    {
        # product-level：每个 row 是一个商品的指标
        "/oec_ads/shopping/v1/oec/stat/post_product_list",
        # session-level：按 spu_id_list 过滤的会话级数据，每个 row 仍属于某个 spu
        # 当前 plugin 已禁用（2026-09-09：post_session_list 无实际用途），
        # 保留白名单为未来启用预留。
        "/oec_ads/shopping/v1/oec/stat/post_session_list",
    }
)


def is_product_level_endpoint(endpoint: str) -> bool:
    """返回 endpoint 是否属于 product-level（有 product_id，能写结构化表）。"""
    return endpoint in _PRODUCT_LEVEL_ENDPOINTS


def _archive_raw_log_only(
    sess: Session,
    *,
    seller_id: str,
    advertiser_id: str,
    endpoint: str,
    campaign_id: str,
    rows: list[dict[str, Any]],
    request_url: str,
    request_body: dict[str, Any] | None,
    response_status: int | None,
    response_body: dict[str, Any] | None,
    created_at: datetime,
    request_id: str | None,
    source: str | None,
    kind: str,
    day: date | None,
    year_month: str | None,
) -> int:
    """campaign-level endpoint 专用：rows 原样保留在 ad_raw_log.response_body，
    不写 ad_daily / ad_today / ad_monthly。返回 inserted=0。

    为什么不让 caller 自己去写 ad_raw_log：保留单事务、参数验证、未来字段扩展
    （如 dump_kind-specific 字段）的统一入口。
    """
    # ad_raw_log.product_id 对 campaign-level 没有意义 → 存 NULL（不是 ''、不是
    # sentinel），下游 spu_roi JOIN 时 NULL 不会污染 ad_daily 聚合（ad_daily
    # 才是 spu_roi 的真数据源）。
    # pi-lens-ignore: python-sql-injection
    sess.execute(
        text(SQL_INSERT_RAW_LOG),
        {
            "seller_id": seller_id,
            "advertiser_id": advertiser_id,
            "endpoint": endpoint,
            "campaign_id": campaign_id,
            "product_id": None,
            "kind": kind,
            "day": day,
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
    return 0


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
    requested_campaign_ids: list[str] | None = None,
) -> tuple[dict[str, list[str]], int]:
    """返回 ({campaign_id: [day1, day2, ...]}, totalCampaigns) 的元组。

    2026-09-11 加分页（隐患 #3）：page/page_size 取一段 campaign；totalCampaigns 是
    整个查询窗口内的总 campaign 数（不随 page 变）。sort 用 ORDER BY campaign_id
    保证分页结果稳定。
    """
    offset = (page - 1) * page_size
    # pi-lens-ignore: python-sql-injection — LIMIT/OFFSET 走 :page_size/:offset 参数化
    product_level = is_product_level_endpoint(endpoint)
    coverage_sql = SQL_COVERAGE_DAILY if product_level else SQL_COVERAGE_DAILY_RAW
    count_sql = SQL_COVERAGE_DAILY_COUNT if product_level else SQL_COVERAGE_DAILY_RAW_COUNT
    params = {
        "seller_id": seller_id,
        "advertiser_id": advertiser_id,
        "endpoint": endpoint,
        "start_day": start_day,
        "end_day": end_day,
        "page_size": page_size,
        "offset": offset,
    }
    requested = None if requested_campaign_ids is None else sorted(set(requested_campaign_ids))
    if requested is not None:
        page_ids = requested[offset:offset + page_size]
        if not page_ids:
            return {}, len(requested)
        requested_sql = coverage_sql.replace(
            "GROUP BY campaign_id",
            "AND campaign_id IN :campaign_ids\nGROUP BY campaign_id",
        )
        rows = sess.execute(
            text(requested_sql).bindparams(bindparam("campaign_ids", expanding=True)),
            {**params, "offset": 0, "campaign_ids": page_ids},
        ).all()
        coverage = {row[0]: [d.isoformat() for d in row[1]] for row in rows}
        return {campaign_id: coverage.get(campaign_id, []) for campaign_id in page_ids}, len(requested)

    rows = sess.execute(
        text(coverage_sql),
        params,
    ).all()
    # pi-lens-ignore: python-sql-injection — COUNT() 参数化
    total_row = sess.execute(
        text(count_sql),
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
    requested_campaign_ids: list[str] | None = None,
) -> tuple[dict[str, list[str]], int]:
    """返回 ({campaign_id: ['2026-01', ...]}, totalCampaigns) 的元组。"""
    offset = (page - 1) * page_size
    # pi-lens-ignore: python-sql-injection
    product_level = is_product_level_endpoint(endpoint)
    coverage_sql = SQL_COVERAGE_MONTHLY if product_level else SQL_COVERAGE_MONTHLY_RAW
    count_sql = SQL_COVERAGE_MONTHLY_COUNT if product_level else SQL_COVERAGE_MONTHLY_RAW_COUNT
    params = {
        "seller_id": seller_id,
        "advertiser_id": advertiser_id,
        "endpoint": endpoint,
        "start_month": start_month,
        "end_month": end_month,
        "page_size": page_size,
        "offset": offset,
    }
    requested = None if requested_campaign_ids is None else sorted(set(requested_campaign_ids))
    if requested is not None:
        page_ids = requested[offset:offset + page_size]
        if not page_ids:
            return {}, len(requested)
        requested_sql = coverage_sql.replace(
            "GROUP BY campaign_id",
            "AND campaign_id IN :campaign_ids\nGROUP BY campaign_id",
        )
        rows = sess.execute(
            text(requested_sql).bindparams(bindparam("campaign_ids", expanding=True)),
            {**params, "offset": 0, "campaign_ids": page_ids},
        ).all()
        coverage = {row[0]: list(row[1]) for row in rows}
        return {campaign_id: coverage.get(campaign_id, []) for campaign_id in page_ids}, len(requested)

    rows = sess.execute(
        text(coverage_sql),
        params,
    ).all()
    # pi-lens-ignore: python-sql-injection — COUNT() 参数化
    total_row = sess.execute(
        text(count_sql),
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
    """解析 rows → INSERT ad_daily + ad_raw_log，单事务。返回 inserted 计数。

    campaign-level endpoint（rows 无 product_id，如 campaign_opt_log_list）：
    只写 ad_raw_log（rows 保留在 response.body），不写 ad_daily。返回 0。
    """
    if not is_product_level_endpoint(endpoint):
        return _archive_raw_log_only(
            sess,
            seller_id=seller_id,
            advertiser_id=advertiser_id,
            endpoint=endpoint,
            campaign_id=campaign_id,
            rows=rows,
            request_url=request_url,
            request_body=request_body,
            response_status=response_status,
            response_body=response_body,
            created_at=created_at,
            request_id=request_id,
            source=source,
            kind="daily",
            day=day,
            year_month=None,
        )

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
    """解析 rows → INSERT ad_today（ON CONFLICT DO UPDATE）+ ad_raw_log，单事务。

    campaign-level endpoint：只写 ad_raw_log，不写 ad_today。返回 0。
    """
    if not is_product_level_endpoint(endpoint):
        return _archive_raw_log_only(
            sess,
            seller_id=seller_id,
            advertiser_id=advertiser_id,
            endpoint=endpoint,
            campaign_id=campaign_id,
            rows=rows,
            request_url=request_url,
            request_body=request_body,
            response_status=response_status,
            response_body=response_body,
            created_at=created_at,
            request_id=request_id,
            source=source,
            kind="today",
            day=day,
            year_month=None,
        )

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
    """解析 rows → INSERT ad_monthly + ad_raw_log，单事务。返回 inserted 计数。

    campaign-level endpoint：只写 ad_raw_log，不写 ad_monthly。返回 0。
    """
    if not is_product_level_endpoint(endpoint):
        return _archive_raw_log_only(
            sess,
            seller_id=seller_id,
            advertiser_id=advertiser_id,
            endpoint=endpoint,
            campaign_id=campaign_id,
            rows=rows,
            request_url=request_url,
            request_body=request_body,
            response_status=response_status,
            response_body=response_body,
            created_at=created_at,
            request_id=request_id,
            source=source,
            kind="monthly",
            day=None,
            year_month=year_month,
        )

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


def list_merge_scope_pairs(
    sess: Session,
    yesterday: date,
) -> list[tuple[str, str]]:
    """Return distinct (seller_id, advertiser_id) pairs in ad_today for *yesterday*."""
    # pi-lens-ignore: python-sql-injection
    rows = sess.execute(
        text("""
            SELECT DISTINCT seller_id, advertiser_id
            FROM plugin.ad_today
            WHERE day = :yesterday
            ORDER BY seller_id, advertiser_id
        """),
        {"yesterday": yesterday},
    ).all()
    return [(row[0], row[1]) for row in rows]


def merge_today_into_daily(
    sess: Session,
    *,
    seller_id: str,
    advertiser_id: str,
    yesterday: date,
) -> None:
    """ad_today 昨天数据 → ad_daily（跨天合并），然后清空 ad_today。"""
    # pi-lens-ignore: python-sql-injection
    sess.execute(
        text("""
            INSERT INTO plugin.ad_daily (
                seller_id, advertiser_id, campaign_id, product_id, endpoint, day,
                mixed_real_cost, onsite_roi2_shopping_sku, onsite_roi2_shopping_value,
                onsite_mixed_real_roi2_shopping, metrics_extra, created_at
            )
            SELECT seller_id, advertiser_id, campaign_id, product_id, endpoint, day,
                   mixed_real_cost, onsite_roi2_shopping_sku, onsite_roi2_shopping_value,
                   onsite_mixed_real_roi2_shopping, metrics_extra, created_at
            FROM plugin.ad_today
            WHERE seller_id = :seller_id AND advertiser_id = :advertiser_id
              AND day = :yesterday
            ON CONFLICT ON CONSTRAINT uq_ad_daily DO UPDATE SET
                mixed_real_cost = EXCLUDED.mixed_real_cost,
                onsite_roi2_shopping_sku = EXCLUDED.onsite_roi2_shopping_sku,
                onsite_roi2_shopping_value = EXCLUDED.onsite_roi2_shopping_value,
                onsite_mixed_real_roi2_shopping = EXCLUDED.onsite_mixed_real_roi2_shopping,
                metrics_extra = EXCLUDED.metrics_extra,
                updated_at = now()
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
            DELETE FROM plugin.ad_today
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
                "plugin_name": log.get("plugin_name", ""),
                "level": log.get("level", "info"),
                "message": log["message"],
                "context": json.dumps(log.get("context", {}), ensure_ascii=False),
                "occurred_at": log["occurred_at"],
            },
        )
        inserted += 1
    sess.commit()
    return inserted


# ─── Campaign opt logs ──────────────────────────────────────────────────

SQL_UPSERT_CAMPAIGN_OPT_LOG = """
INSERT INTO plugin.campaign_opt_logs (
    seller_id, advertiser_id, log_id, campaign_id,
    "user", opt_time, object_type, object_raw_type, activity_details
) VALUES (
    :seller_id, :advertiser_id, :log_id, :campaign_id,
    :user, :opt_time, :object_type, :object_raw_type, CAST(:activity_details AS JSONB)
)
ON CONFLICT (log_id) DO UPDATE SET
    "user" = EXCLUDED."user",
    opt_time = EXCLUDED.opt_time,
    object_type = EXCLUDED.object_type,
    object_raw_type = EXCLUDED.object_raw_type,
    activity_details = EXCLUDED.activity_details,
    updated_at = now()
"""


def upsert_campaign_opt_logs(
    sess: Session,
    *,
    seller_id: str,
    advertiser_id: str,
    logs: list[dict[str, Any]],
) -> int:
    """写入广告操作日志，返回 inserted 数。

    logs 数组每项结构：
    {"id": "...", "user": "...", "opt_time": "...", "object_id": "...",
     "object_type": "...", "object_raw_type": "...", "activity_details": [...]}
    """
    inserted = 0
    for log in logs:
        log_id = log.get("id")
        if not log_id:
            continue
        opt_time_str = log.get("opt_time", "")
        # opt_time 格式: "2026-09-12 15:40:46" (店铺时区)
        try:
            opt_time = datetime.strptime(opt_time_str, "%Y-%m-%d %H:%M:%S").replace(
                tzinfo=UTC
            )
        except (ValueError, TypeError):
            opt_time = datetime.now(UTC)

        sess.execute(
            text(SQL_UPSERT_CAMPAIGN_OPT_LOG),
            {
                "seller_id": seller_id,
                "advertiser_id": advertiser_id,
                "log_id": str(log_id),
                "campaign_id": log.get("object_id", ""),
                "user": log.get("user"),
                "opt_time": opt_time,
                "object_type": log.get("object_type"),
                "object_raw_type": log.get("object_raw_type"),
                "activity_details": json.dumps(
                    log.get("activity_details", []), ensure_ascii=False
                ),
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
    "is_product_level_endpoint",
    "merge_today_into_daily",
    "list_merge_scope_pairs",
    "upsert_daily_rows",
    "upsert_monthly_rows",
    "upsert_today_rows",
]
