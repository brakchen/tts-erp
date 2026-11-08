# tts-erp Bot 团队上手说明

> 建立时间：2026-11-08 · 总管：Chief
> 仓库：`git@github.com:brakchen/tts-erp.git`
> 本地克隆：`F:\MiniMax Work Result\agents\mavis\workspace\tts-erp`
> 测试机：HP desk `schan@192.168.1.2`（SSH 别名 `hp-desk`），代码在 `/home/schan/tts-erp`

---

## 一、团队构成

| Bot | 形象 | 职责 | 主要产出 |
| --- | --- | --- | --- |
| **产品经理** | 翡翠（jade） | 需求分析、写 PRD | `docs/<主题>/01-product-proposal.md` |
| **开发工程师** | 月岩（moon） | 后端与前端实现、技术设计 | `02-technical-design.md` + 代码 |
| **测试工程师** | 玻璃（glass） | 测试设计、隔离环境验证、基线比对 | `04-test-plan-and-results.md` |
| **Chief**（总管） | — | 拆解任务、分配工作、验收把关 | 排期与结论 |

群聊：**「tts-erp 研发协作」**，默认由 Chief 接话，需要某个 Bot 干活时 @它名字。

---

## 二、协作流程

```
用户提需求
   ↓
产品经理：读需求 → 查已有方案 → 出 PRD（01）
   ↓                                        ↑
开发工程师：读 PRD + 技术设计 → 实现 → 跑测试 ↓
   ↓                                        ↑
测试工程师：写测试计划（04）→ 隔离环境验证 → 结果回群
   ↓
Chief 验收汇总
```

四个阶段对应仓库既有的文档编号，团队产出直接进 `docs/<主题>/`，不另建体系。

---

## 三、怎么用

- **私聊**：直接对某个 Bot 说需求，比如私聊产品经理「帮我写一个店铺成本录入的需求」
- **群聊**：说需求 → Chief 分配；或直接 `@产品经理 帮我写……`
- **点对点协作**：在群里 @ 开发工程师、@测试工程师 接力

---

## 四、开工前必读

三个 Bot 的 instructions 里都写死了这些，但值得你知道：

1. **`AGENTS.md`** —— 仓库给 coding agent 的安全边界、命令表、worktree 纪律。**每个 Bot 动手前必须完整读一遍。**
2. **`docs/guides/agent-safety.md`** —— 改数据库、凭证、迁移、鉴权、生产前必读
3. **`docs/guides/agent-testing.md`** —— 测试的唯一工作流
4. **`docs/guides/frontend-development.md`** —— 改前端前必读

---

## 五、环境要点（已实测）

### 测试机
- `ssh hp-desk` → `schan@192.168.1.2`，Ubuntu 24.04
- venv `/home/schan/tts-erp/.venv`（Python 3.13）
- docker `postgres:18`（`tts_erp` 库）+ `minio`
- 测试模板库 `tts_erp_test_template` 已存在

### 新建 worktree 后必须先做环境准备

```bash
bash scripts/envsetup/prepare-worktree.sh
```

原因：`.venv`、`.env.test`、`frontend/node_modules` 都是 gitignored，新 worktree 自带没有。缺一个，测试就会以 `missing project Python` / `TTS_ERP_DB_URL_TEST is unset` / `Frontend dependencies missing` 失败。

**注意**：前端依赖不能用软链接。Node 的 ESM 会解析到软链接的真实路径，而主 checkout 和新 worktree 的 `package.json` 版本可能不同，会出现 `Cannot find package 'semver'`。脚本按当前 worktree 自己的 `package-lock.json` 执行 `npm ci`。

### 主 checkout 需要定期同步

```bash
cd /home/schan/tts-erp
git pull --ff-only origin master
npm ci --prefix frontend     # 依赖落后于 master 时
```

2026-11-08 检查时，主 checkout 停在 `83bbede`，落后 master **312 个提交**（`git rev-list --count 83bbede..0b92d97`；首父口径 56 个），且 `node_modules` 缺 `semver`（master 的 `check-runtime.mjs` 需要它，该文件在 `a3815db` 才引入）。已同步并修复。

---

## 六、测试怎么跑

唯一入口是 `scripts/test_isolated.sh`，它会克隆模板库成一次性 `tts_erp_test_*` 库，跑完自动删除。

```bash
bash scripts/test_isolated.sh fast                       # 快速套件
bash scripts/test_isolated.sh unit                      # 单元层
bash scripts/test_isolated.sh <domain>                  # 单个业务域
bash scripts/test_isolated.sh fast tests/path/test_x.py # 单文件

# DoD 验证（target_sha 用精确同步的 master SHA）
bash scripts/test_baseline.sh --scope auto --base "$target_sha"
bash scripts/test_baseline.sh --scope auto --base "$target_sha" --plan  # 先看会跑什么

bash scripts/envsetup/install-test-deps.sh --check      # 前置依赖体检
```

**红线**：测试绝不能连 `tts_erp`、`tts_erp_prod` 或任何生产形状库；绝不设 `TTS_ERP_TEST_OFF=1`；测试数据一律 `TEST_` 前缀。

---

## 七、本次已完成的修正

提交 `dfac963d`，分支 `chore/fix-test-server-address`（已推送到 origin）：

1. **`AGENTS.md`** —— 测试机地址从 `192.168.47.129`（已下线，SSH 超时）改为 `192.168.1.2`，并补上 venv、docker 服务、隔离测试入口的说明
2. **`docs/ops/tts-erp.md`** —— 更新「本机虚拟机测试服务器（项目记忆）」整段：主机名、系统版本、SSH 别名、已就绪的 venv/数据库/模板库，以及本次验证证据
3. **`scripts/envsetup/prepare-worktree.sh`**（新增）—— worktree 环境准备脚本
4. **`AGENTS.md` §5 命令表** —— 增加「新 worktree 测试环境准备」一行

历史工作日志（`docs/testing/baseline-repair-2026-11-03.md`、`docs/plans/2026-11-06-test-feedback-speed.md` 等）里的旧地址保留原样——那些记录的是当时真实发生的事。

---

## 八、看板不可用时的并行方案

**tower-do 看板工具在本会话不可用**（`mcp list` 为空）。仓库 `AGENTS.md` §8 要求：多会话/多 agent 并行开发时，必须用 tower-do 看板登记 lane、写明文件归属，避免多个 Bot 撞同一批文件。HP desk 上已有 50 多个 worktree 并行，这个冲突风险是真实存在的。

**替代方案（本期采用）**：用 `docs/handoff/p6-retirement-parallel-plan.md` 里的**显式写域声明**协调。每个 lane 开工前在群里声明自己的文件清单，三份清单互不重叠。退役前强制 `git grep` 三连检查（有引用先改引用方，不先删文件）。

这套办法的前提是：切分依据是**文件**，而不是逻辑任务。P6 退役旧模板正好满足——退役对象就是文件，天然可切。

如果后续要并行做**逻辑任务**（比如同时改同一个模块的上下游），这套办法不够，必须先接入 tower-do。

---

## 九、本期主线与当前状态

**主线**：前端渐进迁移的 P6 退役与收口。

**核实结论（与方案文档的状态说明不同，以实测为准）**：P2–P5 实质已完成。

| 事实 | 位置 |
| --- | --- |
| `MIGRATED_PAGE_IDS` 已含 19 项 | `tts_erp_v2/api/v2/frontend_pages.py`（18 个注册页 + `login`） |
| `PAGES` 注册 **18** 个页面，全部已迁移 | `tts_erp_v2/accounts/pages.py:26` |
| 20 个页面目录有真实 React/TS 代码，约 1.4 万行 | `frontend/src/pages/`（18 注册页 + `login` + `message` / `oauth-onboard`；`spu-profitability` 被 `spu-roi` 与 `focused-spus` 共用） |
| 12 个旧 Jinja 模板 + 13 个旧 JS 脚本仍在 | `tts_erp_v2/templates/pages/`、`tts_erp_v2/static/js/` |
| 旧 renderer 分支仍在 | `tts_erp_v2/api/v2/pages.py` |

`docs/plans/frontend-progressive-migration.md` 顶部"P2–P6 待实施"的说法已过时，应在合并 P6 时一并更新。

**三个 lane 的写域分配**：

| lane | 负责人 | 写域 |
| --- | --- | --- |
| A | 开发工程师 | `api/v2/pages.py`、`static/js/console.js`、`static/vendor/`、`frontend/src/app/` |
| B | 产品经理 | 基础设置组 6 个模板 + 5 个脚本 |
| C | 测试工程师 | 数据工具与内容资产组 5 个模板 + 6 个脚本 + `api/v2/ad_daily.py` |

**合并顺序**：lane B、C 相互独立可先合；lane A 依赖 lane B 删除 `manual-costs.html`（console.js 的最后一个页面级引用在那里）。

**已交付的其他产出**：
- `docs/handoff/topic-directory-index.md` —— 190 个文档的四段式主题目录索引
- `docs/handoff/p6-retirement-parallel-plan.md` —— P6 并行执行方案
- `docs/handoff/dev-env-checklist.md` —— 开发环境自检清单（16+6 条实测记录、7 类故障排查）
- `docs/handoff/test-baseline-verify-2026-11-08.md` —— 测试链路独立复核记录（对照实验）
- `scripts/envsetup/prepare-worktree.sh` —— worktree 环境准备脚本（已进 master）

---

## 十、并发调度的实际限制

MiniMax Bot 对**同一个 Bot** 的多个任务不会并行——排队等前一个跑完。同一个 Bot 同一时间只有 1 个活跃任务。

**并发上限 = 团队里的 Bot 数量**，不是任务数。2026-11-08 实测：给 3 个 Bot 各派 1 个 lane，
3 个任务全部 queued 50 分钟没启动（每个 Bot 都还在跑先前派的摸底任务）。

**实操含义**：
- 想真正并行，lane 必须**一个 Bot 一个**分配，不能把多个 lane 派给同一个 Bot。
- 给 Bot 派活前先确认它空闲；否则任务会一直排队。
- 每个任务动辄跑完整 fast 基线（历史测量 1459 秒，单次可达 20+ 分钟），一排就是很久。
- **需要立刻拿到结果时，改用后台助手**，它不受 Bot 队列限制。
- `get_tasks` 里 status 为 `waiting_for_tasks` 且带 `dependencies: [{kind: batch, reviewed: false}]`
  是 Bot 自己在群里拆子任务的正常协作态，不是卡死——但要有人在群里回应它，否则它会一直等。

---

## 十一、已解决的仓库级问题

| 问题 | 状态 | 说明 |
| --- | --- | --- |
| 测试机地址失效 | ✅ | `192.168.47.129` → `192.168.1.2`（HP desk），`AGENTS.md` §2 与 `docs/ops/tts-erp.md` 均已更新 |
| 新 worktree 跑不了测试 | ✅ | 新增 `scripts/envsetup/prepare-worktree.sh` 并合入 master |
| 主 checkout 落后 master | ✅ | 已同步到最新 master，`node_modules` 补齐（master 需要 `semver`） |
| 模板库刷新路径不可用 | ⚠️ | 当前模板库不陈旧（`0076_media_packs` / 306 表，三方对齐），暂不影响；但仓库新增 migration 前必须先解决 |
| 22 个残留测试临时库 | ✅ | 已清理，template 基线库全部保留 |
| revision 漂移难以诊断 | ✅ | `test_isolated.sh` 克隆后检测失配，直接打印原因和修复步骤 |
| `--scope auto` 的假绿 | ✅ | 空计划时 driver 直接 exit 0。已在 `docs/guides/agent-testing.md` 写明「要看 master 真实状态就跑 `--scope fast`」 |
| 多目录 conftest 冲突 | ✅ | `tests/` 无 `__init__.py`，8 个同名 `conftest.py` 按 basename 导入互相覆盖。`browser_conftest` 改用包路径导入 |
| `--surface-secondary` 未声明 | ✅ | React 侧真实 CSS 缺陷（全仓唯一未声明令牌），已在 `tokens.css` 补齐 |
| 前端旧资产未退役 | ✅ | 12 模板 + 12 脚本 + 4 旧 CSS + 9 个无消费者 vendor 副本全部删除 |
| P6 退役无护栏 | ✅ | `tests/api/test_retired_frontend_assets.py`，7 条结构性断言，红→绿双向验证 |

**已知的存量问题**：HP desk 上有 **43 个既有 worktree** 处于「有 vitest、缺 semver」状态（按旧 lock 装过 `node_modules`，但代码已前进到需要 `semver`）。这些 worktree 里跑前端测试会报 `ERR_MODULE_NOT_FOUND`。修复方式是在各自 worktree 内跑 `npm ci`（或 `prepare-worktree.sh`），逐个处理，未批量清理。

**基线文件仍然陈旧**：`docs/testing/baseline-fast.txt` 记录的是 `e04c5e11`（2026-10-15）的 `count: 0`，而 master 已前进 300 多个提交。`--update` 是 master-only 操作且会改写基线，待确认后执行。
