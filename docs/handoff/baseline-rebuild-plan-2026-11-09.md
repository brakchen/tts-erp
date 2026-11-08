# 基线重建方案（先写不执行）

> 产出人：测试工程师（bot_d330a0b9a748）
> 日期：2026-11-09
> 委托：Chief —— 「基线重建方案先写不执行：前置条件、干净 master worktree、`--update` 用哪个 SHA、master 漂移怎么处理、怎么防止刚建好就过期。等失败清单清干净再重建。」
> **本轮不执行重建**，仅出方案。
> 核查基线：`origin/master` = `dab9f4fc`，HP desk worktree `/home/schan/tts-erp/.worktrees/guard-css`
> 工具实现依据：`scripts/test_baseline.sh`（实测读取，非推测）

---

## 0. 结论

**可以重建，但当前不具备重建条件**，且工具本身有两个洞会让「重建出来的基线是错的」。

| 项 | 状态 |
| --- | --- |
| 工具是否支持重建 | ✅ 支持（`--update` 分支），但**不校验是否在 master 上** |
| 当前是否该重建 | ❌ **不该** —— master 仍有大量失败项，重建会把它们固化成「已知失败」 |
| 重建的阻塞项 | ① 失败清单未清干净 ② `--update` 缺 master 校验 ③ `master:` 字段记的是 worktree HEAD 而非 `origin/master` |
| 建议 | 先清失败清单 → 给 `--update` 加 2 道守卫 → 再重建 |

---

## 1. 现状事实

### 1.1 已提交基线

```
docs/testing/baseline-fast.txt  （18 行）
# scope:   fast
# master:  e04c5e114573b2b6aca1da9c8acfcc8a514e1c6f
# updated: 2026-10-15T16:39:57Z
# count:   0
```

基线头停在 **2026-10-15** 的 `e04c5e11`，`count: 0`（零已知失败）。到 `dab9f4fc` 已过 **约 700 个提交**（本轮实测 `rev-list --count` 量级），期间 master 的失败集已完全改变。

**后果**：任何 `--scope fast` 都会把 master 现存失败全部报成 `NEW failures`，DoD 门禁恒为红。这就是开发工程师报「`--scope fast` exit 1」的直接原因。

### 1.2 基线文件的语义

`docs/testing/baseline-fast.txt` 记录的是 **master 的属性**，不是任何 feature lane 的属性。文件头自己写明：

> Known-failing / erroring test ids for the selection below, measured on master. This is a property of master, NOT of any feature lane.

每行一个**归一化的 pytest 测试 id**，已剥离原因文本（避免行号/断言 repr 抖动造成噪声）。抽取逻辑：

```bash
sed -n -E 's/^(FAILED|ERROR) ([^ ]+).*/\2/p' "$1" | LC_ALL=C sort -u
```

⚠ 只认 pytest 输出里的 `FAILED` / `ERROR` 短摘要行。**这意味着被 `-x` 提前中断、或收集阶段就崩掉的用例不会进基线。**

---

## 2. 工具行为（实测 `scripts/test_baseline.sh`）

### 2.1 `--update` 做了什么

```
$ bash scripts/test_baseline.sh --update
```

1. SCOPE 默认 `fast`（第 37 行 `SCOPE="fast"`），**`--update` 不接受 `--scope auto`**
2. 走 `scripts/test_isolated.sh fast` 跑**完整 fast 套件**（第 240 行）
3. 从运行日志抽 `FAILED`/`ERROR` 行 → `current.txt`（第 242 行）
4. **守卫检查**（第 249-254 行）：以下任一成立就 `fail`，拒绝写基线
   - `rc > 1`
   - `rc == 1` 但 `current.txt` 为空
   - 日志含 `[isolated-test] ERROR:` 或 `no tests ran`
   - 日志**缺少** `short test summary info` 或 `slowest ... durations` 头
5. 写 `docs/testing/baseline-fast.txt`：头部元信息 + `cat current.txt`

**这个守卫设计得很好** —— 它专门防「套件没跑成却算出零失败」。实测注释写明：pytest 9 的 `-q` 输出没有 `N passed` 汇总行，所以不能靠那行判断。

### 2.2 前置校验的实际范围

| 校验 | 实现 | 说明 |
| --- | --- | --- |
| worktree 干净 | 第 165-166 行 `git status --porcelain --untracked-files=all` | ✅ 有 |
| **是否在 master 上** | ❌ **无** | ⚠ **洞 1** |
| **HEAD 是否等于 origin/master** | ❌ **无** | ⚠ **洞 2** |

### 2.3 `master:` 字段的来源

第 275 行：

```bash
echo "# master:  $(git rev-parse HEAD)"
```

记的是**当前 worktree 的 HEAD**，不是 `origin/master`。

⚠ **洞 3**：在 feature 分支的 worktree 里跑 `--update`，会写出一条标着 feature 分支 HEAD 的「master 基线」，与文件自述的语义（「a property of master」）矛盾。

### 2.4 `--base` 在 `--update` 下的行为

第 224-229 行：`--base` 会把 `TRUSTED_BASELINE` 指向 `git show $BASE:baseline-fast.txt`（即从 base commit 取基线）。但 **`--update` 分支在第 259 行就 `exit 0` 了，根本不走 compare 逻辑** —— 所以 `--update` 时 `--base` 只影响「从哪个 commit 取旧基线文件」，**不影响写入内容**。

⇒ **`--update` 与 `--base` 一起用没有意义**，重建时**不要**传 `--base`。

---

## 3. 前置条件清单（重建前必须全部满足）

| # | 前置条件 | 怎么验 | 当前状态 |
| --- | --- | --- | --- |
| P1 | master 失败清单已清干净（或已知失败已定论并逐条确认） | `--scope fast` 的失败集只剩**已定论**的项 | ❌ 未满足 |
| P2 | 有干净的 master worktree | `git status --porcelain --untracked-files=all` 输出为空 | ✅ 可满足 |
| P3 | worktree 的 HEAD **就是** `origin/master` | `git rev-parse HEAD` == `git rev-parse origin/master` | ✅ 可满足 |
| P4 | 模板库不陈旧 | 模板库 / 生产形状库 / 仓库最新迁移三方对齐 | ✅ 当前均为 `0076_media_packs` |
| P5 | 前置依赖就绪 | `bash scripts/envsetup/install-test-deps.sh --check` 退出码 0 | ✅ 可满足 |
| P6 | HP desk 空窗（4 核，同时只跑一个套件） | `ps -eo args \| grep -c "[p]ytest"` 为 0 | ⚠ 需等待 |
| P7 | 工具守卫已补（见 §5） | `--update` 在非 master HEAD 上拒绝执行 | ❌ **未满足** |

**P1 是真正的阻塞项。** P2～P6 都是可安排的资源问题。

---

## 4. 重建步骤（方案，待批准后执行）

```bash
# 步骤 0：确认基线前置
cd /home/schan/tts-erp
git fetch origin
bash scripts/envsetup/install-test-deps.sh --check     # 退出码须 0
docker exec postgres psql -U postgres -d tts_erp_test_template -Atc \
  "select version_num from alembic_version;"           # 须 == 仓库最新迁移
ps -eo args | grep -c "[p]ytest"                        # 须为 0（空窗）

# 步骤 1：建干净的 master worktree（detached 在 origin/master）
git worktree add --detach /home/schan/tts-erp/.worktrees/baseline-rebuild origin/master
cd /home/schan/tts-erp/.worktrees/baseline-rebuild

# 步骤 2：确认这个 worktree 确实处于 origin/master
git rev-parse HEAD                                      # 须 == git rev-parse origin/master
git status --porcelain --untracked-files=all             # 须为空

# 步骤 3：准备环境
bash scripts/envsetup/prepare-worktree.sh               # 退出码须 0

# 步骤 4：重建（不传 --base，不传 --scope）
bash scripts/test_baseline.sh --update

# 步骤 5：核对产出
head -15 docs/testing/baseline-fast.txt                  # master: 须为步骤 2 的 HEAD
git diff --stat docs/testing/baseline-fast.txt           # 确认 count 与预期一致
```

### 4.1 「`--update` 用哪个 SHA」的明确回答

**用 `origin/master` 的 SHA，且必须让 worktree 的 HEAD 精确等于它。**

理由：
- 基线的语义是「master 的属性」，master 字段记的就是 `git rev-parse HEAD`
- 若 HEAD ≠ origin/master（例如 HEAD 是某个 merge commit），基线记录的 SHA 与真正的 master 不一致
- 若在 feature 分支跑，语义直接错（洞 3）

**做法**：`git worktree add --detach <path> origin/master` —— detached HEAD 精确落在 `origin/master` 的 commit 上，不创建分支、不污染 master。

### 4.2 master 漂移怎么处理

**场景**：重建耗时 ~40 分钟，期间 master 可能前进。

| 情况 | 处置 |
| --- | --- |
| 重建期间 master 未前进 | 直接用产出。基线的 `master:` 字段 == 当前 `origin/master` ✅ |
| 重建期间 master 前进 | **必须重跑**，不能直接用 |

**为什么必须重跑**：基线是「master 的失败集快照」。master 前进后：
1. 基线的 `master:` 字段指向旧 SHA，与 master 不符；
2. 新增的提交可能引入新失败（不在基线里 → 被报成 NEW failures）或修复旧失败（不在基线里 → 退出码仍红）。

**判定漂移的方法**（重建前后各记一次）：

```bash
git rev-parse origin/master      # 步骤 2 之前记一次
# ... 跑重建 ...
git fetch origin && git rev-parse origin/master   # 重建后
```

两者不同 ⇒ **漂移了，重跑**。

**降低漂移影响的技巧**：重建**紧接在一次 master 合并之后**做；或者先 `git fetch origin` 确认 master 已静置（与上一次合并间隔 > 30 分钟，本轮 master 平均每 10–30 分钟被推一次，见记忆条目）。

---

## 5. 建议给工具补的两道守卫（重建前必须做）

这两条都是**「防止重建出错误基线」**，不改变基线语义，只防止误用。

### 守卫 A：`--update` 只允许在 master HEAD 上执行

```bash
# 在 MODE=update 分支入口处加：
if [[ "$MODE" == "update" ]]; then
  if [[ "$(git rev-parse HEAD)" != "$(git rev-parse origin/master)" ]]; then
    fail "--update requires HEAD == origin/master (this worktree is on $(git rev-parse --abbrev-ref HEAD)); \
regenerate the baseline from a clean master worktree"
  fi
fi
```

堵住洞 1 和洞 3。

### 守卫 B：`master:` 字段记 `origin/master` 而非 `HEAD`

```bash
# 第 275 行改为：
echo "# master:  $(git rev-parse origin/master)"
```

配合守卫 A 后两者等价，但语义更明确，且万一将来放宽守卫也不会写错。

### 守卫 C（可选）：重建后做一次自检

重建完成后，立刻用新基线跑一次 compare，确认「零新增失败」：

```bash
bash scripts/test_baseline.sh --scope fast --base <刚重建时的 origin/master SHA>
```

期望：`exit 0`（本次运行没有比基线更多的失败）。这能验证基线文件本身格式正确、且确实覆盖了当前失败集。

**注意**：这会再跑一次完整 fast 套件（约 40 分钟）。如果只跑一次预算紧张，可跳过，但**建议至少跑一次** —— 基线文件写错一个 id，整个门禁就会持续误报。

---

## 6. 「怎么防止刚建好就过期」

基线过期的**唯一机制**是 master 的失败集变了而基线没跟着变。所以防过期 = **让基线跟着 master 一起更新**，而不是让基线永不过期。

### 6.1 建议机制

| # | 机制 | 说明 | 成本 |
| --- | --- | --- | --- |
| M1 | **master 上加一个「基线健康」检查** | 在 master 的 CI/收口流程里跑一次 `--scope fast` compare；若 master 自身引入了新失败（即基线该更新了），就提示重建 | 需要一个定时任务或 master 合并后的例行检查 |
| M2 | **基线头记录日期 + commit 的双向标注** | 当前只有 `updated:` 时间戳。增加 `master:` 与 `updated:` 的**自动交叉校验**：任何人看到基线头里的 SHA ≠ 当前 master，就知道它过期了 | 低（守卫 B 已顺带做了） |
| M3 | **compare 模式对「基线过期」给出明确信号** | 现有工具已有：第 304 行 `Newly passing (master baseline is stale — regenerate on master):` —— 即「基线里有、这次没跑挂」的项会被单独报出 | ✅ 已实现，无需改 |
| M4 | **每 10–30 分钟的 master 推进节奏下，把重建绑到「失败集变更」事件上** | 只有当 master 的失败集真的变了才需要重建。M1 的本质 | 中 |

### 6.2 现实建议

**不要追求「永不过期」。** 正确的心态是：

> 基线是**快照**，过期是常态。机制要保证的是：**过期能被及时发现，而不是悄悄放过失败。**

现有工具已经具备关键能力：
- compare 模式会把「基线里有、这次没挂」的项报成 `Newly passing (master baseline is stale)`；
- 「这次挂了、基线里没有」的项报成新失败 → 退出码 1。

**只要 `count: 0` 的基线还在，任何真实回归都会被抓到**（被报成 NEW failures，exit 1）。所以**「基线过期」本身不产生漏检，只产生噪声**。

⇒ **结论：M1（例行健康检查）是可选的；M2 + 守卫 A/B 是必须的。**

真正影响效率的是噪声：80 个失败项全被报成 NEW，会淹没真实回归。**所以「清干净失败清单」比「防基线过期」更优先。**

---

## 7. 执行时机建议

```
现在 ──→ [清失败清单] ──→ [补守卫 A/B] ──→ [等 master 静置] ──→ [重建] ──→ [自检 compare]
         ↑ 阻塞项                                  ↑ 需等窗口
```

| 阶段 | 依赖 | 负责 |
| --- | --- | --- |
| 清失败清单 | 无 | 测试侧（断言改写 / 测试退役），需开发配合产品代码 |
| 补守卫 A/B | 无 | 开发工程师（改 `scripts/test_baseline.sh`） |
| 等 master 静置 | master 推进节奏 | — |
| 重建 | 前三项全绿 + HP desk 空窗 | 测试侧 |
| 自检 | 重建完成 | 测试侧 |

**不要在失败清单未清时重建。** 那等于把 80 个失败固化成「已知失败」，从此每个 lane 的 `--scope fast` 都会带着 80 行噪声，真实回归被淹没 —— 与当前 `count: 0` 的状态相比，**可读性反而更差**（现在至少所有红都是真的红）。

---

## 8. 本轮实跑证据

| # | 检查项 | 命令/来源 | 实际结果 |
| --- | --- | --- | --- |
| 1 | 当前基线内容 | `cat docs/testing/baseline-fast.txt` | `scope: fast` / `master: e04c5e114573b2b6aca1da9c8acfcc8a514e1c6f` / `updated: 2026-10-15T16:39:57Z` / `count: 0`；文件 18 行 |
| 2 | `--update` 的 scope 来源 | `scripts/test_baseline.sh:37` | `SCOPE="fast"` —— `--update` 固定跑 fast 全量，不接受 `--scope auto` |
| 3 | worktree 干净校验 | `scripts/test_baseline.sh:165-166` | `[[ -z "$(git status --porcelain --untracked-files=all)" ]] \|\| fail 'scoped runs require a clean committed worktree'` |
| 4 | **是否有 master 校验** | 全文 grep `master` / `branch` | ❌ **无**。仅注释提到 "master only" |
| 5 | `master:` 字段来源 | `scripts/test_baseline.sh:275` | `$(git rev-parse HEAD)` —— **worktree 的 HEAD，不是 origin/master** |
| 6 | 失败 id 抽取 | `scripts/test_baseline.sh:154` | `sed -n -E 's/^(FAILED\|ERROR) ([^ ]+).*/\2/p' \| LC_ALL=C sort -u` |
| 7 | 运行守卫 | `scripts/test_baseline.sh:249-254` | `rc>1` / `rc==1 且 current.txt 空` / 日志含 `[isolated-test] ERROR:` 或 `no tests ran` / 日志缺 `short test summary info\|slowest .* durations` ⇒ `fail` |
| 8 | `--base` 在 update 下的作用 | `scripts/test_baseline.sh:224-229` + `259` | 只影响「从哪个 commit 取旧基线文件」；update 分支第 259 行即 `exit 0`，不走 compare ⇒ **`--update` 不应传 `--base`** |
| 9 | stale 基线提示 | `scripts/test_baseline.sh:304` | `Newly passing (master baseline is stale — regenerate on master):` —— 过期检测能力已存在 |
| 10 | 模板库新鲜度 | HP desk 实查 | 模板库 / 生产形状库均 `0076_media_packs`，与仓库最新迁移三方一致 → 不陈旧 |

**未执行**：**未执行任何重建**（本轮明确只写方案）；未跑完整 fast 套件（HP desk 当时有别的 lane 在跑 `--scope auto`，且 §7 明确 P1 未满足）；未修改 `scripts/test_baseline.sh`（守卫生效属开发工程师写域，本轮只出建议）。

---

## 9. 需要 Chief 拍板的三点

1. **守卫 A/B 何时加**。建议在重建**之前**加，否则重建本身就有写出错误基线的风险。属开发工程师写域（`scripts/test_baseline.sh`）。
2. **是否接受「先清失败清单再重建」的顺序**，还是希望先重建出一份带噪声的基线来让 lane 尽快变绿。**我建议前者**（理由见 §6.2：带噪声的基线会淹没真实回归）。
3. **§3 的 P6（HP desk 空窗）由谁盯**。本机 4 核，重建一次约 40 分钟，期间任何并发套件都会让结果不可信。

---

*本文档为方案产出，未执行重建、未修改任何仓库文件。所有工具行为结论均来自实测读取 `scripts/test_baseline.sh` 源码，非推测。*
