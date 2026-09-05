# handoff.md — tts-erp 跨 session 交接笔记

> 上次 session: 2026-09-05（v1 public.*归档删除；另有 analytics.ad_product_links 视图 lane）
> 上次 session 主题: 删除 v1 遗留 public.* 业务表（19 张 DROP），official dump-then-drop 流程

## TL;DR (2026-09-05)

**v1 `public.*` 遗留层已按官方流程归档删除**（观察期提前收口，原定 ~09-26）：

1. 归档：`/home/schan/backups/tts_erp_public_v1_legacy_20260905T110814Z.sql.gz`（19 表 schema+data，可完整恢复）。
2. DROP 19 张 v1 业务表 + 3 个孤儿函数；`schema_tts_erp.sql` 重生成（-839 行）。
3. **不要动 `public` schema 和 `public.fn_touch_updated_at()`** —— 41 个 v2 updated_at 触发器依赖它
   （migration 0001；`tests/db/test_time_fields_convention.py` 锁定）。
4. oauth_receiver（独立库 :5432/oauth_receiver）未动，仍按原观察期 ~09-26 保留。

## TL;DR (2026-08-31)

procurement UI 重做 + MinIO SPU 图片存储全部落地并提交到 `feature/procurement-ui`：

1. **Backend**：`tts_erp_v2/storage/minio_client.py` + `/v2/spu-images/*`（presigned upload/confirm/list/delete）+ `procurement.spu_images` 表（`schema_storage.sql`，**生产库还没 apply**）。
2. **Frontend**：`/v2/pages/manual-costs` 壳页面 + `/static/console.{css,js}`（shop switcher + 三 tab 工作台）。
3. **修了两个集成 bug**：`/endpoints` 在 FastAPI ≥0.141 lazy router 下丢路由（`_iter_resolved_routes`）；`GET /v2/spu-images` 无 filter 时 `AmbiguousParameter` 500（`CAST(:cp_id AS bigint)`）。

**预存在的基线问题（master 上同样复现，与本分支无关，还没修）**：

- `tests_v2/sync_worker` + `tests_v2/test_models_smoke.py::test_sync_jobs_lifecycle` 失败 —— 真库 `sync_cursors`/`sync_jobs` 有重复行。
- `tests_v2/migration` 跑到 ~59% hang 住（怀疑等 DB 锁）。
- `tests_v2/jobs_tiktok` 5 个失败。

**已收尾（2026-08-31）**：已 merge 回 master（`aca4389`，/endpoints 冲突取 master 的 `_walk_v2_routes`）；`schema_storage.sql` 已 apply（幂等）；MINIO_* 配置已入主 `.env`；生产 :9877 已重启并冒烟通过（/endpoints count=38，spu-images 路由在线）。worktree `~/tts-erp.procurement` 已删。主 worktree 仍有另一 session 的 analytics_sync WIP 未提交（`analytics_sync/app.py`、`middleware/auth.py`、`tests_v2/api/test_auth_login.py` 等）。

## TL;DR (2026-08-25)

三个 drift 全部修完，e2e smoke + 单元测试全部绿：

1. **`/healthz` 不再撒谎** —— `oauth_receiver.token_count` 现在从 `oauth_tokens` 表真实 `SELECT COUNT(*)` 读，不再是 in-memory `_token_history`（永远 0）。
2. **`tts_erp.shops` 被填充** —— FastAPI startup lifespan + `POST /admin/shops/backfill` 都触发 backfill，幂等。`_tiktok_proxy` 在订单详情路径上也调 `persist_shop`（双保险）。
3. **`schema.sql` 重新生成** —— `scripts/regen_schema.py` 从真实 PG 拉出含 **24 张表** 的 schema（1 oauth_receiver + 23 tts_erp），fresh DB clean apply **0 errors**。

新文件：

- `tdd/_backfill.py` — `backfill_shops_from_oauth()` 幂等函数
- `scripts/regen_schema.py` — schema.sql 重新生成工具
- `tdd/test_healthz_token_count_fix.py` — 4 healthz 测试
- `tdd/test_shops_backfill.py` — 3 backfill 测试

## 上次 session (2026-08-16) 主题

## TL;DR

**所有端到端流程已通**：oauth-receiver 续期 cron 在跑、tts-erp 服务在 9877 监听、**`/sync/orders` 已成功入库 365 个订单**（37 页翻页）、`/sync/statements` 入库 31 条对账单、`/sync/payments` 入库 10 条付款记录。

**最终可用的端点**（2026-08-16 实测）：

### Order 模块

- `POST /orders/search` ✅ — 拉取订单列表（body 只放过滤条件，paging/sort 全在 query string）
- `GET /orders/<id>` ✅ — 拉取单个订单（内部转发到 `/order/202309/orders?ids=<id>`，因为 path 版的 `/orders/{id}` 返回 36009009）
- `POST /sync/orders` ✅ — 拉取并入库
- `POST /sync/order/<id>` ✅ — 拉单个并入库
- `GET /db/orders` / `GET /db/orders/<id>` 等 ✅ — 读本地 DB
- `POST /orders/<id>/cancel|confirm|update_status|shipping_info|verify_shipping` ❌ → **返回 501**（TikTok 202309 Order 模块是只读，写操作在 Fulfillment / Reverse Logistics 模块）
- `GET /orders/<id>/tracking|risk|buyer|recipient` ❌ → **返回 501**（同上，202309 Order 模块不暴露这些）
- `/orders/list` ❌ → 36009009 "Invalid path"（路径不存在，所有 list 走 /orders/search）

### Finance / Statement 模块（get-statements-202309）— 2026-08-16 新增

- `GET /finance/statements?shop_id=X&page_size=50&sort_field=statement_time&sort_order=DESC` ✅ — 拉对账单
- `GET /finance/payments?shop_id=X&page_size=50&sort_field=create_time&sort_order=DESC` ✅ — 拉付款记录
- `POST /sync/statements` / `POST /sync/payments` ✅ — 拉取并入库（自动翻页）
- `GET /db/statements?shop_id=X&limit=50` / `GET /db/payments?shop_id=X&status=PAID&limit=50` ✅ — 读本地 DB
- **已知不存在**（36009009）：`/finance/202309/statements/{id}`（无 detail）、`/statements/{id}/transactions`、子 records endpoints、downloads、balance

## 完成了什么

1. ✅ **oauth-receiver cron 续期** — `0 2 * * *` 调 `refresh_tokens.sh`，已加到 schan crontab
2. ✅ **tts-erp 服务** — 端口 9877，REAL 模式，DB `tts_erp` 5 张表都建好
3. ✅ **OAuth 间接获取** — tts-erp **不**直读 PG `oauth_tokens`，全部走 `http://127.0.0.1:9876` HTTP
4. ✅ **HMAC-SHA256 签名** — 实测通过 `106001 invalid sign` 这关
5. ✅ **Order API 代理** — 全部 12 个端点都接了（/orders/list 已移除，action 端点全部返回 501）
6. ✅ **同步逻辑** — `/sync/orders` 翻页拉（365 单/37 页实测成功），`/sync/order/<id>` 单拉
7. ✅ **本地 DB 读** — `/db/orders`（带 status 过滤，name 字段）, `/db/orders/<id>` 等
8. ✅ **4 份文档** — `setup/tts-erp.md`、`AGENTS.md`、`README.md`、`handoff.md`

## 关键路径速查

| 路径                                                | 说明                                |
|-----------------------------------------------------|-------------------------------------|
| `http://127.0.0.1:9877/healthz`                    | tts-erp 健康检查                   |
| ~~`http://127.0.0.1:9877/shops`~~                  | ~~列出 shops（代理到 oauth）~~ **Wave 3 Slice 2 后已删除**（调 oauth_receiver_core.db_list_shops in-process） |
| `http://127.0.0.1:9877/token/<id>?reveal=1`         | 拿 token + cipher（同上，in-process） |
| `http://127.0.0.1:9877/sync/orders`                 | POST body {shop_id, ...}，从 TikTok 拉单入库 |
| `http://127.0.0.1:9877/orders/search?shop_id=X&page_size=10` | 直接代理到 TikTok（page_size 在 URL） |
| `http://127.0.0.1:9877/db/orders?shop_id=X&status=AWAITING_SHIPMENT` | 本地 DB 订单列表 |
| `http://127.0.0.1:9876/healthz`                    | oauth-receiver 健康检查           |
| `http://127.0.0.1:9876/token/<id>`                 | oauth-receiver 单个 shop token     |
| `http://127.0.0.1:9876/tokens/shops`               | oauth-receiver 所有 shops          |

## TikTok /orders/search 字段名踩坑实录

### 1. `page_size` 必须在 query string，不能在 body

**症状**：body `{"page_size": 10}` 返回 36009004 "PageSize is a required field"。

**根因**：TikTok 把 paging/sort 参数都放在 query string。Body 只能放过滤条件。

**已确认工作**（probe_alt.py 2026-08-16）：

```python
# 正确：
extra_params = {"page_size": "10", "sort_field": "create_time", "sort_order": "DESC"}
body = {"order_status": "100"}  # 或 None

# 错误（即便 body 里有 page_size）：
body = {"page_size": 10}  # → 36009004
```

### 2. `sort_order` 必须大写

小写 `desc` → 36009004 "SortOrder is invalid, allowed values: ASC,DESC."。修复：直接用大写 "DESC" / "ASC"。

### 3. `order_status` 在 body 里要是 **string**，不是 int

int 100 → 36009004 "param order_status type invalid. actual type:int64, expected type:string"。

### 4. `/orders/list` 不存在

返回 36009009 "Invalid path. The specified path does not match any available endpoint."。所有 list/query 走 `/orders/search`。

### 5. 202309 spec 的 status 是字符串

不是 int code，而是 `"AWAITING_SHIPMENT"` / `"UNPAID"` / `"IN_TRANSIT"` / `"DELIVERED"` / `"CANCELLED"` 之类。DB schema 已有 `order_status_name` TEXT 列专放这个。

### 6. 实际响应数据结构（实测 2026-08-16）

```json
{
  "code": 0,
  "data": {
    "next_page_token": "...",
    "orders": [
      {
        "id": "585574475916477491",
        "status": "AWAITING_SHIPMENT",
        "create_time": 1786870064,
        "update_time": 1786870565,
        "buyer_email": "v4bE...@scs2.tiktok.com",
        "fulfillment_type": "FULFILLMENT_BY_SELLER",
        "shipping_provider_id": "7439297584469903122",   // top-level
        "shipping_provider_name": "Wise Express - DCS",  // top-level
        "payment": {                                     // nested object
          "total_amount": "495548",
          "currency": "VND",
          ...
        },
        "line_items": [ ... ],
        "recipient_address": { ... }
      }
    ]
  }
}
```

`order_list` / `order_id` 都不存在！字段名是 `orders` / `id`。

### 7. Order 详情端点不在 path 里

**症状**：`GET /order/202309/orders/<id>` 返回 36009009 "Invalid path"。

**正确**：`GET /order/202309/orders?ids=<id>`（id 走 query string，且 `ids` 小写复数）。

实测支持多 id：`?ids=585574475916477491,585574340257089064`。

### 8. 202309 Order 模块是只读

实测 2026-08-16 全部下列端点都返回 36009009 "Invalid path"：

- `cancel` / `confirm` / `update_status` / `shipping_info` / `verify_shipping`（写）
- `tracking` / `risk` / `buyer` / `recipient` / `tracking/get`（读）

所以 `tts-erp` 路由层对 `/orders/<id>/{action}` 一律返回 **501 Not Implemented** + 友好提示，不静默转发避免暴露 36009009 给上游。

**真实路径**：TikTok 写操作在 Fulfillment (`/fulfillment/202309/...`) 和 Reverse Logistics (`/reverse/202309/...`) 模块，需要单独接。本次未实现。

### 9. /finance/202309 模块实测（2026-08-16 新增）

只接 list 端点，**sort_field 是 REQUIRED**（不传 → 36009004）。

```
GET /finance/202309/statements  →  data.statements[]    ✓
GET /finance/202309/payments    →  data.payments[]      ✓
```

全部 detail / sub-records / download 端点 36009009：

```
/finance/202309/statements/{id}                          → 36009009
/finance/202309/statements/{id}/transactions             → 36009009
/finance/202309/statements/{id}/orders                  → 36009009
/finance/202309/statements/{id}/refunds                 → 36009009
/finance/202309/statements/{id}/download                → 36009009
/finance/202309/transactions                             → 36009009
/finance/202309/settlements                              → 36009009
/finance/202309/balance                                 → 36009009
```

### 10. 2026-08-16 用户额外要求（已做 — return_refund/202309 集成）

**最新更新**（v1.3）：2026-08-16 晚，用户改回原意，要求接入 return_refund/202309 接口（reject/approve/accept 等高危写入**不接**）。完成情况：

**实际存在的端点**（probe_refund_v3/v5/v6 实测）：

- `POST /return_refund/202309/returns/search`        ✅ 列表，14 rows / 2 pages 已入库
- `POST /return_refund/202309/cancellations/search`  ✅ 列表，75 rows / 2 pages 已入库

**确认不存在的端点**（不接）：

- `/return_refund/202309/returns/{id}`              → 36009009 (no path)
- `/return_refund/202309/cancellations/{id}`        → 36009009 (no path)
- `/return_refund/202309/returns/list`              → 36009009
- `/reverse/202309/*` (所有 14 个变体)              → HTTP 404 (CDN-level, 模块在 202309 spec 中未开放)
- `/fulfillment/202309/*`                            → 36009009

**确认存在但不接的 WRITE 端点**（按用户要求 reject/approve/accept 等动作一律不接）：

- `POST /return_refund/202309/returns`              (CREATE return request — 需要 order_id + return_reason)
- `POST /return_refund/202309/cancellations`        (CREATE cancellation request — 需要 order_id)
- POST 详情子端点（`/returns/<id>/{approve,reject,cancel,seller_response,evidence_file,dispute}` 等）
- POST cancellation 子端点（`/cancellations/<id>/{approve,reject,accept,decline}` 等）
- POST reverse 子端点（`/orders/<id>/{approve,reject,cancel,confirm_receipt,ship,handle,respond,negotiate}` 等）

`tts-erp` 对 `POST /returns` 和 `POST /cancellations` 一律返 **501 Not Implemented** + 友好说明。

**Schema 新增**（schema.sql，2 张新表）：

```sql
CREATE TABLE returns (
    return_id TEXT PRIMARY KEY,    -- TikTok "return_id" 字段（不是 "id"！）
    shop_id, order_id, return_status, return_reason, return_type, role,
    create_time, update_time, raw JSONB, synced_at
);
CREATE TABLE cancellations (
    cancel_id TEXT PRIMARY KEY,    -- TikTok "cancel_id" 字段
    shop_id, order_id, cancel_status, cancel_reason, cancel_reason_text,
    cancel_type, role, should_replenish_stock,
    create_time, update_time, raw JSONB, synced_at
);
```

**新端点**（tts_erp v1.3）：

- `POST /returns/search`       body: `{shop_id, ...filters}` → 代理
- `POST /cancellations/search` body: `{shop_id, ...filters}` → 代理
- `POST /sync/returns`         body: `{shop_id, create_time_ge?, create_time_lt?, page_size?}`
- `POST /sync/cancellations`   body: `{shop_id, create_time_ge?, create_time_lt?, page_size?}`
- `GET /db/returns?shop_id=&status=&limit=`
- `GET /db/cancellations?shop_id=&status=&limit=`

**字段名关键差异**（与 finance 模式不同）：

- `returns.search` 响应数组在 `data.return_orders`，主键是 `return_id`（不是 `id`！）
- `cancellations.search` 响应数组在 `data.cancellations`，主键是 `cancel_id`（不是 `id`）
- paging 走 query string（page_size/sort_field/sort_order），body 只放过滤条件
- `paging` 用 `next_page_token`（与 finance 一致）
- `cancel_status` 实际是 "CANCELLATION_REQUEST_COMPLETE"（自动完成的取消，buyer 主动取消会立即 complete）
- `return_status` 可能是 "AWAITING_BUYER_SHIP" / "BUYER_SHIPPED_ITEM" / "RETURN_OR_REFUND_REQUEST_COMPLETE"

**page_size 限制**（实测踩坑）：`/return_refund/202309/*/search` 端点的 `page_size` 范围是 **10-50**，
超过 50 返 `98001004 "Value Out Of Range"`。代码里 `min(max(page_size, 10), 50)` 自动夹紧。
注意：order / finance 端点的上限是 100（**不能**用 50 当默认值，TikTok 会觉得"用得不够"）。

**取消 vs 退货的业务差异**：

- 取消（cancellations）：发货前的 order 取消，由 buyer/seller 主动发起。所有 75 条记录都是 `CANCELLATION_REQUEST_COMPLETE`。
- 退货（returns）：发货后 buyer 申请退款/退货，需要走物流寄回 + 平台审核。14 条记录分布：
  - 11 `RETURN_OR_REFUND_REQUEST_COMPLETE`（仅退款，无需退货）
  - 2 `AWAITING_BUYER_SHIP`（等买家寄回）
  - 1 `BUYER_SHIPPED_ITEM`（买家已寄回，等收货）

## 还没完成 / 已知问题

### 1. 没接 webhook

TikTok Shop 支持 order 状态变化的 webhook 推送（order.created, order.paid, order.shipped 等）。
这需要公网回调（已有 cpolar 隧道）+ TikTok webhook 签名验证 + 入库逻辑。

**怎么开始**：

- 在 tts-erp 加 `POST /webhook/tiktok` 端点
- 用 `TIKTOK_APP_SECRET` 验证 `x-tts-signature` header
- 根据事件类型更新 `orders` 表
- 需要在 Partner Center 配置 webhook URL（用 cpolar 域名）

**预估时间**：1-2 小时

### 2. 没做增量同步

现在 `/sync/orders` 是全量翻页拉，page_size 上限 100。生产应该用：

```bash
# 第一次全量
curl -X POST http://127.0.0.1:9877/sync/orders \
  -d '{"shop_id": "X", "page_size": 100}'

# 之后增量（每 N 小时跑一次）
curl -X POST http://127.0.0.1:9877/sync/orders \
  -d '{"shop_id": "X", "create_time_ge": <上次同步时间>, "page_size": 100}'
```

**怎么开始**：

- 加 `last_synced_at` 时间戳到 `shops` 表
- 写个简单的 cron 调 `/sync/orders` 用 `create_time_ge`
- 或者直接在 `tts-erp` 加个 `/sync/incremental` 端点

**预估时间**：30 分钟

### 3. app_secret 暴露在聊天记录

⚠️ **app_secret 已经在多个聊天记录里出现过**。强烈建议在所有功能验证后**去 TikTok Partner Center 重置一次**。同时：

- 重置后要更新 `/home/schan/oauth-receiver/.env` 和 `/home/schan/tts-erp/.env`
- 因为 access_token 是按 app_secret+access_token 算的，app_secret 一变 → 必须全部重新走 OAuth 授权流（用 cpolar 走 `/authorize`）

### 4. DB 密码暴露

⚠️ `==REDACTED_DB_PASS==` 也在多个聊天记录里。同样建议重置。

## 调试历史（给下个 session 的备忘）

### HMAC 签名踩过的坑

1. **位置错位 (positional args)**：`build_signed_url(api_host, path, app_key, app_secret, extra_params, body, timeout)` 第 6 个是 `body` 不是 `timeout`。调用时 positional 传 `timeout` 到了 `body` 位置，导致 `canonical += 30`（int）。**修复**：全部用 keyword args。
2. **Pattern 选错**：
   - `{secret}{path}{kv}{secret}` ❌ (106001)
   - `{secret}{path}{kv}{secret}{body}` ❌ (106001)
   - `{secret}{path}{kv}{body}{secret}` ✅ (通过！)
3. **body SHA256 哈希**：试过 SHA256(body) 也不行，必须是 raw JSON
4. **URL encoding**：不要对 body 做 URL encoding，原 JSON 字符串

最终 canonical（实测通过）：

```
c90503.../order/202309/orders/searchapp_key==REDACTED_APP_KEY==shop_cipherROW_...Entimestamp1786875581{"page_size": 10}c90503...
```

### 启动服务的坑

1. **bash 转义地狱**：Windows PowerShell + Git Bash 调 `nohup ... &` 加 `disown` 经常 launch 失败
2. **env vars 没传进子 shell**：`TTS_DEBUG_SIGN=1` 写在前面但 `set -a; . .env` 在后面 → app_secret 为空
3. **解决方法**：写个 `start.py` 显式读 .env 然后 `subprocess.Popen(env=os.environ)`，比 bash 可靠
4. **最终方案**：只保留 `restart.sh`（之前还备 `start.py` 兜底，但 `restart.sh` 工作稳定后 `start.py` 已删）

**推荐启动方式**：

```bash
bash /home/schan/tts-erp/restart.sh
```

## 下个 session 优先做

1. **重置 app_secret 和 DB 密码**（安全第一）
2. 增量同步（实用）
3. 接 webhook（实用）
4. 加 `/healthz` 返回 DB 状态 + oauth-receiver 连通性
5. 把 tts-erp 跟 oauth-receiver 一起做 systemd service（需要 sudo 密码）
6. **如果用户后续要接 reject/approve 等动作**，需要单独 review 并加 confirm 双确认（这些动作会真改 TikTok 状态）
7. **如果用户后续要接 `/reverse/202309/*`**，需要先确认 TikTok 是否在 202309 中开放了 reverse logistics（目前 CDN 404）；可能要切到 fulfillment 模块

## 配置快照

```
OAUTH_RECEIVER_URL  http://127.0.0.1:9876
TIKTOK_APP_KEY      ==REDACTED_APP_KEY==
TIKTOK_APP_SECRET   ==REDACTED_APP_SECRET==  ← 重置
TIKTOK_API_HOST     https://open-api.tiktokglobalshop.com
TTS_ERP_DB_URL      postgresql://postgres:==REDACTED_DB_PASS==@127.0.0.1:5432/tts_erp  ← 改密码
TIKTOK_REDIRECT_URI http://daqiang.nat100.top/callback   (in oauth-receiver)
```

⚠️ **app_secret 和 DB 密码都已经在多个聊天记录里出现过**。强烈建议在所有功能验证后**去 TikTok Partner Center + Postgres 各自重置一次**。

## 关键命令

```bash
# 看 oauth-receiver 状态
ssh -i "C:\Users\chen\Desktop\keys\192.168.47.130@schan.txt" schan@192.168.47.130 "curl -s http://127.0.0.1:9876/healthz | python3 -m json.tool"

# 看 tts-erp 状态
ssh -i "C:\Users\chen\Desktop\keys\192.168.47.130@schan.txt" schan@192.168.47.130 "curl -s http://127.0.0.1:9877/healthz | python3 -m json.tool"

# 重启两个服务
ssh -i "C:\Users\chen\Desktop\keys\192.168.47.130@schan.txt" schan@192.168.47.130 "bash /home/schan/oauth-receiver/restart.sh && bash /home/schan/tts-erp/restart.sh"

# 端到端冒烟测试
scp "F:\MiniMax Work Result\tts-erp\test_e2e.py" schan@192.168.47.130:/tmp/test_tts_erp.py
ssh -i "..." schan@192.168.47.130 "set -a; source /home/schan/tts-erp/.env; set +a; python3 /tmp/test_tts_erp.py"

# 同步所有模块
for mod in orders statements payments returns cancellations; do
  curl -X POST "http://127.0.0.1:9877/sync/$mod" -H "Content-Type: application/json" -d '{"shop_id":"7494763368967603447","page_size":100}'
done

# 查 DB 里的退货/取消
ssh -i "..." schan@192.168.47.130 "curl -s 'http://127.0.0.1:9877/db/returns?shop_id=7494763368967603447&limit=5'"
ssh -i "..." schan@192.168.47.130 "curl -s 'http://127.0.0.1:9877/db/cancellations?shop_id=7494763368967603447&limit=5'"

# 看 cron 续期日志
ssh -i "..." schan@192.168.47.130 "tail -30 /home/schan/oauth-receiver/logs/cron-refresh.log"
```

## 2026-08-27 — Wave 4.1 (legacy retirement)

- 5 个 Handler `_sync_miaoushou_*` / `_db_list_miaoushou_*` 抽到 `tdd/miaoshou_sync.py` 模块级函数（返回 (code, body)）。
- `_invoke_legacy_sync` 改为直接调模块函数（getattr 兑底 501）。
- `tts_erp.py` 删了 Handler 类 + main() + _proxy_get()：3512 → 1691 行。文件现在是共享 helper 模块。
- AGENTS.md L189 / L371 同步更新。
- 剩余 follow-up（独立 scope）：`do_POST` 里 `from miaoshou.callbacks.router import dispatch_callback` + `_miaoshou_call_endpoint` 仍是 dead 状态。

  **2026-08-27 后续（其他 session）**: 删了 `do_POST` 里的 `from miaoshou.callbacks.router import dispatch_callback` + `_miaoshou_call_endpoint`（160 行 dead code），并删了测该 dead code 的 `test_handler_routing.py`；AGENTS.md L371 同步标记为已完成。
