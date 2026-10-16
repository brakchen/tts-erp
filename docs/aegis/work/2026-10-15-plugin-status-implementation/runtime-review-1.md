# Runtime review 1 — 闭环未通过

## 裁决与边界

- Workflow：`40b173a9-f68a-4304-960b-167ae1bf1d6a`；reviewer `42c30001-c0be-4df0-8025-7167fda56bdb`。
- 最终结构化产物 `reviews/runtime-spec-1.json`：**BLOCKED，13项P1、2项P2**。Owner逐项直接阅读当前代码和方案§4–12，reply `79876f2f-ae8a-4380-a2e7-8232da79fa57` 确认。不是照收外部意见，也不以未挂载/缺真实环境当作本轮源码缺陷。
- T1/T2纯核已有提交和两轴认可不回退；T3/T4任务仍in_progress，T5不得进入。ERP初交71 tests与插件初交641 tests/coverage/build通过只能证明原测试覆盖范围，不能证明运行闭环正确。
- 修正仍用原workflow、原MiniMax writer协议；当前ERP续跑 `c7ff7bbc-b20f-4803-954e-3bfecdef4652`，之后由workflow继续原plugin writer。无另起CLI或新顶层workflow。

## Owner确认的修正项

| 等级 | 唯一owner/问题 | 必须验证的可观察行为 |
| --- | --- | --- |
| P1 | ERP ingest在seq判断前推进episode/transport计数、清人工锁存 | 低seq不同incident只归档，不改变当前episode、锁存、预算或投影；网络last_seen可更新 |
| P1 | ERP结果匹配/解决episode仅检查instance/id/revision | 旧resume结果可补历史，但只有当前incident/epoch/revision及当前running确认能解决当前episode |
| P1 | ERP现有decision复用忽略epoch和terminal状态 | ready新epoch不得取得旧epoch指令；failed/rejected不作为同一可执行命令重发 |
| P1 | ERP忽略服务端登记capabilities；installation binding=null落入无epoch错误 | 客户端声明不授予能力；空grant不能取得recover/resume；合法installation遥测200且无不兼容绑定指令 |
| P1 | ERP缺observedAt相对now的未来5分钟拒绝 | 超界报告在落当前状态/事实前受控拒绝，不能200推进投影 |
| P1 | admin recover绕过60秒/600秒/3次预算 | 解除manual latch不重置恢复预算；第四次不得插入attempt4后500 |
| P1 | plugin周期/freshness/并发owner错误 | 心跳建议不延迟失效；旧draft不冒充本次snapshot；历史补报拒绝不永久阻止新鲜证据；所有入口single-flight，started必须独占声明 |
| P1 | plugin完成结果/报告非原子，生成running snapshot即清incident | terminal结果与其报告一起落盘；保留incident直到服务端确认applied+running回执 |
| P1 | controller被动观察只计tab，初始化/登录修正无法产生候选证据 | 允许有界只读身份核验并上报；仍必须等server recover/ready/resume，不自主刷新或启动 |
| P1 | controller给顺序端口分别90秒且reload无界 | 一次总deadline、取消/晚到提交闸；不能仅测常量90 |
| P1 | wake/reload后仅tabId弱证明，Cookie Store/身份未持续复核 | 同tab变店/变store/离开origin不能保留running或提交旧证据；不新增server wire Cookie Store字段 |
| P1 | HOLD只改显示状态，未停scheduler或撤销generation | 对running和deferred recovery均立即停危险执行，晚到恢复不能重开 |
| P1 | invalidation先清业务绑定，provision跨多个写操作且保留旧binding | 即时generation停机，同时持久失效事实无断电丢口；切instance与队列/账本/绑定在同一序列化事务，禁止跨实例携带可执行上下文 |
| P2 | resolved后用no-episode构造去重键；恢复正文却提示人工 | 不同episode各一次恢复通知，重复同episode幂等；正文与恢复状态一致 |
| P2 | 在线NO_CANDIDATE持续180秒无通知 | 以episode内持续观察计时，按约定阈值通知一次，不等到失联或600秒人工升级 |

巡检超时仅按方案明确事实推进；不能伪造“已执行”或实际未发生的终结结果。共享purpose/mount/model/migration/browser接线本轮不当作缺陷，但整体仍必须补齐。

## 独立行为复现（模拟I/O，不是真浏览器）

冻结源码副本：`/tmp/plugin-status-owner-runtime-r1-01a1406d/`；仅复制插件源码及合成fixture，未修改worker工作树。执行真实runtime/controller/storage/client/queue/ledger模块，只替换Chrome storage、page/scheduler端口和HTTP Response。无网络、凭证或TikTok写操作。

命令：在插件工作树运行 `./node_modules/.bin/vite-node /tmp/plugin-status-owner-runtime-r1-01a1406d/probe.ts`，exit0；日志 `/tmp/plugin-status-owner-runtime-r1-probes.log`。

1. 合法历史`binding_ready` draft observedAt **17:00:09**，本次新snapshot **17:00:10**：发送并执行旧报告响应一次，而新snapshot仍在队列。
2. 健康心跳成功后立即失效：发送数 **1→1**，失效报告仍pending=1，被下一心跳时间阻挡。
3. 允许`/ads-creation`同tab，但business binding Seller/Advertiser已不符：wake仍 **running/gate=true/stop=0**。
4. 没有任何网络确认：生成snapshot后 **incident=null/reportsSent=0**。
5. HOLD后持久状态为suspended，但 **schedulerRunning=true/stop=0**。

并发命令：同目录 `concurrency-probe.ts`，exit0；日志 `/tmp/plugin-status-owner-runtime-r1-concurrency.log`。两次并发observation在可控HTTP barrier同时进入 **2** 个请求，同一冻结decisionId的executor调用 **2** 次。不是仅mock函数计数：实际周期、出队、授权、accepted/started/terminal账本均运行，barrier证明重叠顺序。

首次诊断setup使用heartbeat draft（被正确合并）和非允许`/product`路径，不能证明上述缺陷；已改为不可合并的`binding_ready`及允许origin/path后才记录有效结果。禁止把首次未复现当作RED或错误地修改产品匹配规则。

这些日志证明R1冻结候选的缺陷，不代表正在修正中的新候选状态。后续须对完成的真实候选重跑对应反证，不以副本通过代替当前源码验证。

## 共享purpose兼容源已定位

- ERP初交因在当前clone `git cat-file 03d595fb`不存在而未读维护中的purpose实现。实际源在另一个clone：`/home/schan/pi-workspace/monitor-managed-01a129a2/.worktrees/monitor-config-erp`。
- Owner验证HEAD **03d595fb9b079b541ed6425173fc257854160fa2**；仅foreign WIP `tests/unit/test_monitor_config_persistence.py`，`access/`及`db/models/security.py`相对该提交无差异。
- ERP续跑`c7ff7bbc-b20f-4803-954e-3bfecdef4652`已在handoff明确ack并读完规范来源；此前“排队”状态已消费。没有修改外部工作树，准备了`/tmp/plugin-status-purpose.patch`及待核对revision的迁移草案；尚未应用或通过共享owner审核。
- access/security/app/base等仍需明确handoff；不得整分支cherry-pick或另立purpose owner。T3可备最小patch，但未挂载测试不证明canonical purpose已经接通。

### ERP修后额外OWNER BLOCK（独立于R1冻结清单）

C7交付自身API71 tests GREEN后，owner直接读取实际`purpose_allows_status`（service.py:127–144）与`_mint_status_key`（:300–323），发现两项相连的权限缺口：

- `purpose is None`仍返回True，含义是缺canonical字段时把绑定的普通RW key当status key；这不是fail-closed，也不因“尚未集成”成为合法授权。
- mint只填role=readwrite，不写purpose；docstring说以后integration必须补，不能阻止当前helper铸造有普通RW权限的凭证。C7 acceptance prose称fallback已移除，与实际None分支不符。

**Owner不接受该过渡状态。** 依赖字段/规范provider未就绪时应明确拒绝或报受控unavailable，并在写实例/key前停止；仅明确`plugin_status`事实允许放行，缺失值不得当已授权。确实铸造专用key必须使用规范purpose；不能先发普通RW key再靠调用者记住限制。无需为此越权修改共享model/access，测试可使用明确的外部事实/provider边界，但不能把缺purpose放行断言作为正确GREEN，亦不得mock整个权限域。真实canonical接线及跨路由负例仍待T5/owner交接。

这不要求重做现有purpose体系，恰好是拒绝第二套宽松兼容语义。下一次fresh spec OWNER gate必须确认该点关闭；当前不发布runtime、不将T3标为完成。

## Runtime spec2 — OWNER继续BLOCK

Reviewer `c25ded39-6914-4573-a34c-8dc73544ce9c`，reply `77007b54-8735-4b50-be16-9afe1b290a55`。两writer R1修正已交，A7明确ack scope及owner探针并报告6项原反例已修；这仍是worker证据。Owner再次读当前真实源码，确认以下未关闭/新增问题，不把原冻结源码当修后失败证据：

- purpose缺失默认放行及普通RW mint仍未关闭，继续P1。
- recover先独立提交ready，再返回无commit的applied，runtime允许只落terminal ledger：恢复结果/报告仍非原子，P1。
- passive verify保留invalid/login_required/conflict，服务端只接受discovering/verifying候选，纠正登录后仍卡住，P1。
- await端口无整体deadline race，最后snapshot返回后也无预算检查，仍可挂起或超90秒提交ready，P1。
- resume返回running commit后存在await口，storage无同事务instance/epoch/generation CAS，失效可被旧running覆盖，P1。
- connectivity在LIMIT/OFFSET后才过滤、total未过滤：派生时间条件应进入查询与count，不应持久化标签，P2。
- enable/disable仅role guard，漏既有cookie CSRF helper；角色不证明请求意图，P1。
- OWNER新增：ACK只看全局pending/non-null incident/running，回调无expected context，controller又以分开的read/write清当前incident。必须绑定同一instance/incident/epoch/revision和applied-resume证据，并CAS；旧running心跳/冻结回执不能清新episode。
- 历史响应先触timeDenial就会被terminal-settle，即使它不是当前snapshot。应在账本裁决前识别历史carrier；回归要证明历史NotBefore/skew后同命令新鲜响应确实可合法开始，不能只用本来已过期的命令。
- 默认outbox sender在claim commit后以同session查secret，autobegin又跨HTTP；必须把secret与message DTO读取放短事务并在发送前关闭，以默认resolver路径测试，不仅测试绕过resolver的注入sender，P2。

需用真实文本重新锚定行号，policy.py当前只375行，不能引用缓存图的392行。namespaceStillCurrent已存在，不笼统宣称所有namespace情况失败；关键是await窗口内的原子上下文声明与晚到写入。最终`reviews/runtime-spec-2.json`已返回BLOCKED，8P1/2P2；补充thread`17b0257b-dc1a-4c05-9ae3-bce9e5e06883`确认历史案例：observed t0在server t110合法获得recover[110,230]、回包丢失；t140重试旧报告时观测已过期，但命令仍有90秒开始窗口，新鲜t140证据本可复用。旧carrier触STALE_OBSERVATION却终结指令是可达源码反证，不宣称已执行此案例。源码及SHA保存`/tmp/plugin-status-owner-runtime-r2-01a1406d/`。整体未放行。

## Runtime spec3 — 6项P1继续BLOCK

最终`reviews/runtime-spec-3.json`为BLOCKED。Reviewer `f663c92d-7bcf-4949-b620-729f86b1fe48`首次OWNER请求超时后重新等待；owner已直接读当前runtime/storage/controller/service与指定测试，回复有效thread `2c81934e-a3b3-4747-b182-b0fdd1d5b07d`，没有跳过门禁。

1. LOCAL_DISABLED分支仍保存旧running commit，常规CAS只比较instance/incident/epoch；HOLD保留该三元组，不能阻止晚到resume。需要同一最终写入内的执行权限fence；不能用改变物理binding epoch偷换HOLD语义。
2. budget仅包reload/final snapshot，其他端口仍可永久挂起；而且在ensureInitialized之后才开始计时。总90秒要覆盖第一个await起的所有步骤。所谓hung测试创建未使用promise、没有await recovering，不是取消证据，必须替换为实际注入deferred端口并等待结果、释放晚到值验证无副作用。
3. denial/unsupported/throw/revoked/stale等终结路径仍可只存ledger，测试还期待等未来心跳再报。每条终结路径必须原子保存报告，容量/不可读失败保留明确pending-report与暂停义务，不丢结果、不借机恢复旧绑定。
4. ACK虽有三元组CAS，仍未证明匹配applied resume/revision及当前running/pending权限；同epoch HOLD后可被旧ACK清incident。冻结projectionUpdated=true不能单独证明当前授权。
5. createSnapshotCommit一律NONE，下一次新鲜快照会清掉服务端持续NO_CANDIDATE和transport计数。以真实controller→runtime报告链验证，不人为重复构造同reason；也不能把同一sticky失败的每次心跳当新的业务请求失败。
6. manual recover只验证context/budget，未检查登记capabilities；admin不能创建未授予的能力，拒绝时不能改变decision/latch/budget。

当前未运行新的owner行为探针或ERP测试，上述为直接源码与测试语义证据。R1可执行探针仍只证明R1旧候选。Reviewer认可上一轮purpose fail-closed/mint事务、CSRF、过滤SQL与outbox事务修正，不把这些旧项重开；实际canonical共享接线仍未放行。

同workflow继续原writer：ERP R3 `a5abf9ca-c45a-4cfe-986c-c33bdc25cc1d`已交；frontend R3 `c85638b8-626d-459c-b209-1d3b41a17603`处理。Owner补充manual disabled同优先级检查：lock_instance不滤enabled、manual路径也未检查，新增recover不得在disabled时发新许可；发送至a5的follow-up仅排队到下次resume，**尚非消费证明**。Frontend已排队具体deadline/真实端口/原子结果/ACK复合测试指引，并指出pending getter的sync/async不一致不应靠新缓存权限owner掩盖；仍须查handoff ack。

## Runtime spec4 — OWNER继续BLOCK

R3两writer均已交，不等于验收。Reviewer `8624cdfb-ba50-4d54-a5dc-0a4d7be515ab`等待直接核查；初thread `34c33314-e151-4c45-b313-76bc6949dfea`在核查期间超时，owner已回复新的有效thread `ff5e9618-2815-4cc5-adb2-3e24404af525`。源级R4冻结副本及SHA位于`/tmp/plugin-status-owner-runtime-r4-01a1406d/`，不是执行探针或浏览器验收证据。

- P1：controller已携带sticky transport reason，但ERP把每条该reason的心跳都计为新失败；单次失败+心跳就达到两次阈值。新测试却发送NONE替身，未覆盖真实跨端序列。
- P1：appliedResume只在runtime内存，worker重启后即使ledger有applied结果，ACK也永远不清本地incident。必须恢复持久义务，不能靠重新执行resume。
- ACK补充P1：runtime先清incident再处理响应里的HOLD。若admin已发新HOLD revision，ERP拒绝旧resume解决episode，却可同时确认报告并交付HOLD；客户端先清空incident会使HOLD授权失败，scheduler仍running。必须结合当前carrier/服务端响应和持久applied proof，先尊重superseding命令，不能清后重新认领旧episode掩盖问题。
- P1：总deadline仍漏status read、businessBinding.readBinding/bindTab，final snapshot成功后也没有再验剩余时间。非可取消的RPC不能承诺被Promise.race撤销，但晚到结果不得提交ready或启动监控。
- 通知问题确认，**按P2而非直接接受P1**：USER_DISABLED后仍保留no_candidate_since，sweep没有重检停用/monitor/execution状态，能错误发送故障通知。清连续无候选条件不意味着重置episode恢复预算。
- P1：passive probe比较仅count/verified，丢掉reason/phase变更。初始count1、false→LOGIN_REQUIRED不报告；更强路径是初始化probe成功、随后passive probe登录失败，OR保留true并返回旧running。实际探到负面身份事实必须立即走失效/停机，不能只加文案。
- OWNER新增P1：manual_decision修了capability但仍漏instance.enabled，新的disabled recover可发许可/清latch/占预算；上一轮追加消息未被消费不能算修复。
- exception终结仍需精确核查：正常settleDecision能生成当前报告，不能笼统指控所有拒绝；但真实businessBinding.bindTab抛错会到runtime catch、commit缺失的直接路径，绕过报告生成，仅pendingReport而无pause。已要求reviewer核实该可达性后列入最终问题。

最终`reviews/runtime-spec-4.json`已核为**BLOCKED，6P1/1P2**，含上述ACK复合问题、manual disabled与已确认可达的exception报告缺口。未运行本轮新的owner行为测试。继续保留原writer窄修与行为RED/GREEN；已修purpose/CSRF/SQL/outbox旧项不重开，T5共享接线与真实环境验收仍blocked。

## Runtime spec5 — 4P1/1P2与实际旧候选RED

Reviewer `95cf664a-559d-4c0f-b952-4e08a9841634`最终JSON为BLOCKED，OWNER有效回复`dad804ae-abe7-4258-9a74-34327fee20b1`及`51d7e8a4-e8de-4e09-8a36-45d07df04307`。

- 四P1：历史running draft仍可清incident、terminal原子写后另写applied proof产生crash gap、first store read及初始化仍漏总budget、capture/recheck transport被误判identity conflict。
- 一P2：通知停用判据只覆盖reason USER_DISABLED，遗漏独立monitorEnabled/disabled execution，与policy不一致。
- OWNER冻结旧R5真实模块，仅在副本启用原`it.skip`的HOLD chain case，得到业务断言RED：expected running to be suspended，exit1、1 failed/6 filtered。源码及SHA在`/tmp/plugin-status-owner-runtime-r5-01a1406d/`，日志`/tmp/plugin-status-owner-runtime-r5-hold-probe.log`。边界是假Chrome memory/pages/scheduler/HTTP，不是实际浏览器或服务端。
- 两原writer修正`e922252d-6ef2-4247-b3ee-4faad32aeb1a` / `7f937f58-469f-459e-b2e9-7a1288f7987d`已交；R6源级确认历史carrier排除、同事务proof、分类及disabled判据已修，不将旧RED移用到新候选。Plugin报告708/708、无skip、coverage95.88/92.65/97.49/95.88为worker证据。
- Plugin handoff主动承认F2/F3先实现后补测试；记录过程违规，后续回归不能逆改原始TDD顺序。

## Runtime spec6 — 原生恢复成功，3P1仍BLOCKED

原reviewer `70a77a59-45d5-41c1-a43f-71897e0b5af6`遇`fetch failed`且缺required structured output，导致root `40b173a9-f68a-4304-960b-167ae1bf1d6a`终结failed。32/40 used、8 remaining不等于额度耗尽。已保存三工作树状态/diff及23条改动源文件/SHA到`/tmp/plugin-status-recovery3-snapshot-01a1406d/`，不覆盖失败历史。

新root `533dc0c8-9e02-4397-ae54-0a8d9cd79c24`沿用同mission，最多8次子调用，原生恢复上述reviewer为`b6c9a9e0-0188-46af-be5c-8d717abe7ad6`。没有替换模型/CLI/checked-evidence、重复初始开发或豁免输出。`reviews/runtime-spec-6-recovered.json`已实际解析为3P1/BLOCKED。原thread 4605d433已超时，OWNER不冒称答复成功；实际回复新有效thread `0da14692-c945-484f-a868-4db4e441a64b`与`c97fffd2-b2f4-4302-adaa-b190836d5dc8`。

1. **P1，ERP锁后仍使用旧ORM对象。** authenticate_report在同session加载并强引用StatusInstance，lock_instance的FOR UPDATE未populate_existing。并发manual HOLD/首个advertiser CAS提交后，后到report虽拿锁仍用旧revision/scope；必须在统一锁owner刷新，而非另加状态缓存。
2. **P1，初始化总deadline仍不完整。** first read已guard，但initial commit、negative invalidate、scheduler stop、business clear及rebuilt commit仍可挂住ensureInitialized/runRecovery。要求从首await起统一90秒，逐真实deferred边界验收；Promise.race不等于撤销已发RPC，晚到值也不能授权。
3. **P1，rejected HOLD不是ACK许可。** current carrier检查已修历史case，但handleDecision对rejected HOLD/非accepted ledger返回false，caller仍以!settled清incident。原子storage guard仅查旧applied resume、running/pending及tuple，没有无superseding authority的正向证明。合法新鲜HOLD可在本地证据await后过期被拒，旧resume随即被错误确认，服务端仍hold同episode；须修canonical ledger/gate，不堆平行旗标。

### OWNER新候选执行证据与反例纠正

- R6修正前当前候选：`NODE_ENV=test npm test -- tests/plugin-status-chain.test.ts tests/plugin-status-storage.test.ts tests/tab-binding-controller.test.ts`为109/109；tsc exit0；ERP只通过isolated runner跑API，100%且ephemeral clone dropped。日志`/tmp/plugin-status-owner-recovery3-{plugin-focused,plugin-tsc,erp-isolated}.log`。这不覆盖上述新缺口或后续修正。
- 冻结全src/tests于`/tmp/plugin-status-owner-runtime-r6-01a1406d/plugin/`，保留原文件及SHA，派生`tests/owner-r6-clock-hold-probe.test.ts`。前两个变体错误加入非wire字段issuedAt，accepted=0而通过；它们只是前置校验拒绝，**不算反例RED**。已去掉错误字段，并给replacement runtime独立event ID，避免fixture复用UUID冒充新鲜carrier。
- 有效变体使用zero-skew、120秒合法HOLD，仅在真实await的monitorEvidence推进注入wall/monotonic130秒。Vitest2.1.8实际exit1、1 failed/6 filtered；sent2/accepted2，HOLD outcome=rejected，却execution=running、incidentId=null、pendingIncidentClear=false，失败断言expected null not to be null。日志`/tmp/plugin-status-owner-runtime-r6-expired-hold-valid-probe.log`。是冻结修后R5候选的真实模块/模拟I/O行为RED，非import、未await、HTTP拒绝或实际Chrome/ERP证据，不外推未来fix仍失败。
- ERP续修child `9221a1c8-3941-49a2-a844-37eb3e4c6d88`已交：canonical populate_existing、两个真实Session交错RED，报告93 API+18 models GREEN。仅worker证据，待新的spec/quality及OWNER核查。Plugin继续原MiniMax窄修；新probe发往7f937f58的follow-up `297e51e1-4929-4ac9-831a-6db4fca7b5e9`为queued，须后续handoff明确ack，不能当已消费。

## Runtime spec7 — 两项定向GREEN，三P1仍BLOCKED

Reviewer `14a699e9-389c-4b72-9f6d-c77e12427744`已产出`reviews/runtime-spec-7.json`，实际解析为3P1/BLOCKED。OWNER直接读当前函数与tests后有效回复`62f6becc-5547-422a-b905-9fa359793df5`及`6095ba1e-37de-491a-bc72-f2bfe75be6ce`；source-level发现不冒充reviewer执行测试。

- **初始化总deadline仍P1，源码并未改。** Plugin修正handoff `workers/runtime-recovery3-2-1.md`明确承认三条测试void，却称源码fixed on merits。OWNER比较R6冻结和R7实际controller，整个文件字节一致，SHA `05d652c85ec23d1dccb12d60b9379d863874c6e18e73d1ba22501760aec9f1f0`；ensureInitialized仍直接commit/invalidate/stop/clear。测试未证明目标await到达即推进clock，stop adapter根本没消费deferredPorts，可能只是先让read/list超时。不是“测试不足但源码已修”。
- **Action-start TOCTOU P1。** Runtime在authorize后await persistDecisionStarted再调用executor，没有重核deadline/当前epoch、nonce、enablement；controller只在入口捕获generation，不能察觉claim等待期间已经发生的失效，旧expected epoch也不在浏览器副作用前阻挡。terminal CAS不能撤销已发reload。需在canonical start/首副作用边界保留当前授权，并以真正进入的deferred started-write测试过期/失效后零reload/capture/start。
- **Cookie Store unavailable误判identity P1。** readCookieStore把浏览器读错误折成null，wake/proof及post-reload recheck在明确authorisedCookieStoreId时把它当IDENTITY_MISMATCH，timeout sentinel同样可被折错；没有证明别的身份却走conflict/require_user。需所有caller保留unavailable/timeout原因，仅成功读到不同store才identity conflict。

### 本轮OWNER直接验证

- `repository.lock_instance`的populate_existing与两Session测试获source复核；OWNER通过isolated runner以`-k 'concurrent_operator_hold or concurrent_first_advertiser_assignment'`只跑两个case，exit0、100%、clone dropped。日志`/tmp/plugin-status-owner-runtime-r7-erp-two-session.log`。不重开R6旧ORM问题。
- 将**同一个**合法expired-after-awaited-evidence的OWNER派生test放到冻结R7实际源码，Vitest exit0、1 passed/6 filtered；sent2/accepted2、HOLD rejected，但incident保留、pending=true。日志`/tmp/plugin-status-owner-runtime-r7-expired-hold-green.log`，源码/SHA在`/tmp/plugin-status-owner-runtime-r7-01a1406d/`。canonical ledger事务比较更高revision，关闭旧ACK缺陷；R6失败不移用本候选。
- R6 plugin handoff报告712/712、coverage95.88/92.66/97.49/95.88仍只是worker回归，三条void测试不算deadline证明。其陈旧“ERP未修”残余列表不压过实际源码和独立定向GREEN。
- 原writer续修。已向最新93fd会话排队follow-up `5b82e1b3-2d68-47f3-8b9f-a2b6c1d5fc2f`，要求真实running→bound tab失效进入wake边界、目标端口entered后才推进时间，并读回实际编辑/源码指纹；queued仍非消费证明。ERP no-change child `5ae4a69f-05e0-4fb4-83fa-d28b5ae6479f`已交，不当新功能或整体接受。

## Runtime spec8 — 原writer未完成，回归丢失，流程交回BLOCKED

最新plugin child `3b10dfa1-6cf2-4016-8a58-1d8de435b19c`的`workers/runtime-recovery3-5-1.md`明确**BLOCKED/INCOMPLETE**，不是DONE。其承认：上一轮初始化源码修复未落地；本轮仅部分加budget guard，controller指纹05d652c8变9843e9ec；stop case仍挂起，action-start及Cookie Store两项完全未动。三个目标回归虽报告timeout RED/部分GREEN，但因替换冲突出现TS1128后删除了整个三条初始化边界suite。最后709/709、coverage95.83/92.51/97.50/95.83、tsc/build GREEN只是worker结果，**删除回归后的绿色不予接受**。

Reviewer `86c46407-1d6e-44d2-a7ad-df996d8a0ec2`最终`reviews/runtime-spec-8.json`实际解析为3P1/BLOCKED。OWNER直接读当前函数后回复有效gate `7e47d711-8e7c-4138-a6d8-d2350b941109`：

1. 总90秒只部分修正。negative wake进入invalidateBinding后，stop fence到期分支仍直接await store read，正常分支read也不guard；releaseRefresh仍有无预算await。普通commit直接把binding/observation交异步port，Promise.race不撤销它，迟到状态写须有当前generation/epoch/authority保护。
2. Durable start claim后权限TOCTOU仍未改；必须在首个reload/capture/start前验证当前deadline、expected epoch、nonce及enablement，不能依赖终结时CAS。
3. Cookie Store throw/null/BUDGET_EXPIRED仍在wake及post-reload被当identity conflict；未知/transport不是verified different store，需保真原因并覆盖两条调用链。

恢复有效entered-boundary测试是额外明确的验收阻断，可并入deadline验证而不重复计同一P1。所报5秒timeout只属actor证据；删除源码后，不能仅凭超时推断它发生在recover await而非未到达的entered等待。必须恢复并真正证明端口进入，再推进clock并等待结局，释放晚到值后验证不写旧状态/不启动。不得skip、删除或弱化断言获得GREEN。ERP锁及ACK现有修正保持，不采用handoff残余的陈旧“ERP未修”列表。

### 终止状态与现场

- 同mission恢复root `533dc0c8-9e02-4397-ae54-0a8d9cd79c24`为**native complete、return.state=blocked**：已用7/8，尚余1，不足完整修复与独立复审。不是全部额度已耗尽，也不是本轮fetch/基础设施失败。没有启动quality、T5或额外writer，没有自增预算/替换模型/CLI。preflight提示是语义分组key与实际动态步骤key不相同的advisory，不作为验收证据或failed原因。
- 部分代码、当前所有测试、可用142/144/147/148/149/150/151日志、writer handoff、tracked patch及84条源码/测试SHA保存在`/tmp/plugin-status-owner-runtime-r8-01a1406d/`。原工作树保留未提交runtime，核心提交不变；无运行时stage/push或发布。
- Tower revision623将插件runtime按三P1+测试恢复阻断；revision624将ERP runtime按未完成的spec/quality阻断；T5仍依赖两者blocked，未触他人归属。
- 最新handoff确实ack此前297e51e1及OWNER有效ACK RED/GREEN的边界；这只证明消费消息，不代表三P1已修。F2/F3先实现后测试、vacuous测试、错误merits声明与删除回归的历史均保留。

## 用户批准的batch4续修（仅启动，未验收）

2026-10-16用户明确批准追加调用；新批次最多8次、concurrency1，不改写旧root7/8及blocked历史。采用三次串行原MiniMax保留会话微切片：budget/恢复回归、action-start、cookie-cause；每次edit前通过contact_supervisor请求OWNER核真实entered-boundary RED和canonical方案，GREEN后再核当前源码；之后fresh spec→quality。原native checked-evidence不变，另要求精确JSON handoff供caller验证，不以worker DONE代替review。

- TaskStart：docs e9c721c clean；两组件保留原WIP及HEAD，controller实际SHA9843e9ec。相同budget/gen/epoch/nonce在canonical mutation处约束迟到结果，不再声称race能取消已发I/O；内部port/CAS影响必须先请OWNER审查，无第二authority owner。
- 初次从docs worktree绑定旧mission被project-root lookup拒绝，没有run ID；fleet0active，三树diff/status/HEAD及脚本/packet保存于`/tmp/plugin-status-batch4-launch-block-01a1406d/`。改回原主仓协调cwd后同协议重试成功，不是模型或执行方式fallback。
- 当前root `5227c09e-aa16-4147-9f2c-6f6bb12951a6`已原生async启动，同mission；只等待原生求助/完成事件。尚无本批RED/GREEN、source修正或新接受结论。Tower625仅插件runtime恢复in_progress，ERP与T5仍blocked。
- Script `/tmp/monitor-status-workflow-batch4-01a1406d.js`，packet `/tmp/monitor-status-repair-batch4-context-01a1406d.md`；script validate0，动态调用数量由runtime max8实际约束，不伪称静态证明。

## 下一验证

当前批次从最新3b10dfa1保留的原MiniMax会话续修，不重复初始writer。先恢复有效测试并保留真实失败，再逐项处理完整90秒生命周期、start授权TOCTOU与Cookie原因，fresh spec→quality/OWNER后才考虑组件交付。保留已关闭缺陷回归，不以测试总数或单次GREEN替代因果证据。ERP只走isolated runner；T5共享storage/purpose/auth/model/migration/scheduler接线须明确交接，baseline、插件完整release及真实HTTPS/CORS/Chrome/通知仍未完成。
