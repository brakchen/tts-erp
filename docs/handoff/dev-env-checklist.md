# tts-erp 开发环境自检清单

> **适用范围**：HP desk 测试机（`schan@192.168.1.2`，SSH alias `hp-desk`）上的 tts-erp 仓库。
> **证据来源**：2026-11-08 由开发工程师在 `hp-desk` 上按本清单顺序实跑一遍，每条命令都记录了真实输出（见 §11）。
> **读者**：拿到一个开发任务、需要在新 worktree 上跑通测试的人。

---

## 0. 开工前的硬性边界（先读，别跳过）

来自 `AGENTS.md` §3 和 §7，这些不是建议，是红线：

| 边界 | 说明 |
| --- | --- |
| 测试唯一入口 | `bash scripts/test_isolated.sh ...`。没有 direct runner，也没有共享库 fallback |
| 禁止连生产形状库 | 绝不直接跑 `tts_erp` / `tts_erp_prod`；也**不要**把 `TTS_ERP_TEST_OFF=1` 当免检开关 |
| 禁止对生产跑迁移 | `alembic upgrade` 只允许在隔离 clone 上验证，生产迁移由人工操作 |
| 每个任务独立分支 | 从 `origin/master` 开独立 worktree/分支，不在 master 主工作区写代码 |
| 暂存禁用通配 | 绝不 `git add -A` / `git add .`，只显式列出自己的文件路径 |
| 改后台代码要重启 | 改 `tts_erp_v2/jobs/`、`sync_worker/`、`analytics/` 后必须 `systemctl --user restart tts-erp-sync.service` |
| 凭证走统一出口 | 只用 `tts_erp_v2.proxy.token_service`，不直接解密 `integration.credentials` |

> ⚠ **一条容易踩的坑**：worktree 里的 `.venv`、`.env.test`、`frontend/node_modules` 是 **软链接或 gitignore 文件**，
> `git add -A` 会把 `.venv` 软链一起扫进提交，合到 master 时会把同事真实的 venv 覆盖掉。

---

## 1. 阶段 A — 读文档（离线，Windows 本地就能做）

在开始连机器之前，先把这些读完（顺序固定）：

| # | 文档 | 什么时候必须读 |
| --- | --- | --- |
| 1 | `AGENTS.md` | 每次都要，**完整读完** |
| 2 | `README.md` | 每次都要 |
| 3 | `docs/guides/agent-git-workflow.md` | 涉及 worktree / 分支 / 交接时 |
| 4 | `docs/guides/agent-safety.md` | 改数据库、凭证、迁移、鉴权前 |
| 5 | `docs/guides/frontend-development.md` | 改前端前 |
| 6 | `docs/api/dumps-data-contract.md` | 改 dump 接口前 |
| 7 | `docs/guides/agent-testing.md` | 涉及基线、夹具、测试库时 |

本地代码副本（只读代码 / 编辑文档）：`F:\MiniMax Work Result\agents\mavis\workspace\tts-erp`。

---

## 2. 阶段 B — 连通性自检

```bash
ssh hp-desk "echo SSH_OK; hostname; whoami; pwd"
```

**预期输出**（实测 2026-11-08）：

```
SSH_OK
schan-HP-ProDesk-400-G2-MINI
schan
/home/schan
```

- 连不上先确认走的是 `hp-desk` 这个 alias（`~/.ssh/config` 里已配 `schan@192.168.1.2`）。
- 旧地址 `192.168.47.129` 已失效，不要再用。
- 登录后 `pwd` 应是 `/home/schan`，仓库在 `/home/schan/tts-erp`。

---

## 3. 阶段 C — 仓库同步状态

```bash
cd /home/schan/tts-erp
git fetch origin
git status --short --branch
git rev-parse HEAD
git rev-parse origin/master
git rev-list --left-right --count HEAD...origin/master
```

**判读**：

- `git rev-list --left-right --count HEAD...origin/master` 输出 **左=HEAD 独有的提交数，右=落后 master 的提交数**。
  `0	0` 表示与 origin/master 完全同步。
- `git status --short --branch` 里 `## master...origin/master` 后面没有 `[ahead N]` / `[behind N]` 也表示同步。

**实测输出**（2026-11-08）：

```
fetch_exit=0
## master...origin/master
 M plugins/ads-data-sync
 M plugins/monitor
AM scripts/envsetup/prepare-worktree.sh
?? node_modules/
0b92d97d99c1842db109146df6c58d114aaa1986   <- HEAD
0b92d97d99c1842db109146df6c58d114aaa1986   <- origin/master
0	0
```

> 主 checkout 是共享资源，上面这些脏条目**不归你管**，不要顺手清理、不要 stash、不要 reset。
> `git worktree list` 可以看到全机几十个别人的 worktree，属于正常状态。

---

## 4. 阶段 D — 测试依赖体检

```bash
cd /home/schan/tts-erp
bash scripts/envsetup/install-test-deps.sh --check
```

**预期输出**（实测，四项全 OK，退出码 0）：

```
== tts-erp 测试前置依赖安装 ==
   模式: check-only
-- 1/4 PostgreSQL 客户端工具
  [OK]   容器 postgres 内 psql/createdb/dropdb/pg_dump 已就绪
-- 2/4 venv 测试依赖
  [OK]   pytest 已就绪 (pytest 9.1.1)
-- 3/4 .env.test（测试连接串，gitignored）
  [OK]   .env.test 已存在
-- 4/4 测试库存在性（缺失时只提示，不自动建）
        [OK]   tts_erp_test_template
== 汇总 ==
  [OK]   测试前置依赖就绪。下一步：
        bash scripts/test_isolated.sh fast
```

**退出码**：0 = 就绪；非 0 = 有前置缺失，按提示修复。
缺东西时用 `sudo bash scripts/envsetup/install-test-deps.sh` 安装（该脚本幂等，可重复跑）。

---

## 5. 阶段 E — 模板库存在性与新鲜度

模板库 `tts_erp_test_template` 是隔离测试的"种子"：`test_isolated.sh` 每次用它 clone 一个一次性 `tts_erp_test_*` 库，跑完 drop。
**模板陈旧 = 测试用的是旧 schema**，表现是新列/新表不存在，而不是明确的报错。

### 5.1 存在性

阶段 D 的第 4 项已经覆盖。若要单独确认：

```bash
docker exec postgres psql -U postgres -Atc \
  "select datname from pg_database where datname like 'tts_erp%' order by datname;"
```

> ⚠ **容器名要精确匹配**。这台机上还有 zabbix 的 `postgres:17-alpine`，
> 用 `awk '$2 ~ /^postgres/'` 会抓到 `zabbix-db-1`，然后报 `role "postgres" does not exist`（真踩过）。
> 正确写法：`docker ps --format '{{.Names}} {{.Image}}' | awk '$2=="postgres:18" {print $1; exit}'`。

### 5.2 新鲜度（三方比对）

```bash
docker exec postgres psql -U postgres -d tts_erp_test_template -Atc "select version_num from alembic_version;"
docker exec postgres psql -U postgres -d tts_erp -Atc "select version_num from alembic_version;"
ls -t alembic/versions/*.py | head -3
```

**实测输出**（2026-11-08，三者一致 = 模板不陈旧）：

```
0076_media_packs      <- 模板库 tts_erp_test_template
0076_media_packs      <- 生产形状库 tts_erp（只读比对）
alembic/versions/0076_media_packs.py   <- 仓库最新迁移
```

**判读**：模板库版本必须等于仓库最新迁移。落后就是陈旧。

### 5.3 陈旧了怎么办 —— ⚠ 这条路当前在 HP desk 上是断的

标准修复命令：

```bash
bash scripts/test_isolated.sh --refresh-template fast
```

它内部走 `refresh_template()` → `source_prod_url()`，需要从 **`.env`** 读 `TTS_ERP_DB_URL`（生产形状库的连接串）。

**实测**：HP desk 上 `worktree/.env`、`/home/schan/.env`、`/home/schan/tts-erp/.env` **都不存在**，
`TTS_ERP_DB_URL_PROD_SOURCE` 也未设置 → 解析结果为空 → 刷新会以
`cannot resolve production schema source URL` 失败。

也就是说：**当前模板库一旦落后，除了人工提供生产连接串之外没有自助刷新路径。**
在模板刷新打通之前，遇到"模板陈旧"要**立即上报**，不要自己造库、也不要拿别的库顶替。

---

## 6. 阶段 F — 建 worktree 并准备环境

### 6.1 建 worktree

```bash
cd /home/schan/tts-erp
git fetch origin
git worktree add .worktrees/<slug> -b <prefix>/<slug> origin/master
# 例：git worktree add .worktrees/dev-env-checklist -b docs/dev-env-checklist origin/master
```

前缀用 `fix/` `feature/` `redesign/` `chore/` `docs/` + kebab-case 主题名。

### 6.2 准备测试环境（关键一步，漏了必失败）

```bash
cd /home/schan/tts-erp/.worktrees/<slug>
bash scripts/envsetup/prepare-worktree.sh
```

**实测输出**（2026-11-08，退出码 0）：

```
[LINK] python venv -> /home/schan/tts-erp/.venv
[LINK] .env.test -> /home/schan/tts-erp/.env.test
[INFO] 安装前端依赖（按本 worktree 的 package-lock.json）...
added 232 packages, and audited 233 packages in 7s
found 0 vulnerabilities
[OK]   前端依赖安装完成
[DONE] worktree 测试环境已就绪: /home/schan/tts-erp/.worktrees/dev-env-checklist
```

它做三件事：

| 目标 | 做法 | 为什么不能省 |
| --- | --- | --- |
| `.venv` | 软链到主 checkout | 新 worktree 没有解释器，`test_isolated.sh` 直接退出 |
| `.env.test` | 软链到主 checkout | 没有它就拿不到测试连接串 |
| `frontend/node_modules` | **按本 worktree 的 `package-lock.json` 执行 `npm ci`** | 必须真装，不能软链：Node ESM 会解析软链接的真实路径，主 checkout 与 worktree 的 `package.json` 版本不同会直接 `ERR_MODULE_NOT_FOUND` |

> ✅ **已解决（2026-11-08 20:50）**：`scripts/envsetup/prepare-worktree.sh` 已随 `047a1ac` 合入 `origin/master`。
> 新建的 worktree 里该脚本自动到位，直接跑 `bash scripts/envsetup/prepare-worktree.sh` 即可；处置记录见 §10.1。
> §9.6 记录的是脚本尚未进 master 时的历史现象，现在不再适用；当时的「从主 checkout 手工复制」办法会让 worktree 变脏、挡住 §8 的 DoD 基线命令，不该再走。

---

## 7. 阶段 G — 跑测试

```bash
cd /home/schan/tts-erp/.worktrees/<slug>
bash scripts/test_isolated.sh unit          # 最小可用范围
bash scripts/test_isolated.sh fast          # 更宽的快测集
bash scripts/test_isolated.sh unit tests/accounts/test_passwords.py   # 单文件
```

`unit` = `-m "layer_unit and not slow and not domain_migration"`。

**实测输出**（2026-11-08，完整跑通两次）：

```
[isolated-test] cloning tts_erp_test_template -> tts_erp_test_docs_dev_env_checklist_schan_2026110_5f50910f
[test-timing] database_clone=0.437s
[isolated-test] using existing template DB: tts_erp_test_template
[isolated-test] running: bash scripts/test.sh unit
........................................................................ [ 15%]
...
.....................................                                    [100%]
[test-timing] pytest_total=203.478s
[isolated-test] dropping ephemeral DB: tts_erp_test_docs_dev_env_checklist_schan_2026110_5f50910f
[test-timing] isolated_total=208.903s
```

**退出码**：第二次同命令运行实测 `EXIT_CODE=0`。

### 7.1 怎么判成败（重要）

> **本仓库的 pytest 输出里没有 `N passed` 汇总行**（`unit` 全量和小范围单文件两次实测均确认）。
> 判定规则：
>
> | 信号 | 含义 |
> | --- | --- |
> | 退出码 `0` | 通过 |
> | 退出码非 `0` | 失败 |
> | 进度行里出现 `F` / `E` | 失败 |
> | 出现 `FAILED ...` / `ERROR ...` 行 | 失败（这行只会在失败时打印） |
>
> **不要去 grep "N passed" 找结论**，找不到不代表没跑。

### 7.2 耗时预期

- `unit` 全量：约 **200–210 秒**，其中 `tests/frontend/test_components.py::test_frontend_components` 独占 **约 180 秒**
  （它会跑完整 vitest 套件）。看到 `call=180s` 属正常，不是卡死。
- 小范围单文件（如 `tests/accounts/test_passwords.py`）：约 **5 秒**。

### 7.3 临时库清理

正常结束会打印 `dropping ephemeral DB: <name>` 并删库。实测两次运行后该库均已从 `pg_database` 消失。

---

## 8. 阶段 H — DoD 基线对照

```bash
bash scripts/test_baseline.sh --scope auto --base "$target_sha"
```

- `$target_sha` = **精确同步到的 master SHA**（不是 `origin/master` 这个名字）。
- `--base` 只能配 `--scope auto|frontend|backend` 用；`fast` 不接受 `--base`。
- 退出码：`0` = 没有新增稳定失败（DoD 通过）；`1` = 有新失败或基线陈旧；`2` = 用法错误。

### 8.1 ⚠ 前置条件：worktree 必须完全干净

```bash
git status --porcelain --untracked-files=all   # 必须为空
```

**实测**：当 worktree 里存在未跟踪文件（例如把未提交的 `prepare-worktree.sh` 拷进去）时：

```
[test-baseline] ERROR: scoped runs require a clean committed worktree
H1b_exit=2
```

> 注意 `--plan` **不会**触发这个检查——`--plan` 分支在到达检查之前就 `exec` 掉了选测脚本。
> 想预检选测范围可以用 `--plan`，但它不能代替"worktree 干净"这条约束。

### 8.2 文档类改动（base == head）

**实测**（干净 worktree，base 与 head 同为 `0b92d97`）：

```
[test-baseline] no application tests selected; perform any indicated documentation checks
H2b_exit=0
```

文档/配置类 lane 只需做自己的链接、语法、路径、命令检查，不需要跑应用测试集。

### 8.3 查看已提交基线

```bash
bash scripts/test_baseline.sh --show
```

**实测输出**：

```
# scope:   fast
# master:  e04c5e114573b2b6aca1da9c8acfcc8a514e1c6f
# updated: 2026-10-15T16:39:57Z
# count:   0
```

`count: 0` 表示 master 已知稳定失败为空 —— 你的 lane 不允许引入任何新失败。
基线是 **master 的属性**，只应在 master 自身变化时、从干净的 master worktree 用 `--update` 重新生成。

---

## 9. 故障排查

以下每一条都是**实际复现过**的，报错文本为真实输出。

### 9.1 缺 venv

**症状**（退出码 1）：

```
[isolated-test] ERROR: missing project Python; run test dependency setup
```

**根因**：新 worktree 没有 `.venv`（gitignored，不会随 checkout 带过来）。
**修复**：

```bash
bash scripts/envsetup/prepare-worktree.sh
# 或手工等价操作：
ln -sfn /home/schan/tts-erp/.venv .venv
```

**验证**：`test -x .venv/bin/python && echo OK`

---

### 9.2 缺 .env.test

**症状**（退出码 1）：

```
[isolated-test] ERROR: TTS_ERP_DB_URL_TEST is unset; create .env.test
```

**根因**：新 worktree 没有 `.env.test`（gitignored）。
**修复**：

```bash
bash scripts/envsetup/prepare-worktree.sh
# 或手工等价操作：
ln -sfn /home/schan/tts-erp/.env.test .env.test
```

**验证**：`grep -oE '^[A-Za-z0-9_]+=' .env.test` 能列出 `TTS_ERP_DB_URL_TEST` 和 `TTS_ERP_DB_URL`。

> `.env.test` 里的连接串指向 `tts_erp_v3_test`，**不是**生产形状库 `tts_erp`。值不要打印到日志或聊天里。

---

### 9.3 缺前端依赖

**症状**（退出码 1）：

```
FAILED tests/frontend/test_components.py::test_frontend_components - Assertio...
E       AssertionError: Frontend dependencies missing: run npm ci --prefix frontend before isolated tests
E       assert False
E        +  where False = is_dir()
E        +    where is_dir = ((PosixPath('.../frontend') / 'node_modules') / 'vitest').is_dir
tests/frontend/test_components.py:20: AssertionError
```

**根因**：worktree 没有 `frontend/node_modules`。
**修复**：

```bash
cd frontend && npm ci        # 必须按本 worktree 的 package-lock.json 装
```

**不要软链 `node_modules`**：Node 的 ESM 解析走软链接真实路径，主 checkout 与 worktree 的依赖版本不同会报
`ERR_MODULE_NOT_FOUND`。

---

### 9.4 checkout 落后 master

**症状**：改动像是"没生效"，或测试跑到旧代码；`git status` 显示 `[behind N]`。

**检出方法**（实测输出）：

```bash
git rev-list --left-right --count HEAD...origin/master
# 输出 "左=HEAD独有  右=落后master"
```

```
--- .worktrees/drop-miaoshou-dead-skips ---
1	1178
--- .worktrees/frontend-p4-costs ---
0	627
## feature/frontend-p4-costs...origin/master [behind 627]
```

**修复**：

```bash
git fetch origin
git merge --no-edit origin/master
# 有冲突时按双方意图解决，禁止 --ours / --theirs / -X theirs 一把梭
```

**验证**：`git rev-list --left-right --count HEAD...origin/master` 右侧归零，且 `git status --short --branch` 不再显示 `[behind N]`。

> 落后 master 的 worktree 里 `frontend/node_modules` 可能对应旧 `package-lock.json`。
> 同步后建议重跑 `npm ci`。

---

### 9.5 模板库陈旧

**症状**：新加的列/表在新库上不存在，报 `UndefinedColumn` / `relation does not exist`；
或 `test_isolated.sh` 打印 `[isolated-test] WARNING: source revision is absent; imported schema was not upgraded`。

**检出方法**（三方比对，见 §5.2）：

```bash
docker exec postgres psql -U postgres -d tts_erp_test_template -Atc "select version_num from alembic_version;"
ls -t alembic/versions/*.py | head -3
```

**修复**：

```bash
bash scripts/test_isolated.sh --refresh-template fast
```

**⚠ 当前受阻**：HP desk 上没有 `.env`，生产连接串解析不到空，刷新会以
`cannot resolve production schema source URL` 失败（§5.3 已给出实测依据）。
**遇到这种情况立即上报 owner，不要自建库替代、不要用别的库顶替。**

**绝不要**：在生产库上跑 `alembic upgrade` 来"修"模板问题。

---

### 9.6 附加：`prepare-worktree.sh` 本身不在新 worktree 里

**症状**：

```
bash: scripts/envsetup/prepare-worktree.sh: No such file or directory
```

**根因**：该脚本已在 `origin/master`（`047a1ac`）。本节记录的是脚本尚未进 master 时的现象，现已不适用。

**当前做法**：新建 worktree 后直接跑 `bash scripts/envsetup/prepare-worktree.sh`，脚本随 master 自动到位。

**历史绕行方式（已不需要）**：

```bash
cp /home/schan/tts-erp/scripts/envsetup/prepare-worktree.sh scripts/envsetup/prepare-worktree.sh
```

> 拷贝后 worktree 变脏，§8 的 DoD 基线命令会以 `scoped runs require a clean committed worktree`（退出码 2）失败。
> 根治办法是把该脚本提交到 master —— 见 §10.1（已解决）。

---

### 9.7 附加：残留的一次性克隆库

测试中断（被 kill、机器重启）会让 `tts_erp_test_*` 库留在 `pg_database` 里。

```bash
docker exec postgres psql -U postgres -Atc \
  "select datname, pg_size_pretty(pg_database_size(datname)) from pg_database where datname like 'tts_erp%' order by pg_database_size(datname) desc;"
```

实测 2026-11-08：除 `tts_erp_test_template` 外还有 **22 个** 残留库（`tts_erp_test_<分支>_<会话>_<时间戳>_<pid>` 形态，合计约 400 MB）。
其中 `tts_erp_test_template_*`、`tts_erp_test_video_publish_v*_template` 一类是别的 lane 刻意建的模板，**不要删**。
清理残留前先确认归属，且只删本次任务产生的库。

> **已清理（2026-11-08 20:55）**：22 个残留库已删除，template 基线库全部保留。
> 两个必须注意的坑见 §10.3。

---

## 10. 本次复核发现的问题（需 owner 处理）

### 10.1 `scripts/envsetup/prepare-worktree.sh` 未提交到 master —— 阻断性 ✅ **已解决（2026-11-08 20:50）**

> **处置结果**：由 Chief 用干净 worktree 完成合并并实测验收。
> - 提交：`bc2fbb7 chore: 新增 worktree 测试环境准备脚本`
> - 合并提交：`047a1ac docs: 修正测试服务器地址、新增 worktree 准备脚本与团队交接文档`
> - 当前 `origin/master` = `047a1ac`（记录时点 2026-11-08 20:50），`git ls-tree origin/master scripts/envsetup/` 已含 `prepare-worktree.sh`（mode 100755）
> - 实测：从该 master 新建 worktree，脚本自动到位并跑通（venv/.env.test 链接 + `npm ci` 232 包，退出码 0）
> - `AGENTS.md` §5 命令表已增「新 worktree 测试环境准备」一行
>
> **下面的原始记录保留，作为问题现象的存档。**

- 现象：主 checkout `git status` 显示 `AM scripts/envsetup/prepare-worktree.sh`；`git ls-tree origin/master scripts/envsetup/` 只有 `install.sh` 和 `install-test-deps.sh`。
- 影响：
  1. 任何从 `origin/master` 新建的 worktree 都**跑不了** `prepare-worktree.sh`（§6.2 的标准流程直接断掉）；
  2. 手工拷贝进去会让 worktree 变脏，DoD 基线命令退出码 2（§8.1 / §9.6）。
- 建议：把该脚本提交到 master（commit 类型 `chore`），并由 owner 同步更新 `AGENTS.md` §5 的命令表。

### 10.2 模板库刷新路径当前不可用 —— 高 ⚠️ **当前不阻断，但需在新增 migration 前解决**

> **Chief 复核（2026-11-08 21:00）**：
> - 模板库**当前不陈旧**。`tts_erp`（生产形状）与 `tts_erp_test_template` 均为 `0076_media_packs` / 306 张表，与 `alembic/versions/0076_media_packs.py` 三方对齐。
> - **重要陷阱**：`.env.test` 里的 `TTS_ERP_DB_URL` 和 `TTS_ERP_DB_URL_TEST` **都指向 `tts_erp_v3_test`**，不是生产形状库，**不能拿来当刷新源**。
> - 因此当前无需刷新。但只要仓库新增 migration 而模板库落后，就必须先解决生产 schema 源问题，否则 `--refresh-template` 必然失败。
> - 详细说明见 `docs/ops/tts-erp.md` 的「模板库刷新依赖生产 schema 源」。

- HP desk 上 `.env` 全都不存在，`TTS_ERP_DB_URL_PROD_SOURCE` 未设置 →
  `--refresh-template` 必然失败（§5.3 实测）。
- 建议：由运维在测试机提供只读的生产形状库连接串（写 `.env` 或设 `TTS_ERP_DB_URL_PROD_SOURCE`），并明确该串的权限边界。

### 10.3 30 个残留一次性克隆库 —— 低 ✅ **已清理（2026-11-08 20:55）**

> **处置结果**：清理了 22 个非 template 残留临时库，释放约 400 MB。
> 保留未动：`tts_erp`、`tts_erp_test_template`、全部 `*_template_*` 基线库（`template_ads_ack`、`template_price_data`、`template_price_e2e`、`template_spu_compare_0069`、`video_publish_v1x/v2x_template`、`monitor_health_template`）、`tts_erp_v3_test`。
>
> **两个坑（后续清理必读）**：
> 1. `psql -f -` 批量执行 `DROP DATABASE` 会包在单事务里，PG 报 "DROP DATABASE cannot run inside a transaction block"，**全部静默失败**。必须逐条单独执行。
> 2. 删除前必须确认**没有正在跑的 pytest / test_baseline.sh 进程**——本次清理时有一个 Bot 正在跑 fast 基线，其临时库被误删，已通知其重跑。只查 `test_isolated.sh` 进程是不够的。

- 约 400 MB，占用不大但会随时间累积；建议定期清理（§9.7）。

### 10.4 主 checkout 的既有脏条目 —— 提示，非本次引入

- `plugins/ads-data-sync`、`plugins/monitor` 两个 submodule 指针有改动；
- 仓库根目录有未跟踪的 `node_modules/`（8.3 MB，是 vite/vitest 缓存产物）。
- **另实测到主 checkout 会被并发会话改动**：复核期间（2026-11-08 20:38 前后）主 checkout 的暂存区从
  2 个 submodule + `prepare-worktree.sh` 变成了多出 `docs/handoff/` 下三个新增文档的暂存条目，
  说明有别的会话正在同一 checkout 上工作。`origin/master` 在此期间未变（仍为 `0b92d97`）。
- 这些**都不属于本次任务**，按 AGENTS.md §7「不要改动或 stash 别人的 WIP」保持原样，已上报。
  **推论：不要在主 checkout 上做 `git add` / `git commit` / `git stash`，哪怕只是"顺手"整理 —— 那是别人的 lane。**

---

## 11. 实测记录（2026-11-08，hp-desk）

| # | 检查项 | 命令 | 结果 |
| --- | --- | --- | --- |
| 1 | SSH 连通 | `ssh hp-desk "echo SSH_OK; hostname; whoami; pwd"` | OK，`schan-HP-ProDesk-400-G2-MINI` |
| 2 | 仓库同步 | `git fetch origin` + `git rev-list --left-right --count HEAD...origin/master` | `0	0`，HEAD = origin/master = `0b92d97d99c1842db109146df6c58d114aaa1986` |
| 3 | 依赖体检 | `bash scripts/envsetup/install-test-deps.sh --check` | 4/4 OK，退出码 0 |
| 4 | 模板库存在 | `install-test-deps.sh --check` 第 4 项 + `pg_database` 查询 | `tts_erp_test_template` 存在，13 MB，17 个业务 schema / 78 张表 |
| 5 | 模板新鲜度 | 模板库 vs `tts_erp` vs `alembic/versions/` | 三方均为 `0076_media_packs` |
| 6 | worktree 缺依赖 | 新建 worktree 后直接 `bash scripts/test_isolated.sh unit` | `missing project Python`，退出码 1 |
| 7 | 环境准备 | `bash scripts/envsetup/prepare-worktree.sh` | venv/.env.test 链接 + `npm ci` 232 包，退出码 0 |
| 8 | 隔离单测 | `bash scripts/test_isolated.sh unit` | 跑通，`[100%]`，`isolated_total=208.903s` |
| 9 | 隔离单测（复跑取退出码） | 同上 + 捕获退出码 | `EXIT_CODE=0` |
| 10 | 缺 .env.test | 临时移走 `.env.test` 后重跑 | `TTS_ERP_DB_URL_TEST is unset; create .env.test`，退出码 1 |
| 11 | 缺前端依赖 | 临时移走 `frontend/node_modules` 后重跑 | `AssertionError: Frontend dependencies missing: run npm ci --prefix frontend`，退出码 1 |
| 12 | 落后 master 检测 | 对既有旧 worktree 跑 `rev-list --left-right --count` | `1	1178`、`0	627` |
| 13 | 模板刷新前置 | 只解析 prod URL（不触发刷新） | 解析为空 → `cannot resolve production schema source URL` |
| 14 | DoD 基线（脏 worktree） | `test_baseline.sh --scope auto --base 0b92d97` | `ERROR: scoped runs require a clean committed worktree`，退出码 2 |
| 15 | DoD 基线（干净 worktree） | 同上 | `no application tests selected; perform any indicated documentation checks`，退出码 0 |
| 16 | 基线查看 | `bash scripts/test_baseline.sh --show` | scope `fast`，master `e04c5e1...`，`count: 0` |

**Chief 补充实测（2026-11-08 21:00）**

| # | 检查项 | 命令 | 结果 |
| --- | --- | --- | --- |
| 17 | 模板库表数对比 | 分别查 `tts_erp` 与 `tts_erp_test_template` 的 `information_schema.tables` | 均为 306 张（排除 `pg_catalog`/`information_schema`） |
| 18 | 生产形状库 alembic | `psql -d tts_erp -c "SELECT version_num FROM public.alembic_version;"` | `0076_media_packs` |
| 19 | 模板库 alembic | `psql -d tts_erp_test_template -c "..."` | `0076_media_packs`（与生产形状库一致，模板不陈旧） |
| 20 | `.env.test` 实际指向 | 解析 `.env.test` 两个变量（脱敏） | `TTS_ERP_DB_URL` 与 `TTS_ERP_DB_URL_TEST` **都指向 `tts_erp_v3_test`**，不是生产形状库，不能当刷新源 |
| 21 | 脚本合并验收 | 从 `origin/master`（`047a1ac`）新建 worktree 后跑 `prepare-worktree.sh` | 脚本自动到位，venv/.env.test 链接 + `npm ci` 232 包，退出码 0 |
| 22 | 残留库清理 | 逐条 `DROP DATABASE ... WITH (FORCE)` | 22 个删除成功，10 个 template 基线库 + `tts_erp` + `tts_erp_v3_test` 保留 |

---

## 12. 一页速查版

```bash
# 0 连通
ssh hp-desk "echo SSH_OK; hostname"
# 1 同步
cd /home/schan/tts-erp && git fetch origin && git rev-list --left-right --count HEAD...origin/master
# 2 依赖
bash scripts/envsetup/install-test-deps.sh --check
# 3 模板新鲜度
docker exec postgres psql -U postgres -d tts_erp_test_template -Atc "select version_num from alembic_version;"
# 4 建 worktree + 备环境
git worktree add .worktrees/<slug> -b <prefix>/<slug> origin/master
cd .worktrees/<slug> && bash scripts/envsetup/prepare-worktree.sh
# 5 跑测试（看退出码，别找 "N passed"）
bash scripts/test_isolated.sh unit; echo "EXIT=$?"
# 6 DoD（要求 worktree 干净）
git status --porcelain --untracked-files=all   # 必须为空
bash scripts/test_baseline.sh --scope auto --base "$target_sha"
```
