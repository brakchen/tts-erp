# analytics_sync

Standalone FastAPI backend that replaces the retired CloudBase analytics
upload path used by the `tk-adv-cost-monitor` Chrome extension. Receives
batched analytics records (product analyses, session analyses, campaign
change logs) over HTTPS, deduplicates them on a stable idempotency key,
and tracks the per-scope latest-day cursor for daily job scheduling.

## TL;DR

```bash
# 1. Apply schema
docker exec -i postgres psql -U postgres -d tts_erp < analytics_sync/schema.sql

# 2. Issue a sync token (plaintext shown ONCE)
python3 analytics_sync/analytics_sync_tokens.py create --name chrome-ext-prod

# 3. Run the service
TTS_ERP_DB_URL=postgresql://... \
ANALYTICS_SYNC_AUTH_MODE=enforce \
uvicorn analytics_sync.app:app --host 0.0.0.0 --port 9878
```

## Endpoints

| Method | Path | Auth | Purpose |
|--------|------|------|---------|
| `GET`  | `/healthz`                                  | none | Liveness check |
| `GET`  | `/endpoints`                                | none | API surface |
| `GET`  | `/v1/analytics/sync/cursor`                 | Bearer | Latest-day cursors |
| `POST` | `/v1/analytics/sync/batches`                | Bearer | Idempotent batch upload |

## Layout

```
analytics_sync/
├── README.md                          # this file
├── schema.sql                         # 4 tables + indexes (idempotent)
├── app.py                             # FastAPI application
├── auth.py                            # sync-token middleware (Bearer)
├── domain.py                          # pure types: Scope, Record, CursorEntry, …
├── pg_repositories.py                 # PG-backed implementation
├── analytics_sync_tokens.py           # operator CLI: create/list/revoke/rotate
├── conftest.py                        # pytest fixtures (db_url, sync_token, fastapi_client)
├── pytest.ini
├── tech-doc/
│   └── analytics-sync.md             # OpenAPI-ish spec, curl examples, deploy
└── tests/
    ├── test_auth.py                   # Bearer auth, X-Sync-Token, exempt paths
    ├── test_batches.py                # happy path, dup, partial success, errors
    ├── test_cursor.py                 # cursor endpoint + bootstrap date
    └── test_idempotency.py            # canonical key derivation
```

## Tests

```bash
cd analytics_sync
TTS_ERP_DB_URL=postgresql://postgres:...@127.0.0.1:5432/tts_erp \
  pytest -v
```

35 tests covering: canonical-key derivation, idempotency, duplicate
handling, partial success, schema errors, cursor advance, monotonicity,
timezone bootstrap, auth (missing/invalid/disabled/valid/exempt paths).

## Environment variables

| Variable | Default | Purpose |
|----------|---------|---------|
| `TTS_ERP_DB_URL` (or `ANALYTICS_SYNC_DB_URL`) | — | PostgreSQL DSN. Required. |
| `ANALYTICS_SYNC_AUTH_MODE` | `enforce` | `off` / `shadow` / `enforce`. |
| `ANALYTICS_SYNC_BOOTSTRAP_LOOKBACK_DAYS` | `30` | Days of history to fetch on first sync. |

## See also

- [`tech-doc/analytics-sync.md`](tech-doc/analytics-sync.md) — full spec,
  curl examples, error contract, deployment guide.
- Upper-level project: [`AGENTS.md`](../AGENTS.md) and [`handoff.md`](../handoff.md).
