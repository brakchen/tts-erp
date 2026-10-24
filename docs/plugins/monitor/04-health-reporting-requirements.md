# Monitor 双层探活：需求与上报字段草案

> 状态：**需求讨论稿，字段建议待审阅；不是已批准的 API、数据库设计或实施计划。**
>
> 已确认：同时检测「插件失联」和「插件在线但监控失效」（方案 B）；字段必须详细列出并记录在文档。
>
> 未确认：节点登记方式、监控时段、心跳/扫描/告警超时、通知对象、重复提醒与恢复通知策略。本文不替用户确定这些产品规则。

## 1. 目标、边界与现状依据

目标：监控本应运行却不再推进时，由 **tts-erp 服务端** 发现异常并通过飞书机器人通知人；电脑端完全停止后，不依赖电脑端自行发出最后一条告警。

- 插件负责上报可核验的运行观测；ERP 负责接收、持久化、定时判定、去重和通知。插件不能用一个自报 `healthy=true` 代替具体证据。
- 「节点」暂定义为一个 Chrome profile 中的一次插件安装实例，不是每个广告计划。店铺身份与安装实例身份分开；最终登记和替换规则仍待确认。
- 「埋点」覆盖关键处理阶段，不是上传每次点击、广告明细、完整请求或本地全部日志。
- 只增加可观测性，不改变 TikTok 关停规则、白名单、SKU 保护、原有飞书报表或配置导入语义；不新增服务器操作 TikTok 店铺的能力。
- 本稿只涉及需求文档，不修改插件、服务端、数据库或生产服务。

### 1.1 已核对的当前实现

| 事实 | 依据 |
| --- | --- |
| 插件是 Chrome MV3，service worker 可被正常挂起；只能用 Chrome Alarms 做调度，不能把 worker 每次重建视为崩溃 | `plugins/monitor/AGENTS.md`、`plugins/monitor/README.md` |
| 扫描与报表各有独立 Alarm，慢工作开始前先登记下一次 Alarm；持久化的下次执行时间不等于真实 Alarm 一定存在 | `plugins/monitor/entrypoints/background.ts` 的调度与 Alarm 审计逻辑 |
| 设置已有 `monitorEnabled`、`autoCloseEnabled`、`status`、扫描/报表间隔、`nextScanAt`、`nextNotificationAt`；状态有 running/stopped/unbound/auth_required/error | `plugins/monitor/src/extension/storage.ts`、`messages.ts` |
| 已有绑定 Tab、seller/advertiser 身份、请求模板、完整快照标记；页面捕获的部分数据不能证明完整扫描成功 | `storage.ts`、`background.ts::performLiveCampaignFetch` |
| 当前 20 秒「心跳」服务于关闭失败复查，不是上报 ERP 的外部探活 | 插件 README 的关闭失败提醒说明 |
| 飞书请求目前由插件发出；ERP 有独立 APScheduler worker，本轮未在 `tts_erp_v2/` 找到现成飞书发送实现 | 插件飞书模块、[进程架构](../../architecture/process-architecture.md)及本次关键词检索 |

以上是当前检出源码的调查结果，不代表电脑端已安装同一版本。既有 [campaign-list 技术方案](02-technical-design.md)只覆盖协议兼容，不承载本需求；另一个 lane 的广告通知入库调研关注广告观测事实，不等同于健康上报。本稿不修改其文档，也不引入广告事实上传。

## 2. 上报方式与通用约定（建议）

采用「**独立周期心跳 + 关键状态变化触发补报**」：

1. 独立 Chrome Alarm 周期上报，即使监控开关关闭、广告页解绑或扫描任务卡住，也尝试上报当前状态；不得排在 TikTok 扫描/关闭队列后面。
2. 启动、绑定变化、监控开关变化、关键阶段成功/失败时更新健康快照，并合并/限流补报；不要求每次阶段变化都额外发一个 HTTP 请求。
3. 快照应能让下一次周期上报携带上次补报失败后的最新状态。请求必须有界，ERP 不可达不能阻塞原有扫描、关停或飞书报表。
4. 不把断网期间的旧心跳堆积重放成「当前存活」；恢复后发送新采样快照。完整历史事件审计不在本期范围内。
5. 字段使用 camelCase；时间是带时区 UTC 字符串；ID 用字符串；计数是非负整数。客户端时钟不可信，不能直接与服务器时钟相减来作失联裁决。
6. 必填指字段/对象存在；标记「可空」的 `null` 明确表示从未发生、不适用或尚未知，不能填当前时间、0 或成功来补缺。不同原因通过配套状态区分。
7. 心跳接收成功的 `2xx` 只在契约要求的持久化完成后返回；飞书发送失败不伪装成健康上报失败。沿用 [HTTP 成功语义](../../api/dumps-data-contract.md)，但**不把健康上报塞进广告 dump 协议**。

## 3. 客户端上报字段详细列表（建议版）

下面列出建议首版字段。标记「现有」仅表示可从现有状态安全投影，不表示已有此 wire 字段；标记「新增」表示实施时需要埋点或生成。所有子项组成明确 schema，不接受任意日志对象作为扩展字段。

### 3.1 协议与安装实例身份

| 字段 | 类型 / 必填 | 内容与来源 | 用途 / 约束 |
| --- | --- | --- | --- |
| `schemaVersion` | integer / 是 | 健康上报协议版本；新增，初版建议为 1 | 版本校验，不兼容数据不得静默接收 |
| `requestId` | string / 是 | 本次 HTTP 请求追踪 ID；新增 | 与 `x-request-id` 对齐；不是业务存活依据 |
| `nodeId` | string / 是 | 每次安装随机生成、持久化的实例 ID；新增 | 区分电脑/profile实例；不采集硬件指纹；不随配置备份复制到另一台设备 |
| `nodeLabel` | string / 可空 | 人工可读别名，如 `办公电脑A-浏览器1`；新增 | 帮人定位；不是授权身份，不自动采集用户名/主机名 |
| `extensionVersion` | string / 是 | 当前实际运行插件版本；现有 manifest | 定位版本差异，不使用父仓 submodule 指针代替 |
| `workerBootId` | string / 是 | 每次 worker 初始化生成的随机 ID；新增 | 区分进程内序列；正常 MV3 挂起/重建不报警，不能当作安装 ID |
| `heartbeatSequence` | integer / 是 | 当前 worker 内递增的快照序号；新增 | 检测重复/乱序；不同 boot 间不能直接比较大小 |
| `sampledAt` | UTC string / 是 | 采集这一致性快照的客户端时间；新增 | 排障用；不是服务器 `lastSeenAt` |
| `trigger` | enum / 是 | `periodic` / `startup` / `state_change`；新增 | 解释本次上报来源；不代表严重程度 |

鉴权凭证放受保护的 HTTP header，**不属于 JSON 字段**。服务端必须校验 key 对 node/shop 的权限，不能仅凭 `nodeId`、店名或客户端声称的身份接受归属。

### 3.2 监控意图、绑定和数据上下文

| 字段 | 类型 / 必填 | 内容与来源 | 用途 / 约束 |
| --- | --- | --- | --- |
| `contextId` | string / 是 | 当前业务上下文代号；新增 | 绑定、店铺、监控重启或影响扫描的配置改变时更新；旧上下文成功不得证明新上下文健康 |
| `monitorEnabled` | boolean / 是 | 本地监控开关；现有 | 区分本地停止与运行；**不自动授权服务端停止关注该节点** |
| `autoCloseEnabled` | boolean / 是 | 本地自动关停开关；现有 | 解释关停阶段是否应推进；不上传关停阈值 |
| `runtimeStatus` | enum / 是 | 现有 running/stopped/unbound/auth_required/error | 只作状态线索，不能只因 `running` 就认定正常 |
| `bindingState` | enum / 是 | `bound` / `unbound` / `tab_missing` / `wrong_page` / `unknown`；新增规范化 | 依据当前安全检查，不只读取持久化绑定对象；检查失败为 unknown |
| `sellerId` | string / 可空 | 当前已识别 seller；现有绑定 | 服务端映射与授权校验；未知不得继承上一店铺身份 |
| `advertiserId` | string / 可空 | 当前已识别广告账户；现有绑定 | 与 seller 联合定位业务范围；未知用 null |
| `shopDisplayName` | string / 可空 | 现有通知店名或已识别店名 | 仅展示，不按名字关联 ERP 店铺 |
| `requestTemplateState` | enum / 是 | `ready` / `missing` / `invalid` / `unknown`；新增投影 | 表示当前上下文是否有可用列表请求模板；不上传模板内容 |
| `requestTemplateCapturedAt` | UTC string / 可空 | 当前模板的 `updatedAt`；现有 | 排障；模板长期没有重新捕获不等于扫描停止，不能单独设过期告警 |

不上传 Tab URL、标题、Cookie 或完整请求模板。浏览器 Tab ID 仅保留在本地；告警定位使用 nodeLabel 与已授权店铺身份。

### 3.3 调度证据

| 字段 | 类型 / 必填 | 内容与来源 | 用途 / 约束 |
| --- | --- | --- | --- |
| `schedule.scanMinSeconds` | integer / 是 | 当前扫描最小间隔；现有 | 理解正常随机间隔，不由客户端控制服务端告警阈值 |
| `schedule.scanMaxSeconds` | integer / 是 | 当前扫描最大间隔；现有 | 同上；仍需额外执行耗时及容忍预算 |
| `schedule.nextScanAt` | UTC string / 可空 | 当前计划下次扫描时间；现有 | 与实际 Alarm 检查交叉解释；不是实际执行完成证明 |
| `schedule.scanAlarmState` | enum / 是 | `present` / `missing` / `not_expected` / `unknown`；新增投影 | 必须检查真实 Chrome Alarm；关闭/解绑时可 not_expected；API 异常为 unknown |
| `schedule.reportMinSeconds` | integer / 是 | 当前报表最小间隔；现有 | 解释报表调度 |
| `schedule.reportMaxSeconds` | integer / 是 | 当前报表最大间隔；现有 | 解释报表调度 |
| `schedule.nextReportAt` | UTC string / 可空 | 当前 `nextNotificationAt`；现有 | 与扫描分开，避免用报表成功掩盖扫描停止 |
| `schedule.reportAlarmState` | enum / 是 | 与 scanAlarmState 同一取值集；新增投影 | 真实检查，而不是照抄 nextReportAt |

心跳 Alarm 本身没有执行就无法主动报告自身故障，因此其缺失主要由服务端「未收到新心跳」兜底；不增加一套依赖自身正常运行的自证机制。

### 3.4 处理节点清单与覆盖范围

建议在每个心跳中包含 `stages`，下列五个键必须存在；不适用时明确标记，不省略成「看不见就当正常」。

| 节点键 | 表示的处理环节 | 成功的准确含义 | 不能冒充成功的情况 |
| --- | --- | --- | --- |
| `stages.scan` | 定时扫描完整编排 | 当前上下文的一轮定时扫描完成其应做的拉取和规则评估；实际关停通过独立节点跟踪 | 只收到 Alarm、只排了下一次 Alarm、报表请求成功 |
| `stages.campaignFetch` | 扫描所用 TikTok 列表拉取 | 全分页完成、严格解析通过、当前上下文有效、完整快照提交成功 | 页面捕获部分列表、只成功第一页、HTTP 200 但业务/解析失败；报表窗口成功也不能替代 |
| `stages.ruleEvaluation` | 扫描后的自动关停规则评估 | 在当前完整扫描快照上完成原有规则评估；无需存在命中计划 | 未执行评估、评估异常；autoCloseEnabled=false 时是 not_applicable，不伪造 success |
| `stages.autoClose` | 自动关停执行与确认队列 | 某个关停工作有明确终态，成功须沿用现有关闭后确认语义 | API 返回成功但未完成确认；无候选时不能每次心跳刷新一次「关停成功」 |
| `stages.report` | 定时报表拉取与插件飞书发送 | 本轮独立报表窗口完整拉取，且插件飞书接口按当前协议接受消息 | 只取到数据、仅开始发送、飞书超时；不能证明人已阅读 |

绑定/模板环节由 §3.2 明确字段覆盖；心跳传输环节由服务端接收时间覆盖。健康告警是 **ERP 发飞书**；`stages.report` 是原有 **插件业务报表**，两者不能混为同一投递状态。

### 3.5 每个处理节点的公共字段

下表的 `<stage>` 分别替换为 §3.4 的五个节点名。这是逐节点详细字段契约，不代表上传五份完整日志。

| 字段 | 类型 / 必填 | 更新时机及语义 |
| --- | --- | --- |
| `stages.<stage>.state` | enum / 是 | `idle` / `running` / `succeeded` / `failed` / `blocked` / `not_applicable` / `unknown`；等待业务前置条件是 blocked，采集不确定是 unknown |
| `stages.<stage>.runId` | string / 可空 | 当前或最近一次实际执行 ID；开始新执行时生成，心跳本身不得刷新；扫描、拉取、评估关联同一轮扫描 ID |
| `stages.<stage>.startedAt` | UTC string / 可空 | runId 对应工作实际开始时记录；仅排队不算开始 |
| `stages.<stage>.finishedAt` | UTC string / 可空 | 当前 runId 到达成功/失败终态才记录；执行中为 null |
| `stages.<stage>.lastSuccessRunId` | string / 可空 | 最近一次真正成功的执行 ID；失败后保留原成功，不用本次失败的 ID 覆盖 |
| `stages.<stage>.lastSuccessAt` | UTC string / 可空 | 与 lastSuccessRunId 对应；从未成功用 null；切换 context 后不能继承旧店铺成功 |
| `stages.<stage>.consecutiveFailures` | integer / 是 | 当前上下文中实际连续失败执行次数，成功清零；跳过、不适用和心跳重发不计次数 |
| `stages.<stage>.lastErrorCode` | enum / 可空 | 最近失败/阻塞的白名单代码；不上传任意异常文本；新执行成功后清空当前错误 |
| `stages.<stage>.lastErrorAt` | UTC string / 可空 | 对应错误首次在该次执行被确认的时间；重复心跳不刷新 |

补充规则：

- 重启后不得凭内存标记把持久化 `running` 当作真实仍在运行；需恢复/核对并标记 unknown 或明确中断。正常 worker 重建本身不是故障。
- 每个 stage 的运行记录必须与顶层 contextId 一致；构造心跳时如发生上下文切换，应重新采样，不把两个店铺拼在一个快照中。
- 报表有独立 runId。仅扫描消费者可更新 campaignFetch/ruleEvaluation 的健康进度，手动刷新、报表和关闭回查不得让已停止的定时扫描看似在推进。
- autoClose 无候选时为 idle，开关关闭时为 not_applicable；没有关停成功记录不是异常。
- 一个关停失败若已释放队列，应上报 failed，但不能误判为队列卡死。是否为每次关停失败再发 ERP 告警，需与插件原有失败通知去重后另行确认。

### 3.6 自动关停队列的补充字段

| 字段 | 类型 / 必填 | 内容与用途 |
| --- | --- | --- |
| `autoCloseQueue.pendingCount` | integer / 是 | 当前待执行数量；无候选为 0，不是异常 |
| `autoCloseQueue.oldestPendingAt` | UTC string / 可空 | 当前最早入队时间；用于发现长期排队，队列空为 null |
| `autoCloseQueue.activeRunId` | string / 可空 | 真正占用执行队列的工作 ID；用于跨心跳确认是否一直不释放 |

不上传计划 ID、名称、消耗、ROI、规则阈值、Cookie 或关停请求体。详细广告审计属于另一个需求，不为探活扩大采集面。

### 3.7 错误码建议清单

| 错误码 | 对应含义 | 首版判定注意事项 |
| --- | --- | --- |
| `tab_unbound` / `tab_missing` / `wrong_page` | 未绑定、Tab 消失、离开广告页 | 是否应告警取决于服务器期望该节点此时运行 |
| `request_template_missing` / `request_template_invalid` | 模板缺失/不可用 | 不能靠读取旧 campaigns 缓存伪造恢复 |
| `auth_required` | 原有明确登录/鉴权失败证据 | 不主动解密或上传 Cookie；未知错误不得推断登录过期 |
| `request_timeout` / `network_error` | TikTok 请求超时/网络失败 | ERP 本身收不到心跳时无法证明仅 TikTok 网络故障 |
| `upstream_business_error` / `response_invalid` | TikTok 业务错误/响应不符合契约 | HTTP 200 不代表阶段成功 |
| `alarm_missing` / `alarm_check_failed` | 本应有的 Alarm 缺失/检查失败 | missing 与 unknown 分开 |
| `evaluation_failed` | 原有规则评估异常 | 不改变原规则的成功/失败含义 |
| `close_failed` / `close_timeout` / `close_verification_failed` | 关停或后续确认失败 | 队列已释放与仍被占用分开观察 |
| `feishu_send_failed` | 插件原有报表发送失败 | 不是 ERP 健康告警投递失败 |
| `storage_failed` / `internal_error` / `context_changed` | 状态读写失败、未知内部错误、执行上下文已改变 | context_changed 是取消旧任务的正常分支时不累计业务失败 |

若连最小可信快照都无法采集/构造，不发送虚假成功：有界记录本地安全错误并重试；服务端最终以失联兜底。所有错误码需在实施时映射真实分支，并由测试证明；本表不声称当前源码已有所有分类。

## 4. 服务端自己保存/计算的字段，不由插件决定

| 服务端字段（Python/DB 使用 snake_case） | 来源与用途 |
| --- | --- |
| `registered_node_id`、授权 shop 关联 | 服务端登记与 key scope 校验；与客户端 nodeId 关联，不按店名猜测 |
| `expected_enabled`、期望运行时段及其时区 | 由人授权配置；不能由一次客户端 stopped 状态自动解除监控责任 |
| `first_seen_at`、`last_seen_at` | 服务器接收并持久化有效新鲜样本时记录；无效、重复、旧样本不续命 |
| `last_progress_received_at`、`last_success_received_at`（逐节点） | 只有接纳了新的实际执行/成功证据才推进；重复心跳不刷新；首次看到历史成功不直接视为刚刚成功 |
| `heartbeat_age_seconds` | 按服务器当前时间和服务器 last_seen_at 计算；不直接依赖 sampledAt |
| `health_state`、`health_reason`、`state_changed_at` | 服务端根据期望、进度和容忍预算计算；建议状态见 §5，尚未定最终状态机 |
| `incident_id`、`first_detected_at`、`last_notified_at`、`recovered_at` | 同一故障去重、重复提醒与恢复关联 |
| `delivery_state`、`delivery_attempts`、`next_retry_at`、安全错误码 | ERP 自己的飞书投递结果与有界重试；Webhook 接受不等于人已阅读 |

**新鲜性边界必须在技术设计中补齐：** workerBootId 是随机 ID，不能比较大小；仅去重 `(bootId, sequence)` 不足以防旧 boot 迟到包续命。跨 boot 的接纳/隔离、启动握手或服务器 generation、请求在途时限与首次观测宽限须定稿后才能实施。重试旧请求不得刷新新 context/boot 的健康。

如果要保留历史心跳，保留时长、采样粒度和清理策略须另行确认；不能默认永久存储每条心跳或直接新建一套通用埋点平台。

## 5. 字段如何转化为可行动的异常

以下是**判定需求示例，不是已确认阈值**。所有业务异常均以服务器已登记且当前应运行的节点为前提。

| 场景 | 需要的字段/证据 | 预期表达 |
| --- | --- | --- |
| 电脑断网、休眠、浏览器退出、插件禁用 | 没有新的有效心跳 + 服务器 last_seen_at | 「节点失联」，不谎称已查明崩溃原因 |
| 心跳在，但监控开关被关闭 | monitorEnabled=false + 服务端 expected_enabled=true | 「本应监控但本地已停止」；是否维护静默需授权 |
| 心跳在，广告页解绑/离开/消失 | bindingState、身份、模板状态 | 「在线但广告页不可用」，与整机失联区分 |
| 心跳在，但扫描 Alarm 缺失或长时间没有新 runId | 调度证据 + stages.scan | 「在线但扫描未推进」，不被报表成功掩盖 |
| 扫描一直 running 或请求连续失败 | scan/campaignFetch 执行 ID、始终未完成、错误与失败计数 | 区分执行卡住与反复失败；不能只数心跳次数 |
| 数据拉取成功，但规则评估停滞 | campaignFetch 新成功 + ruleEvaluation 进度；autoCloseEnabled | 「在线但规则评估异常」；开关关闭时不得报该故障 |
| 自动关停队列长时间不释放 | pendingCount、oldestPendingAt、activeRunId 和 autoClose 阶段 | 「关停执行/排队停滞」；无候选不报警 |
| 插件飞书报表持续失败 | report 阶段、实际报表调度、错误码 | 「监控在线但业务报表异常」；ERP 使用独立凭证发健康告警 |
| 故障后收到心跳，但扫描仍失败 | 新鲜心跳 + 仍未恢复的 scan/fetch/evaluation | 仅标记失联恢复，不提前宣称整个监控恢复 |
| 服务器已登记，插件从未上报 | 登记时间、启用时间、首次观测宽限 | 宽限后发现未上线；不能只扫描有过心跳的节点 |

服务端扫描与飞书通知也是故障域：ERP/API、扫描 worker 或出站飞书整体故障时，这套机制本身可能无法报警。需明确残余风险和现有服务器运维监控的覆盖，不宣称它能发现自身所有故障。

## 6. 明确禁止上报的信息

- TikTok Cookie、Authorization、ERP API key、访问/刷新令牌、完整请求头或 URL 查询参数。
- 飞书 Webhook URL、签名密钥、App Secret、收件人手机号/邮箱；告警联系人在服务端配置，不靠心跳传递。
- 全部本地配置/导出备份、广告列表与明细、广告计划名/ID、财务指标或阈值快照。
- 原始接口响应、任意异常堆栈、可包含秘密的自由错误文本、全部本地日志。
- 浏览历史、无关 Tab、操作系统用户名、硬件/设备指纹；IP 不作为节点身份，本期不为健康探活主动新增 IP 采集字段。

服务端日志同样不得记录鉴权头或完整秘密；schema 长度/枚举/请求大小上限、速率限制和鉴权范围须在技术设计中明确，不信任任意 JSON 透传。

## 7. 待逐项确认的产品决策

1. **运行时段**：24 小时值守，还是指定工作时段/时区？正常关机、用户主动停止、解绑如何处理？
2. **节点登记与替换**：几台电脑/几个 profile；是否人工登记别名与负责人；安装重建、备份恢复、旧设备退休如何处理？
3. **时效**：多久心跳一次；失联多久首次报警；扫描无进展、连续失败和队列停滞分别容忍多久？不能共用一个未经讨论的超时。
4. **通知**：目标群/负责人、是否 @、是否复用现有服务端机器人配置（若存在需核实）、重复间隔/上限、恢复通知。
5. **异常范围及去重**：关停单次失败、插件报表失败是否纳入首版服务端告警；如何避免与插件原有通知重复轰炸。
6. **暂停与维护**：谁能暂停服务端期望，暂停是否到期自动恢复，不能让客户端误操作永久静默。
7. **界面与留存**：首版是否需要 ERP 节点列表/状态页，还是只发飞书；最新状态与故障记录保留多久。

## 8. 验收草案

- 有效新心跳能表示节点在线；重复/乱序/延迟旧包不能使失联节点假恢复；客户端时钟偏差不改变失联裁决。
- 停止浏览器或切断电脑网络后，服务端能在**确认后的告警时限**内发出可定位节点/店铺的失联消息。
- 心跳继续但定时扫描不再推进时，仍能告警；手动刷新、旧缓存、报表成功都不能掩盖。
- 解绑、明确登录失败、列表解析失败可分别定位；真实完整空列表算成功，不当作接口没数据。
- 未发生自动关停、正常 MV3 worker 重建、非运行时段不得误报；应运行却本地停止不能被自动免除。
- 切换店铺/上下文后旧成功不续命；历史成功和首次启动有明确、可测的宽限处理。
- ERP 短时不可达、飞书失败均不阻塞插件原有业务；健康告警的重试、去重、恢复通知按确认策略可验证。
- 请求、数据库、日志和飞书消息不包含 §6 禁止内容。

当前仅形成字段与验收草案，未运行插件/服务端行为测试，未做真实 Chrome/TikTok/飞书验证。未来插件实施必须遵守其 AGENTS.md 的严格 RED→GREEN→REFACTOR；ERP 测试只经 [隔离测试规范](../../guides/agent-testing.md)。
