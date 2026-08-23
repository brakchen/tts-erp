# Plugin integration guide — `tk-adv-cost-monitor`

This document is for the Chrome extension maintainers wiring
`tk-adv-cost-monitor` to the `analytics_sync` backend. It is a
companion to [`README.md`](../README.md) and the protocol spec — read
the architecture notes first if anything is ambiguous.

---

## 1. Pre-flight checklist

Before the plugin can talk to `analytics_sync`, two things must change
in the extension:

1. **Extend the local `CapturedAnalyticsData` model** to include
   `storageKey`, `campaignId`, `day`, and `page` as first-class fields.

   Today (per `/home/schan/tk-adv-cost-monitor/src/core/types.ts`):

   ```typescript
   export type CapturedAnalyticsData = {
     sourceRecordId: string;
     endpoint: string;
     method: string;
     body?: unknown;
     response: unknown;
     source: DataSource;
     capturedAt: string;
     localSyncedAt: string;
     remoteSyncStatus: "pending" | "synced";
     remoteSyncedAt?: string;
     sellerId?: string;
     advertiserId?: string;
     shopName?: string;
   };
   ```

   Required addition (illustrative, not a PR):

   ```typescript
   export type AnalyticsStorageKey =
     | "productAnalyses" | "sessionAnalyses" | "campaignChangeLogs";

   export type CapturedAnalyticsData = {
     // ... existing fields ...
     storageKey: AnalyticsStorageKey;     // NEW
     campaignId: string;                  // NEW
     day: string;                         // NEW (YYYY-MM-DD)
     page: number;                        // NEW (positive int)
   };
   ```

2. **Populate those fields at capture time** from the daily-job
   context. The `page` field in particular must be the actual
   pagination index from the originating TikTok request — do NOT
   fabricate one.

   The server does **not** parse these from `requestBody` or `endpoint`.
   The plugin must send them as siblings of `endpoint` / `response`.

---

## 2. Required headers on every request

```http
Authorization: Bearer ttserp_rw_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
Content-Type: application/json
X-Request-Id: <uuid>            # echoed back, surfaces in audit log
X-Protocol-Version: 1
```

`X-Client-Version: 0.4.28` (or whatever the extension's `package.json`
says) is recommended for server-side telemetry but is not parsed.

**Do NOT send** TikTok Cookies, Feishu webhook tokens, or any browser
authorization headers. The middleware does not accept them.

---

## 3. Computing the idempotency key

The server **always recomputes** this key for every received record. The
plugin must produce the same bytes:

```typescript
import { createHash } from "node:crypto";

function canonicalKeyFor(params: {
  sellerId: string;
  advertiserId: string;
  storageKey: "productAnalyses" | "sessionAnalyses" | "campaignChangeLogs";
  campaignId: string;
  day: string;     // "YYYY-MM-DD"
  page: number;
}): string {
  const trimmed = {
    sellerId: params.sellerId.trim(),
    advertiserId: params.advertiserId.trim(),
    storageKey: params.storageKey,
    campaignId: params.campaignId.trim(),
    day: params.day,
    page: Number(params.page),
  };
  // Sort keys, no insignificant whitespace.
  const ordered = Object.keys(trimmed)
    .sort()
    .reduce<Record<string, unknown>>((acc, k) => {
      acc[k] = (trimmed as any)[k];
      return acc;
    }, {});
  const payload = JSON.stringify(ordered);  // default = no whitespace
  return createHash("sha256").update(payload, "utf8").digest("hex");
}
```

**Reference test vector** (computed against
`analytics_sync/domain.py::compute_idempotency_key`):

```python
compute_idempotency_key(
    seller_id="seller-1",
    advertiser_id="adv-1",
    storage_key="productAnalyses",
    campaign_id="campaign-1",
    day="2026-08-23",
    page=1,
)
# → "ce1ba2e1e144ef9c153a4e94f7eb0f200f289a9393d743750adedfa21c16d180"
```

If your key doesn't match, the server rejects with `SCHEMA_INVALID` —
the plugin should treat this as a programming bug, not a transient
error, and surface it for debugging.

---

## 4. Daily-job flow

The plugin runs a daily job per `(storageKey, campaignId)`. The
recommended sequence:

```
loop:
  1. GET /v1/analytics/sync/cursor
     ?sellerId=<current seller>
     &advertiserId=<current advertiser>
     &storageKey=<this job's storageKey>
     &campaignId=<this job's campaignId>

  2. for each item in data.items:
       day = item.nextRequiredDay
       while day <= today_in_shop_tz:
         records = await fetchAllPagesForDay(storageKey, campaignId, day)
         if records.empty:
           day = day + 1
           continue
         response = POST /v1/analytics/sync/batches
         if response.code == 0:
           for accepted in response.data.accepted:
             mark local record with status="synced"
           # rejected[] indicates permanent errors; do NOT retry, surface in diagnostics
         elif response.status_code in [429, 500, 502, 503, 504]:
           # rate-limited or transient — wait Retry-After seconds, then retry unchanged
           sleep(retry_after); retry
         else:
           # 4xx — likely token/scope issue; stop and surface
           stop_and_surface_diagnostic()
         day = day + 1
```

The cursor is **authoritative**: do NOT compute a start date from your
own `queryRecentDays` config or hardcoded backfill range. Always use
`nextRequiredDay` from the server.

---

## 5. Success detection

`POST /v1/analytics/sync/batches` always returns HTTP 200 when the
request was parsed. Per-record outcomes are in `data`:

```json
{
  "code": 0,
  "requestId": "req-001",
  "data": {
    "accepted": [
      { "idempotencyKey": "abcd…", "status": "inserted" },
      { "idempotencyKey": "ef01…", "status": "duplicate" }
    ],
    "rejected": [
      {
        "idempotencyKey": "ff…",
        "code": "SCHEMA_INVALID",
        "message": "idempotencyKey mismatch at records[2]: client=… server=…",
        "retryable": false
      }
    ]
  }
}
```

**Both `inserted` and `duplicate` mean success.** Mark the local
record `remoteSyncStatus = "synced"` on either, set `remoteSyncedAt`
to `now()`, and never re-upload that record.

`rejected[*]` indicates a permanent error specific to that record.
Set the local record's `remoteSyncStatus` to a diagnostic state (e.g.
`syncError`) so the next cursor tick doesn't re-enqueue it; surface the
message in the operator's diagnostic UI until an operator policy is
defined.

---

## 6. Retryable vs non-retryable HTTP errors

| HTTP | Code in body | Meaning | Plugin action |
|------|--------------|---------|---------------|
| 200  | 0              | Parsed; check per-record outcomes | See §5 |
| 400  | `MALFORMED_JSON` / `SCHEMA_INVALID` / `UNSUPPORTED_PROTOCOL_VERSION` | Client bug | Do NOT retry. Surface error. Stop sync until fixed. |
| 401  | —              | Missing/invalid Bearer token | Do NOT retry. Token needs to be reissued / reconfigured. |
| 403  | `SCOPE_DENIED`  | Token not authorized for this scope | Do NOT retry. Token needs new scopes. |
| 413  | `PAYLOAD_TOO_LARGE` | Body > 2 MB | Split the batch; the protocol mandates ≤ 100 records per request and ≤ 2 MB total. |
| 429  | `RATE_LIMITED`  | Per-token rate limit | Read `Retry-After` header (seconds). Wait, then retry the entire batch (records are idempotent). |
| 5xx  | `INTERNAL_ERROR` | Server failure | Bounded exponential backoff (e.g. 1s, 2s, 4s, 8s, give up). Records are idempotent so retry is safe. |

---

## 7. Cursor semantics — what to do with `latestCompletedDay`

The cursor endpoint returns both `latestCompletedDay` (the most recent
day that has at least one durably-stored record) and `nextRequiredDay`
(the day the plugin should sync next).

`nextRequiredDay` is what you act on. `latestCompletedDay` is diagnostic
— you can show it in the UI to confirm the sync is making progress but
don't compute the next day from it yourself.

For example:

```json
{
  "items": [{
    "storageKey": "productAnalyses",
    "campaignId": "campaign-1",
    "latestCompletedDay": "2026-08-22",
    "nextRequiredDay": "2026-08-23"
  }]
}
```

→ Sync from 2026-08-23 forward.

For the first sync (no records):

```json
{
  "items": [{
    "storageKey": "productAnalyses",
    "campaignId": "campaign-1",
    "latestCompletedDay": null,
    "nextRequiredDay": "2026-07-24"   // today − 30 days
  }]
}
```

→ Sync from 2026-07-24 forward. Don't infer a different start.

---

## 8. Multiple pages per day

The same `(scope, storageKey, campaignId, day)` may carry multiple
`page` values when the underlying TikTok API paginates. Each page is a
separate record with the same canonical inputs except `page`:

```
record 1: idempotencyKey = sha256(…page=1)
record 2: idempotencyKey = sha256(…page=2)
record 3: idempotencyKey = sha256(…page=3)
```

Send them all in one batch (up to 100 records). The server dedupes per
`(scope, storageKey, campaignId, day, page)`.

---

## 9. Example TypeScript client

A minimal client for reference (not shipped — write your own):

```typescript
class AnalyticsSyncClient {
  constructor(
    private readonly baseUrl: string,
    private readonly token: string,
    private readonly fetchFn: typeof fetch = fetch,
  ) {}

  async getCursor(params: {
    sellerId: string;
    advertiserId: string;
    storageKey?: AnalyticsStorageKey;
    campaignId?: string;
  }): Promise<CursorResponse> {
    const qs = new URLSearchParams({
      sellerId: params.sellerId,
      advertiserId: params.advertiserId,
    });
    if (params.storageKey) qs.set("storageKey", params.storageKey);
    if (params.campaignId) qs.set("campaignId", params.campaignId);

    const r = await this.fetchFn(`${this.baseUrl}/v1/analytics/sync/cursor?${qs}`, {
      headers: {
        "Authorization": `Bearer ${this.token}`,
        "X-Protocol-Version": "1",
      },
    });
    if (!r.ok) throw new AnalyticsSyncError(r.status, await r.text());
    return await r.json();
  }

  async postBatch(req: BatchRequest): Promise<BatchResponse> {
    const r = await this.fetchFn(`${this.baseUrl}/v1/analytics/sync/batches`, {
      method: "POST",
      headers: {
        "Authorization": `Bearer ${this.token}`,
        "Content-Type": "application/json",
        "X-Protocol-Version": "1",
        "X-Request-Id": req.requestId,
      },
      body: JSON.stringify(req),
    });
    // Even 4xx returns a JSON error envelope — read it.
    const body = await r.json();
    if (!r.ok) throw new AnalyticsSyncError(r.status, body);
    return body;
  }
}

class AnalyticsSyncError extends Error {
  constructor(public readonly status: number, public readonly body: any) {
    super(`HTTP ${status}: ${JSON.stringify(body)}`);
  }
}
```

Wrap calls in a retry policy that:

- Respects `Retry-After` on 429.
- Backs off exponentially on 5xx with a max retry count (e.g. 5).
- Does NOT retry 4xx (except 408 / 429).
- Logs every retry with the requestId for ops correlation.

---

## 10. Operator notes

When the operator issues a token via:

```bash
python3 api_keys.py create \
    --name chrome-ext-prod \
    --expires-days 365
```

They must paste the plaintext token into the extension's secure config
store. The token is the only credential the extension needs; it does
NOT need access to any TikTok OAuth, Feishu webhook, or shop cookies
to use `analytics_sync`.

For multi-shop deployments, issue one token per shop with the
`--scopes` argument restricting it to that seller:

```bash
python3 api_keys.py create \
    --name chrome-ext-shop-42 \
    --scopes "seller:shop-42-id"
```

A shop-scoped token receives 403 `SCOPE_DENIED` if accidentally used
against another shop's data — defense-in-depth in case the extension
sends a wrong `sellerId`.

---

## 11. Compatibility

When the protocol version bumps (e.g. to v2):

- The server will return `400 UNSUPPORTED_PROTOCOL_VERSION` if the
  plugin sends `protocolVersion: 1` after sunset.
- The plugin should be prepared to read the version from a config knob
  or auto-discover via a future `/v1/analytics/sync/version` endpoint.
- The `idempotencyKey` derivation will NOT change without a major
  protocol version bump — it is the load-bearing dedup mechanism.

See [`compatibility.md`](compatibility.md) for the full versioning
policy.
