# tts-erp 数据模型分析（重构基线）

> 2026-08-27 生成。来源：`schema_tts_erp.sql` / `schema_oauth.sql` + 线上库实测
> （行数、孤儿检查）。**schema 中没有任何 FOREIGN KEY 约束**，所有关联关系都靠
> 应用层（`tts_erp.py` 的 `persist_*` 函数）维护 —— 实测当前数据 0 孤儿行，
> 说明应用层目前是可靠的，但重构时应考虑把不变量下沉到 DB 层。

## 1. 库划分

| 库 | 归属服务 | 表数 | 说明 |
| --- | --- | --- | --- |
| `oauth_receiver` | oauth-receiver (:9876) | 1 (`oauth_tokens`) | token 加密存储，tts-erp **不直连** |
| `tts_erp` | tts-erp (:9877) | 24 | 全部业务数据 |

## 2. 域分组（tts_erp 库，24 表）

### 2.1 TikTok 订单域（核心）— 写入方：`tts_erp.persist_*` + sync_cron

| 表 | PK | 行数 | 关联 |
| --- | --- | --- | --- |
| `orders` | `order_id` | 687 | 核心实体。`shop_id` 逻辑外键 → `shops`；`raw jsonb` 存完整上游 payload |
| `order_items` | `(order_id, item_id)` | 715 | → `orders.order_id`。含 sku_id/product_id/quantity/sku_price |
| `order_shippings` | `(order_id)` | 673 | → `orders` **1:1**。tracking_number + provider |
| `returns` | `return_id` | 24 | → `orders.order_id`（可空字段但实测无 NULL）。退款金额在 `raw->'refund_amount'` 嵌套对象里 |
| `cancellations` | `cancel_id` | 160 | → `orders.order_id` |

### 2.2 TikTok 财务域 — 写入方：`persist_statement*` / `persist_payment`

| 表 | PK | 行数 | 关联 |
| --- | --- | --- | --- |
| `statements` | `statement_id` | 42 | 对账单。`payment_id` → `payments`（逻辑 FK）；金额 6 个 numeric 列 |
| `statement_transactions` | `txn_id` | 296 | → `statements.statement_id`；`order_id` → `orders`（可空）。**62 列**平铺的金额明细表（替代已删的 Excel 财务表），按 `type` 区分交易类型 |
| `payments` | `payment_id` | 5 | 付款记录。被 `statements.payment_id` 引用 |

### 2.3 物流追踪域 — 写入方：`persist_logistics_*`，cron 每 10min 追活跃运单

| 表 | PK | 行数 | 关联 |
| --- | --- | --- | --- |
| `logistics_tracking` | `order_id` | 575 | → `orders` 1:1 汇总视图（first/last event、final_status、arrived_overseas 等里程碑时间戳） |
| `logistics_tracking_events` | `(order_id, action_code, event_time)` | 10,920 | → `orders` 1:N 原始事件流 |
| `logistics_sync_targets` | `order_id` | 575 | → `orders`。同步调度状态（`needs_resync`、`last_n_events`），cron 增量抓取依据 |

注意：`order_shippings.tracking_number` 与 `logistics_tracking.tracking_number` 是
**两份冗余数据**（来源不同的两个上游端点），重构时可考虑统一。

### 2.4 店铺 / 鉴权 / 运维域

| 表 | PK | 行数 | 说明 |
| --- | --- | --- | --- |
| `shops` | `shop_id` | 2 | 店铺注册表。从 `oauth_tokens` backfill（startup lifespan + `/admin/shops/backfill`）。token 本体在 oauth_receiver 库 |
| `api_keys` | `id`，unique `key_hash`/`key_prefix` | 6 | API key 鉴权（只存 SHA-256 哈希）。role CHECK 约束 readonly/readwrite/admin |
| `sync_log` | `id` | 10,826 | 每次同步的历史记录（shop_id, sync_type, rows_affected, status）。**无 TTL，持续增长** —— retention.sql 存在但未自动化 |

### 2.5 妙手域（独立子系统，`miaoshou/`）— 写入方：`persist_miaoshou_*`

| 表 | PK | 行数 | 说明 |
| --- | --- | --- | --- |
| `miaoshou_shops` | `(platform, site, shop_id)` | 0 | 授权店铺快照 |
| `miaoshou_collect_box_details` | `(platform, common_collect_box_detail_id)` | 0 | 采集箱商品（28 列） |
| `miaoshou_move_collect_tasks` | `(platform, move_collect_task_detail_id)` | 10 | 搬家/采集任务（28 列） |
| `miaoshou_price_templates` | `price_template_id` | 0 | 定价模板（48 列） |

与 TikTok 订单域**无任何关联**，`shop_id` 是妙手侧的 bigint，不是 TikTok shop_id。

### 2.6 广告分析域（独立子系统，`analytics_sync/`）— 写入方：`analytics_sync/pg_repositories.py`

| 表 | PK | 行数 | 说明 |
| --- | --- | --- | --- |
| `analytics_records` | `id`，unique `idempotency_key` | 1 | 广告报表原始记录 + `raw jsonb` |
| `analytics_cursors` | `(seller_id, advertiser_id, storage_key, campaign_id)` | 1 | 增量抓取游标 |
| `analytics_daily_pages` | 复合 6 列 | 1 | 按日分页完整性追踪 |
| `analytics_daily_completeness` | 复合 5 列 | 1 | 日维度完整性 |
| `analytics_audit_log` | `id` | 6,462 | 请求审计。**持续增长** |
| `analytics_shop_timezones` | `seller_id` | 9 | 时区配置 |

`storage_key` CHECK 约束限定 `productAnalyses / sessionAnalyses / campaignChangeLogs` 等枚举。

### 2.7 oauth_receiver 库（不属于 tts-erp，仅供参照）

`oauth_tokens`：PK `id`，unique `(shop_id, provider)`。token/cipher 均为 `bytea` 加密列。
tts-erp 侧唯一合法的访问方式是 oauth-receiver HTTP API（见 AGENTS.md §2.1）。

## 3. 关联关系图（逻辑 FK，DB 层无约束）

```
                  ┌─────────────────────────────┐
                  │ oauth_receiver.oauth_tokens │  token 凭证，挂在 shop 上（非父级）
                  │  UNIQUE(shop_id, provider)  │  tts-erp 仅走 HTTP 访问，不直连
                  └──────────────┬──────────────┘
                                 │ 凭证 1:1（shop_id）
                           ┌─────▼─────┐
             1:N (shop_id) │   shops   │ 1:N (shop_id)
             ┌─────────────│  店铺主档  │──────────────┐
             ▼             └─────┬─────┘              ▼
      ┌──────────┐              │             ┌────────────┐ payment_id ┌──────────┐
      │  orders  │              │             │ statements │───────────►│ payments │
      └────┬─────┘              │             └─────┬──────┘            └──────────┘
           │ 1:N / 1:1          │                   │ 1:N (statement_id)
  ┌────────┼─────────┬──────────┴───┐         ┌─────▼──────────────────┐
  ▼        ▼         ▼              ▼         │ statement_transactions │── order_id ─► orders
 order_  order_   returns   logistics_*       └────────────────────────┘   （可空）
 items   shippings cancels   (见下)
 (1:N)   (1:1)    (N:1 订单)

  logistics 子结构（都挂 orders，都带 shop_id）：
    logistics_tracking (1:1, PK=order_id)
      └─ 1:N ─► logistics_tracking_events (PK=order_id+action_code+event_time)
    logistics_sync_targets (1:1, PK=order_id, cron 调度状态)

  孤岛（当前与 shops 无映射，见 §6 D2 决策）：
    [miaoshou_*]   shop_id 是妙手侧 bigint，不是 TikTok shop_id
    [analytics_*]  seller_id/advertiser_id 体系；线上仅有测试数据
```

## 4. 关键设计特征（重构时必须知道）

1. **零外键约束**：所有关系靠 `persist_*` 写入顺序保证。实测 0 孤儿（含
   statement_transactions→orders 可空关联）。重构时可选：(a) 补 FK 约束
   （需处理写入顺序和可空列）；(b) 保持现状但加测试守卫。
2. **`raw jsonb NOT NULL` 是通用逃生舱**：orders/items/shippings/returns/
   cancellations/statements/payments/logistics_tracking 全部带完整上游 payload。
   平铺列只是查询便利的投影 —— **重构加列不需要重新同步上游**，从 raw 回填即可。
3. **全库单店铺**：当前所有数据 `shop_id = 7494763368967603447`；`shops` 表 2 行。
   但 schema/代码已按多店铺设计（几乎每个表都带 shop_id 冗余列 + 索引）。
4. **PK 不带 shop_id**：`orders.order_id` 单独做主键，隐含的假设是 TikTok
   order_id 全局唯一（跨店铺不冲突）。多店铺扩张时这是**最需要验证的假设**；
   同理 `payments.payment_id`、`statements.statement_id`。
5. **时间戳类型不统一**：业务时间是 epoch 秒 `bigint`（create_time 等），
   系统时间是 `timestamptz`（synced_at/created_at）。API 层响应会补 `_iso`
   字段。重构若想统一到 timestamptz 要动所有读写路径。
6. **增长表无保留策略**：`sync_log`(10.8k)、`analytics_audit_log`(6.5k)、
   `logistics_tracking_events`(10.9k) 只增不减；`retention.sql` 存在但未接 cron。
7. **冗余/重叠**：
   - `order_shippings.tracking_number` vs `logistics_tracking.tracking_number`
   - `orders.order_status_name` vs `logistics_tracking.final_status`（生命周期重叠）
   - `shops` vs `oauth_tokens.shop_name/region`（backfill 镜像）
8. **写入入口唯一**：业务表写入全部集中在 `tts_erp.py` 的 14 个 `persist_*`
   函数（FastAPI 路由和 sync_cron 都走这里）；analytics 走
   `analytics_sync/pg_repositories.py`。重构数据访问层时这两个是仅有的接缝。

## 5. 实测完整性（2026-08-27）

| 检查 | 结果 |
| --- | --- |
| order_items / order_shippings / returns / cancellations 孤儿 | 0 |
| logistics_tracking / events 孤儿 | 0 |
| statement_transactions → statements 孤儿 | 0 |
| statement_transactions → orders 孤儿（非 NULL 部分） | 0 |
| returns.order_id 为 NULL | 0 |
| orders 无 items | 0 |

订单状态分布：COMPLETED 174 / CANCELLED 160 / DELIVERED 159 / IN_TRANSIT 134 /
AWAITING_COLLECTION 45 / AWAITING_SHIPMENT 14 / UNPAID 1（共 687，单店铺）。

⚠️ 测试数据残留（重构清理对象）：`shops` 含 `MOCK_SHOP_12345` 一行；
`analytics_shop_timezones` 9 行（foo / x / 店铺-A / shop-1…）、`analytics_records`
唯一一行的 `seller_id=shop-1, advertiser_id=adv-1` 均为测试写入，**当前不存在任何
真实的跨系统 ID 映射数据**。

## 6. 重构决策记录（2026-08-27 review）

> 来源：对本文档初稿的 code review，5 条 finding 逐条验证后的结论。
> 这些是**目标模型的设计决策**，开发前如需修改先改这里。

### D1. 关系图方向修正（已落实）

shop 是先存在的实体，token 是挂在 shop 上的凭证 —— §3 图已改为 shops 居中、
oauth_tokens 旁挂为 1:1 凭证。`shops` 只是从 oauth_tokens backfill 的镜像，
数据流向不代表领域父子关系。

### D2. shops 身份模型：(shop_id, region) 为唯一键的 meta 表（已定稿）

**决策**（2026-08-27 review 拍板）：`shop_identities` 是 shops 的元数据扩展
表，**`(shop_id, region)` 复合主键**。TikTok shop_id 是全系统的锚点，所有
数据以它为核心关联；region 进键意味着同一 shop_id 允许按区域分行（多区域
店铺各一行）。外部体系的身份信息作为附属 meta 挂在 shop 上，不设 `system`
列、不做反向约束。

```sql
CREATE TABLE IF NOT EXISTS public.shop_identities (
    shop_id     text NOT NULL,                -- → shops.shop_id
    region      text NOT NULL,                -- 店铺区域（US / VN / ...）
    identities  jsonb NOT NULL DEFAULT '{}',  -- 各体系身份：{"miaoshou": "...", "tiktok_business": "..."}
    meta        jsonb,                        -- 其他附属信息（授权到期、时区、备注等）
    created_at  timestamptz DEFAULT now() NOT NULL,
    updated_at  timestamptz DEFAULT now() NOT NULL
);
ALTER TABLE ONLY public.shop_identities
    ADD CONSTRAINT shop_identities_pkey PRIMARY KEY (shop_id, region);
```

- 身份信息先以 `identities` jsonb 承载：当前真实存在的跨系统 ID 为零
  （见 §5 测试数据残留），无法枚举该建哪些列；等真实接入落地、字段稳定后，
  高频访问的身份再提升为独立列；
- 已明确接受的取舍：一店在同一体系下只挂一个外部 ID（1:1）；若未来出现
  一对多（如一店多广告账号），届时再评估子表。当前业务前提（围绕 TikTok
  shop_id 构建）下不存在此场景。

### D3. orders 唯一性：方案 (a) — 保留单 PK，加租户唯一约束

**决策**：`orders` 保留 `order_id` 单列 PK，新增
`UNIQUE(shop_id, order_id)`；6 张子表（order_items / order_shippings /
returns / cancellations / logistics_*）的关联列**不动**。

理由：当前单店铺零迁移成本；子表 FK 不用级联改造。 TikTok order_id 全局唯一
的假设（§4.4）仍以 PK 形式保留，UNIQUE 约束只是显式声明租户维度。若未来真的
出现跨店铺 order_id 冲突，再评估复合 PK 方案 (b) 及其子表改造。

同样处理：`payments` / `statements` 加 `UNIQUE(shop_id, payment_id)` /
`UNIQUE(shop_id, statement_id)`。

### D4. returns + cancellations 合并为 after_sales，外部契约不变

**决策**：目标模型合并为一张 `after_sales` 表；对外端点 `/db/returns`、
`/db/cancellations`（含 `/db/returns/<id>`）**保持现有契约**，内部换实现。

目标表结构（草案）：

```sql
CREATE TABLE IF NOT EXISTS public.after_sales (
    kind         text NOT NULL,           -- 'return' | 'cancellation'
    id           text NOT NULL,           -- return_id / cancel_id（原样保留上游单号）
    shop_id      text NOT NULL,
    order_id     text,                    -- → orders.order_id
    status       text,                    -- 原 return_status / cancel_status
    reason       text,                    -- 原 return_reason / cancel_reason
    reason_text  text,                    -- 原 cancel_reason_text（return 侧恒 NULL）
    type         text,                    -- 原 return_type / cancel_type
    role         text,
    should_replenish_stock boolean,       -- 原 cancellations 独有列（return 侧恒 NULL）
    create_time  bigint,
    update_time  bigint,
    raw          jsonb NOT NULL,
    synced_at    timestamptz DEFAULT now() NOT NULL
);
ALTER TABLE ONLY public.after_sales
    ADD CONSTRAINT after_sales_pkey PRIMARY KEY (kind, id);
CREATE INDEX IF NOT EXISTS idx_after_sales_order ON public.after_sales USING btree (order_id);
CREATE INDEX IF NOT EXISTS idx_after_sales_shop_ct ON public.after_sales USING btree (shop_id, create_time DESC);
```

关键约束与保留意见：

- PK 用 `(kind, id)` 而**不是** `(shop_id, order_id, 单号)`：一单多次售后在
  TikTok 业务上合法（部分退款/多次退货申请），order_id 只做普通索引列。
  当前实测一单至多一条售后、且无订单同时出现在两表（样本小：returns 24 行），
  合并无损，但 schema 不为这个巧合背书。
- 外部契约保持：读端点 SQL 改查 `after_sales`，SELECT 列别名回旧字段名
  （`id AS return_id` 等），响应 JSON（含 `refund_amount` 计算字段）逐字节
  不变；不写 view 兼容层，验证通过后直接 DROP 旧表。
- `refund_amount` / `refund_currency` 计算字段仍在端点 SQL 层从
  `raw->'refund_amount'->>'refund_total'` 计算，与表结构解耦。

实施方案见 [`after-sales-migration.md`](after-sales-migration.md)（v2，待 review）。

### D5. finance 域 shop_id 加固（可选，随重构一起做）

`statements` / `payments` / `statement_transactions` / `returns` /
`cancellations` 的 shop_id 列已存在且实测无 NULL，但**可空、无 FK**。
重构时统一：`SET NOT NULL` + 视 D3 结论决定是否加
`FOREIGN KEY (shop_id) REFERENCES shops(shop_id)`。
§3 图已补 finance 域挂 shops 的边。

---

## 附录 A：完整建表语句（DDL）

> 逐字提取自 `schema_tts_erp.sql` / `schema_oauth.sql`（pg_dump 风格，
> PK/UNIQUE 约束以独立 ALTER TABLE 呈现）。索引未在此列出，见 §2 各表说明
> 与 schema 文件 L819+ 的 CREATE INDEX 段。
>
> ⚠️ **schema 文件与线上库存在 drift**：`sync_log` / `api_keys` / `analytics_records` /
> `analytics_audit_log` 四张表的 `id` 列在线上库有 `DEFAULT nextval('..._seq')`
> 序列自增，但 schema 文件里**没有**对应的 CREATE SEQUENCE / SET DEFAULT —— 用
> schema 文件全新建库时这四张表的 id 必须手动赋值。重构时应统一 schema 生成路径
> （`scripts/regen_schema.py`）让序列定义入库。

### A.1 店铺主档（tts_erp）

#### `shops`

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

ALTER TABLE ONLY public.shops
    ADD CONSTRAINT shops_pkey PRIMARY KEY (shop_id);
```

### A.2 TikTok 订单域

#### `orders`

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

ALTER TABLE ONLY public.orders
    ADD CONSTRAINT orders_pkey PRIMARY KEY (order_id);
```

#### `order_items`

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

ALTER TABLE ONLY public.order_items
    ADD CONSTRAINT order_items_pkey PRIMARY KEY (order_id, item_id);
```

#### `order_shippings`

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

ALTER TABLE ONLY public.order_shippings
    ADD CONSTRAINT order_shippings_pkey PRIMARY KEY (order_id);
```

#### `returns`

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

ALTER TABLE ONLY public.returns
    ADD CONSTRAINT returns_pkey PRIMARY KEY (return_id);
```

#### `cancellations`

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

ALTER TABLE ONLY public.cancellations
    ADD CONSTRAINT cancellations_pkey PRIMARY KEY (cancel_id);
```

### A.3 TikTok 财务域

#### `statements`

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

ALTER TABLE ONLY public.statements
    ADD CONSTRAINT statements_pkey PRIMARY KEY (statement_id);
```

#### `statement_transactions`

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

ALTER TABLE ONLY public.statement_transactions
    ADD CONSTRAINT statement_transactions_pkey PRIMARY KEY (txn_id);
```

#### `payments`

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

ALTER TABLE ONLY public.payments
    ADD CONSTRAINT payments_pkey PRIMARY KEY (payment_id);
```

### A.4 物流追踪域

#### `logistics_tracking`

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

ALTER TABLE ONLY public.logistics_tracking
    ADD CONSTRAINT logistics_tracking_pkey PRIMARY KEY (order_id);
```

#### `logistics_tracking_events`

```sql
CREATE TABLE IF NOT EXISTS public.logistics_tracking_events (
    order_id text NOT NULL,
    action_code integer NOT NULL,
    event_time bigint NOT NULL,
    description text,
    location text,
    synced_at timestamp with time zone DEFAULT now() NOT NULL
);

ALTER TABLE ONLY public.logistics_tracking_events
    ADD CONSTRAINT logistics_tracking_events_pkey PRIMARY KEY (order_id, action_code, event_time);
```

#### `logistics_sync_targets`

```sql
CREATE TABLE IF NOT EXISTS public.logistics_sync_targets (
    order_id text NOT NULL,
    shop_id text NOT NULL,
    last_synced_at timestamp with time zone,
    last_n_events integer,
    needs_resync boolean DEFAULT true NOT NULL
);

ALTER TABLE ONLY public.logistics_sync_targets
    ADD CONSTRAINT logistics_sync_targets_pkey PRIMARY KEY (order_id);
```

### A.5 鉴权 / 运维

#### `api_keys`

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

ALTER TABLE ONLY public.api_keys
    ADD CONSTRAINT api_keys_key_hash_key UNIQUE (key_hash);

ALTER TABLE ONLY public.api_keys
    ADD CONSTRAINT api_keys_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.api_keys
    ADD CONSTRAINT uq_api_keys_prefix UNIQUE (key_prefix);
```

#### `sync_log`

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

ALTER TABLE ONLY public.sync_log
    ADD CONSTRAINT sync_log_pkey PRIMARY KEY (id);
```

### A.6 妙手域

#### `miaoshou_shops`

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

ALTER TABLE ONLY public.miaoshou_shops
    ADD CONSTRAINT miaoshou_shops_pkey PRIMARY KEY (platform, site, shop_id);
```

#### `miaoshou_collect_box_details`

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

ALTER TABLE ONLY public.miaoshou_collect_box_details
    ADD CONSTRAINT miaoshou_collect_box_details_pkey PRIMARY KEY (platform, common_collect_box_detail_id);
```

#### `miaoshou_move_collect_tasks`

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

ALTER TABLE ONLY public.miaoshou_move_collect_tasks
    ADD CONSTRAINT miaoshou_move_collect_tasks_pkey PRIMARY KEY (platform, move_collect_task_detail_id);
```

#### `miaoshou_price_templates`

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

ALTER TABLE ONLY public.miaoshou_price_templates
    ADD CONSTRAINT miaoshou_price_templates_pkey PRIMARY KEY (price_template_id);
```

### A.7 广告分析域

#### `analytics_records`

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

ALTER TABLE ONLY public.analytics_records
    ADD CONSTRAINT analytics_records_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.analytics_records
    ADD CONSTRAINT uq_analytics_records_idem UNIQUE (idempotency_key);
```

#### `analytics_cursors`

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

ALTER TABLE ONLY public.analytics_cursors
    ADD CONSTRAINT analytics_cursors_pkey PRIMARY KEY (seller_id, advertiser_id, storage_key, campaign_id);
```

#### `analytics_daily_pages`

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

ALTER TABLE ONLY public.analytics_daily_pages
    ADD CONSTRAINT pk_analytics_daily_pages PRIMARY KEY (seller_id, advertiser_id, storage_key, campaign_id, day, page);
```

#### `analytics_daily_completeness`

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

ALTER TABLE ONLY public.analytics_daily_completeness
    ADD CONSTRAINT pk_analytics_daily_completeness PRIMARY KEY (seller_id, advertiser_id, storage_key, campaign_id, day);
```

#### `analytics_audit_log`

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

ALTER TABLE ONLY public.analytics_audit_log
    ADD CONSTRAINT analytics_audit_log_pkey PRIMARY KEY (id);
```

#### `analytics_shop_timezones`

```sql
CREATE TABLE IF NOT EXISTS public.analytics_shop_timezones (
    seller_id text NOT NULL,
    advertiser_id text NOT NULL,
    timezone text DEFAULT 'Asia/Shanghai'::text NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);

ALTER TABLE ONLY public.analytics_shop_timezones
    ADD CONSTRAINT analytics_shop_timezones_pkey PRIMARY KEY (seller_id);
```

### A.8 oauth_receiver 库（参照）

#### `oauth_tokens`

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

ALTER TABLE ONLY public.oauth_tokens
    ADD CONSTRAINT oauth_tokens_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.oauth_tokens
    ADD CONSTRAINT oauth_tokens_shop_provider_unique UNIQUE (shop_id, provider);
```
