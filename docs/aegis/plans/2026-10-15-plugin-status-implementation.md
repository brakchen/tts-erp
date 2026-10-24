# 通用插件状态与 monitor 单目标恢复实施计划

## 当前执行快照：877双Alarm启动组、0bff原Runtime raw归属已push；完整生命周期仍开（2026-10-24）

- **Source `87718fa57520777b9b5297d5b166111effdf28ba`**（前置 `0bff86641dd0949342331cebd727da3d5624d405`）。原Runtime可同步登记任务再启动，控制结束不早释已知raw，复用同一CycleLease/pending/inFlight而非新owner。managed启动固定SCAN＋NOTIFICATION各自cadence，实际stored root/source/leaf/pin＋原clock下单次admit紧邻无await双native调用；逐一观察、allSettled排空，partial失败不报fully running，展示字段各自原source/value/presence CAS。
- BG显式ctx不再走human enable/bind/reload；复用原高层settingsSaveQueue串行managed组与legacy公开create，私有queued-save避免自锁，short业务queue不等待native。原callback/fence/native receiver必须在高层queue handoff前拥有：额外1业务RED/type0证实迟捕获会借替换callback，已在同薄adapter修；传既有orchestration callback不是新队列或授权令牌。
- **验证**：原adoption 2＋3业务RED/type0→4相关335/type0；双组6业务RED/type0、partial四排列/两字段CAS/展示错误/实际Runtime applied或failed终态、late-callback1RED；fail-fast控变杀4例/type0，精确own working SHA还原。最终精确候选5相关文件 **533PASS/no skip/fulltsc0**，不是335＋533或全插件/live验收。LSP零reported但3inconclusive。`1943dfe` genuine clear→NEW recover/新Controller五矩阵与native255/type0亦已交付，不再列尚未执行。
- **真实剩余**：source-owned PRE/own-post stop/noPRE fact-only，独立negative/HOLD事实不等stop；ACK-capable canonical running/applied-resume tick，stop/tick实际raw-adoption接线；所有legacy clear/disable/bind/retry与单scan兼容seam的资源顺序/Worker终止；PageFactory/Store/document/one-read script/staged capture/strict分页/manualAtoB/repair/两config/ERP/E2E。这里只窄B，整体C/原15仍4done11open，caps[]executors{}/0.4.27不变，不ready/业务merge/生产/发布/父指针。
- **协调恢复**：原c397进程丢失及a567 key校验失败留证，同协议精确失败run恢复70b9已completed，d726 Scheduler92行＋3186 ERP86行全读及实际receipt核；咨询非Source/独立执行批准，完成作业不再恢复。ERP审计task完成、Scheduler实施仍in_progress，两infra blocker已解除。peer模板0069/24点与infra/v16释放已收，不再等旧writer；eba0071实际merge/scope/runner-helper-guard/schema/auth和协调isolated刷新仍是Parent pre-DB gate。旧config/perf core未来写域未回交，已明确询问用户，不擅改旧WIP。

### 历史：f39单扫描Alarm入场与展示事实修复（以下为当时断点，不是当前归属/未实现判断）

Source **f39e6a753b30cf987a0895934202ebf722216cab**（原生入口69d6169）：显式BG managed scan通过薄适配器，在原业务队列内复核真实started root/receipt/FP/leaf/NS/GEN/clearing、canonical绑定/config范围/business tab/monitorEnabled/严格pin，最后消费原clock/single-admit并同步调用真实chrome.alarms.create。原生Promise立即观察、嵌套返回，队列不占其原始等待；创建完成后的nextScanAt仅是原source＋原字段value/presence CAS条件展示事实，无新权限/owner/key/queue/token/window。

3个现有API业务RED/type0驱动实际入口，12个SOT矩阵、原生等待中队列可用/迟到保护、真实Runtime→BG→native→终态均验证。临时只删SOT拒绝return、保真实读取/clock，10负例FAIL/2对照PASS/type0，精确备份SHA还原。fresh51f/06f静态窄审48行全读，两个P2经真实2RED/type0修：展示SET失败不能让已创建native事实变RECOVERY_FAILED；旧创建迟到不得覆盖不转GEN的新legacy时间。修前一次15文件980PASS/type0；修后最窄两文件431PASS/type0，不累计982/全插件，不冒独立执行或f39再次独立接受。

**剩余**：完整managed Scheduler Factory（缺ctx/expectation拒绝）、通知/tick gate、pending原生资源顺序/多managed重叠/Worker终止、实际CookieStore/document/PageFactory/capture/分页/manualUI/repair/两config/ERP/E2E；值CAS不是完整native排序或新grant。原15仍4完成11open/caps[]executors{}/0.4.27，无ready/业务merge/生产/发布/父指针；ERP模板/v16只peer01a16719写。网络临时失败留证并原接口有界重试，fetch首次成功/quota week30%。

### 当前后继执行约束：复用Runtime raw lease承接Scheduler任务

- 用户要求持续完成原monitor/status/两config计划，非全共享板。Scheduler完整契约咨询92行与ERP前置审计已由同协议精确失败run恢复后交付；只是设计/前置证据，不是代码验收。Parent保持Source/Git/最终gate唯一owner。
- **Change Necessity / code-change**：现有runReportCycle把重叠报告丢弃，无法在tick/清理启动前登记其实际raw生命期；不应给Scheduler新建资源owner/队列/token。扩展原Runtime入口承接显式同步task factory，共用原CycleLease.pending/inFlight；不授予任何SOT/clock权限。
- **契约**：保留无task参数的report overlap行为。显式task factory在任何任务I/O前由原lease登记观察过的control代理；实际work和必需physicalSettled都登记，控制提前结束/reject不早释raw。已有report lease只加入raw归属，不借其过期wait能力；tick-only使用同一类型lease并在控制结束后等待全部raw才释放。任务本身仍必须在实际副作用前验证原source/root/clock，raw adoption不是grant。
- **严格TDD**：Mode auto / Decision strict / authority插件AGENTS。先以当前单参runReportCycle合法结构化可选第二参复现task factory被忽略的业务RED/type0（不导入缺失API、不用类型/超时作RED），再允许接口/owner局部实现；补同步登记/加入既有lease/失败兄弟raw未决/控制先结束/后继报告可恢复等反例。新独立tests/plugin-status-runtime-adoption.test.ts不导入保护fixture。
- **Owner-fit / pressure**：Runtime本来就是raw cycle owner，局部扩入口/提取相同lease初始值，不加竞争owner；当前约1200行，新增约60行处于at-risk，限现有生命周期职责，不塞业务准入或Scheduler策略。新测试独立文件，background/Controller此步不改。
- **验证与边界**：named adoption + full tsc，再原Runtime/terminal/drain相关；无production/caps激活、tick原生权限、完整Factory或全部任务完成声称。后继共享Alarm的legacy顺序、单admit精确双create组/all-settled、original PRE/own-post stop与独立事实、canonical running/applied-resume tick仍须各自真实反例。ERP实际merge/runner/schema/auth readback及isolated验证另按前置gate执行。

### 当前后继：精确双Alarm组与现有高层队列的创建顺序

- 原生边界已有6个current-API业务RED/type0：合法调用现有setMonitorEnabled额外原expectation，live未消费准入、旧human分支在storedGEN变化/原clock过期/用户已关闭时仍创建两alarm甚至重开用户开关；第一native raw未决时第二调用未发出；legacy同名create越过未决managed create。最初findLast超出ES lib的type2已仅修测试，不充RED。
- code-change：在原薄scheduler adapter统一精确plan，单scan历史入口保留；新monitoring组固定SCAN+NOTIFICATION各用原cadence、单admit后无await调用两次、逐一立即观察sync throw/async raw Promise、allSettled全排空后才返回完整成功，partial native事实不伪装fully running。每字段仍original source/value/presence CAS，展示I/O不否定native事实。
- BG只显式expectation走managed组，绝不复用human开关/绑定/reload逻辑。managed组与legacy公开创建入口复用已有settingsSaveQueue，不建资源队列；short canonical storage queue只覆盖SOT/JIT/native invocation，不持有raw等待。现有已在高层队列内的settings-save私有body用未包队列的scheduling operation避免自锁；公开saveSettings统一从同一高层入口进入。导入仍由原高层owner串行，原业务serializer不改。
- 验证：6指定用例+full tsc，再partial原生各throw/reject/held sibling、两字段展示CAS、原background/Runtime/native/drain回归；任何生产Source前需Parent精确gate。当前完整Factory仍C：既有单scan兼容seam、legacy clear/disable/bind路径的全资源顺序、original-PRE/own-post stop/独立事实、ACK-capable tick、raw adoption实接线尚需单独gates，不把一条create顺序例宣称全coexistence闭合。capabilities/executors继续关闭。

### 历史：1f9/19b8原resume时钟与终态运输

Source最新 **19b8b11acd0b0e613ccee97d5715fe62360b5d15**（生产算法 **1f9bd7cddf0e27e15bd6fcc1690103e2d17c1e03**）。原request clock callback/domain/deadline与root/source在首await前拥有，managed resume匹配完整命令身份；Scheduler expectation只是原数据与一次消费准入，不是新grant。consumer在自身await后消费原clock/gen，未消费不得applied，已启动迟到事实仍可结算而不能重复启动。managed结果marker来自原continuation，修复真实Runtime有效启动却被终态记stale的producer缺口，不在Runtime造latest marker。恢复原clock纯predicate提取保持旧语义，无新90秒窗口。

严格TDD：6当前API业务RED/type0、2身份RED/6controls/type0、1重复admit RED/type0，真实Runtime正例暴露缺marker后producer修；最初null参数/type2仅fixture错误已排除。生产一次 **14文件778PASS/no skip/fulltsc0**；独立2b8554/cabc窄审27行全读，无确认引入代码缺陷；Medium迟到Runtime终态、Low未消费端口/post-admit撤权覆盖由Parent另3controls补证，最后仅native整文件 **230PASS/type0**，不是781合跑／全插件／独立执行。实际已启动HOLD保native1/旧安全stop2及新事实，不冒新旧Scheduler重叠条件清理。

**下一步仍是实际Scheduler Factory在自身await后验证真实存储started root/source/leaf/raw pin并消费原clock紧邻native调用**。目前只有运输与合格注入consumer时钟见证；DTO不是授权、contextless legacy不是managed证明，实际Factory须拒绝缺expectation。再做完整nextrecover矩阵、PageFactory/capture/document/分页/manualUI/repair/两config/ERP/E2E。原15仍4完成11open/caps[]executors{}/0.4.27，无业务merge、生产或发布；ERP模板/v16只peer01a16719写。fetch失败保存断点并原接口有界重试，不切协议/模型/SDK。本轮Git fetch首次成功、quota available week27%。

### 历史：9604启动/resume消费者与8c二次核验回归

Source最新 **8c724dbe1d8ff26d7044ba6726de1db3eba4eb38**：生产9604不再直接复用suspended READY，严格pin只限制positive reuse、不抹去已观测noTab/login；新resume在Scheduler API调用前二次证明实际Store/原fence。genuine READY→newresumeACK/start的5RED1control/type0，另2独立事实RED/type0驱动修补；14相关755/type0。fresh7e8/e119窄审33行无确认代码缺陷、指出Medium隔离覆盖缺口，后加6controls，控变禁用拒绝return杀5负例/1正例/type0后原Source SHA恢复，最终native整文件210/type0；8c仅测试，无761/全插件合跑声称。旧泛型oracle保留并明确配置端口数据，越界reader扩域实验已撤回，不用泛型mock充native授权。

下一步保留实际scheduler adapter after-await JIT/原resume clock运输、nextrecover持久矩阵与PageFactory/capture/两config/ERP/E2E；只证明API调用前校验，不称真实Scheduler原生动作已受保护。原15仍4完成11open/能力关闭/版本未改/无发布、业务merge或父指针。fetch/quota临时失败按原接口留证重试、不断片改协议。

### 历史：4dad原生锁定与组合/终态边界

当前Source **4dad71cb78a734f276aff303cf88b7025e3d1374** 已push：原生pin+业务+GEN+own leaf单SET，显式CAS数据非grant；managed完整唯一Seller/Advertiser/Store组合先于tab偏好、未知竞争者拒绝bootstrap、Store重读一致；positive terminal严格pin相等，negative fact settlement不被全局pin gate绑住。冻结1158 fresh review39行已全读，两P2经2业务RED/type0修receiver与getter-callback顺序；bootstrap3RED2controls和terminal4RED3controls各独立type0后修。最后 **12相关692PASS/fulltsc0**，并非全插件/整体验收。扩展profile超时/3个direct旧profile失败均留痕，仅合法reader/真native预pin fixture迁移，旧oracle不弱化；LSP有2未改行辅助error/3inconclusive，不冒allclean。

下一步：genuine READY/新命令下startup positive reuse、resume实际Store/pin一致以及next-command持久性；然后actualPageFactory/capture/provenance/strictpagination和两config/ERP/live闭环。原15仍4完成11open，caps[]/executors{}，不改版本/发布/业务merge/父指针。ERP v16文件已明确回交01a16719，guarded模板修复由同一peer独立接手；本会话不跑DB或建立第二修复writer。

### 历史：1e配置组件与private native反例准备

**最新 `1e540ef4b3bd946acfa66bb6fad9c6bf39d94ce2` 已push。** 操作者已明确批准首次从无歧义、完整核验的Seller/Advertiser/CookieStore组合建立持久锁定，跨Store只可人工重绑；同组合多页去重，不能凭tab偏好选目标，不再重复询问该政策。当前代码只落在既有CONFIG归属：显式 `cookieStoreBinding` unbound/pinned；fresh scoped空namespace由Owner建unbound；普通轮换保留raw pin/absence/malformed，caller firstpin/unpin/change/repair拒绝；事实scope投影不与执行权限混用，输入kind/StoreID各捕获一次。没有新key/owner/queue/token/registry/window。

严格TDD：原21业务RED/2controls/fulltsc0，后新2个getter后采样补签RED/fulltsc0，修既有raw classifier；最终 **9相关suites545PASS/no skip/fulltsc0**（`cookie-pin-config-final-related.log`）。首轮497PASS46旧fixture期待FAIL、随后156PASS1同类期待FAIL均保留独立记录；只将4处exact config期待补上新unbound字段，未改旧业务oracle。诊断0errors但26辅助warnings/1inconclusive，不报全clean或全插件/真实浏览器。

新CONFIG-only fresh后验workflow **7f1a6673/child74c26234** 已终态，35行全读、无demonstrated局部阻断，Parent限定接受配置组件；LOW pin专用queued CAS/输入替换及更全own-shape覆盖仍留待实链验证，不冒已测。不是恢复已完成设计agent。此前debb763a的5c/96两窄设计已完整读/Owner对话：初pin须业务nativeSET同写config+business+GEN+ownedleaf，terminal/next-command/start/resume须读取真实持久pin，不能配置层绿了就宣称阻止跨Store已完成；人工直接A→B需新完整proof且不清outbox/ledger，不实现UI/legacyrepair于本段。

原生页面方案仍条件性：第一动作是所有adapter await后、原source/leaf/clock JIT下的真实one-read `executeScript` dispatch；不冒HTTP在deadline前发生。oneRead第一await前发唯一fetch，后续页各回SW原域JIT；无callback跨world/90秒补窗。ISOLATED避免MAIN hook只是条件候选，vendor/session/CORS未证；staged capture是可信sender-document独立事实，不伪称reload造成或以marker获权，最终template+binding+GEN+leaf还需接线。官方Chrome documentIds/InjectionResult/MessageSender.documentId均106+文档已实际取，不能据此冒环境支持/live证明。

下一private native当前API RED helper首次a2未分配child，原因是Parent唯一自有node_modules link被clean admission拒绝；已核无源码WIP/新patch，仅移开own link后同协议6dc重试，allocator后精确恢复link，不碰target/SDK/CLI。真实child01a16898/a669位于独立same-lane patchcheckout，Parent完整lane m-547f77cb已登记精确新test reservation并readback，依赖/sparse/完整tsc baseline0后才显式释放3预期RED+3controls测试设计；终稿13行/实际180行测试/host handoff已全读，Parent在隔离checkout独立复核 **3真实业务RED/3controls/fulltsc0**：每个RED实际SET1、tab90→91、GEN3→4并推进旧ledger，缺Store证据/缺失或坏policy仍未被native拒绝。host capture含own依赖symlink的整bundle不接受，Parent仅unstage ownlink并取test-only patch/hash；不碰target、不把预期RED称完成。需正向首次pin/CAS数据carrier与现有消费者完整门，不能只禁全部恢复来绿；尚无native Source修补/集成。

协调规则已按主线64acba9改为canonical board唯一lane登记；已读新AGENTS/工作流，迁移记录保留父lane且释放A/B旧reservation，旧host checkout/partial只保留，不冒已清理。无ACTIVE维护/registry-only提交。原15仍4完成11open；caps=[]/executors={}、candidate0.4.27，上游ff4b/UI未整合，无外发/生产/部署/业务merge/发布/父指针。

### 历史：544组件窄接受与ports库存（当前以以上1e段为准）

**最新54429d5e7a075ecc125c9ed9aad14c6e99ab54e4已push，clock/R1组件已Parent窄接受。** 两个瞬态顺序clock真实RED/type0后，原private probe producer保存该次实际admit/deny角色；candidate/init/final-probe消费显式角色，不用latest权限补签或重试，public ensure state API与原budget拒绝保留。fresh a88终稿47行全读，advisory_no_local_blocker；保3非阻断覆盖缺口（成功后denial、混合候选、最终reprobe），不冒独立执行/全项目接受。7相关549PASS/fulltsc0后仅加1candidate控制，最终focused25PASS/fulltsc0；不报未执行550/739全套，不累计旧736/547。

已实际只读核上游：task544与master5f07的共同祖先是d193，d193已在双方；分歧128/4。ff4b还新增same-business-scope扫描保留、bounded日志/getState默认不读logs，background/storage与status热点需合并保留；导入UI/0.4.30未整合，当前candidate仍0.4.27，无自动merge/改版/父指针。复用矩阵及16个committed Source隔离快照已备，没读原dirty/foreign fixture。

真实端口咨询bc38d899/bf1ec311已终态，122行完整读，不是已批准Source方案。实际source/leaf输入及unobserved返回、GEN/auto-close之前exact operation/document capture分流、跨bridge后fetch原clock/source合同、CookieStore授权来源及严格分页证据尚需spec。Parent已取核官方Chrome原文及MDN sleep/clock警告，另从committed ads8d找到Seller GET/strict正向parser；不冒live/login/选定Store fetch证明。当时向操作者请求CookieStore政策，现已在以上1e段批准并推进配置组件；这段历史不再表示待批。实际capture/bridge/原生pin消费仍按以上新边界推进，不默认fallback。caps=[]/executors={}，原15仍4完成11open；perf core未来写队列已发显式回交请求但未当已获授权。

### 历史：5e7后验及此前局部断点（当前以以上544段为准）

**后验更新：ef13152c终稿46行已完整读，advisory_findings，R1仍未闭合。** 瞬态NaN/rollback拒绝后clock恢复，5e7仍可能重采样latest canDispatch而把该次未发出的probe补签为observed。Parent确认同样禁止；后验已完成不恢复，Source读冻结已结束。下一步在原private probe-result producer保留实际admit/deny角色并向初始化/恢复消费者显式传递，先两个顺序clock current-API typed RED，不再叠加latest-clock guard；旧547不是新瞬态反例证据。

**最新修补已push `5e7a847cbe418cb28b90c2a463f0dfcab73f0cf0`。** c838独立窄审034终稿41行全读，advisory_findings的R1已以2真实业务RED/20对照/fulltsc0复现：healthy running上下文在helper-entry或adapter-point clock denial时发生实际stop/clear，虽无probe dispatch。修既有私有context-proof角色为observed/unobserved；拒绝未观测不得造事实，也不当healthy proof；实际missing-tab/已dispatch不可用仍保原语义，过期原budget/bootstrap仍按原STALE_CONTEXT拒绝。

首次R1整合15suites为735PASS/4旧拒绝语义FAIL，日志保留，修后仅复核受影响 **7 suites547PASS/no skip/fulltsc0**（含protected22/controller133/native176/admission98/drain30/runtime37/chain51）。未重跑最终15、不冒739全绿，也不把旧736累计。新R1后验 workflow `8603a5aa-fd73-4d4c-9981-afc940910639` 已启动，fresh read/contact-only精确16文件，Source5e7冻结，尚无结论；原034及更早C已完成不恢复。原15仍4完成11open，无整体/生产/发布接受。

### c838整合与窄审历史（最新以以上5e7段为准）

**最新已集成并push：`c838275ad98ab083553f5c2f09c1818eaa18493a`。** 两writer终稿/实际diff/5hash已Parent读核后整合；Parent迁移2个缺原clock的native fixture，另以1真实typed RED修正Controller把first-start clock混进事实生命期的问题。事实frame保原90秒/代次，受保护page/native-bind准入单独核clock；已dispatch的raw事实不被追溯取消。最终 **15 suites736PASS/no skip/fulltsc0**（含protected19/native176/admission98/drain30），不累计旧589或两worker数字，不是全插件/生产composition证明。

20d修补后验 `82239dd5` 终稿已全读，advisory_no_local_blocker，F1/F2/F3仅局部处置；专用compat断言未读及type exit仅owner证据的限制保留。该review已完成，不恢复。新c838 delta独立窄审 workflow `dde4f5e7-bdd1-464d-a739-61643af1da30` 已启动，read/contact-only精确20文件，尚无结论。B迁移子任务已完成，A待新审查；原15仍4完成11open。c838候选冻结，未ready/业务merge/发布/父指针。

新审查child `034092d6-5237-4cc7-b31c-82ddcb1b6785` 已提出候选R1：原tab/business/store仍匹配，若probe仅因clock-denied未发出，不能把synthetic unavailable当独立失效事实来stop/rebuild/clear。Parent已确认该语义、待完整终稿后真实typed RED修；此时Source仍冻结，736通过不等新finding闭合。真实missing-tab事实仍须保留，不能再用blanket gate掩盖二者区别。

### 并行开发门禁与20d修补历史（下述启动状态已由上段覆盖）

**最新授权与并行状态**：用户已批准两个独立工作树 writer 并行开发，覆盖此前 Parent-only Source/test 限制；Parent 独占 Git、Source gate、集成及最终接受。纯登记提交 `dc7f28e58881b8fe2d5480ac1c2329f45e466042` 已push，精确写域不交叉。当前 native workflow `7a4b66fc-f7e4-4272-9317-ca92ee64ce8f` 中，A `b9c9a17b-8fcd-493a-896e-34cced48c3fe` 已有7真实业务RED/3controls，Parent独立fulltsc0后仅释放2Source+新测试；B `dfe58ec2-ef74-4fbf-84c4-ce70cfeaf18b` 迁移2旧测试，worker暂报128PASS，最终类型/组合证据尚待收取。没有patch集成或新增完成行。20d后验只读复核排在两writer barrier之后，未给结论。

首轮 `631085d5` A/C `fetch failed`、B仅BLOCKED/零实现；恢复 `5ceefe4b` 因Parent未显式重绑定lane key在启动前拒绝，无child/patch。两次失败均保留，核空patch/干净retained树后，仅修调度参数同协议恢复，未切换CLI/模型。Parent依赖symlink已原样恢复。新策略：只有adapter在自身await后紧邻真实dispatch消费原admit才锁定first-start，port invocation不算；原请求clock/原90秒/source及facts/cleanup分离保持。缺原clock的managed context不得fresh补窗；旧nullable/zero tuple分类与实际managed native权限明确分开，不能把helper通过冒充native成功。详见20-checkpoint及证据目录。

**最新Parent修补：`20d94875c438abba85ba3c1a2a04ea00a58134ae` 已push。** 三项finding先实际26业务RED/38controls/fulltsc0后修：公开fresh仅事实、无clear；managed canonical以19字段own-presence/nonundefined+原值语义拒绝缺损，不改legacy事实decoder；Runtime在任何await及plain getter之前固定原fn并复用canonical pure capture。最终12相关 **589PASS/fulltsc0**，含真实Controller→Runtime→canonical→native ledgerless正案（明确禁HTTP，不冒supported production）。首次25fail但带union.fence类型错误的轮次排除，有效25/type0及最终26/type0均保留。旧报告仍advisory_findings，Parent修补不等独立后验接受；准备新的仅修补范围只读复核，不恢复旧child。下述ac766/542为被审基点，不再是最新HEAD。

Parent继续原十五行，仍 **4完成／11open**，不逐片等待用户确认。下述ac766为此前actorless实现快照，当前已push集成基点以20d为准。隔离writer `feature/plugin-status-runtime-closure` 当前 **`ac766b161ab6aa86dcecb951ffd0d4115b04f3a9`** 已push，自有tracked/indexclean，候选0.4.27；无生产、真实外发、业务merge、父指针或发布授权。

已完整消费423c/54ae的294行actorless clear设计，不恢复或重跑。`d54d31f` 区分原PRE连续性与valid GEN漂移的fact-only；`64e263f` 在既有queue新增实际managed native clear，单set null+GEN、完整校验后noop、不推进撤销的ledger；`fbcacf5` 把原observer/parent PRE及canonical本次POST接入Controller两实际clear点；`ac766b1` 经两真实typed RED修复原callback与source-bound角色getter替换。最终 **12相关542 PASS／完整tsc0**，不是全插件/真实浏览器/首protected action时钟证明。原77失败及15旧fixture失败保留；旧77案已迁到真实native scoped profile、real setter推进GEN，不再靠fake变量或独立90秒假预算。generic controller镜像不冒native删除证据。

新只读**实现**窄审 workflow **`6bc7c52b-2333-4204-885e-216cd726366d`**／child **`4e54ae43-48d3-4488-bb1d-2fa725f7849a`** 已完成，124行完整读，结论advisory_findings；不恢复/重跑。三项Important已Parent接纳：公开`fresh`没有独立invalidity验证；strict canonical缺required字段被legacy补false/null；Runtime在ledger await前未拥有输入及原callback（既存transport弱点影响新lane，非新改runtime算法）。542绿不覆盖这些反例，Parent未判接受；继续真实typed RED与精确gate修，不另造trusted-caller/immutable-input豁免。

旧native95/drain30、first protected clocks、capture/ACK/resume/currentcarrier、actual supported composition、actions/Cookie与最终跨端验收仍开，background caps=[]/executors={}。ERP eba/156相关与一次baseline exit1保持。peer报告master5f07fda/v0.4.30已合入导入UI，Parent已ack但未独立核实/整合；后续保d193性能与peer UI/版本。新peer称当前SPU日期lane不占remote-config路径；这是协调证据，配置原registry/未来写队列仍待核，不凭idle或非冲突声称全已回交。

当前续作以 `20-checkpoint.md`、`resume-state-hint.json.currentExecution` 及 `/tmp/plugin-status-continuous-01a1406d/observation-clear-frontier-checkpoint.json` 为准。实现窄审已终态，不等待或恢复完成child；Parent依新finding直接续作，不poll/bg_wait。

## 历史执行快照（2026-10-22，clear RED阶段）

用户已明确要求连续推进当前 owner 的十五个 open rows（含配置同步两端）；Parent 仍是唯一产品/Git writer，仅允许既定只读独立 reviewer，不逐小片索取继续授权。既有依赖型验收行在实现与证据闭合后合并记账，不机械重复实现。原件/foreign WIP、原单目标/原预算/授权边界保持。用户已准仅从 committed 两 TS fixture 复制到隔离目录加载；不是解除原件/元数据/foreign JSON 保护。上游 ff4b17f/0.4.28 尚未整合，候选仍 0.4.27，保性能语义且不擅改 peer 版本；不得扩为真实外发、生产迁移/重启、业务 master merge、父指针或发布。

当前隔离分支 `feature/plugin-status-runtime-closure` 已push **`e35c4e3`**（test-only真实clear RED），Source **`6086f3b`**。10ea设计与0c6f四项实现findings均完整消费，不恢复完成child：Parent先实际26typedRED修1c alias/目标/末次源复核；9d Runtime startmetadata、7e exact raw metadata Promise、b17 canonical own prep leaf同set、6086 Controller context/postmarker/late-source handoff与failed marker都按窄typedRED提交。realRuntime→Controller→actualnative→READY及失败/conflict在明确注入caps/pages的组合下已验证，非实际production/browser/firstprotectedclock证据。

Source6086当前12相关498PASS/1KNOWNoldlateclearFAIL/fulltsc0；新e35在realstorage/controller/NS/GEN下实际复现old clear擦掉新tab77，1业务RED/type0，候选不ready。旧actual-native95/drain30尚未完整迁移，不在选择；362/354/350/349/323等各轮不累计。Background caps=[]/executors={}仍缺actualsupported composition，clear/ACK/resume/currentcarrier/capture/firstprotected/Cookie等未闭。新readonly423c/54ae只设计这个新的actorless PRE-source→OWNpostproof→nativeclear边界，Source/test冻结e35、Parent仅docs，先答supervisor/消费完整输出再精确Sourcegate，不把outerguard或latest补签当修复。

新importdurable peer已明确不碰status/storage/background，报告3abed5c分支/v0.4.29仅UI与版本；status候选0.4.27仍未整合此前ff4b/0.4.28或该peer分支，不冒master已同步/不覆盖peer版本。

原全面runtime时钟门控实验已精确撤回，不把控制完成当raw事实落盘；trace只导出OS temp/evidence。原完整插件1244PASS/26FAIL+1setup不是最终GREEN；当前323与此前310/398/373等是不同轮次/选择，不累计。当前自有tracked/indexclean，依赖symlink不stage，原dirty树/foreignJSON不动。

此前插件 `ff03e2c`（observation-only）：持久 generation/namespace/clearing、commit/send/replay/ACK proof 与真实配置投影后继已提交；25 startup 控制与自包含20相关620 PASS、guarded63零诊断。canonical login/conflict/RECOVERING 的直接控制已有，不需要为本来正确的投影加分支；native 生命周期构造/action ports/完整90秒/Cookie aggregate仍开。较早旧 runtime test 间接导入 protected fixture 的选择已停止并披露，不能用 compiler 守卫替该命令背书。旧默认自动 reprovision 等合同已在e553d0b提交；不以正确既有行为的测试修正冒新功能 RED。

ERP `eba0b77`：不可变 notification claim、条件 lease settlement、逐条发送时领租约、真实 60秒 system job/audit 与 retained Session reclaim 刷新已提交；当前156个 isolated相关 PASS。一次完整 baseline 2873PASS/1FAIL/14skip/1xfail exit1，唯一自身时钟混用案最小隔离PASS、修后155PASS；不冒最终同字节全GREEN、不重跑整套。fresh local SPEC对两叶无实现阻断；QUALITY提出 retained ORM identity-map reclaim后已实际1RED、一行修复及156PASS，fresh05f532后审无findings。Parent已完成观测三行与ERP本地runtime一行，原15行余11；仍不进入跨端ready。

后继顺序：观测/startup三行及ERP局部runtime已完成 → native物理90秒/actions/Cookie → 两配置rollout（仅已提交依赖，待共享core未来写队列回交）→ cross-domain spec/quality/integration。真实浏览器/生产证据缺失如实标明，不用合成状态冒最终产品完成。唯一当前断点与证据以 `20-checkpoint.md` 和 `resume-state-hint.json.currentExecution` 为准；下文为历史。

## 历史执行快照（2026-10-17）

用户要求继续完整产品，OWNER继续直接实现/测试/Git，**不再launch/resume任何worker**；只读独立reviewer仅承担既定复核，不写产品或跑命令。Terminal补丁`809631f`已由fresh只读reviewer **`6010e42d-f772-4bed-8e2a-8e084cf92304`**局部accept，OWNER据既有26named/type0及该报告接纳这一窄补丁；不是SDK checked接受、合并权或全Caller完成。同审查发现独立既存P1：旧snapshot/observe在首次read等待期间被新HOLD撤权，子ensureInitialized却借新generation先重建事实，外层随后拒绝已经太晚。

OWNER现Source先得到两真实HOLD交错RED/type0，再在既有controller内传递父原frame/首次scalar PRE，child read已知mismatch或原parent失权即只返回带原拒绝fence的只读preview、不开始子变更/cleanup；合法self-revocation仍先消费parent再用own generation。snapshot/observe/recovery均沿用同原frame，不新增authority/schema/port/module。补same-epoch nonce两案，普通私有copy仅controller差异：旧Source4业务FAIL→候选4PASS；最初filter零选择15skip另存勘误，不算RED/GREEN。最终当前产品nonemit chain13+controller39共**52去重namedPASS**、实际unpiped `tsc --noEmit` exit0，旧11完整it字节精确；仅controller+integration两path，冻结两fixture全stat/cache不变。源码/测试检查点 **`0716442a37063868b862f717d386e509ef6781ee`**已push，候选不冒充已终审。

其后5d独立审查正确changes-needed：已拒初始化preview仍被observe负选择委派进入invalidation、先撤gen/stop；虽无旧durable写，仍损伤当前producer许可。OWNER先新增LOGIN_REQUIRED/IDENTITY_MISMATCH真实2RED4PASS11skip/type0（同controller当前snapshot witness被错误撤权、实际canonical apply:false），再仅observe子入口加10行原liveness/原PRE精确比较；不新建owner/flag/schema/port或重开budget。最终当前产品 **15nonemit chain+39controller=54去重namedPASS、actualtsc0**；well-typed private denyallcontrol被现有真实live-parent正案杀死，invalidsetup/type/emptyselection均排除并存勘误。Source/tests **`130c395fd8d3ca04db80d1ca827e2dbb755636f0`**已push，仅两path；两冻结fixture全stat/cache不变、原11整it精确，LSP0reportederrors/1inconclusive非clean。

Fresh独立 **`acfef23b-631a-4970-b778-d2b6facdd4ce`**已complete：corrected parent/observe **accept**、logicalCaller **ready-for-budget**。OWNER结合实际证据窄接纳；advisory不是SDKchecked/merge/全产品验收。Tower **647/184**仅完成Caller/7path回执；新`status-budget-init-read` in_progress，`status-budget-ports` pending待明确config热点handoff，聚合budget blocked依赖两子项。下一步直接读批准S1与Budget owner，先真实entered RED，不等用户再催、不启动worker或新reviewer空等。预算/physical/actions/Cookie/finalspec→quality/ERP/T5/browser/发布仍未闭。共享`storage.ts`/background/scheduler未答复前不写；独立controller不扩大停工。最新证据`/tmp/plugin-status-observe-denied-preview-owner-01a1406d/`；实际接受报告`/tmp/plugin-status-observe-denied-preview-postgreen-owner-01a1406d/review.md`。此前0716/52GREEN只是被5d纠正的候选，全部原件/历史worker流程保留但不再授新worker权。

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

### 历史：OWNER直接开发的terminal初次候选（其后6010独立审查已局部accept）

用户已明确不再调度worker，随后允许四枚举关联unit自动各一轮；OWNER直接完成canonical `commitDecisionResult`原fence消费：freshpresence在queue await后，normal/full和unreadable两个set前同步复用helper，拒绝不落拟议terminal事实，保留args.expect/obsonly/noproposal兼容。不改runtime/producer/协议/版本，非物理mutation/budget修复。TaskStart与现Source2RED在edit前记录；第一修复2GREEN+actualproducttsc0，nonemit chain9+runtime4+storage13共26namedPASS。Source仅+15/-3，**`809631f61551f26c201ca7707d858e7ae7e5d279`**已push；2冻结fixture未stage/全stat不变，147tracked仅Sourcedelta。

独立pre架构建议7de已复用，但**本轮postGREEN只有OWNER检查，没有独立评审，不称局部正式接受或Caller/T4完成**。保留独立审查欠账/最终spec→quality；不能再resume旧worker。当前候选的验证不是full/coverage/build/release或浏览器验证。详见checkpoint及`/tmp/plugin-status-terminal-fence-direct-owner-01a1406d/`。

### 历史：双业务RED已验证并推送检查点，生产权限当时另待确认

653与655均OWNER同named独立复现，actualtype0；后者1FAIL10skip在非空本decisionledger已applied。fa/f8 nativecomplete/terminal/review-required（非SDKaccepted）、DOMAIN7/c55精确，命令审计本pass EXACT654/655两Bash，无额外npx。仅test检查点 **`e1526126f5ed4588a17b2d1149d9726974aa43c3`**已push，2旧fixture未stage，生产P1未修。canonical storage/helper/最后await/两写分支边界已定，runtime-onlyprecheck不是等价候选。Operator一次自动四unit例外未确认，Source仍无授权，Caller仍in_progress，非全项目停止。

### 前一阶段：最后queue await/不可读分支RED计划

56/e03原生勘误与fresh7de边界review已terminal；既有terminal owner适合复用helper，但normal/full及unreadable两个写分支都须最后await后检查。当前fa4a8eee-fbf8-4735-adf3-69843ae9ac4a exact-resume e03先NEW preTEST WAIT，仅existing integration加ONE最后queueIO/原fence过期+unreadable case，old10/helper冻结、654type/655named，不给Source。Operator一次automatic4unit例外问题尚未批准，候选storage/runtime/viewmodel/controller静态清单非SDK执行保证；未确认前仅上述integration工作。Caller643 active，Source预算等后继未闭。

### 前一阶段：terminal-fence真实RED已核，命令勘误/最小Source边界审查

653与OWNER同named真实1FAIL9skip（finalready错误），652/OWNER actualtype0；old9/header/snapshot/84仅testdelta/2fixture全stat/cache均核，test4ec，postRED9ac只接受RED，不是GREEN/Source或Caller完成。root389 terminal/native review-required，真实18Bash含13额外管道npx漏报，合规不接受且保原件；不因此否认独立RED或停止全项目。当前root56bc8e0a-a11c-400f-a0ec-6abab1a048c9原writer只读原生命令勘误→fresh只读canonical最小修复边界审查，无产品编辑。旧合法保存runner片段支持edited+neighbour风险，不绕过SDK读取拒绝、不宣称Source-safe。详checkpoint最新/command-scope-errata与post-RED-independent-proof。

### 历史：terminal-fence RED原子任务运行，643 Caller in_progress

Fresh b377及Owner源读确认逻辑terminal consumer遗漏原commit.fence，与future physical风险不同。267/d448设计terminal，唯一实际SHA无dot/noGit，84不变；原报告dot/行号错误保原件另纠正。新root389a890a-6b37-4629-ad6a-608c2004ddb8 exact-resume d448，先fresh supervisor pre-TEST WAIT，后仅existing integration一个真实case+650type/651namedRED，再postRED receipt；绝无production grant。原子顺序：validRED/type0→另fresh source pre-edit及auto-scope证据→canonical storage最小复用既有permission helper→相关GREEN/产品tsc→独立fresh postGREEN。TDD strict来自repo；新测试不碰旧9/header/2fixture，不建authority或runtime双重owner。见最新checkpoint与terminal-fence-RED packet。

### 历史：继续推进，643 Caller in_progress；fresh逻辑审查→原writer窄RED设计

Operator明确继续，OWNER撤回把只读Git违规扩大为整个任务停工的处理；违规/原件保留不追认，noGit包含全部readonly Git元数据。新单一async root26713a14-e033-4d0d-bd97-41429b63244f先fresh gpt6.1-sol做逻辑Caller闭合审查，再exact-resume246原MiniMax设计下一最小integration RED；当前无编辑/app tests/typecheck权限、无新source grant，不改profile/SDK。84hash/2fixture stat及批准fb42原文c2fa已独立冻结。f408仅本chain已有窄证据，production-edit触发边界未证，不能空泛停工也不能擅自越门禁；结果原生通知后自动续行。见最新checkpoint。

### 历史：十二局部push有效；只读盘点因noGit命令范围违约HOLD

420/246已complete/native review-required/DOMAIN7 needs_context/空delta空gate，649actualtsc0空及全84hash零差异。但Owner实证同bash实际`git rev-parse HEAD`违反worker noGit，与终稿noGit action矛盾；仅只读提交号、没有Git修改或产品变化。原件/actualcall已保全，暂停新的代码授权，先审noGit/f408/宏Caller边界，不改SDK/profile或重写历史。0bab及十二局部接受不受该后续范围违约倒推撤销。两个raw release await只是design候选，不得skip finally cleanup/另借预算；Source305是每operation聚合，不采纳profile-wide预算说法。详细scope HOLD见checkpoint。

### 历史：第十二局部终态已核且push；继续只读Caller闭合盘点

ae28/8b417 complete/terminal、native review-required非SDK accepted，真实DOMAIN7/done/精确old+new paths/完整post0d800/无命令占位符/终态6425字节已核。仅旧新测试路径显式stage，commit/push0bab24702001dc9be12d4a81d181ee33c53e3554（rename99% +2/-5）；两原fixture deltas未stage仍modified，非Repo clean/master/pin/version/release。Tower641/182仅新增测试fidelity窄completed，Caller仍in_progress，f-408 broader未来unit自动超named风险open。新只读root420cd1c3-3dfb-4ae8-aca8-add343a12027 exact-resume8b417，盘点宏Caller vs预算/actions/Cookie/specquality/T5/共享owner边界及future named-only策略；仅准一次actual产品type到649，无app tests/代码/改名/配置授权，不能由微gate推MacroDONE。详细Scope Card/snapshot见checkpoint。

### 历史微切片：integration/snapshot局部postGREEN已接受，待终态

独立新postGREEN0d800a7e-c9b2-4372-94b3-7ea06d64ca4b仅接受本测试fidelity：OWNER2产品named+7自有普通copy related均PASS、actual产品tsc0空；before→after只snapshot arrow+准确局部注释、83其他hash/全部旧case+guard精确，2fixture全stat/cache不变、实际TaskStart窗口无新auto runner。最终新文件64251491ae3d991b3b1a2ff058c16b20db01325e94cd97de6e50aadf0a2bf998，LSP0errors/1inconclusive非clean。仍待rootae28/child8b417实际7字段/native终态/终态范围/Git；全Caller/T4未闭。f-408未来unit自动整文件超named风险仍open，不因单chain分类而泛称全工具修复。以下为分阶段过程。

root986d1b1a-c8f5-437d-97ae-6aed9d9282a9/childd478332e-c442-40fe-a445-ed6c5bfd6453已complete/terminal；native acceptance review-required非SDK accepted。OWNER独立核真实DOMAIN7/needs_context/空delta/空gate/643 actualtsc0空/84文件未变，只确认报告载体修复。新rootae28e0da-0bdf-46df-a09c-f96f2bbc52bd exact-resumed478先NEW architecture/pre-edit WAIT，A新gate0ea744b1-53bc-4fc8-b67a-cc4f94c7ddb4已执行并独立核实精确c180改名/644同RED/645type0、映射84字节不变、2fixture全stat不变、auto cache逐字不变且实际TaskStart窗口无新runner事件。现仅B新pre-edit49988cd1-98c9-41bd-9bae-f54efd551bad获批compose-only，GREEN及distinct postGREEN仍待核，不是DONE。A新gate仅字节精确integration改名，显式同guard RED/type0/无自动9和fixture写，才能另fresh helper pre-edit；B仅snapshot compose-only，2产品+7自有普通scratch related/actual产品type0，再distinct独立postGREEN。自动9本未授权，不依赖它保RED；A显式RED不可丢，失败即停，不是只改名隐藏失败。生产/旧case/guard/2fixture/header/version不改，无新框架/flags。origin/master ACTIVE既有glob真实UUID已覆盖新名，Tower640已显式scope/in_progress；f-408仍open。TaskStartSnapshot与完整Scope Card见checkpoint，宏T4/发布未完成。

### 历史native报告修复（无产品权限）

只读ef087cc1-3f21-46ca-be71-24d563b2595d/e9cf2364-72d0-4206-bad9-650d9130dfb1也因Structured acceptance report not found失败，但实际final合法7字段、642 actualtsc0/空、84产品文件未变。canonical SDK证明需要独立acceptance-report元数据，checked验证后自动strip，DOMAIN仍7字段。OWNER撤回自己误设的“任何footer/fence都不准”，不是降验收或改SDK；真实bare7 parser失败/明确synthetic carrier parser成功且strip逐字一致仅为诊断，不冒充native通过。原件及parser-proof保全native-carrier-diagnostic目录。新只读同协议root986d1b1a-c8f5-437d-97ae-6aed9d9282a9 exact-resumee9仅补报告载体和fresh643 actualtsc，等待真实receipt；无rename/helper/fixture/config权限。e9 integration分类/先后次序意见仍需独立OWNER审查，不是架构或产品gate。

## Goal

实施已批准协议的 D1–D4：monitor 单 Seller/Advertiser 自动绑定、失效安全暂停、ERP 状态与生命周期通知、受限恢复。保持首次服务端放行、独立 status key、离线不得自行恢复、完整 fresh close 快照门禁。OWNER直接负责实现、测试、设计一致性、验收、协调和Git；只读独立reviewer不承担编码或命令，禁止恢复旧worker流程。历史原MiniMax唯一writer约束不再授予任何新worker启动权；不改SDK/profile/CLI绕过约束。

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
