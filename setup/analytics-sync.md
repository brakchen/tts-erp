# TikTok 广告分析同步服务 (analytics_sync)

> schan 服务器 · `0.0.0.0:9878` (内网) · 与 tts-erp (9877) / oauth-receiver (9876) 平行
> 独立 FastAPI 进程 · PostgreSQL 持久化 · Bearer token 鉴权 · 端口 9878
>
> v0.2.0 (2026-08-23) · **替代已退役的 CloudBase 分析上传路径** · Chrome 插件侧 (`tk-adv-cost-monitor`) 每日拉 cursor、批量上传
>
> 上游：Chrome 扩展 (tk-adv-cost-monitor) 推 `productAnalyses` / `sessionAnalyses` / `campaignChangeLogs` 三类记录
> 下游：（无，纯存储 + cursor 服务）
> 存储：PostgreSQL `tts_erp` 数据库 · **5 张表**（analytics_*前缀，与 miaoshou_* / api_keys 共库）

## 是什么

把 Chrome 扩展（`tk-adv-cost-monitor`）在 TikTok 广告分析页面拦截到的
**productAnalyses** / **sessionAnalyses** / **campaignChangeLogs** 三类分析数据
通过 HTTPS + Bearer token 推过来，**稳定幂等入库**（`sha256(canonical_json(...))` 去重），
并为插件的 daily-job 提供权威 cursor（`nextRequiredDay`）。

- ✅ HTTP 接口完全替代 CloudBase 直写路径
- ✅ 幂等 upsert：`ON CONFLICT (idempotency_key) DO NOTHING` + 原子 cursor advance（`GREATEST` 防回退）
- ✅ 每 token prefix 独立限流（默认 100/min）
- ✅ per-token scope 校验（`seller:<id>` / `advertiser:<id>` / `*`）
- ✅ 错误码 400/401/403/413/429/5xx 全部带 `{code, message, requestId, retryable}`，永不回显 token

## 当前状态

| 项目                  | 值                                                              |
|-----------------------|-----------------------------------------------------------------|
| 服务进程              | `python3 -m uvicorn analytics_sync.app:app` (cwd `analytics_sync/`) |
| 端口                  | `0.0.0.0:9878` (TCP LISTEN)                                     |
| 工作模式              | **enforce** (Bearer token 强制)                                 |
| DB                    | `tts_erp` on `postgres` container :5432 · **5 张 analytic_* 表** |
| Auth                  | Bearer / X-Sync-Token，60s 缓存，SHA-256 哈希 + 16 字符前缀         |
| 测试覆盖              | **63/63 passed**（auth 7 + batches 12 + cursor 6 + idempotency 10 + concurrency 2 + isolation 3 + scope 12 + errors 6 + rate-limit 5） |
| 协议版本              | `protocolVersion: 1`                                            |
| OpenAPI 规范          | `analytics_sync/tech-doc/openapi.yaml`                          |
| 设计文档              | `analytics_sync/tech-doc/architecture.md`                       |

## 文件布局

```
/home/schan/tts-erp/
├── analytics_sync/                     # ← 本服务全部代码
│   ├── README.md                       # 操作 quick-start
│   ├── schema.sql                      # 5 张表 + 索引（IF NOT EXISTS 幂等）
│   ├── retention.sql                   # 90d records + 30d audit 清理
│   ├── app.py                          # FastAPI 应用（路由 + 业务）
│   ├── auth.py                         # Bearer + scope 校验中间件
│   ├── rate_limit.py                   # 每 token 滑窗限流
│   ├── domain.py                       # 纯类型（Scope/Record/CursorEntry/StorageKey）
│   ├── pg_repositories.py              # PG 实现（原子 upsert + cursor advance）
│   ├── api_keys.py        # CLI: create / list / revoke / rotate
│   ├── conftest.py                     # pytest 公共 fixture
│   ├── pytest.ini
│   ├── tech-doc/
│   │   ├── analytics-sync.md          # API 契约 + curl 示例 + 部署
│   │   ├── architecture.md            # 架构 + 14 个协议歧义解决
│   │   ├── openapi.yaml               # OpenAPI 3.1 正式规范
│   │   ├── plugin-integration.md      # Chrome 扩展对接说明
│   │   └── compatibility.md          # 协议版本演进 + 保留策略
│   └── tests/                          # 9 个测试文件，63 用例
│       ├── test_auth.py
│       ├── test_batches.py
│       ├── test_concurrency.py
│       ├── test_cursor.py
│       ├── test_errors.py
│       ├── test_idempotency.py
│       ├── test_isolation.py
│       ├── test_rate_limit.py
│       └── test_scope.py
└── setup/
    └── analytics-sync.md              # ← 你正在看的这个文件
```

## PostgreSQL 表

```
database: tts_erp
新增表:   analytics_records, analytics_cursors, analytics_shop_timezones,
          api_keys, analytics_audit_log
```

| 表                            | 说明                                                                  |
|-------------------------------|----------------------------------------------------------------------|
| `analytics_records`           | 原始记录（raw response JSONB + 规范化字段，`UNIQUE(idempotency_key)`）|
| `analytics_cursors`           | 每个 `(seller, advertiser, storageKey, campaignId)` 一行，原子 UPSERT  |
| `analytics_shop_timezones`    | 每店铺 IANA 时区（默认 `Asia/Shanghai`）                              |
| `api_keys`       | Bearer token 只存 SHA-256 + 16 字符前缀，revoke 走 `enabled=false`    |
| `analytics_audit_log`         | requestId-keyed 审计轨迹（无密钥，60s 内可查 `key_prefix`）            |

## 端点完整列表

### 鉴权

- 所有 `/v1/...` 端点必须 `Authorization: Bearer <token>` 或 `X-Sync-Token: <token>`
- token 通过 `api_keys.py create` 签发，**plaintext 仅打印一次**
- `ANALYTICS_SYNC_AUTH_MODE`: `off` / `shadow`（记录 would-deny）/ `enforce`（默认）

### Cursor（每日任务起点）

- `GET /v1/analytics/sync/cursor?sellerId=X&advertiserId=Y[&storageKey=...][&campaignId=...][&pageSize=50][&cursor=...]`
  返回每个 `(storageKey, campaignId)` 的 `latestCompletedDay` + 权威 `nextRequiredDay`
  （空时返回 bootstrap 日期 = `today - ANALYTICS_SYNC_BOOTSTRAP_LOOKBACK_DAYS`，默认 30 天）

### Batch（幂等上传）

- `POST /v1/analytics/sync/batches` body: `{protocolVersion, requestId, scope, records[]}`
  - `records` 1..100 条，body ≤ 2 MB
  - 每条 `idempotencyKey` 必须是 `sha256(canonical_json({sellerId, advertiserId, storageKey, campaignId, day, page}))`（server 会重算验证）
  - 响应：`{accepted: [{idempotencyKey, status: "inserted"|"duplicate"}], rejected: [{idempotencyKey, code, message, retryable}]}`
  - **inserted 和 duplicate 都是成功** —— 插件可标记本地记录为 `synced`

### 运维

- `GET /healthz`    健康检查
- `GET /endpoints`  端点清单 + 错误契约

## 完整使用流程

### 一次性配置

```bash
# 1) 应用 schema（幂等，可重复跑）
cat /home/schan/tts-erp/analytics_sync/schema.sql \
  | docker exec -i postgres psql -U postgres -d tts_erp

# 2) 签发首个 token（plaintext 仅打印一次，存到密码管理器）
TTS_ERP_DB_URL=postgresql://postgres:...@127.0.0.1:5432/tts_erp \
  python3 /home/schan/tts-erp/api_keys.py create \
    --name chrome-ext-prod \
    --expires-days 365

# 输出示例：
#   prefix  : ttserp_rw_xxxxxxxx
#   expires : in 365 days
#   SYNC TOKEN (shown ONCE, store it now):  ttserp_rw_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx

# 3) 启动服务
TTS_ERP_DB_URL=postgresql://postgres:...@127.0.0.1:5432/tts_erp \
  ANALYTICS_SYNC_AUTH_MODE=enforce \
  /home/schan/tts-erp/.venv/bin/python -m uvicorn \
    analytics_sync.app:app --host 0.0.0.0 --port 9878
```

### Chrome 扩展集成（典型 daily-job 流程）

```bash
# 1) 拉 cursor 决定从哪天开始
curl -s \
  -H "Authorization: Bearer ttserp_rw_..." \
  "http://127.0.0.1:9878/v1/analytics/sync/cursor?sellerId=shop-1&advertiserId=adv-1"
# → data.items[0].nextRequiredDay

# 2) 批量上传当天抓到的数据
curl -s -X POST \
  -H "Authorization: Bearer ttserp_rw_..." \
  -H "Content-Type: application/json" \
  -H "X-Request-Id: $(uuidgen)" \
  -H "X-Protocol-Version: 1" \
  http://127.0.0.1:9878/v1/analytics/sync/batches \
  -d @batch.json
# batch.json 形状：
# {
#   "protocolVersion": 1,
#   "scope": {"sellerId": "shop-1", "advertiserId": "adv-1"},
#   "records": [{
#     "idempotencyKey": "<sha256>",
#     "sourceRecordId": "<local uuid>",
#     "storageKey": "productAnalyses",
#     "campaignId": "c-1",
#     "day": "2026-08-23",
#     "page": 1,
#     "endpoint": "/oec_ads/...",
#     "method": "POST",
#     "response": { ... },
#     "capturedAt": "2026-08-23T03:00:00.000Z",
#     "schemaVersion": 1
#   }]
# }
```

完整对接说明（含 TypeScript 客户端示例 + 重试策略）见
[`analytics_sync/tech-doc/plugin-integration.md`](../analytics_sync/tech-doc/plugin-integration.md)。

### 多店铺 / 受限 token

```bash
# 给店铺 A 一个仅限自己的 token
python3 api_keys.py create \
  --name chrome-ext-shop-A \
  --scopes "seller:shop-A-id"

# 该 token 调 /v1/analytics/sync/cursor?sellerId=shop-A-id → 200
# 该 token 调 /v1/analytics/sync/cursor?sellerId=shop-B-id → 403 SCOPE_DENIED
```

## 配置（env vars）

| 变量                                    | 默认值         | 说明                                                  |
|-----------------------------------------|----------------|------------------------------------------------------|
| `TTS_ERP_DB_URL`（或 `ANALYTICS_SYNC_DB_URL`） | —              | **必填**。PG DSN，与 tts-erp 共享                      |
| `ANALYTICS_SYNC_AUTH_MODE`              | `enforce`      | `off` / `shadow` / `enforce`                          |
| `ANALYTICS_SYNC_BOOTSTRAP_LOOKBACK_DAYS`| `30`           | 首次同步时 `nextRequiredDay` 的回溯天数                |
| `ANALYTICS_SYNC_RATE_LIMIT_PER_MIN`     | `100`          | 每 token prefix 滑窗限流（60s 窗口）                   |
| `MIAOSHOU_DEBUG_SIGN` 等                | —              | **不属于**本服务                                       |

`.env` 文件从**仓库根目录**（`/home/schan/tts-erp/.env`）读取，与 tts-erp / miaoshou 共用。

## 幂等键计算（关键！）

```python
# Python 参考实现
import hashlib, json

def compute_idempotency_key(seller_id, advertiser_id, storage_key, campaign_id, day, page):
    canonical = json.dumps({
        "sellerId": seller_id.strip(),
        "advertiserId": advertiser_id.strip(),
        "storageKey": storage_key,
        "campaignId": campaign_id.strip(),
        "day": day,
        "page": int(page),
    }, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
```

```typescript
// TypeScript 参考实现（Chrome 扩展）
import { createHash } from "node:crypto";

function canonicalKeyFor(p: {
  sellerId: string; advertiserId: string; storageKey: string;
  campaignId: string; day: string; page: number;
}): string {
  const o = {
    sellerId: p.sellerId.trim(),
    advertiserId: p.advertiserId.trim(),
    storageKey: p.storageKey,
    campaignId: p.campaignId.trim(),
    day: p.day,
    page: Number(p.page),
  };
  const ordered = Object.keys(o).sort().reduce((acc, k) => { acc[k] = (o as any)[k]; return acc; }, {} as Record<string, unknown>);
  return createHash("sha256").update(JSON.stringify(ordered), "utf8").digest("hex");
}
```

**Reference test vector**：

```python
compute_idempotency_key("seller-1", "adv-1", "productAnalyses", "campaign-1", "2026-08-23", 1)
# → "73b716cce7f8b2c4220b1be3e5ab6327c3a963eaf424af84412402ef8607dae3"
```

如果你的 key 不匹配这个，server 会返回 `rejected[].code = "SCHEMA_INVALID"`。

## 进程管理

**推荐 systemd user unit**（参考 tts-erp.service 模板）：

```bash
mkdir -p ~/.config/systemd/user
cat > ~/.config/systemd/user/analytics-sync.service <<'EOF'
[Unit]
Description=analytics_sync FastAPI
After=network.target

[Service]
WorkingDirectory=/home/schan/tts-erp
EnvironmentFile=/home/schan/tts-erp/.env
ExecStart=/home/schan/tts-erp/.venv/bin/python -m uvicorn \
    analytics_sync.app:app --host 0.0.0.0 --port 9878 --workers 1
Restart=on-failure
RestartSec=3

[Install]
WantedBy=default.target
EOF

systemctl --user daemon-reload
systemctl --user enable --now analytics-sync.service
systemctl --user status analytics-sync.service
journalctl --user -u analytics-sync -n 50
```

Linger 必须开（与 tts-erp.service / oauth-receiver.service 同一个约束）。

## 错误契约速查

| HTTP | code in body              | 重试？ | 插件动作                                    |
|------|---------------------------|--------|--------------------------------------------|
| 200  | 0                         | —      | 看 per-record accepted/rejected              |
| 400  | MALFORMED_JSON / SCHEMA_INVALID / UNSUPPORTED_PROTOCOL_VERSION | 否     | 表面错误，停 sync 直到修复                  |
| 401  | —                         | 否     | token 失效，**不**自动重试                  |
| 403  | SCOPE_DENIED              | 否     | token 不够权限，**不**自动重试              |
| 413  | PAYLOAD_TOO_LARGE         | 否     | 拆分 batch（每 ≤ 100 条 / ≤ 2 MB）         |
| 429  | RATE_LIMITED              | **是** | 读 `Retry-After` header（秒），等           |
| 5xx  | INTERNAL_ERROR            | **是** | 指数退避（1/2/4/8s）后重试，记录幂等安全  |

错误 envelope 永不回显 token / cookie / webhook / 完整请求头。

## 已知偏差 / 协议歧义解决

- **`nextRequiredDay` bootstrap 日期**：服务端权威值 = `today_in_shop_tz - ANALYTICS_SYNC_BOOTSTRAP_LOOKBACK_DAYS`，**插件不应用 `queryRecentDays` 自行推导**
- **scope 语法**：协议未定义，我们用 `seller:<id>` / `advertiser:<id>` / `*`，未提及的维度不受限
- **cursor 分页**：MVP 用 offset-style 游标（`{page_size, offset}` 的 base64）；数据量大时改 keyset
- **token 撤销传播**：60s 内存缓存，DB 撤销后最多等 1 分钟生效
- **per-record response 上限 256 KB**：超过 → `RESPONSE_TOO_LARGE` rejected
- **每条 `accepted.duplicate` 都是成功**：插件可标记本地 `remoteSyncStatus="synced"`

完整歧义清单（14 条）+ 设计动机见
[`analytics_sync/tech-doc/architecture.md` §4](../analytics_sync/tech-doc/architecture.md)。

## 协议演进

- **当前**：`protocolVersion: 1`，稳定
- **未来 v2 触发条件**：移除 `storageKey` / 改 scope 语义 / 改 `idempotencyKey` 算法等 breaking change
- **保留策略**：
  - `analytics_records`：默认 90 天（`retention.sql` cron 可调）
  - `analytics_audit_log`：默认 30 天
  - `analytics_cursors` / `analytics_shop_timezones` / `api_keys`：forever

完整协议版本演进策略见
[`analytics_sync/tech-doc/compatibility.md`](../analytics_sync/tech-doc/compatibility.md)。

## 相关文档

- [`analytics_sync/README.md`](../analytics_sync/README.md) — 操作 quick-start
- [`analytics_sync/tech-doc/analytics-sync.md`](../analytics_sync/tech-doc/analytics-sync.md) — 完整 API 契约 + curl
- [`analytics_sync/tech-doc/architecture.md`](../analytics_sync/tech-doc/architecture.md) — 架构 + 歧义解决
- [`analytics_sync/tech-doc/openapi.yaml`](../analytics_sync/tech-doc/openapi.yaml) — OpenAPI 3.1
- [`analytics_sync/tech-doc/plugin-integration.md`](../analytics_sync/tech-doc/plugin-integration.md) — Chrome 扩展对接
- [`analytics_sync/tech-doc/compatibility.md`](../analytics_sync/tech-doc/compatibility.md) — 协议版本 + 保留策略
- `setup/tts-erp.md`（同目录）— 上游 TikTok Shop ERP 服务
- `setup/oauth-receiver.md` — 上游 token 服务
- `AGENTS.md`（本项目） — AI agent 操作指南
