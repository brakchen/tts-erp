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
| runtime-config-crud-safety | 修复运行配置并发与归档 | 01a0f13c-c315-760e-b7c9-6ac80761f9dc | `fix/runtime-config-crud-safety` / `.worktrees/runtime-config-crud-safety` | `alembic/versions/0050_runtime_config_lifecycle.py`; `tts_erp_v2/{api/v2/config.py,access/_policy.py,db/models/config.py,runtime_config/,static/js/runtime-configs.js}`; `tests/api/test_runtime_config.py`; `tech-doc/{runtime-config-management.md,external-api.md}` | active | — | — | 2026-10-01 02:10:21 |
| remove-sync-jobs-legacy-page | 删除定时任务旧页面入口 | 01a0f39a-7180-760e-b7c9-6b0a00a152c2 | `fix/remove-sync-jobs-legacy-page` / `.worktrees/remove-sync-jobs-legacy-page` | `tts_erp_v2/api/v2/sync_status.py`; `tests/api/test_sync_job_management.py`; `tech-doc/{process-architecture.md,external-api.md}` | ready | c9c27f1ec649e19ced47950763c33b969508e3ec | dc56542dcbab15e68e1824eed739b9bba81f81bb | 2026-10-01 02:30:58 |
