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
- Retry-After 全时长跨刷新/核查保留，过期只允许显式操作；401 共享处理，403 独立。共享 client 收到 403/429 响应头就以同一 `ApiError` 输出状态、request ID 和 Retry-After，取消未读正文，不等待可能停滞且不可信的错误正文。所有非 2xx 的公开 `ApiError.message` 仅包含 HTTP 状态，不拼接上游正文；独立的 `detail` 保持不可信，仅供显式白名单字段映射，不能直接展示。422/409/404 的既有结构化解析及 2xx 解析保持原路径。无响应头或未完成的 2xx 写结果仍不确定。提交快照字段错误在真实编辑时作废，但不能清掉未知恢复/冷却/权限状态。
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
3. 审计实际命令入口的 cookie CSRF、原服务端权限和必要破坏 guard；最小修补，权限不扩大。不得改 middleware 顺序、数据库 schema/migration、金融计算或并行 owner 路径。
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

## 人工成本目标与生效时间契约

- `POST /v2/reporting/manual-costs` 保留既有 `spu_id` / Decimal 成本 / 币种 / note 等字段，新增可选正 bigint `spu_pk`。新页面提交所选行的内部主键和同一行的外部 ID，后端必须同时匹配，不能在主键无效或不匹配时回退到外部 ID 的第一行。
- 旧 external-only 调用在唯一匹配时仍可用；零匹配返回 404，多店铺匹配返回 409，均不得先关闭或新增任何成本事实。外部 SPU ID 的实际唯一键包含店铺，不能把它当作全局唯一键。
- 身份核对后仍复用原每 SPU 锁及单事务 close-old + insert，保留历史，不改金额精度、财务公式或写权限。这里只扩展 Pydantic 请求 DTO，不修改数据库 schema。
- 未传 `valid_from` 时使用 aware UTC 的实际当前时刻；不能把宿主机的 naive 本地墙钟作为 UTC 存储，导致成本在未来数小时才生效。显式传入时间的既有契约保持不变。

## 匿名登录展示契约

- 仍只有一份 frontend bootstrap、AppShell 与 HTTP client。匿名文档以 `pageId=login` 标识，导航为空、业务写权限提示均为 false；不加入用户页面权限目录，不发起独立会话探测引擎。
- `loginNextPath` 仅用于匿名登录，由既有服务端 `_valid_next` 和 root_path 幂等前缀逻辑给出。共享 parser 复用 `pathFor` 核验同源、无穿越且前缀已存在；缺失/非法目标或匿名文档包含导航/高权限提示时拒绝初始化，不信任浏览器查询串另造 redirect 政策。
- 用户名/密码 JSON、认证、cookie 和会话归属不变。既有限流 body `retry_after_s` 保留，并补同值标准 `Retry-After` 响应头，使唯一 client 的 header-first 429 处理能保留完整等待时间。
- `shared/api/request-lifetime.ts` 是从已验证用户页提取的原生 AbortController/Promise.race 小原语，只限定 30 秒本地 UI 等待并清理计时器，不决定服务端执行、认证状态、自动重放或 Retry-After。未知登录结果仍需显式查询当前会话、明确决定，不凭 GET 断言原密码/提交事实。

## 匿名文档与共享 Shell 边界

- 公共登录文档复用唯一 renderer；即便当前请求携带 admin grant，也只下发空导航、两个 false 业务能力提示和服务端规范化的 `loginNextPath`。缺失目标返回 no-store 503，不输出不可用 bootstrap，也不回退旧登录表单。
- 既有 `_valid_next` 仍是服务端目的地出口，保留合法 next/query，补齐与共享 `pathFor` 一致的控制字符、编码斜杠/反斜杠、dot segment、非法百分号和非法 UTF-8 拒绝；拒绝时沿用既有默认目标，不增加前端重定向策略。
- 无导航时复用同一 AppShell/Header/main，但不显示空侧栏、折叠开关或空抽屉。10 个真实 API 行为 RED 和 3 个 Shell 行为 RED 后，相关 wrapper 76 项（含 Shell 11 个 Node cases）、Ruff/typecheck/build 通过。
- 后续匿名主入口的真实加载失败 RED 后，唯一 main 对 login 使用公共标题、禁用 SessionActions/初始会话探测，既有唯一 401 owner 在公共登录页不跳转，私有页保留原重认证路径。5 个 loader RED 和 12 个实际路由/registry RED 后，users/runtime-configs/intercept-configs/盈利变化/login 已接同一候选 renderer/loader，93 项相关 wrapper GREEN；此时 registry 为 12 个候选（manual/video 仍待审），不是生产激活或最终页面验收。真实 cookie、native、视觉、全 baseline 与精确 master/runtime 仍未完成。

## 错误响应等待栅栏

- 已接收的非 2xx **正数有效 Retry-After** 与 403/429 一样优先作为现有 ApiError 返回；不再等待可能挂起的错误 body，也不等待取消 unread body。这样本地 UI deadline 不会丢失服务器最小等待时间；500/503 仍是未知写入，不据此推断回滚或自动重放。
- 无正数有效等待栅栏时，404/409/422 的原结构化 detail 路径保留，2xx 解码/持久化语义不变。带栅栏时优先提供安全 HTTP status/requestId/完整等待时间，而不是读取不可信 body 字段。此处只扩展唯一 SDK 的既有头边界，不增加 page-local metadata map、解析器、重试/会话引擎或第二套计时策略。
- 14 个真实 SDK body-stall RED 后 shared wrapper GREEN；manual 原 35 项加 2 个实际 UI RED 在 SDK-only cherry-pick 后 37 GREEN。仍需独立复核和整体验收。

## 后续验收接点与未完成事项

- manual 内容 a94fced 的 37 个组件用例和独立 SPEC/QUALITY 已闭环，显式释放后合入父候选。新增 loader/route/registry 的 4 个真实 RED 后接入唯一入口（候选现为 13 个；video 仍待最终质量修复），相关路由与组件通过。`tests/api/test_manual_costs_page_v2.py` 尚有 15 个旧 SSR DOM/资产断言需逐项映射到保留的 legacy artifact 与实际 React 控件，不把这些已识别失败当作全套 GREEN；完整 baseline 未运行。
- 先前登录/其他页面的 9 个旧 SSR 兼容失败已按新文档协议适配，两个文件 33 项通过；其余 4 个 SPU 函数和 4 个非展示 auth 函数与 a452 基线逐函数 byte-exact。没有以旧告警规则或旧 CSS 恢复新产品行为。
- manual 旧契约逐项只读映射另发现已知总数的最后一整页仍可点“下一页”（P2）；需优先用精确 server total 判断耗尽，无 total 的候选保留行数 fallback。status/has-orders/排序/实际翻页/lightbox 等还需补直接组件行为覆盖，不能仅把 15 个失败替换为重复 bootstrap 检查。此修复和 legacy-artifact 标注尚未完成。
- 当前 P2 live launcher 硬编码原 factory，没有 child 内协作者接点。`sync trigger` 会运行真实 background job，视频 store/device 路径可访问真实外部系统；浏览器拦截伪 200 不能证明真实 API 持久化或 cookie wire。完整安全 native 验收需要经批准的受限 consumer factory/setup 扩展，保留原数据库绑定/proof/nonce/runner/conftest；在批准前不绕过冻结护栏、不执行这些副作用。此为验收接点决定，不是重新请求已授权的页面实施批准。
- 已在 board 将真实 clone/API、native、三宽度双前缀视觉拆为独立任务；native 显式记录接点批准阻塞。源代码/构建/API-unit、真实 clone、native、截图、精确 master 与运行激活仍分别记账，不能相互替代。

## 视频诊断的公开边界

- 诊断仍是显式授权读取，默认任务读取不附带 prompt/Artemis 输出。`promptSnapshot` 保持可空文本 wire 字段，生产者 `_attempt` 复用既有 `publishing.diagnostics.sanitize_text`：沿用凭证赋值/认证头/URL query 的脱敏规则和最多 2000 字符边界，不增加客户端 redactor 或另一解析引擎。
- 原始完整 `prompt_snapshot` 审计事实不修改、不删除；Artemis 输出继续沿用既有结构化脱敏器。新 UI 必须明确标为“脱敏诊断节选”，不能把有限公开节选声称为完整原始 prompt，也不能把其值用于发布/重试资格判断。
