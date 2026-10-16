# 通用插件状态与 monitor 单目标恢复实施计划

## 当前执行快照（2026-10-16）

纯核已交付；runtime 仍实施中，未合并、发布或整体验收。唯一协议是 `docs/plugins/plugin-status-report.md`（批准基线 `fb42e16575a241fec88740d69137ae6a8153c2bb`）。当前技术断点与证据见本目录 work record 的 `20-checkpoint.md`、`runtime-review-1.md`。

已局部接受十段：原九段producer/result迁移，加第十`mandatory-commit-union`。第十独立postGREEN gate为`f038bef7-2b27-49c3-8592-085445c61658`；native root`fd998ff2-d905-438c-b126-c2db229734fe` / child`3afb1272-caf3-4d96-a940-ad6d0f36e232`已terminal，实际七字段/四path/完整gate已核。OWNER独立57个去重named PASS、product-cwd unpipedtsc0、两个隔离静态控制及scope证明；当前controller`b8cdbf86…`，controller test仍`2065fb2f…`。这不是全部callers/T4、预算或发布接受。

recovery沿用首attempt代次、首read scalar PRE及原capture budget，入口/初始化后/failed组合前/bind await后消费原frame；正常ready与failure经唯一runtime出口真实应用，已知失权不返proposal。OWNER曾在36GREEN候选发现三个真实结果缺口，补三案后39GREEN；physical bind本身仍可能迟到，不能将fact fence说成physical fence。第十仅收紧storage两臂type、controller既有commit helper和八处正向runtime fixture；不改runtime算法或增加authority。orphan fence原runtime会忽略，静态排除是新硬化；fixture从原seed复制nullable scalar，不借最新context。未批准的whole runtime/storage/chain命令已披露，保日志但不追认；最新四文件LSP有104项hint/warning/information、无error，不能称clean。Tower635将callers置blocked，新增`status-runtime-chain`诊断实际STALE_CONTEXT，原因未证，不预设snapshot预写即首拒绝根因。之后单预算/实际mutation fence → action-start → Cookie原因 → fresh spec→quality → ERP/T5/最终验证；各修复仍须新RED/新预审/独立postGREEN。

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
