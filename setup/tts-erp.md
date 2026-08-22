# TikTok Shop ERP 服务 (tts-erp)

> schan 服务器 · `0.0.0.0:9877` (内网) · 端口与 oauth-receiver (9876) 平行
> 单文件 Python 服务 · PostgreSQL 持久化 · HMAC-SHA256 签名
>
> v1.3 (2026-08-16) · **REAL 模式** · 通过 oauth-receiver 拿 token · **Order + Finance + Return/Refund 端到端 sync 已通**
>
> 上游：oauth-receiver (:9876) 拿 token
> 下游：TikTok Shop Open API (open-api.tiktokglobalshop.com)
> 存储：PostgreSQL `tts_erp` 数据库

## 是什么

把 TikTok Shop Partner 的 **Order API**（[官方文档](https://partner.tiktokshop.com/docv2/page/order-api-overview)）、
**Finance / Statement API**（[get-statements-202309](https://partner.tiktokshop.com/docv2/page/get-statements-202309)）
和 **Return / Refund / Cancellation API**（[return-refund-and-cancel-api-overview](https://partner.tiktokshop.com/docv2/page/return-refund-and-cancel-api-overview)）
包成 HTTP 端点，**业务代码不用关心 HMAC 签名 / x-tts-access-token / shop_cipher 哪个放 query
哪个放 body / 翻页这些细节**。同步过来的订单/对账单/付款记录/退货/取消落到本地 PG，ERP 后台随便查。

## 当前状态

| 项目                  | 值                                                              |
|-----------------------|-----------------------------------------------------------------|
| 服务进程              | `python3 -u /home/schan/tts-erp/tts_erp.py` (PID 不固定)         |
| 端口                  | `0.0.0.0:9877` (TCP LISTEN)                                     |
| 工作模式              | **REAL** (连真 TikTok API)                                      |
| DB                    | `tts_erp` on `postgres` container :5432 · **9 张表**            |
| Token 源              | `http://127.0.0.1:9876` (oauth-receiver) · **不**直读 PG        |
| HMAC 签名             | ✅ 验证通过 (canonical = `{secret}{path}{kv}{body}{secret}`)        |
| /orders/search 端到端 | ✅ **2026-08-16 实测成功**（page_size 走 query string，body 只放过滤条件） |
| /finance/statements   | ✅ **2026-08-16 实测成功**（GET with sort_field REQUIRED in query） |
| /finance/payments     | ✅ **2026-08-16 实测成功** |
| /returns/search       | ✅ **2026-08-16 实测成功**（POST, 14 rows / 2 pages）|
| /cancellations/search | ✅ **2026-08-16 实测成功**（POST, 75 rows / 2 pages）|
| 已入库 order 数量     | 365（shop 7494763368967603447，37 页翻页）                     |
| 已入库 statement 数量 | 31（VN 店铺，1 页）                                            |
| 已入库 payment 数量   | 10（VN 店铺，1 页）                                            |
| 已入库 return 数量    | 14（VN 店铺，2 页）                                            |
| 已入库 cancellation 数量 | 75（VN 店铺，2 页）                                         |

## 文件布局

```
/home/schan/tts-erp/
├── tts_erp.py            # 主服务（路由 + 业务逻辑）
├── tts_signing.py        # HMAC-SHA256 签名 + TikTok HTTP 客户端（可复用）
├── .env                  # 凭据 + DB URL（0600）
├── restart.sh            # 重启脚本
├── schema.sql            # PG 表结构
├── setup/
│   └── tts-erp.md        # ← 你正在看的这个文件
└── logs/
    ├── stdout.log
    └── stderr.log
```

## PostgreSQL 表

```
database: tts_erp
tables:   shops, orders, order_items, order_shippings,
          statements, payments, returns, cancellations, sync_log
```

| 表                  | 说明                                            |
|---------------------|-------------------------------------------------|
| `shops`             | 已授权 shop 元数据（name, region, cipher, seller_type） |
| `orders`            | 订单主表（order_id PK, 完整 raw JSONB）         |
| `order_items`       | 订单商品行（order_id + item_id 复合 PK）         |
| `order_shippings`   | 物流信息（order_id PK, tracking, provider）     |
| `statements`        | 财务对账单（statement_id PK）                  |
| `payments`          | 财务付款记录（payment_id PK）                  |
| `returns`           | 退货/退款申请（return_id PK, 完整 raw JSONB）   |
| `cancellations`     | 订单取消记录（cancel_id PK, 含 line_items）     |
| `sync_log`          | 同步历史（每次 /sync 调用一条，便于审计）      |

## 端点完整列表

### OAuth passthrough（代理到 oauth-receiver）
- `GET /shops`                  列出已授权 shops
- `GET /shops/<shop_id>`        单个 shop 元数据
- `GET /token/<shop_id>?reveal=1`   拿 access_token + shop_cipher

### Order API（直传 TikTok — READ-ONLY 202309）
- `POST /orders/search`                       搜索订单
- `GET  /orders/<order_id>`                   订单详情（→ `/order/202309/orders?ids=<id>`）
- ❌ action 端点（cancel/confirm/ship/tracking/risk/buyer/recipient）一律返 501 — 202309 Order 模块只读

所有 Order API 端点都需要 `?shop_id=XXX` 选 shop（用于取 token + shop_cipher）。

### Finance / Statement API（get-statements-202309）— 代理
- `GET  /finance/statements?shop_id=XXX&page_size=50&sort_field=statement_time&sort_order=DESC`
- `GET  /finance/payments?shop_id=XXX&page_size=50&sort_field=create_time&sort_order=DESC`
  **注意**：`sort_field` **REQUIRED**（server 返回 36009004 otherwise），service 会自动注入默认值

### Return / Refund / Cancellation API（return-refund-202309）— 代理（只读）
- `POST /returns/search`        body: `{shop_id, ...filters}`   → `/return_refund/202309/returns/search`
- `POST /cancellations/search`  body: `{shop_id, ...filters}`   → `/return_refund/202309/cancellations/search`
- ❌ `POST /returns`            → **501**（CREATE write endpoint，按用户要求不接）
- ❌ `POST /cancellations`      → **501**（CREATE write endpoint，按用户要求不接）
- ❌ `/reverse/202309/*`        → **HTTP 404**（CDN 级，202309 spec 没有此模块）

### 本地 DB 读
- `GET  /db/orders?shop_id=&status=&limit=`  本地订单列表
- `GET  /db/orders/<order_id>`                单订单（带 raw JSONB）
- `GET  /db/orders/<order_id>/items`          订单商品
- `GET  /db/orders/<order_id>/shipping`      物流信息
- `GET  /db/statements?shop_id=&limit=`        本地对账单
- `GET  /db/payments?shop_id=&status=&limit=`  本地付款记录
- `GET  /db/returns?shop_id=&status=&limit=`   本地退货记录
- `GET  /db/cancellations?shop_id=&status=&limit=` 本地取消记录
- `GET  /db/sync_log`                         同步历史

### 同步（TikTok → 本地 DB）
- `POST /sync/orders`        body: `{shop_id, order_status?, create_time_ge?, create_time_lt?, page_size?}`
                              实际实现：page_size 走 query string，body 只放过滤条件
- `POST /sync/order/<order_id>`               单个订单同步
- `POST /sync/statements`   body: `{shop_id, statement_time_ge?, statement_time_lt?, page_size?}`
- `POST /sync/payments`     body: `{shop_id, create_time_ge?, create_time_lt?, page_size?}`
- `POST /sync/returns`      body: `{shop_id, create_time_ge?, create_time_lt?, page_size?}`
- `POST /sync/cancellations` body: `{shop_id, create_time_ge?, create_time_lt?, page_size?}`

### 运维
- `GET  /healthz`           健康检查
- `GET  /endpoints`         端点清单

## 完整使用流程

### 一次性配置
```bash
# 1) 编辑 .env，确保跟 oauth-receiver 的 TIKTOK_APP_KEY/APP_SECRET 一致
cat /home/schan/tts-erp/.env
# TTS_ERP_HOST=0.0.0.0
# TTS_ERP_PORT=9877
# OAUTH_RECEIVER_URL=http://127.0.0.1:9876
# TIKTOK_APP_KEY===REDACTED_APP_KEY==
# TIKTOK_APP_SECRET===REDACTED_APP_SECRET==
# TIKTOK_API_HOST=https://open-api.tiktokglobalshop.com
# TTS_ERP_DB_URL=postgresql://postgres:...@127.0.0.1:5432/tts_erp

# 2) 启动
bash /home/schan/tts-erp/restart.sh
```

### 同步订单到本地
```bash
# 同步最近 100 个订单
curl -X POST "http://127.0.0.1:9877/sync/orders" \
  -H "Content-Type: application/json" \
  -d '{"shop_id": "7494763368967603447", "page_size": 100}'

# 同步某订单详情
curl -X POST "http://127.0.0.1:9877/sync/order/5800123456789012345"

# 查本地 DB 里的订单
curl "http://127.0.0.1:9877/db/orders?shop_id=7494763368967603447&limit=20"
```

### 直接调 TikTok API（不存本地）
```bash
# 搜订单（代理到 TikTok）
curl -X POST "http://127.0.0.1:9877/orders/search?shop_id=7494763368967603447" \
  -H "Content-Type: application/json" \
  -d '{"order_status": 100, "page_size": 50}'

# 取订单详情
curl "http://127.0.0.1:9877/orders/5800123456789012345?shop_id=7494763368967603447"
```

### 确认发货
```bash
curl -X POST "http://127.0.0.1:9877/orders/5800123456789012345/confirm?shop_id=7494763368967603447" \
  -H "Content-Type: application/json" \
  -d '{"package_id": "..."}'
```

> ⚠️ 202309 Order 模块是只读，**所有 action 端点都返 501**（详见下方"已知偏差"）。
> 真实的写操作在 Fulfillment / Reverse Logistics 模块，**未实现**。

### 同步退货 / 取消记录
```bash
# 同步所有退货（buyer-initiated refund/return requests）
curl -X POST "http://127.0.0.1:9877/sync/returns" \
  -H "Content-Type: application/json" \
  -d '{"shop_id": "7494763368967603447", "page_size": 50}'

# 同步所有取消记录
curl -X POST "http://127.0.0.1:9877/sync/cancellations" \
  -H "Content-Type: application/json" \
  -d '{"shop_id": "7494763368967603447", "page_size": 50}'

# 直传代理（不存 DB）
curl -X POST "http://127.0.0.1:9877/returns/search" \
  -H "Content-Type: application/json" \
  -d '{"shop_id": "7494763368967603447"}'

# 查本地 DB（按状态过滤）
curl "http://127.0.0.1:9877/db/returns?shop_id=7494763368967603447&status=AWAITING_BUYER_SHIP&limit=10"
curl "http://127.0.0.1:9877/db/cancellations?shop_id=7494763368967603447&status=CANCELLATION_REQUEST_COMPLETE&limit=10"
```

## 配置（env vars）

| 变量                  | 默认值                                       | 说明                                       |
|-----------------------|----------------------------------------------|--------------------------------------------|
| `TTS_ERP_HOST`        | `0.0.0.0`                                    | 绑定地址                                   |
| `TTS_ERP_PORT`        | `9877`                                       | 监听端口                                   |
| `OAUTH_RECEIVER_URL`  | `http://127.0.0.1:9876`                      | **Token 源**（必填，且必须可达）            |
| `TIKTOK_APP_KEY`      | (空)                                         | HMAC 签名用                                |
| `TIKTOK_APP_SECRET`   | (空)                                         | HMAC 签名用 + Canonical 前后缀              |
| `TIKTOK_API_HOST`     | `https://open-api.tiktokglobalshop.com`      | 沙箱改成 `https://open-api-sandbox.tiktokglobalshop.com` |
| `TTS_ERP_DB_URL`      | (空)                                         | PG 连接串，**空则 DB 端点会 500**           |
| `TTS_ERP_HTTP_TIMEOUT`| `30`                                         | 调 TikTok 的超时（秒）                       |

## HMAC 签名（关键！）

TikTok Partner API 的 HMAC-SHA256 签名规则（**实测**）：

```python
# canonical = "{secret}{path}{key1}{value1}{key2}{value2}...{keyN}{valueN}{secret}"
# GET:  body=None，canonical 末尾不加 body
# POST: body 拼在 KV 串之后、结尾 secret 之前
#      canonical = f"{secret}{path}{kv_concat}{body}{secret}"
# 然后: sign = HMAC-SHA256(secret, canonical) -> hex

# keys 按字母序排序（app_key, shop_cipher, timestamp）
# body 是 JSON.dumps 出来的字符串（ensure_ascii=False）
# 注意：body 不能 URL-encode，必须是原始 JSON 字符串
```

## 进程管理

**2026-08-18 起由 systemd user 单元托管**（`~/.config/systemd/user/tts-erp.service`，
`WantedBy=default.target` + `Linger=yes`，**开机自启**，无需登录；`.env` 由 `EnvironmentFile` 加载，
`After=oauth-receiver.service` 保证在 token 服务之后启动，崩溃自动 `Restart=always`）：

```bash
# 状态
systemctl --user status tts-erp.service
ss -tlnp | grep 9877
curl -s http://127.0.0.1:9877/healthz

# 重启 / 停止 / 禁用开机自启
systemctl --user restart tts-erp.service   # = bash /home/schan/tts-erp/restart.sh
systemctl --user stop tts-erp.service
systemctl --user disable tts-erp.service

# systemd 层日志（业务日志仍是 logs/{stdout,stderr}.log）
journalctl --user -u tts-erp -n 50
```

旧的 `pkill + nohup` 手动方式已废弃——直接起进程会和 systemd 单元抢 9877 端口。

## 已知偏差

- **没接 webhook**：TikTok Shop 支持 order.* 事件 webhook 推送，本服务还没接（需要公网回调 + 解密）。
- **没做 token 自动续期**：access_token 7 天过期，靠 cron 调 oauth-receiver 的 `/refresh` 续（已配）。
- **没做增量同步**：现在每次 `/sync/orders` 都是全量（受 page_size 限制），生产应该用 create_time_ge 增量。
- **app_secret / DB 密码已在 chat 暴露**：建议去 TikTok Partner Center + Postgres 重置一次。
- **Return/Refund 写入端点没接**：按用户要求，**没有**集成 `POST /return_refund/202309/returns` 和 `/cancellations`（CREATE 端点），
  也没有集成 reject/approve/accept 等 approve 类动作。`tts-erp` 对 `POST /returns` 和 `POST /cancellations` 一律返 501。
  `POST /returns/search` 和 `/cancellations/search`（list）已接。
- **`/reverse/202309/*`（reverse logistics 模块）202309 spec 不存在**：所有路径返回 CDN 级 HTTP 404，
  TikTok 没把这模块开放到 202309。文档里提到但实际不可用。

## TikTok /orders/search 字段名（实测已知坑）

## TikTok /orders/search 字段名（实测已知坑）

| 字段              | 位置              | 注意事项                              |
|-------------------|-------------------|---------------------------------------|
| `page_size`       | **query string**  | body 放 36009004，必须 URL 参数       |
| `sort_field`      | **query string**  | 如 "create_time"                      |
| `sort_order`      | **query string**  | **必须大写** "DESC" / "ASC"          |
| `page_token`      | **query string**  | 翻页用                                |
| `order_status`    | body              | **必须是字符串**（不是 int！否则 36009004 type invalid）|
| `create_time_ge`  | body              | unix seconds int                       |
| `create_time_lt`  | body              | unix seconds int                       |

注意：`/orders/list` **不存在**（返回 36009009 Invalid path），所有 list/query 走 `/orders/search`。
订单实际响应在 `data.orders`（不是 `order_list`），订单 ID 字段是 `id`（不是 `order_id`），
`status` 是字符串（"AWAITING_SHIPMENT" / "UNPAID" / "IN_TRANSIT" / "DELIVERED" / "CANCELLED"），
`payment.total_amount` 是嵌套对象。

## TikTok /finance/202309 字段名（实测已知坑）

| 字段              | 位置              | 注意事项                                       |
|-------------------|-------------------|------------------------------------------------|
| `page_size`       | **query string**  | 上限 100                                       |
| `sort_field`      | **query string**  | **REQUIRED**（不传 36009004）                  |
| `sort_order`      | **query string**  | **必须大写** "DESC" / "ASC"                  |
| `page_token`      | **query string**  | 翻页用                                         |
| `statement_time_ge/lt` | query string | 可选过滤                                       |
| `create_time_ge/lt`    | query string | 可选过滤（payments）                            |

**Statement 响应结构**（`/finance/202309/statements`）：
- 数组在 `data.statements`（不是 `list`）
- 字段：`id`、`payment_id`、`currency`、`payment_status` (PAID/PENDING)、
  `statement_time`、`payment_time`、`revenue_amount`、`fee_amount`（**负数**）、
  `net_sales_amount`、`shipping_cost_amount`、`adjustment_amount`、`settlement_amount`

**Payment 响应结构**（`/finance/202309/payments`）：
- 数组在 `data.payments`
- 金额是嵌套对象：`amount.{currency, value}`、`settlement_amount.{currency, value}`、
  `payment_amount_before_exchange.{currency, value}`、`reserve_amount.{currency, value}`
- 字段：`id`、`status` (PAID/PENDING/FAILED)、`bank_account`（脱敏 "*************200659"）、
  `create_time`、`paid_time`、`exchange_rate`

**已知 36009009 路径**（不存在）：
- `/finance/202309/statements/{id}`（无 detail endpoint）
- `/finance/202309/statements/{id}/orders`
- `/finance/202309/statements/{id}/refunds`
- `/finance/202309/statements/{id}/download`
- `/finance/202309/transactions`、`/finance/202309/transactions/unsettled`（2026-08-18 全版本实测 404）
- `/finance/202309/settlements`
- `/finance/202309/balance`

**2026-08-18 实测更正**：账单子记录端点**存在**，叫 `statement_transactions` 不是 `transactions`：
- `GET /finance/202309/statements/{id}/statement_transactions?sort_field=order_create_time&sort_order=DESC` ✅（58 字段/条含 order_id，sort_field 必填且只接受 `order_create_time`）
- `GET /finance/202309/orders/{order_id}/statement_transactions` ✅（单订单 + SKU 级明细）
- `GET /finance/202501/orders/{order_id}/statement_transactions` ✅（订单汇总 + `sku_transactions.fee_tax_breakdown` 更细费用分类；202501 只开放这两个端点）
- `GET /finance/202309/withdrawals?types=WITHDRAW` ✅（types 必填）
- 注意：增值税/交易手续费/订单处理手续费对 VN 店铺不拆分（含在 fee_amount，对应字段恒 0）

## TikTok /return_refund/202309 字段名（实测已知坑）

**2026-08-16 探测发现**：只有 2 个 read endpoint 存在，**没有 detail-by-id 端点**。

| 端点                                 | 方法 | 状态     | 说明                                         |
|--------------------------------------|------|----------|----------------------------------------------|
| `/return_refund/202309/returns/search`     | POST | ✅ 工作   | 列表，body 只放过滤；page_size/sort 在 query |
| `/return_refund/202309/cancellations/search` | POST | ✅ 工作   | 列表，body 只放过滤；page_size/sort 在 query |
| `/return_refund/202309/returns`     | POST | ⚠️ WRITE | CREATE endpoint，需要 `order_id` + `return_reason` — **不接** |
| `/return_refund/202309/cancellations` | POST | ⚠️ WRITE | CREATE endpoint，需要 `order_id` — **不接** |
| `/return_refund/202309/returns/{id}`  | GET  | ❌ 36009009 | 路径不存在（没 detail-by-id）             |
| `/return_refund/202309/cancellations/{id}` | GET | ❌ 36009009 | 路径不存在（没 detail-by-id）          |
| `/return_refund/202309/returns/list`  | -    | ❌ 36009009 | 不存在                                       |
| `/reverse/202309/*`                 | -    | ❌ HTTP 404 (CDN) | 202309 spec 没有此模块（"Reverse Logistics" 在文档里提到但实际未开放） |

**Returns 响应结构**（`/return_refund/202309/returns/search`）：
- 数组在 `data.return_orders`（不是 `returns` 或 `list`）
- 字段：`return_id`（不是 `id`！）、`order_id`、`return_status` ("AWAITING_BUYER_SHIP" / "BUYER_SHIPPED_ITEM" / "RETURN_OR_REFUND_REQUEST_COMPLETE")、
  `return_reason` (机器码 `ecom_order_delivered_refund_and_return_reason_*`)、`return_type` ("RETURN_AND_REFUND")、
  `role` ("BUYER")、`create_time`、`update_time`、`is_combined_return`、`handover_method` ("PICKUP")、`is_quick_refund`、
  `refund_amount.{currency, refund_subtotal, refund_tax, refund_total, refund_shipping_fee}`、
  `return_line_items[].{order_line_item_id, product_name, product_image, sku_id, sku_name, refund_amount}`

**Cancellations 响应结构**（`/return_refund/202309/cancellations/search`）：
- 数组在 `data.cancellations`
- 字段：`cancel_id`、`order_id`、`cancel_status` ("CANCELLATION_REQUEST_COMPLETE")、
  `cancel_reason` (机器码 `ecom_order_to_ship_canceled_reason_*`)、
  `cancel_reason_text` ("No longer needed" / "Package delivery failed" 等)、
  `cancel_type` ("BUYER_CANCEL" / "CANCEL")、
  `role` ("BUYER" / "SELLER" / "SYSTEM")、`should_replenish_stock` (bool)、
  `create_time`、`update_time`、
  `cancel_line_items[].{cancel_line_item_id, order_line_item_id, product_name, product_image, sku_id, sku_name}`

**实测 2026-08-16 抓取结果**（shop 7494763368967603447, 越南）:
- returns: 14 rows / 2 pages
- cancellations: 75 rows / 2 pages
- 状态分布：
  - returns: 11 `RETURN_OR_REFUND_REQUEST_COMPLETE`, 2 `AWAITING_BUYER_SHIP`, 1 `BUYER_SHIPPED_ITEM`
  - cancellations: 75 `CANCELLATION_REQUEST_COMPLETE` (全部完成)

## 相关文档

- `/home/schan/oauth-receiver/setup/oauth-receiver.md` — 上游 token 服务
- `AGENTS.md` (本项目) — AI agent 操作指南
- `README.md` (本项目) — 人类使用说明
- `handoff.md` (本项目) — 跨 session 交接笔记
- `https://partner.tiktokshop.com/docv2/page/order-api-overview` — TikTok 官方订单 API 文档
- `https://partner.tiktokshop.com/docv2/page/get-statements-202309` — TikTok 官方财务对账单 API
- `https://partner.tiktokshop.com/docv2/page/return-refund-and-cancel-api-overview` — TikTok 官方退货/取消 API 概览
