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
| retire-monthly-ad-sync | 废弃 monthly 广告同步 | 01a0f062-8327-760e-b7c9-6aa8e3a32bcc | `chore/retire-monthly-ad-sync` / `.worktrees/retire-monthly-ad-sync` | `alembic/versions/0047_retire_monthly_ad_sync.py`; `schema_tts_erp.sql`; `tts_erp_v2/{api/v2/analytics.py,plugin/ads/{__init__,domain,repository}.py,db/models/plugin.py}`; `tests/{api/test_analytics_coverage.py,api/test_analytics_dumps_v4.py,plugin/ads/test_repository.py}`; `tech-doc/{external-api.md,database-maintenance-todos.md,dumps-data-contract.md}`; `tech-doc/analytics/daily-sync-with-coverage.md`; `tech-doc/enums/ad-raw-log-kind.md`; `setup/analytics-sync.md`; `biz-doc/analytics/spu-roi-data-sources.md`; `README.md`; `CHANGELOG.md` | blocked (等待采购价清洗 migration 0047) | — | — | 2026-09-30 13:45:56 |
