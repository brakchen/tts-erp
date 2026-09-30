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
| isolated-test-template | 模板库隔离测试脚本 | 01a0f3bd-328d-729f-8747-2a8dddf4961f | `chore/isolated-test-template` / `.worktrees/isolated-test-template` | `scripts/test_isolated.sh`; `AGENTS.md`; `tech-doc/agent-testing.md`; `tech-doc/commands-reference.md`; `tech-doc/agent-safety.md`; `tests/api/test_spu_roi_api.py`; `tests/db/test_miaoshou_schema.py` | ready | 386e0025a2cb1b960a7baaafc1aaeb745886ce85 | 253fbbe7e51134fa0d2aeda69ce76ed520c83032 | 2026-09-30 20:07:30 |
