> ⚠️ **superseded（2026-09-02, dump architecture）**：本文档描述 cursor
> work-list + `/v2/analytics/sync/batches` 批量协议（page/expectedPageCount /
> ad_daily_pages / ad_cursors 时代）。当前协议是 **单 dump**：`POST /dumps`
> （单 object）+ `GET /cursor` has-data 预检；`ad_raw` source-of-truth，
> 派生表 ad_records / ad_daily_completeness。以
> [dump-architecture.md](./dump-architecture.md) 为准（★ 当前事实源）。
> 本文件仅作历史/歧义记录保留。

# Compatibility & retention

Two operational concerns that aren't covered by the API spec but matter
for production deployments: protocol-versioning policy, and data
retention (raw response JSON + audit log).

---

## 1. Protocol versioning policy

`analytics_sync` speaks one `protocolVersion` at a time. The version
is carried in:

- `POST /v2/analytics/sync/batches` request body (`protocolVersion`)
- `X-Protocol-Version` request header

### Current

| Version | Status | Sunset |
|---------|--------|--------|
| 1       | Supported (legacy) | — (no date; re-evaluate once all extension builds are on v2) |
| 2       | Active | — |

### v1 ↔ v2 policy

- **v1 records** omit `expectedPageCount`; the server treats each as an
  implicit single-page day (`expectedPageCount=1`). First insert of a
  v1 record completes its day — exactly the pre-v2 behavior.
- **v2 records** must carry `expectedPageCount` ≥ 1 with
  `1 <= page <= expectedPageCount`. A day is complete only once pages
  `1..expectedPageCount` are all durably stored; the cursor advances to
  the last day of the contiguous complete prefix only.
- **Mixed-version day**: a record whose effective `expectedPageCount`
  disagrees with the value already stored for that daily unit is
  rejected with `PAGE_COUNT_CONFLICT` (`retryable=false`). In
  particular, a v1 record can never "complete" a day that v2 declared
  as multi-page.
- **When can a client switch to v2?** As soon as it knows the real
  TikTok page count per day (from response pagination metadata) and
  can populate `expectedPageCount` consistently per daily unit. No
  server-side flag day or coordinated rollout is required; both
  versions are accepted concurrently on the same deployment.

### Bumping protocolVersion

When introducing a breaking change (new required field, removed field,
semantic change), bump `protocolVersion` to `N+1`. The process:

1. **Server-side**
   - Bump `PROTOCOL_VERSION` constant in `tts_erp_v2/api/v2/analytics.py`.
   - Add a new branch in `BatchRequest` model for the new schema;
     keep the v1 model parseable.
   - Reject requests with `protocolVersion < N` or `> N+1` with
     `400 UNSUPPORTED_PROTOCOL_VERSION`.
   - Roll out behind a flag for canary testing.

2. **Client-side**
   - Bump `X-Protocol-Version` header.
   - Update body schema to match the new version.
   - Test against the staging server first.

3. **Sunset policy**
   - Old version (N) is supported for at least one release window
     after N+1 ships.
   - Server returns `400 UNSUPPORTED_PROTOCOL_VERSION` with a clear
     `message` ("server expects protocolVersion=N+1, client sent N")
     so the plugin can surface a useful diagnostic.

### Backward-compatible additions (no version bump)

These don't require a version bump:

- Adding a new optional field in the response.
- Adding a new optional query parameter on the cursor endpoint.
- Adding a new `storageKey` value to the allowlist.
- Adding a new HTTP header (the server may ignore unknown headers).

### Forward-incompatible changes (REQUIRE a version bump)

These DO require a version bump:

- Changing the `idempotencyKey` derivation (loads of existing data
  depend on the current canonical form).
- Changing the meaning of `latestCompletedDay` or `nextRequiredDay`.
- Removing a `storageKey` from the allowlist.
- Changing the `scope` semantics (e.g. switching from union to
  intersection).
- Changing the partial-success response shape (e.g. adding a required
  field to `accepted[]`).

### Why the idempotency key is sacred

The unique index on `analytics.ad_records.idempotency_key` is the only
dedup mechanism that survives concurrent uploads. If the canonical form
ever changes:

- All existing rows become effectively un-keyed.
- New uploads with old keys would either be re-inserted (duplicates
  not deduplicated) or be rejected (idempotency mismatch).

So any change to `compute_idempotency_key` MUST be a hard cut to a new
protocol version, AND must include a migration plan (e.g. dual-write
period with both old and new keys, then backfill, then drop old).

---

## 2. Retention policy

Since 2026-09-02 retention is automated: the sync-worker runs the daily
job **`analytics.retention`** (`tts_erp_v2/jobs/analytics_retention.py`,
86400s interval in `scheduler.py` JOBS). No operator cron is needed.

### `analytics.ad_records`

| Aspect | Value | Notes |
| ------ | ----- | ----- |
| Default retention | 90 days (`TTS_ERP_ANALYTICS_RECORDS_RETENTION_DAYS`) | Deleted by the daily job, keyed on `received_at`. |
| Per-record size cap | 256 KB (`MAX_RESPONSE_DATA_BYTES`) | Records over cap are rejected at upload time. |
| Compression | none | `JSONB` doesn't compress automatically. Operators may add TOAST settings. |
| Partitioning | none | Single table; `idx_analytics_records_received` supports `received_at DESC` queries. |

The job deletes by `received_at` (when the row was ingested), not by
`captured_at` (when the plugin saw the TikTok response). If you need
"delete the data N days after the source event" semantics, adjust the
job's WHERE clause.

### `analytics.ad_cursors`

| Aspect | Value |
| ------ | ----- |
| Retention | forever |
| Reason | Cursor is the source of truth for "where to resume" |

`analytics.ad_cursors` rows are tiny (4-5 columns) and never get large.
Don't prune them.

### `analytics.ad_shop_timezones`

Forever. Same reason as cursors — these are operator-set or
plugin-learned state.

### `api_keys`（`security.api_keys`）

Forever (revoked or not). Revocation is `enabled = false`. Operators
can hard-delete with:

```sql
DELETE FROM security.api_keys
WHERE enabled = false
  AND created_at < now() - INTERVAL '180 days';
```

### `analytics.ad_audit_log`

| Aspect | Value |
| ------ | ----- |
| Default retention | 30 days (`TTS_ERP_ANALYTICS_AUDIT_RETENTION_DAYS`) |
| Reason | Used for ops debugging; volume grows linearly with traffic |

Deleted by the same daily `analytics.retention` job — no cron needed.

For high-traffic deployments, consider partitioning by month:

```sql
CREATE TABLE ad_audit_log_2026_08 PARTITION OF analytics.ad_audit_log
    FOR VALUES FROM ('2026-08-01') TO ('2026-09-01');
```

…then `DETACH` + `DROP` old partitions instead of `DELETE` (faster).

---

## 3. Secret handling

The service MUST NEVER log, audit, or echo:

- The plaintext Bearer token (only the 16-char prefix).
- TikTok Cookies, Feishu webhook tokens, browser Authorization headers.
- Full request headers (only the X-Request-Id for tracing).

`analytics.ad_audit_log.error_code` MAY include the exception class name
(e.g. `RuntimeError`) for ops debugging. The class name is not
sensitive; full exception messages are NOT stored.

`stderr` writes from the service follow the same rules. The
`write_audit()` helper sanitizes by construction (it takes only the
prefix, not the token).

---

## 4. Future protocol versions (sketch)

Anticipated breaking changes that would warrant a v2:

- **Stronger scope syntax** — currently `seller:<id>` / `advertiser:<id>` /
  `*`. A v2 might allow glob patterns (`seller:shop-*`), time-bounded
  scopes, or hierarchical scopes. The DB schema can already hold the
  extra info; the parsing logic would change.
- **Cursor as opaque base64 keyset** — for very large
  `(scope, storageKey, campaignId)` counts.
- **Server-side dedup window** — currently the unique index is forever.
  A v2 might add a TTL so old records can be re-uploaded (unlikely; this
  breaks the "data is durable" promise).
- **Pagination `cursor` semantics** — currently offset-style; v2 might
  switch to keyset on `(storageKey, campaignId, day)`.

None of these are scheduled. v1 is the production contract.
