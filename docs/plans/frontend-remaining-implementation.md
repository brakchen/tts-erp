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

- manual 内容 a94fced 的 37 项恢复/精度用例保持。已知 server total 的分页缺陷经 6 个真实组件 RED 后修复，新增 8 项含整页末页、短页但仍有总数、大整数及无总数 fallback；分页选择 45 项通过。15 个旧 SSR 断言明确重绑定到仍保留的 legacy artifact，另增 19 项真实 React 筛选/排序/翻页/确认/lightbox/精度行为，原 27 个测试名保持且其余 12 个函数 byte-exact；API/资产 27 项及组件选择 56 项通过。两条并行切片经 SPEC 和 fresh QUALITY 后释放、合入父候选，仍需组合验证。
- 先前登录/其他页面的 9 个旧 SSR 兼容失败已按新文档协议适配，两个文件 33 项通过；其余 4 个 SPU 函数和 4 个非展示 auth 函数与 a452 基线逐函数 byte-exact。没有以旧告警规则或旧 CSS 恢复新产品行为。
- 视频完整内容 99a43ce（83 项组件选择，独立 SPEC/QUALITY 闭环）已按目录 tree hash 原样合入；4 个入口行为 RED 后经既有 loader、renderer 和原 handler 接线，23 项针对入口/路由的 wrapper 通过。唯一 registry 现为 14 个源码候选，两个 ROI 仍排除；这不是生产激活。
- 用户已明确批准并完成单一受限测试接点：默认 factory 不变，显式 `collaborators=True` 仅选择固定三协作者替身；保留原 DB/auth/proof/nonce/runner/conftest 边界。来源精确核对 ASGI 实际监听地址，上传拒绝 Cookie/Authorization/X-API-Key/ERP CSRF。82 项新旧 guard/cold-clone 选择、源码与精确合并基线均通过，master `0f6a4a573770bbe2802818bf35f57ae23d663319` 已非 force 推送并由父候选消费。用法与未证明的范围见 [受限测试接点](../testing/frontend-test-collaborators.md)；替身成功不证明真实同步、MinIO 或设备发布完成。
- 合并后的全 `src` 选择首次为 659/660 通过，一项新增采购完整分页故事超过默认 5 秒；按最小可选择文件重跑 45 项通过。随后固定源码候选 `7f2c758` 的完整 fast baseline 中前端 adapter 通过，但全套仍报告 138 项：136 个 browser setup 同类异常来自受保护 `tests/browser/conftest.py::_render_pages` 无 Request 调用已迁移的广告页 handler，另 2 项是广告/采购旧 HTTP HTML 断言。不是 136 个 ROI 产品回归，不删除这些失败、不改两个排除页或用生产 legacy fallback 掩盖。后续两条旧 HTTP 断言已按现存 artifact / 活跃 React 契约分层适配，37 项两文件验证、SPEC/fresh QUALITY 通过；曾出现 8 个非目标格式化差异，来源未确证，已恢复 HEAD 字节并由父级独证其余 13+24 个函数不变。新真实隔离 API 验证覆盖 root 和 `/tts` 的登录/角色/CSRF、成本精度/历史/拒绝快照、禁用拦截规则持久化及同步 admission：SPEC 缺失的 readonly 有效目标 SQL 不变证明已补齐，8 项通过并独立复审。合入父候选后新旧护栏/API/两文件组合 127 项通过；未重跑整套去宣称其余 browser GREEN。用户随后已明确批准仅 `_render_pages`、导航到新采购页的展示测试和按实际文档选择字体的展示测试三处适配。独立 writer 负责最窄接线 RED→修复与控制回归，另一独立 worktree 并行验证原 P2 真实浏览器链路；其他 conftest 函数、mock 数据、fence、DB/auth/proof/runner 和两 ROI 实现仍冻结。尚未完成整套 GREEN。真实 clone/API、native、三宽度双前缀视觉和精确交付继续在 board 独立记账。native 不再以“尚未批准接点”为阻塞理由，实际完整浏览器/cookie/CORS/视觉/运行验收仍未完成，不能用 HTTP client 冷启验证替代。

## 视频诊断的公开边界

- 诊断仍是显式授权读取，默认任务读取不附带 prompt/Artemis 输出。`promptSnapshot` 保持可空文本 wire 字段，生产者 `_attempt` 复用既有 `publishing.diagnostics.sanitize_text`：沿用凭证赋值/认证头/URL query 的脱敏规则和最多 2000 字符边界，不增加客户端 redactor 或另一解析引擎。
- 原始完整 `prompt_snapshot` 审计事实不修改、不删除；Artemis 输出继续沿用既有结构化脱敏器。新 UI 必须明确标为“脱敏诊断节选”，不能把有限公开节选声称为完整原始 prompt，也不能把其值用于发布/重试资格判断。

## 2026-10-25 验证检查点：仍不是整体验收或生产交付

- 三处已授权 browser 函数已独立修复并采用（`86dd6b6` → `737e550`）：只改三个函数体，19 行新增、11 行删除，其余字节/AST 保持；61 个 browser 控制通过，SPEC 与 fresh QUALITY 通过。原 P2 真实 native 文件另一次 6 项通过。三函数授权和受限协作者接点不再是待批事项。
- 后续父候选完整 baseline 的工具在 900 秒被中断，没有正式 verdict，不能记作 GREEN。找回该轮 pytest 摘要的 6 个旧作业失败；已证明新增真实 API 测试提交的活跃 TEST 店铺跨测试存留导致代表作业时区解析失败：作业独立 1 GREEN、原 API 8 项后同作业 8 GREEN/1 RED。修复唯一新测试文件的自有事实生命周期后，同配对 9 GREEN、完整旧作业文件 11 GREEN，经 SPEC/fresh QUALITY，原 23 个人工成本断言同序不变；`b527539` 已在父候选采用为 `a279c2b`。没有改作业、财务、时区政策，或把事实从一开始改为 inactive。
- 中断留下的唯一自有旧 clone 已核原 proof、相同 connection digest、owner 已退出与零会话，再用**现有公共隔离 runner** 的显式 run 名先重建新 clone，仅收集 8 项而不执行测试体，最后正常清理。独立元数据确认该 run 不存在、私有模板仍为 `0070_monitor_health`；旧过期 proof 精确移除。没有手工 DROP、修改原 helper 或把旧库作为测试 fallback。
- 登录 native 子集 `d99aa12` → `57b00c4` 已采用：root 与 `/tts` 的 375/1440/2560 六个键盘登录/登出场景、两个 1440 未知结果恢复场景，共 8 项通过。六张输入凭证前的空登录截图已逐张打开。五处 URL 断言改为等价布尔断言，避免该断言通道打印 URL 原值，SPEC 闭环与 fresh QUALITY 通过；不宣称 Playwright 全通道脱敏或全部页面视觉验收。
- 新增配置/用户真实 API 的初始 4 RED 与最小两个 root RED 确认缺少 cookie CSRF：有效 cookie 缺少 XHR 头时发生真实 200/201 写入及 SQL 变化。保留全部严格失败断言，未放宽成功状态。父级仅在原持久化 owner 调用既有 `require_cookie_csrf`：9 个 runtime mutation、7 个 users owner（共享 `_revoke` 覆盖两条路由），即 16 次调用保护 17 条写路由。角色检查、签名、服务、认证协议、middleware 和 guard 实现不变，纯 schema preview/read 不新增写 guard；API-key/bypass 保持原行为。
- guard 的 39 个直接入口检查及 12 个原 cookie 控制共 51 项通过，原 runtime/user-auth 两文件另 39 项通过。新配置/用户 4 个 root/`/tts` 真实场景在修复后全部通过：版本/发布/历史/409、readonly/CSRF SQL 不变、实际密码登录/重置/旧会话吊销/停用均有独立 SQL 证据；原登录限流未改。数值证据限于大整数和支持的精确二进制小数，不声称任意财务 JSON 小数精度。SPEC 与 fresh QUALITY 已通过；固定候选完整 baseline 的实际结果见下条，不能称作 GREEN。源码/Node/API 成功不等于 JSON 编辑器 native、媒体/CORS 或生产运行验收。
- 固定候选 `81840fe` 的单轮完整 baseline 正常结束为 exit 1：唯一 pytest 失败为前端 adapter，嵌套采购 full-final-all 用例超过 5000ms（5473ms），659 个 Node 用例通过；先前六个作业失败未再出现。run clone 独立确认不存在、0070 模板未变、966 个 tracked regular files 字节未变。原源码按 Vitest 既有 file:line85 经官方 wrapper 最窄一次隔离通过，只支持“整套下预算敏感的 harness timeout”，不宣称已 profile 证明唯一 CPU 原因或产品分页回归。随后仅在该 callback 把七个前/后页角色查询限定到既有标记“分页”的新鲜容器，复用 `within`，不缓存节点，不改 50 行数据、完整交互/断言、测试名或 5 秒上限；反向替换可逐字节还原整个原文件。改后整个采购组件文件选择通过，两个相关 clone 均独立不存在。未重复整套把旧 RED 改写为 GREEN；该窄加固已通过独立 SPEC、fresh QUALITY 和主动 LSP 检查；后续最终候选/精确交付验证仍未完成。
- **测试工具扩展已获独立授权**：用户随后明确“允许调整测试工具，继续验收”，并要求“多agent一起测试”。允许在同一 canonical browser fence 扩展精确 TEST 页面/API/经验证 task-ticket 目标及负控，并在既有固定受控上传协作者增加 owned-origin 元数据和有界 progress/cancel 观察；不授权任意 URL/loopback、第二白名单/权限/provenance/transport owner，也不改 DB/auth/proof/nonce/runner、生产协作者或 ROI。不得继续把这项授权当阻塞，也不得扩大成生产发布授权。

## 已授权测试接点与并行验收执行切片

- 基点 `ff91d53` 已同步最新 `origin/master=eb71d63` 为 `a75618d`；主线两提交仅修改探活 domain、对应 unit 和文档，没有 schema/migration delta。私有模板仍须逐次验证精确现存 `0070_monitor_health`，不能刷新、stamp、迁移或共享库 fallback。
- `TDD Mode: auto; TDD Route: strict`（记录的 auto 决定：共享测试安全边界/producer-consumer 合同）。单 writer 先产出原 guard/已验证 child/固定 store 的小接口契约，父级读回后以有效允许目标与非目标、错误前缀/来源、过期 task/ticket、重定向、ERP 凭证及二进制/取消负控验证；原 guard/cold-clone 路径必须保持。新增测试的拒绝或接点未实现仅是 harness RED，不能叫产品 RED。
- 并行组分别准备采购/广告/盈利对比、用户/配置/拦截、同步/视频的真实场景与 TEST 数据。共享接点契约冻结和双审通过后，各组在独立工作树、独立 ephemeral clone 同时跑 native；不并发改共享工具，不重新发明 fixture、会话、连接或角色 owner。沿用原登录限流预算及 exact-owned 事实收尾，确保失败测试也不污染后续用例。
- 每页至少 root 与 `/tts`、375/1440/2560，真实键盘/焦点/数据/恢复与安全 TEST 截图；父级须实际打开图。其余八业务页 native、完整视觉、媒体/CORS、最终候选/精确 master/runtime 交付与非 ROI legacy 退役仍未完成，不能用工具接点或单个 agent 的 GREEN 替代。

## 2026-10-25 多组真实 API 验证与接点 N1 决策

- 用户要求多 agent 一起测试后，接点保持一位 writer；另外三组在独立工作树补真实 API 场景，不绕过原 child/probe/nonce/会话/SQL 权威。采购新增查询覆盖在首次整文件中为 8 GREEN/2 RED；最小 root 复现去掉登出清理的异常遮蔽后，观察到真实 HTTP 500。根因的精确组合由循环顺序和源码静态推导，而非服务端 traceback：schema 允许 `id` 与 `desc`，原九项 SQL tail 缺 `id-desc`。父级仅增加 `ORDER BY cp.id DESC` 一项，反向去掉该行可逐字节还原整个原模块；默认排序、过滤、金额公式和查询范围不变。
- 修复后父级一次官方 wrapper 执行新增采购 10 项与既有 commerce 39 项，终端 49 个通过标记、exit 0；所有五排序键的双方向参数及分页首次完整执行；其中 `id`/`status` 有可区分的双向数据，而时间字段全为 null、成本仅一个有价行，不能据此声称已充分区分时间/成本的双向排序。这是明确残余覆盖，不是失败。对应 clone `80e8c29e` 正常清理且独立 catalog 不存在，模板仍精确 `0070_monitor_health`。原模块 Ruff 10 个既有 finding 改前/后相同、新增零；新脚本 Ruff 零。独立 SPEC 与 fresh QUALITY 均无 P0/P1；两份报告保留非阻塞诊断/覆盖残余，不冒充完整排序方向或 UI 验收。既有机器合同的 `sort`/`order` 及默认值已补入对外 API 表；后续整体 baseline 仍待完成。
- 角色/会话与视频组曾分别报告 4 和 12 项通过；独立复核发现自有事实清理在创建失败或返回 ID 尚未捕获时不完整，不能据此完成采用。各作者现仅在自己的新增脚本补真实 HTTP 已提交副作用与预期失败的 harness 回归，再修 exact-owned 收尾；不 mock 原权限、nonce、proof 或 `owned()`，不把这类 harness RED 叫作产品 CSRF 故障。角色实际登录为每 prefix 5 次，低于既有 10/child 限流及 9 次测试预算，已明确修正先前 4 次的记录；不改认证或限流。角色修复后同一未捕获 user ID 的真实提交路径先产生 harness RED，再整文件 6 GREEN，最终 690 行 `4d3ba2c1…`，SPEC 已闭环、fresh QUALITY 待完成。视频同类路径先 RED、作者旧字节 14 GREEN；父级另外在最终 696 行 `33c62d12…` 上实际重验 14 GREEN，执行前后字节不变、clone `8423d31e` 独立不存在，仍待 SPEC 闭环与 fresh QUALITY。曾有 agent 在 wrapper 外查询模板内账号/角色的 marker 行数，属于未获准的非元数据读取；这些计数已撤出验收证据，后续模板只核身份/schema，不据“零行”证明自有事实清理。
- 三个新增 API 脚本交接后出现纯格式变化，生产者未证实。采购作者 549 行与父级 581 行 AST 同一，父级 49 项执行前/后均为 `07f6dbc4…`；角色作者 549 行与复核 559 行 AST 同一；视频复核实际为 620 行，而非旧报告 591 行。不把旧测试计数绑定到新字节。为避免压行，父级仅将这三份新增 API 测试的自定行数上限调整为 700 行，最终修复须记录实际源码、执行前后 hash、独立副本和新的验证结果；这不扩大文件或业务权限。视频后续 695→696 已由原始工具记录重建证明为一条物理行拆分、AST 同一；写入紧随 agent 最终消息之后，格式 hook 是高置信解释，但没有直接捕获写进程身份，不把它泛化为所有历史漂移的确定归因。
- 新 browser fence 扩展曾超时，并留下尚未交付的三处原支持修改与三份新增文件。父级已核对原 0600 receipt/proof、nonce/runId/connection digest、PID/start 与原父进程死亡，用 pidfd 仅终止自有 TEST child，无升级或生产进程信号。私有模板现存/head 与旧库零会话前置核对后，仍通过原公共 runner 的显式旧 run 名重新建 clone，仅收集原 8 项、不执行测试体，再正常清理；catalog 独立确认旧 `0eb0aef8` 不存在。过期 proof 没有被重新用作测试权威，旧日志仍不能称作 GREEN。
- 独立安全复核发现新扩展的 profile 字段可伪装、跨目标关系不足、named 目标及学习/负控不完整；问题限于未部署测试 WIP。父级决定 **N1 接点先仅支持采购与视频两页的精确闭集**，明确拒绝尚未验证的其余六页分支，后续 N2 单独补齐；不删除候选业务页面，也不缩减八页最终验收目标。先以真实默认 child 的伪装 profile、真实跨店 tuple 等负控 RED 修唯一 canonical fence，再验证原 P2/default/nonce/foreign/task-ticket 回归，经 SPEC 闭环与 fresh QUALITY 后才交给页面组。N1 真实默认 child 字段伪装与实际跨店 tuple 两项先产生 guard RED，修复后新旧控制 115 GREEN；交接后的两份新文件再次仅格式变化，父级证明全部 Python AST 同一，并在最终 helper 195 行与 cold test 523 行等八份当前文件上另一次实际验证 115 GREEN，前后八份 hash 不变，clone `29b5d30a` 及相关旧 RED/GREEN clone 均独立不存在。冷启范围 SPEC 已无 P0/P1，fresh QUALITY 待完成；不是完整双页 UI 或全部八页验收。
- Native 仍未放行。后续可单独批准在同一 fence 后使用真实浏览器 XHR 验证受控上传传输，但它只证明 CORS/进度/实际 socket 完成或 EOF 取消，不能证明视频组件可用、页面 readiness、真实 MP4 媒体或缺失的 UI API 路由。暂停控制须在就绪后启用且有 finally 释放，不能把释放/取消意图计为真实短读。
- 最后一次正常完整候选 baseline 仍是 `81840fe` 的 RED；上述窄 GREEN 不重写它。其余 native、双前缀三宽度逐图检查、最终候选/精确 master 验证、非 force 交付、同版本运行验收与非 ROI legacy 退役仍未完成。此阶段没有生产部署、迁移、服务重启或真实外部发布。

## 后续真实接口组合验证与上传诊断（不代表整体验收）

- 上述“待审/未放行”是各历史检查点的状态。角色最终 690 行 `4d3ba2c1…` 的 SPEC 和 fresh QUALITY 均已通过，6 个真实场景的检查点 `332e397` 已采用为父候选 `b05de03`。视频 696 行 `33c62d12…` 的最终 SPEC/fresh QUALITY 亦已通过；随后在采用前单独关闭失败断言的响应泄漏通道。
- 视频既有 `_flag` 仅返回布尔值仍可能被 pytest 重写器解释调用参数。纯测试的非秘密 canary 用例真实复现该通道后，唯一既有 helper 改为在内部以固定信息失败，再返回 True；14 个业务判断仅作等价包裹，原三个 helper 调用也受保护，不增加 redactor、解析器或权限 owner。最终 API 698 行 `315cdf10…` 与新隐私单元 67 行 `c0185bdb…` 的组合 19 项通过（14 API + 5 单元），SPEC 与不同 reviewer 的 fresh QUALITY 均通过。官方 runner 不启用局部变量输出；不据此保证手工 `--showlocals`/pdb 的日志安全。`8b12249` 经精确 cherry-pick 后父候选为 `bc3f545`，两个分支均非 force 推送。
- 隐私作者实际执行了三次 wrapper，而不是获准的两次：首轮两个用例把 canary 放在值中却测键，发生 `DID NOT RAISE`，不作为隐私证明；未经先报告便修正测试并额外重跑得到有效 echo RED，最后组合 19 GREEN。额外的是第二次单元调用，不是另有第四次执行。违规和首轮错误均保留，不追溯授权，不称产品权限故障；原 proof/auth/fixture/事实收尾没有被 mock 或替换。
- 父级在实际 `bc3f545` 上通过一次官方隔离 wrapper 组合采购查询、角色/会话、视频、隐私单元、配置/用户、原余页 API 与旧 commerce 控制七个文件：JUnit **86 项，0 failure / 0 error / 0 skip**，exit 0。执行前后 **970 个 tracked regular files 字节均不变**；自有 run clone 独立 catalog 确认不存在，私有模板身份/head 仍为 `0070_monitor_health`。这是组合 API/控制证据，不是完整 fast baseline、八页 native 或生产运行验收。
- N1 冷启 SPEC 和 fresh QUALITY 已闭环；但实际传输未通过。经单独有界批准，原危险观察器已替换为同一 fence 后的真实 TEST 登录、真实 ERP task/ticket、原 heartbeat/intent 清理、原生 XHR 与接收端观察。首轮执行 `f3ae8f88…` 失败且原 sanitizer 丢失首因；其后格式变化的 `e76447be…` 经 AST 同一证明，不能把旧执行绑定到新字节。一次有界反事实仅移除观察器自身的异步 `all_headers`，原 canonical 全头/来源/凭据/SQL/ticket/continue 校验与 15 个验收判断不变；实际 `302ceb4e…` 再次失败，明确停在接收端暂停的 12 秒观察期限，finally 释放成功。
- 两轮浏览器均观察到 PUT 发起，但接收端 OPTIONS/PUT/字节均为零，第二轮另有 requestfailed 事件；这不能证明头缺失、无 ERP 凭据、成功 CORS 或 canonical 拒绝。移除观察器异步读头不足以解释持续失败，实际网络/拦截原因仍待分类；没有自动第三轮、abort/matrix、放宽头校验、增加 origin、缓冲二进制或提高超时。失败源码已在独立分支 `2dc8176` 提交推送为诊断检查点，未采用到父候选。下一步先复核公开协议和最小被动观测方案，再单独决定是否执行；不是恢复旧四例盲跑。
- 完整候选 baseline 仍保留 `81840fe` 的正常 RED。盈利/广告的真实读取覆盖、N2 六域、八页实际交互/有效媒体/双前缀三宽度逐图检查、精确 master/运行交付和非 ROI legacy 退役仍未完成；无生产迁移、重启、外部作业或发布。
