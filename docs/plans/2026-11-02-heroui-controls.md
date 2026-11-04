# HeroUI 控件迁移收口

## 目标、架构与技术栈

按用户 2026-11-02 的授权，将审计发现的原生基础交互控件、普通数据表和 OAuth 辅助页面统一到既有 React 19 / TypeScript / HeroUI 3.2.6 MPA。复用现有 AppShell、renderer、bootstrap 和 API client，不新增 UI 库、业务计算、认证协议或数据库路径。

TaskStartSnapshot：`origin/master` / `bda08512009390d3d87aeb3a684963183178af0e`；任务分支 `codex/heroui-controls`；Codex 原生创建工作树 `C:/Users/chen/.codex/worktrees/heroui-controls/tts-erp`，初始干净。原主工作树的插件与 `uv.lock` 非本任务所有。主执行器仍默认旧 cwd，所有命令显式指定原生工具返回的新工作树。用户明确要求新 worktree；完成后保留任务工作树供查看，未经本次要求不部署或重启服务。

依据：根 AGENTS.md、`docs/guides/frontend-development.md`、`agent-testing.md`、`agent-safety.md`、`agent-git-workflow.md`，以及 `docs/plans/spu-react-migration-2026-11-01.md`。已有页面只导入 HeroUI 不能证明基础控件都已迁移，因此需改源组件，单改样式不能完成目标。

## 兼容边界与验收

- 保留 URL/rootPath、真实字段、远程排序/分页、全部业务列、逐项选择、只读禁用、状态提示、确认和失败恢复。空字符串选项映射为独立的非空 UI key，API 值不变。
- 用公开 HeroUI Select、Checkbox、RadioGroup、Disclosure、TextArea、ProgressBar、Button、Table；不包装另一套同名控件，不依赖内部 DOM。普通表格若不支持跨行展开，将明细组合到表格附近，保持原数据与可达性。
- 允许保留已有专业能力：Tabulator 盈利主表及其独占 formatter DOM、vanilla-jsoneditor、uPlot；原生文件/视频接口保留浏览器能力，展示入口可用 HeroUI。
- OAuth 和 403 只变呈现，不变认证、角色、state 消费、JSON、HTTP 状态或授权持久化。结构化 bootstrap 不包含 token、App secret 或未经筛选的凭据；公共回调不触发登录重定向。退役被替代内联 HTML/脚本，不新增永久双栈。
- 本次 TDD mode off / route skipped：机械控件替换采用现有行为测试并适配真实交互；辅助公共页增加必要的状态、初始化和注入边界回归。所有测试仍仅通过隔离 runner。

## 分工与执行

工作树只有父执行器写入；三个子 agent 只读任务工作树，在各自外部 scratch 中产出提案，由父逐项审阅后写入。子 agent 不暂存、不提交、不推送；测试由父执行器统一运行。

1. 父：shops、manual-costs；整合与测试适配、最终验证和提交。
2. admin-proposal：runtime-configs、intercept-configs、video-publish 控件及对应测试提案。
3. tables-proposal：ad-daily、spu-profitability、spu-profit-deterioration 的控件/普通表格及对应测试提案。
4. auxiliary-proposal：OAuth/403 renderer、结构化页面数据、前端入口及辅助页测试提案。
5. 独立只读检查范围符合性、公开组件 API、状态/权限保留和残留原生控件；修复后再做质量复查。

## 验证与交付

- Windows：复用已安装的锁定依赖做 TypeScript / Vite 构建检查。
- 用户指定 VM `schan@192.168.47.129`：独立候选源副本，复用已配置 venv 和只读测试配置；不得修改主运行 checkout 的 WIP。先执行依赖 `--check`，再 `bash scripts/test_isolated.sh frontend ...` 及针对 OAuth/403 的最小 API 选择。
- 最后执行一次 `bash scripts/test_baseline.sh` 与已提交 master baseline 比较；失败仅重跑最小选择。浏览器验证375/1440/2560及root/`/tts`，核对展开/焦点、选择、分页、长内容与溢出。
- 显式路径暂存，检查差异与秘密，中文规范提交、推送任务分支；记录证据和仍未覆盖的现场版本。保留专业组件例外，不以静态扫描冒充交互验收。

## 实施与验证记录

- 2026-11-02 建立任务分支，2026-11-03 同步至 `origin/master` 的 `b3544d1f90afe3e5409e40b4594b25d49170ba0a`。保留此期间主线新增的店铺布局和 SPU 销售 / 成本 / 利润三卡、价格分区、ROI 对比表与费率依据；将其中新出现的普通表格和折叠控件一起迁移。
- 基础控件、普通表格及 OAuth/403 辅助页已接入 HeroUI。源码扫描仅剩两个隐藏 file input，均由可见 HeroUI Button 激活；专业 Tabulator、JSON editor、uPlot 与视频播放能力按规范保留。独立范围和最终合并质量审查未发现未解决问题。
- 价格说明按钮置于各价格区的表格外，使用官方 `Modal.Root + Button`：RAC 表格行和 Modal 同时恢复焦点会发生竞争，调整组合后无需手写延时聚焦。广告与对比明细放在普通表格旁，支持展开、关闭和返焦。
- VM 初始验证副本为 `/home/schan/.cache/tts-erp-validation/heroui-controls-20261102`；同步主线后的副本为同级 `heroui-controls-synced-20261103`，不改运行 checkout。Node 22.22.2 经官方 SHA256 核验，依赖来自锁文件；只使用安装器生成的测试配置与隔离 runner，没有生产数据库、部署或重启操作。
- TypeScript 检查与 Vite 构建通过。最窄 OAuth、辅助页、前端页面及用户权限 API 选择 **118 项通过**。组件测试保留完整日志到忽略路径 `frontend/.cache/vitest.log`；修复 Checkbox context、计时器和禁用按钮等待等真实交互适配，保留原业务、请求与精确等待断言。
- 同步主线后浏览器 / API 源码契约窄选 **46 项：44 通过、2 失败**。通过范围包含 375/1440/2560、root/`/tts`、店铺未保存确认、广告键盘展开返焦、SPU 选择/分页/预测、价格键盘打开和返焦及重复展开、公共辅助页。两项筛选面板宽度断言在干净 `b3544d1f` 也同样失败（1616px 与旧断言 <1100px 不符），保留新主线布局，不修改通用旧断言。
- 首次完整 fast 在同步前的 `bda08512` 源上执行 **3392 项：3235 通过、142 失败、15 跳过（含 1 xfail）**。从干净同 SHA 只重跑失败选择，101 项 API 中 98 复现、38 项其他范围中 37 复现，共 **135 项主线已有失败**；本次引入的控件 / 查询 / 焦点失败已修复。未重建或放宽已提交的零失败 baseline。
- 同步主线后对最终源再次执行 `bash scripts/test_baseline.sh`：**3393 项：3242 通过、136 失败、15 跳过（含 1 xfail）**，耗时 1584s。135 个失败 ID 匹配已在干净主线复现的集合；另一个是组件聚合入口（默认 7 workers 下 719 项中 7 项等待 / 5000ms 超时）。只重跑其 4 个失败文件，**101 项全部通过**；不再重跑完整 fast。
- 使用相同隔离入口加 `VITEST_MAX_WORKERS=4` 复验完整组件集，**49 文件 / 719 项全部通过，82.24s**。不增加 180s runner 或 5000ms 单项时限，不减少断言、不修改全局并发配置。默认并发失败按隔离复验结果记为非稳定失败，不能将默认 fast 或已提交的零失败 baseline 宣称为绿色。
- Python 变更范围 Ruff 通过；`tests/api/test_spu_roi_api.py` 的 5 项历史告警与干净 `b3544d1f` 一致。所有测试均经隔离 runner 创建并清理独立测试克隆。初次重名测试文件导致的 collection abort 已通过给浏览器文件独立命名修复，没有将中断运行当作通过。

原始验证日志与 XML 在 VM 的 `/home/schan/.cache/tts-erp-validation/`：`heroui-api.xml`、`heroui-fast.xml`、`heroui-base-api-failures.xml`、`heroui-base-other-failures.xml`、`heroui-synced-targets.xml`、`heroui-synced-base-pilots.xml`、`heroui-synced-fast.xml`、`heroui-synced-fast-vitest.log`、`heroui-components-workers4.xml`、`heroui-components-workers4-vitest.log`。提交后保留任务分支和工作树供审阅，不宣称主线全量基线已绿或生产已部署。

最终复验命令（在 VM 验证副本执行，先将已核验 Node 路径加入 PATH）：

```bash
bash scripts/test_baseline.sh --keep-log ../heroui-synced-fast-suite.log
FRONTEND_TEST_FILTER=src/pages/manual-costs bash scripts/test_isolated.sh frontend tests/frontend/test_components.py
FRONTEND_TEST_FILTER=src/pages/spu-profitability/index.test.tsx bash scripts/test_isolated.sh frontend tests/frontend/test_components.py
FRONTEND_TEST_FILTER=src/pages/sync-jobs/index.test.tsx bash scripts/test_isolated.sh frontend tests/frontend/test_components.py
VITEST_MAX_WORKERS=4 bash scripts/test_isolated.sh frontend tests/frontend/test_components.py
```
