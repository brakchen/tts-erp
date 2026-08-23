# analytics_sync — Architecture, assumptions, ambiguities

This document is the design rationale and assumption list for the
`analytics_sync` backend. It complements [`README.md`](../README.md)
(operational quick-start) and [`analytics-sync.md`](analytics-sync.md)
(API contract); it explains **why** the service is shaped this way and
**which protocol ambiguities we resolved**, so future maintainers can
make consistent decisions when the protocol evolves.

---

## 1. System context

```
┌───────────────────────────┐
│ tk-adv-cost-monitor       │
│ (Chrome MV3 extension)    │
│  ┌─────────────────────┐  │
│  │ CapturedAnalytics   │  │   daily job context
│  │ Data (local cache)  │◄─┼──── (storageKey, campaignId,
│  └─────────────────────┘  │      day, page)
│             │             │
│             │ HTTPS /     │
│             │ Bearer      │
│             ▼             │
│  POST /v1/analytics/      │
│       sync/batches        │
│  GET  /v1/analytics/      │
│       sync/cursor         │
└─────────────┬─────────────┘
              │
              ▼
┌───────────────────────────┐
│ analytics_sync (:9878)    │
│  ┌─────────────────────┐  │
│  │ FastAPI handlers    │  │
│  │  - scope check      │  │
│  │  - body-size check  │  │
│  │  - canonical key    │  │
│  │  - partial success  │  │
│  └──────────┬──────────┘  │
│             │             │
│  ┌──────────▼──────────┐  │
│  │ auth / rate-limit   │  │   plain ASGI middlewares
│  │ middlewares         │  │
│  └──────────┬──────────┘  │
│             │             │
│  ┌──────────▼──────────┐  │
│  │ pg_repositories     │  │
│  │  - upsert_records   │  │
│  │  - fetch_cursor_*   │  │
│  │  - write_audit      │  │
│  └──────────┬──────────┘  │
└─────────────┼─────────────┘
              │
              ▼
┌───────────────────────────┐
│ PostgreSQL (tts_erp DB)   │
│  analytics_records        │   raw + normalized, UNIQUE(idempotency_key)
│  analytics_cursors        │   per (seller, advertiser, storageKey, campaignId)
│  analytics_shop_timezones │   per seller IANA TZ
│  api_keys    │   Bearer tokens (SHA-256 hash + prefix)
│  analytics_audit_log      │   by requestId, no secrets
└───────────────────────────┘
```

The service is a **sibling** to `tts-erp` (port 9877) and `miaoshou` —
they share the same PostgreSQL container but live in separate FastAPI
processes so they can be restarted, versioned, and rate-limited
independently.

---

## 2. Layering (mirror of tts-erp / miaoshou)

```
domain.py            pure types (Scope, Record, CursorEntry, StorageKey, …)
                     no I/O, no framework, no DB
        │
        ▼
pg_repositories.py   PG-backed implementations
                     atomic upsert + cursor advance
        │
        ▼
auth.py              sync-token auth middleware (Bearer / X-Sync-Token)
rate_limit.py        per-token sliding-window rate limiter
app.py               FastAPI handlers, request validation, partial success
api_keys.py   operator CLI for token issuance
```

Each layer is independently testable: `domain.py` and `auth.scope_grants`
have pure-function unit tests; `pg_repositories` uses transactional
rollback; `app.py` uses FastAPI's `TestClient`.

---

## 3. Data flow

### Cursor (daily job bootstrapping)

```
plugin daily job
   │
   │ GET /v1/analytics/sync/cursor?sellerId=…&advertiserId=…
   ▼
AnalyticsSync handler
   │
   │ 1. scope check (token's scopes[] must cover sellerId/advertiserId)
   │ 2. fetch_timezone(sellerId) → IANA TZ, seed Asia/Shanghai if absent
   │ 3. fetch_cursor_page(...) → SQL read of analytics_cursors
   │ 4. for each row, compute nextRequiredDay = max(latestCompleted+1, today-30d)
   ▼
JSON response
   { data: { timezone, items: [{storageKey, campaignId, latestCompletedDay,
                                  nextRequiredDay}], nextCursor } }
```

The plugin reads `items[].nextRequiredDay` and enqueues one job per
`(storageKey, campaignId, day)` for `day ∈ [nextRequiredDay, today]`.

### Batch upload

```
plugin job for (storageKey, campaignId, day)
   │
   │ POST /v1/analytics/sync/batches
   │ body: { protocolVersion, requestId, scope, records: [{...records[]}] }
   ▼
AnalyticsSync handler
   │
   │ 1. body size check (Content-Length pre-check + actual read) → 413
   │ 2. JSON parse → 400 MALFORMED_JSON
   │ 3. pydantic schema validation → 400 SCHEMA_INVALID
   │ 4. protocolVersion check → 400 UNSUPPORTED_PROTOCOL_VERSION
   │ 5. scope check on sellerId/advertiserId in body → 403 SCOPE_DENIED
   │ 6. per-record:
   │      a. response_data size cap (256 KB) → rejected RESPONSE_TOO_LARGE
   │      b. canonical idempotency key recomputation
   │         - mismatch → rejected SCHEMA_INVALID
   │         - match    → record appended to valid_records
   │ 7. PgAnalyticsRepository.upsert_records(scope, valid_records, request_id)
   │      in a single transaction:
   │        - INSERT INTO analytics_records ... ON CONFLICT DO NOTHING
   │        - for each inserted (sid, aid, skey, camp, day):
   │            UPSERT INTO analytics_cursors
   │              SET latest_completed_day = GREATEST(existing, day)
   │        - UPSERT INTO analytics_shop_timezones
   │ 8. write audit row (requestId, status, record counts, key_prefix)
   ▼
JSON response
   { data: { accepted: [{idempotencyKey, status: "inserted"|"duplicate"}],
             rejected:  [{idempotencyKey, code, message, retryable}] } }
```

Both `accepted[].inserted` and `accepted[].duplicate` are **successes**
from the plugin's perspective. The plugin may mark the local record as
`synced` on either.

---

## 4. Key assumptions & ambiguity resolutions

The protocol spec leaves several questions open. Each was resolved to a
specific behavior; this section lists them so future maintainers can
revisit if the protocol clarifies.

| # | Ambiguity | Resolution | Rationale |
|---|-----------|------------|-----------|
| 1 | What value does `nextRequiredDay` take on first sync (no records)? | `today_in_shop_tz − ANALYTICS_SYNC_BOOTSTRAP_LOOKBACK_DAYS` (default 30) | Matches the protocol's "the server must still return a bootstrap date" without inventing arbitrary history. |
| 2 | What is a token's scope? | Empty scopes[] = unrestricted (operator default). `*` = wildcard. Otherwise each entry is `seller:<id>` or `advertiser:<id>` and is a hard constraint on that dimension. | "Least-privilege" mandates per-token scopes; the protocol doesn't define the syntax so we pick the simplest. |
| 3 | Per-record partial-success limit on `response` payload size? | 256 KB per `response` JSON (MAX_RESPONSE_DATA_BYTES) | A chatty record shouldn't blow the table; 256 KB is ~1000× a typical TikTok analytics response. |
| 4 | What timestamp source for "today"? | Server-side, computed in `analytics_shop_timezones.timezone` (default `Asia/Shanghai`) | The plugin sends only `capturedAt` (per record), not a "sync day". Bootstrap must come from the server in the shop's canonical TZ. |
| 5 | How is the cursor `nextCursor` (pagination) implemented? | MVP: opaque base64 of `{page_size, offset}`. Real implementation should keyset-paginate on `(storage_key, campaign_id)`. | Protocol says "the server must return the same cursor for repeated reads until a successful upload changes the state." MVP doesn't optimize for new-campaign inserts during read. |
| 6 | Rate limit value? | `ANALYTICS_SYNC_RATE_LIMIT_PER_MIN` (default 100), per token prefix, sliding 60 s window | Spec only mandates "include Retry-After"; default chosen so a daily sync job (1-2 calls per minute) sits comfortably below. |
| 7 | Where does `nextRequiredDay` come from when `latestCompletedDay` is set but in the past? | `max(latestCompletedDay + 1, today - bootstrap_lookback)` | Prevents a token that was offline for months from being told to skip months of days, but also prevents a token from re-uploading ancient history once the bootstrap window closes. |
| 8 | Token revocation propagation? | 60 s in-process cache; revocation is checked on miss | Same model as tts-erp's `api_keys` cache; trade-off is up-to-60-s delay for revocation vs. DB load on every request. |
| 9 | Should `IdempotencyKeyMismatch` block the entire batch or just the bad record? | Just the bad record (rejected[] entry, others still process) | Per-record outcome keeps a single client bug from blocking a whole sync window. |
| 10 | What happens if `analytics_records` INSERT succeeds but cursor UPSERT fails? | Both in the same transaction → both roll back → entire batch returns 500; client retries (records are idempotent) | Atomicity per protocol §7 requirement 4. |
| 11 | Where does the per-shop timezone come from? | Last-write-wins on `analytics_shop_timezones.timezone` when the plugin uploads a batch. Seed: `Asia/Shanghai` on first sight. | The plugin doesn't carry a TZ per request; the server learns from past batches. Operators can override via direct SQL or via a future PATCH endpoint. |
| 12 | Should the cursor endpoint advance the timezone row? | No — only batch uploads do. A cursor read with no prior uploads returns the seeded default. | Cursor reads are pure; they don't change state. |
| 13 | What's the max pageSize for cursor? | 100 (protocol says ≤ 100) | Matches batch max — keeps server query plans bounded. |
| 14 | How is `analytics_audit_log` retained? | 30-day operator cron (see retention section). | Audit is for ops; raw analytics has its own retention. |

---

## 5. Why "atomic upsert" beats "select + update"

A naive implementation would:

```sql
SELECT latest_completed_day FROM analytics_cursors WHERE ...;
-- if new_day > latest, UPDATE ...
```

That's racy under concurrent inserts. The protocol §7 requirement 4
("an atomic upsert that only advances latestCompletedDay") rules it
out. Our implementation uses a single statement:

```sql
INSERT INTO analytics_cursors (...)
VALUES (...)
ON CONFLICT (seller_id, advertiser_id, storage_key, campaign_id)
DO UPDATE SET
  latest_completed_day = GREATEST(analytics_cursors.latest_completed_day,
                                  EXCLUDED.latest_completed_day),
  last_updated_at = now(),
  request_id = EXCLUDED.request_id;
```

`GREATEST` makes the cursor strictly monotonic; duplicates never
regress. The whole batch (records + cursor) commits or rolls back
together.

---

## 6. Why server-computes the idempotency key

The protocol says the dedup key is `sha256(canonical_json(...))`. The
plugin also sends `idempotencyKey` in the request. We compute it
server-side and verify the client's value matches:

```python
canonical = compute_idempotency_key(sellerId, advertiserId, storageKey,
                                    campaignId, day, page)
if record.idempotencyKey != canonical:
    rejected.append({"code": "SCHEMA_INVALID", ...})
```

Why not trust the client-sent value?

- A buggy plugin might compute the canonical json with wrong field order
  or whitespace — we want the server's dedup to be authoritative.
- The unique index on `analytics_records.idempotency_key` is the
  ultimate dedup; trusting the client would let a buggy plugin bypass it.
- It catches the "client thinks the day is 2026-08-23 but server
  interprets it differently" class of bugs at the boundary.

Cost is a sha256 per record (≈ 1 µs); negligible.

---

## 7. Why per-token buckets (not per-IP)

The plugin is one process; per-IP rate-limiting would punish every shop
that shares a NAT. Bucketing by `sync_token_prefix` means:

- One noisy token doesn't starve siblings.
- Operators can issue multiple tokens for isolation.
- Anonymous traffic (no token) gets an IP bucket so 401-flooding is
  still bounded.

This is the same trade-off tts-erp makes for its `api_keys`.

---

## 8. What's NOT in scope (deferred)

- **Open-source client SDKs.** The plugin is the only known client; it
  ships TypeScript glue in its own repo.
- **Cursor keyset pagination.** MVP uses offset-style opaque cursors.
  Fine while total rows per scope is < 10k; revisit when it isn't.
- **Token rotation grace period.** Rotate immediately revokes the old
  plaintext. If the plugin is mid-upload, retry with the new plaintext
  (records are idempotent).
- **Multi-region replication.** Single Postgres instance. Replicate
  with the existing infra tooling (not analytics_sync's concern).
- **Webhook for plugin-side cursor revert.** Plugin polls.

---

## 9. Operational concerns

- **Time skew**: the cursor uses server-side `now()` for bootstrap math.
  Client clocks can be minutes off; we don't trust them.
- **Postgres connection storms**: each `upsert_records()` call opens a
  short-lived connection. Under heavy parallel uploads this matters;
  if you see DB CPU climb, switch to a connection pool (psycopg_pool).
- **Mem usage of audit log**: append-only, never deleted by the service.
  30-day retention cron is the operator's responsibility.
- **JSONB growth**: `response_data` is unbounded up to 256 KB per
  record. Operators may want a TOAST compression strategy; the table
  already has `JSONB` (no compression on its own).
- **Crash-safety**: the cursor UPSERT is in the same transaction as the
  record INSERT. If the service crashes mid-batch, the whole batch
  rolls back. Client retry succeeds (records are idempotent).
