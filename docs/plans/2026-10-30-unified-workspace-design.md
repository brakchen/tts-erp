# 全站工作台视觉统一与采购查询修复

## 目标与范围

用户批准沿用登录页的墨绿、浅色风格，重新设计其余全部页面，并修复采购工作台列表不可用。
覆盖服务端页面目录的 15 个业务页面（13 个 React 页面、重点关注 SPU 与 SPU ROI 两个旧页面）。
本次沿用当前分支；用户已明确允许不创建 worktree。登录页已有未提交改动属于同一会话，保留。

## 实现边界

- 唯一设计令牌继续由 `tts_erp_v2/static/css/tokens.css` 维护，React 也导入该文件。
- React 导航、页头、内容容器继续由 `frontend/src/app/AppShell.tsx` 维护；旧页面保留既有共享导航实现。
- 页面筛选、内容分组、表格、分页统一采用共享布局类；不复制控件、不深改 HeroUI 内部 DOM。
- 不迁移旧页面的查询与计算逻辑，不改变 API 权限、金额精度、保存确认与未知结果恢复流程。
- 采购列表的可选图片不能因缺少 MinIO 配置阻断商品信息；仅处理明确的配置异常，不能吞掉 SQL 或未知错误。
- Change Necessity: code-change。TDD Mode: off / skipped；补充有行为价值的回归测试，不要求严格先红后绿。
- 单人串行实现。原分支 HEAD: `962f14b6a822bb08c93cafd12b3e6163d36530e8`。

## 步骤

1. [x] 共享设计令牌、React 应用骨架与页面布局样式。
2. [x] 采购列表图片配置异常隔离、错误反馈与工作区重设计（代码实现）。
3. [x] 逐页统一筛选、指标、表格、编辑区与反馈；覆盖所有 15 个业务页面。
4. [x] 旧页面共享样式与导航对齐，保持现有行为。
5. [ ] 类型检查、生产构建、静态资源打包、差异检查及安全测试入口验证。

## 验证与已知限制

必需命令：`bash scripts/test_isolated.sh frontend`、相关 API 测试、`bash scripts/test_baseline.sh`。
构建：`npm run typecheck --prefix frontend`、`npm run build --prefix frontend`、`node frontend/scripts/package-build.mjs`。
验收包含桌面/手机、长标题、空态、读取失败、只读、草稿、逐行提交与未知结果核对。
当前会话已遇到 Bash E_ACCESSDENIED、浏览器连接失败、本机 HTTP 访问受限和 Git 元数据写入受限；
必须明确区分代码实现、构建通过、测试通过、浏览器验收和已提交状态，不能互相代替。

参考：`docs/guides/frontend-development.md`、`docs/guides/agent-testing.md`、
`docs/guides/agent-safety.md`、`tts_erp_v2/accounts/pages.py`。

## 当前检查点

13 个 React 页面都加入共享 PageIntro，筛选、选择框、表格、分页和内容面板使用共享布局。
两个旧页面共用 `spu-profitability.html` 的新页头说明、令牌、导航和表面样式，未迁移计算代码。
本地 `.env.local` 中 7 项必需 MinIO 配置均未设置；两个列表读模型的镜像地址生成已仅捕获
`MinioConfigError`，保留商品事实并返回可选 `image_url=null`。不捕获未知错误，不修改上传契约。
新增 DTO/图片分支回归和 HTTP 500 脱敏反馈用例；原数字精度与保存/恢复测试保留。

最新类型检查、Vite 构建与本地静态资源打包通过；最终资源版本见 `static/frontend/release.json`。
Python AST 语法检查通过。Ruff 本机未安装。
API/前端隔离入口、依赖体检和基线命令均被 `Bash/E_ACCESSDENIED` 阻止，测试未执行。
浏览器验收、现场列表故障复验及后端重启尚未完成，不能声称现场问题已验证修复。
Git 写入仍受当前沙箱限制，改动未提交/推送。

## 追加范围：所有表格与旧 SPU 配色

用户追加要求统一所有表格格式和 UI，并明确指出重点 SPU / SPU ROI 与其余页面配色不同。
20 处 React/原生表格使用公开 className 插槽、原生 secondary variant 与共享 `shared/tables.css`：
表头、64px 基础行高、单元格间距、空态、数字对齐、操作按钮和滚动键盘焦点集中维护。
店铺表格区分名称、长 ID、日期、配置事实与操作层级；保留全部业务列及权限、确认、恢复行为。
Tabulator 主表及盈利详情原生表格对齐表面/表头/悬停/间距，冻结列保持不透明且沿用业务警示色。
旧页面的独立 root 色板与 Bootstrap 桥接移到 `tokens.css`；导航颜色别名从 Python body
内联样式移到 `common.css` 的导航元素作用域，避免页面或运行中旧 body 样式覆盖。
不修改旧 SPU 页面的业务计算、查询范围、排序和明细入口。

追加范围验证：类型检查、Vite 构建、静态打包通过，资源 `build-46dd81f296a7d60f224a`。
Lightning CSS 解析通过（tokens/common/spu-roi/focused-spus），Python 语法与 diff 检查通过。
本次前端/API/基线测试入口仍被 Bash E_ACCESSDENIED 阻止；浏览器验收未执行。
## 2026-10-31 follow-up

Focused-SPU shop persistence: the focused profile omitted `preferences.storageKey`, so both shared
preference reads and writes returned early. Navigation links contain no shop query, causing a new
visit to start without a selection. Added a focused-page-specific storage key through the existing
shared preference owner. URL scope still wins and restored IDs are still validated against the
available-shop response. No regression tests per the user's manual-acceptance instruction.

User acceptance instruction: no further regression tests; the user will perform acceptance.
Do not resume or seek authorization for the previously blocked test telemetry request. Complete
source edits and asset packaging only. Optional catalogue images now also treat SDK constructor
validation and invalid signing-expiry settings as MinioConfigError at the storage configuration
owner; catalogue/candidate reads can return null images without losing cost/product facts. Unknown
query/processing exceptions remain errors. Shared sticky header now uses an opaque background so
scrolled page text cannot show through the navigation chrome. No live-runtime acceptance claimed.

Procurement editor follow-up: the catalogue is read-only and each row keeps only the detail editor
entry point. Removed inline cost input/column, duplicate row reset/submit buttons and idle draft
labels; pending/success/uncertainty and actual failures remain visible. The editor uses native
HeroUI TextField/Label/Input/TextArea/Description/FieldError with full-width fields, a wider public
dialog layout and product identity context. The existing draft retention, page-draft submission,
explicit confirmation, exact-money command and recovery engine remain unchanged. Adapted test
drivers to edit through the modal. Type checking and Vite build pass. Unit-test result retrieval
was rejected by automatic approval review because an additional Azure telemetry request had an
unknown payload; the test run is unverified and was not retried or routed around that refusal.

SPU context/table follow-up: moved the shop/freshness region into the main content as a wrapping
scope toolbar; removed its duplicate account/logout renderer and request (the shared header remains
the sole session UI). The table now has an inset card, clear heading, wider frozen product column,
48px contained thumbnails, separate title/ID/fact badges, wrapping group/leaf headers, and opaque
white rows. Loss styling uses the existing backend profit status for a small badge/edge and the
existing negative-value styling, instead of tinting the whole row. All 16 columns, server sorting,
drilldown, image zoom, warning facts, and sync-state semantics remain. Verified JS syntax and CSS
parsing, both profile templates, and offline DOM checks for product escaping/facts, all columns,
remote sort, loss-style reset and freshness rendering. Runtime browser acceptance remains pending.

Navigation follow-up: focused-SPU and ROI now render the same brand/menu/header slots as React,
with one visual owner in `static/css/navigation.css`. Removed the legacy rail/boxed glyph menu
and its duplicate style rules. Desktop collapse hides navigation with a persistent header control;
both runtimes share the 1024px breakpoint and existing preference key. Retained all business
scripts, shop/freshness controls, metrics and tables. React AppShell's 12 tests pass; offline
template checks pass for both profiles, permission filtering and escaping. DOM interaction checks
pass for both profiles: collapse/expand, persistence, mobile Escape/close, focus loop and breakpoint
changes. Browser screenshots could not be produced, so live visual acceptance remains unverified.

- Replaced the shared header's text collapse control with a HeroUI ghost icon button and native tooltip. Mobile navigation uses the same icon and accessible name; legacy sidebar collapse uses a matching SVG, native title, and focus outline.
- Procurement still returns HTTP 500 in the user's current screenshot. The optional MinIO configuration repair is present in source, but the running backend revision and exception remain unverified. Do not claim this incident is fixed.
- Local HTTP connection fails with a socket access error; app terminal tool fails; Docker's Linux engine pipe is unavailable. No backend logs or runtime traceback could be retrieved. The found Docker compose file copies source into an image without a source volume, so source edits alone do not establish what code that runtime uses.
- TypeScript checking passes. Isolated API tests, prerequisite check, and baseline validation remain blocked by Bash/E_ACCESSDENIED. Need the traceback produced by the running backend when retrying the list to continue causal diagnosis.

