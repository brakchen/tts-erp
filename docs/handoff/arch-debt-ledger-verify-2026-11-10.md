# 台账 owner 身份与契约检查：验证记录（2026-11-10）

> 执行人：测试工程师（bot_d330a0b9a748）｜委托：Chief
> 测试机：HP desk `schan@192.168.1.2`（`ssh hp-desk`）
> 仓库：`/home/schan/tts-erp`，本次工作在 worktree
> `/home/schan/tts-erp/.worktrees/ledger-to-master`（分支 `chore/arch-debt-ledger-to-master`）
>
> 本记录中每条命令均在测试机真实执行；未执行的项目一律标注「未执行」。

---

## 0. 结论速览

| 项 | 结论 |
| --- | --- |
| ① 成果保全 | **完成**。两个本地分支已推到远端新分支名，worktree 已从 `/tmp` 挪到 `/home/schan` 持久位置，master 未动 |
| ② 台账六字段 | **完成 24/24**。owner_ref / owner_blob / symbol / line_digest / 可执行检查引用 / 判据类型，全部落到真列并可被机器解析 |
| ③ 契约检查器去恒绿 | **完成**。旧版无条件 `return 0`；现版缺字段 / 字段形状错 / owner 身份写错 → 退出 1。实测三种破坏均 exit 1 |
| ④ 进度数字 | **引用了已落地可执行检查的 rule = 24/24 = 1.0**；owner 侧判据成立 **19/24**（复跑后），5 项判红且**逐条对应真实未收口的债务**。AD-JOB-002 已随 release 树前进自动转绿，见 §10 |
| ⑤ postgres infra | 三件（`container-spec.yaml` / `rebuild.sh` / `README.md`）已在本分支；本轮新增 `scripts/infra/runtime-facts.yaml` 与之同线 |
| 复跑（release 树前进后） | 生产侧 owner 已重指 `1e6c77ee`；AD-JOB-002 判据重写为「锚实现体」并双向验证通过；主机事实表同步了部署 commit。详见 §10 |

**并且纠正了两处既有错误**：

1. `origin/chore/arch-debt-ledger-to-master` **在委托时并不存在**（`git rev-parse --verify` 失败）⇒ STOP_STATE 里「在 origin 上」这条记录是错的。复核期间它被别的 actor 建起来了（指向同一 commit）。
2. `82a3bd1b`（选测器 `LEGACY_FRONTEND` 遗留引用的修复）**已进 master**，收集期 ImportError 这个历史问题已经不存在。

---

## 1. 委托范围与实况核对

委托的五件事：① 两分支安全上远端；② 台账 24 条 rule 补齐六字段；③ 契约检查器缺字段即非零退出；
④ 进度判据给数字；⑤ `scripts/infra/postgres/` 三件并入同一条线。

### 1.1 远端实况（委托时的核对）

```
$ git rev-parse --verify --quiet origin/chore/arch-debt-ledger-to-master ; echo $?
1                                   # ⇒ 不存在
$ git rev-parse --verify --quiet origin/chore/check-jobs-actually-run
13dc3ebbc3860ac7d0560e131f52c0362b1b62c3
$ git merge-base --is-ancestor 82a3bd1b origin/master && echo IN_MASTER
IN_MASTER
$ git log --oneline -1 82a3bd1b
82a3bd1b fix: 移除选测器里遗留的已退役前端路径引用
```

### 1.2 基线现实（与委托一致，且已复核）

master 的 `docs/testing/baseline-fast.txt` 是**空的**（`count: 0`，最后更新 2026-10-15，早于 P6 退役），
所以任何 `--scope fast` 都会把 80 个存量浏览器测试失败报成 NEW failures 并退出 1。
**master 没有有效稳定基线** ⇒ 「不得引入新失败」不能用基线驱动的退出码判定，本轮改用对照跑（§7）。

---

## 2. ① 成果保全

### 2.1 worktree 从 /tmp 挪到持久位置

```
$ git worktree move /tmp/ledger-to-master /home/schan/tts-erp/.worktrees/ledger-to-master
MOVE_EXIT=0
$ git worktree list | grep ledger
/home/schan/tts-erp/.worktrees/ledger-to-master   cc50fcba [chore/arch-debt-ledger-to-master]
$ ls -d /tmp/ledger-to-master
ls: cannot access '/tmp/ledger-to-master': No such file or directory
```

### 2.2 备份 bundle 复核（Chief 已做的保全）

```
$ git bundle verify /home/schan/tts-erp-ops-backups/20261110_100000_ledger-local-bundle/ledger-local-branches.bundle
... is okay
The bundle contains these 2 refs:
cc50fcba1b498a8e749bf74275fd83826e2d3be8 refs/heads/chore/arch-debt-ledger-to-master
7bab448617cac1ff23625bc30991be91511d329e refs/heads/chore/check-jobs-actually-run
The bundle records a complete history.
```

### 2.3 推送到远端（新建分支名，不覆盖已有）

推之前先确认目标名在远端**不存在**，且没有用 force：

```
$ for b in chore/arch-debt-ledger-20261110 chore/check-jobs-actually-run-20261110; do
    git ls-remote --exit-code --heads origin "$b" >/dev/null 2>&1 && echo EXISTS-ABORT || echo FREE; done
chore/arch-debt-ledger-20261110               FREE
chore/check-jobs-actually-run-20261110        FREE

$ git push origin chore/arch-debt-ledger-to-master:refs/heads/chore/arch-debt-ledger-20261110
 * [new branch]        chore/arch-debt-ledger-to-master -> chore/arch-debt-ledger-20261110
$ git push origin chore/check-jobs-actually-run:refs/heads/chore/check-jobs-actually-run-20261110
 * [new branch]        chore/check-jobs-actually-run -> chore/check-jobs-actually-run-20261110
```

**最终远端状态**（`git ls-remote --heads origin` 直查）：

| 远端分支 | SHA | 说明 |
| --- | --- | --- |
| `chore/arch-debt-ledger-20261110` | `52929fa0` | 本轮工作（含两件新提交） |
| `chore/arch-debt-ledger-to-master` | `52929fa0` | 规范名，同样指向本轮工作 |
| `chore/check-jobs-actually-run-20261110` | `7bab4486` | 领先原远端 13 个提交，快进 |
| `master` | `1fa3ead7` | **未动**（全程未被推送） |

### 2.4 一处并发事实（必须记录）

复核期间 `refs/heads/chore/check-jobs-actually-run` 被**别的 actor**移动过，
先后观察到 `13dc3ebb` → `7bab4486` → `a7726975` → `13dc3ebb`。
本轮的两次 push 的 refspec 都只写新分支名（输出为 `[new branch]`），
**没有触碰原分支**；原分支的移动不是本轮造成的。
本轮内容保存在 `chore/check-jobs-actually-run-20261110`（`7bab4486`），不受影响。

---

## 3. ② 台账六字段

### 3.1 为什么必须是「真列」

台账是 YAML，但下游按 JSONB 消费——JSONB 没有 schema，**字段在不在、值对不对，检查器必须自己解析**，
不能靠字段名的形状去猜。本轮新增 `scripts/check_ledger_owners.py` 就是这个解析器：

| 字段 | 机器怎么判 | 判红条件 |
| --- | --- | --- |
| `owner_ref` | `git rev-parse <ref>:<path>` | ref/path 解析不出 |
| `owner_blob` | 同上，直接比对对象 id | 与实际 blob 不一致 |
| `symbol` | 在该 blob 里定位定义行 | 那一行不含该 symbol |
| `line_digest` | `sha256(该行去空白)` 前 16 位 | 与实际算出的不一致 |
| 可执行检查引用 | `check_ref` 指向的文件必须存在；`verification.command` 引用的脚本必须存在 | 文件不存在 / command 缺失 |
| 判据类型 | `scope_assertion.kind` ∈ {location, count, branch} 且 `method` 非空 | 类型非法 / method 缺失 |

`line_digest` 的算法是从台账首条反推并复算验证的：

```
$ git show 50978019...:tts_erp_v2/runtime_settings.py | sed -n '226p'
    def get_setting_facts(
$ python3 -c "import hashlib;print(hashlib.sha256('def get_setting_facts('.encode()).hexdigest()[:16])"
41822decf73a09b0        # 与台账里的 sha256:41822decf73a09b0 一致
```

### 3.2 owner 落在哪棵树

owner_ref 不是一律取生产树，按「文件在哪个树里真的存在」决定：

| 树 | 用于 | 原因 |
| --- | --- | --- |
| 生产 `50978019f39c4e6ea3671872b540c8ea79644b00` | 生产树里存在的文件 | 台账记的是生产行为 |
| 目标 `5597dcca2139ae210873616b65081246d9b032e3` | `check_install_parity.sh` / `check_jobs_actually_run.py` / `schema_guard_probe.py` | 这三个文件**在生产树里不存在**（目标树独有） |
| 本分支 `70091a40`（新增提交） | 四条仓库外事实 | 见 §3.3 |

### 3.3 四条仓库外事实：先固化进仓库，才有 git owner

`AD-DEPLOY-002` / `AD-ALERT-003` / `AD-NET-001` / `AD-NET-002` 的 owner 原本是
`~/.config/systemd/user/...`、`/etc/systemd/system/...`、`/opt/easytier/...`——
**全仓 `git grep -i` 搜不到**（`pgappname` / `15888` / `rpc-portal` / `easytier` 在 master 与生产树里均 0 命中），
所以它们写不出 owner_ref/owner_blob/symbol/line_digest，也无法被机器判定。

按 `scripts/infra/postgres/container-spec.yaml` 已经用过的同一理由
（「端口绑定的 owner 曾经只存在于服务器的命令历史里，仓库无声明 ⇒ 收口只是一次性的」），
本轮新增 `scripts/infra/runtime-facts.yaml`，把四条运行时事实固化进仓库，每条带 provenance 与内容摘要：

```yaml
  pgappname_drop_in:
    host: hp-desk
    path: ~/.config/systemd/user/tts-erp.service.d/96-pgappname.conf
    content_sha256: e38874658bb41d2c04fc8914af5f358edae9cfd33b0aff376db57d77f776673c
    assert:
      - 'Environment=PGAPPNAME=tts_erp_prod-hpdesk_tts-erp'
```

四条事实的来源都是**实测**（不是期望值）：

```
$ sha256sum ~/.config/systemd/user/tts-erp.service.d/96-pgappname.conf
e38874658bb41d2c04fc8914af5f358edae9cfd33b0aff376db57d77f776673c
$ ssh tts-erp-prod "sha256sum /etc/systemd/system/easytier@default.service.d/rpc-portal-loopback.conf"
9986df892ad47964a2dbd3935358782b2d6cc5c252985d135a67bddfa7c51024
$ ssh tts-erp-prod "grep -nE 'rpc_portal|bind_addr' /opt/easytier/config/default.conf"
8:rpc_portal = "127.0.0.1:15888"
20:bind_addr = "0.0.0.0:2222"
25:bind_addr = "0.0.0.0:9877"
```

⇒ `AD-NET-002` 的暴露面是**实测的两个 0.0.0.0 绑定**（2222 / 9877），不是推测。

### 3.4 判据类型：三类，method 分开

| kind | 数量 | method 形态 | 判据本体 |
| --- | --- | --- | --- |
| `location` | 10 | 符号定义行必须就是被钉住的那一行 | 可选 `assert_contains` / `forbid_contains` / `absent_in_ref` / `equal_blob_ref` |
| `count` | 2 | 模式在 owner blob 里的出现次数必须等于声明值 | `pattern` + `expected_count` |
| `branch` | 12 | 声明的分支锚点必须全部出现在检查范围内 | `anchors`（+ 可选 `forbid_contains`） |

**关键设计：判据断言的是「期望状态」，不是「当前状态」。**
否则就是假绿——一个断言当前坏状态的检查会永远绿，等于没检查。例如：

- `AD-NET-002` 期望「通配绑定数 = 0」，实测 2 ⇒ **判红**（收口后改文件记录才转绿）
- `AD-BACKUP-001` 期望「gzip -t 与 pg_restore --list 两道校验都在」，实测 0 ⇒ **判红**
- `AD-DEPLOY-004` 期望「env_get 里不得再有 `|| true`」（fail-open 标记），实测有 ⇒ **判红**
- `AD-JOB-002` 期望「owner 树里至少有 kill/SIGKILL/timeout 之一」，实测 0 ⇒ **判红**

### 3.5 count 判据必须支持「下限」语义（本轮修正自己的一处缺陷）

初稿把「超时 kill 机制必须存在」写成 `expected_count: 1`（**恰好** 1 次）。
复核别人那份「OOM P0 已进 master」的报告时发现：master 的 `scheduler.py` 里
`kill|SIGKILL|timeout` 是 **3 次命中**（含 docstring 里对 SIGKILL/SIGTERM 的提及），
`1caa296b` 修的就是「父进程把被杀子进程遗留的 running 行结成 failed」。

**⇒ 写死「恰好 1」的话，修复落地后生产树会有 3 处命中，判据仍然判红 —— 判据本身是坏的。**
这正是「绿/红不等于进度」的另一个形态：红得没有道理。

修正：`check_ledger_owners.py` 的 count 判据支持三种语义，判据自己选：

| 字段 | 语义 | 用在哪 |
| --- | --- | --- |
| `expected_count` | **恰好** N 次 | AD-NET-002（期望通配绑定数 = 0）、AD-ALERT-003（期望 absent 事实数 = 0） |
| `min_count` | **至少** N 次 | AD-JOB-002（期望 kill/timeout ≥ 1）、AD-BACKUP-001（期望两道校验 ≥ 2） |
| `max_count` | 至多 N 次 | 预留 |

**前向验证**（模拟「修复已落地」：把 AD-JOB-002 的 owner 指到含 OOM 修复的 master）：

```
模拟：AD-JOB-002 的 owner 指到 master（含 OOM 修复）
  blob: 4ef41312f182a157d1c94686c37e7221f2295134  line: 539  sha256:2138e4b0d08e459b
$ python3 scripts/check_ledger_owners.py --ledger /tmp/TEST_ledger_job002_on_master.yaml --rule AD-JOB-002
  检查规则数 = 1
  通过 = 1   失败 = 0
EXIT=0
```

⇒ 修复落地后判据**如期转绿**；修正前（`恰好 1`）这里会是 3≠1 ⇒ 继续判红。

### 3.6 一处「差点改错」的地方：target_ref 不是 master

台账的 `base.target_ref` = `5597dcca`，看起来像「master 的某个旧 SHA」。
复核时差点把它改指当前 master（`e3e85e0e`），**先查了一步**：

```
$ git merge-base --is-ancestor 5597dcca origin/master ; echo $?
1                                   # 不在 master 上
$ git rev-list --count 5597dcca..origin/master
63                                  # master 比它超前 63 个提交，且已分叉
$ git cat-file -e origin/master:scripts/check_jobs_actually_run.py
MISSING_ON_MASTER                   # 那三个 target-only 文件在 master 上不存在
```

⇒ 台账的两棵树是**生产 release 线**（`50978019`）与**目标 inspection 线**（`5597dcca`），
不是「master 的旧版本」。**不能把 target_ref 改指 master**，否则三条 rule 的 owner 解析不出来。
已作为「别误改」记在这里。

⇒ 同时得到一条维护规则：**这两条线各自前进时，`base.*_ref` 与相关 `owner_ref` 必须重指**，
否则检查器会以「owner 身份对不上」判红（契约退出 1）——这正是我们想要的显形。

---

## 4. ③ 契约检查器去恒绿

### 4.1 改了什么

旧版 `scripts/check_ledger_contract.py` 收集了 `missing_required_fields`、打印了警告，
然后 **`return 0`** —— 台账里再多规则、再多缺字段都不影响退出码。

现版：

- 退出码由三件事决定：**缺必填字段 / 字段形状不对 / owner 身份写错** ⇒ 退出 1
- **「判据不成立」（债务未收口）不影响契约退出码** —— 那是台账要报告的 finding，
  混进去会让契约永远判红，那才是真的恒红
- 五项计数（skipped / not_applicable / excluded / open_unverified / provisional_count）仍照常报出

### 4.2 反恒绿验证（在副本上做，真台账未动）

```
=== 破坏一：删掉一条的 line_digest ===
  ⚠ 缺必填字段: 1 条
  契约判定: 不通过
真实退出码=1

=== 破坏二：owner_blob 写错（字段齐但内容对不上）===
真实退出码=1

=== 破坏三：判据类型写成非法值 ===
真实退出码=1

=== 真台账未被破坏 ===
```

### 4.3 顺带修掉的两处「假红」

| 症状 | 根因 | 修法 |
| --- | --- | --- |
| 24 条报「缺 `in_target_chain`」 | `in_target_chain: false` 是合法取值（未判定 ⇒ 保守按 False），但旧代码用 `not row.get(f)` 判缺失 ⇒ False 被当成缺失 | 改成**存在性**判断：键不存在 / 值为 None / 空串才算缺 |
| 22 条报「缺 `unverified_preconditions`」 | 这些条目的值是 `null`（键存在、值为空） | 生成器把没有前提的条目显式写成 `[]`（**显式声明「无未验证前提」**，与「忘了写」不同）；空列表算存在 |

---

## 5. ④ 进度数字

```
$ python3 scripts/check_ledger_contract.py
  rule 总数 = 24
  五项计数: {'skipped': 0, 'not_applicable': 0, 'excluded': 0, 'open_unverified': 2, 'provisional_count': 2}
  verification 三态: {'green': 11, 'red': 13, 'absent': 0}
  进度: 引用了已落地可执行检查的 rule = 24/24 = 1.0
  契约判定: 通过
```

**进度 = 24/24**：24 条 rule 全部引用了一条**已落地、可执行**的检查
（`check_ref` = `scripts/check_ledger_owners.py --rule <ID>`，文件存在；`verification.command` 同源可执行）。

**但 24/24 不等于「都绿」。** owner 侧判据成立 **19/24**，5 项判红（复跑后的最新状态见 §10.3）：

```
$ python3 scripts/check_ledger_owners.py
  检查规则数 = 24
  通过 = 19   失败 = 5
  ⚠ AD-DEPLOY-004
      - branch 判据不成立：仍含被禁止的分支 ['|| true']
  ⚠ AD-ALERT-003
      - count 判据不成立：模式 'content_sha256: absent' 实际命中 1 次，声明 0 次
  ⚠ AD-BACKUP-001
      - count 判据不成立：模式 'gzip -t|pg_restore --list' 实际命中 0 次，声明 2 次
  ⚠ AD-NET-002
      - count 判据不成立：模式 'bind_addr = "0\.0\.0\.0:' 实际命中 2 次，声明 0 次
  ⚠ AD-BASE-002
      - location 判据不成立：5597dcca... 的 blob dbb5067f... 与 owner b73ffad1... 不相等
```

这 6 条**全部对应真实未收口的债务**，没有一条是误报：

| rule | 判红依据 | 性质 |
| --- | --- | --- |
| AD-DEPLOY-004 | `install.sh` 的 `env_get` 里仍有 `\|\| true`（凭据解析失败仍继续 = fail-open） | 环境/实现未收口 |
| AD-ALERT-003 | watchdog unit 的 `SuccessExitStatus=1` drop-in 尚未写入主机（事实表里记为 `absent`） | 环境未收口 |
| AD-JOB-002 | `scheduler.py` 里 kill/SIGKILL/timeout 出现 0 次 ⇒ 超时后 running 行不会被终结 | 代码未收口（**OOM P0 已进 master**，等 release 线合入后自动转绿，见 §3.5） |
| AD-BACKUP-001 | `pg_dump_hourly.sh` 里 `gzip -t` / `pg_restore --list` 出现 0 次 ⇒ 损坏备份也会被当成功 | 代码未收口 |
| AD-NET-002 | 207 公网机上仍有 2 个 `bind_addr = "0.0.0.0:` 绑定 | 环境未收口 |
| AD-BASE-002 | 生产树 `watchdog_sync.py` blob `b73ffad1` ≠ 目标树 `dbb5067f` ⇒ 收敛未完成 | 收敛未完成 |

其中 `AD-BASE-002` 的 `verification_state` 本来就是 `red`——机器判定与人工标注一致，属于**独立复核互相印证**。

---

## 6. ⑤ postgres infra

`scripts/infra/postgres/{container-spec.yaml,rebuild.sh,README.md}` 三件**已在本分支**
（`git diff --stat origin/master...HEAD` 可见），本轮未改动其内容，只做了一件事：
把同目录的 `scripts/infra/runtime-facts.yaml` 与之**并列成同一条线**——
两者的共同理由都写在各自文件头：

> 端口绑定的 owner 曾经只存在于服务器的命令历史里，仓库无声明 ⇒ 收口只是一次性的。

- `container-spec.yaml` 固化的是 **PG 容器**的端口绑定与卷布局；
- `runtime-facts.yaml` 固化的是 **四个仓库外运行时文件**的内容与摘要。

两者都是「把 owner 从服务器搬回仓库」，同一套思路。

---

## 7. 对照跑（因基线为空，不能用基线驱动的退出码）

方法（按委托给的基线现实）：

```bash
# 候选侧 = 本分支 HEAD 52929fa0
cd /home/schan/tts-erp/.worktrees/ledger-to-master
bash scripts/test_isolated.sh unit > /tmp/TEST_unit_candidate.log 2>&1
sed -n -E 's/^(FAILED|ERROR) ([^ ]+).*/\2/p' /tmp/TEST_unit_candidate.log | sort -u > /tmp/failures-candidate.txt

# base 侧 = origin/master 1fa3ead7
cd /home/schan/tts-erp/.worktrees/baseline-master-20261110
bash scripts/test_isolated.sh unit > /tmp/TEST_unit_base.log 2>&1
sed -n -E 's/^(FAILED|ERROR) ([^ ]+).*/\2/p' /tmp/TEST_unit_base.log | sort -u > /tmp/failures-base.txt

# 只在我这边出现的失败
comm -13 /tmp/failures-base.txt /tmp/failures-candidate.txt
```

### 7.1 先纠正对照基准（这一步很关键）

本分支**不是**基于当前 master：

```
$ git merge-base origin/master HEAD
81bbc24091fd41ca8012b63e9d2ae1203af3862b
$ git rev-list --count 81bbc240..origin/master
11          # 本分支落后 master 11 个提交
$ git rev-list --count 81bbc240..HEAD
4           # 领先 4 个提交
```

具体证据：master 的 `spu-profit-deterioration/index.tsx` 带 `noValidate`（修掉「max 属性吞掉自定义校验」），
**而本分支上的同一文件没有**（分支落后，回退掉了那个修复）。
⇒ 拿 master 当 base 比，比的是「我的改动 + 别人 11 个提交的差距」，**结论会失真**。
⇒ 因此跑**两个基准**：master（外部对照）与**分支自身 base `cc50fcba`**（我的改动的正确基准）。

而本轮改动本身的范围是精确的：

```
$ git diff --name-only cc50fcba..52929fa0
docs/architecture/arch-debt-ledger.yaml
scripts/check_ledger_contract.py
scripts/check_ledger_owners.py
scripts/infra/runtime-facts.yaml
scripts/oneoff_fill_ledger_fields.py
$ git diff --name-only cc50fcba..52929fa0 -- tests/ frontend/ | wc -l
0
```

### 7.2 两侧实测结果

跑了**两个基准**（理由见 §7.1）：

| 侧 | 命令 | 退出码 | 耗时 | 失败 id 数 |
| --- | --- | --- | --- | --- |
| 候选 `52929fa0` | `bash scripts/test_isolated.sh unit` | 1 | 283s | 1 |
| base `master 1fa3ead7` | 同上 | 0 | 219s | 0 |
| base `cc50fcba`（分支自身 base） | 同上 | 0 | 222s | 0 |

差集（两个基准都指向同一项）：

```
$ comm -13 /tmp/failures-base.txt /tmp/failures-candidate.txt
tests/frontend/test_components.py::test_frontend_components
$ comm -13 /tmp/failures-base-cc50fcba.txt /tmp/failures-candidate.txt
tests/frontend/test_components.py::test_frontend_components
$ comm -23 /tmp/failures-base.txt /tmp/failures-candidate.txt
（空）
```

### 7.3 这 1 项是并发偶发，不是回归（已隔离复跑定性）

失败形态与 `docs/testing/baseline-repair-2026-11-03.md` 记录的一致：组件 adapter 的 npm 子进程非零退出
（`assert process.returncode == 0` + vitest 输出转储）。

定位到真正失败的 spec：

```
$ tr '\r' '\n' < /tmp/TEST_unit_candidate.log | grep -oE "src/[a-zA-Z0-9/_-]+\.test\.tsx?" | sort | uniq -c
      5 src/pages/spu-profitability/index.test.tsx
```

该 spec 及其被测源码 `frontend/src/pages/spu-profitability/index.tsx` **不在本轮改动清单里**（见 §7.1），
两侧逐字节相同。

按 `docs/guides/agent-testing.md` §6「只复跑最小失败集合一次」，隔离复跑：

```
$ bash scripts/test_isolated.sh fast tests/frontend/test_components.py
0 个 FAILED/ERROR
[test-timing] test_execution=217.451s
[test-timing] isolated_total=221.588s
```

**同一 worktree、同一用例、单独跑 ⇒ 通过。**
⇒ 该项判定为**并发偶发**（机器 4 核，同一时间确有别的 lane 在跑同一个 unit 选择），不是本轮改动引入的回归。

### 7.4 判定

| 结论 | 依据 |
| --- | --- |
| **本轮改动零新增失败** | 候选侧唯一失败项经隔离复跑判定为并发偶发；本轮改动 5 个文件、0 个在 `tests/` 或 `frontend/` 下；base 侧同样 0 失败 |
| 基线驱动的退出码**不可用** | master 的 `baseline-fast.txt` 为空（`count: 0`），`--scope fast` 会把 80 个存量浏览器失败报成 NEW 并退出 1；本轮因此改用对照跑 |
| 本分支与 master 的差距**不属于本轮** | 差 11 个提交、含 master 的 `noValidate` 修复；本轮未触碰这些文件 |

### 7.5 并发说明（为什么必须串行 + 等安静窗口）

测试机只有 4 核，且本轮复核期间**始终有别的 lane 在跑同一个 `unit` 选择**：

```
$ ps -eo pid,etimes,args | grep -E "python.*pytest" | grep -v grep
1070418  159 /home/schan/tts-erp/.venv/bin/python .venv/bin/pytest ... -m layer_unit and not slow ...
1090417  157 /home/schan/tts-erp/.venv/bin/python .venv/bin/pytest ... tests/frontend/test_components.py
1091833  122 /home/schan/tts-erp/.worktrees/lane-oom-paging/.venv/bin/python -m pytest ... tests/api ...
```

因此本轮所有对照跑都按「**等并发数归零再开跑**」执行（脚本里 20 秒一轮地等），
并且两侧**串行**、不并发。第一次候选侧运行因另一 lane 在同一时刻起跑而被污染，
已作废重跑；第二次（11:16:49 起）才作为有效数据。

另外记一个容易踩的坑：**不能用 `grep -oE "[FE]"` 数失败**——
它会把页面 HTML 里的任意大写 E/F 都算进去（本轮一度读成「555 E」）。
正确口径是行首锚点 `^(FAILED|ERROR) `。

### 7.6 结论

**本轮改动零新增失败。** 依据三条互相独立：

1. **对照跑**：两个基准（master 与分支自身 base）的失败集合都是空；候选侧唯一的差集项经隔离复跑判定为并发偶发。
2. **隔离复跑**：同一 worktree、同一用例单独跑 ⇒ 0 失败。
3. **静态范围**：本轮改动 5 个文件，`tests/` 与 `frontend/` 下 **0 个文件**，且没有任何测试引用改动路径。

<!-- COMPARISON_RESULT -->


---

## 8. 未执行 / 未覆盖的部分

| 项 | 状态 | 原因 |
| --- | --- | --- |
| `bash scripts/test_baseline.sh --scope fast --base ...` 全量对照 | 未执行 | 基线为空（`count: 0`）⇒ 退出码不构成判据；且全量 fast 两侧各约 40 分钟。改用 `unit` 范围 + 两个基准对照（§7） |
| `--scope auto` 的 DoD 判定 | 未执行 | 本分支不涉及应用行为改动；且 auto 在 `head == base` 时是空计划、退出 0，不构成证据 |
| 第一次候选侧 unit 运行（10:50 那轮） | 已作废 | 期间另一 lane 并发跑同一 unit 选择，污染了结果；已重跑（11:16:49 起那轮为有效数据） |
| 6 项判红债务的修复 | 未执行 | 超出本次范围（收口归开发工程师）；本轮只让它们**可判定** |
| 本分支与 master 的 11 个提交差距 | 未处理 | 不属于本轮改动；其中含 master 的 `noValidate` 修复，分支上缺失。**合入 master 前需要先同步 master** |
| 生产库 / 生产主机上的写操作 | 未执行 | 硬边界；207 侧只做了只读 `sha256sum` / `grep` |
| `docs/testing/baseline-fast.txt` 重生成 | 未执行 | 属 master-only 操作，且当前 master 有 80 个存量失败，重生成会把它们固化进基线 —— 需要 Chief 先决定怎么处置那 80 项 |

---

## 9. 现场

| 路径 | 说明 |
| --- | --- |
| `/home/schan/tts-erp/.worktrees/ledger-to-master` | 本轮工作 worktree，HEAD `52929fa0` |
| `/home/schan/tts-erp/.worktrees/baseline-master-20261110` | 对照基准 A：`origin/master` @ `1fa3ead7` |
| `/home/schan/tts-erp/.worktrees/base-cc50fcba-20261110` | 对照基准 B：分支自身 base @ `cc50fcba` |
| `/home/schan/tts-erp-ops-backups/20261110_100000_ledger-local-bundle/` | Chief 的 bundle 备份（已 verify 通过） |
| `/tmp/TEST_unit_candidate.log` | 候选侧 unit 原始日志（283s） |
| `/tmp/TEST_unit_base.log` | base(master) unit 原始日志（219s） |
| `/tmp/TEST_unit_base_cc50fcba.log` | base(cc50fcba) unit 原始日志（222s） |
| `/tmp/TEST_rerun_components_candidate.log` | 组件用例隔离复跑原始日志（221.6s，0 失败） |
| `/tmp/failures-candidate.txt` / `/tmp/failures-base.txt` / `/tmp/failures-base-cc50fcba.txt` | 对照用的失败 id 集合 |

新增文件（均在分支 `chore/arch-debt-ledger-to-master`）：

- `scripts/check_ledger_owners.py` —— owner 身份与判据的机器检查（新增）
- `scripts/oneoff_fill_ledger_fields.py` —— 六字段填充器（一次性，可复跑）
- `scripts/infra/runtime-facts.yaml` —— 四条仓库外运行时事实的固化（新增）
- `scripts/check_ledger_contract.py` —— 改为缺字段即非零退出（修改）
- `docs/architecture/arch-debt-ledger.yaml` —— 24 条 rule 补齐六字段（修改）

提交：

- `70091a40` chore: 固化四条运行时事实，使 AD-DEPLOY/ALERT/NET 具备 git owner
- `52929fa0` fix: 台账 24 条 rule 补齐六字段，契约检查改为缺字段即非零退出
- `c1051075` docs: 新增台账 owner 身份与契约检查的验证记录
- `cb5eb7b3` fix: count 判据支持下限语义，避免修复落地后仍判红
- `31da674c` chore: 同步部署 commit 事实（已推进到 1e6c77ee）
- `058fac73` fix: 生产侧重指到 release 树 1e6c77ee，AD-JOB-002 判据改为锚实现体

远端分支（两个名字都指向同一 commit）：

- `origin/chore/arch-debt-ledger-20261110` = `058fac73`
- `origin/chore/arch-debt-ledger-to-master` = `058fac73`
- `origin/chore/check-jobs-actually-run` = `a7726975`（可靠性工程师推进的 tip，本来就在远端）
- `origin/chore/check-jobs-actually-run-20261110` = `7bab4486`（我推的快照）
- `origin/master` = `9ecea05f`（**本轮全程未推 master**）

---

## 10. 复跑（2026-11-10 晚）：release 树前进后的台账维护

触发原因：release 树 `/home/schan/tts-erp-releases/erp-runtime-secret-84ec0a49` 的 HEAD 已从 `50978019`
前进到 **`1e6c77ee`**（新增 `2b918d42` 流式分页 + `1e6c77ee` 父进程结行），
host 上 `97-deployed-commit.conf` 的部署 commit 也同步推进到了 `1e6c77ee`。
⇒ 台账的**生产侧整体失效**：19 条以生产树为 owner 的 rule 的 `owner_ref` / `owner_blob` / `line_digest` 都指向旧树。

### 10.1 三处漂移，三种不同处理

| # | 漂移 | 处理 |
| --- | --- | --- |
| 1 | 19 条 rule 的生产侧 owner 指向旧树 `50978019` | **重指到 `1e6c77ee`**，owner_blob / symbol_line / line_digest 全部重算（生成器 `--apply`） |
| 2 | AD-JOB-002 的判据口径不成立 | **重写判据**（见 §10.2） |
| 3 | `runtime-facts.yaml` 的 `deployed_commit_drop_in` 摘要过期 | 重新采集主机事实并同步（`content_sha256` 与断言值都变了） |

第 3 条的实测证据（其余四条主机事实未漂移）：

```
$ sha256sum ~/.config/systemd/user/tts-erp.service.d/97-deployed-commit.conf
acccfb7281bc9d048661e0cd859823280a698a6e40c32275cc6d57d9de9be715
$ grep DEPLOYED_COMMIT ~/.config/systemd/user/tts-erp.service.d/97-deployed-commit.conf
Environment=TTS_ERP_DEPLOYED_COMMIT=1e6c77ee15285d8298cfe890ff774b765f606c88
```

### 10.2 AD-JOB-002 的判据必须锚在实现体，不能计数散文

**原判据**：`pattern = kill|SIGKILL|timeout`，`min_count ≥ 1`。
**问题**：这条会被**文档字符串满足**。实测 release 树 `1e6c77ee` 的 3 处命中全部是散文：

```
553:    """Close the ``sync_jobs`` row a killed child left behind.
555:    A child that dies on SIGKILL (the cgroup OOM killer) or SIGTERM never
583:                "sync_jobs row (worker killed — OOM/SIGTERM); closed by the parent"
```

真正的判定逻辑是 `_close_orphaned_running_row`（L548），它**一个字都不含** `kill`——
它靠 `returncode` 判定。⇒ 计数口径既可能假绿（docstring 满足），也可能假红。

**新判据**：锚在实现符号的实现体上。

```
symbol  = _close_orphaned_running_row
kind    = branch, scope = block
anchors = ['row.status = "failed"', 'finished_at']
```

这两个锚点**只出现在实现体里**，docstring 与日志文案都不含 ⇒ 散文满足不了它。

**双向验证**（两个都是真实生产树，不是人造变异）：

```
正向（owner = 1e6c77ee，有实现）：
$ python3 scripts/check_ledger_owners.py --rule AD-JOB-002
  检查规则数 = 1 / 通过 = 1 / 失败 = 0
EXIT=0

反向（只把 owner_ref/owner_blob 换成 50978019，那棵树没有这个符号）：
  ⚠ AD-JOB-002
      - line_digest 对不上: 台账 sha256:4d934989840d1894 vs 实际 sha256:a8dca7e5b074d81f
      - 第 548 行不含 symbol '_close_orphaned_running_row'（钉住的不是符号定义行）
      - branch 判据不成立：缺分支锚点 ['row.status = "failed"', 'finished_at']
EXIT=1
```

⇒ **三路独立判红**（digest / 符号定位 / 实现体锚点），不是靠某一条凑出来的。

### 10.3 结果：AD-JOB-002 自动转绿

```
$ python3 scripts/check_ledger_owners.py
  检查规则数 = 24
  通过 = 19   失败 = 5
```

**19/24**（原 18/24）。AD-JOB-002 判红 → 判绿，**全程没有手工改这条 rule 的状态**：
release 树合入修复 → 重指 owner → 判据自己成立。这正是台账机制该有的样子。

剩余 5 项判红，逐条对应真实未收口的债务：

| rule | 判红依据 | 性质 |
| --- | --- | --- |
| AD-DEPLOY-004 | `install.sh` 的 `env_get` 里仍有 `\|\| true`（凭据解析失败仍继续 = fail-open） | 代码未收口 |
| AD-ALERT-003 | watchdog unit 的 `SuccessExitStatus=1` drop-in 尚未写入主机（事实表记 `absent`） | 环境未收口 |
| AD-BACKUP-001 | `pg_dump_hourly.sh` 里两道校验命中 0 次 ⇒ 损坏备份会被当成功 | 代码未收口 |
| AD-NET-002 | 207 公网机上仍有 2 个 `bind_addr = "0.0.0.0:` 绑定 | 环境未收口 |
| AD-BASE-002 | 生产树 `watchdog_sync.py` blob `b73ffad1` ≠ 目标树 `dbb5067f` ⇒ 收敛未完成 | 收敛未完成 |

### 10.4 ③ 契约检查器复验（仍然非零退出）

```
真台账                              退出码=0（期望 0）
missing_field（删 line_digest）      退出码=1（期望 1）
bad_blob（owner_blob 写错）          退出码=1（期望 1）
bad_kind（判据类型非法）             退出码=1（期望 1）
```

进度数字不变：**引用了已落地可执行检查的 rule = 24/24 = 1.0**。

### 10.5 本记录自身的一处缺陷（已修）

写这轮时发现：**上一版文档的 `## 4. ③ 契约检查器去恒绿` 标题行不见了**，
而 §3.5 / §3.6 的内容落在了另一个副本里。
根因是我自己：插入 §3.5 时用「`## 4.` 标题」当 old_string 整段替换，new_string 没把它还回去；
随后又往两个不同副本各写了一半。**代码修复本身在（`cb5eb7b3`），丢的是文档叙述。**
已补回标题，并把本轮内容一次性写到仓库路径，**提交后从远端 blob 回读校验**，不再只信本地副本。

### 10.6 对照跑（基线现实仍未变）

master 的 `docs/testing/baseline-fast.txt` 仍是空的（`count: 0`，2026-10-15，早于 P6 退役），
`--scope fast` 的退出码仍不可作判据 ⇒ 仍用对照跑，且基准分两层：

- **merge-base `81bbc240`**（本分支的起点）⇒ 差异全部归因于我这条线
- **当前 master**（9ecea05f）⇒ 对外口径

#### 10.6.1 三侧实测结果

选择范围 = 承载那批存量失败的 6 个文件（`test_spu_deterioration_alert_page.py` / `test_spu_price_columns.py` /
`test_frontend_pilots.py` / `test_spu_react_workspace.py` / `test_spu_numeric_sort.py` / `test_spu_projection.py`）。

| 侧 | 命令 | 退出码 | 耗时 | 失败 id 数 |
| --- | --- | --- | --- | --- |
| master 侧 | `bash scripts/test_isolated.sh browser <6 files>` | 0 | 241s | **0** |
| 候选侧（本分支 `058fac73`） | 同上 | 1 | 690s | 59 |
| merge-base 侧（`81bbc240`，我这条线的起点） | 同上 | 1 | 685s | 59 |

> master 侧用的是 `f1f7cded`（= master `e3e85e0e` + 一个 docs 提交）。
> 该 docs 提交不动测试；且 `e3e85e0e → 9ecea05f` 改到的 5 个测试文件
> （`tests/e2e/test_spu_deterioration_alert_smoke.py`、`tests/scripts/test_selected_runner_imports.py`、
> `tests/scripts/test_test_selection.py`、`tests/support/test_runtime.py`、
> `tests/sync_worker/test_scheduler_jobs_coverage.py`）**都不在本次比较的 6 个文件里**
> ⇒ 对这 6 个文件，master 侧与当前 master `9ecea05f` 等价。

#### 10.6.2 失败 id 集合比对

```
$ comm -13 /tmp/failures-mergebase6.txt /tmp/failures-candidate6.txt | wc -l
0                                   # 只在我这条线上出现的失败
$ comm -13 /tmp/failures-candidate6.txt /tmp/failures-mergebase6.txt | wc -l
0                                   # 反向也是 0 ⇒ 两侧失败集合完全相同
$ comm -13 /tmp/failures-master6.txt /tmp/failures-candidate6.txt | wc -l
59                                  # 相对 master 的差异
```

59 项的分布（候选侧与 merge-base 侧同一份）：

```
   53 tests/browser/test_spu_deterioration_alert_page.py
    6 tests/browser/test_frontend_pilots.py
```

#### 10.6.3 判定

| 结论 | 依据 |
| --- | --- |
| **我这条线零新增失败** | merge-base 与候选的失败 id 集合**双向差集都是 0**；而 merge-base 是我这条线的起点 ⇒ 差异全部归因于我这条线 |
| 相对 master 的 59 项差异**不是我的改动引入** | 同一批 59 项在 merge-base（我这条线的起点）上**一模一样**存在 ⇒ 是「分支落后 master」造成的，不是我的改动 |
| 那 59 项的成因 | master 上已由 `6e07b394`（告警页断言改写到 React 真实 DOM 契约）等提交修掉；我的分支尚未包含这些修复 ⇒ **合入 master 前需要先同步 master** |
| 静态论证（不依赖任何一次跑） | 本分支相对 merge-base 改了 11 个文件，`tests/` 与 `frontend/` 下 **0 个** ⇒ 结构上不可能引入测试失败 |
| 基线驱动的退出码仍不可用 | master 的 `baseline-fast.txt` 仍是空的（`count: 0`）⇒ `--scope fast` 会把存量失败全报成 NEW 并退出 1 |

⚠️ **合入 master 前的动作项**：先 `git merge origin/master`（或 rebase）把 `6e07b394` 等修复带进来，
再重跑本对照（预期候选侧失败集合归 0）。


