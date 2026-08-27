# analytics_sync — protocol, API, deployment

Replaces the retired CloudBase analytics upload path. Receives analytics
records from the Chrome extension (`tk-adv-cost-monitor`) and provides
the daily-job cursor endpoint that drives those uploads.

**Protocol versions**: the server accepts `protocolVersion` 1 and 2.
v2 adds per-day page-completeness tracking (`expectedPageCount`) so the
cursor only advances once a day's full page set is durably stored.
See §4.3 for v2 semantics and [`compatibility.md`](compatibility.md) for
the v1↔v2 policy.

This document is the long-form companion to [`README.md`](../README.md);
it covers the API contract, error semantics, env-var matrix, deployment
guide, and a short compatibility note for protocol-version changes.

---

## 1. Domain terms

`storageKey` is one of:

- `productAnalyses`
- `sessionAnalyses`
- `campaignChangeLogs`

`day` is `YYYY-MM-DD`, inclusive, in the shop's canonical IANA timezone
(stored in `analytics_shop_timezones.timezone`, default `Asia/Shanghai`).
`capturedAt` is always an ISO-8601 UTC timestamp with explicit `Z` or
`+00:00` suffix.

The local `sourceRecordId` is a UUID the plugin uses for tracing only;
it is **not** an idempotency key.

---

## 2. Idempotency key

Stable key derived from:

```text
sha256(canonical_json({
  sellerId,
  advertiserId,
  storageKey,
  campaignId,
  day,
  page
}))
```

`canonical_json` rules: UTF-8, sorted keys, no insignificant whitespace
(`separators=(",", ":")`), string values trimmed.

The server **always recomputes** this key for every received record and
rejects any record whose client-sent `idempotencyKey` does not match.
This way the database's unique constraint on `idempotency_key` is the
single source of dedup truth.

---

## 3. Authentication

- `Authorization: Bearer <token>` (preferred) or `X-Sync-Token: <token>`
- Tokens are 40+ char random URLsafed strings prefixed `ttserp_<role>_` (e.g. `ttserp_rw_` for readwrite sync tokens)
- DB stores only the SHA-256 hex digest plus a 16-char prefix
- `ANALYTICS_SYNC_AUTH_MODE`: `off` | `shadow` (log would-deny) | `enforce`
  (default `enforce`)
- 60-second in-process cache; revocation propagates within one TTL
- Plaintext token is emitted **exactly once** at `create`/`rotate` time
- Plugin must NOT send TikTok Cookies, Feishu secrets, or browser auth
  headers — the middleware does not accept them

---

## 4. Endpoints

### `GET /v1/analytics/sync/cursor`

Query parameters:

| Param | Type | Required | Notes |
| ------- | ------ | ---------- | ------- |
| `sellerId` | string | yes | ≤ 128 chars |
| `advertiserId` | string | yes | ≤ 128 chars |
| `storageKey` | enum | no | filter to one dataset |
| `campaignId` | string | no | filter to one campaign |
| `cursor` | string | no | opaque pagination cursor (MVP: unused) |
| `pageSize` | int | no | 1..100, default 50 |

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

`nextRequiredDay` is authoritative. Computation:

```text
if latestCompletedDay is not NULL:
    nextRequiredDay = latestCompletedDay + 1 day
elif the unit has any record (first_seen_day is not NULL):
    nextRequiredDay = first_seen_day        # anchor day not yet complete
else:
    nextRequiredDay = bootstrap day         # today_in_shop_tz - lookback
```

`latestCompletedDay` is the last day of the **contiguous run of complete
days** starting at the unit's first recorded day (`first_seen_day`). It
never skips a missing or incomplete earlier day (§4.3).

The bootstrap lookback defaults to 30 days, configurable via
`ANALYTICS_SYNC_BOOTSTRAP_LOOKBACK_DAYS`.

### `POST /v1/analytics/sync/batches`

Request (protocolVersion 2 — v1 simply omits `expectedPageCount`):

```json
{
  "protocolVersion": 2,
  "requestId": "req-…",
  "scope": {
    "sellerId": "seller-1",
    "advertiserId": "adv-1",
    "shopName": "demo-shop"
  },
  "records": [
    {
      "idempotencyKey": "sha256-hex",
      "sourceRecordId": "local-uuid",
      "storageKey": "productAnalyses",
      "campaignId": "campaign-1",
      "day": "2026-08-23",
      "page": 1,
      "expectedPageCount": 3,
      "endpoint": "/oec_ads/...",
      "method": "POST",
      "requestBody": { "campaign_id": "campaign-1" },
      "response": { "data": [] },
      "source": "background_poll",
      "capturedAt": "2026-08-23T03:00:00.000Z",
      "schemaVersion": 2
    }
  ]
}
```

Limits:

- Maximum 100 records per request (`MIN=1`, `MAX=100`)
- Maximum 2 MB body (returns 413)
- `storageKey` ∈ `{productAnalyses, sessionAnalyses, campaignChangeLogs}`
- `day` is ISO date `YYYY-MM-DD`
- `page` is positive int (≥ 1); under v2 also `page <= expectedPageCount`
- `expectedPageCount` (v2, required) is a positive int; all records for the
  same `(sellerId, advertiserId, storageKey, campaignId, day)` unit must
  agree on it — within one batch and against previously stored batches.
  A mismatch is rejected per-record with `PAGE_COUNT_CONFLICT`
  (`retryable: false`) and the cursor never advances.
- `capturedAt` MUST include a timezone (`Z` or `+HH:MM`)
- `idempotencyKey` MUST be exactly 64 lowercase hex chars
  AND must match the server's canonical computation

Success response (HTTP 200):

```json
{
  "code": 0,
  "requestId": "req-…",
  "data": {
    "accepted": [
      {"idempotencyKey": "sha256…", "status": "inserted"},
      {"idempotencyKey": "sha256…", "status": "duplicate"}
    ],
    "rejected": [
      {
        "idempotencyKey": "sha256…",
        "code": "SCHEMA_INVALID",
        "message": "idempotencyKey mismatch at records[1]: client=… server=…",
        "retryable": false
      }
    ]
  }
}
```

`inserted` and `duplicate` are **both successes** from the plugin's
perspective — the plugin may mark the local record as `synced`.
`rejected[*].retryable = false` means the plugin should NOT retry
unchanged; surface the error in diagnostics.

---

## 4.3 Daily completeness (protocolVersion 2)

The server is the single source of truth for "is this day complete".
A **daily unit** is:

```text
sellerId + advertiserId + storageKey + campaignId + day
```

A unit-day is **complete** iff the server has durably stored every page
`1, 2, …, expectedPageCount` for it (tracked in `analytics_daily_pages`

- `analytics_daily_completeness`). Until then the day is incomplete and
the cursor must not advance past it.

Consequences:

1. Page 1 of 3 arrives → record stored, cursor stays.
2. Page 2 arrives before page 1 → stored, cursor stays.
3. Pages 1, 2, 3 all `inserted` or `duplicate` → day marked complete,
   cursor advances (in the same transaction as the writes).
4. A re-uploaded page returns `duplicate` and still counts toward
   completeness.
5. Conflicting `expectedPageCount` for the same day → per-record
   `PAGE_COUNT_CONFLICT`, `retryable: false`, cursor unmoved.
6. A page that fails, is rejected, or never arrives → the day stays
   incomplete and the cursor stays.
7. Records, completeness flags, and cursor updates commit in ONE
   transaction — no torn state.
8. `latestCompletedDay` is the last day of the contiguous complete
   prefix starting at the unit's first recorded day; a missing earlier
   day is never skipped.
9. `nextRequiredDay` is the first day after `latestCompletedDay` that is
   not yet complete — server-authoritative.
10. With no records at all, the cursor returns the server-defined
    bootstrap day (`today_in_shop_tz - ANALYTICS_SYNC_BOOTSTRAP_LOOKBACK_DAYS`);
    the client must not substitute its own `queryRecentDays` heuristic.

---

## 5. HTTP error contract

| Code | Meaning | Retry? |
| ------ | --------- | -------- |
| 400 | Malformed JSON / schema invalid / unsupported protocol version | No |
| 401 | Missing or invalid Bearer token | No (fix client config) |
| 403 | (reserved for future scope mismatch) | No |
| 409 | (reserved for true scope/idempotency conflict) | No |
| 413 | Request body > 2 MB | No (split the batch) |
| 429 | Rate limited (reserved; not yet implemented) | Yes (after Retry-After) |
| 5xx | Server error | Yes (bounded backoff) |

All error envelopes carry `{code, message, requestId, retryable}` and
**never echo the request body, the token, or any request header**.

---

## 6. Database tables

| Table | Purpose |
| ------- | --------- |
| `analytics_records` | Raw response JSON + normalized scope columns. Unique index on `idempotency_key`. |
| `analytics_daily_pages` | v2: one row per stored `(unit, day, page)` — the completeness evidence. |
| `analytics_daily_completeness` | v2: per `(unit, day)` `expected_page_count` + `is_complete` aggregate. Avoids scanning raw JSON for cursor queries. |
| `analytics_cursors` | Per-`(seller, advertiser, storageKey, campaignId)` latest contiguous complete day + `first_seen_day` anchor. |
| `analytics_shop_timezones` | Per-seller canonical IANA TZ. |
| `api_keys` | Bearer tokens (SHA-256 hash + 16-char prefix only). |
| `analytics_audit_log` | requestId-keyed audit trail (no secrets). |

Schema is in [`schema.sql`](../schema.sql). Apply with:

```bash
docker exec -i postgres psql -U postgres -d tts_erp < analytics_sync/schema.sql
```

All `CREATE` statements are `IF NOT EXISTS`; safe to re-apply.
Existing v1 installs upgrade in place with
[`migration_v2.sql`](../migration_v2.sql) (idempotent; backfills daily
completeness from `analytics_records` and treats every existing day as a
single-page complete day).

### Cursor advance invariant

Everything happens inside ONE transaction per batch:

1. For every accepted record (inserted or duplicate), insert the raw row
   into `analytics_records` (`ON CONFLICT (idempotency_key) DO NOTHING`),
   upsert the unit-day's `expected_page_count` into
   `analytics_daily_completeness`, and mark the page present in
   `analytics_daily_pages`.
2. Recompute each touched unit-day's `is_complete`:
   complete iff the pages stored cover exactly `1..expected_page_count`.
3. Recompute each touched unit's cursor: walk forward from the unit's
   `first_seen_day` (earliest day with any record) while days are
   complete; the last such day becomes `latest_completed_day`. The
   update is advance-only (`GREATEST`-style guard) so a legacy v1 cursor
   value never regresses.

Duplicates therefore never move the cursor backwards, and a partially
paged day never moves it forwards.

---

## 7. Curl examples

Assume `$TOKEN` was minted by:

```bash
python3 api_keys.py create --name chrome-ext-prod --role readwrite --expires-days 365
# SYNC TOKEN (shown ONCE, store it now):  ttserp_rw_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
```

### Healthz (no auth)

```bash
curl -s http://127.0.0.1:9878/healthz
# {"status":"ok","service":"analytics-sync","version":"0.3.0"}
```

### Cursor

```bash
curl -s \
  -H "Authorization: Bearer $TOKEN" \
  "http://127.0.0.1:9878/v1/analytics/sync/cursor?sellerId=seller-1&advertiserId=adv-1"
```

### Batch upload (protocolVersion 2)

```bash
curl -s -X POST \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -H "X-Request-Id: req-001" \
  -H "X-Protocol-Version: 2" \
  http://127.0.0.1:9878/v1/analytics/sync/batches \
  -d @batch.json
```

`batch.json` (canonical `idempotencyKey` can be computed with the
snippet in §8; note `expectedPageCount` is NOT part of the key):

```json
{
  "protocolVersion": 2,
  "requestId": "req-001",
  "scope": { "sellerId": "seller-1", "advertiserId": "adv-1", "shopName": "demo-shop" },
  "records": [
    {
      "idempotencyKey": "<computed below>",
      "sourceRecordId": "11111111-1111-1111-1111-111111111111",
      "storageKey": "productAnalyses",
      "campaignId": "campaign-1",
      "day": "2026-08-23",
      "page": 1,
      "expectedPageCount": 3,
      "endpoint": "/oec_ads/shopping/v1/oec/stat/post_product_list",
      "method": "POST",
      "requestBody": { "campaign_id": "campaign-1" },
      "response": { "data": [] },
      "source": "background_poll",
      "capturedAt": "2026-08-23T03:00:00.000Z",
      "schemaVersion": 2
    }
  ]
}
```

### Idempotency mismatch (rejected)

```bash
curl -s -X POST \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  http://127.0.0.1:9878/v1/analytics/sync/batches \
  -d '{"protocolVersion":1,"scope":{"sellerId":"s","advertiserId":"a"},"records":[{"idempotencyKey":"0000000000000000000000000000000000000000000000000000000000000000","storageKey":"productAnalyses","campaignId":"c","day":"2026-08-23","page":1,"endpoint":"/","method":"POST","response":{},"source":"x","capturedAt":"2026-08-23T00:00:00Z","schemaVersion":1}]}'
```

Returns HTTP 200 with one `rejected` entry.

---

## 8. Plugin-side: computing the idempotency key

The Chrome extension must compute the key the **same way** the server
does. Reference Python snippet:

```python
import hashlib, json

def compute_id(seller_id, advertiser_id, storage_key, campaign_id, day, page):
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

Or in TypeScript (extension):

```typescript
import { createHash } from "node:crypto";

function canonicalKeyFor(params: {
  sellerId: string; advertiserId: string;
  storageKey: string; campaignId: string;
  day: string; page: number;
}): string {
  const trimmed = {
    sellerId: params.sellerId.trim(),
    advertiserId: params.advertiserId.trim(),
    storageKey: params.storageKey,
    campaignId: params.campaignId.trim(),
    day: params.day,
    page: Number(params.page),
  };
  const ordered = Object.keys(trimmed).sort().reduce((acc, k) => {
    acc[k] = (trimmed as any)[k];
    return acc;
  }, {} as Record<string, unknown>);
  const payload = JSON.stringify(ordered);  // no whitespace by default
  return createHash("sha256").update(payload, "utf8").digest("hex");
}
```

(The protocol does not currently include a vector test; lock the canonical
form by reading [`analytics_sync/domain.py`](../domain.py) directly when
in doubt.)

---

## 9. Environment variables

| Variable | Default | Required | Notes |
| ---------- | --------- | ---------- | ------- |
| `TTS_ERP_DB_URL` | — | yes | PostgreSQL DSN. `ANALYTICS_SYNC_DB_URL` is also accepted (alias). |
| `ANALYTICS_SYNC_AUTH_MODE` | `enforce` | no | `off` / `shadow` / `enforce`. |
| `ANALYTICS_SYNC_BOOTSTRAP_LOOKBACK_DAYS` | `30` | no | Cursor bootstrap lookback in days. |

The service reads `.env` from the **repository root** (sibling of
`analytics_sync/`), not from inside `analytics_sync/`. This matches the
sibling services (tts-erp, miaoshou).

---

## 10. Deployment

### Local dev

```bash
TTS_ERP_DB_URL=postgresql://postgres:...@127.0.0.1:5432/tts_erp \
ANALYTICS_SYNC_AUTH_MODE=enforce \
uvicorn analytics_sync.app:app --host 0.0.0.0 --port 9878 --reload
```

### Production (systemd, mirroring tts-erp)

A user-level systemd unit at
`~/.config/systemd/user/analytics-sync.service`:

```ini
[Unit]
Description=analytics_sync FastAPI
After=network.target

[Service]
WorkingDirectory=/home/schan/tts-erp
EnvironmentFile=/home/schan/tts-erp/.env
ExecStart=/home/schan/tts-erp/.venv/bin/python -m uvicorn analytics_sync.app:app \
    --host 0.0.0.0 --port 9878 --workers 1
Restart=on-failure
RestartSec=3

[Install]
WantedBy=default.target
```

```bash
systemctl --user daemon-reload
systemctl --user enable --now analytics-sync.service
systemctl --user status analytics-sync.service
```

`Linger=yes` is required for the user unit to survive logout — same
constraint as `tts-erp.service`.

### Postgres connection

This service shares the `tts_erp` PostgreSQL database with the sibling
`tts-erp` and `miaoshou` services. Tables are namespaced `analytics_*`
so they live alongside `miaoshou_*` and `api_keys`. Migration is one
`psql` invocation; the schema is `IF NOT EXISTS` so re-runs are safe.

---

## 11. Compatibility note (protocol versions)

- The server accepts `protocolVersion` **1 and 2**.
  Any other value is rejected with `400 UNSUPPORTED_PROTOCOL_VERSION`.
- **v1 compatibility policy**: a v1 request omits `expectedPageCount`;
  every record is treated as an implicit single-page day
  (`expectedPageCount=1`). A v1 record therefore completes its day on
  first insert — identical to pre-v2 behavior. A v1 record for a day
  that v2 has already declared multi-page is rejected with
  `PAGE_COUNT_CONFLICT` (its implicit expectation conflicts with the
  stored one), so v1 traffic can never falsely complete a v2 day.
- **v2 enablement**: a client may switch to `protocolVersion: 2` as soon
  as it (a) knows the true TikTok page count per day (from the response's
  pagination metadata) and (b) populates `expectedPageCount` on every
  record of a daily unit consistently. No server flag day is required;
  both versions are accepted concurrently.
- Bumping `protocolVersion` further:
  1. Add new fields as **optional** in the request schema.
  2. Keep accepting older versions for at least one release window.
  3. Server can refuse version `N+1` until the client has caught up by
     returning `400 UNSUPPORTED_PROTOCOL_VERSION` with a clear message.
- Idempotency-key derivation is part of the protocol and **must not
  change** between versions. If the canonical form ever changes, it
  must be a hard cut to a new protocol version — existing cursors and
  unique-key uniqueness guarantees depend on the input bytes being
  deterministic.

---

## 12. Plugin preconditions (TODO before wiring into the extension)

The Chrome extension's existing `CapturedAnalyticsData` model currently
lacks `storageKey`, `campaignId`, `day`, and `page` as first-class fields.
Before the plugin can speak this protocol, extend the local model so
those four values are populated at capture time from the daily-job
context. **The server must NOT parse these from the request body or
from `requestBody`** — the plugin must send them as siblings of
`endpoint` / `response`.
