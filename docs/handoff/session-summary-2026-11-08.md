# 2026-11-08 本期工作总结

> 总管：Chief · 范围：建立 Bot 团队、修复测试环境、完成前端迁移 P6 的路由收敛与测试侧清理

---

## 一、交付物清单

### 已合入 master

| 提交 | 内容 |
| --- | --- |
| `2b68b40` | P6 路由收敛（15 处双路分支 → 单一 React renderer）+ 环境修复 + 评审核实文档 |
| `64f6ff7` | 开发环境自检清单 + 模板库刷新说明 |

### 待验证后合并（12 个提交）

| 提交 | 内容 |
| --- | --- |
| `e232f1c` | 测试侧清理：删除只对旧实现成立的断言（21 文件，+200/-1974） |
| `5a04e70` | 补齐未声明的 `--surface-secondary` 令牌 + 修正设计令牌重定义检查 |
| `a9605de` | `browser_conftest` 改用包路径导入，消除多目录 conftest 冲突 |
| `f205c74` | 告警页就绪信号改用共享 canned 契约 |
| `bd4a7753` | 字体白名单不再要求三套栈互不相同 |
| `f72a3089` | **旧资产退役**：12 模板 + 12 脚本全删（-9198 行） |
| `00654af` | 移除 ad-daily 已退役的内联旧页面 HTML（-108 行 + 死代码） |

---

## 二、修了什么

### 环境（阻断性，全部已解）

1. **测试机地址失效**：`AGENTS.md` 和 `docs/ops/tts-erp.md` 写的是 `192.168.47.129`（已下线，SSH 超时）。
   实际是 HP desk `192.168.1.2`。已更正并补上主机名、系统版本、SSH 别名。
2. **新 worktree 跑不了测试**：`.venv`、`.env.test`、`frontend/node_modules` 都是 gitignored。
   新增 `scripts/envsetup/prepare-worktree.sh` 并合入 master，实测通过。
   **前端依赖不能用软链接**——Node 的 ESM 会解析到真实路径，跨 worktree 共享会报
   `Cannot find package 'semver'`。脚本按各 worktree 自己的 lock 跑 `npm ci`。
3. **主 checkout 落后 312 个提交**且前端依赖过期（缺 `semver`）。已同步并补齐。

### P6 退役（前端迁移最后阶段）

核实结论：**P2–P5 实质已完成**，方案文档"P2–P6 待实施"的状态说明过时。
`MIGRATED_PAGE_IDS` 已含全部 18 个注册页面，19 个页面目录有真实 React/TS 代码（约 1.4 万行）。

- **路由收敛**：`pages.py` 14 处 + `ad_daily.py` 1 处双路分支 → 单一 `render_frontend_page`。
- **测试侧清理**：19 个测试文件、39 处读旧文件内容的断言，分三类处置（详见
  `docs/handoff/p6-test-side-retirement-plan.md`）。
- **退役护栏**：新增 `tests/support/retired_frontend_assets.py` 集中维护退役名单。

### 顺带修掉的真实缺陷

| 缺陷 | 影响 |
| --- | --- |
| `--surface-secondary` 未声明 | `media-workspace.css` 第 70/104 行背景色实际是透明的。全仓唯一未声明令牌 |
| 多目录 conftest basename 冲突 | 8 个同名 `conftest.py` 互相覆盖，34 项测试在完整收集时全挂 |
| `--scope auto` 的假绿 | 空计划时 driver 直接 exit 0，从不跑套件却报"零失败" |

---

## 三、我做错的两件事

### 1. 批量删库误伤了并行测试

清理 22 个残留临时库时只查了 `test_isolated.sh` 进程，漏查 `pytest` 和 `test_baseline`，
把测试工程师正在用的库删了，对方第一次基线出现 700+ 个 E。

测试工程师用 docker events 还原了删除时刻和执行命令，取证比我的自查更完整。

**已补救**：`docs/guides/agent-testing.md` 新增 §1.1 清理规范——只能删自己 lane 的库、
绝不按前缀批量删、删前确认无活跃 pytest/test_baseline、绝不删 template 家族、
`psql -f` 批量 DROP 会因事务块静默失败必须逐条执行。

### 2. 用 `git checkout <branch> -- <dir>` 覆盖工作区，静默丢掉了自己的三处修正

为了把另一个分支的测试侧清理同步到本地，我用了
`git checkout origin/feature/X -- tests/` + `git reset`。这**不是 merge，git 不会报冲突**，
`git diff` 也显示"与目标分支一致"——看起来完全正常。

但它把我在同三个文件上的修正一并覆盖成了旧版：

| 文件 | 被丢掉的修正 | 后果 |
| --- | --- | --- |
| `tests/browser/conftest.py` | `browser_conftest` 改用包路径导入 | 34 项测试在多目录收集时全挂 |
| `tests/render_support.py` | 字体白名单不再要求三套栈互不相同 | 14 项字体栈用例全挂 |
| `tests/browser/test_render_smoke.py` | 告警页就绪信号改用 `wait_canned_page` | 4 个视口溢出用例超时失败 |

**暴露时机**：合并后的 browser 渲染门槛出现 FAILED。查根因时对比"合并候选 vs 我的分支"，
两边**完全一致**——因为两边一致的都是**被覆盖后的错版本**。

**教训**：多来源汇合时，最终状态要按**意图**验收（本轮该有哪些修正），
而不是按**来源一致性**验收。工作区"干净"不等于"正确"。

**正确做法**：
1. 合并两个来源的改动用 `git merge` / `git cherry-pick`，不要用 checkout 覆盖
2. 已经用了 checkout 的话，事后逐个核对本次会话的每个修正是否还在（用 grep 找特征串）
3. 提交前按修正点逐条验证，不只看文件是否一致

### 3. 并发跑测试导致假失败

4 核机器上同时跑多个 pytest，负载到 9.8，浏览器测试因资源竞争假失败
（`test_frontend_components` 并发下失败、单独跑 `[100%]` 通过）。
两次全量 fast 验证都没跑完就停掉了。

---

## 四、遗留

| 项 | 状态 | 说明 |
| --- | --- | --- |
| 删旧模板/旧脚本 | ✅ 已做 | 12 模板 + 12 脚本全删（`console.js` 保留），-9198 行 |
| `_render_page` 与 Jinja 环境 | ✅ 已做 | `pages.py` 的 `_render_page` / `_templates_env` / `_js_version` 已删；`_sidebar` / `_sidebar_css` / `_SIDEBAR_CSS` / `_SIDEBAR_TOGGLE_JS` 按 `docs/design/reuse-evaluation-2026-10.md` 的决定原样保留 |
| 基线文件陈旧 | 待确认 | `baseline-fast.txt` 仍记 `e04c5e11`（2026-10-15）。`--update` 是 master-only 操作 |
| `test_spu_price_columns.py` 4 项失败 | 待查 | 单独跑也失败，是 master 上的确定性失败，不是偶发 |
| 43 个缺 semver 的既有 worktree | 待清理 | 逐个跑 `npm ci` 即可 |
| 9877 绑 `0.0.0.0` | 待用户确认 | 真实暴露面。改法：`/home/schan/.config/tts-erp/runtime.env` 的 `TTS_ERP_HOST` |

---

## 五、平台限制

**MiniMax Bot 对同一个 Bot 的多个任务串行执行**，并发上限 = 团队 Bot 数量。
派给三个 Bot 各一个 lane 后，它们全部排队 50 分钟没启动。我取消了冗余 lane、
自己接手了路由收敛，其余并行部分改用后台助手（`start_helper`）完成。

**后台助手的价值超出预期**——两个助手都发现了我的疏漏：

- 测试侧清理助手找出 3 处我漏掉的引用（`Path()/"static"/"js"/...` 分段拼路径，
  `git grep` 的连续路径模式抓不到），并做对照实验证明那 6 个 `media-assets` 失败是既有问题。
- 方案更新助手发现我的 P6 计划文档里「`PAGES` 注册 19 个页面」与「18 个页面全部已迁移」
  自相矛盾——实际是 18 个（`MIGRATED_PAGE_IDS` 才是 19 项 = 18 + login）。

`tower_do` 看板工具在 MiniMax Bot 会话里不可用（`mcp list` 为空），
仓库 §8 要求的 lane 登记无法执行——本期用「显式写域声明 + 群聊交接 + git grep 检查」替代。
这套办法覆盖退役类任务（切分依据是文件）；并行改同一模块的上下游则不够。

---

## 六、验证记录（实测）

| 验证 | 命令 | 结果 |
| --- | --- | --- |
| 路由收敛后 api 域 | `bash scripts/test_isolated.sh api` | `API_EXIT=0`，822 秒，无 FAILED 行 |
| ad_daily 清理后 api 域 | 同上 | `API_EXIT=0`，348 秒，无 FAILED 行 |
| 字体栈修正 | `bash scripts/test_isolated.sh fast tests/browser/test_render_smoke.py -k font_stacks` | `[100%]`，14 项全绿 |
| 告警页溢出修正 | 同上 `-k spu_deterioration_alert_has_no_horizontal` | `[100%]`，4 个视口全绿 |
| conftest 冲突修正 | `bash scripts/test_isolated.sh fast tests/browser/test_spu_price_columns.py tests/api/test_ad_daily.py tests/proxy/test_miaoshou_rate_limit.py` | 13 个 `_mock_payload` ERROR 消失 |
| style_tokens 修正 | `bash scripts/test_isolated.sh unit tests/frontend/test_style_tokens.py` | 全部通过 |

**注意**：本仓库 pytest 输出**没有 `N passed` 汇总行**，判成败看退出码和 `FAILED`/`ERROR` 行。

---

## 七、相关文档

| 文档 | 内容 |
| --- | --- |
| `docs/handoff/bot-team-setup-2026-11-08.md` | Bot 团队构成、协作流程、环境要点、并发限制 |
| `docs/handoff/p6-retirement-parallel-plan.md` | P6 并行方案、写域切分、语义变化 |
| `docs/handoff/p6-test-side-retirement-plan.md` | 19 个测试文件的三类分类与处置、旧 CSS 未清理的原因 |
| `docs/handoff/dev-env-checklist.md` | 开发环境自检清单（22 条实测记录、7 类故障排查） |
| `docs/handoff/test-baseline-verify-2026-11-08.md` | 测试链路独立复核 + 基线失败分类 |
| `docs/handoff/architecture-review-scope-2026-11-08.md` | 架构评审结论的适用范围核实（v1 vs v2） |
| `docs/handoff/topic-directory-index.md` | 190 个文档的四段式主题目录索引 |
| `docs/guides/agent-testing.md` | 已补充：共享主机容量、worktree 准备、conftest 冲突、renderer 切换清单 |
