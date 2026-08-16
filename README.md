# tts-erp

> TikTok Shop Partner Order API + Finance API + Return/Refund API 代理 + 本地持久化 · schan 服务器

## 它是干什么的

把 [TikTok Shop Order API](https://partner.tiktokshop.com/docv2/page/order-api-overview)、
[Finance / Statement API](https://partner.tiktokshop.com/docv2/page/get-statements-202309)
和 [Return / Refund / Cancellation API](https://partner.tiktokshop.com/docv2/page/return-refund-and-cancel-api-overview)
包成简单 HTTP 端点。你不用管 HMAC 签名、`x-tts-access-token` header、`shop_cipher` 放 query
还是 body、access_token 过期 —— 这些我们都处理好。

**架构**：

```
┌────────────┐  业务 curl    ┌─────────────┐  HTTP (拿 token)  ┌────────────────┐
│ 你的 ERP   ├─────────────►│  tts-erp    ├─────────────────►│  oauth-receiver│
│ / 后台 / BI│               │   :9877     │                   │     :9876      │
└────────────┘               └──────┬──────┘                   └────────────────┘
                                  │ HMAC 签名
                                  ▼
                          ┌────────────────┐
                          │  TikTok Shop   │
                          │  Open API      │
                          └────────────────┘
                                  │
                          ┌────────────────┐
                          │  PostgreSQL    │  本地缓存（订单/对账/付款/退货/取消）
                          │  tts_erp DB    │
                          └────────────────┘
```

## 快速开始

```bash
# 1) 同步订单到本地 DB
curl -X POST http://127.0.0.1:9877/sync/orders \
  -H "Content-Type: application/json" \
  -d '{"shop_id": "7494763368967603447", "page_size": 50}'

# 2) 同步对账单 + 付款
curl -X POST http://127.0.0.1:9877/sync/statements -H "Content-Type: application/json" -d '{"shop_id": "7494763368967603447"}'
curl -X POST http://127.0.0.1:9877/sync/payments   -H "Content-Type: application/json" -d '{"shop_id": "7494763368967603447"}'

# 3) 同步退货 + 取消
curl -X POST http://127.0.0.1:9877/sync/returns       -H "Content-Type: application/json" -d '{"shop_id": "7494763368967603447"}'
curl -X POST http://127.0.0.1:9877/sync/cancellations -H "Content-Type: application/json" -d '{"shop_id": "7494763368967603447"}'

# 4) 查本地订单
curl "http://127.0.0.1:9877/db/orders?shop_id=7494763368967603447&limit=20" | jq
curl "http://127.0.0.1:9877/db/returns?shop_id=7494763368967603447&status=AWAITING_BUYER_SHIP" | jq
curl "http://127.0.0.1:9877/db/cancellations?shop_id=7494763368967603447" | jq

# 5) 直接调 TikTok API（不存本地）
curl -X POST "http://127.0.0.1:9877/orders/search?shop_id=7494763368967603447" \
  -H "Content-Type: application/json" \
  -d '{"order_status": "AWAITING_SHIPMENT", "page_size": 50}'

# 6) 拿订单详情
curl "http://127.0.0.1:9877/orders/5800123456789012345?shop_id=7494763368967603447" | jq
```

所有 `?shop_id=XXX` 必填，内部用 shop_id 找 token + cipher。

## 完整端点清单

`GET /endpoints` 也可以拿到这列表。

**OAuth 代理**
- `GET /shops` 列出已授权 shops
- `GET /shops/<shop_id>` 单个 shop 元数据
- `GET /token/<shop_id>?reveal=1` 拿 token + cipher

**TikTok Order API 代理**（都要 `?shop_id=X`）— 202309 模块是**只读**
- `POST /orders/search`
- `GET  /orders/<order_id>` （→ `/order/202309/orders?ids=<id>`）
- ❌ `POST /orders/<order_id>/{confirm,cancel,update_status,shipping_info,verify_shipping}` 一律返 **501**（202309 Order 模块只读）

**TikTok Finance API 代理**
- `GET /finance/statements?shop_id=X&page_size=50&sort_field=statement_time&sort_order=DESC` （sort_field REQUIRED）
- `GET /finance/payments?shop_id=X&page_size=50&sort_field=create_time&sort_order=DESC`

**TikTok Return/Refund API 代理**（return-refund-and-cancel-202309）— **只读** + 写入 501
- `POST /returns/search`       body: `{shop_id, ...}`   → `/return_refund/202309/returns/search`
- `POST /cancellations/search` body: `{shop_id, ...}`   → `/return_refund/202309/cancellations/search`
- ❌ `POST /returns`            → **501**（CREATE write endpoint，按用户要求不接）
- ❌ `POST /cancellations`      → **501**（同上）

**本地 DB 读**
- `GET /db/orders?shop_id=&status=&limit=`
- `GET /db/orders/<order_id>` / `/items` / `/shipping`
- `GET /db/statements?shop_id=&limit=`
- `GET /db/payments?shop_id=&status=&limit=`
- `GET /db/returns?shop_id=&status=&limit=`
- `GET /db/cancellations?shop_id=&status=&limit=`
- `GET /db/sync_log`

**同步**（TikTok → 本地）
- `POST /sync/orders`         body: `{shop_id, order_status?, create_time_ge?, create_time_lt?, page_size?}`
- `POST /sync/order/<order_id>`
- `POST /sync/statements`     body: `{shop_id, statement_time_ge?, statement_time_lt?, page_size?}`
- `POST /sync/payments`       body: `{shop_id, create_time_ge?, create_time_lt?, page_size?}`
- `POST /sync/returns`        body: `{shop_id, create_time_ge?, create_time_lt?, page_size?}`
- `POST /sync/cancellations`  body: `{shop_id, create_time_ge?, create_time_lt?, page_size?}`

**运维**
- `GET /healthz`
- `GET /endpoints`

## 安装

服务已经在 schan 服务器上部署好了：
- 代码: `/home/schan/tts-erp/`
- DB: `tts_erp` on `postgres` container (9 张表)
- Token 源: `http://127.0.0.1:9876` (oauth-receiver)

如果需要重新部署：
```bash
# 部署代码
scp -r F:\MiniMax Work Result\tts-erp\* schan@192.168.47.130:/home/schan/tts-erp/

# 部署 schema
cat schema.sql | docker exec -i postgres psql -U postgres -d tts_erp

# 启动
bash /home/schan/tts-erp/restart.sh
```

## 配置

`.env` 文件（0600 权限）：

```bash
TTS_ERP_HOST=0.0.0.0
TTS_ERP_PORT=9877
OAUTH_RECEIVER_URL=http://127.0.0.1:9876
TIKTOK_APP_KEY=<your_app_key>
TIKTOK_APP_SECRET=<your_app_secret>
TIKTOK_API_HOST=https://open-api.tiktokglobalshop.com
TTS_ERP_DB_URL=postgresql://postgres:<pwd>@127.0.0.1:5432/tts_erp
```

`TIKTOK_APP_KEY` 和 `TIKTOK_APP_SECRET` 必须跟 oauth-receiver 那边的 `TIKTOK_APP_KEY/SECRET` **完全一致**（HMAC 签名用的）。

## 进程管理

```bash
# 状态
pgrep -af tts_erp.py
ss -tlnp | grep 9877
curl -s http://127.0.0.1:9877/healthz | jq

# 重启
bash /home/schan/tts-erp/restart.sh

# 看日志
tail -f /home/schan/tts-erp/logs/stderr.log
```

## 调试

签名问题（`106001 invalid sign`）的话：
```bash
TTS_DEBUG_SIGN=1 nohup python3 -u tts_erp.py ... 
# 然后看 stderr.log 里的 [tts-erp-debug] 行
```

会打印 canonical string，跟 handoff.md 2.2 节对比。

## 已知问题

- **没接 webhook**：TikTok Shop 支持 order.* 事件 webhook，本服务还没接。
- **没做增量同步**：现在 `/sync/orders` 是全量（按 page_size 限），生产应该用 `create_time_ge` 增量。
- **app_secret / DB 密码已在 chat 暴露**：建议去 TikTok Partner Center + Postgres 重置一次。
- **Return/Refund CREATE 没接**：按用户要求，reject/approve/accept 等动作一律不接；
  `POST /returns` 和 `POST /cancellations`（不带 /search）返 501。
- **`/reverse/202309/*` 不存在**：TikTok 202309 spec 没开放 reverse logistics 模块（HTTP 404 at CDN）。

## 相关

- `/home/schan/setup/oauth-receiver.md` — 上游 token 服务说明
- `/home/schan/setup/tts-erp.md` — tts-erp 服务说明
- `AGENTS.md` — AI agent 操作指南（在这个目录）
- `handoff.md` — 跨 session 交接笔记（在这个目录）
- `test_e2e.py` / `verify_db.py` — 端到端冒烟测试

## License

Internal use.
