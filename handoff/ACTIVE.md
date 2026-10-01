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
| retry-tenacity-argparse | tenacity 替换简单退避 + argparse CLI | 01a0f628-3176-729f-8747-2aac7e599eab | `chore/retry-tenacity-argparse` / `.worktrees/retry-tenacity-argparse` | `pyproject.toml`; `tts_erp_v2/proxy/tts_shop/client.py`; `tts_erp_v2/jobs/miaoshou/purchase_price_clean.py`; `tts_erp_v2/sync_worker/main.py`; `tests/proxy/test_tts_shop_client.py`; `tests/jobs_miaoshou/test_purchase_price_clean.py`; `tests/sync_worker/test_main.py` | active | — | — | 2026-10-01 09:59:17 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
