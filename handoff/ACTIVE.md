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
| fix/miaoshou-purchase-orders-ewm | 接入妙手 EWM 采购单列表；独立实现先完成，scheduler 注册通过热点 owner patch 或后继 lane 收口 | 01a0edb6-1095-77b2-ade9-ac79527c212d | `fix/miaoshou-purchase-orders-ewm` / `.worktrees/miaoshou-purchase-orders-ewm` | `tts_erp_v2/jobs/miaoshou/purchase_orders.py`; `tests/jobs_miaoshou/test_purchase_orders*.py`; `tests/sync_worker/test_scheduler_{miaoshou_reporting,jobs_coverage}.py`; `tech-doc/miaoshou-platform.md` | draft | — | — | 2026-09-29T15:16Z |
| fix/spu-roi-pagination | SPU ROI 分页改造：分页区统一每页选择、跳页和前后页 | 01a0ed94-c7be-77b2-ade9-ac70bd714357 | `fix/spu-roi-pagination` / `.worktrees/spu-roi-pagination` | `tts_erp_v2/api/v2/pages.py`; `tts_erp_v2/static/js/spu-roi.js`; `tts_erp_v2/static/css/spu-roi.css`; `tests/api/test_spu_roi_api.py` | draft | — | — | 2026-09-29T16:12Z |
| feature/focused-spus | 按店铺持久化“重点关注 SPU”；后端先行，UI 作为分页 lane 的显式后继 | 01a0edf5-a325-77b2-ade9-ac88e25bdd89 | `feature/focused-spus` / `.worktrees/focused-spus` | `alembic/versions/0043_focused_spus.py`; `tts_erp_v2/api/v2/focused_spus.py`; `tts_erp_v2/app.py`; `tts_erp_v2/middleware/auth.py`; `tests/api/test_focused_spus.py`; `schema_tts_erp.sql`; `tech-doc/external-api.md`; `tech-doc/analytics/focused-spus.md` | blocked (UI successor of `fix/spu-roi-pagination`; independent backend may continue) | — | — | 2026-09-29T16:35Z |
| chore/parallel-lane-merge-workflow | 多 session 并行流程：ready FIFO、同步 `origin/master`、master 集成锁和跨 session 接力 | 01a0edec-f33a-77b2-ade9-ac86f85e2a1d | `chore/parallel-lane-merge-workflow` / `.worktrees/parallel-lane-merge-workflow` | `AGENTS.md`; `tech-doc/agent-git-workflow.md`; `handoff/ACTIVE.md`（本次迁移例外） | active | — | — | 2026-09-29T16:20Z |
