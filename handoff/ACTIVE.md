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
| runtime-config-rw-access | 运行配置统一读写权限 | 01a0f13c-c315-760e-b7c9-6ac80761f9dc | `fix/runtime-config-rw-access` / `.worktrees/runtime-config-rw-access` | `tts_erp_v2/access/_policy.py`; `tests/api/{test_runtime_config.py,test_pages.py}`; `tech-doc/{external-api.md,runtime-config-management.md}` | active | — | — | 2026-10-01 04:05:13 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| fix/spu-roi-default-tminus1 | SPU ROI 无日期缓存时默认 T-1，所有快捷范围截止 T-1 | 01a0edf5-a325-77b2-ade9-ac88e25bdd89 | `fix/spu-roi-default-tminus1` / `.worktrees/spu-roi-default-tminus1` | `tts_erp_v2/static/js/spu-roi.js`; `tts_erp_v2/static/js/spu-profitability-page.js`; `tests/api/test_spu_roi_api.py` | active | — | — | 2026-10-01T04:06Z |
| sync-jobs-readwrite-access | 定时任务管理访问权限改 readwrite | 01a0f39a-7180-760e-b7c9-6b0a00a152c2 | `fix/sync-jobs-readwrite-access` / `.worktrees/sync-jobs-readwrite-access` | `tts_erp_v2/api/v2/{sync_status.py,pages.py}`; `tests/api/{test_sync_job_management.py,test_pages.py}`; `tech-doc/{external-api.md,process-architecture.md}` | draft | — | — | 2026-10-01 04:04:05 |
