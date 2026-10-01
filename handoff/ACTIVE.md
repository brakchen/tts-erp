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
| runtime-config-mobile-blank | 修复运行配置移动端白屏 | 01a0f13c-c315-760e-b7c9-6ac80761f9dc | `fix/runtime-config-mobile-blank` / `.worktrees/runtime-config-mobile-blank` | `tts_erp_v2/api/v2/pages.py`; `tests/api/test_pages.py` | ready | `b7864b7473f5cc9701b750d0d0a153125986d509` | `e625df77811cead8b8814419cf505b270823f5aa` | 2026-10-01 05:25:04 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| sync-job-trigger-feedback | 修复定时任务执行按钮反馈 | 01a0f39a-7180-760e-b7c9-6b0a00a152c2 | `fix/sync-job-trigger-feedback` / `.worktrees/sync-job-trigger-feedback` | `tts_erp_v2/{api/v2/admin.py,access/_policy.py,static/js/sync-jobs.js}`; `tests/{api/test_sync_job_management.py,access/test_policy.py}`; `tech-doc/{external-api.md,process-architecture.md}` | ready | 536d2c0d777d05c823a4ebed6767a3db11d0a4a7 | e625df77811cead8b8814419cf505b270823f5aa | 2026-10-01 05:22:20 |
| spu-roi-undelivered-terminal-risk | 未送达订单终局拒收全损预测 | 01a0f0c0-2b88-760e-b7c9-6ac0585d1cdb | `fix/spu-roi-undelivered-terminal-risk` / `.worktrees/spu-roi-undelivered-terminal-risk` | `tts_erp_v2/analytics/spu_profitability/{_formula_v10.py,_implementation.py,_types.py}`; `tts_erp_v2/analytics/spu_roi.py`; `tests/analytics/test_spu_profitability_formula.py`; `biz-doc/analytics/spu-roi-profit-calculation.md`; `tech-doc/analytics/roi-calc-prompt.md` | active | — | — | 2026-10-01 05:05:00 |
