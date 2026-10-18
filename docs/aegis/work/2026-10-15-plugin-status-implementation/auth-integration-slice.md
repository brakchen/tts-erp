# Original-goal ERP canonical status authorization — bounded design

Status: fresh review changes_required consumed; native OWNER accepted bounded amendments and conditional phase1 Source gate/phase2 tests-only. Implementation remains blocked-setup-partial, NOT accepted. This is the existing plugin-status/auto-bind delivery task, not a context-mode/Pi Web or unrelated repair.

## Latest execution and accepted amendments

- New seven typed schema/mount/CORS business REDs preceded partial phase1 edits. Sole wrapper refresh then exited4 before clone/collection because existing0070 shop FK blocks default test-template PK restore; no new GREEN, migration application unverified. envsetup --check0 is not schema validation. Worker/workflow complete, no rerun/resume. Formatting check still1; phase2 tests authored but unrun.
- Default template may be partially replaced. OWNER has not issued direct SQL/dropdb or alternate-DB/runner fallback; explicit guarded same-name test-template reset approval is pending. No production action. Existing template-only-table cleanup does not remove this target-only FK on a source-present table.
- Registry16b900d explicitly reserves auth/rate_limit/access_log adapters for exact POST report. Pure error payload helper beside existing status_request_id is shared by API and adapters; resolve one UUID once through existing scope owner, no global response interceptor or middleware order change. Cover401/403/503 and both429 paths, no-store/WWW-Authenticate/Retry-After/XRateLimit preserved, no HTML/redirect.
- Outer access_log must omit entire exact POST report query string before snapshot, including success/deny/exception; auth report logs must not retain plaintext/random token prefixes. Other paths remain unchanged.
- Missing rotate/current-ingest checks are NEW repairs, not retained safeguards. Required immutable authenticated scalar identity is mandatory at ingest and all owned callers, no optional trusted-caller bypass. Actual two-session disable/rotate/rebind/live/rollback tests must prove forced refreshed current instance/key locking; no independent updater under held same key lock.
- Exact report in enforce/off/shadow always needs current valid status Bearer and exact RW/purpose/instance/account/capabilities; valid may succeed, invalid401/wrong403/store503, no anonymous/SHADOW_ALLOW. Ordinary routes and monitor dev-mode policy unchanged.
- app wiring may use official expose_headers union of X-Request-Id,Retry-After,X-RateLimit-Limit,X-RateLimit-Remaining only; this exposes existing nonsensitive transport metadata, not extra API authority. No origin/credentials/method/header allowlist expansion. Source for phase2 remains a separate gate after valid new callsite RED.

## Authority and baseline

- Approved protocol §9: dedicated `role=readwrite, purpose=plugin_status`, exact `POST /v2/plugin/status/report` only; ordinary/admin keys do not gain report permission; monitor_config remains its original shop-bound GET; status recovery cannot be anonymous under off/shadow.
- Reuse `security.api_keys` and canonical `access/`, rotation/revocation, key hashing and existing admin/cookie-CSRF policy. No second credential store/auth service.
- Formal stale-task adoption (task-specific five-day inactivity) was followed by explicit registry transfer on master `4f39a66`. It moves access/, security.py and app.py to this status lane; source is verified committed config-ERP `03d595f`. Foreign worktree/WIP stays untouched. db/base.py, global model registry and plugin storage are NOT transferred.
- Seven maintained sources were integrated unchanged, including immutable original0070; canonical general/monitor compatibility tests are an unchanged copy of the committed tests under this lane's test prefix. Isolated template upgraded ONLY to0070 and all24 compatibility cases passed.
- New typed RED: exact report role remains ADMIN instead of READWRITE; purpose enum lacks plugin_status; real0070 CHECK makes valid provisioning return503 instead of expected200. Three business failures/four controls, no case setup/import error. Full log retained; a summary parser once counted traceback E prefixes as test errors and was corrected separately.

## Change necessity and owner fit

Decision: code-change. The approved purpose cannot be provisioned or authenticated through the current two-purpose owner. Configuration changes or a parallel status-only token parser would not repair the canonical boundary.

Risk high; TDD auto/strict, authority the recorded permission/persistence/contract auto decision and approved protocol. ArchitectureReviewRequired=true before new-feature Source edits. Dependency integration above is baseline preparation only.

- Extend existing typed access purpose/route owner, current credential primitive and security model; no framework/new auth owner.
- service.py is large: deny-only instance/scope composition and removal of raw purpose-column fallback only, not another authentication responsibility.
- app.py changes wiring-only and preserve middleware registration order.
- A new0071 owns the four plugin.status tables and purpose CHECK extension. Never rewrite published0070 or alter production automatically.

## Required semantics

1. Add PLUGIN_STATUS to canonical purpose enum. Preserve existing general/monitor behavior and the24 compatibility controls; no readwrite prefix broadening.
2. Add only exact POST report to the role owner. Status-purpose credentials must not gain other authenticated API/config/admin routes; general/admin/monitor keys and cookies are not status report authority.
3. Reuse the uncached current credential lookup for report authorization so disable/rotation takes effect immediately. Distinguish absent/invalid/revoked credential401, valid wrong role/purpose403, store-unavailable503. A filtered nullable helper must not erase that distinction.
4. Domain service must implement missing current-key transaction rechecks in addition to existing instance/scope checks; current source did not reject rotated keys or recheck key on ingest. Enforce required immutable original authenticated key/instance scalar identity, refresh under lock, then FK, active/unrotated, exact role/purpose/shopNULL, enabled, plugin/seller/advertiser/capability checks. Canonical authentication must run BEFORE acquiring the report's key row lock: calling an independent-session last-used updater after holding that key lock can self-deadlock. No cached grant substitutes for current applicability.
5. Mint with the canonical ORM purpose already set before first flush; avoid inserting a general key and patching a raw column. Remove the old raw SELECT/UPDATE purpose fallback once the canonical mapped column is present; pure compatibility predicates may delegate to the canonical enum.
6. Status-key shop_pk is NULL: its one-to-one StatusInstance association owns shop/installation scope. Monitor keys still require their own nonnull shop_pk and general keys still require NULL. Enforce in both canonical validation and new frozen migration CHECK; do not duplicate a status scope store.
7. Add the four status tables exactly matching the existing models/FKs/unique/index/timezone contracts. Upgrade only via isolated runner template/clones. Downgrade that destroys audit facts must have the shared destructive guard and must not silently delete facts to restore the old purpose constraint. No production upgrade/downgrade/restart.
8. Mount actual status router through existing app assembly, preserving middleware order and wire contracts. Do not activate a live job or send notifications. If a source global model registry change is required, stop and request its separately held scope.
9. Replace temporary test purpose DDL/table bootstrap as integration proof with actual migrated schema checks. Existing component tests may still be retained as components; real-app/middleware success is a separate criterion. Real-app ASGI tests must avoid lifespan/background/external-service activation and use synthetic credentials plus isolated DB.
10. Off/shadow must not anonymously obtain a recovery decision. Preserve ordinary global dev-mode behavior outside this protected report boundary; do not silently reinterpret broader mode policy. If canonical purpose/error-mode handling is ambiguous, ask OWNER before choosing.

## Scope and verification

Writable only within native per-phase gates: canonical access5 files, security.py, status service/contracts, API wire-helper reuse, app router/CORS metadata wiring, new0071, three exact-report ASGI adapters and owned test_plugin_status* tests. Adapter reservations are explicit; phase2 production edits not yet granted. Existing0070 and copied compatibility test are immutable controls. No db/base/global model registry, notification/outbox/business-policy/controller/clock/tool/package edits in this slice.

- Baseline24PASS retained; original typed3RED/4controls retained.
- Strict minimum repair plus tests for valid status key under enforce/off/shadow; wrong purpose/role/method/prefix, general/admin/monitor/cookie, cross-instance/seller, disabled/rotated current key, rollback/no half-minted key, ordinary role and monitor scope compatibility.
- Real app report/management registration with actual0071 schema, no synthetic scope grant replacing real auth, no fixture-added purpose column. Clearly separate no-lifespan route proof from startup/deployment/TLS proof.
- Named isolated tests only for worker; full safe fast baseline OWNER-only at the final stable lane. No all/coverage/archive suites, direct pytest/SQL/alembic or real credentials/requests.
- Fresh post-GREEN spec/quality plus OWNER same-byte reproduction before readiness. Native completion/child local accept does not grant SDK/merge/release acceptance.
