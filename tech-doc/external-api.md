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
`http://daqiang.nat100.top` from outside — the NAT layer strips the port;
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
| List shops (→ internal `channel_account_id`) | `GET /v2/commerce/channel-accounts` | readonly |
| List / get TikTok products (SPU) | `GET /v2/commerce/channel-products[/{id}[/variants]]` | readonly |
| List / get orders (+ lines) | `GET /v2/commerce/sales-orders[/{id}[/lines]]` | readonly |
| TikTok Shop product detail (read-through) | `GET /v2/tiktok-shop/products/{product_id}` | readonly |
| Per-shop order aggregate | `GET /v2/commerce/channel-accounts/{id}/order-stats` | readonly |
| 妙手↔TikTok product links | `GET /v2/linkage/product-links` | readonly |
| Link evidence (raw) | `GET /v2/linkage/evidence` | readonly |
| Link issues queue | `GET /v2/linkage/issues` | readonly |
| Resolve a link issue | `POST /v2/linkage/issues/{id}/resolve` | readwrite (handler-enforced) |
| List / create manual link overrides | `GET` / `POST /v2/linkage/overrides` | readonly / **admin** (handler-enforced) |
| Cost snapshots | `GET /v2/reporting/cost-snapshots` | readonly |
| Daily profit | `GET /v2/reporting/profit-daily` | readonly |
| Coverage / health snapshot | `GET /v2/reporting/coverage` | readonly |
| Active SPUs missing a cost | `GET /v2/reporting/missing-cost-products` | readonly |
| Submit a manual cost | `POST /v2/reporting/manual-costs` | readwrite |
| Operator console (HTML) | `GET /v2/pages/manual-costs` | readonly (browser → 302 login) |
| SPU image list / upload / delete | `GET /v2/spu-images`, `POST /v2/spu-images/upload-url`, `POST /v2/spu-images/{id}/confirm`, `DELETE /v2/spu-images/{id}` | readonly / readwrite |
| Browser login / logout / whoami | `GET\|POST /v2/auth/login`, `POST /v2/auth/logout`, `GET /v2/auth/me` | public |
| Analytics cursor has-data / dump ingest (Chrome ext) | `GET /v2/analytics/sync/cursor`, `POST /v2/analytics/sync/dumps` | readwrite + scope |

Key gotchas (read these before writing code):

- **Filter by internal ids, not shop_id.** v2 list endpoints take
  `channel_account_id` / `channel_product_id` (internal bigint PKs).
  Resolve a TikTok `shop_id` once via
  `GET /v2/commerce/channel-accounts?platform=tiktok` →
  `external_account_id`. Passing `?shop_id=` is **silently ignored**
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
# → [{"id":314,"external_account_id":"7494763368967603447",...}]
curl -sS -H "X-API-Key: $KEY" \
  "http://127.0.0.1:9877/v2/commerce/sales-orders?channel_account_id=314&limit=2"
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
path classification lives in
`tts_erp_v2/middleware/auth.py::required_role()` — unmatched paths default
to **admin** (fail-closed). A few write endpoints
(`POST /v2/linkage/overrides`, `POST /v2/linkage/issues/{id}/resolve`)
enforce their role **inside the handler** on top of the middleware.

Public (auth-exempt) paths: `/healthz`, `/endpoints`, `/openapi.json`,
`/docs`, `/redoc`, `/docs/oauth2-redirect`, `/v2/auth/login`,
`/v2/auth/logout`, `/v2/auth/me`.

**Errors**:

- `401 missing bearer token` — no credential sent
- `401 invalid, disabled or expired api key` — credential not recognised
- `403 requires <role>` — key recognised but lacks the role for this path

The mode is set by env `TTS_ERP_AUTH_MODE=off|shadow|enforce`. In
`enforce` (production default since 2026-08-20) the service returns the
error; in `shadow` the would-deny is only logged; `off` bypasses auth
entirely (development only).

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
- `GET /v2/auth/me` — `{authenticated, role}` for the current cookie.

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

Default: **no browser cross-origin access allowed** (empty allow-origin
list). To enable specific origins, set:

```dotenv
TTS_ERP_CORS_ALLOW_ORIGINS=https://app.example.com,https://admin.example.com
```

For dev/internal deploys, `TTS_ERP_CORS_ALLOW_ORIGINS=wildcard` enables
`*` — do not use in production.

## Endpoints

### Commerce (`/v2/commerce/*`, all readonly GET)

All list endpoints accept `limit` (1..500, default 100) + `offset` (≥0).

| Endpoint | Extra query params | Returns |
| --- | --- | --- |
| `GET /v2/commerce/channel-accounts` | `platform` (e.g. `tiktok`) | list of `{id, platform, external_account_id, account_name, region, seller_type, status, synced_at}` |
| `GET /v2/commerce/channel-accounts/{account_id}` | — | one account; 404 if unknown |
| `GET /v2/commerce/channel-accounts/{account_id}/order-stats` | — | `{order_count, payment_amount_sum}` aggregate (0/0 when empty) |
| `GET /v2/commerce/channel-products` | `channel_account_id`, `status` | SPU list: `{id, channel_account_id, external_product_id, title, status, source_created_at, source_updated_at}` |
| `GET /v2/commerce/channel-products/{product_id}` | — | one SPU; 404 if unknown |
| `GET /v2/commerce/channel-products/{product_id}/variants` | — | SKU list: `{id, channel_product_id, external_variant_id, seller_sku, variant_name}` |
| `GET /v2/commerce/sales-orders` | `channel_account_id`, `status` | order list: `{id, channel_account_id, external_order_id, status, currency, payment_amount, total_amount, source_created_at, source_updated_at, paid_at}` |
| `GET /v2/commerce/sales-orders/{order_id}` | — | one order (internal `id`, **not** `external_order_id`); 404 if unknown |
| `GET /v2/commerce/sales-orders/{order_id}/lines` | — | order lines: `{id, sales_order_id, external_line_id, channel_product_id, channel_product_variant_id, quantity, unit_price}` |

### Linkage (`/v2/linkage/*`)

| Endpoint | Role | Query params / body |
| --- | --- | --- |
| `GET /v2/linkage/product-links` | readonly | `channel_product_id`, `procurement_product_id`, `limit`, `offset` |
| `GET /v2/linkage/evidence` | readonly | `product_link_id`, `limit`, `offset` |
| `GET /v2/linkage/issues` | readonly | `unresolved_only` (default true), `limit`, `offset` |
| `POST /v2/linkage/issues/{issue_id}/resolve` | readwrite (handler-enforced) | — ; 200 `{id, status:"resolved"}`, 404 if missing/already resolved |
| `GET /v2/linkage/overrides` | readonly | `channel_product_id`, `active_only` (default true), `limit`, `offset` |
| `POST /v2/linkage/overrides` | **admin** (handler-enforced) | body `{"channel_product_id": int, "procurement_product_id": int \| null, "decision": "ALLOW"\|"DENY"\|"PRIMARY", "reason"?: str, "valid_from"?: datetime}` → 201 |

Note: the merged "effective links" view exists only at the DB layer
(`linkage.effective_product_links`); there is **no** HTTP endpoint for it —
`GET /v2/linkage/product-links` + `/overrides` are the HTTP surface.

### Reporting (`/v2/reporting/*`)

| Endpoint | Role | Query params / body |
| --- | --- | --- |
| `GET /v2/reporting/cost-snapshots` | readonly | `channel_product_id`, `cost_method`, `limit`, `offset` |
| `GET /v2/reporting/profit-daily` | readonly | `channel_product_id`, `on_date`, `limit`, `offset` |
| `GET /v2/reporting/coverage` | readonly | — → `{total_spus, active_spus, linked_spus, missing_cost_spus, calculation_version}` |
| `GET /v2/reporting/missing-cost-products` | readonly | `channel_account_id`, `limit` (default 200), `offset` → `{items: [{channel_product_id, external_product_id, title, channel_account_id, missing_photo}], total_missing_photo}` |
| `POST /v2/reporting/manual-costs` | readwrite | body `{"channel_product_external_id": str, "unit_cost": decimal>0, "currency": "VND", "valid_from"?: datetime, "note"?: str}` → 201 `ManualCostOut`; auto-closes the previous effective row for the SPU |

Cost semantics: `MANUAL_ENTRY` (this endpoint) > 妙手采购单 > (1688 采集标价
**禁用**). See `tech-doc/refactor-tech-plan-v2.md` §6 decisions 10/12.

> Caveat (2026-08-31): the rebuild jobs behind `cost_snapshots` /
> `profit_daily` are not wired into the sync-worker scheduler yet, so
> those two tables are empty and the GETs return `[]` — expected, not a
> bug in your client.

### Pages

| Endpoint | Role | Notes |
| --- | --- | --- |
| `GET /v2/pages/manual-costs` | readonly | Server-rendered operator console (shop switcher + needs-cost / needs-photo / recently-filed tabs). Browser without a session → 302 to `/v2/auth/login`. Static assets under `/static/*` are readonly-classified too. |

### SPU images (`/v2/spu-images/*`)

Presigned MinIO upload flow (server never proxies bytes; design:
[`procurement-ui-redesign.md`](procurement-ui-redesign.md)):

1. `POST /v2/spu-images/upload-url` (readwrite) — body
   `{"channel_account_id", "channel_product_id", "filename", "content_type", "size_bytes"≤8MiB}`
   → 201 `{image_id, object_key, upload_url, upload_expires_at, required_headers}`.
2. Browser PUTs the file to `upload_url` directly.
3. `POST /v2/spu-images/{image_id}/confirm` (readwrite) — server HEAD-verifies
   the object → `{status: "ready", url, ...}`; 409 `UPLOAD_NOT_FOUND` if the
   PUT never landed.
4. `GET /v2/spu-images?channel_product_id=X` (readonly) — ready images with
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
credentials via `proxy/token_service.load_credentials()` (key by
internal `channel_account_id` → upstream `shop_id` → `access_token` +
`shop_cipher`) and hands the upstream `data` payload back verbatim.
**No DB caching** — callers that need offline durability should add a
sync job on top of the proxy layer
(`tts_erp_v2/proxy/tts_shop/products_api.py`).

| Endpoint | Upstream | Required scope |
| --- | --- | --- |
| `GET /v2/tiktok-shop/products/{product_id}` | `GET /product/202309/products/{product_id}` | `seller.product.basic` |

`GET /v2/tiktok-shop/products/{product_id}` (readonly) — fetch one
product's full details. Query params:

- `channel_account_id` (**required**, `ge=1`) — internal
  `commerce.channel_accounts.id`. Must be a `platform='tiktok'` row;
  non-tiktok or unknown ids return 404.
- `return_under_review_version` (default `false`) — upstream flag; see
  `tts-partner-api-docs/Get Product.md` for semantics.
- `return_draft_version` (default `false`) — upstream flag; mutually
  exclusive with `return_under_review_version` per upstream docs.
  Passing both yields 422.
- `locale` (optional, BCP-47) — display locale; `None` → upstream
  uses the shop default.

Response: the full product dict returned by the upstream (id, title,
status, audit, brand, category_chains, certifications, skus, package
dimensions, ...). No Pydantic model — the client controls its own
consumption of the shape. The envelope `{code, message, request_id}`
is stripped after the success check.

Error mapping:

- `502` upstream `code != 0` → `{detail: {message, upstream_code,
  upstream_message, upstream_request_id}}` so callers can branch on
  the upstream code without re-fetching.
- `404` unknown `channel_account_id` or missing credentials.
- `429` upstream rate-limit (exhausted retry budget).
- `502` upstream auth rejected, upstream HTTP 4xx/5xx, network blip
  after retries.
- `500` signing/config error (`TIKTOK_APP_KEY` / `TIKTOK_APP_SECRET`
  missing).

Example:

```bash
curl -sS -H "X-API-Key: $KEY" \
  "http://127.0.0.1:9877/v2/tiktok-shop/products/1729592969712207008?channel_account_id=314"
```

The other 7 Partner API product-domain GETs in `tts-partner-api-docs/`
(Listing Prerequisites / Categories / Attributes / Brands / Category
Rules / Image Translation Tasks / Submission Records) are deferred to
separate work items — same proxy + router pattern.

### Analytics Sync (`/v2/analytics/sync/*`)

Mounted under tts-erp at `/v2/analytics/sync/*`（2026-09-02 从
`/v1/analytics/sync/*` 单挂载硬切，无 /v1 别名；再早的 standalone
:9878 进程已于 2026-08-30 退役）。Powers the `tk-adv-cost-monitor` Chrome
extension. Auth requires **readwrite** role plus a per-seller scope grant
(the api_key's `scopes` array). Full protocol lives in
[`analytics/dump-architecture.md`](analytics/dump-architecture.md);
this section is the agent-facing quick reference.

#### `GET /v2/analytics/sync/cursor`

has-data 预检（dump architecture，2026-09-02 起）：查这个
`(scope, endpoint, day[, campaignId])` 是否已有 dump 落库
（`analytics.ad_raw` existence）。plugin 在打 TikTok 前先问一次，
`hasData: true` → 跳过该天的抓取（防风控）。work-list 模式
（`items` / `nextRequiredDay` / `pageSize` / `cursor` / `timezone`）
已随 dump architecture 删除（见 `analytics/dump-architecture.md`）。

Query parameters:

| name | type | notes |
| --- | --- | --- |
| `sellerId` | string | required, ≤ 128 chars |
| `advertiserId` | string | required, ≤ 128 chars |
| `endpoint` | string | required；必须在 dump 白名单（见下） |
| `day` | date | required, `YYYY-MM-DD` |
| `campaignId` | string | optional, ≤ 128 chars；缺省查整 day |

`endpoint` 白名单（server 据此推导 `storageKey`）：

- `/oec_ads/shopping/v1/oec/stat/post_product_list` → `productAnalyses`
- `/oec_ads/shopping/v1/oec/stat/post_session_list` → `sessionAnalyses`
- `/oec_ads/shopping/v1/oec/stat/campaign_opt_log_list` → `campaignChangeLogs`

白名单外的 endpoint → `400 SCHEMA_INVALID`。

Response (`code: 0`):

```json
{
  "code": 0,
  "requestId": "req-…",
  "data": {
    "day": "2026-08-23",
    "endpoint": "/oec_ads/shopping/v1/oec/stat/post_product_list",
    "storageKey": "productAnalyses",
    "hasData": false
  }
}
```

带 `campaignId` 查询时响应多带 `"campaignId"` 字段。`403 SCOPE_DENIED`
if the api_key's `scopes[]` doesn't cover the requested
`(sellerId, advertiserId)`.

#### `POST /v2/analytics/sync/dumps`

单 dump 写入（dump architecture，2026-09-02 起；旧 `/batches` 批量协议
已下线 404）。一次请求 = 一次完整 HTTP 交换的原始落库
（`analytics.ad_raw`，source-of-truth）。plugin **严禁批量**：一页一
dump、一页一发，永不把 N 页 buffer 成一批（见
`analytics/dump-architecture.md` D2）。

Body（≤ 2 MB）：

```json
{
  "protocolVersion": 2,
  "requestId": "req-…",
  "scope": {"sellerId": "seller-1", "advertiserId": "adv-1"},
  "dump": {
    "endpoint": "/oec_ads/shopping/v1/oec/stat/post_product_list",
    "method": "POST",
    "day": "2026-08-23",
    "campaignId": "campaign-1",
    "request": {"url": "…", "headers": {}, "body": {}},
    "response": {"status": 200, "headers": {}, "body": {"data": []}},
    "capturedAt": "2026-08-23T03:00:00.000Z"
  }
}
```

- `request` / `response` = plugin 抓的完整 HTTP 交换（JSONB 原样落 ad_raw）。
- `capturedAt` 必须带时区（`Z` 或 `+00:00`）。
- 不带 `page`（隐式 = 1）/ `expectedPageCount` / `storageKey` /
  `sourceRecordId` —— 这些概念在 dump architecture 已删除；`storageKey`
  由 server 从 `endpoint` 推导。

幂等：server 重算 canonical idempotency key（6 字段 SHA-256，page 固定
1），`ad_raw` 的 5 元组 unique 约束
`(seller_id, advertiser_id, endpoint, day, campaign_id)` 兜底 ——
同 dump 重放 → `duplicate`，不是错误。

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

`status ∈ {"inserted", "duplicate"}` — 两者都是成功。

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
  "http://127.0.0.1:9877/v2/commerce/sales-orders?channel_account_id=$ACCT&limit=20" \
  | jq -r '.[] | [.external_order_id, .status, .payment_amount, .currency] | @tsv'
```

### Submit a manual cost for an SPU

```bash
curl -sS -X POST -H "Authorization: Bearer $KEY" \
  -H "Content-Type: application/json" \
  -d '{"channel_product_external_id":"1730000000000000001","unit_cost":"12.40","currency":"VND","note":"1688 议价后价"}' \
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
| `GET /v2/linkage/*` (GETs) | readonly | v2 |
| `POST /v2/linkage/issues/{id}/resolve` | readwrite | v2 |
| `POST /v2/linkage/overrides` | admin | v2 |
| `GET /v2/reporting/*` | readonly | v2 |
| `POST /v2/reporting/manual-costs` | readwrite | v2 |
| `GET /v2/pages/manual-costs` | readonly | v2 (HTML — not a machine contract) |
| `GET /v2/spu-images`, upload/confirm/delete | readonly / readwrite | v2 |
| `GET /v2/llm-context` | readonly | v2 (content evolves with the schema) |
| `GET\|POST /v2/auth/*` | public | v2 |
| `GET /v2/analytics/sync/cursor`, `POST /v2/analytics/sync/dumps` | readwrite + scope | analytics（自有 envelope，frozen） |

Retired (404 since the 2026-08-29 hard switch — do NOT build on these;
they exist only in git history):

`GET /db/*` (24 read endpoints), `POST /orders/*` (search + write
proxies), `GET /orders/{id}/*`, `GET /finance/*`, `POST /sync/*`,
`GET /token/{shop_id}`, `GET /shops*`, `POST /returns/search`,
`POST /cancellations/search`, `GET /miaoshou/{domain}/{method}`,
`POST /miaoshou/callback/*` (dispatcher code remains in
`miaoshou/callbacks/` but no route is mounted in the v2 app).
