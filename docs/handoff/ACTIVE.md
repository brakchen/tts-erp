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
| spu-prd-rewrite | 按用户任务重写 SPU 产品评审草案 | 01a1164e-7563-740c-8f1d-6257c8fb9f2a | `chore/spu-prd-rewrite` / `/home/schan/tts-erp/.worktrees/spu-prd-rewrite` | `docs/spu-profit-deterioration/01-product-proposal.md` | draft | — | — | 2026-10-07T13:31:17Z |
| video-publish-design | TikTok 视频发布 v27 收口：0066 迁移护栏、全局设备槽互斥、测试入口统一 | 01a10600-8186-7668-b8bc-2ca85bd3f955 | `docs/video-publish-design` / `/home/schan/tts-erp/.worktrees/video-publish-design` | `alembic/env.py`, `alembic/versions/005[3-9]*`, `alembic/versions/006[0-6]*`, `scripts/test_isolated.sh`, `scripts/import_prod_to_test.sh`, `scripts/envsetup/install-test-deps.sh`, `scripts/systemd/tts-erp-publish.service`, `tts_erp_v2/access/`, `tts_erp_v2/accounts/pages.py`, `tts_erp_v2/api/v2/pages.py`, `tts_erp_v2/api/v2/video_publish.py`, `tts_erp_v2/app.py`, `tts_erp_v2/db/base.py`, `tts_erp_v2/db/models/`, `tts_erp_v2/publishing/`, `tts_erp_v2/static/{css,js}/video-publish.*`, `tts_erp_v2/templates/pages/video-publish.html`, `tests/publishing/`, `docs/{api,architecture,design,ops,schema}/` | active | — | `f1bc1a5222a42d4dbadf8ffde94af27744418d2c` | 2026-10-06T06:38:46Z |
| drop-miaoshou-dead-skips | 删除已退役的妙手 tdd skip 测试 | 01a10c89-d00c-7038-8bf3-5414c4e3d832 | `chore/drop-miaoshou-dead-skips` / `/home/schan/tts-erp/.worktrees/drop-miaoshou-dead-skips` | `tests/miaoshou/test_sync_routes.py`, `tests/miaoshou/test_sync_shops.py` | active |  |  | 2026-10-05T15:01:20Z |
