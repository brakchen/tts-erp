# 插件状态上报与服务端决策技术方案

> 状态：2026-10-15 用户已批准由 owner 调度 MiniMax M3.1 Flash Preview 实施并审查；尚不代表接口已实现、部署或验收通过。
>
> 用户已确认：复用 ads-data-sync 的成熟机制，为 monitor 实现单 Seller/Advertiser 自动绑定；页面失效必须通知服务端，由服务端决定下一步。本文件是通用 `plugin/status/report` 协议、ERP 后端职责及 monitor 接入的实施基线。计划与进度见[实施计划](../aegis/plans/2026-10-15-plugin-status-implementation.md)。
>
> 关键调整：此前“页面失效后插件自行重绑并恢复运行”改为“立即安全暂停并上报，服务端授权恢复；重新就绪后再由服务端放行”。识别异常、停止不安全执行不等待网络。

> 2026-10-23 实施增量（仅任务分支）：monitor `4dad71c` 已在既有原生业务写中原子持久化首次 Store pin、GEN 与原命令 continuation；输入 expected-policy/Store 是 CAS 数据，不增加授权 owner/token。managed 恢复按完整 Seller/Advertiser/Store 唯一组合选页，未知竞争者不许 bootstrap，同 tuple 多页可按偏好选页；重读 Store 必须一致。canonical READY 还核 raw pinned Store 相等，精确失败事实不受全局 pin gate 阻挡。最新12相关692PASS/完整tsc0，startup/resume/nextcommand、真实页面Factory/配置rollout/跨端/live仍待闭合；这不是已激活、部署或发布证明。

> 2026-10-23 生命周期增量：生产分支 `9604918`（后续 `8c724db`仅补测试）已让 suspended READY/运行态正向复用读取严格持久pin并重证实际上下文；策略不可读本身不制造失效事实，已实际观测的无tab/login仍正常上报与清理。resume在Scheduler API调用前二次核对Store与原generation/fence，新READY→resume命令协议路径、轮换/新Controller持久性均有合格注入测试。该轮14相关755/type0，补隔离竞态后native文件210/type0，不累计为761/不声称全插件或浏览器；实际Scheduler适配器await后的原生JIT、原resume deadline及PageFactory仍需另行证明，能力未激活。

## 1. 现状、基线与方案归属

### 1.1 已核验现状

| 对象 | 基线与事实 |
| --- | --- |
| ERP | 调研代码基线 `2d0f92ab600a2b8b74614dcd89083b7fe8a961c7`；没有通用插件状态/决策端点。已有 `POST /v2/analytics/sync/plugin-logs` 写 `plugin.plugin_logs`，但没有实例身份、状态投影、幂等回执或恢复决策。 |
| ads-data-sync | `8d2245e6da1aded1a662ae7e7703abeb105863ea`：后台 `evaluateAutoBind` 扫描已有 Seller 页、固定页优先；MAIN world 身份捕获、Seller 探测、pending 超时、tab 事件和 Alarm 恢复；跨插件刷新协调已存在。当前无候选时等待，不自动新建页面。 |
| monitor | 实际调查独立插件提交 `51ccab525505d5a497558d46f559357fb1cf4786`，v0.4.26。父仓 gitlink 仍为 `456423b738b5ebcdfafe6d3787fb272e814ce9fb`，不能把父仓固定版本当本次插件调查版本。 |
| monitor 配置下发 | `docs/monitor-config-sync.md` 属于另一条 ready/active 工作线，文档提交 `0d1e2e36b5cd9db13151f98955684c2a1ba80811`；本次 ERP master 尚无此文件与对应路由。其店铺专用 Token、GET 配置、force 规则不是本文件已落地前提。 |
| 广告通知入库 | `docs/research/ads-monitor-notification-ingestion.md` 属另一条 ready 调研线，提交 `af76527ee9c39f4db18015fb8cb2b94a68d599d6`；尚不是已批准监控事实 API。本文件不接管它。 |

本文件是**插件运行状态/恢复协议的唯一方案文档**，monitor 自动绑定的本地接入见 §5、§12。既有 [monitor 列表兼容方案](monitor/02-technical-design.md) 只管 legacy/v2 列表协议，不应追加无关生命周期职责；[ads 插件重构方案](ads-data-sync/01-refactor-technical-design.md) 是参考，不等于全部已经实现。

### 1.2 复用与新建必要性

- 复用 ERP 的 FastAPI/Pydantic、SQLAlchemy/PostgreSQL、`access/`、API key 生命周期、请求 ID、既有 sync-worker；不新建鉴权中间件、消息总线、常驻扩展后台或通用远程执行平台。
- 不复用日志端点作为状态真相：自由文本/context 无法可靠提供顺序、离线推断、失效 episode 和指令确认；不改冻结的广告 dump 契约。
- 新增一个公共状态领域 owner 是必要的：它需要跨广告/订单等插件统一管理实例、最新观测和受限决策，但不能依附广告指标解析或 monitor 远程配置。
- 只借鉴成熟模式：Kubernetes 的“心跳与控制器反应分离”、OpenTelemetry 的不透明实例 ID、CloudEvents 的事件唯一标识和重复投递语义。**不宣称本协议符合 CloudEvents，不引入其 SDK 或 Kubernetes/OTel 运行依赖。** 参考链接见 §17。

## 2. 目标、边界与产品规则

1. 插件明确观测到页面关闭、离开允许 origin、登录失效、身份变化或无法完成可信请求时，先让本地执行失效，再尽快上报。
2. 服务端拥有恢复策略、等待/人工处理/告警及恢复放行的决策权；插件只拥有观测事实、即时安全停止和有界指令执行。
3. 一次监控一个 `(sellerId, advertiserId, cookieStoreId)`；候选页不是监控目标。同一目标可以更换 tab，不自动切到另一店铺。
4. 自动发现仍自动化，不把每次恢复变成人工确认。服务端策略可以自动批准同身份恢复；只有身份冲突、登录问题或预算耗尽才要求人工。
5. `200` 表示报告及约定决策已持久化，不表示浏览器已执行、页面已恢复，更不表示广告已关闭。无 `rowsWritten`、`data.status` 等平行成功通道。
6. 本接口不上传广告列表、消耗/ROI、Cookie、请求模板、完整页面 URL 或通知密钥，不替代广告观测、配置下发或关停审计 API。
7. 服务端不代发 TikTok 业务写请求；指令白名单没有 `close_campaign`、`open_campaign`、任意 URL、JavaScript 或 shell。
8. 普通状态上传短暂失败**不另设“网络一断就全局停机”规则**：原本健康且未发生失效的本地监控保持既有行为。但一旦失效、重启后无法核验原绑定，或已处于恢复等待，就不能离线自行恢复。
9. 首次自动绑定保留已批准的唯一目标发现规则；在本次“受服务端管理”接入中，首次就绪也报告给服务端，取得 `resume_monitoring` 后才开始扫描/关停，避免首次启动成为恢复闸的旁路。此新增放行点随本轮完整方案实施授权纳入基线。

## 3. 交互方案选择与总体架构

| 方案 | 得失 | 选择 |
| --- | --- | --- |
| 只上报，由插件自行恢复 | 服务端只能事后观察，不能决定下一步 | 不满足本次新增要求 |
| 状态 POST 响应返回受限决策；后续心跳续取、上报结果 | 复用 MV3 的短请求/Alarm，无长连接；单一协议可闭环 | **推荐** |
| WebSocket/推送加独立命令平台 | 下发延迟低，但需连接保活、命令系统和断线重建 | 首期不做；没有秒级远程操控需求 |

```text
插件 tab 事件 / 扫描证据
  → binding controller 即时失效并暂停
  → 本地持久报告队列
  → POST /v2/plugin/status/report
  → ERP：鉴权 → 报告去重 → 更新实例投影 → 策略决策，同事务提交
  ← receipt + decision（可空）
插件：持久接收 → 前置条件核验 → 有界恢复 → 结果/就绪再次上报
  ← resume_monitoring
插件：本地开关与完整快照复核 → 恢复原有工作 → 上报实际结果

ERP sync-worker：超时巡检、决策超时及通知 outbox 投递
```

不要求常驻 background。主动状态变化立即尝试发送；健康/等待期间每 60 秒报告，30 秒 Alarm 负责到期唤醒和重试，启动补查 Alarm。浏览器休眠可能延迟，不能承诺严格实时。恢复命令只能在下一次报告响应到达，不承诺服务端主动唤醒已关闭的 Chrome。

## 4. 实例、状态与事件模型

### 4.1 实例与作用域

- `pluginId` 是服务端登记的稳定枚举，首期接入 `ads-monitor`；预留登记 `ads-data-sync`、`order-data-sync`、`miaoshou-order-capture`、`tts-request-intercept`，不自动让这些插件发送或接受恢复指令。
- `instanceId` 是服务端开通时生成的 UUID，唯一指向一个插件安装实例和专用凭证；不是 tabId、扩展签名 ID、IP、机器名或用户标识。重装/丢失身份须重新开通，不能把旧序号归零后沿用旧实例。
- 服务端登记 `scope`：`kind=shop` 时 seller 必填，advertiser 按插件能力可选；`kind=installation` 只用于无店铺上下文的插件。monitor 必须使用 shop scope。advertiser 预登记则精确匹配；未登记时，首次身份已核验的 ready 报告在实例行锁内将空 advertiser 比较并设置一次，此后锁定。seller 不可由报告改变；后续改变 advertiser 或店铺必须重新开通目标实例，不能靠上传新值切换。
- `scope` 是**目标授权范围**，不是当前页面的推测身份。页面未就绪时照样报告既定 scope；未知或冲突身份只报安全原因，不上传其他店铺的候选身份。`shopPk` 由服务端映射，不能由客户端指定或按店名猜测。
- `bindingEpoch` 是本地绑定上下文的 UUID。绑定失效、切换目标或无法证明重启前上下文时换新；单纯 MV3 Worker 唤醒且能重新核验原上下文，不必制造新的失效事件。
- `incidentId` 是一次不可工作 episode 的 UUID。失效后建立，穿过等待/重绑/就绪流程保持不变；真实恢复以 `decision_result` 中 resume 的 applied 结果及 `execution.phase=running` 表达，服务端处理并确认该报告后，插件才在后续健康报告中将 incidentId 清为 null。首次接入也建立一个初始化 episode。不得每次心跳都新建 incident。

### 4.2 正交状态

| 字段 | 枚举/语义 |
| --- | --- |
| `binding.phase` | `unbound / discovering / verifying / ready / invalid / login_required / conflict` |
| `execution.phase` | `disabled / suspended / running`；是否实际运行，不是 HTTP 成功标识 |
| `execution.reasonCode` | `USER_DISABLED / INITIALIZING / PAGE_INVALID / AWAITING_SERVER / RECOVERING / SNAPSHOT_UNAVAILABLE / NONE` |
| `eventType` | `instance_started / heartbeat / binding_invalidated / binding_ready / execution_changed / decision_result` |
| `reasonCode` | `TAB_CLOSED / ORIGIN_LEFT / LOGIN_REQUIRED / IDENTITY_MISMATCH / TAB_DISCARDED / PAGE_LOADING / TRANSPORT_TIMEOUT / REQUEST_FAILED / NO_CANDIDATE / AMBIGUOUS_CANDIDATES / WORKER_CONTEXT_UNKNOWN / USER_DISABLED / REPORT_QUEUE_FULL / NONE` |

理由为有界枚举，不传任意异常字符串。`PAGE_LOADING` 单独表示等待，普通 SPA 路由变化不得直接当登录失效。网络错误只有 `TRANSPORT_TIMEOUT/REQUEST_FAILED`，不能推断成 `LOGIN_REQUIRED`。非 monitor 插件在同一 envelope 中使用已登记、可严格验证的能力状态；不把所有插件的业务工作状态塞入 monitor 枚举。

服务端额外派生 `connectivity=online / stale / unknown`，与 `binding.phase` 分开：心跳超时只能证明“近期未收到消息”，不能证明退出登录、广告已停或店铺无人监控。多个实例独立显示，不能互相覆盖、相互授权或仅凭失联自动认定唯一执行者。

## 5. monitor 自动绑定与失效流程

### 5.1 正常发现

复用 ads 的扫描、固定页优先、pending 单飞、身份捕获及 MAIN world Seller 探测。首期只复用已有页面；无页面显示打开/登录提示，不自动新建、抢焦点或自动登录。

当前 monitor 只支持 `https://seller.tiktokshopglobalselling.com`，不因 ads 支持另一域名就扩大关闭执行 origin。Seller 探测如使用 `api16-normal-sg.tiktokshopglobalselling.com`，只加入准确接口 host 权限。通过页面会话请求，不在 ERP 接收 Cookie。

已有有效绑定优先，激活其他页面不会抢占。无既定 advertiser 时，候选身份必须明确且唯一；多个未核验候选不能按 tab 排序假定是同店铺。固定页优先是候选顺序，不是绕过身份约束的许可。

**Cookie Store 首次锁定与人工换绑（2026-10-23 操作者明确批准）**：首次仅在 Seller、Advertiser 与 Chrome 原生 tab→CookieStore 关联均已完整核验，且符合授权目标的组合无歧义时，建立持久的本地 Store 锁定。同一组合可以有多个页面，不因 tab 数多而误报多目标；不同可用 Store 组合不可凭固定页/激活顺序任选。尚有可能竞争目标的候选未核验完整时，不先绑定或默认一个 Store。后续 Worker 唤醒、页面关闭、自动失效/恢复与普通凭证轮换均不能解除该锁定，不回退默认 Store；换 tab 仍须同一目标 Store。

跨 Store 必须有明确的人工重新绑定意图，并完整核验新目标组合后直接以新锁定替换旧锁定，不开放“先清锁再自动任选”的权限空窗。原 Seller/Advertiser 授权范围不因此改变，Store 仅为本地固定执行上下文，不加入 ERP wire scope、不传 Cookie。人工换绑须停止/撤销旧执行、保持新上下文 suspended 并保留 outbox、ledger、未决事实及原 receipt root；不得借 `clearStatusInstance` 清债、复用旧命令叶或重开旧 90 秒窗口。此处批准行为政策，不表示人工 UI、迁移修复或真实浏览器链已实现。

### 5.2 失效与服务端决策

1. 本地确认失效，立即使现有 `runtimeGeneration` 失效、标记快照不可执行，阻止未发出的关停/扫描任务，创建或更新 episode 并持久报告。
2. `binding_invalidated` 立即发送；服务端默认自动恢复策略只对同目标、可恢复原因返回 `recover_binding`。登录/身份冲突直接 `require_user`，并安排告警。
3. 无响应时保持 `suspended`；允许被动发现页面、收集只读证据，不自行导航、刷新、接管或恢复执行。
4. 有效 `recover_binding` 到达后，controller 选择同目标候选，按共享租约协调刷新/导航、重取身份和 legacy/v2 模板；不能照搬 ads 故障切页仅替换 tabId、保留旧身份的分支。
5. 核验 Cookie Store、Seller、Advertiser、允许 origin 和模板，完整拉取一次 close 窗口快照；成功提交新 bindingEpoch，状态 `ready + suspended`，发送 `binding_ready` 与指令结果。
6. 服务端核对当前 episode、最新就绪证据及策略，返回针对新 epoch 的 `resume_monitoring`。
7. 插件再次核验本地监听开关、绑定、快照新鲜度与完整性；恢复原有调度。自动关停仍需要原有 `autoCloseEnabled`、阈值、SKU/白名单和逐项执行门禁，不因服务端许可被开启。
8. 回报 `decision_result` 和实际 execution。服务端收到结果前只显示“已下发/待确认”，不得显示“已恢复”。

已经发往 TikTok 的请求不能保证撤销；本地必须将其结果视为旧 generation/待核查，不宣称安全暂停能追回请求。后续只用受控、完整新查询核实，不能因服务端要求恢复就重新发送不明结果的关停。

同目标恢复保留业务配置；旧缓存不再作为关闭证据。切换店铺需先停止并取消旧上下文，再匹配新店铺授权，不把本地阈值、失败提醒或凭证串到新目标。

## 6. 公共上报接口

### 6.1 HTTP

```http
POST /v2/plugin/status/report
Authorization: Bearer <PLUGIN_STATUS_TOKEN>
Content-Type: application/json
x-request-id: 620be3ac-9727-4d5c-88e4-3204e21cb201
```

路径为 route-relative；公网按获准 HTTPS origin 加部署前缀 `/tts`，不把前缀写死进 handler。不新增无版本的 `/plugin/status/report` 别名。单次一个报告，body 最大 32 KiB，拒绝未知字段；响应 `Cache-Control: no-store`。请求 10 秒 deadline，`credentials: omit`，不跟随重定向，不传 TikTok/ERP 会话 Cookie。

### 6.2 请求示例：页面关闭

```json
{
  "schemaVersion": 1,
  "pluginId": "ads-monitor",
  "pluginVersion": "0.4.26",
  "instanceId": "9439ce3d-66ca-447c-84c2-408c32cb8dd3",
  "eventId": "9e4436ba-d223-440e-968f-152454a89144",
  "reportSeq": "104",
  "observedAt": "2026-10-15T17:00:00Z",
  "eventType": "binding_invalidated",
  "reasonCode": "TAB_CLOSED",
  "scope": {
    "kind": "shop",
    "sellerId": "TEST_SELLER_A",
    "advertiserId": "TEST_ADVERTISER_A"
  },
  "incidentId": "944aa39e-0865-4296-873d-5be7d99dcefb",
  "binding": {
    "epoch": "8f755b12-dbc8-4542-bf54-049f72d230fc",
    "phase": "invalid",
    "candidateCount": 1,
    "identityVerified": false
  },
  "execution": {
    "phase": "suspended",
    "reasonCode": "PAGE_INVALID"
  },
  "capabilities": ["binding.recover.v1", "monitor.resume.v1"],
  "capabilityState": {
    "monitor": {
      "monitorEnabled": true,
      "autoCloseEnabled": true,
      "closeSnapshotComplete": false,
      "closeSnapshotObservedAt": null
    }
  },
  "decisionResults": []
}
```

字段约束：

- UUID wire 值使用小写、带连字符的规范字符串；整数/布尔字段不得将字符串或布尔值强制转换为整数。`reportSeq`、后文 revision 为正十进制字符串，范围 `1..9223372036854775807`，客户端不能转为 JavaScript Number。序号由本地存储短事务分配，进程唤醒不归零。
- `observedAt` 为 aware UTC；服务端另存 `received_at`，不以客户端时钟排列报告。超过未来 5 分钟拒绝；旧报告可以归档，但恢复/放行只采信相对服务端时间年龄在 -5..120 秒内的观测，不能借明显未来时间延长新鲜度。
- `pluginId` 最长 64、版本最长 64、seller/advertiser 最长 128、capabilities 最多 16、`candidateCount` 为 `0..1000`；能力必须为服务端登记值，不接受 arbitrary extensions/context。
- shop scope 的 advertiser 未知时可省略，但 monitor 的 `ready` 必须具备它；installation scope 不允许夹带 seller。除 §4.1 明确的一次性空 advertiser 锁定外，与凭证目标不符为 403，不据此改变登记范围。
- `incidentId` 在失效/初始化、恢复及其结果报告中必填；健康 heartbeat 可为 null。普通 heartbeat 也携带完整当前状态，帮助状态补偿，不依赖先收到某个事件才能理解现状。
- `decisionResults` 最多 8 项，形状 `{decisionId, revision, outcome, reasonCode, completedAt}`；`outcome=applied / rejected / failed / unknown`，原因使用登记枚举 `NONE / LOCAL_DISABLED / STALE_CONTEXT / EXPIRED / UNSUPPORTED / RECOVERY_FAILED / INTERRUPTED`。指令身份与服务端记录不符不能伪造确认。
- `identityVerified=true` 是插件观测声明，不是上游加密证明；服务端仍必须核验 scope 授权，不能据此扩大凭证权限。
- 公共 `execution` 不含 monitor 专用开关。`capabilityState` 按已登记能力判别验证：首期 `ads-monitor` 必须携带示例中的 `monitor` 块；其他插件暂用 `{}`，新增块需版本化登记，不能任意透传 JSON。无页面能力的 installation 插件允许 `binding=null`，不能取得 binding/monitor 指令。
- `monitor.closeSnapshotComplete` 只能反映本地 close 窗口完整查询；为 true 时 `closeSnapshotObservedAt` 必须是 UTC 时间、不晚于 observedAt，resume 时在服务端和本地年龄均为 -5..60 秒（只容忍 5 秒时钟抖动）。为 false 时该时间必须为 null。服务端用这些明确字段判断就绪，不能仅因 binding.phase=ready 就放行。

## 7. 回执、受限决策与执行确认

### 7.1 成功响应示例

```json
{
  "schemaVersion": 1,
  "requestId": "620be3ac-9727-4d5c-88e4-3204e21cb201",
  "receipt": {
    "eventId": "9e4436ba-d223-440e-968f-152454a89144",
    "reportSeq": "104",
    "receivedAt": "2026-10-15T17:00:01Z",
    "projectionUpdated": true
  },
  "serverTime": "2026-10-15T17:00:01Z",
  "reportAfterSeconds": 60,
  "decision": {
    "decisionId": "b0422ae4-ff2c-473d-9c41-a717f7636a1b",
    "revision": "7",
    "incidentId": "944aa39e-0865-4296-873d-5be7d99dcefb",
    "expectedBindingEpoch": "8f755b12-dbc8-4542-bf54-049f72d230fc",
    "action": "recover_binding",
    "notBefore": "2026-10-15T17:00:01Z",
    "expiresAt": "2026-10-15T17:02:01Z",
    "parameters": {
      "attempt": 1,
      "captureTimeoutSeconds": 90
    }
  }
}
```

仅返回一个当前决策或 null，避免引入通用命令批处理。`reportAfterSeconds` 为 `30..300`，只是下一次心跳建议，不延迟失效/就绪事件发送，也不控制广告扫描周期。HTTP 200 正文缺失、身份不匹配或 schema 错误时客户端不得出队或执行；202/204 不属于成功协议。

### 7.2 决策白名单

| action | parameters | 插件语义 |
| --- | --- | --- |
| `hold` | `{}` | 保持/进入暂停；仍上报与被动观察，不启动恢复副作用。 |
| `recover_binding` | `{attempt: 1..3, captureTimeoutSeconds: 90}` | 只做一次有界的同目标绑定恢复，不开始监控或关停。 |
| `resume_monitoring` | `{}` | 允许当前 epoch 继续已由用户开启的工作，不修改用户配置、auto-close 开关或规则。 |
| `require_user` | `{}` | 暂停并显示按原因生成的本地提示；登录/身份切换由用户完成，不自动输入凭证。 |

`notBefore < expiresAt`，有效窗最多 120 秒。插件核验实例、凭证目标、episode、expected epoch、能力、本地开关及尚有效的时间窗。`resume_monitoring` 额外要求 ready、身份一致且不超过 60 秒的完整 fresh close 快照；过期时回报拒绝并重新取得就绪证据，不使用页面展示 fallback。

revision 每实例单调增加；相同决策重发不增长，策略或当前指令变更才增长。旧 revision、旧 episode/epoch、过期或未知 action 均不得执行，记录 rejected 结果。重复已终结的 decisionId 只重报原结果，不重新刷新页面。

expiresAt 限制该次动作的接收/开始，不是健康运行的周期续租；成功恢复后不因 120 秒到期重新停机。已经合法开始的 recover 仍受独立 90 秒捕获 deadline 约束，超时停止并回报；结果可以在指令过期后补报，过期不能抹去已发生的事实。

### 7.3 至少一次，不宣称恰好一次

插件先持久化 accepted/started，再执行浏览器动作，终结结果与下一份报告原子保存。Worker 在副作用后中断时记录/恢复为 unknown，优先重新核验实际页面并上报，不能简单重做 refresh。当已取得完整就绪证据时可报告 binding_ready，让服务端决定放行；没有证据则等待新决策。

指令只允许来自**当前新鲜状态报告**的合法响应。历史队列补报或旧事件重放返回的决策一律不执行；补报后主动生成当前 snapshot 再请求。插件不能把新生成报告的状态变化与旧响应拼接。

以请求开始时的单调时钟，加上 `expiresAt-serverTime` 的有界差值建立保守 deadline，网络耗时不得延长许可；本地时钟异常则拒绝。Worker 重启后缓存决策只作状态展示，需新报告确认其仍有效，不沿用内存计时器或仅靠本地绝对时钟自动执行。

## 8. 幂等、乱序与 HTTP 错误

- `(instanceId, eventId)` 是业务幂等键；同一报告重试保留 eventId、reportSeq 与业务正文。每次网络尝试可有新 `x-request-id`，它不参与业务 hash。
- 服务端对严格 schema 的规范化 JSON 计算 SHA-256；相同 eventId 内容不同返回 409 `EVENT_CONFLICT`；同一实例相同 seq 指向不同 eventId 返回 409 `SEQUENCE_CONFLICT`，提示实例克隆/本地损坏，不能覆盖。
- 重复报告返回原 receipt 与原决策快照；`requestId` 与 `serverTime` 对应本次传输，只有 receipt/decision 冻结。过期决策不会因重试续期；不能把旧 serverTime 与新请求起点相加而延长 deadline。新决策通过新报告获取。
- 合法乱序低 seq 报告仍归档、返回 200 和 `projectionUpdated=false`，但不倒退最新状态，不产生新恢复动作；不能让延迟的 invalid 覆盖更新的 ready。序号可以有洞，不依赖连续收到历史事件。
- 新鲜心跳传递此前 decision 的结果时，结果匹配记录后幂等处理；迟到的旧结果可补充历史，但不重开已解决 episode 或覆盖更高 revision。
- 所有成功响应仅在事务 commit 后返回；数据库、决策写入或约定 outbox 写入失败则整体 rollback 并返回 5xx，不“日志写失败仍 200”。

错误沿用项目安全 JSON 错误风格：`{requestId, error:{code, message, retryable}}`，HTTP 状态决定成功与否，message 不回显原始 payload/Token。

| HTTP | code 示例 | 插件处理 |
| --- | --- | --- |
| 400/422 | `MALFORMED_JSON / SCHEMA_INVALID / UNSUPPORTED_VERSION` | 隔离坏报告，保留安全摘要并提示升级/修复，不无限重试或执行响应。 |
| 401 | `INVALID_CREDENTIAL` | 停止自动认证重试，保留待发状态，要求重新配置；恢复闸仍关闭。 |
| 403 | `PURPOSE_DENIED / INSTANCE_DENIED / SCOPE_DENIED` | 提示授权错误，不降级普通 key、Cookie 或跨店接口。 |
| 409 | `EVENT_CONFLICT / SEQUENCE_CONFLICT` | 冻结该实例自动恢复，人工修复/重新开通，不能自动 seq 归零。 |
| 413 | `BODY_TOO_LARGE` | 本地协议错误，修复；不截断为看似成功的报告。 |
| 429 | `RATE_LIMITED` | 尊重 Retry-After（delay-seconds/HTTP-date），Alarm 有界重试。 |
| 5xx/网络/超时 | `STORE_UNAVAILABLE / INTERNAL_ERROR` | 原报告幂等重试；已经暂停的流程不得借失败自行恢复。 |

网络/5xx 使用 full-jitter 退避，基数 30 秒、上限 300 秒，Alarm 不保证精确时间。若服务端已提交而响应丢失，重放不得重复创建决策/通知。

## 9. 鉴权、授权与敏感数据

1. 复用 `security.api_keys` 的随机 key、SHA-256 存储、吊销/轮换及统一 `access/`；专门增加 `role=readwrite, purpose=plugin_status` 的显式精确路径授权，只能 POST report。该精确路由最低 readwrite，且另有强制 purpose/instance 门禁；普通 readwrite/admin key 不因角色足够而获得许可。通用角色默认 deny 不得被 readwrite 前缀放宽。
2. status key 与服务端实例记录绑定，实例记录限定 pluginId、scope 和获准 capabilities；接口重新核验活动凭证与实例状态，不接受客户端声明的角色/插件身份作为授权。专用边界在 off/shadow 模式也不能匿名获得恢复许可。
3. **不扩大 `purpose=monitor_config` Token 的权限**，它仍只读原配置 GET。本轮建议独立 status key，本地 ERP origin 可共用；两类凭证不混用。若以后希望统一凭证，应由配置/鉴权 owner 另行批准能力模型，不能偷偷追加路径。
4. 首期管理接口统一 admin：`POST /v2/plugin/admin/instances` 开通实例并生成专用 status key（只展示一次），`GET /v2/plugin/admin/instances` 列表，`GET /v2/plugin/admin/instances/{instanceId}` 查看状态/报告/决策，`POST /v2/plugin/admin/instances/{instanceId}/decisions` 人工改变 hold/recover/require_user 决策。列表按 limit/offset，支持 shop_pk、pluginId、connectivity 过滤。实例开通要求 pluginId、scope、capabilities，shop scope 必须映射唯一有效店铺；未映射/歧义拒绝，不自动注册店铺。installation scope 不映射店铺，也不得取得 monitor 能力。
5. 人工决策使用 `requestId` 幂等、`expectedRevision` 乐观锁及必填的 `incidentId`、`expectedBindingEpoch` 当前上下文条件；后两项必须为规范 UUID，并在实例行锁内精确比较，不允许省略条件后猜测当前 episode。无当前 incident/epoch 时不创建绑定指令，不支持强制跨身份放行。人工 hold/require_user 对当前 episode 锁存，普通心跳不能自动覆盖；管理员明确 recover 才解除该锁存，之后仍需就绪/放行流程。cookie-auth 管理 mutation 沿用 CSRF header/JSON；status 插件客户端 credentials omit。
6. 新目的字段及关联模型属于未来 access/security 变更，必须与 monitor-config-erp lane 协调；不另立 token 服务，不复用未来 monitor-config schema 作为状态表。
7. TLS 是上线前置。本次未验证真实入口证书，禁止凭历史 HTTP 域名发送真实 key、忽略证书或降级 HTTP。精确允许插件 ERP host、实际签名扩展 origin；CORS 允许 Authorization、Content-Type、x-request-id，并暴露 x-request-id、Retry-After；不改 middleware 注册顺序。
8. Token、Cookie、Authorization、webhook、Fernet/app secret、query 参数和完整 HTTP 响应不得进入 report、日志或通知。本地凭证与 instance 身份不进入业务配置导入/导出或远程 force；Chrome storage 不是加密保险箱。清空 Profile 后需重新开通实例。

## 10. 后端持久化与事务

以下均为拟新增模型，放独立 `db/models/plugin_status.py`；沿用 `plugin` schema，不扩已有 725 行 plugin.py 的业务表 owner。迁移编号在实施时基于实际 Alembic head 分配，不在方案抢占编号。

| 表 | 必要字段与约束 |
| --- | --- |
| `plugin.status_instances` | UUID PK，plugin_id、shop_pk nullable、scope JSONB（受严格 schema 约束）、status_api_key_id FK、enabled、capabilities、last_seen_at、latest_report_seq bigint、latest_event_id、latest_state JSONB、latest_observed_at、latest_received_at、current_decision_revision、current_incident_id。全是实例配置/状态投影，不是第二份业务配置。 |
| `plugin.status_reports` | UUID PK、instance_id FK、event_id、report_seq、payload_hash、observed_at/received_at、event_type、reason_code、incident_id、binding_epoch nullable、validated_payload JSONB、receipt JSONB、response_decision JSONB。唯一(instance_id,event_id)、唯一(instance_id,report_seq)，索引(instance_id,received_at)；shop_pk 经 instance 关联，不额外复制归属。 |
| `plugin.status_decisions` | UUID PK、instance_id FK、revision、incident_id、expected_binding_epoch、action、parameters、issued_at/not_before/expires_at、policy_version、source_report_id nullable FK、source admin/automatic、delivery_state、result/outcome、completed_at。唯一(instance_id,revision)，记录创建而非实际执行；episode 恢复次数依据这里的已发指令计数。 |
| `plugin.status_notification_outbox` | UUID PK、instance_id、incident_key、notification_kind、dedupe_key unique、state、attempt_count、next_attempt_at、lease_until、last_error_code、delivered_at。只存非敏感模板参数与受控目标引用，不复制 webhook 密钥；首期仅该领域使用，不建全局消息平台。 |

`status_reports`、`status_decisions` 是审计事实，默认不做自动删除；长期保留/归档另走明确授权方案。outbox 投递状态可更新，不能重写事件历史。

同步 handler 使用 SQLAlchemy 2 Session；raw body 如需异步尺寸闸，由 async dependency 读取再交同步 handler，不在 async handler 直接跑 psycopg。单次事务顺序：

1. 验证身份/用途/实例 scope，锁实例行；冲突约束仍是最终防线。
2. 去重报告或拒绝冲突；保存已校验报告和结果回执。
3. 只有更大 seq 才更新 latest projection。任何合法请求可更新网络 last_seen_at，但旧观测不能据此变成当前就绪证据。
4. 仅当前新鲜状态和已匹配决策结果参与策略，优先尊重当前 episode 的人工锁存；选用/创建一个当前决策，必要时写通知 outbox，冻结报告的 receipt/decision 快照。
5. commit 后回复。并发报告/人工控制/巡检走相同领域 service 和实例行锁，不能各维护一份 current decision。

服务端 readiness 还要求最新观测在 120 秒内；刚补报旧记录只能证明网络可达，不能宣称页面新鲜。心跳超时阈值默认 180 秒，以服务端时间算；首次无消息显示 unknown。

## 11. 服务端策略、巡检和通知

默认恢复策略版本固定记录为 `binding-recovery-v1`，后续变更需版本化，不能把策略隐藏在路由条件里。

| 最新事实 | 服务端默认决策 | 伴随动作 |
| --- | --- | --- |
| 初次 ready，目标明确、monitorEnabled=true、完整快照时间字段满足 §6 | resume_monitoring | 等插件确认实际运行 |
| TAB_CLOSED / ORIGIN_LEFT / TAB_DISCARDED，目标不变且有已知候选 | recover_binding | 同 episode 第一次异常留档；持续故障再通知 |
| NO_CANDIDATE / PAGE_LOADING / 页面租约忙碌 | hold | 等新事实，不反复 reload；无候选持续 180 秒通知 |
| 同 episode 出现同目标候选，或登录/身份问题经被动核验已消除 | recover_binding（人工锁存除外） | 仍须执行完整就绪/放行流程，不能直接恢复监控 |
| TRANSPORT_TIMEOUT / REQUEST_FAILED | hold；连续两次新观测失败且有候选后允许 recover_binding | 不误判登录；计入恢复预算 |
| LOGIN_REQUIRED / IDENTITY_MISMATCH / AMBIGUOUS_CANDIDATES | require_user | 立即安排一次运营通知 |
| 恢复后 ready 且身份/epoch/完整快照时间字段匹配 | resume_monitoring | 收到 applied 且 running 后解决 episode；可发一次恢复通知 |
| USER_DISABLED 或 monitorEnabled=false（优先于就绪/自动恢复规则） | hold | 不远程打开开关，不当作故障催促用户 |
| 新鲜证据缺失、版本不支持、scope 冲突或恢复预算耗尽 | require_user 或 hold（按安全原因） | 告警/升级，不继续自动试错 |
| 心跳超过 180 秒 | 只标 stale，hold/保持已知待处理决策 | “插件失联/可能休眠”通知，不宣称页面关闭；不能向失联实例假装已送达 |

每 episode 最多发出 3 次 recover_binding，发出即占预算，包含响应丢失/unknown；两次至少间隔 60 秒，10 分钟未完成进入人工处理，不无限重试。租约繁忙/正常 loading 不额外制造失效 episode。服务端只能基于报告里的观察安排动作，不保证有候选就一定可恢复。

sync-worker 每 60 秒巡检超时和待投递通知，使用现有调度进程，不在 API 内增设永驻 timer。巡检只标投影/推进领域决策；不会凭一个旧 tabId 调 TikTok。

通知幂等键由 `(instanceId, incidentId 或首次超时episode, notificationKind)` 生成；失联 episode 从 online→stale 建立，到新鲜报告恢复时结束，不能每次巡检换 ID。outbox 与状态转换同事务；网络发送在事务外，采用短领取 lease、重试和已投递标记。若现有发布/通知基础设施没有可用 sender，则实现本领域窄 sender，不依赖未合入的广告通知方案。通知密钥继续走现有 token_service/secret owner，不直读解密 integration.credentials。

投递逐条在即将发送时领取，不为尚在等待的整批通知提前消耗 lease。领取返回不可变的 `(id, lease_until)` 快照，结果更新必须同时匹配当前 `claimed` 状态、同一 lease 与未过期时间；旧 worker 不得通过 ORM 重新加载借到新 worker 的所有权。失权结果不计入本次成功/重试/人工计数。`plugin.status.lifecycle` 通过既有 system-job 注册与执行历史运行，不增加进程；发送期间不持数据库事务。

通知网络响应丢失存在重复可能，只承诺至少一次及本地幂等，不承诺飞书恰好一次。对网络/429/5xx 有界退避，对配置/签名错误标记待人工，不记录目标密钥。插件继续负责既有广告业务通知；本领域 ERP 只发实例/绑定生命周期通知，避免两侧重复发送同一“页面失效”提醒。接入时将该类旧本地提醒切到 ERP 单一 owner，未纳管旧版本不变。

## 12. 插件模块与并发所有权

建议独立插件仓按当前 master 实施，先同步其他 lane，不在父仓暂存插件源码：

| 模块 | 唯一责任 |
| --- | --- |
| `src/core/tab-binding.ts` | origin/身份匹配、候选选择、状态转换纯逻辑；既有核心扩展，不另起一套 binding 判断。 |
| 新 `src/extension/tab-binding-controller.ts` | 绑定生命周期唯一写入者；手动切换、自动发现、服务端恢复均走同一 owner。 |
| 新 `src/core/plugin-status-contract.ts` | 请求/响应和决策的纯严格校验；不拥有 HTTP、调度或广告决策。 |
| 新 `src/extension/plugin-status-client.ts` | 唯一 ERP HTTP 边界，超时、鉴权、状态码；不直接修改 binding 或运行开关。 |
| `src/extension/storage.ts` | 既有业务配置、binding/模板写入及共享配置串行队列；相关变更与观测资格同一次本地提交。 |
| `src/extension/plugin-status-storage.ts` | 状态队列/seq、canonical binding、决策账本、观测资格与 ACK 短事务；委托同一共享队列，不新增并行 serializer。 |
| `entrypoints/background.ts` | 注册事件与 Alarm、把观察/决策交 controller；复用 generation 和现有关停队列，不再内嵌新的绑定状态机。 |
| popup | 当前店铺、绑定状态、已上报/待上报、服务端等待/人工原因、最后确认时间；提供重新检测/停止/明确切换入口。 |

存储不保留第二份可执行模板或业务配置；desired scope 是用户/服务端授权目标，bound scope 是实际验证证据，两者语义不同。长网络请求不持有 storage 写队列；提交时检查 epoch、episode、本地开关和 generation。状态更新与生成不可变 report 同一短事务，不能先清绑定再因 Worker 中断完全丢掉失效上报。

先将 `boundAdTab` 等既有写入口收敛到 controller，再接自动路径，避免两个 owner 各自 clear/rebind。后台只接线，约 3000 行的大文件不继续增加策略/协议实现。只适配 ads 的小型纯逻辑和页面协调协议，固定来源 commit，实施复制前核对仓库使用授权；不跨独立插件仓直接相对 import，也不在本轮抽公共 npm 包。

共享刷新协议继续使用 `__ttsSellerCenterCoordinationV1`、2 分钟请求 lease、30 秒 refresh claim、15 秒 quiet window。monitor 的身份请求/列表请求/关闭执行进入该协议，自己的刷新与关停还需内部互斥；localStorage 跨 tab 读改写不是严格分布锁，不应宣传绝对消除所有竞争。协调不可用则拒绝刷新。

有界本地队列上限 1000 条且总计 2 MiB；心跳仅在入队前合并，已经分配 eventId/seq 的报告不可改写。失效/决策结果优先发送；允许当前 snapshot 先于历史发送，后端按 seq 防倒退。历史补报完成后发新鲜 snapshot。队列满时不静默丢生命周期/结果，持久化 REPORT_QUEUE_FULL 和最新本地状态、暂停恢复、提示人工；故障期间无法保存的历史缺口必须显式展示，不能声称完整审计。

### 12.1 MV3 持久观测资格与回执证明

此处是本地实现约束，不扩展 HTTP wire：

- `pluginStatusObservationEligibility` 保存 `{schemaVersion:1,generation}`；相关 source 写入与 generation 变更在同一共享队列、同一次 `storage.local.set` 内提交。不以 Worker 内存序号或 mutation depth 作为恢复后的授权。
- 本地 envelope 的 `localEligibility` 带 generation、localId 与 namespaceId，均不进入请求正文。current commit、最终发送准入和 ACK/decision 接收重新核验持久资格；过期 exact carrier 可以退休，但不回退 seq、不重用 eventId、不改历史事实。
- `pluginStatusObservationNamespace` 为独立 UUID，防 clear/reprovision ABA；清理先持久轮换 namespace 与 `pluginStatusClearing=true`，再移除业务键，最后解除 clearing。中断残留按原清理流程恢复，未完成前所有相关读写/发送 fail-closed。
- 当前 ACK 与 decision acceptance 同一短提交，在 ledger 持有 receipt proof 和完整 decision fingerprint；历史 replay ACK 没有命令权，重投不得用相同 decisionId 偷换命令。任何网络或浏览器长副作用都不在 serializer 内执行。

当前只读生产接线保留 `capabilities=[]`、空 executor；canonical running/失效/login/conflict 的投影控制不等于已验证真实浏览器生命周期生成这些状态。native action、90 秒窗口、Cookie Store 全链和端到端验收闭合后，才能另行开启能力，不以本节记录宣布已发布。

## 13. 关键异常场景

| 场景 | 协议结果 |
| --- | --- |
| 页面失效同时 ERP 不可达 | 本地先停，持久排队；网络恢复补报，再取新决策，不离线自行恢复。 |
| Chrome 整体关闭/机器断电 | 无法保证退出事件送达；服务端靠心跳超时感知未知失联。 |
| 失效后用户自行登录 | 被动核验新身份并上报；不能仅因登录成功跳过服务端放行。 |
| recover 响应丢失 | 重放返回同一决策，预算不重复增加；客户端按账本和有效期判断，不重复执行已完成动作。 |
| 执行 refresh 后 Worker 中断 | 不确定结果标 unknown，重新观察页面；不依靠“未收到成功”重复 refresh。 |
| 延迟 invalid 在 ready 后到达 | 保存历史，不倒退投影、不重新触发恢复/通知。 |
| 服务端 resume 与本地关闭监听并发 | 本地关闭优先，epoch/generation 和开关复核拒绝旧许可，上报 rejected。 |
| 服务器报告成功而通知失败 | 报告与通知任务已提交，200 正常；outbox 重试投递，不重写报告、不回滚状态。 |
| 两个 Profile 使用同一实例备份 | 不允许导出该身份；检测 seq/event 冲突，冻结恢复并重新开通，不自动合并。 |
| 多插件共用 Seller 页 | 各有独立实例、scope 与决策；复用页面租约，不将 ads 的恢复决定授权给 monitor。 |
| 同店多 monitor 实例 | 本协议只做各实例观测/恢复，不提供全局唯一关停执行者租约；不能据此宣称跨实例无重复关停。需要这项业务约束时另行批准，不通过“失联即接管”隐式实现。 |

## 14. 后端文件地图与分期

建议后端新增 `tts_erp_v2/plugin/status/`：`contracts.py` 纯校验、`service.py` 事务与策略、`repository.py` 持久化，按需要将 outbox sender 保持独立小模块；薄路由 `api/v2/plugin_status.py`。`app.py` 只 include_router，模型注册/迁移/role matrix/CORS 随实施更新，不改中间件顺序。适配既有代码而非为三个函数拆多层抽象。

| 阶段 | 交付与闸 |
| --- | --- |
| D0 方案评审 | 用户已批准实施本方案，包含首次放行、独立 status key、失效后断网保持暂停和自动恢复预算；转入分组件开发和 owner 审查。 |
| D1 后端状态基础 | 纯契约、实例与报告持久化、最小权限；决策固定 hold，验证重放/乱序/鉴权，不触达真实广告。 |
| D2 服务端闭环 | 决策/结果、episode、策略、巡检与生命周期通知 outbox；人工接口可审计，fake browser/sender 完成闭环。 |
| D3 monitor 接入 | 收敛 binding owner、复用发现/探测、持久队列和恢复门禁；遵守插件自身 strict TDD 与覆盖率要求。 |
| D4 集成验收 | 受控 HTTPS/CORS、真实 Profile/登录/休眠场景；关停写操作另需明确授权，模拟验证不能替代实店验证。 |
| D5 后续插件 | 使用相同 envelope/实例/状态读侧，仅接各自获准能力；不默认授予 monitor.resume。 |

实施必须先协调 access/security/app/model 热点及 monitor storage/background 的现有 lane。配置 GET、force 和 status report 是独立 owner；在配置方案主线合入前使用其固定提交作兼容核对，不依赖一个不存在的 master 文件。当前文档不占有任何未来代码文件。

## 15. 验收矩阵

| ID | 可观察验收结果 |
| --- | --- |
| S01 | 不同插件/实例/店铺独立投影；伪造 pluginId/instanceId/scope、普通 key、monitor_config key、浏览器 Cookie、过期/吊销 key 均不能取得恢复许可。 |
| S02 | 页面关闭即本地暂停，ERP 接收 invalidated；没有服务端决策不重绑或恢复，网络断开也不例外。 |
| S03 | 同 event 重试只写一份事实/一份决策/一份通知任务；内容冲突或同 seq 不同 event 为 409。commit 前失败为 5xx，不返回假成功。 |
| S04 | 低 seq invalid 晚到只归档；补报旧 heartbeat 不变成当前就绪；服务端超时只显示 stale，不推断登录失效。 |
| S05 | recover→ready→resume→result 完整闭环；下发仅为待确认，applied+实际 running 后才显示恢复。 |
| S06 | 旧 epoch/episode/revision、过期决策、历史响应、本地关闭并发和未知能力全部拒绝；不能改变业务阈值/auto-close 开关。 |
| S07 | 同目标多页、无候选、多店歧义、SPA、loading、discarded、登录恢复、Cookie Store 差异均按 §5 处理；不会沿用旧身份切页。 |
| S08 | Worker 在入队/接收/refresh/结果提交任一阶段中断，重新唤醒不归零 seq、不重复确认副作用，不靠 setInterval 保持存活。 |
| S09 | 三次/十分钟预算、一分钟间隔和故障通知去重可用受控时钟验证；用户 disabled 不触发故障催促。 |
| S10 | 429/5xx/网络、401/403/409、队列满、无效 200 正文和响应丢失不造成快速循环、报告假出队或自动绕闸。 |
| S11 | 刷新尊重 ads/monitor 共享请求租约；正在发出的旧关闭结果不能被伪装成新 epoch 成功。 |
| S12 | outbox 失败不丢失状态事实，sender 事务外运行；重复巡检无重复通知任务，不向 payload/日志泄露密钥或其他店铺身份。 |
| S13 | 受控 HTTPS、部署前缀、真实签名扩展 CORS/host permission、Chrome 休眠/重启分别有真实联调证据；缺少证据标未验证。 |
| S14 | legacy/v2、分页完整性、扫描/报表窗口隔离、SKU/白名单、关闭队列、远程配置和现有飞书业务通知回归不新增失败。 |

实施测试路线：后端最窄 scope 与 DoD 只通过 `bash scripts/test_isolated.sh ...` / `bash scripts/test_baseline.sh`，迁移只在测试模板/临时 clone 验证；插件在独立仓运行其 Vitest、coverage、tsc 和 build，不用 ERP Python runner 扫 plugins。涉及失效/恢复/乱序/鉴权的行为先写失败用例，复用真实 storage/controller/service，只 mock Chrome、HTTP、DB 时钟等外部边界，不能把整个状态机 mock 掉。

**方案交付阶段（实施前）仅改文档**：当时验证 Markdown 结构、相对路径、JSON 示例、无占位、协议内部一致性及 git diff，不运行数据库/插件测试，不宣称上述 S01–S14 已实现或通过。

## 16. 部署、兼容与回滚

- 先部署后端、开通实例，再接插件；未登记/未升级插件显示“未纳管”，不把没有心跳当成新故障。旧 status 404 不是服务器自动同意恢复。
- 新协议 schemaVersion=1，与插件软件版本、monitor 配置 schemaVersion 相互独立。未知 schema/能力明确拒绝；不静默降级日志上传或自动切回旧本地恢复路径。
- 原有健康本地监控不会因上报网络短断被无条件关停；本次新增的初始化/失效恢复闸一旦纳管，不因 status 错误绕开。ERP 告警不能保证在客户端离线时立刻阻止其已健康运行中的工作，不能宣传成实时远程 kill switch。
- 回滚使用应用/插件版本 revert；状态与审计表保留，不 DROP、不清生产事实。回退旧插件会失去服务端恢复闸，必须人工确认已停止该实例监控后才回退，不能标成无行为变化回滚。
- 生产迁移、服务重启和真实 TikTok 写请求由人操作。本文件不执行迁移、清理、重启、凭证生成或真实报告上报。

## 17. 设计依据、决策记录与交付边界

### 17.1 Baseline 与设计必要性

- 需求基线：用户本轮“接受推荐自动绑定方案；页面失效通知服务端；由服务端决定下一步；通用 plugin/status/report 接口及后端/插件协议落技术方案”。
- 架构基线：[架构概览](../architecture/architecture-overview.md)、[进程架构](../architecture/process-architecture.md)、[外部 API 活契约](../api/external-api.md)、[安全规则](../guides/agent-safety.md)、[Git 工作流](../guides/agent-git-workflow.md)。均已读取；新的 HTTP 路径在实现前还需同步活契约，不能把本草案当已上线清单。
- 代码基线：`tts_erp_v2/access/{_policy,_types}.py`、`api/v2/analytics.py` 的 plugin-logs、`db/models/plugin.py::PluginLog`，以及两个插件 §1 固定提交；索引可能过期，判断以直接读取源码为准。
- 待合入契约：monitor 配置文档 §4.2/§6/§7 保留原用途与失败语义；广告通知调研只作区分生命周期状态与广告业务事实的参考，不将未批准方案当依赖。
- Change Necessity：既有日志只能记录文字，无法承载服务端决策与可靠执行回执；新协议必要。ArchitectureReviewRequired：是，涉及公共契约、状态真相与安全恢复权限；当前仅草案，不生成“accepted” ADR。实施获批后由协议 owner 记录正式 ADR，不复制到两个插件各自成为第二份契约。
- 复杂度边界：一个公共状态 owner、一个 monitor 绑定 controller，复用调度/鉴权/队列；不建通用远程代码执行、长连接、跨插件绑定服务或全局事件平台。后续多店并行/跨实例排他关停不是本方案暗中承诺。

### 17.2 外部先例

- [Chrome Alarm](https://developer.chrome.com/docs/extensions/reference/api/alarms)：周期最低 30 秒，可能延迟，启动检查 Alarm；不是精确调度承诺。
- [MV3 Worker 生命周期](https://developer.chrome.com/docs/extensions/develop/concepts/service-workers/lifecycle)：全局内存会丢失，关键身份、队列、决策账本必须持久化。
- [Kubernetes Leases](https://kubernetes.io/docs/concepts/architecture/leases/)：借鉴心跳与控制器责任分离，不照搬其超时常数或部署依赖。
- [OpenTelemetry service identity](https://opentelemetry.io/docs/specs/semconv/resource/service/)：借鉴不透明且唯一的实例身份，不收集设备隐私。
- [CloudEvents v1.0.2](https://github.com/cloudevents/spec/blob/v1.0.2/cloudevents/spec.md)：借鉴事件唯一标识与重复投递语义，本 envelope 不是 CloudEvents 协议。
- [RFC 9110](https://www.rfc-editor.org/rfc/rfc9110.html)：Retry-After 与 HTTP 重试语义；POST 重试安全性来自本协议持久幂等，不来自方法本身。

### 17.3 文档自审与验证

- 自审修正了公共 execution 与 monitor 能力字段混杂、快照证据字段缺失、重放 serverTime 可能延长有效期、人工 hold 被自动策略覆盖及首次 advertiser 锁定的歧义。
- 2026-10-15：两个 JSON 示例可解析，UUID、报告回执、episode/epoch 对应一致；七条相对链接存在，代码围栏成对，无未填写占位。主动 Markdown LSP 检查 1 文件、0 diagnostics；暂存前后执行 diff 空白检查。
- 上述仅是文档检查；S01–S14 是后续实施验收，不是本轮测试结果。没有运行数据库测试、真实报告或 TikTok 业务请求。

### 17.4 本轮止点

最初文档交付止于评审。2026-10-15 用户进一步授权 owner 调度 MiniMax M3.1 Flash Preview 开发完整方案并审核一致性，原“仅设计”止点解除，实施计划已建立。首次放行、独立 status key、离线安全暂停、预算及通知归属纳入开发基线；生产迁移/重启/真实 TikTok 写操作仍不授权。实现、自动化和真实环境证据分别记录，未满足 §15 前不得宣称全部完成。
