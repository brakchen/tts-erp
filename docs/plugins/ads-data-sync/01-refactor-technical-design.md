# ads-data-sync 大重构技术设计

> 日期：2026-10-11。状态：技术设计定稿，待整体评审；**不是已实现功能说明**。
> 用户已确认产品边界，授权作者决定内部实现。本文不授权生产迁移、数据清空、服务重启或插件发布。
> 基线：插件 `221e4531145e6054ab802d5ddf75595cdbffb8c6`（manifest/package `0.1.244`）；ERP `c7f3f25b87a006016abbabf7a44d005419436a8e`。本次仅交付文档。

## 1. 目标、范围与最终决定

选择“深模块 + 应用门面 + 版本化本地仓储”，复用已有每日广告分析同步模块，不引入 XState，不另造通用工作流引擎。

- **Phase B：内部模型重构。** 收回生命周期所有权、拆开混合存储、加入可靠本地交付，完成本次明确批准的行为简化。
- **Phase C：产品级增量。** 按稳定性 → 效率 → 操作体验交付；不把所有愿望塞进 Phase B。
- 不实现广告计划状态同步；不写店铺、不关闭广告、不发送飞书消息。
- 不更改 TikTok 请求契约、同步端点选择和店铺当地日历语义。
- 保持现有 ERP dumps URL、`protocolVersion: 4`、业务自然键 upsert。**没有 v5、idempotencyKey、receipt 表、payload hash 冲突协议或 ERP 空结果表。**
- ERP 已有的事实不在插件建立长期副本；短期内存缓存允许。插件独占的空结果标记和尚未交付的 payload 是例外，不是 ERP 数据副本。
- 一次运行只服务一个有效 Seller/Advertiser/目标地址；不设计非空 Outbox 的切店迁移。

### 1.1 已确认的产品行为

| 范围 | 最终规则 |
| --- | --- |
| 日期 | 不采集 T0；日常推进店铺当地 T-1；安装/缺洞补采 `[S..T-1]`，S 继续遵守配置、店铺开店日与广告计划创建日的现有边界 |
| 已采集 payload | 闭合日期的同一转储单元进入 Outbox 后不可变，不覆盖、不建版本链；这是客户端政策，不宣称 TikTok 历史指标绝不会修订 |
| TikTok 健康请求 | 全局最多 8 个在途请求，启动间隔至少 500ms；共享限流器、优先级预留槽、慢响应保护保留，不退化为单并发 |
| 普通失败 | 网络、超时、5xx、响应不完整固定 30 秒后重试，无次数上限、无指数退避 |
| TikTok 429 | 有效 `Retry-After`：`max(30秒, Retry-After)`；缺失/无效：30 秒 |
| ERP 不可用 | 不启动新规划/新采集；每 30 秒单飞检查；有 Outbox 时队头上传就是探测，空队列时使用现有健康检查；恢复后先排空，再查询最新 Coverage |
| Outbox | 300 MiB 硬上限、严格 FIFO、非 2xx 永久重试；不跳过、不淘汰、不人工删除；2xx 后出队 |
| 空结果 | 保留本地轻量空结果 checkpoint；不新增 ERP 表。重装后可以重新采集空日期 |
| 人工介入 | 删除 `needsHumanDumps` 与手动暂停；保留可选“立即重新检查”，不点击也自动恢复 |
| Seller Center | 每 30 秒检查，优先复用现有页面，无页面时创建一个后台页；页面存在时不重复创建，关闭后下次检查重建 |
| 登录失效 | 只显示扩展红色 `!` 与 Popup 提示，不切前台、不抢焦点；恢复后自动继续 |
| Popup | 删除持久化 `popupSyncState`；本地先展示、ERP 异步补齐、变更通知后局部更新；不 reload 页面 |
| Runtime Log | 仅本地，最近 30 天且最多 300 MiB，先触达者生效；best-effort；删除 ERP 上传链路 |
| 被动抓包 | 域名观察功能永久退休，基线 `221e453` 已删除；保留同步所需的身份/请求模板捕获，不误删业务桥接 |
| 升级 | 产品部署按全新安装处理，无跨版本 Outbox 迁移；普通 MV3 Worker 重启不等于升级，不清空数据 |

### 1.2 撤回项与行为变更边界

讨论中的 v5/幂等键、ERP 长期空 Coverage 表、单并发 TikTok、指数退避、人工失败队列、手动暂停均已撤回。不得以另一名称重新引入。

Phase B 不是“所有行为完全不变”：Outbox、固定重试、持续自动绑定、暂停/日志上传退休是用户明确批准的例外；其余外部契约保持稳定。不能借结构重构增加端点、减少健康并发或重做广告发现范围。

## 2. 现状核验与需要纠正的旧说明

主要代码在插件独立仓库：`entrypoints/background.ts`、`src/extension/daily-analytics-sync.ts`、`src/core/dump-progress.ts`、`src/core/analytics-sync-v2.ts`、`src/extension/storage.ts`、`src/extension/runtime-log-store.ts`、`entrypoints/popup/main.tsx`。

现状不是本设计目标：

1. `DataSyncState` 混有配置、绑定、计划列表、断点、成功记录、错误、进度和日志；Popup 投影也持久化。
2. 每日同步已有深模块，但轮次仲裁、作用域修订、网络执行和状态写入仍分布在 background 及注入回调中。
3. 现有采集/上传 worker 是 8/8、内存缓冲容量 16、Coverage worker 4；HTTP TikTok 另有 8 在途/500ms 全局限流。**上传改为 FIFO 单飞不等于将 TikTok 改为单并发。**
4. 没有持久化 payload Outbox；`nextPendingDump` 只是单元指针。现有 upload 函数内部最多尝试三次，HTTP 成功后仍解析 JSON/envelope。
5. Runtime Log 已有独立串行写 lane 和分段 Storage，当前预算为 500 MiB；仍有日志 ERP 上传及游标。不是本设计的 30 天/300 MiB。
6. 实际生产候选截止当地 T-1；`today` 类型/fixture 不能当作 T0 生产路径证据。当前集合注册表/设置仅启用商品分析与变更日志；会话分析虽有构造器及前置商品查询代码，但已禁用。本重构不重新启用会话分析。发现实际结束日期也截止T-1；默认起点fallback为2026-07-01，存在服务器开店日期时优先使用该值，不沿用旧README的“默认前31天”。
7. 当前 `evaluateAutoBind` 扫描已有 Seller Center 标签页，无候选时返回；不能把旧 helper 的“一次尝试”注释当作现行完整行为。目标新增缺页后台创建和持续恢复。
8. ERP `tts_erp_v2/plugin/ads/repository.py`：产品级 Coverage 来自 `plugin.ad_daily`；campaign 级 Coverage 来自 `plugin.ad_raw_log`。因此“raw log 完全不参与业务查询”不成立。模型注释建议保留 90 天，不代表已验证存在清理任务。
9. ERP 产品级 daily 行与 raw log 在仓储事务提交；`campaign_opt_logs` 是后续独立写入，异常被捕获，仍可能返回 200。本设计不声称现有 v4 提供所有副作用的原子、恰好一次保证。

ERP 原有文档广告章节含历史协议描述（已有共享 finding），实现核验优先，本文不顺带修改他人 API 文档 lane。

## 3. 模块与唯一所有者

```text
Chrome/WXT events ─┐
Popup commands ───┼─> AdsSyncApplication
Content messages ─┘       ├─ DailyAnalyticsSync（规划、已有纯算法）
                          ├─ SellerSession（身份、标签页、页面租约）
                          ├─ TikTokCollector（请求契约、分页、共享限流）
                          ├─ ErpClient（coverage、v4单次上传）
                          ├─ OperationalRepository（设置/检查点/Outbox）
                          └─ RuntimeLogRepository（独立、本地、尽力写入）
```

`AdsSyncApplication` 是完整同步生命周期的唯一 owner：命令验证、轮次合并、作用域 fencing、调度、恢复、Outbox 交付与前置条件重评。background 只同步注册监听器并组合依赖，不再拥有另一套流程。

建议目录（实现时按职责落地，不以文件数量为目标）：

```text
src/application/ads-sync-application.ts
src/application/sync-scheduler.ts
src/application/popup-projection.ts
src/extension/daily-analytics-sync.ts        # 深化现有模块，不另建竞品
src/extension/seller-session.ts
src/extension/tiktok-collector.ts
src/extension/operational-repository.ts
src/extension/runtime-log-repository.ts
src/core/analytics-sync-v2.ts                # 保留wire v4编解码，移除内部重试
src/core/*                                 # 日期、分页、限流等既有纯逻辑
entrypoints/background.ts                  # composition root / adapter
```

依赖只能由入口向应用、应用向领域/端口流动，repository 不反调应用，core 不依赖 Chrome/React。没有通用事件总线、命令持久化队列或双实现开关。

### 3.1 调用者接口

```ts
interface AdsSyncApplication {
  dispatch(command: AdsSyncCommand): Promise<CommandResult>;
  readPopupProjection(): Promise<PopupProjection>;
}
```

命令集合按来源白名单校验：

| 命令 | 语义与并发 |
| --- | --- |
| `wake {reason}` | 启动/alarm/恢复事件；合并成最多一个待执行唤醒，不累积 tick 队列 |
| `saveSettings {patch}` | 校验后原子保存；令牌可更新，Outbox 非空不得改变目标地址/身份；不支持 syncPaused |
| `recheckNow` | 合并为一次提前检查；不得越过 429 的 not-before、已有网络在途或容量约束 |
| `discoverCampaigns` | 显式发现仍保留；与自动发现单飞，不与本地恢复互抢状态 |
| `sessionChanged {tabId}` | 只接受受信任 adapter 事件；重新核验 Seller/Advertiser，payload 不能自行授予身份 |
| `collectionsChanged` | 由设置入口归一；只改变后续采集，不删除已进入 Outbox 的数据 |

内部网络完成不是可由 Popup 伪造的公开 command。结果只代表命令被接受/拒绝，不另造 ERP HTTP 成功通道。运行日志分页、导出、清空通过独立受限 Log query/command，不把日志管理膨胀进生命周期接口。清空仅限诊断日志，绝不能清空 Outbox。

## 4. 本地存储：两个数据库、两条写 lane

### 4.1 选择 IndexedDB + idb

Operational（含 Outbox）使用一个 IndexedDB 数据库；Runtime Log 使用另一个 IndexedDB 数据库。两者各一条 single-writer Promise lane，跨 repository 可并行。与把 300 MiB 数组放入 `chrome.storage.local` 相比，IDB 能按记录读写并在同一事务内更新队列、计量和检查点，不需要手写分段 WAL。

复用调研结论：现有工程未提供 IDB 仓储；已有运行日志 lane、纯分页/日期/限流代码应复用。选择小型维护中的 [`idb`](https://github.com/jakearchibald/idb)，计划锁定 **8.0.3，ISC**；[npm 元数据](https://registry.npmjs.org/idb/8.0.3) 含 ESM、TypeScript 类型且没有 runtime dependencies，适合当前 WXT/TS。它只包装 IDB，不替代领域模型。不增加 Dexie、XState、自研 Promise-IDB 通用封装。实施时核验安全公告和 lockfile；本轮不安装依赖，不宣称已完成依赖安全扫描。

所有写请求与 `tx.done` 都必须成功才算提交；事务内只等待 IDB 请求，不能等待 fetch、Chrome tabs、日志写入或计时器。请求成功事件不等于事务提交。

已有 `unlimitedStorage` 保留；它不是物理磁盘无限可用的承诺。Chrome 版本基线至少 120（支持 30 秒 alarm）；发布时增加明确最低版本约束。

### 4.2 Operational schema（本地版本 1）

数据库建议名 `ads-sync-operational`：

| object store | 内容 | 唯一 owner / 生命周期 |
| --- | --- | --- |
| `settings` | 目标URL、令牌、发现窗口、启用集合等必要用户配置 | OperationalRepository；不含手动暂停、watchedDomains |
| `checkpoint` | 单一作用域描述、已绑定/候选tab提示、剩余工作、固定retryAt、必要发现期限 | 同上；完整轮次结束清除剩余工作，不保存永久成功账本 |
| `emptyUnits` | unitKey、observedAt（已确认上传的空结果） | 同上；仅插件独有；保留当前同步窗口需要的标记，窗口外可裁剪 |
| `outbox` | seq、unitKey、固定目标与scope、wireBody、capturedAt、recordBytes | 同上；seq自增FIFO、unitKey唯一；直到2xx确认事务 |
| `meta` | usedBytes、队头retryAt/错误摘要、计数、容量reservations | 同上；与outbox操作原子更新 |

`unitKey` 为无歧义元组序列化：规范化目标地址、Seller、Advertiser、endpoint、campaignId、当地日期（现行 daily）。它只去重本地未交付单元，**不发送为服务器幂等键**。`requestId` 仍是普通随机追踪ID；Outbox 重放时不重建 requestId、createdAt 或正文。

Outbox 的 `wireBody` 是最终 v4 JSON 字符串，是唯一持久 payload 副本。HTTP Bearer 令牌不在 body/日志中，发送时读取现有设置；更换同一目的端的令牌不改变队列正文。未完成单元指针在入队事务中从 checkpoint 移入 Outbox，不能两边长期维护一份待办。

### 4.3 DataSyncState 字段处置

| 原字段/字段族 | 目标处理 |
| --- | --- |
| `settings` | 保留必要配置；删除 `syncPaused`；enabledCollections 不等同于全局暂停，关闭端点不撤回Outbox |
| `boundTab`, `autoBindFlow` | 收缩为身份/候选tab恢复提示；每次启动核验Chrome真实tab，不把tabId当永久身份；删除一次性失败终止位 |
| `shopRegion`, `shopOpenedDate` | 通过ERP读取，Worker内存缓存；地区未知时阻止采集，不猜测时区 |
| `campaignIds`, `campaignStartDays`, `campaignListTemplate` | 当前发现结果/模板置内存；checkpoint仅保留恢复剩余工作的必要ID/边界，结束即释放；模板丢失重新捕获 |
| `nextPendingDump`, `dumpCursor`, `missingDumps` | 统一成未完成工作检查点，不保留三套游标/缺洞map |
| `deferredDumps` | 合并为未完成单元的retryAt与最小错误摘要；attempt次数只用于诊断，不决定终态 |
| `activeDumps`, `syncActivity` | 当前Worker内存；持久状态不宣称旧请求仍在运行 |
| `confirmedDumps`, `historyState` | 删除长期本地成功账本；本轮确认只在内存，完成工作从checkpoint移除；无可用检查点时查ERP |
| `emptyDailyDumps` | 收缩为 `emptyUnits`；不新增ERP表、永久服务器receipt或第二份payload |
| `todayDay`, monthly相关状态 | 删除无生产价值的兼容分支；生产只走当地日级历史，不改变实际支持的endpoint |
| `rolloverDay`, `dumpCycleCompletedFor`, `syncScopeKey` | 收敛为checkpoint的window/scope generation；不得独立成为“完成”的权威 |
| `syncProgress`, `dailySyncProgress`, `dailySyncSnapshot`, `dailyThroughput`, `monthlyThroughput` | 从本轮规划/网络结果派生的内存展示；不回写业务事实 |
| `nextSyncAt`, `nextCampaignDiscoveryAt` | 前者由最早retry期限与alarm派生；后者保留必要检查点期限，保留当前10分钟发现节奏 |
| `runtimeLogs`, `capturedRequestLogs`, `pluginLogUploadedThrough` | 从主状态删除；日志仅独立仓储，captured响应只用于业务流程；上传cursor退休 |
| `lastSuccessfulSyncAt`, `lastFailedSyncAt`, `lastErrorSummary` | 活跃问题摘要与必要retry状态合并；历史诊断查日志，不存第二份历史事实 |
| `needsHumanDumps` | 字段、类型、调度跳过、UI列表和特殊回队全部删除 |
| `popupSyncState` | 删除存储键；只保留Popup自身非权威视图状态 |

不持久化通用 operation intent。checkpoint 是“还有什么没做”，Outbox 是“已拿到但未确认的数据”，不是任意步骤的事件溯源账本。

## 5. Outbox 原子性、容量与 FIFO

### 5.1 容量定义

- `C = 300 × 1024 × 1024 = 314572800` bytes。
- `P = 2 × 1024 × 1024 = 2097152` bytes：**完整ERP请求正文**上限，不仅是rows。
- 每条元数据设独立编码上限 `H = 16 KiB`（seq按固定宽度计量，有限长度字段，错误正文不嵌入记录）；每个活动转储单元预留 `R = P + H`。
- 计量为 UTF-8 wireBody + 确定性元数据编码字节数，计数/预留管理开销在固定管理预算 `G = 64 KiB` 内；保持 `used + reserved + G <= C`。
- 最多8个采集worker对应8份转储单元预留，约16.125 MiB；分页和会话前置商品查询共用该单元预留，不为每页无限追加预留。HTTP同时在途数另外由既有8槽限流器约束。
- 这约束逻辑存储字节，不宣称IDB文件物理大小恰好300 MiB；索引/页开销额外存在，磁盘错误仍可发生。

`reserve(unit)` 在同一meta/checkpoint事务内查重并测试预算；不足则不向TikTok发起该单元的首个请求。成功采集后以真实字节数替换预留，不足额/超过P或H绝不截断、假成功或上传未落盘payload。

大于2 MiB的完整响应是现有协议限制：不另造拆包协议、不截断rows，记录可诊断错误，释放预留，保留未完成单元按30秒重试；数据持续超限时不会自愈，需修复实现或另行审批协议，不能许诺重试必然成功。

### 5.2 处理时序

```text
为转储单元预留容量（事务）
→ 在锁外经共享限流器完成TikTok全部分页和验证
→ 构造固定v4 wireBody并测量UTF-8
→ 原子插入outbox + 使用量调整 + 移除原未完成指针（事务）
→ 等tx.done
→ 等待该seq成为队头
→ 在锁外上传（可用刚构造的内存正文，不立即回读IDB）
→ 收到2xx
→ 原子删除该seq + 释放字节 + 更新必要空结果checkpoint（事务）
```

当前内存对象只是加速路径，不是owner；重启或重试从IDB读队头。FIFO指**持久化入队seq顺序**，不是开始请求顺序；并行采集完成顺序不同不影响数据正确性。相同unitKey第二次入队不覆盖现有payload，返回已有seq，禁止为同一单元制造第二版本。

队头一次只能有一个ERP HTTP请求。`ack(seq)` 必须校验它仍为当前队头且提交成功，不能只凭内存 shift。收到2xx后若删除事务失败，记录仍在，下一轮可以重复上传，接受raw log重复。

### 5.3 与8路TikTok并发的配合

- 恢复/重试模式：先排空持久化Outbox，期间不接纳新的TikTok转储单元，也不开始新Coverage规划。
- 健康模式：沿用8个采集worker及8 HTTP槽、500ms启动节奏。只有Outbox为空且有容量才接纳新的采集单元；已有预留的单元允许完成其分页、校验和入队，不丢弃迟到的完整响应。
- 队列出现积压/ERP失败后关闭新单元准入；已获预留的有限工作收尾。严格顺序上传直到队列为空，再恢复采集。不能用不断接纳新单元的方式“边排空边采集”。
- 保留慢响应保护和2个高优先级预留槽；它们是健康请求保护，不是递增失败退避。删除旧连续timeout 5–60秒backoff。
- 不恢复现有8路ERP上传池；它与严格FIFO确认不相容。TikTok配置不变不等于承诺端到端总吞吐完全不变；ERP FIFO及IDB开销必须测量、披露，不能偷偷将TikTok串行化来通过测试。
- 300 MiB是故障/恢复的安全上限，不是必须填满的目标；故障即停止生产时正常积压往往远小于300 MiB。

### 5.4 各中断点

| 中断位置 | 恢复 |
| --- | --- |
| 预留后/分页中 | 新Worker fencing旧回调，清除失主预留，保留单元未完成；重新采集 |
| 已取得响应、入队事务未提交 | 事务回滚；没有承诺持久化，重新采集，不上传半条记录 |
| 入队提交后、HTTP前 | 从Outbox读取，不请求TikTok |
| ERP已提交、响应丢失 | 相同wireBody再发；业务upsert，raw log可重复 |
| 已收2xx、ack未提交 | 重发同一条，不查询coverage代替ack |
| ack事务已提交 | 记录不存在，不重复交付；本轮内存进度可重建 |
| 日志写失败 | 同步与ack不受影响，内存显示有限诊断，不递归记日志 |
| Outbox打不开/记录不合法 | fail closed，不静默新建空队列，不自动清库；显示存储错误并重试打开 |

普通checkpoint不可用可查ERP重建，但**不得借此丢弃仍可能存在的Outbox**。没有checksum、损坏修复状态机、死信队列或兼容解码链。仅保留必要schema/字段校验。Worker启动先建立新的generation、使旧页面结果失效，并沿用页面请求租约/超时协调，避免遗留请求与新请求共同突破8槽。

## 6. 调度、错误与自动恢复

使用现有 `chrome.alarms` 0.5分钟心跳作为持久唤醒兜底；运行态短定时器用于到期及时触发，不能代替alarm。启动检查alarm是否存在，不能每次重建而不断推迟执行。30秒是**失败结束后最早重试期限**，不是浏览器休眠期间的实时保证；醒来合并逾期任务，不补发堆积tick。

应用在短critical section内决定工作/记录generation，在锁外执行网络，在短事务内验证并提交。不得持single-writer lane等待HTTP。重复wake/recheck合并，discovery、规划、Outbox上传各自单飞，失败不得使Promise lane永久拒绝后续操作。

| 情况 | 行为 |
| --- | --- |
| 网络/timeout/5xx/不完整响应 | 未完成单元 retryAt=失败完成+30秒；无永久终态 |
| 429 | 解析秒数或HTTP-date；期限=max(当前+30秒,服务端期限)；作用于当前Seller/Advertiser全局准入，未开始的请求共同等待 |
| 点击立即重新检查 | 可检查前置条件/提前普通retry；不能绕过Retry-After、上传单飞、限流、存储门禁 |
| ERP队头任何非2xx/未知结果 | 保留同body、30秒后单次重发；不读coverage、不轮转后续、不设置nonRetryable |
| ERP无Outbox、健康恢复 | 获取最新coverage完成规划再采集；健康检查通过但coverage失败仍不开生产 |
| 登录/配置/标签页缺失 | 每30秒检查或通过事件触发；无有效会话时不盲发分析请求；条件恢复自动继续 |
| 缺页 | 单飞scan/create，优先当前绑定/已有固定Seller页；create后持久候选提示；重启先scan避免创建重复页 |
| 登录页存在 | 视为受管理候选页，不因“未登录”每30秒再开一个；红色!、Popup说明；不主动聚焦/提交登录 |
| 固定scope不匹配 | 不把另一个Seller绑定为当前scope；提示并等待；非空Outbox的目的地不可变 |
| quota/磁盘不足 | 停止新采集；保留已持久数据；可尽力清理可淘汰日志，不清Outbox；每30秒重评 |

自动登录不在能力范围内：重新认证可能仍需用户操作，但不需再点击“人工重试”。错误摘要是展示而不是终态标志。持续确定性错误可能无限重试；此设计不承诺所有bug能靠重试修复。

页面刷新继续遵守现有2小时条件与跨插件页面租约，不私自更改订单插件协调协议。现有候选页可复用但不能因扫描无效而批量关闭用户页面。

## 7. ERP v4与空结果边界

仍调用 `POST /v2/analytics/sync/dumps`，正文包含现有 `protocolVersion:4, requestId, scope, dump`；ERP认证和2 MiB限制不变。本方案不增加任何服务器表、迁移或端点。

- daily唯一键：Seller、Advertiser、endpoint、campaign、product、day；冲突更新现有指标，重复上传不会增加同键业务行。
- 这只是**至少一次交付 + 业务键upsert**，不是恰好一次。重复写仍可更新updated_at、增加raw log；迟到旧payload在多写方情况下可能覆盖新值。单有效scope/固定payload降低本插件风险，不提供跨客户端版本仲裁。
- 新客户端 upload adapter 每次仅发一次；2xx 是出队信号，响应体可读作诊断，不能因响应JSON解析失败将已收2xx改为失败。没有新增data.status等成功通道；现有v4 JSON响应仍由ERP原样提供。
- 对既有campaign_opt_logs独立写失败仍200的问题，保持兼容并明确风险：当前确认的是v4现有接收行为，不是二次派生表完整性的新增保证。上线可靠性验收需展示这个边界；若业务要求更强保证，应单独修复ERP，不在此文静默承诺。
- 空rows也先入Outbox、上传；2xx的ack事务同时写本地emptyUnits。不能以HTTP200但缺少TikTok必要结构的响应伪造“空”。
- 产品级规划以 `ERP coverage ∪ 本地emptyUnits ∪ 本轮成功内存集合` 去重；其中长期本地例外只有emptyUnits。安装清空后重新查询空日期是已接受成本。
- campaign级coverage依赖raw log的现状保留；如果未来清理raw log，该类旧日期可能再次缺失。本任务不部署清理任务、不保证90天自动清理。

## 8. Runtime Log 与 Popup

### 8.1 日志独立、可淘汰

`ads-sync-runtime-log` IDB按递增序号存事件，时间索引按接收UTC时间；meta保存可校验计量。单独写lane、事务append/淘汰，先清除超过30天事件，再从最旧开始删到300 MiB内。启动和append时执行维护；浏览器关闭期间不能保证物理文件即时清理。

Outbox和日志各300 MiB，合计逻辑预算最多约600 MiB，绝非共同300 MiB。日志允许淘汰，Outbox不允许。只有日志可由UI清空。

为避免长期保存第二份业务payload，新日志只存endpoint、unit/requestId、页码、耗时、状态、计数和有界脱敏错误摘要；**不再把完整业务request/response复制进Runtime Log**。调试信息不能包含Bearer、Cookie、令牌或用户凭据。HTTP摘要与Outbox之间用requestId关联。日志分页查询/导出，不把300 MiB一次传给Popup；写失败不拒绝同步、不建立第二份持久日志fallback。

删除 `flushPluginLogs*`、`readRuntimeLogUploadBatch`、`advanceRuntimeLogUploadCursor`、`countPendingRuntimeLogs`、upload scope/cursor、`data-sync:upload-plugin-logs` 及UI按钮。ERP旧接收端本轮不删，避免影响其他版本。

### 8.2 Popup查询与通知

打开时仅调用应用query：先从已加载本地状态生成设置/绑定/Outbox/当前活动，再异步补齐ERP数据。ERP只做Worker内最多30秒短缓存，请求去重；`stale/loading/unavailable` 明确标识，不将旧值冒充最新。UI查询不触发TikTok采集或改变业务窗口。

Background发布合并的 `state-changed {revision}` 失效通知，不广播整个状态；Popup重查，忽略旧revision结果。ERP数据到达、恢复、队列变化均触发通知；重连/重新获得焦点再次查询，通知丢失不影响同步事实。打开期间可保留10秒兜底query，查询合并且不强制每次网络请求。

React更新对应字段，不 `window.location.reload()`、不卸载整个表单；未保存输入、焦点、滚动位置和展开状态保留。日志面板按需独立分页，不放入Popup主投影。永久删除持久化 `popupSyncState`、日志投影以及手动暂停/人工失败列表。

## 9. 安装、普通重启、迁移与回滚

### 普通Worker重启

恢复顺序：打开Operational → 核验schema/作用域并fence旧回调 → 恢复Outbox/容量计量并释放失主预留 → 重建alarm/标签页状态 → 排空Outbox → 恢复剩余checkpoint；需要ERP规划时重新取coverage。内存不存在只会损失缓存，不会损失已入队payload。

有效本地剩余checkpoint优先，过期窗口/设置revision使它失效时从ERP重建；不能以完整历史已完成快照替代ERP。日志数据库失败不阻止上述步骤。

### 升级与清空

用户选择升级按全新安装处理，这是部署约定，**不是Chrome默认升级就会清空Storage的事实**。未来正式升级路径需明确一次性清理哪些旧store，不能在Worker每次启动时执行清理。本轮文档不会运行清空。

**代价：非空Outbox在清空/卸载/升级重置时会丢失尚未交付的唯一payload。** ERP coverage只能指导重新采集，不能还原当时那份响应。发布步骤应先在正常工作下确认Outbox为空再重置；不得为清空增加日常UI按钮，也不得宣称无条件无损升级。没有跨版本兼容迁移、checksum或隐藏备份副本。

恢复路径：全新安装 → 配置URL/令牌 → 自动绑定与用户登录 → ERP查询地区/coverage → 重建缺口与本地空结果。普通代码结构切片在开发阶段使用隔离profile，不轮番迁移真实用户数据。

### 回滚

只使用Git/发布版本回滚，不保留运行时双轨旧路径。持久化模型不变的结构切片可回退提交；跨schema版本回滚遵守全新安装模型与上述数据损失警告。未交付Outbox不得以“回滚需要”为由自动丢弃。

## 10. 实施切片与删除清单

| 切片 | 交付与删除 | 必须通过的检查 |
| --- | --- | --- |
| B1 应用门面 | 包住现有daily模块，迁移轮次仲裁/generation，background只接线；迁走即删旧owner | 现有日期/作用域/健康8槽/500ms/分页行为测试 |
| B2 事务仓储 | idb Operational + Log独立lane；定义计量、reserve/enqueue/ack、存储故障边界 | 真浏览器IDB事务abort/重启/并发边界；不只用mock验证 |
| B3 Outbox交付 | 采集先落盘，固定v4正文FIFO上传；删除旧8上传worker、内部三次重试、成功批量落盘确认路径 | 中断矩阵、2xx-before-ack、2MiB、300MiB、八个预留、延迟响应 |
| B4 单一检查点 | 替换混合DataSyncState；删除confirmed/history/today/monthly/needsHuman旧结构；空结果保留 | 重启/跨日/coverage失败/范围变化/空结果测试 |
| B5 自愈与UI | 固定重试、自动候选tab、徽标、query+notification；删pause、日志ERP上传、popupSyncState | 429/登录/重复tab/表单焦点/日志失败不阻断 |
| B6 收口 | 删除旧消息、helper、Storage写口及仅保留旧语义的测试；不恢复已退休域名抓包 | 全量测试、类型/构建、coverage、生产调用接线检查与Chrome演练 |

顺序是原子可验证改动，不是线上两套owner并行。新增仓储在未接入阶段只能独立测试；接管的切片一次替换旧读写，不双写。每个切片更新现有架构文档/ADR对应条目，README区分已发布与待发布。

Phase C1：故障注入、长时间Worker挂起、磁盘压力、真实恢复联调；诊断可靠性优先。
Phase C2：依据测量减少IDB/UI/coverage开销；不擅改已确认8槽/500ms，不用重新启用并行ERP上传破坏FIFO。参数变更必须另行批准。
Phase C3：进度、错误说明、日志检索和无障碍体验；不引入需要点击才恢复的状态。

## 11. 验收矩阵与验证门禁

| 场景 | 断言 |
| --- | --- |
| T0/午夜/DST | 请求日期最大当地T-1；旧任务跨午夜不改其日期；新轮次按店铺日历 |
| 健康并发 | 慢响应下可达到8 HTTP在途、不能超过8；启动间隔>=500ms；优先槽和页面保护仍有效 |
| 正文与容量 | 2MiB边界按UTF-8完整envelope；并行reserve不能超过300MiB；不足不先发TikTok；失败释放预留 |
| FIFO | 乱序完成采集按入队seq交付；队头非2xx时后续不发；不能用coverage跳过 |
| ERP响应丢失 | 再发相同字节/requestId/createdAt；无新idempotencyKey；业务行不重复，raw log可多条 |
| 2xx正文损坏 | 已收2xx仍ack；与现有JSON解析强依赖路径分开测试 |
| 事务故障 | enqueue失败不上传；ack失败保留；计量与队列同步回滚；重启不出现半条记录 |
| 预留失主 | kill Worker后不会永久占容量；旧回调不能提交到新generation；页面旧请求不突破槽位 |
| ERP离线恢复 | 禁止新生产，30秒单飞；恢复先排空，再最新coverage；健康探测不与队头重试重复 |
| 超时/429 | 普通失败30秒，无指数退避；Retry-After秒/date/无效值均覆盖；立即检查不绕过服务器期限 |
| 空结果 | 完整空rows在2xx后记录本地；分页/解析失败不标空；重启保留、重装可重新采集 |
| 登录/缺页 | 不抢焦点；一个候选页；重启create后未保存窗口也不重复开页；恢复不用点按钮 |
| Scope/设置 | 非空Outbox不换目标/身份；迟到旧结果不推进新scope；令牌更新可恢复401 |
| Popup | 首屏无需等待ERP；通知丢失可重查；revision旧结果不覆盖；焦点/草稿/滚动保留 |
| 日志 | 30天/300MiB先到先淘汰；独立失败无影响；无完整业务payload长期副本/无凭据/无ERP上传 |
| 升级 | 不把Worker重启当升级；重置数据损失警告明确，不声称旧Outbox迁移成功 |
| 静态退休 | 无needsHuman/pause/today旧生产分支、无popupSyncState写口、无域名观察链路复活 |

实施时插件独立仓库运行：`npm ci`、`npm test -- <聚焦测试路径>`、`npx tsc --noEmit`、`npm test`、`npm run test:coverage`、`npm run build`。保留语句/函数/行95%、分支90%门槛；测试中业务数据使用合成fixture，不访问真实ERP/TikTok。IDB事务与MV3恢复必须另做Chrome隔离profile故障演练。未来需要改ERP代码时才使用仓库规定的 `bash scripts/test_isolated.sh ...`，不得在插件里运行Python数据库测试。

本轮为文档交付：检查Markdown链接/路径、设计一致性、git diff --check、只包含声明的文档，不运行生产命令、不宣称上述实施验收已经通过。

## 12. 残余风险与评审重点

1. 严格FIFO和不跳过意味着一个持续400/413会阻塞交付；UI可诊断，但不会增加隐藏人工放行。
2. 单有效scope不防其他客户端写同一业务键；v4是最后写入覆盖，不是历史版本仲裁。
3. 现有campaign_opt_logs二次写失败可能仍200；不在客户端伪造更强服务器保证。
4. 空日期重新安装后重采、campaign raw-log清理后coverage变化是现状成本，不增表解决。
5. 满载/磁盘故障期间无法保证保存尚未提交的TikTok响应；只保证已提交Outbox不被自动淘汰。
6. 升级清空不是无损迁移；必须让部署者看到唯一payload丢失风险。
7. ERP单飞与本地落盘可能降低端到端吞吐；必须测量而非用“8路不变”掩盖变化。

## 13. 参考与证据

- [插件README（独立仓库）](https://github.com/brakchen/ads-data-sync/blob/master/README.md)
- [现有每日同步模块设计](https://github.com/brakchen/ads-data-sync/blob/221e4531145e6054ab802d5ddf75595cdbffb8c6/docs/architecture/2026-09-28-%E6%AF%8F%E6%97%A5%E5%B9%BF%E5%91%8A%E5%88%86%E6%9E%90%E5%90%8C%E6%AD%A5%E6%A8%A1%E5%9D%97.md)
- [ERP dumps契约（注意§2的旧章节差异）](../../api/dumps-data-contract.md)
- [Chrome Worker生命周期](https://developer.chrome.com/docs/extensions/develop/concepts/service-workers/lifecycle)
- [Chrome alarms：最小周期与延迟](https://developer.chrome.com/docs/extensions/reference/api/alarms)
- [Chrome存储与unlimitedStorage](https://developer.chrome.com/docs/extensions/develop/concepts/storage-and-cookies)
- [idb事务生命周期与tx.done](https://github.com/jakearchibald/idb#transaction-lifetime)
