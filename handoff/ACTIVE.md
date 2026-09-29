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
| feature/focused-spus | 按店铺持久化“重点关注 SPU”；后端先行，UI 作为分页 lane 的显式后继 | 01a0edf5-a325-77b2-ade9-ac88e25bdd89 | `feature/focused-spus` / `.worktrees/focused-spus` | `alembic/versions/0043_focused_spus.py`; `tts_erp_v2/api/v2/focused_spus.py`; `tts_erp_v2/app.py`; `tts_erp_v2/middleware/auth.py`; `tests/api/test_focused_spus.py`; `schema_tts_erp.sql`; `tech-doc/external-api.md`; `tech-doc/analytics/focused-spus.md` | blocked (UI successor of merged `fix/spu-roi-pagination`; owner must claim released UI files before editing) | — | — | 2026-09-29T16:35Z |
| feature/spu-roi-total-orders-column | SPU ROI 主表在有效销售与有效单量之间新增总单量列 | 01a0edde-2481-77b2-ade9-ac82afb99e0a | `feature/spu-roi-total-orders-column` / `.worktrees/spu-roi-total-orders-column` | `tts_erp_v2/api/v2/pages.py`; `tts_erp_v2/static/js/spu-roi.js`; `tests/api/test_spu_roi_api.py` | draft | — | — | 2026-09-29T16:45Z |
