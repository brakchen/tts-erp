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
| spu-roi-delivered-exposure | 排除已送达未结算订单的全损预测暴露 | 01a0f0c0-2b88-760e-b7c9-6ac0585d1cdb | `fix/spu-roi-delivered-exposure` / `.worktrees/spu-roi-delivered-exposure` | `tts_erp_v2/analytics/spu_profitability/_formula_v10.py`; `tts_erp_v2/analytics/spu_profitability/_implementation.py`; `tts_erp_v2/analytics/spu_profitability/_types.py`; `tts_erp_v2/analytics/spu_roi.py`; `tests/analytics/test_spu_profitability_formula.py`; `tests/api/test_spu_roi_api.py`; `biz-doc/analytics/spu-roi-profit-calculation.md`; `tech-doc/analytics/roi-calc-prompt.md` | active | — | — | 2026-09-30 17:53:12 |
| runtime-config-management | 运行配置与凭证管理 | 01a0f13c-c315-760e-b7c9-6ac80761f9dc | `feature/runtime-config-management` / `.worktrees/runtime-config-management` | `alembic/versions/0049_runtime_config_management.py`; `tts_erp_v2/{api/v2/config.py,api/v2/pages.py,access/_policy.py,db/models/config.py,db/models/__init__.py,runtime_config/,static/js/runtime-configs.js,static/css/runtime-configs.css}`; `tests/{api/test_runtime_config.py,api/test_pages.py,runtime_config/}`; `tech-doc/runtime-config-management.md`; `tech-doc/external-api.md` | active | — | — | 2026-09-30 18:34:19 |
