# 剩余控制台页面迁移实施计划

## 已授权目标与边界

2026-10-24 用户明确：SPU ROI (`spu-roi`) 和重点关注 ROI (`focused-spus`) 暂不改，其他页面全部改造，并使用多个 sub-agent。沿用已批准的 React + TypeScript + Vite + 免费 HeroUI MPA 方案，不重复请求架构或实施审批。

完成的是可操作的页面，而不是新目录、DTO、空表、构建或测试数量。保留原 URL、权限、信息、操作、确认、查询/排序/分页、输入及失败恢复。登录展示纳入收尾；不改变认证协议。两个被排除页面及其共享盈利内核、日期修复 lane、既有依赖资源均不变。

实时状态、文件归属和证据只在 canonical tower-do；本文是耐久实施/验收契约，不维护第二份活动登记。

## 开始快照与实现必要性

- `TaskStartSnapshot`：`origin/master = 4bcef08df77172b6cb48007267d3927e4078ca19`。
- 现有五个 React 页面源码已在 master；dashboard 截图缺陷已另行修复，运行静态包已发布。P2 全部页面的后端运行版本仍不能仅凭静态包推断。
- 主工作树存在外部 `plugins/monitor`、logistics/scheduler 源码和测试 WIP，以及 root `node_modules/`；不修改、暂存或 stash。
- `Change Necessity`：剩余页面仍是旧展示 owner，文档或配置不能完成迁移；最小变更是各页私有模块及唯一共享 renderer 接线。
- `ArchitectureReviewRequired`：跨模块和安全接线需要审查，但采用既有已批准架构，不另建 shell、session/client、权限矩阵、金融公式、表格引擎或发布系统。
- 实现为 multi-seam：各页面可独立交付内容模块；共享依赖、安全接线和最终整体验收由 parent 单独负责。并行 writer 必须使用不同 worktree，禁止并发修改共享文件。

## TDD 与共同契约

`TDD Mode: auto; TDD Route: strict`。权限、DTO/查询、读写、状态和恢复具有真实回归风险；先表达有效行为并观察 RED，再最小实现。模块不存在、构建失败或环境故障不能冒充行为 RED。

- 复用 `PageProps`、共享 `requestJson`、路径工具、`ApiError`、`scheduleReadDelay` 和既有命令错误分类；不复制网络/会话基础设施。
- 后端持有权限、财务精度、聚合、排序/筛选范围和业务判断。保留实际 wire 拼写，包括合法 camelCase，长 ID 保持字符串，null/0 不混淆。
- 写操作显式、single-flight，不自动重放。未知结果保留草稿、阻断普通重提，提供可用显式读取和用户明确重试决定；GET 不能证明原提交、密码或秘密值已生效。
- Retry-After 全时长跨刷新/核查保留，过期只允许显式操作；401 共享处理，403 独立。提交快照字段错误在真实编辑时作废，但不能清掉未知恢复/冷却/权限状态。
- 公有 HeroUI/React Aria API，默认主题和少量布局，复用一个 AppShell；表格可局部滚动但不丢字段。避免 Card 内容槽默认列布局造成假响应式。
- 结构化配置需保留 code/tree/view、无效文本和大整数。先复用现有/维护中的 JSON 工具；依赖和 lossless 响应 seam 由 parent 一处处理，不能自造 parser/编辑器或悄悄舍入。
- 秘密不预填、不存储、不日志、不输出原始错误/载荷，不采集客户截图、cookie、trace。

## 独立内容任务

| 内容 seam | 独占写域 | 有效用户任务 |
| --- | --- | --- |
| 同步任务 | `frontend/src/pages/sync-jobs/**` | 状态、任务、店铺、admin 启停、RW 显式触发；不把排队当完成，不把上游触发作为测试 |
| 广告日明细 | `frontend/src/pages/ad-daily/**` | 原筛选/URL/日期、外部 seller ID、USD decimal、九种远程排序、分页、全筛选 server summary、详情和复制 |
| 用户管理 | `frontend/src/pages/users/**` | 用户/角色/权限目录、密码重置、会话吊销、内置角色和最后管理员约束、确认与恢复 |
| 运行配置 | `frontend/src/pages/runtime-configs/**` | 列表/详情/结构化草稿/schema 预览、expectedDraftVersion、发布/历史/回滚、退休/恢复、秘密和 snapshot 304 |
| 采购工作台 | `frontend/src/pages/manual-costs/**` | 原精确成本与历史追加、仅 dirty 项顺序提交、非原子批次与未知结果停后续；shop+SPU 身份歧义先由后端 owner 修正 |
| 拦截配置 | `frontend/src/pages/intercept-configs/**` | 原 RW 读写、规则与结构化校验、必要确认及错误输入保留 |
| 视频发布 | `frontend/src/pages/video-publish/**` | 原上传、状态机、确认、取消及恢复；不新增店铺写 API，不实际生产发布/请求上游 |
| SPU 盈利变化 | `frontend/src/pages/spu-profit-deterioration/**` | 仅已确认 7/3 天对比展示；另会话持有业务/后端/legacy 修复，先交换有效契约，不恢复旧阈值告警 |
| 登录展示 | `frontend/src/pages/login/**` | 保原登录/next/open redirect/限流/cookie/会话协议，不新建鉴权状态真相 |

第一并行组是同步任务、广告日明细、用户管理、运行配置；其他 seam 随资源释放接续。SPU 盈利变化的业务契约交接不阻塞其他页面。没有任何任务授权改两 ROI 页面。

子任务的内容/Node 验证是组件 handoff，不是全页完成。每个 writer 先一次报告实际原生 session UUID，由 parent 依据当前 run metadata 注册 reservation 后写入；不猜测继承的 PI_SESSION_ID。parent 独占 Git 提交、推送、分支和集成；child 不创建子 agent。

## Parent 单一集成职责

1. 维护现有 page-loader、bootstrap/共享传输及唯一 `MIGRATED_PAGE_IDS`，不增加第二激活表或 missing-asset fallback；每页保持原 handler/页面授权。
2. 各内容 writer 只写自己的新目录。parent 在其内容 handoff 冻结/释放后消费提交，不并发修改 child 文件。
3. 审计实际命令入口的 cookie CSRF、原服务端权限和必要破坏 guard；最小修补，权限不扩大。不得改 middleware 顺序、schema/migration、金融计算或并行 owner 路径。
4. 新 native/support 测试使用独立文件，复用 P2 当前 wrapper proof、实际 Engine/Session、owned Popen liveness、nonce/attestation、canary、TEST 账户与 request fence；不重写 DSN/provenance/lifecycle owner。
5. sync/视频等外部动作以隔离可证 no-op/受控替身验证入口，禁止借浏览器测试执行真实上游任务。
6. 用户/运行配置等契约缺口先核 canonical API/legacy behavior；普通可逆实现细节由 owner 决定，不反复请求已满足的授权。新财务/权限政策或未获生产操作授权才上报。

## 验证与交付

- 子任务最窄命令：`FRONTEND_TEST_FILTER=src/pages/<page> bash scripts/test_isolated.sh fast tests/frontend/test_components.py`。所有测试仅 wrapper；无直接 Vitest/pytest/npm test、共享 DB、生产端口 mutation 或 guard 绕过。
- writer 保存真实行为 RED、最终窄 GREEN、实际 changedFiles、缺口和日志。先 fresh spec review，再 fresh quality review；有具体缺陷回原 writer 修，不能自审代替。
- 集成后用本轮 ephemeral clone 验证真实角色/API/持久化与独立读取、401/403/422/409/429/5xx/断线未知结果、不重放和恢复，不混淆 wire 注入与真实服务错误。
- 当前编译产物至少 375/1440/2560，root 和 `/tts`；人工打开图片，核导航触达/数字层级/列对齐/长名称和 ID/状态密度、键盘/弹窗焦点、空/错误与真实数据。保存未查看的图不算验收。
- source、compiled/clone 验收、运行服务实际版本和已验证操作分别陈述；静态 hash 通过不证明 Python 后端更新或所有客户流程可用。
- parent 同步最新 master，固定候选通过窄测试及 `bash scripts/test_baseline.sh`，提交推送；精确 prospective master 在锁内验证再非 force push，按 board ready FIFO 交付。
- 不设计灰度、流量分批或额外观察等待。只在有效操作授权内启用同版本后端/manifest/assets；不擅自生产数据库操作、迁移或重启。
- 替代已验且确无消费者才清非 ROI 旧实现；仍被两个排除页面使用的 vendor/模板/盈利内核必须保留。保留 Git/同版包应急撤销能力，不引入第二套长期运行 owner。

## 同步命令与公开错误的安全出口

- 同步启停仍要求 admin，手动触发仍要求 readwrite。解析后的 cookie 身份必须通过既有 `require_cookie_csrf`，然后才校验任务/店铺、访问数据库或加入后台任务；同时携带 Bearer 不豁免 cookie。既有 API-key 调用方式不变。
- `/v2/sync/status` 的 `last_error` 保持可空字符串字段，但非空时仅公开固定错误存在提示，不再返回 scheduler 保存的异常正文。上游正文可能含凭证、签名 URL 或业务载荷，不适合作为浏览器/只读 API 的错误详情。
- 此变更不修改存储的 `error_message` 审计事实，也不改变 `last_status`、时间、周期和 severity 的原有业务判定。页面不得把错误存在提示解释为新的调度决策。
