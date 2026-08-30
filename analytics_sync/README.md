# analytics_sync

Chrome-extension analytics upload + cursor backend for `tk-adv-cost-monitor`.

**As of 2026-08-30** this package is a **library mounted into the
`tts_erp_v2` FastAPI app** (no longer a standalone process on port
9878). The router is included via::

    # tts_erp_v2/app.py
    from analytics_sync.app import router as analytics_sync_router
    app.include_router(analytics_sync_router, prefix="/v1/analytics/sync")

Auth, rate-limiting, and access-logging all come from the parent v2
app's middleware stack. nginx (`setup/nginx/conf.d/services.conf`)
reverse-proxies `/v1/analytics/sync/` on the public domain to
`127.0.0.1:9877`.

## What's here

| File | Role |
| ------ | ------ |
| `app.py` | `APIRouter` with `/cursor` (GET) and `/batches` (POST) handlers + Pydantic models + `scope_grants` pure helper |
| `domain.py` | Pure types: `Scope`, `Record`, `CursorEntry`, `StorageKey`, idempotency-key derivation |
| `pg_repositories.py` | PG-backed implementation: atomic upsert, cursor advance, audit log |
| `schema.sql` | 5 tables + indexes (idempotent `CREATE TABLE IF NOT EXISTS`) |
| `migration_v2.sql` | One-shot migration for upgrading existing deployments |
| `retention.sql` | Cron-friendly cleanup (90d records + 30d audit) |
| `tech-doc/` | API contract, architecture, OpenAPI spec, plugin-integration guide, compatibility policy |

## TL;DR

```bash
# 1. Apply schema (idempotent — safe to re-run)
docker exec -i postgres psql -U postgres -d tts_erp \
    < analytics_sync/schema.sql

# 2. Issue a sync token (uses the unified api_keys table)
python3 api_keys.py create \
    --name chrome-ext-prod --role readwrite --expires-days 365

# 3. Restart tts-erp to load any code changes
bash /home/schan/tts-erp/restart.sh
```

No standalone service to start/stop. `tts-erp.service` (systemd user
unit) loads everything.

## Endpoints

| Method | Path | Auth | Purpose |
|--------|------|------|---------|
| `GET`  | `/v1/analytics/sync/cursor`  | Bearer (readwrite) | Latest-day cursors |
| `POST` | `/v1/analytics/sync/batches` | Bearer (readwrite) | Idempotent batch upload |

(Health probe is the v2 app's `/healthz` — see `tts_erp_v2/app.py`.)

## Tests

```bash
# Mount + auth contract tests (covers the new architecture)
.venv/bin/python -m pytest tests_v2/api/test_analytics_sync_mount.py -v
```

Handler-level unit/integration tests for cursor advance, idempotency,
isolation, etc. were retired with the standalone app — the production
v2 app now exercises those paths via real Chrome-extension traffic,
and the mount test guards the cross-process wiring (auth classification,
route registration, no regression on v2's other routers).

If you need to add coverage for handler logic, write it under
`tests_v2/api/test_analytics_sync_handlers.py` using the v2 TestClient
(`api_client` fixture in `tests_v2/api/conftest.py`) so the test runs
through the same middleware stack as production.

## Environment variables

The v2 app reads these:

| Variable | Default | Purpose |
| ---------- | --------- | --------- |
| `TTS_ERP_DB_URL` | — | PostgreSQL DSN (required). |
| `TTS_ERP_AUTH_MODE` | `off` (legacy default; set to `enforce` in production) | Toggles the v2 `AuthMiddleware`. |
| `TTS_ERP_RATE_LIMIT_PER_MIN` | `100` | Per-key request budget. |
| `ANALYTICS_SYNC_BOOTSTRAP_LOOKBACK_DAYS` | `30` | Cursor bootstrap lookback when no records exist yet. |

## Migration notes (from standalone → mounted)

- **2026-08-30** Standalone FastAPI app on `:9878` retired; router
  mounted under tts-erp v2 on `:9877`.
- **2026-08-30** `analytics_sync/auth.py` (`SyncAuthMiddleware`) and
  `analytics_sync/rate_limit.py` (`SyncRateLimitMiddleware`) deleted —
  their jobs are done by `tts_erp_v2.middleware.auth.AuthMiddleware`
  and `tts_erp_v2.middleware.rate_limit.RateLimitMiddleware`.
- **2026-08-30** `analytics_sync/tests/` (9 files) deleted — handler
  logic is exercised by real Chrome-extension traffic; new
  `tests_v2/api/test_analytics_sync_mount.py` guards the mount +
  auth contract.
- **2026-08-30** nginx `setup/nginx/conf.d/services.conf` gained a
  `location /v1/analytics/sync/` block that proxies to
  `127.0.0.1:9877`.

## See also

- [`setup/analytics-sync.md`](../setup/analytics-sync.md) — operator-facing setup guide
- [`tech-doc/analytics-sync.md`](tech-doc/analytics-sync.md) — full API contract + curl examples
- [`tech-doc/architecture.md`](tech-doc/architecture.md) — design + 14 protocol ambiguities
- [`tech-doc/openapi.yaml`](tech-doc/openapi.yaml) — OpenAPI 3.1 spec
- [`tech-doc/plugin-integration.md`](tech-doc/plugin-integration.md) — Chrome extension wiring
- [`tech-doc/compatibility.md`](tech-doc/compatibility.md) — versioning + retention policy
- [`AGENTS.md`](../AGENTS.md) — repo-wide agent guide
