# Runtime review — 当前技术裁决

## 接受边界

整体仍 **BLOCKED / 未交付**。纯核与九个微切片局部接受，不等于全部 callers/T4、完整预算、物理副作用、ERP 接线或发布通过。唯一协议：`docs/plugins/plugin-status-report.md`。旧 full/coverage/build、预审、HOLD和native complete不替代当前完整验收。

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
- fresh active LSP两文件clean、0diagnostic，仅代表本次probe，不是workspace/full lint证明；不改第八历史inconclusive。

证据：`/tmp/plugin-status-owner-recovery-fenced-01a1406d/{checks.json,scope-proof.json,harness-proof.json,protected-root.json,current.log,current-tsc.log}`及五source控制日志；HOLD冻结及三私有RED：`/tmp/plugin-status-owner-recovery-fenced-hold-01a1406d/`。worker setup undefined-enter/type corruption为无效setup，不计RED，原日志保留。

root`a6e08374-6fc8-4538-9e13-01e04edc720c` / child`5233faed-09c8-48da-aac3-d528bf79dd7b`已terminal；七字段、两path、准确e3e gate已读核。Artifact中间468 hash多写字符且称equivalent，应以HOLD冻结真实`468660c37aa4afa0172763f161a4b3c601a2b9419831d830473a83eef761186a`为准；重复remaining中的“其余recover/failed producers”不是额外已证缺口，下一步应由mandatory-union inventory确认。原artifact不改写。

## 第十段进行中：mandatory union

- 原writer root`fd998ff2-d905-438c-b126-c2db229734fe` / child`3afb1272-caf3-4d96-a940-ad6d0f36e232`。预审`3e72d2d8-cd2e-4526-b2ef-9c4d52059df1`仅批准最小实现，尚无postGREEN。
- storage现有test追加4个静态契约案；accepted source未改时，实际tsc仅2处TS2578 expected-unused directives，是真STATIC CONTRACT RED，不是业务mutation。Vitest4PASS只证明无害noop载体执行，非法shape也会传给本地noop，不称runtime拒绝。
- union保留两臂observation/runtime，不加kind tag；binding arm binding+fence required。observation-only不带fence是新静态约束：现有runtime对orphan fence会忽略，而非拒绝；不改runtime算法。
- OWNER私有type-only preview找到runtime测试8处非完整fixture：default/custom snapshot、3次reportObservation、3个executor commit。不是故意malformed负案；5个旧正案在accepted基线已失败，不算union引入。默认frame须取实际original seed（含incident:null/seed:null），不能泛用常量incident或借最新context。
- 允许改storage type、controller commit required第三参数/directfence/local comment、storage载体/runtime八fixture；旧39行为保护。最终须指定named/实际tsc0/独立新postGREEN，不能借九段完成名义扩大到其他业务修复。

## 前八段技术证据索引

完整gate表与技术断点见`20-checkpoint.md`；早期逐轮过程不再重放。

- invalidation：OWNER22named/tsc0、五source控制，raw-read迟到拒绝unhandled1→0、live原error保留、真实fact/report-before-clear；证据`/tmp/plugin-status-owner-invalidate-final-01a1406d/`。旧handoff虚构gate按真实`64f2b158-82fd-42e2-87ca-7c60b33cd06e`纠正，原artifact保留。
- wake rebuild：OWNER25named/tsc0、旧三业务RED、own-gen/late-tuple及私有early-parent控制；保持fresh episode/discovering/INITIALIZING，证据`/tmp/plugin-status-owner-wake-rebuild-01a1406d/`。
- resume：OWNER29named/tsc0、1start业务RED+3缺fence契约RED及四source控制；证据`/tmp/plugin-status-owner-resume-fenced-01a1406d/`。私有nullable probe为invalid open-episode fixture，排除且保log；历史LSP实际0diagnostic/2inconclusive，不是handoff的2 hints。
- firstReadGate误清stopGate独立记录`f-b7b24e8c-ef9`，未修，不同时arm；不据此否定既有合法entered证据。

## 尚待关闭的技术缺口

| 范围 | 所需实现与证明 |
| --- | --- |
| Mandatory union / inventory | 类型强制observation-only与binding+fence，审查全部真实producer/consumer，不再保留optional unfenced默许 |
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
