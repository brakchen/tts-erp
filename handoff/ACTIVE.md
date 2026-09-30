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
| fix-manual-cost-unregistered-prefill | 修复未登记筛选仍显示货源价 | 01a0f062-2785-760e-b7c9-6aa719abe2d4 | `fix/manual-cost-unregistered-prefill` / `.worktrees/manual-cost-unregistered-prefill` | `tts_erp_v2/static/js/console.js`; `tests/api/test_manual_costs_page_v2.py` | draft | — | — | 2026-09-30 06:49:57 |
| miaoshou-package-schema | 妙手包裹数据归位 miaoshou schema | 01a0efbb-1bcc-77b2-ade9-ac9dfdf3d0a0 | `fix/miaoshou-package-schema` / `/home/schan/tts-erp/.worktrees/miaoshou-package-schema` | `alembic/versions/0045_miaoshou_package_schema.py`; `tts_erp_v2/db/models/miaoshou.py`; `tts_erp_v2/db/models/__init__.py`; `tts_erp_v2/jobs/miaoshou/packages.py`; `tests/jobs_miaoshou/test_packages.py`; `tests/db/test_miaoshou_schema.py`; `tech-doc/miaoshou-platform.md`; `schema_tts_erp.sql`; `scripts/import_prod_to_test.sh`; `tech-doc/architecture-overview.md`; `tech-doc/process-architecture.md`; `README.md` | active | — | — | 2026-09-30 05:49:45 |
