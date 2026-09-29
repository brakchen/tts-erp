"""Private PostgreSQL implementation for the v10 SPU profitability module.

This file owns fact queries, cost resolution, normalization, global order-level
deduplication, and evidence reads.  The pure business formula lives in
``_formula_v10.py``; request-level snapshot ownership lives in ``_snapshot.py``.
HTTP routes and wire formatting are intentionally absent.

The stable ``/v2/analytics/spu-roi`` name remains only in the adapter because it
is an existing client contract.  The canonical business rubric is
``biz-doc/analytics/spu-roi-profit-calculation.md`` v10.
"""

from __future__ import annotations

import logging
from datetime import UTC, date, datetime, time, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from tts_erp_v2.analytics.spu_profitability._formula_v10 import (
    FormulaInput,
    calculate,
    calculate_order_metrics,
)
from tts_erp_v2.analytics.spu_profitability._types import (
    FormulaStatus,
    FxBasis,
    FxRateUnavailable,
    ProfitabilityBasis,
    ProfitabilityOverview,
    ProfitabilityTotals,
    SpuProfitability,
)
from tts_erp_v2.db.constants import PAID_SALES_ORDER_STATUSES
from tts_erp_v2.fx.rates import load_rate_map

log = logging.getLogger(__name__)

# ═════════════════════════════════════════════════════════════════════
# §3.1 模块抽取：常量（口径见 dashboard §4 + D9/D10 实测重定）
# ═════════════════════════════════════════════════════════════════════

# K1 默认 = 40 CNY/件（D1 拍板：原 K1=30 作废；≈ $5.95/件 @0.148823）
K1_DEFAULT_CNY = Decimal(40)
# 平台佣金基线 r̂（dashboard D10 2026-09-06 实测重定）
FEE_RATE_BASELINE = Decimal("0.308")

_RATE_Q8 = Decimal("0.00000001")

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

# 主表 SQL ── 广告域（v8.1：随日期切片 + 单源 ad_daily）
# 2026-09-15 之前从 plugin.ad_daily ∪ ad_today 全窗口累计（v7），ROI 分母恒定。
# v8.1 修 v8 选错源问题：v8 误读 ad_today（已被遗弃的临时表，仅 09-13+ 2 天），
# 切到 ad_daily 才是当前生产主源（覆盖 07-10 ~ 09-13，45 天 × 17k+ 行）。
#   - 只读 plugin.ad_daily；ad_today 调为未来 merge job 重新启用后的回填目标，
#     不进 SQL 取数路径。
#   - 按 day BETWEEN :ws AND :we 裁剪（与销售/退款同语义）；:ws/:we 任一为 NULL
#     时该侧条件短路（沿用 spu_roi 既有可空窗口约定）。
# 已知数据窗口缺口：merge job 2026-09-13 禁用后 ad_daily 未增——09-14+ 选
# 日期范围 ad 消耗仍为 0。需重启用 merge job 或迁移 Chrome 扩展写入
# 路径以填实 09-14+（不在本 lane；v8 §10 follow-up 提级为 P0）。
_SQL_ROI_AD = text(
    """
    SELECT spu_pk,
           count(DISTINCT campaign_id)::int          AS ad_count,
           coalesce(sum(mixed_real_cost), 0)         AS spend,
           coalesce(sum(onsite_roi2_shopping_value), 0) AS gmv_ad,
           coalesce(sum(onsite_roi2_shopping_sku), 0)::bigint AS ad_orders,
           min(day)                                  AS ad_first_day,
           max(day)                                  AS ad_last_day
    FROM (
        SELECT d.campaign_id, d.product_id, d.day,
               d.mixed_real_cost, d.onsite_roi2_shopping_sku,
               d.onsite_roi2_shopping_value,
               cp.id AS spu_pk
        FROM plugin.ad_daily d
        LEFT JOIN commerce.shops ca ON ca.platform = 'tiktok' AND ca.shop_id = d.seller_id
        LEFT JOIN commerce.products_spu cp ON cp.shop_pk = ca.id AND cp.spu_id = d.product_id
        WHERE d.endpoint = '/oec_ads/shopping/v1/oec/stat/post_product_list'
          AND (CAST(:ws AS timestamptz) IS NULL
               OR d.day >= CAST(:ws AS timestamptz)::date)
          AND (CAST(:we AS timestamptz) IS NULL
               OR d.day <  CAST(:we AS timestamptz)::date)
    ) combined
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
    order_gmv AS (
        SELECT order_pk, SUM(quantity * unit_price) AS order_gmv_vnd
        FROM commerce.sales_order_lines
        GROUP BY order_pk
    ),
    lines AS (
        SELECT sl.spu_pk,
               sl.order_pk,
               sl.quantity,
               sl.quantity * sl.unit_price AS line_gmv_vnd,
               og.order_gmv_vnd,
               os.settlement_vnd,
               (coalesce(so.paid_at, so.order_time)
                AT TIME ZONE 'UTC')::date AS event_day
        FROM commerce.sales_order_lines sl
        JOIN commerce.sales_orders so ON so.id = sl.order_pk
        JOIN order_gmv og ON og.order_pk = sl.order_pk
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

# 主表 SQL ── 全损件数（v9 口径：全损 = 退货 + 海外取消；国内取消 ≠ 全损）
#   退货桶：RETURN_AND_REFUND / REFUND_ONLY 已完结（不论物流是否到海外，
#     rubric v9「退货 = 直接全损」），件数取 case_lines.quantity；
#     窗口跟随原订单 coalesce(paid_at, order_time)，跨日退款回归订单日；
#     限定已付白名单订单 —— 异常单(UNPAID 等)退款仍按 §4.2 rule 0 进未归属
#   海外取消桶：CANCELLED + tracking_events.action_code=38301（已到目的国），
#     件数取行 quantity；窗口同样按订单 coalesce(paid_at, order_time)
_SQL_ROI_FULL_LOSS = text(
    """
    WITH buckets AS (
        SELECT sl.spu_pk AS spu_pk,
               cl.quantity AS qty,
               0::numeric AS cancel_qty
        FROM after_sales.cases c
        JOIN after_sales.case_lines cl ON cl.case_id = c.id
        JOIN commerce.sales_order_lines sl ON sl.id = cl.sales_order_line_id
        JOIN commerce.sales_orders so ON so.id = c.order_pk
        WHERE c.case_type IN ('RETURN_AND_REFUND', 'REFUND_ONLY')
          AND c.status = :st_return
          AND sl.spu_pk IS NOT NULL
          AND so.status = ANY(CAST(:paid_statuses AS text[]))
          AND (CAST(:ws AS timestamptz) IS NULL
               OR coalesce(so.paid_at, so.order_time) >= CAST(:ws AS timestamptz))
          AND (CAST(:we AS timestamptz) IS NULL
               OR coalesce(so.paid_at, so.order_time) <  CAST(:we AS timestamptz))
        UNION ALL
        SELECT sl.spu_pk AS spu_pk,
               sl.quantity AS qty,
               sl.quantity AS cancel_qty
        FROM commerce.sales_order_lines sl
        JOIN commerce.sales_orders so ON so.id = sl.order_pk
        WHERE sl.spu_pk IS NOT NULL
          AND so.status = 'CANCELLED'
          AND EXISTS (SELECT 1 FROM fulfillment.shipments sh
                      JOIN fulfillment.tracking_events te
                        ON te.shipment_id = sh.id AND te.action_code = :ac
                      WHERE sh.order_pk = so.id)
          AND (CAST(:ws AS timestamptz) IS NULL
               OR coalesce(so.paid_at, so.order_time) >= CAST(:ws AS timestamptz))
          AND (CAST(:we AS timestamptz) IS NULL
               OR coalesce(so.paid_at, so.order_time) <  CAST(:we AS timestamptz))
    )
    SELECT spu_pk,
           sum(qty)         AS full_loss_qty,
           sum(cancel_qty)  AS full_loss_cancelled_qty
    FROM buckets
    GROUP BY spu_pk
    """
)

# 主表 SQL ── 行级取消/退货订单统计（v9：取消拆国内/海外两桶，
# 海外取消(CANCELLED∧38301)与全损重叠 → 取消率只计国内取消）
_SQL_ROI_ROW_STATUS = text(
    """
    SELECT sl.spu_pk,
           count(DISTINCT so.id) FILTER (
               WHERE so.status = 'CANCELLED')                              AS cancelled_order_count,
           count(DISTINCT so.id) FILTER (
               WHERE so.status = 'CANCELLED'
                 AND NOT EXISTS (SELECT 1 FROM fulfillment.shipments sh
                                 JOIN fulfillment.tracking_events te
                                   ON te.shipment_id = sh.id AND te.action_code = :ac
                                 WHERE sh.order_pk = so.id))                AS domestic_cancelled_order_count,
           count(DISTINCT so.id) FILTER (
               WHERE so.status = 'CANCELLED'
                 AND EXISTS (SELECT 1 FROM fulfillment.shipments sh
                             JOIN fulfillment.tracking_events te
                               ON te.shipment_id = sh.id AND te.action_code = :ac
                             WHERE sh.order_pk = so.id))                    AS overseas_cancelled_order_count,
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

# 主表 SQL ── 退款拆分（窗口跟随原订单，退款发生时间只作明细展示）
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
           OR coalesce(so.paid_at, so.order_time) >= CAST(:ws AS timestamptz))
      AND (CAST(:we AS timestamptz) IS NULL
           OR coalesce(so.paid_at, so.order_time) <  CAST(:we AS timestamptz))
    GROUP BY sl.spu_pk
    """
)

# 主表 SQL ── 跨 SPU 范围退款订单数（全局 distinct refunded orders）
_SQL_ROI_REFUND_SCOPE = text(
    """
    SELECT count(DISTINCT so.id)::int AS refund_order_count
    FROM commerce.sales_order_lines sl
    JOIN commerce.sales_orders so ON so.id = sl.order_pk
    JOIN after_sales.case_lines cl ON cl.sales_order_line_id = sl.id
    JOIN after_sales.cases c ON c.id = cl.case_id
    WHERE sl.spu_pk = ANY(CAST(:pks AS bigint[]))
      AND so.status = ANY(CAST(:paid_statuses AS text[]))
      AND c.status IN (:st0, :st1)
      AND c.case_type IN ('REFUND_ONLY', 'RETURN_AND_REFUND')
      /* 退款跟随原订单归属：跨日售后仍回到订单时间窗口。 */
      AND (CAST(:ws AS timestamptz) IS NULL
           OR coalesce(so.paid_at, so.order_time) >= CAST(:ws AS timestamptz))
      AND (CAST(:we AS timestamptz) IS NULL
           OR coalesce(so.paid_at, so.order_time) <  CAST(:we AS timestamptz))
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
      count(DISTINCT so.id) FILTER (
          WHERE so.status = 'CANCELLED'
            AND NOT EXISTS (SELECT 1 FROM fulfillment.shipments sh
                            JOIN fulfillment.tracking_events te
                              ON te.shipment_id = sh.id AND te.action_code = :ac
                            WHERE sh.order_pk = so.id))                  AS domestic_cancelled_order_count,
      count(DISTINCT so.id) FILTER (
          WHERE so.status = 'CANCELLED'
            AND EXISTS (SELECT 1 FROM fulfillment.shipments sh
                        JOIN fulfillment.tracking_events te
                          ON te.shipment_id = sh.id AND te.action_code = :ac
                        WHERE sh.order_pk = so.id))                      AS overseas_cancelled_order_count,
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
    "SELECT min(day) AS first_day, max(day) AS last_day "
    "FROM plugin.ad_daily "
    "WHERE endpoint = '/oec_ads/shopping/v1/oec/stat/post_product_list' "
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
        SELECT (coalesce(so.paid_at, so.order_time) AT TIME ZONE 'UTC')::date AS d
        FROM after_sales.cases c
        JOIN commerce.sales_orders so ON so.id = c.order_pk
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
                   JOIN after_sales.case_lines cl ON cl.case_id = c.id
                   WHERE c.order_pk = so.id
                     AND cl.sales_order_line_id = sl.id
                     AND c.status = :st_return
                     AND c.case_type IN ('RETURN_AND_REFUND', 'REFUND_ONLY')
                  ) AS has_completed_return_case,
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
      AND (CAST(:ws AS timestamptz) IS NULL
           OR coalesce(so.paid_at, so.order_time) >= CAST(:ws AS timestamptz))
      AND (CAST(:we AS timestamptz) IS NULL
           OR coalesce(so.paid_at, so.order_time) < CAST(:we AS timestamptz))
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
           OR coalesce(so.paid_at, so.order_time) >= CAST(:ws AS timestamptz))
      AND (CAST(:we AS timestamptz) IS NULL
           OR coalesce(so.paid_at, so.order_time) <  CAST(:we AS timestamptz))
    ORDER BY c.updated_at_source DESC NULLS LAST, c.id DESC
    """
)

# /ads — campaign × SPU（v8：随日期切片 + 单源 ad_today）
# 主表 _SQL_ROI_AD 同语义；空窗口时按 NULL 短路；不传 :ws/:we 仍走全历史。
_SQL_DETAIL_ADS = text(
    """
    SELECT campaign_id,
           sum(mixed_real_cost)        AS spend,
           sum(onsite_roi2_shopping_sku)::bigint AS ad_orders,
           min(day)                     AS first_day,
           max(day)                     AS last_day
    FROM (
        SELECT campaign_id, product_id, day,
               mixed_real_cost, onsite_roi2_shopping_sku
        FROM plugin.ad_daily
        WHERE endpoint = '/oec_ads/shopping/v1/oec/stat/post_product_list'
          AND (CAST(:ws AS timestamptz) IS NULL
               OR day >= CAST(:ws AS timestamptz)::date)
          AND (CAST(:we AS timestamptz) IS NULL
               OR day <  CAST(:we AS timestamptz)::date)
    ) combined
    WHERE product_id IN (
        SELECT cp.spu_id FROM commerce.products_spu cp WHERE cp.id = :spu_pk
    )
    GROUP BY campaign_id
    ORDER BY max(day) DESC NULLS LAST, campaign_id
    """
)


# ═════════════════════════════════════════════════════════════════════
# 工具函数
# ═════════════════════════════════════════════════════════════════════


def _resolve_fx_basis(sess: Session) -> FxBasis:
    """Load the mandatory database FX snapshot used by one calculation."""
    rm = load_rate_map(sess, base_code="USD")
    if rm is None or not {"VND", "CNY"} <= set(rm.rates):
        raise FxRateUnavailable(
            "USD exchange-rate snapshot must contain both VND and CNY"
        )
    cny_rate = rm.rates["CNY"]
    vnd_rate = rm.rates["VND"]
    if (
        not cny_rate.is_finite()
        or not vnd_rate.is_finite()
        or cny_rate <= 0
        or vnd_rate <= 0
    ):
        raise FxRateUnavailable(
            "USD exchange-rate snapshot must contain finite positive rates"
        )
    cny_usd = (Decimal(1) / cny_rate).quantize(
        _RATE_Q8, rounding=ROUND_HALF_UP
    )
    return FxBasis(
        snapshot_id=rm.snapshot_id,
        usd_cny=cny_rate,
        cny_usd=cny_usd,
        usd_vnd=vnd_rate,
        as_of=rm.upstream_last_update,
    )


def _resolve_conversion_rates(sess: Session) -> tuple[Decimal, Decimal]:
    """Return exact USD→CNY and VND-per-CNY rates from one snapshot."""
    basis = _resolve_fx_basis(sess)
    return basis.usd_cny, basis.usd_vnd / basis.usd_cny


def _row_int(value: Any) -> int:
    try:
        return int(value) if value is not None else 0
    except (TypeError, ValueError):
        return 0


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
        out[int(r["spu_pk"])] = (
            Decimal(r["unit_cost"]),
            "MANUAL",
        )  # pi-lens-ignore: no-try-except

    # L2: SOURCE_PRICE（1688 货源价，direct + via offer 两条路径）
    missing = [pk for pk in spu_pks if pk not in out]
    if missing:
        rows = sess.execute(_SQL_COST_SOURCE_DIRECT, {"pks": missing}).mappings().all()
        for r in rows:
            out[int(r["spu_pk"])] = (
                Decimal(r["unit_cost"]),
                "SOURCE_PRICE",
            )  # pi-lens-ignore: no-try-except
        still_missing = [pk for pk in missing if pk not in out]
        if still_missing:
            rows = (
                sess.execute(_SQL_COST_SOURCE_VIA_OFFER, {"pks": still_missing})
                .mappings()
                .all()
            )
            for r in rows:
                out[int(r["spu_pk"])] = (
                    Decimal(r["unit_cost"]),
                    "SOURCE_PRICE",
                )  # pi-lens-ignore: no-try-except

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
    active_only: bool,
    include_without_activity: bool,
    sort_field: str,
    ascending: bool,
    limit: int,
    offset: int,
    fee_rate: Decimal | None,
    calculated_at: datetime,
    only_spu_pk: int | None = None,
    w_start: date | None = None,
    w_end: date | None = None,
) -> ProfitabilityOverview:
    rate = fee_rate if fee_rate is not None else FEE_RATE_BASELINE
    fx_basis = _resolve_fx_basis(sess)
    fx_usd_cny = fx_basis.usd_cny
    fx_usd_vnd = fx_basis.usd_vnd
    ws_dt, we_dt = _window_dates(w_start, w_end)
    paid_statuses = list(PAID_SALES_ORDER_STATUSES)
    st0, st1 = _CASE_COMPLETED_STATUSES

    # Legacy callers may omit shop_pk and historically used q as their only
    # bounded scope.  Once a shop is present (the production page contract), q
    # is presentation-only and must not alter the profitability overview.
    catalog_q = q if shop_pk is None else None
    cats = (
        sess.execute(
            _SQL_ROI_CATALOG,
            {"shop_pk": shop_pk, "q": catalog_q, "active_only": active_only},
        )
        .mappings()
        .all()
    )
    if only_spu_pk is not None:
        cats = [cat for cat in cats if int(cat["spu_pk"]) == only_spu_pk]

    ad_rows = (
        sess.execute(
            _SQL_ROI_AD,
            {"ws": ws_dt, "we": we_dt},
        )
        .mappings()
        .all()
    )
    ad_map = {
        int(r["spu_pk"]): r for r in ad_rows if r["spu_pk"] is not None
    }  # pi-lens-ignore: no-try-except

    sales_rows = (
        sess.execute(
            _SQL_ROI_SALES,
            {"paid_statuses": paid_statuses, "ws": ws_dt, "we": we_dt},
        )
        .mappings()
        .all()
    )
    sales_map = {
        int(r["spu_pk"]): r for r in sales_rows if r["spu_pk"] is not None
    }  # pi-lens-ignore: no-try-except

    fl_rows = (
        sess.execute(
            _SQL_ROI_FULL_LOSS,
            {
                "paid_statuses": paid_statuses,
                "ac": _TRACK_ACTION_CODE_OVERSEAS,
                "st_return": _CASE_COMPLETED_STATUSES[1],
                "ws": ws_dt,
                "we": we_dt,
            },
        )
        .mappings()
        .all()
    )
    fl_map = {
        int(r["spu_pk"]): r for r in fl_rows if r["spu_pk"] is not None
    }  # pi-lens-ignore: no-try-except

    rs_rows = (
        sess.execute(
            _SQL_ROI_ROW_STATUS,
            {
                "paid_statuses": paid_statuses,
                "st0": st0,
                "st1": st1,
                "ac": _TRACK_ACTION_CODE_OVERSEAS,
                "ws": ws_dt,
                "we": we_dt,
            },
        )
        .mappings()
        .all()
    )
    rs_map = {
        int(r["spu_pk"]): r for r in rs_rows if r["spu_pk"] is not None
    }  # pi-lens-ignore: no-try-except

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
    refund_map = {
        int(r["spu_pk"]): r for r in refund_rows if r["spu_pk"] is not None
    }  # pi-lens-ignore: no-try-except

    # 成本链批量解析（D1）
    spu_pks_all = [int(c["spu_pk"]) for c in cats]  # pi-lens-ignore: no-try-except
    cost_map = _resolve_costs_batch(sess, spu_pks_all)

    plain: list[dict] = []
    total_spend = Decimal(0)
    total_net_revenue_cny = Decimal(0)
    total_return_loss = Decimal(0)

    for cat in cats:
        pk = int(cat["spu_pk"])
        ad = ad_map.get(pk)
        sales = sales_map.get(pk)
        fl = fl_map.get(pk)
        if (
            not include_without_activity
            and ad is None
            and sales is None
            and refund_map.get(pk) is None
        ):
            continue

        spend_usd = Decimal(ad["spend"]) if ad else Decimal(0)
        gmv_ad_usd = Decimal(ad["gmv_ad"]) if ad else Decimal(0)
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
        domestic_cancelled_orders = (
            _row_int(rs["domestic_cancelled_order_count"]) if rs else 0
        )
        overseas_cancelled_orders = (
            _row_int(rs["overseas_cancelled_order_count"]) if rs else 0
        )
        refund_order_count = _row_int(rs["refund_order_count"]) if rs else 0
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
        # 成本解析（D1 全链）与纯 v10 公式。
        unit_cost_cny, cost_source = cost_map.get(pk, (K1_DEFAULT_CNY, "DEFAULT_K1"))
        formula = calculate(
            FormulaInput(
                spend_usd=spend_usd,
                ad_gmv_usd=gmv_ad_usd,
                ad_orders=ad_orders,
                order_count=order_count,
                cancelled_orders=cancelled_orders,
                domestic_cancelled_orders=domestic_cancelled_orders,
                overseas_cancelled_orders=overseas_cancelled_orders,
                units_sold=units_sold,
                full_loss_cancelled_qty=flc_qty,
                full_loss_qty=full_loss_qty,
                refund_order_count=refund_order_count,
                refund_only_qty=refund_only_qty,
                refund_return_qty=refund_return_qty,
                sales_vnd=sales_vnd,
                settled_net_vnd=settled_net_vnd,
                settled_sales_vnd=settled_sales_vnd,
                unsettled_sales_vnd=unsettled_sales_vnd,
                refund_only_vnd=refund_only_vnd,
                refund_return_vnd=refund_return_vnd,
                refund_cancelled_vnd=refund_cancelled_vnd,
                cancelled_sales_vnd=cancelled_sales_vnd,
                unit_cost_cny=unit_cost_cny,
                usd_cny=fx_usd_cny,
                usd_vnd=fx_usd_vnd,
                unsettled_fee_rate=rate,
            )
        )
        spend_cny = formula.spend_cny
        gmv_ad_cny = formula.ad_gmv_cny
        unit_cost_cny = formula.unit_cost_cny
        sales_cny = formula.sales_cny
        effective_sales_cny = formula.effective_sales_cny
        settled_net_cny = formula.settled_net_cny
        settled_sales_cny = formula.settled_sales_cny
        unsettled_sales_cny = formula.unsettled_sales_cny
        refund_only_cny = formula.refund_only_cny
        refund_return_cny = formula.refund_return_cny
        refund_net_cny = formula.refund_net_cny
        refund_cancelled_cny = formula.refund_cancelled_cny
        net_revenue_cny = formula.net_revenue_cny
        platform_fee_cny = formula.platform_fee_cny
        return_loss_cny = formula.return_loss_cny
        net_profit_cny = formula.net_profit_cny
        roi_real = formula.roi_real
        roi_breakeven = formula.roi_breakeven
        ad_system_actual_roi = formula.ad_system_actual_roi
        ad_system_breakeven_roi = formula.ad_system_breakeven_roi
        ad_system_max_ad_spend = formula.ad_system_max_ad_spend_cny
        ad_system_remaining_ad_spend_capacity = (
            formula.ad_system_remaining_ad_spend_capacity_cny
        )
        cpa_cny = formula.cpa_cny
        roi_l0 = formula.roi_l0
        refund_rate = formula.refund_rate
        refund_amount_rate = formula.refund_amount_rate
        gmv_sales_cny = formula.gmv_sales_cny
        full_loss_rate = formula.full_loss_rate
        full_loss_qty_rate = formula.full_loss_qty_rate
        cancel_rate = formula.cancel_rate
        refund_rate_qty = formula.refund_rate_qty

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
                "spend": spend_cny,
                "gmv_ad": gmv_ad_cny,
                "roi_l0": roi_l0,
                "ad_first_day": ad_first_day,
                "ad_last_day": ad_last_day,
                "order_count": order_count,
                "cancelled_order_count": cancelled_orders,
                "total_orders": formula.total_orders,
                "effective_order_count": formula.effective_order_count,
                "refund_order_count": formula.refund_order_count,
                "full_loss_order_count": formula.full_loss_order_count,
                "domestic_cancelled_order_count": domestic_cancelled_orders,
                "overseas_cancelled_order_count": overseas_cancelled_orders,
                "units_sold": units_sold,
                "sales": sales_cny,
                "effective_sales": effective_sales_cny,
                "gmv_sales": gmv_sales_cny,
                "cancel_rate": cancel_rate,
                "refund_rate_qty": refund_rate_qty,
                "refund_only_qty": refund_only_qty,
                "refund_only_amount": refund_only_cny,
                "refund_return_qty": refund_return_qty,
                "refund_return_amount": refund_return_cny,
                "refund_net_qty": refund_only_qty + refund_return_qty,
                "refund_net_amount": refund_net_cny,
                "refund_rate": refund_rate,
                "refund_amount_rate": refund_amount_rate,
                "refund_cancelled_qty": refund_cancelled_qty,
                "refund_cancelled_amount": refund_cancelled_cny,
                "refund_cancelled_missing_lines": refund_cancelled_missing,
                "return_loss": return_loss_cny,
                "net_profit": net_profit_cny,
                "platform_fee": platform_fee_cny,
                "roi_real": roi_real,
                "roi_breakeven": roi_breakeven,
                "cpa": cpa_cny,
                "unit_cost_used": unit_cost_cny,
                "cost_source": cost_source,
                # v7 新增字段（§4）
                "net_revenue": net_revenue_cny,
                "settled_net": settled_net_cny,
                "unsettled_net": formula.unsettled_net_cny,
                "settled_sales": settled_sales_cny,
                "unsettled_sales": unsettled_sales_cny,
                "cogs_sold": formula.cogs_sold_cny,
                "cogs_full_loss_cancelled": formula.cogs_full_loss_cancelled_cny,
                "cogs_total": formula.cogs_all_cny,
                "settled_order_count": settled_order_count,
                "full_loss_qty": full_loss_qty,
                "full_loss_cancelled_qty": flc_qty,
                "full_loss_rate": full_loss_rate,
                "full_loss_qty_rate": full_loss_qty_rate,
                "ad_system_actual_roi": ad_system_actual_roi,
                "ad_system_breakeven_roi": ad_system_breakeven_roi,
                "ad_system_max_ad_spend": ad_system_max_ad_spend,
                "ad_system_remaining_ad_spend_capacity": (
                    ad_system_remaining_ad_spend_capacity
                ),
                "ad_system_breakeven_roi_status": (
                    FormulaStatus.ESTIMATED_KNOWN_COSTS
                ),
            }
        )
        total_spend += spend_cny
        total_net_revenue_cny += net_revenue_cny
        total_return_loss += return_loss_cny

    # 盈利大盘按完整 scope 计算；搜索只改变明细行，不改变 totals。
    scope_plain = plain
    if q and shop_pk is not None:
        needle = q.casefold()
        plain = [
            row
            for row in scope_plain
            if needle in str(row["spu_id"] or "").casefold()
            or needle in str(row["title"] or "").casefold()
        ]

    # 排序（None 沉底；按 spend 决胜可复现）
    def _key(r: dict) -> tuple[bool, Decimal, Decimal]:
        v = r[sort_field]
        if v is None:
            return (True, Decimal(0), Decimal(0))
        primary = v if ascending else -v
        tie = -r["spend"] if ascending else r["spend"]
        return (False, primary, tie)

    plain.sort(key=_key)

    # totals（§3.3：跨分页/当前筛选；行级 CNY 服务端加总）
    money_total: dict[str, Decimal] = {
        "spend": sum((r["spend"] for r in scope_plain), Decimal(0)),
        "sales": sum((r["sales"] for r in scope_plain), Decimal(0)),
        "effective_sales": sum(
            (r["effective_sales"] for r in scope_plain), Decimal(0)
        ),
        "refund_net_amount": sum(
            (r["refund_net_amount"] for r in scope_plain), Decimal(0)
        ),
        "return_loss": sum((r["return_loss"] for r in scope_plain), Decimal(0)),
        "net_profit": sum((r["net_profit"] for r in scope_plain), Decimal(0)),
        "gmv_ad": sum((r["gmv_ad"] for r in scope_plain), Decimal(0)),
        "net_revenue": sum((r["net_revenue"] for r in scope_plain), Decimal(0)),
        "cogs_total": sum((r["cogs_total"] for r in scope_plain), Decimal(0)),
    }
    # 整体实际 ROI = ΣNC′ / Σspend（全 CNY）。领域层保留 Decimal。
    overall_roi: Decimal | None = None
    if total_spend != 0:
        overall_nc_prime = total_net_revenue_cny - total_return_loss
        overall_roi = overall_nc_prime / total_spend

    ad_system_actual_roi: Decimal | None = None
    if money_total["spend"] != 0:
        ad_system_actual_roi = money_total["gmv_ad"] / money_total["spend"]
    ad_system_max_ad_spend = (
        money_total["net_revenue"] - money_total["cogs_total"]
    )
    ad_system_remaining_ad_spend_capacity = (
        ad_system_max_ad_spend - money_total["spend"]
    )
    ad_system_breakeven_roi: Decimal | None = None
    if ad_system_max_ad_spend > 0 and money_total["gmv_ad"] > 0:
        ad_system_breakeven_roi = (
            money_total["gmv_ad"] / ad_system_max_ad_spend
        )

    spu_pks_in_scope = [r["spu_pk"] for r in scope_plain]
    scope_row = None
    if spu_pks_in_scope:
        scope_row = (
            sess.execute(
                _SQL_ROI_ORDER_SCOPE,
                {
                    "paid_statuses": paid_statuses,
                    "pks": spu_pks_in_scope,
                    "ac": _TRACK_ACTION_CODE_OVERSEAS,
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
    # 取消拆分：全局 distinct（不再 per-SPU 求和，避免跨 SPU 订单重复计数）
    total_domestic_cancelled = (
        _row_int(scope_row["domestic_cancelled_order_count"]) if scope_row else 0
    )
    total_overseas_cancelled = (
        _row_int(scope_row["overseas_cancelled_order_count"]) if scope_row else 0
    )
    vnd_per_cny = fx_usd_vnd / fx_usd_cny
    gmv_total = Decimal(scope_row["gmv"]) / vnd_per_cny if scope_row else Decimal(0)

    # 全局退款订单数（distinct orders with refund cases；窗口跟随原订单）
    refund_scope_row = None
    if spu_pks_in_scope:
        refund_scope_row = (
            sess.execute(
                _SQL_ROI_REFUND_SCOPE,
                {
                    "paid_statuses": paid_statuses,
                    "pks": spu_pks_in_scope,
                    "st0": st0,
                    "st1": st1,
                    "ws": ws_dt,
                    "we": we_dt,
                },
            )
            .mappings()
            .first()
        )
    refund_order_count_total = (
        _row_int(refund_scope_row["refund_order_count"]) if refund_scope_row else 0
    )

    # 从行级数据聚合：全损件数、COGS_kept（件数口径，用于 return_loss/roi）
    total_full_loss_qty = sum((r["full_loss_qty"] for r in scope_plain), 0)
    total_full_loss_cancelled_qty = sum(
        (r["full_loss_cancelled_qty"] for r in scope_plain), 0
    )
    total_cogs_kept = Decimal(0)
    for r in scope_plain:
        unit_cost = r["unit_cost_used"] or Decimal(0)
        total_cogs_kept += max(
            Decimal(0),
            (
                Decimal(r["units_sold"])
                - Decimal(r["refund_only_qty"])
                - Decimal(r["refund_return_qty"])
            )
            * unit_cost,
        )

    # 行级和大盘共用同一个 v10 订单漏斗公式；大盘只替换为全局去重事实。
    dashboard_orders = calculate_order_metrics(
        order_count=eff_orders,
        cancelled_orders=cancelled_orders_total,
        domestic_cancelled_orders=total_domestic_cancelled,
        overseas_cancelled_orders=total_overseas_cancelled,
        refund_order_count=refund_order_count_total,
    )

    # 有效销售额可按 SPU 行加总；订单数量必须使用上面的全局去重事实。
    effective_sales_total = money_total["effective_sales"]

    # 整体保本 ROI = NC' / (NC' - COGS_kept)
    overall_nc_prime = total_net_revenue_cny - total_return_loss
    overall_breakeven: Decimal | None = None
    breakeven_denom = overall_nc_prime - total_cogs_kept
    if total_spend != 0 and breakeven_denom > 0:
        overall_breakeven = overall_nc_prime / breakeven_denom

    totals = ProfitabilityTotals(
        row_count=len(scope_plain),
        order_count=eff_orders,
        cancelled_order_count=cancelled_orders_total,
        total_orders=dashboard_orders.total_orders,
        spend=money_total["spend"],
        sales=money_total["sales"],
        gmv=gmv_total,
        refund_net_amount=money_total["refund_net_amount"],
        return_loss=money_total["return_loss"],
        net_profit=money_total["net_profit"],
        roi_real=overall_roi,
        refund_order_count=refund_order_count_total,
        full_loss_qty=total_full_loss_qty,
        full_loss_cancelled_qty=total_full_loss_cancelled_qty,
        domestic_cancelled_order_count=total_domestic_cancelled,
        overseas_cancelled_order_count=total_overseas_cancelled,
        roi_breakeven=overall_breakeven,
        effective_sales=effective_sales_total,
        effective_order_count=dashboard_orders.effective_order_count,
        full_loss_order_count=dashboard_orders.full_loss_order_count,
        refund_rate=dashboard_orders.refund_rate,
        full_loss_rate=dashboard_orders.full_loss_rate,
        cancel_rate=dashboard_orders.cancel_rate,
        ad_system_actual_roi=ad_system_actual_roi,
        ad_system_breakeven_roi=ad_system_breakeven_roi,
        ad_system_max_ad_spend=ad_system_max_ad_spend,
        ad_system_remaining_ad_spend_capacity=(
            ad_system_remaining_ad_spend_capacity
        ),
        ad_system_breakeven_roi_status=(
            FormulaStatus.ESTIMATED_KNOWN_COSTS
        ),
    )

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

    warnings: list[str] = []
    if any(row["cost_source"] == "DEFAULT_K1" for row in scope_plain):
        warnings.append("default_unit_cost_used")
    if any(
        row["settled_order_count"] < row["order_count"] for row in scope_plain
    ):
        warnings.append("unsettled_orders_estimated")
    warnings.append("ad_system_other_necessary_costs_not_modeled")

    basis = ProfitabilityBasis(
        calculated_at=calculated_at,
        fx=fx_basis,
        fee_rate=rate,
        fee_mode="override" if fee_rate is not None else "baseline",
        rubric_version="v10",
        coverage_first_day=(
            data_window_row["first_day"]
            if data_window_row and data_window_row["first_day"]
            else None
        ),
        coverage_last_day=(
            data_window_row["last_day"]
            if data_window_row and data_window_row["last_day"]
            else None
        ),
        ad_first_day=window_row["first_day"] if window_row else None,
        ad_last_day=window_row["last_day"] if window_row else None,
        unattributed_refund_lines=_row_int(unattributed["n"]) if unattributed else 0,
        warnings=tuple(warnings),
    )

    page = plain[offset : offset + limit]
    items = tuple(SpuProfitability(**row) for row in page)
    return ProfitabilityOverview(
        items=items,
        total=len(plain),
        totals=totals,
        basis=basis,
    )


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
                "st_return": _CASE_COMPLETED_STATUSES[1],
                "ws": ws_dt,
                "we": we_dt,
                "lim": _ORDERS_MAX,
            },
        )
        .mappings()
        .all()
    )
    truncated = len(rows) >= _ORDERS_MAX
    _, vnd_per_cny = _resolve_conversion_rates(sess)
    orders: list[dict] = []
    for r in rows:
        order_pk = int(r["order_pk"])
        line_gmv_cny = Decimal(r["line_gmv_vnd"]) / vnd_per_cny
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
            share_ratio = Decimal(r["line_gmv_vnd"]) / order_gmv_vnd
        # settled_net_share = SETTLEMENT × share_ratio（未结算 → null）
        settled_net_share: Decimal | None = None
        if settlement_vnd is not None and share_ratio is not None:
            settled_net_share = (settlement_vnd * share_ratio) / vnd_per_cny

        arrived_overseas = bool(r["arrived_overseas"])
        has_completed_return_case = bool(r["has_completed_return_case"])
        is_cancelled = bool(r["is_cancelled"])
        # v9 全损旗标：完结退货(不论物流) ∨ 海外取消(CANCELLED∧38301)
        full_loss = has_completed_return_case or (is_cancelled and arrived_overseas)

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
                        "event_at": tk["event_at"],
                    }
                )

        orders.append(
            {
                "order_id": r["order_id"],
                "status": r["status"],
                "qty": _row_int(r["qty"]),
                "line_gmv": line_gmv_cny,
                "paid_at": r["paid_at"],
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
        "window": {"w_start": w_start, "w_end": w_end},
        "orders": orders,
        "meta": {
            "orders_truncated": truncated,
            "rubric_version": "v9",
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
            {"spu_pk": spu_pk, "ws": ws_dt, "we": we_dt},
        )
        .mappings()
        .all()
    )
    _, vnd_per_cny = _resolve_conversion_rates(sess)
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
            share_ratio = spu_line_gmv_vnd / order_gmv_vnd
        comps = (
            sess.execute(_SQL_DETAIL_SETTLE_COMPONENTS, {"txn_pk": int(r["txn_pk"])})
            .mappings()
            .all()
        )
        components = [
            {
                "code": c["component_code"],
                "amount_vnd": Decimal(c["amount"]),
                "amount": Decimal(c["amount"]) / vnd_per_cny,
            }
            for c in comps
        ]
        settlements.append(
            {
                "order_id": r["order_id"],
                "statement_time": r["statement_time"],
                "share_ratio": share_ratio,
                "components": components,
            }
        )
    return {
        "spu_pk": spu_pk,
        "settlements": settlements,
        "meta": {
            "rubric_version": "v9",
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
    _, vnd_per_cny = _resolve_conversion_rates(sess)
    cases: list[dict] = []
    for r in rows:
        refund_amount_cny = (
            Decimal(r["refund_amount"]) / vnd_per_cny
            if r["refund_amount"] is not None
            else None
        )
        cases.append(
            {
                "case_id": r["case_id"],
                "order_id": r["order_id"],
                "type": r["case_type"],
                "status": r["status"],
                "refund_amount": refund_amount_cny,
                "reason": (
                    f"{r['reason_code']}: {r['reason_text']}"
                    if r["reason_code"] or r["reason_text"]
                    else None
                ),
                "updated_at": r["updated_at_source"],
            }
        )
    return {
        "spu_pk": spu_pk,
        "cases": cases,
        "meta": {
            "rubric_version": "v9",
            "computed_at": datetime.now(UTC).isoformat(),
        },
    }


def _detail_ads(
    sess: Session, spu_pk: int, w_start: date | None, w_end: date | None
) -> dict:
    ws_dt, we_dt = _window_dates(w_start, w_end)
    rows = (
        sess.execute(_SQL_DETAIL_ADS, {"spu_pk": spu_pk, "ws": ws_dt, "we": we_dt})
        .mappings()
        .all()
    )
    fx_usd_cny, _ = _resolve_conversion_rates(sess)
    ads: list[dict] = []
    for r in rows:
        ads.append(
            {
                "campaign_id": r["campaign_id"],
                "spend": Decimal(r["spend"]) * fx_usd_cny,
                "orders": _row_int(r["ad_orders"]),
                "first_day": r["first_day"],
                "last_day": r["last_day"],
            }
        )
    return {
        "spu_pk": spu_pk,
        "ads": ads,
        "meta": {
            "rubric_version": "v9",
            "computed_at": datetime.now(UTC).isoformat(),
            "note": "广告域与日期窗口同语义裁剪（v8）",
        },
    }