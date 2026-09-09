"""SPU 实际 ROI 看板 + 钻取面板（D6/D7/D8，2026-09-07）。

设计稿：``tech-doc/analytics/spu-roi-v7-refactor.md``（D1–D8 全拍板）。
口径真理：``handoff/spu-roi-full-loss-rubric.md`` v7（v8 待升版同步）；
``tech-doc/analytics/spu-real-roi-dashboard.md`` §4/§5（v7 公式已就位）。

模块边界（D3）：
  - 主表路由：``GET  /v2/analytics/spu-roi``        （薄 handler → ``_query_spu_roi``）
  - 钻取路由：``GET  /v2/analytics/spu-roi/{spu_pk}/{orders|settlements|cases|ads}``
  - 所有 SQL 常量与公式实现仅在本文件；``tts_erp_v2/api/v2/analytics.py`` 仅 import。
"""

from __future__ import annotations

import logging
from datetime import UTC, date, datetime, time, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import text
from sqlalchemy.orm import Session

from tts_erp_v2.api.deps import get_session
from tts_erp_v2.db.constants import PAID_SALES_ORDER_STATUSES
from tts_erp_v2.fx.rates import load_rate_map

log = logging.getLogger(__name__)

# ═════════════════════════════════════════════════════════════════════
# §3.1 模块抽取：常量（口径见 dashboard §4 + D9/D10 实测重定）
# ═════════════════════════════════════════════════════════════════════

# 固定汇率常量（仅作 fx 快照缺失时的兜底）
FX_USD_VND = Decimal(26330)
FX_CNY_USD = Decimal("0.14774")
# K1 默认 = 40 CNY/件（D1 拍板：原 K1=30 作废；≈ $5.95/件 @0.148823）
K1_DEFAULT_CNY = Decimal(40)
# 平台佣金基线 r̂（dashboard D10 2026-09-06 实测重定）
FEE_RATE_BASELINE = Decimal("0.308")
FX_AS_OF = "2026-09-05"

_MONEY_Q = Decimal("0.0001")
_RATIO_Q = Decimal("0.01")
_RATE_Q8 = Decimal("0.00000001")

# 排序白名单（D8 主列新增 full_loss_rate；其余与旧实现一致）
_ROI_SORT_FIELDS = (
    "roi_real",
    "spend",
    "refund_rate",
    "refund_rate_qty",
    "cancel_rate",
    "net_profit",
    "sales",
    "gmv_sales",
    "ad_count",
    "gmv_ad",
    "order_count",
    "cancelled_order_count",
    "units_sold",
    "refund_net_amount",
    "return_loss",
    "roi_breakeven",
    "full_loss_rate",  # D8 新增
)

# 售后/case 完结状态白名单（与旧实现一致）
_CASE_COMPLETED_STATUSES = (
    "CANCELLATION_REQUEST_COMPLETE",
    "RETURN_OR_REFUND_REQUEST_COMPLETE",
)

_TRACK_ACTION_CODE_OVERSEAS = 38301  # "Arrived in destination country/region"

# 钻取面板 orders 上限（D6 拍板）
_ORDERS_MAX = 500


# ═════════════════════════════════════════════════════════════════════
# §3.2 SQL 常量集
# ═════════════════════════════════════════════════════════════════════

# 主表 SQL ── 广告域（不变）
_SQL_ROI_AD = text(
    """
    SELECT spu_pk,
           count(DISTINCT campaign_id)::int          AS ad_count,
           coalesce(sum(real_cost_total), 0)         AS spend,
           coalesce(sum(order_value_total), 0)       AS gmv_ad,
           coalesce(sum(order_sku_total), 0)::bigint AS ad_orders,
           min(first_day)                            AS ad_first_day,
           max(last_day)                             AS ad_last_day
    FROM analytics.ad_product_links
    WHERE spu_pk IS NOT NULL
    GROUP BY spu_pk
    """
)

# 主表 SQL ── 销售域（v7 CTE：已/未结算分层 + line_net 分摊 + per-SPU 桶）
_SQL_ROI_SALES = text(
    """
    WITH order_settlement AS (
        SELECT st.order_pk, SUM(sc.amount) AS settlement_vnd
        FROM finance.settlement_transactions st
        JOIN finance.settlement_components sc
          ON sc.transaction_id = st.id AND sc.component_code = 'SETTLEMENT'
        WHERE st.order_pk IS NOT NULL
        GROUP BY st.order_pk
    ),
    lines AS (
        SELECT sl.spu_pk,
               sl.order_pk,
               sl.quantity,
               sl.quantity * sl.unit_price AS line_gmv_vnd,
               SUM(sl.quantity * sl.unit_price)
                   OVER (PARTITION BY sl.order_pk) AS order_gmv_vnd,
               os.settlement_vnd,
               (coalesce(so.paid_at, so.order_time)
                AT TIME ZONE 'UTC')::date AS event_day
        FROM commerce.sales_order_lines sl
        JOIN commerce.sales_orders so ON so.id = sl.order_pk
        LEFT JOIN order_settlement os ON os.order_pk = sl.order_pk
        WHERE sl.spu_pk IS NOT NULL
          AND so.status = ANY(CAST(:paid_statuses AS text[]))
          /* 窗口裁剪：COALESCE(paid_at, order_time) UTC 日 */
          AND (CAST(:ws AS timestamptz) IS NULL
               OR coalesce(so.paid_at, so.order_time) >= CAST(:ws AS timestamptz))
          AND (CAST(:we AS timestamptz) IS NULL
               OR coalesce(so.paid_at, so.order_time) <  CAST(:we AS timestamptz))
    )
    SELECT spu_pk,
           count(DISTINCT order_pk)                                       AS order_count,
           sum(quantity)                                                  AS units_sold,
           sum(line_gmv_vnd)                                              AS sales_vnd,
           sum(settlement_vnd * line_gmv_vnd / NULLIF(order_gmv_vnd, 0))
               FILTER (WHERE settlement_vnd IS NOT NULL)                 AS settled_net_vnd,
           sum(line_gmv_vnd)
               FILTER (WHERE settlement_vnd IS NOT NULL)                 AS settled_sales_vnd,
           sum(line_gmv_vnd)
               FILTER (WHERE settlement_vnd IS NULL)                     AS unsettled_sales_vnd,
           count(DISTINCT order_pk)
               FILTER (WHERE settlement_vnd IS NOT NULL)                 AS settled_order_count
    FROM lines
    GROUP BY spu_pk
    """
)

# 主表 SQL ── 全损件数（D4 B 口径：38301 ∨ 完结 case ∨ CANCELLED）
_SQL_ROI_FULL_LOSS = text(
    """
    SELECT sl.spu_pk,
           sum(sl.quantity)                                                AS full_loss_qty,
           sum(sl.quantity) FILTER (WHERE so.status = 'CANCELLED')        AS full_loss_cancelled_qty
    FROM commerce.sales_order_lines sl
    JOIN commerce.sales_orders so ON so.id = sl.order_pk
    WHERE sl.spu_pk IS NOT NULL
      AND EXISTS (SELECT 1 FROM fulfillment.shipments sh
                  JOIN fulfillment.tracking_events te
                    ON te.shipment_id = sh.id AND te.action_code = :ac
                  WHERE sh.order_pk = so.id)
      AND (so.status = 'CANCELLED'
           OR EXISTS (SELECT 1 FROM after_sales.cases c
                      WHERE c.order_pk = so.id
                        AND c.status IN (:st0, :st1)))
      AND (CAST(:ws AS timestamptz) IS NULL
           OR coalesce(so.paid_at, so.order_time) >= CAST(:ws AS timestamptz))
      AND (CAST(:we AS timestamptz) IS NULL
           OR coalesce(so.paid_at, so.order_time) <  CAST(:we AS timestamptz))
    GROUP BY sl.spu_pk
    """
)

# 主表 SQL ── 行级取消/退货订单统计（不变）
_SQL_ROI_ROW_STATUS = text(
    """
    SELECT sl.spu_pk,
           count(DISTINCT so.id) FILTER (
               WHERE so.status = 'CANCELLED')                              AS cancelled_order_count,
           coalesce(sum(sl.quantity * sl.unit_price) FILTER (
               WHERE so.status = 'CANCELLED'), 0)                          AS cancelled_sales,
           count(DISTINCT so.id) FILTER (
               WHERE so.status = ANY(CAST(:paid_statuses AS text[]))
                 AND EXISTS (SELECT 1 FROM after_sales.cases c2
                             JOIN after_sales.case_lines cl2 ON cl2.case_id = c2.id
                             WHERE c2.order_pk = so.id
                               AND cl2.sales_order_line_id = sl.id
                               AND c2.status IN (:st0, :st1)
                               AND c2.case_type IN ('REFUND_ONLY', 'RETURN_AND_REFUND'))) AS refund_order_count
    FROM commerce.sales_order_lines sl
    JOIN commerce.sales_orders so ON so.id = sl.order_pk
    WHERE sl.spu_pk IS NOT NULL
      AND (so.status = ANY(CAST(:paid_statuses AS text[]))
           OR so.status = 'CANCELLED')
      AND (CAST(:ws AS timestamptz) IS NULL
           OR coalesce(so.paid_at, so.order_time) >= CAST(:ws AS timestamptz))
      AND (CAST(:we AS timestamptz) IS NULL
           OR coalesce(so.paid_at, so.order_time) <  CAST(:we AS timestamptz))
    GROUP BY sl.spu_pk
    """
)

# 主表 SQL ── 退款拆分（不变）
_SQL_ROI_REFUNDS = text(
    """
    SELECT sl.spu_pk,
           coalesce(sum(cl.quantity)       FILTER (
               WHERE so.status = ANY(CAST(:paid_statuses AS text[]))
                 AND c.case_type = 'REFUND_ONLY'), 0)      AS refund_only_qty,
           coalesce(sum(cl.refund_amount)  FILTER (
               WHERE so.status = ANY(CAST(:paid_statuses AS text[]))
                 AND c.case_type = 'REFUND_ONLY'), 0)      AS refund_only_amount,
           coalesce(sum(cl.quantity)       FILTER (
               WHERE so.status = ANY(CAST(:paid_statuses AS text[]))
                 AND c.case_type = 'RETURN_AND_REFUND'), 0) AS refund_return_qty,
           coalesce(sum(cl.refund_amount)  FILTER (
               WHERE so.status = ANY(CAST(:paid_statuses AS text[]))
                 AND c.case_type = 'RETURN_AND_REFUND'), 0) AS refund_return_amount,
           coalesce(sum(cl.quantity)       FILTER (
               WHERE so.status = 'CANCELLED'
                 AND c.case_type IN ('CANCELLATION', 'CANCEL')), 0) AS refund_cancelled_qty,
           coalesce(sum(cl.refund_amount)  FILTER (
               WHERE so.status = 'CANCELLED'
                 AND c.case_type IN ('CANCELLATION', 'CANCEL')
                 AND cl.refund_amount IS NOT NULL), 0)      AS refund_cancelled_amount,
           count(*)                         FILTER (
               WHERE so.status = 'CANCELLED'
                 AND c.case_type IN ('CANCELLATION', 'CANCEL')
                 AND cl.refund_amount IS NULL)::int         AS refund_cancelled_missing_lines
    FROM after_sales.cases c
    JOIN after_sales.case_lines cl ON cl.case_id = c.id
    JOIN commerce.sales_order_lines sl ON sl.id = cl.sales_order_line_id
    JOIN commerce.sales_orders so ON so.id = c.order_pk
    WHERE c.status IN (:st0, :st1)
      AND sl.spu_pk IS NOT NULL
      AND (CAST(:ws AS timestamptz) IS NULL
           OR c.updated_at_source >= CAST(:ws AS timestamptz))
      AND (CAST(:we AS timestamptz) IS NULL
           OR c.updated_at_source <  CAST(:we AS timestamptz))
    GROUP BY sl.spu_pk
    """
)

# 主表 SQL ── 跨 SPU 范围 distinct（order_count/cancelled/total/GMV）
_SQL_ROI_ORDER_SCOPE = text(
    """
    SELECT
      count(DISTINCT so.id) FILTER (
          WHERE so.status = ANY(CAST(:paid_statuses AS text[])))        AS order_count,
      count(DISTINCT so.id) FILTER (
          WHERE so.status = 'CANCELLED')                                AS cancelled_order_count,
      coalesce(sum(sl.quantity * sl.unit_price) FILTER (
          WHERE so.status = ANY(CAST(:paid_statuses AS text[]))
             OR so.status = 'CANCELLED'), 0)                            AS gmv
    FROM commerce.sales_order_lines sl
    JOIN commerce.sales_orders so ON so.id = sl.order_pk
    WHERE sl.spu_pk = ANY(CAST(:pks AS bigint[]))
      AND (so.status = ANY(CAST(:paid_statuses AS text[]))
           OR so.status = 'CANCELLED')
      AND (CAST(:ws AS timestamptz) IS NULL
           OR coalesce(so.paid_at, so.order_time) >= CAST(:ws AS timestamptz))
      AND (CAST(:we AS timestamptz) IS NULL
           OR coalesce(so.paid_at, so.order_time) <  CAST(:we AS timestamptz))
    """
)

# 主表 SQL ── SPU 目录（不变）
_SQL_ROI_CATALOG = text(
    """
    SELECT cp.id AS spu_pk, cp.shop_pk, cp.spu_id, cp.title, cp.status,
           cp.main_image_url, s.shop_id, s.account_name AS shop_name
    FROM commerce.products_spu cp
    LEFT JOIN commerce.shops s ON s.id = cp.shop_pk
    WHERE (CAST(:shop_pk AS bigint) IS NULL
           OR cp.shop_pk = CAST(:shop_pk AS bigint))
      AND (CAST(:q AS text) IS NULL OR cp.spu_id ILIKE '%' || :q || '%')
      AND (CAST(:active_only AS boolean) IS NOT TRUE
           OR cp.status ILIKE 'activate')
    ORDER BY cp.id
    """
)

_SQL_ROI_WINDOW = text(
    "SELECT min(first_day) AS first_day, max(last_day) AS last_day "
    "FROM analytics.ad_product_links"
)

_SQL_ROI_DATA_WINDOW = text(
    """
    WITH croppable AS (
        SELECT (coalesce(so.paid_at, so.order_time) AT TIME ZONE 'UTC')::date AS d
        FROM commerce.sales_orders so
        WHERE so.status = ANY(CAST(:paid_statuses AS text[]))
          AND (CAST(:shop_pk AS bigint) IS NULL
               OR so.shop_pk = CAST(:shop_pk AS bigint))
        UNION
        SELECT (c.updated_at_source AT TIME ZONE 'UTC')::date AS d
        FROM after_sales.cases c
        WHERE c.status IN (:st0, :st1)
          AND (CAST(:shop_pk AS bigint) IS NULL
               OR c.shop_pk = CAST(:shop_pk AS bigint))
    )
    SELECT min(d) AS first_day, max(d) AS last_day FROM croppable
    """
)

_SQL_ROI_UNATTRIBUTED = text(
    """
    SELECT count(*)::int AS n
    FROM (
        SELECT cl.id
        FROM after_sales.cases c
        JOIN after_sales.case_lines cl ON cl.case_id = c.id
        LEFT JOIN commerce.sales_order_lines sl ON sl.id = cl.sales_order_line_id
        WHERE c.status IN (:st0, :st1)
          AND (cl.sales_order_line_id IS NULL OR sl.spu_pk IS NULL)
          AND (CAST(:shop_pk AS bigint) IS NULL
               OR c.shop_pk = CAST(:shop_pk AS bigint))
        UNION ALL
        SELECT cl.id
        FROM after_sales.cases c
        JOIN after_sales.case_lines cl ON cl.case_id = c.id
        JOIN commerce.sales_order_lines sl ON sl.id = cl.sales_order_line_id
        JOIN commerce.sales_orders so ON so.id = c.order_pk
        WHERE c.status IN (:st0, :st1)
          AND sl.spu_pk IS NOT NULL
          AND so.status <> ALL (CAST(:paid_statuses AS text[]))
          AND so.status <> 'CANCELLED'
          AND (CAST(:shop_pk AS bigint) IS NULL
               OR c.shop_pk = CAST(:shop_pk AS bigint))
    ) u
    """
)

# ═════════════════════════════════════════════════════════════════════
# §3.4 成本链 SQL（批量 set-based，与 jobs/reporting.py 口径 1:1）
# ═════════════════════════════════════════════════════════════════════

# L1: 人工标注的采购成交价（manual_product_costs）
_SQL_COST_MANUAL = text(
    """
    SELECT spu_pk, unit_cost
    FROM procurement.manual_product_costs
    WHERE valid_to IS NULL
      AND spu_pk = ANY(CAST(:pks AS bigint[]))
    """
)

# L3a: 1688 货源价 — TK-side 直取（external_product_id = spu_id）
_SQL_COST_SOURCE_DIRECT = text(
    """
    SELECT DISTINCT ON (cp.id)
           cp.id AS spu_pk,
           pp.source_unit_cost AS unit_cost
    FROM commerce.products_spu cp
    JOIN procurement.procurement_products pp
      ON pp.external_product_id = cp.spu_id
    WHERE cp.id = ANY(CAST(:pks AS bigint[]))
      AND pp.source_unit_cost IS NOT NULL
    ORDER BY cp.id, pp.synced_at DESC NULLS LAST, pp.id DESC
    """
)

# L3b: 1688 货源价 — 通过 source_item_id 桥公共采集箱行
_SQL_COST_SOURCE_VIA_OFFER = text(
    """
    WITH offer AS (
        SELECT DISTINCT ON (cp.id)
               cp.id AS spu_pk,
               pp.source_item_id
        FROM commerce.products_spu cp
        JOIN procurement.procurement_products pp
          ON pp.external_product_id = cp.spu_id
        WHERE cp.id = ANY(CAST(:pks AS bigint[]))
          AND pp.source_item_id IS NOT NULL
        ORDER BY cp.id, pp.synced_at DESC NULLS LAST, pp.id DESC
    )
    SELECT DISTINCT ON (o.spu_pk)
           o.spu_pk AS spu_pk,
           pp.source_unit_cost AS unit_cost
    FROM offer o
    JOIN procurement.procurement_products pp
      ON pp.source_item_id = o.source_item_id
    WHERE pp.source_unit_cost IS NOT NULL
    ORDER BY o.spu_pk, pp.synced_at DESC NULLS LAST, pp.id DESC
    """
)

# ═════════════════════════════════════════════════════════════════════
# 钻取面板 SQL（§6.3）
# ═════════════════════════════════════════════════════════════════════

# /orders — 单 SPU 窗口内订单 + 物流 + tracking
_SQL_DETAIL_ORDERS = text(
    """
    SELECT so.id AS order_pk,
           so.order_id,
           so.status,
           coalesce(so.paid_at, so.order_time) AS paid_at,
           sl.quantity AS qty,
           (sl.quantity * sl.unit_price) AS line_gmv_vnd,
           EXISTS (SELECT 1 FROM fulfillment.shipments sh
                   JOIN fulfillment.tracking_events te
                     ON te.shipment_id = sh.id AND te.action_code = :ac
                   WHERE sh.order_pk = so.id) AS arrived_overseas,
           EXISTS (SELECT 1 FROM after_sales.cases c
                   WHERE c.order_pk = so.id
                     AND c.status IN (:st0, :st1)) AS has_completed_case,
           so.status = 'CANCELLED' AS is_cancelled,
           (SELECT SUM(sc.amount) FROM finance.settlement_transactions st
            JOIN finance.settlement_components sc
              ON sc.transaction_id = st.id AND sc.component_code = 'SETTLEMENT'
            WHERE st.order_pk = so.id) AS settlement_vnd,
           sh.id AS shipment_pk,
           sh.status AS shipment_status,
           sh.tracking_number AS shipment_tracking_number
    FROM commerce.sales_order_lines sl
    JOIN commerce.sales_orders so ON so.id = sl.order_pk
    LEFT JOIN fulfillment.shipments sh ON sh.order_pk = so.id
    WHERE sl.spu_pk = :spu_pk
      AND (CAST(:ws AS timestamptz) IS NULL
           OR coalesce(so.paid_at, so.order_time) >= CAST(:ws AS timestamptz))
      AND (CAST(:we AS timestamptz) IS NULL
           OR coalesce(so.paid_at, so.order_time) <  CAST(:we AS timestamptz))
    ORDER BY coalesce(so.paid_at, so.order_time) DESC NULLS LAST, so.id DESC
    LIMIT :lim
    """
)

_SQL_DETAIL_TRACKING = text(
    """
    SELECT te.action_code,
           te.event_at,
           te.description
    FROM fulfillment.tracking_events te
    JOIN fulfillment.shipments sh ON sh.id = te.shipment_id
    WHERE sh.id = :shipment_pk
    ORDER BY te.event_at ASC NULLS LAST, te.id ASC
    """
)

# /settlements — 已结算订单的组件拆分
_SQL_DETAIL_SETTLEMENTS = text(
    """
    SELECT so.id AS order_pk,
           so.order_id,
           st.id AS txn_pk,
           st.transaction_time AS statement_time
    FROM commerce.sales_orders so
    JOIN finance.settlement_transactions st ON st.order_pk = so.id
    WHERE so.id IN (
        SELECT order_pk FROM commerce.sales_order_lines
        WHERE spu_pk = :spu_pk
    )
    ORDER BY st.transaction_time DESC NULLS LAST, st.id DESC
    """
)

_SQL_DETAIL_SETTLE_LINE_GMV = text(
    """
    SELECT so.id AS order_pk, sum(sl.quantity * sl.unit_price) AS order_gmv_vnd
    FROM commerce.sales_orders so
    JOIN commerce.sales_order_lines sl ON sl.order_pk = so.id
    WHERE so.id = :order_pk
    GROUP BY so.id
    """
)

_SQL_DETAIL_SETTLE_COMPONENTS = text(
    """
    SELECT sc.component_code, sc.amount, sc.currency
    FROM finance.settlement_components sc
    WHERE sc.transaction_id = :txn_pk
    ORDER BY sc.component_code
    """
)

# /cases — 售后 case（带 order_id 关联）
_SQL_DETAIL_CASES = text(
    """
    SELECT c.id AS case_pk,
           c.external_case_id AS case_id,
           c.order_pk,
           so.order_id,
           c.case_type,
           c.status,
           cl.quantity,
           cl.refund_amount,
           c.reason_code,
           c.reason_text,
           c.updated_at_source,
           sl.spu_pk
    FROM after_sales.cases c
    LEFT JOIN after_sales.case_lines cl ON cl.case_id = c.id
    LEFT JOIN commerce.sales_order_lines sl ON sl.id = cl.sales_order_line_id
    LEFT JOIN commerce.sales_orders so ON so.id = c.order_pk
    WHERE sl.spu_pk = :spu_pk
      AND (CAST(:ws AS timestamptz) IS NULL
           OR c.updated_at_source >= CAST(:ws AS timestamptz))
      AND (CAST(:we AS timestamptz) IS NULL
           OR c.updated_at_source <  CAST(:we AS timestamptz))
    ORDER BY c.updated_at_source DESC NULLS LAST, c.id DESC
    """
)

# /ads — campaign × SPU（无窗口；广告全窗口累计）
_SQL_DETAIL_ADS = text(
    """
    SELECT campaign_id,
           sum(real_cost_total)        AS spend,
           sum(order_sku_total)::bigint AS ad_orders,
           min(first_day)               AS first_day,
           max(last_day)                AS last_day
    FROM analytics.ad_product_links
    WHERE spu_pk = :spu_pk
    GROUP BY campaign_id
    ORDER BY max(last_day) DESC NULLS LAST, campaign_id
    """
)


# ═════════════════════════════════════════════════════════════════════
# 工具函数
# ═════════════════════════════════════════════════════════════════════


def _resolve_fx_rates(sess: Session) -> tuple[Decimal, Decimal, str, str]:
    """ROI 账页换算汇率：在线 fx 缓存优先，D9 常量兜底。"""
    rm = load_rate_map(sess, base_code="USD")
    if rm is not None and {"VND", "CNY"} <= set(rm.rates):
        cny_usd = (Decimal(1) / rm.rates["CNY"]).quantize(
            _RATE_Q8, rounding=ROUND_HALF_UP
        )
        return (
            cny_usd,
            rm.rates["VND"],
            rm.upstream_last_update.date().isoformat(),
            "fx-cache",
        )
    return (FX_CNY_USD, FX_USD_VND, FX_AS_OF, "fixed-const")


def _fmt_money(value: Decimal | None) -> str | None:
    if value is None:
        return None
    return format(value.quantize(_MONEY_Q, rounding=ROUND_HALF_UP), ".4f")


def _fmt_ratio(value: Decimal | None) -> str | None:
    if value is None:
        return None
    return format(value.quantize(_RATIO_Q, rounding=ROUND_HALF_UP), ".2f")


def _row_int(value: Any) -> int:
    try:
        return int(value) if value is not None else 0
    except (TypeError, ValueError):
        return 0


def _parse_fee_rate(raw: str | None) -> Decimal | None:
    """fee_rate query param 解析（防御 NaN/Infinity/超量级/负值）。"""
    if raw is None or not raw.strip():
        return None
    s = raw.strip()
    try:
        v = Decimal(s)
    except Exception as exc:
        raise HTTPException(
            status_code=422, detail="fee_rate must be a decimal"
        ) from exc
    if not v.is_finite():
        raise HTTPException(status_code=422, detail="fee_rate must be a finite decimal")
    if v.copy_abs() > Decimal("1e6"):
        raise HTTPException(
            status_code=422,
            detail="fee_rate out of reasonable range (|fee_rate| <= 1e6)",
        )
    if v < 0:
        raise HTTPException(status_code=422, detail="fee_rate must be >= 0")
    return v


def _window_dates(w_start: date | None, w_end: date | None) -> tuple[Any, Any]:
    ws_dt = datetime.combine(w_start, time.min, tzinfo=UTC) if w_start else None
    we_dt = (
        datetime.combine(w_end + timedelta(days=1), time.min, tzinfo=UTC)
        if w_end
        else None
    )
    return ws_dt, we_dt


def _spu_pk_exists(sess: Session, spu_pk: int) -> bool:
    row = sess.execute(
        text("SELECT 1 FROM commerce.products_spu WHERE id = :pk"),
        {"pk": spu_pk},
    ).first()
    return row is not None


# ═════════════════════════════════════════════════════════════════════
# 成本链批量解析
# ═════════════════════════════════════════════════════════════════════

# cost_source 枚举（按命中优先级排序）
# 2026-09-08 业务调整: 去 PURCHASE（妙手采购单）这一档，3 档链
# MANUAL（人工标注）> SOURCE_PRICE（1688 货源价）> DEFAULT_K1（40 CNY 兜底）
COST_SOURCE_PRIORITY = ("MANUAL", "SOURCE_PRICE", "DEFAULT_K1")


def _resolve_costs_batch(
    sess: Session, spu_pks: list[int]
) -> dict[int, tuple[Decimal, str]]:
    """批量解析每个 SPU 的单位成本 CNY + 来源枚举（§3.4 全链）。

    优先级：MANUAL > SOURCE_PRICE > DEFAULT(40 CNY)。
    返回：{spu_pk: (unit_cost_cny, cost_source)}。DEFAULT_K1 行仅当两层
    全部 miss 时兜底，UI 上需标 ⚠ 提示。
    """
    if not spu_pks:
        return {}
    out: dict[int, tuple[Decimal, str]] = {}

    # L1: manual_product_costs（人工标注的采购成交价）
    rows = sess.execute(_SQL_COST_MANUAL, {"pks": spu_pks}).mappings().all()
    for r in rows:
        out[int(r["spu_pk"])] = (Decimal(r["unit_cost"]), "MANUAL")  # pi-lens-ignore: no-try-except

    # L2: SOURCE_PRICE（1688 货源价，direct + via offer 两条路径）
    missing = [pk for pk in spu_pks if pk not in out]
    if missing:
        rows = sess.execute(_SQL_COST_SOURCE_DIRECT, {"pks": missing}).mappings().all()
        for r in rows:
            out[int(r["spu_pk"])] = (Decimal(r["unit_cost"]), "SOURCE_PRICE")  # pi-lens-ignore: no-try-except
        still_missing = [pk for pk in missing if pk not in out]
        if still_missing:
            rows = (
                sess.execute(_SQL_COST_SOURCE_VIA_OFFER, {"pks": still_missing})
                .mappings()
                .all()
            )
            for r in rows:
                out[int(r["spu_pk"])] = (Decimal(r["unit_cost"]), "SOURCE_PRICE")  # pi-lens-ignore: no-try-except

    # L3: DEFAULT_K1（40 CNY/件 兜底，UI 上需 ⚠ 标注）
    for pk in spu_pks:
        if pk not in out:
            out[pk] = (K1_DEFAULT_CNY, "DEFAULT_K1")

    return out


# ═════════════════════════════════════════════════════════════════════
# 主表查询 + 计算（§2/§3.3 公式）
# ═════════════════════════════════════════════════════════════════════


def _query_spu_roi(
    sess: Session,
    *,
    q: str | None,
    shop_pk: int | None,
    include_all: bool,
    sort_field: str,
    ascending: bool,
    limit: int,
    offset: int,
    fee_rate: Decimal | None,
    w_start: date | None = None,
    w_end: date | None = None,
) -> dict:
    rate = fee_rate if fee_rate is not None else FEE_RATE_BASELINE
    fx_cny_usd, fx_usd_vnd, fx_as_of, fx_source = _resolve_fx_rates(sess)
    ws_dt, we_dt = _window_dates(w_start, w_end)
    paid_statuses = list(PAID_SALES_ORDER_STATUSES)
    st0, st1 = _CASE_COMPLETED_STATUSES

    cats = (
        sess.execute(
            _SQL_ROI_CATALOG,
            {"shop_pk": shop_pk, "q": q, "active_only": include_all},
        )
        .mappings()
        .all()
    )

    ad_rows = sess.execute(_SQL_ROI_AD).mappings().all()
    ad_map = {int(r["spu_pk"]): r for r in ad_rows if r["spu_pk"] is not None}  # pi-lens-ignore: no-try-except

    sales_rows = (
        sess.execute(
            _SQL_ROI_SALES,
            {"paid_statuses": paid_statuses, "ws": ws_dt, "we": we_dt},
        )
        .mappings()
        .all()
    )
    sales_map = {int(r["spu_pk"]): r for r in sales_rows if r["spu_pk"] is not None}  # pi-lens-ignore: no-try-except

    fl_rows = (
        sess.execute(
            _SQL_ROI_FULL_LOSS,
            {
                "paid_statuses": paid_statuses,
                "ac": _TRACK_ACTION_CODE_OVERSEAS,
                "st0": st0,
                "st1": st1,
                "ws": ws_dt,
                "we": we_dt,
            },
        )
        .mappings()
        .all()
    )
    fl_map = {int(r["spu_pk"]): r for r in fl_rows if r["spu_pk"] is not None}  # pi-lens-ignore: no-try-except

    rs_rows = (
        sess.execute(
            _SQL_ROI_ROW_STATUS,
            {
                "paid_statuses": paid_statuses,
                "st0": st0,
                "st1": st1,
                "ws": ws_dt,
                "we": we_dt,
            },
        )
        .mappings()
        .all()
    )
    rs_map = {int(r["spu_pk"]): r for r in rs_rows if r["spu_pk"] is not None}  # pi-lens-ignore: no-try-except

    refund_rows = (
        sess.execute(
            _SQL_ROI_REFUNDS,
            {
                "paid_statuses": paid_statuses,
                "st0": st0,
                "st1": st1,
                "ws": ws_dt,
                "we": we_dt,
            },
        )
        .mappings()
        .all()
    )
    refund_map = {int(r["spu_pk"]): r for r in refund_rows if r["spu_pk"] is not None}  # pi-lens-ignore: no-try-except

    # 成本链批量解析（D1）
    spu_pks_all = [int(c["spu_pk"]) for c in cats]  # pi-lens-ignore: no-try-except
    cost_map = _resolve_costs_batch(sess, spu_pks_all)

    plain: list[dict] = []
    total_spend = Decimal(0)
    total_net_revenue_usd = Decimal(0)
    total_return_loss = Decimal(0)

    for cat in cats:
        pk = int(cat["spu_pk"])
        ad = ad_map.get(pk)
        sales = sales_map.get(pk)
        fl = fl_map.get(pk)
        if (
            not include_all
            and ad is None
            and sales is None
            and refund_map.get(pk) is None
        ):
            continue

        spend = Decimal(ad["spend"]) if ad else Decimal(0)
        gmv_ad = Decimal(ad["gmv_ad"]) if ad else Decimal(0)
        ad_orders = _row_int(ad["ad_orders"]) if ad else 0
        ad_count = _row_int(ad["ad_count"]) if ad else 0
        ad_first_day = ad["ad_first_day"] if ad else None
        ad_last_day = ad["ad_last_day"] if ad else None

        order_count = _row_int(sales["order_count"]) if sales else 0
        units_sold = _row_int(sales["units_sold"]) if sales else 0
        sales_vnd = Decimal(sales["sales_vnd"] or 0) if sales else Decimal(0)
        settled_net_vnd = (
            Decimal(sales["settled_net_vnd"] or 0) if sales else Decimal(0)
        )
        settled_sales_vnd = (
            Decimal(sales["settled_sales_vnd"] or 0) if sales else Decimal(0)
        )
        unsettled_sales_vnd = (
            Decimal(sales["unsettled_sales_vnd"] or 0) if sales else Decimal(0)
        )
        settled_order_count = _row_int(sales["settled_order_count"]) if sales else 0

        flc_qty = _row_int(fl["full_loss_cancelled_qty"]) if fl else 0
        full_loss_qty = _row_int(fl["full_loss_qty"]) if fl else 0

        rs = rs_map.get(pk)
        cancelled_orders = _row_int(rs["cancelled_order_count"]) if rs else 0
        cancelled_sales_vnd = Decimal(rs["cancelled_sales"]) if rs else Decimal(0)

        refund = refund_map.get(pk)
        refund_only_qty = _row_int(refund["refund_only_qty"]) if refund else 0
        refund_return_qty = _row_int(refund["refund_return_qty"]) if refund else 0
        refund_cancelled_qty = _row_int(refund["refund_cancelled_qty"]) if refund else 0
        refund_cancelled_missing = (
            _row_int(refund["refund_cancelled_missing_lines"]) if refund else 0
        )
        refund_only_vnd = (
            Decimal(refund["refund_only_amount"]) if refund else Decimal(0)
        )
        refund_return_vnd = (
            Decimal(refund["refund_return_amount"]) if refund else Decimal(0)
        )
        refund_cancelled_vnd = (
            Decimal(refund["refund_cancelled_amount"]) if refund else Decimal(0)
        )
        refund_net_vnd = refund_only_vnd + refund_return_vnd

        # 成本解析（D1 全链）
        unit_cost_cny, cost_source = cost_map.get(pk, (K1_DEFAULT_CNY, "DEFAULT_K1"))
        unit_cost_usd = unit_cost_cny * fx_cny_usd

        # 原币 → USD（§3.6 通用规则：输出层一次换算）
        sales_usd = sales_vnd / fx_usd_vnd
        settled_net_usd = settled_net_vnd / fx_usd_vnd
        settled_sales_usd = settled_sales_vnd / fx_usd_vnd
        unsettled_sales_usd = unsettled_sales_vnd / fx_usd_vnd
        refund_only_usd = refund_only_vnd / fx_usd_vnd
        refund_return_usd = refund_return_vnd / fx_usd_vnd
        refund_net_usd = refund_only_usd + refund_return_usd
        refund_cancelled_usd = refund_cancelled_vnd / fx_usd_vnd
        cancelled_sales_usd = cancelled_sales_vnd / fx_usd_vnd

        # refund_rate_spu（M12 金额口径）— 全 0 时=0，钳位 [0,1]
        refund_rate_spu = Decimal(0)
        if sales_vnd > 0:
            refund_rate_spu = min(
                Decimal(1), max(Decimal(0), refund_net_vnd / sales_vnd)
            )

        # net_revenue: settled_net + unsettled × (1−r̂) × (1−rate) （D5）
        net_revenue_usd = settled_net_usd + unsettled_sales_usd * (
            Decimal(1) - rate
        ) * (Decimal(1) - refund_rate_spu)

        # COGS（v6 补扣：售出 + 全损取消）
        cogs_all_usd = (Decimal(units_sold) + Decimal(flc_qty)) * unit_cost_usd

        # platform_fee = r̂ × unsettled_sales_usd（信息列，不进 net_profit）
        platform_fee_usd = rate * unsettled_sales_usd

        # return_loss（M13b 切 38301 全损口径，D4 B）
        return_loss_usd = Decimal(full_loss_qty) * unit_cost_usd

        # net_profit（v7）：net_revenue − COGS_all − spend
        net_profit_usd = net_revenue_usd - cogs_all_usd - spend

        # ROI（M14）：(net_revenue − return_loss) / spend
        roi_real_usd = None
        if spend != 0:
            roi_real_usd = (net_revenue_usd - return_loss_usd) / spend
        # 保本 ROI（M17）：NC′ / (NC′ − COGS_kept)；COGS_kept ≥ 0 钳位
        cogs_kept_usd = max(
            Decimal(0),
            (Decimal(units_sold) - Decimal(refund_return_qty)) * unit_cost_usd,
        )
        breakeven_denom = (net_revenue_usd - return_loss_usd) - cogs_kept_usd
        roi_breakeven_usd = None
        if spend != 0 and breakeven_denom > 0:
            roi_breakeven_usd = (net_revenue_usd - return_loss_usd) / breakeven_denom

        # CPA / ROI₀ / cancel_rate / refund_rate 等
        cpa_usd = spend / Decimal(ad_orders) if ad_orders else None
        roi_l0_usd = gmv_ad / spend if spend != 0 else None
        refund_rate_usd = refund_net_usd / sales_usd if sales_usd > 0 else None
        gmv_sales_usd = sales_usd + cancelled_sales_usd

        # full_loss_rate（D8 主列），分母 0 → None；不钳位（B2 拍板原值展示）
        fl_rate_denom = Decimal(units_sold) + Decimal(flc_qty)
        full_loss_rate: Decimal | None = None
        if fl_rate_denom > 0:
            full_loss_rate = Decimal(full_loss_qty) / fl_rate_denom

        cancel_rate_usd = None
        if (order_count + cancelled_orders) > 0:
            cancel_rate_usd = Decimal(cancelled_orders) / Decimal(
                order_count + cancelled_orders
            )
        refund_rate_qty_usd = None
        if order_count > 0:
            # 退货订单数：白名单订单中有完结 case 的（SAME 行内口径）
            refund_rate_qty_usd = Decimal(
                _row_int(rs["refund_order_count"]) if rs else 0
            ) / Decimal(order_count)

        plain.append(
            {
                "spu_pk": pk,
                "spu_id": cat["spu_id"],
                "title": cat["title"],
                "status": cat["status"],
                "main_image_url": cat["main_image_url"],
                "shop_id": cat["shop_id"],
                "shop_name": cat["shop_name"],
                "ad_count": ad_count,
                "ad_orders": ad_orders,
                "spend": spend,
                "gmv_ad": gmv_ad,
                "roi_l0": roi_l0_usd,
                "ad_first_day": ad_first_day,
                "ad_last_day": ad_last_day,
                "order_count": order_count,
                "cancelled_order_count": cancelled_orders,
                "units_sold": units_sold,
                "sales": sales_usd,
                "gmv_sales": gmv_sales_usd,
                "cancel_rate": cancel_rate_usd,
                "refund_rate_qty": refund_rate_qty_usd,
                "refund_only_qty": refund_only_qty,
                "refund_only_amount": refund_only_usd,
                "refund_return_qty": refund_return_qty,
                "refund_return_amount": refund_return_usd,
                "refund_net_qty": refund_only_qty + refund_return_qty,
                "refund_net_amount": refund_net_usd,
                "refund_rate": refund_rate_usd,
                "refund_cancelled_qty": refund_cancelled_qty,
                "refund_cancelled_amount": refund_cancelled_usd,
                "refund_cancelled_missing_lines": refund_cancelled_missing,
                "return_loss": return_loss_usd,
                "net_profit": net_profit_usd,
                "platform_fee": platform_fee_usd,
                "roi_real": roi_real_usd,
                "roi_breakeven": roi_breakeven_usd,
                "cpa": cpa_usd,
                "unit_cost_used": unit_cost_usd,
                "cost_source": cost_source,
                # v7 新增字段（§4）
                "net_revenue": net_revenue_usd,
                "settled_net": settled_net_usd,
                "settled_sales": settled_sales_usd,
                "unsettled_sales": unsettled_sales_usd,
                "settled_order_count": settled_order_count,
                "full_loss_qty": full_loss_qty,
                "full_loss_cancelled_qty": flc_qty,
                "full_loss_rate": full_loss_rate,
            }
        )
        total_spend += spend
        total_net_revenue_usd += net_revenue_usd
        total_return_loss += return_loss_usd

    # 排序（None 沉底；按 spend 决胜可复现）
    def _key(r: dict) -> tuple[bool, Decimal, Decimal]:
        v = r[sort_field]
        if v is None:
            return (True, Decimal(0), Decimal(0))
        primary = v if ascending else -v
        tie = -r["spend"] if ascending else r["spend"]
        return (False, primary, tie)

    plain.sort(key=_key)

    # totals（§3.3：跨分页/当前筛选；行级 USD 服务端加总）
    money_total = {
        "spend": sum((r["spend"] for r in plain), Decimal(0)),
        "sales": sum((r["sales"] for r in plain), Decimal(0)),
        "refund_net_amount": sum((r["refund_net_amount"] for r in plain), Decimal(0)),
        "return_loss": sum((r["return_loss"] for r in plain), Decimal(0)),
        "net_profit": sum((r["net_profit"] for r in plain), Decimal(0)),
    }
    # 整体实际 ROI = ΣNC′ / Σspend（用 settle_net_vnd 累计对账；§5.4-4）
    overall_roi: str | None = None
    if total_spend != 0:
        # v7：overall = (Σnet_revenue − Σreturn_loss) / Σspend（全 USD）
        overall_nc_prime = total_net_revenue_usd - total_return_loss
        overall_roi = _fmt_ratio(overall_nc_prime / total_spend)

    spu_pks_visible = [r["spu_pk"] for r in plain]
    scope_row = None
    if spu_pks_visible:
        scope_row = (
            sess.execute(
                _SQL_ROI_ORDER_SCOPE,
                {
                    "paid_statuses": paid_statuses,
                    "pks": spu_pks_visible,
                    "ws": ws_dt,
                    "we": we_dt,
                },
            )
            .mappings()
            .first()
        )
    eff_orders = _row_int(scope_row["order_count"]) if scope_row else 0
    cancelled_orders_total = (
        _row_int(scope_row["cancelled_order_count"]) if scope_row else 0
    )
    gmv_total = Decimal(scope_row["gmv"]) / fx_usd_vnd if scope_row else Decimal(0)
    totals = {
        "row_count": len(plain),
        "order_count": eff_orders,
        "cancelled_order_count": cancelled_orders_total,
        "total_orders": eff_orders + cancelled_orders_total,
        "spend": _fmt_money(money_total["spend"]),
        "sales": _fmt_money(money_total["sales"]),
        "gmv": _fmt_money(gmv_total),
        "refund_net_amount": _fmt_money(money_total["refund_net_amount"]),
        "return_loss": _fmt_money(money_total["return_loss"]),
        "net_profit": _fmt_money(money_total["net_profit"]),
        "roi_real": overall_roi,
    }

    # meta（§4 v7）
    window_row = sess.execute(_SQL_ROI_WINDOW).mappings().first()
    data_window_row = (
        sess.execute(
            _SQL_ROI_DATA_WINDOW,
            {
                "paid_statuses": paid_statuses,
                "st0": st0,
                "st1": st1,
                "shop_pk": shop_pk,
            },
        )
        .mappings()
        .first()
    )
    unattributed = (
        sess.execute(
            _SQL_ROI_UNATTRIBUTED,
            {
                "st0": st0,
                "st1": st1,
                "paid_statuses": paid_statuses,
                "shop_pk": shop_pk,
            },
        )
        .mappings()
        .first()
    )

    override_rate = None if fee_rate is None else str(fee_rate)
    if w_start is None and w_end is None:
        window_note = (
            "ad=视图全窗口累计(供参考)；销售/退款=全历史(未裁剪,可传 w_start/w_end)；"
            "概览单量/GMV 按下单状态全量累计"
        )
    else:
        window_note = (
            "ad=视图全窗口累计(供参考)；销售/退款已裁剪:"
            f"{w_start.isoformat() if w_start else '不限'}"
            f" ~ {w_end.isoformat() if w_end else '不限'}(含 w_end 当日)；"
            "概览单量/GMV 按 COALESCE(paid_at, order_time) 裁剪"
        )

    meta = {
        "fx": {
            "usd_vnd": _fmt_money(fx_usd_vnd),
            "cny_usd": format(
                fx_cny_usd.quantize(_MONEY_Q, rounding=ROUND_HALF_UP), ".4f"
            ),
            "as_of": fx_as_of,
            "source": fx_source,
        },
        "cost_assumption": (
            "按 SPU 解析：人工标注采购成交价(MANUAL)优先，其次采购单成交价(PURCHASE)、"
            "1688 货源价(SOURCE_PRICE)；均未命中 → 默认 40 CNY/件 ≈ $5.95/件；"
            "DEFAULT_K1 行页面 ⚠ 可跳 manual-costs 补录"
        ),
        "fee": {
            "mode": "override" if override_rate is not None else "baseline",
            "rate": override_rate
            if override_rate is not None
            else str(FEE_RATE_BASELINE),
            "override": override_rate,
            "note": (
                "平台佣金=平台从销售额直接扣除的全部费用(抽佣/联盟/运费类)；"
                "v7 已结算=实到账(SETTLEMENT，已含扣费)；未结算=sales×r̂×(1−spu退款率)(D5)；"
                "M19 纯信息列，不进净利"
            ),
        },
        "window": {
            "first_day": window_row["first_day"].isoformat()
            if window_row["first_day"]
            else None,
            "last_day": window_row["last_day"].isoformat()
            if window_row["last_day"]
            else None,
            "coverage_first_day": (
                data_window_row["first_day"].isoformat()
                if data_window_row and data_window_row["first_day"]
                else None
            ),
            "coverage_last_day": (
                data_window_row["last_day"].isoformat()
                if data_window_row and data_window_row["last_day"]
                else None
            ),
            "note": window_note,
        },
        "unattributed_refund_lines": unattributed["n"] if unattributed else 0,
        "computed_at": datetime.now(UTC).isoformat(),
        "rubric_version": "v8",
        "currency": {
            "display": "USD",
            "native": {"ad": "USD", "sales_refund": "VND", "cost": "CNY"},
        },
    }

    # 分页切片 + 序列化（money/ratio 都按 4 位/2 位）
    page = plain[offset : offset + limit]
    items: list[dict] = []
    for r in page:
        items.append(
            {
                "spu_pk": r["spu_pk"],
                "spu_id": r["spu_id"],
                "title": r["title"],
                "status": r["status"],
                "main_image_url": r["main_image_url"],
                "shop_id": r["shop_id"],
                "shop_name": r["shop_name"],
                "ad_count": r["ad_count"],
                "ad_orders": r["ad_orders"],
                "spend": _fmt_money(r["spend"]),
                "gmv_ad": _fmt_money(r["gmv_ad"]),
                "roi_l0": _fmt_ratio(r["roi_l0"]),
                "ad_first_day": r["ad_first_day"].isoformat()
                if r["ad_first_day"]
                else None,
                "ad_last_day": r["ad_last_day"].isoformat()
                if r["ad_last_day"]
                else None,
                "order_count": r["order_count"],
                "cancelled_order_count": r["cancelled_order_count"],
                "units_sold": r["units_sold"],
                "sales": _fmt_money(r["sales"]),
                "gmv_sales": _fmt_money(r["gmv_sales"]),
                "cancel_rate": _fmt_ratio(r["cancel_rate"]),
                "refund_rate_qty": _fmt_ratio(r["refund_rate_qty"]),
                "refund_only_qty": r["refund_only_qty"],
                "refund_only_amount": _fmt_money(r["refund_only_amount"]),
                "refund_return_qty": r["refund_return_qty"],
                "refund_return_amount": _fmt_money(r["refund_return_amount"]),
                "refund_net_qty": r["refund_net_qty"],
                "refund_net_amount": _fmt_money(r["refund_net_amount"]),
                "refund_rate": _fmt_ratio(r["refund_rate"]),
                "refund_cancelled_qty": r["refund_cancelled_qty"],
                "refund_cancelled_amount": _fmt_money(r["refund_cancelled_amount"]),
                "refund_cancelled_missing_lines": r["refund_cancelled_missing_lines"],
                "return_loss": _fmt_money(r["return_loss"]),
                "net_profit": _fmt_money(r["net_profit"]),
                "platform_fee": _fmt_money(r["platform_fee"]),
                "roi_real": _fmt_ratio(r["roi_real"]),
                "roi_breakeven": _fmt_ratio(r["roi_breakeven"]),
                "cpa": _fmt_money(r["cpa"]),
                "unit_cost_used": _fmt_money(r["unit_cost_used"]),
                "cost_source": r["cost_source"],
                "net_revenue": _fmt_money(r["net_revenue"]),
                "settled_net": _fmt_money(r["settled_net"]),
                "settled_sales": _fmt_money(r["settled_sales"]),
                "unsettled_sales": _fmt_money(r["unsettled_sales"]),
                "settled_order_count": r["settled_order_count"],
                "full_loss_qty": r["full_loss_qty"],
                "full_loss_cancelled_qty": r["full_loss_cancelled_qty"],
                "full_loss_rate": _fmt_ratio(r["full_loss_rate"]),
            }
        )

    return {"items": items, "total": len(plain), "totals": totals, "meta": meta}


# ═════════════════════════════════════════════════════════════════════
# 钻取面板查询（§6.3）
# ═════════════════════════════════════════════════════════════════════


def _detail_orders(
    sess: Session, spu_pk: int, w_start: date | None, w_end: date | None
) -> dict:
    ws_dt, we_dt = _window_dates(w_start, w_end)
    rows = (
        sess.execute(
            _SQL_DETAIL_ORDERS,
            {
                "spu_pk": spu_pk,
                "ac": _TRACK_ACTION_CODE_OVERSEAS,
                "st0": _CASE_COMPLETED_STATUSES[0],
                "st1": _CASE_COMPLETED_STATUSES[1],
                "ws": ws_dt,
                "we": we_dt,
                "lim": _ORDERS_MAX,
            },
        )
        .mappings()
        .all()
    )
    truncated = len(rows) >= _ORDERS_MAX
    fx_cny_usd, fx_usd_vnd, _, _ = _resolve_fx_rates(sess)
    orders: list[dict] = []
    for r in rows:
        order_pk = int(r["order_pk"])
        line_gmv_usd = Decimal(r["line_gmv_vnd"]) / fx_usd_vnd
        settlement_vnd = (
            Decimal(r["settlement_vnd"]) if r["settlement_vnd"] is not None else None
        )
        # share_ratio = line_gmv / order_gmv（占整单 GMV 比例）
        share_ratio: Decimal | None = None
        # 取整单 GMV（仅这一行的 order_gmv）
        order_gmv_row = sess.execute(
            text(
                "SELECT coalesce(sum(quantity * unit_price), 0) AS g "
                "FROM commerce.sales_order_lines WHERE order_pk = :op"
            ),
            {"op": order_pk},
        ).scalar()
        order_gmv_vnd = Decimal(order_gmv_row or 0)
        if order_gmv_vnd > 0:
            share_ratio = (Decimal(r["line_gmv_vnd"]) / order_gmv_vnd).quantize(
                Decimal("0.0001"), rounding=ROUND_HALF_UP
            )
        # settled_net_share = SETTLEMENT × share_ratio（未结算 → null）
        settled_net_share: str | None = None
        if settlement_vnd is not None and share_ratio is not None:
            settled_net_share = _fmt_money((settlement_vnd * share_ratio) / fx_usd_vnd)

        arrived_overseas = bool(r["arrived_overseas"])
        has_completed_case = bool(r["has_completed_case"])
        is_cancelled = bool(r["is_cancelled"])
        full_loss = arrived_overseas and (has_completed_case or is_cancelled)

        # tracking 时间线（按 event_at 升序）
        tracking: list[dict] = []
        if r["shipment_pk"]:
            tk_rows = (
                sess.execute(
                    _SQL_DETAIL_TRACKING, {"shipment_pk": int(r["shipment_pk"])}
                )
                .mappings()
                .all()
            )
            for tk in tk_rows:
                tracking.append(
                    {
                        "action_code": _row_int(tk["action_code"]),
                        "desc": tk["description"],
                        "event_at": tk["event_at"].isoformat()
                        if tk["event_at"]
                        else None,
                    }
                )

        orders.append(
            {
                "order_id": r["order_id"],
                "status": r["status"],
                "qty": _row_int(r["qty"]),
                "line_gmv": _fmt_money(line_gmv_usd),
                "paid_at": r["paid_at"].isoformat() if r["paid_at"] else None,
                "is_settled": settlement_vnd is not None,
                "settled_net_share": settled_net_share,
                "arrived_overseas": arrived_overseas,
                "full_loss": full_loss,
                "shipment": (
                    {
                        "status": r["shipment_status"],
                        "tracking_number": r["shipment_tracking_number"],
                    }
                    if r["shipment_pk"]
                    else None
                ),
                "tracking": tracking,
            }
        )

    spu_id = sess.execute(
        text("SELECT spu_id FROM commerce.products_spu WHERE id = :pk"),
        {"pk": spu_pk},
    ).scalar()
    return {
        "spu_pk": spu_pk,
        "spu_id": spu_id,
        "window": {
            "w_start": w_start.isoformat() if w_start else None,
            "w_end": w_end.isoformat() if w_end else None,
        },
        "orders": orders,
        "meta": {
            "orders_truncated": truncated,
            "rubric_version": "v8",
            "computed_at": datetime.now(UTC).isoformat(),
        },
    }


def _detail_settlements(
    sess: Session, spu_pk: int, w_start: date | None, w_end: date | None
) -> dict:
    ws_dt, we_dt = _window_dates(w_start, w_end)
    rows = (
        sess.execute(
            _SQL_DETAIL_SETTLEMENTS,
            {"spu_pk": spu_pk},
        )
        .mappings()
        .all()
    )
    # 窗口过滤（按 statement_time；客户端期望窗口裁剪与主表同语义）
    if ws_dt or we_dt:
        rows = [
            r
            for r in rows
            if (
                ws_dt is None
                or (r["statement_time"] is not None and r["statement_time"] >= ws_dt)
            )
            and (
                we_dt is None
                or (r["statement_time"] is not None and r["statement_time"] < we_dt)
            )
        ]
    fx_cny_usd, fx_usd_vnd, _, _ = _resolve_fx_rates(sess)
    settlements: list[dict] = []
    for r in rows:
        order_pk = int(r["order_pk"])
        # share_ratio（SPU 行占整单 GMV 比例）
        order_gmv_row = sess.execute(
            text(
                "SELECT coalesce(sum(quantity * unit_price), 0) AS g "
                "FROM commerce.sales_order_lines WHERE order_pk = :op"
            ),
            {"op": order_pk},
        ).scalar()
        order_gmv_vnd = Decimal(order_gmv_row or 0)
        # 该 SPU 行 GMV
        spu_line_gmv_row = sess.execute(
            text(
                "SELECT coalesce(sum(quantity * unit_price), 0) AS g "
                "FROM commerce.sales_order_lines WHERE order_pk = :op AND spu_pk = :sp"
            ),
            {"op": order_pk, "sp": spu_pk},
        ).scalar()
        spu_line_gmv_vnd = Decimal(spu_line_gmv_row or 0)
        share_ratio: Decimal | None = None
        if order_gmv_vnd > 0:
            share_ratio = (spu_line_gmv_vnd / order_gmv_vnd).quantize(
                Decimal("0.0001"), rounding=ROUND_HALF_UP
            )
        comps = (
            sess.execute(_SQL_DETAIL_SETTLE_COMPONENTS, {"txn_pk": int(r["txn_pk"])})
            .mappings()
            .all()
        )
        components = [
            {
                "code": c["component_code"],
                "amount_vnd": _fmt_money(Decimal(c["amount"]) / 1),
                "amount": _fmt_money(Decimal(c["amount"]) / fx_usd_vnd),
            }
            for c in comps
        ]
        settlements.append(
            {
                "order_id": r["order_id"],
                "statement_time": (
                    r["statement_time"].isoformat() if r["statement_time"] else None
                ),
                "share_ratio": (
                    format(share_ratio, ".4f") if share_ratio is not None else None
                ),
                "components": components,
            }
        )
    return {
        "spu_pk": spu_pk,
        "settlements": settlements,
        "meta": {
            "rubric_version": "v8",
            "computed_at": datetime.now(UTC).isoformat(),
        },
    }


def _detail_cases(
    sess: Session, spu_pk: int, w_start: date | None, w_end: date | None
) -> dict:
    ws_dt, we_dt = _window_dates(w_start, w_end)
    rows = (
        sess.execute(
            _SQL_DETAIL_CASES,
            {
                "spu_pk": spu_pk,
                "st0": _CASE_COMPLETED_STATUSES[0],
                "st1": _CASE_COMPLETED_STATUSES[1],
                "ws": ws_dt,
                "we": we_dt,
            },
        )
        .mappings()
        .all()
    )
    fx_cny_usd, fx_usd_vnd, _, _ = _resolve_fx_rates(sess)
    cases: list[dict] = []
    for r in rows:
        refund_amount_usd = (
            Decimal(r["refund_amount"]) / fx_usd_vnd
            if r["refund_amount"] is not None
            else None
        )
        cases.append(
            {
                "case_id": r["case_id"],
                "order_id": r["order_id"],
                "type": r["case_type"],
                "status": r["status"],
                "refund_amount": _fmt_money(refund_amount_usd),
                "reason": (
                    f"{r['reason_code']}: {r['reason_text']}"
                    if r["reason_code"] or r["reason_text"]
                    else None
                ),
                "updated_at": (
                    r["updated_at_source"].isoformat()
                    if r["updated_at_source"]
                    else None
                ),
            }
        )
    return {
        "spu_pk": spu_pk,
        "cases": cases,
        "meta": {
            "rubric_version": "v8",
            "computed_at": datetime.now(UTC).isoformat(),
        },
    }


def _detail_ads(sess: Session, spu_pk: int) -> dict:
    rows = sess.execute(_SQL_DETAIL_ADS, {"spu_pk": spu_pk}).mappings().all()
    ads: list[dict] = []
    for r in rows:
        ads.append(
            {
                "campaign_id": r["campaign_id"],
                "spend": _fmt_money(Decimal(r["spend"])),
                "orders": _row_int(r["ad_orders"]),
                "first_day": r["first_day"].isoformat() if r["first_day"] else None,
                "last_day": r["last_day"].isoformat() if r["last_day"] else None,
            }
        )
    return {
        "spu_pk": spu_pk,
        "ads": ads,
        "meta": {
            "rubric_version": "v8",
            "computed_at": datetime.now(UTC).isoformat(),
            "note": "广告域全窗口累计，不随日期裁剪",
        },
    }


# ═════════════════════════════════════════════════════════════════════
# 路由注册（薄 handler；主表路由在原 analytics.py 这里只 re-export）
# ═════════════════════════════════════════════════════════════════════

# 主表路由前缀（与旧实现一致：/v2/analytics）
roi_router = APIRouter(prefix="/v2/analytics", tags=["analytics"])


@roi_router.get("/spu-roi")
def list_spu_roi(
    sess: Session = Depends(get_session),  # noqa: B008
    q: str | None = Query(default=None, max_length=200),
    sort: str = Query(default="roi_real"),  # 白名单在 handler 层校验
    order: str = Query(default="asc"),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    include_all: bool = Query(default=False),
    shop_pk: int | None = Query(default=None, ge=1),
    fee_rate: str | None = Query(default=None, max_length=20),
    w_start: date | None = Query(default=None),  # noqa: B008
    w_end: date | None = Query(default=None),  # noqa: B008
) -> dict:
    """SPU 实际 ROI 主表（每 SPU 一行）。readonly。

    v7（D1–D8）：净收入按已结算 SETTLEMENT + 未结算 ×(1−r̂)×(1−退款率) 分层；
    成本走四层链（MANUAL→PURCHASE→SOURCE_PRICE→DEFAULT 40 CNY）；
    M13b 切 38301 全损口径；M19 缩为信息列；新增 full_loss_rate 主列。
    端点 sort 默认值保持 "roi_real" 不变（D8 不动 API 契约）。
    """
    if sort not in _ROI_SORT_FIELDS:
        raise HTTPException(
            status_code=422,
            detail=f"sort must be one of {_ROI_SORT_FIELDS}",
        )
    fee_value = _parse_fee_rate(fee_rate)
    if w_start is not None and w_end is not None and w_start > w_end:
        raise HTTPException(status_code=422, detail="w_start must be <= w_end")
    return _query_spu_roi(
        sess,
        q=q or None,
        shop_pk=shop_pk,
        include_all=include_all,
        sort_field=sort,
        ascending=(order != "desc"),
        limit=limit,
        offset=offset,
        fee_rate=fee_value,
        w_start=w_start,
        w_end=w_end,
    )


# 钻取面板路由（/v2/analytics/spu-roi/{spu_pk}/...）
drilldown_router = APIRouter(prefix="/v2/analytics/spu-roi", tags=["analytics"])


def _check_spu_or_404(sess: Session, spu_pk: int) -> None:
    if not _spu_pk_exists(sess, spu_pk):
        raise HTTPException(status_code=404, detail=f"spu_pk {spu_pk} not found")


@drilldown_router.get("/{spu_pk:int}/orders")
def list_orders(
    spu_pk: int,
    sess: Session = Depends(get_session),  # noqa: B008
    w_start: date | None = Query(default=None),  # noqa: B008
    w_end: date | None = Query(default=None),  # noqa: B008
) -> dict:
    _check_spu_or_404(sess, spu_pk)
    return _detail_orders(sess, spu_pk, w_start, w_end)


@drilldown_router.get("/{spu_pk:int}/settlements")
def list_settlements(
    spu_pk: int,
    sess: Session = Depends(get_session),  # noqa: B008
    w_start: date | None = Query(default=None),  # noqa: B008
    w_end: date | None = Query(default=None),  # noqa: B008
) -> dict:
    _check_spu_or_404(sess, spu_pk)
    return _detail_settlements(sess, spu_pk, w_start, w_end)


@drilldown_router.get("/{spu_pk:int}/cases")
def list_cases(
    spu_pk: int,
    sess: Session = Depends(get_session),  # noqa: B008
    w_start: date | None = Query(default=None),  # noqa: B008
    w_end: date | None = Query(default=None),  # noqa: B008
) -> dict:
    _check_spu_or_404(sess, spu_pk)
    return _detail_cases(sess, spu_pk, w_start, w_end)


@drilldown_router.get("/{spu_pk:int}/ads")
def list_ads(
    spu_pk: int,
    sess: Session = Depends(get_session),  # noqa: B008
) -> dict:
    _check_spu_or_404(sess, spu_pk)
    return _detail_ads(sess, spu_pk)
