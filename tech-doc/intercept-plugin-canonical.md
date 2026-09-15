# Chrome 扩展（ads-data-sync / dumps）数据取数口径

> 状态：2026-09-13 拍板，docs-only lane
> 范围：tts-erp 端 **接收的** dumps 数据的取数口径
> 不含：chrome 端**抓取**规则（chrome-plugins 仓负责，不在本仓）

## 0. TL;DR

| 问题 | 答案 |
| --- | --- |
| ads-data-sync 抓什么 | 4 张表 8 个 endpoint 群（ad 域 + 订单/物流/结算/售后）|
| dumps 怎么解析 | 1 事务 1 张表，dump body 写 `plugin.raw_log` **（Phase 1 deprecated，见 §5）**，业务表 FK 引用 |
| 4 域数据怎么关联 | `shop_id` 跨域唯一键 + 4 类 ID 映射（main_order_id / fulfill_unit_id / statement_id / reverse_order_id）|

---

## 1. ads-data-sync 采集的 endpoint 全集

### 1.1 4 张表 dump 落库（plugin schema）

| 表 | 用途 | 来源 endpoint | 频率 |
| --- | --- | --- | --- |
| `plugin.orders` | 订单头 | `POST /api/fulfillment/order/list` | daily / on-demand |
| `plugin.order_lines` | SKU 行 | `POST /api/fulfillment/order/list` (sku_module[]) | 随 order |
| `plugin.shipments` | 包裹 | `POST /logistic_detail/list` (package_list[]) | 非终态循环 |
| `plugin.tracking_events` | 轨迹事件 | `POST /logistic_detail/list` (track_list[]) | 随 shipment |
| `plugin.settlements` | 结算单头 | `POST /api/v1/pay/statement/order/list?settlement_status=1` | 终态后 |
| `plugin.settlement_details` | 结算明细 | `POST /api/v1/pay/statement/transaction/detail` | 随 settlement |
| `plugin.after_sales` | 售后/取消单头 | `POST /return_refund/202309/cancellations/search` | 每天 + on-demand |
| `plugin.after_sale_items` | 售后/取消行项目（SKU 级，支持部分取消）| 同上 (cancel_line_items[]) | 随 after_sales |
| `plugin.raw_log` | 原始 dump 流水 | （所有 endpoint 的 raw body 都在这） | 每次 dump 1 行 |

### 1.2 广告消耗 5 张表（2026-09-11 由 analytics schema 并入 plugin）

| 表 | 用途 | 字段命名 | 协议 |
| --- | --- | --- | --- |
| `plugin.ad_today` | 今天实时（30s 滚动）| TikTok API 原名（`mixed_real_cost` / `onsite_roi2_shopping_sku` 等）| v4 dump |
| `plugin.ad_daily` | 历史天级可校准 | 同上 | v4 dump |
| `plugin.ad_monthly` | 月级聚合 | 同上 | v4 dump |
| `plugin.ad_raw_log` | 原始 dump（ad 域）| 完整 request+response | dump 协议 |
| `plugin.plugin_logs` | 扩展运行日志 | — | 内部 |

**ads-data-sync 抓的 3 个 ad 端点**（见 `tech-doc/tiktok-seller-center-api-catalog.md` §1.7）：

```
POST /oec_ads/shopping/v1/oec_ads/stat/post_product_list   ← 商品分析
POST /oec_ads/shopping/v1/oec_ads/stat/post_session_list   ← 会话分析
POST /oec_ads/shopping/v1/oec_ads/stat/campaign_opt_log_list ← 变更日志
```

**关键设计决策**（D1-D5，详见 `tech-doc/analytics/dump-architecture.md` §2）：
- D1：`ad_raw` 是 source-of-truth（不可变原始 dump，jsonb 直存）
- D2：`/dumps` 协议替换 `/batches`（**单 dump object** body，不是 array）
- D3：`/cursor` 只剩 has-data 模式（plugin 用它做防风控预检）
- D4：schema 5 → 3 张表（去掉 page bitmap 和 work-list 状态机）
- D5：schema 3 → 1 张表（2026-09-05 reorg，ad_raw 是唯一保留）

### 1.3 订单/物流/结算/取消 抓的 endpoint

| 域 | endpoint | 用途 | 何时抓 |
| --- | --- | --- | --- |
| 订单 | `POST /api/fulfillment/order/list` | 订单头+SKU+价格+状态 | 每天 + 终态判断 |
| 物流 | `POST /logistic_detail/list` | 包裹 + 轨迹事件 | 非终态循环（按 action_code 50101/80101/110101 判定）|
| 结算 | `POST /api/v1/pay/statement/order/list?settlement_status=1` | 已结算订单 | 终态后 |
| 结算 | `POST /api/v1/pay/statement/transaction/detail` | 单条费用拆分 | 随 settlement |
| 取消 | `/return_refund/202309/cancellations/search` | 取消/退款 | 每天 |

**取消/售后 endpoint 已有结构化表**（`plugin.after_sales` + `plugin.after_sale_items`，2026-09-13 lane feat/after-sales-table 建，migration 0030）。chrome 扩展未抓到过 0 hit 数据，schema 基于 `order-domain-business-rules.md §3` 描述设计（BUYER_CANCEL / CANCEL 两种 cancel_type、CANCELLATION_REQUEST_COMPLETE 状态、行项目级 cancel_line_items）。parser: `tts_erp_v2/plugin/orders/parser.py::parse_after_sales_response`。

---

## 2. dumps 数据如何解析

### 2.1 协议总览（v4 协议，2026-09-10 上线）

```
chrome 扩展
  ↓ 抓 TikTok API HTTP 响应
  ↓ 解析出 5 元组 key: (seller_id, advertiser_id, endpoint, day, campaign_id)
  ↓ 组装 dump object: { endpoint, method, request, response, capturedAt }
  ↓ POST /v2/analytics/sync/dumps
  ↓ server 1 事务写 1 张表（ad_raw）
```

**v3 → v4 关键变化**（详见 `tech-doc/analytics/dump-architecture.md` §3）：

| 维度 | v3（已废）| v4（现行）|
| --- | --- | --- |
| 请求体 | `array` of dump objects | `object`（单 dump）|
| 写入表 | 3 张（ad_raw + ad_records + ad_daily_completeness）| 1 张（ad_raw）|
| /cursor 协议 | work-list（items / nextRequiredDay）| has-data 单查询 |
| plugin 状态 | lease + epoch + page bitmap | "dumb dump"（无状态）|

### 2.2 dumps 字段结构（request body）

```jsonc
// POST /v2/analytics/sync/dumps  body 示例
{
  "protocolVersion": 4,
  "requestId": "client-uuid",                     // 幂等键
  "scope": {
    "sellerId":     "7494763368967603447",
    "advertiserId": "7661087232599212040"
  },
  "dump": {                                        // 单 dump object（不是 array）
    "endpoint":   "post_product_list",
    "method":     "POST",
    "request":    { /* 完整 HTTP request body */ },
    "response":   { /* 完整 HTTP response body */ },
    "capturedAt": 1789293724140                    // unix ms
  }
}
```

**v4 协议详见** `.agents/ao/handoff/2026-09-13-intercept-standard-protocol.md`

### 2.3 解析流程（server 端 `/v2/analytics/sync/dumps`）

```
接收 dump
  ↓
1. 解析 scope (seller_id, advertiser_id)
2. 提取 endpoint + day + campaign_id (从 dump.request/response)
3. INSERT INTO plugin.ad_raw
   ON CONFLICT (seller_id, advertiser_id, endpoint, day, campaign_id)
   DO UPDATE SET response = EXCLUDED.response, captured_at = EXCLUDED.captured_at
   RETURNING xmax = 0 -- 判 inserted/duplicate
4. COMMIT
```

**5 元组唯一键**：`(seller_id, advertiser_id, endpoint, day, campaign_id)` —— 不可变原始 dump 天然幂等

### 2.4 订单/物流/结算 dumps 解析（v3 协议，沿用 plugin 端有状态机）

`plugin.raw_log` 落库结构（见 `tts_erp_v2/db/models/plugin.py:23-60`）：

```jsonc
// plugin.raw_log 每行
{
  "id":              12345,
  "domain":          "tiktok",
  "shop_id":         "7494763368967603447",
  "endpoint":        "/api/fulfillment/order/list",
  "captured_at":     "2026-09-13T11:31:23Z",       // 卖家浏览器时间
  "request_params":  { ... },                       // GET query string 或 POST body
  "request_body":    { ... },
  "response_body":   { "code": 0, "data": { "main_orders": [...] } },
  "parse_error":     null,                          // 解析失败时填错误信息
  "rows_written":    50,                            // 该 dump 解析后写入业务表的行数
  "source":          "chrome-ext",                  // dump 来源（区分 chrome-ext / other）
  "created_at":      "2026-09-13T11:31:24Z"        // server 接收时间
}
```

**业务表解析示例**（`POST /api/fulfillment/order/list`）：

```sql
-- 1. 写 raw_log，log_id 备用
INSERT INTO plugin.raw_log (domain, shop_id, endpoint, captured_at, request_body, response_body, source)
VALUES (..., 'order/list', '2026-09-13T11:31:23Z', ?, ?, 'chrome-ext')
RETURNING id;  -- 假设 12345

-- 2. 解析 response_body.data.main_orders[] → 写 orders
INSERT INTO plugin.orders (log_id, shop_id, order_id, main_order_status, currency, payment_amount, total_amount, pay_method, sale_region, order_time, ...)
SELECT 12345, '749476...', main_order_id, main_order_status, currency, payment_total, grand_total, pay_method, sale_region, create_time, ...
FROM jsonb_array_elements(?::jsonb->'data'->'main_orders');

-- 3. 解析 sku_module[] → 写 order_lines
INSERT INTO plugin.order_lines (log_id, shop_id, order_id, sku_id, product_id, quantity, unit_price, total_price, currency, ...)
SELECT 12345, o.shop_id, o.order_id, s.sku_id, s.product_id, s.quantity, s.sku_unit_price, s.sku_total_price, s.currency, ...
FROM plugin.orders o, jsonb_array_elements(o.raw_log_id_response.sku_module) s;
```

**核心规则**：所有业务表 `log_id` FK 引用 `plugin.raw_log.id`，**保证每个写入都可溯源到具体 dump 请求**。

### 2.5 解析失败处理

```
response_body 不符合预期 schema
  ↓
1. raw_log 仍写入（保留原始 dump）
2. parse_error 字段填错误信息
3. rows_written = 0
4. 不写业务表
5. 后续人工排查 / retry
```

→ **raw_log 是 source-of-truth，业务表是 derived view**。dump 永不丢，失败可重放。

> **2026-09-14 教训（字段级静默失败）**：`_ts_to_datetime` 的 str 分支只认 ISO 格式，
> 而 create_time/update_time 实测是**数字字符串**（秒/毫秒/微秒都有），导致
> plugin.orders 四个时间字段几乎全 NULL 且无任何信号——parse_error 只覆盖整 dump 级
> 失败，字段级失败不可观测。修复（commit 122cad1）：数字字符串解析 + 不可解析值
> log.warning。原则：**字段级解析失败不落 parse_error（避免整批重试），但必须
> log.warning 显式可见**；`_to_decimal` 同策略（带 field 标签）。
> 另：logistics 域 353 条 dump 全部 `response.body=null`（插件端 logistic_detail
> 抓取 100% 返回非 JSON/空 body，仍上传空 dump 并被按 RETRYABLE 无限重试）——
> 修复在插件仓：payload==null 不上传 + 带 responseReadError 诊断。

---

## 3. 订单 / 物流 / 结算 / 售后 4 域数据关联

### 3.1 4 域 ID 映射（核心连接键）

```
shop_id                       ← 跨域主键（=oec_seller_id=TikTok shop_id）
  ↓
main_order_id (订单)         ← 业务订单号
  ├─ order_line_id → sku_id → product_id → global_product_id
  ├─ fulfill_unit_id (包裹)    ← 物流粒度
  │     ├─ package_id
  │     ├─ tracking_no
  │     ├─ warehouse_id / name / region
  │     ├─ shipment_provider_id → shipment_provider_info.name
  │     ├─ logistics_service_id → logistics_service_name
  │     └─ delivery_time
  └─ payment_id (支付)        ← 结算粒度
        ├─ statement_id (已结)         ← 结算单
        │     └─ statement_detail_id (SKU 级)
        └─ statement_detail_id (未结时= 0 条)
reverse_order_id (售后/退款)  ← reverse_module[0]
  └─ reverse_status / reverse_type / refund_time
```

**别名映射**（已多 sample 验证）：
- `reference_id`（settlement 端单条 query param）= `trade_order_id`（settlement 字段）= `main_order_id`（订单）
- `oec_seller_id`（URL query）= `seller_id`（body）= `shop_id`（DB 列）

### 3.2 4 域数据流（卖家操作时间线）

```
T0  买家下单
   └─ POST /api/fulfillment/order/list
      → plugin.orders + plugin.order_lines
      → 19 modules 内联（price / delivery / fulfillment / reverse 等）

T1  卖家发货
   └─ POST /logistic_detail/list (per-order 查询)
      → plugin.shipments + plugin.tracking_events
      → 持续更新直到 action_code ∈ {50101, 80101, 110101}（物流终态）

T2  买家签收
   └─ tracking_events 最后一条 = action_code 50101
   └─ order 状态 → DELIVERED / COMPLETED（订单终态）

T3  结算周期触发（7~30 天后）
   └─ POST /api/v1/pay/statement/order/list?settlement_status=1
      → plugin.settlements（每行一个结算单）

T4  卖家看结算明细
   └─ POST /api/v1/pay/statement/transaction/detail
      → plugin.settlement_details（每行一个 SKU 级费用）

退款路径（独立，不影响订单状态）：
T'  买家发起退款
   └─ /return_refund/202309/cancellations/search  (raw_log only, 暂未建 plugin.cancellations)
   └─ 同时：order.list 同 main_order_id 出现 reverse_module[]
```

### 3.3 4 域表关系（plugin schema ER）

```
plugin.raw_log
  │ (log_id FK)
  ├── plugin.orders
  │      │ (shop_id, order_id)
  │      ├── plugin.order_lines  (1:N, shop_id+order_id+sku_id)
  │      ├── plugin.shipments   (1:N, shop_id+order_id → package_id)
  │      │      └── plugin.tracking_events  (1:N, shop_id+package_id+event_key)
  │      ├── plugin.settlements (1:1, shop_id+statement_id+statement_version)
  │      │      └── plugin.settlement_details  (1:N, shop_id+sku_detail_id)
  │      └── [TODO] plugin.cancellations  (1:N, shop_id+order_id → reverse_order_id)
  │
  └── (未来扩展表...)
```

**注意**：plugin 表之间**没有硬 FK**（逻辑链接靠 shared 5 元组 key）—— 与 ad_raw 同模式（D1 决策）。理由：避免 plugin 重写时连锁 cascade 删数据。

### 3.4 状态终态判断（关键决策）

| 域 | 终态判断字段 | 终态值 | 决策日 |
| --- | --- | --- | --- |
| 订单 | `main_order_status` | COMPLETED / CANCELLED | 2026-09-12（order-domain-business-rules.md §1）|
| 物流 | `tracking_events[].action_code` | 50101 / 80101 / 110101 | 2026-09-12（同上 §5）|
| 结算 | `settlements[].settlement_time != null` | 已结算 | 2026-09-13（catalog §7.6.2）|
| 售后 | `reverse_module[].refund_time != null` | 已退款 | 2026-09-13（同上 §7.4.4）|

**关键原则**：物流终态**独立于**订单终态判断。即使订单已 COMPLETED，只要物流还没拿到 50101 签收事件，插件继续拉直到拿到为止（避免漏抓延迟回传的签收事件）。

### 3.5+ main_order_status 码值实测交叉验证（2026-09-14）

prod 店铺 7494864868604150914，494 单逐码取样 raw_log 原始响应（`order_status_module` + 伴随字段）：

| main_order_status | 单数 | 伴随证据 | 推断文本态 |
| --- | ---: | --- | --- |
| 100 | 1 | 无 tracking_no、无 reverse_module | UNPAID（待付款） |
| 101 | 71 | 有 tracking_no/receipt_id（面单已建）、无 reverse | AWAITING_SHIPMENT（待发货） |
| 102 | 329 | 有 tracking_no、18 单带 reverse | 已发货/运输中（IN_TRANSIT 一类） |
| 103 | 7 | 7/7 带 reverse_module（reverse_type=3 退货） | 售后/退货中 |
| 104 | 86 | 86/86 带 reverse_module（reverse_type=4 买家取消） | CANCELLED |

- raw 响应**只有 int 码**（main_order_status / sku_display_status / main_sub_order_status），无文本枚举
- 104/103 高置信（reverse_module 全量伴随）；101/102 需 Seller Center 页面 tab 对照终验
- **reverse_module 内嵌在 order/list 响应里**（reverse_order_id / reverse_type / reverse_reason / refund_time 齐全），取消/售后可从订单 dump 挖掘；reverse_type 枚举（3=退货 / 4=取消）为样本推断待核实

### 3.5 跨表 JOIN 模板（4 域合一查询）

```sql
-- 找一单的完整链路：订单 + 物流 + 结算 + 退款
SELECT
  o.order_id, o.main_order_status, o.payment_amount,
  s.package_id, s.tracking_number, s.carrier_name,
  st.settlement_id, st.settle_amount, st.earning_amount,
  -- reverse_module 在 plugin.intercepted_requests,不在 plugin.orders
  -- 需要 JOIN 到 intercepted_requests
  ir.response_body->'data'->'main_orders'->0->'reverse_module' AS reverse_module
FROM plugin.orders o
LEFT JOIN plugin.shipments s
  ON s.shop_id = o.shop_id AND s.order_id = o.order_id
LEFT JOIN plugin.settlements st
  ON st.settlement_id IN (
    SELECT jsonb_array_elements(
      ir.response_body->'data'->'order_records'->0->'payment_id'
    )
  )
-- reverse_module 通过 raw_log 链路
LEFT JOIN plugin.raw_log rl
  ON rl.id = o.log_id
LEFT JOIN plugin.intercepted_requests ir
  ON ir.seller_id = o.shop_id
  AND ir.endpoint_path = '/api/fulfillment/order/list'
  AND ir.response_body->'data'->'main_orders'->0->'main_order_id' = o.order_id
WHERE o.shop_id = ?
  AND o.order_id = ?;
```

> 上面 SQL 是**伪码**示意，实际 JOIN 路径以具体查询需求为准。

---

## 4. 待补（out of scope / TODO）

| # | 内容 | 状态 | 谁负责 |
|---|---|---|---|
| 1 | `plugin.cancellations` 结构化表 | ✅ 已由 0030 建为 `plugin.after_sales` + `after_sale_items`（2026-09-14 merged 7d98a28） | feat/after-sales-table |
| 2 | 取消 endpoint 解析器（/return_refund/.../cancellations/search）| ❌ TODO | 同上 |
| 3 | `main_order_status` 状态码字典（101/102/... 全集）| 🔄 部分（§3.5+ 实测 100-104 交叉验证，101/102 待 tab 终验） | feature/plugin-shop-analytics |
| 4 | 物流 tracking_events 字段完整结构 | ❌ TODO | 未抓到完整 burst |
| 5 | settlement `reasons_detail[].reason` 码表 | ❌ TODO | catalog §7.4.5 TODO |
| 6 | multi-package 订单 | ❌ TODO | catalog §7.4.6.3 TODO |
| 7 | chrome 端抓取规则 + 权限 | ❌ 跨仓 | chrome-plugins |
| 8 | dumps 上传失败重试策略 | ❌ 跨仓 | chrome-plugins |

## 5. plugin.raw_log 下线计划（chore/deprecate-plugin-raw-log，2026-09-15 起）

**状态**：Phase 1 已发布（停写 + 1 天观察期）；Phase 3 待 Phase 2 观察结果触发。

### 历史背景

- `plugin.raw_log` 是 chrome-ext dumps 流水表，v3 协议（约 2024 末）创建
- 设计目的：保留 dump 原始 body 用于事后重放（重解析 + 历史回填）
- prod 实测（2026-09-15）：38,037 行 / 24h 32,777 行 / ~2,257 行/h
- **6 张业务表（shipments / tracking_events / settlements / settlement_details / after_sales / after_sale_items）FK 引用但完全空** → raw_log 实际只服务 `orders` + `order_lines` 两张
- 唯一存活的 history-backfill 用例：`oneoff_backfill_plugin_order_times.py`（9d8ad11，2026-09-14 已跑）

### Phase 1（2026-09-15 上线，本 lane 提交）

- alembic 0032_make_plugin_log_id_nullable：8 张业务表 `log_id` 列 `DROP NOT NULL`
- `tts_erp_v2/plugin/orders/repository.py::write_raw_log` 改 no-op（log.warning + return 0）
- `tts_erp_v2/api/v2/order_sync.py::post_dumps` 不再调 `write_raw_log`，parse → 业务表 INSERT 不带 `log_id`
- 所有 `upsert_*` / `parse_*` 函数移除 `log_id` 参数
- 测试更新：`_make_log_id` / `_write_raw_log` helper 删除，`logId` 字段恒 0
- chrome-plugins 端无感知（API 仍返 `logId` 字段，值=0）

### Phase 2（1 天观察期，2026-09-15 → 2026-09-16）

- 监控 prod：业务表 `orders` / `order_lines` 是否还在 upsert（chrome-plugins 不停 = 业务表应当继续增长）
- 监控 prod：`plugin.raw_log` 不再有新行（SELECT MAX(created_at) 应当停在 Phase 1 上线时刻附近）
- 监控 prod：chrome-plugins 端 stderr / plugin_logs 是否报错（plugins 端不应该感知 API 变化）

### Phase 3（条件触发，2026-09-16+ 用户拍板）

- alembic 0033_drop_plugin_raw_log：DROP CONSTRAINT × 6 + DROP COLUMN log_id × 8 + DROP TABLE plugin.raw_log + DROP SEQUENCE raw_log_id_seq
- `tts_erp_v2/plugin/orders/repository.py::write_raw_log` 删除
- `tts_erp_v2/db/models/plugin.py::RawLog` 删除
- `tts_erp_v2/api/v2/admin.py::_PLUGIN_ORDER_RAW_LOG` / `list_known_shops` SELECT 删除
- `tech-doc/intercept-plugin-canonical.md` §5 本节改写为历史归档
- `tests/conftest.py:225` cleanup 行删除
- `scripts/oneoff_backfill_plugin_order_times.py` 删除（已无意义）

**前置检查**：如果 Phase 2 观察发现业务表停止 upsert 或 chrome 端报错，应回滚 Phase 1（write_raw_log 恢复 INSERT + 业务表 INSERT 恢复 log_id）。
