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
Every endpoint other than the explicitly-public ones requires one of two
credential kinds:

- **Programmatic access (unchanged)** — an API key via
  `Authorization: Bearer <key>` or `X-API-Key: <key>` (Chrome extension,
  scripts, integrations).
- **Human / browser access** — username + password login that mints a
  server-side session cookie `tts_erp_session` (see
  [Browser session login](#browser-session-login)). The old browser API-key
  login (`POST /v2/auth/login` with `{"key": ...}`) has been **removed**.

| What you want | Endpoint | Role |
| --- | --- | --- |
| Service liveness / fingerprint | `GET /healthz` | public |
| Discover every route | `GET /endpoints` | public |
| Auto-generated schema | `GET /openapi.json`, `/docs`, `/redoc` | public |
| LLM-oriented system + data dictionary | `GET /v2/llm-context` | readonly |
| List shops (→ internal `shop_pk`) | `GET /v2/commerce/channel-accounts` | readonly |
| Look up shop by upstream shop_id | `GET /v2/commerce/channel-accounts/by-external/{shop_id}` | readonly — see [`docs/api/channel-accounts-by-external.md`](channel-accounts-by-external.md) |
| List / get TikTok products (SPU) | `GET /v2/commerce/channel-products[/{id}[/variants]]` | readonly |
| List / get orders (+ lines) | `GET /v2/commerce/sales-orders[/{id}[/lines]]` | readonly |
| TikTok Shop product detail (read-through) | `GET /v2/tiktok-shop/products/{product_id}` | readonly — see [`docs/api/tiktok-shop-get-product.md`](tiktok-shop-get-product.md) |
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
| SPU 盈利看板主表 | `GET /v2/analytics/spu-roi` | readonly — 实现/接口见 [`../design/spu-profitability-technical-design.md`](../design/spu-profitability-technical-design.md)；业务公式/日期口径见 [`../business/spu-profitability.md`](../business/spu-profitability.md) |
| SPU 实际 ROI 页面 (HTML) | `GET /v2/pages/spu-roi` | readonly (browser → 302 login) |
| 重点关注 SPU 页面 (HTML) | `GET /v2/pages/focused-spus` | readonly (browser → 302 login) |
| SPU image list / upload / delete | `GET /v2/spu-images`, `POST /v2/spu-images/upload-url`, `POST /v2/spu-images/{id}/confirm`, `DELETE /v2/spu-images/{id}` | readonly / readwrite |
| TikTok video publish workflow | `GET /v2/pages/video-publish`, `GET /v2/video-publish/config`, `GET /v2/video-publish/tasks[/{id}]`, `POST /v2/video-publish/tasks`, upload-url, confirm, cancel, retry, replace-upload, verify, `/cleanup/retry` | session: page:video-publish + readonly/readwrite action tier; API key: admin by default; owner-scoped |
| Browser login / logout / whoami | `GET\|POST /v2/auth/login`, `POST /v2/auth/logout`, `GET /v2/auth/me` | public |
| Change own password | `POST /v2/auth/change-password` | session user (cookie) |
| User & role administration | `GET\|POST /v2/users`, `GET\|PATCH /v2/users/{id}`, `POST /v2/users/{id}/password`, `GET\|DELETE /v2/users/{id}/sessions[/{sessionId}]`, `GET\|POST /v2/roles`, `PATCH\|DELETE /v2/roles/{code}` | **admin** + `page:users` 权限点 |
| Intercept request statistics | `GET /v2/intercept/requests/stats` | readonly |
| Analytics cursor has-data / dump ingest (Chrome ext) | `GET /v2/analytics/sync/cursor`, `POST /v2/analytics/sync/dumps` | readwrite + scope |
| Order / logistics reconcile and dump ingest (Chrome ext) | `POST /v2/order-sync/{reconcile,has-data,dumps}` | readwrite + scope |
| Start TikTok seller authorization | `GET /v2/oauth/tiktok/authorize` | **readwrite** or above (handler-enforced) |
| TikTok OAuth redirect target (new-shop onboarding) | `GET /v2/oauth/tiktok/callback?code&state` | **public** — see [`docs/api/tiktok-shop-oauth.md`](tiktok-shop-oauth.md) |

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

Two credential families share one authorization layer
(`tts_erp_v2/access/`, route → minimum role matrix `required_role()`):

1. **用户名 + 密码 → 服务端会话 cookie `tts_erp_session`**（人类操作者 / 浏览器）。
   登录、登出、会话管理见 [Browser session login](#browser-session-login)；
   设计基准 [`user-account-authz-design.md`](../design/user-account-authz-design.md)。
   会话用户的授权档位取其角色的 `api_tier`（`readonly|readwrite|admin`），
   走**同一张路由角色矩阵**；页面路由以及视频发布 API 还要求对应的
   `page:<id>` 权限点（视频发布为 `page:video-publish`）。
2. **API key（程序化访问，不变）** — `Authorization: Bearer <key>` 或
   `X-API-Key: <key>`，两 header 形态：

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
- `403 requires page:<id>` — session user lacks the page permission point
  (page routes, `/v2/video-publish*` → `page:video-publish`, and the
  `/v2/users*` / `/v2/roles*` APIs, which map to `page:users`); API-key
  credentials are not subject to page permission points

The mode is set by env `TTS_ERP_AUTH_MODE=off|shadow|enforce`. In
`enforce` (production default since 2026-08-20) the service returns the
error; in `shadow` the would-deny is only logged and never redirected or
rejected; `off` bypasses middleware and handler role gates entirely
(development only). Invalid mode values are logged and fail closed as `enforce`.
Enforced 401/403 requests, including browser 302 attempts, consume the shared
denied-request rate-limit budget before response presentation is selected.

## Browser session login

（用户名 + 密码 → 服务端会话 cookie `tts_erp_session`）

For human operators the browser login is **username + password** backed by
server-side session records (`security.users` / `security.user_sessions`,
design: [`user-account-authz-design.md`](../design/user-account-authz-design.md)).
There is **no self-registration** — accounts are created by an admin via the
user management API/CLI. The old API-key browser login
(`POST /v2/auth/login` with `{"key": ...}`, HMAC cookie `tts_session`,
[`browser-login-design.md`](../archive/browser-login-design.md)) has been **removed**;
old-format cookies are treated as unauthenticated (no transition compatibility).

Programmatic access is unaffected: API keys (`Bearer` / `X-API-Key`) keep
working exactly as before. If a request carries both a session cookie and an
API key, the **cookie wins**; the API key is the fallback (curl / extension
scenarios).

### Auth endpoints (`/v2/auth/*`)

Request and response bodies are camelCase JSON; errors use the standard
`{"detail": "..."}` body (see [Error responses](#error-responses)). Successful
login/logout complete parsing + persistence (`2xx` per repo semantics).

| Method | Path | Auth | Request body | Response |
| --- | --- | --- | --- | --- |
| `GET` | `/v2/auth/login` | public | — (query `?next=`) | 200 login form (HTML). `next` is open-redirect-guarded (same-origin absolute path only), default `/v2/pages/dashboard` |
| `POST` | `/v2/auth/login` | public (IP rate-limited) | `{username, password, next?}` | 200 `{ok: true, username, displayName, role, pages}` + `Set-Cookie: tts_erp_session=...`；`role` = api_tier 文本，`pages` = 有效页面权限点 id 列表 |
| `POST` | `/v2/auth/logout` | public (idempotent) | — | 204；吊销服务端会话 + 清 cookie（同 Path） |
| `GET` | `/v2/auth/me` | public (handler self-checks cookie) | — | `{authenticated: false}` 或 `{authenticated: true, username, displayName, role, roles, pages}`（`role` = api_tier；吊销/禁用即时反映） |
| `POST` | `/v2/auth/change-password` | session user (cookie) | `{oldPassword, newPassword}` | `{ok: true}`；成功后吊销该用户**其他**会话 |

Login errors: `401 {"detail":"用户名或密码错误"}` (uniform message, no user
enumeration); `429 {"detail":"too many login attempts","retry_after_s":N}`
(IP sliding window, `TTS_ERP_LOGIN_RATE_LIMIT`, default 10/min);
`503 {"detail":"auth store unavailable: ..."}` (fail closed).
`change-password`: 401 `{"detail":"未登录"}` without a valid session;
400 `{"detail":...}` on wrong old password / password-policy violation.

### Session cookie `tts_erp_session`

- Opaque random token (`v2.`-prefixed); the DB stores only `sha256(token)`
  (`security.user_sessions`) — a DB leak does not leak usable sessions.
- Attributes: `HttpOnly; Secure; SameSite=Lax; Path=<root_path>` (production
  `/tts`, derived from `TTS_ERP_EXTERNAL_PREFIX`; never `Path=/`), host-only
  (no `Domain`). Logout deletes the cookie with the identical Path.
- TTL: `TTS_ERP_SESSION_TTL` (default 43200 s = 12 h, fixed expiry, no
  sliding renewal). `TTS_ERP_SESSION_SECURE=0` for local http dev.
- Lifecycle: logout revokes the current session; disabling a user revokes all
  of their sessions; password reset/change revokes the others; expiry is
  enforced per request (`revoked_at IS NULL AND expires_at > now()` plus
  `users.status = 'active'`).

Browser navigations (`Accept: text/html`) that fail auth get a **302** to
`/v2/auth/login?next=...` instead of a JSON 401. Cookie-authed
POST/DELETE requests must carry `X-Requested-With: tts-erp` (CSRF guard;
double-checked with `SameSite=Lax` + JSON-only write APIs + default-deny
CORS). HTML page routes additionally return a friendly **403 page** (not a
302) when the user is logged in but lacks the `page:<id>` permission point.

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
| `GET /v2/commerce/channel-accounts/by-external/{shop_id}` | [`api/channel-accounts-by-external.md`](channel-accounts-by-external.md) | reverse-lookup by upstream shop_id; `?platform=tiktok` default; 404 if unknown |
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

Focused membership 按 `(shop_pk, spu_id)` 隔离；每店 active 关注总数没有业务上限。新增 ID 必须属于路径店铺；add/remove trim/去空/去重后重叠返回 422。详见 [`analytics/focused-spus.md`](../design/focused-spus.md)。

### Intercept statistics (`/v2/intercept/requests/stats`)

`GET /v2/intercept/requests/stats` 返回 `total_requests`、白名单/今日/错误计数，以及 `by_host`、`by_method`、`by_status`、`daily`。每个分布项由后端返回 `{label, count, percentage, host|method|status}`；`percentage` 是一位小数字符串，分母始终为完整时间范围内的 `total_requests`，即使 `by_host`/`by_status` 仅返回 Top 10 也不能改用可见 bucket 之和。

Cost semantics: `MANUAL_ENTRY` (this endpoint) > 妙手采购单 > (1688 采集标价
**禁用**). See `docs/archive/refactor-tech-plan-v2.md` §6 decisions 10/12.

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
[`fx-exchange-rates.md`](../design/fx-exchange-rates.md). **Agent 快速操作版（在哪查汇率、
怎么用参数换汇、红线）见 [`fx-agent-handbook.md`](../guides/fx-agent-handbook.md)。**

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
| `GET /v2/sync/freshness?shop_pk=` | readonly | SPU ROI 页头四类同步时间。→ `{server_time, shop_pk, shop_id, sources: [{key, label, synced_at, scope, basis, job_name, last_status, severity, detail}]}`，`sources` 固定为 `ads` / `orders` / `logistics` / `miaoshou`。广告：`max(plugin.ad_daily.updated_at, plugin.ad_today.updated_at)`，`seller_id = shop_id`，判灯间隔 15 分钟（插件今日刷新是 30 秒，页头不按 30 秒报红）。订单 / 物流：该店 `integration.sync_jobs` 中 `extra.shop_id` 归因的最近成功 `finished_at`（`tiktok.orders` / `tiktok.logistics`，周期取自 `JOBS`）；尚无归因行时回退 `integration.sync_cursors.updated_at`（`scope=shop_id`）。妙手：注册表内 `miaoshou.*` 最近一次成功，按该作业自己的周期判灯，`scope="system"`。最近一次运行失败且新于上次成功时，`severity` 至少为 `warn`。未知 `shop_pk` 为 404。只读、零上游外呼。 |
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
[`runtime-config-management.md`](../ops/runtime-config-management.md).

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
| `GET /v2/pages/users` | admin + `page:users` | 用户管理页（用户 / 角色权限两页签），仅 `page:users` 权限点持有者可见可用；侧边栏入口同样按权限过滤。无权限 → 403 页面。 |

### User & role management (`/v2/users/*`, `/v2/roles/*`)

用户与角色管理 API（设计 `user-account-authz-design.md` §9.1）。**访问要求：**
路由矩阵未列路径默认 **admin** 档（fail-closed）；会话用户还必须持有
`page:users` 权限点（预置角色中仅 `admin` 拥有），否则 `403 {"detail":"requires
page:users"}`。API key 凭证（admin 档）也可调用，不受页面权限点约束。护栏：
禁止禁用自己、禁止禁用/降权最后一个 admin、内置 `admin` 角色不可编辑/删除、
内置角色不可删除、写操作仅接受 `Content-Type: application/json`。

请求/响应均为 camelCase JSON；错误体 `{"detail": "..."}`；`2xx` = 解析与持久化
均成功（无副作用的裸 JSON 不套 envelope）。时间字段 ISO-8601 UTC。

| Method | Path | Request body / notes | Response |
| --- | --- | --- | --- |
| `GET` | `/v2/users` | — | `{users: [{id, username, displayName, status, roles, lastLoginAt, activeSessions}]}`（`status` ∈ `active\|disabled`，`roles` 为角色 code 数组） |
| `POST` | `/v2/users` | `{username, displayName, password, roles?}`；用户名规则 `^[a-z0-9][a-z0-9_.-]{1,31}$`（创建时小写归一化），密码需过密码策略（≥6 位且含大小写与数字） | 201 `{id, username}`；400 `{detail}`（重名/密码策略/角色不存在） |
| `GET` | `/v2/users/{id}` | — | `{id, username, displayName, status, roles, pages, apiTier, lastLoginAt}`（`pages` = 有效页面权限点并集）；404 `{detail}` |
| `PATCH` | `/v2/users/{id}` | `{displayName?, status?, roles?}` 任一（启用/禁用、换角色都走这里；禁用即吊销全部会话） | `{ok: true}`；400/404 `{detail}`（自禁用/最后一个 admin 护栏） |
| `POST` | `/v2/users/{id}/password` | `{newPassword}`；重置后吊销该用户全部会话 | `{ok: true}`；400/404 `{detail}` |
| `GET` | `/v2/users/{id}/sessions` | — | `{sessions: [{id, createdAt, expiresAt, lastSeenAt, revokedAt, ip, userAgent, active}]}`；404 `{detail}` |
| `DELETE` | `/v2/users/{id}/sessions/{sessionId}` | 吊销单个会话 | `{ok: true, revoked: 1}`；404 `{detail}` |
| `DELETE` | `/v2/users/{id}/sessions` | 吊销该用户**全部**会话 | `{ok: true, revoked: N}`；404 `{detail}` |
| `GET` | `/v2/roles` | — | `{roles: [{code, name, description, apiTier, isBuiltin, permissions, userCount}], allPermissions: [{code, label, group}]}`（`allPermissions` = 页面权限点目录，创建/编辑表单勾选清单） |
| `POST` | `/v2/roles` | `{code, name, apiTier, permissions?}`；`code` 规则 `^[a-z0-9][a-z0-9_-]{1,31}$`，`permissions` 为 `page:<id>` 数组；勾选写类页面时按 `PAGE_MIN_WRITE_TIER` 校验 `api_tier`（配置类页面至少 readwrite，`page:users` 要求 admin 档） | 201 `{code}`；400 `{detail}`（重名/未知权限点/tier 不足） |
| `PATCH` | `/v2/roles/{code}` | `{name?, apiTier?, permissions?}` 任一 | `{ok: true}`；400/404 `{detail}`（内置 admin 不可编辑） |
| `DELETE` | `/v2/roles/{code}` | 删除自定义角色 | `{ok: true}`；被用户引用/内置角色 → 400 `{detail}`；404 `{detail}` |

页面级权限点 `page:<id>` 与侧边栏页面一一对应（单一清单：
`tts_erp_v2/accounts/pages.py`）；`/v2/users*` 与 `/v2/roles*` 归属 `page:users`。
账号创建/密码重置的无 UI 入口见 CLI：`python -m tts_erp_v2.accounts.cli --help`
（`create-user` / `reset-password` / `create-role` 等，首次部署建首个 admin 用）。

### Admin (`/v2/admin/*`, handler-enforced roles)

| Endpoint | Role | Notes |
| --- | --- | --- |
| `POST /v2/admin/shops/register` | **readwrite** | 人工注册店铺。body 可含 `service_id/app_key/app_secret`；App Key/Secret 必须成对且 service_id 必填，同一事务写入 `integration.tiktok_app_credentials`，Secret 只加密存储、不返回。注册幂等。 |
| `GET /v2/admin/shops/unregistered` | **readwrite** | 列出在 `plugin.*` 插件数据里出现、但 `commerce.shops` 无行的 shop_id → `{candidates: [{shop_id, sources}]}`；注册页的候选清单。 |
| `PATCH /v2/admin/shops/{shop_pk}` | **readwrite** | 更新店铺元信息或原子配置 `service_id/app_key/app_secret`。App pair 按 service_id 共享并加密；App Key/Secret 必须成对。只改 service_id 时目标 App pair 必须已存在（否则 409），防止生成必然失败的授权链接。`credential_id`/`status` 不可修改。 |

### TikTok video publishing (`/v2/video-publish/*`)

The browser workbench and API-key publishing clients store tasks through the same owner-scoped API in the `publishing` schema; both keep Artemis/ADB server-side. `POST /tasks` returns `201` for a new upload
票据 and `200` for an owned idempotent replay with the same payload; payload
mismatches return `409`, and another owner receives `404 TASK_NOT_FOUND` without
metadata. Task list/detail and state-changing operations are owner-scoped;
explicit admin access is the documented operational exception. Cookie-authored
mutations must send `X-Requested-With: tts-erp`; API-key clients are exempt from CSRF/page-permission checks but `/v2/video-publish/*` requires an **admin-tier API key by default**, including reads. `TTS_ERP_VIDEO_PUBLISH_ALLOW_READWRITE_API_KEYS=1` is an explicit future rollout feature gate and must remain unset until a separate documented approval; session role/page-permission behavior is unchanged.

| Endpoint | Role | Notes |
| --- | --- | --- |
| `GET /v2/pages/video-publish` | page:video-publish | Browser publishing workbench; page access follows the authenticated page permission. |
| `GET /v2/video-publish/config` | readonly + page:video-publish | Limits, masked target, fresh Worker liveness, admin-only `canViewDiagnostics`, `writeBlockReason`, and server-owned readiness (`ready|busy|offline|locked|unknown`) covering ADB/unlock, TikTok package, Artemis, MinIO, and active device cleanup; offline/locked/busy is informational and does not disable queue creation. No credentials or signed URLs. API keys are exempt from page permission points. |
| `GET /v2/video-publish/tasks/current` | readonly + page:video-publish | Current owner-visible task plus filter-independent polling summary. API keys are exempt from page permission points. |
| `GET /v2/video-publish/tasks` | readonly + page:video-publish | Owner-scoped history; `status`, `limit`, and opaque `(created_at,id)` keyset `cursor` filters. Queued snapshots include `queuedAt`, globally ordered `queuePosition`, and status/stage labels. API keys are exempt from page permission points. |
| `GET /v2/video-publish/tasks/{task_id}` | readonly + page:video-publish | Owner-scoped detail; `includeDiagnostics=true` requires admin. API keys are exempt from page permission points. |
| `GET /v2/video-publish/metrics` | readonly + page:video-publish | Content-free owner-visible current gauges: tasks-by-status, attempts-by-kind/status, queue/running/review, cleanup `devicePending/deviceFailed/spoolPending/spoolFailed/objectPending/objectFailed`, `currentStageAgeSeconds`, and Worker-heartbeat age; these snapshots are not monotonic counters or completed-stage histograms. Completed-stage duration is emitted as controlled `duration_ms` on `publish_transition`. Admin receives global task aggregates. API keys are exempt from page permission points. |
| `POST /v2/video-publish/tasks` | readwrite + page:video-publish | A genuinely new task requires a fresh ready Worker heartbeat or returns retryable `503 PUBLISH_WORKER_UNAVAILABLE`; owned idempotent retrieval/replay remains available without Worker readiness. Creation allocates a unique upload generation/key, persists expiry, and returns a short-lived PUT ticket. Presign failure is retryable `503 OBJECT_STORE_UNAVAILABLE`. API keys are exempt from page permission points but remain admin-tier by default. |
| `POST /v2/video-publish/tasks/{task_id}/upload-url` | readwrite + page:video-publish | Under a task-row lock, revalidates awaiting-upload/generation, signs, persists the monotonic maximum expiry and advances `rowVersion` before exposing the URL. A stage race returns structured 409 and discards the unreturned URL; presign failure is retryable `503 OBJECT_STORE_UNAVAILABLE`. API keys are exempt from page permission points but remain admin-tier by default. |
| `POST /v2/video-publish/tasks/{task_id}/replace-upload` | readwrite + page:video-publish | After retired-generation cleanup has completed, reopens a failed task with a never-reused generation/key so the owner can upload a replacement. API keys are exempt from page permission points. |
| `POST /v2/video-publish/tasks/{task_id}/confirm-upload` | readwrite + page:video-publish | HEAD-verifies existence, exact size, explicit `video/mp4`, and stores the confirmed ETag. Worker download uses conditional `If-Match` against that exact single-part or multipart ETag, so replacement/missing objects fail before device staging or attempt creation. Storage transport failures are retryable 503, while missing/size/MIME/blank-ETag validation errors have distinct stable retryable codes and leave the task awaiting upload. A later Worker-observed missing/replaced object remains selector-owned until ticket expiry plus completion grace and post-expiry idempotent cleanup/absence verification complete; only then is replacement exposed. API keys are exempt from page permission points. |
| `POST /v2/video-publish/tasks/{task_id}/cancel` | readwrite + page:video-publish | Cancels an unstarted task and schedules generation-bound cleanup; selector independently waits for latest persisted PUT expiry plus 15-minute completion grace using PostgreSQL time. API keys are exempt from page permission points but remain admin-tier by default. |
| `POST /v2/video-publish/tasks/{task_id}/retry` | readwrite + page:video-publish | Retries only a failed, retry-safe task within its attempt budget and returns the new `queuedAt`/`queuePosition`. API keys are exempt from page permission points. |
| `POST /v2/video-publish/tasks/{task_id}/verify` | readwrite + page:video-publish | Requests verification for an ambiguous result. API keys are exempt from page permission points. |
| `POST /v2/video-publish/tasks/{task_id}/cleanup/retry` | readwrite + page:video-publish | Retries eligible device, spool, or object cleanup without changing business status. API keys are exempt from page permission points. |

Publishing errors use `code`, `message`, `retryable`, and `requestId`. Stateful 409 responses additionally include `rowVersion` and `allowedActions`; clients must redraw actions from that response rather than infer them locally. `attemptCount`/`publishAttemptCount` are append-only created publish-attempt counts, while `retryBudgetUsed` is the independently refundable budget counter.

### SPU images (`/v2/spu-images/*`)

Presigned MinIO upload flow (server never proxies bytes; design:
[`procurement-ui-redesign.md`](../archive/procurement-ui-redesign.md)):

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
| `GET /v2/tiktok-shop/products/{product_id}` | [`api/tiktok-shop-get-product.md`](tiktok-shop-get-product.md) | `GET /product/202309/products/{product_id}` | `seller.product.basic` |

The other 7 Partner API product-domain GETs in `tts-partner-api-docs/`
(Listing Prerequisites / Categories / Attributes / Brands / Category
Rules / Image Translation Tasks / Submission Records) are deferred to
separate work items — same proxy + router pattern.

### Analytics — SPU 盈利导航 (`/v2/analytics/spu-roi*`)

SPU 盈利主端点和四个懒加载钻取端点是稳定的 readonly API。完整的当前实现、目标契约、字段矩阵、示例、页面交互、快照、预测隔离和验收要求集中在 [`docs/design/spu-profitability-technical-design.md`](../design/spu-profitability-technical-design.md)；业务公式和日期口径唯一来源是 [`docs/business/spu-profitability.md`](../business/spu-profitability.md)。本节只保留全局导航，避免在 API 总览中维护第二套盈利契约。

| Endpoint | Role | Purpose |
| --- | --- | --- |
| `GET /v2/analytics/spu-roi` | readonly | SPU 盈利主表、完整范围 `totals` 与 meta。带 `shop_pk` 时同一响应额外返回件数加权的 `items[].priceStats`/`priceCoverage` 与 `totals.priceStats`/`totals.priceCoverage`（采购/原价/实付 × 均值/中位数，CNY 四位小数或 `null`），并接受六个独立排序标识 `purchasePriceMean`、`purchasePriceMedian`、`originalSalePriceMean`、`originalSalePriceMedian`、`paidPriceMean`、`paidPriceMedian`；契约见 [`../design/spu-price-statistics.md`](../design/spu-price-statistics.md) |
| `GET /v2/analytics/spu-roi/{spu_pk}/orders` | readonly | 订单与物流证据 |
| `GET /v2/analytics/spu-roi/{spu_pk}/settlements` | readonly | 结算组件证据 |
| `GET /v2/analytics/spu-roi/{spu_pk}/cases` | readonly | 售后证据 |
| `GET /v2/analytics/spu-roi/{spu_pk}/ads` | readonly | 广告证据 |
| `GET /v2/pages/spu-roi` | readonly HTML | 标准 SPU 盈利页面 |
| `GET /v2/pages/focused-spus` | readonly HTML | 重点关注 SPU 页面 |

鉴权沿用本文件前文的 readonly 角色矩阵、API key/session 方式、限流和标准错误；盈利 GET 不写入数据。`/v2/analytics/sync/*` 是 Chrome 扩展 ingest 的另一组 readwrite+scope API，不属于盈利查询契约。

### Analytics Sync (`/v2/analytics/sync/*`)

Mounted under tts-erp at `/v2/analytics/sync/*`（2026-09-02 从
`/v1/analytics/sync/*` 单挂载硬切，无 /v1 别名；再早的 standalone
:9878 进程已于 2026-08-30 退役）。Powers the `tk-adv-cost-monitor` Chrome
extension. Auth requires **readwrite** role plus a per-seller scope grant
(the api_key's `scopes` array). Full protocol lives in
[`analytics/dump-architecture.md`](../archive/dump-architecture.md);
[`analytics/range-aggregate-history-sync.md`](../archive/range-aggregate-history-sync.md)
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
设计文档：`docs/archive/chrome-ext-order-sync-design.md`。

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
| `GET /v2/sync/freshness` | readonly | v2 — 当前店铺广告/订单/物流与妙手最近同步时间 |
| `GET /v2/pages/manual-costs` | readonly | v2 (HTML — not a machine contract) |
| `GET /v2/pages/spu-roi` | readonly | v2 (HTML — not a machine contract) |
| `GET /v2/analytics/spu-roi` | readonly | stable 只读；实现/接口见 [`../design/spu-profitability-technical-design.md`](../design/spu-profitability-technical-design.md)，业务公式/日期口径见 [`../business/spu-profitability.md`](../business/spu-profitability.md) |
| `GET /v2/spu-images`, upload/confirm/delete | readonly / readwrite | v2 |
| `GET /v2/llm-context` | readonly | v2 (content evolves with the schema) |
| `GET\|POST /v2/auth/*` | public / session user | v2 — 用户名+密码 + 会话 cookie `tts_erp_session`（浏览器 API key 登录已移除） |
| `GET\|POST /v2/users`, `GET\|PATCH /v2/users/{id}`, `POST /v2/users/{id}/password`, `GET\|DELETE /v2/users/{id}/sessions[/{sessionId}]`, `GET\|POST /v2/roles`, `PATCH\|DELETE /v2/roles/{code}` | admin + `page:users` | v2 |
| `GET /v2/analytics/sync/cursor`, `POST /v2/analytics/sync/dumps`, `GET /v2/analytics/sync/coverage` | readwrite + scope | analytics（自有 envelope，frozen） |

Retired endpoints (404; do NOT build on these):

- Since 2026-09-30 / migration 0044: every `/v2/linkage/*` endpoint.
- Since the 2026-08-29 hard switch: `GET /db/*` (24 read endpoints), `POST /orders/*` (search + write
proxies), `GET /orders/{id}/*`, `GET /finance/*`, `POST /sync/*`,
`GET /token/{shop_id}`, `GET /shops*`, `POST /returns/search`,
`POST /cancellations/search`, `GET /miaoshou/{domain}/{method}`,
`POST /miaoshou/callback/*` (dispatcher code remains in
`miaoshou/callbacks/` but no route is mounted in the v2 app).
