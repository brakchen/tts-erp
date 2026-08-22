# tts-erp External API Guide

This document is the **stable public API contract** for the tts-erp FastAPI
service (port 9877). Internal-only endpoints (sync, token, oauth-receiver
passthrough) are listed in `GET /endpoints` but NOT covered here.

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

- `refund_amount` — `numeric`, derived from `raw->'refund'->>'refund_total'`.
  NULL when raw doesn't have a refund object.

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

### Misc

#### `GET /healthz`

Public. Returns `{status, ts, version}`.

#### `GET /endpoints`

Public. Lists every endpoint registered.

#### `GET /openapi.json`

Public (in production deployment via reverse proxy; auth mode default may
restrict). Auto-generated OpenAPI schema for all routes.

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

| endpoint | status |
| --- | --- |
| `GET /healthz` | stable, public |
| `GET /endpoints` | stable, public |
| `GET /db/orders` | stable, v1 |
| `GET /db/orders/{id}` | stable, v1 |
| `GET /db/orders/{id}/items` | stable, v1 |
| `GET /db/orders/{id}/shipping` | stable, v1 |
| `GET /db/returns` | stable, v1 |
| `GET /db/returns/{id}` | stable, v1 |
| `GET /db/cancellations` | stable, v1 |
| `GET /db/statements` | stable, v1 |
| `GET /db/payments` | stable, v1 |
| `GET /db/statement_transactions` | stable, v1 |
| `GET /db/logistics_tracking` | stable, v1 |
| `GET /db/logistics_events` | stable, v1 |
| `POST /sync/*` | internal, NOT external-stable |
| `POST /orders/*` (TikTok proxy) | internal, NOT external-stable |
| `GET /finance/*` (TikTok proxy) | internal, NOT external-stable |
| `GET /token/{shop_id}` | admin-only, internal |
