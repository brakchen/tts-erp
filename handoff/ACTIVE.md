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
| feature/playwright-page-e2e | 建立页面级 Playwright E2E 测试集 | 01a0f871-dc44-729f-8747-2ac66a078db4 | `feature/playwright-page-e2e` / `.worktrees/playwright-page-e2e` | `package.json`; `package-lock.json`; `playwright.config.js`; `scripts/test_e2e.sh`; `scripts/select_e2e_suites.py`; `.gitignore`; `tests/e2e/`; `tests/e2e_browser/`; `tests/support/`; `tech-doc/browser-e2e-testing.md`; `tech-doc/agent-testing.md`; `tech-doc/commands-reference.md` | active | — | — | 2026-10-02T10:00Z |
| fix/ad-daily-adblock-class-names | 修复广告日明细页被广告拦截规则隐藏 | 01a0fac1-0c77-7532-8dce-f7ebdc6fa670 | `fix/ad-daily-adblock-class-names` / `.worktrees/ad-daily-adblock-class-names` | `tts_erp_v2/static/css/ad-daily.css`; `tts_erp_v2/static/js/ad-daily.js`; `tts_erp_v2/api/v2/ad_daily.py`; `CHANGELOG.md` | ready | 7cf12f8878bdece8deefd8fac685256897e7f240 | 600934788ea1a695fcdc218d3a777c689837383a | 2026-10-02T05:24 |
| fix/import-prod-tmp-leak | 修复 import_prod_to_test.sh 临时 dump 泄漏到 postgres 容器 /tmp | 01a0fac8-1e5b-7532-8dce-f7f7941f6d83 | `fix/import-prod-tmp-leak` / `.worktrees/import-prod-tmp-leak` | `scripts/import_prod_to_test.sh` | draft | — | — | 2026-10-02T05:19Z |
