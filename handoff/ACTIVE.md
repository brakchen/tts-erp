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
| runtime-config-schema-preview | 运行配置实时Schema预览 | 01a0f13c-c315-760e-b7c9-6ac80761f9dc | `feat/runtime-config-schema-preview` / `.worktrees/runtime-config-schema-preview` | `tts_erp_v2/{api/v2/config.py,static/js/runtime-configs.js,static/css/runtime-configs.css}`; `tests/api/test_runtime_config.py` | ready | `738a894120d7c79bfc94445d015ef16ef9c559fa` | `4f3d66fa977bee4d2b0f8778c0832891c31ee8c8` | 2026-10-01 11:28:35 |
| spu-table-tabulator | SPU 盈利页表格迁移 Tabulator | 01a0f628-3176-729f-8747-2aac7e599eab | `feat/spu-table-tabulator` / `.worktrees/spu-table-tabulator` | `tts_erp_v2/static/js/spu-profitability-page.js`; `tts_erp_v2/static/css/spu-roi.css`; `tts_erp_v2/templates/pages/spu-profitability.html`; `tts_erp_v2/static/vendor/tabulator*.min.*`; `tests/api/test_spu_roi_api.py`; `tests/analytics/test_spu_profitability_sorting.py`; `tests/api/test_focused_spus.py` | active | — | — | 2026-10-01 11:11:32 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
