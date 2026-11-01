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
