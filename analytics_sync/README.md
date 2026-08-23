# analytics_sync

Standalone FastAPI backend that replaces the retired CloudBase analytics
upload path used by the `tk-adv-cost-monitor` Chrome extension.
Receives batched analytics records (product analyses, session analyses,
campaign change logs), deduplicates them on a stable idempotency key,
and tracks the per-scope latest-day cursor for daily-job scheduling.

## TL;DR

```bash
# 1. Apply schema (idempotent)
docker exec -i postgres psql -U postgres -d tts_erp \
    < analytics_sync/schema.sql

# 2. Issue a sync token (plaintext shown ONCE)
# Uses tts-erp's unified api_keys table; --scopes is optional per-seller restriction.
python3 api_keys.py create \
    --name chrome-ext-prod --role readwrite --expires-days 365

# 3. Run the service
TTS_ERP_DB_URL=postgresql://postgres:...@127.0.0.1:5432/tts_erp \
ANALYTICS_SYNC_AUTH_MODE=enforce \
/home/schan/tts-erp/.venv/bin/python -m uvicorn \
    analytics_sync.app:app --host 0.0.0.0 --port 9878
```

## Endpoints

| Method | Path | Auth | Purpose |
|--------|------|------|---------|
| `GET`  | `/healthz`                          | none | Liveness check |
| `GET`  | `/endpoints`                        | none | API surface |
| `GET`  | `/v1/analytics/sync/cursor`         | Bearer + scope | Latest-day cursors |
| `POST` | `/v1/analytics/sync/batches`        | Bearer + scope | Idempotent batch upload |

## Layout

```
analytics_sync/
├── README.md                          # this file
├── schema.sql                         # 5 tables + indexes (idempotent)
├── retention.sql                      # cron-friendly cleanup queries
├── app.py                             # FastAPI application
├── auth.py                            # sync-token + scope-validation middleware
├── rate_limit.py                      # per-token sliding-window rate limiter
├── domain.py                          # pure types: Scope, Record, CursorEntry, …
├── pg_repositories.py                 # PG-backed implementation
├── analytics_sync_tokens.py           # (removed 2026-08-23 refactor; use api_keys.py)
├── conftest.py                        # pytest fixtures
├── pytest.ini
├── tech-doc/
│   ├── analytics-sync.md             # API contract + curl examples
│   ├── architecture.md               # design + assumptions + ambiguities
│   ├── openapi.yaml                  # OpenAPI 3.1 spec
│   ├── plugin-integration.md         # extension wiring guide
│   └── compatibility.md              # protocol versioning + retention
└── tests/
    ├── test_auth.py                   # Bearer auth, scope, exempt paths
    ├── test_batches.py                # happy path, dup, partial success, errors
    ├── test_concurrency.py            # concurrent duplicate, distinct inserts
    ├── test_cursor.py                 # cursor endpoint + bootstrap date
    ├── test_errors.py                 # 413/5xx/audit
    ├── test_idempotency.py            # canonical key derivation
    ├── test_isolation.py              # cross-shop isolation
    ├── test_rate_limit.py             # 429 with Retry-After
    └── test_scope.py                  # scope validation (pure + e2e)
```

## Tests

```bash
cd analytics_sync
TTS_ERP_DB_URL=postgresql://postgres:...@127.0.0.1:5432/tts_erp \
  pytest -v
```

**63 tests** covering (per protocol §8):

| Category | Coverage |
|----------|----------|
| First write | ✅ test_first_write_inserts_and_advances_cursor |
| Duplicate write | ✅ test_duplicate_write_returns_duplicate_status |
| **Concurrent duplicate** | ✅ test_concurrent_duplicate_writes_have_exactly_one_insert |
| Cross-shop isolation | ✅ test_cursor_isolated_by_seller + 2 more |
| Cursor initial value | ✅ test_cursor_returns_empty_list_when_no_records + latest |
| Cursor advance | ✅ test_cursor_advances_to_max_day + no_regress |
| Partial success | ✅ test_partial_success_mixed_valid_and_invalid |
| **429** | ✅ test_over_limit_returns_429_with_retry_after + 4 more |
| **5xx** | ✅ test_5xx_audit_log_on_unhandled_exception + audit_code |
| 413 | ✅ test_413_for_oversized_body + content-length + audit |
| Illegal data | ✅ bad storageKey / page=0 / missing fields / wrong key |
| Auth failure | ✅ missing / invalid / disabled tokens; X-Sync-Token header |
| **Scope validation** | ✅ test_restricted_token_cannot_access_other_seller + 11 more |
| Canonical key derivation | ✅ 10 unit tests (sorted keys, trim, date, …) |

## Environment variables

| Variable | Default | Required | Purpose |
|----------|---------|----------|---------|
| `TTS_ERP_DB_URL` (or `ANALYTICS_SYNC_DB_URL`) | — | yes | PostgreSQL DSN. |
| `ANALYTICS_SYNC_AUTH_MODE` | `enforce` | no | `off` / `shadow` / `enforce`. |
| `ANALYTICS_SYNC_BOOTSTRAP_LOOKBACK_DAYS` | `30` | no | Cursor bootstrap lookback. |
| `ANALYTICS_SYNC_RATE_LIMIT_PER_MIN` | `100` | no | Per-token rate limit. |

## See also

- [`tech-doc/analytics-sync.md`](tech-doc/analytics-sync.md) — full API contract
- [`tech-doc/architecture.md`](tech-doc/architecture.md) — design + ambiguity resolutions
- [`tech-doc/openapi.yaml`](tech-doc/openapi.yaml) — OpenAPI 3.1 spec
- [`tech-doc/plugin-integration.md`](tech-doc/plugin-integration.md) — extension wiring
- [`tech-doc/compatibility.md`](tech-doc/compatibility.md) — versioning + retention
- Upper-level project: [`AGENTS.md`](../AGENTS.md) and [`handoff.md`](../handoff.md)
