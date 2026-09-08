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
| List shops (→ internal `shop_pk`) | `GET /v2/commerce/channel-accounts` | readonly |
| Look up shop by upstream shop_id | `GET /v2/commerce/channel-accounts/by-external/{shop_id}` | readonly — see [`tech-doc/api/channel-accounts-by-external.md`](api/channel-accounts-by-external.md) |
| List / get TikTok products (SPU) | `GET /v2/commerce/channel-products[/{id}[/variants]]` | readonly |
| List / get orders (+ lines) | `GET /v2/commerce/sales-orders[/{id}[/lines]]` | readonly |
| TikTok Shop product detail (read-through) | `GET /v2/tiktok-shop/products/{product_id}` | readonly — see [`tech-doc/api/tiktok-shop-get-product.md`](api/tiktok-shop-get-product.md) |
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
| Latest cached FX rates | `GET /v2/fx/latest` | readonly |
| Currency conversion (local, cached) | `GET /v2/fx/convert` | readonly |
| Operator console (HTML) | `GET /v2/pages/manual-costs` | readonly (browser → 302 login) |
| SPU 实际 ROI 看板主表 | `GET /v2/analytics/spu-roi` | readonly — 口径见 [`analytics/spu-real-roi-dashboard.md`](analytics/spu-real-roi-dashboard.md) |
| SPU 实际 ROI 页面 (HTML) | `GET /v2/pages/spu-roi` | readonly (browser → 302 login) |
| SPU image list / upload / delete | `GET /v2/spu-images`, `POST /v2/spu-images/upload-url`, `POST /v2/spu-images/{id}/confirm`, `DELETE /v2/spu-images/{id}` | readonly / readwrite |
| Browser login / logout / whoami | `GET\|POST /v2/auth/login`, `POST /v2/auth/logout`, `GET /v2/auth/me` | public |
| Analytics cursor has-data / dump ingest (Chrome ext) | `GET /v2/analytics/sync/cursor`, `POST /v2/analytics/sync/dumps` | readwrite + scope |
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
| `GET /v2/commerce/channel-accounts` | `platform` (e.g. `tiktok`) | list of `{id, platform, shop_id, account_name, region, seller_type, status, synced_at}` |
| `GET /v2/commerce/channel-accounts/{shop_pk}` | — | one account; 404 if unknown |
| `GET /v2/commerce/channel-accounts/by-external/{shop_id}` | [`api/channel-accounts-by-external.md`](api/channel-accounts-by-external.md) | reverse-lookup by upstream shop_id; `?platform=tiktok` default; 404 if unknown |
| `GET /v2/commerce/channel-accounts/{shop_pk}/order-stats` | — | `{order_count, payment_amount_sum}` aggregate (0/0 when empty) |
| `GET /v2/commerce/channel-products` | `shop_pk`, `status` | SPU list: `{id, shop_pk, spu_id, title, status, source_created_at, source_updated_at}` |
| `GET /v2/commerce/channel-products/{spu_pk}` | — | one SPU; 404 if unknown |
| `GET /v2/commerce/channel-products/{spu_pk}/variants` | — | SKU list: `{id, spu_pk, sku_id, seller_sku, variant_name}` |
| `GET /v2/commerce/sales-orders` | `shop_pk`, `status` | order list: `{id, shop_pk, order_id, status, currency, payment_amount, total_amount, order_time, order_modify_time, paid_at}` |
| `GET /v2/commerce/sales-orders/{order_pk}` | — | one order (internal id, **not** the TikTok `order_id`); 404 if unknown |
| `GET /v2/commerce/sales-orders/{order_pk}/lines` | — | order lines: `{id, order_pk, external_line_id, spu_pk, sku_pk, quantity, unit_price}` |

### Linkage (`/v2/linkage/*`)

| Endpoint | Role | Query params / body |
| --- | --- | --- |
| `GET /v2/linkage/product-links` | readonly | `spu_pk`, `procurement_product_id`, `limit`, `offset` |
| `GET /v2/linkage/evidence` | readonly | `product_link_id`, `limit`, `offset` |
| `GET /v2/linkage/issues` | readonly | `unresolved_only` (default true), `limit`, `offset` |
| `POST /v2/linkage/issues/{issue_id}/resolve` | readwrite (handler-enforced) | — ; 200 `{id, status:"resolved"}`, 404 if missing/already resolved |
| `GET /v2/linkage/overrides` | readonly | `spu_pk`, `active_only` (default true), `limit`, `offset` |
| `POST /v2/linkage/overrides` | **admin** (handler-enforced) | body `{"spu_pk": int, "procurement_product_id": int \| null, "decision": "ALLOW"\|"DENY"\|"PRIMARY", "reason"?: str, "valid_from"?: datetime}` → 201 |

Note: the merged "effective links" view exists only at the DB layer
(`linkage.effective_product_links`); there is **no** HTTP endpoint for it —
`GET /v2/linkage/product-links` + `/overrides` are the HTTP surface.

### Reporting (`/v2/reporting/*`)

| Endpoint | Role | Query params / body |
| --- | --- | --- |
| `GET /v2/reporting/cost-snapshots` | readonly | `spu_pk`, `cost_method`, `limit`, `offset` |
| `GET /v2/reporting/profit-daily` | readonly | `spu_pk`, `on_date`, `limit`, `offset` |
| `GET /v2/reporting/coverage` | readonly | — → `{total_spus, active_spus, linked_spus, missing_cost_spus, calculation_version}` |
| `GET /v2/reporting/missing-cost-products` | readonly | `shop_pk`, `limit` (default 200), `offset` → `{items: [{spu_pk, spu_id, title, shop_pk, missing_photo}], total_missing_photo}` |
| `POST /v2/reporting/manual-costs` | readwrite | body `{"spu_id": str, "unit_cost": decimal>0, "currency": "VND", "valid_from"?: datetime, "note"?: str}` → 201 `ManualCostOut`; auto-closes the previous effective row for the SPU |

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

### Pages

| Endpoint | Role | Notes |
| --- | --- | --- |
| `GET /v2/pages/manual-costs` | readonly | Server-rendered operator console (shop switcher + needs-cost / needs-photo / recently-filed tabs). Browser without a session → 302 to `/v2/auth/login`. Static assets under `/static/*` are readonly-classified too. |
| `GET /v2/pages/spu-roi` | readonly | SPU 实际 ROI 看板(账页式)。Server-rendered HTML shell;数据来自 `GET /v2/analytics/spu-roi`;JS 在 `/static/js/spu-roi.js`。 |

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
credentials via `proxy/token_service.load_credentials()` (key by
internal `shop_pk` → upstream `shop_id` → `access_token` +
`shop_cipher`) and hands the upstream `data` payload back verbatim.

| Endpoint | Spec | Upstream | Required upstream scope |
| --- | --- | --- | --- |
| `GET /v2/tiktok-shop/products/{product_id}` | [`api/tiktok-shop-get-product.md`](api/tiktok-shop-get-product.md) | `GET /product/202309/products/{product_id}` | `seller.product.basic` |

The other 7 Partner API product-domain GETs in `tts-partner-api-docs/`
(Listing Prerequisites / Categories / Attributes / Brands / Category
Rules / Image Translation Tasks / Submission Records) are deferred to
separate work items — same proxy + router pattern.

### Analytics — SPU 实际 ROI (`/v2/analytics/spu-roi`)

**Stability: stable · 只读(readonly)**。按 SPU 一行的「广告消耗 → 有效销售 → 退款 → 净收入 → 货本 → 净利润 → 实际 ROI/保本线」账页数据源;页面 `GET /v2/pages/spu-roi` 消费它。**口径唯一真相** = [`analytics/spu-real-roi-dashboard.md`](analytics/spu-real-roi-dashboard.md) §4/§5(公式 M1–M19,v8 实测) + [`handoff/spu-roi-full-loss-rubric.md`](../handoff/spu-roi-full-loss-rubric.md) v8 记忆;本端点只读计算并序列化,不做任何写。

Query parameters:

| name | type | default | notes |
| --- | --- | --- | --- |
| `q` | string | — | `spu_id` 子串搜索(ILIKE) |
| `sort` | enum | `roi_real` | `roi_real` \| `spend` \| `refund_rate` \| `refund_rate_qty` \| `cancel_rate` \| `net_profit` \| `sales` \| `gmv_sales` \| `ad_count` \| `gmv_ad` \| `order_count` \| `cancelled_order_count` \| `units_sold` \| `refund_net_amount` \| `return_loss` \| `roi_breakeven` \| **`full_loss_rate`**(v8 新增);同值次级键 spend DESC 保证可复现 |
| `order` | enum | `asc` | `asc` \| `desc`;**默认 `sort="roi_real"` 升序保持不变**——避免改 API 契约;**页面 JS 显式传 `sort=net_profit&order=asc` 实现「最亏在前」视图** |
| `limit` | int | 100 | 1..500(分页 v2 约定) |
| `offset` | int | 0 | ≥ 0 |
| `include_all` | bool | `false` | `false` 只含有广告∨有效销售∨退款的 SPU;`true` 拉全部 **ACTIVE**(status ILIKE 'activate')目录 SPU(DEACTIVATE/DELETED 等排除) |
| `shop_pk` | int | — | 店铺过滤(内部主键) |
| `fee_rate` | decimal-str | — | 平台佣金费率页面覆写;缺省固定基线 `0.308`(决策 D10,2026-09-06 实测重定);**v8 语义变化：仅作用于未结算订单 (r̂ × unsettled_sales)，已结算订单费用已含在 SETTLEMENT 不受此影响** |
| `w_start` | date | — | ISO `yyyy-mm-dd`;提供时销售按 `paid_at`、退款按 `updated_at_source` 裁剪(含当日) |
| `w_end` | date | — | ISO `yyyy-mm-dd`;与 `w_start` 配对使用;不提供 `w_start`/`w_end` = 销售/退款**全历史累计**(ad 无日期参数,恒整窗累计,§4.5) |

Response envelope:`{items: [...], total, totals, meta}`。

**v8 行字段契约（32 字段，**全量**——页面主列仅渲染 6 列 + 商品维度，其余由下钻面板消费）**：

| 字段 | 类型 | 公式 / 含义 | 主列? |
| --- | --- | --- | --- |
| `spu_pk` | int | `commerce.products_spu.id` 内部主键 | — |
| `spu_id` | str | 业务 SPU 编号（TikTok 端） | 商品列 |
| `title` / `status` / `main_image_url` | str | 商品维度列 | 商品列 |
| `shop_id` / `shop_name` | str/int | 店铺维度 | 商品列 |
| `ad_count` | int | M2: 投放广告数 | — |
| `ad_orders` | int | M2b: 平台出单量 | — |
| `spend` | money-str (USD) | M1: 广告消耗 = `Σ real_cost_total` | **主列** |
| `gmv_ad` | money-str (USD) | M3: 平台归因 GMV | — |
| `roi_l0` | ratio-str/null | M4: `gmv_ad / spend` | — |
| `ad_first_day` / `ad_last_day` | date/null | 广告观测窗口 | — |
| `order_count` | int | M5b: 有效销售订单数（白名单状态，含 COD 在途） | **主列** |
| `cancelled_order_count` | int | M5c: 取消订单数 | — |
| `units_sold` | int | M5: 售出件数 | — |
| `sales` | money-str (USD) | M6: 有效销售金额 = `Σ quantity×unit_price` | **主列**(标记为"有效GMV") |
| `gmv_sales` | money-str (USD) | M6c: 全单 = sales + 取消原额 | — |
| `cancel_rate` | ratio-str/null | M12b: 取消 ÷(有效+取消) | **主列** |
| `refund_only_qty` / `refund_only_amount` | int/money | M7: 仅退款 | — |
| `refund_return_qty` / `refund_return_amount` | int/money | M8: 退货退款 | — |
| `refund_net_qty` / `refund_net_amount` | int/money | M10: M7+M8 | — |
| `refund_rate` | ratio-str/null | M12: 净额 ÷ sales | — |
| `refund_rate_qty` | ratio-str/null | M12c: 单量口径 | — |
| `refund_cancelled_qty` / `refund_cancelled_amount` / `refund_cancelled_missing_lines` | int/money/int | M9: 已付被取消信息列 | — |
| **`net_revenue`** | **money-str (USD)** | **v8 新增：DUAL-LAYER = `Σ SETTLEMENT 分摊` + `Σ 未结 line_gmv × (1−r̂) × (1−refund_rate_spu)`；是 `net_profit` 的输入** | 下钻·结算 tab |
| **`settled_sales`** | **money-str (USD)** | **v8 新增：`SUM line_gmv WHERE settlement_vnd IS NOT NULL`** | 下钻·结算 tab |
| **`unsettled_sales`** | **money-str (USD)** | **v8 新增：`SUM line_gmv WHERE settlement_vnd IS NULL`** | 下钻·结算 tab |
| **`settled_order_count`** | **int** | **v8 新增：已结算订单数** | 下钻·结算 tab |
| **`full_loss_qty`** | **int** | **v8 新增：38301 ∧ (完结 case ∨ CANCELLED) 件数 (127 实测)** | 下钻·结算 tab |
| **`full_loss_cancelled_qty`** | **int** | **v8 新增：CANCELLED ∧ 38301 件数（COGS 补扣基数）** | 下钻·结算 tab |
| **`full_loss_rate`** | **ratio-str/null** | **v8 新增(D8 主列)：`full_loss_qty ÷ (units_sold + full_loss_cancelled_qty)`；分母 0 → null；不钳位（>100% 标识数据异常）** | **主列**(标记为"全损率%") |
| `return_loss` | money-str (USD) | **M13b v8** = `full_loss_qty × unit_cost_used` | — |
| `unit_cost_used` | money-str (USD) | 单位成本 = `unit_cost × fx_cny_usd` | — |
| `cost_source` | enum | **v8 扩为四值**：`MANUAL`(人工标注的采购成交价) \| `PURCHASE`(妙手采购单成交价) \| `SOURCE_PRICE`(1688 货源价) \| `DEFAULT_K1`(40 CNY/件) | — |
| `net_profit` | money-str (USD) | **M18 v8** = `net_revenue − (units_sold + full_loss_cancelled_qty) × unit_cost − spend`（**不**扣 platform_fee：已结费用含 SETTLEMENT，未结按 (1−r̂) 折算） | **主列** |
| `platform_fee` | money-str (USD) | **M19 v8** = `r̂ × unsettled_sales`（**信息列，不**进 M18） | — |
| `roi_real` | ratio-str/null | **M14 v8** = `(net_revenue − return_loss) / spend` | 下钻·利润构成 |
| `roi_breakeven` | ratio-str/null | **M17 v8** = `NC′ ÷ (NC′ − COGS_kept)`（fee_est 项移除） | 下钻·利润构成 |
| `cpa` | money-str/null | M15: `spend / ad_orders` | — |

> **v8 语义变化（breaking relative to v5 文本）**：
> - `net_profit / roi_real / roi_breakeven / platform_fee` 公式重写（见 M18/M14/M17/M19）
> - `return_loss` 口径从"完结退货件 × cost"切到"全损件数 × cost"（M13b v8）
> - `cost_source` 从两值扩为四值
> - `unit_cost_used` 来源从 manual+30 兜底切到 MANUAL→PURCHASE→SOURCE_PRICE→40 兜底链
> - 新增 6 字段：`net_revenue / settled_sales / unsettled_sales / settled_order_count / full_loss_qty / full_loss_cancelled_qty / full_loss_rate`
> - 主列（D8）从 13 列精简为 6 列：商品 + `spend` + `sales` + `order_count` + `cancel_rate` + `full_loss_rate` + `net_profit`；其余 26 字段继续在 JSON 返回，由下钻面板消费

格式化约定(§5.1):**money = 4 位小数字符串**、比率/ROI = 2 位小数字符串、件数/单量整数;`null` = 无解/除数为 0(页面显示 `—`);无投放 SPU `spend="0.0000"` + `ad_count=0`。全表金额统一 USD(原币 VND/CNY 服务端按 fx 快照一次换算,`meta.fx` 标注)。`totals` = 跨分页、当前筛选的加总:`row_count`(SPU 数)、单量(`order_count` 有效单 / `cancelled_order_count` 取消单 / `total_orders` = 两者之和,跨可见 SPU 全局去重)、`gmv`(全部订单销售额 = 白名单有效 ∪ 取消订单的原始行金额;money-str)与 `spend, sales, refund_net_amount, return_loss, net_profit`(行级 USD 服务端加总,4 位小数字符串)、`roi_real`(Σ(net_revenue−return_loss)/Σspend,Σspend=0 → null);`total` = 匹配行数。**口径注(2026-09-06 全链状态口径,COD 店)**:行级 `sales`/单量/`gmv` 全部按**订单状态**下单即算——白名单状态订单(含 COD 在途/待收款)计入 `sales` 与有效单量;取消订单只进 `gmv`/`cancelled_order_count`,不重复入 sales;净利润/退款率/ROI 等派生金额自动跟随状态口径 sales(回款前偏乐观)。窗口裁剪列 = `COALESCE(paid_at, order_time)`(已收款按收款日；COD 在途/取消未收款按下单日)。**主表行内列集(v8 D8 主表精简)**:**主列 = 商品 + 消耗USD/有效GMV(`sales`)/有效出单量(`order_count`)/取消率(`cancel_rate`)/全损率%(`full_loss_rate`)/净利润**;其余 26 字段（ROI/保本/平台佣金/全损货损金额/已结未结 GMV/退款拆分/广告归因/订单结构）由行内 accordion 钻取面板五 tab 顶部汇总区展示（详见下节）。`meta` 携带 fx/fee/cost_assumption/window/**`rubric_version`(v8 新增)**:"/"unattributed_refund_lines/computed_at/currency;`meta.window` 为 ad 视图观测窗口(供参考),销售/退款是否裁剪见 `note`。

**v8 默认值总览**：
- `sort="roi_real"`（API 契约不动；页面 JS 显式传 `sort=net_profit&order=asc`）
- `fee_rate=0.308`（仅作用于未结算订单 `unsettled_sales × 0.308`，已结不受影响）
- `include_all=false`、`limit=100`、`order="asc"`
- `meta.rubric_version="v8"`（口径漂移一眼定位）

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
| `w_start` / `w_end` | date | — | 销售/退款裁剪窗口（同主表语义：销售按 `COALESCE(paid_at, order_time)`、退款按 `updated_at_source`，含 `w_end` 当日） |

Response `{spu_pk, spu_id, window, orders[], meta}`。`orders[]` 字段：`order_id, status, qty, line_gmv(USD), paid_at, is_settled(已结 ✓/未结), settled_net_share(SETTLEMENT × 分摊比例，未结 → null), arrived_overseas(38301 命中), full_loss(38301 ∧ (完结case ∨ CANCELLED)), shipment{status, tracking_number}, tracking[]`（按事件时间排序的 `tracking_events` 子集：`action_code, desc, event_at`）。

防呆：`orders` 上限 500 条；超限返回 `{meta.orders_truncated: true}`。404：spu_pk 不存在。金额与主表同序列化（money 4 位、USD）。

#### `GET /v2/analytics/spu-roi/{spu_pk}/settlements`

结算 tab 数据源（已结订单组件拆分）。

| query | type | default | notes |
| --- | --- | --- | --- |
| `w_start` / `w_end` | date | — | 同 orders |

Response `{spu_pk, settlements[], meta}`。`settlements[]` 每条 = 一笔已结订单：`order_id, statement_time, share_ratio(该 SPU 行占整单 GMV 比例), components[]`。`components[]` = 完整 53 字段（v8 D2 零值落库后含 0 行），每条 `{code(如 SETTLEMENT/GROSS_SALES/PLATFORM_COMMISSION…), amount_vnd(VND 原值), amount(USD 换算)}`。

未结算订单 **不**进 `settlements[]`（tab 底部由行字段 `settled_order_count / unsettled_sales` 计算一行汇总："未结算 N 单，估算净收入 $X（基线 r̂ × (1−退款率)）"）。

#### `GET /v2/analytics/spu-roi/{spu_pk}/cases`

售后 tab 数据源。

| query | type | default | notes |
| --- | --- | --- | --- |
| `w_start` / `w_end` | date | — | 同 orders |

Response `{spu_pk, cases[], meta}`。`cases[]` 每条 = `case_id, order_id(可跳订单 tab 对号), type(REFUND_ONLY/RETURN_AND_REFUND/CANCELLATION), status(未完结标黄), refund_amount, reason(code+text), updated_at`。退款金额按 `case_lines.sales_order_line_id → sales_order_lines.spu_pk` 归集（同主表 M7/M8/M9）。

#### `GET /v2/analytics/spu-roi/{spu_pk}/ads`

广告 tab 数据源（**无窗口参数**——广告全窗口累计，与主表一致）。

Response `{spu_pk, ads[], meta}`。`ads[]` 每条 = `campaign_id, spend(USD), orders, first_day, last_day`（`analytics.ad_product_links` 视图聚合）。**不含 `campaign_name`**——v8 拍板不追（同步数据无名称字段）。

#### 4 端点共享约定

- 鉴权：`_READONLY_EXACT`（readonly 角色矩阵沿用主表）
- 404：`spu_pk` 不存在
- 金额：money 4 位小数字符串（USD，原币字段同时给 VND）；比率 2 位
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
#### `POST /v2/analytics/sync/dumps`

单 dump 写入（dump architecture，2026-09-02 起；旧 `/batches` 批量协议
已下线 404）。一次请求 = 一次完整 HTTP 交换的原始落库
（`analytics.ad_raw`，source-of-truth）。plugin **严禁批量**：一页一
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
- `request` / `response` = plugin 抓的完整 HTTP 交换（JSONB 原样落 ad_raw）。
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

内容被取代事件（history 替换/推进/重建、daily 折叠、today 跨天 reset）写
`analytics.ad_sync_audit` 一行元数据审计（与主写同事务；30s today 常规刷新不写）。

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
HTTP 响应通过此端点写入后端 chrome_sync schema。Auth requires **readwrite** role。
设计文档：`tech-doc/chrome-ext-order-sync-design.md`。

#### `POST /v2/order-sync/has-data`

批量查业务表存在性。插件拿到 order_id 列表后，一次请求查出哪些已有数据，
只对缺失的发 TikTok 请求（解决物流 N+1 问题）。

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

接收 dump → inline 解析 → 写业务表 + raw_log。每个 dump 对应一次 TikTok
HTTP 交换的完整原始响应。

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

响应（`code: 0`）：

```json
{
  "code": 0,
  "requestId": "req-...",
  "data": {
    "status": "inserted",
    "logId": 42,
    "rowsWritten": 3
  }
}
```

- `status` ∈ `{inserted, parse_error}`
- `parse_error` 时 `rowsWritten=0`，`parseError` 字段含原因

#### `GET /v2/order-sync/synced-ids`

查询已同步 id 列表（分页）。

| 参数 | 类型 | 说明 |
| --- | --- | --- |
| `shopId` | string | required |
| `domain` | string | required；`orders`/`logistics`/`statements` |
| `limit` | int | 1-500，默认 500 |
| `offset` | int | ≥ 0，默认 0 |

响应（`code: 0`）：

```json
{
  "code": 0,
  "requestId": "req-...",
  "data": {
    "domain": "orders",
    "ids": ["order-1", "order-2"],
    "total": 2,
    "limit": 500,
    "offset": 0
  }
}
```

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
| `GET /v2/linkage/*` (GETs) | readonly | v2 |
| `POST /v2/linkage/issues/{id}/resolve` | readwrite | v2 |
| `POST /v2/linkage/overrides` | admin | v2 |
| `GET /v2/reporting/*` | readonly | v2 |
| `POST /v2/reporting/manual-costs` | readwrite | v2 |
| `GET /v2/fx/latest`, `/v2/fx/convert` | readonly | v2 — cached (fx.sync ≈1 上游请求/天，API 路径零上游) |
| `GET /v2/pages/manual-costs` | readonly | v2 (HTML — not a machine contract) |
| `GET /v2/pages/spu-roi` | readonly | v2 (HTML — not a machine contract) |
| `GET /v2/analytics/spu-roi` | readonly | stable 只读（口径见 `analytics/spu-real-roi-dashboard.md`） |
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
