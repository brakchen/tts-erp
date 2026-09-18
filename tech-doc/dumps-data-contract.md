# `dumps-data-contract` — Chrome 插件 ↔ tts-erp dumps 端契约

> **范围**：订单/物流/结算/售后 + 广告 5 域同步的端到端契约 —— Chrome 插件（在 Seller Center 页面拦截 TikTok 接口）↔ tts-erp 后端 dumps 端点 ↔ `plugin.*` 业务表。
> **不涵盖**：`/v2/order-sync/has-data` 和 `/v2/order-sync/synced-ids` —— 这两个端点是 progress/diagnostic 用的，**不属于同步契约**（参见 [`chrome-ext-order-sync-design.md §5`](chrome-ext-order-sync-design.md)）。
>
> **设计稿 vs 契约稿**：本文档是**现状契约**（`chrome-plugins/ads-data-sync` + `tts_erp_v2/api/v2/order_sync.py` + `tts_erp_v2/plugin/orders/parser.py` + `tts_erp_v2/analytics/*.py` 实际代码为准）；设计稿见 [`chrome-ext-order-sync-design.md`](chrome-ext-order-sync-design.md)。
>
> **数据查询入口**：DB 行数核对、endpoint 抓取统计见 §5 "已知 gap"（2026-09-15 prod 数据）。
>
> **文档历史**：原 `tech-doc/intercept-plugin-canonical.md`（2026-09-18）的有用内容（ad 域详情、4 域 ID 映射、时间线、ER、状态终态、JOIN 模板、TODO 清单）已合并到 §6-§13；该文件已删除。
>
> **当前改造方案**：详见 [`tech-doc/dumps-tts-erp-refactor-proposal.md`](dumps-tts-erp-refactor-proposal.md)（7 个 lane，已完成 Lane A/C/F/G，待 Lane B/E）。
>
> **设计稿**（部分内容已落后）：[`tech-doc/chrome-ext-order-sync-design.md`](chrome-ext-order-sync-design.md)（§3.2 raw_log 已下线，§3.3 业务表 log_id FK 已删除）。

---

## §1 端到端总览

```
┌─────────────────┐         ┌──────────────────────┐         ┌─────────────────────┐
│ Chrome 插件      │         │ tts-erp 后端           │         │ plugin.* 业务表       │
│ (ads-data-sync)  │  POST   │ POST /v2/order-sync/  │ 解析    │                      │
│                 ├────────►│ dumps                 ├────────►│ orders               │
│ - 拦截 page fetch│         │                      │         │ order_lines          │
│ - 打包 DumpRequest│        │ 1. 调 parser_*        │         │ shipments            │
│ - Zod 校验        │         │ 2. upsert 业务表       │         │ tracking_events      │
│ - 401/429 重试    │         │ 3. 返 4 字段 envelope  │         │ settlements          │
│                 │ ◄───────┤    (code/message/reqId/data)│  settlement_details  │
│                 │ 2xx/4xx │    （HTTP code 是唯一成功/│  after_sales         │
└─────────────────┘         │      失败信号）        │         │ after_sale_items     │
                              └──────────────────────┘         └─────────────────────┘
```

> 严格 HTTP 语义（AGENTS.md §2.5）：未写入数据 / 副作用落库之前**禁止**返 200；`empty_response` / `parse_error` 都走 422 PERMANENT（不再是 200 + `data.status` 隐式信号）。详见 §5.6 + `dumps-tts-erp-refactor-proposal.md` Lane B/E。

**2 参与者**：
- **Chrome 插件**：在 Seller Center tab 上拦截 TikTok page fetch，按 `domain` 拆条 POST
- **tts-erp 后端**：接 dump，调 parser 落 `plugin.*` 业务表（Phase 3 起 `plugin.raw_log` 已 drop，不再写原始 dump body）

**1 端点**：
- `POST /v2/order-sync/dumps` —— 订单/物流/结算/售后 4 域单一契约入口
- **ad 域独立端点**：`POST /v2/analytics/sync/dumps`（v4 协议，详见 §6）

**4 个 Chrome 端采集 endpoint（订单域）**：

| 域 | endpoint path | method | 触发场景 | 状态 |
| --- | --- | --- | --- | --- |
| 订单 | `/api/fulfillment/order/list` | POST | 10 分钟 alarm（订单列表 polling）| ✅ 工作 |
| 物流 | `/api/v1/fulfillment/logistic_detail/list` | GET | 30 分钟 alarm（按 main_order_id 逐单）| ⚠️ 0 行（§5.3）|
| 售后 | `/return_refund/202309/cancellations/search` | POST | 每天 + on-demand | ❌ 未采集（§5.1）|
| 结算 | `/api/v1/pay/statement/list/detail` | GET | 60 分钟 alarm（全局分页）| ⚠️ 0 行（§5.2）|
| 结算 | `/api/v1/pay/statement/transaction/detail` | GET | 每 statement 拉完后逐 SKU | ⚠️ 0 行（§5.2）|

> 路径常量定义在 chrome-plugins 端的 `src/core/tiktok-order-endpoints.ts` 和 `src/core/tiktok-statement-endpoints.ts`（不是 dumps 契约的一部分，但 dumps 端的 `dump.endpoint` 字段值必须匹配这些路径常量）。

---

## §2 dumps 请求 schema

### 2.1 端到端 schema（Python Pydantic ↔ TypeScript Zod 镜像）

| 字段 | 类型 | 必填 | Pydantic 校验（server `tts_erp_v2/api/v2/order_sync.py:125-170`） | Zod 校验（plugin `src/core/order-sync-schemas.ts:46-65`） |
| --- | --- | --- | --- | --- |
| `protocolVersion` | int | 否（默认 1） | `Field(default=PROTOCOL_VERSION)` | `z.number()`（必填，无 default） |
| `requestId` | string\|null | 否 | `Field(default=None, min_length=1, max_length=128)` | `z.string().optional()` |
| `scope.sellerId` | string | 是 | `Field(min_length=1, max_length=128)` | `z.string().min(1)` |
| `scope.shopId` | string | 是 | `Field(min_length=1, max_length=128)` | `z.string().min(1)` |
| `dump.domain` | enum | 是 | `Field(min_length=1, max_length=32)` + `_domain_must_be_valid`（`{"orders", "logistics", "statements"}`） | `z.enum(['orders', 'logistics', 'statements'])` |
| `dump.mainOrderId` | string\|null | logistics 必填 | `Field(default=None, max_length=128)` | `z.string().optional()` |
| `dump.statementId` | string\|null | 结算明细必填 | `Field(default=None, max_length=128)` | `z.string().optional()` |
| `dump.statementVersion` | int\|null | 结算必填 | `Field(default=None)` | `z.number().optional()` |
| `dump.endpoint` | string | 是 | `Field(min_length=1, max_length=512)` | `z.string().min(1)` |
| `dump.method` | string | 是 | `Field(min_length=1, max_length=16)` | `z.string().min(1)` |
| `dump.request.params` | object\|null | 否 | `DumpRequestIn.params`（任意 dict） | `z.record(z.string(), z.unknown()).optional()` |
| `dump.request.body` | object\|null | 否 | `DumpRequestIn.body`（任意） | `z.unknown().optional()` |
| `dump.response.status` | int | 是 | `DumpResponseIn.status` | `z.number()` |
| `dump.response.body` | object\|null | **是（语义必填）** | `DumpResponseIn.body`（`null` 走 `empty_response` 特殊路径） | `z.unknown()` |
| `dump.createdAt` | ISO8601 string | 是 | `_created_at_must_be_utc` —— **必须带 tz**（naive datetime 拒收） | `z.string().min(1)` |

**两端校验差异（一致性陷阱）**：

| 差异 | server | plugin |
| --- | --- | --- |
| `protocolVersion` | **可选**（默认 1） | **必填**（无 default） |
| `createdAt` 时区 | **必须带 tz**（naive datetime 报错） | 仅 min(1)（plugin 端 `new Date().toISOString()` 默认带 Z，所以实际上不会触发） |
| `requestId` | 可选 | 可选 |

### 2.2 完整例子

```json
{
  "protocolVersion": 1,
  "requestId": "550e8400-e29b-41d4-a716-446655440000",
  "scope": {
    "sellerId": "7494763368967603447",
    "shopId": "7494763368967603447"
  },
  "dump": {
    "domain": "orders",
    "mainOrderId": "585971536482567908",
    "endpoint": "https://seller.tiktokshopglobalselling.com/api/fulfillment/order/list?locale=zh-CN&...",
    "method": "POST",
    "request": {
      "params": null,
      "body": {
        "search_condition": { "condition_list": {} },
        "offset": 0,
        "count": 20,
        "sort_info": "6",
        "search_cursor": "",
        "pagination_type": 0
      }
    },
    "response": {
      "status": 200,
      "body": {
        "data": {
          "main_orders": [
            {
              "main_order_id": "585971536482567908",
              "order_status_module": [...],
              "price_module": {...},
              "sku_module": [...],
              ...
            }
          ]
        }
      }
    },
    "createdAt": "2026-09-15T03:52:11.443Z"
  }
}
```

### 2.3 错误码清单

| HTTP | `code` (response body) | 触发条件 | dumps 端处理 |
| --- | --- | --- | --- |
| 200 | `code: 0` | dump 接受并解析（即使 rowsWritten=0） | 正常返回 `{data: {status, logId, rowsWritten}}` |
| 200 | `code: 0` + `data.status: "parse_error"` | 解析失败（`dump.response.body` 有内容但 parser 抛异常） | 仍返 200，让 plugin 知道是数据问题（**不是协议问题**） |
| 200 | `code: 0` + `data.status: "empty_response"` | `dump.response.body is None`（插件抓取失败/超时） | 写 raw_log + 空响应占位，**不解析** |
| 400 | `code: "MALFORMED_JSON"` | body 不是合法 JSON | — |
| 400 | `code: "SCHEMA_INVALID"` | Pydantic 校验失败（domain 不在 3 选 1 / createdAt 无 tz 等） | — |
| 413 | `code: "PAYLOAD_TOO_LARGE"` | body > 2 MB | — |
| 401 | `code: "UNAUTHORIZED"` | 缺/错 `syncToken`（参见 §1 鉴权） | — |

### 2.4 dumps 成功响应 envelope

```json
{
  "code": 0,
  "requestId": "550e8400-e29b-41d4-a716-446655440000",
  "data": {
    "status": "inserted" | "updated" | "stale_ignored" | "duplicate" | "parse_error" | "empty_response",
    "parseError": "<exception message，parse_error 时存在>",
    "logId": 12345,
    "rowsWritten": 1
  }
}
```

> ⚠️ `data` 各字段**全部可选** —— 硬契约只到 `code === 0`；缺字段不报错（参见 `order-sync-schemas.ts:75` 注释：`硬契约只有 envelope 的 code === 0；data 字段防御式读取并给默认值`）。

---

## §3 4 域 × dumps 路由总表

> dumps 路由 = `dump.domain` 字段 → 调哪个 parser 函数 → 落哪些 plugin 表

| 域 | chrome 采集 endpoint（plugin `tiktok-*-endpoints.ts`） | dumps `domain` | parser 函数（`tts_erp_v2/plugin/orders/parser.py`） | 落 plugin 表 |
| --- | --- | --- | --- | --- |
| **订单** | `/api/fulfillment/order/list`（POST） | `"orders"` | `parse_order_response` | `orders` / `order_lines` |
| **物流** | `/api/v1/fulfillment/logistic_detail/list`（GET） | `"logistics"` | `parse_logistics_response` | `shipments` / `tracking_events` |
| **售后** | **（chrome 端未采集）** | **`"after_sales"` 已加入 `VALID_DOMAINS`（Lane A）** | `parse_after_sales_response`（孤儿函数，dumps 路由表已接） | `after_sales` / `after_sale_items` 期待 chrome 端首次真实 dump |
| **结算** | `/api/v1/pay/statement/list/detail`（GET） | `"statements"` | `parse_statement_list_response` 或 `parse_statement_transaction_response`（按 `dump.response.body.data` 是否有 `sku_record` 字段分流） | `settlements` / `settlement_details` |

`dump.domain` 路由判定逻辑（`tts_erp_v2/api/v2/order_sync.py:407-446`）：

```python
if domain == "orders":
    rows = parse_order_response(sess, log_id, shop_id, response_body, captured_at)
elif domain == "logistics":
    if not main_order_id:
        parse_error = "mainOrderId is required for logistics domain"
    else:
        rows = parse_logistics_response(...)
elif domain == "statements":
    data = response_body.get("data") or {}
    if "sku_record" in data:
        rows = parse_statement_transaction_response(...)  # → settlement_details
    else:
        rows = parse_statement_list_response(...)          # → settlements
```

**关键约束**：
- `mainOrderId` 在 `domain=logistics` 时**必填**（缺则 `parse_error`，不写库）
- `statementId` + `statementVersion` 在 `domain=statements` 时**强烈建议填**（`has-data` 用作幂等键），但 dumps 端不强校验
- **`domain=after_sales`** 已接入 `VALID_DOMAINS`（Lane `feat/after-sales-routing` 完成）→ chrome 端开始采集 `/return_refund/202309/cancellations/search` 后即可 dump 走通

---

## §4 字段映射（6 张 plugin 业务表）

> 每张表 4 列：API response 路径 → parser 中间变量 → upsert 函数 → plugin 表列（含类型）
> 类型来源于 `tts_erp_v2/db/models/plugin.py`
> parser 中间变量名 = `tts_erp_v2/plugin/orders/parser.py` 实际代码里的变量名

### §4.1 订单域 → `plugin.orders`

**输入**：1 条 `main_orders[i]`（来自 `data.main_orders[]` 或 response body 直接是单订单对象）

| API response path | parser 中间变量 | upsert 函数 | plugin 列 + 类型 |
| --- | --- | --- | --- |
| `main_order_id` | `order_id` (str) | `upsert_order(...)` | `order_id` TEXT NOT NULL |
| `order_status_module[0].main_order_status` | `main_order_status` (int\|None) | `↳` | `main_order_status` Integer |
| `order_status_module[0].sku_display_status` | `sku_display_status` (int\|None) | `↳` | `sku_display_status` Integer |
| `price_module.grand_total.currency` | `currency` (str\|None) | `↳` | `currency` Text |
| `price_module.grand_total.price_val` | `payment_amount` (Decimal\|None) | `↳` | `payment_amount` Numeric(20,4) |
| `price_module.sub_total.price_val` | (不写入 orders，写入 `total_amount` 见下) | `↳` | — |
| `price_module.sub_total.price_val` | `total_amount` (Decimal\|None) | `↳` | `total_amount` Numeric(20,4) |
| `trade_order_module.fulfillment_type` | `fulfillment_type` (int\|None) | `↳` | `fulfillment_type` Integer |
| `trade_order_module.pay_method` | `pay_method` (str\|None) | `↳` | `pay_method` Text |
| `trade_order_module.sale_region` | `sale_region` (str\|None) | `↳` | `sale_region` Text |
| `trade_order_module.create_time` | `order_time` (datetime\|None) | `↳` | `order_time` TIMESTAMP |
| `trade_order_module.update_time` | `update_time` (datetime\|None) | `↳` | `update_time` TIMESTAMP |
| `trade_order_module.latest_rts_time` | `latest_rts_time` (datetime\|None) | `↳` | `latest_rts_time` TIMESTAMP |
| `trade_order_module.latest_tts_time` | `latest_tts_time` (datetime\|None) | `↳` | `latest_tts_time` TIMESTAMP |
| `buyer_info_module.buyer_nickname` | `buyer_nickname` (str\|None) | `↳` | `buyer_nickname` Text |
| `log_id` (dumps 端注入) | `log_id` (int) | `↳` | `log_id` BIGINT FK |
| `shop_id` (dumps 端注入) | `shop_id` (str) | `↳` | `shop_id` Text |

**业务表自然键**：`UNIQUE (shop_id, order_id)` —— dump 同一订单多次上传是 upsert（覆盖写），不是 insert。

### §4.2 订单域 → `plugin.order_lines`

**输入**：每个 `main_orders[i].sku_module[j]`（数组，按 `order_line_id` 关联 order_status_module）

| API response path | parser 中间变量 | upsert 函数 | plugin 列 + 类型 |
| --- | --- | --- | --- |
| `order_id` (来自父 main_order) | `order_id` (str) | `upsert_order_line(...)` | `order_id` Text |
| `sku_id` | `sku_id` (str) | `↳` | `sku_id` Text |
| `product_id` | `product_id` (str\|None) | `↳` | `product_id` Text |
| `product_name` | `product_name` (str\|None) | `↳` | `product_name` Text |
| `sku_name` | `variant_name` (str\|None) | `↳` | `variant_name` Text |
| `product_image.url_list[0]` | `image_url` (str\|None) | `↳` | `image_url` Text |
| `quantity` | `quantity` (Decimal\|None) | `↳` | `quantity` Numeric(20,4) |
| `sku_unit_price.price_val` | `unit_price` (Decimal\|None) | `↳` | `unit_price` Numeric(20,4) |
| `sku_total_price.price_val` | `total_price` (Decimal\|None) | `↳` | `total_price` Numeric(20,4) |
| `unit_price.price_val` 对应的 `currency` | `currency` (str\|None) | `↳` | `currency` Text |
| `order_status_module[matched_by order_line_id].main_order_status` | `main_order_status` (int\|None) | `↳` | `main_order_status` Integer |
| `order_status_module[matched_by order_line_id].sku_display_status` | `sku_display_status` (int\|None) | `↳` | `sku_display_status` Integer |

**业务表自然键**：`UNIQUE (shop_id, order_id, sku_id)` —— 同一 SKU 多次上传 upsert。

### §4.3 物流域 → `plugin.shipments`

**输入**：1 条 `data.package_list[i]`（来自 `logistic_detail/list` 响应，GET）

| API response path | parser 中间变量 | upsert 函数 | plugin 列 + 类型 |
| --- | --- | --- | --- |
| `package_id` | `package_id` (str) | `upsert_shipment(...)` | `package_id` Text |
| `tracking_no` | `tracking_number` (str\|None) | `↳` | `tracking_number` Text |
| `logistic_supplier` | `carrier_name` (str\|None) | `↳` | `carrier_name` Text |
| `logistic_detail.track_list[-1].track_status`（最新一条） | `status` (str\|None) | `↳` | `status` Text |
| `logistic_detail.track_list[0].time`（按时间排序首条） | `shipped_at` (datetime\|None) | `↳` | `shipped_at` TIMESTAMP |
| `logistic_detail.track_list[-1].time`（按时间排序末条，且 status 含 "elivered"） | `delivered_at` (datetime\|None) | `↳` | `delivered_at` TIMESTAMP |
| `dump.mainOrderId`（dumps 端注入） | `order_id` (str) | `↳` | `order_id` Text |
| `dump.scope.shopId` | `shop_id` (str) | `↳` | `shop_id` Text |
| `log_id` (dumps 端注入) | `log_id` (int) | `↳` | `log_id` BIGINT FK |

**业务表自然键**：`UNIQUE (shop_id, package_id)` —— 同一包裹多次上传 upsert。

### §4.4 物流域 → `plugin.tracking_events`

**输入**：每个 `data.package_list[i].logistic_detail.track_list[j]`

| API response path | parser 中间变量（event_key 合成） | upsert 函数 | plugin 列 + 类型 |
| --- | --- | --- | --- |
| `package_id` | `package_id` (str) | `upsert_tracking_event(...)` | `package_id` Text |
| `event_id` 字段（如有） | `event_id` (任意) | `↳` | — |
| `time` | `event_at` (datetime\|None) | `↳` | `event_at` TIMESTAMP |
| `track_status` | `description` (str\|None) | `↳` | `description` Text |
| `location` | `location` (str\|None) | `↳` | `location` Text |
| `{package_id}:{event_id}` 或 5 元组 fallback（`{package_id}:{time}:{track_status}:{action_code}:{location}`） | `event_key` (str) | `↳` | `event_key` Text |
| `dump.scope.shopId` | `shop_id` (str) | `↳` | `shop_id` Text |
| `log_id` (dumps 端注入) | `log_id` (int) | `↳` | `log_id` BIGINT FK |

**业务表自然键**：`UNIQUE (shop_id, package_id, event_key)` —— 同一事件多次上报 upsert。

> ⚠️ **`action_code` 列不存在于 `plugin.tracking_events`**！原始 `track_list[]` 里有 `action_code` 字段（参考 [`tech-doc/enums/action-code.md`](enums/action-code.md) 23 个事件码字典），但 parser 没写库。这是 §5 (c) 已知 gap 的一部分。

### §4.5 结算域 → `plugin.settlements`

**输入**：每条 `data.statement_records[i]`（来自 `statement/list/detail`，GET 全局分页）

| API response path | parser 中间变量 | upsert 函数 | plugin 列 + 类型 |
| --- | --- | --- | --- |
| `statement_id` | `statement_id` (str) | `upsert_settlement(...)` | `statement_id` Text |
| `statement_version` | `statement_version` (int) | `↳` | `statement_version` Integer |
| `bill_period`（毫秒字符串） | `bill_period` (str\|None) | `↳` | `bill_period` Text |
| 派生（`_parse_bill_period` 拆） | `period_start` (date\|None) | `↳` | `period_start` Date |
| 派生 | `period_end` (date\|None) | `↳` | `period_end` Date |
| `settlement_time` (毫秒字符串) | `settlement_time` (datetime\|None) | `↳` | `settlement_time` TIMESTAMP |
| `settlement_id` | `settlement_id` (str\|None) | `↳` | `settlement_id` Text |
| `payment_id` | `payment_id` (str\|None) | `↳` | `payment_id` Text |
| `payment_status`（int 码；`_payment_status_to_text` 转换） | `payment_status` (str\|None) | `↳` | `payment_status` Text |
| `statement_type`（int） | `statement_type` (int\|None) | `↳` | `statement_type` Integer |
| `payment_pending_reason`（int） | `payment_pending_reason` (int\|None) | `↳` | `payment_pending_reason` Integer |
| `settle_amount.amount` | `settle_amount` (Decimal\|None) | `↳` | `settle_amount` Numeric(20,4) |
| `earning_amount.amount` | `earning_amount` (Decimal\|None) | `↳` | `earning_amount` Numeric(20,4) |
| `fee_amount.amount` | `fee_amount` (Decimal\|None) | `↳` | `fee_amount` Numeric(20,4) |
| `adjust_amount.amount` | `adjust_amount` (Decimal\|None) | `↳` | `adjust_amount` Numeric(20,4) |
| `payable_amount.amount` | `payable_amount` (Decimal\|None) | `↳` | `payable_amount` Numeric(20,4) |
| `shipping_amount.amount` | `shipping_amount` (Decimal\|None) | `↳` | `shipping_amount` Numeric(20,4) |
| `total_reserve_amount.amount` | `total_reserve_amount` (Decimal\|None) | `↳` | `total_reserve_amount` Numeric(20,4) |
| `settle_amount.currency` | `currency` (str\|None) | `↳` | `currency` Text |
| `dump.scope.shopId` | `shop_id` (str) | `↳` | `shop_id` Text |
| `log_id` (dumps 端注入) | `log_id` (int) | `↳` | `log_id` BIGINT FK |

**业务表自然键**：`UNIQUE (shop_id, statement_id, statement_version)` —— 同一 statement 不同版本都保留。

### §4.6 结算域 → `plugin.settlement_details`

**输入**：每条 `data.sku_record`（来自 `statement/transaction/detail`，GET 逐 SKU）

| API response path | parser 中间变量 | upsert 函数 | plugin 列 + 类型 |
| --- | --- | --- | --- |
| `statement_sku_detail_id` | `sku_detail_id` (str) | `upsert_settlement_detail(...)` | `sku_detail_id` Text |
| `statement_id`（响应里也有） | `statement_id` (str) | `↳` | `statement_id` Text |
| `statement_version` | `statement_version` (int) | `↳` | `statement_version` Integer |
| `trade_order_id` | `trade_order_id` (str\|None) | `↳` | `trade_order_id` Text |
| `sku_id` | `sku_id` (str\|None) | `↳` | `sku_id` Text |
| `product_name` | `product_name` (str\|None) | `↳` | `product_name` Text |
| `sku_name` | `sku_name` (str\|None) | `↳` | `sku_name` Text |
| `quantity` | `quantity` (Decimal\|None) | `↳` | `quantity` Numeric(20,4) |
| `settlement_status`（int 码，保留 text/int 混合编码） | `settlement_status` (str\|None) | `↳` | `settlement_status` Text |
| `placed_time` | `placed_time` (datetime\|None) | `↳` | `placed_time` TIMESTAMP |
| `settlement_amount.amount` | `settlement_amount` (Decimal\|None) | `↳` | `settlement_amount` Numeric(20,4) |
| `earning_amount.amount` | `earning_amount` (Decimal\|None) | `↳` | `earning_amount` Numeric(20,4) |
| `fees.amount` | `fees_amount` (Decimal\|None) | `↳` | `fees_amount` Numeric(20,4) |
| `currency`（顶层） | `currency` (str\|None) | `↳` | `currency` Text |
| `fee_list[]`（费用树递归展开） | `fee_components` (dict\|None) | `↳` | `fee_components` JSONB |
| `seller_web_cut_flow`（bool） | `seller_web_cut_flow` (bool\|None) | `↳` | `seller_web_cut_flow` Boolean |
| `seller_app_cut_flow`（bool） | `seller_app_cut_flow` (bool\|None) | `↳` | `seller_app_cut_flow` Boolean |
| `dump.scope.shopId` | `shop_id` (str) | `↳` | `shop_id` Text |
| `log_id` (dumps 端注入) | `log_id` (int) | `↳` | `log_id` BIGINT FK |

`fee_components` JSONB 结构：

```json
{
  "fees": [
    {
      "fee_name": "Platform commission",
      "fee_amount": { "amount": "53231", "currency": "VND" },
      "fee_type": "PLATFORM"
    },
    ...
  ],
  "in_come": {...},
  "out_come": {...}
}
```

> 完整 fee_components schema 见 [`tech-doc/enums/settlement-component-code.md`](enums/settlement-component-code.md) 53 个 EAV 字段定义。

**业务表自然键**：`UNIQUE (shop_id, sku_detail_id)` —— 同一 SKU 明细多次上报 upsert。

---

## §5 已知 gap

> **每项**列出现象 + 数据观察 + 假设根因 + 现状。**不下结论**，留 owner 拍板。

### §5.1 (a) 售后域：完全未接入

| 维度 | 现状（2026-09-15 prod） |
| --- | --- |
| **现象** | `plugin.after_sales` 0 行 / `plugin.after_sale_items` 0 行 |
| Chrome 端采集 | **0 hit** —— 整个 `~/chrome-plugins/ads-data-sync/` 无 `cancellations/search` / `returns/search` / `reverse` 引用（`grep` 0 hit） |
| Dumps 端路由 | `VALID_DOMAINS` **已包含** `after_sales`（Lane `feat/after-sales-routing`）—— chrome 端开始采集后即可走通 |
| Parser 函数 | `parse_after_sales_response` **已接入路由表**（不再是孤儿函数）|
| 假设根因 | (a1) chrome 端未开发 `/return_refund/202309/cancellations/search` 拦截；(a2) 无明确 schedule 触发 |
| 修复路径 | chrome 加采集即可（dumps 端已 ready）|

### §5.2 (b) 结算域：chrome 抓了但 dumps 没收到

| 维度 | 现状（2026-09-15 prod） |
| --- | --- | --- |
| **现象** | `plugin.settlements` 0 行 / `plugin.settlement_details` 0 行 / `plugin.raw_log` 含 `statement` endpoint **0 条** |
| Chrome 端采集 | ✅ 抓到了 —— `plugin.intercepted_requests` 含 statement endpoint **148 条**（`/list/detail` × 19、`/transaction/detail` × 13、`/order/list` × 42 等） |
| Chrome 端 dumps 上传 | ⚠️ 代码看似做了 —— `background.ts:702` `pollOrderDomain('statements')` → `uploadOrderSyncDump(domain='statements', ...)`。**但 raw_log 实际 0 条** —— 说明此分支从未真触发 / 上传失败被吞 |
| 假设根因 | (b1) `logistic_detail` 等失败触发 `clearBoundDataSyncTab('statement_authentication_failed')` 提前退出 → 0 条 statement 上传（`background.ts:768-775`） |
|  | (b2) 60min alarm 没真触发过（绑定的 tab 访问 Finance 页 < 60min） |
|  | (b3) chrome `fetchStatementRows` 在 main-frame fetch schema 校验失败返回 `[]`（`background.ts:724-728` 直接 `recordOrderProgress(... 'ok', '...返回 0 行...')`） |
|  | (b4) dumps 端 `parse_statement_list_response` 解析失败但 `_ok_response` 仍返 200，错误被吞在 `raw_log.parse_error` 字段里（**可查证**：补查 `plugin.raw_log WHERE endpoint LIKE '%statement%' AND parse_error IS NOT NULL`） |
| 现状 | **数据流路径上有 4 个可能断点**；**未 root cause**。Owner 拍板前不动 |
| 旁路 | `plugin.intercepted_requests.response_body` 已抓到 148 条 statement 响应 —— **A13 不回填**（用户原话 "B4 不需要回填，我重新抓取就可以了"），仅供 Lane C 诊断证据使用 |

### §5.3 (c) 物流域：接口响应全空

| 维度 | 现状（2026-09-15 prod） |
| --- | --- |
| **现象** | `plugin.shipments` 0 行 / `plugin.tracking_events` 0 行 |
| Chrome 端 | `logistic_detail/list` 100% 返回 `response.body=null`（参见 `tech-doc/plugin-sourced-shop-analytics.md §8`） |
| 假设根因 | (c1) Chrome ext `logistic_detail/list` GET 抓取逻辑 bug（不同于 POST `order/list`） |
|  | (c2) TikTok 卖家中心改了 API（auth/session 限制）|
|  | (c3) Chrome ext 没有触发到详情页（点了订单详情才发出 `logistic_detail/list`） |
| 旁路 | `action_code` 已能通过 `/fulfillment/202309/orders/{order_id}/tracking` 服务端 API 拿到（`integration.raw_records.payload[].action_code`，参考 [`tech-doc/enums/action-code.md`](enums/action-code.md) 24 个事件码字典）—— 但**未写入 plugin.tracking_events** |
| 已知 TODO | `tech-doc/plugin-sourced-shop-analytics.md §4.3`：给 `plugin.tracking_events` 加 `action_code` 列 |

### §5.4 (d) 订单域：`order_time` / `update_time` NULL

| 维度 | 现状（2026-09-15 prod） |
| --- | --- |
| **现象** | `plugin.orders.order_time` 1456 行中 **1455 行 NULL**；`update_time` 1456 行中仅 8 行非空 |
| 数据源 | parser 第 124-126 行：`_ts_to_datetime(tom.get("create_time"))` —— 时间戳在响应里**应该有** |
| 假设根因 | (d1) `trade_order_module.create_time` 字段路径解析路径不对（`tom` 是空 dict？） |
|  | (d2) `_ts_to_datetime` 函数对毫秒/秒时间戳判断有 bug（`util._ts_to_datetime`）|
|  | (d3) chrome 端 dumps 上传时未填 `dump.request.body` 内的搜索条件，导致 TikTok 返回的订单字段缺 `trade_order_module` |
| 已知 TODO | `tech-doc/plugin-sourced-shop-analytics.md §7.8` |

### §5.5 lessons learned：字段级静默失败（2026-09-14 教训）

> **背景**：`_ts_to_datetime` 的 `str` 分支只认 ISO 格式，而 `create_time` / `update_time` 实测是**数字字符串**（秒/毫秒/微秒都有），导致 `plugin.orders` 四个时间字段几乎全 NULL 且无任何信号——`parse_error` 只覆盖整 dump 级失败，字段级失败不可观测。

**修复**（commit 122cad1）：数字字符串解析 + 不可解析值 `log.warning`。

**原则**（_ts_to_datetime / _to_decimal 都适用）：
- **字段级解析失败不落 `parse_error`**（避免整批重试）
- **必须 `log.warning` 显式可见**（带 field 标签）—— 这样 `plugin.plugin_logs` 能观测

### §5.6 lessons learned：物流 353 条 dump 全部空 body（跨仓协调）

> **现象**：prod `logistics` 域 353 条 dump 全部 `response.body=null`（插件端 `logistic_detail` 抓取 100% 返回非 JSON / 空 body），但仍上传空 dump 并按 RETRYABLE 无限重试。

**根因**（插件仓）：chrome ext 上传时未判 `response.body == null`。

**修复**（跨仓，chrome-plugins）：
1. `payload == null` 不上传
2. 上报 `responseReadError` 诊断到 `plugin.plugin_logs`

**与本仓 dumps 端的协调**：见 `tech-doc/dumps-tts-erp-refactor-proposal.md` Lane B —— 严格 HTTP 语义下服务端 `empty_response` 改 **422 PERMANENT**（不是 RETRYABLE），plugin 跳过 + 记日志 + 等下次 alarm，不再无限重试。

---

## 附：关键代码定位

| 模块 | 路径 | 行数 |
| --- | --- | --- |
| Dumps 端点 + 路由 | `tts_erp_v2/api/v2/order_sync.py` | 604 |
| Schema 定义 | `tts_erp_v2/api/v2/order_sync.py:105-170` | — |
| Domain 路由判定 | `tts_erp_v2/api/v2/order_sync.py:407-446` | — |
| 错误码返回 | `tts_erp_v2/api/v2/order_sync.py:192-225` | — |
| Parser 总入口 | `tts_erp_v2/plugin/orders/parser.py` | 600 |
| Chrome 端 schema（Zod 镜像） | `chrome-plugins/ads-data-sync/src/core/order-sync-schemas.ts` | 117 |
| Chrome 端 dumps 封装 | `chrome-plugins/ads-data-sync/src/core/order-sync.ts` | ~500 |
| Chrome 端 endpoint 路径常量 | `chrome-plugins/ads-data-sync/src/core/tiktok-order-endpoints.ts` | — |
|  | `chrome-plugins/ads-data-sync/src/core/tiktok-statement-endpoints.ts` | — |
| Chrome 端同步调度 | `chrome-plugins/ads-data-sync/entrypoints/background.ts:141-152` (常量) / `:262-280` (alarm 路由) / `:590-680` (orders alarm handler) / `:681-790` (logistics+statements handler) | — |
| Chrome 端 progress/diagnostic 端点 | `POST /v2/order-sync/has-data`（plugin `hasDataBulk`） / `GET /v2/order-sync/synced-ids`（plugin `fetchSyncedIds`） —— **不属于本契约** | — |
| 错误码 + 空响应处理 | `tts_erp_v2/api/v2/order_sync.py:328-345`（`empty_response`）/ `:352-360`（`parse_error`） | — |

---

## §6 广告消耗（ad 域）

> **独立于订单/物流/结算/售后 dumps 链路**，走另一套 `analytics-sync-v2` 协议 + `POST /v2/analytics/sync/dumps` 端点。**两者协议不同，不要合并**（合并会让 Pydantic schema 复杂度爆炸）。

### 6.1 ad 域 5 张表（2026-09-11 由 analytics schema 并入 plugin）

| 表 | 用途 | 字段命名 | 协议 |
| --- | --- | --- | --- |
| `plugin.ad_today` | 今天实时（30s 滚动）| TikTok API 原名（`mixed_real_cost` / `onsite_roi2_shopping_sku` 等）| v4 dump |
| `plugin.ad_daily` | 历史天级可校准 | 同上 | v4 dump |
| `plugin.ad_monthly` | 月级聚合 | 同上 | v4 dump |
| `plugin.ad_raw_log` | 原始 dump（ad 域）| 完整 request+response | dump 协议 |
| `plugin.plugin_logs` | 扩展运行日志 | — | 内部 |

**ads-data-sync 抓的 3 个 ad 端点**（见 `tech-doc/tiktok-seller-center-api-catalog.md` §1.7）：

```
POST /oec_ads/shopping/v1/oec/ads/stat/post_product_list   ← 商品分析
POST /oec_ads/shopping/v1/oec/ads/stat/post_session_list   ← 会话分析
POST /oec_ads/shopping/v1/oec/ads/stat/campaign_opt_log_list ← 变更日志
```

### 6.2 v4 协议关键设计决策（D1-D5，详见 `tech-doc/analytics/dump-architecture.md` §2）

| 决策 | 含义 |
| --- | --- |
| **D1** | `ad_raw` 是 source-of-truth（不可变原始 dump，jsonb 直存）|
| **D2** | `/dumps` 协议替换 `/batches`（**单 dump object** body，不是 array）|
| **D3** | `/cursor` 只剩 has-data 模式（plugin 用它做防风控预检）|
| **D4** | schema 5 → 3 张表（去掉 page bitmap 和 work-list 状态机）|
| **D5** | schema 3 → 1 张表（2026-09-05 reorg，ad_raw 是唯一保留）|

### 6.3 v4 dumps 请求 schema（示例）

```jsonc
// POST /v2/analytics/sync/dumps body 示例
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

### 6.4 ad 域解析流程（server 端 `/v2/analytics/sync/dumps`）

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

**v3 → v4 关键变化**（详见 `tech-doc/analytics/dump-architecture.md` §3）：

| 维度 | v3（已废）| v4（现行）|
| --- | --- | --- |
| 请求体 | `array` of dump objects | `object`（单 dump）|
| 写入表 | 3 张（ad_raw + ad_records + ad_daily_completeness）| 1 张（ad_raw）|
| /cursor 协议 | work-list（items / nextRequiredDay）| has-data 单查询 |
| plugin 状态 | lease + epoch + page bitmap | "dumb dump"（无状态）|

### 6.5 ad 域代码定位

| 模块 | 路径 |
| --- | --- |
| dumps 端点 + v4 协议 | `tts_erp_v2/api/v2/analytics.py` |
| analytics v2 repository | `tts_erp_v2/analytics/repository.py` |
| has-data 缓存 | `tts_erp_v2/analytics/has_data_cache.py` |
| AdDaily / AdToday / AdMonthly 模型 | `tts_erp_v2/db/models/plugin.py`（已合并入 plugin schema）|
| Chrome 端 v4 dumps | `chrome-plugins/ads-data-sync/src/core/analytics-sync-v2.ts` |
| Chrome 端 Zod schema | `chrome-plugins/ads-data-sync/src/core/order-sync-schemas.ts` |

---

## §7 4 域 ID 映射（核心连接键）

> **关键**：4 域跨表关联**不依赖**硬 FK（plugin 表间无 FK，靠 shared 5 元组 key）—— 与 ad_raw 同模式（D1 决策）。理由：避免 plugin 重写时连锁 cascade 删数据。

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

---

## §8 4 域数据流（卖家操作时间线）

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
   └─ /return_refund/202309/cancellations/search  （详见 §5.1 (a) 已知 gap）
   └─ 同时：order.list 同 main_order_id 出现 reverse_module[]
```

**关键原则**：物流终态**独立于**订单终态判断。即使订单已 COMPLETED，只要物流还没拿到 50101 签收事件，插件继续拉直到拿到为止（避免漏抓延迟回传的签收事件）。

---

## §9 plugin.* 业务表 ER（plugin schema）

```
plugin.orders
  │ (shop_id, order_id)
  ├── plugin.order_lines         (1:N, shop_id+order_id+sku_id)
  ├── plugin.shipments           (1:N, shop_id+order_id → package_id)
  │      └── plugin.tracking_events  (1:N, shop_id+package_id+event_key)
  ├── plugin.settlements         (1:N, shop_id+statement_id+statement_version)
  │      └── plugin.settlement_details  (1:N, shop_id+sku_detail_id)
  └── plugin.after_sales         (1:N, shop_id+main_order_id → cancel_id)
         └── plugin.after_sale_items    (1:N, shop_id+cancel_id → line_item_id)
```

**重要变化（Phase 3, 2026-09-17）**：`plugin.raw_log` 表已 drop，原 8 张业务表 `log_id` FK 链全部断开。Plugin 间业务表不依赖硬 FK（逻辑链接靠 shared 5 元组 key）。

---

## §10 状态终态判断（关键决策）

| 域 | 终态判断字段 | 终态值 | 决策日 |
| --- | --- | --- | --- |
| 订单 | `main_order_status` | COMPLETED / CANCELLED | 2026-09-12（order-domain-business-rules.md §1）|
| 物流 | `tracking_events[].action_code` | 50101 / 80101 / 110101 | 2026-09-12（同上 §5）|
| 结算 | `settlements[].settlement_time != null` | 已结算 | 2026-09-13（catalog §7.6.2）|
| 售后 | `reverse_module[].refund_time != null` | 已退款 | 2026-09-13（同上 §7.4.4）|

**关键原则**：物流终态**独立于**订单终态判断。即使订单已 COMPLETED，只要物流还没拿到 50101 签收事件，插件继续拉直到拿到为止（避免漏抓延迟回传的签收事件）。

---

## §11 main_order_status 码值实测交叉验证（2026-09-14）

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

---

## §12 跨表 JOIN 模板（4 域合一查询）

```sql
-- 找一单的完整链路：订单 + 物流 + 结算 + 退款
SELECT
  o.order_id, o.main_order_status, o.payment_amount,
  s.package_id, s.tracking_number, s.carrier_name,
  st.settlement_id, st.settle_amount, st.earning_amount,
  -- reverse_module 不在 plugin.orders（需 plugin.after_sales 联表）
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
-- reverse_module 通过 intercepted_requests 链路
LEFT JOIN plugin.intercepted_requests ir
  ON ir.seller_id = o.shop_id
  AND ir.endpoint_path = '/api/fulfillment/order/list'
  AND ir.response_body->'data'->'main_orders'->0->'main_order_id' = o.order_id
WHERE o.shop_id = ?
  AND o.order_id = ?;
```

> 上面 SQL 是**伪码**示意，实际 JOIN 路径以具体查询需求为准。

---

## §13 TODO 清单（out of scope）

| # | 内容 | 状态 | 谁负责 |
|---|---|---|---|
| 1 | ~~dumps 端 `after_sales` 域接入（VALID_DOMAINS 加 + if/elif 链）~~ | ✅ 已完成（Lane `feat/after-sales-routing`） | — |
| 2 | 取消 endpoint 解析器（/return_refund/.../cancellations/search）| ❌ TODO | 同上 |
| 3 | `main_order_status` 状态码字典全集（§11 部分覆盖 100-104）| 🔄 部分 | feature/plugin-shop-analytics |
| 4 | 物流 tracking_events 字段完整结构 | ❌ TODO | 未抓到完整 burst |
| 5 | settlement `reasons_detail[].reason` 码表 | ❌ TODO | catalog §7.4.5 TODO |
| 6 | multi-package 订单 | ❌ TODO | catalog §7.4.6.3 TODO |
| 7 | chrome 端抓取规则 + 权限 | ❌ 跨仓 | chrome-plugins |
| 8 | dumps 上传失败重试策略 | ❌ 跨仓 | chrome-plugins |
| 9 | 物流空 body 修复（插件端 + 服务端 422 落地）| ❌ TODO | `dumps-tts-erp-refactor-proposal.md` Lane B |
| 10 | 结算 0 行根因定位（Lane C 诊断 + parser fallback） | ❌ TODO | `dumps-tts-erp-refactor-proposal.md` Lane C（**不做回填**，见 review §G7 A13） |
| 11 | 严格 HTTP 语义落地（empty_response 改 422 + rowsWritten/data.status 删）| ❌ TODO | `dumps-tts-erp-refactor-proposal.md` Lane E |
