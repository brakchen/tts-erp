# TikTok 广告分析同步服务 (analytics ingest)

> **v0.6.0 (2026-09-02)** · **dump architecture** · 与 tts-erp v2 共进程 · 共享 tts-erp 的 AuthMiddleware / RateLimit / AccessLog
>
> Chrome 插件侧 (`tk-adv-cost-monitor`) 逐页拉数据、逐页 dump 原始 HTTP 交换
>
> 上游：Chrome 扩展 (tk-adv-cost-monitor) 推 `productAnalyses` / `sessionAnalyses` / `campaignChangeLogs` 三类分析 dump
> 下游：（无，纯存储 + has-data 预检服务）
> 存储：PostgreSQL `tts_erp` 数据库 · `analytics` schema · **1 张表**（`ad_raw`）—— 2026-09-05 reorg 后由 5 张收为 1 张（详见 `tech-doc/analytics/reorg-plan.md`）
>
> **变更背景**：
>
> - 2026-09-02 v2 化 + 路径硬切：`analytics_sync/` 包删除，路由由 `tts_erp_v2/api/v2/analytics.py` 提供；schema 独立 `analytics`，表名 `ad_*`（migration 0004 `SET SCHEMA` + `RENAME`）。`/v1/analytics/sync/*` 硬切下线（404）。
> - 2026-09-02 dump architecture（migration 0005，见 `tech-doc/analytics/dump-architecture.md`）：删除 plugin 端 page-task 状态机与 server 端 cursor work-list；`/batches`（批量 records[]）换为 `/dumps`（**单 dump object，严禁批量**）；新增 `ad_raw` source-of-truth 表；cursor 降级为 **has-data 预检**。

## 是什么

Chrome 扩展（`tk-adv-cost-monitor`）在 TikTok 广告分析页拦截到一次 HTTP 交换
（request + response 原样）后，**立即逐页 POST 成单条 dump** 推给 tts-erp，
原始 JSONB 落 `analytics.ad_raw`（source-of-truth，5 元组唯一约束幂等），
同事务派生 `ad_records` / `ad_daily_completeness`。

- ✅ 原始落库：ad_raw 存完整 request/response，永远可重放派生
- ✅ 幂等：server 重算 canonical key（6 字段 SHA-256，page 隐式 = 1），
  5 元组 `(seller_id, advertiser_id, endpoint, day, campaign_id)` unique 兜底
- ✅ 插件免状态机：没有 page task 队列 / lease / expected_page_count ——
  一页一 dump、一页一发，失败单页重试即可
- ✅ `GET /cursor` = has-data 预检（防风控）：打 TikTok 前问「这天这 endpoint
  dump 过没」，`hasData: true` 直接跳过
- ✅ 每 token prefix 独立限流（默认 100/min）；per-token scope 校验
- ✅ 错误码 400/401/403/413/429/5xx 全部带 `{code, message, requestId, retryable}`，永不回显 token
- ✅ retention 由 sync-worker daily job ~~`analytics.retention`~~（2026-09-05 reorg 后已摘除）—— ad_raw 永久保留，无需 cron；其他派生表已 drop,无需清

## 当前状态

| 项目                  | 值                                                              |
|-----------------------|-----------------------------------------------------------------|
| 服务进程              | `uvicorn tts_erp_v2.app:app` (systemd `tts-erp.service`)       |
| 端口                  | **`0.0.0.0:9877`** (与 tts-erp v2 业务 API 同进程)              |
| 工作模式              | 跟随 tts-erp v2 的 `TTS_ERP_AUTH_MODE`（默认 `enforce`）        |
| Auth                  | tts-erp v2 `AuthMiddleware`（`security.api_keys`，Bearer / X-API-Key，60s TTL 缓存）|
| RateLimit             | tts-erp v2 `RateLimitMiddleware`（默认 100/min/key，`TTS_ERP_RATE_LIMIT_PER_MIN` 可调）|
| DB                    | `tts_erp` on `postgres` container :5432 · `analytics` schema（迁移 alembic 0004 + 0005）|
| 测试覆盖              | `tests/api/test_analytics_v2_contract.py` + `test_analytics_v2_errors.py` + `test_endpoints_index.py` |
| 协议版本              | `protocolVersion ∈ {1, 2}`（2 = dump 单 object 形状）            |
| 设计文档              | `tech-doc/analytics/dump-architecture.md`（另见同目录 architecture.md / analytics-sync.md）|

## 文件布局

```text
/home/schan/tts-erp/
├── tts_erp_v2/
│   ├── analytics/
│   │   ├── domain.py                 # 纯类型（Scope/DumpPayload/DumpResult/HasDataResult）
│   │   └── repository.py             # SQLAlchemy 实现（raw SQL：ad_raw 单表 + has_data）
│   ├── api/v2/analytics.py           # 唯一 APIRouter + handlers + Pydantic models
│   └── db/models/analytics.py        # 1 表（ad_raw）SQLAlchemy metadata 镜像（读写走 repository raw SQL）
│       # 2026-09-05 reorg：jobs/analytics_retention.py 删除,TDD 详情见 tech-doc/analytics/reorg-plan.md
├── alembic/versions/
│   ├── 0004_analytics_ad_schema.py   # 迁移：public.analytics_* → analytics.ad_*
│   └── 0005_ad_raw_per_unit_day.py   # dump 化：+ad_raw，drop ad_daily_pages/ad_cursors，去 page 列
└── tech-doc/
    ├── analytics/                    # Chrome 扩展对接文档
    │   ├── dump-architecture.md      # ★ dump 架构方案（当前协议事实源）
    │   ├── analytics-sync.md         # API 契约 + curl 示例（v2 化版）
    │   ├── architecture.md           # v1→v2 架构演进 + 协议歧义解决
    │   ├── plugin-integration.md     # Chrome 扩展对接说明（旧 page/expectedPageCount 协议，superseded）
    │   ├── compatibility.md          # 协议版本演进 + 保留策略
    │   └── openapi.yaml              # OpenAPI 规范（旧 batches 协议，superseded）
    └── analytics-v2-migration-plan.md # v2 化方案
```

## PostgreSQL 表（`analytics` schema）

```text
database: tts_erp
schema:   analytics
tables:   ad_raw
```

| 表                            | 说明                                                                  |
|-------------------------------|----------------------------------------------------------------------|
| `analytics.ad_raw`            | **source-of-truth** —— 全 analytics schema 仅此 1 表：每 dump 一行完整 HTTP 交换（request/response JSONB）。UNIQUE 5 元组 `(seller_id, advertiser_id, endpoint, day, campaign_id)`。不 purge |
| `security.api_keys`           | Bearer token 只存 SHA-256 + 16 字符前缀，revoke 走 `enabled=false`    |

> 2026-09-05 reorg 后删除（migration 0007）：
> - `ad_records` —— 生产零 SELECT 的纯写放大表
> - `ad_daily_completeness` —— 与 ad_raw existence 完全冗余
> - `ad_shop_timezones` —— 生产读写路径均死，配置概念随需随取
> - `ad_audit_log` —— 审计改 logger 文件日志（``tts_erp_v2.analytics.ingest``）
>
> 详见 `tech-doc/analytics/reorg-plan.md`。

## 端点完整列表

### 鉴权

- 所有 `/v2/...` 端点必须 `Authorization: Bearer <token>` 或 `X-API-Key: <token>`
- token 通过 `api_keys.py create` 签发，**plaintext 仅打印一次**
- analytics ingest 要求 **readwrite 角色** + token `scopes[]` 覆盖请求的 `(sellerId, advertiserId)`

### Cursor（has-data 预检，防风控）

- `GET /v2/analytics/sync/cursor?sellerId=X&advertiserId=Y&endpoint=E&day=D[&campaignId=C]`
- 查 `ad_raw` existence → `data: {day, endpoint, storageKey, hasData, campaignId?}`
- `endpoint` 必须在 dump 白名单（3 条，server 推导 storageKey；白名单外 400）
- **没有 work-list / nextRequiredDay / pageSize / cursor / timezone**（dump 架构删除）

### Dumps（单 dump 写入，严禁批量）

- `POST /v2/analytics/sync/dumps` body: `{protocolVersion, requestId, scope, dump: {...}}`
  - `dump` = 一次完整 HTTP 交换：`{endpoint, method, day, campaignId, request, response, capturedAt}`
  - 不带 `page`（隐式 1）/ `expectedPageCount` / `storageKey`（server 推导）/ `sourceRecordId`
  - body ≤ 2 MB；单 dump `response` ≤ 256 KB（超 413 / 400 `RESPONSE_TOO_LARGE`）
  - 响应：`data: {idempotencyKey, status: "inserted"|"duplicate"}` —— **都是成功**
- 同 dump 重放 → `duplicate`，由 ad_raw 5 元组 unique 兜底（server 重算 key 校验）

### 运维

- `GET /healthz`    健康检查（公开）
- `GET /endpoints`  端点清单（公开）

## 完整使用流程

### 一次性配置

```bash
# 1) 迁移（幂等；老库已有数据走 alembic，新库直接 upgrade head）
cd /home/schan/tts-erp && .venv/bin/alembic upgrade head

# 2) 签发 token（plaintext 仅打印一次，存到密码管理器）
python3 /home/schan/tts-erp/api_keys.py create \
  --role readwrite \
  --name chrome-ext-prod \
  --expires-days 365

# 输出示例：
#   prefix  : ttserp_rw_xxxxxxxx
#   expires : in 365 days
#   SYNC TOKEN (shown ONCE, store it now):  ttserp_rw_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx

# 3) 服务已随 tts-erp.service 常驻；改代码后重启
bash /home/schan/tts-erp/restart.sh
```

### Chrome 扩展集成（dump 协议）

```bash
KEY="ttserp_rw_..."

# 1) has-data 预检（可选，防风控）：这天这个 endpoint 已 dump → 跳过
curl -s \
  -H "Authorization: Bearer $KEY" \
  "http://127.0.0.1:9877/v2/analytics/sync/cursor?sellerId=shop-1&advertiserId=adv-1&endpoint=%2Foec_ads%2Fshopping%2Fv1%2Foec%2Fstat%2Fpost_product_list&day=2026-08-23"
# → {"code":0,"requestId":"...","data":{"day":"2026-08-23","endpoint":"/oec_ads/...","storageKey":"productAnalyses","hasData":false}}

# 2) 逐页 dump：抓到一页就发一页，严禁攒 N 页批量
curl -s -X POST \
  -H "Authorization: Bearer $KEY" \
  -H "Content-Type: application/json" \
  -H "X-Request-Id: $(uuidgen)" \
  http://127.0.0.1:9877/v2/analytics/sync/dumps \
  -d @dump.json
# dump.json 形状：
# {
#   "protocolVersion": 2,
#   "scope": {"sellerId": "shop-1", "advertiserId": "adv-1"},
#   "dump": {
#     "endpoint": "/oec_ads/shopping/v1/oec/stat/post_product_list",
#     "method": "POST",
#     "day": "2026-08-23",
#     "campaignId": "c-1",
#     "request": {"url": "https://...", "headers": {}, "body": {}},
#     "response": {"status": 200, "headers": {}, "body": {"data": []}},
#     "capturedAt": "2026-08-23T03:00:00.000Z"
#   }
# }
# → {"code":0,"requestId":"...","data":{"idempotencyKey":"<64hex>","status":"inserted"}}
```

完整对接说明见 `tech-doc/analytics/dump-architecture.md`（★ 当前事实源）。

### 多店铺 / 受限 token

```bash
# 给店铺 A 一个仅限自己的 token
python3 api_keys.py create \
  --role readwrite \
  --name chrome-ext-shop-A \
  --scopes "seller:shop-A-id"

# 该 token 调 cursor?sellerId=shop-A-id → 200
# 该 token 调 cursor?sellerId=shop-B-id → 403 SCOPE_DENIED
```

## 配置（env vars）

| 变量                                    | 默认值     | 说明                                                  |
|-----------------------------------------|-----------|------------------------------------------------------|
| `TTS_ERP_DB_URL`                        | —          | **必填**。PG DSN                                       |
| `TTS_ERP_AUTH_MODE`                     | `enforce`  | `off` / `shadow` / `enforce`                          |
| `TTS_ERP_RATE_LIMIT_PER_MIN`            | `100`      | 每 key 滑窗限流（60s 窗口）                            |

`.env` 文件从**仓库根目录**（`/home/schan/tts-erp/.env`）读取。旧
`ANALYTICS_SYNC_*` / `TTS_ERP_ANALYTICS_*` 变量已随 v2 化 + dump 化废弃
（body 上限 / bootstrap 天数现在是模块常量）。

## 幂等键计算（关键！）

server 对每个 dump 重算 canonical key（6 字段，page 固定 1），校验后
写 `ad_raw.idempotency_key`。算法与旧 v2 batches 协议**字节兼容**：

```python
# Python 参考实现
import hashlib, json

def compute_idempotency_key(seller_id, advertiser_id, storage_key, campaign_id, day, page=1):
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

> `storageKey` 由 server 从 endpoint 推导（`STORAGE_KEY_BY_PATH`），
> plugin 不需要算 key —— 上表仅供 server 端调试 / 审计核对。

**Reference test vector**：

```python
compute_idempotency_key("seller-1", "adv-1", "productAnalyses", "campaign-1", "2026-08-23", 1)
# → "73b716cce7f8b2c4220b1be3e5ab6327c3a963eaf424af84412402ef8607dae3"
```

## 进程管理

analytics ingest 与 tts-erp v2 业务 API **同进程**（`tts-erp.service`）：

```bash
systemctl --user status tts-erp.service
systemctl --user restart tts-erp.service       # = bash restart.sh
journalctl --user -u tts-erp -n 50
```

retention job 在 sync-worker `tts-erp-sync.service`（2026-09-05 reorg 后
analytics 域已无日级 job）：

```bash
systemctl --user restart tts-erp-sync.service  # 改了 jobs/ 或 sync_worker/ 后跑
```

## 错误契约速查

| HTTP | code in body              | 重试？ | 插件动作                                    |
|------|---------------------------|--------|--------------------------------------------|
| 200  | 0                         | —      | 看 data.status = inserted / duplicate       |
| 400  | MALFORMED_JSON / SCHEMA_INVALID / UNSUPPORTED_PROTOCOL_VERSION | 否 | 表面错误，停 sync 直到修复 |
| 400  | RESPONSE_TOO_LARGE        | 否     | 单 dump response > 256 KB，检查抓取       |
| 401  | —                         | 否     | token 失效，**不**自动重试                  |
| 403  | SCOPE_DENIED              | 否     | token 不够权限，**不**自动重试              |
| 413  | PAYLOAD_TOO_LARGE         | 否     | body > 2 MB（单 dump 一般到不了）           |
| 429  | RATE_LIMITED              | **是** | 读 `Retry-After` header（秒），等           |
| 5xx  | INTERNAL_ERROR            | **是** | 指数退避（1/2/4/8s）后重试（同 dump 重放 = duplicate，安全）|

`SCHEMA_INVALID` 响应带结构化 `errors[]`（`loc`/`msg`/`type` 安全三元组，
无 input/ctx）；其余错误码不带 `errors`。错误 envelope 永不回显 token /
cookie / 完整请求头。

## 已知偏差 / 协议歧义解决

- **dump 粒度**：1 dump = 1 次 HTTP 交换（1 页）。多页数据 = 多 dump 逐个发；
  server **没有**「等齐 N 页才 complete」的概念，收到即算有数据
- **complete 语义**：不再有 expected_page_count —— has-data 检查
  `hasData` 只回答「有没有」，不回答「齐不齐」（dump-architecture D3）
- **scope 语法**：`seller:<id>` / `advertiser:<id>` / `*`，未提及维度不受限
- **token 撤销传播**：60s 内存缓存，DB 撤销后最多等 1 分钟生效
- **ad_raw 永久保留**：派生表可随时从 ad_raw 重建，retention 不 purge 它

## 协议演进

- **当前**：`protocolVersion: 2`（dump 单 object 形状）；1 仍接受（同形状）
- **未来 breaking 触发条件**：改 dump 字段语义 / 改 idempotency key 算法 /
  恢复批量 / 改 scope 语义等
- **保留策略**：
  - `ad_raw`：forever（source-of-truth）
  - `ad_records`：90 天（sync-worker `analytics.retention` job）
  - `ad_audit_log`：30 天
  - `ad_shop_timezones`：forever

## 相关文档

- `tech-doc/analytics/dump-architecture.md` — ★ 当前 dump 架构方案
- `tech-doc/external-api.md` — tts-erp 全量外部端点契约（analytics 节）
- `tech-doc/_archive/plugin-integration.md` — 插件对接（旧 page 协议，已 superseded 归档）
- `tests/api/test_analytics_v2_contract.py` + `test_analytics_v2_errors.py` — 契约 + 错误观测测试
