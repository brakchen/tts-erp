# tts-erp External API Guide

This document is the **stable public API contract** for the tts-erp FastAPI
service (port 9877). Internal-only endpoints (sync, token, oauth-receiver
passthrough) are listed in `GET /endpoints` but NOT covered here.

## TL;DR — quick reference for agents

All endpoints below are served by the FastAPI service at
`http://127.0.0.1:9877` (or `http://daqiang.nat100.top` from outside, **port
already stripped** at the NAT layer). Every endpoint other than the
explicitly-public ones requires `Authorization: Bearer <key>` or
`X-API-Key: <key>`.

| What you want | Endpoint | Role | Time fields |
| --- | --- | --- | --- |
| Service liveness / version | `GET /healthz` | public | — |
| Discover every route | `GET /endpoints` | public | — |
| Auto-generated schema | `GET /openapi.json`, `/docs`, `/redoc` | public | — |
| List orders | `GET /db/orders` | readonly | `create_time`, `paid_time`, `shipped_time`, `delivered_time`, `cancelled_time` (epoch sec) |
| Get one order + items + shipping | `GET /db/orders/{id}` (+ `/items`, `/shipping`) | readonly | epoch + ISO |
| List refunds / cancellations | `GET /db/returns`, `/db/cancellations` | readonly | `create_time`, `update_time` |
| Get one refund (with computed `refund_amount`) | `GET /db/returns/{id}` | readonly | epoch + ISO |
| List statements / payments | `GET /db/statements`, `/db/payments` | readonly | `statement_time` |
| Per-statement fee breakdown (58 fields) | `GET /db/statement_transactions` | readonly | epoch |
| Logistics tracking summary | `GET /db/logistics_tracking` | readonly | — |
| Logistics event stream (per order) | `GET /db/logistics_events` | readonly | milliseconds |
| Live tracking (TikTok proxy, auto-persists) | `GET /logistics/orders/{id}/tracking?shop_id=X` | readonly | — |
| Sync ingest state | `GET /db/sync_log` | admin (default) | ISO |
| Analytics cursor (for `tk-adv-cost-monitor`) | `GET /v1/analytics/sync/cursor` | readwrite + scope | `latestCompletedDay` / `nextRequiredDay` (date) |

Key gotchas (read these before writing code):

- **Epoch seconds, not millis**, on `/db/*` (BIGINT in DB). Response includes
  matching `_iso` UTC strings for convenience. **`/db/logistics_events` is
  the exception** — it uses **milliseconds** because the source API does.
- Pagination on `/db/orders` and `/db/returns` is **keyset** via opaque
  base64 `cursor`. Pass the previous response's `next_cursor` as-is.
  `limit` is 1..500 (default 50).
- `cursor` is opaque (do not parse). Invalid cursor → 400.
- Refunds expose computed `refund_amount` (numeric) + `refund_currency`
  (string), derived from `raw->'refund_amount'->>'refund_total'` /
  `raw->'refund_amount'->>'currency'` (TikTok 202309 spec; **note the
  nested object, not `raw->'refund'`** — this changed on 2026-08-20). NULL
  when raw has no refund_amount object.
- All auth modes: see [Authentication](#authentication) below.

Minimal recipe — first call:

```bash
KEY=$(cat ~/.tts-erp-key)        # mint with: python3 api_keys.py create --role readonly --name agent-x
curl -sS -H "X-API-Key: $KEY" \
  "http://127.0.0.1:9877/db/orders?shop_id=7494763368967603447&limit=2"
```

Minimal recipe — paginate to end:

```bash
URL="http://127.0.0.1:9877/db/orders?shop_id=7494763368967603447&limit=200"
while : ; do
  RESP=$(curl -sS -H "X-API-Key: $KEY" "$URL")
  echo "$RESP" | jq -r '.items[] | [.order_id, .order_status_name, .create_time_iso] | @tsv'
  CURSOR=$(echo "$RESP" | jq -r '.next_cursor // ""')
  [ -z "$CURSOR" ] && break
  URL="$URL&cursor=$CURSOR"
done
```

For full request/response schemas, see the **Endpoints** section below.

## Authentication

Every request to a non-public endpoint must carry an API key. Two header
forms are accepted:

```http
Authorization: Bearer <your-api-key>
```

```http
X-API-Key: <your-api-key>
```

`Authorization` takes precedence if both are present. Keys are 32+ chars,
prefixed by role (`ttserp_rw_…` for readwrite, `ttserp_admin_…` for
admin, `ttserp_ro_…` for readonly).

**Errors**:

- `401 missing bearer token` — no header sent
- `401 invalid, disabled or expired api key` — header present but key not
  recognised (typo, disabled, or expired)
- `403 requires <role>` — key is recognised but lacks the role for this path

In `enforce` mode (production), the service returns the error. In `shadow`
mode, the error is logged to stderr and the request is still served. In
`off` mode auth is bypassed entirely (development only).

## Rate Limiting

Sliding-window per API key. Default: **100 requests per 60 seconds**.

Over-quota responses:

```http
HTTP/1.1 429 Too Many Requests
Retry-After: 47
X-RateLimit-Limit: 100
X-RateLimit-Remaining: 0
Content-Type: application/json

{"detail":"rate limit exceeded: 100 req/60s per api key","retry_after_s":47}
```

Configure the per-key limit via env: `TTS_ERP_RATE_LIMIT_PER_MIN=200`.

Note: the `/healthz` endpoint is exempt from rate limiting (it is also
auth-exempt).

## CORS

Default: **no browser cross-origin access allowed** (empty allow-origin
list). To enable specific origins, set:

```
TTS_ERP_CORS_ALLOW_ORIGINS=https://app.example.com,https://admin.example.com
```

For dev/internal deploys, `TTS_ERP_CORS_ALLOW_ORIGINS=wildcard` enables
`*` — do not use in production.

## Pagination

All list endpoints (`GET /db/orders`, `GET /db/returns`) support keyset
pagination via opaque base64 cursors:

```
GET /db/orders?limit=100   → 200 {count, next_cursor, items}
GET /db/orders?limit=100&cursor=<next_cursor>   → next page
```

Cursors encode `(create_time, order_id)` for orders or
`(create_time, return_id)` for returns. Stop when `next_cursor` is `null`
on the response.

`limit` is 1..500 (default 50). Invalid cursor → 400. Out-of-range limit
→ 400.

## Timestamps

- Query parameters that take a time range (`create_time_ge`,
  `paid_time_lt`, etc.) are **unix epoch seconds** (BIGINT in the DB).
- Response objects include `_iso` suffixed fields (e.g. `create_time_iso`)
  for convenience. These are ISO-8601 UTC strings.
- `synced_at` and `updated_at` are returned as ISO-8601 strings (these
  columns are `TIMESTAMPTZ` in the DB; original format is preserved).

## Endpoints

### Orders

#### `GET /db/orders`

Local-DB orders list. Returns a JSON object `{count, next_cursor, items}`
where each item has:

| field | type | notes |
| --- | --- | --- |
| `order_id` | string | PK |
| `shop_id` | string | FK |
| `order_status_name` | string | one of AWAITING_SHIPMENT / AWAITING_COLLECTION / IN_TRANSIT / DELIVERED / COMPLETED / CANCELLED |
| `payment_amount` | numeric | |
| `payment_currency` | string | |
| `total_amount` | numeric | |
| `buyer_email` | string | |
| `create_time` | integer | epoch seconds |
| `create_time_iso` | string | UTC ISO |
| `update_time`, `paid_time`, `shipped_time`, `delivered_time`, `cancelled_time` | integer | epoch seconds + corresponding `_iso` strings |
| `fulfillment_type` | string | |
| `synced_at`, `updated_at` | string | TIMESTAMPTZ ISO |

Query parameters:

| name | type | notes |
| --- | --- | --- |
| `shop_id` | string | required for filtering (use the actual shop_id) |
| `status` | string | exact match on `order_status_name` |
| `limit` | int | 1..500, default 50 |
| `create_time_ge`, `create_time_lt` | epoch | inclusive / exclusive |
| `paid_time_ge`, `paid_time_lt` | epoch | inclusive / exclusive |
| `shipped_time_ge`, `shipped_time_lt` | epoch | inclusive / exclusive |
| `delivered_time_ge`, `delivered_time_lt` | epoch | inclusive / exclusive |
| `cancelled_time_ge`, `cancelled_time_lt` | epoch | inclusive / exclusive |
| `cursor` | string | opaque, from previous response `next_cursor` |

Example:

```bash
curl -H "X-API-Key: $KEY" \
  "http://127.0.0.1:9877/db/orders?shop_id=7494763368967603447&limit=2"
```

```json
{
  "count": 2,
  "next_cursor": "eyJ0IjoxNzg3MjM1ODA5LCJpIjoiNTg1NjQzNjI5NDY1MjA3OTE3In0",
  "items": [...]
}
```

#### `GET /db/orders/{order_id}`

Single order detail (SELECT * FROM orders). 404 if not in local DB.

```bash
curl -H "X-API-Key: $KEY" \
  "http://127.0.0.1:9877/db/orders/585627776242845445"
```

#### `GET /db/orders/{order_id}/items`

Order line items (`order_items` table).

#### `GET /db/orders/{order_id}/shipping`

Order shipping info (`order_shippings` table).

### Refunds

#### `GET /db/returns`

Local-DB returns list. Same pagination contract as `/db/orders`. Each item
includes the computed field:

- `refund_amount` — `numeric`, derived from
  `raw->'refund_amount'->>'refund_total'` (TikTok 202309 spec; **note the
  nested object, not `raw->'refund'`** — this changed on 2026-08-20 when
  the wrong path was corrected). NULL when raw doesn't have a
  `refund_amount` object.
- `refund_currency` — string, from `raw->'refund_amount'->>'currency'`. NULL
  when no refund_amount object.

Query parameters (plus standard `limit`, `cursor`, `shop_id`, `status`):

| name | type |
| --- | --- |
| `create_time_ge`, `create_time_lt` | epoch |
| `update_time_ge`, `update_time_lt` | epoch |

#### `GET /db/returns/{return_id}?include_raw=true`

Single return detail. `include_raw=false` omits the heavy `raw` JSON.

```bash
curl -H "X-API-Key: $KEY" \
  "http://127.0.0.1:9877/db/returns/4042016489520465323"
```

### Logistics

#### `GET /db/logistics_tracking`

Query parameters: `shop_id`, `final_status`, `arrived_overseas`,
`tracking_number`, `order_id`, `limit` (1..500, default 100).

#### `GET /db/logistics_events`

Per-order event list (timestamps in **milliseconds**, unlike other tables).

| name | type | notes |
| --- | --- | --- |
| `order_id` | string | |
| `action_code` | int | |
| `event_time` | int | milliseconds |
| `event_time_iso` | string | computed UTC ISO |
| `description`, `location` | string | |

#### `GET /logistics/orders/{order_id}/tracking?shop_id=X`

**Live** tracking (proxies the TikTok `/fulfillment/202309/orders/<id>/tracking`
endpoint) AND auto-persists the result into `logistics_tracking` /
`logistics_events`, AND backfills `tracking_number` from
`order_shippings` if present. This is the right endpoint when you want
the freshest possible tracking data — `/db/logistics_tracking` is the
persisted view (cron refreshes every 10 min).

Returns the upstream TikTok envelope (`code`, `data.tracking`, etc.)
verbatim. If `code != 0`, the endpoint returns HTTP 502 with the upstream
payload in `detail`.

Query parameters:

| name | type | notes |
| --- | --- | --- |
| `order_id` | path | required |
| `shop_id` | string | required |

```bash
curl -H "X-API-Key: $KEY" \
  "http://127.0.0.1:9877/logistics/orders/585627776242845445/tracking?shop_id=7494763368967603447"
```

> Note: this is **different** from `GET /db/logistics_tracking` (persisted,
> maybe stale by up to 10 min) and from `GET /orders/{id}/tracking` (also
> a TikTok proxy but does NOT persist). Use this endpoint when you want
> the live data plus a side-effect refresh of the persisted view.

### Finance

#### `GET /db/statements`

Statement summary list. Parameters: `shop_id`, `limit` (1..500, default 50).

#### `GET /db/payments`

Outgoing payments list. Parameters: `shop_id`, `status`, `limit`.

#### `GET /db/statement_transactions`

Statement transaction detail (fee breakdowns). Parameters: `shop_id`,
`statement_id`, `order_id`, `type`, `limit` (1..500, default 100).

### Cancellations

#### `GET /db/cancellations`

Cancel list. Parameters: `shop_id`, `status`, `limit`.

### Analytics Sync

Mounted under tts-erp at `/v1/analytics/sync/*`. Powers the
`tk-adv-cost-monitor` Chrome extension. Auth requires **readwrite**
role plus a per-seller scope grant (api_key's `scopes` array).
Full protocol lives in
[`analytics_sync/tech-doc/analytics-sync.md`](../analytics_sync/tech-doc/analytics-sync.md);
this section is the agent-facing quick reference.

#### `GET /v1/analytics/sync/cursor`

Returns the latest-day state for every `(storageKey, campaignId)` pair
known for the scope. `nextRequiredDay` is authoritative for the daily
job scheduler.

Query parameters (all required except `storageKey` / `campaignId` /
`cursor` / `pageSize`):

| name | type | notes |
| --- | --- | --- |
| `sellerId` | string | required, ≤ 128 chars |
| `advertiserId` | string | required, ≤ 128 chars |
| `storageKey` | enum | optional; one of `productAnalyses`, `sessionAnalyses`, `campaignChangeLogs` |
| `campaignId` | string | optional, ≤ 128 chars |
| `cursor` | string | optional, opaque (reserved for future use) |
| `pageSize` | int | optional, 1..100, default 50 |

Response:

```json
{
  "code": 0,
  "requestId": "req-…",
  "data": {
    "timezone": "Asia/Shanghai",
    "items": [
      {
        "storageKey": "productAnalyses",
        "campaignId": "campaign-1",
        "latestCompletedDay": "2026-08-22",
        "nextRequiredDay": "2026-08-23"
      }
    ],
    "nextCursor": null
  }
}
```

`nextRequiredDay = max(latestCompletedDay + 1 day, today_in_shop_tz − bootstrap_lookback_days)`.
If `latestCompletedDay` is NULL, returns `today_in_shop_tz − 30 days`
(configurable via env `ANALYTICS_SYNC_BOOTSTRAP_LOOKBACK_DAYS`).

`403 SCOPE_DENIED` if the api_key's `scopes[]` doesn't cover the
requested `(sellerId, advertiserId)`.

#### `POST /v1/analytics/sync/batches`

Idempotent batch upload (≤ 100 records / 2 MB body). The server
**recomputes** the canonical idempotency key from each record and
rejects any record whose client-sent `idempotencyKey` doesn't match the
recomputed one — the DB unique constraint is the single source of
dedup truth.

Request body:

```json
{
  "protocolVersion": 1,
  "requestId": "req-…",
  "scope": {"sellerId": "seller-1", "advertiserId": "adv-1", "shopName": "demo-shop"},
  "records": [
    {
      "idempotencyKey": "<64-char lowercase hex>",
      "storageKey": "productAnalyses",
      "campaignId": "campaign-1",
      "day": "2026-08-23",
      "page": 1,
      "endpoint": "/oec_ads/...",
      "method": "POST",
      "response": {"data": []},
      "source": "background_poll",
      "capturedAt": "2026-08-23T03:00:00.000Z"
    }
  ]
}
```

Success response (`code: 0`):

```json
{
  "code": 0,
  "requestId": "req-…",
  "data": {
    "accepted": [
      {"idempotencyKey": "...", "status": "inserted"},
      {"idempotencyKey": "...", "status": "duplicate"}
    ],
    "rejected": [
      {"idempotencyKey": "...", "code": "SCHEMA_INVALID", "message": "...", "retryable": false}
    ]
  }
}
```

`inserted` and `duplicate` are **both successes**. `rejected[*].retryable = false`
means do NOT retry unchanged — surface the error to a human.

Errors:

| code | meaning | retry? |
| --- | --- | --- |
| 400 | malformed JSON / schema invalid / unsupported protocol version | no |
| 401 | missing or invalid Bearer token | no |
| 403 | scope mismatch (`SCOPE_DENIED`) | no |
| 413 | body > 2 MB | no (split batch) |
| 429 | rate limited | yes (after `Retry-After`) |

### Misc

#### `GET /healthz`

Public (auth-exempt, rate-limit-exempt). Returns `{status, ts, version}`.

#### `GET /endpoints`

Public. Lists every endpoint registered (both external-stable AND
internal-only — useful as a discovery surface for ops).

#### `GET /openapi.json` / `GET /docs` / `GET /redoc`

Public. Auto-generated OpenAPI 3.1 schema (`/openapi.json`), Swagger UI
(`/docs`), ReDoc UI (`/redoc`). All three are in `EXEMPT_PATHS` in
`tdd/auth.py`, so they bypass both auth and rate limit.

> Production hardening: if you don't want browsers poking at the schema,
> restrict these three at the reverse proxy. The service itself does not
> gate them.

## Error responses

| status | meaning |
| --- | --- |
| 400 | malformed query (invalid cursor, bad time range, bad limit, missing required field) |
| 401 | missing / invalid / expired api key |
| 403 | key lacks required role |
| 404 | resource not in local DB |
| 429 | rate limit exceeded (see Rate Limiting) |
| 502 | upstream TikTok / oauth-receiver error |
| 500 | unexpected server error |

All error responses are JSON: `{"detail": "<message>"}`.

## OpenAPI / Swagger UI

The FastAPI service auto-publishes OpenAPI 3.1 at:

- `GET /openapi.json` — schema
- `GET /docs` — Swagger UI (interactive)
- `GET /redoc` — ReDoc UI

By default these are gated by `TTS_ERP_AUTH_MODE=shadow` (so they're
reachable during pre-production). For production hardening, restrict them
at the reverse proxy.

## Creating / managing API keys

Use `api_keys.py`:

```bash
# create a readonly key
python3 api_keys.py create --role readonly --name "external-orders-reader"

# create a readwrite key (for sync endpoints)
python3 api_keys.py create --role readwrite --name "external-sync"

# list all keys
python3 api_keys.py list

# disable (revoke) a key
python3 api_keys.py disable --key-prefix "ttserp_ro_abc123"
```

The full key is shown ONCE on creation. Store it securely.

## Versioning

External endpoints follow semantic stability:

- A new query parameter is **backwards-compatible** (old clients ignore).
- A new response field is **backwards-compatible**.
- Removing a field, renaming a field, or changing the meaning of an
  existing field is a **breaking change** and requires a new API version.

Current contract is v1 (no version prefix in the URL).

## Examples

### Analytics cursor poll (Chrome extension pattern)

```bash
# Get the next day to upload for every campaign of seller-1 / adv-1
curl -sS -H "X-API-Key: $KEY" \
  "http://127.0.0.1:9877/v1/analytics/sync/cursor?sellerId=seller-1&advertiserId=adv-1&pageSize=100" \
  | jq -r '.data.items[] | [.storageKey, .campaignId, .nextRequiredDay] | @tsv'
```

### Daily summary

```bash
# UTC+7 today 00:00 = epoch <get from python>
GE=$(python3 -c "from datetime import datetime, timezone, timedelta; print(int(datetime.now(timezone(timedelta(hours=7))).replace(hour=0,minute=0,second=0,microsecond=0).timestamp()))")
LT=$(($GE + 86400))

curl -H "X-API-Key: $KEY" \
  "http://127.0.0.1:9877/db/orders?shop_id=7494763368967603447&create_time_ge=$GE&create_time_lt=$LT&limit=500"
```

### Page through all returns

```bash
URL="http://127.0.0.1:9877/db/returns?shop_id=7494763368967603447&limit=200"
while : ; do
  RESP=$(curl -s -H "X-API-Key: $KEY" "$URL")
  echo "$RESP" | python3 -c "import json,sys; d=json.load(sys.stdin); [print(it['return_id'], it['refund_amount']) for it in d['items']]"
  CURSOR=$(echo "$RESP" | python3 -c "import json,sys; v=json.load(sys.stdin).get('next_cursor'); print(v or '')")
  [ -z "$CURSOR" ] && break
  URL="http://127.0.0.1:9877/db/returns?shop_id=7494763368967603447&limit=200&cursor=$CURSOR"
done
```

### Refund amount detail for one return

```bash
curl -H "X-API-Key: $KEY" \
  "http://127.0.0.1:9877/db/returns/4042016489520465323?include_raw=false" \
  | python3 -c "import json,sys; d=json.load(sys.stdin); print('refund_amount:', d.get('refund_amount')); print('currency:', d.get('refund_currency'))"
```

## Stability matrix

Stable external endpoints (safe to build dashboards on):

| endpoint | role | stability |
| --- | --- | --- |
| `GET /healthz` | public | stable |
| `GET /endpoints` | public | stable |
| `GET /openapi.json`, `/docs`, `/redoc` | public | stable (consider proxy-restricting in prod) |
| `GET /db/orders` | readonly | v1 |
| `GET /db/orders/{id}` | readonly | v1 |
| `GET /db/orders/{id}/items` | readonly | v1 |
| `GET /db/orders/{id}/shipping` | readonly | v1 |
| `GET /db/returns` | readonly | v1 |
| `GET /db/returns/{id}` | readonly | v1 |
| `GET /db/cancellations` | readonly | v1 |
| `GET /db/statements` | readonly | v1 |
| `GET /db/payments` | readonly | v1 |
| `GET /db/statement_transactions` | readonly | v1 |
| `GET /db/logistics_tracking` | readonly | v1 |
| `GET /db/logistics_events` | readonly | v1 |
| `GET /logistics/orders/{id}/tracking` | readonly | v1 (writes to DB; counts as a query+side-effect) |
| `GET /v1/analytics/sync/cursor` | readwrite + scope | v1 |
| `POST /v1/analytics/sync/batches` | readwrite + scope | v1 |

Internal-only (may break without notice — do NOT build dashboards on
these):

| endpoint | notes |
| --- | --- |
| `POST /sync/*` | cron-driven data ingest |
| `POST /orders/*` (TikTok proxy) | live writes to TikTok; side-effects |
| `GET /finance/*` (TikTok proxy) | live upstream calls |
| `GET /token/{shop_id}` | admin-only, reveals plaintext tokens |
| `GET /db/sync_log` | admin-only, in-process mirror |
| `GET /miaoshou/*` | Wanshifu/Miaoshou SDK proxy (separate domain; admin role by default) |
