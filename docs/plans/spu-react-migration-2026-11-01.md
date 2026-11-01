# SPU ROI 与重点关注页面 React 迁移

日期：2026-11-01。用户本次追加授权两页 React 重构，覆盖此前“暂不改这两页”的范围限制。

## Goal / Architecture / Tech Stack

将 `spu-roi`、`focused-spus` 接入现有 React/TypeScript/Vite/HeroUI MPA，共用一个盈利页面模块和现有 AppShell、PageIntro、API client。React 拥有页面、筛选、请求与展示状态；已有 Tabulator 6.3.1 MIT 专业表格可在独占 ref 容器中维护行列 DOM，不运行旧整页 mount、不使用 iframe 或长期兼容 fallback。布局先放店铺和日期/范围工具条，再放紧凑指标、主表与就近明细。

## Baseline / Authority Refs

- TaskStartSnapshot: origin/master `5eb3ff5c63dd0194cef2f72cbb4c19e0fd636a8e`；独立 `codex/spu-react-layout` 工作树。
- 用户本次要求：全站按钮、输入和表格布局 review；两旧 SPU 页面也用 React 重构。其他页面只报告问题，不顺手修改。
- 根 AGENTS.md；docs/guides/frontend-development.md、agent-testing.md、agent-safety.md；docs/plans/frontend-progressive-migration.md。
- 业务与接口：docs/business/spu-profitability.md、docs/design/spu-profitability-technical-design.md；现有盈利内核与两 profile、tests/browser/test_spu*.py。
- Change Necessity：旧两页仍有独立页面 owner、Bootstrap/TomSelect 外壳，未接入共享 React 页面布局；仅改模板或包裹旧 mount 不满足授权目标。
- ArchitectureReviewRequired：是，复用已批准 MPA 边界，重点审查展示 owner 迁移和旧资源退役；不引入业务规则、API、数据库或发布系统。

## Compatibility Boundary

保留原 URL、权限与 rootPath；金融值、聚合、阈值和排序由后端提供。两页共享行为但各自 profile：标准页 T-1 与临时精确 SPU 范围（最多100，25个分片解析），重点页全历史及服务端持久关注（分页500、PATCH增删与失败恢复）。保留 URL 日期优先于偏好、店铺时区门槛、独立偏好 key、include_all、六价格列/动态能力、全部现存 KPI/投影/费率说明、5种明细、图片预览、日期与店铺切换后的请求取消和缓存失效、分页/服务端排序/新鲜度。

## TDD Route / Verification

TDD mode auto，strict：迁移有日期、跨店铺异步、写入关注和错误恢复回归信号。优先复用既有行为测试，并为 React 接线、profile、查询、旧响应隔离、关注失败恢复和布局补充有区分力的验证。测试只通过 `bash scripts/test_isolated.sh ...`；前置失败先用 `bash scripts/envsetup/install-test-deps.sh --check` 修复，绝不直接跑框架/共享库。

窄测试：frontend + 相关 API renderer / SPU 浏览器行为（按 runner 支持选择）。完成后 `bash scripts/test_baseline.sh` 一次，与已提交 master 基线比较。构建/类型检查独立可运行，但不能替代测试。浏览器验收至少375/1440/2560宽度，真实足量 fixture，检查查询按钮邻近、日期前置、表格身份列、标签/输入关系和明细可达；线上只读 review 与隔离候选验收分开陈述。

## Tasks / Ownership

1. 实现 writer 独占本工作树：梳理并记录可复用上游（官方文档/GitHub/npm，版本/许可证）；将盈利内核的展示功能迁到 `frontend/src/pages/spu-profitability/`，两入口共用组件与 profile，不复制两套实现。
2. 接线现有 page-loader、frontend_pages 和显式页面路由；保留服务端鉴权。新增行为与浏览器验证，不删除仍有效的行为覆盖来换取通过。
3. 退役不再消费的旧两 profile、旧共享页面脚本/模板及专用样式，先查所有消费者；仍被其他页面使用的库/公共资源保留。同步相关技术文档。内部代码退役可逆，不涉及持久数据删除，不需要额外审批。
4. 独立 agent 只读 review 功能清单及 diff；writer修复反馈。父负责环境修复和全站布局报告，与writer不并发编辑本checkout。
5. 通过隔离窄测、fast baseline、diff/秘密/外部WIP检查后显式路径暂存、中文规范提交并push分支。最终整合按仓库锁和精确提交验收流程执行；无生产迁移或服务重启。

## Plan Pressure Test / Retirement

唯一盈利展示 owner 为新的共享 React 模块，专业表格仅拥有其 ref 下内部DOM；旧整页 owner 退出主路径。不新增权限/金融/日期权威来源；日期工具沿用维护中的项目实现。静态资源引用、API URL、五明细和原语义测试逐项交叉核对。环境不足时交付已提交可审查结果与准确缺口，不能声称已验证或已上线。

## 页面结构与复用记录

页面依次为店铺和日期工具条、SPU 范围维护、简明新鲜度、当前关键指标、可展开的其余指标/预测、价格统计、主表与就近明细、分页及具名计算依据。原十余组 KPI 不再挡在日期操作之前；所有字段仍可展开读取。价格采用内部可滚表格，避免四列卡片挤压后裁切金额。

- 复用既有 Tabulator **6.3.1 / MIT**（`static/vendor/NOTICE.md`），上游 <https://github.com/olifolkerd/tabulator>、官方 React ref 集成 <https://www.tabulator.info/docs/6.x/react/>。不新增 `react-tabulator` 竞争包装；表格保留分组价格列、冻结商品列和 remote sort。
- React 的 `query.ts` 拥有展示查询/偏好适配；`Selection.tsx` 拥有显式逐项增删、25 ID 批量校验和关注写入恢复；`useRead.ts` 复用统一客户端、取消/超时与 Retry-After 等待。
- `Summary.tsx` 只读取 totals 和状态，不累加可见行；价格能力仍由实际 nested priceStats 推导。`ProfitabilityTable.tsx` 只管理 ref 内的表格 DOM，不发请求或修改汇总。行内空 host 由表格创建，内部内容由 React portal 独占；`DrillDetails.tsx` 用 HeroUI Tabs 维护五明细，保留懒读/缓存/范围失效与关闭返焦点。
- HeroUI 提供按钮、表单输入、Modal、Tabs；浏览器 checkbox 的单项切换只影响明确目标，避免原生 multiple 的普通点击误移除其他持久关注。手机号/业务 SPU/店铺标识始终作为字符串处理。

初版 `dc444ade` 类型检查及 Vite 构建通过；尚不能由此推断隔离行为测试或线上发布完成。严格 TDD 的 RED 阶段受本地隔离运行前置限制，先写了查询用例而未直接运行框架；后续验证由服务器同版本隔离 runner 执行并记录真实结果。

## 已执行验证（2026-11-01）

服务器独立工作树在 `7c3d271` 的以下两项最小验证均退出 0，隔离 wrapper 完成模板克隆与临时库清理；前端构建产物为 `build-9b4c704321c5b9cddf96`：

```bash
FRONTEND_TEST_FILTER=src/pages/spu-profitability bash scripts/test_isolated.sh frontend tests/frontend/test_components.py
FRONTEND_TEST_FILTER=src/shared/api/client.test.ts bash scripts/test_isolated.sh frontend tests/frontend/test_components.py
```

这是指定 SPU 组件/查询与统一客户端错误 code 测试的通过证据，不代表整个 frontend 域、fast baseline、浏览器或生产验收通过。后续提交仍需匹配版本复验。父协调会话在干净 `origin/master 5eb3ff5c` 对最小 shops 组件和 manual-cost owned-child 404 代表失败做了隔离复现；未改写 master baseline。

## 旧资源退役核对

`b3ce09b1` 测试维护并入后，运行代码、浏览器 fixture 和旧行为测试不再导入 SPU Jinja helper 或读取旧整页脚本。移除 `_SpuProfitabilityPageConfig`、`_render_spu_profitability_page`、旧盈利模板、三份全局脚本与两份专用 CSS。其他旧页面模板、公共 token/导航与 vendor 文件保持原有消费者；Tabulator JS/CSS 仍由新 React ref adapter 复用。

离线 HTML 生成器改用现有 manifest renderer；历史部署脚本仅更新静态资产存在性检查到 release/manifest，迁移、授权和服务操作不执行也不更改。旧 CSS 一次性主题脚本不再枚举已退役两份 CSS。文档中初始设计的旧实现段已明确被本迁移记录取代，不作为恢复旧 owner 的依据。

## 最终验收记录与交付限制（2026-11-01）

代码与测试冻结提交为 `4a2d345a61b34fed089c7d848b08500f48205ec4`，分支为 `codex/spu-react-layout`；已整合的 master 修订为 `ed4da9add9a63aefb5afa6444f39c7e1f2b9b6c8`。下列隔离运行由父协调会话在用户指定服务器的独立测试工作树执行并回传结果，均经唯一入口 `bash scripts/test_isolated.sh ...`；实现 writer 未直接运行测试框架。本地该冻结提交的 `npm --prefix frontend run typecheck` 与 `git diff --check` 通过。

| 提交 | 验证范围 | 结果与覆盖边界 |
| --- | --- | --- |
| `fd3a5b1c` | SPU React 组件/查询窄测 | 退出 0，含混合有效/无效 ID 写入失败后保留未确认提示，以及跨店铺 Retry-After 不可绕过的两项恢复回归；此前跨页移除关注后的 offset 重置回归也已通过 |
| `836a3b66` | `test_spu_react_workspace.py` | 11 项通过，覆盖 375/1440/2560 宽度；隔离候选截图由父协调会话目视检查，控制区前置、表格与就近明细布局符合本轮要求 |
| `ee1841ee` | `test_spu_price_columns.py` | 16 项通过，包含六价格列、远程排序、键盘价格说明及关闭返焦点 |
| `5dcf3c43` | `test_spu_comparison_drilldown_dates.py` | 7 项通过，保留比较页传入钻取的日期范围 |
| `f3acd5c0` | 日期、数值排序与预测浏览器窄测 | 6 项通过；这是指定浏览器范围的证据 |
| `b6976bb4` | `test_spu_deterioration_alert_api.py` 整份 | 118 个参数化 case 通过，包含新增真实 fresh/RW/404 回归，隔离库清理退出 0 |
| `fd3a5b1c` | financial 真实读取整份 | 12 项通过，包含获批的一致快照边界修复 |
| `62c9dad7` | 费率 API 5 项、价格 API 19 项、ROI 参数校验与空值展示契约 2 项 | 共 26 项通过；授权 fixture 补齐后保留原金额、权限与参数断言 |
| `62c9dad7` | `test_spu_price_stats_live.py` 整份真实数据链路 | 9 项通过，保留独立价格 oracle、金额/来源/FX、权限及键盘说明与原生返焦约束 |
| `4a2d345a` | 两项页面登记与完整字体选择范围 | 共 16 项，14 通过、2 失败；两项登记及两 SPU 页字体均通过。失败为 dashboard、ad-daily 使用非 canonical 的 Tailwind `ui-monospace` 字体栈，未放宽白名单或改其 CSS |

字体检查仍以 `static/tokens.css` 的三套规范字体栈为唯一白名单，保留 normalize 与全部可见文字检查。删除的是将 HeroUI 默认变量当作规范字体的错误覆盖逻辑。价格弹窗测试等待真实 visible/hidden 与 FocusScope 返回焦点，保留原键盘及返焦断言，没有用强制 focus 或重复 Escape 替代恢复能力。

本轮独立 review 发现的关注变更后分页复位、失败写入提示保留与跨店铺冷却边界已修复并由相应窄测覆盖。关注写入仍须明确增删，失败后读取事实再恢复；前端未新增金融公式、汇总、阈值或权限判定权威来源。旧整页 owner 已退出，保留的 Tabulator 只维护 ref 内专业表格；本轮以共享模块取代旧页面脚本，没有保留双 owner 的兼容路径。

用户在验收过程中另行明确批准两项最小产品修复：

- 店铺未确认写入后的 verify 事实读回 GET 同时核对内部 id 与外部 shop_id，阻止错误身份读回解除重试锁；未改其他页面布局。
- 利润比较接口将店铺授权读取和原有比较读取放入同一个 canonical 一致只读快照，修复授权 SQL 提前开启默认事务导致的 503；未 rollback/commit 重启事务，未改金额公式或权限 owner。

必要测试维护为已有 TEST 店铺补齐现有授权关系、对齐 React owner 的有效行为断言及 Modal 生命周期等待。ROI 查询预算仍将所有 SQL 计入总数，仅精确识别最多 2 条固定授权查询，其余业务 SQL 保留原 31 条上限；没有放宽业务预算或价格 oracle。

### 完整 baseline 的限制

在 `f3acd5c0` 执行了一次 `bash scripts/test_baseline.sh`，退出 1，相对于已提交的零失败基线报告 **313 个机械差异测试 ID**。其中含未同步的旧授权 fixture、原生页面文案和旧 schema 预期；后续窄测已修复并验证其中部分差异。313 既不是当前冻结 HEAD 的重新执行结果，也不能全部判定为本次新增回归或全部归为 master 既有失败。其余差异尚未逐项完成归因，未重跑整套来生成新基线，未修改 `docs/testing/baseline-fast.txt`。

父协调会话在干净 master `5eb3ff5c` 最小复现了以下三项相同失败：`tests/api/test_commerce.py::test_list_shops_with_data` 因缺授权结果为空；`tests/api/test_admin_shops.py::test_register_creates_plugin_only_shop` 的 status 为 None 而非 active；`tests/api/test_style_tokens.py::test_every_var_reference_resolves` 的现有令牌检查器报告 `--sidebar-rule` 未声明（`common.css:38` 实际已声明，旧检查器结果待归因）。此前的 shops 组件、manual-cost owned-child 404 与 ROI total 为 0 的代表问题也已在干净该 master 复现。这些是指定失败的既有性证据，不能外推为全部 313 项的归因结论。

当前交付为已提交并推送的可审查分支，**尚不满足 ready/merge 条件，未合并 master、未部署、未重启服务**。目标 SPU 窄测通过不代表全站或完整 fast suite 通过；仍需由协调者保留并分类剩余 baseline 差异，再按仓库整合流程验证拟合并提交。本轮到此停止扩大无关产品与测试修复。

### 后续用户指示：先合并，再排查

上述为原交付时状态。用户随后明确指示“先合并 master，然后排查为什么会有313个失败项”，覆盖此前先通过基线再合并的顺序要求。已在整合锁下用独立工作树创建并推送合并提交 `7335af492dc5039b61f2726d03b2a9f0ead8d9ee`，未部署或重启生产服务。失败记录和基线保持原样，未宣称测试通过；后续归因见 [313项基线差异排查](../testing/baseline-failure-investigation-2026-11-01.md)。

### 用户纠正：恢复搜索框内的关注标签（2026-11-01）

用户明确指出，将已关注 SPU 移到折叠的“当前关注清单”改变了原交互。本次恢复所有已关注 SPU 在同一搜索框内以标签展示，每项保留可直接取消的 ×；输入关键词继续添加，批量粘贴仍在输入旁。两 profile 复用现有 HeroUI 3.2.6 / MIT TagGroup，不再另设折叠的已选清单。商品标题作为标签提示和辅助描述，标识仍按字符串完整展示。

取消沿用原有 `change()` 的显式增删提交，不替换整个关注集合。等待写入回执期间标签保留且移除入口禁用；只读、失败后事实读回、跨店铺冷却与 Retry-After 保护仍由原 handler 负责。本次没有金融、权限、接口或数据库变更。

代码冻结提交 `ce47567b`，分支 `codex/restore-focused-spu-tags`，已合并为 `46e2f3c0`。服务器独立工作树执行：

| 验证 | 结果与边界 |
| --- | --- |
| `FRONTEND_TEST_FILTER=src/pages/spu-profitability bash scripts/test_isolated.sh frontend tests/frontend/test_components.py --tb=short` | 退出 0；含 29 项均在搜索框内、保存中保持标签、确认后仅移除目标、只读禁止取消，以及既有失败恢复测试 |
| `bash scripts/test_isolated.sh browser tests/browser/test_spu_react_workspace.py --tb=short` | 11 项通过；含 1003 项完整分页读取及框内展示、单项 PATCH、写入失败读回和只读保护 |
| 本机 `npm --prefix frontend run typecheck`、`npm --prefix frontend run build` | 均退出 0；现有打包脚本生成 `build-0eca2d17c3e009751ab8` |
| 本机实际页面只读验收 | 当前 29 项全部在框内，29 个 × 与搜索输入同框；2560px 与 375px 无整页横向溢出。未操作真实关注数据 |

本机运行目录已更新并激活上述前端包，刷新页面即可见。独立只读 review 未发现新 P1/P2 问题。

完整基线仅在初版 `dd2f6f78` 执行一次 `bash scripts/test_baseline.sh --keep-log /tmp/codex-spu-tags-baseline-pytest.log`，退出 1，共 **153 个失败 ID**：与前次逐项复验的 149 个剩余失败完全重合，另有本次 4 个差异（组件 adapter 及本浏览器文件的分页、单项增删、失败读回三个 case）。这 4 项的失败详情均为初版标签的可访问名称重复，导致原有精确按钮选择器无法匹配；组件 adapter 内具体失败为 `a mixed bulk lookup keeps an uncertain focused save visible instead of overwriting it with missing IDs`。`ce47567b` 改用原生标签与移除按钮的名称关联后，上述两份专项复验全部通过，未放宽精确按钮查询或原有业务断言。

未重跑整个 fast suite，未更新零失败基线。153 是初版实际运行结果，不能写成最终修正版仍有 153 项失败；149 是本次基线与此前失败清单的交集，不是对 `ce47567b` 的另一次完整执行结果。原始日志、退出码和清单对比保留于测试服务器 `/tmp/codex-spu-tags-baseline*`，既有失败的归因仍见前述报告。专项通过不代表全站测试通过。

### 用户更新：重点页不展示价格（2026-11-01）

从 `origin/master 92bcde80` 开始，`codex/hide-focused-spu-prices` 仅调整共享 React 页的 profile 展示：重点关注页不挂载价格统计块，并向现有表格传递关闭价格列的展示参数。SPU ROI 页继续展示价格统计与六列价格，后端价格 API、公式、经营指标和关注维护保持原合同。需求变更同时记录在 [价格设计](../design/spu-price-statistics.md) 原批准范围之前。

产品提交 `c2fb82c0`、宽屏测试维护 `b1546774` 已合并为 `7e5bb7e4`。`bash scripts/test_isolated.sh browser tests/browser/test_spu_price_columns.py tests/browser/test_spu_react_workspace.py --tb=short` 共 27 项中 26 项通过；唯一失败为重点页 2560px 表格必须滚动的旧断言。移除六个价格列后表格可完整放下，保留手机及普通桌面的滚动、整页溢出和完整经营列检查，仅允许重点页超宽屏无需滚动。该参数化 case 在 `b1546774` 经隔离 runner 单项复验通过。价格范围检查使用服务端仍返回完整价格数据的 fixture，确认重点页不显示价格块、六个价格列及其分组标题，SPU ROI 保留价格原值、空值及服务端排序覆盖。

类型检查与构建退出 0，本机已激活 `build-f113c0c55d327bf48d5e`。实际页面只读验收：重点页 29 个关注标签及 29 行数据正常，价格块不存在、可见价格列为 0；SPU ROI 显示价格统计及六列价格。2560px、375px 已目视检查，页面没有横向溢出，未修改真实关注数据。

在 `c2fb82c0` 仅运行一次完整 `bash scripts/test_baseline.sh --keep-log /tmp/codex-spu-hide-prices-baseline-pytest.log`，退出 1，记录 **152 个失败 ID**。其中 149 个与此前剩余清单完全重合；另外 3 个为上述宽屏断言、真实价格浏览器测试仍等待重点页价格块，以及 frontend adapter 内视频发布 `fails closed for task writes when role hint is missing/false 1` 的瞬时空表。

真实价格用例在 `df8f7e32` 经 `bash scripts/test_isolated.sh browser tests/browser/test_spu_price_stats_live.py::test_focused_spus_profile_matches_spu_roi_price_totals --tb=short` 单项复验通过。新预期为重点页不显示价格块/列，而 ROI 页面继续接受原有六个独立 oracle 金额及状态校验；额外用两个页面各自实际查询 URL 和已认证上下文请求真实隔离 API，逐一比较总价格统计及两行全部价格事实，保留同范围价格一致性覆盖。首次维护复验漏导入 `expect` 已补齐，最终复验退出 0。视频发布未修改，使用 `FRONTEND_TEST_FILTER=src/pages/video-publish/index.test.tsx bash scripts/test_isolated.sh frontend tests/frontend/test_components.py --tb=short` 单独复验退出 0，按测试指南归为顺序/负载偶发，未放宽权限断言。

未再次执行整个 fast suite，未修改零失败基线；152 属于 `c2fb82c0` 的完整运行结果，不能写成最终版本仍有 152 个失败。专项复验关闭上述新增差异，不代表全站通过。原始日志、退出码和逐 ID 对比位于测试服务器 `/tmp/codex-spu-hide-prices-*`。
