# AGENTS.md — tts-erp

> Repository-wide instructions for coding agents. Read this file before changing anything.
> Detailed procedures live in `tech-doc/`; this file keeps only rules that apply to most tasks.

## 1. Instruction scope and precedence

1. Explicit user instructions override repository instructions.
2. Rules in a more specific instruction file override this root file for that subtree.
3. Safety rules in this file always apply unless the user explicitly authorizes a documented exception.
4. If two repository instructions conflict, stop and ask instead of choosing the less restrictive rule.
5. Before working on a specialized area, read the matching document in §8.

## 2. Project overview

- Stack: Python 3.14, FastAPI/uvicorn, SQLAlchemy 2, psycopg3, PostgreSQL, APScheduler, MinIO, Fernet, systemd user units.
- Purpose: ingest TikTok Shop and Miaoshou data into a local analytics database, expose read-oriented APIs, and run scheduled synchronization.
- Main code: `tts_erp_v2/`.
- Tests: `tests/`.
- Operational scripts: `scripts/`.
- Architecture and active contracts: `tech-doc/`.
- Work ownership registry: `handoff/ACTIVE.md`.

## 3. Non-negotiable safety boundaries

- Never run tests against `tts_erp`, `tts_erp_prod`, or any prod-shaped database name.
- Run tests only through `bash scripts/test.sh ...`; do not invoke pytest directly.
- Agents must not run `bash scripts/test.sh all`, `bash scripts/test.sh coverage`, or migration suites archived under `tech-doc/_archive/migrate-v1-to-v2-2026-08-29/`. These paths include or restore production-touching migration behavior.
- Never execute `DELETE`, `TRUNCATE`, `DROP`, or irreversible `UPDATE` against production data without the documented guard and explicit human authorization.
- Never run `alembic upgrade` against production. Agents may validate migrations only against `tts_erp_v3_test`; production migration and restart are human-operated.
- Do not add a destructive HTTP, CLI, migration, or job path without the shared guard from `tts_erp_v2.api.deps`.
- Credentials must go through `tts_erp_v2.proxy.token_service`; never query legacy `oauth_tokens` or decrypt `integration.credentials` directly.
- Do not reintroduce v1 `public.*` business tables or remove `public.fn_touch_updated_at()`.
- Do not add store-writing TikTok endpoints. This repository is a read-oriented analytics system.
- Do not change middleware registration order in `tts_erp_v2/app.py`.
- Never use repository-wide destructive Git commands such as `git reset --hard`, `git checkout -- .`, or `git clean -f`.

Read `tech-doc/agent-safety.md` before any database, credential, migration, destructive, authentication, or production-adjacent change.

## 4. Domain invariants

### Credentials

```python
from tts_erp_v2.proxy.token_service import load_credentials

cred = load_credentials(session, provider="tiktok", external_account_id=shop_id)
```

### TikTok signing

- Signing implementation: `tts_erp_v2/proxy/tts_shop/signing.py`.
- `shop_cipher` stays in the query string.
- Sort signing keys alphabetically.
- Sign the raw `json.dumps(..., ensure_ascii=False)` body; never URL-encode the body.
- Read `tech-doc/tiktok-hmac-signing.md` before changing signing code.

### HTTP success semantics

- `2xx` means parsing and contracted persistence/processing completed successfully.
- Null response bodies, parser failures, and missing required response structures must not return `200`.
- HTTP status is the success/failure signal; do not restore `rowsWritten`, `logId`, or `data.status` as parallel status channels.
- Wire JSON/TypeScript uses `requestId`; Python and database code use `request_id`; headers use `x-request-id`.
- Read `tech-doc/dumps-data-contract.md` before changing dumps endpoints or response envelopes.

### Authentication and time

- Production auth mode is `enforce`; roles are `readonly < readwrite < admin`.
- Store timestamps as aware UTC using `datetime.now(UTC)`, never `datetime.utcnow()`.
- Reporting dates are grouped by the shop's local timezone, not the UTC calendar day. Bridge nook currently uses Vietnam time (UTC+7).

## 5. Canonical commands

| Task | Command |
| --- | --- |
| Fast test suite | `bash scripts/test.sh fast` |
| One test domain | `bash scripts/test.sh <domain>` |
| Unit layer | `bash scripts/test.sh unit` |
| API restart | `bash restart.sh` |
| Sync-worker restart after `tts_erp_v2/jobs/` or `tts_erp_v2/sync_worker/` changes | `systemctl --user restart tts-erp-sync.service` |
| Service status | `systemctl --user status tts-erp{,-sync}.service` |
| API logs | `journalctl --user -u tts-erp -n 50` |

- `scripts/test.sh` sources `.env.test` and targets the dedicated `tts_erp_v3_test` database.
- Serialize shared-DB test runs with `flock -n /tmp/tts-erp-test.lock bash scripts/test.sh fast`.
- Full command reference: `tech-doc/commands-reference.md`.

## 6. Code conventions

- Use `from __future__ import annotations` and type hints in Python modules.
- Use SQLAlchemy 2 `select()`/`Session` patterns.
- Never run synchronous psycopg/database work inside an async handler.
- Prefix test data with `TEST_`.
- Put one-off scripts under `scripts/` with a descriptive `oneoff_`, `probe_`, `smoke_`, or `dump_` prefix.
- Use internal primary keys such as `shop_pk` and `spu_pk` for API filters; do not assume `shop_id` is accepted.
- Keep naming conventional by layer: JSON/TypeScript camelCase, Python/SQL snake_case, HTTP headers lowercase-with-hyphens.
- Do not restate formatter, linter, or type-checker rules here; follow the configured tools.

## 7. Worktree, review, and completion rules

- Every task starts in a dedicated branch/worktree; the master worktree is for registration, review, testing, and merge.
- Register file ownership in `handoff/ACTIVE.md` before the first task edit.
- Do not modify or stash another lane's work. If ownership is unclear, follow `tech-doc/agent-git-workflow.md`.
- Keep each writable worktree owned by one writer unless separate worktrees are used.
- Commit and push the task branch before merge or before pausing for user input. If the network is unavailable, commit locally and report that push remains pending.
- Merge into master with `--no-ff`, rerun the required checks on master, remove the worktree only after the branch is fully merged, then push master without force.
- Commit messages use `feat/fix/chore/docs/style/merge` plus a concise Chinese description.
- In a worktree, never use `git add -A`, `git add .`, or `-A`-style wildcards for staging. They sweep in the worktree's `.venv` symlink, `.env*`, and other gitignored-but-not-protected local files, and the resulting commit will silently wipe a teammate's real venv on merge checkout. Always stage with explicit file paths (e.g. `git add tts_erp_v2/.../spu-roi.js tests/...`). If you used `-A`, run `git status` before `git commit` and unstage anything that is not your own change.

Definition of done:

1. Run the narrowest relevant test command.
2. For code/test changes, run `bash scripts/test.sh fast` under the shared test lock.
3. The default requirement is zero failures. If master has an explicitly recorded stable baseline, the change must introduce zero new stable failures; isolate and rerun failures once to distinguish flakes.
4. Update contracts and operational documentation affected by the change.
5. Confirm no secrets, production data, unrelated WIP, or staged foreign files are included.
6. Confirm the worktree and master are clean after merge and the required branch/master pushes succeeded.

Detailed lifecycle, environment setup, conflict handling, and cleanup: `tech-doc/agent-git-workflow.md`.

## 8. Required context by task

| When touching | Read first |
| --- | --- |
| Architecture, credentials, database layout | `tech-doc/architecture-overview.md` |
| Destructive operations, migrations, production, auth | `tech-doc/agent-safety.md` |
| Tests, fixtures, test database, baseline failures | `tech-doc/agent-testing.md` |
| Worktrees, handoff, merge, push, conflict handling | `tech-doc/agent-git-workflow.md` |
| Dumps endpoints and HTTP envelopes | `tech-doc/dumps-data-contract.md` |
| TikTok signing | `tech-doc/tiktok-hmac-signing.md` |
| External endpoints, roles, pagination, schemas | `tech-doc/external-api.md` |
| Process/service architecture | `tech-doc/process-architecture.md` |
| Miaoshou integration | `tech-doc/miaoshou-platform.md` |
| Known recurring failures | `tech-doc/common-bugs.md` |

`tech-doc/_archive/` is historical reference only. Do not restore or execute archived code.
