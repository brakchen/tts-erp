# dev-env-checklist.md 与 agent-testing.md 职责对照 + 合并建议

> 产出人：测试工程师（bot_d330a0b9a748）
> 日期：2026-11-08
> 状态：**只读分析，未修改任何仓库文件**（按 Chief 本轮指示「先不改文件」）
> 核查基线：`origin/master` = `2b68b40a`（本地克隆 `F:\MiniMax Work Result\agents\mavis\workspace\tts-erp`，分支 `feature/p6-collapse-migrated-routes`）

---

## 0. 结论先行

两份文档**不是竞争关系，是职责不同**，但边界没有划干净，导致 4 块内容平行重复。

核心建议：**不合并成一份**。按「规则 vs 环境事实」切开，agent-testing.md 保留为唯一的**规则权威**（contract），dev-env-checklist.md 降级为**环境事实与故障排查手册**（runbook）。入口改成 AGENTS.md §9 指 agent-testing.md，agent-testing.md 里加一行指向 runbook。

真正需要动的是 **4 处重复块的归属**，不是重写两份文档。

---

## 1. 两份文档的定位差异（为什么不该合并）

| 维度 | `docs/guides/agent-testing.md` | `docs/handoff/dev-env-checklist.md` |
| --- | --- | --- |
| 性质 | **规则/契约**（contract） | **环境事实/操作手册**（runbook） |
| 回答的问题 | 「什么是对的，什么禁止做」 | 「在这台机器上怎么跑、出错了怎么办」 |
| 适用机器 | 任何开发机、任何 CI | **只适用于 HP desk**（`schan@192.168.1.2`） |
| 环境假设 | 零假设（不写 IP、不写用户名） | 写死 IP、SSH alias、容器名、库名 |
| 证据类型 | 规范性陈述 | **实测输出**（22 处「实测」字样） |
| 失效模式 | 规则过时 = 有人按错规矩做事 | 环境事实过时 = 白跑一趟 |
| 篇幅 | 345 行 | 626 行 |

量化证据（关键词命中次数）：

| 关键词 | agent-testing.md | dev-env-checklist.md | 判读 |
| --- | --- | --- | --- |
| `origin/master` | 0 | 27 | checkout 同步是环境事实 |
| `rev-list` | 0 | 7 | 同上 |
| `hp-desk` | 0 | 7 | 环境专属 |
| `192.168` | 0 | 3 | 环境专属 |
| `ssh` | 0 | 6 | 环境专属 |
| `实测` | 0 | 22 | checklist 是实测记录体 |
| `is_prod_shaped_db` | 1 | 0 | 规则专属 |
| `TTS_ERP_TEST_OFF` | 1 | 1 | 两边都讲（安全边界，应只有一处） |
| `npm ci` | 2 | 8 | 两边都讲 |
| `DROP DATABASE` | 2 | 3 | 两边都讲 |
| `template_ads_ack` | 1 | 1 | 两边都讲 |
| `N passed` | 0 | 3 | checklist 独有（实操判读） |
| `prepare-worktree` | 0 | 19 | checklist 独有 |

**结论**：两份文档的内容集合基本不重叠，各自的独有部分正是它们该负责的东西。问题集中在少数几块。

---

## 2. 逐块对照表

图例：**重叠** = 两份都讲了同一件事；**独有** = 只有一份讲；**同名不同义** = 看起来一样但内容不同。

| # | 主题块 | agent-testing.md | dev-env-checklist.md | 关系 | 处置建议 |
| --- | --- | --- | --- | --- | --- |
| 1 | 数据库隔离契约（唯一入口、禁生产库、`TTS_ERP_TEST_OFF=1`、conftest 硬退出） | §1 (L5-15) | §0 表格 (L15-16) | **重叠**（一份是契约，一份是红线摘抄） | **契约留在 agent-testing**；checklist §0 改为一行链接「安全边界见 agent-testing §1」+ 只保留 HP desk 特有的一条（主 checkout 共享） |
| 2 | 残留克隆库清理规则 | §1.1 (L17-39)，5 条编号规则 | §9.7 (L501-515) 简述 + 指向 §10.3 | **重叠**（checklist 版是 agent-testing 版的不完整子集） | **规则留在 agent-testing §1.1**（版本最全）；checklist §9.7 只留「本机特有事实 + 一行链接」，**删掉自己那套重述的规则** |
| 3 | 模板库与共享库 | §4 (L219-247)，含 refresh 语义、provenance/fence、无共享库兜底 | §5 (L133-185) + §9.5 (L452-475) | **重叠**（checklist 多出「三方比对新鲜度」这个实用手法） | **规则留 agent-testing §4**；checklist §5 保留**三方比对的具体命令**（这是 agent-testing 没有的实操增量），但删掉对 refresh 语义的复述 |
| 4 | 命令表 | §2 (L45-142) 完整命令表 + candidate selection + 选测规则 | §7 (L234-285) + §8 (L288-341) + §12 (L607-626) 速查版 | **重叠**（checklist 是命令表的一个子集重排） | **命令表唯一权威 = agent-testing §2**；checklist 只保留**「怎么判成败」「耗时预期」「worktree 必须干净」**这三样 agent-testing 没有的判读知识 |
| 5 | worktree 前置 | §3 (L144-197) 软链规则、`.env` 不得软链、npm engine、renderer 生命周期 | §6 (L188-231) 建 worktree + `prepare-worktree.sh` | **同名不同义**（agent-testing 讲**规则**，checklist 讲**操作步骤**） | agent-testing 保留规则；checklist 保留操作步骤（`git worktree add` → `prepare-worktree.sh`），**两边不重复叙述** |
| 6 | SSH 连通性 | 无 | §2 (L46-63) | **独有** | **留在 checklist**（环境专属） |
| 7 | 仓库同步状态自检 | 无 | §3 (L67-99) | **独有** | **留在 checklist** |
| 8 | 测试依赖体检 | 只有 `install-test-deps.sh --check` 一句（§1 末） | §4 (L103-129) 完整输出 + 退出码语义 | **重叠**（checklist 更细） | checklist 保留**完整实测输出**（这是它独有的）；agent-testing §1 保留命令本身即可 |
| 9 | 故障排查（7 类） | 无 | §9 (L345-516) | **独有** | **留在 checklist** —— 这是 agent-testing 完全没有的增量价值 |
| 10 | 判读技巧（无 `N passed` 汇总行、`--plan` 不代替干净检查） | 无（`--plan` 只在 §6 提了一句） | §7.1 (L262-274)、§8.1 (L298-312) | **独有** | **留在 checklist**，但这两条是**通用判读知识**（任何机器都成立），建议**上提到 agent-testing §6**，checklist 留链接 |
| 11 | 基线陈旧 / `--scope auto` 假绿 | §6 (L257-325)，**含本地分支未进 master 的警告块** | §8 (L288-341) 基线命令 | **重叠** + **版本不一致** | **规则留 agent-testing**；checklist §8 只留命令与退出码表。**注意 agent-testing 那段警告块目前在本地分支未进 master**（见 §5） |
| 12 | 失败分类与完成证据 | §6、§7、§8 | 无 | **独有** | **留在 agent-testing** |
| 13 | 本次复核发现的问题（§10）与实测记录（§11） | 无 | §10 (L519-569)、§11 (L573-603) | **独有** | **留在 checklist**，但这类**时点性内容应定期归档**，否则会像第 228 行那样变成过时警告（见 §4） |

### 重叠量化

- **真重叠 4 块**：#1 安全边界、#2 清理规则、#3 模板库、#4 命令表
- **同名不同义 1 块**：#5 worktree 前置
- **各自独有 8 块**：#6 #7 #9 #10 #12 + #8 的实测输出 + #13
- **版本冲突 1 处**：#11（agent-testing 里的假绿警告块尚未进 master）

---

## 3. 建议怎么切

### 3.1 切分原则

> **规则进 agent-testing.md，环境事实进 dev-env-checklist.md。**
> 判据一句话：**这条内容换一台机器还成立吗？** 成立 → agent-testing；不成立（依赖 HP desk 的 IP / 容器名 / 库名 / 路径）→ checklist。

### 3.2 具体动作

**agent-testing.md（保持规则权威，3 处微调）**

1. 新增一小节「判读技巧」或在 §6 补两条通用规则：
   - 本仓库 pytest 输出**无 `N passed` 汇总行**，判成败看退出码 + `FAILED`/`ERROR` 行；
   - `--plan` 在到达「worktree 干净」检查之前就 `exec` 掉了，**不能**用它代替该约束。
   （来源：checklist §7.1 / §8.1，内容与机器无关，应上提）
2. §1.1 清理规则保持 5 条完整版不变，作为唯一权威。
3. §2 命令表保持不变，作为唯一命令权威。

**dev-env-checklist.md（降级为 runbook，4 处收敛）**

1. §0 硬性边界表 → **删除与 agent-testing §1 重复的 3 行**（测试唯一入口、禁生产形状库、禁 `TTS_ERP_TEST_OFF`），改为一行：
   > 安全边界以 `docs/guides/agent-testing.md` §1 为准，本清单只补充 HP desk 特有约束。
   保留 HP desk 特有的一条：**主 checkout 是共享资源，不要在主 checkout 上 `git add` / `commit` / `stash`**。
2. §9.7 残留库 → **删掉自己那套重述规则**，只留「本机实测数量 + 一行链接到 agent-testing §1.1」。
3. §8 DoD 基线 → 保留命令与退出码表，**删掉**对基线语义/陈旧规则的复述（已在 agent-testing §6）。
4. §7 / §8 中重复的判读知识上提到 agent-testing 后，这里改为链接。

**净效果**：checklist 626 行 → 约 400 行，规则类内容零重复；agent-testing 345 行 → 约 365 行，只增通用判读。

### 3.3 入口指向怎么写

**改 AGENTS.md §9 一行**，不改两份文档互相指。

理由：
- AGENTS.md §9 是**所有 agent 读规则的总入口**，入口指向必须唯一且稳定；
- 两份互指会制造「两份都像权威」的现状，正是现在的病根；
- runbook 是**遇到问题时才查**的，规则文档是**开工前就该读**的，两者触发时机不同，不该并列。

**具体改法**（`AGENTS.md` L275）：

```markdown
| Tests, fixtures, test database, baseline failures | `docs/guides/agent-testing.md`（规则权威）；HP desk 环境体检与故障排查见 `docs/handoff/dev-env-checklist.md` | 
```

同时在 `agent-testing.md` 开头加一行指向 runbook（**单向**，不是互指）：

```markdown
> HP desk 测试机的环境体检、实测输出与故障排查见 `docs/handoff/dev-env-checklist.md`。
> 本文件是规则权威；两者内容重复时以本文件为准。
```

这样形成单向权威链：`AGENTS.md §9` → `agent-testing.md`（规则）→ `dev-env-checklist.md`（环境手册）。

---

## 4. 本次核查中发现的、需要 Chief 处置的具体问题

| # | 问题 | 位置 | 性质 | 建议 |
| --- | --- | --- | --- | --- |
| A | agent-testing.md 的「`--scope auto` 假绿」警告块**尚在本地分支，未进 master** | 本地 `feature/p6-collapse-migrated-routes` vs `origin/master` 差 +20 行 | 内容缺失 | 该块价值很高（2026-11-08 实测：master 前进数百提交后，auto scope 一直报 0 而 `--scope fast` 实际暴露 56 个失败）。**建议合并进 master** |
| B | checklist §6.2 末尾仍有「prepare-worktree.sh 尚未提交到 origin/master（git status 显示 AM）」的过时警告 | `dev-env-checklist.md` L228-230 | **自相矛盾** | 与同文件 §10.1「✅ 已解决（2026-11-08 20:50）」直接打架。开发工程师本轮的 `b94fb2cc` 已修此处，**待其提交合入 master 后自动消解** |
| C | checklist §5.3 / §9.5 / §10.2 三处重复描述「模板库刷新路径断着」 | L169-184、L470-472、L538-548 | 重复 | 合并建议：保留 §5.3（讲断路），§9.5 改为一行链接，§10.2 保留（是待办项） |
| D | checklist §10 / §11 混装「时点性实测记录」与「长期有效规则」 | L519-603 | **腐化风险** | §11 的 22 条实测记录是 2026-11-08 的快照。半年后读的人会当成现状。建议 §10/§11 标注「快照日期 + 复核有效期」，或迁到 `docs/handoff/` 下的日期化文件名 |
| E | checklist §2 写「旧地址 `192.168.47.129` 已失效」 | L62 | 陈旧风险 | 该地址在 user memory 里记的是 `192.168.47.130`（schan 的旧服务器）。**129 vs 130 疑似笔误**，建议核对后修正，避免下次连错机器 |
| F | agent-testing.md 与 checklist 各自维护「模板库家族名单」 | agent-testing L30-32 / checklist L511、L553 | 重复 + 漂移风险 | 名单以 agent-testing §1.1 为准；checklist 改为链接 |
| G | **（2026-11-09 补充）master 上的 `agent-testing.md` 已是 439 行**，§1.2 共享机容量、§1.3 worktree 准备、§1.4 conftest basename 冲突、§1.5 换 renderer 断旧 DOM 断言**全部已合入 master** | 实测 `git show origin/master:docs/guides/agent-testing.md \| wc -l` = **439**；章节清单含 §1.1–§1.5 | **状态已变** | 本文档 §4 的「问题 A」（那 79 行 stranded 在本地）**已自然消解**，无需再提合 master。§2 对照表按新行数重读即可 |
| H | **（2026-11-09 补充）master 上 `test_spu_deterioration_alert_page.py` 仍有 53 处 legacy 选择器** | 实测 `grep -cE "alert-rows\|total-spus\|alert-status"` = **53**；而修复提交 `6e07b394`/`ed6811eb`/`08c44603` 均在 `origin/fix/alert-page-react-dom-assertions` 分支上，`git merge-base --is-ancestor` 验证**均未进 master** | 状态澄清 | 开发工程师所说「P6 lane 已改完」指**分支上已改**，master 尚未合。**「53 项」这个数字对 master 仍然成立**；合入后需重测 |

---

## 4.5 复核时点说明

本文档初版写于 2026-11-08 晚，§4 的问题清单基于当时的 master（`2b68b40a`）。2026-11-09 复核时 master 已到 `dab9f4fc`，逐条复核后：

- **问题 A（79 行 stranded）**：**已消解** —— §1.2–§1.5 与假绿警告块已进 master，`agent-testing.md` 现 439 行。
- **问题 B（第 228 行过时警告）**：**已消解** —— 开发工程师 `b94fb2cc` 已修，随 `6c99ecc3` 合入。
- **问题 C（模板库刷新三处重复）**：仍开着，需按本文档 §3.2 收敛。
- **问题 D（§10/§11 时点性内容腐化）**：仍开着。
- **问题 E（`192.168.47.129`）**：**已消解** —— 已改为指向 `hp-desk` alias、不列具体旧 IP，随 `e152ec15` 合入。
- **问题 F（模板库家族名单重复）**：仍开着。

---

## 5. 本轮实测证据（HP desk，均为实跑）

按测试纪律列出真实命令与结果。**未执行任何非隔离测试，未触碰生产库，未删除任何测试库。**

| # | 检查项 | 命令 | 实际结果 |
| --- | --- | --- | --- |
| 1 | SSH 连通 | `ssh hp-desk "echo SSH_OK; hostname; whoami"` | `SSH_OK` / `schan-HP-ProDesk-400-G2-MINI` / `schan` |
| 2 | HP desk 的 origin/master | `git rev-parse origin/master` | `2b68b40abd7241f1f94fd9ea8002137e6fb0cb2c`（与本地一致） |
| 3 | `.env.test` 实际存在 | `ls -la .env.test` | 存在，`-rw------- 334 字节`，`readlink` 指向自身（非软链） |
| 4 | `.env` 存在性 | 检查 `.env`、`/home/schan/.env` | **两处都 MISSING** |
| 5 | 刷新源环境变量 | `echo $TTS_ERP_DB_URL_PROD_SOURCE` | `<unset>` |
| 6 | `source_prod_url()` 实现 | `sed -n '105,125p' scripts/test_isolated.sh` | 优先读 `TTS_ERP_DB_URL_PROD_SOURCE` → 否则读 `.env` 的 `TTS_ERP_DB_URL` → 否则读 `../../.env`。**三处皆空 ⇒ 刷新必然失败**，与 checklist §5.3 描述一致 |
| 7 | 模板库 alembic | `docker exec postgres psql -d tts_erp_test_template -Atc "select version_num from alembic_version;"` | `0076_media_packs` |
| 8 | 生产形状库 alembic | 同上，查 `tts_erp` | `0076_media_packs` → **模板库不陈旧**，三方一致 |
| 9 | 全部 `tts_erp*` 库 | `select datname from pg_database where datname like 'tts_erp%'` | `tts_erp` + `tts_erp_v3_test` + `tts_erp_test_template` + 7 个 `*_template_*` + 4 个 `tts_erp_test__schan_20261108*` |
| 10 | 那 4 个克隆库是不是残留 | `ps aux \| grep -E "[p]ytest\|[t]est_baseline\|[t]est_isolated"` | **不是残留，是正在运行的活跃测试**：PID 2878844（browser 域）、2901530（基线）、2929985（api 域）、2953945（`test_spu_price_columns.py`）。**未删除，符合纪律** |
| 11 | 基线运行所在 worktree 状态 | `cd .worktrees/docs-node-ignore && git status --porcelain --untracked-files=all` | **干净**（无输出）；HEAD = `e152ec1513b...`（开发工程师分支），`rev-list --left-right --count HEAD...origin/master` = `2  0` |
| 12 | 04 文档引用的测试文件是否仍在 | 检查 9 个路径 | **全部 EXISTS**；前端 `frontend/src/pages/spu-profit-deterioration/` 含 `index.test.tsx`、`contracts.test.ts` |

**未执行**：本轮**没有**跑 SPU 比较域测试或完整 fast 基线。理由：核查时 HP desk 已有 10 个测试相关进程在跑（含开发工程师的 `--scope auto` 基线与别人的 browser/api 域验证），叠加新负载会互相干扰、且违反「每个 lane 正常只跑一次套件」的纪律。

---

## 6. 需要 Chief 拍板的三个问题

1. **切分方案是否采纳**（§3.2）：checklist 4 处收敛 + agent-testing 2 条上提 + AGENTS.md §9 单行改写。
2. **问题 A**：本地分支那段「`--scope auto` 假绿」警告块要不要合并进 master？（我建议要，它防的是一类真实的假绿事故）
3. **问题 E**：`192.168.47.129` 是否笔误？该地址的权威来源需要确认。

问题 B 待开发工程师的 `b94fb2cc` 合入 master 即自动消解，**无需额外动作**。

---

*本文档为只读分析产出，未修改仓库任何文件。文中所有命令与输出均为 2026-11-08 实跑结果。*
