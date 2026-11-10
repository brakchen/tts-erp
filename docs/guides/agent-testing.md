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
- `tests/conftest.py` hard-exits with status 2 before dotenv/schema probing when the current isolated-run proof is absent or inconsistent. A test-shaped URL alone is not authorization to use that database.
- Agents must never set `TTS_ERP_TEST_OFF=1`.

### 1.1 Cleaning up leftover ephemeral databases

The per-run clause above guarantees **row-level** isolation between concurrent runs. It does **not** protect against an
agent that drops whole databases by name pattern. On a shared test host, a bulk cleanup can therefore delete a
database another lane is actively running against.

Rules for any cleanup of leftover `tts_erp_test_*` databases:

1. **Only delete databases your own lane created.** A run's database name embeds its branch, session and pid
   (`tts_erp_test_<branch>_<session>_<timestamp>_<pid>`). Delete only names matching your own branch/session.
2. **Never delete by prefix alone.** `DELETE ... WHERE datname LIKE 'tts_erp_test_%'` hits other lanes' databases.
3. **Before any bulk cleanup, confirm no isolated run is active.** Check both `pgrep -f "pytest -q"` and
   `pgrep -f test_baseline` — a `test_isolated.sh` wrapper is not the only way a run can be active.
4. **Never delete the template family.** `tts_erp_test_template` and every `*_template_*` database are other
   lanes' baselines (`template_ads_ack`, `template_price_data`, `template_price_e2e`, `template_spu_compare_0069`,
   `video_publish_v1x/v2x_template`, `monitor_health_template`).
5. **Drop databases one statement at a time.** `psql -f` wraps the script in a single transaction, and PostgreSQL
   refuses `DROP DATABASE` inside a transaction block — the whole batch fails silently. Loop with individual
   `DROP DATABASE ...` statements instead.

Evidence and incident analysis: `docs/handoff/test-baseline-verify-2026-11-08.md` §7.1.

### 1.2 Shared-host capacity

The test host runs browser tests (Playwright/Chromium) and is memory-bound. Concurrency has a real cost:

- **Run one suite at a time.** Two concurrent pytest runs push the 4-core host's load past 7 and produce
  **false failures** — `tests/frontend/test_components.py::test_frontend_components` fails under contention
  and passes alone (`[100%]`). Measured 2026-11-08.
- **Check before starting**: `pgrep -fc "pytest -q"`. If another lane is running, wait.
- Prefer the narrowest selection that covers the change; `unit` is ~3.5 min, the full `fast` suite is
  20–40 min depending on host load.
- If a browser test fails, **re-run it alone** before treating it as a real failure.

The hard exit is the final safety net, not the normal workflow. Always use `scripts/test_isolated.sh`.

Test prerequisites (PostgreSQL client tools, pytest, `.env.test`) are checked and
installed by `bash scripts/envsetup/install-test-deps.sh` — run `--check` first, then
`sudo bash scripts/envsetup/install-test-deps.sh` for the root-owned part.

### 1.3 New worktrees need environment preparation

`.venv`, `.env.test` and `frontend/node_modules` are gitignored, so a freshly created
worktree has none of them and `scripts/test_isolated.sh` fails with
`missing project Python`, `TTS_ERP_DB_URL_TEST is unset` or
`Frontend dependencies missing`.

Run `bash scripts/envsetup/prepare-worktree.sh` once per worktree. It links the venv and
`.env.test` from the main checkout and installs frontend dependencies **for that worktree**.

> **Do not symlink `frontend/node_modules` across worktrees.** Node's ESM resolution follows
> the real path of the link, so a shared `node_modules` resolves against the *other*
> worktree's `package.json` and fails with `Cannot find package 'semver'` when the two
> worktrees sit on different commits.

### 1.4 Conftest basename collisions

`tests/` has no `__init__.py`, so pytest imports every `conftest.py` under its bare basename
(`"conftest"`). With eight such files, a sibling directory's conftest can own that key.

The symptom is specific: **the same file passes alone and fails in a wider selection**, with
`AttributeError: module 'conftest' has no attribute '_mock_payload'`. A fixture that resolves
its own module through `sys.modules[__name__]` is the usual cause.

Import the module by its package path instead:

```python
from tests.browser import conftest as browser_conftest
```

#### 1.4.1 The same file imported twice: patching the payload silently does nothing

Resolving the module by package path fixes the `AttributeError`, but it creates a second
instance of the **same file**: `tests/browser/conftest.py` is imported both as `conftest`
(the module that owns the HTTP handler) and as `tests.browser.conftest` (the object the
`browser_conftest` fixture hands out). They are different objects with different globals.

The browser HTTP handler answers `/v2/**` from `_mock_payload`. Test modules patch it on the
module the fixture returns:

```python
monkeypatch.setattr(browser_conftest, "_mock_payload", mock_payload)
```

If the handler reads its own module global instead, the patch never reaches the server.

**The symptom is not an exception — it is silence.** The page renders the *unpatched* canned
payload, so the browser sees a fixture without the fields the test meant to inject. That reads
as "the page never received the data" (a missing summary panel, a price block that stays
`hidden`, a retry link that never appears) and sends you hunting for a renderer or DOM
regression that does not exist.

Check which object the server reads before touching any selector:

```python
import sys
print(sys.modules["conftest"].__file__, id(sys.modules["conftest"]))
print(sys.modules["tests.browser.conftest"].__file__, id(sys.modules["tests.browser.conftest"]))
```

`tests/browser/conftest.py` therefore routes every canned lookup through `_canned_payload()`,
which resolves `_mock_payload` on the canonical `tests.browser.conftest` object — the one the
fixture returns. Keep that indirection when adding new canned endpoints, and assert on the
served payload when a browser test depends on injected data.

### 1.5 Changing which owner renders a page

When a page's renderer changes (Jinja template → React), every test that asserts on the **old
DOM** breaks — and the failure does not say so. A Playwright `wait_for_selector` on an id that no
longer exists simply times out.

Before switching a page's owner, list what depends on its markup:

```bash
git grep -n "<old-element-id>" -- tests/
git grep -n "<old-template-name>" -- tests/
```

Prefer a shared readiness contract over a hardcoded selector. `tests/support/page_readiness.py`
(`wait_canned_page`) dispatches per slug to the signal the current owner actually renders.

### 1.6 Names that nothing binds

A deleted import leaves its name behind in the source, so `grep` still finds it and every
review reads as "defined". Only the syntax tree knows that nothing binds it any more — and
`from __future__ import annotations` means the annotation is never evaluated at runtime, so
the module still imports, the app still starts, and the page still opens. Only a direct call
raises.

```bash
python scripts/check_undefined_names.py tts_erp_v2        # production tree: must be zero
python scripts/check_undefined_names.py tests \
    --baseline docs/testing/undefined-names-baseline.txt    # test tree: no new findings
```

The production tree is zero-tolerance. The test tree may carry recorded findings (skipped
legacy modules that name things they never import); those live in
`docs/testing/undefined-names-baseline.txt`, and a name that moves or appears for the first
time turns the gate red. The baseline records what is known, it does not excuse it.

Run this after deleting an import, a constant, or a class — a text search will not catch the
call sites you missed.


## 2. Supported commands

| Scope | Command |
| --- | --- |
| **DoD validation (one selected run against the target baseline)** | `bash scripts/test_baseline.sh --scope auto --base "$target_sha"` |
| Full regression for test infrastructure changes | `bash scripts/test_baseline.sh --scope fast --base "$target_sha"` |
| Show the committed master baseline | `bash scripts/test_baseline.sh --show` |
| Refresh the master baseline (master worktree only) | `bash scripts/test_baseline.sh --update` |
| Fast suite (raw, no baseline diff) | `bash scripts/test_isolated.sh fast` |
| Unit-layer tests | `bash scripts/test_isolated.sh unit` |
| Business domain | `bash scripts/test_isolated.sh <domain>` |
| Specific file within fast selection | `bash scripts/test_isolated.sh fast tests/path/test_file.py` |
| Specific test within a domain | `bash scripts/test_isolated.sh <domain> tests/path/test_file.py::test_name` |
| Refresh template then run fast suite | `bash scripts/test_isolated.sh --refresh-template fast` |
| Check / install test prerequisites | `bash scripts/envsetup/install-test-deps.sh --check` · `sudo bash scripts/envsetup/install-test-deps.sh` |

Domain names may be passed with or without the `domain_` prefix.

### Committed candidate selection

Use the exact synchronized master SHA as `target_sha`. On a prospective merge
commit it is the first parent. `--plan` reads Git and prints the affected
groups and reasons. An ordinary run uses a clean committed worktree and
compares the executed tests with the baseline from that target commit.

```bash
bash scripts/test_baseline.sh --scope auto --base "$target_sha" --plan
bash scripts/test_baseline.sh --scope auto --base "$target_sha" --keep-log /tmp/selection.log
bash scripts/test_baseline.sh --scope frontend
bash scripts/test_baseline.sh --scope backend
```

Known pages and API contracts narrow the scope. Unclassified application paths
select both browser-free suites. Inspect the plan when a change affects real
browser behavior. Append `--scenario login-cookie` for browser login and cookies,
`--scenario page-assets` for the bundled assets serving contract, or
`--scenario account-page` for the isolated account page contract. A schema change
requires `--refresh-template`. Browser scopes build current assets when needed.

Page source changes select the registered page browser files without cutting
their desktop/mobile parameters; component-test-only edits stay component-only.
Shared application-shell changes select the shared shell browser coverage. Media
fixtures/helpers select the media browser consumers rather than both complete
suites. Ordinary backend test edits select the changed file; deleted tests and
shared fixtures keep broader coverage. Documentation (including API guides)
selects documentation checks; behavior changes still require code and its tests.
Unclassified code keeps the conservative fallback. Use `--plan` to review it.

The raw isolated `unit`, domain and file commands build frontend assets only
after successful collection, when retained cases request `frontend_build`, carry
`requires_frontend_build`, or are browser tests. The selected runner's explicit
build requirement is honored by the same owner. Build-free API cases reuse the
existing test-only asset-manifest fixture. Required builds use the worktree-local
owner in `scripts/_test_frontend.py`. It hashes actual frontend files (including
uncommitted inputs), shared static sources (excluding packaged output),
lock/config/build scripts, Tailwind's root `.gitignore`, its imported external
TypeScript test contract, Node/npm versions, build environment
and the private npm installation marker. A prior release is reusable only when
this key and every packaged artifact's content still match. Missing/corrupt
artifacts or changed inputs trigger a rebuild; failure or changes during a build
cannot publish cache evidence. Do not hand-edit `node_modules`: use `npm ci`.
The cache is worktree-private, under Git's `tts-test-frontend.json` path; removing
that one file forces a cold build. Producers take EX; consuming pytest sessions
hold SH leases. Builds reclaim their complete subprocess groups before releasing
the lock, including timeout/termination paths.

Every isolated run prints `[test-timing]` phases for connection preparation,
template-lock waiting, template preparation, cloning, test execution, cleanup and
total time. Pytest prints collection/cache/build and setup/call/teardown totals plus
the 20 slowest phases. Phase totals distinguish accumulated test durations from
wall time; they are diagnostics, not a second success/failure signal. Successful
baseline runs also print these timings; `--keep-log` retains full details.
Browser startup, navigation, readiness and cleanup are subparts of those
setup/call/teardown totals, not additional wall time. `[test-build]` identifies
cache hits and builds.

The Monitor Zabbix export selects its YAML syntax and structure checks through
the isolated runner. These checks do not validate a live Zabbix import. Other YAML
paths keep their existing fallback. Mobile navigation rules live in
`frontend/src/app/navigation-drawer.css` and select the application-shell component
tests and the two-prefix mobile drawer color/focus browser check. Shared
`workspace.css` changes keep their broader layout coverage. Named browser scenarios
are added to either plan when requested.

Use `--scope fast --base "$target_sha"` when changing selection, the baseline
driver, test execution or isolation, result reporting, or global test collection
and fixtures. This includes `scripts/test*.sh`, `scripts/_test_selection.py`,
`scripts/_run_selected_tests.py`, `tests/support/selected_test_report.py`, and
changes to global pytest collection or `tests/conftest.py`. Fast remains an explicit
regression command. An auto run counts as the baseline comparison for an ordinary
change, so a second full run is unnecessary.

Changes to `tests/e2e/` require separate evidence from the affected explicit
smoke command in its approved environment. Keep deployment-dependent smoke runs
outside the automatic gate. For docs-only changes, validate links, referenced
paths and Markdown in addition to the diff check.

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

Frontend selections also need Node within `frontend/package.json`'s engine range
and a worktree-local `npm ci --prefix frontend` installation. Do not share writable
`node_modules` or Vite caches between lanes. Component tests use two jsdom workers
to fit the validation VM's memory budget. The component adapter and local Vite
fixtures own their npm process groups and reclaim descendants on success, failure,
or timeout; increasing test deadlines is not a substitute for process cleanup.
The shared `frontend/scripts/check-runtime.mjs` uses npm/node-semver 7.8.6 (ISC,
https://github.com/npm/node-semver) against that one engine declaration. npm
typecheck/build/component/dev commands run it first; cache reuse also checks the
active runtime. Browser-free Python selections do not require Node.

The common read-only browser renderer reuses Chromium within each test module.
Each test owns its Renderer/request records and fresh BrowserContexts. Module
teardown closes the browser and Playwright; an async item's setup also releases
the sync API before pytest-asyncio starts its loop. It is not session-wide reuse.
Lifecycle regression covers same-module reuse, cookie isolation, async cases
between sync cases, and a following async module. P2 auth/permissions, media,
runtime-config and live price fixtures borrow the same module owner, retaining
hard failures for missing Chromium and fresh contexts, fences, login, canaries
and persistence checks per case. Six P2 role/prefix cases reuse their verified
API child/accounts per module and prefix; command facts remain case-owned.

The common canned Renderer waits for hydration/lazy-page completion instead of
networkidle. Cases wait for actual data/command state; generic layout/font checks
use `tests/support/page_readiness.py` to require positive tables/data and fonts.
Canned payloads follow current decoder contracts. Deployed key-based E2E keeps
its previous network-idle behavior until separately migrated; writable workflow
negative/replay waits are not mechanically removed.

### Optional two-way read-only browser execution

```bash
bash scripts/test_isolated.sh --browser-workers 2 browser \
  tests/browser/test_frontend_ad_daily_filters.py \
  tests/browser/test_frontend_dashboard_layout.py
```

This first-option form accepts one or two workers, explicit files/node targets
from `scripts/_test_browser_parallel.py::SAFE_FILES`, and no pytest flags. Unknown
files, duplicate/overlapping targets and `TTS_ERP_TEST_DB_NAME` overrides are
rejected. Use serial commands for filters, writable workflows, Vite and heavy
components. Whole modules stay together. Prewarming releases its lease before
children revalidate inputs after loading `.env.test`. Each child enters the
original isolated wrapper, gets its own generated database/proof, local server
ports and private log. Every child must finish; any failure, empty collection or
cleanup failure makes the overall command fail. Cancellation/launch failure
allows pytest/shell cleanup before reclaiming groups. This opt-in adapter does
not replace full fast baseline execution or its completeness report, and never
shares test data.

## 4. Template and shared database usage

`bash scripts/test_isolated.sh ...` is parallel-safe for ordinary agent work: it serializes template refresh/clone through `/tmp/tts-erp-test-template.lock`, clones `tts_erp_test_template` to a unique `tts_erp_test_*` database, points the test process at that clone, and drops the clone on exit. Concurrent isolated runs do not delete each other's `TEST_` rows.

Refresh the template when schema/migration state changes or if a run reports missing tables:

```bash
bash scripts/test_isolated.sh --refresh-template fast
```

The refresh path rebuilds only the test-shaped template DB. It imports production schema read-only through `scripts/import_prod_to_test.sh --schema-only --recreate-test-db --yes`. This explicit mode binds the shared destructive-script guard to the effective test destination, completes source/client preflight and the entire schema dump **before** replacing that database with PostgreSQL `dropdb`/`createdb -T template0` (without forced disconnection). Rebuilding the clean test target removes all target-only objects, including constraints added to otherwise shared tables; the wrapper no longer deletes target-only tables ahead of dump generation. Failures are nonzero and must not be treated as a healthy template.

The wrapper then stamps the production alembic revision and upgrades to the worktree head. If the production revision is absent, it retains the existing warning rather than guessing; this warning is not proof that the imported schema satisfies that branch. It must never target production. Normal importer modes are unchanged; agents still invoke tests only through the isolated wrapper, not this lower-level importer.

`scripts/_test_db_connections.py` is the shared identity authority: psycopg parses effective parameters and SQLAlchemy constructs template, unique run and maintenance connections. An original `dbname` override is replaced by the explicit lifecycle identities, never retained as a shared test destination. Production-shaped effective base connections, ambiguous standalone recreation destinations, missing explicit user identity, service/hostaddr/multi-host and control-character configurations fail closed. Ambient libpq routing/role options (`PGHOST`, `PGHOSTADDR`, `PGPORT`, `PGDATABASE`, `PGUSER`, `PGSERVICE`, `PGSERVICEFILE`, `PGOPTIONS`, `PGTARGETSESSIONATTRS`, `PGLOADBALANCEHOSTS`) are unsupported and rejected; do not silently unset them or guess a different destination. All existence/clone/cleanup and test connections share the same server/user/port; test credentials must permit creating their own databases.

Host-client mode (`PG_DOCKER=''`) supports a single explicit TCP endpoint. Docker mode supports only the repository's verified `127.0.0.1:5432` profile: no ambient `DOCKER_HOST`/`DOCKER_CONTEXT` overrides, an active local `unix:///var/run/docker.sock` context, and the selected container publishing `5432/tcp` to host `5432`. Before PostgreSQL commands, the helper also probes only the selected container's libpq routing/role variables and rejects nonempty overrides; values and unrelated container credentials are never returned. It pins the inspected immutable container ID as well as the local socket, so the probe and subsequent lifecycle commands cannot drift through a rebinding of the container name. Unsupported or unverifiable container environments are rejected, not silently cleared. CLI `ValueError` diagnostics are fixed text, never arbitrary library exception messages (including Unicode URI parser failures that may contain credentials). Commands do not guess remote/VM routing. Unsupported routes require correct prerequisites, not an automatic fallback. Ephemeral cleanup failure is reported as a failing run, not a green result.

### Current-run provenance and connection fence

Only after `createdb` succeeds, the wrapper issues a fresh private proof through the same connection authority and passes `TTS_ERP_TEST_RUN_PROOF` / `TTS_ERP_TEST_RUN_ID` to the test command, replacing inherited values. The versioned artifact binds the original base, selected template and newly created run database, salted connection-identity digest, and the live wrapper's PID/start identity. It contains no URI, password or plaintext connection parameters. Base and template may coincide; the run must differ from both and cannot use the reserved default template/shared names.

`load_run_binding()` rejects absent, malformed, non-private, nonregular/symlink, stale or non-ancestor-owner proofs and noncanonical/mismatched candidate connections. The canonical conftest consumes it before dotenv or engine construction; probe/fixture engines are checked against that identity before connecting. The schema probe checks actual `current_database()` before table inspection. Do not invent a second DSN parser, infer the expected database from the candidate URL itself, or suppress refusal with a production override.

Normal/error cleanup removes the proof even with `--keep-db`: retained data is not writable run provenance. The nonce/digest/process checks fence accidental misbinding and stale artifacts under the local process model, not malicious same-UID processes. New writable subprocess fixtures must also transfer and verify this binding against their actual app engine/session, then bind fresh child/HTTP evidence and perform an authenticated canary read before login/business commands. The P2 plan defines those additional gates; the run proof alone does not establish browser write acceptance.

### No shared-database fallback

If template cloning or PostgreSQL client tooling is unavailable, stop and repair prerequisites with `bash scripts/envsetup/install-test-deps.sh --check` followed by the documented installer. Agents must not substitute a shared database, a direct framework invocation, or an archived migration selection.

## 5. Selecting validation scope

- Documentation-only change: validate links, referenced paths, and Markdown structure; application tests are not normally required.
- Ordinary code change: use the narrowest relevant tests and the committed candidate selection above.
- Shared change: inspect the automatic plan and add an affected named scenario when needed. Changes to the global test infrastructure use the explicit fast rule above.
- Migration change: pass `--refresh-template` to the isolated baseline command so the schema and tests use an ephemeral clone.
- Service-dependent tests must use the documented service setup and remain bounded by a timeout.

## 6. Failure classification

The default completion criterion is zero failures.

The master baseline is a property of **master**, not of your lane. It is
committed at `docs/testing/baseline-fast.txt`, so a lane does **not** re-measure
it. `scripts/test_baseline.sh` runs the suite once and produces the diff
mechanically:

```bash
bash scripts/test_baseline.sh --scope auto --base "$target_sha"
bash scripts/test_baseline.sh --scope fast --base "$target_sha"  # test infrastructure
bash scripts/test_baseline.sh --show       # print the committed baseline
bash scripts/test_baseline.sh --scope auto --base "$target_sha" --keep-log /tmp/lane.log
```

Exit codes: `0` zero new stable failures (DoD pass), `1` new failures present,
`2` usage/baseline error.

> **A `0` exit from `--scope auto` does not always mean "the suite is green".**
> When the auto selection plan comes out empty — which happens for
> documentation-only changes, and whenever `head == base` — the driver prints
> `no application tests selected; perform any indicated documentation checks`
> and exits `0` **without running anything** (`scripts/test_baseline.sh`,
> the empty-plan branch). A lane that only runs `--scope auto` therefore gets a
> clean verdict that never touched the suite.
>
> This matters because the committed baseline is a **property of master**, and it
> goes stale as master advances. On 2026-11-08 the baseline still described
> `e04c5e11` (2026-10-15, `count: 0`) while master had moved on by hundreds of
> commits; a full `--scope fast` run then surfaced 56 failures that every
> auto-scoped lane had been reporting as zero.
>
> **When you need to know the real state of master, run `--scope fast`, not
> `--scope auto`.** Use `--scope auto` to check *your own* change against the
> baseline, and read the selection plan (`--plan`) before trusting its verdict.
> If the baseline is stale, regenerate it from a clean master worktree
> (`bash scripts/test_baseline.sh --update`, master only).

The diff is computed on normalized test ids only (`FAILED`/`ERROR <id>` with the
reason text stripped), because reason text carries line numbers and assertion
reprs that churn between runs and would make every diff look like noise.

**Run the suite once.** The old procedure — capture a baseline, merge, run
again, then rerun the suite per failure — cost 2+ full runs per lane and was the
main reason worker sessions hit their wall-clock cap. Only if the tool reports
new failures do you rerun, and then only the smallest failing selection.

For every reported failure:

1. Rerun the smallest failing test selection once through the isolated runner.
2. If the isolated rerun passes, record it as a likely flake.
3. If it fails consistently, treat it as a real failure and fix it before completion.
4. Do not classify an unrerun failure as pre-existing or flaky.

### When the baseline goes stale

If `--show` reports that previously failing tests are now passing, master itself
fixed them and the committed baseline is out of date. Regenerate it **from a
clean master worktree**:

```bash
bash scripts/test_baseline.sh --update
```

A lane must never regenerate the baseline to make its own run look clean — that
would silently absorb a real regression into the master baseline.

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
