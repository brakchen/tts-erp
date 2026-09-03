# Test split by domain

The pytest suite at `tests/` + `tests/` is tagged with **domain** +
**layer** markers so you can run a single business slice without paying
for the full ~10k LOC run.

## Why split?

- **Speed.** A typical `pytest` over everything takes minutes because
  the suite touches 36 migration tables + the FastAPI app + SDKs. A
  single domain (`domain_commerce`, `domain_api`, …) usually runs in
  single-digit seconds.
- **Signal-to-noise.** A failure in `domain_finance` shouldn't block
  iteration on `domain_linkage`.
- **Selective CI.** CI / pre-push hooks can run only the slice that
  the changed file belongs to (see "Mapping files to domains" below).

## Taxonomy

Every test file carries one or more of these markers via
`pytestmark = pytest.mark.<name>` at module level, so you don't need
to think about it per-test.

### Business domains

| Marker                  | What it covers                                                                  |
| ----------------------- | ------------------------------------------------------------------------------- |
| `domain_miaoshou`       | 妙手 SDK / callbacks / jobs / silent truncation                                  |
| `domain_commerce`       | TikTok 订单/商品同步 (`orders_job`, `order_detail_job`, `products_job`)          |
| `domain_finance`        | 财务/对账 (`finance_job`) + 利润/成本报表 (`profit_daily`, `cost_snapshots`)    |
| `domain_logistics`      | 物流追踪 (`logistics_job`)                                                       |
| `domain_after_sales`    | 退货/取消 (`after_sales_job` + `migrate_after_sales`)                            |
| `domain_linkage`        | 关联分析 (`linkage/*`)                                                            |
| `domain_reporting`      | 报表 (`profit` / `coverage` / `cost`)                                            |
| `domain_api`            | FastAPI 路由 / auth / middleware (`api/*`)                                       |
| `domain_proxy`          | 出站代理层 (`signing`, `token_service`)                                          |
| `domain_migration`      | v1→v2 数据迁移                                                                   |
| `domain_middleware`     | 中间件 (`access_log` 等)                                                          |
| `domain_sync`           | 同步 worker (`sync_worker/*`)                                                    |
| `domain_models`         | 模型 smoke (`test_models_smoke.py`)                                              |
| `domain_token_refresh`  | token 续期 (`jobs_token_refresh/*`)                                              |
| `domain_sdk`            | SDK 自身测试 (`miaoshou/endpoints/test_tools.py`)                                |
| `domain_e2e`            | 端到端 smoke，需要 live `:9877` 服务                                             |

### Layers (orthogonal)

| Marker              | Meaning                                                                |
| ------------------- | ---------------------------------------------------------------------- |
| `layer_unit`        | pure helpers, no DB / no fixtures (very fast, ≤ 100ms / test)          |
| `layer_integration` | uses DB / fixtures / `TestClient` (default)                            |
| `slow`              | ≥ 1 s per test (migration tests, e2e)                                  |
| `requires_db`       | needs `TTS_ERP_DB_URL` env var (most `tests/` tests)                |
| `requires_service`  | needs live `:9877` service (`domain_e2e`)                              |

## Common invocations

The recommended way is `scripts/test.sh` — it handles `pytest -m` quoting
and falls back to the parent repo's venv when running in a worktree.

```bash
# Default: everything except slow + requires_service (~unit + integration)
scripts/test.sh

# Only pure unit tests (sub-second)
scripts/test.sh unit

# Single business domain (prefix optional)
scripts/test.sh commerce
scripts/test.sh domain_miaoshou
scripts/test.sh finance
scripts/test.sh linkage

# Everything, including slow / e2e
scripts/test.sh all

# Coverage report
scripts/test.sh coverage
```

If you want to call `pytest` directly:

```bash
# Single domain, skip slow
.venv/bin/pytest -q -m "domain_commerce and not slow"

# All integration but no DB
.venv/bin/pytest -q -m "integration and not requires_db"

# Only layer_unit (fastest)
.venv/bin/pytest -q -m "layer_unit"

# Only slow / DB-bound tests
.venv/bin/pytest -q -m "slow or requires_db"
```

## Gotchas

- **`pytest -m ""` runs nothing.** An empty marker expression is treated
  as "no tests selected", not "all tests". If you mean "all tests", drop
  the `-m` flag entirely:

  ```bash
  # WRONG: collects 0 tests
  .venv/bin/pytest -m ""

  # RIGHT: collects everything
  .venv/bin/pytest
  .venv/bin/pytest -m "not slow and not requires_service"
  ```

- **`requires_db` vs `requires_service` are separate.** Most `tests/`
  tests need a Postgres reachable via `TTS_ERP_DB_URL`. The `domain_e2e`
  suite additionally needs `:9877` running locally. Run them with:

  ```bash
  scripts/test.sh e2e                    # :9877 must be up
  TTS_ERP_DB_URL=... scripts/test.sh migration   # PG must be reachable
  ```

- **Worktrees.** `chore/*` worktrees don't carry their own `.venv` — the
  script falls back to `/home/schan/tts-erp/.venv/bin/pytest` when
  `./.venv/bin/pytest` is missing.
- **Migration tests — ARCHIVED（2026-09-03）。** `tests/migration/` 和
  `scripts/migrate_v1_to_v2/` 已 git mv 到
  `tech-doc/_archive/migrate-v1-to-v2-2026-08-29/`,原位只留 README 指针（见
  `tests/MIGRATION_TESTS_ARCHIVED.md` 和 `scripts/MIGRATE_V1_TO_V2_ARCHIVED.md`）。
  迁移在 2026-08-29 切流时已执行完毕,这两个目录是 08-31 22h 全线停摆事故的根因,
  归档是 `tech-doc/test-domains.md:134` 原计划的"DOC 计划删除"动作。
  下面这段三层闸的描述作历史保留,方便复盘:
  - **NOT autouse**：`_ensure_migrations_applied` fixture opt-in;`scripts/test.sh migration`
    默认跑 dry-run,不重写生产库。
  - **默认 excluded（2026-08-31）**:`pyproject.toml addopts` 带 `-m 'not domain_migration'`,
    裸 `pytest` 跳整目录。原因:`test_reconcile.py` autouse 全量重放迁移对 PROD 写,
    在全量跑时与早 test 持锁冲突(无 `statement_timeout`),在 60% 处卡住整套。
  - **需显式 opt-in（2026-08-31）**:`TTS_ERP_ALLOW_PROD_MIGRATION=1`
    + `-m domain_migration` 才解开。三层闸:
    (1) `tests/migration/conftest.py` module-level skip;(2)
    `_ensure_migrations_applied` session fixture 再检;(3)
    `scripts.migrate_v1_to_v2.common.require_prod_guard(dry_run=False)` 抛 `SystemExit(2)`。
  - **SQL 锁定**:`migrate_shops._UPSERT_CREDENTIAL` 不再 `ON CONFLICT DO UPDATE`
    覆盖 `ciphertext` / `company_secret_ciphertext`(只 INSERT 写),
    由 `tests/migration/test_migrate_shops.py::TestUpsertCredentialSql`
    字符串断言锁定。08-30 incident 闭环:autouse fixture 把 v2 JSON-envelope
    `integration.credentials.ciphertext` 写成 legacy `Fernet(raw_access_token)` 格式,
    同步 worker 停摆 22h。

## Mapping files to domains

Quick lookup for "which slice do I run after editing X":

| You edited…                                 | Run                                  |
| ------------------------------------------- | ------------------------------------ |
| `tts_erp_v2/jobs/tiktok/orders.py`          | `scripts/test.sh commerce`           |
| `tts_erp_v2/jobs/tiktok/finance.py`         | `scripts/test.sh finance`            |
| `tts_erp_v2/jobs/tiktok/logistics.py`       | `scripts/test.sh logistics`          |
| `tts_erp_v2/jobs/tiktok/after_sales.py`     | `scripts/test.sh after_sales`        |
| `tts_erp_v2/jobs/miaoshou/*.py`             | `scripts/test.sh miaoshou`           |
| `tts_erp_v2/api/**/*.py`                    | `scripts/test.sh api`                |
| `tts_erp_v2/middleware/*.py`                | `scripts/test.sh middleware`         |
| `tts_erp_v2/reporting/*.py`                 | `scripts/test.sh reporting finance`   |
| `tts_erp_v2/linkage/*.py`                   | `scripts/test.sh linkage`            |
| `tts_erp_v2/proxy/*.py`                     | `scripts/test.sh proxy`              |
| `scripts/migrate_v1_to_v2/*.py`             | `scripts/test.sh migration`          |
| `miaoshou/miaoshou_signing.py`              | `scripts/test.sh miaoshou unit`      |

## Adding new tests

When you add a new test file, set the module-level marker so it joins
the right slice:

```python
import pytest

pytestmark = pytest.mark.domain_<your_domain>
# add layer markers only if you know the test is unit/slow/etc.
```

For files that mix multiple domains (rare), mark the file with the
dominant one and use per-test `pytest.mark.domain_*` for the outliers.
Keep `addopts = "-ra -q"` working — no stdout noise.

If your test needs the DB, also add `pytest.mark.requires_db`; if it
needs the live `:9877` service, add `pytest.mark.requires_service`. The
default `scripts/test.sh` invocation skips both.
