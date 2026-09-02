# TikTok 广告分析同步服务 (analytics ingest)

> **v0.5.0 (2026-09-02)** · **v2 化 + /v2 路径硬切** · 与 tts-erp v2 共进程 · 共享 tts-erp 的 AuthMiddleware / RateLimit / AccessLog
>
> Chrome 插件侧 (`tk-adv-cost-monitor`) 每日拉 cursor、批量上传
>
> 上游：Chrome 扩展 (tk-adv-cost-monitor) 推 `productAnalyses` / `sessionAnalyses` / `campaignChangeLogs` 三类记录
> 下游：（无，纯存储 + cursor 服务）
> 存储：PostgreSQL `tts_erp` 数据库 · `analytics` schema · 6 张表（`ad_records` / `ad_daily_pages` / `ad_daily_completeness` / `ad_cursors` / `ad_shop_timezones` / `ad_audit_log`）
>
> **变更背景**：2026-09-02 完成 v2 化 + 路径硬切。旧 `analytics_sync/` 包删除，路由由 `tts_erp_v2/api/v2/analytics.py`（`APIRouter(prefix="/v2/analytics/sync")`）提供；存储改 SQLAlchemy，由 `tts_erp_v2/analytics/repository.py` 实现。schema 单独立到 `analytics`，表名 `ad_*`（migration 0004 走 `SET SCHEMA` + `RENAME`，老库原地迁）。路径前缀 `/v1/analytics/sync` → `/v2/analytics/sync`（**单挂载硬切，无 /v1 别名**）。Chrome 扩展必须同窗口发布只改 path 后缀的更新，否则 404。

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
- ✅ retention 由 sync-worker daily job `analytics.retention` 自动跑（90d records + 30d audit），无需 cron

## 当前状态

| 项目                  | 值                                                              |
|-----------------------|-----------------------------------------------------------------|
| 服务进程              | `uvicorn tts_erp_v2.app:app` (systemd `tts-erp.service`)       |
| 端口                  | **`0.0.0.0:9877`** (与 tts-erp v2 业务 API 同进程)              |
| nginx 反代            | `/v2/analytics/sync/` → `127.0.0.1:9877`（见 `setup/nginx/conf.d/services.conf`）|
| 工作模式              | 跟随 tts-erp v2 的 `TTS_ERP_AUTH_MODE`（默认 `enforce`）        |
| Auth                  | tts-erp v2 `AuthMiddleware`（`security.api_keys` 表，Bearer / X-API-Key，60s TTL 缓存）|
| RateLimit             | tts-erp v2 `RateLimitMiddleware`（默认 100/min/key，`TTS_ERP_RATE_LIMIT_PER_MIN` 可调）|
| AccessLog             | tts-erp v2 `AccessLogMiddleware`（统一在 stdout 一行/请求）     |
| DB                    | `tts_erp` on `postgres` container :5432 · `analytics` schema（迁移见 alembic 0004）|
| 测试覆盖              | `tests_v2/api/test_analytics_v2_contract.py` + `test_analytics_v2_errors.py` + `test_endpoints_index.py` |
| 协议版本              | `protocolVersion: 2`（v1 client 仍接受并 advance cursor）       |
| OpenAPI 规范          | `tech-doc/analytics/openapi.yaml`                                |
| 设计文档              | `tech-doc/analytics/architecture.md`                            |

## 文件布局

```text
/home/schan/tts-erp/
├── tts_erp_v2/
│   ├── analytics/
│   │   ├── domain.py                 # 纯类型（Scope/Record/CursorEntry/StorageKey）
│   │   └── repository.py             # SQLAlchemy 实现（原子 upsert + cursor advance + 审计）
│   ├── api/v2/analytics.py           # 唯一 APIRouter + handlers + Pydantic models
│   ├── jobs/analytics_retention.py   # sync-worker daily retention job（90d records + 30d audit）
│   └── app.py                        # v2 app 在这里 include_router(router, prefix="/v2/analytics/sync")
├── alembic/versions/
│   └── 0004_analytics_ad_schema.py   # 迁移：public.analytics_* → analytics.ad_*
└── tech-doc/
    ├── analytics/                    # Chrome 扩展对接文档（v2）
    │   ├── analytics-sync.md         # API 契约 + curl 示例
    │   ├── architecture.md           # 架构 + 14 个协议歧义解决
    │   ├── openapi.yaml              # OpenAPI 3.1 正式规范
    │   ├── plugin-integration.md     # Chrome 扩展对接说明（/v2 路径）
    │   └── compatibility.md          # 协议版本演进 + 保留策略
    └── analytics-v2-migration-plan.md # v2 化方案 + 4 个决策
```

## PostgreSQL 表（`analytics` schema）

```text
database: tts_erp
schema:   analytics
tables:   ad_records, ad_daily_pages, ad_daily_completeness,
          ad_cursors, ad_shop_timezones, ad_audit_log
```

| 表                            | 说明                                                                  |
|-------------------------------|----------------------------------------------------------------------|
| `analytics.ad_records`        | 原始记录（raw response JSONB + 规范化字段，`UNIQUE(idempotency_key)`）|
| `analytics.ad_daily_pages`    | v2: 每个 `(unit, day, page)` 一行 — 完整性证据                         |
| `analytics.ad_daily_completeness` | v2: 每个 `(unit, day)` 的 `expected_page_count` + `is_complete`       |
| `analytics.ad_cursors`        | 每个 `(seller, advertiser, storageKey, campaignId)` 一行，原子 UPSERT  |
| `analytics.ad_shop_timezones` | 每店铺 IANA 时区（默认 `Asia/Shanghai`）                              |
| `analytics.ad_audit_log`      | requestId-keyed 审计轨迹（无密钥，60s 内可查 `key_prefix`）            |
| `security.api_keys`           | Bearer token 只存 SHA-256 + 16 字符前缀，revoke 走 `enabled=false`    |

## 端点完整列表

### 鉴权

- 所有 `/v2/...` 端点必须 `Authorization: Bearer <token>` 或 `X-API-Key: <token>`
- token 通过 `api_keys.py create` 签发，**plaintext 仅打印一次**
- `TTS_ERP_AUTH_MODE`: `off` / `shadow`（记录 would-deny）/ `enforce`（默认）
- analytics ingest 要求 **readwrite 角色** + token `scopes[]` 覆盖请求的 `(sellerId, advertiserId)`

### Cursor（每日任务起点）

- `GET /v2/analytics/sync/cursor?sellerId=X&advertiserId=Y[&storageKey=...][&campaignId=...][&pageSize=50][&cursor=...]`
  返回每个 `(storageKey, campaignId)` 的 `latestCompletedDay` + 权威 `nextRequiredDay`
  （空时返回 bootstrap 日期 = `today - TTS_ERP_ANALYTICS_BOOTSTRAP_LOOKBACK_DAYS`，默认 30 天）

### Batch（幂等上传）

- `POST /v2/analytics/sync/batches` body: `{protocolVersion, requestId, scope, records[]}`
  - `records` 1..100 条，body ≤ `TTS_ERP_ANALYTICS_MAX_BODY_BYTES`（默认 2 MB；超 413）
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
