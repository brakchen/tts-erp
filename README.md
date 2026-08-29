# tts-erp

> TikTok Shop Partner API 代理 + 本地持久化 + 定时同步 + 财务对账查询 · schan 服务器 · FastAPI/uvicorn（端口 9877）

## 它是干什么的

把 [TikTok Shop Order API](https://partner.tiktokshop.com/docv2/page/order-api-overview)、
[Finance / Statement API](https://partner.tiktokshop.com/docv2/page/get-statements-202309)
和 [Return / Refund / Cancellation API](https://partner.tiktokshop.com/docv2/page/return-refund-and-cancel-api-overview)
包成简单 HTTP 端点。你不用管 HMAC 签名、`x-tts-access-token` header、`shop_cipher` 放 query
还是 body、access_token 过期 —— 这些我们都处理好。

财务侧：账单内**逐交易明细**（佣金/运费/税费/实结，58 字段/条）也由 Finance API
`statement_transactions` 端点直接落库 —— 2026-08-18 起完全以接口数据为准，
不再依赖人工导出 Excel。

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
                          │  PostgreSQL    │  本地存储：全部来自 API 同步
                          │  tts_erp DB    │  13 张表
                          └────────────────┘
```

## 快速开始

```bash
# 1) 手动触发同步（一般不用——cron 每 10 分钟自动增量同步 7 类）
curl -X POST http://127.0.0.1:9877/sync/orders \
  -H "Content-Type: application/json" \
  -d '{"shop_id": "7494763368967603447", "page_size": 50}'

# 2) 查本地订单（API 同步）
curl "http://127.0.0.1:9877/db/orders?shop_id=7494763368967603447&limit=20" | jq

# 3) 查财务明细（API 同步，58 字段/条含佣金/运费/税费/实结）
curl "http://127.0.0.1:9877/db/statement_transactions?limit=20" | jq
curl "http://127.0.0.1:9877/db/statement_transactions?order_id=<订单号>" | jq

# 4) 直接调 TikTok API（不存本地）
curl -X POST "http://127.0.0.1:9877/orders/search?shop_id=7494763368967603447" \
  -H "Content-Type: application/json" \
  -d '{"order_status": "AWAITING_SHIPMENT", "page_size": 50}'

# 5) 拿订单详情
curl "http://127.0.0.1:9877/orders/5800123456789012345?shop_id=7494763368967603447" | jq
```

所有 TikTok 代理/同步端点 `?shop_id=XXX` 必填，内部用 shop_id 找 token + cipher。

**鉴权**：除 `/healthz`、`/endpoints` 外需要 `Authorization: Bearer <key>`（2026-08-17 起，当前为
shadow 观察模式；详见 `tech-doc/api-key-auth-design.md`）。key 分 readonly/readwrite/admin 三级，
管理用 `python3 api_keys.py create/list/revoke/rotate`。

## 完整端点清单

`GET /endpoints` 也可以拿到这列表。

**OAuth 透传**（原样转发到 oauth-receiver:9876）

- `GET /shops` 列出已授权 shops
- `GET /shops/<shop_id>` 单个 shop 元数据
- `GET /token/<shop_id>?reveal=1` 拿 token + cipher（明文 token，注意暴露面）

**TikTok Order API 代理**（都要 `?shop_id=X`）

- `POST /orders/search` / `POST /orders/list`
- `GET  /orders/<order_id>`（详情，成功自动落库）
- `POST /orders/<order_id>/{confirm,cancel,update_status,shipping_info,verify_shipping}` ⚠️ 写操作，直改真实店铺
- `GET  /orders/<order_id>/{tracking,tracking/get,risk,buyer,recipient}`

**TikTok Finance API 代理**

- `GET /finance/statements?shop_id=X&page_size=50&sort_field=statement_time&sort_order=DESC`（sort_field 必填）
- `GET /finance/payments?shop_id=X&page_size=50&sort_field=create_time&sort_order=DESC`

**TikTok Return/Refund API 代理**（只读）

- `POST /returns/search`       body: `{shop_id, ...}`
- `POST /cancellations/search` body: `{shop_id, ...}`
- CREATE 写端点（`POST /returns`、`/cancellations`）**不存在**——2026-08-17 起从代码删除，调用返 404

**物流追踪**

- `GET /logistics/orders/<order_id>/tracking?shop_id=X`（代理 + 自动落库）
- `POST /sync/logistics_tracking` body: `{shop_id, order_ids?|all_with_tracking?, limit?, max_per_run?}`

**本地 DB 读**

- `GET /db/orders?shop_id=&status=&limit=` ｜ `/db/orders/<id>` ｜ `/items` ｜ `/shipping`
- `GET /db/statements?shop_id=&limit=` ｜ `/db/payments?shop_id=&status=&limit=`
- `GET /db/returns?shop_id=&status=&limit=` ｜ `/db/cancellations?shop_id=&status=&limit=`
- `GET /db/logistics_tracking?...` ｜ `/db/logistics_events?order_id=&limit=`
- `GET /db/sync_log`

**财务 DB 读**（API 同步，2026-08-18 起替代 Excel 数据）

- `GET /db/statement_transactions?shop_id=&statement_id=&order_id=&type=&limit=` 账单逐交易明细（58 字段/条：佣金/运费/税费/实收/实结）

**同步**（TikTok → 本地；cron 每 10 分钟自动跑，见「定时同步」）

- `POST /sync/{orders,statements,payments,returns,cancellations,statement_transactions}` body: `{shop_id, ...时间窗/分页参数}`
- `POST /sync/logistics_tracking`
- `POST /sync/order/<order_id>` 单订单同步 —— 未移植，恒 501

**运维**

- `GET /healthz` ｜ `GET /endpoints`

## 本地数据

**一个库**：`tts_erp`（docker 容器 `postgres`，5432），public schema，**13 张表**，全部来自 API 同步。

- orders / order_items / order_shippings / payments / statements / **statement_transactions** / returns / cancellations / shops / sync_log / logistics_tracking / logistics_tracking_events / logistics_sync_targets
- `statement_transactions`：账单内逐交易明细（`/finance/202309/statements/{id}/statement_transactions`），替代 2026-08-18 删除的 Excel 财务表；佣金/运费逐项与 Excel 对齐验证过，增值税/手续费含在 fee_amount 中不单列（VN 店铺接口不拆）

## 定时同步（cron）

```cron
*/10 * * * *  run_sync_cron.sh        # 7 类 /sync/* 同步：orders/payments/statements/returns/cancellations
                                      #   /statement_transactions 走时间窗口（sync_log 上次成功 - 5min）；
                                      #   logistics_tracking 每轮追"有运单号且未到终态"的订单
30 0 * * *    SELECT cleanup_sync_log(60)   # sync_log 60 天 retention
```

token 续期归 oauth-receiver 管（每天 02:00），**tts-erp 侧不要再加续期 cron**。

## 安装 / 部署

服务已在 schan 服务器上运行：

- 代码：`/home/schan/tts-erp/`（生产代码在 `tdd/tts_erp_fastapi.py`）
- DB：`tts_erp` on `postgres` container
- Token 源：`http://127.0.0.1:9876`（oauth-receiver）

重新部署：

```bash
# 应用 schema（幂等）
cat schema.sql | docker exec -i postgres psql -U postgres -d tts_erp

# 重启（pkill 旧进程 → source .env → uvicorn 起在 9877）
bash /home/schan/tts-erp/restart.sh
```

## 配置

`.env` 文件（0600 权限）：

```bash
TTS_ERP_PORT=9877
OAUTH_RECEIVER_URL=http://127.0.0.1:9876
TIKTOK_APP_KEY=<your_app_key>
TIKTOK_APP_SECRET=<your_app_secret>
TIKTOK_API_HOST=https://open-api.tiktokglobalshop.com
TTS_ERP_DB_URL=postgresql://postgres:<pwd>@127.0.0.1:5432/tts_erp
```

`TIKTOK_APP_KEY` / `TIKTOK_APP_SECRET` 必须跟 oauth-receiver 那边的**完全一致**（HMAC 签名用的）。

## 开发方式：TDD

本项目**测试先行**（目录名 `tdd/` 即此意）。改任何业务逻辑的顺序是：

1. 先在 `tdd/test_*.py` 写/改测试（红）
2. 实现到通过（绿）
3. 重构 + 全量回归

测试约定（见 `tdd/conftest.py`）：

- **事务回滚隔离**：DB 测试每个用例跑在事务里，结束即 rollback，可安全对生产库跑
- **`TEST_%` 哨兵**：必须落库提交的数据（如 upsert 类），shop_id/txn_id 等键一律 `TEST_` 前缀，session 结束自动清理
- **Out-of-TDD-scope**（不测，文档化在 conftest docstring）：TikTok 真实响应、HMAC 被服务端接受、schema DDL 正确性、部署/限流退避等——这些靠 e2e 冒烟和生产监控兜底

## 测试与验证

```bash
cd /home/schan/tts-erp/tdd && python3 -m pytest     # 152 passed（单元/仓库/端点）
python3 /home/schan/tts-erp/final_smoke.py          # e2e 冒烟：6 类 sync + db 读 + statement_transactions + healthz
```

## 进程管理

```bash
pgrep -af "uvicorn.*tts_erp_fastapi"     # 状态
ss -tlnp | grep 9877
bash /home/schan/tts-erp/restart.sh      # 重启
tail -f /home/schan/tts-erp/logs/stderr.log
```

注意：**不要**手动启动旧的 stdlib 版 `tts_erp.py`（保留作共享库/回滚，不承担服务）。

## 调试

签名问题（`106001 invalid sign`）：

```bash
TTS_DEBUG_SIGN=1 ...   # 看 stderr.log 里的 [tts-erp-debug] canonical 串，跟 AGENTS.md 2.2 节对比
```

## 已知问题 / 边界

- **没接 webhook**：TikTok Shop 支持 order.* 事件 webhook，本服务还没接（目前靠 10 分钟轮询）
- **服务无鉴权且监听 0.0.0.0**：局域网内可调 `/token/<id>?reveal=1` 拿明文 token、可调用订单写操作端点。网段不可信时请收敛监听或加防火墙
- **Return/Refund CREATE 不接**：端点已删除（404）；要接需单独 review
- **`/reverse/202309/*` 不存在**：TikTok 202309 spec 没开放 reverse logistics 模块（HTTP 404 at CDN）
- **app_secret / DB 密码历史上在 chat 暴露过**：建议去 TikTok Partner Center + Postgres 重置一次

## 相关

- `AGENTS.md` — AI agent 操作指南（单一真理源，改代码前必读）
- `miaoshou/README.md` — 妙手集成：search_move_collect_list 字段语义、与 TikTok 数据的对应关系、实测结论
- `CHANGELOG.md` — 变更历史
- `handoff.md` — 跨 session 交接笔记
- `/home/schan/setup/tts-erp-cron-sync.md` — cron 同步安装记录

## License

Internal use.
