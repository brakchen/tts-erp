# 通用插件状态与 monitor 单目标恢复实施计划

## 当前执行快照（2026-10-16）

纯核已交付；runtime 仍实施中，未合并、发布或整体验收。唯一协议是 `docs/plugins/plugin-status-report.md`（批准基线 `fb42e16575a241fec88740d69137ae6a8153c2bb`）。当前技术断点与证据见本目录 work record 的 `20-checkpoint.md`、`runtime-review-1.md`。

已局部接受十段：原九段producer/result迁移，加第十`mandatory-commit-union`。第十独立postGREEN gate为`f038bef7-2b27-49c3-8592-085445c61658`；native root`fd998ff2-d905-438c-b126-c2db229734fe` / child`3afb1272-caf3-4d96-a940-ad6d0f36e232`已terminal，实际七字段/四path/完整gate已核。OWNER独立57个去重named PASS、product-cwd unpipedtsc0、两个隔离静态控制及scope证明；当前controller`b8cdbf86…`，controller test仍`2065fb2f…`。这不是全部callers/T4、预算或发布接受。

recovery沿用首attempt代次、首read scalar PRE及原capture budget，入口/初始化后/failed组合前/bind await后消费原frame；正常ready与failure经唯一runtime出口真实应用，已知失权不返proposal。OWNER曾在36GREEN候选发现三个真实结果缺口，补三案后39GREEN；physical bind本身仍可能迟到，不能将fact fence说成physical fence。第十仅收紧storage两臂type、controller既有commit helper和八处正向runtime fixture；不改runtime算法或增加authority。orphan fence原runtime会忽略，静态排除是新硬化；fixture从原seed复制nullable scalar，不借最新context。未批准的whole runtime/storage/chain命令已披露，保日志但不追认；最新四文件LSP有104项hint/warning/information、无error，不能称clean。Tower635将callers置blocked，新增`status-runtime-chain`诊断实际STALE_CONTEXT，原因未证，不预设snapshot预写即首拒绝根因。之后单预算/实际mutation fence → action-start → Cookie原因 → fresh spec→quality → ERP/T5/最终验证；各修复仍须新RED/新预审/独立postGREEN。

只读chain诊断已进一步定位最早拒绝：首命名案在候选/第九各1FAIL/7skip，原predicate仍live但raw binding为own-key null→unknown；configure新instance的原子null写与absence-only bootstrap相冲突。私有仅移除snapshot预持久化仍同一失败，不能据此给假根因。finding`f-57bdab7b-eab`已认领，原writer read-only root`ab494011-10ea-4a26-8e08-088d15b27121`续接审查表示/原子性/最小owner修复，产品与测试仍冻结；不是修复授权或完整root closure。

inventory ab494011/c108已terminal BLOCKED/空delta/空gate；其“一个set丢弃binding”建议被HOLD。官方StorageArea文档明确set遗漏key不受影响，remove独立；不能虚构原子删除或允许old在途absence操作跨namespace复活。现corrected-design只读root`437688b5-70fc-4d60-a633-82a19cf9791e`续接原c108，审查现有schema有效非执行初态/真正可复用reset；仍无tests/source修复权限。

corrected-design root437688b5/child4d7be134因原生`commands-run evidence missing`失败，空delta/空gate输出不算接受。已停止/保存WIP快照并确认产品冻结；同协议证据修复root`6bc5edee-db7c-48de-864d-451aeb955482`仅授一次实际tsc命令证据，不降checked/换model或批准改码。valid canonical PRESENT不会重开absence，相关“第二独立缺陷”误断已撤回，设计仍未决。

最新：补证root6bc5edee/child45a0df7e-8d20-4a6a-8c74-b2df9d6b459f已complete，七字段needs_context/空delta/空gate、实际tsc0，仅报告证据修复。OWNER私有仅改单namespace binding payload的closed有效初态反事实，使原首chain案1PASS/7skip+private tsc0；首heartbeat报告同canonical incident，不是真ERP或其余七案证明。选择provisioning物化初始化episode、复用已有字段构造器到storage/controller薄委托原newId（已有runtime import，无新edge），不动unknown/fence/同实例事实、真正absence的instance_started。Board636实质扩scope；新原writer root`7187c653-a34f-462c-a29c-cb995e31ed42` validate0/exact-resume45a0，仅Storage测试八named/实际tsc许可，源码须有效RED后新pre-edit、后独立postGREEN；packet`/tmp/monitor-status-provision-initial-record-01a1406d.md`。84源/测/入口文件启动前同第十freeze，完整T4仍未交付。

最新source pre-edit`05666f7b-cf27-40db-823c-f82202bdf1b8`已窄授权非DONE：实际seed-only B heartbeat/accepted非空ledger、91a4test及603三业务RED五guardPASS39skip/604tsc0与源码冻结已核。仅storage共享原19字段factory+configpayload/controller薄委托，19不是OWNER笔误20；八storage+39controller+首chain1+tsc后另新独立postGREEN。setup591/600/601排除，整体仍开。

此前pre-edit`ccf57c10-01a3-4505-88b3-67e2cf73dddd`仍HOLD：正案改B但仍ready/incident:null、ledger start/result未accepted ID却未断言非空；需actual B seed保持不变并heartbeat、真实accepted非空ledger，再named8/type/NEW preedit，源码未准。591 fixture defect排除，592实际3RED5PASS39skip/593 type0。

此前pre-edit`5d113505-4c66-48ff-bc1f-92178e7dbfce` HOLD：实际test-only/3业务RED+5guardPASS/39skip/tsc0已核，但新正案configure B后仍写/断言A fixture，同实例ledger/runtime保护亦空泛。只准新测试纠正为actual B seed与一致heartbeat、实际old-A PRE、非空ledger/runtime保事实/no set-remove；再八named/实际tsc后NEW pre-edit，源码未准，不把该人工fixture当生产第二root。

当前 **STOP**：root`38f2a50b-2850-443e-9392-6aa6b8ec9832`/child`5631b4c9-52bc-4caf-882c-0b53ce88d24a`原生失败`Structured acceptance report not found`；旁路七字段JSON不能替代实际prose终稿。新guard为c180、仅27行新增，产品641命名案1FAIL/8skip、640 actualtsc0；helper/生产源码/旧八case未改。272496f9-5e89-4e2c-a107-fd622b36d8d2是HOLD、15c4108c-f86d-4fd8-8aab-970071307e8b是WAIT，无pre-edit/GREEN/DONE。

两个产品fixture写入已找到直接机制：pi-lens在opaque编辑后自动全文件9案（8PASS/1FAIL/0skip），PID129439的vitest日志、00:51:37缓存与两个emit写入时间一致；不是worker四条显式命令中的full调用。自动9案保留而不追认。原件/patch/缓存/日志及`provenance-review.md`保全于`/tmp/plugin-status-chain-adapter-native-failure-01a1406d/`，未restore、未执行可疑外部命令；注入仅worker未获独立字节证实的声称。现有no-tests/tests.enabled是global/CLI控制，不支持项目配置；retained resume拒extensionBindings，不能写无效局部设置、改全局影响同伴或偷换profile。安全启动策略与inline严格七字段修复前不续helper。Board639 callers blocked，finding`f-408d5c17-c51`已认领。又发现既有自动runner明确排除`**/*.integration.*`，而该chain跨真实runtime/storage/controller；同目录仅改basename为`plugin-status-chain.integration.test.ts`可保持内容/相对导入/fixture路径，仍被vitest include收集。已用维护中的minimatch静态核实旧名autoExcluded:false、新名true、两名include:true，非产品执行。故撤回“唯有改启动参数”的前提；同原生协议只读root`ef087cc1-3f21-46ca-be71-24d563b2595d`续5631评估此分类是否正当而非掩盖失败，尚无rename/helper许可，待真实报告后新架构门禁。chain既有八命名变体窄完成不重开。此前OWNER七案仅私有7PASS/1skip、修正setup后private/producttsc0仍有效，但不交ERP、不等于T4。

历史接受基线：当前已接受**十一微切片**，第十一最终gate`3c1d5e92-e7e8-4ca7-b4fa-2774999a462c`/root7187c653/child6ee1cecc已原生terminal七字段核读。provisioning单set写已有closed初始化episode、纯构造器迁storage唯一19字段owner/controller传原newId，不改unknown/fence/union；首heartbeat报告同episode。独立48去重产品named+1私有probe PASS、producttsc0、两反事实杀死且tsc0/精确三path证明。LSP0reported errors但3inconclusive；其余七chain仍未知、完整T4未完成。产品聚合16-path检查点18d0887已push且树clean，仅保存原12034行WIP，不是全验收/发布或master/pin变更。原artifact test-before fb11为held中间态，真实微切片基线ef333→91a4已记erratum，不改原稿。证据`/tmp/plugin-status-owner-provision-initial-record-01a1406d/`。

### 当前：第十二局部终态已核且push；继续只读Caller闭合盘点

ae28/8b417 complete/terminal、native review-required非SDK accepted，真实DOMAIN7/done/精确old+new paths/完整post0d800/无命令占位符/终态6425字节已核。仅旧新测试路径显式stage，commit/push0bab24702001dc9be12d4a81d181ee33c53e3554（rename99% +2/-5）；两原fixture deltas未stage仍modified，非Repo clean/master/pin/version/release。Tower641/182仅新增测试fidelity窄completed，Caller仍in_progress，f-408 broader未来unit自动超named风险open。新只读root420cd1c3-3dfb-4ae8-aca8-add343a12027 exact-resume8b417，盘点宏Caller vs预算/actions/Cookie/specquality/T5/共享owner边界及future named-only策略；仅准一次actual产品type到649，无app tests/代码/改名/配置授权，不能由微gate推MacroDONE。详细Scope Card/snapshot见checkpoint。

### 历史微切片：integration/snapshot局部postGREEN已接受，待终态

独立新postGREEN0d800a7e-c9b2-4372-94b3-7ea06d64ca4b仅接受本测试fidelity：OWNER2产品named+7自有普通copy related均PASS、actual产品tsc0空；before→after只snapshot arrow+准确局部注释、83其他hash/全部旧case+guard精确，2fixture全stat/cache不变、实际TaskStart窗口无新auto runner。最终新文件64251491ae3d991b3b1a2ff058c16b20db01325e94cd97de6e50aadf0a2bf998，LSP0errors/1inconclusive非clean。仍待rootae28/child8b417实际7字段/native终态/终态范围/Git；全Caller/T4未闭。f-408未来unit自动整文件超named风险仍open，不因单chain分类而泛称全工具修复。以下为分阶段过程。

root986d1b1a-c8f5-437d-97ae-6aed9d9282a9/childd478332e-c442-40fe-a445-ed6c5bfd6453已complete/terminal；native acceptance review-required非SDK accepted。OWNER独立核真实DOMAIN7/needs_context/空delta/空gate/643 actualtsc0空/84文件未变，只确认报告载体修复。新rootae28e0da-0bdf-46df-a09c-f96f2bbc52bd exact-resumed478先NEW architecture/pre-edit WAIT，A新gate0ea744b1-53bc-4fc8-b67a-cc4f94c7ddb4已执行并独立核实精确c180改名/644同RED/645type0、映射84字节不变、2fixture全stat不变、auto cache逐字不变且实际TaskStart窗口无新runner事件。现仅B新pre-edit49988cd1-98c9-41bd-9bae-f54efd551bad获批compose-only，GREEN及distinct postGREEN仍待核，不是DONE。A新gate仅字节精确integration改名，显式同guard RED/type0/无自动9和fixture写，才能另fresh helper pre-edit；B仅snapshot compose-only，2产品+7自有普通scratch related/actual产品type0，再distinct独立postGREEN。自动9本未授权，不依赖它保RED；A显式RED不可丢，失败即停，不是只改名隐藏失败。生产/旧case/guard/2fixture/header/version不改，无新框架/flags。origin/master ACTIVE既有glob真实UUID已覆盖新名，Tower640已显式scope/in_progress；f-408仍open。TaskStartSnapshot与完整Scope Card见checkpoint，宏T4/发布未完成。

### 历史native报告修复（无产品权限）

只读ef087cc1-3f21-46ca-be71-24d563b2595d/e9cf2364-72d0-4206-bad9-650d9130dfb1也因Structured acceptance report not found失败，但实际final合法7字段、642 actualtsc0/空、84产品文件未变。canonical SDK证明需要独立acceptance-report元数据，checked验证后自动strip，DOMAIN仍7字段。OWNER撤回自己误设的“任何footer/fence都不准”，不是降验收或改SDK；真实bare7 parser失败/明确synthetic carrier parser成功且strip逐字一致仅为诊断，不冒充native通过。原件及parser-proof保全native-carrier-diagnostic目录。新只读同协议root986d1b1a-c8f5-437d-97ae-6aed9d9282a9 exact-resumee9仅补报告载体和fresh643 actualtsc，等待真实receipt；无rename/helper/fixture/config权限。e9 integration分类/先后次序意见仍需独立OWNER审查，不是架构或产品gate。

## Goal

实施已批准协议的 D1–D4：monitor 单 Seller/Advertiser 自动绑定、失效安全暂停、ERP 状态与生命周期通知、受限恢复。保持首次服务端放行、独立 status key、离线不得自行恢复、完整 fresh close 快照门禁。OWNER负责设计一致性、独立验收和协调，原 MiniMax M3.1 Flash Preview 是唯一产品 writer，不换模型/CLI。

## Architecture

一个 ERP status domain 拥有实例、报告事实、状态投影、受限决策和通知 outbox；一个插件 binding controller 拥有绑定生命周期。报告响应是唯一决策交付路径，不新建长连接、远程代码平台或跨插件后台依赖。

- binding/report/terminal事实复用既有 canonical 串行事务，不另造缓存、registry、proof flag或 authority owner。
- 每个操作保留首 await 前 generation、首次 read 的 scalar PRE tuple、同一 budget；父负证据不得在子操作中借新授权。
- 合法 self-revocation前同步消费 parent predicate，随后只用自己的 mutation generation；不能因自己的自增而拒绝全部正常路径。
- typed `applied/refused` 与 enqueue `accepted`、执行/cleanup 许可分离；拒绝或未知不返 proposal、不继续 clear。
- expired/unknown不是 raw absence，literal `expect:null`只用于真实 null；不重开预算。
- durable fact/report 先于实际 clear；已落库事实不因cleanup失权被抹去。外层检查不等于业务端口自身 awaited mutation fence。

## Tech Stack / Reuse

ERP复用 FastAPI/Pydantic、SQLAlchemy 2/PostgreSQL、access/API key、token_service与sync-worker；插件复用 TypeScript strict、Chrome MV3 alarms、WXT、Vitest与现有storage串行机制。参考 ads-data-sync 的 binding/page coordination/MAIN捕获实现，不修改参考插件。无新增运行依赖；琐碎逻辑沿用本项目既有owner，不建设新框架。

## Authority / Workspace

- Session：`01a1406d-2041-7452-8338-3d7de574337f`；Tower identity：`自动绑定tab 优化`。
- Docs：`.worktrees/monitor-auto-bind-status`，`docs/monitor-auto-bind-status`。
- ERP：`.worktrees/plugin-status-erp`，`feature/plugin-status-erp`；core `f2c0fa7`已push，runtime仍WIP。
- Plugin：`/home/schan/pi-workspace/monitor-status-01a1406d`，`feature/plugin-status-auto-bind`；core `718c83e`已push，runtime仍WIP，版本0.4.26。
- Tower：`/home/schan/.pi/tower-do/home-schan-tts-erp-a0cde689/board.jsonl`；父会话维护，worker只读，不自行Git或写板。
- 已有lane registry与文件归属保持。主仓 foreign `M plugins/monitor`、`?? node_modules/`不暂存、清理或改写；共享热点须明确handoff，不接管他人owner。
- 遵循根/插件AGENTS、agent-safety/testing/git-workflow及architecture/process/external-api契约；生产迁移和服务重启由人执行。

## Compatibility Boundary

1. 单 Seller/Advertiser/Cookie Store，不加第二 Seller origin、自动建页/登录、多店或跨店fallback。
2. 不改变campaign白名单、maxSKU、阈值、close/report窗口、完整分页与逐项关闭复核；展示缓存不是 fresh close proof。
3. status身份/凭证独立，不进入配置导入/导出/force；导入/停监听取消旧generation，迟到结果不复活任务。
4. 健康running的短上报中断不无条件停止；失效/初始化/不可证明上下文不离线恢复；动作许可过期不是健康运行续租。
5. 健康无incident running心跳返回decision=null，人工hold/require_user锁存优先。
6. 报告/决策/audit事实保留，不删除旧ledger修测试。status key缺purpose failclosed，不借普通RW或monitor_config key。
7. 不执行生产迁移/重启、真实凭证操作、TikTok写请求或真实通知；真实环境证据单独列出。

## TDD / Architecture Gates

- TDD Route：strict（公共契约、权限、持久事务、producer/consumer、真实关停风险；插件AGENTS亦强制strict）。
- ArchitectureReviewRequired：yes。
- 每段：可观察的有效 RED → 新OWNER pre-edit并WAIT → 既有canonical owner最小修改 → 相关GREEN/实际unpiped tsc → 独立新postGREEN。旧gate不能批准新slice，worker/native complete不等于接受。
- 测试真实controller/storage，I/O边界模拟；实际entered后交错，Promise创建即观察，finally释放并await所有主工作；setup/类型/未entered timeout不是业务RED。recorder证据和真实mutation分开标识。
- 先正常真apply/report/clear排除default拒绝假绿，再原generation/nonce与选择性source控制；事实在真实clear入口已durable，不仅commit:start早于clear。
- 不通过skip/删除有效回归、降低95% statements/functions/lines或90% branches门槛获得GREEN。
- 当前只named/related+tsc，不跑旧full/four-file/coverage/build；最终精确candidate统一验证。

## Change Necessity / Owner Fit

Decision=code-change。自由文本plugin logs不能提供实例、排序、幂等回执及恢复许可；组件内新增职责已进入独立status/controller模块，旧background等大文件只做协调后的薄接线。现阶段各producer迁移是既有owner局部修复，不增加模块/权限/依赖。

配置、文档或末端判断不能替代canonical mutation检查。短事务拥有状态真相，长网络不持storage/DB队列锁。需要变更公共API、共享owner、schema、权限或增加fallback时先报OWNER，不靠改方案适配错误实现。

## Implementation Topology

| Lane | 责任与边界 | 验证/交接 |
| --- | --- | --- |
| T1 ERP core（已交） | 严格contracts/纯policy，UUID/int64字符串/UTC/未知字段/受限action参数 | isolated unit，公开契约与golden fixtures |
| T2 plugin core（已交） | 严格协议、HTTPS HTTP/client、seq/queue/ledger/retry纯核 | Vitest/tsc、跨端golden与合法负面输入 |
| T3 ERP runtime | repository/service/API、实例锁、幂等报告、投影/决策、outbox | DB/API隔离测试，spec→quality，不能先接共享热点 |
| T4 Chrome runtime | 唯一controller、MV3生命周期、page协调、canonical storage与现有业务端口 | 有效故障注入、真实producer/consumer，最终full/coverage/tsc/build |
| T5 integration | auth/model/migration/scheduler/CORS、GUI/MAIN/metadata/分页、版本与部署契约 | 明确owner handoff、跨端与浏览器证据、exact-head集成验证 |

### ERP contracts/runtime

- JSON camelCase、Python/SQL snake_case、UUID、十进制int64字符串、32KiB、严格未知字段、aware UTC，两端同一正负fixture。
- 所有模式拒绝匿名/错误purpose；status key仅精确report用途，admin mutation保CSRF。
- 实例锁串行报告/人工/巡检；event/hash+seq唯一，低seq仅归档，receipt/decision冻结而requestId/serverTime每次新。
- 完整事务commit才2xx；first advertiser空值CAS、manual requestId幂等/expectedRevision、两阶段就绪放行和恢复预算。
- outbox事务内入队、发送事务外领取lease，有界重试/dedupe；stale不是login，未纳管旧版本不告警。
- 已验证实例锁populate_existing及真实两Session交错、canonical ACK更高revision/superseder拒绝，不重开这些已闭领域。

### Plugin lifecycle

- desired scope与bound tab分离；稳定合法绑定不被active抢占；Cookie Store/Seller/Advertiser分别验证，多身份不猜店。
- MV3事件+alarms、单飞、共享90秒/冷却；有限recover才导航/刷新，ready仍suspended；resume需服务端许可+原开关+完整新close快照。
- 所有producer生产原context并消费typed结果；强制observation-only和binding+fence判别union，不保留unfenced默许。
- 实际bind/clear写点、terminal binding写点必须消费fence；两个refresh-release await、claim后首副作用重新验证deadline/epoch/nonce/enablement。
- Cookie unavailable/timeout不是verified different store，wake/post-reload保留原因。
- 现有手动/自动入口最终均调用同一controller，不产生双owner；GUI仅展示，不重新计算业务权限。

### Final integration / DoD

1. 精确candidate先fresh spec再quality，修后重spec；各组件无活动writer时再共享接线。
2. ERP只用 `bash scripts/test_isolated.sh ...`，最终 `bash scripts/test_baseline.sh` 对已提交master baseline一次执行；新失败仅最小重跑，无其他数据库/runner回退。
3. 插件最终full、coverage、unpiped tsc、build；版本package/lock/WXT/README/CHANGELOG同步，发布ZIP另查manifest/hash。
4. S01–S14映射源码/命令/日志/退出码。模拟I/O、受控Chrome/HTTPS/CORS、真实TikTok/Feishu分层列证，没有真实证据标needs-verification。
5. 共享auth/storage/model/migration/scheduler/GUI/MAIN/metadata/分页经明确handoff；不改middleware顺序、不绕过purpose、不改foreign gitlink。
6. 显式路径暂存、提交推送；同步origin/master后按registry/merge锁构造prospective commit并验证，未全部完成不关闭T4/T5或发布。

## 当前执行纪律与历史

原生事件唤醒，不轮询等待；原MiniMax唯一writer，基础设施失败先保全run/ref/diff，仅同协议恢复，不私换CLI/model。内部max8/12/1不是用户数字授权或provider额度，无实际hardcap不得预估停工。

早期过程从当前计划收敛，完整历史保留于Git提交`1f90588477314f59b0d1fa41308abdcefc00bb7c`、原始native artifacts及OWNER冻结目录；不改写旧门禁、失败或回归无效记录。当前具体调度/源码hash以work checkpoint为准。
