# AGENTS.md — tts-erp

> AI agent 操作指南 · 单一真理源 · 改任何东西之前先读这文件

## 1. 服务是什么

`tts-erp` 是 TikTok Shop Partner **Order API + Finance API + Return/Refund API** 的 HTTP 代理 + 本地持久化服务。

- **上游**：`oauth-receiver` (`:9876`)，专门管 token 加密 + 续期
- **下游**：TikTok Shop Open API (`open-api.tiktokglobalshop.com`)
- **存储**：PostgreSQL `tts_erp` 数据库（`postgres` 容器，端口 5432）
- **对外端口**：`9877`
- **公网域名**（用户已配 NAT 穿透）：`daqiang.nat100.top` —— **已 strip 9877 端口**，访问任何 endpoint 用 `http://daqiang.nat100.top/<path>` 而不是 `http://daqiang.nat100.top:9877/<path>`
  - 给用户的 deploy URL / TikTok 填的 redirect URL / 文档里的 curl 示例 — 一律 strip 端口
  - 业务代码 / curl 跑本地用 `http://127.0.0.1:9877/...` 仍然带端口（直连本机）

代理 + 持久化，业务代码**完全不用关心**：

- HMAC-SHA256 签名
- `x-tts-access-token` header
- `shop_cipher` 放 query 哪个位置
- 翻页 / `next_page_token`
- 过期 token 续期

业务代码直接 `curl http://127.0.0.1:9877/...` 就行。

## 2. 必读 — 关键设计决策

### 2.1 Token 来源**不**直读 PG，必须走 oauth-receiver

```python
# ✓ 正确
tok = fetch_token(shop_id, reveal=True)  # HTTP call to oauth-receiver
access_token = tok["access_token"]
shop_cipher = tok["shop_cipher"]

# ✗ 错误
# conn.execute("SELECT access_token_encrypted FROM oauth_tokens WHERE shop_id=...")  # NO
```

**为什么**：oauth-receiver 是 auth 的 single source of truth。tts-erp 不持有密钥、不解密密文、不和加密层耦合。

### 2.2 HMAC 签名（最常出错）

```python
# canonical for POST:
#   {app_secret}{path}{app_key}{value}{shop_cipher}{value}{timestamp}{value}{body}{app_secret}
# canonical for GET (no body):
#   {app_secret}{path}{app_key}{value}{shop_cipher}{value}{timestamp}{value}{app_secret}

# 关键点：
# - keys 按字母序排（app_key < shop_cipher < timestamp）
# - body 必须是 json.dumps(..., ensure_ascii=False) 的**原始字符串**
# - body 在 KV 串之后、结尾 secret 之前
# - 千！万！不！要 URL-encode body

sign = hmac.new(secret.encode(), canonical.encode(), hashlib.sha256).hexdigest()
```

错误示例（**实测都会 106001 invalid sign**）：

- 把 body 放在 canonical 最后（secret 之后）
- 用 SHA256(body) 代替原始 body
- 不放 body 也不放结尾 secret
- URL-encode body

### 2.3 shop_cipher 永远在 query

不论 GET/POST，`shop_cipher` 都是 query 参数：

```
GET  /order/202309/orders/<id>?app_key=...&shop_cipher=...&timestamp=...&sign=...
POST /order/202309/orders/search?app_key=...&shop_cipher=...&timestamp=...&sign=...
body: {...}
```

### 2.4 每个 TikTok 请求都要带 shop_id

我们的代理端点统一要求 `?shop_id=XXX`：

```bash
curl "http://127.0.0.1:9877/orders/search?shop_id=7494763368967603447" -d '...'
```

内部会自动用 shop_id 拿 token + cipher，签名，发请求。

### 2.5 API key 鉴权（2026-08-17 起，设计文档 `tech-doc/api-key-auth-design.md`）

- 除 `/healthz`、`/endpoints` 外所有端点需要 `Authorization: Bearer <key>`（当前为 shadow 模式：放行但记 would-deny 日志；enforce 后无 key 401、角色不够 403）
- 三级角色：`readonly`（/db/*、GET 代理、search）< `readwrite`（+ /sync/*、订单写操作）< `admin`（+ /token/*）
- key 管理走 `python3 api_keys.py create/list/revoke/rotate`；**库里只有 SHA-256 哈希**，完整 key 只在创建时打印一次
- 模式开关：`.env TTS_ERP_AUTH_MODE=off|shadow|enforce`；cron/脚本用 `.env TTS_ERP_SERVICE_KEY`
- 新加端点时**必须**在 `tdd/auth.py` 的 `required_role()` 里分类（未匹配路径默认按 admin 拦截）

## 3. 端点速查

| 端点 | 用途 |
| ----------------------------------------------- | ------------------------------- |
| `GET /healthz` | 健康检查（`oauth_receiver.token_count` 2026-08-25 修复：之前用 in-memory deque 永远 0，现走 `SELECT COUNT(*) FROM oauth_tokens`） |
| `GET /ads-monitor` | TikTok OAuth 授权回跳落地页（公开，无 auth） |
| `GET /endpoints` | 完整端点清单 |
| ~~`GET /shops`~~ | **404**（Wave 3 Slice 2 2026-08-18 删除，改走 `oauth_receiver_core.db_list_shops()` in-process） |
| ~~`GET /shops/<shop_id>`~~ | **404**（同上，Wave 3 Slice 2 删） |
| ~~`GET /token/<shop_id>?reveal=1`~~ | **404**（同上，Wave 3 Slice 2 删） |
| `POST /admin/shops/backfill` | **2026-08-25 新增** —— admin 角色；从 `oauth_tokens` 幂等回写 `tts_erp.shops` |
| `POST /orders/search?shop_id=X` | 搜索订单 |
| `POST /orders/list?shop_id=X` | 订单列表 |
| `GET /orders/<id>?shop_id=X` | 订单详情 |
| `POST /orders/<id>/confirm?shop_id=X` | 确认发货 |
| `POST /orders/<id>/cancel?shop_id=X` | 取消订单 |
| `POST /orders/<id>/update_status?shop_id=X` | 更新状态 |
| `POST /orders/<id>/shipping_info?shop_id=X` | 添加物流 |
| `POST /orders/<id>/verify_shipping?shop_id=X` | 校验物流 |
| `GET /orders/<id>/tracking?shop_id=X` | 物流追踪 |
| `GET /orders/<id>/tracking/get?shop_id=X` | 物流追踪详情 |
| `GET /orders/<id>/risk?shop_id=X` | 风控检查 |
| `GET /orders/<id>/buyer?shop_id=X` | 买家信息 |
| `GET /orders/<id>/recipient?shop_id=X` | 收货地址 |
| `GET /logistics/orders/<id>/tracking?shop_id=X` | 物流追踪聚合视图（区别于 `/orders/<id>/tracking`：聚合 `logistics_tracking` + `logistics_events`） |
| `GET /finance/statements?shop_id=X&page_size=50&sort_field=statement_time&sort_order=DESC` | 对账单列表（202309 spec 唯一可用 Finance 端点） |
| `GET /finance/payments?shop_id=X&page_size=50&sort_field=create_time&sort_order=DESC` | 付款记录列表 |
| `POST /returns/search`     body: `{shop_id, ...filters}` | 退货/退款列表（→ `/return_refund/202309/returns/search`） |
| `POST /cancellations/search` body: `{shop_id, ...filters}` | 取消列表（→ `/return_refund/202309/cancellations/search`） |
| `GET /db/orders?shop_id=X&status=&limit=` | 本地 DB 订单列表 |
| `GET /db/orders/<id>` | 本地 DB 单订单 |
| `GET /db/orders/<id>/items` | 本地 DB 订单商品 |
| `GET /db/orders/<id>/shipping` | 本地 DB 物流信息 |
| `GET /db/statements?shop_id=X&limit=` | 本地 DB 对账单 |
| `GET /db/payments?shop_id=X&status=&limit=` | 本地 DB 付款记录 |
| `GET /db/returns?shop_id=X&status=&limit=` | 本地 DB 退货记录 |
| `GET /db/returns/<id>` | 本地 DB 单退货详情（带 `refund_amount` 计算字段，源 `raw->'refund'->>'refund_total'`） |
| `GET /db/cancellations?shop_id=X&status=&limit=` | 本地 DB 取消记录 |
| `GET /db/sync_log` | 同步历史 |
| `GET /db/statement_transactions?shop_id=&statement_id=&order_id=&type=&limit=` | 账单逐交易明细（58 字段，替代已删的 Excel 财务表） |
| `GET /db/logistics_tracking` | 本地 DB 物流追踪表（按 `order_id` 维度汇总） |
| `GET /db/logistics_events` | 本地 DB 物流事件表（原始逐事件流，按 `update_time_millis` 排序） |
| `POST /sync/orders` | body: `{shop_id, order_status?, create_time_ge?, create_time_lt?, page_size?}` |
| `POST /sync/order/<id>` | 单订单详情同步 |
| `POST /sync/statements` | body: `{shop_id, statement_time_ge?, statement_time_lt?, page_size?}` |
| `POST /sync/payments` | body: `{shop_id, create_time_ge?, create_time_lt?, page_size?}` |
| `POST /sync/returns` | body: `{shop_id, create_time_ge?, create_time_lt?, page_size?}` |
| `POST /sync/cancellations` | body: `{shop_id, create_time_ge?, create_time_lt?, page_size?}` |
| `POST /sync/logistics_tracking` | body: `{shop_id, order_ids?, all_with_tracking?, limit?, max_per_run?}`（cron 每 10 分钟自动追活跃运单） |
| `POST /sync/statement_transactions` | body: `{shop_id, statement_ids?, statement_time_ge?, statement_time_lt?, page_size?}`（账单逐交易明细） |

## 4. 改代码时的 do / don't

### DO

- **TDD：先写/改 `tdd/test_*.py`，再实现到通过**；约定见 `tdd/conftest.py`（事务回滚隔离、`TEST_%` 哨兵）
- 改完调 `bash /home/schan/tts-erp/restart.sh` 验证 healthz 200
- 改完跑 `python3 /tmp/test_tts_erp.py` (test_e2e.py 副本) 端到端验证
- 看 `logs/stderr.log` 抓 traceback
- 用 `TTS_DEBUG_SIGN=1` env var 看 canonical string 排查签名问题
- 改 schema 走 schema_tts_erp.sql / schema_oauth.sql（按库拆分），`IF NOT EXISTS` 兼容老库
- **优先复用成熟开源组件**：GitHub 上有维护活跃、star 数高（成熟领域 ≥ 5k、新兴领域 ≥ 1k）、license 友好的现成方案时，**优先用**，不要裸写 / 自己强制实现 —— 典型场景：HTTP 代理 / 网关、限流、熔断、retry/backoff、分布式锁、定时任务、对象存储 SDK、消息队列、数据库迁移、auth/JWT、参数校验、structlog 类日志、metrics/prom client 等。理由：(a) 现成组件已踩过生产坑、坑更少；(b) 维护/升级是社区分摊；(c) 业务代码更聚焦 domain logic。**评估开源时的护栏**：(a) 引入前查最后一次 release 是否在 1 年内 + issues 关闭率 ≥ 80%；(b) 优先选有公司/组织背书（如 Starlette/FastAPI/httpx/structlog/sqlalchemy/alembic/pydantic 这类），避免个人小项目单点故障；(c) 真没有合适开源时再考虑自研，自研时要留出能替换的 seam（接口层、依赖注入）。

### DON'T

- ❌ 不要在 **任何 service 文件里**（`tts_erp.py` legacy / `tdd/tts_erp_fastapi.py` 当前 / cron 脚本）直连 PG `oauth_tokens` 表（**只能**走 oauth-receiver HTTP API）
- ❌ 不要在 .env 里写 app_secret 给客户端调用者（明文暴露，app_secret 应只服务自己用）
- ❌ 不要在 canonical 里 URL-encode body（用 raw JSON 字符串）
- ❌ 不要改 `_db_load_token` 直接返回（它本来就是 oauth-receiver 的职责）
- ❌ 不要假设 `code: 0` 是唯一的 success —— TikTok 也会返回 `code: 105005` (scope 缺失) `code: 36009004` (字段缺失) 等
- ❌ **不要**接 `POST /returns` / `POST /cancellations`（CREATE write endpoint，会在真实店铺创建退货/取消单）。
     这两个端点 2026-08-17 起已从代码中**整个删除**（不再返回 501，而是无路由 404）。如果以后要接，单独 review。

## 5. 常见 bug + 修复

| 症状                                | 原因                                  | 修复                                          |
|-------------------------------------|---------------------------------------|-----------------------------------------------|
| `106001 invalid sign`               | 签名格式错（最常见）                  | 看 `TTS_DEBUG_SIGN=1` 输出的 canonical 串，对比 2.2 节 |
| `105005 Access denied`              | app 没勾对应 scope                    | Partner Center 改 app scope + 重新授权         |
| `36009004 PageSize is required`     | body 字段名/格式错                    | 查 TikTok API 文档的 Request Body 章节         |
| `/db/orders` 返回 500              | TTS_ERP_DB_URL 没配                   | `.env` 里加 TTS_ERP_DB_URL                    |
| `/token/<id>` 返 404              | oauth-receiver 里没这个 shop          | 走一次 /authorize + /callback                 |
| `TTS_ERP_DB_URL not configured`     | 同上                                  | 配 .env                                       |
| `psycopg.OperationalError`         | PG 容器 down / 网络不通                | `docker exec postgres pg_isready`              |
| 物流汇总 first/last 写反、last_description 恒 "Order placed." | TikTok tracking 列表是**最新事件在前**，旧代码按列表位置取首尾（2026-08-17 已修为按 update_time_millis 排序） | 重跑 `POST /sync/logistics_tracking` 刷新；cron 已接入每 10 分钟自动追 |
| 物流数据多日不更新                  | `/sync/logistics_tracking` 之前不在 sync_cron 的 SYNC_PLANS 里（2026-08-17 已接入） | 手动触发：`curl -X POST :9877/sync/logistics_tracking -d '{"shop_id":"X","all_with_tracking":true}'` |
| `/healthz` 报 `oauth_receiver.token_count: 0` 但 `oauth_tokens` 表有行 | healthz 用了 `len(oc._token_history)`（进程内 deque，重启后为 0），不是 DB 行数（2026-08-25 修复为 `SELECT COUNT(*)`） | 已修；部署后新版本应自动报真实数。不需手动修复 |
| `tts_erp.shops` 表为空但 `oauth_receiver.oauth_tokens` 有 shop | FastAPI 接管订单路由后未继承 legacy `_proxy_order` 的 `persist_shop` 调用（2026-08-25 修复） | 重启服务即可，startup lifespan 会自动 backfill。手动触发：`curl -X POST -H "Authorization: Bearer <admin_key>" :9877/admin/shops/backfill` |

## 6. 文件清单

| 路径 | 用途 |
| ------------------------------------------------- | -------------------------------------------- |
| **`tdd/tts_erp_fastapi.py`** | **主服务（FastAPI app，systemd 启动的实际服务，监听 9877）** |
| `tts_erp.py` | **legacy** stdlib `BaseHTTPRequestHandler` 服务（迁移中，**当前未运行**，只保留给 cron 脚本 `import` 用；不要在这里加新代码） |
| `tts_signing.py` | HMAC 签名 + HTTP 客户端（**可独立复用**） |
| `tdd/auth.py` | API key 鉴权中间件（2026-08-17，设计见 `tech-doc/api-key-auth-design.md`） |
| `tdd/rate_limit.py` | sliding-window 限流中间件（key-id 桶，默认 100/min） |
| `tdd/conftest.py` | pytest fixtures：事务回滚隔离 + `TEST_%` 哨兵 |
| `tdd/domain.py` / `tdd/http_client.py` / `tdd/repositories.py` / `tdd/pg_repositories.py` / `tdd/token_provider.py` / `tdd/tts_business.py` | 业务层 + 数据访问层（FastAPI handler 依赖注入） |
| **`miaoshou/`** | 妙手 SDK 包（独立模块；FastAPI 路由代理调用，2026-08-24 迁完） |
| `miaoshou/miaoshou_client.py` / `miaoshou/miaoshou_erp_client.py` / `miaoshou/miaoshou_signing.py` | 出站客户端 + MD5/HMAC 签名（`_call()` 走 `safe_http_post_json` helper，scheme 白名单防 SSRF） |
| `miaoshou/endpoints/` | 12 个出站 endpoint 模块（按 domain 划分：orders/fees/refunds/arbitrations/closes/complaints/queries/accounts/products/logistics/aftersales/tests） |
| `miaoshou/callbacks/router.py` / `miaoshou/callbacks/payloads.py` | 18 个回调 node + `dispatch_callback()` |
| `schema_tts_erp.sql` / `schema_oauth.sql` | PG 表结构（按库拆分，2026-08-27 Wave 2；`python3 scripts/regen_schema.py` 重新生成） |
| `api_keys.py` | API key 管理 CLI（create/list/revoke/rotate） |
| `sync_cron.py` | 同步 cron 主入口（订单/对账/付款/退货/取消/物流），由 `run_sync_cron.sh` 调度 |
| `.env` | 配置（0600，含 app_key/secret/DB URL） |
| `restart.sh` | 重启脚本（内部走 `systemctl --user restart tts-erp.service`） |
| `setup/tts-erp.md` | 用户向 setup 文档（服务介绍） |
| **`tech-doc/`** | 设计文档目录（api-key-auth-design / external-api 等） |
| `AGENTS.md` | 本文件 —— AI agent 操作指南 |
| `README.md` | 人类使用说明 |
| `handoff.md` | 跨 session 交接笔记 |
| `CHANGELOG.md` | 变更日志（按日期分批记录重要变更） |
| `tests/test_e2e.py` / `tests/test_e2e_finance.py` | 端到端冒烟测试（scp 到 server 后跑） |
| `tests/miaoshou/` | miaoshou SDK 单测（96 个 test，覆盖 signing / client / callbacks / handler routing / 36 出站 endpoint） |

## 7. 部署 / 启动

```bash
# 一次性
ssh schan@192.168.47.130 "mkdir -p /home/schan/tts-erp/{logs,setup,tests}"

# 部署
scp F:\path\to\tts-erp\* schan@192.168.47.130:/home/schan/tts-erp/
ssh schan@192.168.47.130 "chmod 600 /home/schan/tts-erp/.env && chmod +x /home/schan/tts-erp/restart.sh"

# PG schema (幂等)
cat schema_tts_erp.sql | docker exec -i postgres psql -U postgres -d tts_erp

# 启动（systemd --user 托管，开机自启；restart.sh 内部走 systemctl --user restart）
ssh schan@192.168.47.130 "bash /home/schan/tts-erp/restart.sh"
```

## 7.1 进程托管（2026-08-18 起）

两个服务都由 **systemd user 单元**托管（`~/.config/systemd/user/`，`Linger=yes` 已开，开机自启，无需登录）：

- `tts-erp.service` — `uvicorn tts_erp_fastapi:app`（cwd `tdd/`），`EnvironmentFile=.env`，`After=oauth-receiver.service`
- `oauth-receiver.service` — 见 oauth-receiver 的 setup 文档

```bash
systemctl --user status tts-erp.service      # 状态
systemctl --user restart tts-erp.service     # 重启（= restart.sh）
journalctl --user -u tts-erp -n 50           # systemd 日志（业务日志仍看 logs/）
```

## 8. Token 续期 cron

`oauth-receiver` 已经配了 cron（`0 2 * * *`）每天凌晨 2 点调 `/token/<shop_id>/refresh`。
**不要**在 tts-erp 这边再加一个，tts-erp 永远不该主动续期 token（不归它管）。

验证过的真实配置（2026-08-27 crontab -l）：续期归 `~/oauth-receiver/refresh_tokens.sh`
管，打的是**独立** oauth-receiver 服务（:9876，`~/oauth-receiver/`，有自己的
`/token/<id>/refresh` 路由）——与本仓库 `tdd/oauth_receiver_core.py` 的 in-process
副本是两回事。本仓的 `refresh_shop_token()` 无生产调用方，属预期。

## 9. External API Guide (2026-08-20)

The FastAPI service at `:9877` exposes **stable external API contracts** for
clients (dashboards, BI tools, internal apps) to query orders, refunds, and
logistics. The full contract — auth, rate limiting, CORS, pagination, every
endpoint's schema and curl examples — lives in:

**[`tech-doc/external-api.md`](tech-doc/external-api.md)** — read this before
adding or changing any external-facing endpoint.

### 9.1 Key facts an agent must remember

- **Auth**: `Authorization: Bearer <key>` **or** `X-API-Key: <key>`.
  Default mode is `enforce` (since 2026-08-20). Keys are stored as
  SHA-256 hashes only; the plaintext is shown ONCE on creation.
  Roles: `readonly` < `readwrite` < `admin`.
- **Rate limit**: 100 req/min/key, sliding window. Override via env
  `TTS_ERP_RATE_LIMIT_PER_MIN=…`. 429 + Retry-After on overflow.
- **CORS**: default **deny** (empty allow-origin list). Set
  `TTS_ERP_CORS_ALLOW_ORIGINS` to a comma-list of explicit origins, or
  the literal token `wildcard` for dev/internal deploys.
- **Pagination**: opaque base64 cursors on `/db/orders` and `/db/returns`.
  `limit` is 1..500 (default 50). `next_cursor` is null on the last page.
- **Timestamps**: query params are **epoch seconds** (BIGINT in DB);
  responses include matching `_iso` fields in UTC ISO-8601.
- **Refunds**: `GET /db/returns` and `GET /db/returns/{id}` both expose a
  computed `refund_amount` field derived from
  `raw->'refund_amount'->>'refund_total'` (TikTok 202309 spec — **note the
  nested object**, not `raw->'refund'`; corrected 2026-08-20) plus
  `refund_currency` from `raw->'refund_amount'->>'currency'`. Both are
  **NULL** when the raw JSON has no `refund_amount` object (happens for
  AWAITING returns, not yet closed).

### 9.2 Creating an external API key

```bash
python3 api_keys.py create --role readonly --name "external-orders-reader"
python3 api_keys.py list
python3 api_keys.py disable --key-prefix "ttserp_ro_…"
```

The plaintext key is printed ONCE — store it before the prompt scrolls away.

### 9.3 Middleware order (do not change without thought)

FastAPI `add_middleware` wraps in reverse, so add order = innermost-first.
Current order in `tts_erp_fastapi.py`:

1. `RateLimitMiddleware` (innermost requested layer)
2. `AuthMiddleware`
3. `CORSMiddleware` (outermost)

Auth runs BEFORE RateLimit — so the rate limiter can bucket by key. If
you add a new middleware, place it accordingly; do not put auth at the
outermost or rate limiting breaks (key_id will be None).

### 9.4 Validation harness

`/tmp/verify_external_api.sh` (or re-run from scratch with the script in
this repo's tooling) exercises:

- 401 / 200 / 403 transitions
- CORS preflight
- Time-range filtering
- Cursor pagination (next_cursor + invalid cursor → 400)
- Rate limit burst (110 reqs → 100×200 + 10×429)
- `/db/returns/{id}` detail + `include_raw=false`

Run after any change to `tts_erp_fastapi.py`, `auth.py`, or `rate_limit.py`.

### 9.5 What is NOT external-stable

These endpoints (and their semantics) may break without notice:

- `POST /sync/*` — internal cron-driven data ingest
- `POST /orders/*`, `GET /finance/*` — TikTok API proxy
- `GET /token/{shop_id}` — admin-only token reveal
- `GET /db/sync_log` — in-memory mirror
- `GET /miaoshou/*` — Wanshifu/Miaoshou SDK proxy (separate domain)

External clients should NOT depend on these.

The full table (with role requirements + v1 status for every endpoint)
is at the bottom of [`tech-doc/external-api.md`](tech-doc/external-api.md)
under **Stability matrix**.

## 10. 妙手开放平台（apifox fd54e57e-9b98-4c34-bada-306221c39e68）

apifox 文档标题“妙手开放平台”，实际底层 endpoint 指向 `openapi.wanshifu.com`。
集成代码在 **`miaoshou/`** 下（独立 SDK 包，`miaoshou_client.py` + `miaoshou_erp_client.py` + `miaoshou_signing.py`，**不**单独起服务，走 tts_erp 的 :9877）。

### 10.1 接入

- 申请：user.wanshifu.com（生产）/ test-user.wanshifu.com（测试）→ 账号安全 → 管理授权 → 新增授权 → 拿 `licenseId` + `companySecret`。
- 配置：写到 tts-erp `.env`（`MIAOSHOU_LICENSE_ID` / `MIAOSHOU_COMPANY_SECRET` / `MIAOSHOU_ENV` / `MIAOSHOU_HTTP_TIMEOUT`）。
- SDK：`from miaoshou import MiaoshouClient, MiaoshouErpClient`；也可直接 `curl POST :9877/miaoshou/<domain>/<method>`。
- 调试：`MIAOSHOU_DEBUG_SIGN=1` 在 stderr 打 canonical + sign。

### 10.2 路由

路由全部已迁到 FastAPI（`tdd/tts_erp_fastapi.py`）。生产 :9877 直接可用。

- `POST /miaoshou/<domain>/<method>` body 转 SDK 方法 `**kwargs`（36 个出站接口，domain ∈ orders/fees/refunds/arbitrations/closes/complaints/queries/accounts/products/logistics/aftersales/tests）
- `POST /miaoshou/callback/<node-alias>` 18 个回调节器（每个一个 node-alias，按 orderStatus 字段自动派发）
- `POST /miaoshou/callback/all` 按 orderStatus 字段自动选 model

错误处理（统一在 `_miaoshou_outbound_call` / `dispatch_callback`）：

- 未支持的 domain / method → 404 + supported 列表
- body 字段名与 SDK 方法签名不符（`TypeError`） → 400 + 提示匹配 SDK 签名
- 上游 MiaoshouApiError（业务 code != 200） → 502 + 透传 code/message/data（不静默吞错）
- 网络错误（URLError） → 502
- 未预期异常 → 500 + 日志

Auth: admin role（middleware 已在 `tdd/auth.py` `required_role` 把 `/miaoshou/*` 默认归 admin）。

测试覆盖：`tdd/test_miaoshou_routes.py`（11 passed + 1 skipped因 Mock auto-create child attr 限制，未知 method 路径靠生产 MiaoshouClient 真实 class 行为覆盖）。

⚠️ **Legacy cleanup pending**: `tts_erp.py` 里 `do_POST` 还保留了 `from miaoshou.callbacks.router import dispatch_callback`（已修对路径名，不再 `wanshifu`）和 `_miaoshou_call_endpoint` 这些 **已 dead** 的处理器（FastAPI 接管后不会被请求触达）。建议 follow-up 任务：删 legacy 处理代码 + 修可能重复的 `_sync_miaoshou_*` / `_db_list_miaoshou_*`（这些属于 MiaoshouErpClient sync 路线，FastAPI 尚未迁移，2026-08-17 批次承诺的 collect_box / price_template / move_collect_tasks sync 仍缺 `persist_*` 函数 — 与本次任务正交，独立 scope）。

### 10.3 签名（apifox doc-824327）

```text
busData = base64(json.dumps(business_params, ensure_ascii=False))
sign    = MD5(busData + companySecret).upper()
```

请求 envelope 含 `licenseId / companySecret / sign / busData / timestamp`（毫秒）。
签名实现见 `miaoshou/miaoshou_signing.py`（**没有独立的 `SIGNING.md` 文件**，所有签名规范都在 AGENTS.md 本节），锁定签名向量在 `tests/miaoshou/test_signing.py::test_build_sign_doc_824327_vector`。

### 10.4 测试

```shell
.venv/bin/pytest tests/miaoshou/ -q
```

**96 个 test function**（15 个文件：`test_signing` / `test_client` / `test_callbacks` / `test_handler_routing` / `test_endpoints_happy` / `test_sync_routes` / `test_sync_shops` / `test_bug_fixes` / `test_erp_bugfixes` / `test_regression_guards` / `test_round2_bugfixes` / `test_round3_bugfixes` / `test_round4_ensure_ascii` / `test_round5_retry_edge_cases` / `test_round6_signature_per_attempt`），覆盖 signing / client / callbacks / handler routing / 36 出站 endpoint。
