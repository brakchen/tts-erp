# Monitor 最小探活实施计划

## 1. Goal / Architecture / Tech Stack

- **Goal**：按已批准的[四字段需求](04-health-reporting-requirements.md)，实现插件独立心跳与内部业务埋点、ERP 持久化与定时扫描、统一飞书群首报和恢复通知。电脑端完全停止后仍能由服务器发现。
- **Architecture**：插件是事实生产者；ERP monitor-health 域是当前健康与通知状态的唯一 owner；现有 API/auth、SQLAlchemy、sync-worker APScheduler 和运行配置分别承载接线。只增加一份必要的店铺当前健康持久状态，不建安装实例、历史事件、通知平台或额外配置系统。
- **Tech Stack**：现有 TypeScript/WXT/Chrome Alarms/Vitest；Python/FastAPI/Pydantic/SQLAlchemy/PostgreSQL/APScheduler/HTTPX。沿用已安装依赖，不引入新 SDK。
- **Baseline / Authority**：用户已审阅最小需求，并明确授权多 agent 并行开发；根 AGENTS.md、插件 AGENTS.md、docs/guides/agent-{safety,testing,git-workflow}.md、docs/architecture/{architecture-overview,process-architecture}.md、docs/ops/runtime-config-management.md。服务器基点 `4bcef08df77172b6cb48007267d3927e4078ca19`，插件基点 `73789efeac42cd1e92feedd05bf520acbe2b5a9d`。
- **Compatibility Boundary**：不改广告公式、关停资格、队列、原有业务飞书、middleware 顺序；不接管 ROI/自动绑定/perf 的保留写域。不执行生产迁移、服务重启或真实广告/飞书操作；发布配置和真实浏览器验证另行明确证据。
- **TDD Route**：mode=auto，decision=strict。插件 AGENTS.md 明确要求 strict；服务器涉及契约/权限/持久化/生产消费两端，auto 同样选择 strict。每个实现先行为性 RED，再最小 GREEN、相关回归；缺模块/import 错误不算有效 RED。父级最终运行 isolated baseline，插件还需全量 tests/coverage/tsc/build。
- **Verification**：服务器只经 `bash scripts/test_isolated.sh ...`；最终 `bash scripts/test_baseline.sh` 一次，新增失败只重跑最小选择。插件本仓 npm/vitest 工具，不调用 ERP runner。自动化通过不等于已在用户电脑安装、生产数据库迁移或飞书实发。

为保持并行 writer 不撞共享入口，本计划把生产者和消费者拆成独立组件，保留一名父级集成 owner，先完成组件再串行接线。

## 2. Readiness、必要性与复用

需求来源、24 小时、shopId 唯一/店名展示、独立心跳、全关停流程、两个 3 分钟规则、主动停止仅抑制业务告警、首报与恢复各一次、统一群 Webhook 均已确认。字段/枚举已获用户此次实施授权，不再次请求重复审批。

代码必要性：现有插件仅在本机发业务飞书，ERP 没有 monitor 健康接收/判定能力；配置或文档修改不能发现电脑端死亡。最小改动为独立上报器、一个健康域及现有边界接线。

复用调查：

- 本仓已有 Chrome Alarms、真实 seller 身份、通知显示店名、严格 HTTP 与隔离测试、运行配置、token_service 和 APScheduler。优先复用，不再造 scheduler/配置/凭证加密。
- 已搜索官方文档、GitHub/PyPI：Healthchecks（https://github.com/healthchecks/healthchecks ，BSD-3-Clause）有成熟 dead-man-switch 模式，但整套 Django/平台引入不符合 ERP owner 与最小实体要求；不复制源码，只采用缺失进展由服务端判断的常见模式。
- Chrome Alarms 官方接口 https://developer.chrome.com/docs/extensions/reference/api/alarms ：Chrome 120 起最短 30 秒，可能延迟，必须允许 MV3 worker 正常重建。Google 页面直取超时，官方搜索结果与插件现有 scheduler 已交叉核对；不得用 setInterval/offscreen 保活。
- APScheduler https://apscheduler.readthedocs.io/en/3.x/userguide.html 、PyPI APScheduler/HTTPX：直接使用项目已锁定版本，库包版本/许可证以锁文件为准；不新增依赖或复制实现。飞书官方 https://open.feishu.cn/document/client-docs/bot-v3/add-custom-bot 为 SPA，抓取仅得标题；发送成功语义还必须结合现有插件飞书代码验证业务 code，不能只看 HTTP 200。

ArchitectureReviewRequired=true：新增公开契约/权限/持久化/producer-consumer。唯一健康状态归 ERP；插件仅描述事实。持久状态用于重启后保留基线和通知去重，现有广告日志/发布 worker 心跳语义不匹配，因此最多新增一个健康当前态表，而不是滥用日志或新建完整平台。

## 3. 冻结的两端契约

### 3.1 请求

`POST /v2/plugin/monitor/health`，JSON **只允许**：

| 字段 | 约束 |
| --- | --- |
| shopId | 非空 string，真实店铺稳定标识，最大 128 字符；不能用店名或 advertiserId 代替 |
| shopName | string，最大 200 字符；空串允许，消息回退显示 shopId |
| step | heartbeat / scan / evaluate / close |
| status | heartbeat 仅 running/stopped；其他仅 started/progress/completed/failed，其中 evaluate 不需要虚构 progress |

拒绝 extra、错误类型和无效 step/status 组合；小请求体上限 4 KiB。时间由服务器接收时记录。只允许 readwrite+，保留现有 API key 鉴权/店铺 seller scope；不能把 advertiser-only scope 当作任意 shop 授权。HTTP 成功必须在持久化完成后返回；204 即可，不增加并行业务状态字段。

已核对插件 `src/core/tab-binding.ts`：身份取 URL 的 `oec_seller_id`，其次 `seller_id`。本契约 shopId 使用这一稳定 Seller Center 店铺身份，与既有 `seller:<id>` scope 字面匹配；不按 display name 关联 commerce 主键，不声称它与 OpenAPI shop_id 或内部 shop_pk 等价。准确来源未识别时不能虚构可用店铺身份。

### 3.2 处理/告警语义

- 接收 heartbeat/running 或 stopped 时更新在线证据与监控意图；stopped 立即抑制业务超时，心跳丢失仍检查。恢复 running 为业务建立新观察起点，不继承停止期间超时。
- scan 表示完整广告列表扫描；evaluate 表示本次处理的**首次真实 canonical 规则判断**，只在原有 `evaluateAutoCloseCandidate` 首次实际返回后 completed，不做全批资格预计算。close 表示之后整个处理流程，包括后续逐条资格复查、跳过、提交、通知等待与共享关停回查；后续失败汇总到 close/failed，不追改真实完成的首次 evaluate。无候选/确实不适用可正常结束；失败不变成 completed，不变更已有广告执行结果。
- 定时扫描可能加入同 generation 下由页面捕获先启动、首次判断已完成的 canonical drain。迟加入只观察当前 close、未来真实单位进展和整个 drain 的实际聚合结果；不补发过去的 evaluate 或已提交进展。ERP 在 scan 已完成、当前等待 evaluate 时接受这一 close 当前阶段事实（首个可为任意合法 close 状态），不虚构 evaluate 成功，也不发送其恢复。已有 evaluate/其他失败的独立时钟与同阶段恢复条件保留，close 不能越过尚未完成的 scan；重复 close/started 不续命。
- 只检查当前应执行环节与下一轮扫描是否按时启动；不把等待上游的环节或正常批量总耗时当作无进展。started 不能反复续命，真实 progress/completed 才推进成功进展基线；普通心跳不能刷新业务进展。
- 完全失联首报目标 180 秒内：心跳 30 秒，服务端 scan 15 秒且本 job 的 jitter 为 0，失联判定 150 秒，整轮通知总预算不超过 10 秒，留有余量；不是等满 180 秒再扫描。内部阶段按 180 秒无进展判异常，15 秒扫描附加时延要在结果中如实说明。上述为固定实现常量，不增加客户端阈值配置。多个 16 KiB UTF-8 消息块共用一次请求总预算，不能每块再独享 8 秒或每个 15 秒 tick 只发一块；只确认已成功接受的前缀，其余保持待发，部分失败明确返回 degraded 并提交已有确认。
- 同一故障已成功通知后不再发送；失败允许有界重试且不标已通知。每个故障恢复后一次恢复消息；主动停止不冒充业务恢复。事务与并发保护需防止多个扫描器重复成功投递、故障变化后旧发送结果误覆盖。
- 不重放旧埋点，不无限积累 HTTP 任务；正常网络失败后只上报当前心跳意图和新发生的工作，传输有界且独立于广告执行队列。序列化与合并只在上报器内部，不另造业务调度器。

### 3.3 固定配置

复用 ERP 已发布运行配置；本功能建议固定键 `plugin.monitor_health.v1`，payload 使用 `feishuWebhook: "secret://monitor-health-feishu"`。若部署已有对应键，接线时对齐而不自动覆盖；不查询或修改生产真实配置来开发。

Webhook 只能由服务端经 token_service 解密，普通 snapshot 仅 secret 引用。HTTPS 官方飞书机器人域名、有限请求时限与业务响应 code 校验；不得在异常中泄露完整 URL/secret。缺配置/停用/无发布版本必须明确失败而不是吞掉通知。没有配置不阻止健康事件按约定持久化。

插件当前基点没有 ERP transport 配置：组件先接受调用方提供 server URL/key；后继接线再最小增加必要 transport 设置并复用现有设置保存/UI，不把它们加入四字段埋点。不猜密钥、不无鉴权开放服务。

## 4. 并行任务与文件 owner

拓扑是 multi-seam：独立且可单测的 ERP 消费/判定域，以及插件生产/传输域；不是多 writer 争改同一后台入口。

### A. ERP 健康组件（独立工作树，服务端 child）

文件：新增 `tts_erp_v2/plugin/monitor_health/**`、`tts_erp_v2/api/v2/monitor_health.py`、`tts_erp_v2/db/models/monitor_health.py`、`alembic/versions/0070_monitor_health.py`、`tests/unit/test_monitor_health*.py`、`tests/api/test_monitor_health.py`。

交付：严格契约/授权/大小限制；纯状态推进与异常计划；最多一个健康当前态表和加法 migration；原子 upsert/停止与恢复/通知去重；固定配置+secret读取及有界 Webhook 发送；暴露 `run(session)` 供现有 system job 接线。

测试：行为 RED→GREEN（不可只报 import RED）；覆盖 179/180 秒边界、重复开始、有效进展、无候选、上下游等待、停止与重启、先失联后业务仍未恢复、重名不同 ID、未知 scope/readonly、extra/4KiB、Webhook HTTP200业务失败、缺配置、发失败重试/并发/恢复。命令 `bash scripts/test_isolated.sh --template-db tts_erp_test_monitor_health_template fast tests/unit/test_monitor_health*.py tests/api/test_monitor_health.py`（shell 展开只限实际新增文件）。migration 仅通过同一官方 runner 的私有模板 `--refresh-template` 验证，不刷新其他 0069 lane 使用的默认模板，不运行生产升级。

不得改 app/auth 全局策略、model registry、scheduler 或其他测试；父级后续接线。不得新建通用 outbox/事件/实例表或删除任何事实。

### B. 插件最小生产者（独立插件工作树，插件 child）

文件：仅新增 `src/core/health-reporting.ts`、`src/extension/health-reporter.ts`、`tests/health-reporting.test.ts`、`tests/health-reporter.test.ts`。

交付：四字段类型与构造、来源/状态约束；独立心跳 Alarm 协调能力与有界 HTTP transport；主动 stop/running、真实 checkpoint、latest-only/顺序/失败释放。通过显式依赖接收 settings/identity，不自行扫描其他店铺、不读取 Cookie/真实配置。

测试：先最小行为 RED，再实现 GREEN；验证确实到达异步边界、失败不堵塞、停止仍心跳、旧事件不重放、四字段/无敏感信息、同店名不同 shopId 不混淆。运行 `NODE_ENV=test npx vitest run tests/health-reporting.test.ts tests/health-reporter.test.ts`，加 tsc/必要 WXT prepare。parent 持有 Git；不得修改已有 background/storage/messages/popup/manifest/version 文件。

### C. 串行接线与交付（父级，组件交付后）

1. 独立 spec-compliance 审查，再 quality/security 审查；接受的问题回到同一 writer 修复，不接受 self-report 代替验收。为每组件形成可恢复的提交/报告后再集成。
2. ERP 只编辑 `tts_erp_v2/{app.py,db/models/__init__.py,access/_policy.py,sync_worker/scheduler.py}`：挂新 router/model、精确 POST readwrite、注册 `plugin.monitor_health` 独立 15 秒 system job。既有 JobSpec 增加可选 jitter 覆盖值，仅本 job 取 0，其他 job 仍沿用原全局 jitter。不得改 middleware 顺序。新增专属 wiring tests；仅在隔离工作树更新既有 scheduler registry 测试的新增 job/count/jitter 契约，主仓 foreign WIP 不移植、不修改。
3. 插件共享入口由 perf/ROI/auto-bind owner 明确交接后再写。把埋点接入真实 scan/evaluate/close 分支、主动停止和 Alarm；加入必要 server URL/key 设置与 host permissions。交接前该部分诚实 blocked，不把独立组件宣称完整功能。
4. 仅获交接后同步插件版本元数据/README/CHANGELOG，执行 focused→全 suite→coverage→tsc→build；需要 ZIP 时验证 manifest/hash，不把 mock 当真实 Chrome/飞书验收。
5. 更新本组文档、运行配置使用说明、`docs/api/external-api.md` 探活专属段落，以及 migration 对应的 `docs/schema/README.md` / `schema_tts_erp.sql`；SQL 快照仅由现有 `scripts/regen_schema.py` 从已到 head 的独占测试模板只读生成，不手改。共享文档写域须先在 board 确认交接，不越权写。
6. ERP narrow isolated tests 与一次 `TTS_ERP_TEST_TEMPLATE_DB=tts_erp_test_monitor_health_template bash scripts/test_baseline.sh`；所有本 lane 的 0070 验证只使用该私有模板。主动停监控→业务免告警→关浏览器失联、正常心跳但 scan 卡住、无候选 close 等用户路径必须有跨端契约/集成证据。生产迁移/配置/安装/重启由人操作，明确未验证边界。
7. 明确 paths staging、提交推送；满足 current-master 同步、锁内 prospective merge/验证/push 与 board reconciliation 后才能声明 integrated。插件指针仅在有意推进时修改，不把插件源代码暂存父仓。

## 5. 风险与停止条件

- 共享插件文件当前保留给其他 lane，组件之外必须等明确交接；不碰 lost-child dirty WIP。
- 四字段协议不提供跨重启执行代号：若真实测试证明现有串行/不重放不足以防旧意图覆盖，先给缺口证据再讨论必要扩展，不暗增版本/ID字段。
- 当扫描配置超过业务 3 分钟阈值时提示契约冲突，不暗放宽告警，不为配合本功能偷偷改用户业务配置。
- 飞书/API/worker 不可用时无法承诺端到端成功通知；只在对应可用验收条件下证明预算，生产配置与 live 验证不伪装完成。
- 新库无必要；新 schema 只加必要当前态。不退休原有报表、关停失败提醒或任意事实。禁止无关重构、自动接管其他 lane、生产迁移/重启及真实上游请求。

执行路线：subagent-driven，两个已分区 writer 并行，父级集成串行；用户已授权，无需重复确认。所有 child 先报告真实 session UUID，父级登记 reservation 后才允许写入；父级保留 Git lifecycle 与最终验收权。
