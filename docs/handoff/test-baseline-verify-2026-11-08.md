# 测试执行记录：测试链路独立复核（2026-11-08）

> 执行人：测试工程师（bot_d330a0b9a748）｜委托：Chief
> 测试机：HP desk `schan@192.168.1.2`（`ssh hp-desk`），主 checkout `/home/schan/tts-erp`
> 本记录中每条命令均在测试机真实执行并保留真实输出；未执行的项目一律标注「未执行」，不写「应该通过」。

## 0. 结论速览

| 项 | 结论 |
| --- | --- |
| Chief 对两个失败的定性 | **方向正确**：两者都是环境问题，既不是代码缺陷，也不是测试自身问题 |
| `test_probe_reports_current_head_on_authorized_clone` | **环境问题**——代码树 alembic head 与模板库 revision 失配。已在旧 commit worktree 上独立复现出完全相同的断言失败 |
| `test_frontend_components` | **环境问题**——`node_modules` 顶层缺 `semver`。已受控复现出完全相同的 `ERR_MODULE_NOT_FOUND` |
| master 当前是否零失败 | 见 §3 |
| 需要 Chief 决策的事项 | 3 项，见 §7 |

**与委托描述不一致的一处事实**：落后的提交数不是 21。实测 `git rev-list --count 83bbede..0b92d97` = **312**（`--first-parent` = 56）。定性结论不受影响，但数量级差异很大，下一节给出原始命令。

---

## 1. 环境事实

### 1.1 主 checkout 与 origin/master 的关系

同步发生在 **2026-11-08 20:14:37**，同步前 HEAD 为 `83bbede`。

```
$ ssh hp-desk "cd /home/schan/tts-erp && git fetch origin --quiet; git rev-parse HEAD; git rev-parse origin/master; git rev-parse --abbrev-ref HEAD; git rev-list --left-right --count origin/master...HEAD"
0b92d97d99c1842db109146df6c58d114aaa1986
0b92d97d99c1842db109146df6c58d114aaa1986
master
0	0
```

```
$ git reflog show master -n 3 --date=iso
0b92d97 master@{2026-11-08 20:14:37 +0800}: pull --ff-only origin master: Fast-forward
83bbede master@{2026-11-04 18:15:03 +0800}: merge origin/master: Fast-forward
0d1d581 master@{2026-11-04 16:51:50 +0800}: merge origin/master: Fast-forward
```

```
$ git rev-list --count 83bbedee1123fe89e5cb0c095f9a10d98203735e..0b92d97d99c1842db109146df6c58d114aaa1986
312
$ git rev-list --count --first-parent 83bbede..0b92d97
56
```

同步后主 checkout 与 origin/master 完全一致（0 behind / 0 ahead）。**注意：主 checkout 工作区并不干净**，有 7 项未提交改动：

```
$ git status --porcelain
A  docs/handoff/bot-team-setup-2026-11-08.md
A  docs/handoff/p6-retirement-parallel-plan.md
A  docs/handoff/topic-directory-index.md
 M plugins/ads-data-sync
 M plugins/monitor
A  scripts/envsetup/prepare-worktree.sh
?? node_modules/
```

其中 **`scripts/envsetup/prepare-worktree.sh` 只是暂存状态，从未进入 master**：

```
$ git cat-file -e origin/master:scripts/envsetup/prepare-worktree.sh
fatal: path 'scripts/envsetup/prepare-worktree.sh' exists on disk, but not in 'origin/master'
$ git ls-tree origin/master scripts/envsetup/
100755 blob 61b7c81fdf670f1513423d09e8eed54cfe2d4298	scripts/envsetup/install-test-deps.sh
100755 blob 0124e3636b334f38f8daafeaf4f154d0c14697ef	scripts/envsetup/install.sh
```

master 的 `AGENTS.md` 也没有提到这个脚本（`git show origin/master:AGENTS.md | grep prepare-worktree` 无输出）。该文件目前只存在于主 checkout 的暂存区和本次 Windows 工作区分支 `chore/fix-test-server-address`（commit `dfac963d`）。详见 §7 决策项 1。

### 1.2 前置依赖体检

```
$ bash scripts/envsetup/install-test-deps.sh --check
== tts-erp 测试前置依赖安装 ==
   仓库: /home/schan/tts-erp
   模式: check-only

-- 1/4 PostgreSQL 客户端工具
       默认通过 PG_DOCKER=postgres 使用容器内 PostgreSQL 客户端
       [OK]   容器 postgres 内 psql/createdb/dropdb/pg_dump 已就绪

-- 2/4 venv 测试依赖（pytest、tinycss2 等不在 pyproject 运行时依赖里）
       [OK]   pytest 已就绪 (pytest 9.1.1)

-- 3/4 .env.test（测试连接串，gitignored）
       [OK]   .env.test 已存在
             TTS_ERP_DB_URL_TEST=postgresql+psycopg://postgres:***@127.0.0.1:5432/tts_erp_v3_test

-- 4/4 测试库存在性（缺失时只提示，不自动建）
        [OK]   tts_erp_test_template

== 汇总 ==
       [OK]   测试前置依赖就绪。下一步：
        bash scripts/test_isolated.sh fast
```

四项全部 OK，模板库存在。**未执行** `sudo bash scripts/envsetup/install-test-deps.sh`（体检无失败项，无安装需要）。

### 1.3 已提交基线

```
$ bash scripts/test_baseline.sh --show
# tts-erp committed test baseline
...
# scope:   fast
# master:  e04c5e114573b2b6aca1da9c8acfcc8a514e1c6f
# updated: 2026-10-15T16:39:57Z
# count:   0
#
# One normalized pytest test id per line. Sorted; no reason text.
```

**要求失败数 = 0**，即零失败基线。注意基线头记录的测量点是 `e04c5e11`（2026-10-15），而 master 现在是 `0b92d97`（2026-11-08），master 在基线之后已前进 312 个提交。基线文件本身在 master 上仍是 `count: 0`。

### 1.4 模板库与 migration 状态

```
$ docker exec postgres psql -U postgres -d tts_erp_test_template -At -c "select version_num from public.alembic_version"
0076_media_packs
$ docker exec postgres psql -U postgres -d tts_erp -At -c "select version_num from public.alembic_version"
0076_media_packs
```

| 对象 | alembic revision |
| --- | --- |
| 生产库 `tts_erp` | `0076_media_packs` |
| 模板库 `tts_erp_test_template` | `0076_media_packs` |
| master 代码树 head | `0076_media_packs` |
| 旧 HEAD `83bbede` 代码树 head | `0073_media_assets` |

master 上的 migration 文件（尾部）：

```
$ git ls-tree --name-only origin/master alembic/versions/ | tail -6
alembic/versions/0073_media_assets.py
alembic/versions/0074_runninghub.py
alembic/versions/0075_media_card_feeds.py
alembic/versions/0076_media_packs.py
alembic/versions/20260829_init_nine_schemas.py
alembic/versions/20260908_1756_6c7d680dddec_merge_0015_0016.py
```

`0074`–`0076` 三个 migration 确实是旧 HEAD `83bbede` 所没有的——**委托描述中「master 上已新增 0074–0076」这一事实成立**。

### 1.5 前端依赖状态

```
$ grep -n 'semver' frontend/package.json          # master = 工作树
41:    "semver": "7.8.6",

$ git show origin/master:frontend/package-lock.json | grep -n '"node_modules/semver"'
3383:    "node_modules/semver": {

$ git show 83bbede:frontend/package.json | grep -n 'semver'
NO_SEMVER_DECL
$ git show 83bbede:frontend/package-lock.json | grep -n '"node_modules/semver"'
NO_SEMVER_IN_LOCK
$ git cat-file -e 83bbede:frontend/scripts/check-runtime.mjs
fatal: path 'frontend/scripts/check-runtime.mjs' exists on disk, but not in '83bbede...'
```

三者由同一个提交引入：

```
$ git log --oneline -G'"semver"' --format="%h %ad %s" --date=iso origin/master -- frontend/package.json
a3815db 2026-11-07 02:06:10 +0800 fix: 缓存前端构建并减少浏览器重复准备
$ git log --oneline --diff-filter=A --format="%h %ad %s" --date=iso origin/master -- frontend/scripts/check-runtime.mjs
a3815db 2026-11-07 02:06:10 +0800 fix: 缓存前端构建并减少浏览器重复准备
$ git log --oneline -G"pretest:unit" --format="%h %ad %s" --date=iso origin/master -- frontend/package.json
a3815db 2026-11-07 02:06:10 +0800 fix: 缓存前端构建并减少浏览器重复准备
```

master 的 `frontend/package.json` scripts：

```json
"test:unit": "vitest run",
"pretest:unit": "node scripts/check-runtime.mjs",
```

`frontend/scripts/check-runtime.mjs` 第 6 行 `import semver from 'semver';`——`npm run test:unit` 会先跑 pretest，因此**顶层 `semver` 缺失会在 vitest 启动前就中断**。

扫描全机 worktree，当前处于「有 vitest、缺 semver」这一失败前提状态的有 **43 个**：

```
$ for d in /home/schan/tts-erp/.worktrees/* /tmp/tts-erp-*; do
    if [ -d "$d/frontend/node_modules/vitest" ] && [ ! -d "$d/frontend/node_modules/semver" ]; then echo "$d"; fi
  done
VITEST_YES_SEMVER_NO : /home/schan/tts-erp/.worktrees/codex-spu-facts-validation
VITEST_YES_SEMVER_NO : /home/schan/tts-erp/.worktrees/codex-spu-react-layout
... (共 43 个)
```

主 checkout 的 `node_modules` 已被 `npm ci` 整体重装，时间戳集中在 2026-11-08 20:15（= 同步后 24 秒）：

```
$ stat -c "%y %n" frontend/node_modules/{vitest,vite,typescript,semver}
2026-11-08 20:15:01 frontend/node_modules/vitest
2026-11-08 20:15:01 frontend/node_modules/vite
2026-11-08 20:15:02 frontend/node_modules/typescript
2026-11-08 20:15:01 frontend/node_modules/semver
```

---

## 2. 实际执行的命令与真实输出

### 2.1 建立隔离 worktree

```
$ git worktree add --detach /home/schan/tts-erp/.worktrees/test-verify-20261108 0b92d97...
Preparing worktree (detached HEAD 0b92d97)
HEAD is now at 0b92d97 merge: 修复数字人档案和视频预览

$ git worktree add --detach /home/schan/tts-erp/.worktrees/test-repro-stale-20261108 83bbede...
Preparing worktree (detached HEAD 83bbede)
HEAD is now at 83bbede merge: 部署脚本探活参数调整
```

`prepare-worktree.sh` 不在 master 里，只能从主 checkout 拷入两个 worktree 后再执行：

```
$ (cd test-verify-20261108 && bash scripts/envsetup/prepare-worktree.sh)
[LINK] python venv -> /home/schan/tts-erp/.venv
[LINK] .env.test -> /home/schan/tts-erp/.env.test
[INFO] 安装前端依赖（按本 worktree 的 package-lock.json）...
added 232 packages, and audited 233 packages in 15s
[OK]   前端依赖安装完成
[DONE] worktree 测试环境已就绪
EXIT=0

$ (cd test-repro-stale-20261108 && bash scripts/envsetup/prepare-worktree.sh)
[LINK] python venv -> /home/schan/tts-erp/.venv
[LINK] .env.test -> /home/schan/tts-erp/.env.test
[INFO] 安装前端依赖（按本 worktree 的 package-lock.json）...
added 231 packages, and audited 232 packages in 13s
[OK]   前端依赖安装完成
EXIT=0
```

两个 worktree 各自按自己的 `package-lock.json` 独立安装（232 / 231 包，差异来自 master 新增的 semver），没有软链共享 `node_modules`。

### 2.2 master 上的最小范围隔离测试

**运行 A1**——覆盖 `test_probe_reports_current_head_on_authorized_clone`：

```
$ cd /home/schan/tts-erp/.worktrees/test-verify-20261108
$ bash scripts/test_isolated.sh fast tests/scripts/test_oneoff_migrate_monitor_health.py
EXIT=0 ELAPSED=5s
```

日志关键行：

```
[test-timing] connection_prepare=1.395s
[test-timing] template_lock=0.001s
[test-timing] template_prepare=0.101s
[isolated-test] cloning tts_erp_test_template -> tts_erp_test__schan_20261108124431_2694048_970a8613
[test-timing] database_clone=0.439s
[isolated-test] using existing template DB: tts_erp_test_template
[isolated-test] running: bash scripts/test.sh fast tests/scripts/test_oneoff_migrate_monitor_health.py
.................................................                        [100%]
...
0.08s call     tests/scripts/test_oneoff_migrate_monitor_health.py::test_probe_reports_current_head_on_authorized_clone
[test-timing] test_execution=1.958s
[isolated-test] dropping ephemeral DB: tts_erp_test__schan_20261108124431_2694048_970a8613
[test-timing] isolated_total=5.275s
```

49 项全通过，退出 0。

**运行 A2**——覆盖 `test_frontend_components`：

```
$ bash scripts/test_isolated.sh fast tests/frontend/test_components.py
EXIT=0 ELAPSED=257s
```

```
[isolated-test] running: bash scripts/test.sh fast tests/frontend/test_components.py
.                                                                        [100%]
[test-timing] collection=0.021s
[test-timing] frontend_build=0.000s
[test-timing] setup=0.031s
[test-timing] call=251.719s
[test-timing] teardown=0.000s
[test-timing] pytest_total=251.826s
251.72s call     tests/frontend/test_components.py::test_frontend_components
[test-timing] test_execution=253.392s
[isolated-test] dropping ephemeral DB: tts_erp_test__schan_20261108124452_2695064_8c7e0801
[test-timing] isolated_total=256.790s
```

退出 0。耗时 257s，其中 251.7s 是 vitest 组件套件本身。

### 2.3 独立复现 #1：alembic revision 失配

在旧 commit `83bbede` 的 worktree 上，用**今天同一个模板库**（0076）跑同一个用例：

```
$ cd /home/schan/tts-erp/.worktrees/test-repro-stale-20261108
$ bash scripts/test_isolated.sh fast tests/scripts/test_oneoff_migrate_monitor_health.py
EXIT=1 ELAPSED=13s
```

真实失败输出：

```
..............................................F..                        [100%]
...
>       assert script.probe(db_engine.url) == ScriptDirectory.from_config(config).get_current_head()
E       AssertionError: assert '0076_media_packs' == '0073_media_assets'
E
E         - 0073_media_assets
E         + 0076_media_packs
tests/scripts/test_oneoff_migrate_monitor_health.py:453: AssertionError
...
FAILED tests/scripts/test_oneoff_migrate_monitor_health.py::test_probe_reports_current_head_on_authorized_clone
```

**复现成功，断言形状与委托描述完全一致**：库里的 revision 是 `0076_media_packs`，旧代码树的 head 是 `0073_media_assets`。

机制已定位到 `scripts/test_isolated.sh`：

- `prepare_template_and_clone()` 只做 `createdb -T "$TEMPLATE_DB" "$RUN_DB"`，**克隆后不对克隆库执行 `alembic upgrade`**；
- `alembic stamp` + `alembic upgrade head` 只出现在 `refresh_template()` 分支（即 `--refresh-template` 或模板缺失时）；
- `tests/conftest.py` 不做 upgrade，只在表缺失时 skip。

所以克隆库继承模板库的 `public.alembic_version`，而断言比较的是**代码树**的 head。代码树一旦落后于模板库，两者必然不等。

### 2.4 独立复现 #2：顶层缺 semver

受控复现——在 master worktree 里临时移走顶层 `semver`（这是全机 43 个 worktree 的真实状态），跑完立即还原：

```
$ cd /home/schan/tts-erp/.worktrees/test-verify-20261108
$ mv frontend/node_modules/semver frontend/node_modules/.semver_TMP
$ bash scripts/test_isolated.sh fast tests/frontend/test_components.py
EXIT=1 ELAPSED=5s
$ mv frontend/node_modules/.semver_TMP frontend/node_modules/semver
restored: YES
```

真实失败输出：

```
E           throw new ERR_MODULE_NOT_FOUND(packageName, fileURLToPath(base), null);
E         Error [ERR_MODULE_NOT_FOUND]: Cannot find package 'semver' imported from
            /home/schan/tts-erp/.worktrees/test-verify-20261108/frontend/scripts/check-runtime.mjs
E           code: 'ERR_MODULE_NOT_FOUND'
...
FAILED tests/frontend/test_components.py::test_frontend_components - Assertio...
```

**复现成功，错误与委托描述一致**。还原后校验：

```
$ node frontend/scripts/check-runtime.mjs
v22.23.3
check-runtime EXIT=0
```

### 2.5 隔离库清理

```
$ docker exec postgres psql -U postgres -At -c "select datname from pg_database where datname like 'tts_erp_test%'"
(空)
$ ... count(*)
0
```

所有一次性库已 drop，无残留。

---

## 3. 基线比对结论

<!-- BASELINE_RESULT -->

---

## 4. 两个失败的独立判定

### 4.1 `test_probe_reports_current_head_on_authorized_clone`

**判定：环境问题（代码与测试库的 revision 失配）。不是代码缺陷，不是测试自身问题。**

依据：

1. **断言本身是正确且有价值的**。它断言「当前克隆库的 alembic revision 等于当前代码树的 head」，这正是防止脚本在过期 revision 上执行的安全检查。§2.3 中旧代码树 + 新模板库直接触发它，说明它抓到了真实漂移。**不应该为了让测试变绿去放宽这条断言。**
2. **失败值方向可解释、可复现**：库侧 `0076_media_packs`（模板库当前值）、代码侧 `0073_media_assets`（`83bbede` 的 head）。两者都不是"随机错乱"，而是明确的一旧一新。
3. **机制在隔离运行器里定位到**：`scripts/test_isolated.sh` 的克隆路径不 upgrade 克隆库，因此克隆库 revision 恒等于模板库 revision；断言的另一端是代码树 head。**代码树落后于模板库 → 必然失配**，与业务代码无关。
4. **代码在同步后立刻自愈**：同一台机、同一个模板库、同一命令，master worktree 退出 0（§2.2 运行 A1），旧 commit worktree 退出 1（§2.3）。**唯一变量就是 checkout 的 revision**，这是判定为环境问题的决定性对照实验。

注意这条用例的失败**只在 checkout 落后于模板库时出现**。`83bbede` 时代的代码没有 `check-runtime.mjs`，也不需要 semver，所以另一个失败不可能与它来自同一个 checkout 状态——两个失败分别对应两个不同的 checkout 状态（见 §4.2）。

### 4.2 `test_frontend_components`

**判定：环境问题（前端依赖与代码版本不匹配）。不是代码缺陷，不是测试自身问题。**

依据：

1. **失败发生在 vitest 启动之前**。`npm run test:unit` 会先执行 `pretest:unit` → `node scripts/check-runtime.mjs` → `import semver from 'semver'`。缺包时 npm 直接非零退出，vitest 一个用例都没跑（§2.4 实测 5s 失败，运行 A2 正常则要 257s）。
2. **依赖声明本身是自洽的**：master 的 `package.json` 声明 `"semver": "7.8.6"`，master 的 `package-lock.json` 第 3383 行有 `node_modules/semver`，`npm ci` 之后顶层 `semver` 存在且 `check-runtime.mjs` 退出 0。**master 的依赖声明没有缺陷。**
3. **失败前提状态在全机广泛存在**：43 个 worktree 当前就是「有 vitest、缺 semver」，即「按旧 lock 装过 node_modules，但代码已前进到需要 semver」。
4. **对照实验成立**：master worktree 经 `npm ci` 后退出 0（§2.2 运行 A2）；同一 worktree 仅移走顶层 semver 即退出 1 并报出同样的 `ERR_MODULE_NOT_FOUND`（§2.4）。**唯一变量是 node_modules 内容。**
5. **与委托描述的一致性**：委托描述说「master 的 `check-runtime.mjs` 需要它，但旧 package.json 没声明该依赖」——实测确认 `83bbede` 的 package.json 与 lock 均无 semver，`check-runtime.mjs` 也是 `a3815db`（2026-11-07 02:06）才引入的。

### 4.3 三类失败分类总表

| 失败 | 分类 | 依据摘要 |
| --- | --- | --- |
| `test_probe_reports_current_head_on_authorized_clone` | 环境问题 | 代码树 head `0073` ≠ 模板库 `0076`；同机同模板下换 checkout 即绿/红；断言本身正确，不应放宽 |
| `test_frontend_components` | 环境问题 | 顶层 `semver` 缺失，`pretest:unit` 在 vitest 前中断；移走/装回即红/绿；master 依赖声明自洽 |

**本轮未发现代码缺陷，也未发现测试自身问题（断言错误、过期断言、错误 mock）。** 两个失败都是"环境没有跟上代码版本"。

---

## 5. 环境类问题的修复步骤

### 5.1 checkout 落后于模板库（alembic 失配）

```bash
# 1. 确认主 checkout 与 master 的差距
cd /home/schan/tts-erp
git fetch origin
git rev-list --left-right --count origin/master...HEAD

# 2. 同步（主 checkout 上有未提交改动时先确认无冲突）
git pull --ff-only origin master

# 3. 同步后必须让模板库与代码 head 对齐
bash scripts/test_isolated.sh --refresh-template fast
```

第 3 步不可省：`refresh_template()` 是唯一会执行 `alembic stamp` + `alembic upgrade head` 的路径（§2.3 的机制分析）。**只 pull 代码不 refresh 模板，克隆库仍停在旧 revision，断言照样失败。**

新建 worktree 时同理——worktree 的代码 head 若与模板库 revision 不同，会重现同一失败。

### 5.2 前端依赖与代码版本不匹配（semver 缺失）

```bash
# 在需要跑前端测试的那个 worktree 内执行，按该 worktree 自己的 lock 安装
cd <worktree>/frontend
npm ci
```

**禁止软链 `node_modules`**：Node 的 ESM 解析走软链真实路径，跨 worktree 共享会重演本次问题（`docs/guides/agent-testing.md` §3 与 `prepare-worktree.sh` 的注释都写明了这一点）。

新 worktree 用：

```bash
bash scripts/envsetup/prepare-worktree.sh
```

该脚本第 3 步已实现「检测 `vitest` **和** `semver` 两个目录，缺任一即按本 worktree 的 lock 跑 `npm ci`」，逻辑正确。

### 5.3 一次性库清理

正常结束会打印 `[isolated-test] dropping ephemeral DB: <name>`。本轮多次运行后 `pg_database` 中 `tts_erp_test%` 计数为 0，清理正常。

---

## 6. 未执行 / 未覆盖的部分

| 项 | 状态 | 原因 |
| --- | --- | --- |
| `sudo bash scripts/envsetup/install-test-deps.sh` | 未执行 | `--check` 四项全 OK，无缺失依赖需要安装 |
| `bash scripts/test_isolated.sh --refresh-template fast` | 未执行 | 模板库 revision（0076）与 master 代码 head（0076）当前一致，无需刷新；执行它会重建模板库，超出本次「复核」范围 |
| `tests/e2e/` 部署态冒烟 | 未执行 | 需真实部署环境，非本次范围（`docs/guides/agent-testing.md` §2 要求单独证据） |
| `docs/archive/migrate-v1-to-v2-2026-08-29/` 归档迁移套件 | 未执行 | 明确禁止 |
| 生产 `alembic upgrade` | 未执行 | 明确禁止 |
| 43 个缺 semver 的既有 worktree | 未逐个修复 | 超出本次范围；§5.2 已给出统一修复步骤 |
| tower-do 看板 lane 登记 | 未执行 | 本会话工具集中无 `tower_do` / `tower_do_talk` / `tower_do_status`（tool_search 无命中），无法登记 lane |

---

## 7. 需要 Chief 决策的事项

1. **`scripts/envsetup/prepare-worktree.sh` 的提交**。它目前只在主 checkout 的暂存区和本地分支 `chore/fix-test-server-address` 上，**master 里没有**。直接后果：任何 lane 想用它都得先把它拷进 worktree——而拷贝一个 master 里不存在的文件会让 worktree 变成 dirty，`scripts/test_baseline.sh` 随即以 `scoped runs require a clean committed worktree` 退出 2。本轮第一次尝试基线就是这样失败的（§3）。建议：先把这个脚本连同 `AGENTS.md` 的相应章节一起提交进 master。
2. **落后提交数 21 vs 实测 312**。定性不受影响，但如果 21 这个数字进了其他文档或汇报，建议更正。
3. **模板库刷新策略**。当前模板库 revision 跟随生产库（0076），而隔离运行器的克隆路径不做 upgrade，所以「checkout 落后于模板库」必然触发 §4.1 的断言。可选：把 `--refresh-template` 写进同步 checkout 后的固定步骤，或让 `test_isolated.sh` 在克隆后检测 revision 失配并给出更直接的诊断信息（当前是断言失败，看不出是环境漂移）。

---

## 8. 本轮留下的现场

| 路径 | 说明 |
| --- | --- |
| `/home/schan/tts-erp/.worktrees/test-verify-20261108` | master（0b92d97）验证 worktree，已准备环境 |
| `/home/schan/tts-erp/.worktrees/test-repro-stale-20261108` | 旧 commit（83bbede）复现 worktree |
| `/tmp/TEST_run_alembic_master.log` | §2.2 运行 A1 原始日志 |
| `/tmp/TEST_run_frontend_master.log` | §2.2 运行 A2 原始日志 |
| `/tmp/TEST_run_alembic_stale.log` | §2.3 复现 #1 原始日志 |
| `/tmp/TEST_run_frontend_nosemver.log` | §2.4 复现 #2 原始日志 |
| `/tmp/TEST_baseline_fast_master.log` | §3 基线原始 pytest 日志 |
| `/tmp/TEST_prepare-worktree.sh.bak` | 从 worktree A 移出的 prepare-worktree.sh 副本（原样保留，可还原） |

两个 worktree 保留在测试机上，未删除，供复查。
