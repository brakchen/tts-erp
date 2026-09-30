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
| miaoshou-purchase-price-clean-job | 妙手采购价清洗定时同步 | 01a0efbb-1bcc-77b2-ade9-ac9dfdf3d0a0 | `feature/miaoshou-purchase-price-clean-job` / `/home/schan/tts-erp/.worktrees/miaoshou-purchase-price-clean-job` | `alembic/versions/0046_miaoshou_purchase_price_clean.py`; `tts_erp_v2/db/models/miaoshou.py`; `tts_erp_v2/db/models/__init__.py`; `tts_erp_v2/jobs/miaoshou/purchase_price_clean.py`; `tts_erp_v2/sync_worker/scheduler.py`; `tests/jobs_miaoshou/test_purchase_price_clean.py`; `tests/db/test_miaoshou_schema.py`; `tests/sync_worker/test_scheduler_miaoshou_reporting.py`; `tests/sync_worker/test_scheduler_jobs_coverage.py`; `scripts/configure_miaoshou_web_session.py`; `tests/scripts/test_configure_miaoshou_web_session.py`; `scripts/oneoff_migrate_0046_miaoshou_purchase_prices.sh`; `tests/scripts/test_oneoff_migrate_0046_miaoshou_purchase_prices.py`; `scripts/import_prod_to_test.sh`; `schema_tts_erp.sql`; `tech-doc/miaoshou-platform.md`; `tech-doc/miaoshou-purchase-price-import.md`; `tech-doc/architecture-overview.md`; `tech-doc/process-architecture.md`; `README.md`; `tech-doc/commands-reference.md`; `tts_erp_v2/reporting/manual_cost_lock.py`; `tts_erp_v2/api/v2/reporting.py`; `tests/api/test_manual_costs_single_tx.py` | blocked (等待 remove-unused-tables ready；0046 migration/scheduler/models 需作为 successor 重新对齐) | — | — | 2026-09-30 10:47:31 |
| sidebar-nav-reclassify | 侧边栏重新归类 | 01a0f13c-2917-760e-b7c9-6ac6fcdee98f | `style/sidebar-nav-reclassify` / `/home/schan/tts-erp/.worktrees/sidebar-nav-reclassify` | `tts_erp_v2/api/v2/pages.py`; `tests/api/test_pages.py` | active | — | — | 2026-09-30 10:22:13 |
