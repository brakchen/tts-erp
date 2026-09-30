# ACTIVE.md — tts-erp live lane registry

> This file contains **live work only**. Completed and abandoned history lives in Git history, not in this table.
> The canonical lifecycle is `tech-doc/agent-git-workflow.md`.

## Registry rules

- States: `draft` → `active` → `ready`; temporary `blocked (<reason>)` is allowed. Delete the row after merge or abandonment.
- `draft`, `active`, and `blocked` reserve their declared files. A `ready` branch is immutable and may be integrated by any session.
- A ready row records immutable `head_commit` and `synced_master` values. Later registry-only commits do not invalidate it; later non-registry master changes do.
- `owner(session)` must be the real Pi session UUID, never `本 session`.
- `updated/ready_at (UTC)` is the last state-change time; for `ready`, it is the FIFO queue timestamp.
- `handoff/ACTIVE.md` is coordination metadata and must not appear in the owned-file column.
- Edit registry state only in a clean master/coordination/integration worktree under `/tmp/tts-erp-active-md.lock`; lane worktrees do not carry registry-only edits.
- Shared hotspot ownership does not require waiting for the original session: exchange a focused patch, hand off ownership explicitly, or create a successor lane from the predecessor's ready commit.

| lane_id | Topic | owner(session) | branch/worktree | Owned files/directories | State | head_commit | synced_master | updated/ready_at (UTC) |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| backend-owned-ui-computation | 前端只渲染、业务计算收敛到后端 | 01a0f006-c8e7-77b2-ade9-aca42c580c53 | fix/backend-owned-ui-computation / .worktrees/backend-owned-ui-computation | tts_erp_v2/api/v2/{commerce.py,intercept.py,reporting.py}; tts_erp_v2/analytics/{spu_roi.py,spu_profitability/}; tts_erp_v2/static/js/{dashboard.js,console.js,intercept-stats.js,spu-profitability-page.js}; tests/api/; biz-doc/analytics/spu-roi-profit-calculation.md; tech-doc/{external-api.md,analytics/spu-profitability-module-decisions.md} | draft | — | — | 2026-09-30T02:46:05Z |
