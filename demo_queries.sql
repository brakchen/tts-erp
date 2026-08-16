-- =============================================================================
-- tts_erp demo queries
-- 用途：DBeaver / psql 直接跑，看 9 张表的数据湖
-- 适用于 PostgreSQL 16+（postgresql://postgres:...@192.168.47.130:5432/tts_erp）
-- 表：shops, orders, order_items, order_shippings, statements, payments,
--      returns, cancellations, sync_log
--
-- 用法：可以一段一段选中跑（Ctrl+Enter），也可以整文件跑（F5）
-- =============================================================================

\set ON_ERROR_STOP on

-- 0. 基础健康度：每张表多少行、最近一次同步时间
-- 注：order_items 表 schema 没有时间列（2026-08-16 漏的）—— 用 NULL 占位
-- -----------------------------------------------------------------------------
SELECT 'shops'          AS table_name, count(*) AS rows, max(last_seen_at)  AS last_event FROM shops
UNION ALL SELECT 'orders',          count(*), max(synced_at)   FROM orders
UNION ALL SELECT 'order_items',     count(*), NULL::timestamptz              FROM order_items
UNION ALL SELECT 'order_shippings', count(*), max(synced_at)   FROM order_shippings
UNION ALL SELECT 'statements',      count(*), max(synced_at)   FROM statements
UNION ALL SELECT 'payments',        count(*), max(synced_at)   FROM payments
UNION ALL SELECT 'returns',         count(*), max(synced_at)   FROM returns
UNION ALL SELECT 'cancellations',   count(*), max(synced_at)   FROM cancellations
UNION ALL SELECT 'sync_log',        count(*), max(finished_at) FROM sync_log
ORDER BY table_name;


-- 1. 已授权 shop 概览
-- -----------------------------------------------------------------------------
SELECT shop_id, shop_name, shop_region, seller_type, last_seen_at
FROM shops
ORDER BY last_seen_at DESC NULLS LAST;


-- 2. 订单状态分布（用 202309 spec 的字符串状态名）
-- -----------------------------------------------------------------------------
SELECT order_status_name, count(*) AS n, sum(payment_amount) AS total_amount, payment_currency
FROM orders
WHERE shop_id = '7494763368967603447'
GROUP BY order_status_name, payment_currency
ORDER BY n DESC;


-- 3. 最近 30 天每日下单量 + GMV
-- -----------------------------------------------------------------------------
SELECT
    to_timestamp(create_time) AT TIME ZONE 'UTC'::text AS day_utc,
    count(*) AS orders,
    sum(payment_amount)::numeric(18, 2) AS gmv,
    payment_currency
FROM orders
WHERE shop_id = '7494763368967603447'
  AND create_time >= extract(epoch from now() - interval '30 days')::bigint
GROUP BY day_utc, payment_currency
ORDER BY day_utc DESC;


-- 4. Top 10 SKU（按 GMV 排）
-- -----------------------------------------------------------------------------
SELECT
    oi.sku_id,
    oi.sku_name,
    count(DISTINCT oi.order_id) AS order_lines,
    sum(oi.quantity)            AS total_qty,
    sum(oi.sku_price * oi.quantity)::numeric(18, 2) AS gmv,
    oi.shop_id
FROM order_items oi
WHERE oi.shop_id = '7494763368967603447'
GROUP BY oi.sku_id, oi.sku_name, oi.shop_id
ORDER BY gmv DESC NULLS LAST
LIMIT 10;


-- 5. 退货：状态分布 + 原因
-- -----------------------------------------------------------------------------
SELECT
    return_status,
    return_reason,
    count(*) AS n,
    sum((raw->'refund_amount'->>'refund_total')::numeric) AS total_refund
FROM returns
WHERE shop_id = '7494763368967603447'
GROUP BY return_status, return_reason
ORDER BY n DESC;


-- 6. 取消：业务原因 + 类型
-- -----------------------------------------------------------------------------
SELECT
    cancel_type,
    cancel_reason_text,
    role,
    count(*) AS n
FROM cancellations
WHERE shop_id = '7494763368967603447'
GROUP BY cancel_type, cancel_reason_text, role
ORDER BY n DESC;


-- 7. 取消率 = cancellations / orders
-- -----------------------------------------------------------------------------
WITH o AS (
    SELECT count(*) AS n_orders FROM orders WHERE shop_id = '7494763368967603447'
), c AS (
    SELECT count(*) AS n_canc FROM cancellations WHERE shop_id = '7494763368967603447'
), r AS (
    SELECT count(*) AS n_ret FROM returns WHERE shop_id = '7494763368967603447'
)
SELECT
    o.n_orders,
    c.n_canc,
    r.n_ret,
    round(100.0 * c.n_canc / o.n_orders, 2) AS cancel_pct,
    round(100.0 * r.n_ret  / o.n_orders, 2) AS return_pct
FROM o, c, r;


-- 8. 跨表关联：找到"被退货的订单"
-- -----------------------------------------------------------------------------
SELECT
    o.order_id,
    o.order_status_name,
    o.payment_amount,
    o.payment_currency,
    r.return_id,
    r.return_status,
    r.return_reason,
    r.raw->'refund_amount'->>'refund_total' AS refund_total
FROM orders o
JOIN returns r ON r.order_id = o.order_id
WHERE o.shop_id = '7494763368967603447'
ORDER BY o.create_time DESC
LIMIT 20;


-- 9. 跨表关联：找到"被取消的订单"
-- -----------------------------------------------------------------------------
SELECT
    o.order_id,
    o.order_status_name,
    o.payment_amount,
    o.payment_currency,
    c.cancel_id,
    c.cancel_status,
    c.cancel_reason_text,
    c.cancel_type,
    c.role
FROM orders o
JOIN cancellations c ON c.order_id = o.order_id
WHERE o.shop_id = '7494763368967603447'
ORDER BY o.create_time DESC
LIMIT 20;


-- 10. 财务对账单：每张对账单的净额 + 费用
-- -----------------------------------------------------------------------------
SELECT
    statement_id,
    currency,
    payment_status,
    to_timestamp(statement_time) AT TIME ZONE 'UTC'::text AS period_end,
    to_timestamp(payment_time)   AT TIME ZONE 'UTC'::text AS paid_at,
    revenue_amount,
    fee_amount,
    net_sales_amount,
    shipping_cost_amount,
    adjustment_amount,
    settlement_amount
FROM statements
WHERE shop_id = '7494763368967603447'
ORDER BY statement_time DESC
LIMIT 20;


-- 11. 财务汇总：总 GMV / 总费用 / 总净额 / 总结算
-- -----------------------------------------------------------------------------
SELECT
    currency,
    count(*)                                  AS statement_count,
    sum(revenue_amount)::numeric(18, 2)       AS total_revenue,
    sum(fee_amount)::numeric(18, 2)           AS total_fee,
    sum(net_sales_amount)::numeric(18, 2)     AS total_net_sales,
    sum(shipping_cost_amount)::numeric(18, 2) AS total_shipping,
    sum(adjustment_amount)::numeric(18, 2)    AS total_adjustment,
    sum(settlement_amount)::numeric(18, 2)    AS total_settlement
FROM statements
WHERE shop_id = '7494763368967603447'
GROUP BY currency;


-- 12. 付款：每笔付款 + 关联的 statement
-- -----------------------------------------------------------------------------
SELECT
    p.payment_id,
    p.status,
    p.bank_account,
    p.amount_value            AS paid_amount,
    p.settlement_amount_value AS settlement_amount,
    p.reserve_amount_value    AS reserve,
    p.exchange_rate,
    to_timestamp(p.create_time) AT TIME ZONE 'UTC'::text AS created_at,
    to_timestamp(p.paid_time)   AT TIME ZONE 'UTC'::text AS paid_at,
    s.statement_id
FROM payments p
LEFT JOIN statements s ON s.payment_id = p.payment_id
WHERE p.shop_id = '7494763368967603447'
ORDER BY p.create_time DESC
LIMIT 20;


-- 13. 物流：哪些订单还在等揽收 / 已发货 / 已签收
-- -----------------------------------------------------------------------------
SELECT
    s.order_id,
    o.order_status_name,
    s.tracking_number,
    s.shipping_provider_name
FROM order_shippings s
JOIN orders o ON o.order_id = s.order_id
WHERE o.shop_id = '7494763368967603447'
  AND o.order_status_name IN ('AWAITING_SHIPMENT', 'IN_TRANSIT', 'DELIVERED')
ORDER BY o.create_time DESC
LIMIT 20;


-- 14. JSONB 探险：每个退货带了多少 line item
-- -----------------------------------------------------------------------------
SELECT
    return_id,
    order_id,
    return_status,
    jsonb_array_length(raw->'return_line_items') AS n_lines,
    raw->'refund_amount'->>'currency'             AS ccy,
    (raw->'refund_amount'->>'refund_total')::numeric(18, 2) AS refund_total,
    raw->>'handover_method'                       AS handover,
    raw->>'is_quick_refund'                       AS quick_refund
FROM returns
WHERE shop_id = '7494763368967603447'
ORDER BY create_time DESC
LIMIT 20;


-- 15. 同步历史
-- -----------------------------------------------------------------------------
SELECT
    sync_type,
    status,
    rows_affected                       AS rows,
    started_at AT TIME ZONE 'UTC'::text AS started,
    error_message
FROM sync_log
WHERE shop_id = '7494763368967603447'
ORDER BY id DESC
LIMIT 20;


-- 16. 数据质量检查：有没有"孤儿"
-- -----------------------------------------------------------------------------
-- 16.1 退货里 order_id 在 orders 表不存在的
SELECT 'returns_orphan' AS check, count(*) AS bad_rows
FROM returns r
WHERE r.shop_id = '7494763368967603447'
  AND NOT EXISTS (SELECT 1 FROM orders o WHERE o.order_id = r.order_id);

-- 16.2 取消里 order_id 在 orders 表不存在的
SELECT 'cancellations_orphan', count(*)
FROM cancellations c
WHERE c.shop_id = '7494763368967603447'
  AND NOT EXISTS (SELECT 1 FROM orders o WHERE o.order_id = c.order_id);

-- 16.3 订单里没有 line items 的
SELECT 'orders_no_items', count(*)
FROM orders o
WHERE o.shop_id = '7494763368967603447'
  AND NOT EXISTS (SELECT 1 FROM order_items i WHERE i.order_id = o.order_id);

-- 16.4 statement 的 payment_id 在 payments 表不存在的
SELECT 'statements_orphan_payment', count(*)
FROM statements s
WHERE s.shop_id = '7494763368967603447'
  AND s.payment_id IS NOT NULL
  AND NOT EXISTS (SELECT 1 FROM payments p WHERE p.payment_id = s.payment_id);

-- 16.5 任意行 raw 为 NULL（不可能但兜底）
SELECT 'returns_raw_null', count(*) FROM returns WHERE raw IS NULL
UNION ALL SELECT 'cancellations_raw_null', count(*) FROM cancellations WHERE raw IS NULL
UNION ALL SELECT 'orders_raw_null',        count(*) FROM orders       WHERE raw IS NULL;
