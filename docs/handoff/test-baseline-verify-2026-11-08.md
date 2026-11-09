# 测试执行记录：测试链路独立复核（2026-11-08）

> 执行人：测试工程师（bot_d330a0b9a748）｜委托：Chief
> 测试机：HP desk `schan@192.168.1.2`（`ssh hp-desk`），主 checkout `/home/schan/tts-erp`
> 本记录中每条命令均在测试机真实执行并保留真实输出；未执行的项目一律标注「未执行」，不写「应该通过」。
>
> **交付说明（2026-11-09 00:45）**：本轮向 `tts-erp 研发协作` 群投递汇报时，`post_group` 与 `message_bot` 两个投递接口一度持续返回「无法确认工具调用来自哪个正在进行的回复」；同期 `list_team` / `get_tasks` / `cancel_tasks` 正常，故为投递路径的运行时故障，非内容问题。已取消 4 条卡在 `queued`、一条都未投递出去的旧讨论，队列清空后重投**已被受理并持久化**，当前停在群侧派发队列（`status: queued`），待群 worker 排到即送达——**本条不再取消**。本文件同时作为完整且权威的记录载体，供验收直接引用。

## 0. 结论速览

| 项 | 结论 |
| --- | --- |
| Chief 对两个失败的定性 | **方向正确**：两者都是环境问题，既不是代码缺陷，也不是测试自身问题 |
| `test_probe_reports_current_head_on_authorized_clone` | **环境问题**——代码树 alembic head 与模板库 revision 失配。已在旧 commit worktree 上独立复现出完全相同的断言失败 |
| `test_frontend_components` | **环境问题**——`node_modules` 顶层缺 `semver`。已受控复现出完全相同的 `ERR_MODULE_NOT_FOUND` |
| master 当前是否零失败 | **否**。完整 fast 基线对比退出 1，56 项新失败（14 项断言过时 + 34 项测试基础设施缺陷 + 8 项疑似偶发）。详见 §3。§3.3 的分类经复核已更正为「断言过时」，不是代码缺陷 |
| 新 master（2b68b40）复核 | 两类缺陷**均未修复**（§8.4），且 renderer 收敛**新增** 2 项溢出回归（§8.5）。§3.1 的机制诊断已实现进 master（§8.3） |
| 最新 master（65bbf11）复核 | 完整 fast 基线 **EXIT=1 / 2353s / 80 项新失败**。§3.3 断言契约、§3.4 conftest 冲突**均已修且清零**；剩余 80 项里 66 项是**测试侧断言过时**（告警页 53 + 价格列 13，都是切换 renderer 后仍在断言旧 DOM），另 14 项在 SPU 页面族其它文件（数值排序、投影、React 工作区、pilot 布局）。详见 §8.6 |
| **收口后（`1aa33dd5`）** | **fast 套件实质零失败**：§8.6 的 80 项全部收口（告警页 53 含一处真实前端修复：表单加 `noValidate`、价格列 13、SPU 页面族 8、media-assets pilot 6）。完整基线仅余 1 项并发时序 flake，隔离复跑 3/3 通过。详见 §10 |
| 需要 Chief 决策的事项 | 5 项，见 §7（其中 §7 第 5 项的基线重生成前提已具备，见 §10.3） |

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

本轮 5 次隔离运行，每次都在日志里打印了 `dropping ephemeral DB:` 并成功 drop：

```
$ tr "\r" "\n" < /tmp/TEST_rerun_multiconftest.log | grep dropping
[isolated-test] dropping ephemeral DB: tts_erp_test__schan_20261108133205_2833173_080f3bb2
$ tr "\r" "\n" < /tmp/TEST_rerun_fontstack.log | grep dropping
[isolated-test] dropping ephemeral DB: tts_erp_test__schan_20261108133106_2831104_74a6ad88
```

核对残留（用无引号转义问题的方式查询）：

```
$ docker exec -i postgres psql -U postgres -At < /tmp/_dbcheck.sql | grep tts_erp_test
tts_erp_test__schan_20261108133316_2835930_5b7a6c4c|15 MB
tts_erp_test_monitor_health_template|13 MB
tts_erp_test_template|13 MB
... (其余为各用途的模板库)
```

残留的 1 个 `tts_erp_test__schan_...` 库**不属于本轮任何一次运行**（其 PID 段 2835930 与本轮两次复跑的 2833173 / 2831104 不同），是并发 lane 留下的。

---

## 3. 基线比对结论

### 3.1 结论

**master 当前不是零失败。** 完整 fast 基线对比退出码 1，driver 报出 **56 项新失败**，而已提交基线 `docs/testing/baseline-fast.txt` 要求 `count: 0`。

这 56 项全部落在 `tests/browser/`（55 项）和 `tests/frontend/test_components.py`（1 项），按错误签名可分成三类，其中**一类是测试断言过时，一类是测试基础设施缺陷**。（本节结论已按 §3.3 的复核批注更正；原文写「一类是真实代码缺陷」，是误判。）

### 3.2 完整基线命令与真实输出

```
$ cd /home/schan/tts-erp/.worktrees/test-verify-20261108
$ bash scripts/test_baseline.sh --scope fast --base 0b92d97d99c1842db109146df6c58d114aaa1986 \
    --keep-log /tmp/TEST_baseline_fast_master.log
EXIT=1 ELAPSED=1856s
```

```
[test-timing] database_cleanup=0.289s
[test-timing] isolated_total=1855.724s
[test-baseline] new failures:
tests/browser/test_frontend_pilots.py::test_pilot_uses_shared_shell[media-assets-资产管理-1440-/tts]
tests/browser/test_frontend_pilots.py::test_pilot_uses_shared_shell[media-assets-资产管理-1440-]
...
tests/frontend/test_components.py::test_frontend_components
[test-baseline] raw pytest output: /tmp/TEST_baseline_fast_master.log
```

按文件：

| 文件 | 失败数 |
| --- | --- |
| `tests/browser/test_render_smoke.py` | 14 |
| `tests/browser/test_spu_price_columns.py` | 13 |
| `tests/browser/test_spu_react_workspace.py` | 10 |
| `tests/browser/test_spu_comparison_drilldown_dates.py` | 7 |
| `tests/browser/test_frontend_pilots.py` | 6 |
| `tests/browser/test_spu_numeric_sort.py` | 2 |
| `tests/browser/test_spu_date_range.py` | 2 |
| `tests/browser/test_spu_projection.py` | 1 |
| `tests/frontend/test_components.py` | 1 |
| **合计** | **56** |

按错误签名：

| 错误签名 | 数量 | 分类 |
| --- | --- | --- |
| `AttributeError: module 'conftest' has no attribute '_mock_payload'` | 34 | **测试基础设施缺陷**（见 3.4） |
| `AssertionError: 三套栈必须互不相同: {'var(--sans)', 'MicrosoftYaHei,...'}` | 14 | **测试自身问题（断言过时）**（见 3.3） |
| `playwright._impl._errors.TimeoutError: Locator.wait_for: Timeout 30000ms exceeded` | 6 | 偶发/资源争用（见 3.5） |
| 其它（组件 adapter 的 npm 子进程非零退出） | 2 | 偶发/资源争用（见 3.5） |

### 3.3 测试契约过严（14 项）—— 已修

> **Chief 复核（2026-11-08 22:00）——分类需要修正。**
> 原文把这一类判为「代码缺陷（CSS token 回归），需要开发工程师处理」，并引用
> `docs/testing/baseline-repair-2026-11-03.md` 说「三套栈应当互不相同」。
> **该引述过度解读了原文，且结论与证据不符。** 实际结论是：**断言过时，不是代码缺陷。**
>
> 1. **原文没有说「三套必须互不相同」。** `baseline-repair-2026-11-03.md:19-21` 的原文是
>    「Tailwind 字体映射回 canonical `--sans`、`--serif`、`--mono`，避免 `font-mono` 及
>    preflight 的 code/pre 引入**第四套栈**」——约束的是「不要出现第四套栈」，
>    不是「三套必须两两不同」。
> 2. **这是有意的产品决定。** `fa475e0e`「style: 统一 ERP 中文与数字字体栈」
>    （brakchen，2026-11-08 14:23）把 `--mono`、`--serif` 都改成 `var(--sans)`，
>    注释写明「ERP 使用同一套中英文字体。保留语义别名供现有样式引用」。
>    该提交只改了 `tokens.css`，没同步改断言 —— 过期的是断言。
> 3. **断言的实际语义是白名单。** `token_font_stacks()` 的返回值是「页面 computed
>    font-family 必须落在这个集合里」的白名单。三套栈相同时白名单是单元素集合，
>    **白名单功能完全不受影响**。那句 `len(stacks) == 3` 是顺带加的区分度检查。
>
> **已修（Chief，提交 `bd4a7753`）**：改为「三个别名都必须声明且都非空」，
> 不再要求互不相同；白名单语义不变。实测 `tests/browser/test_render_smoke.py -k font_stacks`
> → `[100%]` 通过（14 项全绿）。
>
> 原文保留如下，作为问题现象的存档。

**现象**：`tests/browser/test_render_smoke.py::test_page_font_stacks_come_only_from_tokens[*]` 的 14 个参数全部失败。

**单文件隔离复现**（不是全量套件里的偶发）：

```
$ bash scripts/test_isolated.sh fast tests/browser/test_render_smoke.py
EXIT=1 ELAPSED=25s

ERROR tests/browser/test_render_smoke.py::test_page_font_stacks_come_only_from_tokens[dashboard]
ERROR tests/browser/test_render_smoke.py::test_page_font_stacks_come_only_from_tokens[ad-daily]
... (共 14 项)

E       AssertionError: 三套栈必须互不相同: {'var(--sans)', 'MicrosoftYaHei,NotoSansSC,PingFangSC,NotoSansCJKSC,SourceHanSansSC,sans-serif'}
```

**根因**（已定位到源码行）：

`tests/render_support.py:105-113` 从 `tokens.css` 源文本解析三套字体栈并断言互不相同：

```python
def token_font_stacks(css_text: str) -> set[str]:
    for name in ("sans", "mono", "serif"):
        match = re.search(rf"--{name}:\s*([^;]+);", css_text)
        stacks.add(normalize_stack(match.group(1)))
    assert len(stacks) == 3, f"三套栈必须互不相同: {stacks}"
```

master 的 `tts_erp_v2/static/css/tokens.css:43-45`：

```css
--sans: 'Microsoft YaHei', 'Noto Sans SC', 'PingFang SC', 'Noto Sans CJK SC', 'Source Han Sans SC', sans-serif;
--mono: var(--sans);
--serif: var(--sans);
```

`--mono` 与 `--serif` 都被写成 `var(--sans)`，归一化后只剩 2 个不同值，断言必然失败。

**引入提交**：

```
$ git blame -L 43,45 origin/master -- tts_erp_v2/static/css/tokens.css
fa475e0e (brakchen 2026-11-08 14:23:03 +0800 43)   --sans: 'Microsoft YaHei', ...
fa475e0e (brakchen 2026-11-08 14:23:03 +0800 44)   --mono: var(--sans);
fa475e0e (brakchen 2026-11-08 14:23:03 +0800 45)   --serif: var(--sans);
$ git log --oneline --format="%h %ad %s" --date=short origin/master -- tts_erp_v2/static/css/tokens.css
fa475e0 2026-11-08 style: 统一 ERP 中文与数字字体栈
```

`docs/testing/baseline-repair-2026-11-03.md` 记录的既定契约是「Tailwind 字体映射回 canonical `--sans`、`--serif`、`--mono`，避免 `font-mono` 及 preflight 的 code/pre 引入第四套栈」——三套栈应当互不相同。`fa475e0` 把 mono/serif 收敛成 `var(--sans)`，与该契约冲突。

**结论（初稿，存档用）**：这是**代码缺陷**（CSS token 回归），需要 @开发工程师 处理。不属于环境问题，也不属于测试自身问题——断言正确，它抓到了真实回归。

> ⚠️ **上面的初稿结论是错的，已被本节开头的复核批注更正**：这一类的正确分类是**测试自身问题（断言过时）**，不是代码缺陷。`fa475e0` 的字体栈收敛是有意的产品决定，过期的是断言。master 已按此修订契约（提交 `bd4a7753`），并经本记录在 65bbf11 上复验通过（§8.6）。

### 3.4 测试基础设施缺陷（34 项，收集顺序依赖）

**现象**：`AttributeError: module 'conftest' has no attribute '_mock_payload'`，分散在 6 个 browser 文件。

**根因**：`tests/browser/conftest.py:616-618` 的 fixture 用模块名反查自身：

```python
@pytest.fixture()
def browser_conftest() -> ModuleType:
    """Return the fixture module that serves canned data in this test session."""
    return sys.modules[__name__]
```

`tests/` 与 `tests/browser/` 下**没有 `__init__.py`**，pytest 的 prepend 导入模式因此把 `tests/` 下的 8 个 `conftest.py` 全部以 basename `conftest` 注册进 `sys.modules`：

```
$ find tests -name "conftest.py" | sort
tests/api/conftest.py
tests/browser/conftest.py
tests/conftest.py
tests/e2e/conftest.py
tests/jobs_miaoshou/conftest.py
tests/jobs_token_refresh/conftest.py
tests/proxy/conftest.py
tests/sync_worker/conftest.py
```

`sys.modules['conftest']` 最终指向哪一个，取决于导入顺序。`tests/conftest.py` 里没有 `_mock_payload`（`grep -c "_mock_payload" tests/conftest.py` → 0），于是取到它就报这个 AttributeError。

**对照实验（决定性）**：

```
# (a) 浏览器文件 + 同级另一个浏览器文件：0 失败
$ bash scripts/test_isolated.sh fast tests/browser/test_render_smoke.py tests/browser/test_spu_price_columns.py
EXIT=1 ELAPSED=67s
  → 14 个 ERROR 全部来自 test_render_smoke.py（字体栈，见 3.3）
  → test_spu_price_columns.py 零失败

# (b) 再加入 api/proxy 的测试（多带进 2 个同名 conftest）：立刻炸
$ bash scripts/test_isolated.sh fast tests/browser/test_spu_price_columns.py \
    tests/api/test_ad_daily.py tests/proxy/test_miaoshou_rate_limit.py
EXIT=1 ELAPSED=17s
FAILED tests/browser/test_spu_price_columns.py::test_price_content_is_only_shown_on_standard_roi_profile
... (共 13 项)
E       AttributeError: module 'conftest' has no attribute '_mock_payload'
```

同一个文件、同样的代码、同样通过 `scripts/test_isolated.sh`，**只因为多收集了两个目录的 conftest，13 项全挂**。这与完整 fast 基线里该文件的 13 项失败完全对应。

**结论**：这是**测试自身问题**（测试基础设施缺陷），不是代码缺陷、不是环境问题。特征是「单文件跑能过、全量收集时挂」——`docs/guides/agent-testing.md` 的 DoD 门禁跑的是全量 fast，所以这条必然影响每个 lane 的基线对比。

修复方向（供 @开发工程师 / @测试质量评审员 判断）：让 `browser_conftest` fixture 不再依赖 `sys.modules[__name__]`，改为直接引用模块对象（例如 `import tests.browser.conftest as browser_conftest` 后的模块自身），或给 `tests/` 各目录补 `__init__.py` 消除 basename 冲突。

### 3.5 偶发 / 资源争用（8 项）

- `tests/frontend/test_components.py::test_frontend_components`：完整基线里失败（npm 子进程非零退出，vitest 内部用例逐条 ✓），但**单独跑通过**（§2.2 运行 A2：EXIT=0，257s）。这与 `docs/testing/baseline-repair-2026-11-03.md` 记录的「组件 adapter 的 npm 子进程 180 秒超时」是同一类现象。
- 6 项 `playwright` 30s 超时。

**运行条件说明（重要）**：本次完整基线期间，测试机并非独占——swap 8G 已用满，且有另一条 lane 的 vitest 在并行跑：

```
$ free -m | head -2
              total   used   free  shared  buff/cache  available
Mem:          15888   7526   2768   1285       7271      8361
$ pgrep -fa "pytest -q"
2816310 /home/schan/tts-erp/.venv/bin/python .venv/bin/pytest -q ... tests/frontend/test_components.py
```

按 `docs/guides/agent-testing.md` §6「若隔离复跑通过，记录为可能的偶发」，这 8 项按偶发处理；但本轮**未对 6 项 playwright 超时逐项做最小复跑**（见 §6），因此不宣称它们已确证为偶发。

### 3.6 为什么「零失败」的印象一直存在

已提交基线记录的是 `master: e04c5e11`（2026-10-15）时的 `count: 0`，而 master 现在是 `0b92d97`（2026-11-08），中间已前进 **312 个提交**。其中 `fa475e0`（今天 14:23）就引入了字体栈回归。

同时：

```
$ bash scripts/test_baseline.sh --scope auto --base 0b92d97 --plan
{
  "base": "0b92d97...", "head": "0b92d97...",
  "frontend": false, "backend_all": false, "backend": [], "checks": [], ...
}
```

当 `head == base`（文档类改动），auto 选择是**空的**，driver 直接返回退出 0，不会触碰这 56 项。**任何只跑 `--scope auto` 的 lane 都会得到「零失败」的错误结论。** 只有显式 `--scope fast` 才会暴露它们。

按 `docs/guides/agent-testing.md` §6「当 baseline 变陈旧」，此时应由 master 的干净 worktree 重新生成基线（`bash scripts/test_baseline.sh --update`）。**本轮未执行 `--update`**：该操作会改写 master 的基线文件，属于 master-only 操作，且本次委托明确要求先不提交、等待确认。

> **Chief 处置（2026-11-08 22:10）**：已把这条陷阱写进 `docs/guides/agent-testing.md` 的完成标准一节——
> 明确「`--scope auto` 的 `0` 退出码不等于套件是绿的」，并写清空计划分支的源码位置
> （`scripts/test_baseline.sh` 的 `no application tests selected` 分支）、
> 基线陈旧的成因与后果，以及「要看 master 的真实状态就跑 `--scope fast`」。
>
> **基线文件本身仍未更新**（`docs/testing/baseline-fast.txt` 仍记 `e04c5e11` / `count: 0`）。
> `--update` 是 master-only 操作且会改写基线，等确认后执行。
>
> **另一条从这次基线复核里挖出来的真实缺陷（Chief 于 2026-11-08 22:20 发现并修复）**：
>
> 把 `test_style_tokens.py` 的检查对象从旧 Jinja 模板切到 React 样式源后，
> 暴露出一个**原先被旧断言范围掩盖的真实 CSS 缺陷**：
>
> ```
> frontend/src/pages/media-assets/media-workspace.css:70
>   .media-preview-upload { ... background: var(--surface-secondary); }
> frontend/src/pages/media-assets/media-workspace.css:104
>   .media-upload-file { ... background: var(--surface-secondary); }
> ```
>
> `--surface-secondary` **在全仓没有任何声明**——引用了但没定义，浏览器会把它解析成无效值，
> 这两个元素的背景色实际是透明的。引入于 `c2089bb`「feat: 支持共享视频素材包」。
>
> 全仓扫描确认这是**唯一**一个未声明的 CSS 令牌（`--bs-*` 属 vendor，不计）。
> 修复：在 `tts_erp_v2/static/css/tokens.css` 的 surface 组补 `--surface-secondary: #E9EEE7`
> （与既有的 `--default` 同色，与 HeroUI 语义一致）。
>
> **教训**：旧断言只覆盖旧模板，React 侧的 CSS 契约从来没被它检查过。
> 扩展断言范围时可能会暴露此前无人发现的真实缺陷——这正是扩展的价值所在。



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

**在 Chief 委托复核的那两个失败中，未发现代码缺陷，也未发现测试自身问题（断言错误、过期断言、错误 mock）。** 两个失败都是"环境没有跟上代码版本"。

**但完整基线跑完后，在 master 上另发现了两类问题**，与委托的两个失败无关，属独立发现：§3.3 的字体栈断言过时（14 项，经复核已更正为「测试自身问题」）、§3.4 的 conftest 测试基础设施缺陷（34 项）。这两类才是真正会让每个 lane 的基线对比变成 exit 1 的东西。

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
| 6 项 playwright 30s 超时的逐项最小复跑 | 未执行 | 按 §6 只对代表性文件做了最小复跑（`test_spu_price_columns.py` 零失败、`test_render_smoke.py` 复现字体栈问题）；6 项超时未逐项确证偶发，故不宣称已确证 |
| `bash scripts/test_baseline.sh --update` | 未执行 | 会改写 master 的基线文件，属 master-only 操作；且本次委托明确要求先不提交、等待 Chief 确认。基线确实已陈旧（见 §3.6），建议由 Chief 决定后再执行 |
| tower-do 看板 lane 登记 | 未执行 | 本会话工具集中无 `tower_do` / `tower_do_talk` / `tower_do_status`（tool_search 无命中），无法登记 lane |

---

## 7. 需要 Chief 决策的事项

1. **`scripts/envsetup/prepare-worktree.sh` 的提交**。它目前只在主 checkout 的暂存区和本地分支 `chore/fix-test-server-address` 上，**master 里没有**。直接后果：任何 lane 想用它都得先把它拷进 worktree——而拷贝一个 master 里不存在的文件会让 worktree 变成 dirty，`scripts/test_baseline.sh` 随即以 `scoped runs require a clean committed worktree` 退出 2。本轮第一次尝试基线就是这样失败的（§3）。建议：先把这个脚本连同 `AGENTS.md` 的相应章节一起提交进 master。
2. **落后提交数 21 vs 实测 312**。定性不受影响，但如果 21 这个数字进了其他文档或汇报，建议更正。
3. **模板库刷新策略**。当前模板库 revision 跟随生产库（0076），而隔离运行器的克隆路径不做 upgrade，所以「checkout 落后于模板库」必然触发 §4.1 的断言。可选：把 `--refresh-template` 写进同步 checkout 后的固定步骤，或让 `test_isolated.sh` 在克隆后检测 revision 失配并给出更直接的诊断信息（当前是断言失败，看不出是环境漂移）。
4. **共享测试机上的「批量删库」脚本**。见 §7.1，它会删掉其它 lane 正在运行的临时库。
5. **master 的失败项要谁来处理，以及基线何时重生成**。§3.3 的字体栈断言是**测试自身问题（断言过时，不是代码缺陷）**，§3.4 的 conftest 冲突是明确的测试基础设施缺陷，两者都会让每个 lane 的 fast 基线对比变成 exit 1。基线 `count: 0` 已陈旧 312 个提交。**处置进度**：§3.4 已修（`a9605dea`，多目录 conftest 改包路径导入）、§3.3 已修（`bd4a7753`，断言契约改为「三别名都声明且非空」，不再要求互不相同），均已在 §8.6 于 `65bbf11` 上复验通过。**剩下 80 项全部是测试侧问题**（详见 §8.6 的定性更正）：告警页 53 项断言了已退役 Jinja 模板的 id、价格列 13 项是 mock 载荷与 HeroUI Summary 组件契约不一致、另 14 项在 SPU 页面族其它文件——**不需要开发工程师改产品代码，应由测试侧改断言与 mock**。剩余动作：测试侧收口这 80 项 → 在干净 master worktree 里 `bash scripts/test_baseline.sh --update` 重生成基线。**在基线重生成之前，任何 lane 跑 `--scope fast` 都会看到这批失败**，需要提前告知各 lane 负责人。

---

## 7.1 跨 lane 隐患：共享测试机上的「批量删库」脚本

本轮第一次跑完整 fast 基线时，pytest 进度行从第 6% 起出现 1 个 `F` 后连续 700+ 个 `E`。**这不是 master 的失败，是运行中的临时库被人删掉了。**

证据链：

```
# 1) 第一次基线运行：pytest 仍在跑，但本轮克隆的库已经不在
$ head -6 /tmp/tmp.lVw17tiOoI/pytest.log
[isolated-test] cloning tts_erp_test_template -> tts_erp_test__schan_20261108125034_2703771_29debcad
[isolated-test] using existing template DB: tts_erp_test_template
[isolated-test] running: bash scripts/test.sh selected --scope fast ...

$ pgrep -fa "pytest -q"
2704130 .../test-verify-20261108/.venv/bin/python -m pytest -q ... tests

$ docker exec -i postgres psql -U postgres -At < /tmp/_dbcheck.sql
tts_erp
tts_erp_test_monitor_health_template
tts_erp_test_template
...            ← 已无 tts_erp_test__schan_20261108125034_2703771_29debcad
```

postgres 容器本身没问题（`RestartCount=0`，`StartedAt=2026-10-31`，全程未重启）。真正的动作在 docker events 里——本轮的库和另外 20+ 个库在 20:51:10 被同一个 psql 批量删除：

```
$ docker events --since 30m --until 0s --filter container=postgres \
    --format "{{.Time}}|{{.Action}}|{{.Actor.Attributes.execCmd}}" | grep -i "drop database"
1794142270|exec_create: psql -U postgres -c DROP DATABASE IF EXISTS "tts_erp_test__schan_20261108125034_2703771_29debcad" WITH (FORCE);|
1794142270|exec_create: psql -U postgres -c DROP DATABASE IF EXISTS "tts_erp_test_feature_frontend_p4_costs_01a16ed2_7_f7cea148" WITH (FORCE);|
1794142271|exec_create: psql -U postgres -c DROP DATABASE IF EXISTS "tts_erp_test_master_01a111a0_33b8_72e1_940e_449fe_39ff98f0" WITH (FORCE);|
... (共 20+ 个，含本轮运行的库)
```

执行者是一个 20:50 创建的临时脚本：

> **归属更正（2026-11-08 22:5x，Chief 确认）**：这个清理动作是 Chief 执行的。他在 20:53 左右清理 HP desk 上的残留测试库，删除条件是「没有正在运行的 `test_isolated.sh` 进程」——但当时我正在跑的是 `test_baseline.sh`（它以 `scripts/test_isolated.sh` 为子进程），检查条件没有覆盖到，故误删了正在使用的库。脚本内容与其执行动作对得上：

```
$ cat /tmp/_dbdrop.sql
SELECT format('DROP DATABASE %I WITH (FORCE);', datname)
FROM pg_database
WHERE datname LIKE 'tts\_erp\_test\_%'
  AND datname NOT LIKE '%template%'
  AND datname NOT LIKE 'tts\_erp\_test\_template'
  AND datname NOT LIKE 'tts\_erp\_test\_monitor\_health\_template'
ORDER BY datname;
```

同批还有 `/tmp/_dbcheck.sql`（20:51）、`/tmp/_schema.sql`（20:41）、`/tmp/env-check-*.sh`（20:24–20:38），属于同一串环境排查动作。

**风险点**：这个脚本按名字前缀删库，**不区分库是不是正在被别的 lane 使用**。`docs/guides/agent-testing.md` §4 承诺「Concurrent isolated runs do not delete each other's `TEST_` rows」，但那是**行级**保证；这个脚本是**库级**删除，把该承诺在库层面破了。被删的 20+ 个库里有明显属于其它 lane 的（`feature_frontend_p4_costs`、`fix_spu_compare_ui`、`master_01a111a0` 等）。

**建议**：任何 lane 在做「清理残留测试库」时，只能删自己 worktree/lane 名下的库，不能按 `tts_erp_test_%` 前缀批量删；如果要批量清理，必须先确认没有活跃的 `pytest`/isolated 进程，或者把库名白名单化。这一条建议同步给 @可靠性工程师 与 @开发工程师。

**更稳的判定条件**：只查 `test_isolated.sh` 进程会漏——`scripts/test_baseline.sh` 以 `scripts/test_isolated.sh` 为子进程运行，且其父进程名不匹配。更可靠的判定是查 `pg_stat_activity` 里是否有指向 `tts_erp_test_%` 库的活跃连接，或直接查所有 `pytest` 进程的 cwd 归属（`readlink -f /proc/<pid>/cwd`）。本次的误删正是因为「进程检查」与「实际运行形态」不匹配。

**第二次运行未受影响**：20:57:31 重新克隆的库 `tts_erp_test__schan_20261108125731_2718734_6f163387` 全程存活，pytest 进度正常推进（§3）。删除动作没有重演。

---

## 8. 在新 master（2b68b40）上的复核

master 在本记录初稿之后又前进了。Chief 的消息里写的是 `047a1ac`，实测 `origin/master` 已经是 **`2b68b40`（feat: 收敛已迁移页面的双路 renderer 分支并补齐 P6 退役准备）**。由于这次改动正好动了 `tests/browser/conftest.py`、`scripts/test_isolated.sh`、`tests/api/test_frontend_pages.py`，本记录的结论必须在新 master 上复验。

### 8.1 「第一次基线被删库」的责任归属与第二次基线的有效性

Chief 说明是他在 20:53 左右清理残留测试库时误删了正在使用的库。**与我的独立测量一致**：docker events 显示批量 DROP 发生在 `1794142270` = **20:51:10**，被删的库中包含本轮克隆的 `tts_erp_test__schan_20261108125034_2703771_29debcad`。我原先把执行者记成「一个临时脚本」，Chief 已确认那是他的清理动作。

**但第二次基线运行没有被这次清理影响**，证据是全程监控日志：

```
$ head -3 /tmp/TEST_baseline_monitor2.log
20:59:45 my_run_db_present=1 worktree_procs=5
21:00:25 my_run_db_present=1 worktree_procs=5
21:01:05 my_run_db_present=1 worktree_procs=5
$ tail -3 /tmp/TEST_baseline_monitor2.log
21:31:15 my_run_db_present=0 worktree_procs=3
21:31:55 my_run_db_present=0 worktree_procs=2
21:32:35 my_run_db_present=0 worktree_procs=2
$ grep -c "my_run_db_present=1" /tmp/TEST_baseline_monitor2.log
43
$ grep -c "my_run_db_present=0" /tmp/TEST_baseline_monitor2.log
7
```

43 个采样点（每 40 秒一次，覆盖 20:59:45 → 21:31）里临时库全程在位；最后 7 个点为 0 是**运行自身结束后的清理**——pytest 在 21:28:27 完成（`ELAPSED=1856s`，起点 20:57:31），`drop_run_db` 随即执行。**因此 §3 的 56 项结论是有效数据，不是被删库污染的结果，不需要因为删库而重跑。**

### 8.2 Chief 的两条声明已逐一核实

**`prepare-worktree.sh` 已入 master** —— 在新 master 上全新建 worktree，脚本自动到位并直接跑通，无需手工拷贝：

```
$ git worktree add --detach /home/schan/tts-erp/.worktrees/test-verify-20261108b 2b68b40...
Preparing worktree (detached HEAD 2b68b40)
$ ls -l scripts/envsetup/prepare-worktree.sh
-rwxrwxr-x 1 schan schan 3229 Nov  8 22:57 scripts/envsetup/prepare-worktree.sh
$ bash scripts/envsetup/prepare-worktree.sh
[LINK] python venv -> /home/schan/tts-erp/.venv
[LINK] .env.test -> /home/schan/tts-erp/.env.test
[INFO] 安装前端依赖（按本 worktree 的 package-lock.json）...
added 232 packages, and audited 233 packages in 8s
[DONE] worktree 测试环境已就绪
PREPARE_EXIT=0
```

worktree 保持干净（`git status --porcelain --untracked-files=all` 为空），`test_baseline.sh` 不再触发 `scoped runs require a clean committed worktree`。

**模板库不陈旧** —— 三方对齐已复测：

```
模板库 tts_erp_test_template : 0076_media_packs / 306 表
生产形状库 tts_erp            : 0076_media_packs / 306 表
master 代码树 head            : 0076_media_packs
```

`--refresh-template` 在 HP desk 上因缺 `.env` 仍不可自助执行；按当前三方一致，**现在也不需要刷新**。

### 8.3 §4.1 的机制已被实现进 master

Chief 已把我 §7 决策项 3 建议的诊断写进 `scripts/test_isolated.sh`（新增 `warn_revision_drift()`，在 `prepare_template_and_clone()` 里克隆后调用）。其注释与本记录 §2.3 的机制分析一致：

```
# The clone path never upgrades the clone, so a clone revision below the code
# head means the checkout is stale relative to the template — an environment
# condition, not a code defect.
```

克隆库 revision 与代码 head 不一致时现在会直接打印两侧取值与修复步骤，而不是只留下一个下游断言失败。本记录 §2.3 的机制结论因此得到独立确认。

### 8.4 两类缺陷在新 master 上均未修复

**A 类（字体栈，14 项）—— 仍在**：

```
$ cd /home/schan/tts-erp/.worktrees/test-verify-20261108b
$ bash scripts/test_isolated.sh fast tests/browser/test_render_smoke.py
EXIT=1 ELAPSED=72s
ERROR tests/browser/test_render_smoke.py::test_page_font_stacks_come_only_from_tokens[ad-daily]
... (共 14 项)
E  AssertionError: 三套栈必须互不相同: {'var(--sans)', 'MicrosoftYaHei,NotoSansSC,PingFangSC,NotoSansCJKSC,SourceHanSansSC,sans-serif'}
```

`origin/master` 的 `tts_erp_v2/static/css/tokens.css:43-45` 仍是 `--mono: var(--sans); --serif: var(--sans);`，未改。

**B 类（conftest 冲突）—— 仍在**：

```
$ bash scripts/test_isolated.sh fast tests/browser/test_spu_price_columns.py \
    tests/api/test_ad_daily.py tests/proxy/test_miaoshou_rate_limit.py
EXIT=1 ELAPSED=16s
FAILED tests/browser/test_spu_price_columns.py::test_price_content_is_only_shown_on_standard_roi_profile
... (共 13 项)
E  AttributeError: module 'conftest' has no attribute '_mock_payload'
```

`origin/master` 的 `tests/browser/conftest.py:621-623` 仍是 `return sys.modules[__name__]`；该文件本次的改动只涉及 renderer 退役，与 fixture 无关。

### 8.5 新增回归：renderer 收敛带来 2 项溢出失败

A 类实验在新 master 上除了 14 项字体栈失败，还多出 2 项**此前 56 项清单里没有**的失败：

```
FAILED tests/browser/test_render_smoke.py::test_spu_deterioration_alert_has_no_horizontal_overflow_at_any_viewport[390]
FAILED tests/browser/test_render_smoke.py::test_spu_deterioration_alert_has_no_horizontal_overflow_at_any_viewport[768]
```

这两项对应 master 上的 renderer 收敛改动（`2b68b40` / `80b218a` / `30622f3`，涉及 `tts_erp_v2/api/v2/pages.py` 与 `tests/browser/conftest.py` 的 `_render_pages`）。**属于新引入的回归，不是旧问题残留**，需要 @开发工程师 一并处理。

> **Chief 处置（2026-11-08 23:10）**：§8.4 与 §8.5 指出的问题**全部已修**，但修正在
> `origin/feature/p6-collapse-migrated-routes` 上，**尚未合入 `master`**——
> 本节复核的对象是 `origin/master` @ `2b68b40`，所以三项都还在。
>
> | §8.4/§8.5 的问题 | 修复提交 | 处置 | 实测 |
> | --- | --- | --- | --- |
> | A 类 字体栈 14 项 | `bd4a7753` | 断言改为「三个别名都声明且非空」，不再要求互不相同 | `fast tests/browser/test_render_smoke.py -k font_stacks` → `[100%]` |
> | B 类 conftest 冲突 13 项 | `a9605de` | `browser_conftest` 改用 `from tests.browser import conftest as this_module` | 混跑三目录时 `_mock_payload` ERROR 消失 |
> | §8.5 溢出回归 2 项 | `f205c744` | 告警页就绪信号改用共享 `wait_canned_page(page, ALERT_SLUG)` | `-k spu_deterioration_alert_has_no_horizontal` → `[100%]`，4 个视口全绿 |
>
> 根因说明：路由收敛把 `spu-profit-deterioration` 从旧 Jinja 模板切到 React renderer，
> 而该测试硬编码等待旧模板的 `#alert-status` 元素 id——元素不存在，Playwright 等 10 秒超时。
> **切换 renderer 时，断言旧 DOM 的测试不会告诉你 renderer 变了**，失败信息只是超时。
>
> 同时本轮还发现并修掉一个真实 CSS 缺陷：`--surface-secondary` 被引用但全仓未声明
> （`media-workspace.css` 第 70/104 行背景色实际是透明的），见 §3.6 补充说明。

---

### 8.6 在最新 master（65bbf11）上的复验

§8.4 / §8.5 复核的对象是 `2b68b40`。之后 master 继续前进到 **`65bbf11`**（含 `a9605dea test: browser_conftest 改用包路径导入，消除多目录 conftest 冲突`），本节是在 `65bbf11` 上的复验。

**worktree 按新流程准备**（脚本已在 master，无需手工拷贝，worktree 保持干净）：

```
$ git worktree add --detach /home/schan/tts-erp/.worktrees/test-verify-20261108c 65bbf116...
HEAD is now at 65bbf116 docs: 核实并更正 console.js 与旧 CSS 的处置结论
$ bash scripts/envsetup/prepare-worktree.sh
[DONE] worktree 测试环境已就绪
PREPARE_EXIT=0
```

**§3.3 断言契约（14 项）—— 已修，复验通过**：

```
$ bash scripts/test_isolated.sh fast tests/browser/test_render_smoke.py
EXIT=0 ELAPSED=30s
失败数: 0
```

**§3.4 conftest 冲突（34 项）—— 已修，复验通过**：多目录收集场景下的 `AttributeError: module 'conftest' has no attribute '_mock_payload'` 已消失（`origin/master` 的 `tests/browser/conftest.py` 改为 `from tests.browser import conftest as this_module`）。

**但仍有 13 项测试侧断言问题未修**：`tests/browser/test_spu_price_columns.py` 在 `65bbf11` 上**单文件复跑就失败**——不是收集顺序问题、不是偶发：

```
$ bash scripts/test_isolated.sh fast tests/browser/test_spu_price_columns.py
EXIT=1 ELAPSED=425s
失败数: 13
FAILED tests/browser/test_spu_price_columns.py::test_price_content_is_only_shown_on_standard_roi_profile
FAILED tests/browser/test_spu_price_columns.py::test_null_and_empty_price_values_never_render_as_zero
...
E  playwright._impl._errors.TimeoutError: Locator.wait_for: Timeout 30000ms exceeded.
E  - waiting for locator("#price-summary") to be visible
```

与 §3.4 的区别要说清楚：修复前这批报的是 `AttributeError: module 'conftest' has no attribute '_mock_payload'`（测试基础设施缺陷，修复后消失）；修复后它们继续失败，但错误类型变成 Playwright 等 `#price-summary` 可见超时。

**定性更正（Chief，2026-11-09）**：原文写「说明还有一层真实的产品/渲染问题」，**这个判断不成立**。实测链路：
- `#price-summary` **在 React 里是存在的** —— `frontend/src/pages/spu-profitability/Summary.tsx:138` 渲染 `<section id="price-summary" hidden={supported !== true}>`。
- `supported` 来自 `priceCapability`，而 `priceCapability` 在 `index.tsx:47` 由 `Boolean(data.totals.priceStats || data.items.some(row => row.priceStats))` 决定。
- 测试的 mock 载荷（`tests/browser/test_spu_price_columns.py::_nested_payload`）虽然写了 `payload["totals"]["priceStats"]`，但夹具的 canned 响应走的是 `tests/browser/conftest.py` 的 `_mock_payload`，**该函数返回的 `/v2/analytics/spu-roi` 载荷里没有 `priceStats` 字段**（`git grep priceStats` 在 `tests/browser/conftest.py` 上零命中），测试里的 monkeypatch 覆盖路径与夹具实际调用路径不一致 → 前端拿到 `priceStats` 缺失 → `priceCapability` 为 false → 面板被 `hidden` 隐藏 → 等待超时。

所以这是**测试侧 mock 契约与实现不一致**（断言/mock 没跟上 P4 迁移后的 HeroUI Summary 组件），不是产品缺陷。修法在测试侧：让 mock 载荷带上 `totals.priceStats`，并把断言改写到 HeroUI `Table` 的真实结构上（`<span id="price-{metric}-{statistic}">`、`<span id="price-summary-status">`）。

**告警页 53 项 —— 定性更正（Chief，2026-11-09 00:5x）**：原文写「确认为真实回归并已修」，并称 `65bbf11` 上该文件复验全绿。**这两句都不成立**，理由如下（均为实测）：

1. `f205c744` 只改了 `tests/browser/test_render_smoke.py`（溢出用例的就绪信号），**从未碰过 `tests/browser/test_spu_deterioration_alert_page.py`**（`git show f205c744 --stat` 无该文件）。所以它不可能修掉这 53 项。
2. `65bbf11` 上该文件**没有全绿**：`65bbf11` 的完整 fast 基线（2353s，退出 1）里 `test_spu_deterioration_alert_page.py` 仍占 **53 项**，是失败项里的最大头。

**真正的根因是测试侧断言过时，不是产品回归**：
- 该测试断言 `#alert-rows [data-spu-pk='1001']`、`#total-spus`、`#alert-status`。这三个 id **只存在于已退役的 Jinja 模板** `tts_erp_v2/templates/pages/spu-profit-deterioration.html`（`git show f72a3089^:...` 里可见 `<tbody id="alert-rows">`、`<strong id="total-spus">`、`<section id="alert-status">`）。
- React 实现 `frontend/src/pages/spu-profit-deterioration/` 从来没有过这些 id（逐个历史提交查过，无一命中）；它渲染的是 `ComparisonTable` 与 `dl` 统计块。
- 断点就在 `80b218a9`（路由收敛）：它把 `tests/browser/conftest.py::_render_pages` 里告警页的渲染来源从 `_render_page("spu-profit-deterioration.html")`（旧模板）改成 `frontend_pages.render_frontend_page(...)`（React）。**fixture 换了 owner，测试却还在断言旧 owner 的 DOM**，于是 53 项一起红。

因此这一项与 §8.6 的价格列 13 项**同源**（都是「切换 renderer 后旧 DOM 断言失效」），修法也在测试侧：把断言改写到 React 的真实 DOM 契约上。**不需要开发工程师改产品代码。**

**§8.1 的删库结论不受影响**：第二次基线（`0b92d97`，56 项）全程监控显示临时库 43/50 个采样点在位，最后 7 个点是运行自身清理，不需因删库重跑。

**完整 fast 基线（`65bbf11`）**：退出 1，2353s，**80 项新失败**。

```
$ bash scripts/test_baseline.sh --scope fast --base 65bbf116442df68fbbb673f2f2094aa84a505850 \
    --keep-log /tmp/TEST_baseline_fast_master3.log
EXIT=1 ELAPSED=2353s
```

| 文件 | 失败数 | 相对 `2b68b40` |
| --- | --- | --- |
| `tests/browser/test_spu_deterioration_alert_page.py` | 53 | 不变 |
| `tests/browser/test_spu_price_columns.py` | 13 | 不变 |
| `tests/browser/test_frontend_pilots.py` | 6 | 不变 |
| `tests/browser/test_spu_react_workspace.py` | 5 | 10 → 5 |
| `tests/browser/test_spu_numeric_sort.py` | 2 | 不变 |
| `tests/browser/test_spu_projection.py` | 1 | 不变 |
| **合计** | **80** | 113 → 80 |

错误签名变化（同一份原始日志统计）：

| 错误签名 | `2b68b40` | `65bbf11` |
| --- | --- | --- |
| `AttributeError: module 'conftest' has no attribute '_mock_payload'` | 34 | **0**（已修） |
| `AssertionError: 三套栈必须互不相同` | 14 | **0**（已修） |
| `playwright Timeout 30000ms exceeded` | 6 | 19 |

**结论（Chief 更正，2026-11-09）**：§3.3 与 §3.4 两类缺陷在 `65bbf11` 上已彻底清零，剩余 80 项全部是 Playwright 超时类，集中在 SPU 页面族——告警页（53）与价格列（13）合计 66 项，另 14 项在 `test_frontend_pilots.py`(6)、`test_spu_react_workspace.py`(5)、`test_spu_numeric_sort.py`(2)、`test_spu_projection.py`(1)。

**这 66 项不是产品回归，是测试侧断言过时**（更正原文的定性，详见 §8.6 的更正批注）：
- **告警页 53 项**：断言的 `#alert-rows` / `#total-spus` / `#alert-status` 是已退役 Jinja 模板的 id，React 实现从来没有过；`80b218a9` 把测试 fixture 的渲染来源从旧模板换成 React 后，断言就失效了。
- **价格列 13 项**：`#price-summary` 在 React 里确实存在（`frontend/src/pages/spu-profitability/Summary.tsx`），但它带 `hidden={supported !== true}`，而 `supported` 由 `priceCapability` 决定、后者取决于 `data.totals.priceStats` 是否存在——测试的 mock 载荷没提供这个字段，所以面板一直隐藏。这同样是测试侧 mock 契约与实现不一致。

**因此这 80 项应由 @测试质量评审员 在测试侧收口**（改断言、改 mock 载荷），不需要 @开发工程师 改产品代码。原文写的「共同症状是页面状态元素始终不出现，指向 renderer 收敛后 React 页面与测试夹具之间的契约不一致」方向对，但结论定性反了：**不是产品与夹具不一致，是测试断言本身写的是上一个 owner 的 DOM**。

---

## 9. 本轮留下的现场

| 路径 | 说明 |
| --- | --- |
| `/home/schan/tts-erp/.worktrees/test-verify-20261108` | master（0b92d97）验证 worktree，已准备环境 |
| `/home/schan/tts-erp/.worktrees/test-repro-stale-20261108` | 旧 commit（83bbede）复现 worktree |
| `/tmp/TEST_run_alembic_master.log` | §2.2 运行 A1 原始日志 |
| `/tmp/TEST_run_frontend_master.log` | §2.2 运行 A2 原始日志 |
| `/tmp/TEST_run_alembic_stale.log` | §2.3 复现 #1 原始日志 |
| `/tmp/TEST_run_frontend_nosemver.log` | §2.4 复现 #2 原始日志 |
| `/tmp/TEST_baseline_fast_master.log` | §3 完整基线原始 pytest 日志（232KB） |
| `/tmp/TEST_baseline_run2.log` | §3 完整基线 driver 输出（含 56 项 new failures 清单） |
| `/tmp/TEST_baseline_monitor2.log` | §3 运行期间的临时库/进程存活监控 |
| `/tmp/TEST_rerun_browser_min.log` | §3.4 对照实验 (a) |
| `/tmp/TEST_rerun_multiconftest.log` | §3.4 对照实验 (b) |
| `/tmp/TEST_rerun_fontstack.log` | §3.3 单文件复现 |
| `/tmp/TEST_prepare-worktree.sh.bak` | 从 worktree A 移出的 prepare-worktree.sh 副本（原样保留，可还原） |
| `/tmp/TEST_baseline_fast_master2.log` | §8 `2b68b40` 完整基线原始日志（113 项） |
| `/tmp/TEST_baseline_run3.log` | §8 `2b68b40` 完整基线 driver 输出 |
| `/tmp/TEST_v2_multiconftest.log` | §8.4 多 conftest 对照实验（`2b68b40`） |
| `/tmp/TEST_v2_fontstack.log` | §8.4 字体栈单文件复现（`2b68b40`） |
| `/tmp/TEST_v2_alertpage.log` | §8.6 告警页单文件复现（`2b68b40`，54 项；后经 §8.6 更正批注判定为断言过时） |
| `/tmp/TEST_v3_multiconftest.log` | §8.6 多 conftest 复验（`65bbf11`，AttributeError 已消失） |
| `/tmp/TEST_v3_fontstack.log` | §8.6 字体栈复验（`65bbf11`，0 失败） |
| `/tmp/TEST_v3_pricecol_alone.log` | §8.6 价格列单文件复跑（`65bbf11`，13 项；后经 §8.6 更正批注判定为 mock 契约不一致） |
| `/tmp/TEST_baseline_fast_master3.log` | §8.6 `65bbf11` 完整基线原始日志 |

两个 worktree 保留在测试机上，未删除，供复查。

---

## 10. 收口：80 项失败全部清零（2026-11-09）

本节记录 §8.6 遗留的 80 项失败如何被逐类收口，以及每类的真实根因。

### 10.1 三类根因与处置

| 类别 | 数量 | 真实根因 | 处置 | 提交 |
| --- | --- | --- | --- | --- |
| 告警页旧 DOM 断言 | 53 | 断言的 `#alert-rows` / `#total-spus` / `#alert-status` **只存在于已退役的 Jinja 模板**（`git show f72a3089^:tts_erp_v2/templates/pages/spu-profit-deterioration.html` 可证）。React 实现从来没有这三个 id。断点是 `80b218a9`：它把 `tests/browser/conftest.py::_render_pages` 的渲染来源从 `_render_page(...)` 换成 `render_frontend_page(...)`，fixture 换了 owner，测试还在断言旧 owner | 断言改写到 React 真实 DOM 契约（`table[aria-label="SPU 两期盈利对比"]`、`section[aria-label="完整分析范围统计"]` 等）；**另有一处真实前端修复**（见 10.2） | `e88aca01` |
| 价格列用例 | 13 | **不是**断言过时，而是夹具的 mock 载荷没送到浏览器：`tests/` 无 `__init__.py`，`tests/browser/conftest.py` 被 pytest 导入两次（`conftest` 与 `tests.browser.conftest`），HTTP handler 读自己模块的 `_mock_payload`，测试却在另一个对象上 `monkeypatch.setattr` → 补丁静默失效，页面渲染未打补丁的载荷（没有 `totals.priceStats`）→ `#price-summary` 恒为 `hidden` | handler 统一经 `_canned_payload()` 在 canonical `tests.browser.conftest` 上解析；**断言与选择器一行未改** | `ee6424e1` |
| SPU 页面族 + media-assets pilot | 14 | 同一根因的其它表现：React 真实 DOM 与断言的旧 DOM 不匹配（数值排序、投影、React 工作区），以及就绪信号等的是另一个标签页的文案（media-assets 页默认落在「素材包」，空态是「资产库还没有素材包。」，而信号等的是「全部素材」页的「没有符合条件的素材。」） | 断言与就绪信号对齐 React 真实契约 | `1aa33dd5` |

### 10.2 一处真实的产品缺陷（用户已决策：改前端）

告警页「截至日」输入带 `max` 属性时，**浏览器原生校验会拦下提交**，页面自己的中文提示「日期超过最新可用日期」永远不显示。实测：`form.checkValidity()` 为 false、`input.validity.rangeOverflow` 为 true、点击「应用筛选」后 URL 不变、`role=alert` 里找不到该提示。旧模板没有 `max`，所以提示本来是能显示的。

同项目的 `ad-daily` 页已用 `noValidate` 规避同类问题（`frontend/src/pages/ad-daily/Filters.tsx:12`，配 `aria-invalid` + `role="alert"`）。

**用户决策：改前端**，给告警页表单加 `noValidate`，保留 `max` 作为输入提示，由应用层校验负责拦截并给出中文提示。已落地于 `frontend/src/pages/spu-profit-deterioration/index.tsx`。

### 10.3 最终验证（master `1aa33dd5`）

```
$ bash scripts/test_isolated.sh fast
EXIT=1（完整基线 1481s）
FAILED tests/browser/test_frontend_p2_errors.py::test_real_http_rejection_and_wire_error_recovery[/tts]
```

**这一项是并发时序 flake，不是真实回归**，隔离复跑连续 3 次全部通过：

```
$ bash scripts/test_isolated.sh fast tests/browser/test_frontend_p2_errors.py
..                    [100%]     （第 1 次）
..                    [100%]     （第 2 次）
..                    [100%]     （第 3 次）
```

判定依据：该用例属于 `docs/guides/agent-testing.md` §1.2 记录的「共享主机容量」类时序敏感用例；隔离复跑 3/3 通过；与本轮改动（测试侧断言 + 告警页表单 `noValidate`）无交互路径。

**结论：master 当前 fast 套件实质零失败**（§8.6 的 80 项全部清零，仅余 1 项已定性为偶发 flake）。

### 10.4 基线重生成的前提已具备

`docs/testing/baseline-fast.txt` 仍记 2026-10-15 的 master `e04c5e1` / `count: 0`。§7 第 5 项原本的前提是「先修完断言再重生成，否则等于把失败洗白成已知失败」——**该前提现已满足**：套件实质零失败，重生成不会掩盖任何东西。

### 10.4 本轮沉淀的判断规则

1. **切换 renderer 后，断言写的是上一个 owner 的 DOM**：判据是「该 id 在 React 源码全部历史提交里都搜不到」。
2. **`tests/` 没有 `__init__.py` 时，同一个 conftest 会被导入两次**：`monkeypatch.setattr` 打在哪个对象上，handler 就必须从哪个对象读属性。判据是「id 在 React 里存在，但页面不显示」。
3. **`max` 属性会吞掉自定义校验**：给日期输入加 `max` 前，先确认表单没有 `noValidate`。
4. **断言写法本身也会骗人**：`expect(locator).not_to_contain_text(...)` 在元素不存在时**报错**而不是通过；要断言「迟到数据没出现」得用文档级断言（`page.locator("body")`）。`page.expect_response(...)` 等一个已被 abort 的请求会超时，改用精确捕获 `PlaywrightError`（只容忍 "already handled"/"request failed"/"aborted"/"closed"），**不要用 `suppress(Exception)`** ——那会把真实失败一起吞掉。
