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
| fix/focused-spus-nav-label | 将左侧导航入口明确显示为“重点关注 SPU” | 01a0edf5-a325-77b2-ade9-ac88e25bdd89 | `fix/focused-spus-nav-label` / `.worktrees/focused-spus-nav-label` | `tts_erp_v2/api/v2/pages.py`; `tests/api/test_focused_spus.py` | active | — | — | 2026-09-30T00:53Z |
| miaoshou-manual-cost-import | 妙手采购价映射与人工成本导入 | 01a0efbb-1bcc-77b2-ade9-ac9dfdf3d0a0 | `chore/miaoshou-manual-cost-import` / `/home/schan/tts-erp/.worktrees/miaoshou-manual-cost-import` | `tech-doc/miaoshou-purchase-price-import.md` | active | — | — | 2026-09-30 00:46:23 |
