# lane A 交接记录：前端旧实现退役收口 + master 测试链路修复（2026-11-09）

> 执行人：开发工程师（bot_a7b5359e0d7c）｜委托：Chief（lane A）
> 测试机：HP desk `schan@192.168.1.2`（`ssh hp-desk`），worktree `/home/schan/tts-erp/.worktrees/retire-legacy-frontend-routes`
> 本记录中每条命令均在测试机真实执行并保留真实输出；未执行的项目一律标注「未执行」，不写「应该通过」。
>
> **交付说明**：`post_group` 与 `message_bot` 两个投递接口在本次全程返回「queued」未实际投递（最早一条挂 1 小时仍未送达），与测试工程师在 `docs/handoff/test-baseline-verify-2026-11-08.md` 记录的故障一致。故本文件是本轮完整且权威的记录载体，待投递通道恢复后再同步群内。

## 0. 结论速览

| 项 | 结论 |
| --- | --- |
| lane A 派工的 4 项任务 | **在 master 上早已完成**，本轮未重复做，改的是退役**留下的两处陈旧说明**（2 文件，+15/-6，纯注释与文档串） |
| master 测试链路 | 本轮曾**整体断裂**（任何测试跑批都 0 用例），已定位并修复；**修复已进入 master**（`dab9f4fc`） |
| 窄范围验证 | 退出码 0 |
| 全量 unit | `UNIT3_EXIT=0`（重新同步后复验），`pytest_total=295.833s`，0 个 FAILED/ERROR |
| DoD 基线 | `BASELINE4_EXIT=1`，**仅 1 个 new failure**，在最终分支上隔离复跑通过 → 并发时序偶发，非本 lane 引入 |
| 需要 Chief 决策 | 2 项，见 §6 |

## 1. 分支

| 分支 | HEAD | 内容 |
| --- | --- | --- |
| `chore/retire-legacy-frontend-routes` | `a5bed714` | lane A 本体（2 文件）+ 本交接文档；已同步到最新 master `dab9f4fc` |
| `fix/selection-legacy-frontend-const` | `82a3bd1b` | 修 master 测试链路断裂 —— **已由上游合入 master（`23c69681` → `dab9f4fc`）**，本分支保留作记录 |

lane A 相对 master 的完整改动（**3 个文件**）：

```
docs/handoff/lane-a-frontend-retirement-2026-11-09.md   | 183 +++++++++++++
tts_erp_v2/api/v2/pages.py                              |  14 +-
tts_erp_v2/static/js/console.js                         |   7 +-
```

worktree 状态干净，本 lane 一次性克隆库已清理。

## 2. 核实结论：lane A 的 4 项任务在 master 上已经完成

| 任务 | 实测状态 |
| --- | --- |
| pages.py 删旧 renderer 分支 | **已完成** `80b218a9 feat: 收敛已迁移页面的双路 renderer 分支`（14 处双路分支收敛为单一 `render_frontend_page`；`MIGRATED_PAGE_IDS` 分支全部消失） |
| `_render_page()` 退役 | **已完成**：pages.py 中 `_render_page` 与 `_templates_env` 零出现；护栏 `tests/api/test_retired_frontend_assets.py::test_pages_module_has_no_legacy_jinja_renderer` 锁死不得复活 |
| console.js | **保留**，但已零运行时消费者（§5） |
| vendor/ 清理 | **已完成** `cec49a02`：uPlot / JSONEditor / Tom Select 共 9 个 vendored 副本已删除，React 侧改用 npm 依赖 |

依据：`docs/plans/frontend-progressive-migration.md` §P6 写明「实施完成，已合入 master（`cec49a02`）」；`docs/handoff/p6-retirement-parallel-plan.md` 头部亦标注「状态：已完成（2026-11-08）」。

`docs/handoff/p6-retirement-parallel-plan.md` §7 另列「console.js 和 vendor/ 是否要一起清」为**待用户确认**项，本轮未擅自决定。

### 2.1 现存 vendor 资源的活消费者（逐个文件核实）

| 文件 | 消费者 |
| --- | --- |
| `bootstrap.min.css` | `tts_erp_v2/api/v2/auth.py:305` 回落登录页；`tests/browser/test_runtime_config_workflow.py:155`、`tests/support/frontend_p2_browser.py:264` |
| `tabulator.min.css` | `frontend/src/pages/spu-profitability/ProfitabilityTable.tsx:6`（直接 import） |
| `tabulator.min.js` | 同文件 `:25`（运行时加载 pinned 版本） |
| `NOTICE.md` | 许可证归属说明，保留 |

## 3. lane A 本体改动（2 文件，+15/-6，不改运行时行为）

1. `tts_erp_v2/api/v2/pages.py` —— 模块说明仍宣称本模块保留 "retained legacy-document rendering helpers"、由 "Jinja context" 拥有共享导航。实测 legacy renderer 已删除，**所有路由只走 `render_frontend_page`，不再渲染任何 Jinja 文档**。说明改为写实，并注明 `_sidebar_html` / `_SIDEBAR_CSS` / `_SIDEBAR_TOGGLE_JS` 与内容戳助手是「有意保留的复用参考，不是渲染路径」。
2. `tts_erp_v2/static/js/console.js` —— 文件头仍写着 "Wired from /v2/pages/manual-costs"。该页已由 React 接管（`frontend/src/pages/manual-costs/`），原 Jinja 模板随 P6 删除，**现在没有任何页面在运行时加载本文件**。头部改为如实描述现状，并指向方案 §7 的待确认项。

改动方式：一次性脚本做精确片段替换，逐条断言旧片段在源码中唯一存在；编辑后自检护栏字符串（`static/js/`、`templates/pages`、`MIGRATED_PAGE_IDS:`、`_render_page`、`_templates_env`）均未出现在 pages.py。

## 4. 顺带修复：master 测试链路曾整体断裂

### 4.1 症状与成因

上游 `6f603eab`（经 `403d8724` 合入 master）删掉了 `scripts/_test_selection.py` 的 `LEGACY_FRONTEND` 常量——那三个旧前端测试文件已随 P6 退役删除，留着路径会让每次非 fast 选测把它们报成 missing 并卡死基线门禁——但**漏改了两个调用点**：

```
scripts/_run_selected_tests.py:81
    args.extend(f"--ignore={path}" for path in LEGACY_FRONTEND if path not in required)
NameError: name 'LEGACY_FRONTEND' is not defined
   → test_baseline.sh --scope auto|frontend|backend 全部无法启动

tests/scripts/test_selected_runner.py:14
    from scripts._test_selection import COMPONENTS, LEGACY_FRONTEND, Selection
ImportError: cannot import name 'LEGACY_FRONTEND' from 'scripts._test_selection'
   → 整个套件在收集阶段就中断（"Interrupted: 1 error during collection"），**一个用例都跑不了**
```

该断裂可在 `origin/master` 本身复现（`git show origin/master:scripts/_test_selection.py` 中 `LEGACY_FRONTEND` 计数为 0，而另两个文件仍引用它），**不是本 lane 引入**。

### 4.2 修法（行为等价删除）

按上游自己在 `scripts/_test_selection.py` 留下的意图（"Frontend coverage now lives under `tests/frontend/` and is picked up by the glob in `_run_selected_tests.py`"）：

- runner：删掉 `--ignore` 块——那三个路径对应的文件已不存在，忽略它们本就没有对象。
- 测试：去掉 `legacy_count` 这一维参数化与对应断言（它断言的正是那三个已删文件的收集数量），保留该测试真正要守的「混合选测不重复收集」语义。

改动：`scripts/_run_selected_tests.py`（-2 行）、`tests/scripts/test_selected_runner.py`（+6/-9 行），合计 **+6/-11**。

**该修复现已进入 master**：`git show origin/master:scripts/_run_selected_tests.py | grep -c LEGACY_FRONTEND` → 0；`git show origin/master:tests/scripts/test_selected_runner.py | grep -c LEGACY_FRONTEND` → 0；`git show origin/master:scripts/_test_selection.py | grep -c test_polling_channels.py` → 0。

### 4.3 护栏作用域缺口

上游同时新增了 `tests/scripts/test_test_selection.py`，但它**只覆盖 `select()` 纯函数，不 import 也不执行 `_run_selected_tests.py`**，因此没能发现这次断点。建议后续把「runner 的参数拼装路径」也纳入护栏。

## 5. 要 Chief 决策：console.js 现在零运行时消费者

实测证据链：

1. `tts_erp_v2/templates/` 整个目录**已不存在**（`find` 报 No such file or directory）。
2. 全仓搜 `console.js` 只剩 4 处**非运行时**引用：
   - `tts_erp_v2/app.py:149` —— public static mount 的注释
   - `tts_erp_v2/static/__init__.py:7` —— docstring 的 URL 映射示例
   - `tests/api/test_middleware.py:310,317` —— 断言 `/static/js/console.js` 静态可达
   - `tests/api/test_manual_costs_page_v2.py` —— 20 余处源码级断言
3. React shell 实测只加载 `/static/frontend/{release_id}/` 的 Vite 产物（`frontend_pages.py` 中无 console 引用）。

原先保留它的理由（「是 public static 资源、不是页面资产」）在 lane B 删除 `manual-costs.html` 之后，**只剩「测试还在断言它的源码」这一条**。删除它需要改动 4 个不在 lane A 写域内的文件 + 退役护栏白名单 `KEPT_JS`（`tests/api/test_retired_frontend_assets.py`），且方案 §7 将其列为**待用户确认**项 → 本轮未擅自删除。

### 5.1 pages.py 的 sidebar 助手：生产无消费者，但按设计决定保留

`_sidebar` / `_sidebar_css` / `_sidebar_html` / `_SIDEBAR_CSS` / `_SIDEBAR_TOGGLE_JS` 在 pages.py 内已无任何路由调用，但：

- `scripts/oneoff_p6_retire_legacy_assets.py` 头部写明：「路由收敛后已无调用者；保留 …（`docs/design/reuse-evaluation-2026-10.md` 已决定原样保留，测试也直接断言这些常量）」；
- `docs/design/reuse-evaluation-2026-10.md:57-60` 有同样的决定；
- `tests/api/test_pages.py` 直接 import 并断言这些常量。

删除它需要改测试文件 + 推翻已记录的设计决定，超出普通 lane 范围，**本轮未动**。

## 6. 需要 Chief 决策的两项

1. **console.js 去留**（§5）。
2. **master 的已提交基线文件已陈旧**：`docs/testing/baseline-fast.txt` 记的是 2026-10-15 的 master `e04c5e1`、`count: 0`，而当前 master 已有已知失败（测试工程师 §8.6 记录 80 项，最新提交修掉其中一批）。在基线重新生成之前，门禁里的 "new failures" **不是可靠信号**——本次唯一的那个 new failure 就是这一类。

## 7. 验证（真实输出）

### 7.1 窄范围 — 通过

```bash
bash scripts/test_isolated.sh unit \
  tests/api/test_manual_costs_page_v2.py \
  tests/api/test_middleware.py \
  tests/api/test_retired_frontend_assets.py \
  tests/api/test_pages.py
```

```
NARROW_EXIT=0
```

### 7.2 全量 unit — 通过（同时证明整个套件的收集已恢复）

```bash
bash scripts/test_isolated.sh unit
```

合并修复后的首轮：

```
UNIT2_EXIT=0
[test-timing] pytest_total=208.383s
[test-timing] isolated_total=214.447s
FAILED/ERROR 行数：0
```

重新同步 master 后的复验（最终分支 `a5bed714`）：

```
UNIT3_EXIT=0
[test-timing] pytest_total=295.833s
[test-timing] isolated_total=302.226s
FAILED/ERROR 行数：0
```

修复前，同一条命令在 master 上是收集中断（0 个用例）。

### 7.3 DoD 基线对照 — 1 个 new failure，定性为并发时序偶发

```bash
bash scripts/test_baseline.sh --scope auto --base dab9f4fcec7281bddccb9ab3e711f253c47ac83a
```

```
BASELINE4_EXIT=1
[test-baseline] new failures:
tests/scripts/test_browser_parallel.py::test_termination_allows_children_to_run_cleanup_traps
```

定性依据：

- 该用例在**修复前**那次基线（base 为另一个 master）里同样失败，是同一个用例 → **不是本 lane 引入**；
- 在**最终分支 `a5bed714` 上隔离复跑通过**：
  ```bash
  bash scripts/test_isolated.sh fast tests/scripts/test_browser_parallel.py::test_termination_allows_children_to_run_cleanup_traps
  ```
  ```
  FINAL_ISOLATED_EXIT=0
  [test-timing] isolated_total=7.283s
  FAILED/ERROR 行数：0
  ```
  （注意必须用 `fast` 作用域：该用例没有 `layer_unit` 标记，用 `unit` 跑会因标记过滤而 0 收集、退出码 5，看不出真实结果。）
- 失败机理是硬超时：等 2 个 worker 就绪 10s + SIGTERM 后等协调进程退出 20s，满负载时正好卡在 20.39s 超时；
- 与本 lane 改动无任何交互——本 lane 改的是一个 docstring 与一个注释。

（一次「干净 master 隔离复跑」未能给出有效信号：该 ad-hoc 命令自身退出码 2、0 个 FAILED 行，**不作为证据**。）

### 7.4 其它实测

- 强制检索（方案 §4）：`git grep` 模板名 / 脚本名 / `templates/pages` 字面量三项，命中项经逐个反查均为注释、一次性脚本或已退役测试的引用，无运行时消费者。
- 临时库：本 lane 一次性克隆库已 drop，`pg_database` 中无 `tts_erp_test_chore_retire_legacy_frontend_routes%` 残留。

## 8. 本轮留下的现场

- tower_do 看板工具在 MiniMax Bot 会话不可用（`tool_search` 仅返回 cron_*），lane 无法在 board 登记，只能以本文件与分支留痕。
- `post_group` 与 `message_bot` 投递全程返回「queued」未实际投递（见文首交付说明）。
- 工作区：`/home/schan/tts-erp/.worktrees/retire-legacy-frontend-routes`，状态干净。
