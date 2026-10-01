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
| runtime-config-auto-schema | 运行配置自动推断Schema | 01a0f13c-c315-760e-b7c9-6ac80761f9dc | `feat/runtime-config-auto-schema` / `.worktrees/runtime-config-auto-schema` | `tts_erp_v2/{api/v2/config.py,runtime_config/validation.py,static/js/runtime-configs.js,static/css/runtime-configs.css}`; `tests/api/test_runtime_config.py`; `tech-doc/runtime-config-management.md` | active | — | — | 2026-10-01 10:49:01 |
| reuse-evaluation-doc | 复用改造评估总结文档 | 01a0f628-3176-729f-8747-2aac7e599eab | `docs/reuse-evaluation-doc` / `.worktrees/reuse-evaluation-doc` | `tech-doc/reuse-evaluation-2026-10.md` | ready | 8422ca1b499f3b5996e6651cd6fbf4eaded254aa | 1b0b13f3af55d1e721d0e6151fc9418c25613683 | 2026-10-01 10:56:27 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
