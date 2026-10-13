# ads-monitor 通知入库与多店铺广告看板调研

> 状态：调研结论，待业务决策；不代表已批准实施。
>
> 目标：让多个店铺的广告监控事实先落入 tts-erp，再由 ERP 页面、BI 和飞书通知消费同一份数据，避免群消息混杂且无法回查。

## 结论

推荐把 **ERP 内可校验的结构化广告监控观测** 作为唯一业务记录；飞书仅是该记录持久化成功后的异步投影，卡片正文不再作为数据源或审计依据。

推荐 MVP 路径：

```text
TikTok 广告页实时观测
  → ads-monitor 以专用 API key 写入 ERP
  → ERP 单事务保存观测批次、计划事实与阈值快照
  → ERP 页面 / 受控 BI 视图读取相同事实
  →（下一阶段）ERP outbox 异步发送飞书
```

这比“从飞书卡片回采”可靠，也不应把 monitor 的阈值、通知及关停结果塞入既有广告 dump 协议。

## 已核验现状

1. `ads-monitor` 的定时飞书通知会重新拉取 TikTok 实时广告计划，再直接调用飞书 webhook；当前没有把报告/通知事实写入 ERP。参见 `plugins/monitor/entrypoints/background.ts` 中的 `sendScheduledFeishuReport()` 与 `sendFeishuReport()`。[R1]
2. 插件已有 `sellerId`、`advertiserId`、可选店铺名、计划 ID/名称/状态、消耗、币种、ROI、平均订单成本、SKU 订单数、更新时间及阈值判定所需字段。参见 `plugins/monitor/src/core/types.ts`、`src/core/thresholds.ts`、`src/core/feishu.ts`。[R1]
3. 目前的飞书卡片包含计划、消耗、阈值、超阈状态、ROI、单均成本、SKU 订单、触发时间和店铺名；HTTP 请求只有 20 秒 deadline，并非服务端持久 outbox。参见 `src/core/feishu-http.ts`、`entrypoints/background.ts`。[R1]
4. ERP 已有受限插件写入先例：`POST /v2/analytics/sync/dumps` 使用 `readwrite` API key、按 seller/advertiser scope 授权、请求 ID、幂等处理与安全错误信封；但它的语义是 TikTok HTTP dump，不是广告监控事件。[R2]
5. `plugin.ad_daily` 是按 `(seller_id, advertiser_id, endpoint, campaign_id, product_id, day)` 存放的日粒度广告事实，且已有只读浏览页。它适合广告事实核查或对账，但不能完整表达某次轮询的阈值、规则版本、通知和关停结果。[R3]

### 现有缺口

| 维度 | 现状 | 缺口 |
| --- | --- | --- |
| 历史事实 | Chrome `storage.local` 和飞书卡片 | 换设备、卸载插件、清理本地状态或 webhook 失败后不可回查 |
| 店铺身份 | `(sellerId, advertiserId)`，店铺名可空 | 未形成到 ERP `shop_pk` 的显式、fail-closed 映射 |
| 规则审计 | 本地阈值与单计划覆盖 | 无规则快照，无法回答“当时为何超阈/关停” |
| 通知投递 | 插件直接发飞书 | 无 durable outbox、投递尝试、失败重试或送达记录 |
| 消费方式 | 单店铺插件界面、混在群里的消息 | 无多店汇总、权限裁剪、明细钻取或 BI 语义层 |

## 方案比较

| 方案 | 流程 | 优点 | 问题 | 结论 |
| --- | --- | --- | --- | --- |
| A. 从飞书消息回采 | 飞书卡片 → 解析 → ERP | 插件改动少 | 卡片是展示格式；模板/漏发/重复/权限都会破坏事实 | 不采用 |
| B. 插件直写 ERP，ERP 投影飞书 | 观测 → ERP → 页面/BI/outbox → 飞书 | 可追溯、可重放、飞书失败不丢事实 | 需要新 API、表、插件重试队列 | **推荐** |
| C. 先接消息队列/事件平台 | 事件总线 → 多消费者 | 大规模多下游时解耦好 | 当前单 Chrome 插件和本地 ERP 的运维成本过高 | 非 MVP |
| D. 复用 `/analytics/sync/dumps` 推导通知 | dump → 推导 | 可复用旧端点 | 现有协议与 monitor 快照、阈值、关停语义不匹配 | 仅作后续广告事实对账补充 |

## 推荐架构

### 1. 事实先入库，投递后置

插件在每次成功取得 **notification 窗口** 的完整计划列表后，生成一份不可变观测批次；即使计划列表为空也应可保存，从而区分“本轮确实无计划”与“本轮没有数据”。

建议新增独立端点（路径为建议，实施前由 API owner 定稿）：

```http
POST /v2/plugin/monitor/events
Authorization: Bearer <专用 readwrite key>
Idempotency-Key: <sha256>
Content-Type: application/json
```

最小请求语义：

```json
{
  "protocolVersion": 1,
  "requestId": "uuid",
  "clientBatchId": "uuid",
  "scope": {"sellerId": "...", "advertiserId": "..."},
  "capturedAt": "2026-10-13T18:00:00Z",
  "reportWindowDays": 90,
  "isComplete": true,
  "rules": {"version": 1, "...": "阈值与白名单快照"},
  "campaigns": [{"campaignId": "...", "cost": "12.3400", "currency": "USD", "...": "观测与判定字段"}]
}
```

服务端在一个事务中执行：scope 校验 → 解析唯一 `shop_pk` → 幂等检查 → 写批次与明细 → 写安全 ingest 审计/待投递 outbox。成功 `2xx` 必须表示解析与约定持久化已经完成；不得用 `rowsWritten` 之类旁路字段表达成功。

### 2. 建议数据模型

以下是建议，不是现存 schema。

- `plugin.ad_monitor_observation_batches`：`id`、`client_batch_id`、`shop_pk`、`seller_id`、`advertiser_id`、`captured_at`、`report_window_days`、`received_at`、`request_id`、`payload_version`、`campaign_count`、`is_complete`、`payload_hash`。
  - 唯一键：`(seller_id, advertiser_id, client_batch_id)`。
  - `shop_pk` 只能由服务端 scope/店铺映射生成；禁止从 `shopName` 推断。
- `plugin.ad_monitor_campaign_observations`：`batch_id`、`campaign_id`、`campaign_name`、`status`、`cost numeric`、`currency`、`target_roi numeric null`、`roi numeric null`、`average_order_cost numeric null`、`sku_orders numeric null`、`source_updated_at`、`threshold_snapshot jsonb`、`decision jsonb`、`row_hash`。
  - 唯一键：`(batch_id, campaign_id)`。
  - 金额使用 PostgreSQL `numeric`，wire 传十进制字符串；阈值和严重性由服务端权威计算或保存判定快照，前端只展示。
- `plugin.ad_monitor_actions`（第二期）：记录 `candidate`、`close_requested`、`close_verified`、`close_failed` 等浏览器关停审计，和观测事实分离。
- `plugin.notification_outbox`：`aggregate_type/id`、`channel`、`template_version`、`dedupe_key`、`state`、`attempt_count`、`next_attempt_at`、`last_error_code`、`sent_at`。
  - 业务事实与飞书投递状态分表；飞书失败不能回滚或重写观测事实。

### 3. 幂等与重试

- 客户端生成稳定 `clientBatchId`；传输幂等键建议为 `sha256(plugin_installation_id + client_batch_id)`。
- 同一幂等键但 canonical payload hash 不同，服务端返回 `409 IDEMPOTENCY_CONFLICT`，不静默覆盖。
- 通知去重键建议为 `sha256(channel + event_type + batch_id + template_version)`；失败提醒以失败 episode 加 reminder ordinal 去重。
- 插件对网络错误、超时、`429` 和 `5xx` 使用有上限的本地 pending queue、指数退避与 full jitter；`400/401/403/409` 不重试，`413` 需缩小载荷或拆分。
- ERP outbox 采用至少一次投递；由幂等键抑制重复飞书。

## 身份、权限与安全边界

1. **身份映射是 P0 前置条件。** `(sellerId, advertiserId)` 必须由服务端映射到唯一 `commerce.shops.shop_pk`；未映射或歧义时 fail closed（拒绝或隔离待人工处理），绝不能用 `shopName` 猜测。
2. 插件使用专用最小权限 `readwrite` key，并强制既有 seller/advertiser scope 约束；不要复用浏览器会话或 TikTok Cookie。[R2]
3. 人类页面使用现有会话与新的 `page:ad-monitor` 页面权限；所有多店查询先按可访问店铺裁剪，再聚合。
4. webhook URL、签名 secret、飞书 App Secret、API key、TikTok cookie、原始 Authorization header 和完整 TikTok HTTP body 都不能进入事件 payload、BI view、日志或错误回显。
5. 输入需限制 body/行数/字符串长度、时间范围和 decimal 精度；以 `requestId`、`clientBatchId`、`batchId` 串联全链路观测。

## 页面与 BI

### ERP 页面：首选运营入口

建议新增“广告监控”只读页：

- **多店摘要**：店铺、最近成功采集、活跃计划数、消耗、超阈数量、待处理关闭失败、最后通知状态；默认只显示有权限店铺。
- **计划明细**：按 `shop_pk`、批次/时间、计划、超阈和关停状态筛选与钻取。
- **事件详情**：展示规则快照、事实行、outbox 尝试摘要、飞书深链/事件 ID；不展示敏感数据。
- 后续可有确认、静音、备注等 ERP 内工作流；**不**通过 ERP 新增写 TikTok 店铺的 endpoint，自动关停仍由已绑定同店铺 Tab 的插件负责。

### BI：与页面读取同一事实

对 BI 不直接授予 `plugin.*` 原表权限，而是提供受控只读视图，例如：

- `analytics.v_ad_monitor_campaign_latest`：每店每计划最新观测；
- `analytics.v_ad_monitor_daily`：按采集时刻聚合的消耗、超阈数、通知/投递状态。

视图仅暴露已映射店铺、必要维度/指标和非敏感文本。为 BI 创建专用数据库用户，只允许该视图的 `SELECT`，使用 TLS/私网连接，不授予 `ad_raw_log`、secret schema 或写权限。

**工具选择：**

- **Metabase（推荐 MVP）**：适合运营自助筛选、保存问题和仪表盘；官方支持 PostgreSQL 连接、SSL 和 schema 管理。[R5]
- **Grafana（备选）**：适合 freshness、outbox backlog、投递延迟等时序运营指标与告警；官方建议 datasource 使用最小 `SELECT` 权限。[R6]
- **Superset（备选）**：若组织已运营 Superset、需要更复杂 SQL 数据集治理，再接入 PostgreSQL。[R7]

本调研未验证上述 BI 是否已部署、SSO/网络/TLS、许可和责任人；这些不是工具选型事实。

## 飞书投递

在 P1 过渡期，插件可继续直接发飞书，但只有 ERP 成功入库后才发送，并在卡片加入不可猜测的 `eventId`/详情深链。P2 后由 ERP outbox worker 接管卡片渲染与发送，插件不再保存或发送 webhook。

飞书自定义机器人官方限制单租户单机器人 **5 次/秒、100 次/分钟、请求体不超过 20KB**；签名校验要求 Unix 秒级 `timestamp` 和 `sign`，可配置关键词与 IP 白名单。[R4]

因此 outbox 必须：

- 按机器人限速；
- 对网络错误、超时、`5xx` 和可确认的 `429` 做有限退避重试；
- 对签名、卡片校验、配置等 `4xx` 进入 dead-letter 并可见；
- 只记录 HTTP 状态、飞书安全错误码、尝试时间和追踪 ID，不记录 webhook。

## 分期实施

| 阶段 | 交付 | 完成条件 |
| --- | --- | --- |
| P0：契约与身份 | 映射权威来源、事件粒度、保留期、群归属、权限模型；API/迁移/outbox ADR | 未映射/歧义店铺的 fail-closed 行为已确认 |
| P1：写入 MVP | 新表、专用 API、scope/幂等测试、插件有界重试队列 | 重放、超时后重试、scope 拒绝、未映射、429 均可验证 |
| P2：ERP outbox | 服务端模板、签名、限速、retry/dead-letter、投递历史 | 入库成功而飞书失败时可重试且不重写事实 |
| P3：页面与 BI | `page:ad-monitor`、多店列表/详情/权限测试、BI 视图及 Metabase 初始看板 | 页面与 BI 显示同一批次和同一汇总口径 |
| P4：强化 | 关停审计、趋势、保留/聚合、备份恢复演练 | 有吞吐与多下游证据时才评估 event bus |

## 待业务确认

1. 需要保存每次完整轮询、仅超阈计划，还是仅状态变化？空列表是否也持久化？
2. `sellerId + advertiserId` 到 `shop_pk` 的权威映射在哪里维护？未映射时阻断、隔离池还是管理员匹配？
3. 明细批次、规则快照、计划名称和关闭失败原因的保留期限分别是多少？
4. 飞书是否最终由 ERP 唯一发送？每店独立群/机器人还是共享机器人？谁负责 webhook 轮换？
5. 页面用户是否需要店铺级行权限？BI 是否只允许可信管理员，还是也要行级隔离？
6. 第一套 BI 是 Metabase、已有 Superset 还是 Grafana？谁负责部署、更新、备份和 SSO？
7. 自动关停是否进入一期？建议否：一期只审计观测，保留现有浏览器闭环。

## 明确不做

- 不解析飞书消息回填 ERP；
- 不改变既有 `/v2/analytics/sync/dumps` 冻结协议；
- 不把 ERP 变成可写 TikTok 店铺的自动关停服务；
- 不把 cookie、webhook、App Secret 或原始 TikTok 请求存到事件/BI；
- 不在 MVP 引入 Kafka 或第三方 CDP；
- 不让前端成为阈值判定、严重性或业务金额的权威实现。

## 证据与来源

- **[R1] 本仓 ads-monitor 实现**：`plugins/monitor/README.md`；`plugins/monitor/entrypoints/background.ts`；`plugins/monitor/src/core/{types,thresholds,feishu,feishu-http}.ts`。
- **[R2] ERP 插件 ingest、认证和 scope 契约**：[`docs/api/external-api.md`](../api/external-api.md)；`tts_erp_v2/api/v2/analytics.py`；[`docs/architecture/architecture-overview.md`](../architecture/architecture-overview.md)。
- **[R3] 当前广告日事实与浏览页**：[`docs/api/ad-daily.md`](../api/ad-daily.md)；`tts_erp_v2/api/v2/ad_daily.py`；`tts_erp_v2/db/models/plugin.py`。
- **[R4] 飞书自定义机器人官方文档**：<https://open.feishu.cn/document/client-docs/bot-v3/add-custom-bot>。
- **[R5] Metabase PostgreSQL 官方文档**：<https://www.metabase.com/docs/latest/databases/connections/postgresql>。
- **[R6] Grafana PostgreSQL datasource 官方文档**：<https://grafana.com/docs/grafana/latest/datasources/postgres/configure/>。
- **[R7] Apache Superset PostgreSQL 官方文档**：<https://superset.apache.org/user-docs/databases/supported/postgresql/>。
