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
    ProjectionInput,
    calculate,
    calculate_order_metrics,
    calculate_projection,
)
from tts_erp_v2.analytics.spu_profitability._selection import resolve_selected_spus
from tts_erp_v2.analytics.spu_profitability._types import (
    FormulaStatus,
    FxBasis,
    FxRateUnavailable,
    ProfitabilityBasis,
    ProfitabilityOverview,
    ProfitabilityTotals,
    ProjectionStatus,
    ShopFeeRateEntry,
    ShopFeeRateEstimate,
    SpuProfitability,
    SpuSelection,
)
from tts_erp_v2.db.constants import (
    PAID_SALES_ORDER_STATUSES,
    SHOP_FEE_RATE_CALCULATION_VERSION,
)
from tts_erp_v2.fx.rates import load_rate_map

log = logging.getLogger(__name__)

# ═════════════════════════════════════════════════════════════════════
# §3.1 模块抽取：常量（口径见 dashboard §4 + D9/D10 实测重定）
# ═════════════════════════════════════════════════════════════════════

# K1 默认 = 40 CNY/件（D1 拍板：原 K1=30 作废；≈ $5.95/件 @0.148823）
K1_DEFAULT_CNY = Decimal(40)
# 平台佣金基线 r̂（dashboard D10 2026-09-06 实测重定）
# 2026-09-29 起为兜底值：reporting.shop_fee_rate_estimates 有当前口径实测行的
# 店铺使用近 180 天费率；analytics.shop_fee_rate 每 24h 重算。无新鲜实测样本
# 的店铺回退此基线。
FEE_RATE_BASELINE = Decimal("0.308")

_RATE_Q8 = Decimal("0.00000001")

# 售后/case 完结状态白名单（与旧实现一致）
_CASE_COMPLETED_STATUSES = (
    "CANCELLATION_REQUEST_COMPLETE",
    "RETURN_OR_REFUND_REQUEST_COMPLETE",
)

_TRACK_ACTION_CODE_OVERSEAS = 38301  # "Arrived in destination country/region"
_TRACK_ACTION_CODE_RETURNED_TO_SELLER = 80101
_TRACK_ACTION_CODE_DELIVERED = 50101  # "Your package was delivered!"
_DELIVERY_TERMINAL_ORDER_STATUSES = ("DELIVERED", "COMPLETED")
_DELIVERY_TERMINAL_SHIPMENT_STATUSES = ("DELIVERED",)

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
          AND d.seller_id = ANY(CAST(:selected_seller_ids AS text[]))
          AND cp.id = ANY(CAST(:selected_pks AS bigint[]))
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
    WITH selected_orders AS (
        SELECT DISTINCT order_pk
        FROM commerce.sales_order_lines
        WHERE spu_pk = ANY(CAST(:selected_pks AS bigint[]))
    ),
    order_settlement AS (
        SELECT st.order_pk, SUM(sc.amount) AS settlement_vnd
        FROM selected_orders selected
        JOIN finance.settlement_transactions st ON st.order_pk = selected.order_pk
        JOIN finance.settlement_components sc
          ON sc.transaction_id = st.id AND sc.component_code = 'SETTLEMENT'
        GROUP BY st.order_pk
    ),
    order_gmv AS (
        SELECT sl.order_pk, SUM(sl.quantity * sl.unit_price) AS order_gmv_vnd
        FROM selected_orders selected
        JOIN commerce.sales_order_lines sl ON sl.order_pk = selected.order_pk
        GROUP BY sl.order_pk
    ),
    lines AS (
        SELECT sl.spu_pk,
               sl.order_pk,
               sl.quantity,
               sl.quantity * sl.unit_price AS line_gmv_vnd,
               og.order_gmv_vnd,
               os.settlement_vnd,
               (coalesce(so.order_time, so.paid_at)
                AT TIME ZONE 'UTC')::date AS event_day
        FROM commerce.sales_order_lines sl
        JOIN commerce.sales_orders so ON so.id = sl.order_pk
        JOIN order_gmv og ON og.order_pk = sl.order_pk
        LEFT JOIN order_settlement os ON os.order_pk = sl.order_pk
        WHERE sl.spu_pk = ANY(CAST(:selected_pks AS bigint[]))
          AND so.status = ANY(CAST(:paid_statuses AS text[]))
          /* 窗口裁剪：下单时间 order_time 优先（COALESCE(order_time, paid_at)）UTC 日 */
          AND (CAST(:ws AS timestamptz) IS NULL
               OR coalesce(so.order_time, so.paid_at) >= CAST(:ws AS timestamptz))
          AND (CAST(:we AS timestamptz) IS NULL
               OR coalesce(so.order_time, so.paid_at) <  CAST(:we AS timestamptz))
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

# 预计事实：已完结样本包含已结算、已送达和结果已确定的国内取消订单。
# 全损分子包含 80101 终局物流全损，以及到达海外/送达后最终全额退款的订单；
# 国内取消进入分母但不进入分子。该订单全损率应用于尚未送达的未结算风险池，
# 同时预测订单数、件数和费后收入折损。旧物流终态与售后字段保留作兼容解释。
_SQL_ROI_PROJECTION = text(
    """
    WITH selected_orders AS (
        SELECT DISTINCT order_pk
        FROM commerce.sales_order_lines
        WHERE spu_pk = ANY(CAST(:selected_pks AS bigint[]))
    ),
    settled_orders AS (
        SELECT st.order_pk,
               coalesce(sum(sc.amount) FILTER (
                   WHERE sc.component_code = 'CUSTOMER_REFUND'), 0)
                   AS customer_refund_vnd
        FROM selected_orders selected
        JOIN finance.settlement_transactions st ON st.order_pk = selected.order_pk
        JOIN finance.settlement_components sc ON sc.transaction_id = st.id
        GROUP BY st.order_pk
        HAVING count(*) FILTER (WHERE sc.component_code = 'SETTLEMENT') > 0
    ),
    order_gmv AS (
        SELECT sl.order_pk,
               coalesce(sum(sl.quantity * sl.unit_price), 0) AS order_gmv_vnd
        FROM selected_orders selected
        JOIN commerce.sales_order_lines sl ON sl.order_pk = selected.order_pk
        GROUP BY sl.order_pk
    ),
    completed_cases AS (
        SELECT cl.sales_order_line_id,
               count(*) AS completed_case_line_count,
               coalesce(sum(cl.quantity), 0) AS confirmed_qty,
               coalesce(sum(cl.refund_amount), 0) AS confirmed_refund_amount
        FROM after_sales.cases c
        JOIN after_sales.case_lines cl ON cl.case_id = c.id
        WHERE c.status IN (:st0, :st1)
          AND c.case_type IN ('REFUND_ONLY', 'RETURN_AND_REFUND')
        GROUP BY cl.sales_order_line_id
    ),
    completed_case_orders AS (
        SELECT c.order_pk,
               coalesce(sum(cl.refund_amount), 0) AS confirmed_refund_amount
        FROM selected_orders selected
        JOIN after_sales.cases c ON c.order_pk = selected.order_pk
        JOIN after_sales.case_lines cl ON cl.case_id = c.id
        WHERE c.status IN (:st0, :st1)
          AND c.case_type IN (
              'REFUND_ONLY', 'RETURN_AND_REFUND', 'CANCELLATION', 'CANCEL'
          )
        GROUP BY c.order_pk
    ),
    order_facts AS (
        SELECT so.id AS order_pk,
               so.status,
               so.status = ANY(CAST(:paid_statuses AS text[])) AS is_paid,
               settled.order_pk IS NOT NULL AS is_settled,
               coalesce(settled.customer_refund_vnd, 0) AS customer_refund_vnd,
               og.order_gmv_vnd,
               greatest(
                   abs(coalesce(settled.customer_refund_vnd, 0)),
                   coalesce(case_orders.confirmed_refund_amount, 0)
               ) AS final_refund_vnd,
               (
                   so.status = ANY(CAST(:delivery_terminal_order_statuses AS text[]))
                   OR EXISTS (
                       SELECT 1
                       FROM fulfillment.shipments sh
                       WHERE sh.order_pk = so.id
                         AND (
                             sh.status = ANY(CAST(
                                 :delivery_terminal_shipment_statuses AS text[]
                             ))
                             OR sh.delivered_at IS NOT NULL
                             OR EXISTS (
                                 SELECT 1
                                 FROM fulfillment.tracking_events te
                                 WHERE te.shipment_id = sh.id
                                   AND te.action_code = :delivered_action_code
                             )
                         )
                   )
               ) AS is_delivery_terminal,
               (
                   so.status = 'CANCELLED'
                   AND EXISTS (
                       SELECT 1
                       FROM fulfillment.shipments sh
                       JOIN fulfillment.tracking_events te
                         ON te.shipment_id = sh.id
                       WHERE sh.order_pk = so.id
                         AND te.action_code = :returned_to_seller_action_code
                   )
               ) AS is_terminal_full_loss
        FROM selected_orders selected
        JOIN commerce.sales_orders so ON so.id = selected.order_pk
        JOIN order_gmv og ON og.order_pk = so.id
        LEFT JOIN settled_orders settled ON settled.order_pk = so.id
        LEFT JOIN completed_case_orders case_orders ON case_orders.order_pk = so.id
        WHERE (
              so.status = ANY(CAST(:paid_statuses AS text[]))
              OR so.status = 'CANCELLED'
          )
          AND (CAST(:ws AS timestamptz) IS NULL
               OR coalesce(so.order_time, so.paid_at) >= CAST(:ws AS timestamptz))
          AND (CAST(:we AS timestamptz) IS NULL
               OR coalesce(so.order_time, so.paid_at) < CAST(:we AS timestamptz))
    ),
    exposed_orders AS (
        SELECT order_facts.*,
               (
                   is_delivery_terminal
                   OR EXISTS (
                       SELECT 1
                       FROM fulfillment.shipments sh
                       JOIN fulfillment.tracking_events te
                         ON te.shipment_id = sh.id
                       WHERE sh.order_pk = order_facts.order_pk
                         AND te.action_code = :overseas_action_code
                   )
               ) AS is_overseas_exposed
        FROM order_facts
    ),
    completed_orders AS (
        SELECT exposed_orders.*,
               (is_settled OR is_delivery_terminal OR status = 'CANCELLED')
                   AS is_completed
        FROM exposed_orders
    ),
    classified_orders AS (
        SELECT completed_orders.*,
               (
                   is_completed
                   AND (
                       is_terminal_full_loss
                       OR (
                           is_overseas_exposed
                           AND order_gmv_vnd > 0
                           AND final_refund_vnd >= order_gmv_vnd
                       )
                   )
               ) AS is_completed_full_loss
        FROM completed_orders
    ),
    line_facts AS (
        SELECT sl.spu_pk,
               sl.order_pk,
               sl.quantity,
               sl.unit_price,
               sl.quantity * sl.unit_price AS line_sales_vnd,
               orders.is_paid,
               orders.is_settled,
               orders.is_delivery_terminal,
               orders.is_terminal_full_loss,
               orders.is_completed,
               orders.is_completed_full_loss,
               coalesce(cc.completed_case_line_count, 0) > 0
                   AS is_delivered_full_loss,
               least(sl.quantity, coalesce(cc.confirmed_qty, 0)) AS confirmed_qty,
               CASE
                   WHEN orders.is_completed_full_loss THEN sl.quantity
                   ELSE 0
               END AS confirmed_full_loss_qty,
               CASE
                   WHEN orders.customer_refund_vnd <> 0
                   THEN abs(orders.customer_refund_vnd)
                        * (sl.quantity * sl.unit_price)
                        / nullif(orders.order_gmv_vnd, 0)
                   ELSE coalesce(cc.confirmed_refund_amount, 0)
               END AS basis_refund_amount,
               coalesce(cc.confirmed_refund_amount, 0) AS confirmed_refund_amount,
               CASE
                   WHEN orders.is_terminal_full_loss THEN sl.quantity
                   ELSE 0
               END AS terminal_full_loss_qty,
               CASE
                   WHEN coalesce(cc.completed_case_line_count, 0) > 0
                   THEN least(
                       sl.quantity,
                       greatest(
                           coalesce(cc.confirmed_qty, 0),
                           coalesce(cc.confirmed_refund_amount, 0)
                           / nullif(sl.unit_price, 0)
                       )
                   )
                   ELSE 0
               END AS delivered_full_loss_qty,
               greatest(
                   sl.quantity - least(sl.quantity, coalesce(cc.confirmed_qty, 0)),
                   0
               ) AS unresolved_qty,
               CASE
                   WHEN orders.is_completed_full_loss THEN 0
                   ELSE sl.quantity
               END AS unresolved_full_loss_qty
        FROM commerce.sales_order_lines sl
        JOIN classified_orders orders ON orders.order_pk = sl.order_pk
        LEFT JOIN completed_cases cc ON cc.sales_order_line_id = sl.id
        WHERE sl.spu_pk = ANY(CAST(:selected_pks AS bigint[]))
    )
    SELECT spu_pk,
           count(DISTINCT order_pk) FILTER (WHERE is_paid AND is_settled)
               AS projection_basis_order_count,
           coalesce(sum(quantity) FILTER (WHERE is_paid AND is_settled), 0)
               AS projection_basis_qty,
           coalesce(sum(line_sales_vnd) FILTER (
               WHERE is_paid AND is_settled), 0)
               AS projection_basis_sales_vnd,
           coalesce(sum(basis_refund_amount) FILTER (
               WHERE is_paid AND is_settled), 0)
               AS projection_basis_refund_amount_vnd,
           count(DISTINCT order_pk) FILTER (
               WHERE (is_paid AND is_delivery_terminal)
                  OR is_terminal_full_loss)
               AS projection_terminal_basis_order_count,
           count(DISTINCT order_pk) FILTER (
               WHERE is_paid AND is_delivery_terminal)
               AS projection_full_loss_basis_order_count,
           coalesce(sum(line_sales_vnd) FILTER (
               WHERE (is_paid AND is_delivery_terminal)
                  OR is_terminal_full_loss), 0)
               AS projection_terminal_basis_sales_vnd,
           coalesce(sum(line_sales_vnd) FILTER (
               WHERE is_terminal_full_loss), 0)
               AS projection_terminal_full_loss_sales_vnd,
           count(DISTINCT order_pk) FILTER (WHERE is_terminal_full_loss)
               AS projection_terminal_full_loss_order_count,
           coalesce(sum(terminal_full_loss_qty) FILTER (
               WHERE is_terminal_full_loss), 0)
               AS projection_terminal_full_loss_qty,
           count(DISTINCT order_pk) FILTER (WHERE is_completed)
               AS projection_completed_basis_order_count,
           count(DISTINCT order_pk) FILTER (WHERE is_completed_full_loss)
               AS projection_completed_full_loss_order_count,
           count(DISTINCT order_pk) FILTER (
               WHERE is_paid
                 AND is_delivery_terminal
                 AND is_delivered_full_loss)
               AS projection_basis_full_loss_order_count,
           coalesce(sum(delivered_full_loss_qty) FILTER (
               WHERE is_paid AND is_delivery_terminal), 0)
               AS projection_basis_full_loss_qty,
           count(DISTINCT order_pk) FILTER (WHERE is_paid AND NOT is_settled)
               AS unsettled_order_count,
           count(DISTINCT order_pk) FILTER (
               WHERE is_paid AND NOT is_settled AND NOT is_delivery_terminal)
               AS full_loss_exposure_unsettled_order_count,
           count(DISTINCT order_pk) FILTER (
               WHERE is_paid AND NOT is_settled AND unresolved_qty > 0)
               AS unresolved_unsettled_order_count,
           count(DISTINCT order_pk) FILTER (
               WHERE is_paid
                 AND NOT is_settled
                 AND NOT is_delivery_terminal
                 AND unresolved_full_loss_qty > 0)
               AS unresolved_full_loss_exposure_order_count,
           coalesce(sum(confirmed_refund_amount) FILTER (
               WHERE is_paid AND NOT is_settled), 0)
               AS confirmed_unsettled_refund_amount_vnd,
           coalesce(sum(confirmed_refund_amount) FILTER (
               WHERE is_paid AND NOT is_settled AND NOT is_delivery_terminal), 0)
               AS confirmed_full_loss_exposure_refund_amount_vnd,
           count(DISTINCT order_pk) FILTER (
               WHERE is_paid AND NOT is_settled AND confirmed_full_loss_qty > 0)
               AS confirmed_unsettled_full_loss_order_count,
           count(DISTINCT order_pk) FILTER (
               WHERE is_paid
                 AND NOT is_settled
                 AND NOT is_delivery_terminal
                 AND confirmed_full_loss_qty > 0)
               AS confirmed_full_loss_exposure_order_count,
           coalesce(sum(confirmed_full_loss_qty) FILTER (
               WHERE is_paid AND NOT is_settled), 0)
               AS confirmed_unsettled_full_loss_qty,
           coalesce(sum(confirmed_full_loss_qty) FILTER (
               WHERE is_paid AND NOT is_settled AND NOT is_delivery_terminal), 0)
               AS confirmed_full_loss_exposure_qty,
           coalesce(sum(unresolved_qty) FILTER (
               WHERE is_paid AND NOT is_settled), 0)
               AS unresolved_unsettled_qty,
           coalesce(sum(unresolved_full_loss_qty) FILTER (
               WHERE is_paid AND NOT is_settled AND NOT is_delivery_terminal), 0)
               AS unresolved_full_loss_exposure_qty,
           coalesce(sum(line_sales_vnd) FILTER (
               WHERE is_paid AND NOT is_settled AND NOT is_delivery_terminal), 0)
               AS full_loss_exposure_unsettled_sales_vnd,
           coalesce(sum(unresolved_qty * unit_price) FILTER (
               WHERE is_paid AND NOT is_settled), 0)
               AS unresolved_unsettled_sales_vnd
    FROM line_facts
    GROUP BY spu_pk
    """
)

_SQL_ROI_PROJECTION_SCOPE_COUNTS = text(
    """
    WITH selected_orders AS (
        SELECT DISTINCT order_pk
        FROM commerce.sales_order_lines
        WHERE spu_pk = ANY(CAST(:selected_pks AS bigint[]))
    ),
    settled_orders AS (
        SELECT st.order_pk,
               coalesce(sum(sc.amount) FILTER (
                   WHERE sc.component_code = 'CUSTOMER_REFUND'), 0)
                   AS customer_refund_vnd
        FROM selected_orders selected
        JOIN finance.settlement_transactions st ON st.order_pk = selected.order_pk
        JOIN finance.settlement_components sc ON sc.transaction_id = st.id
        GROUP BY st.order_pk
        HAVING count(*) FILTER (WHERE sc.component_code = 'SETTLEMENT') > 0
    ),
    order_gmv AS (
        SELECT sl.order_pk,
               coalesce(sum(sl.quantity * sl.unit_price), 0) AS order_gmv_vnd
        FROM selected_orders selected
        JOIN commerce.sales_order_lines sl ON sl.order_pk = selected.order_pk
        GROUP BY sl.order_pk
    ),
    completed_cases AS (
        SELECT cl.sales_order_line_id,
               count(*) AS completed_case_line_count,
               coalesce(sum(cl.quantity), 0) AS confirmed_qty
        FROM after_sales.cases c
        JOIN after_sales.case_lines cl ON cl.case_id = c.id
        WHERE c.status IN (:st0, :st1)
          AND c.case_type IN ('REFUND_ONLY', 'RETURN_AND_REFUND')
        GROUP BY cl.sales_order_line_id
    ),
    completed_case_orders AS (
        SELECT c.order_pk,
               coalesce(sum(cl.refund_amount), 0) AS confirmed_refund_amount
        FROM selected_orders selected
        JOIN after_sales.cases c ON c.order_pk = selected.order_pk
        JOIN after_sales.case_lines cl ON cl.case_id = c.id
        WHERE c.status IN (:st0, :st1)
          AND c.case_type IN (
              'REFUND_ONLY', 'RETURN_AND_REFUND', 'CANCELLATION', 'CANCEL'
          )
        GROUP BY c.order_pk
    ),
    order_facts AS (
        SELECT so.id AS order_pk,
               so.status,
               so.status = ANY(CAST(:paid_statuses AS text[])) AS is_paid,
               settled.order_pk IS NOT NULL AS is_settled,
               og.order_gmv_vnd,
               greatest(
                   abs(coalesce(settled.customer_refund_vnd, 0)),
                   coalesce(case_orders.confirmed_refund_amount, 0)
               ) AS final_refund_vnd,
               (
                   so.status = ANY(CAST(:delivery_terminal_order_statuses AS text[]))
                   OR EXISTS (
                       SELECT 1
                       FROM fulfillment.shipments sh
                       WHERE sh.order_pk = so.id
                         AND (
                             sh.status = ANY(CAST(
                                 :delivery_terminal_shipment_statuses AS text[]
                             ))
                             OR sh.delivered_at IS NOT NULL
                             OR EXISTS (
                                 SELECT 1
                                 FROM fulfillment.tracking_events te
                                 WHERE te.shipment_id = sh.id
                                   AND te.action_code = :delivered_action_code
                             )
                         )
                   )
               ) AS is_delivery_terminal,
               (
                   so.status = 'CANCELLED'
                   AND EXISTS (
                       SELECT 1
                       FROM fulfillment.shipments sh
                       JOIN fulfillment.tracking_events te
                         ON te.shipment_id = sh.id
                       WHERE sh.order_pk = so.id
                         AND te.action_code = :returned_to_seller_action_code
                   )
               ) AS is_terminal_full_loss
        FROM selected_orders selected
        JOIN commerce.sales_orders so ON so.id = selected.order_pk
        JOIN order_gmv og ON og.order_pk = so.id
        LEFT JOIN settled_orders settled ON settled.order_pk = so.id
        LEFT JOIN completed_case_orders case_orders ON case_orders.order_pk = so.id
        WHERE (
              so.status = ANY(CAST(:paid_statuses AS text[]))
              OR so.status = 'CANCELLED'
          )
          AND (CAST(:ws AS timestamptz) IS NULL
               OR coalesce(so.order_time, so.paid_at) >= CAST(:ws AS timestamptz))
          AND (CAST(:we AS timestamptz) IS NULL
               OR coalesce(so.order_time, so.paid_at) < CAST(:we AS timestamptz))
    ),
    exposed_orders AS (
        SELECT order_facts.*,
               (
                   is_delivery_terminal
                   OR EXISTS (
                       SELECT 1
                       FROM fulfillment.shipments sh
                       JOIN fulfillment.tracking_events te
                         ON te.shipment_id = sh.id
                       WHERE sh.order_pk = order_facts.order_pk
                         AND te.action_code = :overseas_action_code
                   )
               ) AS is_overseas_exposed
        FROM order_facts
    ),
    completed_orders AS (
        SELECT exposed_orders.*,
               (is_settled OR is_delivery_terminal OR status = 'CANCELLED')
                   AS is_completed
        FROM exposed_orders
    ),
    classified_orders AS (
        SELECT completed_orders.*,
               (
                   is_completed
                   AND (
                       is_terminal_full_loss
                       OR (
                           is_overseas_exposed
                           AND order_gmv_vnd > 0
                           AND final_refund_vnd >= order_gmv_vnd
                       )
                   )
               ) AS is_completed_full_loss
        FROM completed_orders
    ),
    line_facts AS (
        SELECT sl.order_pk,
               orders.is_paid,
               orders.is_settled,
               orders.is_delivery_terminal,
               orders.is_terminal_full_loss,
               orders.is_completed,
               orders.is_completed_full_loss,
               coalesce(cc.completed_case_line_count, 0) > 0
                   AS is_delivered_full_loss,
               least(sl.quantity, coalesce(cc.confirmed_qty, 0)) AS confirmed_qty,
               CASE
                   WHEN orders.is_completed_full_loss THEN sl.quantity
                   ELSE 0
               END AS confirmed_full_loss_qty,
               greatest(
                   sl.quantity - least(sl.quantity, coalesce(cc.confirmed_qty, 0)),
                   0
               ) AS unresolved_qty,
               CASE
                   WHEN orders.is_completed_full_loss THEN 0
                   ELSE sl.quantity
               END AS unresolved_full_loss_qty
        FROM commerce.sales_order_lines sl
        JOIN classified_orders orders ON orders.order_pk = sl.order_pk
        LEFT JOIN completed_cases cc ON cc.sales_order_line_id = sl.id
        WHERE sl.spu_pk = ANY(CAST(:selected_pks AS bigint[]))
    )
    SELECT count(DISTINCT order_pk) FILTER (WHERE is_paid AND is_settled)
               AS projection_basis_order_count,
           count(DISTINCT order_pk) FILTER (
               WHERE (is_paid AND is_delivery_terminal)
                  OR is_terminal_full_loss)
               AS projection_terminal_basis_order_count,
           count(DISTINCT order_pk) FILTER (
               WHERE is_paid AND is_delivery_terminal)
               AS projection_full_loss_basis_order_count,
           count(DISTINCT order_pk) FILTER (WHERE is_terminal_full_loss)
               AS projection_terminal_full_loss_order_count,
           count(DISTINCT order_pk) FILTER (WHERE is_completed)
               AS projection_completed_basis_order_count,
           count(DISTINCT order_pk) FILTER (WHERE is_completed_full_loss)
               AS projection_completed_full_loss_order_count,
           count(DISTINCT order_pk) FILTER (
               WHERE is_paid
                 AND is_delivery_terminal
                 AND is_delivered_full_loss)
               AS projection_basis_full_loss_order_count,
           count(DISTINCT order_pk) FILTER (WHERE is_paid AND NOT is_settled)
               AS unsettled_order_count,
           count(DISTINCT order_pk) FILTER (
               WHERE is_paid AND NOT is_settled AND NOT is_delivery_terminal)
               AS full_loss_exposure_unsettled_order_count,
           count(DISTINCT order_pk) FILTER (
               WHERE is_paid AND NOT is_settled AND confirmed_full_loss_qty > 0)
               AS confirmed_unsettled_full_loss_order_count,
           count(DISTINCT order_pk) FILTER (
               WHERE is_paid
                 AND NOT is_settled
                 AND NOT is_delivery_terminal
                 AND confirmed_full_loss_qty > 0)
               AS confirmed_full_loss_exposure_order_count,
           count(DISTINCT order_pk) FILTER (
               WHERE is_paid AND NOT is_settled AND unresolved_qty > 0)
               AS unresolved_unsettled_order_count,
           count(DISTINCT order_pk) FILTER (
               WHERE is_paid
                 AND NOT is_settled
                 AND NOT is_delivery_terminal
                 AND unresolved_full_loss_qty > 0)
               AS unresolved_full_loss_exposure_order_count
    FROM line_facts
    """
)

# 主表 SQL ── 全损件数（v9 口径：全损 = 退货 + 海外取消；国内取消 ≠ 全损）
#   退货桶：RETURN_AND_REFUND / REFUND_ONLY 已完结（不论物流是否到海外，
#     rubric v9「退货 = 直接全损」），件数取 case_lines.quantity；
#     窗口跟随原订单下单时间 coalesce(order_time, paid_at)，跨日退款回归下单日；
#     限定已付白名单订单 —— 异常单(UNPAID 等)退款仍按 §4.2 rule 0 进未归属
#   海外取消桶：CANCELLED + tracking_events.action_code=38301（已到目的国），
#     件数取行 quantity；窗口同样按订单下单时间 coalesce(order_time, paid_at)
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
          AND sl.spu_pk = ANY(CAST(:selected_pks AS bigint[]))
          AND so.status = ANY(CAST(:paid_statuses AS text[]))
          AND (CAST(:ws AS timestamptz) IS NULL
               OR coalesce(so.order_time, so.paid_at) >= CAST(:ws AS timestamptz))
          AND (CAST(:we AS timestamptz) IS NULL
               OR coalesce(so.order_time, so.paid_at) <  CAST(:we AS timestamptz))
        UNION ALL
        SELECT sl.spu_pk AS spu_pk,
               sl.quantity AS qty,
               sl.quantity AS cancel_qty
        FROM commerce.sales_order_lines sl
        JOIN commerce.sales_orders so ON so.id = sl.order_pk
        WHERE sl.spu_pk = ANY(CAST(:selected_pks AS bigint[]))
          AND so.status = 'CANCELLED'
          AND EXISTS (SELECT 1 FROM fulfillment.shipments sh
                      JOIN fulfillment.tracking_events te
                        ON te.shipment_id = sh.id AND te.action_code = :ac
                      WHERE sh.order_pk = so.id)
          AND (CAST(:ws AS timestamptz) IS NULL
               OR coalesce(so.order_time, so.paid_at) >= CAST(:ws AS timestamptz))
          AND (CAST(:we AS timestamptz) IS NULL
               OR coalesce(so.order_time, so.paid_at) <  CAST(:we AS timestamptz))
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
    WHERE sl.spu_pk = ANY(CAST(:selected_pks AS bigint[]))
      AND (so.status = ANY(CAST(:paid_statuses AS text[]))
           OR so.status = 'CANCELLED')
      AND (CAST(:ws AS timestamptz) IS NULL
           OR coalesce(so.order_time, so.paid_at) >= CAST(:ws AS timestamptz))
      AND (CAST(:we AS timestamptz) IS NULL
           OR coalesce(so.order_time, so.paid_at) <  CAST(:we AS timestamptz))
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
      AND sl.spu_pk = ANY(CAST(:selected_pks AS bigint[]))
      AND (CAST(:ws AS timestamptz) IS NULL
           OR coalesce(so.order_time, so.paid_at) >= CAST(:ws AS timestamptz))
      AND (CAST(:we AS timestamptz) IS NULL
           OR coalesce(so.order_time, so.paid_at) <  CAST(:we AS timestamptz))
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
           OR coalesce(so.order_time, so.paid_at) >= CAST(:ws AS timestamptz))
      AND (CAST(:we AS timestamptz) IS NULL
           OR coalesce(so.order_time, so.paid_at) <  CAST(:we AS timestamptz))
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
           OR coalesce(so.order_time, so.paid_at) >= CAST(:ws AS timestamptz))
      AND (CAST(:we AS timestamptz) IS NULL
           OR coalesce(so.order_time, so.paid_at) <  CAST(:we AS timestamptz))
    """
)

_SQL_ROI_WINDOW = text(
    "SELECT min(day) AS first_day, max(day) AS last_day "
    "FROM plugin.ad_daily "
    "WHERE endpoint = '/oec_ads/shopping/v1/oec/stat/post_product_list' "
)

# 店铺级平台抽成费率日快照（analytics.shop_fee_rate 任务产出）。
# 取每店最新一行；读取侧按 MAX_ESTIMATE_AGE_DAYS 判过期后回退基线。
_SQL_SHOP_FEE_RATES = text(
    """
    SELECT DISTINCT ON (shop_pk)
           shop_pk, fee_rate, calculated_on, calculated_at, lookback_days,
           kept_order_count, kept_line_gmv, window_line_gmv,
           kept_share, total_fee, currency
    FROM reporting.shop_fee_rate_estimates
    WHERE calculation_version = :calculation_version
    ORDER BY shop_pk, calculated_at DESC
    """
)

# 表存在性探测。用 ``to_regclass``（不存在返回 NULL，永不报错）而不是 try/except：
# ``consistent_read_snapshot`` 开的是真事务（非 savepoint），语句报错会毒化整个
# 事务（后续查询全部 "current transaction is aborted"）—— 那样「回退基线」
# 反而会把整页打挂。
_SQL_SHOP_FEE_TABLE_EXISTS = text(
    "SELECT to_regclass('reporting.shop_fee_rate_estimates') IS NOT NULL"
)

#: 店铺费率快照的最大有效期（天）。超过即视为过期并回退全局基线 —— job 长
#: 期未跑时不应继续用陈旧费率估算未结算净额。
MAX_ESTIMATE_AGE_DAYS = 7

_SQL_ROI_DATA_WINDOW = text(
    """
    WITH croppable AS (
        SELECT (coalesce(so.order_time, so.paid_at) AT TIME ZONE 'UTC')::date AS d
        FROM commerce.sales_orders so
        WHERE so.status = ANY(CAST(:paid_statuses AS text[]))
          AND (CAST(:shop_pk AS bigint) IS NULL
               OR so.shop_pk = CAST(:shop_pk AS bigint))
        UNION
        SELECT (coalesce(so.order_time, so.paid_at) AT TIME ZONE 'UTC')::date AS d
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
# §3.4 成本 SQL（仅使用人工标注的当前有效采购成交价）
# ═════════════════════════════════════════════════════════════════════

_SQL_COST_MANUAL = text(
    """
    SELECT spu_pk, unit_cost
    FROM procurement.manual_product_costs
    WHERE valid_to IS NULL
      AND spu_pk = ANY(CAST(:pks AS bigint[]))
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
           OR coalesce(so.order_time, so.paid_at) >= CAST(:ws AS timestamptz))
      AND (CAST(:we AS timestamptz) IS NULL
           OR coalesce(so.order_time, so.paid_at) <  CAST(:we AS timestamptz))
    ORDER BY coalesce(so.order_time, so.paid_at) DESC NULLS LAST, so.id DESC
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
           OR coalesce(so.order_time, so.paid_at) >= CAST(:ws AS timestamptz))
      AND (CAST(:we AS timestamptz) IS NULL
           OR coalesce(so.order_time, so.paid_at) < CAST(:we AS timestamptz))
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
           OR coalesce(so.order_time, so.paid_at) >= CAST(:ws AS timestamptz))
      AND (CAST(:we AS timestamptz) IS NULL
           OR coalesce(so.order_time, so.paid_at) <  CAST(:we AS timestamptz))
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
    cny_usd = (Decimal(1) / cny_rate).quantize(_RATE_Q8, rounding=ROUND_HALF_UP)
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


def _load_shop_fee_estimates(
    sess: Session,
) -> dict[int, ShopFeeRateEstimate]:
    """加载每店最新费率快照：``{shop_pk: ShopFeeRateEstimate}``。

    表不存在（迁移未跑）时按空 map 处理，全量回退全局基线 —— 读路径不因
    快照表缺失而失败（存在性用 ``to_regclass`` 探测，不走异常）。
    """
    if not sess.execute(_SQL_SHOP_FEE_TABLE_EXISTS).scalar():
        log.warning(
            "reporting.shop_fee_rate_estimates missing (migration not applied?); "
            "falling back to baseline %s",
            FEE_RATE_BASELINE,
        )
        return {}
    rows = (
        sess.execute(
            _SQL_SHOP_FEE_RATES,
            {"calculation_version": SHOP_FEE_RATE_CALCULATION_VERSION},
        )
        .mappings()
        .all()
    )
    # 这里不再套 int()：列值是 psycopg 直接返回的 Python int/Decimal，
    # 多余的类型转换还会触发 pi-lens 的 unchecked-throwing-call-python。
    return {
        r["shop_pk"]: ShopFeeRateEstimate(
            calculated_on=r["calculated_on"],
            calculated_at=r["calculated_at"],
            lookback_days=r["lookback_days"],
            fee_rate=Decimal(r["fee_rate"]),
            kept_order_count=r["kept_order_count"],
            kept_line_gmv=Decimal(r["kept_line_gmv"]),
            window_line_gmv=Decimal(r["window_line_gmv"]),
            kept_share=Decimal(r["kept_share"]),
            total_fee=Decimal(r["total_fee"]),
            currency=r["currency"],
        )
        for r in rows
    }


def _resolve_shop_fee(
    shop_pk: int,
    *,
    shop_name: str | None,
    override_rate: Decimal | None,
    estimates: dict[int, ShopFeeRateEstimate],
    fresh_before: date,
) -> ShopFeeRateEntry:
    """解析单店费率：页面覆写 > 未过期实测快照 > 全局基线。"""
    if override_rate is not None:
        return ShopFeeRateEntry(
            shop_pk=shop_pk,
            shop_name=shop_name,
            fee_rate=override_rate,
            source="user_override",
        )
    estimate = estimates.get(shop_pk)
    if estimate is not None and estimate.calculated_on >= fresh_before:
        return ShopFeeRateEntry(
            shop_pk=shop_pk,
            shop_name=shop_name,
            fee_rate=estimate.fee_rate,
            source="shop_estimate",
            estimate=estimate,
        )
    return ShopFeeRateEntry(
        shop_pk=shop_pk,
        shop_name=shop_name,
        fee_rate=FEE_RATE_BASELINE,
        source="baseline",
        fallback_reason="no_estimate" if estimate is None else "stale_estimate",
    )


# ═════════════════════════════════════════════════════════════════════
# 成本批量解析
# ═════════════════════════════════════════════════════════════════════


def _resolve_costs_batch(
    sess: Session, spu_pks: list[int]
) -> dict[int, tuple[Decimal, str]]:
    """批量解析每个 SPU 的单位成本 CNY + 来源枚举。

    仅使用 ``manual_product_costs`` 的当前有效人工成本；未命中时统一回退
    40 CNY/件。妙手/1688 同步的 ``source_unit_cost`` 不参与盈利计算。
    """
    if not spu_pks:
        return {}
    out: dict[int, tuple[Decimal, str]] = {}

    rows = sess.execute(_SQL_COST_MANUAL, {"pks": spu_pks}).mappings().all()
    for r in rows:
        out[int(r["spu_pk"])] = (
            Decimal(r["unit_cost"]),
            "MANUAL",
        )  # pi-lens-ignore: no-try-except

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
    selection: SpuSelection,
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
    # 费率解析优先级（2026-09-29 用户拍板）：页面覆写 > 店铺实测 > 全局基线。
    # 覆写时全 scope 统一；否则逐店取 reporting.shop_fee_rate_estimates 的
    # 最新快照（超 MAX_ESTIMATE_AGE_DAYS 视为过期），无可用快照的店回退
    # FEE_RATE_BASELINE。
    shop_fee_estimates = _load_shop_fee_estimates(sess)
    override_rate = fee_rate
    fee_fresh_before = calculated_at.date() - timedelta(days=MAX_ESTIMATE_AGE_DAYS)
    fx_basis = _resolve_fx_basis(sess)
    fx_usd_cny = fx_basis.usd_cny
    fx_usd_vnd = fx_basis.usd_vnd
    vnd_per_cny = fx_usd_vnd / fx_usd_cny
    ws_dt, we_dt = _window_dates(w_start, w_end)
    paid_statuses = list(PAID_SALES_ORDER_STATUSES)
    st0, st1 = _CASE_COMPLETED_STATUSES

    # Legacy callers may omit shop_pk and historically used q as their only
    # bounded scope.  Once a shop is present (the production page contract), q
    # is presentation-only and must not alter the profitability overview.
    catalog_q = q if shop_pk is None else None
    cats = resolve_selected_spus(
        sess,
        selection=selection,
        shop_pk=shop_pk,
        catalog_search=catalog_q,
        active_only=active_only,
    )
    if only_spu_pk is not None:
        cats = [cat for cat in cats if int(cat["spu_pk"]) == only_spu_pk]

    # 店铺费率解析（覆写 > 实测快照 > 基线）按 shop_pk 一次性算好，行级与
    # meta 共用同一份结果，避免两处各自推导出不一致口径。
    shop_fee: dict[int, ShopFeeRateEntry] = {}
    for cat in cats:
        spk = cat["shop_pk"]
        if spk in shop_fee:
            continue
        shop_fee[spk] = _resolve_shop_fee(
            spk,
            shop_name=cat["shop_name"],
            override_rate=override_rate,
            estimates=shop_fee_estimates,
            fresh_before=fee_fresh_before,
        )

    # Resolve the selected relation once, then push it into every expensive fact
    # query. This keeps focused scopes bounded without duplicating formulas.
    selected_pks = [
        int(cat["spu_pk"]) for cat in cats
    ]  # pi-lens-ignore: no-try-except
    selected_seller_ids = sorted(
        {str(cat["shop_id"]) for cat in cats if cat.get("shop_id")}
    )
    common_fact_params = {
        "selected_pks": selected_pks,
        "selected_seller_ids": selected_seller_ids,
        "ws": ws_dt,
        "we": we_dt,
    }

    ad_rows = (
        sess.execute(_SQL_ROI_AD, common_fact_params).mappings().all()
        if selected_pks
        else []
    )
    ad_map = {
        int(r["spu_pk"]): r for r in ad_rows if r["spu_pk"] is not None
    }  # pi-lens-ignore: no-try-except

    sales_rows = (
        sess.execute(
            _SQL_ROI_SALES,
            {**common_fact_params, "paid_statuses": paid_statuses},
        )
        .mappings()
        .all()
        if selected_pks
        else []
    )
    sales_map = {
        int(r["spu_pk"]): r for r in sales_rows if r["spu_pk"] is not None
    }  # pi-lens-ignore: no-try-except

    fl_rows = (
        sess.execute(
            _SQL_ROI_FULL_LOSS,
            {
                **common_fact_params,
                "paid_statuses": paid_statuses,
                "ac": _TRACK_ACTION_CODE_OVERSEAS,
                "st_return": _CASE_COMPLETED_STATUSES[1],
            },
        )
        .mappings()
        .all()
        if selected_pks
        else []
    )
    fl_map = {
        int(r["spu_pk"]): r for r in fl_rows if r["spu_pk"] is not None
    }  # pi-lens-ignore: no-try-except

    rs_rows = (
        sess.execute(
            _SQL_ROI_ROW_STATUS,
            {
                **common_fact_params,
                "paid_statuses": paid_statuses,
                "st0": st0,
                "st1": st1,
                "ac": _TRACK_ACTION_CODE_OVERSEAS,
            },
        )
        .mappings()
        .all()
        if selected_pks
        else []
    )
    rs_map = {
        int(r["spu_pk"]): r for r in rs_rows if r["spu_pk"] is not None
    }  # pi-lens-ignore: no-try-except

    refund_rows = (
        sess.execute(
            _SQL_ROI_REFUNDS,
            {
                **common_fact_params,
                "paid_statuses": paid_statuses,
                "st0": st0,
                "st1": st1,
            },
        )
        .mappings()
        .all()
        if selected_pks
        else []
    )
    refund_map = {
        int(r["spu_pk"]): r for r in refund_rows if r["spu_pk"] is not None
    }  # pi-lens-ignore: no-try-except

    projection_rows = (
        sess.execute(
            _SQL_ROI_PROJECTION,
            {
                **common_fact_params,
                "paid_statuses": paid_statuses,
                "st0": st0,
                "st1": st1,
                "delivery_terminal_order_statuses": list(
                    _DELIVERY_TERMINAL_ORDER_STATUSES
                ),
                "delivery_terminal_shipment_statuses": list(
                    _DELIVERY_TERMINAL_SHIPMENT_STATUSES
                ),
                "delivered_action_code": _TRACK_ACTION_CODE_DELIVERED,
                "overseas_action_code": _TRACK_ACTION_CODE_OVERSEAS,
                "returned_to_seller_action_code": (
                    _TRACK_ACTION_CODE_RETURNED_TO_SELLER
                ),
            },
        )
        .mappings()
        .all()
        if selected_pks
        else []
    )
    projection_map = {
        int(r["spu_pk"]): r for r in projection_rows if r["spu_pk"] is not None
    }  # pi-lens-ignore: no-try-except

    # 成本链批量解析（D1）
    cost_map = _resolve_costs_batch(sess, selected_pks)

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

        projection_facts = projection_map.get(pk)
        projection_basis_order_count = (
            _row_int(projection_facts["projection_basis_order_count"])
            if projection_facts
            else 0
        )
        projection_basis_qty = (
            _row_int(projection_facts["projection_basis_qty"])
            if projection_facts
            else 0
        )
        projection_basis_sales_cny = (
            Decimal(projection_facts["projection_basis_sales_vnd"] or 0)
            / vnd_per_cny
            if projection_facts
            else Decimal(0)
        )
        projection_basis_refund_amount_cny = (
            Decimal(projection_facts["projection_basis_refund_amount_vnd"] or 0)
            / vnd_per_cny
            if projection_facts
            else Decimal(0)
        )
        projection_terminal_basis_order_count = (
            _row_int(projection_facts["projection_terminal_basis_order_count"])
            if projection_facts
            else 0
        )
        projection_terminal_basis_sales_cny = (
            Decimal(projection_facts["projection_terminal_basis_sales_vnd"] or 0)
            / vnd_per_cny
            if projection_facts
            else Decimal(0)
        )
        projection_terminal_full_loss_sales_cny = (
            Decimal(
                projection_facts["projection_terminal_full_loss_sales_vnd"] or 0
            )
            / vnd_per_cny
            if projection_facts
            else Decimal(0)
        )
        projection_terminal_full_loss_order_count = (
            _row_int(
                projection_facts["projection_terminal_full_loss_order_count"]
            )
            if projection_facts
            else 0
        )
        projection_terminal_full_loss_qty = (
            _row_int(projection_facts["projection_terminal_full_loss_qty"])
            if projection_facts
            else 0
        )
        projection_completed_basis_order_count = (
            _row_int(projection_facts["projection_completed_basis_order_count"])
            if projection_facts
            else 0
        )
        projection_completed_full_loss_order_count = (
            _row_int(
                projection_facts["projection_completed_full_loss_order_count"]
            )
            if projection_facts
            else 0
        )
        projection_full_loss_basis_order_count = (
            _row_int(projection_facts["projection_full_loss_basis_order_count"])
            if projection_facts
            else 0
        )
        projection_basis_full_loss_order_count = (
            _row_int(projection_facts["projection_basis_full_loss_order_count"])
            if projection_facts
            else 0
        )
        projection_basis_full_loss_qty = (
            _row_int(projection_facts["projection_basis_full_loss_qty"])
            if projection_facts
            else 0
        )
        unsettled_order_count = (
            _row_int(projection_facts["unsettled_order_count"])
            if projection_facts
            else 0
        )
        full_loss_exposure_unsettled_order_count = (
            _row_int(
                projection_facts["full_loss_exposure_unsettled_order_count"]
            )
            if projection_facts
            else 0
        )
        unresolved_unsettled_order_count = (
            _row_int(projection_facts["unresolved_unsettled_order_count"])
            if projection_facts
            else 0
        )
        unresolved_full_loss_exposure_order_count = (
            _row_int(
                projection_facts["unresolved_full_loss_exposure_order_count"]
            )
            if projection_facts
            else 0
        )
        confirmed_unsettled_refund_amount_cny = (
            Decimal(projection_facts["confirmed_unsettled_refund_amount_vnd"] or 0)
            / vnd_per_cny
            if projection_facts
            else Decimal(0)
        )
        confirmed_full_loss_exposure_refund_amount_cny = (
            Decimal(
                projection_facts[
                    "confirmed_full_loss_exposure_refund_amount_vnd"
                ]
                or 0
            )
            / vnd_per_cny
            if projection_facts
            else Decimal(0)
        )
        confirmed_unsettled_full_loss_order_count = (
            _row_int(
                projection_facts["confirmed_unsettled_full_loss_order_count"]
            )
            if projection_facts
            else 0
        )
        confirmed_full_loss_exposure_order_count = (
            _row_int(
                projection_facts["confirmed_full_loss_exposure_order_count"]
            )
            if projection_facts
            else 0
        )
        confirmed_unsettled_full_loss_qty = (
            _row_int(projection_facts["confirmed_unsettled_full_loss_qty"])
            if projection_facts
            else 0
        )
        confirmed_full_loss_exposure_qty = (
            _row_int(projection_facts["confirmed_full_loss_exposure_qty"])
            if projection_facts
            else 0
        )
        unresolved_unsettled_qty = (
            _row_int(projection_facts["unresolved_unsettled_qty"])
            if projection_facts
            else 0
        )
        unresolved_full_loss_exposure_qty = (
            _row_int(projection_facts["unresolved_full_loss_exposure_qty"])
            if projection_facts
            else 0
        )
        full_loss_exposure_unsettled_sales_cny = (
            Decimal(
                projection_facts["full_loss_exposure_unsettled_sales_vnd"] or 0
            )
            / vnd_per_cny
            if projection_facts
            else Decimal(0)
        )
        unresolved_unsettled_sales_cny = (
            Decimal(projection_facts["unresolved_unsettled_sales_vnd"] or 0)
            / vnd_per_cny
            if projection_facts
            else Decimal(0)
        )
        # 成本解析（D1 全链）与纯 v10 公式。
        unit_cost_cny, cost_source = cost_map.get(pk, (K1_DEFAULT_CNY, "DEFAULT_K1"))
        fee_entry = shop_fee[int(cat["shop_pk"])]
        row_rate, row_fee_source = fee_entry.fee_rate, fee_entry.source
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
                unsettled_fee_rate=row_rate,
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

        one_minus_fee = Decimal(1) - row_rate
        projection = calculate_projection(
            ProjectionInput(
                projection_basis_order_count=projection_basis_order_count,
                projection_basis_qty=Decimal(projection_basis_qty),
                projection_basis_sales_cny=projection_basis_sales_cny,
                projection_basis_refund_amount_cny=(
                    projection_basis_refund_amount_cny
                ),
                projection_terminal_basis_order_count=(
                    projection_terminal_basis_order_count
                ),
                projection_terminal_basis_sales_cny=(
                    projection_terminal_basis_sales_cny
                ),
                projection_terminal_full_loss_sales_cny=(
                    projection_terminal_full_loss_sales_cny
                ),
                projection_terminal_full_loss_order_count=(
                    projection_terminal_full_loss_order_count
                ),
                projection_terminal_full_loss_qty=Decimal(
                    projection_terminal_full_loss_qty
                ),
                projection_completed_basis_order_count=(
                    projection_completed_basis_order_count
                ),
                projection_completed_full_loss_order_count=(
                    projection_completed_full_loss_order_count
                ),
                projection_full_loss_basis_order_count=(
                    projection_full_loss_basis_order_count
                ),
                projection_basis_full_loss_order_count=(
                    projection_basis_full_loss_order_count
                ),
                projection_basis_full_loss_qty=Decimal(
                    projection_basis_full_loss_qty
                ),
                unsettled_order_count=unsettled_order_count,
                confirmed_unsettled_full_loss_order_count=(
                    confirmed_unsettled_full_loss_order_count
                ),
                confirmed_unsettled_full_loss_qty=Decimal(
                    confirmed_unsettled_full_loss_qty
                ),
                unresolved_unsettled_order_count=(
                    unresolved_unsettled_order_count
                ),
                full_loss_exposure_unsettled_order_count=(
                    full_loss_exposure_unsettled_order_count
                ),
                confirmed_full_loss_exposure_order_count=(
                    confirmed_full_loss_exposure_order_count
                ),
                confirmed_full_loss_exposure_qty=Decimal(
                    confirmed_full_loss_exposure_qty
                ),
                unresolved_full_loss_exposure_order_count=(
                    unresolved_full_loss_exposure_order_count
                ),
                unresolved_full_loss_exposure_qty=Decimal(
                    unresolved_full_loss_exposure_qty
                ),
                unresolved_full_loss_exposure_cogs_cny=(
                    Decimal(unresolved_full_loss_exposure_qty) * unit_cost_cny
                ),
                unsettled_sales_after_fee_cny=(
                    unsettled_sales_cny * one_minus_fee
                ),
                full_loss_exposure_unsettled_sales_after_fee_cny=(
                    full_loss_exposure_unsettled_sales_cny * one_minus_fee
                ),
                confirmed_unsettled_refund_after_fee_cny=(
                    confirmed_unsettled_refund_amount_cny * one_minus_fee
                ),
                confirmed_full_loss_exposure_refund_after_fee_cny=(
                    confirmed_full_loss_exposure_refund_amount_cny
                    * one_minus_fee
                ),
                unresolved_unsettled_qty=Decimal(unresolved_unsettled_qty),
                unresolved_unsettled_sales_after_fee_cny=(
                    unresolved_unsettled_sales_cny * one_minus_fee
                ),
                unresolved_unsettled_cogs_cny=(
                    Decimal(unresolved_unsettled_qty) * unit_cost_cny
                ),
                settled_net_cny=settled_net_cny,
                observed_full_loss_qty=Decimal(full_loss_qty),
                observed_full_loss_cost_cny=return_loss_cny,
                current_unsettled_net_cny=formula.unsettled_net_cny,
                current_cogs_kept_cny=formula.cogs_kept_cny,
                cogs_total_cny=formula.cogs_all_cny,
                spend_cny=spend_cny,
                ad_gmv_cny=gmv_ad_cny,
                current_net_revenue_cny=net_revenue_cny,
                current_net_profit_cny=net_profit_cny,
            )
        )

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
                "ad_system_breakeven_roi_status": (FormulaStatus.ESTIMATED_KNOWN_COSTS),
                "fee_rate_used": row_rate,
                "fee_source": row_fee_source,
                "projection_status": projection.status,
                "projection_basis_order_count": projection_basis_order_count,
                "projection_basis_qty": projection_basis_qty,
                "projection_basis_sales": projection_basis_sales_cny,
                "projection_basis_refund_amount": (
                    projection_basis_refund_amount_cny
                ),
                "projection_terminal_basis_order_count": (
                    projection_terminal_basis_order_count
                ),
                "projection_terminal_basis_sales": (
                    projection_terminal_basis_sales_cny
                ),
                "projection_terminal_full_loss_sales": (
                    projection_terminal_full_loss_sales_cny
                ),
                "projection_terminal_full_loss_order_count": (
                    projection_terminal_full_loss_order_count
                ),
                "projection_terminal_full_loss_qty": (
                    projection_terminal_full_loss_qty
                ),
                "projection_completed_basis_order_count": (
                    projection_completed_basis_order_count
                ),
                "projection_completed_full_loss_order_count": (
                    projection_completed_full_loss_order_count
                ),
                "projection_full_loss_basis_order_count": (
                    projection_full_loss_basis_order_count
                ),
                "projection_basis_full_loss_order_count": (
                    projection_basis_full_loss_order_count
                ),
                "projection_basis_full_loss_qty": (
                    projection_basis_full_loss_qty
                ),
                "projection_refund_amount_rate": projection.refund_amount_rate,
                "pre_delivery_full_loss_rate": (
                    projection.pre_delivery_full_loss_rate
                ),
                "completed_full_loss_rate": projection.completed_full_loss_rate,
                "delivered_full_loss_rate": projection.delivered_full_loss_rate,
                "settled_full_loss_rate": projection.settled_full_loss_rate,
                "projection_full_loss_qty_rate": projection.full_loss_qty_rate,
                "unsettled_order_count": unsettled_order_count,
                "delivered_unsettled_order_count": max(
                    0,
                    unsettled_order_count
                    - full_loss_exposure_unsettled_order_count,
                ),
                "full_loss_exposure_unsettled_order_count": (
                    full_loss_exposure_unsettled_order_count
                ),
                "confirmed_full_loss_exposure_order_count": (
                    confirmed_full_loss_exposure_order_count
                ),
                "confirmed_full_loss_exposure_qty": (
                    confirmed_full_loss_exposure_qty
                ),
                "unresolved_unsettled_order_count": (
                    unresolved_unsettled_order_count
                ),
                "unresolved_full_loss_exposure_order_count": (
                    unresolved_full_loss_exposure_order_count
                ),
                "unresolved_unsettled_qty": unresolved_unsettled_qty,
                "unresolved_full_loss_exposure_qty": (
                    unresolved_full_loss_exposure_qty
                ),
                "unresolved_unsettled_sales": unresolved_unsettled_sales_cny,
                "full_loss_exposure_unsettled_sales": (
                    full_loss_exposure_unsettled_sales_cny
                ),
                "confirmed_unsettled_refund_amount": (
                    confirmed_unsettled_refund_amount_cny
                ),
                "confirmed_full_loss_exposure_refund_amount": (
                    confirmed_full_loss_exposure_refund_amount_cny
                ),
                "confirmed_unsettled_full_loss_order_count": (
                    confirmed_unsettled_full_loss_order_count
                ),
                "confirmed_unsettled_full_loss_qty": (
                    confirmed_unsettled_full_loss_qty
                ),
                "projected_future_refund_amount": (
                    projection.projected_future_refund_amount_cny
                ),
                "projected_terminal_refund_amount": (
                    projection.projected_terminal_refund_amount_cny
                ),
                "projected_future_full_loss_order_count": (
                    projection.projected_future_full_loss_order_count
                ),
                "projected_future_full_loss_qty": (
                    projection.projected_future_full_loss_qty
                ),
                "projected_terminal_full_loss_qty": (
                    projection.projected_terminal_full_loss_qty
                ),
                "projected_full_loss_cost": (
                    projection.projected_full_loss_cost_cny
                ),
                "projected_unsettled_net": projection.projected_unsettled_net_cny,
                "projected_net_revenue": projection.projected_net_revenue_cny,
                "projected_net_profit": projection.projected_net_profit_cny,
                "projected_roi_real": projection.projected_roi_real,
                "projected_roi_breakeven": (
                    projection.projected_roi_breakeven
                ),
                "projected_nc_prime": projection.projected_nc_prime_cny,
                "projected_cogs_kept": projection.projected_cogs_kept_cny,
                "projected_ad_gmv": projection.projected_ad_gmv_cny,
                "projected_ad_system_actual_roi": (
                    projection.projected_ad_system_actual_roi
                ),
                "projected_ad_system_max_ad_spend": (
                    projection.projected_ad_system_max_ad_spend_cny
                ),
                "projected_ad_system_breakeven_roi": (
                    projection.projected_ad_system_breakeven_roi
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

    # 排序（None 沉底；spend 后以 spu_pk ASC 最终决胜，分页稳定）。
    def _key(r: dict) -> tuple[bool, Decimal, Decimal, int]:
        v = r[sort_field]
        if v is None:
            return (True, Decimal(0), Decimal(0), int(r["spu_pk"]))
        primary = v if ascending else -v
        tie = -r["spend"] if ascending else r["spend"]
        return (False, primary, tie, int(r["spu_pk"]))

    plain.sort(key=_key)

    # totals（§3.3：跨分页/当前筛选；行级 CNY 服务端加总）
    money_total: dict[str, Decimal] = {
        "spend": sum((r["spend"] for r in scope_plain), Decimal(0)),
        "sales": sum((r["sales"] for r in scope_plain), Decimal(0)),
        "effective_sales": sum((r["effective_sales"] for r in scope_plain), Decimal(0)),
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
    ad_system_max_ad_spend = money_total["net_revenue"] - money_total["cogs_total"]
    ad_system_remaining_ad_spend_capacity = (
        ad_system_max_ad_spend - money_total["spend"]
    )
    ad_system_breakeven_roi: Decimal | None = None
    if ad_system_max_ad_spend > 0 and money_total["gmv_ad"] > 0:
        ad_system_breakeven_roi = money_total["gmv_ad"] / ad_system_max_ad_spend

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

    projection_counts_row = None
    if spu_pks_in_scope:
        projection_counts_row = (
            sess.execute(
                _SQL_ROI_PROJECTION_SCOPE_COUNTS,
                {
                    "selected_pks": spu_pks_in_scope,
                    "paid_statuses": paid_statuses,
                    "st0": st0,
                    "st1": st1,
                    "delivery_terminal_order_statuses": list(
                        _DELIVERY_TERMINAL_ORDER_STATUSES
                    ),
                    "delivery_terminal_shipment_statuses": list(
                        _DELIVERY_TERMINAL_SHIPMENT_STATUSES
                    ),
                    "delivered_action_code": _TRACK_ACTION_CODE_DELIVERED,
                    "overseas_action_code": _TRACK_ACTION_CODE_OVERSEAS,
                    "returned_to_seller_action_code": (
                        _TRACK_ACTION_CODE_RETURNED_TO_SELLER
                    ),
                    "ws": ws_dt,
                    "we": we_dt,
                },
            )
            .mappings()
            .first()
        )
    total_projection_basis_order_count = (
        _row_int(projection_counts_row["projection_basis_order_count"])
        if projection_counts_row
        else 0
    )
    total_projection_terminal_basis_order_count = (
        _row_int(projection_counts_row["projection_terminal_basis_order_count"])
        if projection_counts_row
        else 0
    )
    total_projection_terminal_full_loss_order_count = (
        _row_int(
            projection_counts_row["projection_terminal_full_loss_order_count"]
        )
        if projection_counts_row
        else 0
    )
    total_projection_completed_basis_order_count = (
        _row_int(projection_counts_row["projection_completed_basis_order_count"])
        if projection_counts_row
        else 0
    )
    total_projection_completed_full_loss_order_count = (
        _row_int(
            projection_counts_row["projection_completed_full_loss_order_count"]
        )
        if projection_counts_row
        else 0
    )
    total_projection_full_loss_basis_order_count = (
        _row_int(projection_counts_row["projection_full_loss_basis_order_count"])
        if projection_counts_row
        else 0
    )
    total_projection_basis_full_loss_order_count = (
        _row_int(projection_counts_row["projection_basis_full_loss_order_count"])
        if projection_counts_row
        else 0
    )
    total_unsettled_order_count = (
        _row_int(projection_counts_row["unsettled_order_count"])
        if projection_counts_row
        else 0
    )
    total_full_loss_exposure_unsettled_order_count = (
        _row_int(
            projection_counts_row["full_loss_exposure_unsettled_order_count"]
        )
        if projection_counts_row
        else 0
    )
    total_confirmed_unsettled_full_loss_order_count = (
        _row_int(
            projection_counts_row["confirmed_unsettled_full_loss_order_count"]
        )
        if projection_counts_row
        else 0
    )
    total_confirmed_full_loss_exposure_order_count = (
        _row_int(
            projection_counts_row["confirmed_full_loss_exposure_order_count"]
        )
        if projection_counts_row
        else 0
    )
    total_unresolved_unsettled_order_count = (
        _row_int(projection_counts_row["unresolved_unsettled_order_count"])
        if projection_counts_row
        else 0
    )
    total_unresolved_full_loss_exposure_order_count = (
        _row_int(
            projection_counts_row["unresolved_full_loss_exposure_order_count"]
        )
        if projection_counts_row
        else 0
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

    total_projection_basis_qty = sum(
        (r["projection_basis_qty"] for r in scope_plain), 0
    )
    total_projection_basis_sales = sum(
        (r["projection_basis_sales"] for r in scope_plain), Decimal(0)
    )
    total_projection_basis_refund_amount = sum(
        (r["projection_basis_refund_amount"] for r in scope_plain), Decimal(0)
    )
    total_projection_terminal_basis_sales = sum(
        (r["projection_terminal_basis_sales"] for r in scope_plain), Decimal(0)
    )
    total_projection_terminal_full_loss_sales = sum(
        (r["projection_terminal_full_loss_sales"] for r in scope_plain),
        Decimal(0),
    )
    total_projection_terminal_full_loss_qty = sum(
        (r["projection_terminal_full_loss_qty"] for r in scope_plain), 0
    )
    total_projection_basis_full_loss_qty = sum(
        (r["projection_basis_full_loss_qty"] for r in scope_plain), 0
    )
    total_unresolved_unsettled_qty = sum(
        (r["unresolved_unsettled_qty"] for r in scope_plain), 0
    )
    total_unresolved_full_loss_exposure_qty = sum(
        (r["unresolved_full_loss_exposure_qty"] for r in scope_plain), 0
    )
    total_unresolved_unsettled_sales = sum(
        (r["unresolved_unsettled_sales"] for r in scope_plain), Decimal(0)
    )
    total_full_loss_exposure_unsettled_sales = sum(
        (r["full_loss_exposure_unsettled_sales"] for r in scope_plain),
        Decimal(0),
    )
    total_confirmed_unsettled_refund_amount = sum(
        (r["confirmed_unsettled_refund_amount"] for r in scope_plain),
        Decimal(0),
    )
    total_confirmed_full_loss_exposure_refund_amount = sum(
        (r["confirmed_full_loss_exposure_refund_amount"] for r in scope_plain),
        Decimal(0),
    )
    total_confirmed_unsettled_full_loss_qty = sum(
        (r["confirmed_unsettled_full_loss_qty"] for r in scope_plain), 0
    )
    total_confirmed_full_loss_exposure_qty = sum(
        (r["confirmed_full_loss_exposure_qty"] for r in scope_plain), 0
    )
    total_unsettled_sales_after_fee = sum(
        (
            r["unsettled_sales"] * (Decimal(1) - r["fee_rate_used"])
            for r in scope_plain
        ),
        Decimal(0),
    )
    total_confirmed_refund_after_fee = sum(
        (
            r["confirmed_unsettled_refund_amount"]
            * (Decimal(1) - r["fee_rate_used"])
            for r in scope_plain
        ),
        Decimal(0),
    )
    total_full_loss_exposure_sales_after_fee = sum(
        (
            r["full_loss_exposure_unsettled_sales"]
            * (Decimal(1) - r["fee_rate_used"])
            for r in scope_plain
        ),
        Decimal(0),
    )
    total_confirmed_full_loss_exposure_refund_after_fee = sum(
        (
            r["confirmed_full_loss_exposure_refund_amount"]
            * (Decimal(1) - r["fee_rate_used"])
            for r in scope_plain
        ),
        Decimal(0),
    )
    total_unresolved_sales_after_fee = sum(
        (
            r["unresolved_unsettled_sales"]
            * (Decimal(1) - r["fee_rate_used"])
            for r in scope_plain
        ),
        Decimal(0),
    )
    total_unresolved_cogs = sum(
        (
            Decimal(r["unresolved_unsettled_qty"]) * r["unit_cost_used"]
            for r in scope_plain
        ),
        Decimal(0),
    )
    total_unresolved_full_loss_exposure_cogs = sum(
        (
            Decimal(r["unresolved_full_loss_exposure_qty"])
            * r["unit_cost_used"]
            for r in scope_plain
        ),
        Decimal(0),
    )
    dashboard_projection = calculate_projection(
        ProjectionInput(
            projection_basis_order_count=total_projection_basis_order_count,
            projection_basis_qty=Decimal(total_projection_basis_qty),
            projection_basis_sales_cny=total_projection_basis_sales,
            projection_basis_refund_amount_cny=(
                total_projection_basis_refund_amount
            ),
            projection_terminal_basis_order_count=(
                total_projection_terminal_basis_order_count
            ),
            projection_terminal_basis_sales_cny=(
                total_projection_terminal_basis_sales
            ),
            projection_terminal_full_loss_sales_cny=(
                total_projection_terminal_full_loss_sales
            ),
            projection_terminal_full_loss_order_count=(
                total_projection_terminal_full_loss_order_count
            ),
            projection_terminal_full_loss_qty=Decimal(
                total_projection_terminal_full_loss_qty
            ),
            projection_completed_basis_order_count=(
                total_projection_completed_basis_order_count
            ),
            projection_completed_full_loss_order_count=(
                total_projection_completed_full_loss_order_count
            ),
            projection_full_loss_basis_order_count=(
                total_projection_full_loss_basis_order_count
            ),
            projection_basis_full_loss_order_count=(
                total_projection_basis_full_loss_order_count
            ),
            projection_basis_full_loss_qty=Decimal(
                total_projection_basis_full_loss_qty
            ),
            unsettled_order_count=total_unsettled_order_count,
            confirmed_unsettled_full_loss_order_count=(
                total_confirmed_unsettled_full_loss_order_count
            ),
            confirmed_unsettled_full_loss_qty=Decimal(
                total_confirmed_unsettled_full_loss_qty
            ),
            unresolved_unsettled_order_count=(
                total_unresolved_unsettled_order_count
            ),
            full_loss_exposure_unsettled_order_count=(
                total_full_loss_exposure_unsettled_order_count
            ),
            confirmed_full_loss_exposure_order_count=(
                total_confirmed_full_loss_exposure_order_count
            ),
            confirmed_full_loss_exposure_qty=Decimal(
                total_confirmed_full_loss_exposure_qty
            ),
            unresolved_full_loss_exposure_order_count=(
                total_unresolved_full_loss_exposure_order_count
            ),
            unresolved_full_loss_exposure_qty=Decimal(
                total_unresolved_full_loss_exposure_qty
            ),
            unresolved_full_loss_exposure_cogs_cny=(
                total_unresolved_full_loss_exposure_cogs
            ),
            unsettled_sales_after_fee_cny=total_unsettled_sales_after_fee,
            full_loss_exposure_unsettled_sales_after_fee_cny=(
                total_full_loss_exposure_sales_after_fee
            ),
            confirmed_unsettled_refund_after_fee_cny=(
                total_confirmed_refund_after_fee
            ),
            confirmed_full_loss_exposure_refund_after_fee_cny=(
                total_confirmed_full_loss_exposure_refund_after_fee
            ),
            unresolved_unsettled_qty=Decimal(total_unresolved_unsettled_qty),
            unresolved_unsettled_sales_after_fee_cny=(
                total_unresolved_sales_after_fee
            ),
            unresolved_unsettled_cogs_cny=total_unresolved_cogs,
            settled_net_cny=sum(
                (r["settled_net"] for r in scope_plain), Decimal(0)
            ),
            observed_full_loss_qty=Decimal(total_full_loss_qty),
            observed_full_loss_cost_cny=money_total["return_loss"],
            current_unsettled_net_cny=sum(
                (r["unsettled_net"] for r in scope_plain), Decimal(0)
            ),
            current_cogs_kept_cny=total_cogs_kept,
            cogs_total_cny=money_total["cogs_total"],
            spend_cny=money_total["spend"],
            ad_gmv_cny=money_total["gmv_ad"],
            current_net_revenue_cny=money_total["net_revenue"],
            current_net_profit_cny=money_total["net_profit"],
        )
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
        ad_system_remaining_ad_spend_capacity=(ad_system_remaining_ad_spend_capacity),
        ad_system_breakeven_roi_status=(FormulaStatus.ESTIMATED_KNOWN_COSTS),
        projection_status=dashboard_projection.status,
        projection_basis_order_count=total_projection_basis_order_count,
        projection_basis_qty=total_projection_basis_qty,
        projection_basis_sales=total_projection_basis_sales,
        projection_basis_refund_amount=total_projection_basis_refund_amount,
        projection_terminal_basis_order_count=(
            total_projection_terminal_basis_order_count
        ),
        projection_terminal_basis_sales=total_projection_terminal_basis_sales,
        projection_terminal_full_loss_sales=(
            total_projection_terminal_full_loss_sales
        ),
        projection_terminal_full_loss_order_count=(
            total_projection_terminal_full_loss_order_count
        ),
        projection_terminal_full_loss_qty=(
            total_projection_terminal_full_loss_qty
        ),
        projection_completed_basis_order_count=(
            total_projection_completed_basis_order_count
        ),
        projection_completed_full_loss_order_count=(
            total_projection_completed_full_loss_order_count
        ),
        projection_full_loss_basis_order_count=(
            total_projection_full_loss_basis_order_count
        ),
        projection_basis_full_loss_order_count=(
            total_projection_basis_full_loss_order_count
        ),
        projection_basis_full_loss_qty=total_projection_basis_full_loss_qty,
        projection_refund_amount_rate=dashboard_projection.refund_amount_rate,
        pre_delivery_full_loss_rate=(
            dashboard_projection.pre_delivery_full_loss_rate
        ),
        completed_full_loss_rate=dashboard_projection.completed_full_loss_rate,
        delivered_full_loss_rate=dashboard_projection.delivered_full_loss_rate,
        settled_full_loss_rate=dashboard_projection.settled_full_loss_rate,
        projection_full_loss_qty_rate=dashboard_projection.full_loss_qty_rate,
        unsettled_order_count=total_unsettled_order_count,
        delivered_unsettled_order_count=max(
            0,
            total_unsettled_order_count
            - total_full_loss_exposure_unsettled_order_count,
        ),
        full_loss_exposure_unsettled_order_count=(
            total_full_loss_exposure_unsettled_order_count
        ),
        full_loss_exposure_unsettled_sales=(
            total_full_loss_exposure_unsettled_sales
        ),
        confirmed_full_loss_exposure_order_count=(
            total_confirmed_full_loss_exposure_order_count
        ),
        confirmed_full_loss_exposure_qty=(
            total_confirmed_full_loss_exposure_qty
        ),
        confirmed_full_loss_exposure_refund_amount=(
            total_confirmed_full_loss_exposure_refund_amount
        ),
        unresolved_unsettled_order_count=(
            total_unresolved_unsettled_order_count
        ),
        unresolved_full_loss_exposure_order_count=(
            total_unresolved_full_loss_exposure_order_count
        ),
        unresolved_unsettled_qty=total_unresolved_unsettled_qty,
        unresolved_full_loss_exposure_qty=(
            total_unresolved_full_loss_exposure_qty
        ),
        unresolved_unsettled_sales=total_unresolved_unsettled_sales,
        confirmed_unsettled_refund_amount=(
            total_confirmed_unsettled_refund_amount
        ),
        confirmed_unsettled_full_loss_order_count=(
            total_confirmed_unsettled_full_loss_order_count
        ),
        confirmed_unsettled_full_loss_qty=(
            total_confirmed_unsettled_full_loss_qty
        ),
        projected_future_refund_amount=(
            dashboard_projection.projected_future_refund_amount_cny
        ),
        projected_terminal_refund_amount=(
            dashboard_projection.projected_terminal_refund_amount_cny
        ),
        projected_future_full_loss_order_count=(
            dashboard_projection.projected_future_full_loss_order_count
        ),
        projected_future_full_loss_qty=(
            dashboard_projection.projected_future_full_loss_qty
        ),
        projected_terminal_full_loss_qty=(
            dashboard_projection.projected_terminal_full_loss_qty
        ),
        projected_full_loss_cost=(
            dashboard_projection.projected_full_loss_cost_cny
        ),
        projected_unsettled_net=(
            dashboard_projection.projected_unsettled_net_cny
        ),
        projected_net_revenue=dashboard_projection.projected_net_revenue_cny,
        projected_net_profit=dashboard_projection.projected_net_profit_cny,
        projected_roi_real=dashboard_projection.projected_roi_real,
        projected_roi_breakeven=(
            dashboard_projection.projected_roi_breakeven
        ),
        projected_nc_prime=dashboard_projection.projected_nc_prime_cny,
        projected_cogs_kept=dashboard_projection.projected_cogs_kept_cny,
        projected_ad_gmv=dashboard_projection.projected_ad_gmv_cny,
        projected_ad_system_actual_roi=(
            dashboard_projection.projected_ad_system_actual_roi
        ),
        projected_ad_system_max_ad_spend=(
            dashboard_projection.projected_ad_system_max_ad_spend_cny
        ),
        projected_ad_system_breakeven_roi=(
            dashboard_projection.projected_ad_system_breakeven_roi
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
    if any(row["settled_order_count"] < row["order_count"] for row in scope_plain):
        warnings.append("unsettled_orders_estimated")
    if any(
        row["projection_status"] is ProjectionStatus.INSUFFICIENT_SAMPLE
        for row in scope_plain
    ):
        warnings.append("projection_insufficient_sample")
    if any(row["unsettled_order_count"] > 0 for row in scope_plain):
        warnings.append("projection_uses_completed_order_full_loss_rate")
    warnings.append("ad_system_other_necessary_costs_not_modeled")

    # 费率 meta：标量 source/rate 在 scope 内口径唯一时可信；多口径混合时
    # 为 "mixed" + 基线参考值。per_shop 携带逐店铺真实口径与实测样本
    # （含覆盖率、样本量、快照日期），前端/对账以它为准。
    fee_per_shop = [shop_fee[spk] for spk in sorted(shop_fee)]
    if override_rate is not None:
        meta_fee_rate, meta_fee_source = override_rate, "user_override"
    else:
        distinct = {(e.source, e.fee_rate) for e in fee_per_shop}
        if len(distinct) == 1:
            meta_fee_source, meta_fee_rate = next(iter(distinct))
        elif not distinct:
            meta_fee_source, meta_fee_rate = "baseline", FEE_RATE_BASELINE
        else:
            meta_fee_source, meta_fee_rate = "mixed", FEE_RATE_BASELINE

    basis = ProfitabilityBasis(
        calculated_at=calculated_at,
        fx=fx_basis,
        fee_rate=meta_fee_rate,
        fee_source=meta_fee_source,
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
        fee_per_shop=tuple(fee_per_shop),
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
