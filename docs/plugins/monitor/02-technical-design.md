# Monitor 新旧 campaign-list 接口兼容技术方案

- Goal：保留旧广告列表接口行为，新增 `post_campaign_list_v2` 的显式路由与 cell-row 解析，恢复页面捕获、后台全分页、阈值评估和关闭回查的连通性。
- Architecture：URL 决定协议版本；协议适配只负责把旧 record / 新 cell row 交给同一个 Campaign 规范化出口；不新增第二套关闭、调度或状态存储。
- Tech Stack：现有 TypeScript / WXT / Chrome MV3 / Vitest；不新增依赖。
- Baseline / Authority：用户本次需求、[完整脱敏抓包](01-api-captures.md)、插件 `AGENTS.md` / `README.md`、0.4.19 安全加固及 0.4.22 告警诊断。实施基点为 ads-monitor `origin/master` 的 `e49d00b`（0.4.22），不是父仓当前固定的 `456423b`（0.4.19）。
- Compatibility Boundary：旧接口、旧 GET/POST 模板、旧消息名、旧持久化模板都保留；不以 v2 替换旧 URL，不在失败时切换到另一接口，不新增自动开启。
- TDD Route：strict，依据插件 `AGENTS.md` 的明确要求；先运行新增测试观察业务性 RED，再实现并取得 GREEN，最后全量测试、coverage、类型检查及 build。
- Verification：见 §7 的逐项验收矩阵与命令；真实 Chrome/TikTok 行为另列未验证项，不把 mock 通过等同实店验证。

## 1. 需求与边界

用户要求先完整保存接口信息（仅删除 Cookie / Token 等凭证），owner 写完本方案，再由 `minimax-cn/MiniMax-M3.1-Flash-Preview` 开发，owner 审核方案一致性。文档属于父仓 `docs/plugins/monitor/`；代码属于独立 ads-monitor 插件仓，不在父仓暂存插件源码，也不自动推进 submodule 指针。

本次只包含一个协议兼容切片：捕获 → URL 校验 → 解析 → 全分页获取 → 原有阈值/关闭回查消费者。以下均不改变：业务阈值、白名单、SKU 保护、币种规则、关闭队列、监控/报表时间窗口隔离、Cookie 归属、配置同步、飞书、Alarm 调度。不得执行抓包 cURL，也不得对真实广告做关闭/开启测试。

### 代码必要性 / 复用判断

- 不改代码或只换设置无法让 `Cell[][]` 变成现有解析器接受的记录，且后台 URL 校验明确拒绝 `_v2`；需要代码变更。
- 复用 `parsePostCampaignList` 的业务字段规范化、`campaign-pagination.ts`、查询窗口与 `fetchLiveCampaigns`，不复制整个 fetch / parser / closer。
- 2026-10-14 已搜索本仓、GitHub、npm 和官方资料：未找到适配这个 Seller Center 私有 `post_campaign_list_v2` / `table_v2` 协议的可直接采用实现。公开 TikTok Shop Affiliate API 不是此接口，不能替换登录态抓包协议。单行 cell 转 record 和 URL 路由属于小型可审计逻辑，不值得新增 SDK/依赖。
- TypeScript 判别类型参考：[Handbook: Narrowing](https://www.typescriptlang.org/docs/handbook/2/narrowing.html)。仅使用语言已有能力，无复制上游源码，无新增 licence/dependency 负担。

## 2. 已证实事实与未知项

### 2.1 当前代码问题（纠正此前判断）

1. `page-hook.content.ts::shouldCapture` 使用 `url.includes('/post_campaign_list')`，所以**已能碰巧捕获 `_v2`**；不是“完全未捕获”。但它也会误匹配 `_v20`、路径后缀或 query 中含该词的其他请求。
2. `background.ts::toTikTokAbsoluteUrl` 用 `pathname.endsWith('/post_campaign_list')` 校验，因此 `_v2` 在后台模板入口会被拒绝。
3. `tiktok.ts` 只寻找 `table/list/campaign_list/campaigns`，没有 `table_v2`。
4. `live-campaign-fetch.ts` 同样硬编码旧列表字段作为响应结构校验；即使仅修 parser，后台仍无法接收 v2。
5. `fetchLiveCampaigns` 是分页、后台扫描、报表、关闭后查询与失败复查共用的出口，必须在这里贯通路由，不能只修页面显示。

### 2.2 新接口（抓包证据）

- POST `/oec_ads/shopping/v1/oec/stat/post_campaign_list_v2`，query 有 seller / advertiser、locale / language。
- JSON 请求包含 `query_list`、起止日期、page/page_size、dims、排序与 filters。
- 捕获 filters 是 `external_type in [304,307]`、`campaign_status in [no_delete]`、`gmv_roi_mode` type 10 / value 2。这是页面列表，不是现有监控标准化后的 active-only 查询。
- 响应 `code=0`，`data.pagination={page:1,page_count:2,total_count:11}`，当前页 10 行；**不能把这一页当完整快照**。
- `data.table_v2` 为 `Array<Array<{name:string,data:string}>>`，没有 `data.table`；`statistics` 不是 campaign 行。
- 关键字段同名：campaign_id/name、cost、onsite_roi2_shopping_sku、template_ad_roas_bid、onsite_roi2_shopping、cost_per_onsite_roi2_shopping_sku、campaign_primary_status/campaign_status。
- 已生效值 `delivery_ok` / `campaign_delivery_ok`；页面也返回 `disable` / `roi2_mutex_exclusive_product`。
- `compensation_info`、`promotion_days_schedules` 的 data 是 JSON 字符串；本次不展开它们。原始文档完整保留所有字段与嵌套字符串。
- 抓包未提供 currency；现有“缺省 USD”的兼容行为保留，不宣称接口证明了店铺币种。

### 2.3 状态接口

`/oec_ads/shopping/v1/creation/campaign/update_status`，body `{campaign_list:[id],operation:2}` 为关闭；`operation:1` 为开启；成功响应为 `{code:0,msg:'success',data:{status:'success'}}`。现有 `closeCampaignInPageWorld` 已使用此关闭协议，不需要切换 endpoint。只证明了单 ID 请求，不推断批量部分成功、重试幂等或最大数量。不新增开启路径。

### 2.4 不确定性与 fail-closed

v2 抓包只证明 `no_delete` 页面请求，未证明服务端对标准化 `delivery_ok` 过滤的行为。此轮继续复用旧监控的 active-only 请求规范，**不擅自改成 no_delete 全状态权威查询**。若服务端忽略 active filter，返回非生效行，严格 live 校验仍使整轮失败，而不是忽略该行并认定关闭。真实浏览器验收需要确认 v2 支持该过滤；失败时报告不支持，不悄悄降级。

## 3. 唯一协议路由与适配契约

### 3.1 路由 owner

新增 `src/core/campaign-list-contract.ts`，只承载轻量协议知识，保持纯函数。建议出口：

```ts
export type CampaignListApi = 'legacy' | 'v2';
export function resolveCampaignListApi(url: string): CampaignListApi | null;
export function extractCampaignRows(response: unknown, api: CampaignListApi): unknown[];
```

实现约束：

- `new URL(url, 'https://seller.tiktokshopglobalselling.com')`，接受既有相对路径及带 query 的绝对 URL；只允许 exact origin。
- 读取 pathname 的最后路径组件：`/post_campaign_list` → legacy；`/post_campaign_list_v2` → v2。保留旧代码允许的任意前缀 pathname，不要求旧模板都长成完整 v1 路径。
- 不接受 `_v20`、`_v2_extra`、额外尾路径、无关 query 字样、其他 origin、非 HTTPS origin、非法 URL。
- `shouldCapture` 和 `toTikTokAbsoluteUrl` 共用该函数，避免一个宽松 includes、另一个窄 endsWith。
- `POST_CAMPAIGN_LIST_PATH` 旧导出保留以免破坏现有消费者；如新增 v2 常量，仍只让一个模块拥有路由实现，不维护两套判断。
- 新协议选择从 URL 派生，不新增持久化字段、导入迁移或消息必填字段。旧模板天然路由 legacy。

### 3.2 行与响应验证

- legacy 保留现有 data/root 的 table/list/campaign_list/campaigns 候选、顺序与记录规范化行为。
- v2 只读 `data.table_v2`，不读取 legacy 列表作为 fallback；显式 legacy 路由也不因为看到 table_v2 就切换到 v2。
- missing / wrong type 的目标列表是错误；显式存在的 `[]` 才是空列表。页面捕获也拒绝业务错误或目标容器缺失，不替换健康模板。
- 每个 v2 row 必须为 cell 数组；每个 cell 必须为 object，name 为非空字符串，data 为字符串。拒绝同名字段重复、保留名 `__proto__` / `prototype` / `constructor`，避免最后值获胜或原型污染。损坏 cell/row 不可静默跳过，哪怕损坏的是可选指标。
- 用安全 record 映射 name → data，再复用现有 `normalizeCampaign` / 数值解析 / 状态映射 / 单次转化成本排序。未知合法 cell 保留为普通值或忽略其业务用途；不递归解析 JSON 字符串。
- 必填 id/name/status/cost 不完整：page 捕获可沿用既有非严格行策略作为展示，但绝不能成为完整 live 证据；strict live 模式必须整页失败。结构损坏始终失败。
- 非生效行：普通 page capture 过滤；active-only live 查询必须拒绝整页（保留 `requireCompleteActiveRows` 既有保证）。不能为了让原始混合抓包通过 live 而放宽这个保护。
- 业务 code 非 0 / '0'（存在时）、响应非 object、缺失容器均不能被当作有效空列表。旧接口 code 缺省的既有测试兼容性保留。

`parsePostCampaignList` 继续导出，第四参数增加可选 `api`，默认 legacy 以兼容旧调用与现有测试。所有生产的页面/live 调用显式传入 URL 解析结果；只允许测试或明确的旧兼容调用依赖默认值。抽取行验证只有一个 owner，禁止 fetch 和 parser 各复制一套 v2 hasRows 判断。

## 4. 链路接入与安全不变式

1. page hook：只更换 `shouldCapture` 的判断。fetch/XHR hook、Request clone、正文提取、postMessage 名称和 content bridge 不变。
2. background capture：合法 URL 解析确定 api，现有 seller/advertiser/tab/generation 校验不变；解析时传 api，解析失败不写入新模板或清空现有 authoritative 列表。
3. live fetch：在网络请求前解析模板 URL；未知接口失败。一次 fetch 固定一个 api，所有分页、strict 校验和最终解析都用此 api。请求 URL 不重写成另一版本；原 query、query_list、dims、order 与 page_size 不变（既有日期/分页/active filter 改写除外）。
4. 请求处理继续复用滚动时间窗、GET query / POST body 分页、去 keyword / 普通过滤、保留 gmv_roi_mode、active delivery_ok、10 秒 abortable deadline。不得因为 v2 另起调度器或分页器。
5. 所有页都严格成功后才去重、排序、提交完整 snapshot；第二页损坏/业务失败不得写回第一页部分结果。页面 fallback 仅用于展示，`campaignsComplete` 不能变 true，不能触发自动关闭。
6. 自动关闭、测试关闭、失败消失二次确认、关闭后回查继续调用既有 fetch 出口，不直接用 table_v2、statistics 或页面缓存判断消失。有效完整 `table_v2:[]` 可以证明 active query 为空；字段缺失/损坏不行。
7. 不改变现存“缺少 requestTemplate 时跳过回查”的分支。本次不能把这种既有行为宣传成完整验证；正常捕获链路必须带合法模板，新增测试始终提供模板。
8. 不修改关闭接口、operation、凭证处理、队列、最大 SKU / whitelist、远程配置或飞书逻辑；不得顺手重构 background 的其他区域。

## 5. 文件地图与复杂度预算

| 文件 | 变更归属 |
| --- | --- |
| `src/core/campaign-list-contract.ts`（新增） | 唯一路由、目标容器选择和 cell-row 结构验证；保持约 150 行以内，避免通用框架 |
| `src/core/tiktok.ts` | 接受 api 并复用适配后的记录；原业务字段出口不复制 |
| `src/core/live-campaign-fetch.ts` | 模板路由传递、strict 校验共用出口；删除被共用出口替代的重复 hasRows 检查 |
| `src/extension/messages.ts` | 旧常量兼容或纯 re-export；现有消息/type/storage 形状不破坏 |
| `entrypoints/page-hook.content.ts` | shared matcher |
| `entrypoints/background.ts` | 仅 URL 校验与 capture parser 传 api；不增加 v2 专用编排函数 |
| `tests/campaign-list-contract.test.ts`（新增） | URL/协议边界与畸形 cell/容器 |
| `tests/{core,core-coverage,live-campaign-fetch,page-hook,background}.test.ts` | 新旧回归和端到端 mocked capture/close verification（按实际既有文件名放置） |
| `package.json`, `package-lock.json`, `wxt.config.ts`, `README.md`, `CHANGELOG.md` | GREEN 后按当前 master patch 升级（基线 0.4.22 → 0.4.23） |

`campaign-pagination.ts` 原则上不需要改动；若必须修改，只能保持旧请求改写语义并先提交原因供 owner 确认。大文件 background 约 2954 行，不继续内嵌解析；协议能力下沉纯 core。新 helper 是协议 seam，不是第二个 campaign 业务 owner。

## 6. 开发任务与委派

### T1：纯路由及行适配（同一个 writer）

1. 使用独立插件 worktree `/home/schan/pi-workspace/monitor-api-01a13a78`，分支 `feature/campaign-list-v2-compat`，先读 AGENTS/README、相关源码与测试。
2. 增加 URL 矩阵、v2-only、空数组、错误 schema、重复 name、旧/v2 不互相 fallback 测试；`npx vitest run tests/campaign-list-contract.test.ts tests/core.test.ts` 观察预期 RED。
3. 加最小 shared contract 和 parser 接入，同命令取得 GREEN；不得以放宽 strict 模式让混合 live 结果通过。

#### Owner 范围确认（2026-10-14）

已核对 `tests/core-coverage.test.ts` 两个 fetch 用例：额外授权仅将两处正向模板 URL 和一处对应断言 URL 的 `https://example.test` 改为合法 Seller Center origin，保持用例意图和其他断言不变。其余允许文件中的正向/传输 fixture 同理；专门验证非法 origin 的负向测试不得机械替换。必须测试非法 origin 在网络调用前被拒绝。此为测试 fixture 修正，不放宽 §3.1 的 exact-origin 规则，也不引入第二套路由。

### T2：协议链路贯通（同一个 writer，顺序执行）

1. 先增加 page fetch/XHR、后台 URL/capture、2 页 v2 live、坏第二页、业务失败、active-filter 请求、完整空结果及关闭后回查测试并观察 RED。
2. 只在上述入口传递 URL 选择的 api；保留旧接口全套测试，不删除旧逻辑。
3. 执行相关测试取得 GREEN；记录实际 RED/GREEN 命令、失败原因与通过数。

### T3：发布元数据及 owner 审核

1. 行为 GREEN 后同步五处 patch 版本、README 新旧路由与 fail-closed 说明、CHANGELOG。
2. 执行 §7 全套插件检查；生成变更清单、测试/coverage/typecheck/build 原始日志路径与安全边界说明。禁止提交真实 capture 到插件仓；使用最小人工 TEST_ 数据，不复制文档中的店铺/广告内容为 fixture。
3. worker 不写父仓文档/registry/gitlink，不提交 archive/output，不推 master、不创建真实广告操作。worker 可按明确路径提交自己的代码并推 task branch，或保留供 owner 统一提交；不得大范围 staging。
4. owner 逐条对照 §7 审核，不把 M3.1 自述当证据；发现偏离通过同一 retained writer 修复，不替换模型。仅在实现和要求验证全部通过后交付。

这是一条协议 seam 的顺序实现，不拆为并行 writer，避免 matcher/parser/fetch 类型漂移。代码由用户指定模型完成，owner 持有范围、方案与验收权。若模型/运行设施失败，停止并报告，不自动换 CLI 或模型。

## 7. 验收矩阵与命令

| ID | 必须观察到的结果 |
| --- | --- |
| R1 | 新旧绝对/相对 URL、query 都路由正确；v20/v2_extra/尾路径/其他 origin/query 假阳性不捕获 |
| R2 | 旧接口 table/list/campaign_list/campaigns、旧模板/消息兼容；v2 cell 产生相同 Campaign 业务字段与排序 |
| R3 | protocol/schema 不匹配、缺失容器、畸形 row/cell、重复关键 name、非 0 code 失败，不作空列表 |
| R4 | 两页 v2 完整聚合/去重；原 URL 保留 _v2；日期、page、active filter、gmv_roi_mode、query_list 等改写/保留正确 |
| R5 | v2 活动行可驱动既有阈值；page 混合 inactive 仅展示 active，live mixed 则拒绝，不评估部分结果 |
| R6 | 后页失败不提交 complete snapshot，不自动关停；报告窗口不覆盖关闭窗口；旧 concurrency/queue/generation 测试保持绿 |
| R7 | 自动/测试关闭成功后 fresh v2 回查：目标仍存在 → 失败；完整空 active 页 → 可确认；缺失/畸形 v2 → 不能确认、不误报成功 |
| R8 | fetch 与 XHR 捕获 _v2，经 background 接受模板（不仅 parser 单测）；坏 payload 不覆盖健康模板 |
| R9 | 无 operation:1 新调用，无真实网络 mutation，无敏感值/真实抓包进入插件仓；无新增 scheduler/dependency/storage schema |
| R10 | patch 版本五处一致，全量 test、coverage floor、tsc、build 全通过，owner 对照方案审查有记录 |

在**独立插件仓根目录**执行（不要用 ERP 的 Python isolated runner）：

```sh
npm ci
npx vitest run tests/campaign-list-contract.test.ts tests/core.test.ts
npx vitest run tests/live-campaign-fetch.test.ts tests/page-hook.test.ts tests/background.test.ts
npm test
npm run test:coverage
npx tsc --noEmit
npm run build
git diff --check
```

如果仓库 page-hook 实际测试名不同，使用既有对应文件的真实路径并记录，不创建平行的假 producer 测试。coverage 沿用 95% statements/functions/lines、90% branches，不降低阈值。无发布 archive 要求，因此不生成/提交 zip；build 产物保持 untracked。本次父仓只改文档，不运行 Python 全量 suite，也不接触数据库。

owner 审核输出写入 `03-verification.md`：被审核 commit / 基线、R1–R10 映射、各命令 exit/计数/coverage、文档脱敏及完整性证据、真实浏览器未验证项、最终分支/推送状态。未全绿或未解决偏离不得标记交付完成。

## 8. 风险、回滚与保留

- active-filter 私有协议未经实店验证：保守失败，不扩大查询/关闭范围；mock 不能证明服务端支持。
- 原始页面抓包包含 inactive，不可直接当 strict live 成功 fixture。
- 多会话正在开发 monitor 远程配置；独立 worktree、不接触 remote-config/storage，background 热点变更需协商并在交付前同步最新 origin/master，必要时停止等待归属解决。
- 旧接口是明确外部兼容边界，本次永久保留，不设暗中退役日期，也不引入自动 fallback。仅移除被 shared contract 替代的重复内部结构检查，不删除旧能力。
- 回滚使用 revert 本任务提交；可继续使用旧模板。不要切换持久化数据格式，无迁移、无数据清理、无真实广告副作用。
- 本方案锁定意图/基线/文件/验收；运行时出现新 schema、服务端过滤不支持或文件 ownership 冲突时回到 owner 决策，worker 不自行扩大范围。
