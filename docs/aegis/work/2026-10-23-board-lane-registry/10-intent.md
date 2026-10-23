# Board-only lane coordination — intent

## Authority and outcome

The user approved replacing manual `docs/handoff/ACTIVE.md` maintenance with the board as the sole live state source. Preserve lane ownership, child reservations, ready ordering, immutable commit evidence, and safe integration. The preceding conversation is the parent plan; this is its bounded execution record, not a new approval request.

## Change Necessity

- Need: eliminate duplicate live registries and registry-only Git commits.
- No-change option: leaves conflicting task/file ownership and stale process logs.
- Decision: docs/config-only plus board data migration. No application, plugin, extension, database, dependency, or production changes.
- Minimum boundary: `AGENTS.md`, `docs/guides/agent-git-workflow.md`, static handoff pointers, and this work record.

## Baseline and TaskStartSnapshot

- Session: `01a16851-2ab0-7605-a9b1-1e390a2830c5`; root `/home/schan/tts-erp`.
- Initial local master: `0e249b9da681d3a6fe37a40e1e8cb0d1f5e82f70`, behind origin by one registry-only commit; index empty. Pre-existing `plugins/monitor` modification and untracked `node_modules/` remain untouched. No merge/rebase in progress; worktree inventory inspected.
- Dedicated clean worktree: `/tmp/tts-erp-board-lanes-01a16851`, branch `chore/board-lane-registry`, initially `a64881365ee499c0fc803c3e3b7b35bc29def9ec`.
- Registration commit `9d97b4e892d6f2e6891aaf2a0c6b0e187812c835` pushed under the old registry lock before task edits.
- Required baseline: complete root `AGENTS.md`, complete Git workflow, current remote ACTIVE registry, installed tower-do README, contracts, and project-root implementation. No task-relevant root CONTEXT map found. Missing Aegis index does not authorize creating a workspace-wide index.

## Implementation classification and design

- Risk: medium shared-workflow/data retirement; ArchitectureReviewRequired: yes, covered by user-approved direction and installed-tool compatibility review.
- TDD route: Mode auto / Decision skipped / no strict authority. This slice changes documentation and live coordination records, not executable source. Verify migration equivalence, ownership constraints, examples, links, and exact Git delivery instead.
- Installed tower-do has no task `scope`, `changedFiles`, lane state, or project override fields. It supports four task statuses, 2000-character descriptions, 50 open tasks, and 32-KiB messages. Board tools are path-scoped: different checkouts do not automatically share a board.
- Reuse existing task/thread facilities: one permanent `lane-registry` index, full lane JSON records in threaded messages, explicit predecessor IDs, and procedural file ownership guards. Do not modify the installed extension or invent unsupported tool fields.
- Canonical coordination session root is `/home/schan/tts-erp`; all code/Git operations use explicit worktree paths. Sessions anchored to another board must hand off through a canonical-root coordinator before writes.
- Long process notes remain recoverable from immutable source commits; scope, handoffs, reservations, states, owners, branches and commit claims must be copied without widening authority. Legacy ready claims require fresh revalidation, not automatic acceptance.
- No mirror is necessary: ACTIVE becomes a static relocation notice. No generator, extra dependency, second live registry, or speculative extension API.

## Plan and acceptance

1. Inspect capabilities, establish ownership, and announce migration (done).
2. Import every current remote lane and temporary reservation; update canonical workflow and root instructions.
3. Re-read under the legacy lock before cutover to capture concurrent registrations. Verify imported owner/scope/branch/commit/timestamps against that exact source, validate documentation and examples, push branch, integrate under master lock, and notify peers.

Success requires all source lanes accounted for, no dropped reservations or foreign WIP, a single discoverable canonical board, honest imported readiness, no remaining manual-registry requirement, and successful branch/master pushes. A concurrent unknown ownership change, malformed/forked lane chain, wrong board, or lost source row stops dependent actions. Stable business/test/production safeguards remain unchanged.
