# `.env.test` 两个键改指对测试基线的影响核实

日期：2026-11-11
范围：Chief 派工的四项交付 —— ① 隔离测试实跑证据、② 改前/改后失败 id 双向对比、③ 模板库双路径污染检查、④ 台账 owner 重指。
测试机：HP desk（`ssh hp-desk`，schan@192.168.1.2），代码 `/home/schan/tts-erp`。
全程未改生产配置、未重启服务、未删库、未推 master。

---

## 结论摘要

1. **这次改指对隔离测试基线没有影响。** 改前侧（`.env.test` 指向已被删除的 `tts_erp_v3_test`）与改后侧（指向 `tts_erp_test_template`）各跑 **353 条用例，全部通过**；失败 id 集合 `comm -13` 双向差集都是空。两侧用例清单逐字相同。
2. **原因是 base 库名只提供连接身份，不提供被访问的库。** `derive` 只解析 base URL 的 server/user/password/port，克隆源固定是模板库，测试进程连的是一次性克隆库 —— base 指向哪个库名在整条链路上都不影响连到哪个库。
3. **隔离运行没有写模板库。** 跑前跑后模板库 91 张业务表的行数指纹**逐表一致**（digest `b634b248520a436f278f9fbe2d03410d1d17467bb3103ec7e2ae8f640f13a17c`），alembic 修订停在 `0076_media_packs` 未动；同一套查询打在生产库上有非零行（正对照），排除"查不到所以是 0"。
4. **`--refresh-template` 分支与模板库之间没有自导入回路。** 刷新源取自 `TTS_ERP_DB_URL_PROD_SOURCE` 或 `.env`，**从不取 `.env.test`**；同库护栏在重建路径（`recreation()`）与 legacy 路径上都会拒绝 SRC==DST。HP desk 上那些源路径全部不存在 ⇒ 该分支本就无法自助执行。
5. **台账 owner 重指不需要再做 —— 它早已落在"要进 master 的那条线"上。** 远端 `chore/arch-debt-ledger-to-master` 的 tip 是 `7b32b9af`，其 `base.production_ref = 1e6c77ee`（= `production_tree` 实际 HEAD），owner 判据实测 **19/24**，契约检查 exit 0。
   本次额外产出一个**单一独立 commit `24477270`**（分支 `chore/arch-debt-ledger-repoint-20261111`），其三个重指文件与 `7b32b9af` **逐字节一致** —— 即同一份重指的单提交等价形式，**未推 master**。
   ⚠️ 核实过程中我一度给出过相反结论（"重指没落到 to-master 线上"），那是**本地 remote-tracking ref 陈旧**造成的假象，已更正 —— 详见 §4.5。

### 一句话

> 这次改指是**连接身份层面的改名**，对测试基线零影响；台账的重指也已经在正确的线上，本次只补了一份等价的单提交形式作为可复核件。

---

## 0.1 核实过程中我自己踩的两个坑（都已在正文里更正，留档）

| 坑 | 现象 | 真相 | 教训 |
|---|---|---|---|
| 改前侧复现用占位凭据 | "改前"侧 1.471 秒就失败，报 `cannot inspect template existence` | 我手写的 `.env.test` 里用户名是字面量 `USER`、密码 3 位，psql 认证失败 | 造"改前"侧要用**真实备份** `env.test.bak`，不要手写占位符 |
| 本地 remote-tracking ref 陈旧 | `git rev-parse origin/chore/arch-debt-ledger-to-master` 返回 `b92c1e4b`，据此判定"重指没落地" | 远端一直是 `7b32b9af`；我用的 `git fetch origin <branch>` **不带目标 refspec**，只写 FETCH_HEAD，不更新 `refs/remotes/origin/...` | 判断远端真实状态用 `git ls-remote`，或 fetch 时带目标：`+refs/heads/X:refs/remotes/origin/X` |

---

## 0. 变更事实与被改对象的真实语义

Chief 已执行：删掉 9 个「连接数=0 且非模板源」的克隆库，保留 `tts_erp_test_template`；把 `.env.test` 的两个键从 `tts_erp_v3_test` 改指 `tts_erp_test_template`（主 checkout 与 release 树各一份，均已备份到 `/home/schan/tts-erp-ops-backups/20261111_002500_drop-clone-dbs/`）。

`.env.test` 只有两行有效内容：

```
TTS_ERP_DB_URL_TEST=postgresql+psycopg://postgres:***@127.0.0.1:5432/tts_erp_test_template
TTS_ERP_DB_URL=postgresql+psycopg://postgres:***@127.0.0.1:5432/tts_erp_test_template
```

新旧指纹比对（用户 / 密码 sha256 前 12 位 / host / port **全同**，仅 dbname 不同）：

| 文件 | user | pw_sha12 | pw_len | host | port | dbname |
|---|---|---|---|---|---|---|
| 主 checkout `.env.test`（改后） | postgres | 7f2614617568 | 28 | 127.0.0.1 | 5432 | tts_erp_test_template |
| 备份 `env.test.bak`（改前） | postgres | 7f2614617568 | 28 | 127.0.0.1 | 5432 | tts_erp_v3_test |

⇒ **不是凭据差异**，两个键指向的 server/user/password/port 完全一样。

**`.env.test` 两个键的真实用途**（代码事实）：

| 键 | 谁读 | 用途 |
|---|---|---|
| `TTS_ERP_DB_URL_TEST` | `scripts/test_isolated.sh:56` → `BASE_TEST_URL` | 只作为**连接身份**（server/user/password/port）交给 `derive` |
| `TTS_ERP_DB_URL` | 同上（`:56` 的 fallback）；`import_prod_to_test.sh:103` | 同样只作连接身份 |

`derive`（`scripts/_test_db_connections.py:155-165`）**不检查 base 库是否存在**，只校验库名形状（`_test_name`：必须含 "test"）与 `run ∉ {template, base, *_RESERVED_RUN_DATABASES}`。`_RESERVED_RUN_DATABASES = {"tts_erp_test_template", "tts_erp_v3_test"}`（`:36`）。

**当前数据库实况**（容器 `postgres`，`pg_database` 实查）：

```
tts_erp_v3_test        count = 0     ← 已被删除
tts_erp_test_template  count = 1     ← 保留
tts_erp                生产库
```

---

## ① 隔离测试实跑证据

**判据不看退出码**（master 的 `docs/testing/baseline-fast.txt` 是空的，`count: 0` ⇒ `--scope fast` 会把存量失败全报成 NEW 并退出 1，退出码不可信）。判据是三条：**克隆成功 + 用例真的跑到 100% + 失败 id 集合**。

两侧用同一组 10 个文件、同一个 commit `bf00a4d8`、同一台机器、串行跑（跑前 `ps -eo args | grep -c "[p]ytest"` = 0，确认无并发套件）。

### 改后侧（base = `tts_erp_test_template`）

```
[test-timing] connection_prepare=1.371s
[test-timing] template_prepare=0.128s
[isolated-test] cloning tts_erp_test_template -> tts_erp_test__schan_20261110170926_2049307_3270f50e
[test-timing] database_clone=0.720s
[isolated-test] using existing template DB: tts_erp_test_template
[test-timing] pytest_total=352.745s
[test-timing] test_execution=354.436s
[isolated-test] dropping ephemeral DB: tts_erp_test__schan_20261110170926_2049307_3270f50e
[test-timing] isolated_total=358.709s
```

进度条跑到 `[100%]`，字符直方图（只统计形如 `...[ nn%]` 的进度行，避免把页面 HTML 里的 E/F 当成失败）：

```
5 %   353 .   5 [   5 ]   4 0   3 1   1 2   1 4   1 6   1 8
```

⇒ **353 条用例、0 个 F/E、0 个失败 id。**

### 改前侧（base = 已删除的 `tts_erp_v3_test`）

第一次跑"改前"侧时它 **1.471 秒就失败了**，报 `cannot inspect template existence on the selected server`。**根因是我自己的复现方式出错，不是配置变更**：我手写的改前 `.env.test` 里用户名是字面量占位符 `USER`、密码只有 3 位（真实备份是 `postgres` / 28 位），psql 认证失败 ⇒ `db_exists()`（`test_isolated.sh:86-90`）里的 psql 非零退出 ⇒ 脚本按设计报"无法检查模板是否存在"。

真实 stderr（日志原文）：

```
psql: error: connection to server at "127.0.0.1", port 5432 failed: FATAL:  password authentication failed for user "USER"
[isolated-test] ERROR: cannot inspect template existence on the selected server
```

用**真实备份** `env.test.bak` 重建改前侧后重跑：

```
[test-timing] connection_prepare=1.331s
[test-timing] template_prepare=0.119s
[isolated-test] cloning tts_erp_test_template -> tts_erp_test__schan_20261110172142_2076049_5204ada2
[test-timing] database_clone=0.460s
[isolated-test] using existing template DB: tts_erp_test_template
[test-timing] pytest_total=356.865s
[test-timing] test_execution=358.529s
[isolated-test] dropping ephemeral DB: tts_erp_test__schan_20261110172142_2076049_5204ada2
[test-timing] isolated_total=362.529s
```

进度条直方图与改后侧**逐项相同**：`353 .`、0 个 F/E。

⇒ **判据满足**：克隆成功 + 跑满 100% + 失败 id 集合为空，两侧都成立。

> 顺带确认：改前侧 `template_prepare=0.119s` 且日志里**没有** `refreshing template DB` ⇒ 走的是"模板已存在，直接克隆"分支，没有触发刷新。

---

## ② 改前 / 改后失败 id 集合双向对比

失败 id 用行首锚点 `^(FAILED|ERROR) ` 提取（不能用 `grep -oE "[FE]"`，那会把页面 HTML 里的 E/F 也计进去）。

```
===== 失败 id 计数 =====
  改后 = 0
  改前 = 0

===== 方向1: comm -13 改后 改前   （改后新增的失败） =====
  (行数: 0)

===== 方向2: comm -13 改前 改后   （改后消失的失败） =====
  (行数: 0)

===== 两次运行的实际用例清单是否一致 =====
  一致: bash scripts/test.sh fast tests/browser/test_spu_deterioration_alert_page.py
        tests/browser/test_spu_price_columns.py tests/browser/test_frontend_pilots.py
        tests/browser/test_spu_react_workspace.py tests/browser/test_spu_numeric_sort.py
        tests/browser/test_spu_projection.py tests/scripts/test_frontend_p2_live_support.py
        tests/test_test_run_binding.py tests/test_test_template_refresh.py
        tests/test_no_dotenv_bootstrap.py
```

两个方向都空 —— 但**这个结论只在两侧都真的跑起来之后才成立**。第一版"改前"侧也是空集合，可那是"没跑起来"的空，与"跑了 353 条全过"的空含义完全不同。修正复现方式后才有第 ① 节那组数字。

**⇒ 零新增失败、零消失失败、零用例数差异。这次改指对测试基线无影响。**

### 为什么改指影响不到基线（代码链）

```
.env.test ──▶ BASE_TEST_URL (test_isolated.sh:56)
                 │
                 ▼
        derive(base, template, run)  (_test_db_connections.py:155)
          ├─ parameters = base 的 server/user/password/port   ← base 只贡献这些
          ├─ template URL / run URL / postgres 维护库 URL
          └─ ✗ 从不检查 base 库是否存在
                 │
                 ▼
        createdb --maintenance-db <postgres 维护库> -T <模板库> <临时库>   (test_isolated.sh:200)
                 │  ← 克隆源固定是 TEMPLATE_DB，不是 base
                 ▼
        TTS_ERP_DB_URL_TEST="$RUN_URL"  (test_isolated.sh:251)
                 │
                 ▼
        tests/conftest.py:49-50
          _db_url, _run_binding = _isolated_test_context()
          os.environ["TTS_ERP_DB_URL"] = _db_url     ← 在任何 fixture 建引擎之前覆盖
```

三处决定性事实：

- base 库名**从不作为连接目标出现**（`MAINTENANCE_URL` 的 dbname 被 `derive` 换成 `postgres`）；
- 克隆源是 `TEMPLATE_DB`（`test_isolated.sh:200`），不是 base；
- `tests/conftest.py:50` 在建引擎之前把应用层 `TTS_ERP_DB_URL` 覆写成临时克隆库 URL ⇒ 应用进程永远连克隆库，不碰模板库。

⇒ 把 base 从 `tts_erp_v3_test` 改成 `tts_erp_test_template`，**对"连哪个库"没有任何影响**。

---

## ③ 模板库「当基址」与「当克隆源」双路径污染检查

### 3.1 污染判据：用行数指纹，不是靠读代码推断

模板库的业务表**不在 `public` 架构**（在 `after_sales` / `commerce` / `integration` 等 16 个业务架构里），`public` 只有 `alembic_version` 一张 —— 只扫 `public` 会得出"污染不了"的假结论。指纹覆盖全部业务架构。

改前侧跑**结束前**（T2）与**结束后**（T3）各取一次：

| | T2（跑之前） | T3（跑之后） |
|---|---|---|
| 业务表总数 | 91 | 91 |
| 非空表 | `public.alembic_version`=1, `security.roles`=3, `security.permissions`=18, `security.role_permissions`=39 | 同左 |
| alembic 修订 | `0076_media_packs` | `0076_media_packs` |
| **指纹 digest** | `b634b248520a436f278f9fbe2d03410d1d17467bb3103ec7e2ae8f640f13a17c` | **完全相同** |

逐表差异：**无**。

**正对照**（同一套查询打在生产库上，证明计数方法能数到非零行）：

```
prod  after_sales.cases       = 1062        tmpl  after_sales.cases       = 0
prod  after_sales.case_lines  = 1093        tmpl  after_sales.case_lines  = 0
prod  integration.sync_jobs   = 101504      tmpl  integration.sync_jobs   = 0
prod  integration.raw_records = 2433892     tmpl  integration.raw_records = 0
```

⇒ **隔离运行没有写模板库。** 模板库里那 4 张非空表是模板自身的种子（角色/权限），与测试写入无关。

补充：两侧日志都**没有** `refreshing template DB` ⇒ 刷新分支未触发；两次运行的临时克隆库都已 drop，跑完 `tts_erp_test%` 只剩 `tts_erp_test_template` 一个。

### 3.2 `--refresh-template` 分支

`refresh_template()`（`test_isolated.sh:156`）的源来自 `source_prod_url()`（`:110`）：

```bash
source_prod_url() {
  if [[ -n "${TTS_ERP_DB_URL_PROD_SOURCE:-}" ]]; then printf '%s' "$TTS_ERP_DB_URL_PROD_SOURCE"; return; fi
  local url
  url="$(read_env_key .env TTS_ERP_DB_URL)"
  [[ -n "$url" ]] || url="$(read_env_key ../../.env TTS_ERP_DB_URL)"
  printf '%s' "$url"
}
```

- 源 = `TTS_ERP_DB_URL_PROD_SOURCE` 环境变量，或 `.env` / `../../.env` 里的 `TTS_ERP_DB_URL`；
- **从不读 `.env.test`** ⇒ 模板库不可能成为自己的导入源，**无自导入回路**；
- HP desk 实查：`.env`、`../../.env`、`/home/schan/.env`、worktree 下的 `.env` **全部不存在**，`TTS_ERP_DB_URL_PROD_SOURCE` 未设置，`.env.test` 里 `PROD_SOURCE` 出现 0 次 ⇒ **该分支在 HP desk 上本就无法自助执行**，会 `fail 'cannot resolve production schema source URL'`。

同库护栏（两条路径都有）：

- 重建路径（`--recreate-test-db`，`refresh_template` 走这条）：`scripts/_test_db_connections.py:168` `recreation()` → `:174` `source and target database names must differ`；
- legacy 路径：`import_prod_to_test.sh:199` `REFUSING: source and target resolve to the same database.`

⇒ **模板库当基址（base=模板库）与当克隆源（`-T 模板库`）不会互相污染**：基址只贡献连接身份，克隆只读模板，刷新有独立的同库护栏。

### 3.3 一个改指带来的新边角（不影响本次基线，但要记）

`import_prod_to_test.sh:104` 里 `DST_URL` 的默认值取 `.env.test` 的 `TTS_ERP_DB_URL`。改指后，若有人在一个 **已经 `source .env.test` 的 shell** 里直接跑这个脚本（不经过 `--refresh-template` 的环境覆盖），`SRC_URL` 与 `DST_URL` 会**同时**解析成模板库 ⇒ 被同库护栏拒绝（安全，但会挡住本来该做的模板重建）。旧配置下 `SRC` 指向已删库、会在 `pg_dump` 阶段失败、同样达不到目的 —— 两边都跑不成，只是失败点不同。

---

## ④ 台账 owner_ref / owner_blob 重指

### 4.1 结论：重指**已经在**要进 master 的那条线上，不需要再做

| 远端分支（`git ls-remote` 实查） | tip | `base.production_ref` | 与 `production_tree` 实际 HEAD（`1e6c77ee`）是否一致 |
|---|---|---|---|
| `chore/arch-debt-ledger-to-master` | **`7b32b9af`** | **`1e6c77ee…`** | **一致 ✅** |
| `chore/arch-debt-ledger-20261110` | `7b32b9af` | `1e6c77ee…` | 一致 ✅ |

两条远端分支指向**同一个 commit** `7b32b9af`，它已含完整重指：`base.production_ref = 1e6c77ee`、15 条规则的 `owner_ref` 直接指向 `1e6c77ee`、5 条指向 `31da674c`、4 条指向 master 侧 `5597dcca`。

对远端 tip 的台账直接跑检查（实测）：

```
owner: passed=19 failed=5 / 24
    fail: AD-DEPLOY-004 / AD-ALERT-003 / AD-BACKUP-001 / AD-NET-002 / AD-BASE-002
契约 EXIT=0
```

⇒ **④ 的实质结论：台账 owner 已经指对了，19/24 是当前真实状态，剩下 5 条是尚未收口的债务，不是重指没做。**

### 4.2 逐条 owner 解析（重指前 / 重指后各 24 条）

| | entries | owner_blob 在各自 owner_ref 上精确解析 | owner_ref == 所在树 HEAD | owner 代码在 owner_ref 之后变过 |
|---|---|---|---|---|
| 重指前 `b92c1e4b`（`7b32b9af` 的父提交） | 24 | 24/24 ✅ | 0/24 | 13 条 |
| 重指后 `7b32b9af`（远端 tip） | 24 | 24/24 ✅ | 15/24 | 9 条 |

⇒ 两个状态**内部都自洽**（台账没有"写坏"）。差别在**基准指针是否陈旧**：重指前的 `base.production_ref` 指的是一个**已经是祖先**的旧提交（`50978019` 是 `1e6c77ee` 的祖先，中间隔着 `1a319978` / `2b918d42` / `1e6c77ee` 三个提交），而它声明的 `production_tree` 路径实际 HEAD 是 `1e6c77ee`。读者按 `base` 去核对会核到错的树。

其中两处 owner 代码确实变过：`1a319978` 改了 `tts_erp_v2/runtime_settings.py`，`1e6c77ee` 改了 `tts_erp_v2/sync_worker/scheduler.py`。

### 4.3 本次产出的独立 commit（内容上与已落地的重指等价）

分支 **`chore/arch-debt-ledger-repoint-20261111`**，commit **`24477270`**，父提交 `b92c1e4b`。

改法不是手改 yaml，而是**用生成器在 to-master 线的父提交上重跑**：

- `scripts/oneoff_fill_ledger_fields.py`：`PROD` 常量 `50978019` → `1e6c77ee`；AD-JOB-002 判据由 `count(kill|SIGKILL|timeout, ≥1)` 改为 `branch`，锚在 `_close_orphaned_running_row` 实现体里的 `row.status = "failed"` 与 `finished_at`（原计数口径会被 docstring 与日志文案满足 —— release 树 `1e6c77ee` 的 3 处命中里没有一处是判定逻辑）；写回时同步 `base.production_ref`。
- `scripts/infra/runtime-facts.yaml`：部署 commit 事实推进到 `1e6c77ee`。
- `docs/architecture/arch-debt-ledger.yaml`：由上面的生成器重跑产出。

**与远端已落地状态的关系**（实测）：

```
$ for f in docs/architecture/arch-debt-ledger.yaml scripts/infra/runtime-facts.yaml scripts/oneoff_fill_ledger_fields.py; do
    diff -q <(git show origin/chore/arch-debt-ledger-to-master:$f) <(git show 24477270:$f); done
  一致  docs/architecture/arch-debt-ledger.yaml
  一致  scripts/infra/runtime-facts.yaml
  一致  scripts/oneoff_fill_ledger_fields.py

$ git diff --stat origin/chore/arch-debt-ledger-to-master 24477270
 docs/handoff/arch-debt-ledger-verify-2026-11-10.md | 189 +--------------------
```

⇒ **三个重指文件与远端 tip 逐字节一致**；`24477270` 与 `7b32b9af` 的唯一差异是**没有带上那份 handoff 文档更新**（`7b32b9af` 有）。两者是从同一个 `b92c1e4b` 分出的两条平行线（互不为祖先），**合入需二选一，不要两个都进 master**。

**验证**（全部在提交内容上跑）：

| 检查 | 结果 |
|---|---|
| 生成器重跑产出 vs 已验证版本 `7b32b9af` | **diff = 0 行**（逐字节一致） |
| 生成器幂等（连跑两次 `--apply`） | 逐字节一致 ✅ |
| `check_ledger_owners.py` | **18/24 → 19/24**；AD-JOB-002 由判红转绿（OOM P0 修复已在 `1e6c77ee` 落地） |
| `check_ledger_contract.py` | exit 0 ✅ |
| **破坏性负对照**：篡改一条 `owner_blob` | owner 检查 exit 1 ✅、契约检查 exit 1 ✅ |

AD-JOB-002 转绿是重指的**实质效果**：owner 基准前移后，判据才落在真正实现了"把被杀子进程遗留的 running 行结成 failed"的那棵树与那个符号上。

**给 Chief 的处置建议**：`24477270` 相对已落地的 `7b32b9af` 是**冗余**的（内容等价、少一份文档）。可以留着当"单提交等价件"复核用，也可以在确认后删掉这个远端分支 —— 我没有替它做决定。

### 4.4 顺手查出的护栏缺口（本次未修，建议单独立项）

`check_ledger_contract.py` **不覆盖**「`base.production_ref` 与规则侧 `owner_ref` / `production_tree` 实际 HEAD 不一致」这一条。复现（实测）：

```bash
# 把 base.production_ref 退回旧树，规则侧 owner_ref 仍是 1e6c77ee
python3 scripts/check_ledger_contract.py --ledger /tmp/ledger_bad_base.yaml
#   契约判定: 通过
#   EXIT=0        ← 期望非 0，实际判绿
```

⇒ 台账"base 声明与规则侧自相矛盾"这类错误，**没有任何自动检查会判红**，只能靠生成器保证。这也是当初必须在生成器里补 `base["production_ref"] = PROD` 的原因。

建议（未实施，涉及检查器契约语义，留给 Chief 定）：在契约检查里加一条 —— `base.production_ref` 必须等于规则侧实际使用的 owner_ref 集合所对应的生产树 HEAD；并按本次同样的破坏性负对照验证它真会红。

### 4.5 我一度给出的错误结论及更正（留档）

核实 ④ 的中途，我给过这样一条结论：

> "台账重指需要做，而且还没落到要进 master 的那条线上" —— 依据是 `git rev-parse origin/chore/arch-debt-ledger-to-master` 返回 `b92c1e4b`。

**这条结论是错的。** 原因：

```bash
# 我当时用的 fetch —— 不带目标 refspec
$ git fetch --no-tags origin chore/arch-debt-ledger-to-master
$ git rev-parse origin/chore/arch-debt-ledger-to-master
b92c1e4b...          ← 陈旧值（remote-tracking ref 上次更新是 2026-11-10 22:56）

# 权威做法：ls-remote 直问远端
$ git ls-remote origin refs/heads/chore/arch-debt-ledger-to-master
7b32b9afa12e2d54b2a8e332c8b66a8edb292502

# 或 fetch 时带目标 refspec
$ git fetch --no-tags origin +refs/heads/chore/arch-debt-ledger-to-master:refs/remotes/origin/chore/arch-debt-ledger-to-master
   b92c1e4b..7b32b9af  chore/arch-debt-ledger-to-master -> origin/chore/arch-debt-ledger-to-master
$ git rev-parse origin/chore/arch-debt-ledger-to-master
7b32b9af...
```

不带目标 refspec 的 `git fetch origin <branch>` **只写 FETCH_HEAD，不更新 `refs/remotes/origin/...`** ⇒ `git rev-parse origin/<branch>` 返回的是本地缓存的旧值。**凡是要判断"远端现在到底在哪个 commit"，必须用 `git ls-remote`，不能用未刷新的 remote-tracking ref。**

（另：早先一次 `git log --oneline origin/X..Y` 显示"分叉"，同样是同一原因造成的假象。）

---

## 5. 发现的问题与建议（按需要处理程度排序）

| # | 问题 | 证据 | 建议 |
|---|---|---|---|
| 1 | 契约检查不覆盖 base 与规则侧的一致性 | 把 `base.production_ref` 退回 `50978019`，契约检查仍 exit 0 | 见 §4.4，建议单独立项 |
| 2 | `.env.test` 首行注释仍是陈旧的 | 注释写「测试库固定为 tts_erp_v3_test」，而实际值已是 `tts_erp_test_template`，且 `tts_erp_v3_test` 已被删除 | 改注释（`.env.test` 是 gitignored 的，改动落在两台机器的本地副本上） |
| 3 | `import_prod_to_test.sh:112` 的错误提示会教人写回死库 | `sed -i 's\|/tts_erp\b\|/tts_erp_v3_test\|' .env.test` —— 而 `tts_erp_v3_test` 已被删除 | 提示改成 `tts_erp_test_template` |
| 4 | 台账重指的**单提交等价件** `24477270` 与已落地的 `7b32b9af` 并存 | 三个重指文件逐字节一致，仅少一份 handoff 文档更新；两者互不为祖先 | 二选一，别两个都进 master。`24477270` 可留作复核件，也可删；我未替 Chief 决定 |
| 5 | 同上，`install-test-deps.sh` **不会**把改指退回死库（已核实，无需处理） | `:156` 生成的是 `base + '/tts_erp_test_base'`；`:194` 体检查硬编码 `tts_erp_test_template`；`:143` `.env.test` 已存在时只打印不覆盖 | 无需动作，仅记录 |

---

## 6. 复现步骤

```bash
# 改后侧（.env.test 指向模板库）
cd /home/schan/tts-erp/.worktrees/envtest-after-20261111
bash scripts/test_isolated.sh fast \
  tests/browser/test_spu_deterioration_alert_page.py \
  tests/browser/test_spu_price_columns.py \
  tests/browser/test_frontend_pilots.py \
  tests/browser/test_spu_react_workspace.py \
  tests/browser/test_spu_numeric_sort.py \
  tests/browser/test_spu_projection.py \
  tests/scripts/test_frontend_p2_live_support.py \
  tests/test_test_run_binding.py \
  tests/test_test_template_refresh.py \
  tests/test_no_dotenv_bootstrap.py

# 改前侧（把 .env.test 换成真实备份：dbname=tts_erp_v3_test，该库已不存在）
cd /home/schan/tts-erp/.worktrees/envtest-before-20261111
cp /home/schan/tts-erp-ops-backups/20261111_002500_drop-clone-dbs/env.test.bak .env.test
bash scripts/test_isolated.sh fast <上面同样的 10 个文件>

# 双向对比（失败 id 必须用行首锚点提取）
comm -13 <(grep -E '^(FAILED|ERROR) ' before.log | awk '{print $2}' | sort -u) \
         <(grep -E '^(FAILED|ERROR) ' after.log  | awk '{print $2}' | sort -u)
comm -13 <(grep -E '^(FAILED|ERROR) ' after.log  | awk '{print $2}' | sort -u) \
         <(grep -E '^(FAILED|ERROR) ' before.log | awk '{print $2}' | sort -u)
```

跑前务必 `ps -eo args | grep -c "[p]ytest"` 确认为 0 —— 4 核机器上并发套件会让浏览器测试假失败。

---

## 附录 A：本次使用的命令与产物

| 产物 | 位置 |
|---|---|
| 改后侧运行日志 | `/tmp/TEST_env_after.log`（测试机） |
| 改前侧运行日志 | `/tmp/TEST_env_before_fixed.log`（测试机） |
| 第一次改前跑（复现方式有误）日志 | `/tmp/TEST_env_before.log`（测试机，保留作反例） |
| 双向对比脚本 | `/tmp/compare_failures.sh`（测试机） |
| 模板库行数指纹 | `/tmp/tpl_fp_T2.json`、`/tmp/tpl_fp_T3.json`（测试机） |
| owner 解析探针 | `ledger_owner_probe.py`（本次产出目录） |
| 台账重指 commit | `24477270`，分支 `chore/arch-debt-ledger-repoint-20261111`（与已落地的 `7b32b9af` 内容等价） |

## 附录 B：本次未做/不能做的事

- 未跑 `--refresh-template`（它会重建模板库，属破坏性操作；且 HP desk 上无 `.env`，该分支本就无法自助执行）。
- 未改生产配置、未重启服务、未删任何库、未推 master。
- 未修改契约检查器（§4.4 的缺口只报告不修，涉及检查器契约语义）。
- 未替 Chief 决定 `chore/arch-debt-ledger-repoint-20261111` 这个冗余分支的去留（§4.3）。
- 未动共享分支 `chore/arch-debt-ledger-to-master`（它已含重指，无需动）。

## 附录 C：顺带观察到的、与本次改动无关的库残留

跑完收尾时数据库清单如下：

```
tts_erp
tts_erp_test_feat_single_writer_lock_schan_202611_a6fc4720
tts_erp_test_feat_single_writer_lock_schan_202611_a8b15c5a
tts_erp_test_template
```

前两个是 `feat_single_writer_lock` 相关的克隆库，**不是本次两个对照跑留下的**（本次的临时库 `..._20261110170926_2049307_3270f50e` 与 `..._20261110172142_2076049_5204ada2` 都已 drop），来源是别的会话。只记录，不处理。
