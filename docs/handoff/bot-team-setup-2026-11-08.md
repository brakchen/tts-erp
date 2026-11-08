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

2026-11-08 检查时，主 checkout 停在 `83bbede`，落后 master 21 个提交，且 `node_modules` 缺 `semver`（master 的 `check-runtime.mjs` 需要它）。已同步并修复。

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

## 八、待确认事项

**tower-do 看板工具在本会话不可用**。仓库 `AGENTS.md` §8 要求：多会话/多 agent 并行开发时，必须用 tower-do 看板登记 lane、写明文件归属，避免多个 Bot 撞同一批文件。HP desk 上已有 50 多个 worktree 并行，这个冲突风险是真实存在的。

目前 Bot 之间靠群聊消息交接文件范围来规避。如果要上真正的并行开发，需要先解决看板接入；在此之前建议串行推进（一次一个开发任务）。

如果本期要并行，需要你决定：

- 接入 tower-do（Pi 扩展，需要在 MiniMax Bot 侧有对应 MCP）
- 或者接受用群聊 + 明确的文件范围声明来协调
