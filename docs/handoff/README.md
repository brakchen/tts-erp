# Agent handoff

The **tower-do board is the only live task and lane registry**. Do not maintain an ACTIVE table or commit generated live status.

- Start with `tower_do_status` from the canonical `/home/schan/tts-erp` coordination session; confirm the board path.
- Use the permanent `lane-registry` task's message thread for lane ownership, child reservations and the ready queue.
- Read [the canonical Git workflow](../guides/agent-git-workflow.md) for the record schema, read-only projection, identity/handoff checks and integration rules.
- [ACTIVE.md](ACTIVE.md) remains only as a relocation notice for older sessions and links.

This checkout's board is host-local; another worktree or machine does not automatically share it. Do not confuse ordinary task completion with validated lane readiness. Preserve foreign WIP and all production/test safety boundaries.

The migration rationale, immutable source references and verification receipt are in [the migration work record](../aegis/work/2026-10-23-board-lane-registry/10-intent.md). These are historical evidence, not live lane status.
