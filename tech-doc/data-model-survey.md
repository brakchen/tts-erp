# tts-erp 数据模型调研（2026-08-29）

> 数据来源：schema_tts_erp.sql / schema_oauth.sql（scripts/regen_schema.py 生成）+ 生产库实抽 demo 行。
> 敏感字段（token 密文 / key 哈希 / email / phone / address / recipient）已脱敏为 `<masked>`；超长字符串截断。

## 表清单总览

| # | 表 | 库 | 行数 | 作用 |
| --- | ---- | ---- | ------ | ------ |
| 1 | orders | tts_erp | 719 | TikTok 订单头镜像。sync 层幂等 upsert；抽取状态/金额/买家/5 个时间戳列，完整载荷存 raw jsonb。 |
| 2 | order_items | tts_erp | 748 | 订单商品行（sku/数量/单价 + raw）。PK (order_id, item_id)。 |
| 3 | order_shippings | tts_erp | 703 | 订单物流头（承运商/运单号 + raw）。PK order_id。 |
| 4 | logistics_tracking | tts_erp | 615 | 物流聚合视图：每单一行，n_events、首末事件时间、里程碑（到海外/清关/妥投/退回）。由 logistics_tracking_events 聚合而来。 |
| 5 | logistics_tracking_events | tts_erp | 12323 | 物流原始事件流（action_code + event_time + 描述/地点）。当前增长最快的表。 |
| 6 | logistics_sync_targets | tts_erp | 615 | 物流增量同步水位表：标记哪些 order 需要重追轨迹（needs_resync）。 |
| 7 | statements | tts_erp | 44 | TikTok 对账单（statement 级金额汇总，9 个 numeric 金额列 + raw）。 |
| 8 | statement_transactions | tts_erp | 296 | 账单逐交易明细，58 列平铺（全库最宽表），替代已删的 Excel 财务表。 |
| 9 | payments | tts_erp | 23 | 付款/结算记录（payment 级金额 + 汇率 + 银行账户）。 |
| 10 | returns | tts_erp | 31 | 退货/退款单。refund_amount 是查询时从 raw->refund_amount 计算，非物化列。 |
| 11 | cancellations | tts_erp | 175 | 取消单（取消状态/原因/是否补库存）。 |
| 12 | shops | tts_erp | 2 | 店铺注册表（shop_id/名称/region/seller_type），从 oauth_tokens 幂等 backfill。 |
| 13 | sync_log | tts_erp | 13536 | 同步运行日志（shop × sync_type × 起止时间 × 行数 × 状态）。AFTER STATEMENT trigger 每次清理 60 天前数据。 |
| 14 | api_keys | tts_erp | 6 | 外部 API key：SHA-256 哈希 + 角色(readonly/readwrite/admin) + 过期时间。明文只在创建时出现一次。 |
| 15 | miaoshou_shops | tts_erp | 1 | 妙手店铺授权信息（平台/站点/授权过期，gmt_* 为 text 时间）。 |
| 16 | miaoshou_price_templates | tts_erp | 0 | 妙手定价模板（~45 列平铺：利润/汇率/物流计费规则）。 |
| 17 | miaoshou_collect_box_details | tts_erp | 0 | 妙手采集箱商品明细。 |
| 18 | miaoshou_move_collect_tasks | tts_erp | 10 | 妙手搬家/刊登任务。 |
| 19 | analytics_records | tts_erp | 1 | 广告分析 ingestion 原始记录（幂等 key + request/response jsonb + schema/protocol 双版本号）。 |
| 20 | analytics_cursors | tts_erp | 1 | ingestion 游标：按 (seller, advertiser, storage_key, campaign) 记录已完成的最近一天。 |
| 21 | analytics_daily_pages | tts_erp | 1 | 每日分页到达记录（哪天哪页已入库）。 |
| 22 | analytics_daily_completeness | tts_erp | 1 | 每日完整性标记（expected_page_count vs 实际到达页）。 |
| 23 | analytics_shop_timezones | tts_erp | 9 | seller 级时区配置（默认 Asia/Shanghai）。 |
| 24 | analytics_audit_log | tts_erp | 6462 | ingestion 请求审计（endpoint/status/records_in/ok/rej）。 |
| 25 | oauth_tokens | oauth_receiver | 2 | TikTok token 密文存储（bytea 加密列 + 过期时间 + granted_scopes）。(shop_id, provider) 唯一。属 oauth_receiver 库，重构拟并入主库。 |

---

# 一、TikTok 订单域

## `orders`

**库**: `tts_erp` · **行数**: 719

**作用**: TikTok 订单头镜像。sync 层幂等 upsert；抽取状态/金额/买家/5 个时间戳列，完整载荷存 raw jsonb。

### 建表语句

```sql
CREATE TABLE IF NOT EXISTS public.orders (
    order_id text NOT NULL,
    shop_id text NOT NULL,
    order_status_name text,
    payment_amount numeric(18,2),
    payment_currency text,
    total_amount numeric(18,2),
    buyer_email text,
    buyer_message text,
    create_time bigint,
    update_time bigint,
    paid_time bigint,
    shipped_time bigint,
    delivered_time bigint,
    cancelled_time bigint,
    fulfillment_type text,
    raw jsonb NOT NULL,
    synced_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);
```

### 索引

```sql
CREATE INDEX idx_orders_create_time ON public.orders USING btree (create_time DESC)
CREATE INDEX idx_orders_shop ON public.orders USING btree (shop_id)
CREATE INDEX idx_orders_shop_ct ON public.orders USING btree (shop_id, create_time DESC, order_id DESC)
CREATE INDEX idx_orders_status ON public.orders USING btree (order_status_name)
CREATE OR REPLACE TRIGGER trg_orders_touch BEFORE UPDATE ON public.orders FOR EACH ROW EXECUTE FUNCTION public.touch_updated_at()
```

### Demo 数据

```json
{
  "order_id": "585786519745300287",
  "shop_id": "7494763368967603447",
  "order_status_name": "AWAITING_SHIPMENT",
  "payment_amount": 434566,
  "payment_currency": "VND",
  "total_amount": 434566,
  "buyer_email": "<masked>",
  "buyer_message": null,
  "create_time": 1787997915,
  "update_time": 1787998417,
  "paid_time": null,
  "shipped_time": null,
  "delivered_time": null,
  "cancelled_time": null,
  "fulfillment_type": "FULFILLMENT_BY_SELLER",
  "raw": "{\n  \"id\": \"585786519745300287\",\n  \"is_cod\": true,\n  \"status\": \"AWAITING_SHIPMENT\",\n  \"payment\": {\n    \"tax\": \"39506\",\n    \"currency\": \"VND\",\n    \"sub_total\": \"434566\",\n    \"product_tax\": \"39506\",\n    \"shipping_fee\": \"0\",\n    \"total_amount\": \"434566\",\n    \"seller_discount\": \"343044\",\n    \"shipping_fee_tax\": \"0\",\n    \"platform_discount\": \"80000\",\n    \"original_shipping_fee\": \"30000\",\n    \"original_total_product_price\": \"857610\",\n    \"shipping_fee_seller_discount\": \"0\",\n    \"shipping_fee_cofunded_discount\": \"0\",\n    \"shipping_fee_platform_discount\": \"30000\"\n  },\n  \"user_id\": \"7494844936183908159\",\n  \"packages\": [\n    {\n      \"id\": \"1209020685803095871\"\n    }\n  ],\n  \"line_items\": [\n    {\n      \"id\": \"585786519745365823\",\n      \"sku_id\": \"1736929605432607991\",\n      \"is_gift\": false,\n      \"currency\": \"VND\",\n      \"sku_name\": \"Màu đen, M 45KG-55KG\",\n      \"sku_type\": \"NORMAL\",\n      \"sku_image\": \"https://p16-oec-sg.ibyteimg.com/tos-alisg-i-aphluv4xwc-sg/48ef3a8009cb45f185378789436bf6a1~tplv-aphluv4xwc-origin-jpeg.jpeg?dr=15568&t=555f072d&ps=933b5bde&shp=54477afb&shcp=3c3d9ffb&idc=my&from=1413970683\",\n      \"package_id\": \"1209020685803095871\",\n      \"product_id\": \"1736929955366339831\",\n      \"sale_price\": \"434566\",\n      \"seller_sku\": \"\",\n      \"product_name\": \"Áo thun nam tay ngắn nhanh khô chất liệu lụa băng, thương hiệu thời trang mùa hè, rộng rãi và thoáng khí, phong cách và mát mẻ, cổ tròn, áo thun mỏng tay lửng.\",\n      \"display_status\": \"AWAITING_SHIPMENT\",\n      \"original_price\": \"857610\",\n      \"package_status\": \"TO_FULFILL\",\n      \"seller_discount\": \"343044\",\n      \"tracking_number\": \"\",\n      \"gift_retail_price\": \"0\",\n      \"platform_discount\": \"80000\",\n      \"shipping_provider_id\": \"7439297584469903122\",\n      \"shipping_provider_name\": \"Wise Express - DCS\"\n    }\n  ],\n  \"order_type\": \"NORMAL\",\n  \"buyer_email\": \"<masked>\",\n  \"create_time\": 1787997915,\n  \"update_time\": 1787998417,\n  \"rts_sla_time\": 1788170717,\n  \"tts_sla_time\": 1788278399,\n  \"warehouse_id\": \"7661207737776293652\",\n  \"buyer_message\": \"\",\n  \"delivery_type\": \"HOME_DELIVERY\",\n  \"shipping_type\": \"TIKTOK\",\n  \"is_sample_order\": false,\n  \"fulfillment_type\": \"FULFILLMENT_BY_SELLER\",\n  \"is_on_hold_order\": false,\n  \"commerce_platform\": \"TIKTOK_SHOP\",\n  \"recipient_address\": {\n    \"name\": \"T** K*\",\n    \"last_name\": \"\",\n    \"first_name\": \"\",\n    \"postal_code\": \"\",\n    \"region_code\": \"VN\",\n    \"full_address\": \"<masked>\",\n    \"phone_number\": \"<masked>\",\n    \"address_line1\": \"<masked>\",\n    \"address_line\n…[raw truncated, total 3719 chars]",
  "synced_at": "2026-08-29T10:30:12.332162+00:00",
  "updated_at": "2026-08-29T10:30:12.332162+00:00"
}
```

## `order_items`

**库**: `tts_erp` · **行数**: 748

**作用**: 订单商品行（sku/数量/单价 + raw）。PK (order_id, item_id)。

### 建表语句

```sql
CREATE TABLE IF NOT EXISTS public.order_items (
    order_id text NOT NULL,
    item_id text NOT NULL,
    shop_id text,
    sku_id text,
    product_id text,
    product_name text,
    sku_name text,
    sku_image text,
    quantity integer,
    sku_price numeric(18,2),
    raw jsonb NOT NULL
);
```

### 索引

```sql
CREATE INDEX idx_order_items_shop ON public.order_items USING btree (shop_id)
```

### Demo 数据

```json
{
  "order_id": "585642444391876260",
  "item_id": "585642444391941796",
  "shop_id": "7494763368967603447",
  "sku_id": "1737021566083237111",
  "product_id": "1737021580322768119",
  "product_name": "Áo thun nam ngắn tay họa tiết sọc nhiều màu, cổ tròn, phong cách thời trang mùa hè",
  "sku_name": "Sọc màu kem, L (57.5‑65 kg)",
  "sku_image": "https://p16-oec-sg.ibyteimg.com/tos-alisg-i-aphluv4xwc-sg/4b9899f589a248d9ad292940699681c2~tplv-aphluv4xwc-origin-jpeg.jpeg?dr=15568&t=555f072d&ps=933b5bde&shp=54477afb&shcp=3c3d9ffb&idc=my&from=1413970683",
  "quantity": 1,
  "sku_price": 587523,
  "raw": {
    "id": "585642444391941796",
    "sku_id": "1737021566083237111",
    "is_gift": false,
    "currency": "VND",
    "rts_time": 1787290054,
    "sku_name": "Sọc màu kem, L (57.5‑65 kg)",
    "sku_type": "NORMAL",
    "sku_image": "https://p16-oec-sg.ibyteimg.com/tos-alisg-i-aphluv4xwc-sg/4b9899f589a248d9ad292940699681c2~tplv-aphluv4xwc-origin-jpeg.jpeg?dr=15568&t=555f072d&ps=933b5bde&shp=54477afb&shcp=3c3d9ffb&idc=my&from=1413970683",
    "package_id": "1208001816323393188",
    "product_id": "1737021580322768119",
    "sale_price": "587523",
    "seller_sku": "",
    "cancel_user": "SYSTEM",
    "product_name": "Áo thun nam ngắn tay họa tiết sọc nhiều màu, cổ tròn, phong cách thời trang mùa hè",
    "cancel_reason": "Giao gói hàng thất bại",
    "display_status": "CANCELLED",
    "original_price": "979204",
    "package_status": "CANCELLED",
    "seller_discount": "391681",
    "tracking_number": "WSWH3396865462",
    "gift_retail_price": "0",
    "platform_discount": "0",
    "shipping_provider_id": "7439297584469903122",
    "shipping_provider_name": "Wise Express - DCS"
  }
}
```

## `order_shippings`

**库**: `tts_erp` · **行数**: 703

**作用**: 订单物流头（承运商/运单号 + raw）。PK order_id。

### 建表语句

```sql
CREATE TABLE IF NOT EXISTS public.order_shippings (
    order_id text NOT NULL,
    shop_id text,
    tracking_number text,
    shipping_provider_id text,
    shipping_provider_name text,
    raw jsonb NOT NULL,
    synced_at timestamp with time zone DEFAULT now() NOT NULL
);
```

### 索引

```sql
CREATE INDEX idx_order_shippings_tracking ON public.order_shippings USING btree (shop_id, order_id) WHERE ((tracking_number IS NOT NULL) AND (tracking_number <> ''::text))
```

### Demo 数据

```json
{
  "order_id": "585755093288650385",
  "shop_id": "7494763368967603447",
  "tracking_number": null,
  "shipping_provider_id": "7439297584469903122",
  "shipping_provider_name": "Wise Express - DCS",
  "raw": {
    "shipping_provider_id": "7439297584469903122",
    "shipping_provider_name": "Wise Express - DCS"
  },
  "synced_at": "2026-08-27T11:40:06.067966+00:00"
}
```

## `logistics_tracking`

**库**: `tts_erp` · **行数**: 615

**作用**: 物流聚合视图：每单一行，n_events、首末事件时间、里程碑（到海外/清关/妥投/退回）。由 logistics_tracking_events 聚合而来。

### 建表语句

```sql
CREATE TABLE IF NOT EXISTS public.logistics_tracking (
    order_id text NOT NULL,
    shop_id text,
    tracking_number text,
    n_events integer DEFAULT 0 NOT NULL,
    first_event_at bigint,
    last_event_at bigint,
    last_action_code integer,
    last_description text,
    final_status text,
    arrived_overseas boolean DEFAULT false NOT NULL,
    arrived_at bigint,
    origin_departed_at bigint,
    import_cleared_at bigint,
    delivered_at bigint,
    returned_at bigint,
    raw jsonb NOT NULL,
    synced_at timestamp with time zone DEFAULT now() NOT NULL
);
```

### 索引

```sql
CREATE INDEX idx_logistics_tracking_final_status ON public.logistics_tracking USING btree (final_status)
CREATE INDEX idx_logistics_tracking_last_event ON public.logistics_tracking USING btree (last_event_at DESC)
CREATE INDEX idx_logistics_tracking_overseas ON public.logistics_tracking USING btree (arrived_overseas) WHERE arrived_overseas
CREATE INDEX idx_logistics_tracking_shop ON public.logistics_tracking USING btree (shop_id)
CREATE INDEX idx_logistics_tracking_tracking_number ON public.logistics_tracking USING btree (tracking_number)
```

### Demo 数据

```json
{
  "order_id": "585780503220356609",
  "shop_id": "7494763368967603447",
  "tracking_number": "WSWH3323137472",
  "n_events": 2,
  "first_event_at": 1787972539808,
  "last_event_at": 1787982077487,
  "last_action_code": 20101,
  "last_description": "Your order was packed by the seller and is awaiting carrier pickup and transport to their hub.",
  "final_status": "AWAITING_PICKUP",
  "arrived_overseas": false,
  "arrived_at": null,
  "origin_departed_at": null,
  "import_cleared_at": null,
  "delivered_at": null,
  "returned_at": null,
  "raw": {
    "tracking": [
      {
        "action_code": 20101,
        "description": "Your order was packed by the seller and is awaiting carrier pickup and transport to their hub.",
        "update_time_millis": 1787982077487
      },
      {
        "action_code": 10101,
        "description": "Order placed.",
        "update_time_millis": 1787972539808
      }
    ]
  },
  "synced_at": "2026-08-29T12:30:43.550863+00:00"
}
```

## `logistics_tracking_events`

**库**: `tts_erp` · **行数**: 12323

**作用**: 物流原始事件流（action_code + event_time + 描述/地点）。当前增长最快的表。

### 建表语句

```sql
CREATE TABLE IF NOT EXISTS public.logistics_tracking_events (
    order_id text NOT NULL,
    action_code integer NOT NULL,
    event_time bigint NOT NULL,
    description text,
    location text,
    synced_at timestamp with time zone DEFAULT now() NOT NULL
);
```

### 索引

```sql
CREATE INDEX idx_lt_events_action ON public.logistics_tracking_events USING btree (action_code)
CREATE INDEX idx_lt_events_time ON public.logistics_tracking_events USING btree (event_time DESC)
```

### Demo 数据

```json
{
  "order_id": "585740183740974156",
  "action_code": 30301,
  "event_time": 1787943545000,
  "description": "In transit in country/region of origin. Departed sorting center in yiwu.",
  "location": "yiwu",
  "synced_at": "2026-08-29T12:34:05.925382+00:00"
}
```

## `logistics_sync_targets`

**库**: `tts_erp` · **行数**: 615

**作用**: 物流增量同步水位表：标记哪些 order 需要重追轨迹（needs_resync）。

### 建表语句

```sql
CREATE TABLE IF NOT EXISTS public.logistics_sync_targets (
    order_id text NOT NULL,
    shop_id text NOT NULL,
    last_synced_at timestamp with time zone,
    last_n_events integer,
    needs_resync boolean DEFAULT true NOT NULL
);
```

### 索引

```sql
CREATE INDEX idx_lt_targets_resync ON public.logistics_sync_targets USING btree (needs_resync) WHERE (needs_resync = true)
CREATE INDEX idx_lt_targets_shop ON public.logistics_sync_targets USING btree (shop_id)
```

### Demo 数据

```json
{
  "order_id": "585028082701010441",
  "shop_id": "7494763368967603447",
  "last_synced_at": "2026-08-27T14:07:35.274418+00:00",
  "last_n_events": 37,
  "needs_resync": false
}
```

# 二、财务域

## `statements`

**库**: `tts_erp` · **行数**: 44

**作用**: TikTok 对账单（statement 级金额汇总，9 个 numeric 金额列 + raw）。

### 建表语句

```sql
CREATE TABLE IF NOT EXISTS public.statements (
    statement_id text NOT NULL,
    shop_id text,
    payment_id text,
    currency text,
    payment_status text,
    statement_time bigint,
    payment_time bigint,
    revenue_amount numeric(18,2),
    fee_amount numeric(18,2),
    net_sales_amount numeric(18,2),
    shipping_cost_amount numeric(18,2),
    adjustment_amount numeric(18,2),
    settlement_amount numeric(18,2),
    raw jsonb NOT NULL,
    synced_at timestamp with time zone DEFAULT now() NOT NULL
);
```

### 索引

```sql
CREATE INDEX idx_statements_payment_id ON public.statements USING btree (payment_id)
CREATE INDEX idx_statements_shop ON public.statements USING btree (shop_id)
CREATE INDEX idx_statements_stime ON public.statements USING btree (statement_time DESC)
```

### Demo 数据

```json
{
  "statement_id": "7673672914201216785",
  "shop_id": "7494763368967603447",
  "payment_id": "3681946141890086135",
  "currency": "VND",
  "payment_status": "PAID",
  "statement_time": 1786752000,
  "payment_time": 1786760988,
  "revenue_amount": 1585500,
  "fee_amount": -624615,
  "net_sales_amount": 1585500,
  "shipping_cost_amount": 0,
  "adjustment_amount": 0,
  "settlement_amount": 960885,
  "raw": {
    "id": "7673672914201216785",
    "currency": "VND",
    "fee_amount": "-624615",
    "payment_id": "3681946141890086135",
    "payment_time": 1786760988,
    "payment_status": "PAID",
    "revenue_amount": "1585500",
    "statement_time": 1786752000,
    "net_sales_amount": "1585500",
    "adjustment_amount": "0",
    "settlement_amount": "960885",
    "shipping_cost_amount": "0"
  },
  "synced_at": "2026-08-18T07:05:10.103529+00:00"
}
```

## `statement_transactions`

**库**: `tts_erp` · **行数**: 296

**作用**: 账单逐交易明细，58 列平铺（全库最宽表），替代已删的 Excel 财务表。

### 建表语句

```sql
CREATE TABLE IF NOT EXISTS public.statement_transactions (
    txn_id text NOT NULL,
    statement_id text NOT NULL,
    shop_id text,
    order_id text,
    order_create_time bigint,
    type text,
    currency text,
    actual_return_shipping_fee_amount numeric(18,2),
    actual_shipping_fee_amount numeric(18,2),
    adjustment_amount numeric(18,2),
    affiliate_ads_commission_amount numeric(18,2),
    affiliate_commission_amount numeric(18,2),
    affiliate_commission_before_pit numeric(18,2),
    affiliate_partner_commission_amount numeric(18,2),
    after_seller_discounts_subtotal_amount numeric(18,2),
    customer_order_refund_amount numeric(18,2),
    customer_paid_shipping_fee_amount numeric(18,2),
    customer_paid_shipping_fee_refund_amount numeric(18,2),
    customer_payment_amount numeric(18,2),
    customer_refund_amount numeric(18,2),
    customer_shipping_fee_amount numeric(18,2),
    customer_shipping_fee_offset_amount numeric(18,2),
    fbm_shipping_cost_amount numeric(18,2),
    fbt_fulfillment_fee_amount numeric(18,2),
    fbt_fulfillment_fee_reimbursement_amount numeric(18,2),
    fbt_shipping_cost_amount numeric(18,2),
    fee_amount numeric(18,2),
    gross_sales_amount numeric(18,2),
    gross_sales_refund_amount numeric(18,2),
    isr_income_tax_amount numeric(18,2),
    iva_vat_amount numeric(18,2),
    net_sales_amount numeric(18,2),
    pit_amount numeric(18,2),
    platform_commission_amount numeric(18,2),
    platform_discount_amount numeric(18,2),
    platform_discount_refund_amount numeric(18,2),
    platform_refund_subsidy_amount numeric(18,2),
    platform_shipping_fee_discount_amount numeric(18,2),
    promo_shipping_incentive_amount numeric(18,2),
    referral_fee_amount numeric(18,2),
    refund_administration_fee_amount numeric(18,2),
    refund_shipping_cost_discount_amount numeric(18,2),
    retail_delivery_fee_amount numeric(18,2),
    retail_delivery_fee_payment_amount numeric(18,2),
    retail_delivery_fee_refund_amount numeric(18,2),
    return_shipping_fee_amount numeric(18,2),
    revenue_amount numeric(18,2),
    sales_tax_amount numeric(18,2),
    sales_tax_payment_amount numeric(18,2),
    sales_tax_refund_amount numeric(18,2),
    seller_discount_amount numeric(18,2),
    seller_discount_refund_amount numeric(18,2),
    settlement_amount numeric(18,2),
    shipping_cost_amount numeric(18,2),
    shipping_cost_discount_amount numeric(18,2),
    shipping_fee_amount numeric(18,2),
    shipping_fee_subsidy_amount numeric(18,2),
    shipping_insurance_fee_amount numeric(18,2),
    signature_confirmation_fee_amount numeric(18,2),
    transaction_fee_amount numeric(18,2),
    raw jsonb NOT NULL,
    synced_at timestamp with time zone DEFAULT now() NOT NULL
);
```

### 索引

```sql
CREATE INDEX idx_stmt_txns_order ON public.statement_transactions USING btree (order_id)
CREATE INDEX idx_stmt_txns_shop ON public.statement_transactions USING btree (shop_id)
CREATE INDEX idx_stmt_txns_stmt ON public.statement_transactions USING btree (statement_id)
CREATE INDEX idx_stmt_txns_type ON public.statement_transactions USING btree (type)
```

### Demo 数据

```json
{
  "txn_id": "7669386856647280385",
  "statement_id": "7677029942223210260",
  "shop_id": "7494763368967603447",
  "order_id": "585335984381331327",
  "order_create_time": 1785667815,
  "type": "ORDER",
  "currency": "VND",
  "actual_return_shipping_fee_amount": 0,
  "actual_shipping_fee_amount": -54400,
  "adjustment_amount": 0,
  "affiliate_ads_commission_amount": 0,
  "affiliate_commission_amount": -74332,
  "affiliate_commission_before_pit": -74332,
  "affiliate_partner_commission_amount": 0,
  "after_seller_discounts_subtotal_amount": 495548,
  "customer_order_refund_amount": 0,
  "customer_paid_shipping_fee_amount": 0,
  "customer_paid_shipping_fee_refund_amount": 0,
  "customer_payment_amount": 495548,
  "customer_refund_amount": 0,
  "customer_shipping_fee_amount": 0,
  "customer_shipping_fee_offset_amount": 0,
  "fbm_shipping_cost_amount": 0,
  "fbt_fulfillment_fee_amount": 0,
  "fbt_fulfillment_fee_reimbursement_amount": 0,
  "fbt_shipping_cost_amount": 0,
  "fee_amount": -221114,
  "gross_sales_amount": 825912,
  "gross_sales_refund_amount": 0,
  "isr_income_tax_amount": 0,
  "iva_vat_amount": 0,
  "net_sales_amount": 495548,
  "pit_amount": 0,
  "platform_commission_amount": -74332,
  "platform_discount_amount": 0,
  "platform_discount_refund_amount": 0,
  "platform_refund_subsidy_amount": 0,
  "platform_shipping_fee_discount_amount": 30000,
  "promo_shipping_incentive_amount": 0,
  "referral_fee_amount": 0,
  "refund_administration_fee_amount": 0,
  "refund_shipping_cost_discount_amount": 0,
  "retail_delivery_fee_amount": 0,
  "retail_delivery_fee_payment_amount": 0,
  "retail_delivery_fee_refund_amount": 0,
  "return_shipping_fee_amount": 0,
  "revenue_amount": 495548,
  "sales_tax_amount": 0,
  "sales_tax_payment_amount": 0,
  "sales_tax_refund_amount": 0,
  "seller_discount_amount": -330364,
  "seller_discount_refund_amount": 0,
  "settlement_amount": 274434,
  "shipping_cost_amount": -24400,
  "shipping_cost_discount_amount": 0,
  "shipping_fee_amount": -24400,
  "shipping_fee_subsidy_amount": 0,
  "shipping_insurance_fee_amount": 0,
  "signature_confirmation_fee_amount": 0,
  "transaction_fee_amount": 0,
  "raw": {
    "id": "7669386856647280385",
    "type": "ORDER",
    "currency": "VND",
    "order_id": "585335984381331327",
    "fee_amount": "-221114",
    "pit_amount": "0",
    "iva_vat_amount": "0",
    "revenue_amount": "495548",
    "net_sales_amount": "495548",
    "sales_tax_amount": "0",
    "adjustment_amount": "0",
    "order_create_time": 1785667815,
    "settlement_amount": "274434",
    "gross_sales_amount": "825912",
    "referral_fee_amount": "0",
    "shipping_fee_amount": "-24400",
    "shipping_cost_amount": "-24400",
    "isr_income_tax_amount": "0",
    "customer_refund_amount": "0",
    "seller_discount_amount": "-330364",
    "transaction_fee_amount": "0",
    "customer_payment_amount": "495548",
    "sales_tax_refund_amount": "0",
    "fbm_shipping_cost_amount": "0",
    "fbt_shipping_cost_amount": "0",
    "platform_discount_amount": "0",
    "sales_tax_payment_amount": "0",
    "gross_sales_refund_amount": "0",
    "actual_shipping_fee_amount": "-54400",
    "fbt_fulfillment_fee_amount": "0",
    "platform_commission_amount": "-74332",
    "retail_delivery_fee_amount": "0",
    "return_shipping_fee_amount": "0",
    "affiliate_commission_amount": "-74332",
    "shipping_fee_subsidy_amount": "0",
    "customer_order_refund_amount": "0",
    "customer_shipping_fee_amount": "0",
    "seller_discount_refund_amount": "0",
    "shipping_cost_discount_amount": "0",
    "shipping_insurance_fee_amount": "0",
    "platform_refund_subsidy_amount": "0",
    "affiliate_ads_commission_amount": "0",
    "affiliate_commission_before_pit": "-74332",
    "platform_discount_refund_amount": "0",
    "promo_shipping_incentive_amount": "0",
    "refund_administration_fee_amount": "0",
    "actual_return_shipping_fee_amount": "0",
    "customer_paid_shipping_fee_amount": "0",
    "retail_delivery_fee_refund_amount": "0",
    "signature_confirmation_fee_amount": "0",
    "retail_delivery_fee_payment_amount": "0",
    "affiliate_partner_commission_amount": "0",
    "customer_shipping_fee_offset_amount": "0",
    "refund_shipping_cost_discount_amount": "0",
    "platform_shipping_fee_discount_amount": "30000",
    "after_seller_discounts_subtotal_amount": "495548",
    "customer_paid_shipping_fee_refund_amount": "0",
    "fbt_fulfillment_fee_reimbursement_amount": "0"
  },
  "synced_at": "2026-08-24T15:47:38.211275+00:00"
}
```

## `payments`

**库**: `tts_erp` · **行数**: 23

**作用**: 付款/结算记录（payment 级金额 + 汇率 + 银行账户）。

### 建表语句

```sql
CREATE TABLE IF NOT EXISTS public.payments (
    payment_id text NOT NULL,
    shop_id text,
    status text,
    currency text,
    amount_value numeric(18,2),
    settlement_amount_value numeric(18,2),
    payment_amount_before_value numeric(18,2),
    reserve_amount_value numeric(18,2),
    exchange_rate text,
    bank_account text,
    create_time bigint,
    paid_time bigint,
    raw jsonb NOT NULL,
    synced_at timestamp with time zone DEFAULT now() NOT NULL
);
```

### 索引

```sql
CREATE INDEX idx_payments_paid_time ON public.payments USING btree (paid_time DESC)
CREATE INDEX idx_payments_shop ON public.payments USING btree (shop_id)
CREATE INDEX idx_payments_status ON public.payments USING btree (status)
```

### Demo 数据

```json
{
  "payment_id": "3682661311119852791",
  "shop_id": "7494763368967603447",
  "status": "PAID",
  "currency": "VND",
  "amount_value": 2176420,
  "settlement_amount_value": 2176420,
  "payment_amount_before_value": 2176420,
  "reserve_amount_value": 0,
  "exchange_rate": "1",
  "bank_account": "*************200659",
  "create_time": 1786852494,
  "paid_time": 1786852498,
  "raw": {
    "id": "3682661311119852791",
    "amount": {
      "value": "2176420",
      "currency": "VND"
    },
    "status": "PAID",
    "paid_time": 1786852498,
    "create_time": 1786852494,
    "bank_account": "*************200659",
    "exchange_rate": "1",
    "reserve_amount": {
      "value": "0",
      "currency": "VND"
    },
    "settlement_amount": {
      "value": "2176420",
      "currency": "VND"
    },
    "payment_amount_before_exchange": {
      "value": "2176420",
      "currency": "VND"
    }
  },
  "synced_at": "2026-08-18T07:05:11.880571+00:00"
}
```

# 三、售后域

## `returns`

**库**: `tts_erp` · **行数**: 31

**作用**: 退货/退款单。refund_amount 是查询时从 raw->refund_amount 计算，非物化列。

### 建表语句

```sql
CREATE TABLE IF NOT EXISTS public.returns (
    return_id text NOT NULL,
    shop_id text,
    order_id text,
    return_status text,
    return_reason text,
    return_type text,
    role text,
    create_time bigint,
    update_time bigint,
    raw jsonb NOT NULL,
    synced_at timestamp with time zone DEFAULT now() NOT NULL
);
```

### 索引

```sql
CREATE INDEX idx_returns_create_time ON public.returns USING btree (create_time DESC)
CREATE INDEX idx_returns_order ON public.returns USING btree (order_id)
CREATE INDEX idx_returns_shop ON public.returns USING btree (shop_id)
CREATE INDEX idx_returns_status ON public.returns USING btree (return_status)
```

### Demo 数据

```json
{
  "return_id": "4041558367614568413",
  "shop_id": "7494763368967603447",
  "order_id": "585053161544976349",
  "return_status": "RETURN_OR_REFUND_REQUEST_COMPLETE",
  "return_reason": "ecom_order_delivered_refund_and_return_reason_no_longer_needed_new",
  "return_type": "RETURN_AND_REFUND",
  "role": "BUYER",
  "create_time": 1784523293,
  "update_time": 1785668092,
  "raw": {
    "role": "BUYER",
    "order_id": "585053161544976349",
    "return_id": "4041558367614568413",
    "create_time": 1784523293,
    "return_type": "RETURN_AND_REFUND",
    "update_time": 1785668092,
    "refund_amount": {
      "currency": "VND",
      "refund_tax": "44516",
      "refund_total": "489681",
      "refund_subtotal": "489681",
      "refund_shipping_fee": "0"
    },
    "return_method": "PLATFORM_SHIPPED",
    "return_reason": "ecom_order_delivered_refund_and_return_reason_no_longer_needed_new",
    "return_status": "RETURN_OR_REFUND_REQUEST_COMPLETE",
    "shipment_type": "PLATFORM",
    "discount_amount": [
      {
        "currency": "VND",
        "product_seller_discount": "335787",
        "product_platform_discount": "14000",
        "shipping_fee_seller_discount": "0",
        "shipping_fee_platform_discount": "0"
      }
    ],
    "handover_method": "PICKUP",
    "is_quick_refund": true,
    "return_line_items": [
      {
        "sku_id": "1736496304524657911",
        "sku_name": "Màu xanh lá, XL",
        "product_name": "Áo thun nam tay ngắn in họa tiết toàn thân mùa hè 2026, thời trang, giản dị, đa năng, áo cơ bản hợp xu hướng.",
        "product_image": {
          "url": "https://p16-oec-sg.ibyteimg.com/tos-alisg-i-aphluv4xwc-sg/765929c49d7d4cb694f5ae1df660befc~tplv-aphluv4xwc-origin-jpeg.jpeg?dr=15568&from=4246405447&idc=my2&ps=933b5bde&shcp=3c3d9ffb&shp=fd1b0147&t=555f072d",
          "width": 200,
          "height": 200
        },
        "refund_amount": {
          "currency": "VND",
          "refund_tax": "44516",
          "refund_total": "489681",
          "refund_subtotal": "489681",
          "refund_shipping_fee": "0"
        },
        "order_line_item_id": "585053161545041885",
        "return_line_item_id": "4041558367614633949"
      }
    ],
    "combined_return_id": "0",
    "is_combined_return": false,
    "return_provider_id": "6841743441349706241",
    "return_reason_text": "Change of mind",
    "shipping_fee_amount": [
      {
        "currency": "VND",
        "buyer_paid_return_shipping_fee": "0",
        "seller_paid_return_shipping_fee": "0",
        "platform_paid_return_shipping_fee": "15150"
      }
    ],
    "return_provider_name": "J&T Express",
    "return_tracking_number": "854151285877",
    "return_warehouse_address": {
      "full_address": "<masked>"
    }
  },
  "synced_at": "2026-08-27T02:11:27.593752+00:00"
}
```

## `cancellations`

**库**: `tts_erp` · **行数**: 175

**作用**: 取消单（取消状态/原因/是否补库存）。

### 建表语句

```sql
CREATE TABLE IF NOT EXISTS public.cancellations (
    cancel_id text NOT NULL,
    shop_id text,
    order_id text,
    cancel_status text,
    cancel_reason text,
    cancel_reason_text text,
    cancel_type text,
    role text,
    should_replenish_stock boolean,
    create_time bigint,
    update_time bigint,
    raw jsonb NOT NULL,
    synced_at timestamp with time zone DEFAULT now() NOT NULL
);
```

### 索引

```sql
CREATE INDEX idx_cancellations_create_time ON public.cancellations USING btree (create_time DESC)
CREATE INDEX idx_cancellations_order ON public.cancellations USING btree (order_id)
CREATE INDEX idx_cancellations_shop ON public.cancellations USING btree (shop_id)
CREATE INDEX idx_cancellations_status ON public.cancellations USING btree (cancel_status)
```

### Demo 数据

```json
{
  "cancel_id": "4042092549129209339",
  "shop_id": "7494763368967603447",
  "order_id": "585743041784677883",
  "cancel_status": "CANCELLATION_REQUEST_COMPLETE",
  "cancel_reason": "ecom_order_to_ship_canceled_reason_high_delivery_costs",
  "cancel_reason_text": "High delivery costs",
  "cancel_type": "BUYER_CANCEL",
  "role": "BUYER",
  "should_replenish_stock": true,
  "create_time": 1787757241,
  "update_time": 1787757241,
  "raw": {
    "role": "BUYER",
    "order_id": "585743041784677883",
    "cancel_id": "4042092549129209339",
    "cancel_type": "BUYER_CANCEL",
    "create_time": 1787757241,
    "update_time": 1787757241,
    "cancel_reason": "ecom_order_to_ship_canceled_reason_high_delivery_costs",
    "cancel_status": "CANCELLATION_REQUEST_COMPLETE",
    "cancel_line_items": [
      {
        "sku_id": "1737132876206212343",
        "sku_name": "Màu mẫu, M 47.5KG‑57.5KG",
        "product_name": "Áo thun nam tay ngắn cổ tròn, họa tiết mảng màu trừu tượng xanh đỏ vàng phong cách nghệ thuật hiện đại",
        "product_image": {
          "url": "https://p16-oec-sg.ibyteimg.com/tos-alisg-i-aphluv4xwc-sg/d7147c61e10346f7bc5b56ed1af23b70~tplv-aphluv4xwc-origin-jpeg.jpeg?dr=15568&from=4246405447&idc=my&ps=933b5bde&shcp=3c3d9ffb&shp=fd1b0147&t=555f072d",
          "width": 200,
          "height": 200
        },
        "order_line_item_id": "585743041784743419",
        "cancel_line_item_id": "4042092549129274875"
      }
    ],
    "cancel_reason_text": "High delivery costs",
    "should_replenish_stock": true
  },
  "synced_at": "2026-08-27T02:11:37.917758+00:00"
}
```

# 四、基础设施 / 系统表

## `shops`

**库**: `tts_erp` · **行数**: 2

**作用**: 店铺注册表（shop_id/名称/region/seller_type），从 oauth_tokens 幂等 backfill。

### 建表语句

```sql
CREATE TABLE IF NOT EXISTS public.shops (
    shop_id text NOT NULL,
    shop_name text,
    shop_region text,
    seller_type text,
    last_seen_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);
```

### 索引

```sql
CREATE OR REPLACE TRIGGER trg_shops_touch BEFORE UPDATE ON public.shops FOR EACH ROW EXECUTE FUNCTION public.touch_updated_at()
```

### Demo 数据

```json
{
  "shop_id": "MOCK_SHOP_12345",
  "shop_name": "MOCK Test Shop",
  "shop_region": "US",
  "seller_type": "CROSS_BORDER",
  "last_seen_at": "2026-08-27T14:06:59.282929+00:00",
  "created_at": "2026-08-25T07:59:05.161099+00:00",
  "updated_at": "2026-08-27T14:06:59.282929+00:00"
}
```

## `sync_log`

**库**: `tts_erp` · **行数**: 13536

**作用**: 同步运行日志（shop × sync_type × 起止时间 × 行数 × 状态）。AFTER STATEMENT trigger 每次清理 60 天前数据。

### 建表语句

```sql
CREATE TABLE IF NOT EXISTS public.sync_log (
    id bigint NOT NULL,
    shop_id text,
    sync_type text,
    started_at timestamp with time zone DEFAULT now() NOT NULL,
    finished_at timestamp with time zone,
    rows_affected integer,
    status text,
    error_message text
);
```

### 索引

```sql
CREATE INDEX idx_sync_log_shop ON public.sync_log USING btree (shop_id, started_at DESC)
CREATE OR REPLACE TRIGGER trg_sync_log_retention AFTER INSERT ON public.sync_log FOR EACH STATEMENT EXECUTE FUNCTION public.trg_sync_log_retention_fn()
```

### Demo 数据

```json
{
  "id": 1,
  "shop_id": "7494763368967603447",
  "sync_type": "orders_search",
  "started_at": "2026-08-16T09:43:20.626285+00:00",
  "finished_at": "2026-08-16T09:43:20.626285+00:00",
  "rows_affected": 0,
  "status": "error",
  "error_message": "Invalid credentials. The 'sign' query parameter is invalid. For more details: https://m.tiktok.shop/s/AIu6dbFhs2XW"
}
```

## `api_keys`

**库**: `tts_erp` · **行数**: 6

**作用**: 外部 API key：SHA-256 哈希 + 角色(readonly/readwrite/admin) + 过期时间。明文只在创建时出现一次。

### 建表语句

```sql
CREATE TABLE IF NOT EXISTS public.api_keys (
    id bigint NOT NULL,
    key_hash text NOT NULL,
    key_prefix text NOT NULL,
    name text NOT NULL,
    role text NOT NULL,
    enabled boolean DEFAULT true NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    last_used_at timestamp with time zone,
    expires_at timestamp with time zone,
    scopes text[] DEFAULT ARRAY[]::text[] NOT NULL,
    CONSTRAINT api_keys_role_check CHECK ((role = ANY (ARRAY['readonly'::text, 'readwrite'::text, 'admin'::text])))
);
```

### Demo 数据

```json
{
  "id": 1505,
  "key_hash": "<masked>",
  "key_prefix": "ttserp_admin_MrO",
  "name": "phase2-verify",
  "role": "admin",
  "enabled": true,
  "created_at": "2026-08-24T05:44:59.508304+00:00",
  "last_used_at": "2026-08-24T05:45:08.738625+00:00",
  "expires_at": null,
  "scopes": []
}
```

## `oauth_tokens`

**库**: `oauth_receiver` · **行数**: 2

**作用**: TikTok token 密文存储（bytea 加密列 + 过期时间 + granted_scopes）。(shop_id, provider) 唯一。属 oauth_receiver 库，重构拟并入主库。

### 建表语句

```sql
CREATE TABLE IF NOT EXISTS public.oauth_tokens (
    id bigint NOT NULL,
    shop_id text NOT NULL,
    provider text DEFAULT 'tiktok'::text NOT NULL,
    access_token_encrypted bytea NOT NULL,
    refresh_token_encrypted bytea NOT NULL,
    shop_cipher_encrypted bytea,
    shop_name text,
    shop_region text,
    seller_type text,
    access_token_expires_at bigint,
    refresh_token_expires_at bigint,
    granted_scopes text[],
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    last_used_at timestamp with time zone,
    last_refresh_at timestamp with time zone
);
```

### 索引

```sql
CREATE INDEX idx_oauth_tokens_expires ON public.oauth_tokens USING btree (access_token_expires_at)
CREATE OR REPLACE TRIGGER trg_oauth_tokens_touch BEFORE UPDATE ON public.oauth_tokens FOR EACH ROW EXECUTE FUNCTION public.touch_updated_at()
```

### Demo 数据

```json
{
  "id": 18,
  "shop_id": "MOCK_SHOP_12345",
  "provider": "tiktok",
  "access_token_encrypted": "<masked>",
  "refresh_token_encrypted": "<masked>",
  "shop_cipher_encrypted": "<masked>",
  "shop_name": "MOCK Test Shop",
  "shop_region": "US",
  "seller_type": "CROSS_BORDER",
  "access_token_expires_at": 1788434103,
  "refresh_token_expires_at": 1819365303,
  "granted_scopes": [
    "user_info",
    "orders",
    "products"
  ],
  "created_at": "2026-08-24T06:41:50.121837+00:00",
  "updated_at": "2026-08-27T11:15:03.255362+00:00",
  "last_used_at": null,
  "last_refresh_at": "2026-08-27T11:15:03.255362+00:00"
}
```

# 五、妙手域

## `miaoshou_shops`

**库**: `tts_erp` · **行数**: 1

**作用**: 妙手店铺授权信息（平台/站点/授权过期，gmt_* 为 text 时间）。

### 建表语句

```sql
CREATE TABLE IF NOT EXISTS public.miaoshou_shops (
    shop_id bigint NOT NULL,
    platform text NOT NULL,
    site text NOT NULL,
    platform_shop_name text,
    shop_nick text,
    parent_shop_id bigint,
    is_cb integer,
    is_cnsc integer,
    status text,
    gmt_expire text,
    gmt_last_auth text,
    raw_json jsonb,
    synced_at timestamp with time zone DEFAULT now()
);
```

### 索引

```sql
CREATE INDEX idx_miaoshou_shops_platform_site ON public.miaoshou_shops USING btree (platform, site)
CREATE INDEX idx_miaoshou_shops_synced_at ON public.miaoshou_shops USING btree (synced_at DESC)
```

### Demo 数据

```json
{
  "shop_id": 17060852,
  "platform": "tiktok",
  "site": "VN",
  "platform_shop_name": "Bridge nook",
  "shop_nick": null,
  "parent_shop_id": 17060851,
  "is_cb": 1,
  "is_cnsc": 1,
  "status": "normal",
  "gmt_expire": "2026-08-29 16:18:01",
  "gmt_last_auth": "2026-07-11 22:15:18",
  "raw_json": {
    "isCb": 1,
    "site": "VN",
    "isCnsc": 1,
    "shopId": 17060852,
    "status": "normal",
    "platform": "tiktok",
    "shopNick": null,
    "siteName": "越南",
    "gmtExpire": "2026-08-29 16:18:01",
    "gmtLastAuth": "2026-07-11 22:15:18",
    "parentShopId": 17060851,
    "platformShopName": "Bridge nook"
  },
  "synced_at": "2026-08-24T08:56:08.026982+00:00"
}
```

## `miaoshou_price_templates`

**库**: `tts_erp` · **行数**: 0

**作用**: 妙手定价模板（~45 列平铺：利润/汇率/物流计费规则）。

### 建表语句

```sql
CREATE TABLE IF NOT EXISTS public.miaoshou_price_templates (
    price_template_id bigint NOT NULL,
    app_account_id bigint,
    sub_app_account_id bigint,
    platform text,
    site text,
    name text,
    remark text,
    currency text,
    display_weight_unit text,
    profit_type text,
    profit_percent numeric,
    fixed_profit_amount numeric,
    exchange_rate numeric,
    discount numeric,
    price_tail_compute_type text,
    price_tail text,
    price_process_decimal_type text,
    logistics_compute_type text,
    weight_ref_type text,
    first_weight_charge numeric,
    first_weight_interval numeric,
    continued_weight_charge numeric,
    continued_weight_interval numeric,
    logistics_charge numeric,
    platform_charge_percent numeric,
    payment_charge_percent numeric,
    activity_charge_percent numeric,
    withdraw_charge_percent numeric,
    other_charge numeric,
    is_cal_light_cargo integer,
    light_cargo_coefficient integer,
    weight_logistics_charge_list text,
    domestic_logistics_compute_type text,
    domestic_logistics_first_weight_charge numeric,
    domestic_logistics_first_weight_interval numeric,
    domestic_logistics_continued_weight_charge numeric,
    domestic_logistics_continued_weight_interval numeric,
    domestic_logistics_charge numeric,
    buyer_logistic_charge numeric,
    seller_logistic_charge numeric,
    has_seller_logistic_charge integer,
    official_tpl_mode text,
    official_tpl_logistics_channel text,
    snapshot_id bigint,
    gmt_create text,
    gmt_modified text,
    raw_json jsonb,
    synced_at timestamp with time zone DEFAULT now()
);
```

### Demo 数据

（表为空，无数据）

## `miaoshou_collect_box_details`

**库**: `tts_erp` · **行数**: 0

**作用**: 妙手采集箱商品明细。

### 建表语句

```sql
CREATE TABLE IF NOT EXISTS public.miaoshou_collect_box_details (
    platform text NOT NULL,
    common_collect_box_detail_id bigint CONSTRAINT miaoshou_collect_box_detail_common_collect_box_detail__not_null NOT NULL,
    app_account_id bigint,
    sub_app_account_id bigint,
    item_num text,
    title text,
    thumbnail text,
    list_thumbnail text,
    price numeric,
    min_sku_price numeric,
    max_sku_price numeric,
    stock integer,
    remark text,
    status text,
    reason text,
    gmt_create text,
    gmt_modified text,
    weight numeric,
    max_sku_weight numeric,
    min_sku_weight numeric,
    common_collect_box_group_id bigint,
    common_collect_box_group_name text,
    owner_sub_account_alias_name text,
    is_mark text,
    is_cb integer,
    is_cnsc integer,
    raw_json jsonb,
    synced_at timestamp with time zone DEFAULT now()
);
```

### 索引

```sql
CREATE INDEX idx_miaoshou_collect_box_platform_status ON public.miaoshou_collect_box_details USING btree (platform, status)
```

### Demo 数据

（表为空，无数据）

## `miaoshou_move_collect_tasks`

**库**: `tts_erp` · **行数**: 10

**作用**: 妙手搬家/刊登任务。

### 建表语句

```sql
CREATE TABLE IF NOT EXISTS public.miaoshou_move_collect_tasks (
    platform text NOT NULL,
    move_collect_task_detail_id text CONSTRAINT miaoshou_move_collect_tasks_move_collect_task_detail_i_not_null NOT NULL,
    collect_box_detail_id text,
    shop_id text,
    item_num text,
    cid text,
    source text,
    source_site text,
    source_item_id text,
    title text,
    thumbnail text,
    is_timing text,
    status text,
    reason text,
    gmt_create text,
    gmt_modified text,
    platform_item_id text,
    is_renew_item boolean,
    shop_name text,
    site_name text,
    site text,
    source_item_url text,
    item_edit_url text,
    breadcrumb text,
    owner_sub_app_account_id bigint,
    owner_sub_account_alias_name text,
    raw_json jsonb,
    synced_at timestamp with time zone DEFAULT now()
);
```

### 索引

```sql
CREATE INDEX idx_miaoshou_move_collect_status ON public.miaoshou_move_collect_tasks USING btree (platform, status)
CREATE INDEX idx_miaoshou_move_collect_synced ON public.miaoshou_move_collect_tasks USING btree (synced_at DESC)
```

### Demo 数据

```json
{
  "platform": "tiktok",
  "move_collect_task_detail_id": "8507531700",
  "collect_box_detail_id": "3303946302",
  "shop_id": "17060852",
  "item_num": null,
  "cid": "601226",
  "source": "1688",
  "source_site": "",
  "source_item_id": "1047858038849",
  "title": "2026夏款男士圆领短袖休闲薄款T恤户外透气弹力夏季印花潮流迷彩",
  "thumbnail": "https://cbu01.alicdn.com/img/ibank/O1CN01BRBzlz2Ea8fruRGtz_!!2216730888760-0-cib.jpg",
  "is_timing": "0",
  "status": "success",
  "reason": null,
  "gmt_create": "2026-08-20 19:05:33",
  "gmt_modified": "2026-08-20 19:39:57",
  "platform_item_id": "1737133200968680695",
  "is_renew_item": false,
  "shop_name": "Bridge nook",
  "site_name": "越南",
  "site": "VN",
  "source_item_url": "http://detail.1688.com/offer/1047858038849.html",
  "item_edit_url": "https://seller.tiktokglobalshop.com/product/edit/1737133200968680695?shop_region=VN",
  "breadcrumb": "Trang phục nam & Đồ lót>Áo nam>Áo thun",
  "owner_sub_app_account_id": 0,
  "owner_sub_account_alias_name": "主账号",
  "raw_json": {
    "cid": "601226",
    "site": "VN",
    "title": "2026夏款男士圆领短袖休闲薄款T恤户外透气弹力夏季印花潮流迷彩",
    "reason": null,
    "shopId": "17060852",
    "source": "1688",
    "status": "success",
    "itemNum": null,
    "isTiming": "0",
    "shopName": "Bridge nook",
    "siteName": "越南",
    "gmtCreate": "2026-08-20 19:05:33",
    "thumbnail": "https://cbu01.alicdn.com/img/ibank/O1CN01BRBzlz2Ea8fruRGtz_!!2216730888760-0-cib.jpg",
    "breadcrumb": "Trang phục nam & Đồ lót>Áo nam>Áo thun",
    "sourceSite": "",
    "gmtModified": "2026-08-20 19:39:57",
    "isRenewItem": false,
    "itemEditUrl": "https://seller.tiktokglobalshop.com/product/edit/1737133200968680695?shop_region=VN",
    "sourceItemId": "1047858038849",
    "sourceItemUrl": "http://detail.1688.com/offer/1047858038849.html",
    "platformItemId": "1737133200968680695",
    "collectBoxDetailId": "3303946302",
    "ownerSubAppAccountId": 0,
    "moveCollectTaskDetailId": "8507531700",
    "ownerSubAccountAliasName": "主账号"
  },
  "synced_at": "2026-08-24T06:37:56.533427+00:00"
}
```

# 六、Analytics ingestion 子系统

## `analytics_records`

**库**: `tts_erp` · **行数**: 1

**作用**: 广告分析 ingestion 原始记录（幂等 key + request/response jsonb + schema/protocol 双版本号）。

### 建表语句

```sql
CREATE TABLE IF NOT EXISTS public.analytics_records (
    id bigint NOT NULL,
    idempotency_key text NOT NULL,
    source_record_id text,
    seller_id text NOT NULL,
    advertiser_id text NOT NULL,
    storage_key text NOT NULL,
    campaign_id text NOT NULL,
    day date NOT NULL,
    page integer NOT NULL,
    shop_name text,
    endpoint text NOT NULL,
    method text NOT NULL,
    request_body jsonb,
    response_data jsonb NOT NULL,
    source text NOT NULL,
    captured_at timestamp with time zone NOT NULL,
    schema_version integer DEFAULT 1 NOT NULL,
    protocol_version integer DEFAULT 1 NOT NULL,
    received_at timestamp with time zone DEFAULT now() NOT NULL,
    request_id text,
    expected_page_count integer,
    CONSTRAINT ck_analytics_records_page CHECK ((page > 0)),
    CONSTRAINT ck_analytics_records_protocol CHECK ((protocol_version > 0)),
    CONSTRAINT ck_analytics_records_schema CHECK ((schema_version > 0)),
    CONSTRAINT ck_analytics_records_storage CHECK ((storage_key = ANY (ARRAY['productAnalyses'::text, 'sessionAnalyses'::text, 'campaignChangeLogs'::text])))
);
```

### 索引

```sql
CREATE INDEX idx_analytics_records_received ON public.analytics_records USING btree (received_at DESC)
CREATE INDEX idx_analytics_records_request ON public.analytics_records USING btree (request_id)
CREATE INDEX idx_analytics_records_scope ON public.analytics_records USING btree (seller_id, advertiser_id, storage_key, campaign_id, day)
CREATE INDEX idx_analytics_records_scope_page ON public.analytics_records USING btree (seller_id, advertiser_id, storage_key, campaign_id, day, page)
```

### Demo 数据

```json
{
  "id": 415,
  "idempotency_key": "73b716cce7f8b2c4220b1be3e5ab6327c3a963eaf424af84412402ef8607dae3",
  "source_record_id": "00000000-0000-4000-8000-000000000001",
  "seller_id": "seller-1",
  "advertiser_id": "adv-1",
  "storage_key": "productAnalyses",
  "campaign_id": "campaign-1",
  "day": "2026-08-23",
  "page": 1,
  "shop_name": "integration-test",
  "endpoint": "/integration-test/analytics-sync",
  "method": "POST",
  "request_body": {
    "deleteMe": true,
    "integrationTest": true
  },
  "response_data": {
    "marker": "tk-adv-cost-monitor-protocol-test",
    "deleteMe": true,
    "integrationTest": true
  },
  "source": "integration_test",
  "captured_at": "2026-08-23T12:00:00+00:00",
  "schema_version": 1,
  "protocol_version": 1,
  "received_at": "2026-08-23T13:39:41.759355+00:00",
  "request_id": "5b5dc7ae-75f5-45f6-962b-1f1fb9334c68",
  "expected_page_count": 1
}
```

## `analytics_cursors`

**库**: `tts_erp` · **行数**: 1

**作用**: ingestion 游标：按 (seller, advertiser, storage_key, campaign) 记录已完成的最近一天。

### 建表语句

```sql
CREATE TABLE IF NOT EXISTS public.analytics_cursors (
    seller_id text NOT NULL,
    advertiser_id text NOT NULL,
    storage_key text NOT NULL,
    campaign_id text NOT NULL,
    latest_completed_day date,
    last_updated_at timestamp with time zone DEFAULT now() NOT NULL,
    request_id text,
    first_seen_day date,
    CONSTRAINT ck_analytics_cursors_storage CHECK ((storage_key = ANY (ARRAY['productAnalyses'::text, 'sessionAnalyses'::text, 'campaignChangeLogs'::text])))
);
```

### Demo 数据

```json
{
  "seller_id": "seller-1",
  "advertiser_id": "adv-1",
  "storage_key": "productAnalyses",
  "campaign_id": "campaign-1",
  "latest_completed_day": "2026-08-23",
  "last_updated_at": "2026-08-23T13:39:41.759355+00:00",
  "request_id": "5b5dc7ae-75f5-45f6-962b-1f1fb9334c68",
  "first_seen_day": null
}
```

## `analytics_daily_pages`

**库**: `tts_erp` · **行数**: 1

**作用**: 每日分页到达记录（哪天哪页已入库）。

### 建表语句

```sql
CREATE TABLE IF NOT EXISTS public.analytics_daily_pages (
    seller_id text NOT NULL,
    advertiser_id text NOT NULL,
    storage_key text NOT NULL,
    campaign_id text NOT NULL,
    day date NOT NULL,
    page integer NOT NULL,
    inserted_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT ck_analytics_daily_pages_page CHECK ((page > 0)),
    CONSTRAINT ck_analytics_daily_pages_storage CHECK ((storage_key = ANY (ARRAY['productAnalyses'::text, 'sessionAnalyses'::text, 'campaignChangeLogs'::text])))
);
```

### 索引

```sql
CREATE INDEX idx_analytics_daily_pages_unit ON public.analytics_daily_pages USING btree (seller_id, advertiser_id, storage_key, campaign_id, day)
```

### Demo 数据

```json
{
  "seller_id": "seller-1",
  "advertiser_id": "adv-1",
  "storage_key": "productAnalyses",
  "campaign_id": "campaign-1",
  "day": "2026-08-23",
  "page": 1,
  "inserted_at": "2026-08-26T17:06:18.584838+00:00"
}
```

## `analytics_daily_completeness`

**库**: `tts_erp` · **行数**: 1

**作用**: 每日完整性标记（expected_page_count vs 实际到达页）。

### 建表语句

```sql
CREATE TABLE IF NOT EXISTS public.analytics_daily_completeness (
    seller_id text NOT NULL,
    advertiser_id text NOT NULL,
    storage_key text NOT NULL,
    campaign_id text NOT NULL,
    day date NOT NULL,
    expected_page_count integer NOT NULL,
    is_complete boolean DEFAULT false NOT NULL,
    completed_at timestamp with time zone,
    last_recomputed_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT ck_analytics_daily_completeness_expected CHECK ((expected_page_count > 0)),
    CONSTRAINT ck_analytics_daily_completeness_storage CHECK ((storage_key = ANY (ARRAY['productAnalyses'::text, 'sessionAnalyses'::text, 'campaignChangeLogs'::text])))
);
```

### 索引

```sql
CREATE INDEX idx_analytics_daily_completeness_unit_complete ON public.analytics_daily_completeness USING btree (seller_id, advertiser_id, storage_key, campaign_id, day, is_complete)
```

### Demo 数据

```json
{
  "seller_id": "seller-1",
  "advertiser_id": "adv-1",
  "storage_key": "productAnalyses",
  "campaign_id": "campaign-1",
  "day": "2026-08-23",
  "expected_page_count": 1,
  "is_complete": true,
  "completed_at": "2026-08-26T17:06:18.584838+00:00",
  "last_recomputed_at": "2026-08-26T17:06:18.584838+00:00"
}
```

## `analytics_shop_timezones`

**库**: `tts_erp` · **行数**: 9

**作用**: seller 级时区配置（默认 Asia/Shanghai）。

### 建表语句

```sql
CREATE TABLE IF NOT EXISTS public.analytics_shop_timezones (
    seller_id text NOT NULL,
    advertiser_id text NOT NULL,
    timezone text DEFAULT 'Asia/Shanghai'::text NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);
```

### Demo 数据

```json
{
  "seller_id": "foo",
  "advertiser_id": "",
  "timezone": "Asia/Shanghai",
  "updated_at": "2026-08-23T06:28:26.71008+00:00"
}
```

## `analytics_audit_log`

**库**: `tts_erp` · **行数**: 6462

**作用**: ingestion 请求审计（endpoint/status/records_in/ok/rej）。

### 建表语句

```sql
CREATE TABLE IF NOT EXISTS public.analytics_audit_log (
    id bigint NOT NULL,
    request_id text,
    endpoint text NOT NULL,
    method text NOT NULL,
    path text NOT NULL,
    status integer NOT NULL,
    key_prefix text,
    records_in integer,
    records_ok integer,
    records_rej integer,
    error_code text,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);
```

### 索引

```sql
CREATE INDEX idx_analytics_audit_created ON public.analytics_audit_log USING btree (created_at DESC)
CREATE INDEX idx_analytics_audit_request ON public.analytics_audit_log USING btree (request_id)
```

### Demo 数据

```json
{
  "id": 1,
  "request_id": null,
  "endpoint": "cursor",
  "method": "GET",
  "path": "/v1/analytics/sync/cursor?sellerId=foo&advertiserId=bar",
  "status": 200,
  "key_prefix": null,
  "records_in": null,
  "records_ok": null,
  "records_rej": null,
  "error_code": null,
  "created_at": "2026-08-23T06:28:26.752097+00:00"
}
```

---

# 七、表关联分析（主键 / 唯一键 / 外键，基于 schema + 代码逻辑推理）

## 7.1 声明级约束（schema 中真实存在的）

**外键：0 个。** 这是显式设计决策（schema_tts_erp.sql 头注释 FK policy 2026-08-27）：sync-mirror 表不带 FK，写入是自然键幂等 upsert，父先子后由 sync 层保证。历史上唯一的例外 `logistics_events → orders` 已在 Wave 2 随该死表一起删除。

**主键（全部）与唯一键：**

| 表 | 主键 | 唯一键 | 代理 id（sequence） |
| ---- | ------ | -------- | --------------------- |
| orders | `order_id` | — | 否 |
| order_items | `(order_id, item_id)` | — | 否 |
| order_shippings | `order_id` | — | 否 |
| logistics_tracking | `order_id` | — | 否 |
| logistics_tracking_events | `(order_id, action_code, event_time)` | — | 否 |
| logistics_sync_targets | `order_id` | — | 否 |
| statements | `statement_id` | — | 否 |
| statement_transactions | `txn_id` | — | 否 |
| payments | `payment_id` | — | 否 |
| returns | `return_id` | — | 否 |
| cancellations | `cancel_id` | — | 否 |
| shops | `shop_id` | — | 否 |
| sync_log | `id` | — | 是（sync_log_id_seq） |
| api_keys | `id` | `key_hash`、`key_prefix` | 是（api_keys_id_seq） |
| miaoshou_shops | `(platform, site, shop_id)` | — | 否 |
| miaoshou_price_templates | `price_template_id` | — | 否 |
| miaoshou_collect_box_details | `(platform, common_collect_box_detail_id)` | — | 否 |
| miaoshou_move_collect_tasks | `(platform, move_collect_task_detail_id)` | — | 否 |
| analytics_records | `id` | `idempotency_key` | 是（analytics_records_id_seq） |
| analytics_cursors | `(seller_id, advertiser_id, storage_key, campaign_id)` | — | 否 |
| analytics_daily_completeness | `(seller_id, advertiser_id, storage_key, campaign_id, day)` | — | 否 |
| analytics_daily_pages | `(seller_id, advertiser_id, storage_key, campaign_id, day, page)` | — | 否 |
| analytics_shop_timezones | `seller_id` | — | 否 |
| analytics_audit_log | `id` | — | 是（analytics_audit_log_id_seq） |
| oauth_tokens | `id` | `(shop_id, provider)` | 是（oauth_tokens_id_seq） |

**CHECK 约束**：`api_keys.role ∈ {readonly, readwrite, admin}`；`analytics_*` 四张表的 `storage_key ∈ {productAnalyses, sessionAnalyses, campaignChangeLogs}`；`page > 0`、`expected_page_count > 0`、`schema_version/protocol_version > 0`。

**Trigger**：`touch_updated_at()` 维护 `orders.updated_at` / `oauth_tokens.updated_at` / `shops`（等有 updated_at 列的表）；`sync_log` 的 AFTER STATEMENT trigger 每次写入后执行 `cleanup_sync_log(60)` 清 60 天前日志。

## 7.2 逻辑外键（代码推理，无 DB 约束）

### 枢纽：orders.order_id（订单域 1:N / 1:1 星型）

```
orders (order_id)
 ├─1:N─ order_items.order_id          PK (order_id, item_id)
 ├─1:1─ order_shippings.order_id
 ├─1:1─ logistics_tracking.order_id
 ├─1:N─ logistics_tracking_events.order_id
 ├─1:1─ logistics_sync_targets.order_id   （水位表，只进不出）
 ├─1:N─ returns.order_id                 （可空场景：理论上必有）
 ├─1:N─ cancellations.order_id
 └─1:N─ statement_transactions.order_id   （可空：平台费/调整类交易无订单）
```

**代码证据**：应用层**没有任何 JOIN**。`/db/orders/{id}`、`/db/orders/{id}/items`、`/db/orders/{id}/shipping` 是三次独立 `WHERE order_id = %s` 查询，由客户端/调用方自行拼装（tts_erp_fastapi.py L691-716）。`logistics_tracking` 是 `logistics_tracking_events` 的**代码维护的物化聚合**（不是 VIEW）：sync 时先写事件流，再按 `update_time_millis` 排序算首末事件/里程碑后 upsert 聚合行。

### 财务域内部

```
payments (payment_id)
 └─1:1─ statements.payment_id            （有 idx_statements_payment_id 索引）
statements (statement_id)
 └─1:N─ statement_transactions.statement_id
```

代码证据：`pg_repositories.py:54` `SELECT statement_id FROM statements WHERE shop_id = %s` —— 先取 statement 列表，再逐个拉逐交易明细，父先子后。

### 店铺维度（横向关联所有业务表）

```
oauth_tokens.shop_id ──backfill──> shops.shop_id ──逻辑──> orders/returns/cancellations/
                                      （当前跨库）            statements/payments/sync_log.shop_id
```

`shops` 表完全由 `oauth_tokens` 幂等 backfill 而来（`/admin/shops/backfill` + startup lifespan），自身无独立写入源。**合并两库后这条跨库逻辑关联变成同库普通关联。**

### Analytics 子系统内部（与 ERP 域零关联）

```
共同维度键：(seller_id, advertiser_id, storage_key, campaign_id)

analytics_records (维度键 + day + page)     记录级，幂等 key 防重
  └─N:1─ analytics_daily_pages (维度键 + day + page)      页级到达登记
           └─N:1─ analytics_daily_completeness (维度键 + day)  天级完整性汇总
analytics_cursors (维度键)                   拉取进度水位（latest_completed_day）
analytics_shop_timezones (seller_id)         独立，按 seller 配时区
analytics_audit_log                          独立审计，无外联
```

代码证据：`analytics_sync/pg_repositories.py` —— 写 records 同事务 upsert daily_pages / daily_completeness，cursor 单独推进。**注意 `seller_id`/`advertiser_id` 是广告体系 id，与 TikTok Shop 的 `shop_id` 不是同一套标识，两域之间没有关联键。**

### 妙手域（与 ERP 域零关联）

```
miaoshou_shops (platform, site, shop_id)
 └─逻辑─ miaoshou_move_collect_tasks.shop_id        ⚠️ 类型不一致：shops.shop_id=bigint vs tasks.shop_id=text
miaoshou_collect_box_details (platform, common_collect_box_detail_id)
 └─逻辑─ miaoshou_move_collect_tasks.collect_box_detail_id  ⚠️ 同样 text vs bigint
miaoshou_price_templates                           独立（按 app_account 维度，无 shop 关联列）
```

## 7.3 关联设计观察点（供重构讨论）

1. **零 FK + 零 JOIN**：一致性完全靠 sync 写入顺序和幂等 upsert。优点是 TikTok 数据乱序/重放安全；代价是孤儿行无防护（如 `order_items` 可以有 `orders` 里没有的 order_id），且查询侧拼装成本转移给调用方。
2. **id 类型混用**：TikTok 域全部 text 自然键；妙手域 PK 混 bigint/text 且逻辑关联列类型不一致；5 张系统表用 sequence 代理 id。重构时需要统一 id 策略。
3. **跨库逻辑关联**：`orders.shop_id ↔ oauth_tokens.shop_id` 当前是跨库 + HTTP 隔离的，合库后可变为正常关联（甚至可以加真 FK）。
4. **聚合表靠代码维护**：`logistics_tracking`（事件→聚合）和 `analytics_daily_completeness`（页→天）都是代码物化，重算逻辑分散在 sync 路径里，重构可考虑物化视图或明确的派生表重算任务。
5. **三个域零关联却同库**：ERP 域（shop_id 体系）/ 妙手域（platform+site+shop_id 体系）/ analytics 域（seller_id+advertiser_id 体系）三套标识互不相通，分库/分 schema 有天然边界。
