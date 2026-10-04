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
| e2e-ui-render | e2e 补强：多视口溢出 / 全站字体栈 / 广告日明细排序 | 01a10506-4459-7668-b8bc-2c9c6958e703 | feature/e2e-ui-render | tests/e2e/*.py, tests/e2e/conftest.py, scripts/probe_ui_layout_audit.js, scripts/probe_ui_font_audit.js, scripts/ui_audit_mocks.js, tests/api/test_ad_daily.py, tts_erp_v2/static/css/tokens.css, tts_erp_v2/static/css/common.css, docs/guides/test-domains.md, docs/guides/browser-ui-layout-audit.md | active | — | — | 2026-10-04 05:08 |
