# ACTIVE.md — tts-erp live lane registry

> This file contains **live work only**. Completed and abandoned history lives in Git history, not in this table.
> The canonical lifecycle is `tech-doc/agent-git-workflow.md`.

## Registry rules

- States: `draft` -> `active` -> `ready`; temporary `blocked (<reason>)` is allowed. Delete the row after merge or abandonment.
- `draft`, `active`, and `blocked` reserve their declared files. A `ready` branch is immutable and may be integrated by any session.
- A ready row records immutable `head_commit` and `synced_master` values. Later registry-only commits do not invalidate it; later non-registry master changes do.
- `owner(session)` must be the real Pi session UUID, never `本 session`.
- `updated/ready_at (UTC)` is the last state-change time; for `ready`, it is the FIFO queue timestamp.
- `handoff/ACTIVE.md` is coordination metadata and must not appear in the owned-file column.
- Edit registry state only in a clean master/coordination/integration worktree under `/tmp/tts-erp-active-md.lock`; lane worktrees do not carry registry-only edits.
- Shared hotspot ownership does not require waiting for the original session: exchange a focused patch, hand off ownership explicitly, or create a successor lane from the predecessor's ready commit.

| lane_id | Topic | owner(session) | branch/worktree | Owned files/directories | State | head_commit | synced_master | updated/ready_at (UTC) |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| feature/playwright-page-e2e | 建立页面级 Playwright E2E 测试集 | 01a0f871-dc44-729f-8747-2ac66a078db4 | `feature/playwright-page-e2e` / `.worktrees/playwright-page-e2e` | `package.json`; `package-lock.json`; `playwright.config.js`; `scripts/test_e2e.sh`; `scripts/select_e2e_suites.py`; `.gitignore`; `tests/e2e/`; `tests/e2e_browser/`; `tests/support/`; `tech-doc/browser-e2e-testing.md` | active | — | — | 2026-10-02T10:00Z |
| fix/sync-ops-reliability | 同步死锁/shop_fee_rate 停更/日志滚动三修 | 01a100eb-49c4-753a-aac7-9c0311d3ec5c | `fix/sync-ops-reliability` / `.worktrees/sync-ops-reliability` | `tts_erp_v2/jobs/tiktok/logistics.py`; `tts_erp_v2/sync_worker/job_runner.py`; `tts_erp_v2/sync_worker/scheduler.py`; `scripts/logrotate/`; `scripts/systemd/tts-erp-logrotate.*`; `tests/jobs_tiktok/test_logistics_job.py`; `tests/sync_worker/test_job_runner.py`; `tests/sync_worker/test_scheduler_jobs_coverage.py`; `tech-doc/process-architecture.md` | ready | 7ffef90 | 5020d2b | 2026-10-03T11:35Z |
