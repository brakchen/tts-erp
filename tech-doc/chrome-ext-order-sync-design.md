# Chrome 扩展 订单/物流/结算 数据同步方案

> Date: 2026-09-08. Reference: `analytics/dump-architecture.md` (canonical pattern),
> `chrome-plugins/ads-data-sync/docs/HANDOFF-2026-09-08.md` (plugin side).

## 1. 问题

Chrome 扩展新增了对 TikTok Seller Center **订单/物流/结算**三个域的请求监听与数据同步。

| 域 | TikTok 端点 | 请求特征 | 痛点 |
| --- | --- | --- | --- |
| 订单 | `POST /api/fulfillment/order/list` | 列表分页，2-3 次请求拉完 | 低频，可接受 |
| 物流 | `GET /api/v1/fulfillment/logistic_detail/list` | **每单 1 次请求** | ⚠️ 高频：100 单 = 100 次请求 |
| 结算 | `GET /api/v1/pay/statement/list/detail` + `transaction/detail` | 列表分页 + 逐单明细 | 中频 |

**核心矛盾**：

1. **物流 N+1 问题**：订单列表 2-3 次 + 物流每单 1 次 = 100 单产生 ~103 次请求。TikTok 有频率限制，大量请求易触发风控。
2. **插件重装/更新后重复拉取**：插件无持久状态（chrome.storage.local 可被清除），重装后不知道哪些数据已同步到后端，会全量重拉。
3. **物流状态时效性**：物流状态随时间变化（已发货→运输中→已签收），"已同步"不等于"不需要更新"。

## 2. 设计方案

### 2.1 核心思路：复用 analytics dump 架构模式

参照现有广告分析同步（`analytics/dump-architecture.md`）的成熟模式：

- **cursor/has-data 端点**：插件先问后端"这些数据我已经有了吗"，避免重复抓取
- **dumps 端点**：插件把从 TikTok 抓到的原始 HTTP 响应上传到后端，后端做幂等存储
- **raw 表**：原始 dump 的 source-of-truth，后续可派生规范化数据

### 2.2 与 analytics 的关键差异

| 维度 | analytics | 订单/物流/结算 |
| --- | --- | --- |
| 粒度 | 每 (campaign, endpoint, day) 一行 | 每 (order_id) 或 (statement_id) 一行 |
| has-data 查询 | 单个单元存在性 | **批量查询**（一次传 N 个 order_id，按 shop_id 定位） |
| 时效性 | history=永不过期, today=当日刷新 | 物流有**保鲜期**（默认 24h） |
| 数据量 | 4 端点 × N campaign × M 天 | 订单/物流各 1 端点，结算 2 端点 |

### 2.3 架构总览

```
Chrome 插件                                    tts-erp 后端
─────────────                                 ─────────────
1. fetchOrderList()                           
   → 拿到 main_order_id[]                    
                                             
2. POST /v2/order-sync/has-data              
   body: {order_ids: [id1,id2,...id100]}     
   ← {covered: {id1:true, id2:false, ...}}  
                                             
3. 只对 covered=false 的 order_id:           
   fetchLogisticDetail(order_id)             
                                             
4. POST /v2/order-sync/dumps                 
   body: {domain:"logistics", ...}           
   ← {status:"inserted"}                    
                                             
5. 下次重装插件，重复步骤 1-2:               
   id1..id98 已 covered → 跳过              
   只拉 id99, id100                          
```

## 3. 数据模型

### 3.1 架构：`chrome_sync` schema（完全独立）

```
插件 POST /dumps
    │
    ▼
┌─────────────────────────────────────┐
│ POST /v2/order-sync/dumps           │
│                                     │
│  1. 校验请求                         │
│  2. 解析 TikTok 响应（inline）       │
│  3. 写入 chrome_sync 业务表          │
│  4. 写入 chrome_sync.raw_log（流水） │
│  5. 返回 inserted/updated/stale      │
└─────────────────────────────────────┘
```

**核心原则**：

- `raw_log` = 同步流水日志（每条 dump 一行），**不是暂存表**，不需要"未处理"状态
- 解析在 dump handler 内 **inline 完成**，不经过 sync-worker
- 业务表 = 查询 source of truth，has-data 查这里
- 与 `commerce`/`fulfillment`/`finance` **完全隔离**，不建 FK、不共享数据

```sql
CREATE SCHEMA IF NOT EXISTS chrome_sync;
```

### 3.2 `chrome_sync.raw_log` — 同步流水（完整 dump 存档）

每条 dump 请求一行，只追加不修改。存储完整的原始 dump 内容，用于审计、
问题排查和数据回溯。

```sql
CREATE TABLE chrome_sync.raw_log (
    id              BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    domain          TEXT NOT NULL,              -- 'orders' | 'logistics' | 'statements'
    shop_id         TEXT NOT NULL,              -- TikTok 外部 shop_id
    endpoint        TEXT NOT NULL,              -- TikTok 原始路径（如 /api/fulfillment/order/list）
    captured_at     TIMESTAMPTZ NOT NULL,       -- 插件抓取时间（TikTok 侧）
    request_params  JSONB,                      -- URL query params（如 {main_order_id: "..."}）
    request_body    JSONB,                      -- 请求 body（POST 时有值）
    response_body   JSONB NOT NULL,             -- 完整响应 body（原始 TikTok 响应）
    parse_error     TEXT,                        -- 解析失败原因；NULL = 解析成功
    rows_written    INT NOT NULL DEFAULT 0,     -- 本次写入业务表的行数
    source          TEXT NOT NULL DEFAULT 'chrome-ext',
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()  -- 后端收到时间
);

COMMENT ON TABLE chrome_sync.raw_log IS 'Chrome 扩展同步流水日志。每条 dump 请求一行，只追加不修改，存完整原始响应，用于审计和数据回溯。';
COMMENT ON COLUMN chrome_sync.raw_log.id IS '自增主键';
COMMENT ON COLUMN chrome_sync.raw_log.domain IS '同步域：orders=订单, logistics=物流, statements=结算';
COMMENT ON COLUMN chrome_sync.raw_log.shop_id IS 'TikTok 外部店铺 ID';
COMMENT ON COLUMN chrome_sync.raw_log.endpoint IS 'TikTok API 路径，如 /api/fulfillment/order/list';
COMMENT ON COLUMN chrome_sync.raw_log.captured_at IS '插件在 TikTok 页面抓取响应的时间';
COMMENT ON COLUMN chrome_sync.raw_log.request_params IS 'URL query params，如 {main_order_id: "...", offset: 0}';
COMMENT ON COLUMN chrome_sync.raw_log.request_body IS 'POST 请求 body（GET 请求为 NULL）';
COMMENT ON COLUMN chrome_sync.raw_log.response_body IS 'TikTok 完整原始响应，source-of-truth，可重跑解析修复业务表';
COMMENT ON COLUMN chrome_sync.raw_log.parse_error IS '解析失败原因；NULL 表示解析成功';
COMMENT ON COLUMN chrome_sync.raw_log.rows_written IS '本次解析写入业务表的行数';
COMMENT ON COLUMN chrome_sync.raw_log.source IS '数据来源标识，默认 chrome-ext';
COMMENT ON COLUMN chrome_sync.raw_log.created_at IS '后端收到并写入的时间';

CREATE INDEX ix_raw_log_domain_shop ON chrome_sync.raw_log(domain, shop_id);
CREATE INDEX ix_raw_log_created ON chrome_sync.raw_log(created_at);
CREATE INDEX ix_raw_log_endpoint ON chrome_sync.raw_log(endpoint);
```

**设计要点**：

- **无唯一约束**：同一 entity 可以多次同步（保鲜刷新），每次都是新的一行日志
- **存完整 dump**：endpoint + request_params + request_body + response_body 全量存，是所有同步数据的原始 source-of-truth
- **两个时间戳**：`captured_at`（插件抓取时间，TikTok 侧）+ `created_at`（后端收到时间）
- **解析状态**：`parse_error IS NULL` = 解析成功，`parse_error IS NOT NULL` = 解析失败（含原因）
- **retention**：定期清理 90 天前的日志（`DELETE FROM chrome_sync.raw_log WHERE created_at < now() - interval '90 days'`）；或按需保留更长
- **数据回溯**：业务表数据有问题时，可从 raw_log.response_body 重跑解析修复

### 3.3 业务表

#### `chrome_sync.orders` — 订单

```sql
CREATE TABLE chrome_sync.orders (
    id              BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    shop_id         TEXT NOT NULL,
    order_id        TEXT NOT NULL,              -- TikTok main_order_id
    status          TEXT,                       -- order_status_module.order_status
    currency        TEXT,                       -- ISO 4217
    payment_amount  NUMERIC(20,4),              -- price_module.payment.amount
    total_amount    NUMERIC(20,4),              -- price_module.total_amount.amount
    fulfillment_type TEXT,                      -- fulfillment_module.fulfillment_type
    order_time      TIMESTAMPTZ,               -- create_time
    paid_at         TIMESTAMPTZ,
    shipped_at      TIMESTAMPTZ,
    delivered_at    TIMESTAMPTZ,
    cancelled_at    TIMESTAMPTZ,
    raw_response    JSONB,                      -- 完整原始响应（可选，用于溯源）
    captured_at     TIMESTAMPTZ NOT NULL,       -- 插件抓取时间
    synced_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT uq_orders_shop_order UNIQUE (shop_id, order_id)
);

CREATE INDEX ix_orders_shop ON chrome_sync.orders(shop_id);
CREATE INDEX ix_orders_status ON chrome_sync.orders(status);

COMMENT ON TABLE chrome_sync.orders IS 'Chrome 扩展同步的 TikTok 订单头，来自 order/list 响应';
COMMENT ON COLUMN chrome_sync.orders.id IS '自增主键';
COMMENT ON COLUMN chrome_sync.orders.shop_id IS 'TikTok 外部店铺 ID';
COMMENT ON COLUMN chrome_sync.orders.order_id IS 'TikTok main_order_id';
COMMENT ON COLUMN chrome_sync.orders.status IS '订单状态，如 DELIVERED/CANCELLED/IN_TRANSIT';
COMMENT ON COLUMN chrome_sync.orders.payment_amount IS '买家实付金额（price_module.payment.amount）';
COMMENT ON COLUMN chrome_sync.orders.total_amount IS '订单总金额（price_module.total_amount.amount）';
COMMENT ON COLUMN chrome_sync.orders.fulfillment_type IS '履约方式，如 FBT/FBF';
COMMENT ON COLUMN chrome_sync.orders.order_time IS '下单时间（create_time，秒级 Unix 转换）';
COMMENT ON COLUMN chrome_sync.orders.paid_at IS '付款时间；0 或缺失为 NULL';
COMMENT ON COLUMN chrome_sync.orders.shipped_at IS '发货时间';
COMMENT ON COLUMN chrome_sync.orders.delivered_at IS '签收时间';
COMMENT ON COLUMN chrome_sync.orders.cancelled_at IS '取消时间；0 或缺失为 NULL';
COMMENT ON COLUMN chrome_sync.orders.raw_response IS 'TikTok order/list 完整原始响应（可选，溯源用）';
COMMENT ON COLUMN chrome_sync.orders.captured_at IS '插件在 TikTok 页面抓取响应的时间';
COMMENT ON COLUMN chrome_sync.orders.currency IS '订单币种，ISO 4217';
COMMENT ON COLUMN chrome_sync.orders.synced_at IS '数据入库时间';
COMMENT ON COLUMN chrome_sync.orders.updated_at IS '最后更新时间';
```

#### `chrome_sync.order_lines` — 订单行

```sql
CREATE TABLE chrome_sync.order_lines (
    id              BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    shop_id         TEXT NOT NULL,
    order_id        TEXT NOT NULL,              -- 关联 chrome_sync.orders.order_id
    sku_id          TEXT NOT NULL,              -- TikTok sku_id，同订单内唯一
    product_id      TEXT,                       -- TikTok product_id
    product_name    TEXT,
    variant_name    TEXT,
    image_url       TEXT,
    seller_sku      TEXT,
    quantity        NUMERIC(20,4),
    unit_price      NUMERIC(20,4),
    currency        TEXT,
    line_status     TEXT,
    synced_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT uq_order_lines_order_sku UNIQUE (shop_id, order_id, sku_id)
);

COMMENT ON TABLE chrome_sync.order_lines IS 'Chrome 扩展同步的 TikTok 订单行（SKU 级），来自 order/list 的 sku_module/fulfill_line_module';
COMMENT ON COLUMN chrome_sync.order_lines.id IS '自增主键';
COMMENT ON COLUMN chrome_sync.order_lines.product_name IS '商品名称快照';
COMMENT ON COLUMN chrome_sync.order_lines.variant_name IS 'SKU 名称快照';
COMMENT ON COLUMN chrome_sync.order_lines.image_url IS 'SKU 图片 URL 快照';
COMMENT ON COLUMN chrome_sync.order_lines.seller_sku IS '卖家自定义 SKU 编码';
COMMENT ON COLUMN chrome_sync.order_lines.line_status IS '行状态，如 DELIVERED/CANCELLED';
COMMENT ON COLUMN chrome_sync.order_lines.sku_id IS 'TikTok sku_id，同订单内唯一';
COMMENT ON COLUMN chrome_sync.order_lines.product_id IS 'TikTok product_id';
COMMENT ON COLUMN chrome_sync.order_lines.quantity IS '购买数量';
COMMENT ON COLUMN chrome_sync.order_lines.unit_price IS 'SKU 单价（sale_price.amount）';
COMMENT ON COLUMN chrome_sync.order_lines.shop_id IS 'TikTok 外部店铺 ID';
COMMENT ON COLUMN chrome_sync.order_lines.order_id IS '关联 chrome_sync.orders.order_id';
COMMENT ON COLUMN chrome_sync.order_lines.currency IS 'SKU 币种，ISO 4217';
COMMENT ON COLUMN chrome_sync.order_lines.synced_at IS '数据入库时间';
COMMENT ON COLUMN chrome_sync.order_lines.updated_at IS '最后更新时间';
```

#### `chrome_sync.shipments` — 物流包裹

```sql
CREATE TABLE chrome_sync.shipments (
    id              BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    shop_id         TEXT NOT NULL,
    order_id        TEXT NOT NULL,              -- 关联 chrome_sync.orders.order_id
    package_id      TEXT NOT NULL,              -- TikTok package_id
    tracking_number TEXT,
    carrier_name    TEXT,                       -- logistic_supplier
    status          TEXT,                       -- 最新轨迹状态
    shipped_at      TIMESTAMPTZ,               -- 首条轨迹时间
    delivered_at    TIMESTAMPTZ,               -- 最后一条轨迹时间（仅 status 含 delivered）
    raw_response    JSONB,
    captured_at     TIMESTAMPTZ NOT NULL,       -- 插件抓取时间（保鲜判断依据）
    synced_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT uq_shipments_shop_pkg UNIQUE (shop_id, package_id)
);

CREATE INDEX ix_shipments_order ON chrome_sync.shipments(shop_id, order_id);
CREATE INDEX ix_shipments_captured ON chrome_sync.shipments(captured_at);

COMMENT ON TABLE chrome_sync.shipments IS 'Chrome 扩展同步的 TikTok 物流包裹，来自 logistic_detail/list 的 package_list[]';
COMMENT ON COLUMN chrome_sync.shipments.id IS '自增主键';
COMMENT ON COLUMN chrome_sync.shipments.shop_id IS 'TikTok 外部店铺 ID';
COMMENT ON COLUMN chrome_sync.shipments.package_id IS 'TikTok package_id';
COMMENT ON COLUMN chrome_sync.shipments.tracking_number IS '运单号（tracking_no）';
COMMENT ON COLUMN chrome_sync.shipments.carrier_name IS '物流服务商（logistic_supplier）';
COMMENT ON COLUMN chrome_sync.shipments.status IS '最新轨迹状态（track_list 最后一条）';
COMMENT ON COLUMN chrome_sync.shipments.shipped_at IS '发货时间（首条轨迹时间）';
COMMENT ON COLUMN chrome_sync.shipments.delivered_at IS '签收时间（仅 status 含 delivered 时填入）';
COMMENT ON COLUMN chrome_sync.shipments.captured_at IS '插件抓取时间，用于保鲜判断';
COMMENT ON COLUMN chrome_sync.shipments.order_id IS '关联 chrome_sync.orders.order_id';
COMMENT ON COLUMN chrome_sync.shipments.raw_response IS 'TikTok logistic_detail/list 完整原始响应';
COMMENT ON COLUMN chrome_sync.shipments.synced_at IS '数据入库时间';
COMMENT ON COLUMN chrome_sync.shipments.updated_at IS '最后更新时间';
```

#### `chrome_sync.tracking_events` — 物流轨迹

```sql
CREATE TABLE chrome_sync.tracking_events (
    id              BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    shop_id         TEXT NOT NULL,
    package_id      TEXT NOT NULL,              -- 关联 chrome_sync.shipments.package_id
    event_key       TEXT NOT NULL,              -- 合成唯一键（package_id + index 或 time）
    event_at        TIMESTAMPTZ,
    description     TEXT,                       -- track_status 原文
    location        TEXT,
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT uq_tracking_events_pkg_key UNIQUE (shop_id, package_id, event_key)
);

COMMENT ON TABLE chrome_sync.tracking_events IS 'Chrome 扩展同步的物流轨迹事件，来自 logistic_detail/list 的 track_list[]';
COMMENT ON COLUMN chrome_sync.tracking_events.id IS '自增主键';
COMMENT ON COLUMN chrome_sync.tracking_events.shop_id IS 'TikTok 外部店铺 ID';
COMMENT ON COLUMN chrome_sync.tracking_events.package_id IS '关联 chrome_sync.shipments.package_id';
COMMENT ON COLUMN chrome_sync.tracking_events.event_key IS '合成唯一键，如 {package_id}_{index}';
COMMENT ON COLUMN chrome_sync.tracking_events.event_at IS '轨迹发生时间';
COMMENT ON COLUMN chrome_sync.tracking_events.description IS '轨迹描述原文（track_status）';
COMMENT ON COLUMN chrome_sync.tracking_events.location IS '轨迹地点';
COMMENT ON COLUMN chrome_sync.tracking_events.updated_at IS '最后更新时间';
```

#### `chrome_sync.settlements` — 结算单

```sql
CREATE TABLE chrome_sync.settlements (
    id                  BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    shop_id             TEXT NOT NULL,
    statement_id        TEXT NOT NULL,
    statement_version   INT NOT NULL DEFAULT 0,
    bill_period         TEXT,                       -- 原始 "2026-09-01~2026-09-07"
    period_start        DATE,
    period_end          DATE,
    settlement_time     TIMESTAMPTZ,
    payment_id          TEXT,
    payment_status      TEXT,                       -- 'PENDING' / 'PAID' / 'FAILED'
    settle_amount       NUMERIC(20,4),
    earning_amount      NUMERIC(20,4),
    fee_amount          NUMERIC(20,4),
    adjust_amount       NUMERIC(20,4),
    payable_amount      NUMERIC(20,4),
    shipping_amount     NUMERIC(20,4),
    currency            TEXT,
    raw_response        JSONB,
    captured_at         TIMESTAMPTZ NOT NULL,
    synced_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT uq_settlements_shop_stmt UNIQUE (shop_id, statement_id, statement_version)
);

CREATE INDEX ix_settlements_shop ON chrome_sync.settlements(shop_id);

COMMENT ON TABLE chrome_sync.settlements IS 'Chrome 扩展同步的 TikTok 结算单头，来自 statement/list/detail';
COMMENT ON COLUMN chrome_sync.settlements.id IS '自增主键';
COMMENT ON COLUMN chrome_sync.settlements.shop_id IS 'TikTok 外部店铺 ID';
COMMENT ON COLUMN chrome_sync.settlements.captured_at IS '插件在 TikTok 页面抓取响应的时间';
COMMENT ON COLUMN chrome_sync.settlements.statement_id IS 'TikTok statement_id';
COMMENT ON COLUMN chrome_sync.settlements.statement_version IS '结算版本号';
COMMENT ON COLUMN chrome_sync.settlements.bill_period IS '账期原始文本，如 2026-09-01~2026-09-07';
COMMENT ON COLUMN chrome_sync.settlements.settle_amount IS '结算金额';
COMMENT ON COLUMN chrome_sync.settlements.payable_amount IS '应付金额';
COMMENT ON COLUMN chrome_sync.settlements.payment_id IS 'TikTok payment_id，关联打款';
COMMENT ON COLUMN chrome_sync.settlements.period_start IS '账期起始日（从 bill_period 解析）';
COMMENT ON COLUMN chrome_sync.settlements.period_end IS '账期结束日（从 bill_period 解析）';
COMMENT ON COLUMN chrome_sync.settlements.settlement_time IS '结算时间';
COMMENT ON COLUMN chrome_sync.settlements.payment_status IS '打款状态：PENDING / PAID / FAILED';
COMMENT ON COLUMN chrome_sync.settlements.earning_amount IS '收入金额';
COMMENT ON COLUMN chrome_sync.settlements.fee_amount IS '费用金额';
COMMENT ON COLUMN chrome_sync.settlements.adjust_amount IS '调整金额';
COMMENT ON COLUMN chrome_sync.settlements.shipping_amount IS '运费金额';
COMMENT ON COLUMN chrome_sync.settlements.currency IS '币种，ISO 4217';
COMMENT ON COLUMN chrome_sync.settlements.raw_response IS 'TikTok statement/list/detail 完整原始响应';
COMMENT ON COLUMN chrome_sync.settlements.synced_at IS '数据入库时间';
COMMENT ON COLUMN chrome_sync.settlements.updated_at IS '最后更新时间';
```

#### `chrome_sync.settlement_details` — SKU 级结算明细 + 费用拆分

```sql
CREATE TABLE chrome_sync.settlement_details (
    id                      BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    shop_id                 TEXT NOT NULL,
    statement_id            TEXT NOT NULL,
    statement_version       INT NOT NULL DEFAULT 0,
    sku_detail_id           TEXT NOT NULL,              -- statement_sku_detail_id
    trade_order_id          TEXT,                       -- TikTok trade_order_id（暂无 main_order_id 映射）
    sku_id                  TEXT,
    product_name            TEXT,
    sku_name                TEXT,
    quantity                NUMERIC(20,4),
    settlement_status       TEXT,
    placed_time             TIMESTAMPTZ,
    settlement_amount       NUMERIC(20,4),
    earning_amount          NUMERIC(20,4),
    fees_amount             NUMERIC(20,4),
    currency                TEXT,
    fee_components          JSONB,                      -- 递归展开后的扁平 [{code, amount, currency}]
    raw_response            JSONB,
    captured_at             TIMESTAMPTZ NOT NULL,
    synced_at               TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT uq_settlement_details_shop_sku UNIQUE (shop_id, sku_detail_id)
);

CREATE INDEX ix_settlement_details_stmt ON chrome_sync.settlement_details(shop_id, statement_id);

COMMENT ON TABLE chrome_sync.settlement_details IS 'Chrome 扩展同步的 SKU 级结算明细 + 费用拆分，来自 statement/transaction/detail';
COMMENT ON COLUMN chrome_sync.settlement_details.id IS '自增主键';
COMMENT ON COLUMN chrome_sync.settlement_details.shop_id IS 'TikTok 外部店铺 ID';
COMMENT ON COLUMN chrome_sync.settlement_details.quantity IS '购买数量（NUMERIC 兼容小数）';
COMMENT ON COLUMN chrome_sync.settlement_details.captured_at IS '插件在 TikTok 页面抓取响应的时间';
COMMENT ON COLUMN chrome_sync.settlement_details.sku_detail_id IS 'TikTok statement_sku_detail_id，唯一标识一笔 SKU 级结算';
COMMENT ON COLUMN chrome_sync.settlement_details.trade_order_id IS 'TikTok trade_order_id，与 main_order_id 映射关系待验证';
COMMENT ON COLUMN chrome_sync.settlement_details.fee_components IS '递归展开后的扁平费用列表 [{code, amount, currency}]';
COMMENT ON COLUMN chrome_sync.settlement_details.statement_id IS '关联 chrome_sync.settlements.statement_id';
COMMENT ON COLUMN chrome_sync.settlement_details.statement_version IS '关联 chrome_sync.settlements.statement_version';
COMMENT ON COLUMN chrome_sync.settlement_details.sku_id IS 'TikTok sku_id';
COMMENT ON COLUMN chrome_sync.settlement_details.product_name IS '商品名称';
COMMENT ON COLUMN chrome_sync.settlement_details.sku_name IS 'SKU 名称';
COMMENT ON COLUMN chrome_sync.settlement_details.settlement_status IS '结算状态（文本枚举）';
COMMENT ON COLUMN chrome_sync.settlement_details.placed_time IS '下单时间';
COMMENT ON COLUMN chrome_sync.settlement_details.settlement_amount IS '结算金额';
COMMENT ON COLUMN chrome_sync.settlement_details.earning_amount IS '收入金额';
COMMENT ON COLUMN chrome_sync.settlement_details.fees_amount IS '费用总金额';
COMMENT ON COLUMN chrome_sync.settlement_details.currency IS '币种，ISO 4217';
COMMENT ON COLUMN chrome_sync.settlement_details.raw_response IS 'TikTok statement/transaction/detail 完整原始响应';
COMMENT ON COLUMN chrome_sync.settlement_details.synced_at IS '数据入库时间';
COMMENT ON COLUMN chrome_sync.settlement_details.updated_at IS '最后更新时间';
```

### 3.4 表设计决策

| 决策 | 选择 | 理由 |
| --- | --- | --- |
| `raw_log` 存储内容 | 完整 dump（request + response + 元数据） | 原始 source-of-truth，可从 raw_log 重跑解析修复业务表 |
| 解析时机 | inline（dump handler 内） | 数据立即可查，不需要等 sync-worker 轮询 |
| 业务表放在哪 | `chrome_sync` schema（独立） | 与 sync-worker 的 `commerce`/`fulfillment`/`finance` 完全隔离 |
| 物流保鲜 | 有保鲜期（24h），查 `shipments.captured_at` | 物流状态实时变化 |
| 订单/结算保鲜 | 永不过期 | 创建后核心字段不变 |
| `fee_components` 存 JSONB vs EAV | JSONB | 费用树递归结构，EAV 展开太碎；JSONB 保留完整层级 |

## 4. API 设计

路由挂载：`/v2/order-sync/*`（与 `/v2/analytics/sync/*` 平级）。

### 4.1 `POST /v2/order-sync/has-data` — 批量查询已有数据

**这是解决 N+1 问题的核心端点。**

插件拿到 order_id 列表后，一次请求查出哪些已有数据，只对缺失的发 TikTok 请求。

```http
POST /v2/order-sync/has-data
Authorization: Bearer <key>
Content-Type: application/json

{
  "scope": {
    "sellerId": "7493838482981827388",
    "shopId": "7493838482981827388"
  },
  "domain": "logistics",
  "ids": ["id1", "id2", ..., "id100"]
}
```

**响应**：

```json
{
  "code": 0,
  "requestId": "req-xxx",
  "data": {
    "domain": "logistics",
    "covered": {
      "id1": true,
      "id2": false,
      "id3": true
    },
    "freshnessHours": 24
  }
}
```

**后端逻辑（查业务表）**：

```python
if domain == "orders":
    rows = sess.execute(
        select(ChromeOrder.order_id)
        .where(ChromeOrder.shop_id == shop_id, ChromeOrder.order_id.in_(ids))
    ).scalars().all()

elif domain == "logistics":
    cutoff = datetime.now(UTC) - timedelta(hours=FRESHNESS_HOURS)
    rows = sess.execute(
        select(ChromeShipment.order_id.distinct())
        .where(
            ChromeShipment.shop_id == shop_id,
            ChromeShipment.order_id.in_(ids),
            ChromeShipment.captured_at >= cutoff,
        )
    ).scalars().all()

elif domain == "statements":
    rows = sess.execute(
        select(ChromeSettlement.statement_id)
        .where(
            ChromeSettlement.shop_id == shop_id,
            ChromeSettlement.statement_id.in_(ids),
        )
    ).scalars().all()

covered = {id: (id in set(rows)) for id in ids}
```

### 4.2 `POST /v2/order-sync/dumps` — 上传 + 解析 + 写入

dump 请求进来后 **立即解析并写入业务表**，同时写 `raw_log` 流水。

```http
POST /v2/order-sync/dumps
Authorization: Bearer <key>
Content-Type: application/json

{
  "protocolVersion": 1,
  "requestId": "uuid",
  "scope": {
    "sellerId": "7493838482981827388",
    "shopId": "7493838482981827388"
  },
  "dump": {
    "domain": "logistics",
    "mainOrderId": "57694276327119",
    "endpoint": "/api/v1/fulfillment/logistic_detail/list",
    "method": "GET",
    "request": { "params": {"main_order_id": "57694276327119"}, "body": null },
    "response": { "status": 200, "body": { ... } },
    "capturedAt": "2026-09-08T10:30:00.000Z"
  }
}
```

**后端 handler 伪代码**：

```python
def post_dumps(request):
    payload = validate(request)

    # 1. 解析 TikTok 响应 → 结构化数据
    try:
        if payload.dump.domain == "orders":
            records = parse_order_response(payload.dump.response.body)
        elif payload.dump.domain == "logistics":
            records = parse_logistics_response(payload.dump.response.body)
        elif payload.dump.domain == "statements":
            records = parse_statement_response(payload.dump.response.body)
    except ParseError as e:
        # 解析失败 → 只写 raw_log，返回 error
        write_raw_log(domain, shop_id, endpoint, captured_at,
                      request_params, request_body, response_body,
                      parse_error=str(e), rows_written=0)
        return error_response(400, "PARSE_ERROR", str(e))

    # 2. 写入业务表（幂等：ON CONFLICT DO UPDATE）
    rows_written = upsert_business_table(sess, payload.dump.domain, records, captured_at)

    # 3. 写 raw_log 流水（完整 dump 存档，无论解析成功失败都写）
    write_raw_log(
        domain=payload.dump.domain,
        shop_id=shop_id,
        endpoint=payload.dump.endpoint,
        captured_at=payload.dump.capturedAt,
        request_params=payload.dump.request.params,  // URL query params
        request_body=payload.dump.request.body,
        response_body=payload.dump.response.body,
        parse_error=None,
        rows_written=rows_written,
    )

    # 4. 返回
    return {"status": "inserted"}  # or "updated" / "stale_ignored"
```

**幂等语义**：

- 业务表用 `ON CONFLICT DO UPDATE`（unique key 覆盖）
- `captured_at` 单调守卫：新 captured_at > 旧 → updated；≤ 旧 → stale_ignored
- raw_log **始终追加**（不做幂等，每次 dump 都是一行日志）

### 4.3 `GET /v2/order-sync/synced-ids` — 查询已同步的 id 列表

```http
GET /v2/order-sync/synced-ids?shopId=7493838482981827388&domain=orders&limit=500&offset=0
Authorization: Bearer <key>
```

**响应**：

```json
{
  "code": 0,
  "data": {
    "domain": "orders",
    "ids": ["id1", "id2", ...],
    "total": 1234,
    "limit": 500,
    "offset": 0
  }
}
```

## 5. 插件侧对接流程

### 5.1 正常同步流程（插件已在运行）

```
┌─────────────────────────────────────────────────────────────┐
│ 1. 插件监听到 order/list 响应                                 │
│    → 提取 main_order_id[]                                   │
│                                                             │
│ 2. POST /v2/order-sync/dumps (domain=orders)                │
│    → 后端立即解析 + 写入业务表 + 写 raw_log                   │
│                                                             │
│ 3. POST /v2/order-sync/has-data (domain=logistics, ids=[..])│
│    → 返回 {id1:true, id2:false, ...}                        │
│                                                             │
│ 4. 只对 covered=false 的 id:                                 │
│    fetchLogisticDetail(id)                                  │
│    → POST /v2/order-sync/dumps (domain=logistics)           │
│                                                             │
│ 5. 结算同理：先 has-data 再 dumps                            │
└─────────────────────────────────────────────────────────────┘
```

**效果**：100 个订单，如果 90 个已有物流数据 → 只发 10 次物流请求（而非 100 次）。

### 5.2 插件重装后的恢复流程

```
┌─────────────────────────────────────────────────────────────┐
│ 1. 插件安装/更新后首次运行                                    │
│                                                             │
│ 2. fetchOrderList() → 拿到当前页 order_id[]                  │
│                                                             │
│ 3. POST /v2/order-sync/has-data (domain=logistics, ids=[..])│
│    → 大部分 covered=true（后端业务表数据还在）                │
│                                                             │
│ 4. 只对 covered=false 的新订单发物流请求                      │
│    → 节省 90%+ 请求                                          │
│                                                             │
│ 5. 或者用 GET /v2/order-sync/synced-ids 做更粗粒度过滤       │
└─────────────────────────────────────────────────────────────┘
```

### 5.3 物流保鲜刷新流程

```
┌─────────────────────────────────────────────────────────────┐
│ 场景：订单 3 天前下单，物流状态可能已变化                       │
│                                                             │
│ 1. POST /v2/order-sync/has-data (domain=logistics)          │
│    → shipments.captured_at 超过 24h → covered=false         │
│                                                             │
│ 2. 插件重新抓取该订单物流                                     │
│    → POST /v2/order-sync/dumps (domain=logistics)           │
│    → 后端 UPSERT shipments + tracking_events                │
│    → captured_at 更新 → 下次 covered=true                    │
│                                                             │
│ 3. raw_log 记录两次同步流水（第一次 + 保鲜刷新）              │
└─────────────────────────────────────────────────────────────┘
```

## 6. 后端实现清单

### 6.1 数据库

| 文件 | 内容 |
| --- | --- |
| `alembic/versions/XXXX_chrome_sync_schema.py` | 创建 `chrome_sync` schema + 7 张表 |
| `tts_erp_v2/db/models/chrome_sync.py` | SQLAlchemy 模型（7 个 class） |
| `schema_tts_erp.sql` | `python3 scripts/regen_schema.py` 重新生成 |

### 6.2 API + 解析层

| 文件 | 内容 |
| --- | --- |
| `tts_erp_v2/api/v2/order_sync.py` | 新路由：`/v2/order-sync/{has-data,dumps,synced-ids}` |
| `tts_erp_v2/chrome_sync/parser.py` | 解析函数：`parse_order_response()` / `parse_logistics_response()` / `parse_statement_response()` |
| `tts_erp_v2/chrome_sync/repository.py` | `has_data_bulk()` / `upsert_order()` / `upsert_logistics()` / `upsert_statement()` / `write_raw_log()` |
| `tts_erp_v2/app.py` | 挂载新路由 |

### 6.3 测试

| 文件 | 内容 |
| --- | --- |
| `tests/api/test_order_sync_contract.py` | 端点契约测试 |
| `tests/chrome_sync/test_parser.py` | 解析函数单测（各种边界 case） |

### 6.4 文档

| 文件 | 内容 |
| --- | --- |
| `tech-doc/external-api.md` | 新增 `/v2/order-sync/*` 端点文档 |

## 7. 配置

```bash
# .env
TTS_ERP_LOGISTICS_FRESHNESS_HOURS=24   # 物流保鲜窗口，默认 24h
```

保鲜窗口可通过 API 响应的 `freshnessHours` 字段告知插件，便于动态调整。

## 8. 与现有系统的关系

| 现有组件 | 关系 |
| --- | --- |
| `commerce.*` / `fulfillment.*` / `finance.*` | **完全隔离**。chrome_sync 有自己独立的 orders/shipments/settlements 表，不建 FK、不共享数据、不走 sync-worker |
| `analytics.ad_raw` | 模式相似（dump → 存储），但 analytics 用 raw 暂存 + sync-worker 派生；chrome_sync 是 inline 解析 + raw_log 审计 |
| `integration.raw_records` | 旧 v1 遗物，存 sync-worker 拉的数据。chrome_sync 来源完全不同（Chrome 扩展抓的） |
| `sync_worker` | **不参与**。chrome_sync 的解析在 API handler 内 inline 完成，不需要调度 |

**隔离原因**：

1. 数据来源不同：sync-worker 从 TikTok Open API 拉数据（需 API key + HMAC 签名）；Chrome 扩展从浏览器会话抓数据（cookie 认证）
2. 数据完整性不同：sync-worker 数据经过 API 契约校验；Chrome 扩展数据来自浏览器抓包，字段可能缺失
3. 更新节奏不同：sync-worker 按 cron 调度；Chrome 扩展按用户浏览实时同步

## 9. 演进路径

| 阶段 | 做什么 | 价值 |
| --- | --- | --- |
| **Phase 1（本次）** | `chrome_sync` 7 张表 + has-data/dumps/synced-ids 端点 + inline 解析 | 解决插件重复拉取问题，数据立即可查 |
| **Phase 2** | 物流保鲜窗口动态化（已签收→永不过期，运输中→24h） | 减少不必要的保鲜刷新 |
| **Phase 3（可选）** | chrome_sync → commerce/fulfillment/finance 数据桥接 | 如果需要把 Chrome 扩展数据纳入主分析链路 |

## 10. 逻辑解析规则（dump → 业务表）

### 10.1 总体流程

```
POST /dumps 请求到达
    │
    ├─ domain=orders    → parse_order_response()    → upsert chrome_sync.orders + order_lines
    ├─ domain=logistics → parse_logistics_response() → upsert chrome_sync.shipments + tracking_events
    └─ domain=statements→ parse_statement_response() → upsert chrome_sync.settlements + settlement_details
    │
    └─ write_raw_log()（无论成功失败都写）
```

解析在 dump handler 内 **同步完成**，数据立即可查。

### 10.2 订单解析

TikTok `order/list` 响应结构（模块化）：

```json
{
  "data": {
    "main_orders": [
      {
        "main_order_id": "57694276327119",
        "order_status_module": {
          "order_status": "DELIVERED",
          "create_time": 1694000000,
          "paid_time": 1694001000,
          "shipped_time": 1694020000,
          "delivered_time": 1694030000,
          "cancelled_time": 0,
          "update_time": 1694030000
        },
        "price_module": {
          "payment": { "amount": "299000", "currency": "VND" },
          "total_amount": { "amount": "329000", "currency": "VND" }
        },
        "sku_module": [
          {
            "product_id": "1729446829584539881",
            "sku_id": "1729446829584539883",
            "product_name": "Wireless Earbuds Pro",
            "sku_name": "Black / Standard",
            "seller_sku": "SKU-001",
            "sku_image": "https://...",
            "quantity": 2,
            "sale_price": { "amount": "149500", "currency": "VND" },
            "sku_order_status": "DELIVERED"
          }
        ],
        "fulfill_line_module": [ ... ],
        "fulfillment_module": { "fulfillment_type": "FBT" },
        "delivery_module": { ... }
      }
    ]
  }
}
```

**注意**：字段名基于 codex 文档推断，首次接入需用域名观察功能确认。

#### `chrome_sync.orders` 字段映射

| 业务表列 | TikTok 来源 | 转换规则 |
| --- | --- | --- |
| `shop_id` | 请求 scope | 直传 |
| `order_id` | `main_order_id` | 直传 |
| `status` | `order_status_module.order_status` | 直传 |
| `currency` | `price_module.payment.currency` | 直传 |
| `payment_amount` | `price_module.payment.amount` | `Decimal(str)` |
| `total_amount` | `price_module.total_amount.amount` | `Decimal(str)` |
| `fulfillment_type` | `fulfillment_module.fulfillment_type` | 直传 |
| `order_time` | `order_status_module.create_time` | 秒级 Unix → `datetime(UTC)` |
| `paid_at` | `order_status_module.paid_time` | 同上；`0` → `NULL` |
| `shipped_at` | `order_status_module.shipped_time` | 同上 |
| `delivered_at` | `order_status_module.delivered_time` | 同上 |
| `cancelled_at` | `order_status_module.cancelled_time` | 同上；`0` → `NULL` |
| `raw_response` | 完整 response body | JSONB 直存（可选） |
| `captured_at` | dump 请求的 `capturedAt` | 直传 |

#### `chrome_sync.order_lines` 字段映射

遍历 `sku_module[]`（优先）或 `fulfill_line_module[]`：

| 业务表列 | TikTok 来源 | 转换规则 |
| --- | --- | --- |
| `shop_id` | 请求 scope | 直传 |
| `order_id` | `main_order_id` | 直传 |
| `sku_id` | `sku_module.sku_id` | 直传（同订单内唯一标识） |
| `product_id` | `sku_module.product_id` | 直传 |
| `sku_id` | `sku_module.sku_id` | 直传 |
| `product_name` | `sku_module.product_name` | 缺失 → `NULL` |
| `variant_name` | `sku_module.sku_name` | 缺失 → `NULL` |
| `image_url` | `sku_module.sku_image` | 缺失 → `NULL` |
| `seller_sku` | `sku_module.seller_sku` | 缺失 → `NULL` |
| `quantity` | `sku_module.quantity` | `Decimal(str)` |
| `unit_price` | `sku_module.sale_price.amount` | `Decimal(str)` |
| `currency` | `sku_module.sale_price.currency` | 直传 |
| `line_status` | `sku_module.sku_order_status` | 直传 |

#### 异常处理

| 场景 | 处理 |
| --- | --- |
| `sku_module` 和 `fulfill_line_module` 都为空 | 跳过 line INSERT，只写 order header；raw_log WARNING |
| `price_module` 缺失 | 金额字段设 `NULL` |
| `order_status_module` 缺失 | 时间字段设 `NULL`，status 设 `NULL` |
| 时间戳为 `0` | → `NULL` |
| 同一 `sku_id` 出现在两个 module | 以 `sku_module` 为准（去重） |
| `order_id` 已存在 | ON CONFLICT DO UPDATE（以最新 captured_at 为准） |

### 10.3 物流解析

TikTok `logistic_detail/list` 响应：

```json
{
  "data": {
    "package_list": [
      {
        "main_order_id": "57694276327119",
        "package_id": "PKG-001",
        "tracking_no": "VN1234567890",
        "logistic_supplier": "VNPost",
        "logistic_detail": {
          "track_list": [
            { "time": "2026-09-08T10:00:00Z", "track_status": "Package picked up" },
            { "time": "2026-09-10T14:00:00Z", "track_status": "Delivered" }
          ]
        }
      }
    ]
  }
}
```

**一个订单可能有多个 package**。

#### `chrome_sync.shipments` 字段映射

| 业务表列 | TikTok 来源 | 转换规则 |
| --- | --- | --- |
| `shop_id` | 请求 scope | 直传 |
| `order_id` | `package.main_order_id` | 直传 |
| `package_id` | `package.package_id` | 直传 |
| `tracking_number` | `package.tracking_no` | 缺失 → `NULL` |
| `carrier_name` | `package.logistic_supplier` | 直传 |
| `status` | `track_list[-1].track_status` | 最新轨迹；空 → `NULL` |
| `shipped_at` | `track_list[0].time` | 首条轨迹；空 → `NULL` |
| `delivered_at` | `track_list[-1].time` | 仅当 status 含 "elivered"；否则 `NULL` |
| `raw_response` | 完整 response body | JSONB 直存 |
| `captured_at` | dump 请求的 `capturedAt` | 直传（保鲜判断依据） |

#### `chrome_sync.tracking_events` 字段映射

遍历 `package.logistic_detail.track_list[]`：

| 业务表列 | TikTok 来源 | 转换规则 |
| --- | --- | --- |
| `shop_id` | 请求 scope | 直传 |
| `package_id` | `package.package_id` | 直传 |
| `event_key` | 合成 | `{package_id}_{index}` |
| `event_at` | `track_list[].time` | ISO 字符串 / 时间戳 → `datetime` |
| `description` | `track_list[].track_status` | 直传 |
| `location` | `track_list[].location`（如有） | 缺失 → `NULL` |

#### 异常处理

| 场景 | 处理 |
| --- | --- |
| `package_list` 为空 | 无操作（订单可能还没发货） |
| 同一 `package_id` 已存在 | ON CONFLICT DO UPDATE（轨迹可能更新） |
| `track_list` 为空 | 只写 shipment header，不写 tracking_events |
| `time` 格式不确定 | 先尝试 int → fromtimestamp；失败 → fromisoformat |

### 10.4 结算解析

涉及两个端点：`statement/list/detail`（结算单头）+ `statement/transaction/detail`（SKU 明细）。

#### `statement/list/detail` → `chrome_sync.settlements`

| 业务表列 | TikTok 来源 | 转换规则 |
| --- | --- | --- |
| `shop_id` | 请求 scope | 直传 |
| `statement_id` | `statement_records[].statement_id` | 直传 |
| `statement_version` | `statement_records[].statement_version` | 直传 |
| `bill_period` | `statement_records[].bill_period` | 直传（原始文本） |
| `period_start` | `bill_period` 解析 | `"2026-09-01~2026-09-07"` → `date(2026,9,1)` |
| `period_end` | 同上 | → `date(2026,9,7)` |
| `settlement_time` | `settlement_time` | ISO → `datetime` |
| `payment_id` | `payment_id` | 缺失 → `NULL` |
| `payment_status` | `payment_status` | int → TEXT 映射（1→PENDING, 2→PAID, 3→FAILED） |
| `settle_amount` | `settle_amount.amount` | `Decimal(str)` |
| `earning_amount` | `earning_amount.amount` | `Decimal(str)` |
| `fee_amount` | `fee_amount.amount` | `Decimal(str)` |
| `payable_amount` | `payable_amount.amount` | `Decimal(str)` |
| `currency` | `settle_amount.currency` | 直传 |

#### `statement/transaction/detail` → `chrome_sync.settlement_details`

| 业务表列 | TikTok 来源 | 转换规则 |
| --- | --- | --- |
| `shop_id` | 请求 scope | 直传 |
| `statement_id` | `sku_record.statement_id` | 直传 |
| `statement_version` | `sku_record.statement_version` | 直传 |
| `sku_detail_id` | `sku_record.statement_sku_detail_id` | 直传（唯一键） |
| `trade_order_id` | `sku_record.trade_order_id` | 直传；⚠️ 与 main_order_id 映射未验证 |
| `sku_id` | `sku_record.sku_id` | 直传 |
| `product_name` | `sku_record.product_name` | 直传 |
| `quantity` | `sku_record.quantity` | 直传（int） |
| `settlement_amount` | `sku_record.settlement_amount.amount` | `Decimal(str)` |
| `earning_amount` | `sku_record.earning_amount.amount` | `Decimal(str)` |
| `fees_amount` | `sku_record.fees.amount` | `Decimal(str)` |
| `currency` | `sku_record.settlement_amount.currency` | 直传 |
| `fee_components` | `in_come.fee_list` + `out_come.fee_list` | 递归展开后存 JSONB |

#### 费用树递归展开 → `fee_components` JSONB

```python
def flatten_fees(fee_list: list) -> list[dict]:
    """递归展开 fee_list 为扁平 [{code, amount, currency}]。"""
    result = []
    for fee in fee_list:
        code = fee.get('type', 'UNKNOWN')
        amount = fee.get('amount', {}).get('amount', '0')
        currency = fee.get('amount', {}).get('currency', '')
        result.append({"code": code, "amount": amount, "currency": currency})
        for sub in flatten_fees(fee.get('sub_fees', [])):
            result.append(sub)
    return result
```

存储示例：

```json
[
  {"code": "GROSS_SALES", "amount": "250000", "currency": "VND"},
  {"code": "REFUND", "amount": "0", "currency": "VND"},
  {"code": "PLATFORM_COMMISSION", "amount": "25000", "currency": "VND"},
  {"code": "COMMISSION_TAX", "amount": "2500", "currency": "VND"},
  {"code": "SHIPPING_FEE", "amount": "25000", "currency": "VND"}
]
```

#### 异常处理

| 场景 | 处理 |
| --- | --- |
| `bill_period` 格式不标准 | 正则提取；失败 → `period_start`/`period_end` 设 `NULL` |
| `amount` 字段缺失 | `Decimal('0')`（显式零） |
| `fee_list` 中 `amount` 缺失 | 该 fee 跳过 |
| 同一 `sku_detail_id` 已存在 | ON CONFLICT DO UPDATE |

### 10.5 已知缺口

| 缺口 | 影响 | 后续 |
| --- | --- | --- |
| TikTok 订单模块内部字段名未确认 | 解析规则可能字段名不对 | 用域名观察功能抓一份完整响应确认 |
| `trade_order_id` ↔ `main_order_id` 映射 | 结算明细无法关联到订单 | 补抓映射接口或用 `sku_id` 间接关联 |
| `statement_sku_detail_id` 获取路径 | 无法从 statement list 构造 transaction detail 请求 | 补抓中间接口 |
| `global_product_id` 缺失 | 无法调用商品同款接口 | 从其他商品接口补齐 |

## 11. 风险与缓解

| 风险 | 缓解 |
| --- | --- |
| inline 解析增加 dump 接口延迟 | 订单解析 ~10 行 INSERT，物流 ~5 行，结算 ~20 行；单次 <50ms |
| `raw_log` 膨胀 | 单条含 response body，物流 ~5KB/条，订单 ~20KB/条；每天 1000 单 × 3 域 ≈ 75MB；90 天 retention 自动清理；后续可按需调短或迁冷存储 |
| has-data 批量查询 500 id 性能 | unique 索引 + IN 查询，~1ms |
| 解析逻辑 bug 导致数据损坏 | `raw_response` JSONB 保留完整原始数据，可重跑解析修复 |
| TikTok 字段名变更 | raw_response 保留原始数据 + raw_log 记录解析结果，可快速定位 |
| 保鲜窗口内物流状态未更新 | 24h 窗口是折中；Phase 3 可按订单状态动态调整 |

---

**关于 `shop_id` vs `shop_pk`**：

- `shop_pk` = `commerce.shops` 表的内部自增主键（`id` 列），后端内部使用
- `shop_id` = TikTok 外部店铺 ID（如 `7493838482981827388`），Chrome 插件只知道这个

本方案所有 `chrome_sync.*` 表和 API 端点统一用外部 `shop_id` 作为关联键。
chrome_sync 与 commerce/fulfillment/finance 完全隔离，不建跨 schema FK。
