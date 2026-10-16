# 通用插件状态与单目标恢复 — 当前技术断点

- Task ID: `2026-10-15-plugin-status-implementation`。
- Current todo: Tower635新增`status-runtime-chain`为`in_progress`，`status-runtime-callers`明确`blocked`依赖它；不是T4或全功能完成。
- 最新局部接受：第十`mandatory-commit-union`，独立postGREEN完整gate`f038bef7-2b27-49c3-8592-085445c61658`。root`fd998ff2-d905-438c-b126-c2db229734fe` / child`3afb1272-caf3-4d96-a940-ad6d0f36e232`已terminal，实际七字段/四path/完整gate已读核。预审/triage均不是该DONE gate。
- Blocked on：chain实际STALE_CONTEXT、aggregate budget/实际业务写点fence、action、Cookie、fresh spec→quality、ERP/T5；无当前full/coverage/build或发布验收。
- OWNER现已独立复现首chain命名案：候选/第九各1FAIL/7skip；首次实际check为live:true、expect:null、raw own-key:null→presence:unknown。configure新实例单次set写binding:null，与absence-only bootstrap相冲突，记录并认领`f-57bdab7b-eab`。私有仅移除snapshot adapter预持久化仍同一首拒绝，三诊断variant各tsc0；故不能将预写说成首次根因。只证最早机制，未遍历后续chain/关闭整体root。初始^selector零测试为INVALID，日志保留。
- 原writer只读诊断/设计root`ab494011-10ea-4a26-8e08-088d15b27121`续接已接受的3afb native；packet`/tmp/monitor-status-chain-provision-inventory-01a1406d.md`、script`/tmp/monitor-status-workflow-chain-inventory-01a1406d.js` validate0。全产品/测试冻结，先联系OWNER审查canonical表示/原子性/最小tests与owner-fit；本workflow不授修复权限，不能用清fixture/null-as-absence掩盖provision问题。

- 只读inventory rootab494011 / child`c1083666-cc72-4c66-9dcf-09cb85a4dcf9`已terminal BLOCKED，实际七字段/空changedFiles/空gate已核。triage`4793a99e-808d-42bc-b13a-fbcce455a969`未批准其“一个set删除binding”Option A；[官方StorageArea文档](https://developer.chrome.com/docs/extensions/reference/api/storage/StorageArea)明确set只更新所列key，其他pair不变，remove是独立方法，不能把遗漏/undefined当删除或虚构set+remove原子性。官方页面提取成功，直接ctx fetch超时另记，不是live浏览器证明。
- 现原writer corrected design只读root`437688b5-70fc-4d60-a633-82a19cf9791e`续接c108，packet`/tmp/monitor-status-provision-reset-design-01a1406d.md`、workflow validate0。比较既有schema的有效非执行初态原子落位/真正可复用reset方案，需证明old非null绑定及old在途absence操作不能跨新namespace复活；产品/测试仍全冻结，未授tests/source修复门禁。

## 技术方案与不可变约束

唯一协议：`docs/plugins/plugin-status-report.md`（批准基线`fb42e16575a241fec88740d69137ae6a8153c2bb`）。

1. 单Seller/Advertiser/Cookie Store、单origin，只扫描已有允许页面，不自动建页、登录或扩多店。
2. ERP status domain拥有实例/审计/投影/决策/outbox；插件controller拥有生命周期，runtime/storage拥有唯一terminal+binding+report原子出口。
3. 首await前generation、首canonical read scalar PRE tuple、同一budget不得重借；expired/unknown不是absence，null不是授权通配符。
4. negative parent先同步消费许可再合法self-revoke，固定own generation；不借新context，也不因自己自增拒绝所有正常路径。
5. `applied`、enqueue `accepted`、执行/cleanup许可分离；拒绝/未知不proposal、不clear；已应用事实不回滚。durable失效事实先于business clear。
6. 已知失权在入口/结果组合owner拒绝proposal，不能只检查一次前缀。physical bind/clear/scheduler自身awaited mutation fence仍未完成，fact fence不等于物理写点fence。
7. Strict TDD、ArchitectureReviewRequired=yes；真实entered后交错，主Promise创建即观察，try/finally无条件release并await。setup/type/timeout/recorder不包装成实际mutation。

## 已局部接受的实现（十段）

| 微切片 | 已验证责任 | 最终OWNER gate |
| --- | --- | --- |
| runtime-result | typed result/fence原样返回，拒绝不dispatch、正常真实dispatch | `b32a5224-0cff-4490-a843-40c98c1a5968` |
| snapshot-producer | 原generation/首scalar PRE，不借初始化后授权 | `4bc563c0-e586-4e60-b84a-5bef4f58b69d` |
| bootstrap-typed | 真实applied/refused；raw null bootstrap原fence；application与acceptance分离 | `8709ea55-9c3a-44a5-8657-b806c5522916` |
| safe-stop-producers | HOLD/requireUser同步撤权、即时read/stop、原tuple拒绝迟到写 | `330af378-658c-4b2c-a0c4-1c71a98c45b1` |
| passive-observation | 撤直接预写；原generation/PRE/budget交canonical，expired failclosed | `ed21c35a-f31d-4975-9e47-7c8d62adaf5e` |
| invalidate-canonical-fact | 原parent tuple/budget、合法自身撤权、真实fact-before-clear、raw rejection观察 | `64f2b158-82fd-42e2-87ca-7c60b33cd06e` |
| wake-rebuild-producer | 原bootstrap许可先消费、own generation、discovering新episode事实先clear | `55f416c9-a968-47c1-a3cb-5d1bddcc1730` |
| resume-fenced-producer | 复用snapshot原frame，已知tuple不符先拒绝start，原fence交唯一出口 | `f997085d-f73a-4dda-bbfe-56c56dac68f8` |
| recovery-fenced-producers | 原attempt/首scalar/同budget全传required frame，已知失权不proposal，正常ready/failure真应用 | `e3e236ab-5a0c-4e4a-83e0-268d261de471` |
| mandatory-commit-union | type强制observation-only或binding+fence；required既有helper；八正向fixture原seed迁移 | `f038bef7-2b27-49c3-8592-085445c61658` |

## 第十段当前技术决策

- union两臂都保observation/runtime；observation-only显式binding/fence undefined，binding分支两者required，不加wire kind/tag/framework。orphan fence原runtime会忽略，静态禁止是新硬化，不称已有runtime拒绝。
- 真实静态RED：accepted source未改时，storage test仅两处expected-unused @ts-expect-error TS2578；Vitest四案PASS只是载体执行，不是类型证明。无效重复import已修且不计RED。
- OWNER独立type-only私有preview确认8处runtime fixture需迁移：默认/custom snapshot、3次reportObservation、3个executor-return commit。它们不是故意malformed负案；5个相关旧runtime正案在accepted基线已FAIL，独立记录，不算union引入。
- 仅storage type、controller commit required参数/directfence/local comment、storage载体及runtime八fixture；算法/runtime/source其他方法不改。共享default frame须复制原seed options.state（含healthy case incidentId:null，state:null是真absence），不硬编码incident或借late context。fixture true predicate不充当生产代次/交错证明。
- OWNER实际39 controller + 4 carrier + 12 distinct related runtime + 另外两个真实typed-result case = **57去重named PASS**；product-cwd实际unpipedtsc0。`result` regex选择4个旧result标题案，不等于两个实际typed-refusal/live-fence案，不能错标或重复计数。
- 两隔离type控制分别只报TS2578 at966/971，验证binding无fence与orphan fence的静态排除。never/deny-all私有控制有非隔离narrowing诊断，保留但排除，不拿作业务/物理mutation证明。
- scope：storage type外、controller commit helper外源码逐字同第九；storage旧38 case body精确；runtime除buildHarness及五原case外逐字同，所有expect calls及case names不变；protected src/tests/entrypoints含chain/runtime implementation保持。
- 原seed捕获在module/config setup awaits后、binding初写及snapshot/case交错前，不称字面“所有首await前”。保incident:null与optional nonce definedness，seed:null返回observation-only；fixture true predicate不是生产代次证明。
- 当前四hash：storage`ffe6cc65748d8ec73f79be7455f827327adc87984842e8c13fa83fd43b5b18b0`；controller`b8cdbf862b9a4fb7259549c4f48596671d430b147287c2b0dfece18489f1d0c2`；storage test`ef3336b99e44cfc01161ce53352416a2c52cf6702290b2d72233362fae874d9e`；runtime test`11b43257ed7ae8d889750d32d6a55b2c31dec0a9d34f446bce1cfc8a5f86881d`。
- fresh LSP四文件104项hint/warning/information、无error，**不是clean/0diagnostic**；actual tsc0另证。未授权whole runtime37/storage42/chain8保日志并记scope breach，不追认。worker报告candidate/第九冻结chain均8/8 STALE_CONTEXT，实际首次fence/PRE/因果仍待独立诊断，不能据stack宣称snapshot预写就是根因。
- OWNER证据`/tmp/plugin-status-owner-mandatory-union-01a1406d/{owner-review.md,checks.json,scope-proof.json,control-dispositions.json,controller.log,carrier.log,runtime.log,runtime-typed-two.log,current-tsc.log}`；RED/preview日志沿用。原native artifact不改写；其中“helper signature only”应读为signature/direct fence body/local comment，“其余unfenced producers”泛称不建立新缺口，以已核inventory和明确剩余项为准。

## 第九历史冻结与验证（第十仅覆盖helper）

- controller：`dbc8c4c95642cb5de8b477cbec1a4610cbe6d09ef35e4e526a90faaf41d6efd1`。
- tests：`2065fb2fc6b5eb13bf56770ab490055513955b10c941a8f39ad36d21390783cb`。
- 仅runRecovery body、原private failed/expired及local JSDoc；无新helper/公共API。snapshot helper无budget参数，recovery本地最小scalar适配，不开无界前序/不改helper。
- 首read即时观察raw拒绝；入口在ensure前查liveness，初始化后比精确tuple；failed组合前及bind await后查完整原frame。正常ready保incident、suspended/AWAITING_SERVER/closed gate，正常NO_CANDIDATE保sticky失败；两者实际canonical应用/入队，producer未先写事实。
- OWNER39named PASS，actual product-cwd unpipedtsc0。原七案对旧c281复现：1实际bind业务RED+1失权proposal行为RED+5缺fence契约RED，不称两实际mutation。
- HOLD468虽36GREEN，OWNER发现later-probe撤权仍返proposal、bind await过窗仍ready、firstRead过窗后第二read；准确2输出行为RED+1真实I/O边界RED。三新增案及越界helper删除后才接受。无效undefined-enter/type setup日志保留，不计RED。
- 五source控制：去gen/重开budget使旧proposal真实canonical apply；借晚tuple/删known-mismatch各实际bind11；拒绝returned-ready fence使正常canonical apply false。各variant实际tsc0，非type/setup kill。
- AST屏蔽三既有函数后，其余源码与接受基线c281逐字同；旧103literal body逐字同。仅10新case+授权的nullable bindEntryGate/armBindEntry/consume-before-await/reset，其余harness tokens/unarmed bind保留。protected src/tests/entrypoints/package/lock/config不变。
- 第九当次active LSP两文件clean、0diagnostic；不代表当前四文件/workspace/full lint，不改历史第八inconclusive。复杂度压力披露，不在本scope抽取重构。

证据：`/tmp/plugin-status-owner-recovery-fenced-01a1406d/`的checks/scope-proof/harness-proof/protected-root及current/tsc/五controls logs；HOLD和私有三RED在`/tmp/plugin-status-owner-recovery-fenced-hold-01a1406d/`。root`a6e08374-6fc8-4538-9e13-01e04edc720c` / child`5233faed-09c8-48da-aac3-d528bf79dd7b`已terminal。产品changedFiles仅controller/test两path。

报告errata：第九artifact把中间held hash多写字符；真实是`468660c37aa4afa0172763f161a4b3c601a2b9419831d830473a83eef761186a`，以冻结为准。其重复remaining所称额外recover/failed不算新已证缺口，下一inventory确认；原artifact不改写。

## 前段证据与已知fixture边界

- 第六：22named/tsc0、五source控制、原budget/generation/tuple、真实fact-before-clear；迟到read unhandled1→0，live原error保留。`/tmp/plugin-status-owner-invalidate-final-01a1406d/`。
- 第七：25named/tsc0、旧三业务RED、own-gen/late-tuple及私有early-parent控制，保fresh episode/discovering/INITIALIZING。`/tmp/plugin-status-owner-wake-rebuild-01a1406d/`。
- 第八：29named/tsc0、1start业务+3缺fence契约RED、四source控制；复用原snapshot frame，不存terminal。`/tmp/plugin-status-owner-resume-fenced-01a1406d/`。私有nullable probe为invalid open-episode fixture，排除且保log；当次LSP0diagnostic/2inconclusive，不是handoff的2 hints。
- 第六旧handoff误展开gate，真实接受是表中完整64f2b158监督回复；原错误artifact保留，后续UUID逐字复制。
- firstReadGate误清stopGate独立记录`f-b7b24e8c-ef9`，未修，不同时arm；不以无效timeout否定既有合法entered证据。

## 执行与交付边界

- 原MiniMax M3.1 Flash Preview唯一产品writer；OWNER不代写，不换writer/model/CLI。只按原生通知续接，不轮询；8/12/max1是内部配置不是用户/provider数字额度。
- Plugin：`/home/schan/pi-workspace/monitor-status-01a1406d`，`feature/plugin-status-auto-bind`；core718c83e已push，runtime WIP，v0.4.26。
- ERP：`.worktrees/plugin-status-erp`，`feature/plugin-status-erp`；coref2c0fa7已push，锁populate_existing真实两Session/ACK高revision拒绝已验证，不重开。
- Docs：`.worktrees/monitor-auto-bind-status`，`docs/monitor-auto-bind-status`。主仓foreign gitlink/node_modules不碰；共享热点须明确handoff。
- Tower revision635：callers blocked依赖新增chain诊断项in_progress；之后budget→actions→cookies→spec→quality。只有真实status/deps改变才更新板，其余实质进展用talk。
- 下一先最小命名chain复现/实际fence与canonical PRE诊断，经独立tests-first/pre-edit门禁才修；不预设去掉snapshot预写可解决首拒绝。再关90秒全ports/两release/physical mutation、action TOCTOU、Cookie原因、fresh spec→quality、ERP isolated baseline/共享purpose/auth/model/migration/scheduler/GUI/MAIN/metadata/分页、T5/browser。
- 当前无full/coverage/build/最终ERP baseline、真实浏览器/HTTPS/CORS/TikTok/通知验收，未合并发布。禁生产迁移/重启/真实凭证/真实写操作。完整验证与prospective merge另执行，不提前记完成。

## 历史范围

仅保技术方案、当前实现、验证边界；早期过程在Git提交`1f90588477314f59b0d1fa41308abdcefc00bb7c`、原native artifacts、OWNER冻结及`docs-before-condense/`，未删除或改写原始证据。
