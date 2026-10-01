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
| runtime-config-jsoneditor | 官方JSONEditor集成 | 01a0f13c-c315-760e-b7c9-6ac80761f9dc | `feat/runtime-config-jsoneditor` / `.worktrees/runtime-config-jsoneditor` | `tts_erp_v2/static/{vendor/jsoneditor.*,vendor/jsoneditor.LICENSE,vendor/NOTICE.md,js/runtime-configs.js,css/runtime-configs.css}`; `tests/api/test_runtime_config.py` | active | — | — | 2026-10-01 06:26:57 |
| reuse-first-policy | 引入复用优先策略 | 01a0f628-3176-729f-8747-2aac7e599eab | `chore/reuse-first-policy` / `.worktrees/reuse-first-policy` | `AGENTS.md` | ready | 780374448e98ca89f3d3c2af512763583b3ec15f | 8c3615b1055fa325dff455e133619642bd40de0e | 2026-10-01 06:34:57 |
