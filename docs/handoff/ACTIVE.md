# ACTIVE.md — tts-erp live lane registry

> This file contains **live work only**. Completed and abandoned history lives in Git history, not in this table.
> The canonical lifecycle is `docs/guides/agent-git-workflow.md`.

## Registry rules

- States: `draft` -> `active` -> `ready`; temporary `blocked (<reason>)` is allowed. Delete the row after merge or abandonment.
- `draft`, `active`, and `blocked` reserve their declared files. A `ready` branch is immutable and may be integrated by any session.
- A ready row records immutable `head_commit` and `synced_master` values. Later registry-only commits do not invalidate it; later non-registry master changes do.
- `owner(session)` must be the real Pi session UUID, never `本 session`.
- `updated/ready_at (UTC)` is the last state-change time; for `ready`, it is the FIFO queue timestamp.
- `docs/handoff/ACTIVE.md` is coordination metadata and must not appear in the owned-file column.
- Edit registry state only in a clean master/coordination/integration worktree under `/tmp/tts-erp-active-md.lock`; lane worktrees do not carry registry-only edits.
- Shared hotspot ownership does not require waiting for the original session: exchange a focused patch, hand off ownership explicitly, or create a successor lane from the predecessor's ready commit.

| lane_id | Topic | owner(session) | branch/worktree | Owned files/directories | State | head_commit | synced_master | updated/ready_at (UTC) |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| video-publish-design | TikTok 视频发布 v27 迁移/锁/UI/文档收口 | 01a10600-8186-7668-b8bc-2ca85bd3f955 | `docs/video-publish-design` / `/home/schan/tts-erp/.worktrees/video-publish-design` | `docs/design/tiktok-video-publish.md` | ready | `cd994c0586f5e49cdc4db04025b047dc8bfbe80b` | `d7fa77c4a490feac9ab930b3a4ccd2cb1c7c0810` | 2026-10-06T11:55:00Z |
| spu-profit-deterioration-alert | SPU 利润劣化预警完整实现 | 01a10bdd-827c-7038-8bf3-53cab2fcffea | `feature/spu-profit-deterioration-alert` / `/home/schan/tts-erp/.worktrees/spu-profit-deterioration-alert` | `docs/design/spu-profit-deterioration-alert.md`, `docs/aegis/plans/2026-10-05-spu-profit-deterioration-alert.md`, `scripts/probe_spu_profit_deterioration_thresholds.py`, `tts_erp_v2/analytics/spu_deterioration_alert/`, `tts_erp_v2/api/v2/spu_deterioration_alert.py`, `tts_erp_v2/api/v2/pages.py`, `tts_erp_v2/api/v2/config.py`, `tts_erp_v2/access/_policy.py`, `tts_erp_v2/app.py`, `tts_erp_v2/db/models/analytics.py`, `tts_erp_v2/db/models/__init__.py`, `alembic/versions/*spu_deterioration_alert*.py`, `tts_erp_v2/runtime_config/`, `tts_erp_v2/jobs/spu_deterioration_alert.py`, `tts_erp_v2/sync_worker/scheduler.py`, `tts_erp_v2/templates/pages/spu-profit-deterioration.html`, `tts_erp_v2/static/js/spu-profit-deterioration.js`, `tts_erp_v2/static/css/spu-profit-deterioration.css`, `tests/**/*deterioration*`, `tests/api/test_runtime_config.py`, `tests/api/test_pages.py`, `tests/sync_worker/test_scheduler_jobs_coverage.py` | active |  |  | 2026-10-05T14:44:26Z |
| drop-miaoshou-dead-skips | 删除已退役的妙手 tdd skip 测试 | 01a10c89-d00c-7038-8bf3-5414c4e3d832 | `chore/drop-miaoshou-dead-skips` / `/home/schan/tts-erp/.worktrees/drop-miaoshou-dead-skips` | `tests/miaoshou/test_sync_routes.py`, `tests/miaoshou/test_sync_shops.py` | active |  |  | 2026-10-05T15:01:20Z |
