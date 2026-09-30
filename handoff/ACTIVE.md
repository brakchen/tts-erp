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
| runtime-config-management | 运行配置与凭证管理 | 01a0f13c-c315-760e-b7c9-6ac80761f9dc | `feature/runtime-config-management` / `.worktrees/runtime-config-management` | `alembic/versions/0049_runtime_config_management.py`; `tts_erp_v2/{api/v2/config.py,api/v2/pages.py,access/_policy.py,db/models/config.py,db/models/__init__.py,runtime_config/,static/js/runtime-configs.js,static/css/runtime-configs.css}`; `tests/{api/test_runtime_config.py,api/test_pages.py,runtime_config/}`; `tech-doc/runtime-config-management.md`; `tech-doc/external-api.md` | active | — | — | 2026-09-30 18:34:19 |
| sync-job-management | 定时任务管理页面 | 01a0f39a-7180-760e-b7c9-6b0a00a152c2 | `feature/sync-job-management` / `.worktrees/sync-job-management` | `tts_erp_v2/api/v2/sync_status.py`; `tts_erp_v2/api/v2/admin.py`; `tts_erp_v2/sync_worker/scheduler.py`; `tts_erp_v2/static/js/sync-jobs.js`; `tts_erp_v2/static/css/sync-jobs.css`; `tests/api/test_sync_job_management.py`; `tests/sync_worker/test_scheduler_job_controls.py`; `tech-doc/external-api.md`; `tech-doc/process-architecture.md` | ready | 9a4f5c05e419085a163833c35840a6794bc6784f | e73c5a0c7266760f35186917d1fddc7aae0f2b28 | 2026-09-30 19:09:16 |
| spu-roi-delivered-unsettled-card | 大盘新增未结算已送达订单量 | 01a0f0c0-2b88-760e-b7c9-6ac0585d1cdb | `feature/spu-roi-delivered-unsettled-card` / `.worktrees/spu-roi-delivered-unsettled-card` | `tts_erp_v2/analytics/spu_profitability/_implementation.py`; `tts_erp_v2/analytics/spu_profitability/_types.py`; `tts_erp_v2/static/js/spu-profitability-page.js`; `tts_erp_v2/static/js/spu-roi.js`; `tts_erp_v2/static/js/focused-spus.js`; `tests/api/test_spu_roi_api.py`; `tests/api/test_focused_spus.py`; `biz-doc/analytics/spu-roi-profit-calculation.md` | ready | fa45c45a7491abdc3182ad0376cb02310190311b | d8b3e44a8f3725122104844a3b42ea4d5139cfab | 2026-09-30 19:19:55 |
