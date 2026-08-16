# AGENTS.md — tts-erp

> AI agent 操作指南 · 单一真理源 · 改任何东西之前先读这文件

## 1. 服务是什么

`tts-erp` 是 TikTok Shop Partner **Order API + Finance API + Return/Refund API** 的 HTTP 代理 + 本地持久化服务。

- **上游**：`oauth-receiver` (`:9876`)，专门管 token 加密 + 续期
- **下游**：TikTok Shop Open API (`open-api.tiktokglobalshop.com`)
- **存储**：PostgreSQL `tts_erp` 数据库（`postgres` 容器，端口 5432）
- **对外端口**：`9877`

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

## 3. 端点速查

| 端点                                          | 用途                          |
|-----------------------------------------------|-------------------------------|
| `GET /healthz`                                 | 健康检查                      |
| `GET /endpoints`                               | 完整端点清单                  |
| `GET /shops`                                   | 列出已授权 shops              |
| `GET /shops/<shop_id>`                         | 单个 shop 元数据              |
| `GET /token/<shop_id>?reveal=1`                | 拿 token + cipher (代理)     |
| `POST /orders/search?shop_id=X`                | 搜索订单                      |
| `POST /orders/list?shop_id=X`                  | 订单列表                      |
| `GET /orders/<id>?shop_id=X`                   | 订单详情                      |
| `POST /orders/<id>/confirm?shop_id=X`          | 确认发货                      |
| `POST /orders/<id>/cancel?shop_id=X`           | 取消订单                      |
| `POST /orders/<id>/update_status?shop_id=X`    | 更新状态                      |
| `POST /orders/<id>/shipping_info?shop_id=X`    | 添加物流                      |
| `POST /orders/<id>/verify_shipping?shop_id=X`  | 校验物流                      |
| `GET /orders/<id>/tracking?shop_id=X`           | 物流追踪                      |
| `GET /orders/<id>/tracking/get?shop_id=X`       | 物流追踪详情                  |
| `GET /orders/<id>/risk?shop_id=X`               | 风控检查                      |
| `GET /orders/<id>/buyer?shop_id=X`              | 买家信息                      |
| `GET /orders/<id>/recipient?shop_id=X`          | 收货地址                      |
| `GET /finance/statements?shop_id=X&page_size=50&sort_field=statement_time&sort_order=DESC` | 对账单列表（202309 spec 唯一可用 Finance 端点）|
| `GET /finance/payments?shop_id=X&page_size=50&sort_field=create_time&sort_order=DESC` | 付款记录列表 |
| `POST /returns/search`     body: `{shop_id, ...filters}` | 退货/退款列表（→ `/return_refund/202309/returns/search`）|
| `POST /cancellations/search` body: `{shop_id, ...filters}` | 取消列表（→ `/return_refund/202309/cancellations/search`）|
| `POST /returns` (CREATE)  | **→ 501**（按用户要求不接，拒绝 reject/approve/accept 等）|
| `POST /cancellations` (CREATE) | **→ 501**（同上）|
| `GET /db/orders?shop_id=X&status=&limit=`       | 本地 DB 订单列表              |
| `GET /db/orders/<id>`                          | 本地 DB 单订单                |
| `GET /db/orders/<id>/items`                    | 本地 DB 订单商品              |
| `GET /db/orders/<id>/shipping`                 | 本地 DB 物流信息              |
| `GET /db/statements?shop_id=X&limit=`           | 本地 DB 对账单                |
| `GET /db/payments?shop_id=X&status=&limit=`     | 本地 DB 付款记录              |
| `GET /db/returns?shop_id=X&status=&limit=`      | 本地 DB 退货记录              |
| `GET /db/cancellations?shop_id=X&status=&limit=` | 本地 DB 取消记录            |
| `GET /db/sync_log`                             | 同步历史                      |
| `POST /sync/orders`                            body: `{shop_id, order_status?, create_time_ge?, create_time_lt?, page_size?}` |
| `POST /sync/order/<id>`                        | 单订单详情同步                |
| `POST /sync/statements`                        body: `{shop_id, statement_time_ge?, statement_time_lt?, page_size?}` |
| `POST /sync/payments`                          body: `{shop_id, create_time_ge?, create_time_lt?, page_size?}` |
| `POST /sync/returns`                           body: `{shop_id, create_time_ge?, create_time_lt?, page_size?}` |
| `POST /sync/cancellations`                     body: `{shop_id, create_time_ge?, create_time_lt?, page_size?}` |

## 4. 改代码时的 do / don't

### DO
- 改完调 `bash /home/schan/tts-erp/restart.sh` 验证 healthz 200
- 改完跑 `python3 /tmp/test_tts_erp.py` (test_e2e.py 副本) 端到端验证
- 看 `logs/stderr.log` 抓 traceback
- 用 `TTS_DEBUG_SIGN=1` env var 看 canonical string 排查签名问题
- 改 schema 走 schema.sql，`IF NOT EXISTS` 兼容老库

### DON'T
- ❌ 不要在 tts_erp.py 里直连 PG `oauth_tokens` 表（**只能**走 oauth-receiver HTTP API）
- ❌ 不要在 .env 里写 app_secret 给客户端调用者（明文暴露，app_secret 应只服务自己用）
- ❌ 不要在 canonical 里 URL-encode body（用 raw JSON 字符串）
- ❌ 不要改 `_db_load_token` 直接返回（它本来就是 oauth-receiver 的职责）
- ❌ 不要假设 `code: 0` 是唯一的 success —— TikTok 也会返回 `code: 105005` (scope 缺失) `code: 36009004` (字段缺失) 等
- ❌ **不要**给 `POST /returns` 或 `POST /cancellations` 写实际转发逻辑 —— 这两个是 CREATE write endpoint，
     按用户要求统一返 501。如果以后用户改主意要接，单独 review。

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

## 6. 文件清单

| 路径                                            | 用途                                       |
|-------------------------------------------------|--------------------------------------------|
| `tts_erp.py`                                    | 主服务（路由 + 业务逻辑）                  |
| `tts_signing.py`                                | HMAC 签名 + HTTP 客户端（**可独立复用**）    |
| `schema.sql`                                    | PG 表结构（9 张表 + 触发器）               |
| `.env`                                         | 配置（0600，含 app_key/secret/DB URL）     |
| `restart.sh`                                   | 重启脚本                                   |
| `setup/tts-erp.md`                             | 用户向 setup 文档（服务介绍）              |
| `AGENTS.md`                                    | 本文件 —— AI agent 操作指南                |
| `README.md`                                    | 人类使用说明                               |
| `handoff.md`                                   | 跨 session 交接笔记                        |
| `tests/test_e2e.py`                            | 端到端冒烟测试（scp 到 server 后跑）        |

## 7. 部署 / 启动

```bash
# 一次性
ssh schan@192.168.47.130 "mkdir -p /home/schan/tts-erp/{logs,setup,tests}"

# 部署
scp F:\path\to\tts-erp\* schan@192.168.47.130:/home/schan/tts-erp/
ssh schan@192.168.47.130 "chmod 600 /home/schan/tts-erp/.env && chmod +x /home/schan/tts-erp/restart.sh"

# PG schema (幂等)
cat schema.sql | docker exec -i postgres psql -U postgres -d tts_erp

# 启动
ssh schan@192.168.47.130 "bash /home/schan/tts-erp/restart.sh"
```

## 8. Token 续期 cron

`oauth-receiver` 已经配了 cron（`0 2 * * *`）每天凌晨 2 点调 `/token/<shop_id>/refresh`。
**不要**在 tts-erp 这边再加一个，tts-erp 永远不该主动续期 token（不归它管）。
