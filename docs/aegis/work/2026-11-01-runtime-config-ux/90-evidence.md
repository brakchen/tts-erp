# 最终验证证据

## 静态审查后的六项修复（2026-11-01）

此前“独立代码复核无剩余实质问题”是首轮交付时的结论；用户要求再次静态审查后，发现暂存预检查期间可放弃但仍写入、读取重试目标错误、提交核对跳入旧配置、发布回填使用原始数字、保存错误位于弹窗外、已发布却提示没有写入六项遗漏。本节记录本轮新证据，不把之前的测试成功当成本轮修复证明。

- 在 `4785576f` 原实现上，仅加入回归后，通过隔离组件入口观察到 8 处失败，日志 `/tmp/TEST_runtime_repair_red.log`；没有直接运行底层框架。
- 修复集中在现有 reading / commands / 页面状态所有者：记录失败的只读操作；输入或视图变化时撤销旧重试；暂存并离开期间锁住按钮、Escape 和遮罩关闭；按原 attempt 匹配核对对象；回填已校验 `candidateText`；错误和核对入口只在当前活动交互区域呈现。旧的按当前视图猜测重试出口、原始输入回填和“没有发出写入操作”提示已移除，无兼容旁路或新增依赖。
- 最终新增 10 个组件用例，另强化 1 个已有用例。覆盖延迟预检查与 Escape、详情/Schema/历史/发布检查重试、输入和导航使旧重试失效、放弃创建后在列表核对、规范化数字回填与只重试 GET、弹窗内校验失败和未知结果核对、异常候选响应在写入前拒绝。
- `FRONTEND_TEST_FILTER=src/pages/runtime-configs/index.test.tsx bash scripts/test_isolated.sh frontend tests/frontend/test_components.py`：exit 0。最终选择日志 `/tmp/TEST_runtime_repair_focused.log`。失败响应及延迟场景是组件层控制的传输证据，不能称为真实网络故障或生产现场验收。
- `npm run typecheck --prefix frontend`、`npm run build --prefix frontend`、`git diff --check`：通过。构建保留原有大编辑器 chunk 提示。
- 本轮 baseline 的原生浏览器运行已生成新的运行配置截图；人工查看 390、1440、2560 宽度的检查页，确认长名称/配置键换行、差异符号和数字、完整内容排列及发布按钮可见。截图在该验证 checkout 的 `logs/TEST_runtime_config/screenshots/`；本机审阅副本在临时目录 `TEST_runtime_repair_screens/`。这些截图验证正常发布检查页，新增失败弹窗场景以组件回归为证据，没有宣称真实浏览器故障注入。
- `bash scripts/test_baseline.sh --keep-log /tmp/TEST_runtime_repair_fast_raw.log`：全套只执行一次，原始 exit 1；唯一失败项为 `tests/frontend/test_components.py::test_frontend_components`，其内 43 个组件文件中 42 个通过，673 个用例中 672 个通过。运行配置文件通过；手工成本 `manual-costs server-total pagination > disables next on a full final all page at zero and nonzero offsets` 超过既有 5000ms 超时。完整日志 `/tmp/TEST_runtime_repair_baseline.log` 和上述 raw log；原始结果保留，没有声称全量首次零失败。
- 按测试指南 §6 只复跑失败组件文件一次：`FRONTEND_TEST_FILTER=src/pages/manual-costs/index.test.tsx bash scripts/test_isolated.sh frontend tests/frontend/test_components.py`，exit 0；日志 `/tmp/TEST_runtime_repair_manual_costs_rerun.log` / `.exit`。据此归类为隔离复跑通过的偶发超时，本轮没有新增稳定失败。未修改手工成本、公共测试配置、超时或主干基线，也没有重跑整套 fast。其余完整基线选择（含运行配置的 6 个原生浏览器场景）没有失败。

本轮 Linux 独占验证 checkout 为 `/home/schan/tts-erp/.worktrees/codex-runtime-config-repair-20261101`，基于 `4785576f75145370df26112e2c10c937107ffa7f`；环境体检通过，既有 PostgreSQL Docker 模式可用。最终 5 个应用/组件测试文件与本机 SHA256 完全一致，应用源码在 baseline 开始后保持不变。远端原主工作树和生产服务未操作。

复杂度闭环：修改保留原页面、commands 和 reducer 的责任边界；失败操作的闭包替代按当前视图推断，没有并行重试出口、新依赖、后台任务或 API。弹窗和页面共享同一份反馈，只在活动区域渲染。主工作区和远端验证 checkout 保留供后续合入/复查，Git 提交及推送结果以实际收据为准。

日期：2026-11-01（Asia/Shanghai）。本次交付为技术方案和源码开发，生产服务未部署或重启。

## 结果

| 验证 | 结果 | 范围 |
| --- | --- | --- |
| `bash scripts/test_isolated.sh fast tests/api/test_runtime_config_review.py tests/api/test_runtime_config.py` | exit 0，61 项通过 | 只读检查、原子发布、旧请求兼容、版本/引用/历史护栏、非有限数字和失败无写入 |
| `FRONTEND_TEST_FILTER=src/pages/runtime-configs/index.test.tsx bash scripts/test_isolated.sh frontend tests/frontend/test_components.py` | exit 0 | 真实组件生命周期、输入/取消、分步操作、冲突和未知结果、独立表单、错误映射及筛选隔离 |
| `bash scripts/test_isolated.sh browser tests/browser/test_runtime_config_workflow.py` | exit 0，6 场景通过 | root、/tts × 390/1440/2560；当前临时库绑定、原生浏览器操作、独立 SQL 回读、焦点/无溢出、精确大整数 |
| `bash scripts/test_baseline.sh` | exit 0，suite exit 0；既有失败 0，本次失败 0，新增稳定失败 0 | 最终源代码完整 fast 选择执行一次，包含组件与浏览器回归；未更新主干 baseline 文件 |
| TypeScript / Vite | 通过 | Windows Node 22.23.2 和远端 Linux Node 22.23.3；打包有既有大编辑器 chunk 提示，无编译错误 |
| npm 安装审计 | 0 已知漏洞 | 精确锁定 `diff@9.0.0`，BSD-3-Clause，无新增运行时传递依赖 |
| Ruff / Markdown / 差异检查 | 通过 | 新 Python 文件及受影响文件；原路由既有 B008 单独排除检查，新端点用 Annotated；文档本地链接、围栏与 `git diff --check` |
| 独立代码复核 | 无剩余实质问题 | 后端、前端分别复核规格与质量，修复后再次集成复核 |

## 环境与可复查记录

- 用户指定 Linux 机器上的独立验证工作树：`/home/schan/tts-erp/.worktrees/codex-runtime-config-ux-20261101`，从计划提交 `d5a2e0a862625e21af47da09d88bd89295a6ed24` 创建，仅叠加本任务文件。主干依据为 `8b082808a025c4250852c683ae2ebc54c11df47b`。
- 使用该机器既有测试环境（Python 3.13，项目声明支持 >=3.13）和维护的测试模板。每次应用验证均由唯一 runner 创建、使用并清理独占 `tts_erp_test_*` clone，没有共享/生产数据库替代。
- 18 个应用/测试文件的本地与远端 SHA256 清单完全一致；最终 baseline 运行期间未再修改应用源代码。
- 远端日志：`/tmp/TEST_runtime_api.log`、`/tmp/TEST_runtime_frontend_checked.log`、`/tmp/TEST_runtime_browser.log`、`/tmp/TEST_runtime_baseline.log`。对应 `.exit` 文件均为 0；后者明确输出 `PASS — zero new stable failures`。
- 浏览器截图位于验证工作树的 `logs/TEST_runtime_config/screenshots/`，最终三种宽度的检查页已下载并人工查看。桌面、手机与超宽屏分别确认长名称换行、变更文字/符号、旧新内容布局、发布按钮及全页无横向溢出。
- 原 Linux 主工作树的插件和物流/调度相关未提交工作保留；未调用生产迁移、业务同步、API 或 worker 重启。临时测试引导补丁已撤回，没有进入本分支。

## 边界与续接

发布成功表示新 revision 已提交，后续业务读取时使用；不承诺运行中的任务自动刷新。未知写入结果仍需要手动核对，没有新增持久化回执系统。凭证原 PUT 的 upsert 兼容语义保留。

技术方案、现行 API/运维文档同步更新。后端辅助工作树已通过 Codex 归档保存可恢复快照；主要实现分支保留供合入。Git 提交 SHA、推送和工作树清洁状态以实际 Git 收据及最终回复为准。
