# AGENTS.md — tts-erp

> Repository-wide instructions for coding agents. Read this file before changing anything.
> Detailed procedures live in `docs/`; this file keeps only rules that apply to most tasks.

## 1. Instruction scope and precedence

1. Explicit user instructions override repository instructions.
2. Rules in a more specific instruction file override this root file for that subtree.
3. Safety rules in this file always apply unless the user explicitly authorizes a documented exception.
4. If two repository instructions conflict, stop and ask instead of choosing the less restrictive rule.
5. Before working on a specialized area, read the matching document in §9.

## 2. Project overview

- Stack: Python 3.14, FastAPI/uvicorn, SQLAlchemy 2, psycopg3, PostgreSQL, APScheduler, MinIO, Fernet, systemd user units.
- Purpose: ingest TikTok Shop and Miaoshou data into a local analytics database, expose read-oriented APIs, and run scheduled synchronization.
- Main code: `tts_erp_v2/`.
- Tests: `tests/`.
- Operational scripts: `scripts/`.
- Architecture and active contracts: `docs/`.
- Work ownership registry: `docs/handoff/ACTIVE.md`.
- Live multi-agent coordination board: tower-do (`tower_do`, `tower_do_talk`, `tower_do_status`; see §8).

## 3. Non-negotiable safety boundaries

- Never run tests against `tts_erp`, `tts_erp_prod`, or any prod-shaped database name.
- Run tests only through `bash scripts/test_isolated.sh ...`; it must create an ephemeral clone for every run. There is no direct-runner or shared-database fallback.
- Agents must not select full-history, coverage, or archived migration suites under `docs/archive/migrate-v1-to-v2-2026-08-29/`. These paths include or restore production-touching migration behavior.
- Never execute `DELETE`, `TRUNCATE`, `DROP`, or irreversible `UPDATE` against production data without the documented guard and explicit human authorization.
- Never run `alembic upgrade` against production. Agents may validate migrations only through the isolated runner against `tts_erp_test_template` and its ephemeral `tts_erp_test_*` clones; production migration and restart are human-operated.
- Do not add an HTTP, CLI, migration, or job path that destroys source-of-truth facts (settlement, order, refund, ad, or config rows) without the shared guard from `tts_erp_v2.api.deps`.
- Recomputing a derived snapshot the same job owns (for example `analytics.spu_deterioration_alerts`, whose rows are rebuilt from source facts on the next run) is not a destructive path and does not take that guard; adding a release switch to it reintroduces the mismatch. If a new write path is unclear, ask before adding it.
- Credentials must go through `tts_erp_v2.proxy.token_service`; never query legacy `oauth_tokens` or decrypt `integration.credentials` directly.
- Do not reintroduce v1 `public.*` business tables or remove `public.fn_touch_updated_at()`.
- Do not add store-writing TikTok endpoints. This repository is a read-oriented analytics system.
- Do not change middleware registration order in `tts_erp_v2/app.py`.
- Never use repository-wide destructive Git commands such as `git reset --hard`, `git checkout -- .`, or `git clean -f`.

### `plugins/` boundary (chrome-plugins submodule)

- `plugins/chrome-plugins` is a **git submodule** pointing at `git@github.com:brakchen/chrome-plugins.git`. It holds the external Chrome-extension data collectors (ads-data-sync, order-data-sync, miaoshou-order-capture, monitor, tts-request-intercept) that feed this service's plugin intake endpoints.
- **The §3 production-safety rules do not apply inside `plugins/`.** Those extensions run in the user's browser, hold shop sessions, and have no database session. Do not run migrations, `alembic upgrade`, the isolated test runner, or any destructive SQL against that subtree.
- `plugins/` has its own TypeScript/WXT toolchain (`vitest`, `wxt`) and its own `.gitignore`. Run `bash scripts/test_isolated.sh ...` only from the repository root, against `tests/`.
- Python tooling is explicitly scoped away from it: `pyproject.toml` `[tool.ruff] exclude`, `pyrightconfig.json` `exclude`. pytest's `testpaths` and setuptools' package `include` are already explicit lists, so they never collect it. If you add a tool that scans the whole tree, exclude `plugins/` too.
- Each plugin is itself a repository with its own history and release cadence. **Never `git add` inside `plugins/`** — changes there belong to the plugin repositories, not to this lane. Commit a submodule pointer change only when a plugin's pinned commit is intentionally advanced.

Read `docs/guides/agent-safety.md` before any database, credential, migration, destructive, authentication, or production-adjacent change.

## 4. Domain invariants

### Credentials

```python
from tts_erp_v2.proxy.token_service import load_credentials

cred = load_credentials(session, provider="tiktok", external_account_id=shop_id)
```

### TikTok signing

- Signing implementation: `tts_erp_v2/proxy/tts_shop/signing.py`.
- `shop_cipher` stays in the query string.
- Sort signing keys alphabetically.
- Sign the raw `json.dumps(..., ensure_ascii=False)` body; never URL-encode the body.
- Read `docs/reference/tiktok-hmac-signing.md` before changing signing code.

### HTTP success semantics

- `2xx` means parsing and contracted persistence/processing completed successfully.
- Null response bodies, parser failures, and missing required response structures must not return `200`.
- HTTP status is the success/failure signal; do not restore `rowsWritten`, `logId`, or `data.status` as parallel status channels.
- Wire JSON/TypeScript uses `requestId`; Python and database code use `request_id`; headers use `x-request-id`.
- Read `docs/api/dumps-data-contract.md` before changing dumps endpoints or response envelopes.

### Authentication and time

- Production auth mode is `enforce`; roles are `readonly < readwrite < admin`.
- Store timestamps as aware UTC using `datetime.now(UTC)`, never `datetime.utcnow()`.
- Reporting dates are grouped by the shop's local timezone, not the UTC calendar day. Bridge nook currently uses Vietnam time (UTC+7).

### Frontend calculation boundary — IMPORTANT

Frontend code may perform **presentation-only simple calculations** from values already returned for display, including:

- choosing an up/down/flat arrow;
- calculating a displayed absolute difference;
- calculating a displayed percentage difference with explicit null/zero/negative-denominator handling;
- formatting, rounding, grouping, and other view-only transformations.

These results are non-authoritative display values. Name or document them as `display*` / “展示值” when they could be confused with a server decision field. They must not determine eligibility, severity, state, threshold matches, sample gates, persisted values, audit facts, business totals, or server query scope.

Complex or business-significant calculations belong on the backend, including ROI, net profit, currency or financial formulas, cross-row aggregation and deduplication, threshold evaluation, alert classification, and any percentage used for a business decision. Prefer Python `Decimal` / database `numeric` so precision and rounding policy have one owner. JavaScript `Number` uses binary floating-point; reproducing exact financial precision in every browser consumer adds cost and creates competing calculation paths.

The purpose of this boundary is not “the frontend may never calculate”. It is to keep one authoritative backend calculation exit while allowing inexpensive UI derivations. If a calculation affects a business result, is reused by multiple consumers, requires exact precision, or is hard to classify, implement and test it on the backend and let the frontend render the result.

## 5. Canonical commands

| Task | Command |
| --- | --- |
| Fast test suite (isolated DB) | `bash scripts/test_isolated.sh fast` |
| One test domain (isolated DB) | `bash scripts/test_isolated.sh <domain>` |
| Unit layer (isolated DB) | `bash scripts/test_isolated.sh unit` |
| Refresh isolated test template | `bash scripts/test_isolated.sh --refresh-template fast` |
| 测试前置依赖体检 / 安装 | `bash scripts/envsetup/install-test-deps.sh --check` / `sudo bash scripts/envsetup/install-test-deps.sh` |
| API restart | `bash restart.sh` |
| Sync-worker restart after `tts_erp_v2/jobs/`, `tts_erp_v2/sync_worker/`, or `tts_erp_v2/analytics/` changes (the worker imports job modules and their analytics deps at load time; without a restart the running process keeps the old code) | `systemctl --user restart tts-erp-sync.service` |
| Service status | `systemctl --user status tts-erp{,-sync}.service` |
| API logs | `journalctl --user -u tts-erp -n 50` |

- `scripts/test_isolated.sh` is the sole agent entry point: it clones `tts_erp_test_template` into a per-session ephemeral DB, injects `TTS_ERP_DB_URL_TEST`, runs the selected scope, then drops the clone.
- Refresh the template with `bash scripts/test_isolated.sh --refresh-template fast` when migrations/schema change or the template is missing/stale.
- If template cloning or its prerequisites fail, stop and repair them with `bash scripts/envsetup/install-test-deps.sh --check`; do not substitute another runner or database.
- Full command reference: `docs/guides/commands-reference.md`.

### 5.1 Reuse-first implementation policy

Before implementing non-trivial functionality, agents must first search for a maintained existing solution:

1. Search this repository for an existing equivalent.
2. Search official documentation, GitHub, and the relevant package ecosystem:
   - Python: PyPI
   - JavaScript/TypeScript: npm
3. Prefer, in order:
   - existing project code;
   - official SDKs and maintained libraries;
   - small, auditable adaptations of a proven upstream implementation;
   - a new in-house implementation only when no suitable reusable solution exists.

When a suitable library, SDK, or upstream implementation satisfies the request, adopt it directly without requesting human confirmation. Before adoption, verify compatibility with this repository, licence, maintenance status, security posture, and dependency footprint. Pin or constrain dependency versions appropriately. Record the upstream URL, package version or commit, and licence in relevant code comments or technical documentation.

Do not reimplement functionality that a suitable maintained dependency already provides. Do not add a dependency or copy upstream code for trivial logic where a small local implementation is clearer and safer.

## 6. Code conventions

- Use `from __future__ import annotations` and type hints in Python modules.
- Use SQLAlchemy 2 `select()`/`Session` patterns.
- Never run synchronous psycopg/database work inside an async handler.
- Prefix test data with `TEST_`.
- Put one-off scripts under `scripts/` with a descriptive `oneoff_`, `probe_`, `smoke_`, or `dump_` prefix.
- Put environment-preparation and installation scripts under `scripts/envsetup/` (e.g. `scripts/envsetup/install.sh`, `scripts/envsetup/install-test-deps.sh`). These are idempotent, safe to re-run, and support `--dry-run` / `--check`. Do not mix them with one-off data scripts (`scripts/oneoff_*`) or with deployment docs (`docs/ops/*.md`).
- Use internal primary keys such as `shop_pk` and `spu_pk` for API filters; do not assume `shop_id` is accepted.
- Keep naming conventional by layer: JSON/TypeScript camelCase, Python/SQL snake_case, HTTP headers lowercase-with-hyphens.
- Do not restate formatter, linter, or type-checker rules here; follow the configured tools.

## 7. Worktree, review, and completion rules

- Every task starts in a dedicated branch/worktree created from current `origin/master`. Do not develop task code in the main master worktree; use it only for short registry updates, or use a clean temporary coordination worktree when foreign WIP is present.
- Before the first task edit, register the lane in `docs/handoff/ACTIVE.md` with the real Pi session UUID and explicit file ownership. `docs/handoff/ACTIVE.md` is coordination metadata and must not be listed as a lane-owned file.
- Track task decomposition, ownership, dependencies, and cross-session progress on tower-do (§8). The board complements but never replaces the lane registry.
- `draft`/`active` lanes own their declared files. A `ready` lane is immutable and may be integrated by any session; session identity never blocks merge or cleanup.
- Ready lanes merge in `ready_at` order by default. Before entering `ready`, merge current `origin/master` into the lane, resolve conflicts, rerun required checks, commit, and push; record both lane HEAD and the synchronized master commit. If master later gains non-registry changes, repeat synchronization and validation. Commits changing only `docs/handoff/ACTIVE.md` do not invalidate the lane.
- Serialize the final master merge, post-merge validation, registry cleanup, and push with `/tmp/tts-erp-master-merge.lock`. Build the prospective master commit in a clean temporary integration worktree, merge with `--no-ff`, validate that exact commit, then push it to master without force.
- Do not modify or stash another lane's work. For shared hotspots, keep one writer and exchange a patch or create an explicit successor lane instead of waiting for the original session to return.
- Keep each writable worktree owned by one writer unless separate worktrees are used.
- Commit and push the task branch before merge or before pausing for user input. If the network is unavailable, commit locally and report that push remains pending.
- Commit messages use `feat/fix/chore/docs/style/merge` plus a concise Chinese description.
- In a worktree, never use `git add -A`, `git add .`, or `-A`-style wildcards for staging. They sweep in the worktree's `.venv` symlink, `.env*`, and other gitignored-but-not-protected local files, and the resulting commit will silently wipe a teammate's real venv on merge checkout. Always stage with explicit file paths (e.g. `git add tts_erp_v2/.../spu-roi.js tests/...`). If you used `-A`, run `git status` before `git commit` and unstage anything that is not your own change.

### 7.1 pi-lens 抑制注释的正确写法

`pi-lens-ignore` 必须写在被标记行的 **紧邻上一行**（`docs/dispositions.md`），
**行尾注释无效**；而且规则 id 必须**精确**，`— 原因` 后缀会让匹配失败：

```python
# ✅ 正确
# pi-lens-ignore: python-sql-injection
conn.execute(text("..."))
```

```python
# ❌ 无效（行尾）
conn.execute(text("..."))  # pi-lens-ignore: python-sql-injection
# ❌ 无效（带原因后缀，规则 id 匹配不上）
# pi-lens-ignore: python-sql-injection — bound params
conn.execute(text("..."))
```

仓库里历史遗留的抑制注释大多是行尾写法，因此从未生效。新写的请按上面的正确形式。

另：pi-lens 自带的 ruff 配置（`config/ruff/core.toml`，`select` 含 `I`）与仓库
`pyproject.toml` 不一致时，以 **能同时通过两者的写法** 为准；若两边都报而重排无法
同时满足，先用 `.venv/bin/ruff check --config <pi-lens 自带 core.toml> <file>` 核实
是否为误报。

Definition of done:

1. Run the narrowest relevant test command.
2. For code/test changes, run `bash scripts/test_isolated.sh fast`.
3. The default requirement is zero failures. If master has an explicitly recorded stable baseline, the change must introduce zero new stable failures; isolate and rerun failures once to distinguish flakes.
4. Update contracts and operational documentation affected by the change.
5. Confirm no secrets, production data, unrelated WIP, or staged foreign files are included.
6. Confirm the lane was synchronized with the master revision it integrated, then confirm the worktree and master are clean and the required branch/master pushes succeeded.
7. Reconcile tower-do: complete delivered tasks with `changedFiles`, leave honest blocker state, and reply to relevant messages/findings.

Detailed lifecycle, environment setup, conflict handling, and cleanup: `docs/guides/agent-git-workflow.md`.

## 8. 多会话 / 多 agent 协调（tower-do）

本仓库已安装 Pi 扩展 `tower-do`。它是所有 Pi 会话与子 agent 共享的实时协调板，记录任务、归属、依赖、留言和 findings。复杂开发任务默认优先在板上拆解；只要涉及多会话或多 agent 并行，就必须用板协调，避免撞文件、重复劳动和交接丢失。

`tower-do` 与 Git lane 各管一层，两者都要维护：

| 层 | 工具 / 文件 | 职责 |
| --- | --- | --- |
| 实时任务协调 | `tower_do` / `tower_do_talk` / `tower_do_status` | 任务拆解、认领、依赖、留言、findings、完成回执 |
| Git lane 登记 | `docs/handoff/ACTIVE.md` | branch/worktree、文件归属、ready 队列、合并顺序（§7） |

### 8.1 三个工具

- `tower_do_status`：只读查看任务、owner、依赖、留言、findings、在场会话、scope 冲突、板文件路径和当前 `revision`。开工前、交接前、结束前都要读。
- `tower_do`：原子更新整个任务板；用于创建、认领（`owner` + `in_progress`）、阻塞（`blocked` + `blockedBy`）和完成（`completed` + `changedFiles`）。
- `tower_do_talk`：给任务 owner、`tower` 或 `all` 发留言；用 `finding` 记录范围外的 `bug` / `improve` / `vuln` / `idea`。`inbox` 会确认已读；只想查看而不确认时用 `tower_do_status`。

### 8.2 开工前先拆解

- 三步以上的开发任务应先上板拆解，再改文件。一个任务应对应一次可独立提交、验证或交接的结果；不要把整个大特性塞进一个模糊任务。
- `key` 使用稳定、简短的小写标识；`subject` 写祈使句；`description` 只写耐久任务陈述，临时日志和证据放留言或 finding。
- 用 `scope` 声明可能修改的文件或 glob。并行任务的 scope 要尽量不相交；同一文件同一时刻只允许一个 writer。scope 冲突只是提示，不是锁；看到冲突后立即用 `tower_do_talk` 与 owner 协商。
- 有顺序关系的任务用 `dependsOn`；依赖未完成时不得把后继任务置为 `in_progress` 或 `completed`。等待外部动作或非任务标识时，用 `blocked` + `blockedBy` 明确写出等待对象。
- 大特性优先拆成可并行的设计、后端、前端、测试、文档等子任务。用户允许委派且 scope 独立时，尽量交给不同 agent / 会话并行执行；需要串行的部分用 `dependsOn`，不要靠口头约定。

### 8.3 认领、写板与完成纪律

- 开始工作前先读 `tower_do_status`，然后把 `owner` 与 `status: in_progress` 在同一次写入中设置；不要修改其他 owner 的任务，应用 `tower_do_talk` 联系对方。
- 每次 `tower_do` 写入都要携带最近一次读取或写入返回的 `baseRevision`。遇到 stale 拒绝时重新读板、合并同伴更新后再提交。
- `tower_do` 是任务级全量替换：`tasks` 数组必须复述所有要保留的 key；现有任务的未改字段可省略。写前先看完整 board，不能因默认视图折叠而漏掉同伴任务或历史完成回执。
- owner 保护适用于任务所有字段。owner 自身在该任务上连续 6 小时无活动时，才可按工具契约接管未完成任务：第一次写入只改 `owner`，第二次再更新内容或状态。
- 任务只有在实现和验证都完成后才能标记 `completed`，且同一次写入必须附实际改动的仓库相对路径 `changedFiles`；失败、未验证或部分完成时保持 `in_progress` 或诚实标记 `blocked`。
- 离开、暂停或最终回复前再次读板并对账：自己的任务都有明确状态，相关留言已回复，认领的 finding 已完成、拒绝或附原因延期。

### 8.4 多 agent 并行与交接

- 每个可写 worktree 只安排一个 writer；tower-do 的 task `scope` 应与 `docs/handoff/ACTIVE.md` 的 lane 文件归属一致。板负责实时协作，§7 的 worktree、测试、提交、推送和合并规则仍是最终约束。
- 把 `tower_do_status` 返回的 board 文件路径交给参与任务的子 agent，作为共享的 file-as-state；替子 agent 记账时使用其真实 id 作为 `as`。
- agent 完成子任务后必须回写状态与 `changedFiles`，父会话再汇总依赖、运行整体验证并收尾。不要只在聊天里说“完成”而让板保持过期状态。
- 发现不属于当前 scope 的问题时，优先创建 finding 并通知 owner；不要顺手修改别人的 lane。认领 finding 后要走 `accepted` → `done` / `rejected` / `snoozed` 生命周期，并在关闭或延期时写明原因。

## 9. Required context by task

| When touching | Read first |
| --- | --- |
| Architecture, credentials, database layout | `docs/architecture/architecture-overview.md` |
| Destructive operations, migrations, production, auth | `docs/guides/agent-safety.md` |
| Tests, fixtures, test database, baseline failures | `docs/guides/agent-testing.md` |
| Worktrees, handoff, merge, push, conflict handling | `docs/guides/agent-git-workflow.md` |
| Dumps endpoints and HTTP envelopes | `docs/api/dumps-data-contract.md` |
| TikTok signing | `docs/reference/tiktok-hmac-signing.md` |
| External endpoints, roles, pagination, schemas | `docs/api/external-api.md` |
| SPU profit-deterioration product, design, implementation, or tests | `docs/spu-profit-deterioration/01-product-proposal.md`, then the matching numbered document in that directory |
| Process/service architecture | `docs/architecture/process-architecture.md` |
| Miaoshou integration | `docs/reference/miaoshou-platform.md` |
| Known recurring failures | `docs/guides/common-bugs.md` |

`docs/archive/` is historical reference only. Do not restore or execute archived code.
