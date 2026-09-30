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
| fix/focused-spus-selector-order | 将重点关注 SPU 多选框移动到汇总大盘前面 | 01a0edf5-a325-77b2-ade9-ac88e25bdd89 | `fix/focused-spus-selector-order` / `.worktrees/focused-spus-selector-order` | `tts_erp_v2/static/js/focused-spus.js`; `tts_erp_v2/static/css/focused-spus.css`; `tests/api/test_focused_spus.py` | active | — | — | 2026-09-30T01:43Z |
| spu-roi-manual-cost-only | SPU ROI 仅使用人工采购成本 | 01a0eff1-24a9-77b2-ade9-aca12edebf11 | `fix/spu-roi-manual-cost-only` / `.worktrees/spu-roi-manual-cost-only` | `tts_erp_v2/analytics/spu_profitability/_implementation.py`; `tts_erp_v2/analytics/spu_roi.py`; `tts_erp_v2/static/js/spu-profitability-page.js`; `tests/api/test_spu_roi_api.py`; `biz-doc/analytics/spu-roi-data-sources.md`; `biz-doc/analytics/spu-roi-profit-calculation.md`; `tech-doc/analytics/spu-profitability-module-decisions.md`; `tech-doc/analytics/spu-real-roi-dashboard.md`; `tech-doc/external-api.md` | active | — | — | 2026-09-30T01:42:51Z |
