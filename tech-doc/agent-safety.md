# Agent safety rules

This document contains detailed safety procedures referenced by the root `AGENTS.md`. The root safety boundaries remain authoritative.

## 1. Database environments

| Environment | Database | Agent policy |
| --- | --- | --- |
| Production | `tts_erp`, `tts_erp_prod`, `tts_erp_prod_*` | Read-only unless a documented guarded operation is explicitly authorized by the user |
| Tests | `tts_erp_test_template`, ephemeral `tts_erp_test_*`, shared fallback `tts_erp_v3_test` | The only databases agents may use for tests and migration validation |

- `is_prod_shaped_db()` in `tts_erp_v2.api.deps` is the single source of truth for production-shaped database detection.
- An unset `TTS_ERP_DB_URL` is treated as production-shaped (fail closed).
- Tests should enter through `scripts/test_isolated.sh`, which clones a per-run test DB and delegates to `scripts/test.sh` with `TTS_ERP_DB_URL_TEST` set. Direct `scripts/test.sh` runs are the shared-DB fallback and must be serialized.
- `tests/conftest.py` hard-exits with status 2 when pytest would target a production-shaped database. This is not a warning-only check.
- `TTS_ERP_TEST_OFF=1` bypasses the test guard and risks live data. Agents must not set it.

## 2. Destructive operation guards

A destructive operation includes `DELETE`, `TRUNCATE`, `DROP`, an irreversible `UPDATE`, destructive schema migration, or a job that permanently replaces data.

### HTTP paths

Import and call:

```python
from tts_erp_v2.api.deps import require_destructive_guard

require_destructive_guard(request, op_name="descriptive-operation-name")
```

The guard rejects production-shaped databases unless `ALLOW_PROD_DESTRUCTIVE=1` is explicitly present in the human-operated environment.

### CLI scripts, migrations, and jobs

Import and call:

```python
from tts_erp_v2.api.deps import require_destructive_script_guard

require_destructive_script_guard(
    script_name="script-name",
    confirmation=confirmation,
    dangerous=dangerous,
)
```

- A dry run uses `dangerous=False` and may inspect a production-shaped database without mutating it.
- A real operation must require an explicit confirmation flag and the guard.
- Agent-authored destructive entry points without one of these guards are release-blocking findings.

Existing guarded paths include:

- `POST /v2/admin/purge-plugin-data`
- `DELETE /v2/intercept/configs/{id}`
- `POST /v2/intercept/configs/batch` with `action=delete`
- `DELETE /v2/spu_images/{id}`
- `scripts/oneoff_finance_reset.py`
- `scripts/oneoff_regen_finance_components.py`
- Alembic upgrade execution

`/v2/admin/purge-plugin-data` also requires:

1. `admin` role;
2. `?confirm=true` for execution (otherwise dry run);
3. production override via `ALLOW_PROD_PURGE=1`, or `?allow_prod=true` only when `TTS_ERP_ENVIRONMENT=dev`.

Agents must not enable these production override variables.

## 3. Migration policy

- Agents may create and validate migrations only against test-shaped databases: `tts_erp_test_template`, ephemeral `tts_erp_test_*`, or the shared fallback `tts_erp_v3_test`.
- Agents never run production `alembic upgrade head`.
- Production schema renames and data-moving migrations are manually coordinated with service restart.
- Do not restore or execute migration suites under `tech-doc/_archive/migrate-v1-to-v2-2026-08-29/`.
- Do not rebuild v1 `public.*` business tables.
- `public.fn_touch_updated_at()` supports updated-at triggers across the v2 database and must not be removed.

## 4. Credential policy

Credential management has one implementation:

```text
tts_erp_v2/proxy/token_service.py
```

Use its `encrypt`, `decrypt`, `load_credentials`, `upsert_credentials`, and `refresh_if_needed` behavior rather than reimplementing any part of the lifecycle.

Forbidden:

- querying the removed v1 `oauth_tokens` table;
- accessing the legacy oauth-receiver database;
- decrypting `integration.credentials` with Fernet directly;
- exposing `app_secret` in client-visible configuration or `.env` guidance;
- logging access tokens, refresh tokens, API keys, cookies, or decrypted credentials.

## 5. Authentication and API boundaries

- Production uses `TTS_ERP_AUTH_MODE=enforce`.
- Except for documented exemptions, endpoints accept `Authorization: Bearer <key>` or `X-API-Key: <key>`.
- Role ordering is `readonly < readwrite < admin`.
- The role matrix lives in `tech-doc/external-api.md` and `tts_erp_v2/middleware/auth.py::required_role()`.
- New endpoints must have an explicit role or an intentional documented exemption.

Do not add or reconnect endpoints that mutate the real TikTok Shop, including:

- `POST /returns`
- `POST /cancellations`
- `POST /orders/<id>/confirm`
- `POST /orders/<id>/cancel`
- `POST /orders/<id>/update_status`
- `POST /orders/<id>/shipping_info`
- `POST /orders/<id>/verify_shipping`

## 6. Middleware and process boundaries

Do not change middleware registration order in `tts_erp_v2/app.py`.

Registration order:

```text
RateLimit → Auth → CORS → AccessLog
```

Effective request order:

```text
AccessLog → CORS → Auth → RateLimit
```

Auth must run before rate limiting can bucket requests by API key.

Changes under `jobs/` or `tts_erp_v2/sync_worker/` require a separate sync-worker restart:

```bash
systemctl --user restart tts-erp-sync.service
```

## 7. Production-adjacent review checklist

Before completing a production-adjacent change, verify:

- The test command used `.env.test` and `tts_erp_v3_test`.
- No production override variable was set.
- Every destructive path calls the correct shared guard before issuing SQL.
- Dry run and real execution are distinguishable.
- Real execution requires explicit confirmation.
- Credentials flow through `token_service.py`.
- Endpoint auth role and exemption behavior match `tech-doc/external-api.md`.
- No production migration, purge, restart, or store-writing request was executed by the agent.
