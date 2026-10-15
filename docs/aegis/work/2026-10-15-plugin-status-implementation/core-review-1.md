# Core spec reviews — owner dispositions

## Evidence and gate

- Workflow: `920f5138-1015-4db1-801f-c971795deed5`.
- ERP core: `f68dc2c3-9a1b-4613-bd18-de26e449ff3c`; plugin core: `f7be6413-1916-48dc-9e6d-fa4fd4d7d61f`.
- Fresh spec reviewer: `2c25078c-9fd8-47b4-a75d-8f5187aa3a03`; supervisor reply `19be0c2c-b2ae-4007-ab6c-34873f48dc7c`.
- Verdict: **BLOCKED**, not feature acceptance. Existing core writers receive fixes through the same workflow. Runtime and integration stay gated.
- Owner independently read the full approved specification and the actual Python contracts/policy, TypeScript contract/primitives/queue/state/retry/client. Reported GREEN is component evidence, not protocol acceptance.

## Confirmed corrections (core ownership only)

1. ERP must apply the observation age window `-5..120s` to recovery as well as release. Old reports remain history, not authority.
2. Python nonrecover decision parameters must serialize `{}`, not null; capture timeout is exactly 90. Add Python-producer/TypeScript-consumer parity examples.
3. Reject coercible JSON values and non-UTC/numeric timestamps; use canonical lowercase dashed UUID consistently. Cross-language boundary tests must include malformed calendar dates, optional/null fields and numeric/boolean distinctions, not only matching happy-path fixtures.
4. Episode requirements depend on reported state as well as event type. Invalid, initializing and recovering state cannot omit the incident by labeling itself heartbeat. User-disabled nonincident state is not automatically a fault episode.
5. The plugin's sole pure authorization gate must require finite current clocks and the client-derived monotonic deadline. `observedAt` is evidence, not current time. Fail closed for invalid clocks, aged observations/snapshots and local monitoring disabled (recover as well as resume). The future Chrome wrapper is not a substitute for a safe exported authorizer.
6. A same-episode verified same-target candidate appearing after no-candidate/unknown context may request recovery. Do not broaden this to loading/lease contention or bypass transport failure count, manual latch, disabled priority or budget.
7. Ten-minute escalation covers unresolved episodes, including no-candidate/loading/in-flight recovery, preserving manual/disabled priorities. Episode churn cannot reset the budget.
8. Owner additionally reproduced client body-timeout misclassification: a synthetic stream aborted at the 10ms test deadline returns permanent `protocol`, not retryable `transport`. Race the complete fetch/body operation and distinguish timeout from invalid JSON. No network was used.
9. Owner additionally reproduced arbitrary `error.code` forwarding. Only bounded registered codes may leave the HTTP boundary; unknown text becomes null, never an exception/message fallback.
10. ERP installation scope must not acquire binding/monitor capabilities through registration or a fabricated report binding. Valid generic installation reports with no such capabilities and null binding remain possible.

## Owner rulings

- `AdminDecisionRequest.incidentId` and `expectedBindingEpoch` are required canonical UUID preconditions alongside `expectedRevision`. The runtime compares them against current context under the instance lock. No current context means no forced binding command. Specification §9.5 now makes this explicit.
- UUID wire spelling is lowercase dashed, now explicit in §6.2. Strictness must preserve valid JSON enum/UUID strings; do not blindly enable global Python strict mode that rejects normal wire inputs.
- Queue identities must be allocated in observation chronology before send prioritization. Allocating a later high sequence to an old observation would let history overwrite current projection. Current oldest-draft allocator supports the safe order; runtime must retain it.

## Artifacts and limitations

- New plugin files are untracked; an empty tracked Git diff does not mean no implementation. No existing tracked plugin file was modified during T2.
- Owner probe: `/tmp/plugin-status-owner-client-probes-01a1406d.ts`, executed by the existing plugin `vite-node`, exit 0. Observed: body timeout category `protocol`, `permanent=true`; unknown code forwarded `true`. These are defect reproductions, not passing acceptance tests.
- ERP repair run: `f6f1a26a-7b3a-4b2f-96d1-524a00bc24a2`. Extra scope-capability/admin-precondition guidance queued via native steer `fbe0d528-5b01-400b-b482-872abd74f88d`; queued is not consumed/fixed evidence.
- Plugin HTTP additions 8/9 were confirmed again and included in review 2's blocking verdict; no second writer is launched.
- No production migration, service restart, live credentials, real browser profile or TikTok/Feishu request was used. S13 remains unverified.

## Review 2 — still blocked

- Reviewer `ae296447-8dd9-4243-9bd3-f5136ce9f383`; owner reply `b5b72b18-a199-4944-8ee0-366419cd38e9` confirmed source findings after direct rereads. ERP repair 2: `2de2a5ee-5ead-4298-9f5c-8f13e37cc34f`.
- Prior fixes for recovery freshness, empty parameters/exact 90s, integer/UTC/UUID strictness, verified candidates, non-ready episode timeout, admin preconditions and installation capability denial are now present; that is not full core acceptance.
- Still blocking: HTTP header-only deadline and arbitrary error-code forwarding; TypeScript normalizes February 30 through Date.parse; Python episode predicate uses AND where the state contract needs OR; invalid snapshot timestamps can still authorize resume.
- Independent owner probe `/tmp/plugin-status-owner-rereview-probes-01a1406d.ts`, existing vite-node, exit 0: February 30 accepted `true`; invalid snapshot authorized `resume_monitoring`; invalid decision notBefore/expiresAt authorized `recover_binding`. These are concrete failing safety observations, not passing tests. All timestamp comparisons must reject nonfinite/unreadable instants.
- Owner ruling: any unresolved not-running episode at 600s, including ready+suspended with stale or fresh snapshot, enters require_user. Mere readiness does not finish the episode. Manual/disabled priorities and healthy-running null remain ahead of timeout. Add 599/600 boundary coverage.
- Owner additionally inspected policy precedence: REPORT_QUEUE_FULL must beat generic verified-candidate recovery; top-level USER_DISABLED must remain hold even if other fields are momentarily inconsistent. ERP writer guidance queued as `e1d32fc8-4ce4-4acc-9ef9-570176ee7ae0`; queued is not fixed evidence.
- Future T4 prerequisite: the known duplicate/missing-page completeness defect `f-03dab10a-559` would make the new complete-snapshot gate falsely ready if reused unchanged. Requested narrow file ownership coordination from the existing monitor owner; no edits to that file or shared runtime paths yet.
- requestId string-vs-UUID is a P2 compatibility consideration, not a default-path blocker because the current client generates UUIDs. Do not alter project-wide request-ID semantics as a side effect.
