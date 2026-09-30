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
| dashboard-runtime-config-entry | Dashboard 运行配置入口 | 01a0f13c-c315-760e-b7c9-6ac80761f9dc | `feature/dashboard-runtime-config-entry` / `.worktrees/dashboard-runtime-config-entry` | `tts_erp_v2/api/v2/pages.py`; `tests/api/test_pages.py` | ready | `07712908c927c693ae5b7b903bcdb648a0d5fae2` | `11c8a13cb8e302c0d9dc2c3f40df5f57502a0ebc` | 2026-09-30 18:17:53 |
| spu-roi-delivered-exposure | 排除已送达未结算订单的全损预测暴露 | 01a0f0c0-2b88-760e-b7c9-6ac0585d1cdb | `fix/spu-roi-delivered-exposure` / `.worktrees/spu-roi-delivered-exposure` | `tts_erp_v2/analytics/spu_profitability/_formula_v10.py`; `tts_erp_v2/analytics/spu_profitability/_implementation.py`; `tts_erp_v2/analytics/spu_profitability/_types.py`; `tts_erp_v2/analytics/spu_roi.py`; `tests/analytics/test_spu_profitability_formula.py`; `tests/api/test_spu_roi_api.py`; `biz-doc/analytics/spu-roi-profit-calculation.md`; `tech-doc/analytics/roi-calc-prompt.md` | active | — | — | 2026-09-30 17:53:12 |
