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
| tests-schema-tidy | 散落测试归入 tests/（e2e 子目录）+ schema 快照迁入 docs/schema/ 并按当前结构重生成 | 01a101e0-0c40-753a-aac7-9c0d9eb9f82e | `tests-schema-tidy` / `.worktrees/tests-schema-tidy` | `test_e2e.py`；`test_e2e_finance.py`；`tests/`；`docs/schema/schema_tts_erp.sql`；`docs/schema/schema_storage.sql`；`docs/schema/`；`scripts/regen_schema.py`；`scripts/test.sh`；`pyproject.toml`；`docs/guides/commands-reference.md`；`docs/guides/test-domains.md`；`docs/architecture/process-architecture.md`；`README.md`；`CHANGELOG.md`；其余文件仅改路径字符串 | active | — | — | 2026-10-03T23:20Z |
