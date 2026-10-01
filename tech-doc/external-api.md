# tts-erp External API Guide

This document is the **stable public API contract** for the tts-erp FastAPI
service (port 9877). The authoritative live route list is `GET /endpoints`;
this document explains semantics, auth, and conventions.

> **2026-08-29 hard switch**: all legacy v1 endpoints — `/db/*`, `/orders/*`,
> `/finance/*`, `/sync/*`, `/token/*`, `/miaoshou/*` — were **deleted** and
> now return 404. This guide covers only the live v2 contract. The v1-era
> version of this document is in git history.

## TL;DR — quick reference for agents

All endpoints are served at `http://127.0.0.1:9877` (or
`https://daqiang.nat100.top` from outside — TLS terminates at the public gateway and the NAT layer strips the port;
browser traffic may additionally sit under a `/tts` prefix handled by nginx).
Every endpoint other than the explicitly-public ones requires
`Authorization: Bearer <key>` or `X-API-Key: <key>` — or a browser session
cookie (see [Browser session login](#browser-session-login)).

| What you want | Endpoint | Role |
| --- | --- | --- |
| Service liveness / fingerprint | `GET /healthz` | public |
| Discover every route | `GET /endpoints` | public |
| Auto-generated schema | `GET /openapi.json`, `/docs`, `/redoc` | public |
| LLM-oriented system + data dictionary | `GET /v2/llm-context` | readonly |
| List shops (→ internal `shop_pk`) | `GET /v2/commerce/channel-accounts` | readonly |
| Look up shop by upstream shop_id | `GET /v2/commerce/channel-accounts/by-external/{shop_id}` | readonly — see [`tech-doc/api/channel-accounts-by-external.md`](api/channel-accounts-by-external.md) |
| List / get TikTok products (SPU) | `GET /v2/commerce/channel-products[/{id}[/variants]]` | readonly |
| List / get orders (+ lines) | `GET /v2/commerce/sales-orders[/{id}[/lines]]` | readonly |
| TikTok Shop product detail (read-through) | `GET /v2/tiktok-shop/products/{product_id}` | readonly — see [`tech-doc/api/tiktok-shop-get-product.md`](api/tiktok-shop-get-product.md) |
| Per-shop order aggregate | `GET /v2/commerce/channel-accounts/{id}/order-stats` | readonly |
| Cost snapshots | `GET /v2/reporting/cost-snapshots` | readonly |
| Daily profit | `GET /v2/reporting/profit-daily` | readonly |
| Coverage / health snapshot | `GET /v2/reporting/coverage` | readonly |
| Active SPUs missing a cost | `GET /v2/reporting/missing-cost-products` | readonly |
| Search recent manual costs | `GET /v2/reporting/manual-costs` | readonly |
| Submit a manual cost | `POST /v2/reporting/manual-costs` | readwrite |
| List focused SPUs for one shop | `GET /v2/reporting/focused-spus/{shop_pk}` | readonly |
| Add/remove focused SPUs | `PATCH /v2/reporting/focused-spus/{shop_pk}` | readwrite |
| Latest cached FX rates | `GET /v2/fx/latest` | readonly |
| Currency conversion (local, cached) | `GET /v2/fx/convert` | readonly |
| Operator console (HTML) | `GET /v2/pages/manual-costs` | readonly (browser → 302 login) |
| SPU 实际 ROI 看板主表 | `GET /v2/analytics/spu-roi` | readonly — 口径见 [`analytics/spu-real-roi-dashboard.md`](analytics/spu-real-roi-dashboard.md) |
| SPU 实际 ROI 页面 (HTML) | `GET /v2/pages/spu-roi` | readonly (browser → 302 login) |
| 重点关注 SPU 页面 (HTML) | `GET /v2/pages/focused-spus` | readonly (browser → 302 login) |
| SPU image list / upload / delete | `GET /v2/spu-images`, `POST /v2/spu-images/upload-url`, `POST /v2/spu-images/{id}/confirm`, `DELETE /v2/spu-images/{id}` | readonly / readwrite |
| Browser login / logout / whoami | `GET\|POST /v2/auth/login`, `POST /v2/auth/logout`, `GET /v2/auth/me` | public |
| Intercept request statistics | `GET /v2/intercept/requests/stats` | readonly |
| Analytics cursor has-data / dump ingest (Chrome ext) | `GET /v2/analytics/sync/cursor`, `POST /v2/analytics/sync/dumps` | readwrite + scope |
| Order / logistics reconcile and dump ingest (Chrome ext) | `POST /v2/order-sync/{reconcile,has-data,dumps}` | readwrite + scope |
| Start TikTok seller authorization | `GET /v2/oauth/tiktok/authorize` | **readwrite** or above (handler-enforced) |
| TikTok OAuth redirect target (new-shop onboarding) | `GET /v2/oauth/tiktok/callback?code&state` | **public** — see [`tech-doc/api/tiktok-shop-oauth.md`](api/tiktok-shop-oauth.md) |

Key gotchas (read these before writing code):

- **Filter by internal ids, not shop_id.** v2 list endpoints take
  `shop_pk` / `spu_pk` (internal bigint PKs).
  Resolve a TikTok `shop_id` once via
  `GET /v2/commerce/channel-accounts?platform=tiktok` →
  `shop_id`. Passing `?shop_id=` is **silently ignored**
  (FastAPI drops unknown query params) and you get an unfiltered list.
- **Pagination is `limit` + `offset`** (no cursors in v2). `limit` is
  1..500, default 100 (200 on `missing-cost-products`).
- **Money is a string.** `numeric(20,4)` columns serialize as JSON strings
  (`"1187324.0000"`) to avoid float drift — parse with a decimal type.
- **Timestamps are ISO-8601 UTC strings** (`timestamptz` in DB), e.g.
  `2026-08-30T13:33:37Z`. `profit-daily`'s `on_date` filter accepts a date
  or datetime.
- Cookie-authed mutations (browser session) must send
  `X-Requested-With: tts-erp` (CSRF guard). Header-key clients are exempt.

Minimal recipe — first call:

```bash
KEY=$(cat ~/.tts-erp-key)        # mint with: python3 api_keys.py create --role readonly --name agent-x
# resolve the shop's internal account id once
curl -sS -H "X-API-Key: $KEY" \
  "http://127.0.0.1:9877/v2/commerce/channel-accounts?platform=tiktok"
# → [{"id":314,"shop_id":"7494763368967603447",...}]
curl -sS -H "X-API-Key: $KEY" \
  "http://127.0.0.1:9877/v2/commerce/sales-orders?shop_pk=314&limit=2"
```

## Authentication

Every request to a non-public endpoint must carry an API key. Two header
forms are accepted:

```http
Authorization: Bearer <your-api-key>
```

```http
X-API-Key: <your-api-key>
```

`Authorization` takes precedence if both are present. Keys are prefixed by
role (`ttserp_ro_…` readonly, `ttserp_rw_…` readwrite, `ttserp_admin_…`
admin). Roles are linearly ordered `readonly < readwrite < admin`; the
path classification lives in `tts_erp_v2.access` — unmatched paths default
to **admin** (fail-closed). Handler-level mutation gates consume the same typed
`AccessGrant`, so `off` and `shadow` semantics remain consistent through the
handler.

Public (API-key auth-exempt) paths: `/healthz`, `/endpoints`, `/openapi.json`,
`/docs`, `/redoc`, `/docs/oauth2-redirect`, `/v2/auth/login`,
`/v2/auth/logout`, `/v2/auth/me`, `/static/*`. Docs paths can still be protected
by `TTS_ERP_DOCS_USER` / `TTS_ERP_DOCS_PASSWORD`; classification uses the
route-relative path and therefore also covers `/tts/docs` deployments.

**Errors**:

- `401 missing bearer token` — no credential sent
- `401 invalid, disabled or expired api key` — credential not recognised
- `403 requires <role>` — key recognised but lacks the role for this path

The mode is set by env `TTS_ERP_AUTH_MODE=off|shadow|enforce`. In
`enforce` (production default since 2026-08-20) the service returns the
error; in `shadow` the would-deny is only logged and never redirected or
rejected; `off` bypasses middleware and handler role gates entirely
(development only). Invalid mode values are logged and fail closed as `enforce`.
Enforced 401/403 requests, including browser 302 attempts, consume the shared
denied-request rate-limit budget before response presentation is selected.

## Browser session login

For human operators there is a thin cookie layer on top of the API-key
system (design: [`browser-login-design.md`](browser-login-design.md)):

- `GET /v2/auth/login` — public HTML form.
- `POST /v2/auth/login` — body `{"key": "...", "next": "/v2/pages/manual-costs"}`;
  validates the key against `security.api_keys`, sets an HMAC-signed
  `HttpOnly` session cookie `tts_session` (12 h fixed TTL; the cookie
  stores only the key hash, re-validated against the DB per request —
  revoking the key kills the session within the cache TTL).
- `POST /v2/auth/logout` — clears the cookie.
- `GET /v2/auth/me` — `{authenticated, role}` for the current cookie; `role`
  is read from the current database credential, not the role embedded when the
  cookie was minted.

Browser navigations (`Accept: text/html`) that fail auth get a **302** to
`/v2/auth/login?next=...` instead of a JSON 401. Cookie-authed
POST/DELETE requests must carry `X-Requested-With: tts-erp` (CSRF guard;
double-checked with `SameSite=Lax` + default-deny CORS).

## Rate Limiting

Sliding-window per API key (or per session key-hash for cookie traffic).
Default: **100 requests per 60 seconds**, configurable via env
`TTS_ERP_RATE_LIMIT_PER_MIN`. Anonymous requests (public paths) pass
through unbucketed.

Over-quota responses:

```http
HTTP/1.1 429 Too Many Requests
Retry-After: 47
X-RateLimit-Limit: 100
X-RateLimit-Remaining: 0
Content-Type: application/json

{"detail":"rate limit exceeded: 100 req/60s per api key","retry_after_s":47}
```

## CORS

Default: browser access is limited to the signed production
`ads-data-sync` extension origin. To replace that allow-list or add a
managed browser client, set:

```dotenv
TTS_ERP_CORS_ALLOW_ORIGINS=chrome-extension://obpgdepgjmchplabmkoeboceddbmlbok,https://app.example.com
```

The analytics extension sends `Authorization`, `X-API-Key`, and `X-Request-Id`
headers; the server allows these headers during the CORS preflight. For
dev/internal deploys, `TTS_ERP_CORS_ALLOW_ORIGINS=wildcard` enables `*` — do
not use in production.

## Endpoints

### Commerce (`/v2/commerce/*`, all readonly GET)

All list endpoints accept `limit` (1..500, default 100) + `offset` (≥0).

| Endpoint | Extra query params | Returns |
| --- | --- | --- |
| `GET /v2/commerce/channel-accounts` | `platform` (e.g. `tiktok`) | list of `{id, platform, shop_id, account_name, region, seller_type, status, opened_date, credential_id, service_id, app_credentials_configured, synced_at}`；响应头 `X-Total-Count` 是同一过滤条件下、分页前的店铺总数。`credential_id` 非空 = 已 OAuth 授权走 API 同步；`app_credentials_configured` 只表示 service_id 有可解析 App pair，不泄露 Secret |
| `GET /v2/commerce/channel-accounts/{shop_pk}` | — | one account; 404 if unknown |
| `GET /v2/commerce/channel-accounts/by-external/{shop_id}` | [`api/channel-accounts-by-external.md`](api/channel-accounts-by-external.md) | reverse-lookup by upstream shop_id; `?platform=tiktok` default; 404 if unknown |
| `GET /v2/commerce/channel-accounts/{shop_pk}/order-stats` | — | `{order_count, payment_amount_sum}` aggregate (0/0 when empty) |
| `GET /v2/commerce/channel-products` | `shop_pk`, `status`, `q`, `has_orders`, `missing_manual_cost` | SPU list: `{id, shop_pk, spu_id, title, status, source_created_at, source_updated_at}`；`q` 在服务端对 `spu_id/title` 做大小写不敏感子串过滤；`missing_manual_cost=true` 由服务端仅返回 `procurement.manual_product_costs` 中没有当前有效行的 SPU；`X-Total-Count` 返回全部服务端过滤后、分页前总数 |
| `GET /v2/commerce/channel-product-options` | required `shop_pk`; optional `q`, `spu_ids`, `limit` (1..100, default 50) | Bootstrap multi-select 的轻量 SPU 选项：`[{spu_id,title,status}]`。`q` 对 `spu_id/title` 做 ILIKE；`spu_ids` 按中英文逗号拆分后精确匹配，最多 100 个。 |
| `GET /v2/commerce/channel-products/{spu_pk}` | — | one SPU; 404 if unknown |
| `GET /v2/commerce/channel-products/{spu_pk}/variants` | — | SKU list: `{id, spu_pk, sku_id, seller_sku, variant_name}` |
| `GET /v2/commerce/sales-orders` | `shop_pk`, `status` | order list: `{id, shop_pk, order_id, status, currency, payment_amount, total_amount, order_time, order_modify_time, paid_at}` |
| `GET /v2/commerce/sales-orders/{order_pk}` | — | one order (internal id, **not** the TikTok `order_id`); 404 if unknown |
| `GET /v2/commerce/sales-orders/{order_pk}/lines` | — | order lines: `{id, order_pk, external_line_id, spu_pk, sku_pk, quantity, unit_price}` |

### Reporting (`/v2/reporting/*`)

| Endpoint | Role | Query params / body |
| --- | --- | --- |
| `GET /v2/reporting/cost-snapshots` | readonly | `spu_pk`, `cost_method`, `limit`, `offset` |
| `GET /v2/reporting/profit-daily` | readonly | `spu_pk`, `on_date`, `limit`, `offset` |
| `GET /v2/reporting/coverage` | readonly | — → `{total_spus, active_spus, costed_spus, missing_cost_spus, calculation_version}` |
| `GET /v2/reporting/missing-cost-products` | readonly | `shop_pk`, `limit` (default 200), `offset` → `{items: [{spu_pk, spu_id, title, shop_pk, missing_photo}], total_missing_photo}` |
| `GET /v2/reporting/manual-costs` | readonly | `shop_pk`, `q`, `limit`, `offset` → `{total, items}`；`q` 在服务端过滤 `spu_id/title`，`total` 是过滤后、分页前的提交记录数 |
| `POST /v2/reporting/manual-costs` | readwrite | body `{"spu_id": str, "unit_cost": decimal>0, "currency": "VND", "valid_from"?: datetime, "note"?: str}` → 201 `ManualCostOut`; auto-closes the previous effective row for the SPU |
| `GET /v2/reporting/focused-spus/{shop_pk}` | readonly | `q`, `limit` (default 50), `offset` → `{shopPk, items:[{spuPk,spuId,title,status,createdAt,updatedAt}], total, matchedTotal, limit, offset}`；按 `updated_at DESC, spu_id ASC` 稳定排序 |
| `PATCH /v2/reporting/focused-spus/{shop_pk}` | readwrite | body `{"addSpuIds": [...], "removeSpuIds": [...]}`；单次最多 500 个 ID、整批原子校验、移除为软删除；返回有界 `{shopPk,total,addedSpuIds,removedSpuIds}`。Cookie mutation 必须带 `X-Requested-With: tts-erp` |

Focused membership 按 `(shop_pk, spu_id)` 隔离；每店 active 关注总数没有业务上限。新增 ID 必须属于路径店铺；add/remove trim/去空/去重后重叠返回 422。详见 [`analytics/focused-spus.md`](analytics/focused-spus.md)。

### Intercept statistics (`/v2/intercept/requests/stats`)

`GET /v2/intercept/requests/stats` 返回 `total_requests`、白名单/今日/错误计数，以及 `by_host`、`by_method`、`by_status`、`daily`。每个分布项由后端返回 `{label, count, percentage, host|method|status}`；`percentage` 是一位小数字符串，分母始终为完整时间范围内的 `total_requests`，即使 `by_host`/`by_status` 仅返回 Top 10 也不能改用可见 bucket 之和。

Cost semantics: `MANUAL_ENTRY` (this endpoint) > 妙手采购单 > (1688 采集标价
**禁用**). See `tech-doc/refactor-tech-plan-v2.md` §6 decisions 10/12.

> Caveat (2026-08-31): the rebuild jobs behind `cost_snapshots` /
> `profit_daily` are not wired into the sync-worker scheduler yet, so
> those two tables are empty and the GETs return `[]` — expected, not a
> bug in your client.

### FX rates (`/v2/fx/*`)

Cached exchange rates + local currency conversion. Backed by the
ExchangeRate-API Standard endpoint (Free plan = **1500 requests / month,
overage billed**), synced only by the `fx.sync` sync-worker job on the
upstream's own refresh cadence (`time_next_update_utc`) — a healthy install
makes **~1 upstream request/day** and **these handlers never dial upstream**;
all reads serve the local `fx.*` cache tables and conversion math runs
locally through the snapshot base as a bridge. Design / quota budget / ops:
[`fx-exchange-rates.md`](fx-exchange-rates.md). **Agent 快速操作版（在哪查汇率、
怎么用参数换汇、红线）见 [`fx-agent-handbook.md`](fx-agent-handbook.md)。**

| Endpoint | Role | Query params |
| --- | --- | --- |
| `GET /v2/fx/latest` | readonly | `base_code` (default `USD`) → `{base_code, upstream_last_update, next_update_at, fetched_at, rate_count, stale, rates}` — `rates` maps every code to a **JSON string** rate (8 dp); `stale=true` means the cache is past the upstream refresh horizon (data ≈1 day old at most until fx.sync refetches). 404 when fx.sync has never fetched that base. |
| `GET /v2/fx/convert` | readonly | `amount`, `from_code`, `to_code`, `base_code?` (default `USD`) → `{base_code, upstream_last_update, next_update_at, stale, amount, from_code, to_code, rate, converted}` — 8-dp quantized Decimal strings. 400 on an uncached currency code; 404 when the base has no snapshot. |

```bash
# full USD-based rate map + freshness
curl -sS -H "X-API-Key: $TTS_ERP_RO_KEY" \
  "http://127.0.0.1:9877/v2/fx/latest"

# 100 CNY → USD at cached rates (no upstream call)
curl -sS -H "X-API-Key: $TTS_ERP_RO_KEY" \
  "http://127.0.0.1:9877/v2/fx/convert?amount=100&from_code=CNY&to_code=USD"
```

### Sync status (`/v2/sync/*`)

sync-worker 周期作业健康展示（dashboard「数据同步状态」卡片的数据源，2026-09-28 新增）。

| Endpoint | Role | Notes |
| --- | --- | --- |
| `GET /v2/sync/status` | readonly | → `{server_time, jobs: [{job_name, interval_seconds, last_run_at, last_finished_at, last_status, last_error, next_expected_at, lag_seconds, cycles_late, severity}]}`。周期取自 `sync_worker.scheduler.JOBS` 注册表（单一真相源），运行记录取自 `integration.sync_jobs`（tiktok 作业按 shop 扇出多行，按 job_name 聚合取最新一行）。红灯规则：`now - last_run_at >= 2 × interval_seconds` → `severity="crit"`；≥1 周期 `"warn"`；周期内 `"ok"`；从未运行或注册表外 job `"unknown"`。只读、零上游外呼。 |
| `GET /v2/sync/jobs` | readwrite | 周期任务管理页的数据源：返回 `JOBS` 定义、`enabled` 启停状态、最近运行状态，以及可手动选择的 TikTok 店铺列表。启停状态持久化在 `integration.sync_cursors` 的保留命名空间 `scheduler.job_controls`。 |
| `GET /v2/pages/sync-jobs` | readwrite | 定时任务管理 HTML 页面（侧边栏入口）。页面可查看任务；readwrite 会话可立即执行任务；admin 会话还可切换周期启停。 |
| `PATCH /v2/admin/sync-jobs/{job_name}/enabled` | admin | body `{"enabled": false}`：启用/停用周期 tick。只影响 APScheduler 自动触发；手动触发仍可执行。Cookie mutation 必须带 `X-Requested-With: tts-erp`。 |
| `POST /v2/admin/sync-jobs/{job_name}/trigger` | readwrite | body 可选 `{"shop_id": "..."}`。TikTok 店铺级任务传 `shop_id` 时只跑该店，省略则按当前授权店铺 fan-out；系统级任务不接受 `shop_id`。返回 200 accepted，表示已提交 API 后台任务；最终运行结果异步写 `integration.sync_jobs`，调用方需稍后刷新状态查看。 |

```bash
curl -sS -H "X-API-Key: $TTS_ERP_RO_KEY" \
  "http://127.0.0.1:9877/v2/sync/status"
```

### Runtime configuration (`/v2/config/runtime/*`)

Versioned JSON configuration with draft/publish/rollback and encrypted
`secret://` references. Full lifecycle and resolver contract:
[`runtime-config-management.md`](runtime-config-management.md).

| Endpoint | Role | Notes |
| --- | --- | --- |
| `GET /v2/config/runtime/items` | readwrite | Active published-state list only; never returns drafts or secret values. `?includeRetired=true` includes archived metadata. |
| `POST /v2/config/runtime/items` | readwrite | Creates a key, immutable JSON Schema and optional initial draft. |
| `GET /v2/config/runtime/items/{config_key}` | readwrite | Editor detail including draft and published reference payloads. |
| `PUT /v2/config/runtime/items/{config_key}/draft` | readwrite | Optimistic draft save; body contains `expectedDraftVersion`, `payload`, `rollout`. |
| `POST /v2/config/runtime/items/{config_key}/publish` | readwrite | Publishes the current draft as a higher immutable version. |
| `GET /v2/config/runtime/items/{config_key}/revisions` | readwrite | Revision history. |
| `POST /v2/config/runtime/items/{config_key}/rollback` | readwrite | Republishes a historical revision as a higher version. |
| `POST /v2/config/runtime/items/{config_key}/{retire,restore}` | readwrite | Soft-retire or restore a key. Retired keys are absent from snapshots and cannot be edited/published. |
| `GET /v2/config/runtime/snapshot` | readwrite | Published, rollout-selected values with ETag; secret references stay redacted. |
| `GET /v2/config/runtime/secrets`, `PUT /v2/config/runtime/secrets/{name}` | readwrite | Fingerprint/reference metadata and encrypted write only; no secret plaintext read endpoint. `?includeRetired=true` includes archived metadata. |
| `POST /v2/config/runtime/secrets/{name}/{retire,restore}` | readwrite | Soft-retire or restore a secret. Retirement is refused while an active config or draft references it. |

### Pages

| Endpoint | Role | Notes |
| --- | --- | --- |
| `GET /v2/pages/manual-costs` | readonly | Server-rendered operator console（店铺切换、全部 SPU / 最近提交、仅展示未登记 SPU 的后端筛选、人工采购价录入）。Browser without a session → 302 to `/v2/auth/login`. Static assets under `/static/*` are readonly-classified too. |
| `GET /v2/pages/spu-roi` | readonly | SPU 实际 ROI 看板。与重点关注页共享 `/static/js/spu-profitability-page.js` kernel；`/static/js/spu-roi.js` 只定义标准 PageProfile。 |
| `GET /v2/pages/focused-spus` | readonly | 重点关注 SPU。使用相同盈利汇总、表格、分页和钻取；`/static/js/focused-spus.js` 提供持久 selection adapter 与编辑器。 |
| `GET /v2/pages/shops` | readonly | 店铺注册台。人工注册插件同步店铺（`commerce.shops` 补登记）；写入走 `POST /v2/admin/shops/register`（含 App Key/Secret 均 readwrite）；行内元信息编辑走 `PATCH /v2/admin/shops/{shop_pk}`；App pair 按 service_id 加密保存；「获取授权链接」按钮走 `GET /v2/oauth/tiktok/authorize?format=json`（readwrite）。 |
| `GET /v2/pages/sync-jobs` | readwrite | 定时任务管理页。读取 `/v2/sync/jobs`；readwrite 会话可调用 `/v2/admin/sync-jobs/{job_name}/trigger` 立即提交后台执行；admin 会话还可调用 `/v2/admin/sync-jobs/{job_name}/enabled` 启停周期 tick。 |
| `GET /v2/pages/runtime-configs` | readwrite | 运行配置台：创建草稿、发布/恢复版本、管理灰度规则及加密 secret 引用。 |

### Admin (`/v2/admin/*`, handler-enforced roles)

| Endpoint | Role | Notes |
| --- | --- | --- |
| `POST /v2/admin/shops/register` | **readwrite** | 人工注册店铺。body 可含 `service_id/app_key/app_secret`；App Key/Secret 必须成对且 service_id 必填，同一事务写入 `integration.tiktok_app_credentials`，Secret 只加密存储、不返回。注册幂等。 |
| `GET /v2/admin/shops/unregistered` | **readwrite** | 列出在 `plugin.*` 插件数据里出现、但 `commerce.shops` 无行的 shop_id → `{candidates: [{shop_id, sources}]}`；注册页的候选清单。 |
| `PATCH /v2/admin/shops/{shop_pk}` | **readwrite** | 更新店铺元信息或原子配置 `service_id/app_key/app_secret`。App pair 按 service_id 共享并加密；App Key/Secret 必须成对。只改 service_id 时目标 App pair 必须已存在（否则 409），防止生成必然失败的授权链接。`credential_id`/`status` 不可修改。 |

### SPU images (`/v2/spu-images/*`)

Presigned MinIO upload flow (server never proxies bytes; design:
[`procurement-ui-redesign.md`](procurement-ui-redesign.md)):

1. `POST /v2/spu-images/upload-url` (readwrite) — body
   `{"shop_pk", "spu_pk", "filename", "content_type", "size_bytes"≤8MiB}`
   → 201 `{image_id, object_key, upload_url, upload_expires_at, required_headers}`.
2. Browser PUTs the file to `upload_url` directly.
3. `POST /v2/spu-images/{image_id}/confirm` (readwrite) — server HEAD-verifies
   the object → `{status: "ready", url, ...}`; 409 `UPLOAD_NOT_FOUND` if the
   PUT never landed.
4. `GET /v2/spu-images?spu_pk=X` (readonly) — ready images with
   presigned GET URLs.
5. `DELETE /v2/spu-images/{image_id}` (readwrite) — soft-delete, 204,
   idempotent.

### LLM context

`GET /v2/llm-context` (readonly) — self-describing system + data dictionary
for LLM agents, generated from the live PG schema. `?format=md` (default)
returns `text/markdown`; `?format=json` returns
`{schema_version, generated_at, key_role, markdown, sections}`.

### TikTok Shop Partner API read-through (`/v2/tiktok-shop/*`)

Live, **uncached** pass-throughs to the TikTok Shop Partner API
documented in `tts-partner-api-docs/`. Each call resolves the seller's
token via `proxy/token_service.load_credentials()` and then resolves the
issuing `service_id` to its encrypted App Key/App Secret pair before signing
(`shop_pk` → `shop_id` → token + shop_cipher + issuing service_id).

| Endpoint | Spec | Upstream | Required upstream scope |
| --- | --- | --- | --- |
| `GET /v2/tiktok-shop/products/{product_id}` | [`api/tiktok-shop-get-product.md`](api/tiktok-shop-get-product.md) | `GET /product/202309/products/{product_id}` | `seller.product.basic` |

The other 7 Partner API product-domain GETs in `tts-partner-api-docs/`
(Listing Prerequisites / Categories / Attributes / Brands / Category
Rules / Image Translation Tasks / Submission Records) are deferred to
separate work items — same proxy + router pattern.

### Analytics — SPU 实际 ROI (`/v2/analytics/spu-roi`)

**Stability: stable · 只读(readonly)**。按 SPU 一行的「广告消耗 → 有效销售 → 退款 → 净收入 → 货本 → 净利润 → 实际 ROI/保本线」账页数据源；页面 `GET /v2/pages/spu-roi` 与 `GET /v2/pages/focused-spus` 共用该端点和预计终局字段。

> **2026-09-29 明确兼容例外（operator-requested breaking currency migration）**：用户要求把现有盈利计算从 USD 全量切换为 CNY，因此本端点在 `/v2` 原字段名不变的前提下，将所有 money 字段的语义原地改为 CNY。部署方确认当前页面是主要消费者并接受该 breaking 变更；其他消费者必须读取 `meta.currency.display`，不能继续假定 USD。ROI/比率字段无量纲，语义不变。

**口径唯一真相** = [`analytics/spu-real-roi-dashboard.md`](analytics/spu-real-roi-dashboard.md) §4/§5(公式 M1–M19) + [`handoff/spu-roi-full-loss-rubric.md`](../handoff/spu-roi-full-loss-rubric.md)(**v9 当前**) + [`biz-doc/analytics/spu-roi-profit-calculation.md`](../biz-doc/analytics/spu-roi-profit-calculation.md);本端点只读计算并序列化,不做任何写。

Query parameters:

| name | type | default | notes |
| --- | --- | --- | --- |
| `q` | string | — | 兼容的 `spu_id` 子串搜索；页面标准调用（带 `shop_pk`）中只改变 `items/total`、不改变大盘 `totals`；不能和 `spu_ids` 同传 |
| `spu_ids` | comma-list | — | 当前店铺内按 `spu_id` 精确匹配的临时 scope，使用时 `shop_pk` 必填；同时约束 `items/total/totals`。支持 `,`/`，`、trim/去空/去重，最多 100 个且单项最多 128 字符；只有分隔符、缺 `shop_pk` 或与 `q` 同传 → 422 |
| `scope` | enum | — | 目前仅 `focused`：从 `reporting.focused_spus` 解析该店完整 active 集合，不受 100-ID/URL 长度限制；`shop_pk` 必填且不能与 `spu_ids` 同传。空集合返回空 overview，绝不回退整店 |
| `sort` | enum | `roi_real` | `roi_real` \| `spend` \| `refund_rate` \| `refund_rate_qty` \| `cancel_rate` \| `net_profit` \| `sales` \| `gmv_sales` \| `ad_count` \| `gmv_ad` \| `order_count` \| `cancelled_order_count` \| `units_sold` \| `refund_net_amount` \| `return_loss` \| `roi_breakeven` \| **`full_loss_rate`**(v8 新增);同值次级键 spend、最终 `spu_pk ASC` 保证分页稳定 |
| `order` | enum | `asc` | `asc` \| `desc`;**默认 `sort="roi_real"` 升序保持不变**——避免改 API 契约;**页面 JS 显式传 `sort=net_profit&order=asc` 实现「最亏在前」视图** |
| `limit` | int | 100 | 1..500(分页 v2 约定) |
| `offset` | int | 0 | ≥ 0 |
| `include_all` | bool | `false` | `false` 只含有广告∨有效销售∨退款的 SPU;`true` 拉全部 **ACTIVE**(status ILIKE 'activate')目录 SPU(DEACTIVATE/DELETED 等排除) |
| `shop_pk` | int | — | 店铺过滤(内部主键) |
| `fee_rate` | decimal-str | — | 临时页面覆写（仅本次请求，不持久化）；缺省按店铺取当前 `fee-v2` 实测快照，缺失/超过 7 天才回退基线 `0.308`。只作用于未结算订单 `r̂ × unsettled_sales`，已结算费用已含在 SETTLEMENT |
| `w_start` | date | — | ISO `yyyy-mm-dd`;提供 `shop_pk` 时按该店 `commerce.shops.region` 对应的 IANA 报表时区解释本地日，销售与退款按关联订单 `COALESCE(order_time, paid_at)` 的本地日期裁剪；退款跟随原订单归属（含当日） |
| `w_end` | date | — | ISO `yyyy-mm-dd`;与 `w_start` 配对使用并包含结束日；店铺地区缺失或不能唯一确定时区返回 422。未提供 `shop_pk` 的兼容性跨店查询继续按 UTC 解释日期；不提供窗口 = 销售/退款全历史累计。预计终局的已结算样本和未结算预测对象也使用同一订单时间窗口，因此时间选择会改变预测字段 |

Response envelope:`{items: [...], total, totals, meta}`。店铺报表时区仅对单时区地区码映射：VN/TH/SG/MY/PH/CN/JP/KR/GB；时区边界使用 IANA `ZoneInfo` 规则转换为 UTC 半开区间，因此支持夏令时日的 23/25 小时长度。详情端点从 `spu_pk` 反查所属店铺并使用相同边界。`spu_ids` 属于盈利范围：金额从命中 SPU 行聚合，订单/取消/退款 totals 在命中 SPU 集合内跨 SPU 去重，且 totals 不受分页影响。页面使用 Bootstrap 5 + 自托管 Tom Select Bootstrap 5 主题的原生 `<select multiple>` 选择/搜索/粘贴 SPU，点击「查询」后才应用 scope；已应用的 scope 会同步到页面 URL，刷新或分享链接后恢复。批量粘贴校验期间可点「清空」取消，最多选择 100 个 SPU。

`meta.fee` 契约：

- `source`: `user_override | shop_estimate | baseline | mixed`，表示本次范围实际费率来源；
- `mode`: **deprecated 兼容字段**，仅为旧客户端保留，值仍是 `override | baseline`；`shop_estimate`/`mixed` 映射为 `baseline`，新客户端必须读取 `source`；
- `rate`: 本次范围展示费率（4 位小数字符串；多店不同费率时 `source=mixed`，逐店真实值见 `per_shop`）；
- `override`: 页面覆写值，否则 `null`；
- `degraded` / `fallback_message`: 是否有店铺回退基线，以及由后端给出的降级说明；
- `per_shop[]`: `shop_pk/shop_name/rate/source/fallback_reason/estimate`；实测 `estimate` 包含 `calculated_on/calculated_at/lookback_days/kept_order_count/kept_line_gmv/window_line_gmv/kept_share/total_fee/currency`；
- 解析优先级：页面覆写 > 7 天内 `fee-v2` 店铺实测 > `0.308` 基线。

**当前行字段契约（页面主列仅渲染 6 列 + 商品维度，其余由下钻面板或外部分析消费）**：

| 字段 | 类型 | 公式 / 含义 | 主列? |
| --- | --- | --- | --- |
| `spu_pk` | int | `commerce.products_spu.id` 内部主键 | — |
| `spu_id` | str | 业务 SPU 编号（TikTok 端） | 商品列 |
| `title` / `status` / `main_image_url` | str | 商品维度列 | 商品列 |
| `shop_id` / `shop_name` | str/int | 店铺维度 | 商品列 |
| `ad_count` | int | M2: 投放广告数 | — |
| `ad_orders` | int | M2b: 平台出单量 | — |
| `spend` | money-str (CNY) | M1: 广告消耗 = `Σ real_cost_total`（原生 USD，按 `meta.fx` 换算 CNY） | **主列** |
| `gmv_ad` | money-str (CNY) | M3: 平台归因 GMV（原生 USD，按同一快照换算 CNY） | — |
| `roi_l0` | ratio-str/null | M4: `gmv_ad / spend`；广告系统实际 ROI 的兼容别名 | — |
| `ad_system_actual_roi` | ratio-str/null | 广告系统实际 ROI = 广告归因 GMV ÷ 广告实际消耗 | **主列** |
| `ad_system_max_ad_spend` | money-str (CNY) | 最大可承受广告费 = 预计净结算收入 − 同范围采购成本 − 结算外必要成本 | — |
| `ad_system_remaining_ad_spend_capacity` | money-str (CNY) | 最大可承受广告费 − 当前广告实际消耗；可为负 | — |
| `ad_system_breakeven_roi` | ratio-str/null | 广告归因 GMV ÷ 最大可承受广告费；分母≤0或无归因 GMV 时为 null | **主列**（`estimated_known_costs` 时前端标 `≈`） |
| `ad_system_breakeven_roi_status` | enum | 当前为 `estimated_known_costs`：结算外必要成本尚未结构化，不能解释为最终保本线 | — |
| `ad_first_day` / `ad_last_day` | date/null | 广告观测窗口 | — |
| `order_count` | int | M5b: 有效销售订单数（白名单状态，含 COD 在途） | **主列** |
| `cancelled_order_count` | int | M5c: 取消订单数（**全部 CANCELLED，信息列口径不变**；取消率不再用它，见下两行拆分） | — |
| **`domestic_cancelled_order_count`** | **int** | **v9 新增：国内取消单量 = CANCELLED ∧ 无 `tracking_events.action_code=38301`（物流未到海外）→ `cancel_rate` 分子** | — |
| **`overseas_cancelled_order_count`** | **int** | **v9 新增：海外取消单量 = CANCELLED ∧ 38301 → 已入全损件数，不进取消率** | — |
| `units_sold` | int | M5: 售出件数 | — |
| `sales` | money-str (CNY) | M6: 有效销售金额 = `Σ quantity×unit_price`（原生 VND，按 `meta.fx` 换算 CNY） | **主列**(标记为"有效GMV") |
| `gmv_sales` | money-str (CNY) | M6c: 全单 = sales + 取消原额 | — |
| `cancel_rate` | ratio-str/null | M12b **v9**: 国内取消 ÷(有效+国内取消)；海外取消已入全损不重复计（与 `full_loss_rate` 互斥） | **主列** |
| `refund_only_qty` / `refund_only_amount` | int/money | M7: 仅退款 | — |
| `refund_return_qty` / `refund_return_amount` | int/money | M8: 退货退款 | — |
| `refund_net_qty` / `refund_net_amount` | int/money | M10: M7+M8 | — |
| `refund_rate` | ratio-str/null | M12: 净额 ÷ sales | — |
| `refund_rate_qty` | ratio-str/null | M12c: 单量口径 | — |
| `refund_cancelled_qty` / `refund_cancelled_amount` / `refund_cancelled_missing_lines` | int/money/int | M9: 已付被取消信息列 | — |
| **`net_revenue`** | **money-str (CNY)** | **v8 新增：DUAL-LAYER = `Σ SETTLEMENT 分摊` + `Σ 未结 line_gmv × (1−r̂) × (1−refund_rate_spu)`；是 `net_profit` 的输入** | 下钻·结算 tab |
| **`settled_sales`** | **money-str (CNY)** | **v8 新增：`SUM line_gmv WHERE settlement_vnd IS NOT NULL`** | 下钻·结算 tab |
| **`unsettled_sales`** | **money-str (CNY)** | **v8 新增：`SUM line_gmv WHERE settlement_vnd IS NULL`** | 下钻·结算 tab |
| **`settled_order_count`** | **int** | **v8 新增：已结算订单数** | 下钻·结算 tab |
| **`full_loss_qty`** | **int** | **v9（2026-09-13 落地）：完结退货(RETURN_AND_REFUND/REFUND_ONLY，不论物流，限已付白名单订单) + 海外取消(CANCELLED∧38301) 件数** | 下钻·结算 tab |
| **`full_loss_cancelled_qty`** | **int** | **v9：其中海外取消件数（COGS 补扣基数；退货件已含在 units_sold 里不重复补扣）** | 下钻·结算 tab |
| **`full_loss_rate`** | **ratio-str/null** | **v9(D8 主列)：`full_loss_qty ÷ (units_sold + full_loss_cancelled_qty)`；分母 0 → null；不钳位（>100% 标识数据异常）** | **主列**(标记为"全损退款率%") |
| `return_loss` | money-str (CNY) | **M13b v9** = `full_loss_qty × unit_cost_used` | — |
| `unit_cost_used` | money-str (CNY) | 当前有效人工标注采购成本；未标注时使用 40 CNY/件。妙手/1688 同步货源价不参与计算 | — |
| `cost_source` | enum | `MANUAL`(当前有效人工标注采购成交价) \| `DEFAULT_K1`(40 CNY/件) | — |
| `net_profit` | money-str (CNY) | **M18 v8** = `net_revenue − (units_sold + full_loss_cancelled_qty) × unit_cost − spend`（**不**扣 platform_fee：已结费用含 SETTLEMENT，未结按 (1−r̂) 折算） | **主列** |
| `platform_fee` | money-str (CNY) | **M19 v8** = `r̂ × unsettled_sales`（**信息列，不**进 M18） | — |
| `fee_rate_used` | ratio-str | 本行实际使用的 r̂（4 位小数字符串） | — |
| `fee_source` | enum | `user_override | shop_estimate | baseline`；逐行来源，不出现聚合层 `mixed` | — |
| `profit_status` / `roi_status` | enum | 后端判定的盈利状态 `loss|profit|break_even` 与 ROI 状态 `negative|non_negative|unavailable`；前端不得从金额重新推导 | 标色 |
| `has_unsettled_orders` | bool | `settled_order_count < order_count`；包括结算数为 0 的全估算场景 | 估算标记 |
| `uses_default_unit_cost` / `refund_rate_alert` | bool | 后端判定的默认成本与高退款警戒状态；阈值见 `meta.presentation.refund_rate_alert_threshold` | 告警标记 |
| `roi_real` | ratio-str/null | **M14 v8** = `(net_revenue − return_loss) / spend` | 下钻·利润构成 |
| `roi_breakeven` | ratio-str/null | **M17 v8** = `NC′ ÷ (NC′ − COGS_kept)`（fee_est 项移除） | 下钻·利润构成 |
| `cpa` | money-str/null | M15: `spend / ad_orders` | — |

**预计终局增量字段**（不改变或覆盖上述当前字段）：

| 字段 | 类型 | 公式 / 含义 |
| --- | --- | --- |
| `projection_status` | enum | `available \| no_unsettled_orders \| insufficient_sample` |
| `projection_basis_order_count` | int | 同一订单时间窗口内有 SETTLEMENT 实际到账的样本订单数；大盘全局去重 |
| `projection_basis_qty` | int | 已结算样本商品件数 |
| `projection_basis_sales` | money-str | 已结算样本商品行销售额 CNY |
| `projection_basis_refund_amount` | money-str | 已结算样本已完结退款金额 CNY |
| `projection_terminal_basis_order_count` | int | 物流终态样本订单数：成功送达 paid 订单 + `CANCELLED` 且有 80101 退回卖家事件的订单；大盘全局去重 |
| `projection_terminal_basis_sales` | money-str | 物流终态样本商品行销售额 CNY |
| `projection_terminal_full_loss_sales` | money-str | 80101 退回卖家全损终态订单商品行销售额 CNY |
| `projection_terminal_full_loss_order_count` | int | 兼容诊断：80101 退回卖家全损订单数；大盘全局去重 |
| `projection_terminal_full_loss_qty` | int | 兼容诊断：80101 退回卖家全损订单商品件数 |
| `projection_completed_basis_order_count` | int | 权威预测分母：已结算、已送达或结果已确定的 `CANCELLED` 订单数；大盘全局去重 |
| `projection_completed_full_loss_order_count` | int | 权威预测分子：终局物流全损，或到达海外/已送达后最终全额退款的订单数；国内取消不计全损；大盘全局去重 |
| `projection_full_loss_basis_order_count` | int | 兼容字段：已送达 paid 样本订单数；保留原字段语义，不作为新预测输入 |
| `projection_basis_full_loss_order_count` | int | 兼容字段：已送达样本中存在已完成退款/退货退款 case 的订单数 |
| `projection_basis_full_loss_qty` | int | 兼容字段：已送达退款订单按 case 数量/金额折算的全损件数 |
| `projection_refund_amount_rate` | rate-str/null | 兼容诊断：退回卖家全损销售额 ÷ 全部物流终态样本销售额；不再驱动预测 |
| `pre_delivery_full_loss_rate` | rate-str/null | 兼容诊断：退回卖家全损订单数 ÷ 全部物流终态样本订单数；不再驱动预测 |
| `completed_full_loss_rate` | rate-str/null | 权威预测比例：`projection_completed_full_loss_order_count ÷ projection_completed_basis_order_count`；同时用于风险订单数、件数和收入折损，4 位小数字符串 |
| `delivered_full_loss_rate` | rate-str/null | 兼容字段：继续返回已送达退款订单数 ÷ 全部已送达 paid 样本订单数；不作为新预测输入 |
| `settled_full_loss_rate` | rate-str/null | 兼容别名：继续与 `delivered_full_loss_rate` 相同 |
| `projection_full_loss_qty_rate` | rate-str/null | 兼容别名：继续与 `delivered_full_loss_rate` 相同 |
| `unsettled_order_count` | int | 当前范围内无 SETTLEMENT 实际到账的 paid 订单数；大盘全局去重 |
| `delivered_unsettled_order_count` | int | 未结算但已送达的订单数；不进入未来全损风险池 |
| `full_loss_exposure_unsettled_order_count` | int | 未结算且尚未送达的待完结风险订单数；大盘全局去重 |
| `unresolved_unsettled_order_count` | int | 未结算且仍有未确认商品件的订单数；大盘全局去重 |
| `unresolved_unsettled_qty` | int | 未结算件数减去已确认退款/退货/全损件数 |
| `unresolved_unsettled_sales` | money-str | 待确认件数按订单行单价计算的销售额 CNY |
| `full_loss_exposure_unsettled_sales` | money-str | 尚未送达未结算风险池的商品行销售额 CNY；`completed_full_loss_rate` 只作用于此范围 |
| `confirmed_unsettled_refund_amount` | money-str | 未结算订单中已经确认的退款金额 CNY，只扣一次 |
| `confirmed_full_loss_exposure_refund_amount` | money-str | 尚未送达风险池中已经确认的退款金额 CNY；从风险池预计额度中扣除 |
| `confirmed_unsettled_full_loss_qty` | int | 未结算订单中按当前严格口径已经确认的全损件数；部分退款不计入 |
| `projected_future_full_loss_order_count` | decimal-str/null | `待完结风险订单数 × completed_full_loss_rate − 已确认风险池全损订单数`，以未确认风险订单数封顶 |
| `projected_future_full_loss_qty` | decimal-str/null | `待完结风险件数 × completed_full_loss_rate − 已确认风险池全损件数`，以未确认风险件数封顶，不为显示提前取整 |
| `projected_terminal_full_loss_qty` | decimal-str/null | 当前已观察全损件数 + 预计未来新增全损件数 |
| `projected_full_loss_cost` | money-str/null | 预计终局全损对应成本，用于预计 ROI/保本 ROI，不重复加入 COGS |
| `projected_unsettled_net` | money-str/null | 未结算销售扣已知退款、预测退款和平台费后的预计净收入 |
| `projected_net_revenue` | money-str/null | 已结算实际到账 + `projected_unsettled_net` |
| `projected_net_profit` | money-str/null | `projected_net_revenue − 当前 cogs_total − spend`；预测全损不重复扣货本 |
| `projected_nc_prime` | money-str/null | `projected_net_revenue − projected_full_loss_cost` |
| `projected_cogs_kept` | money-str/null | 当前已确认保留货本减去预计新增全损对应货本 |
| `projected_roi_real` | ratio-str/null | `projected_nc_prime ÷ spend`；广告消耗为 0 时 null |
| `projected_roi_breakeven` | ratio-str/null | `projected_nc_prime ÷ (projected_nc_prime − projected_cogs_kept)`；分母≤0或样本不足时 null |

`totals` 同样返回上述预计终局字段。金额/件数商品行事实按 scope 聚合；样本订单数、未结算订单数、待确认订单数独立按订单全局去重；比例和预计终局值按完整 scope 重新计算，不是行级比例平均值。单 SPU scope 下 `totals` 与该 SPU 的预测字段一致。

`meta.projection` 返回预测说明、日期归属、已完结样本、`refund_amount_rate_source`、`full_loss_rate_source`、目标定义和状态中文标签；`meta.warnings` 在适用时增加 `projection_insufficient_sample` 和 `projection_uses_completed_order_full_loss_rate`。未结算中已经确认的退款金额只扣一次，对应确认件数先从待确认集合排除，已完结订单全损率只作用于待完结风险池。`meta.ad_system_roi` 给出实际 ROI、最大可承受广告费和保本 ROI 的公式与范围，并明确 `mixed_real_cost` 不混入广告赠金、赠金目前不可单独取得；`meta.presentation` 返回 `rubric_label`、退款警戒阈值/文案、估算/默认成本文案和 `pnl_hints`。前端只格式化和渲染这些状态/说明，不保存业务阈值、不重算分类。净结算已扣除的平台费用不得再次扣除；结算外成本补齐前，前端以 `≈` 展示该估算。

> **2026-09-30 成本来源语义变化**：
>
> - `unit_cost_used` 只读取当前有效的 `manual_product_costs.unit_cost`；未命中时直接使用 40 CNY/件
> - 妙手/1688 同步的 `procurement_products.source_unit_cost` 不再参与 SPU ROI 计算
> - `cost_source` 当前只会返回 `MANUAL` 或 `DEFAULT_K1`；该规则覆盖下方 v8 历史成本链说明
>
> **v9 语义变化（2026-09-13，merge `3c8ea96`）**：
>
> - `cancel_rate` 只计**国内取消**（CANCELLED ∧ 无 38301）；海外取消改由全损口径承载——修复 v8 及之前两率重叠（海外取消同单重复计入取消率与全损退款率）
> - `full_loss_qty` 从「38301 ∧ (完结 case ∨ CANCELLED)」切到 v9 两桶：**完结退货不论物流**（限已付白名单订单，保住 rule 0 未归属不变量）+ 海外取消
> - 新增 2 字段：`domestic_cancelled_order_count` / `overseas_cancelled_order_count`
> - `meta.rubric_version` = `v9`；钻取 orders 的 `full_loss` 旗标同 v9（完结退货 ∨ 海外取消）
>
> **v8 语义变化（breaking relative to v5 文本）**：
>
> - `net_profit / roi_real / roi_breakeven / platform_fee` 公式重写（见 M18/M14/M17/M19）
> - `return_loss` 口径从"完结退货件 × cost"切到"全损件数 × cost"（M13b v8）
> - `cost_source` 从两值扩为四值
> - `unit_cost_used` 来源从 manual+30 兜底切到 MANUAL→PURCHASE→SOURCE_PRICE→40 兜底链
> - 新增 6 字段：`net_revenue / settled_sales / unsettled_sales / settled_order_count / full_loss_qty / full_loss_cancelled_qty / full_loss_rate`
> - 主列（2026-09-29）为 8 个经营指标：商品 + `spend` + `ad_system_actual_roi` + `ad_system_breakeven_roi` + `effective_sales` + `effective_order_count` + `cancel_rate` + `full_loss_rate` + `net_profit`；其余字段继续在 JSON 返回，由下钻面板消费

格式化约定(§5.1):**money = 4 位小数字符串（CNY）**、比率/ROI = 2 位小数字符串、件数/单量整数;`null` = 无解/除数为 0(页面显示 `—`);无投放 SPU `spend="0.0000"` + `ad_count=0`。广告原生 USD、销售/退款原生 VND 在公式入口按同一 fx 快照换算 CNY，采购成本保持 CNY；`meta.currency.display="CNY"`，`meta.fx` 同时标注 `usd_vnd/cny_usd/usd_cny/cny_vnd/vnd_cny`（键名均为 from→to；`cny_vnd` 是 1 CNY 对应 VND，`vnd_cny` 是 1 VND 对应 CNY）。`totals` = 跨分页、当前筛选的加总:`row_count`(SPU 数)、单量(`order_count` 有效单 / `cancelled_order_count` 取消单 / `total_orders` = 两者之和,跨可见 SPU 全局去重)、`gmv`(全部订单销售额 = 白名单有效 ∪ 取消订单的原始行金额;money-str)与 `spend, sales, refund_net_amount, return_loss, net_profit`(行级 CNY 服务端加总,4 位小数字符串)、`roi_real`(Σ(net_revenue−return_loss)/Σspend,Σspend=0 → null);`total` = 匹配行数。**口径注(2026-09-06 全链状态口径,COD 店)**:行级 `sales`/单量/`gmv` 全部按**订单状态**下单即算——白名单状态订单(含 COD 在途/待收款)计入 `sales` 与有效单量;取消订单只进 `gmv`/`cancelled_order_count`,不重复入 sales;净利润/退款率/ROI 等派生金额自动跟随状态口径 sales(回款前偏乐观)。窗口裁剪列 = `COALESCE(order_time, paid_at)`（优先按下单日；仅缺失 `order_time` 时回退收款日）。**主表行内列集(2026-09-29)**:**主列 = 商品 + 广告消耗CNY(`spend`) + 广告系统实际ROI(`ad_system_actual_roi`) + 广告系统保本ROI(`ad_system_breakeven_roi`) + 有效销售(`effective_sales`) + 有效单量(`effective_order_count`) + 取消率(`cancel_rate`) + 全损率(`full_loss_rate`) + 净利润**；其中广告系统保本 ROI 的 `estimated_known_costs` 状态在前端以 `≈` 明示。其余字段（通用 ROI/保本、平台佣金、全损货损金额、已结未结 GMV、退款拆分、广告归因明细、订单结构）由行内 accordion 钻取面板五 tab 顶部汇总区展示（详见下节）。`meta` 携带 fx/fee/cost_assumption/window/**`rubric_version`(v8 新增,当前值 v9)**:/"unattributed_refund_lines/computed_at/currency;`meta.window` 为 ad 视图观测窗口(供参考),销售/退款是否裁剪见 `note`。

**v9 默认值总览**：

- `sort="roi_real"`（API 契约不动；页面 JS 显式传 `sort=net_profit&order=asc`）
- `fee_rate` 不传：逐店使用 7 天内 `fee-v2` 实测快照，无可用快照时回退 `0.308`；只作用于未结算订单
- `include_all=false`、`limit=100`、`order="asc"`
- `meta.rubric_version="v9"`（口径漂移一眼定位）

Example:

```bash
curl -sS -H "X-API-Key: $KEY" \
  'http://127.0.0.1:9877/v2/analytics/spu-roi?sort=net_profit&order=asc&limit=5'  # 页面视图
curl -sS -H "X-API-Key: $KEY" \
  'http://127.0.0.1:9877/v2/analytics/spu-roi?sort=roi_real&order=asc&limit=5'  # API 默认（外部分析兼容）
```

Auth 分类细节:`/v2/analytics/spu-roi` 命中 `_READONLY_EXACT`(readonly),与 `/v2/analytics/sync/*`(readwrite,Chrome 扩展 ingest)是两条不相干的路由。

### Analytics — SPU 实际 ROI 钻取 (`/v2/analytics/spu-roi/{spu_pk}/...`)

**Stability: stable · 只读(readonly)**。v8 D6 拍板的"每 tab 懒加载"端点集——行内 accordion 展开详情面板时，前端按 `(spu_pk, tab, 窗口)` 缓存，首次激活 tab 才请求。利润构成 tab **不发请求**（主表行字段直出）。4 个端点 + 共享约定如下。

#### `GET /v2/analytics/spu-roi/{spu_pk}/orders`

订单·物流 tab 数据源。

| query | type | default | notes |
| --- | --- | --- | --- |
| `w_start` / `w_end` | date | — | 销售/退款裁剪窗口（同主表语义：均按关联订单 `COALESCE(order_time, paid_at)` 归属，含 `w_end` 当日；退款发生时间不改变所属窗口） |

Response `{spu_pk, spu_id, window, orders[], meta}`。`orders[]` 字段：`order_id, status, qty, line_gmv(CNY), paid_at, is_settled(已结 ✓/未结), settled_net_share(SETTLEMENT × 分摊比例，CNY；未结 → null), arrived_overseas(38301 命中), full_loss(**v9：完结退货(RETURN_AND_REFUND/REFUND_ONLY，不论物流) ∨ 海外取消(CANCELLED∧38301)**), shipment{status, tracking_number}, tracking[]`（按事件时间排序的 `tracking_events` 子集：`action_code, desc, event_at`）。

防呆：`orders` 上限 500 条；超限返回 `{meta.orders_truncated: true}`。404：spu_pk 不存在。金额与主表同序列化（money 4 位、CNY），`meta.currency.display="CNY"`。

#### `GET /v2/analytics/spu-roi/{spu_pk}/settlements`

结算 tab 数据源（已结订单组件拆分）。

| query | type | default | notes |
| --- | --- | --- | --- |
| `w_start` / `w_end` | date | — | 同 orders |

Response `{spu_pk, settlements[], meta}`。`settlements[]` 每条 = 一笔已结订单：`order_id, statement_time, share_ratio(该 SPU 行占整单 GMV 比例), components[]`。`components[]` = 完整 53 字段（v8 D2 零值落库后含 0 行），每条 `{code(如 SETTLEMENT/GROSS_SALES/PLATFORM_COMMISSION…), amount_vnd(VND 原值), amount(CNY 换算)}`。

未结算订单 **不**进 `settlements[]`（tab 底部由行字段 `settled_order_count / unsettled_sales` 计算一行汇总："未结算 N 单，估算净收入 $X（基线 r̂ × (1−退款率)）"）。

#### `GET /v2/analytics/spu-roi/{spu_pk}/cases`

售后 tab 数据源。

| query | type | default | notes |
| --- | --- | --- | --- |
| `w_start` / `w_end` | date | — | 同 orders |

Response `{spu_pk, cases[], meta}`。`cases[]` 每条 = `case_id, order_id(可跳订单 tab 对号), type(REFUND_ONLY/RETURN_AND_REFUND/CANCELLATION), status(未完结标黄), refund_amount, reason(code+text), updated_at`。退款金额按 `case_lines.sales_order_line_id → sales_order_lines.spu_pk` 归集（同主表 M7/M8/M9）。

#### `GET /v2/analytics/spu-roi/{spu_pk}/ads`

广告 tab 数据源（**无窗口参数**——广告全窗口累计，与主表一致）。

Response `{spu_pk, ads[], meta}`。`ads[]` 每条 = `campaign_id, spend(CNY；源数据 USD), orders, first_day, last_day`（`plugin.ad_daily` ∪ `plugin.ad_today` 聚合（`spu_roi.py::_SQL_ROI_AD` 直读））。**不含 `campaign_name`**——v8 拍板不追（同步数据无名称字段）。

#### 4 端点共享约定

- 鉴权：`_READONLY_EXACT`（readonly 角色矩阵沿用主表）
- 404：`spu_pk` 不存在
- 金额：money 4 位小数字符串（CNY，结算组件同时保留 `amount_vnd` 原值）；比率 2 位
- 窗口：`w_start/w_end` 与主表同语义；`tracking / settlement / cases` 随订单走，不单独裁剪；`ads` 无窗口
- 缓存：前端按 `(spu_pk, tab, 窗口)` 缓存；主表筛选变化 → 清缓存
- 错误：参数非法 → 422；未授权 → 401/403（沿用 auth middleware 矩阵）

### Analytics Sync (`/v2/analytics/sync/*`)

Mounted under tts-erp at `/v2/analytics/sync/*`（2026-09-02 从
`/v1/analytics/sync/*` 单挂载硬切，无 /v1 别名；再早的 standalone
:9878 进程已于 2026-08-30 退役）。Powers the `tk-adv-cost-monitor` Chrome
extension. Auth requires **readwrite** role plus a per-seller scope grant
(the api_key's `scopes` array). Full protocol lives in
[`analytics/dump-architecture.md`](analytics/dump-architecture.md);
[`analytics/range-aggregate-history-sync.md`](analytics/range-aggregate-history-sync.md)
is the v3 区间聚合方案（2026-09-07 起，插件端 v3 + 服务端双模式）；
this section is the agent-facing quick reference.

#### `GET /v2/analytics/sync/cursor`

双模式预检（dump architecture + v3 range-aggregate）：

- **v3 coverage（带 `kind=history|today` + `campaignId`）**：返回该
  `(scope, endpoint, campaign)` 的 live 行状态 `{kind, hasRow, dayStart,
  dayEnd, capturedAt}`。plugin 据此决策 history 是否已 settled（无行 / 区间
  不匹配 → 抓取整段；精确覆盖 → 跳过）。
- **legacy has-data（无 `kind` + `day`）**：查该
  `(scope, endpoint, day[, campaignId])` 是否已被覆盖（daily 单日 或 live 区间含该日）。
  `hasData: true` → 跳过该天的抓取（防风控）。

work-list 模式（`items` / `nextRequiredDay` / `pageSize` / `cursor` /
`timezone`）已随 dump architecture 删除（见 `analytics/dump-architecture.md`）。

Query parameters:

| name | type | notes |
| --- | --- | --- |
| `sellerId` | string | required, ≤ 128 chars |
| `advertiserId` | string | required, ≤ 128 chars |
| `endpoint` | string | required；必须在 dump 白名单（见下） |
| `kind` | string | optional；`history`/`today` 时启用 v3 coverage 模式 |
| `campaignId` | string | coverage 模式必带；legacy 模式可选 |
| `dayStart` / `dayEnd` | date | optional（coverage 模式请求冗余回显） |
| `day` | date | legacy 模式必带, `YYYY-MM-DD` |

`endpoint` 白名单（server 据此推导 `storageKey`）：

- `/oec_ads/shopping/v1/oec/stat/post_product_list` → `productAnalyses`
- `/oec_ads/shopping/v1/oec/stat/post_session_list` → `sessionAnalyses`
- `/oec_ads/shopping/v1/oec/stat/campaign_opt_log_list` → `campaignChangeLogs`

白名单外的 endpoint → `400 SCHEMA_INVALID`。coverage 模式缺 `campaignId` /
非法 `kind` → `400 SCHEMA_INVALID`。

coverage 响应示例（`code: 0`）：

```json
{
  "code": 0,
  "requestId": "req-…",
  "data": {
    "endpoint": "…/post_product_list",
    "storageKey": "productAnalyses",
    "kind": "history",
    "hasRow": true,
    "campaignId": "campaign-1",
    "dayStart": "2026-07-01",
    "dayEnd": "2026-09-05",
    "capturedAt": "2026-09-05T12:00:00.000Z"
  }
}
```

legacy has-data 响应仍为 `{day, endpoint, storageKey, hasData[, campaignId]}`。

#### `GET /v2/analytics/sync/coverage`

批量 coverage 查询（方案 B）：一次返回所有 campaign 的日级覆盖数据。

Auth：**readwrite** + per-seller scope grant（同 `/cursor` 和 `/dumps`）。

Query parameters:

| name | type | notes |
| --- | --- | --- |
| `sellerId` | string | required, ≤ 128 chars |
| `advertiserId` | string | required, ≤ 128 chars |
| `endpoint` | string | required；必须在 dump 白名单（同 `/cursor`） |
| `kind` | string | required；仅 `daily` |
| `startDay` / `endDay` | date | `kind=daily` 时必带，`YYYY-MM-DD`；`startDay` ≤ `endDay` |
| `campaignId` | string[] | optional；可重复传入本轮完整计划集合。服务端会为没有任何覆盖的计划返回空 `coveredPeriods`，避免冷启动计划被误判为响应缺失 |

`endpoint` 白名单（同 `/cursor` 和 `/dumps`）：

- `/oec_ads/shopping/v1/oec/stat/post_product_list` → `productAnalyses`
- `/oec_ads/shopping/v1/oec/stat/post_session_list` → `sessionAnalyses`
- `/oec_ads/shopping/v1/oec/stat/campaign_opt_log_list` → `campaignChangeLogs`

响应示例（`kind=daily`）：

```json
{
  "code": 0,
  "requestId": "req-…",
  "data": {
    "kind": "daily",
    "endpoint": "/oec_ads/…/post_product_list",
    "storageKey": "productAnalyses",
    "startDay": "2026-01-01",
    "endDay": "2026-10-08",
    "totalRequested": 281,
    "campaigns": {
      "campaign-1": {
        "coveredPeriods": ["2026-01-01", "2026-01-02", "..."],
        "totalCovered": 280
      },
      "campaign-2": {
        "coveredPeriods": ["2026-03-15", "2026-03-16", "..."],
        "totalCovered": 207
      }
    }
  }
}
```


#### `POST /v2/analytics/sync/dumps`

单 dump 写入（dump architecture，2026-09-02 起；旧 `/batches` 批量协议
已下线 404）。一次请求 = 一次完整 HTTP 交换的原始落库
（`plugin.ad_raw_log`，原始请求日志）。plugin **严禁批量**：一页一
dump、一页一发，永不把 N 页 buffer 成一批（见
`analytics/dump-architecture.md` D2）。

Body（≤ 2 MB）：

```json
{
  "protocolVersion": 3,
  "requestId": "req-…",
  "scope": {"sellerId": "seller-1", "advertiserId": "adv-1"},
  "dump": {
    "endpoint": "/oec_ads/shopping/v1/oec/stat/post_product_list",
    "method": "POST",
    "kind": "history",
    "dayStart": "2026-07-01",
    "dayEnd": "2026-09-05",
    "campaignId": "campaign-1",
    "request": {"url": "…", "headers": {}, "body": {}},
    "response": {"status": 200, "headers": {}, "body": {"data": []}},
    "capturedAt": "2026-09-05T12:00:00.000Z"
  }
}
```

- v3（`protocolVersion: 3`）：`kind` ∈ `history`/`today`，`dayStart`/`dayEnd`
  必带；`day` 保留为兼容冗余（必须 == `dayEnd`）。每 `(scope, endpoint,
  campaign, kind)` 至多一行 live（Design A 快照）。
- v2（`protocolVersion: 2`，无 `kind`/区间）：legacy daily 单日写入兼容
  （`day_start=day_end=day`），首个覆盖它们的 v3 history 写入时被同事务折叠。
- `request` / `response` = plugin 抓的完整 HTTP 交换（JSONB 原样落 `ad_raw_log`）。
- `capturedAt` 必须带时区（`Z` 或 `+00:00`）。
- 不带 `page`（隐式 = 1）/ `expectedPageCount` / `storageKey` /
  `sourceRecordId` —— 这些概念在 dump architecture 已删除；`storageKey`
  由 server 从 `endpoint` 推导。

幂等（server 自算 canonical key）：

- v3：6 字段 SHA-256 `(sellerId, advertiserId, storageKey, campaignId, kind,
  dayStart, dayEnd)` + capturedAt 单调守卫 —— 同一 live 行重放 → `updated`；
  更旧 capturedAt 的迟到重试 → `stale_ignored`（视为成功，不覆盖新快照）。
- v2：6 字段 SHA-256（含 day + page=1），daily 行重放 → `duplicate`。

Success response (`code: 0`):

```json
{
  "code": 0,
  "requestId": "req-…",
  "data": {
    "idempotencyKey": "<64-char lowercase hex>",
    "status": "inserted"
  }
}
```

`status ∈ {"inserted", "updated", "duplicate", "stale_ignored"}` — 全部视为成功。
> **2026-09-11 变更**：原 `api_managed` 状态已移除。当时该状态会把已 OAuth 授权店铺的广告 dump
> **全域静默忽略**，但广告数据没有 server-side 同步路径（JOBS 里无 ad job），等于把唯一数据来源
> 挡掉。现插件数据统一落 `plugin.*` schema，与 API 同步数据（`commerce.*` 等）按 schema 物理隔离，
> 不再需要「来源判定」来决定是否拦截。

~~内容被取代事件（history 替换/推进/重建、daily 折叠、today 跨天 reset）写
`analytics.ad_sync_audit` 一行元数据审计（与主写同事务；30s today 常规刷新不写）。~~
**（2026-09-11 起失效：v3 遗留对象已由 migration 0020 删除）**：v4 逐日协议不做取代审计 ——
`ad_daily` 每日一行按自然键 upsert，允许 TikTok 延迟归因后的指标校准；
`ad_today` 用 `ON CONFLICT DO UPDATE` 原地刷新；2026-09-19 起跨天固化
job (`plugin.ad_merge_today2daily`) 已停用——ad_today 当前无清理路径，作为未来
merge job 重新启用后的回填目标保留（v8.1 起 ROI 看板只读 `ad_daily`）。

Errors:

| code | meaning | retry? |
| --- | --- | --- |
| 400 `MALFORMED_JSON` / `SCHEMA_INVALID` / `UNSUPPORTED_PROTOCOL_VERSION` | body 解析 / 校验 / 协议版本 | no |
| 400 `RESPONSE_TOO_LARGE` | 单 dump `response` > 256 KB | no (split) |
| 401 | missing or invalid Bearer token | no |
| 403 `SCOPE_DENIED` | scope mismatch | no |
| 413 `PAYLOAD_TOO_LARGE` | body > 2 MB | no (split) |
| 429 | rate limited | yes (after `Retry-After`) |

`SCHEMA_INVALID` 响应带结构化 `errors[]`（`loc`/`msg`/`type` 安全三元组，
无 input/ctx）；其余错误码不带 `errors` 字段。

### Order Sync (`/v2/order-sync/*`)

Chrome 扩展订单/物流/结算数据同步端点。插件从 TikTok Seller Center 抓取的
HTTP 响应通过此端点写入后端 plugin schema。Auth requires **readwrite** role。
设计文档：`tech-doc/chrome-ext-order-sync-design.md`。

#### `POST /v2/order-sync/has-data`

批量查业务表存在性。插件拿到 order_id 列表后，一次请求查出哪些已有数据。
物流域用它减少 N+1 详情请求；订单域在 checkpoint 丢失、窗口变化或轮转精确
巡检时用它识别服务端缺失订单。`covered=true` 只代表存在，不代表数据新鲜，
订单/物流/结算的热区刷新不能被它阻断。

Body：

```json
{
  "scope": {"sellerId": "...", "shopId": "..."},
  "domain": "logistics",
  "ids": ["order-1", "order-2", "order-3"]
}
```

- `domain` ∈ `{orders, logistics, statements}`
- `ids` 最多 500 个

响应（`code: 0`）：

```json
{
  "code": 0,
  "requestId": "req-...",
  "data": {
    "domain": "logistics",
    "covered": {"order-1": true, "order-2": false, "order-3": true}
  }
}
```

#### `POST /v2/order-sync/dumps`

接收 dump → inline 解析 → 写业务表，并记录 `plugin_logs` 健康指标。每个 dump
仍携带一次 TikTok HTTP 交换的完整原始响应，供解析使用和诊断；当前订单同步
接口不再写入旧的 `raw_log` 表。

Body（≤ 2 MB）：

```json
{
  "protocolVersion": 1,
  "requestId": "req-...",
  "scope": {"sellerId": "...", "shopId": "..."},
  "dump": {
    "domain": "logistics",
    "mainOrderId": "order-1",
    "endpoint": "/api/v1/fulfillment/logistic_detail/list",
    "method": "GET",
    "request": {"params": {"main_order_id": "order-1"}, "body": null},
    "response": {"status": 200, "body": {"code": 0, "data": {"package_list": [...]}}},
    "capturedAt": "2026-09-08T10:00:00.000Z"
  }
}
```

- `domain` ∈ `{orders, logistics, statements}`
- `logistics` 域必须带 `mainOrderId`
- `statements` 域根据响应体自动判断 list / transaction detail
- `capturedAt` 必须带时区

成功响应（HTTP 200，`code: 0`）：

```json
{
  "code": 0,
  "requestId": "req-...",
  "data": {}
}
```

- `rowsWritten` 仅作为服务端内部健康指标写入 `plugin_logs`，不是客户端成功判定条件。
- 解析失败返回 HTTP 422 / `PARSE_ERROR`；`dump.response.body` 为 `null` 返回 HTTP 422 /
  `EMPTY_RESPONSE_BODY`，客户端不得推进该条队列。
- **2026-09-11**：原 `api_managed` 状态已移除（连同 `commerce.shops.data_source` 列与
  `api/deps.py::shop_is_api_managed` 守卫）。插件与 API 同步数据现按 schema 物理隔离
  （`plugin.*` vs `commerce.*`/`fulfillment.*`/`finance.*`），不再需要来源判定。
- `parse_error` 时 `rowsWritten=0`，`parseError` 字段含原因
- 订单域的 `statements` 请求可以使用单条 statement 对象作为 `response.body`；同一
  `statement_id` 的多个 `statement_version` 在 `has-data` 中以数组传递。订单、物流、
  结算均按可变数据刷新，`has-data` 不作为更新闸门。

#### `POST /v2/order-sync/reconcile`

订单和物流共用的查询接口。物流返回可恢复游标分页及明确终态的包裹候选；
订单返回服务端总数、锚点和排序诊断信息，保留用于观测和旧版本兼容，但不得
把订单 `serverTotal` 与 TikTok 当前滚动窗口 `total_count` 直接比较，也不得仅
凭此字段触发订单全量上传。订单增量真值由插件本地 TikTok checkpoint 提供；
checkpoint 异常或精确巡检时，插件按页调用 `has-data` 只补缺失订单。

请求：

```json
{
  "protocolVersion": 1,
  "scope": {"sellerId": "...", "shopId": "..."},
  "domains": ["orders", "logistics"],
  "orders": {
    "pageSize": 20,
    "sortInfo": "6",
    "anchorPositions": [0, 400, 899],
    "hotWindowSize": 40
  },
  "logistics": {"limit": 500, "cursor": null}
}
```

响应关键字段：

```json
{
  "code": 0,
  "data": {
    "orders": {
      "serverTotal": 900,
      "anchors": [{"position": 0, "orderId": "order-1"}],
      "canIncremental": true,
      "offsetSafe": true,
      "ordering": {"field": "order_time", "direction": "desc", "tieBreaker": "order_id"},
      "hotWindowSize": 40
    },
    "logistics": {
      "complete": false,
      "items": [{
        "orderId": "order-1",
        "packageIds": ["package-1"],
        "isTerminal": false,
        "terminalReason": null,
        "nextCheckAt": null
      }],
      "nextCursor": "500"
    }
  }
}
```

订单 reconcile 返回的 `offsetSafe` / `canIncremental` 仅作诊断和兼容旧客户端，
不直接作为订单客户端的增量门禁；客户端根据本地 checkpoint、当前窗口、锚点、
排序方向和游标分页的逻辑位置决定快速路径或安全修复路径。安全修复可以读取全量
当前列表，但应通过 `has-data` 避免无条件重复上传服务端已有订单。`has-data` 请求失败时
必须保守上传当前页，不能把未知状态判为已覆盖。物流只有所有已知包裹均命中
明确终态时才跳过，未知状态和缺少包裹记录的订单继续返回为候选。

### Misc

#### `GET /healthz`

Public (auth-exempt; anonymous so also unbucketed by the rate limiter).
Returns the service fingerprint, e.g.:

```json
{"status":"ok","service":"tts-erp-v2","auth_mode":"enforce"}
```

`service: "tts-erp-v2"` is how smoke tests tell v2 apart from the retired
v1 service (which returned a bare `{"status":"ok"}`).

#### `GET /endpoints`

Public. Lists every registered route (`{path, methods, name}`) — useful
as a discovery surface for ops.

#### `GET /openapi.json` / `GET /docs` / `GET /redoc`

Public. Auto-generated OpenAPI schema + Swagger UI + ReDoc UI. All are in
`EXEMPT_PATHS` in `tts_erp_v2/middleware/auth.py`.

> Production hardening: if you don't want browsers poking at the schema,
> restrict these at the reverse proxy. The service itself does not gate
> them.

## Error responses

| status | meaning |
| --- | --- |
| 400 | malformed query / body validation failed |
| 401 | missing / invalid / expired credential |
| 403 | key lacks required role (or analytics scope) |
| 404 | resource not in local DB |
| 409 | domain conflict (e.g. SPU image confirm before upload) |
| 429 | rate limit exceeded (see Rate Limiting) |
| 500 | unexpected server error |

Standard FastAPI error responses are JSON: `{"detail": "<message>"}`.
The analytics_sync sub-API uses its own envelope
(`{code, message, requestId, retryable}`) — see its protocol doc.

## Creating / managing API keys

Use `api_keys.py` (talks to PG directly, table `security.api_keys`):

```bash
# create a readonly key
python3 api_keys.py create --role readonly --name "external-orders-reader"

# create a readwrite key (for sync / mutation endpoints)
python3 api_keys.py create --role readwrite --name "external-sync"

# list all keys (prefix/role/usage — never hashes or plaintext)
python3 api_keys.py list

# disable a key
python3 api_keys.py revoke --prefix "ttserp_ro_abc123"

# rotate: mint a fresh key with same name/role, revoke the old one
python3 api_keys.py rotate --prefix "ttserp_ro_abc123"
```

The full key is shown ONCE on creation. Store it securely.

## Versioning

- New business endpoints ship under `/v2/...`. A new query parameter or
  response field is backwards-compatible; removing/renaming/changing the
  meaning of a field is a breaking change and requires a new version
  prefix.
- **Documented exception:** `/v2/analytics/spu-roi` received an explicit operator-approved in-place currency migration on 2026-09-29: existing money fields changed from USD to CNY and now declare `meta.currency.display="CNY"`. This is the only current exception; new breaking changes still require a new version prefix.
- `/v2/analytics/sync/*` keeps its own envelope for the deployed Chrome
  extension（success `{code, requestId, data}` / error
  `{code, message, requestId, retryable}`，与 `/v2` 其余端点的裸 JSON 不同）——
  payload `protocolVersion` ∈ {1, 2} 均接受（dump 单 object 形状），契约 frozen。
  2026-09-02 前该 API 挂在 `/v1/analytics/sync/*`，已硬切下线（404）。
  2026-09-02 dump architecture 将 `/batches` 换为 `/dumps`（404 无别名）。

## Examples

### Resolve a shop and list its recent orders

```bash
KEY=$(cat ~/.tts-erp-key)
ACCT=$(curl -sS -H "X-API-Key: $KEY" \
  "http://127.0.0.1:9877/v2/commerce/channel-accounts?platform=tiktok" \
  | jq -r '.[0].id')
curl -sS -H "X-API-Key: $KEY" \
  "http://127.0.0.1:9877/v2/commerce/sales-orders?shop_pk=$ACCT&limit=20" \
  | jq -r '.[] | [.order_id, .status, .payment_amount, .currency] | @tsv'
```

### Submit a manual cost for an SPU

```bash
curl -sS -X POST -H "Authorization: Bearer $KEY" \
  -H "Content-Type: application/json" \
  -d '{"spu_id":"1730000000000000001","unit_cost":"12.40","currency":"VND","note":"1688 议价后价"}' \
  "http://127.0.0.1:9877/v2/reporting/manual-costs"
```

### Analytics cursor has-data poll (Chrome extension pattern)

```bash
curl -sS -H "X-API-Key: $KEY" \
  "http://127.0.0.1:9877/v2/analytics/sync/cursor?sellerId=seller-1&advertiserId=adv-1&endpoint=%2Foec_ads%2Fshopping%2Fv1%2Foec%2Fstat%2Fpost_product_list&day=2026-08-23" \
  | jq -r '.data | [.day, .storageKey, (.hasData|tostring)] | @tsv'
```

## Stability matrix

Stable external endpoints (safe to build dashboards / agents on):

| endpoint | role | stability |
| --- | --- | --- |
| `GET /healthz` | public | stable |
| `GET /endpoints` | public | stable |
| `GET /openapi.json`, `/docs`, `/redoc` | public | stable (consider proxy-restricting in prod) |
| `GET /v2/commerce/*` | readonly | v2 |
| `GET /v2/reporting/*` | readonly | v2 |
| `POST /v2/reporting/manual-costs` | readwrite | v2 |
| `GET /v2/fx/latest`, `/v2/fx/convert` | readonly | v2 — cached (fx.sync ≈1 上游请求/天，API 路径零上游) |
| `GET /v2/sync/status` | readonly | v2 — sync-worker 周期作业健康（红灯 = 落后 ≥2 周期） |
| `GET /v2/pages/manual-costs` | readonly | v2 (HTML — not a machine contract) |
| `GET /v2/pages/spu-roi` | readonly | v2 (HTML — not a machine contract) |
| `GET /v2/analytics/spu-roi` | readonly | stable 只读（口径见 `analytics/spu-real-roi-dashboard.md`） |
| `GET /v2/spu-images`, upload/confirm/delete | readonly / readwrite | v2 |
| `GET /v2/llm-context` | readonly | v2 (content evolves with the schema) |
| `GET\|POST /v2/auth/*` | public | v2 |
| `GET /v2/analytics/sync/cursor`, `POST /v2/analytics/sync/dumps`, `GET /v2/analytics/sync/coverage` | readwrite + scope | analytics（自有 envelope，frozen） |

Retired endpoints (404; do NOT build on these):

- Since 2026-09-30 / migration 0044: every `/v2/linkage/*` endpoint.
- Since the 2026-08-29 hard switch: `GET /db/*` (24 read endpoints), `POST /orders/*` (search + write
proxies), `GET /orders/{id}/*`, `GET /finance/*`, `POST /sync/*`,
`GET /token/{shop_id}`, `GET /shops*`, `POST /returns/search`,
`POST /cancellations/search`, `GET /miaoshou/{domain}/{method}`,
`POST /miaoshou/callback/*` (dispatcher code remains in
`miaoshou/callbacks/` but no route is mounted in the v2 app).
