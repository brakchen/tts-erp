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
| feature/focused-spus | 实现按店铺持久化“重点关注 SPU”：关注管理 API、盈利 focused selection、共享 PageProfile 页面 kernel 与重点关注页 | 01a0edf5-a325-77b2-ade9-ac88e25bdd89 | `feature/focused-spus` / `.worktrees/focused-spus` | `alembic/versions/0043_focused_spus.py`; `schema_tts_erp.sql`; `tts_erp_v2/reporting/focused_spus.py`; `tts_erp_v2/api/v2/focused_spus.py`; `tts_erp_v2/app.py`; `tts_erp_v2/access/_policy.py`; `tts_erp_v2/analytics/spu_profitability/`; `tts_erp_v2/analytics/spu_roi.py`; `tts_erp_v2/api/v2/pages.py`; `tts_erp_v2/static/js/{spu-profitability-page,spu-roi,focused-spus}.js`; `tts_erp_v2/static/css/{spu-roi,focused-spus}.css`; `tests/reporting/test_focused_spus.py`; `tests/analytics/test_spu_profitability_selection.py`; `tests/api/{test_focused_spus,test_spu_roi_api}.py`; `tech-doc/{external-api,analytics/focused-spus}.md` | active | — | — | 2026-09-29T17:11Z |
