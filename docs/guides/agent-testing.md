# Agent testing guide

This document defines the only supported test workflow for coding agents in `tts-erp`.

## 1. Database isolation

- Default agent test entry point: `scripts/test_isolated.sh`. **It is the only supported entry point.**
- Template database: `tts_erp_test_template` (schema-only, maintained by `scripts/test_isolated.sh --refresh-template`).
- Per-run database: an ephemeral `tts_erp_test_*` clone created from the template and dropped after the command.
- The long-lived shared database is not an agent fallback; repair isolated-runner prerequisites instead.
- Production database: `tts_erp` or another production-shaped name recognized by `tts_erp_v2.api.deps.is_prod_shaped_db()`.
- `.env.test` supplies the base `TTS_ERP_DB_URL_TEST`; `scripts/test_isolated.sh` rewrites only the database name for template/ephemeral clones.
- The isolated wrapper loads `.env.test` and rewrites `TTS_ERP_DB_URL_TEST` to the ephemeral clone.
- `tests/conftest.py` hard-exits with status 2 if a direct pytest invocation would target a production-shaped database.
- Agents must never set `TTS_ERP_TEST_OFF=1`.

The hard exit is the final safety net, not the normal workflow. Always use `scripts/test_isolated.sh`.

Test prerequisites (PostgreSQL client tools, pytest, `.env.test`) are checked and
installed by `bash scripts/envsetup/install-test-deps.sh` — run `--check` first, then
`sudo bash scripts/envsetup/install-test-deps.sh` for the root-owned part.

## 2. Supported commands

| Scope | Command |
| --- | --- |
| Fast suite | `bash scripts/test_isolated.sh fast` |
| Unit-layer tests | `bash scripts/test_isolated.sh unit` |
| Business domain | `bash scripts/test_isolated.sh <domain>` |
| Specific file within fast selection | `bash scripts/test_isolated.sh fast tests/path/test_file.py` |
| Specific test within a domain | `bash scripts/test_isolated.sh <domain> tests/path/test_file.py::test_name` |
| Refresh template then run fast suite | `bash scripts/test_isolated.sh --refresh-template fast` |
| Check / install test prerequisites | `bash scripts/envsetup/install-test-deps.sh --check` · `sudo bash scripts/envsetup/install-test-deps.sh` |

Domain names may be passed with or without the `domain_` prefix.

Agents must not invoke the underlying test framework or low-level wrapper directly, and must not select `all`, `coverage`, or archived migration domains. Those paths can include production-touching historical behavior.

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

Creating the worktree `.venv` link is required for the isolated runner, pi-lens, and other tooling discovery.

## 4. Template and shared database usage

`bash scripts/test_isolated.sh ...` is parallel-safe for ordinary agent work: it serializes template refresh/clone through `/tmp/tts-erp-test-template.lock`, clones `tts_erp_test_template` to a unique `tts_erp_test_*` database, points the test process at that clone, and drops the clone on exit. Concurrent isolated runs do not delete each other's `TEST_` rows.

Refresh the template when schema/migration state changes or if a run reports missing tables:

```bash
bash scripts/test_isolated.sh --refresh-template fast
```

The refresh path rebuilds only the test-shaped template DB. It imports production schema read-only through `scripts/import_prod_to_test.sh --schema-only`, stamps the production alembic revision, then upgrades the template to the current worktree's alembic head. If the production alembic revision is not present in the worktree, the script leaves the imported schema in place and prints a warning instead of guessing. It must not be pointed at a production-shaped target DB.

### No shared-database fallback

If template cloning or PostgreSQL client tooling is unavailable, stop and repair prerequisites with `bash scripts/envsetup/install-test-deps.sh --check` followed by the documented installer. Agents must not substitute a shared database, a direct framework invocation, or an archived migration selection.

## 5. Selecting validation scope

- Documentation-only change: validate links, referenced paths, and Markdown structure; application tests are not normally required.
- Single-domain code change: run that domain first, then the fast suite before merge.
- Shared fixture, middleware, database model, schema, scheduler, or cross-domain change: run the narrow tests first and then the fast suite.
- Migration change: validate with `scripts/test_isolated.sh --refresh-template ...` or another test-shaped ephemeral DB; never run the production migration.
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
bash scripts/test_isolated.sh fast 2>&1 | grep '^FAILED' | sort > /tmp/fail-before.txt
# merge the lane
bash scripts/test_isolated.sh fast 2>&1 | grep '^FAILED' | sort > /tmp/fail-after.txt
diff -u /tmp/fail-before.txt /tmp/fail-after.txt
```

For every failure:

1. Rerun the smallest failing test selection once through `scripts/test_isolated.sh`.
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
- whether the isolated runner was used, or whether the shared DB lock was acquired;
- test database identity when relevant (template/ephemeral/shared);
- pass/fail count or the before/after failure diff;
- isolated rerun results for failures;
- tests intentionally not run and why.
