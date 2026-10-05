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
| spu-profitability-contract | 按飞书权威口径重写盈利开发契约 | 01a10af5-a470fa3c | `docs/spu-profitability-contract` / `/home/schan/tts-erp/.worktrees/spu-profitability-contract` | `docs/business/spu-profitability.md` | ready | `d7f9c76f4cb8b49a54b094a5b21ce7dc4196d477` | `bf88edc6640651c377ddbee8cd321b5b9570d7e0` | 2026-10-05T09:11:19Z |
