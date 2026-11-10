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
| ④ 进度数字 | **引用了已落地可执行检查的 rule = 24/24 = 1.0**；其中 owner 侧判据成立 **18/24**，6 项判红且**逐条对应真实未收口的债务** |
| ⑤ postgres infra | 三件（`container-spec.yaml` / `rebuild.sh` / `README.md`）已在本分支；本轮新增 `scripts/infra/runtime-facts.yaml` 与之同线 |

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

**但 24/24 不等于「都绿」。** owner 侧判据成立 **18/24**，6 项判红，逐条依据：

```
$ python3 scripts/check_ledger_owners.py
  检查规则数 = 24
  通过 = 18   失败 = 6
  ⚠ AD-DEPLOY-004
      - branch 判据不成立：仍含被禁止的分支 ['|| true']
  ⚠ AD-ALERT-003
      - count 判据不成立：模式 'content_sha256: absent' 实际命中 1 次，声明 0 次
  ⚠ AD-JOB-002
      - count 判据不成立：模式 'kill|SIGKILL|timeout' 实际命中 0 次，声明 1 次
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
| AD-JOB-002 | `scheduler.py` 里 kill/SIGKILL/timeout 出现 0 次 ⇒ 超时后 running 行不会被终结 | 代码未收口 |
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
