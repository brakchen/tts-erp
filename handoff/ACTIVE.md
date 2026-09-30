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
| spu-roi-final-projection-model | SPU ROI最终预测模型与指标展示 | 01a0f0c0-2b88-760e-b7c9-6ac0585d1cdb | `fix/spu-roi-final-projection-model` / `.worktrees/spu-roi-final-projection-model` | `tts_erp_v2/analytics/spu_profitability/_formula_v10.py`; `tts_erp_v2/analytics/spu_profitability/_implementation.py`; `tts_erp_v2/analytics/spu_profitability/_types.py`; `tts_erp_v2/analytics/spu_roi.py`; `tts_erp_v2/api/v2/pages.py`; `tts_erp_v2/static/js/spu-profitability-page.js`; `tts_erp_v2/static/js/spu-roi.js`; `tts_erp_v2/static/js/focused-spus.js`; `tests/analytics/test_spu_profitability_formula.py`; `tests/api/test_spu_roi_api.py`; `tests/api/test_focused_spus.py`; `biz-doc/analytics/spu-roi-profit-calculation.md`; `tech-doc/analytics/roi-calc-prompt.md` | active | — | — | 2026-09-30 14:04:45 |
| miaoshou-price-store-match | 按店铺匹配写独立采购价格表 | 01a0efbb-1bcc-77b2-ade9-ac9dfdf3d0a0 | `fix/miaoshou-price-store-match` / `/home/schan/tts-erp/.worktrees/miaoshou-price-store-match` | `alembic/versions/0047_miaoshou_purchase_price_clean.py`; `tts_erp_v2/{db/models/miaoshou.py,db/models/__init__.py,jobs/miaoshou/purchase_price_clean.py}`; `tests/{jobs_miaoshou/test_purchase_price_clean.py,db/test_miaoshou_schema.py}`; `scripts/{import_prod_to_test.sh,oneoff_migrate_0047_miaoshou_purchase_prices.sh}`; `tech-doc/{miaoshou-platform.md,miaoshou-purchase-price-import.md}` | draft | — | — | 2026-09-30 15:01:30 |
