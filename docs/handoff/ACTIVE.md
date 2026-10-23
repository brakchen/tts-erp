# ACTIVE.md — live registry moved to tower-do

> **Static relocation notice. Do not add or maintain lane rows here.**

The canonical tower-do board is now the only live task/lane registry. From the `/home/schan/tts-erp` coordination session:

1. Call `tower_do_status` and confirm the canonical board path.
2. Locate the permanent `lane-registry` task and read its lane-record message chains, including child reservations.
3. Follow [the Git workflow](../guides/agent-git-workflow.md) for record format, read-only projection, ownership, ready validation and integration.

Different worktree/plugin checkouts do not automatically share the same board. Use a canonical-root coordinator; do not restore an old ACTIVE table or fall back to this file when the board is unavailable.

The pre-cutover snapshot remains in Git at `f67a7b8e5e923f06bcc763e0cba4a0a12ff85ac5:docs/handoff/ACTIVE.md`. Its historical notes are evidence, not a competing live registry. Migration provenance and validation are recorded in [the migration work record](../aegis/work/2026-10-23-board-lane-registry/10-intent.md).
