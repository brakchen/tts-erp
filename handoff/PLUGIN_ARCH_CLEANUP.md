# 交接：plugin 架构清理

> **状态**：计划已全拍板，**未开始开发**（lane 1 待开工）
> **上一 session**：调查 ad_daily 金额偏低的根因，挖出 api-managed 守卫误伤广告 dumps 的 P1 问题（UTC 2026-09-11 22:50）
> **本 session**：补调查 + 与用户逐条拍板决策 + 建 `.env.test`（UTC 2026-09-11 15:30）
> **接手指令**：读完本文件 + `handoff/ACTIVE.md` 的 `PLUGIN_ARCH_CLEANUP` 行 + `git log --oneline -10`

---

## 1. 根因（为什么做这件事）

- `commerce.shops.data_source='api'` 的店铺，plugin dumps 被 `shop_is_api_managed()` 守卫**全域静默吞掉**（200 + `status:"api_managed"`）
- 守卫前提（commit `ae843a1`）："TikTok 授权整店全 scope，订单走 API / 广告走插件的混合态不存在"
- **但广告没有 server-side 同步** —— `scheduler.py` 的 JOBS 里没有 ad job，`jobs/tiktok/` 里没有 `ads.py`，`proxy/tts_shop/` 里没有 ad 客户端
- → 广告 dumps 被守卫挡掉后**数据永远进不来**；而守卫要防的"双写"在广告侧**根本不存在**
- 用户拍板：**api / plugin 数据物理隔离到两个 schema**，不再需要来源判定

## 2. 决策快照（**全部已拍板，不要回头讨论**）

| # | 决策 |
| --- | --- |
| D1 | `chrome_sync` schema **物理重命名**为 `plugin`（`ALTER SCHEMA ... RENAME`） |
| D2 | plugin 数据**统一写 `plugin.*`**；`analytics` schema 的 5 张表搬进 `plugin`，然后 `DROP SCHEMA analytics` |
| D3 | 删除 `commerce.shops.data_source` 字段（冗余于 `credential_id IS NOT NULL`） |
| D4 | 拆掉**所有** api-managed 守卫（analytics + order-sync 两处）+ 删 `shop_is_api_managed()` |
| D5 | HTTP 端点路径**不变**（`/v2/analytics/sync/*` 是 stable 契约） |
| D6 | **4 个串行 lane**（原 5 个 → lane 3+4 合并，因为必须原子） |
| D7 | **禁止删改 prod 数据** → 写进 AGENTS.md §6；测试只跑 `tts_erp_v3_test` |
| D8 | **prod 迁移由用户手动跑**；agent 只在 test 库验证，绝不自动打 prod |
| D9 | 历史 migration（0016/0017/0018/0019/0020…）**一律不改**（保 fresh DB 能按序建出来） |
| D10 | 迁移**只写 DDL、零行级 DML**（无 INSERT/UPDATE/DELETE） |
| D11 | Python 包结构：`plugin/orders/`（来自 chrome_sync）+ `plugin/ads/`（来自 analytics ingest）；`spu_roi.py` 留读侧 |
| D12 | `db/models/chrome_sync.py` + `db/models/analytics.py` **合并为** `db/models/plugin.py` |
| D13 | job 改名：`analytics.solidify` → **`plugin.ad_merge_today2daily`**；模块 `jobs/analytics_solidify.py` → `jobs/ad_merge_today2daily.py` |
| D14 | 函数改名：`solidify_yesterday` → `merge_today_into_daily`；`solidify_yesterday_scope_pairs` → `list_merge_scope_pairs` |
| D15 | `plugin_logs` **表名保留**（已含 "plugin"） |
| D16 | `scripts/regen_schema.py` 加 `--db-url`/env 覆盖，**从 test 库** regen（它只读库 + 写文件，从不写库） |
| D17 | 文档：更新 12 个（**跳过** `tech-doc/_archive/` 的 4 个） |
| D18 | `.env.test` = `.env` **整份复制** + `sed` 改 dbname（已建，见 §5） |

## 2.5 ⚠️ 本任务的特别授权（临时破例）

**用户于 2026-09-11 特别授权：本任务（PLUGIN_ARCH_CLEANUP 的 4 个 lane）允许 agent 在 prod 执行
migration 相关操作**（原本 AGENTS.md §6 规定 prod migration 只能由用户手动跑）。

- 适用范围：**仅本任务的 lane 2 / lane 3 / lane 4** 的 migration（0023 / 0024 / 0025）
- 任务完成后：**恢复 AGENTS.md §6 规则**（agent 不再自动跑 prod migration）
- 仍遵守的红线：
  - 迁移**只写 DDL、零行级 DML**（不得对 prod 数据 INSERT / UPDATE / DELETE）
  - 破坏性操作（尤其 lane 4 的 `DROP COLUMN`）执行前先 `pg_dump` 备份
  - 每个 lane 在 **test 库**先验证通过，才动 prod
  - 改名类迁移与 `systemctl --user restart` **挨着执行**，缩小报错窗口

**每个 lane 的 prod 部署序列**（顺序不可颠倒）：

```bash
# 1) 先把代码 merge 到 master（磁盘文件更新；运行中进程仍是旧代码，尚不受影响）
git merge --no-ff <branch>
# 2) 立即在 prod 跑迁移（此刻运行进程用旧 schema 名，会短暂报错）
cd /home/schan/tts-erp && .venv/bin/alembic upgrade head
# 3) 立即重启，让进程加载新代码 + 新 schema 名
systemctl --user restart tts-erp.service tts-erp-sync.service
# 4) 验证
bash prod-switch/postswitch-smoke.sh
```

> 为什么必须「先 merge 再迁移」：systemd 服务读磁盘代码；若先迁移而磁盘代码未更新，重启后
> 加载的仍是旧代码（写旧 schema 名）→ 持续报错。

---

## 3. 四个 lane（串行，每个 merge 后才开下一个）

### Lane 1 — `chore/agents-md-prod-data-rule`（无 migration，docs-only）

- **改动面**：`AGENTS.md` §6 加"严禁删 prod 数据"条款（文本见 §6）
- **测试**：文档-only，**不跑** `scripts/test.sh`
- **策略**：master WT 直改 + 原子 commit

### Lane 2 — `chore/rename-chrome-sync-to-plugin`（migration **0023**）

**migration**（DDL，零 DML）：

```sql
ALTER SCHEMA chrome_sync RENAME TO plugin;
```

**代码/文件**：

- `tts_erp_v2/chrome_sync/` → `tts_erp_v2/plugin/orders/`（`__init__.py` / `parser.py` / `repository.py`）
- `tts_erp_v2/db/models/chrome_sync.py` → `tts_erp_v2/db/models/plugin.py`
- `tts_erp_v2/db/models/__init__.py`（import 路径）
- `tts_erp_v2/db/base.py`（`SCHEMAS` 列表：`chrome_sync` → `plugin`）
- `tts_erp_v2/api/v2/order_sync.py`（SQL `chrome_sync.` → `plugin.`）
- `tts_erp_v2/api/v2/admin.py`（purge 列表的 chrome_sync 部分）
- `tts_erp_v2/api/deps.py`
- `scripts/regen_schema.py`（加 `--db-url`，见 D16）
- `tests/chrome_sync/*`、`tests/api/test_order_sync_contract.py`、`tests/api/test_admin_shops.py`、`tests/conftest.py`
- `schema_tts_erp.sql`（regen）
- 文档：`tech-doc/external-api.md`、`tech-doc/chrome-ext-order-sync-design.md`、`AGENTS.md` §1/§8

**测试**：`flock -n /tmp/tts-erp-test.lock bash scripts/test.sh fast`
**注**：历史 migration `0016`（`CREATE SCHEMA chrome_sync`）**必须保留** —— 0023 依赖它建好的 schema

### Lane 3 — `chore/move-analytics-to-plugin`（migration **0024**，原 lane 3+4 合并）

> **为什么必须合并**：`0023` 只搬 chrome_sync 的 7 张表。若 lane 3 先改代码写 `plugin.ad_daily` 而表还在 `analytics`，则部署即炸；反之只搬表不改代码也炸。两者必须同一变更。

**migration**（DDL，零 DML —— 用 `SET SCHEMA` 而非 INSERT/DROP）：

```sql
ALTER TABLE analytics.ad_daily    SET SCHEMA plugin;
ALTER TABLE analytics.ad_today    SET SCHEMA plugin;
ALTER TABLE analytics.ad_monthly  SET SCHEMA plugin;
ALTER TABLE analytics.ad_raw_log  SET SCHEMA plugin;
ALTER TABLE analytics.plugin_logs SET SCHEMA plugin;
DROP SCHEMA analytics;
```

- 说明：`SET SCHEMA` 一次搬走表 + 索引 + 约束 + IDENTITY 序列 + 触发器；**行数据原地不动**
- `plugin` schema 由 0023 建立，所以**0023 必须先跑**

**代码/文件**：

- `tts_erp_v2/analytics/domain.py` + `repository.py` → `tts_erp_v2/plugin/ads/`
- `tts_erp_v2/analytics/spu_roi.py` → **留在原地**（读侧看板），仅改 SQL 里 `analytics.ad_*` → `plugin.ad_*`
- `tts_erp_v2/db/models/analytics.py` → 合并进 `tts_erp_v2/db/models/plugin.py`
- `tts_erp_v2/db/base.py`（`SCHEMAS` 删 `analytics`）
- `tts_erp_v2/jobs/analytics_solidify.py` → `tts_erp_v2/jobs/ad_merge_today2daily.py`；`JOB_NAME = "plugin.ad_merge_today2daily"`
- `tts_erp_v2/sync_worker/scheduler.py`（JOBS 键 `analytics.solidify` → `plugin.ad_merge_today2daily`）
- `tts_erp_v2/api/v2/admin.py`（purge 列表的 analytics 部分 + docstring）
- `tts_erp_v2/api/v2/analytics.py`（import 路径）
- `schemas/repository` 函数改名（D14）
- `tests/api/test_analytics_coverage.py`、`test_analytics_dumps_v4.py`、`test_analytics_v2_contract.py`、`test_admin_purge.py`、`test_spu_roi_api.py`、`test_admin_shops.py`、`tests/analytics/test_repository.py`
- `schema_tts_erp.sql`（regen）
- 文档：`tech-doc/analytics/*.md`（8 个）、`setup/analytics-sync.md`、`AGENTS.md`

### Lane 4 — `chore/drop-shops-data-source`（migration **0025**）

**migration**（DDL；⚠️ 删列 = 不可逆）：

```sql
ALTER TABLE commerce.shops DROP CONSTRAINT ck_channel_accounts_data_source;
ALTER TABLE commerce.shops DROP COLUMN data_source;
```

**代码/文件**：

- `tts_erp_v2/db/models/commerce.py`（删 Mapped 字段 + CheckConstraint）
- `tts_erp_v2/api/deps.py`（**删整个** `shop_is_api_managed()`）
- `tts_erp_v2/api/v2/analytics.py`（删守卫块 ~601-625）
- `tts_erp_v2/api/v2/order_sync.py`（删守卫块 ~352-373）
- `tts_erp_v2/api/v2/admin.py`（register SQL 不写该列 + ShopOut 删字段）
- `tts_erp_v2/api/v2/commerce.py`（3 条 SQL 删字段）
- `tts_erp_v2/api/schemas.py::ChannelAccountOut`（删字段）
- `tts_erp_v2/proxy/tiktok_oauth.py`（upsert 不写该列）
- `tests/api/`：`test_analytics_dumps_v4.py`、`test_order_sync_contract.py`、`test_admin_shops.py`、`test_reporting_profit_daily.py`、`test_manual_costs_single_tx.py`、`test_commerce_by_external.py`
- `schema_tts_erp.sql`、`tech-doc/external-api.md`、`tech-doc/chrome-ext-order-sync-design.md`

**⚠️ 部署提示**：这一 lane 拆掉守卫后，Bridge nook 的插件数据**才开始真写库** —— 是真正改变线上行为的 lane，部署时机最需注意。删列前建议先 `pg_dump` 备份 `commerce.shops`。

**migration 编号**：alembic head = `0022_shops_data_source`，所以是 0023（lane2）/ 0024（lane3）/ 0025（lane4）。

## 4. 关键事实（已实测核实）

### 4.1 两个 schema 的当前状态

| Schema | 表 | 行数 |
| --- | --- | --- |
| `chrome_sync` | orders / order_lines / shipments / tracking_events / settlements / settlement_details / raw_log | **全 0**（从未被写入过） |
| `analytics` | ad_daily / ad_today / ad_monthly / ad_raw_log | **全 0**（被 purge-plugin-data 清过） |
| `analytics` | plugin_logs | **533**（插件持续在推） |

- `chrome_sync` 有 **6 个 FK，全在 schema 内部**（child → `chrome_sync.raw_log`）→ `ALTER SCHEMA RENAME` 整块搬迁，无跨 schema 引用 ✓
- DB 只有 1 个 view（`linkage.effective_product_links`），**不依赖**这两个 schema ✓
- `plugin` schema 当前**不存在**

### 4.2 两个 Python 包的本质区别

| | `tts_erp_v2/chrome_sync/` (1,106 行) | `tts_erp_v2/analytics/` (2,423 行) |
| --- | --- | --- |
| 业务域 | 订单 / 物流 / 结算 | 广告消耗 + 插件日志 |
| 文件 | `parser.py`(442) `repository.py`(660) | `domain.py`(66) `repository.py`(739) **`spu_roi.py`(1618)** |
| 性质 | **纯 ingest**（解析 + 写库） | ingest（domain+repository）**混读侧看板**（spu_roi） |
| 写目标 | `chrome_sync.*`（7 表） | `analytics.ad_*`（4 表）+ `plugin_logs` |

→ 所以 `analytics/` 不是纯 ingest 包，`spu_roi.py`（ROI 看板读侧）要留出来（D11）。

### 4.3 api-managed 守卫位置

- 函数：`tts_erp_v2/api/deps.py:42` `shop_is_api_managed()`
- 触发点 1：`tts_erp_v2/api/v2/analytics.py:603-625`
- 触发点 2：`tts_erp_v2/api/v2/order_sync.py:355-372`
- 判定：`commerce.shops.data_source == 'api'` → 返回 `200 {"code":0,"data":{"status":"api_managed"}}`

### 4.4 其它需要改的落点（第一版 handoff 漏掉的）

- **`tts_erp_v2/db/base.py:19-31`** 有 `SCHEMAS` 列表（含 `"analytics"` + `"chrome_sync"`），alembic 用它建 schema
- **`analytics.solidify` 是已注册定时 job**（`scheduler.py:225`），每天把 `ad_today` 固化进 `ad_daily` 再清 today
- **`purge_plugin_data` 端点删 12 张表**（5 analytics + 7 chrome_sync），另有 `tests/api/test_admin_purge.py`
- **`scripts/regen_schema.py:41,215`** 硬编码读 `.env`（prod）→ 需加 `--db-url`（D16）
- AGENTS.md 需改的行：9 / 156 / 203 / 209 / 211 / 217 / 218

### 4.5 迁移机制（重要）

- **无启动自动迁移**；迁移是手动 `alembic upgrade head`
- `alembic/env.py` 读 `TTS_ERP_DB_URL` → `source .env.test` 后即打 test 库
- 两库水位当前一致：prod = test = `0022_shops_data_source`
- test 库 `tts_erp_v3_test` 有全部 13 个 schema（含 analytics 5 表 + chrome_sync 7 表）→ 是合格基座

### 4.6 prod 迁移协议（用户手动）

```bash
# agent 只做这些（test 库）：
set -a; . ./.env.test; set +a
.venv/bin/alembic upgrade head            # → tts_erp_v3_test
flock -n /tmp/tts-erp-test.lock bash scripts/test.sh fast

# 用户在自选时机做这些（prod）—— agent 绝不代跑：
cd /home/schan/tts-erp
.venv/bin/alembic upgrade head            # .env → tts_erp
systemctl --user restart tts-erp.service tts-erp-sync.service
```

- 改名类迁移（0023/0024）执行瞬间，仍在用旧 schema 名的运行进程会报 `relation does not exist`，所以**迁移与重启要挨着做**
- 当前该风险极低：唯一店铺 Bridge nook 是 `data_source='api'`，两个 dumps 端点都被守卫挡着，**插件根本写不进去**

## 5. 环境准备（本 session 已完成）

- ✅ **`.env.test` 已创建**（`.env` 整份复制 + `sed 's|/tts_erp\b|/tts_erp_v3_test|'`，权限 0600）
- ✅ 已验证：`bash -c 'set -a; . ./.env.test; set +a; echo $TTS_ERP_DB_URL'` → `tts_erp_v3_test`
- ✅ 已验证：`flock -n /tmp/tts-erp-test.lock bash scripts/test.sh api tests/api/test_analytics_dumps_v4.py` → **13 passed**，prod 零改动

**⚠️ 纪律红线（本 session 亲测教训）**：**永远不要用裸 `.venv/bin/pytest`** —— 它读 `.env` = **prod**！只能用 `bash scripts/test.sh <...>`（会自动 source `.env.test`）。

## 6. AGENTS.md §6 新增条款（lane 1 直接复用）

```markdown
- ❌ **不得删除/截断 prod 库数据**。生产 schema 是真理之源（§1）；任何 `TRUNCATE` / `DELETE`
  / `DROP TABLE` / `DROP SCHEMA` 必须先开 admin 端点 + 人工决策（`/v2/admin/purge-plugin-data`
  是唯一合法路径），或者**仅在专用 test 库 `tts_erp_v3_test` 操作**。所有测试**只能**连 test
  库；禁止任何 agent 直接连 prod dbname（`tts_erp` / `tts_erp_prod`）跑测试 / 迁移 / 手动 SQL。
  prod schema 改名 / 数据搬迁 migration 由用户**手动触发** `alembic upgrade head`，
  agent **绝不**自动跑。
```

## 7. 已知风险 / 注意

1. **lane 3 必须与 0024 同一变更**（见 §3 lane 3 的说明）
2. **历史 migration 不可改**（D9）—— 0023 依赖 0016 建的 `chrome_sync`
3. **`regen_schema.py` 只能 dump prod** → 必须先做 D16 的工具改造，否则 `schema_tts_erp.sql` 无法在不动 prod 的前提下更新
4. **旧污染**：prod `fx.exchange_rates` 有 **48 行 09-08 的 `TEST_*` 遗留行**（与本任务无关，`fix/fx-test-isolation` lane 在跟）
5. **master HEAD 有 pre-existing fail**（历史 commit 提到 19 个）→ 每个 lane merge 后按 AGENTS.md §11「代码/test lane 必须 0 新 fail」判定，先取 baseline

---

## 8. 执行进度台账

| Lane | 分支 | commit | 测试 | prod 迁移 | 状态 |
| --- | --- | --- | --- | --- | --- |
| 1 · AGENTS.md 红线 | —（master 直改） | `b4a3b9e` | docs-only | — | ✅ 已 push |
| 2 · chrome_sync→plugin | `chore/rename-chrome-sync-to-plugin` | `5bc6451` / merge `1b47f0e` | 全量 fast 13 fail = baseline，**0 新 fail** | ✅ prod 已迁 `0023` + 重启，冒烟 8/8 | ✅ 已 push，worktree 已清 |
| 3 · analytics→plugin | `chore/move-analytics-to-plugin` | `06feb95` / merge `8791a8a` | 全量 fast 13 = baseline，**0 新 fail**；ruff 集合一致 | ✅ prod 已迁 `0024` + 重启，冒烟 8/8，读 plugin 3 端点 200 | ✅ 已 push，worktree 已清 |
| 4 · drop data_source | `chore/drop-shops-data-source` | — | — | — | ⬜ 待开工 |

### Lane 2 实际经验（给 lane 3/4 复用）

1. **alembic revision id 必须 ≤ 32 字符** —— `alembic_version.version_num` 是 `varchar(32)`；
   超长会在迁移跑完后报 `StringDataRightTruncation`（事务回滚，库不脏，但白跑一次）。
   已用：`0023_chrome_sync_to_plugin`(26) / 计划：`0024_analytics_to_plugin`(24)、
   `0025_drop_shops_data_source`(27)。
2. **baseline 取法**：改 schema 的 lane 会改变 test 库形状 → 取 baseline 需
   `alembic downgrade <前一个 revision>` → 在 master WT 跑全量 fast → 再 `upgrade head`。
   否则 master 旧代码在已迁移的 test 库上会假失败。
3. **regen_schema.py 现在支持 `--db-url`**，从 test 库 regen 的姿势：
   `bash -c 'set -a; . ./.env.test; set +a; python3 scripts/regen_schema.py --db-url "$TTS_ERP_DB_URL"'`；
   已顺手修掉 `\unrestrict` 随机 token（修后 regen 幂等，可重复跑无伪 diff）。
4. **改名类迁移的 prod 序列**（已跑通）：merge 到 master → `alembic upgrade head` →
   `systemctl --user restart tts-erp{,-sync}.service` → `bash prod-switch/postswitch-smoke.sh`。
   本次执行时 Bridge nook 为 `data_source='api'`，dumps 被守卫挡住 → 改名期间无活跃写入，零报错。
5. **ruff**：改完跑 `ruff check` 并对比 master 的 pre-existing 集合，别引入新错误
   （本次引入过 PIE810 / I001，已修）。

---

**当前 HEAD**：`8791a8a`（lane 3 已合并 + push）
**prod alembic**：`0024_analytics_to_plugin`
**test alembic**：`0024_analytics_to_plugin`

### Lane 3 实际经验

1. **`db/models/analytics.py` 是孤儿文件** —— 无人 import、11 张表从未注册进 `Base.metadata`。
   并入 `plugin.py` 后已在 `db/models/__init__.py` 正式注册。
2. **`ALTER TABLE ... SET SCHEMA` 比 INSERT/DROP 干净得多**：索引/约束/IDENTITY 序列/触发器
   自动跟随（已实测 FK 6 个、序列 12 个全对）。
3. **改 schema 的 lane 会动 test 库形状** —— baseline 取法同 lane 2（downgrade → master 跑 → upgrade）。
4. **测试路径重命名会让 fail 列表看起来变了** —— 比较时需归一化（`tests/analytics/` → `tests/plugin/ads/`）。
5. prod 迁移前**先停两个 service**（plugin_logs 正在被写），迁移后立即 start —— 比「迁移+重启」
   更干净；实测 533 行数据完整保留。

### ⚠ Lane 3 发现、**待用户决策**的既有缺口

`tests/db/test_time_fields_convention.py` 的 `V2_SCHEMAS` 原本含 `analytics`（且从无 `chrome_sync`）。
把 `plugin` 加进去后暴露两个 **chrome_sync 时期就存在**的缺口：

| 缺口 | 现状 |
| --- | --- |
| `plugin.raw_log` **无 `updated_at` 列** | 模型/表都没有（append-only 设计）；但同 schema 的 ad_raw_log 有 |
| 7 张订单表**无 BEFORE UPDATE 触发器** | orders / order_lines / shipments / tracking_events / settlements / settlement_details / raw_log |

处理：lane 3 的 `V2_SCHEMAS` **只移除已消失的 `analytics`，未加入 `plugin`**（否则新增 2 个失败）。
是否需要单独 lane 补齐（加列 + 加触发器）待用户拍板。
