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
| runtime-config-schema-override | Schema手动覆盖交互 | 01a0f13c-c315-760e-b7c9-6ac80761f9dc | `fix/runtime-config-schema-override` / `.worktrees/runtime-config-schema-override` | `tts_erp_v2/static/{js/runtime-configs.js,css/runtime-configs.css}`; `tests/api/test_runtime_config.py` | ready | `b40db191cfd8754149b5975b1809862a53a685cb` | `18fdf30d32bfe749ac15dfe0f2226f81dc3f2861` | 2026-10-01 12:57:40 |
| --- | spu-tabulator-deep-qa | SPU Tabulator 深度复审与浏览器QA | 01a0f628-3176-729f-8747-2aac7e599eab | `fix/spu-tabulator-deep-qa` / `.worktrees/spu-tabulator-deep-qa` | `tts_erp_v2/static/js/spu-profitability-page.js`; `tts_erp_v2/static/css/spu-roi.css`; `tts_erp_v2/templates/pages/spu-profitability.html`; `tests/api/test_spu_roi_api.py`; `tests/analytics/test_spu_profitability_sorting.py`; `scripts/probe_spu_tabulator_deep_qa.js` | active | — | — | 2026-10-01 13:01:46 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| spu-roi-completed-order-forecast | 已完结订单全损率驱动预计利润与ROI | 01a0f0c0-2b88-760e-b7c9-6ac0585d1cdb | `fix/spu-roi-completed-order-forecast` / `.worktrees/spu-roi-completed-order-forecast` | `tts_erp_v2/analytics/spu_profitability/{_formula_v10.py,_implementation.py,_types.py}`; `tts_erp_v2/analytics/spu_roi.py`; `tests/analytics/test_spu_profitability_formula.py`; `biz-doc/analytics/spu-roi-profit-calculation.md`; `tech-doc/analytics/roi-calc-prompt.md` | active | — | — | 2026-10-01 13:05:00 |
