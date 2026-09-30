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
| isolated-test-hardening | 加固隔离测试脚本与文档 | 01a0f3bd-328d-729f-8747-2a8dddf4961f | `chore/isolated-test-hardening` / `.worktrees/isolated-test-hardening` | `scripts/test_isolated.sh`; `AGENTS.md`; `tech-doc/agent-testing.md`; `tech-doc/agent-safety.md`; `tests/conftest.py` | active |  |  | 2026-09-30 20:28:00 |
| sync-job-page-fixes | 修复定时任务管理页面逻辑 | 01a0f39a-7180-760e-b7c9-6b0a00a152c2 | `fix/sync-job-page-fixes` / `.worktrees/sync-job-page-fixes` | `tts_erp_v2/api/v2/pages.py`; `tts_erp_v2/static/js/sync-jobs.js`; `tests/api/test_pages.py`; `tests/api/test_sync_job_management.py`; `tech-doc/external-api.md` | ready | b2f91701193b4787a087a01ba3ef55fff264cfbb | 7af69b59de0f4c8df7eb9777e4ad5825a2f04e12 | 2026-09-30 19:59:47 |
