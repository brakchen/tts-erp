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
| video-publish-design | TikTok 视频发布最终契约实现 | 01a10600-8186-7668-b8bc-2ca85bd3f955 | `docs/video-publish-design` / `/home/schan/tts-erp/.worktrees/video-publish-design` | `alembic/versions/005[3-9]*`, `alembic/versions/0060_video_publish_invariants.py`, `tts_erp_v2/publishing/`, `tts_erp_v2/api/v2/video_publish.py`, `tts_erp_v2/db/models/publishing.py`, `tts_erp_v2/static/{css,js}/video-publish.*`, `tts_erp_v2/templates/pages/video-publish.html`, `tests/publishing/`, `docs/{api,design,ops}/` | ready | `c6212e0b80281f89d0b564926f56fdcd691f0917` | `f1bc1a5222a42d4dbadf8ffde94af27744418d2c` | 2026-10-05T06:46:41Z |
