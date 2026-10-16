# Runtime review — 当前技术裁决

## 接受边界

整体仍 **BLOCKED / 未交付**。纯核与十个微切片局部接受，不等于全部 callers/T4、完整预算、物理副作用、ERP 接线或发布通过。唯一协议：`docs/plugins/plugin-status-report.md`。旧 full/coverage/build、预审、HOLD和native complete不替代当前完整验收。

## 当前实现与唯一 owner

- storage 区分 raw absent/present/unknown，在实际 canonical mutation 点核验原 context 与同步 predicate，返回 typed `ObservationWriteResult`。
- runtime 原样传播 result/fence，统一 terminal+binding+report；producer只组合proposal，不另造状态缓存、authority或事务出口。
- controller 已迁移 snapshot、空 bootstrap、safe-stop、passive、invalidation、wake rebuild、resume及recovery成功/失败producer；保留首 await 前 generation、首 read scalar PRE tuple与同一budget。
- `applied`、enqueue `accepted`、执行/cleanup许可分离；已应用事实不因后续失权回滚。expired/unknown不是absence，null不是授权通配符。
- negative parent先同步消费，再合法self-revoke并固定own generation；不借子操作新nonce/gen，也不因自身撤权拒绝正常路径。durable失效事实先于business clear。
- resume复用既有snapshot原frame；recovery不能调用无budget的snapshot helper开无界前序，而是在原函数内最小复用scalar pattern，保持既有API。

## 第九微切片：recovery fenced producers

**局部接受 gate：`e3e236ab-5a0c-4e4a-83e0-268d261de471`。**

- controller：`dbc8c4c95642cb5de8b477cbec1a4610cbe6d09ef35e4e526a90faaf41d6efd1`。
- tests：`2065fb2fc6b5eb13bf56770ab490055513955b10c941a8f39ad36d21390783cb`。
- 原attempt generation/startBudget在首await前；first raw read即时观察拒绝，live I/O error仍传播。首read复制四scalar，nullable incident/optional nonce不重建。
- 同一个frame贯穿ready、required私有failed/expired参数；入口在ensure前、初始化后精确tuple比较、failed组合前、bind await后各拒绝已知失权/过窗，不返binding proposal。没有新增helper/公共type/port/import或producer持久化。
- 正常ready真实应用并入队binding_ready，保留incident、suspended/AWAITING_SERVER/closed gate；正常NO_CANDIDATE真实应用sticky失败及binding_invalidated，producer事先未写事实。
- OWNER实际 **39named PASS / product cwd本地unpipedtsc0**。最终tests的原七案在旧c281复现：**1实际bind业务RED+1失权proposal返回行为RED+5缺fence契约RED**，不能称两次实际mutation。
- 第一候选468虽36GREEN，OWNER独立复现later-probe撤权仍返proposal、bind await耗尽原窗仍返ready、firstRead耗尽后仍开第二canonical read；准确是2输出行为RED+1真实I/O边界RED。新增三案修正，并移除越界helper/纠正private参数与stale JSDoc后才接受。
- 五source-only控制均被真实结果杀死且各自tsc0：去原gen/重开budget使旧proposal canonical apply；借晚tuple/去已知mismatch均真实bind11；拒绝returned-ready fence使正常canonical apply false。不是type/setup失败。
- AST仅屏蔽runRecovery body与failed/expired（含local JSDoc）后，其余源码与接受基线c281逐字同；旧103 literal case body逐字同。测试仅10新案+明确授权的nullable bindEntryGate/armBindEntry/consume-before-await/reset，其他harness tokens及unarmed bind语义保留。其余src/tests/entrypoints/package/lock/config保持。
- 第九当次fresh active LSP两文件clean、0diagnostic，仅代表当次probe，不是当前四文件/workspace/full lint证明；不改第八历史inconclusive。

证据：`/tmp/plugin-status-owner-recovery-fenced-01a1406d/{checks.json,scope-proof.json,harness-proof.json,protected-root.json,current.log,current-tsc.log}`及五source控制日志；HOLD冻结及三私有RED：`/tmp/plugin-status-owner-recovery-fenced-hold-01a1406d/`。worker setup undefined-enter/type corruption为无效setup，不计RED，原日志保留。

root`a6e08374-6fc8-4538-9e13-01e04edc720c` / child`5233faed-09c8-48da-aac3-d528bf79dd7b`已terminal；七字段、两path、准确e3e gate已读核。Artifact中间468 hash多写字符且称equivalent，应以HOLD冻结真实`468660c37aa4afa0172763f161a4b3c601a2b9419831d830473a83eef761186a`为准；重复remaining中的“其余recover/failed producers”不是额外已证缺口，下一步应由mandatory-union inventory确认。原artifact不改写。

## 第十段局部接受：mandatory union

- 独立postGREEN完整gate`f038bef7-2b27-49c3-8592-085445c61658`。原writer root`fd998ff2-d905-438c-b126-c2db229734fe` / child`3afb1272-caf3-4d96-a940-ad6d0f36e232`已terminal，实际七字段/四path/完整gate已核；预审与triage不是DONE gate。
- storage现有test追加4个静态契约案；accepted source未改时，实际tsc仅2处TS2578 expected-unused directives，是真STATIC CONTRACT RED，不是业务mutation。Vitest4PASS只证明无害noop载体执行，非法shape也会传给本地noop，不称runtime拒绝。
- union保留两臂observation/runtime，不加kind tag；binding arm binding+fence required。observation-only不带fence是新静态约束：现有runtime对orphan fence会忽略，而非拒绝；不改runtime算法。
- OWNER私有type-only preview找到runtime测试8处非完整fixture：default/custom snapshot、3次reportObservation、3个executor commit。不是故意malformed负案；5个旧正案在accepted基线已失败，不算union引入。默认frame须取实际original seed（含incident:null/seed:null），不能泛用常量incident或借最新context。
- 实际仅storage type、controller既有commit required第三参数/directfence/local comment、storage载体/runtime八fixture。OWNER39 controller+4 carrier+12 distinct related runtime+另外两个真正typed-result case，共57去重named PASS；product-cwd实际unpipedtsc0。result regex的4标题案不是那两个typed测试的可靠选择器，不混称/重复计数。
- 两隔离static控制分别只报预期unused directive TS2578 at966/971；never/deny-all私有控制因非隔离narrowing诊断排除并保留，不算业务mutation。scope证明storage type/helper外源码精确、storage旧38body精确、runtime其余harness/case节点及全部expect/name不变、protected含chain保持。
- default原seed捕获在module/config setup awaits后、binding初写/snapshot交错前，保nullable incident/nonce definedness；seed:null observation-only，无bootstrap/absence权限，不借late context。fixture true predicate不证明production generation。
- fresh LSP四文件104项hint/warning/information、无error，不能称clean；actual tsc0另证。未授权whole runtime37/storage42/chain8保日志记scope breach，不追认。worker报告chain在candidate与第九冻结均8/8 STALE_CONTEXT；首失败实际fence/canonical PRE/因果未独立证明，snapshot pre-persist只是观察，不是已证根因。
- 证据`/tmp/plugin-status-owner-mandatory-union-01a1406d/`的owner-review/checks/scope-proof/control-dispositions及named/tsc/两个static-control logs；四完整hash见20-checkpoint.md。原artifact泛称“其余unfenced producers”不建立新缺口，helper实际含signature/body/comment变化，原件保留不改。
- Tower635：新增status-runtime-chain in_progress；callers blocked依赖它。诊断后另新门禁修复，不越过本micro保护的chain文件；本段接受不扩成全部callers或生产端口安全证明。

## 前八段技术证据索引

完整gate表与技术断点见`20-checkpoint.md`；早期逐轮过程不再重放。

- invalidation：OWNER22named/tsc0、五source控制，raw-read迟到拒绝unhandled1→0、live原error保留、真实fact/report-before-clear；证据`/tmp/plugin-status-owner-invalidate-final-01a1406d/`。旧handoff虚构gate按真实`64f2b158-82fd-42e2-87ca-7c60b33cd06e`纠正，原artifact保留。
- wake rebuild：OWNER25named/tsc0、旧三业务RED、own-gen/late-tuple及私有early-parent控制；保持fresh episode/discovering/INITIALIZING，证据`/tmp/plugin-status-owner-wake-rebuild-01a1406d/`。
- resume：OWNER29named/tsc0、1start业务RED+3缺fence契约RED及四source控制；证据`/tmp/plugin-status-owner-resume-fenced-01a1406d/`。私有nullable probe为invalid open-episode fixture，排除且保log；历史LSP实际0diagnostic/2inconclusive，不是handoff的2 hints。
- firstReadGate误清stopGate独立记录`f-b7b24e8c-ef9`，未修，不同时arm；不据此否定既有合法entered证据。

## Chain首阻塞独立诊断（不是修复接受）

OWNER首命名案在候选/第九各1FAIL/7skip；实际最早permission check记录live:true、expect:null、raw own-key:true/value:null、presence:unknown。新instance configure单次set写null，与absence-only bootstrap表示冲突；认领finding`f-57bdab7b-eab`。私有只去snapshot adapter预持久化仍同一失败，不能将其说成首拒绝根因。trace/trace-raw/no-adapter-prepersist各actualtsc0，未新增read/await且原predicate只调用一次；证据`/tmp/plugin-status-owner-chain-diagnostic-01a1406d/`。初始^selector零测试为INVALID保留，不算GREEN/RED。只确认最早机制，不宣称其后全部chain根因闭合。

原writer只读inventory root`ab494011-10ea-4a26-8e08-088d15b27121`续接原3afb native，workflow validate0；全产品/测试冻结。先审查配置/bootstrapping的canonical表示、原子性、同instance轮换事实和最小修复边界，再另tests-first/架构门禁。禁止靠清fixture、把raw null/unknown当absence或放宽fence制造假绿。不是新增writer/CLI或已批准产品修复。

只读inventory rootab494011 / child`c1083666-cc72-4c66-9dcf-09cb85a4dcf9`已terminal BLOCKED，实际七字段/空delta/空gate已核。triage`4793a99e-808d-42bc-b13a-fbcce455a969` HOLD Option A：官方StorageArea文档明确set遗漏key不受影响、remove是独立调用，不存在已证的“一个set删除旧binding”方案。不得猜undefined/null删除或set/remove原子性；官方页提取是文档证据非live浏览器。还须检查移除后重开absence是否允许old在途expect:null操作恢复OLD instance。

现原writer只读corrected-design root`437688b5-70fc-4d60-a633-82a19cf9791e`续接c108，workflow validate0；只比较现有canonical schema的有效非执行初态/维护中的reset与构造器复用，审查原子性、旧操作反重放、轮换事实、owner-fit/最小测试矩阵。没有实现或测试修改权限。官方引文在`/tmp/plugin-status-owner-chain-diagnostic-01a1406d/api-contract.md`，原artifact不改。

## 尚待关闭的技术缺口

| 范围 | 所需实现与证明 |
| --- | --- |
| Chain恢复handoff | 最小命名复现，记录实际original fence/canonical PRE/liveness，单变量判定fixture或生产owner，再新tests-first/pre-edit门禁；不预设snapshot预写即首拒绝原因 |
| 原90秒窗口 | 所有ports/两个refresh-release/晚到值共用原budget，不把Promise.race说成取消I/O |
| 实际业务写点 | bind/clear/scheduler自身await后的实际mutation点消费原fence；本次fact fence不解决潜在late physical bind |
| Action-start TOCTOU | authorize→await persistDecisionStarted后首副作用重查deadline/epoch/nonce/enablement；terminal CAS不能撤销已发副作用 |
| Cookie原因保真 | throw/unavailable/timeout不等verified different store，wake/post-reload保原因 |
| Fresh reviews | 精确新candidate先spec后quality，修后重spec，不借旧gate |
| ERP / shared / T5 | 最终isolated baseline、明确purpose/auth/model/migration/scheduler/storage/GUI/MAIN/metadata/分页handoff、真实HTTPS/CORS/Chrome等分层验证 |

ERP populate_existing真实两Session交错、canonical ACK更高revision/superseder拒绝已验证，不重开；purpose缺失failclosed，不借普通RW或新registry。

## 后续执行纪律与历史

原MiniMax唯一产品writer、原cwd/profile/native checked contract；OWNER独立验收/协调/Git。每段有效RED→新预审WAIT→最小修复→相关GREEN/实际tsc→独立新postGREEN。不得触生产迁移/重启、真实凭证、TikTok写或真实通知。当前不跑full/coverage/build；最终exact候选另行统一执行。

完整早期历史保留在Git提交`1f90588477314f59b0d1fa41308abdcefc00bb7c`、原native artifacts、OWNER冻结目录与`docs-before-condense/`；不删除或改写原门禁、失败与产物。
