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
| sidebar-nav-reclassify | 侧边栏重新归类 | 01a0f13c-2917-760e-b7c9-6ac6fcdee98f | `style/sidebar-nav-reclassify` / `/home/schan/tts-erp/.worktrees/sidebar-nav-reclassify` | `tts_erp_v2/api/v2/pages.py`; `tests/api/test_pages.py` | ready | 48b57535e811d09d78f2a0b03244e7338bc405a8 | 331a030b4dbf04c646bc145cba686372d5466e23 | 2026-09-30 13:50:00 |
| retire-monthly-ad-sync | 废弃 monthly 广告同步 | 01a0f062-8327-760e-b7c9-6aa8e3a32bcc | `chore/retire-monthly-ad-sync` / `.worktrees/retire-monthly-ad-sync` | `alembic/versions/0047_retire_monthly_ad_sync.py`; `schema_tts_erp.sql`; `tts_erp_v2/{api/v2/analytics.py,plugin/ads/{__init__,domain,repository}.py,db/models/plugin.py}`; `tests/{api/test_analytics_coverage.py,api/test_analytics_dumps_v4.py,plugin/ads/test_repository.py}`; `tech-doc/{external-api.md,database-maintenance-todos.md,dumps-data-contract.md}`; `tech-doc/analytics/daily-sync-with-coverage.md`; `tech-doc/enums/ad-raw-log-kind.md`; `setup/analytics-sync.md`; `biz-doc/analytics/spu-roi-data-sources.md`; `README.md`; `CHANGELOG.md` | blocked (等待采购价清洗 migration 0047) | — | — | 2026-09-30 13:45:56 |
| miaoshou-purchase-price-clean-v2 | 妙手采购价清洗定时同步（0047 successor） | 01a0f062-8327-760e-b7c9-6aa8e3a32bcc | `feature/miaoshou-purchase-price-clean-v2` / `.worktrees/miaoshou-purchase-price-clean-v2` | `alembic/versions/0047_miaoshou_purchase_price_clean.py`; `tts_erp_v2/{db/models/miaoshou.py,db/models/__init__.py,jobs/miaoshou/purchase_price_clean.py,sync_worker/scheduler.py,reporting/manual_cost_lock.py,api/v2/reporting.py}`; `tests/{jobs_miaoshou/test_purchase_price_clean.py,db/test_miaoshou_schema.py,sync_worker/test_scheduler_miaoshou_reporting.py,sync_worker/test_scheduler_jobs_coverage.py,api/test_manual_costs_single_tx.py}`; `scripts/{configure_miaoshou_web_session.py,oneoff_migrate_0047_miaoshou_purchase_prices.sh,import_prod_to_test.sh}`; `tech-doc/{miaoshou-platform.md,miaoshou-purchase-price-import.md,architecture-overview.md,process-architecture.md,commands-reference.md}`; `schema_tts_erp.sql`; `README.md` | active | — | — | 2026-09-30 13:45:56 |
