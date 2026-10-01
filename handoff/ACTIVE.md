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
| spu-roi-delivered-full-loss-rate | 预计全损率改用已送达退款样本 | 01a0f0c0-2b88-760e-b7c9-6ac0585d1cdb | `fix/spu-roi-delivered-full-loss-rate` / `.worktrees/spu-roi-delivered-full-loss-rate` | `tts_erp_v2/analytics/spu_profitability/{_formula_v10.py,_implementation.py,_types.py}`; `tts_erp_v2/analytics/spu_roi.py`; `tts_erp_v2/static/js/{spu-profitability-page.js,spu-roi.js,focused-spus.js}`; `tests/analytics/test_spu_profitability_formula.py`; `tests/api/{test_spu_roi_api.py,test_focused_spus.py}`; `biz-doc/analytics/spu-roi-profit-calculation.md`; `tech-doc/{analytics/roi-calc-prompt.md,external-api.md}` | active | — | — | 2026-10-01 00:10:00 |
| sidebar-css-external-pages | 修复外链 CSS 页面侧边栏注入 | 01a0f39a-7180-760e-b7c9-6b0a00a152c2 | `fix/sidebar-css-external-pages` / `.worktrees/sidebar-css-external-pages` | `tts_erp_v2/api/v2/pages.py`; `tests/api/test_pages.py` | ready | cc377d8ba04b04c0fa94656881514b9f887e0f25 | 0f321af932cde0efaec40c6381a9c375bfa655aa | 2026-10-01 01:49:16 |
