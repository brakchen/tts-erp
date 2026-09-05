-- =============================================================================
-- 0007: commerce 域命名重构（ADR-0003 §2.6 + D1 拍板）
--   tables:  channel_accounts->shops | channel_products->products_spu | channel_product_variants->products_sku
--   FKs 一律 <业务名>_pk;上游文本 id 改业务名;sales_orders 时间列业务化
--   views:  linkage.effective_product_links / analytics.ad_product_links 重建
--
-- 实施前提:代码已切到新命名(同一 commit 内);ALTER 为瞬时操作(表行数小),
--   但窗口内旧码查询会报"列/表不存在",须随即重启 tts-erp + tts-erp-sync。
-- 幂等性:不可重复执行(无 IF);如需重跑,先 git 还原 schema 或从备份恢复。
-- 依据:tech-doc/adr/0003-commerce-naming-refactor-pending.md
-- =============================================================================

-- ---------- 1. 表改名 ----------
ALTER TABLE commerce.channel_accounts       RENAME TO shops;
ALTER TABLE commerce.channel_products       RENAME TO products_spu;
ALTER TABLE commerce.channel_product_variants RENAME TO products_sku;

-- ---------- 2. 列改名 ----------
-- commerce.shops
ALTER TABLE commerce.shops RENAME COLUMN external_account_id TO shop_id;

-- commerce.products_spu
ALTER TABLE commerce.products_spu RENAME COLUMN channel_account_id TO shop_pk;
ALTER TABLE commerce.products_spu RENAME COLUMN external_product_id TO spu_id;

-- commerce.products_sku
ALTER TABLE commerce.products_sku RENAME COLUMN channel_product_id TO spu_pk;
ALTER TABLE commerce.products_sku RENAME COLUMN external_variant_id TO sku_id;

-- commerce.sales_orders (D1: 时间列业务化)
ALTER TABLE commerce.sales_orders RENAME COLUMN channel_account_id TO shop_pk;
ALTER TABLE commerce.sales_orders RENAME COLUMN external_order_id   TO order_id;
ALTER TABLE commerce.sales_orders RENAME COLUMN source_created_at   TO order_time;
ALTER TABLE commerce.sales_orders RENAME COLUMN source_updated_at   TO order_modify_time;

-- commerce.sales_order_lines
ALTER TABLE commerce.sales_order_lines RENAME COLUMN sales_order_id            TO order_pk;
ALTER TABLE commerce.sales_order_lines RENAME COLUMN channel_product_id        TO spu_pk;
ALTER TABLE commerce.sales_order_lines RENAME COLUMN channel_product_variant_id TO sku_pk;

-- after_sales.cases
ALTER TABLE after_sales.cases RENAME COLUMN channel_account_id TO shop_pk;
ALTER TABLE after_sales.cases RENAME COLUMN sales_order_id     TO order_pk;

-- finance.payouts
ALTER TABLE finance.payouts RENAME COLUMN channel_account_id TO shop_pk;

-- finance.settlement_transactions
ALTER TABLE finance.settlement_transactions RENAME COLUMN sales_order_id TO order_pk;

-- fulfillment.shipments
ALTER TABLE fulfillment.shipments RENAME COLUMN sales_order_id TO order_pk;

-- linkage.account_links
ALTER TABLE linkage.account_links RENAME COLUMN channel_account_id TO shop_pk;

-- linkage.product_links / link_overrides / link_issues
ALTER TABLE linkage.product_links  RENAME COLUMN channel_product_id TO spu_pk;
ALTER TABLE linkage.link_overrides RENAME COLUMN channel_product_id TO spu_pk;
ALTER TABLE linkage.link_issues    RENAME COLUMN channel_product_id TO spu_pk;

-- linkage.variant_links
ALTER TABLE linkage.variant_links RENAME COLUMN channel_product_variant_id TO sku_pk;

-- procurement.manual_product_costs
ALTER TABLE procurement.manual_product_costs RENAME COLUMN channel_product_id TO spu_pk;

-- reporting.product_cost_snapshots / product_profit_daily
ALTER TABLE reporting.product_cost_snapshots RENAME COLUMN channel_product_id TO spu_pk;
ALTER TABLE reporting.product_profit_daily   RENAME COLUMN channel_product_id TO spu_pk;

-- procurement.spu_images (storage schema SQL,无 ORM 模型)
ALTER TABLE procurement.spu_images RENAME COLUMN channel_account_id  TO shop_pk;
ALTER TABLE procurement.spu_images RENAME COLUMN channel_product_id  TO spu_pk;

-- ---------- 3. 视图重建(改 source 引用;输出列同步 _pk 化,遵循 D2)
-- 注意:输出列名变了(CREATE OR REPLACE 不允许改列名),必须先 DROP 再 CREATE
DROP VIEW IF EXISTS linkage.effective_product_links;
CREATE VIEW linkage.effective_product_links AS
 SELECT cp.id AS spu_pk,
    COALESCE(lo.procurement_product_id, pl.procurement_product_id) AS procurement_product_id,
    COALESCE(lo.decision, pl.relation_type) AS effective_relation_type,
    COALESCE(lo.id, pl.id) AS source_link_id,
        CASE
            WHEN lo.id IS NOT NULL THEN 'OPERATOR_OVERRIDE'::text
            ELSE 'MIAOSHOU_PUBLISHED_TO_TIKTOK'::text
        END AS source_kind,
    COALESCE(lo.valid_from, pl.valid_from) AS effective_from,
    pp.procurement_account_id,
    cp.shop_pk AS shop_pk
   FROM commerce.products_spu cp
     LEFT JOIN linkage.link_overrides lo ON lo.spu_pk = cp.id AND lo.valid_to IS NULL
     LEFT JOIN linkage.product_links pl ON pl.spu_pk = cp.id AND pl.valid_to IS NULL AND (lo.id IS NULL OR lo.decision <> 'DENY'::text)
     LEFT JOIN procurement.procurement_products pp ON pp.id = COALESCE(lo.procurement_product_id, pl.procurement_product_id)
  WHERE COALESCE(lo.decision, 'ALLOW'::text) <> 'DENY'::text;

DROP VIEW IF EXISTS analytics.ad_product_links;
CREATE VIEW analytics.ad_product_links AS
 WITH daily AS (
         SELECT r.seller_id,
            r.advertiser_id,
            r.campaign_id,
            r.day,
            el.value ->> 'product_id'::text AS product_id,
            el.value ->> 'product_name'::text AS product_name,
            el.value ->> 'product_status'::text AS product_status,
            el.value ->> 'gmv_max_bid_type'::text AS gmv_max_bid_type,
                CASE
                    WHEN (el.value ->> 'mixed_real_cost'::text) ~ '^[0-9]+([.][0-9]+)?$'::text THEN (el.value ->> 'mixed_real_cost'::text)::numeric
                    ELSE NULL::numeric
                END AS real_cost,
                CASE
                    WHEN (el.value ->> 'onsite_roi2_shopping_sku'::text) ~ '^[0-9]+$'::text THEN (el.value ->> 'onsite_roi2_shopping_sku'::text)::bigint
                    ELSE NULL::bigint
                END AS order_sku,
                CASE
                    WHEN (el.value ->> 'onsite_roi2_shopping_value'::text) ~ '^[0-9]+([.][0-9]+)?$'::text THEN (el.value ->> 'onsite_roi2_shopping_value'::text)::numeric
                    ELSE NULL::numeric
                END AS order_value
           FROM analytics.ad_raw r
             CROSS JOIN LATERAL jsonb_array_elements(((r.response -> 'body'::text) -> 'data'::text) -> 'table'::text) el(value)
          WHERE r.endpoint = '/oec_ads/shopping/v1/oec/stat/post_product_list'::text AND el.value ? 'product_id'::text AND NULLIF(el.value ->> 'product_id'::text, ''::text) IS NOT NULL
        ), latest AS (
         SELECT DISTINCT ON (daily.seller_id, daily.advertiser_id, daily.campaign_id, daily.product_id) daily.seller_id,
            daily.advertiser_id,
            daily.campaign_id,
            daily.product_id,
            daily.product_name,
            daily.product_status,
            daily.gmv_max_bid_type
           FROM daily
          ORDER BY daily.seller_id, daily.advertiser_id, daily.campaign_id, daily.product_id, daily.day DESC
        )
 SELECT d.seller_id,
    d.advertiser_id,
    d.campaign_id,
    d.product_id,
    l.product_name,
    l.product_status,
    l.gmv_max_bid_type,
    count(DISTINCT d.day) AS observed_days,
    min(d.day) AS first_day,
    max(d.day) AS last_day,
    COALESCE(sum(d.order_sku), 0::numeric)::bigint AS order_sku_total,
    COALESCE(sum(d.real_cost), 0::numeric)::numeric(20,4) AS real_cost_total,
    COALESCE(sum(d.order_value), 0::numeric)::numeric(20,4) AS order_value_total,
    ca.id AS shop_pk,
    cp.id AS spu_pk
   FROM daily d
     JOIN latest l USING (seller_id, advertiser_id, campaign_id, product_id)
     LEFT JOIN commerce.shops ca ON ca.platform = 'tiktok'::text AND ca.shop_id = d.seller_id
     LEFT JOIN commerce.products_spu cp ON cp.shop_pk = ca.id AND cp.spu_id = d.product_id
  GROUP BY d.seller_id, d.advertiser_id, d.campaign_id, d.product_id, l.product_name, l.product_status, l.gmv_max_bid_type, ca.id, cp.id;
