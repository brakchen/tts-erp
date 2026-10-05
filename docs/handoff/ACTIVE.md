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
| video-publish-design | TikTok 视频发布技术方案与 UI 交互 | 01a10600-8186-7668-b8bc-2ca85bd3f955 | `docs/video-publish-design` / `/home/schan/tts-erp/.worktrees/video-publish-design` | `docs/design/tiktok-video-publish.md` | ready | `a6925197db2d9cee6a1450ed4a2ecdc80731eb67` | `d7fa77c4a490feac9ab930b3a4ccd2cb1c7c0810` | 2026-10-04T09:47:24Z |
| spu-roi-sync-freshness | SPU ROI 页头四类数据同步时间（插队：前序 ready lane video-publish-design 远端 HEAD efc3e4f 与登记 head a6925197 不一致，无法按记录合并） | 01a10af1-f1e5-7038-8bf3-53b5efe8e029 | `feature/spu-roi-sync-freshness` / `/home/schan/tts-erp/.worktrees/spu-roi-sync-freshness` | `tts_erp_v2/api/v2/sync_status.py`, `tts_erp_v2/sync_worker/job_runner.py`, `tts_erp_v2/sync_worker/scheduler.py`, `tts_erp_v2/templates/pages/spu-profitability.html`, `tts_erp_v2/static/js/spu-profitability-page.js`, `tts_erp_v2/static/css/spu-roi.css`, `tests/api/test_sync_freshness_api.py`, `tests/browser/conftest.py`, `scripts/probe_ui_layout_audit.js`, `scripts/probe_ui_font_audit.js`, `docs/api/external-api.md` | ready | `84fb5953535da90bdca8206a417d04094f521204` | `260e6058157e66a6c4140be16816a8f5aa0786ec` | 2026-10-05T07:49:35Z |
