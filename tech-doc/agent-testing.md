# Agent testing guide

This document defines the only supported test workflow for coding agents in `tts-erp`.

## 1. Database isolation

- Test database: `tts_erp_v3_test`.
- Production database: `tts_erp` or another production-shaped name recognized by `tts_erp_v2.api.deps.is_prod_shaped_db()`.
- `.env.test` supplies `TTS_ERP_DB_URL_TEST`.
- `scripts/test.sh` loads `.env.test` before invoking pytest.
- `tests/conftest.py` prefers `TTS_ERP_DB_URL_TEST` and hard-exits with status 2 if a direct pytest invocation would target a production-shaped database.
- Agents must never set `TTS_ERP_TEST_OFF=1`.

The hard exit is the final safety net, not the normal workflow. Always use the wrapper.

## 2. Supported commands

| Scope | Command |
| --- | --- |
| Fast suite | `bash scripts/test.sh fast` |
| Unit-layer tests | `bash scripts/test.sh unit` |
| Business domain | `bash scripts/test.sh <domain>` |
| Specific file within fast selection | `bash scripts/test.sh fast tests/path/test_file.py` |
| Specific test within a domain | `bash scripts/test.sh <domain> tests/path/test_file.py::test_name` |

Domain names may be passed with or without the `domain_` prefix.

Do not run:

```bash
.venv/bin/pytest ...
/home/schan/tts-erp/.venv/bin/pytest ...
bash scripts/test.sh all
bash scripts/test.sh coverage
```

`all` and `coverage` deliberately include `domain_migration`, which contains production-touching historical behavior. They are human-only commands.

## 3. Worktree prerequisites

A worktree needs access to the shared virtual environment and test configuration:

```bash
ln -s ../../.venv .venv
ln -s ../../.env.test .env.test
```

Do not link the main repository's writable `.env` into a worktree by default. If non-test code requires settings that are not in `.env.test`, create an independent local copy and make it read-only:

```bash
cp ../../.env .env
chmod u-w .env
```

Rules:

- Never edit a worktree `.env` symlink that points to the main repository.
- Never commit `.env`, `.env.test`, credentials, or copied secrets.
- Recreate the private copy instead of changing the main repository's `.env` for debugging.
- Temporary overrides belong in the process environment or a gitignored worktree-local file, and must be removed after use.

`scripts/test.sh` falls back to `/home/schan/tts-erp/.venv/bin/pytest` if the worktree virtual-environment link is missing, but creating the link is still required for pi-lens and other tooling discovery.

## 4. Shared database serialization

Tests share one development test database. Concurrent suites can delete each other's `TEST_` rows and produce false 401 or missing-row failures.

Preferred command:

```bash
flock -n /tmp/tts-erp-test.lock bash scripts/test.sh fast
```

If the lock is already held, do not run a competing suite. Either wait for the existing run or use a separately provisioned ephemeral test database.

Do not poll with an unbounded loop. If waiting is necessary, use a bounded timeout and report when the lock cannot be acquired.

## 5. Selecting validation scope

- Documentation-only change: validate links, referenced paths, and Markdown structure; application tests are not normally required.
- Single-domain code change: run that domain first, then the fast suite before merge.
- Shared fixture, middleware, database model, schema, scheduler, or cross-domain change: run the narrow tests first and then the fast suite.
- Migration change: validate only against `tts_erp_v3_test`; never run the production migration.
- Service-dependent tests must use the documented service setup and remain bounded by a timeout.

## 6. Failure classification

The default completion criterion is zero failures.

When master has an explicitly recorded stable failure baseline:

1. Capture the baseline before merging the lane.
2. Run the same command after merging.
3. Compare normalized failure identifiers.
4. The lane may introduce zero new stable failures.

Suggested capture:

```bash
bash scripts/test.sh fast 2>&1 | grep '^FAILED' | sort > /tmp/fail-before.txt
# merge the lane
bash scripts/test.sh fast 2>&1 | grep '^FAILED' | sort > /tmp/fail-after.txt
diff -u /tmp/fail-before.txt /tmp/fail-after.txt
```

For every failure:

1. Rerun the smallest failing test selection once through `scripts/test.sh`.
2. If the isolated rerun passes, record it as a likely flake.
3. If it fails consistently, treat it as a real failure and fix it before completion.
4. Do not classify an unrerun failure as pre-existing or flaky.

A documentation/config-only lane may compare validation appropriate to its changed files, but must not claim the application test suite passed unless it was actually run.

## 7. Test data rules

- Prefix generated test data with `TEST_`.
- Fixtures clean only their own test-owned rows.
- Tests must not assume production-shaped tables are empty.
- Tests must not delete or mutate non-test rows.
- Do not rely on execution order or data left by another test.
- Prefer unique identifiers per test when a global cleanup would risk concurrent lanes.

## 8. Completion evidence

Record:

- exact commands run;
- whether the shared DB lock was acquired;
- test database identity when relevant;
- pass/fail count or the before/after failure diff;
- isolated rerun results for failures;
- tests intentionally not run and why.
