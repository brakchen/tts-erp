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
| miaoshou-package-schema | 妙手包裹数据归位 miaoshou schema | 01a0efbb-1bcc-77b2-ade9-ac9dfdf3d0a0 | `fix/miaoshou-package-schema` / `/home/schan/tts-erp/.worktrees/miaoshou-package-schema` | `alembic/versions/0045_miaoshou_package_schema.py`; `tts_erp_v2/db/models/miaoshou.py`; `tts_erp_v2/db/models/__init__.py`; `tts_erp_v2/jobs/miaoshou/packages.py`; `tests/jobs_miaoshou/test_packages.py`; `tests/db/test_miaoshou_schema.py`; `tech-doc/miaoshou-platform.md`; `schema_tts_erp.sql`; `scripts/import_prod_to_test.sh`; `tech-doc/architecture-overview.md`; `tech-doc/process-architecture.md`; `README.md`; `tts_erp_v2/sync_worker/scheduler.py` | ready | `b43b0c8139cc09359b87842c6684bab933b23539` | `86af36b2b86059523657203df60e24b501aeff20` | 2026-09-30 07:30:00 |
| spu-roi-terminal-projection | SPU ROI 未结算订单终局预测 | 01a0f0c0-2b88-760e-b7c9-6ac0585d1cdb | `feature/spu-roi-terminal-projection` / `.worktrees/spu-roi-terminal-projection` | `tts_erp_v2/analytics/spu_roi.py`; `tts_erp_v2/analytics/spu_profitability/_formula_v10.py`; `tts_erp_v2/analytics/spu_profitability/_implementation.py`; `tts_erp_v2/analytics/spu_profitability/_types.py`; `tts_erp_v2/static/js/spu-profitability-page.js`; `tts_erp_v2/static/js/spu-roi.js`; `tts_erp_v2/api/v2/pages.py`; `tests/analytics/test_spu_profitability_formula.py`; `tests/api/test_spu_roi_api.py`; `biz-doc/analytics/spu-roi-profit-calculation.md`; `biz-doc/analytics/spu-roi-data-sources.md`; `tech-doc/analytics/roi-calc-prompt.md` | active | — | — | 2026-09-30 07:29:12 |
