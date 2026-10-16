# 通用插件状态与 monitor 单目标恢复实施计划

## 当前执行快照（2026-10-16）

第五passive-observation已获独立新门禁ed21c35a，仅observeBinding撤direct prewrite并产原gen/PREtuple/同budget；source85f2d6ab/testac3588c3，OWNER最终15namedGREEN/实际tsc0，私有同tests仅旧cd67四RED，direct-prewrite/gen-after-ensure/latestnonce/expiry-OR四控制选择性杀对应案；全src唯一controller变且AST屏蔽observe后与前版逐字同，其他owner不扩。1f99曾因过期放行暂缓、c4b补test finally，均非最终批准；总预算/其余producer/T4等未过。第四个safe-stop micro已独立门禁330af378通过，rootb060/原ecf terminal，仅controller+test；OWNER冻结11namedGREEN/tsc0，旧7b同最终tests3RED+1coveragePASS、nonce借权控制仅外部nonce案FAIL，独立私有fixture隔离gen控制current1GREEN/gen=true实际旧写致1FAIL，非callers/T4完成。controllercd67b2e6/test0a701211，runtimef969/storagea6/chain2db43458不动。native complete但仍review-required，fresh spec→quality未到。当前rootcf2b19f1-4999-4802-99fb-62da18bcc62d已同协议原7cd resume，仅invalidate-canonical-fact+ensure/observe两负证据callsite原fence/同预算转发及tests，保护原15/其他bodies，先5业务RED/源预审再局部修/新postGREEN，不claimphysicalclear mutation闭环。此前recovery82e1/7cd已complete、7字段/gate/paths/receipt/resumable核；handoff Five named应Four、direct-pre-write实际direct-prewrite，记录errata不改artifact。此前82e1从原d6同协议resume；原11440/d6 runner消失未写result，failed/proof-write-failed，非已证quota/deadline；已保存/tmp/plugin-status-passive-runner-failure-01a1406d现场且一次proc扫描无匹配活writer。275b仅条件pre-edit/source仍cd67，先完成tests-only清理新RED，不回滚四micro，只撤observeBinding直接预写绕过canonical owner并产原fence；严格新RED/预审/独立postGREEN，保护原11及四micro。packet `/tmp/monitor-status-passive-observation-01a1406d.md`；其他producer/union/全90秒与两release/actions/cookie/ERP/T5/浏览器发布未过。数字cap仅OWNER内部scope，不是用户限额，16:52Z MiniMax9%/19%available，不预测停工；恢复script `/tmp/monitor-status-workflow-passive-recover-01a1406d.js` validate0，若native eligibility拒绝报真实原因，不CLI/model fallback。

## Goal

执行 `docs/plugins/plugin-status-report.md` 的 D1–D4 开发与验收。用户于 2026-10-15 明确授权：本会话担任 owner，调度 MiniMax M3.1 Flash Preview worker 开发，由 owner 审核实现与方案一致性，完成前持续推进。方案中的首次服务端放行、独立 status key、失效后离线暂停、有限恢复和 ERP 生命周期通知随此次实施批准。

## Architecture

一个 ERP 状态领域拥有实例、报告事实、状态投影、受限决策及通知 outbox；一个插件 binding controller 拥有绑定生命周期。核心与运行接入分批，先交付稳定契约，再接数据库/HTTP/Chrome。状态报告响应是唯一决策交付路径，不增建长连接、远程代码执行平台或跨插件后台依赖。

## Tech Stack

复用 FastAPI/Pydantic、SQLAlchemy 2/PostgreSQL、现有 access/API key/token_service/sync-worker；插件复用 TypeScript strict、Chrome MV3 alarms、WXT、Vitest 和既有 storage 串行机制。无新增运行依赖计划；必须新增时先报告兼容、维护、许可证与体积依据。

## Baseline / Authority Refs

- 唯一产品/协议基线：`docs/plugins/plugin-status-report.md` @ `fb42e16575a241fec88740d69137ae6a8153c2bb`；本轮用户开发授权覆盖该文档此前的“仅设计/待批准”止点。
- ERP 远端 master 开工代码基线：`c1fe9d787f2a0c20e0b431f31f08675252b5c60d`；登记后组件从 `origin/master` 创建。
- monitor：`51ccab525505d5a497558d46f559357fb1cf4786`；ads 参考：`8d2245e6da1aded1a662ae7e7703abeb105863ea`，只读参考不修改。
- `AGENTS.md`、`docs/guides/{agent-git-workflow,agent-testing,agent-safety}.md` 已在开工前读取；设计调研已读取 `docs/architecture/{architecture-overview,process-architecture}.md`、`docs/api/external-api.md`。各接入 worker 在触及对应层前重读，源码以实际分支为准，不依赖旧语义索引。
- monitor 自身 `AGENTS.md`、README 和相关生产模块/测试是插件约束，尤其严格 TDD、95/90 覆盖门槛、版本同步和现有写队列。
- monitor-config 与广告通知尚未合入 master；不得把它们当已上线依赖，不扩大 monitor_config key 权限。

## TaskStartSnapshot

- Session UUID：`01a1406d-2041-7452-8338-3d7de574337f`；本轮 tower identity：`自动绑定tab 优化`。
- 主工作区 `/home/schan/tts-erp` 保留既有 `M plugins/monitor`、`?? node_modules/`，不暂存、清理或改写。
- 方案工作区 `.worktrees/monitor-auto-bind-status`：branch `docs/monitor-auto-bind-status`，文档 HEAD `fb42e16`；同步 registry-only master 后 HEAD `8a5c7a036881d6123be5517c08ceedfb3b628a57`，源码未变化。
- ERP 组件工作区 `.worktrees/plugin-status-erp`：branch `feature/plugin-status-erp`，从登记提交 `5a7a6c9` 创建；只链接 `.venv`、`.env.test`，不链接生产 `.env`。
- 插件组件工作区 `/home/schan/pi-workspace/monitor-status-01a1406d`：独立 ads-monitor 仓 branch `feature/plugin-status-auto-bind`，从其当前 origin/master `51ccab5` 创建。
- TowerDo：`/home/schan/.pi/tower-do/home-schan-tts-erp-a0cde689/board.jsonl`。父会话维护板，worker 只读取并回报，不直接写板或改 Git 生命周期。
- Registry：新 ERP/plugin lane 已登记；access/security/app/model 注册和插件 storage/background/popup 共享接线已通知现有 owner，未获得范围协调前只交付独立模块或窄接线补丁。

## Compatibility Boundary

- 不扩大单 Seller/Advertiser/Cookie Store 目标；不增加第二 Seller origin、自动建页、自动登录或跨店 fallback。
- 不改变自动关闭阈值、白名单/SKU 保护、close/report 窗口、legacy/v2 解析和逐项复核；完整 fresh close 快照是恢复门禁，不接受展示缓存。
- status 身份/凭证独立于业务配置；不进入导入/导出/force。配置导入和关闭监听取消旧 generation，迟到结果不复活任务。
- 原本健康工作遇到短上报中断不无条件停止；已失效/初始化/上下文不可证明时绝不离线恢复。许可到期限制动作开始，不是健康运行续租。
- 健康且无 incident 的 running 心跳返回 `decision=null`，不得误发默认 hold 中断健康监控；人工 hold/require_user 锁存优先。
- 库中报告/决策是审计事实，不自动清除；生产迁移、重启、真实 TikTok 写请求和真实凭证均不由 agent 执行。

## TDD Route

- Mode: auto
- Decision: strict
- Strict authority: recorded auto decision；插件 AGENTS 另有显式 strict TDD。
- Strict signals: 公共契约、权限、持久事务、生产者/消费者、恢复状态机及真实关停的回归风险。
- Test posture: strict RED test；先覆盖一个可观察行为并记录行为失败，再最小实现、GREEN 和相关回归。
- Reason: 单元测试只能证明内核；真实 service/storage/controller 的边界测试和跨端 golden fixtures 必须另外证明安全闭环。
- Verification: ERP 仅 isolated runner；插件 Vitest 目标、全量、coverage、tsc、build；人工/真实环境证据独立列出。

## Necessity / Owner / Complexity Checks

- Change Necessity: code-change。原有自由文本 plugin logs 没有实例、排序、幂等回执及恢复授权，配置或文档无法实现服务端决策。最低新增 owner 是 `plugin/status` 和 `tab-binding-controller`。
- Existence Check: 复用现有 API key、调度、短请求、写入队列；新协议不得借用 monitor_config、广告 dump 或广告指标作为状态真相。
- ArchitectureReviewRequired: yes。owner 按协议逐项审查，spec 合格后再审质量；实施过程中不静默调整恢复权限或 HTTP 成功含义。
- Architecture Integrity Lens: binding 写入口先收敛，不能同时保留两个 clear/rebind owner；服务端报告/人工控制/巡检必须走同一个实例锁和 service。
- Plan-Time Complexity: background 已约 3000 行、plugin.py 已约 725 行；新职责必须进入独立小模块，旧大文件只薄接线/抽出原责任。独立组件核心、持久化、浏览器接入分别分批，不让小模型拥有全栈所有 outcome。
- Retirement: 纳管实例页面失效提醒迁至 ERP 单一 owner；未纳管旧版本不变。旧手动绑定入口如保留必须调用同一 controller，不留服务端恢复闸旁路。不要顺带修早期无关币种/Cookie问题；若阻碍新安全门禁则报告 owner 定范围并补回归。

## Execution Readiness View

- Intent Lock: 完整实施已批准方案，owner 审查，不以空路由、固定 hold、纯 mock 或局部 GREEN 作为完成。
- Scope Fence: status report、管理开通/读取/人工决策、持久状态/策略/通知、单目标绑定、MV3 生命周期、popup 和部署文档；不触生产执行、不扩多店。
- Baseline Lock: 上述固定提交及实际当前 master；主仓外来 WIP 保留。
- Contract Constraints: JSON camelCase、Python/SQL snake_case、UUID、int64 十进制字符串、32 KiB、严格未知字段、aware UTC；两个端点实现必须与同一 JSON 示例及负面用例一致。
- Task Batches: T1/T2 并行；各完成后 owner 核验再进 T3/T4；两端可运行后 T5 独立集成与逐项审查。
- Review Gates: worker 记录完成证据并请求 owner gate；spec→quality 两阶段，不允许自审替代；问题回原组件 writer 修正并重验。
- Drift/Rewind: 新 owner、依赖、权限、fallback、真实业务写操作或 schema 不兼容先问 owner；实现问题不直接改批准方案以适应代码。
- Evidence: 命令/退出码/目标测试数量/日志路径、改变文件、S01–S14 映射、真实验证与未验证边界；最终 exact-head baseline 和插件完整验证。
- Advisory Boundary: 方法和工作记录不是部署授权或完成判定。

## Implementation Topology / Lane Board

Multi-seam：ERP schema/策略、ERP 持久服务、插件纯协议、Chrome 生命周期是独立可测试契约；不会给单一 worker 全栈委托。

| Lane | cwd / exact decision | Claimed boundary | Authority | Gate / handoff |
| --- | --- | --- | --- | --- |
| T1 ERP core | `.worktrees/plugin-status-erp`；严格解析和纯策略 | `plugin/status/{__init__,contracts,policy}.py`、`tests/unit/test_plugin_status*.py` | 单 writer，读写和 isolated 窄测；不 Git、不共享热点 | 公开类型/函数清单、RED/GREEN、owner spec gate，供 T3 消费 |
| T2 monitor core | `/home/schan/pi-workspace/monitor-status-01a1406d`；校验/HTTP/纯状态 | `src/core/plugin-status*.ts`、`src/extension/plugin-status-client.ts`、`tests/plugin-status*.test.ts` | 单 writer，不碰既有 storage/background；不 Git | 严格协议/队列/账本接口、目标 Vitest/tsc，供 T4 消费 |
| T3 ERP runtime | 同 ERP cwd，T1 交付后；事务与生命周期服务 | `plugin/status/{repository,service,scheduler,notifications}.py`（按职责实需）、新 models/plugin_status.py、api/v2/plugin_status.py、目标 API 测试 | 不触共享接线；先提供窄 patch，由 owner 协调 | DB/api 边界测试与 outbox/巡检，owner gate |
| T4 Chrome runtime | 同插件 cwd，T2 交付后；唯一 controller 接现有调度 | 新 controller/page coordination/storage adapter；共享旧文件协调后接线 | 一 writer；保留现有配置与业务规则 | 真实 controller/storage 的故障注入、全量覆盖/tsc/build，owner gate |
| T5 Integration owner | 两端 handoff 后；只有接线/兼容/跨端验证 | access/security/app/base/sync-worker/迁移/CORS、popup和版本、活契约/证据 | owner 协调文件归属；需改组件发回对应 writer | S01–S14 与 exact-head 验证，提交/推送/安全合并 |

### T1 — ERP 严格契约及纯策略

1. 读取项目 schema/typing 风格和对应 tests；设计 `StatusReport`、`StatusResponse`、判别 scope/能力/决策、受限注册/人工命令类型。对 §6/§7 的每项约束列正负用例。
2. 首个 RED 表达报告有效或未知字段被拒绝，不只靠 import failure；按当前项目接口定义最薄可调用边界后观察预期断言失败。
3. 对 UUID/十进制字符串/时间/boolean 整数混淆/多余字段、monitor 快照完整性、installation 禁止 monitor、action参数/TTL/结果上限逐项 GREEN。
4. 纯策略输入为已核验事实、now、episode 起点、恢复历史/人工锁存；输出 action 或 null。覆盖 disabled 优先、healthy/null、初始化 ready/resume、无候选hold、登录人工、候选恢复、2次传输失败、3次/60秒/10分钟预算、stale不能当登录。
5. 命令：`bash scripts/test_isolated.sh unit tests/unit/test_plugin_status_contracts.py tests/unit/test_plugin_status_policy.py`；不执行 fast 全量。记录 RED/GREEN及公开符号供 T3。

### T2 — 插件严格协议、HTTP 与纯状态核心

1. 读取当前 strict TS、测试 fixture 和 Chrome mock 风格；实现与 T1/方案相同的请求/响应校验，不使用 any 或宽泛 cast。
2. 先 RED：合法回执能匹配、坏 200 不出队、旧 epoch/revision 不授权；再按小行为最小实现。序号用 bigint 算法/字符串序列化，不通过 Number。
3. 纯状态 API 表达持久 seq、不可变报告队列、2MiB/1000条上限、未分配心跳合并、优先发当前报告、决策 accepted/started/终结账本和中断 unknown；实际 Chrome storage 在 T4 适配。
4. HTTP 唯一边界：获准 HTTPS base，10秒 abort、Bearer、omit、redirect error、200严格正文、401/403/409冻结、429 Retry-After两种格式、网络full-jitter/30..300秒；不发 TikTok或Cookie。
5. 命令：`npm ci --include=dev`、`npm test -- tests/plugin-status-contract.test.ts tests/plugin-status-state.test.ts tests/plugin-status-client.test.ts`、`npx tsc --noEmit`。依赖 setup 问题不改代码掩盖。
6. 交付公开类型/函数、可持久 state 结构、RED/GREEN、未覆盖接线。

### T3 — ERP 原子持久化、API、管理、通知

1. 读取 `db/models/security.py`、`api/deps.py`、`access/*`、现有 key创建流程与 scheduler/job，同步模块不得跑在 async handler 的 psycopg 路径。
2. 先 RED/再 GREEN：实例登记（唯一有效shop映射）、最小scope/能力、key只一次明文、正用途鉴权；所有 auth mode 下拒绝匿名/错误purpose。
3. 新增四表及服务：锁实例、event/hash+seq唯一、低seq只归档、receipt与decision冻结而requestId/serverTime每次新、last_seen与观测新鲜分离、首次advertiser空值CAS；完整事务commit后200。
4. 匹配结果、两阶段就绪放行、人工requestId幂等/expectedRevision、人工hold锁存、三次/60秒/十分钟预算。旧结果保留历史但不复活incident；并发报告/人工/巡检同锁。
5. outbox事务内入队、外部发送事务外领取lease；失败有界重试，配置错误待人工，通知dedupe；旧插件未纳管不告警，stale不是login。
6. 提供同步report路由、body尺寸预闸、no-store、安全错误码，admin注册/列表/详情/人工命令和limit/offset/shop_pk/pluginId/connectivity过滤。共享 app/access/security/base/scheduler/CORS 接线先做独立patch交owner，不绕开统一鉴权。
7. 目标命令：`bash scripts/test_isolated.sh unit tests/unit/test_plugin_status*.py`（实际shell明确文件列表），`bash scripts/test_isolated.sh fast tests/api/test_plugin_status_api.py`。迁移测试仅由隔离runner模板/clone执行。不要直接 pytest/alembic。

### T4 — Chrome controller、持久存储与用户操作

1. 单目标desired scope与bound tab分离；扫描已有允许origin页，稳定有效绑定不被active抢占，多候选不同身份不猜店。Cookie Store、Seller探测与Advertiser模板分别验证。
2. 先 controller 边界 RED：tab关闭立刻generation失效并报告；断网不恢复；有效recover才有导航/刷新；ready仍suspended；resume+完整新close快照+原开关才恢复。真实状态机不整体mock。
3. MV3 事件+30秒alarm，启动检查/补alarm、单飞pending、90秒deadline、冷却；storage短串行更新绑定/模板/epoch/报告；长网络不持队列锁。
4. 适配ads `seller-page-coordination`、MAIN身份捕获与页面请求协调，固定来源commit并核对使用授权。刷新、列表、关闭共同遵守租约，协调失败拒绝刷新；不复制跨店继承旧scope的错误分支。
5. 接入现有 `bootstrap`、`clearTabBinding`、手动切换、开关/导入、扫描/关闭队列；旧写入口调用controller，不存在自动/手动双owner。旧generation在途关停结果不变成新上下文成功。
6. popup 展示纳管设置、实例目标、绑定、报告/服务端等待、人工原因与最后确认；状态凭证不导入导出，切换目标需新实例，关监听永远优先。
7. 旧业务门禁全部保留；纳管实例解绑提醒由ERP单一owner。业务提醒不顺带迁移。每个已协调共享文件只窄接线。
8. 命令：目标controller/storage/background/popup Vitest→`npm test`→`npm run test:coverage`→`npx tsc --noEmit`→`npm run build`。满足95% statements/functions/lines、90% branches，不降低门槛/排除新模块。

### T5 — 兼容接线、一致性审查与完成证据

1. owner在组件 handoff 完成、没有活动writer时检查源代码和证据，spec审查通过后再quality审查；新问题发回原worker，新验证覆盖修正blast radius。
2. 共享热点经归属协调后最小接线；迁移编号依据实际Alembic head；不改middleware顺序，不新增生产破坏入口。status key仅精确report路由，普通key和monitor_config key均不能恢复。CORS使用实际配置origin与获准HTTPS host，不能泛化`*`携凭证。
3. 同一golden请求/响应和负面变体在Python/TS两侧保持一致；验证实际HTTP/service和插件client/controller，不将两个端整体mock成自证。
4. 浏览器证据分层：mocked fault injection、受控Chrome MV3/HTTPS/CORS、真实TikTok/Feishu。可运行受控本地设施，不向真实店铺发写请求。没有证据的S13子项标needs-verification，而非通过。
5. 目标测试及 `bash scripts/test_baseline.sh --keep-log /tmp/plugin-status-baseline.log` 在最终ERP lane一次执行，发现新失败只最小重跑；合并prospective master按仓库流程再验exact commit。插件完整命令在最终HEAD运行一次，修正后只先重跑相关项，再按发布门槛验最终构建。
6. 插件行为GREEN后patch版本同步package/lock/WXT/README/CHANGELOG；无授权不改变父仓gitlink。独立仓显式路径暂存、提交推送；父仓不git add插件源码。
7. 更新external API、架构/运行契约、schema文档与正式ADR（独立status key、控制面/安全暂停边界）；保留报告事实，不编造部署结果。
8. owner记录S01–S14代码位置、测试/命令/退出码、开放真实验证子项及安全运行说明；分支均提交推送，按实际状态更新registry和tower-do。没有全部代码/验证完成前不标全任务完成。

## 2026-10-16 明确指导的原MiniMax续修

用户最新要求继续调度MiniMax修改并指导清楚，不改成OWNER/其他模型写代码。此前max8为OWNER自设工作流参数，不能表述为用户批准的数字额度；原始用户只答“对”同意未带数字的追加请求。保留历史3次partial及所有native receipts，不以“剩余5次”要求用户另批额度。历史root0808e372的max12/最多2轮review-repair同样只是OWNER内部调度保护；其一child正常blocked后未继续其他阶段。

- Resume TaskStart：docs325adb0 clean；plugin718c83e+原WIP，storage a6be6d4e/controller9843e9ec/runtimef1c434da；ERPf2c0fa7+7路径WIP。第三段冻结源storage38/38经OWNER实跑，55全量失败为worker证据，不称完整runtime通过。
- T4拆为六个顺序门禁：status-runtime-callers → status-runtime-budget → status-runtime-actions → status-runtime-cookies → status-runtime-spec → status-runtime-quality；Tower633，6新任务+174旧任务全部保留。仅callers开始，T4聚合等依赖，ERP质量/T5不提前放行。
- 当前同一观测提交seam需要单writer：storage强制fence/typed result、controller真正生产上下文及处理拒绝、runtime原样转发、测试生产/消费链相互耦合，拆并行writer会撞同三文件。先一条真实正常链vertical GREEN+选择性拒绝，联系OWNER看实际代码但不final；再补所有producer并强制union/相关回归/tsc，最终另取postGREEN OWNER gate。临时编译红是迁移工作，不是hard cap。
- Decision=code-change；最低边界仍已注册T4纯模块。撤销observeBinding在报告前直接预写，复用canonical串行事务和既有ExpectedBindingContext/gen/budget；不新造owner/缓存/proof flag/依赖。先恢复producer/consumer闭环，再处理单90秒、真实业务/terminal写点、首副作用TOCTOU与Cookie原因；不谎称55全同因。
- 当前指导packet /tmp/monitor-status-guided-minimax-01a1406d.md、script /tmp/monitor-status-workflow-guided-minimax-01a1406d.js；原writer433e9d38曾resume为07886337（root0808e372），同mission/原模型/cwd/tools/checked evidence；078回滚后77个既有src/tests文件净差0，未证明任何新caller修复。最新root65447207从078原生resume，仅runtime返回契约及对应测试，先新RED/OWNER再edit/GREEN/postGREEN，禁止宽化controller后回滚；packet /tmp/monitor-status-runtime-result-only-01a1406d.md，script /tmp/monitor-status-workflow-runtime-result-01a1406d.js已validate0。其他T4子任务与T5不提前推进。旧a611/de07/357只供pre-edit形状，不当postGREEN批准；新native schema不降。

### 后续实际微切片进度（只扩展事实，不宣称T4通过）

runtime-result根65447207/原writer7058df8f已terminal，OWNER真实postGREEN b32a5224仅接受runtime f969两文件增量。OWNER逐src核单变量对照（同storagea6、逐字同tests，仅旧/新runtime差异），亲跑2RED→2GREEN/工作树tsc0；原顺序307/308、混杂319/320和最终私有对照328/329分开保留。e8零dispatch假绿、d71混杂与stale snapshot请求被拒；最终positive实际current snapshot、assigned/503和真实模拟HTTP发送均验证。日志/tmp/plugin-status-owner-runtime-result-{red,green,tsc}.log；不把旧55失败计数套到新源、未跑新全量/coverage/build。既有5条import-extension LSP warning已defer，不称全clean。

最新a8c78b60同原MiniMax从705 resume，仅createSnapshotCommit producer及controller tests，受保护runtime/test不动。packet /tmp/monitor-status-snapshot-producer-only-01a1406d.md，script /tmp/monitor-status-workflow-snapshot-producer-01a1406d.js validate0。先实际正常写入+原代次/同epoch nonce撤销的entered反例，保留首次await前generation/真实pre-state，后续初始化不能借新authority；不一口气改全局commit/port/API，冷启动/rebuild等其余producer仍明确pending。Tower callers仍in_progress、后继依赖未过，未写runtime完成回执。

snapshot后续已真实通过OWNER4bc：根a8/f35 terminal、七字段仅两file。最终controller20e8/testa5bc，OWNER亲跑原tests3GREEN/旧controller3RED（A应用/B-C契约）/tsc0，私有同byte probe24e3badb先实际canonical断言：current3GREEN，gen=true或借新nonce各actual applied:true使对应1FAIL、另2PASS，非默认拒绝假绿。/tmp/plugin-status-owner-snapshot-producer-01a1406d及/tmp/plugin-status-owner-snapshot-*.log保留，非cold-start/rebuild/所有callers接受。

最新root8ed7301a-8ef9-42ca-b54c-0a8970112cff从原f35续接，/tmp/monitor-status-bootstrap-typed-01a1406d.md：仅typed commit拒绝消费+EMPTY bootstrap原operation fence；三file(chain仅真实adapter full result转发)，runtime/snapshot函数保护。新业务RED/OWNER/独立postGREEN；不向所有producer合成权限、BUDGET_EXPIRED不当absence、不新budget。其他producer/union/budget/actions/cookie/spec/quality/T5未接受，callers仍in_progress。

bootstrap随后独立通过OWNER8709，仅helper typed拒绝+空bootstrap，8ed/555 terminal/7字段与receipt已核。OWNER当前7named GREEN/tsc0，仅旧20e8同finaltests四bootstrap全RED，四私有单点控制（忽略applied/混accepted/去gen/借context）各对应B-C/D/B/C选择性失败，其他PASS；/tmp/plugin-status-owner-bootstrap-typed-01a1406d及/tmp/plugin-status-owner-bootstrap-*.log。§12 plain observation满queue的durable pause/latest state符合既有contract，pendingReport错误假设不当storage漏洞。其余producer/预算/全量仍未接受。

用户再次继续，最新b060735b-0dfe-4c9b-9230-c31d6664b297原555 resume；/tmp/monitor-status-safe-stop-producers-01a1406d.md及workflow-safe-stop script validate0。ONLY HOLD/requireUser/suspend+controller tests两file，既有owner局部修复/strict TDD/ArchitectureReviewRequired；同步自身gen原边界、read即时PRE scalar、即刻stop且两Promise立即观测、不借新authority。原B控制以更强真实HOLD成功+显式fixture还原tuple/absence保持generation隔离，不删弱化，不抹audit queue。新4+原7named/tsc、新两gate；runtime/storage/snapshot/空bootstrap/helper/chain保护，其他producer/总预算/actions/cookie/union/spec/quality/T5不扩。callers仍in_progress，单步cap非用户次数限制。

## Risks and Stop Conditions

- Hotspot归属未协调：先完成独立模块、给精确patch，不碰外来工作树；等待不阻止另一独立组件推进。
- 测试模板/工具失败：只用官方依赖体检/修复路径，绝不换数据库或直接测试。
- 模型/子agent启动、runtime、工具基础设施失败：保留run/ref/diff，通过同协议明确恢复，不换CLI或其他模型。
- 无真实凭证、证书、签名扩展或授权：完成代码/自动化后准确标识外部验证缺口；不能擅自生产迁移、重启或实店写请求来消除缺口。
- 不以固定hold、无调度注册、sender占位、没有真实接线或纯mock为完成。S01–S14全部有实施映射，真实环境部分不得靠单元测试替代。
