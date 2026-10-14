# monitor 按店铺同步 ERP 配置

> **状态：待实施。** 业务规则已获用户确认；本文是实现方案，**尚未实施、迁移或上线**。
> 本仓库内不存在对应的后端路由、迁移、管理页面或插件同步模块。文中所有表结构、
> 接口与文件路径都是**拟新增**内容，不是既有功能。

- 首次成稿：2026-10-11（会话 `01a129a2-4ae0-76f7-a174-dea4d678a420`）
- 入库：2026-10-12，lane `monitor-config-docs`，前驱 lane `monitor-config-contracts`（`3953a6d2`）
- 源草案：`.pi/opus-manager/design.md`（同一会话的工作区副本，未入库）
- 基线：ERP `c491ade8`；`plugins/monitor` `456423b7`（版本 0.4.19）
- 影响的安全边界：为飞书凭据设**限定例外**，见 [§4.3](#43-飞书密钥的明确例外)
- 影响的安全部署前提：需要受信任 HTTPS 入口，见 [§4.4](#44-https-部署前置)

## 0. 本文的定位与关联切片

本文只描述**目标形态**。实施切片按 [§10](#10-实施拆分与归属) 拆成独立 lane。

已落地的部分：

| 切片 | lane / commit | 内容 |
| --- | --- | --- |
| T001 纯契约 | `monitor-config-contracts` / `3953a6d2` | `tts_erp_v2/monitor_config/contracts.py` + `tests/unit/test_monitor_config_contracts.py`。DB-free / plugin-free / network-free 的 wire 校验与合成层，`WIRE_SCHEMA_VERSION = 1`。本文 [§3.1](#31-可下发业务配置schemaversion1) 与 [§6.1](#61-插件读取) 的字段命名以该契约为准。 |

因此本文的小节编号必须与 T001 契约模块 docstring 中的 `design.md §3.1` / `design.md §6.1`
引用保持一致；§3.1 仍是“可下发业务配置”，§6.1 仍是“插件读取接口”。
把契约模块 docstring 里的 `design.md` 改写成本文路径属于 `monitor-config-contracts` lane
的归属，本 lane 未修改 `tts_erp_v2/monitor_config/**`。

仓库约定：见根 [`AGENTS.md`](../AGENTS.md)、
[`docs/README.md`](README.md) 的目录/状态行约定、
[`docs/guides/agent-safety.md`](guides/agent-safety.md)、
[`docs/guides/agent-git-workflow.md`](guides/agent-git-workflow.md)、
[`docs/api/external-api.md`](api/external-api.md)、
[`docs/ops/runtime-config-management.md`](ops/runtime-config-management.md)、
[`docs/architecture/architecture-overview.md`](architecture/architecture-overview.md)、
[`docs/handoff/ACTIVE.md`](handoff/ACTIVE.md)，
以及插件侧 `plugins/monitor/AGENTS.md`（子模块内，不随本仓检出）。

## 1. 目标、已确认规则与非目标

### 1.1 已确认的业务规则

| 编号 | 规则 |
| --- | --- |
| R1 | 配置按 `sellerId` 唯一定位。`advertiserId` 不参与配置请求或配置唯一键。 |
| R2 | 五店使用五个独立浏览器环境，每个实例只监听自己的店铺。真正隔离要求独立 Profile/用户数据目录，不仅是不同进程号。 |
| R3 | 每 30 秒检查配置；插件启动、绑定或切换店铺、保存 Token 后立即尝试。 |
| R4 | 采用 HTTP `ETag` / `If-None-Match`：未变 304，变化 200。 |
| R5 | 保留现有人工配置 UI。人工修改立即生效，普通远端发布永远不能覆盖人工修改。 |
| R6 | 后台允许按店铺执行一次性“强制覆盖”。成功应用后解除该店本地人工保护；之后再次人工修改仍受保护。 |
| R7 | 首次拉取失败可以人工填写；任何后续失败都只记日志、保留当前有效配置，等待下一轮重试。没有五分钟熔断，没有因 ERP 不可达而停止插件功能。 |
| R8 | 飞书仍由插件直接发送。飞书应用密钥可经受授权接口下发，不改为 ERP 代发。 |
| R9 | 每个浏览器环境首次填写一次店铺专用 Token；不内置共享 Token，不要求日常反复填写。 |
| R10 | 页面展示 sellerId、实际生效来源、版本/hash、本地修改及同步状态。远端已下载不等于已应用。 |
| R11 | 已存在的人工配置在升级接入时受保护，不能因首次成功拉取被静默覆盖。 |
| R12 | 支持全局默认与店铺覆盖，由 ERP 合成为完整配置；单广告规则保留。 |

### 1.2 不做什么

不增加 WebSocket、ERP 消息中转、远程执行 TikTok 关停、同一实例并行监听五店、远程启停整个插件、默认自动上传浏览器现有配置、自动清空本地配置或自动数据库迁移。配置同步不是插件运行的租约或心跳许可。

`advertiserId` 仅退出配置定位，不删除原有广告执行上下文和 `sellerId + advertiserId` Cookie 隔离。

## 2. 现状与复用依据

本方案基于以下源文件实际核对，不把建议接口描述为现成功能：

| 现有能力 | 位置 | 使用方式/不足 |
| --- | --- | --- |
| 草稿、不可变 revision、发布、回滚 | `tts_erp_v2/api/v2/config.py`、`tts_erp_v2/runtime_config/repository.py`、`config.runtime_config_items` / `config.runtime_config_revisions` | 复用配置真相与管理表单；回滚仍发布更高版本。 |
| 通用 snapshot 和 ETag | `GET /v2/config/runtime/snapshot` | 返回所有未归档已发布配置，`subject` 用于灰度分桶，不是店铺授权；只返回 `secret://` 引用，且返回 `Cache-Control: private, max-age=60`。不能直接给插件使用。 |
| Secret 加密存储 | `config.runtime_config_secrets`、`tts_erp_v2/runtime_config/repository.py` | 复用 `token_service.encrypt/decrypt`；普通管理 API、通用 snapshot 保持不回读明文。 |
| API Key 与角色 | `tts_erp_v2/db/models/security.py`（`security.api_keys`）、`tts_erp_v2/access/` | 已有 SHA-256 存储、`role`（`readonly < readwrite < admin`）与禁用/轮换字段，但当前 key 没有用途或店铺绑定；`Credential.scopes` 的存在不代表已实现店铺限制。 |
| 店铺主键 | `commerce.shops`（模型 `ChannelAccount`） | `(platform, shop_id)` 唯一；内部仍使用 `shop_pk`，不把外部 seller 字符串当内部主键。 |
| seller 身份捕获 | `plugins/monitor/src/core/tab-binding.ts` | 读取 `oec_seller_id`/`seller_id`；按现有页面捕获链路获取，不要求人工填写。 |
| 配置写入串行化 | `plugins/monitor/entrypoints/background.ts`、`plugins/monitor/src/extension/storage.ts` | 复用高层 settings operation queue 和低层 configuration write queue。 |
| 现有导入 | `replaceConfiguration`、`plugins/monitor/src/core/config-backup.ts` | 导入会停止监控、清绑定；远程更新绝不能直接复用该入口。旧备份继续可读。 |
| 配置字段与 UI | `plugins/monitor/src/extension/messages.ts`、`plugins/monitor/entrypoints/popup/main.tsx` | 保留现有表单和自动保存。monitor 的“店铺授权与关闭测试”已显示 seller/advertiser。 |
| MV3 调度和发布 | `plugins/monitor/wxt.config.ts`、`plugins/monitor/AGENTS.md` | 使用 `chrome.alarms`；当前 `host_permissions` 只有 `seller.tiktokshopglobalselling.com`、`open.feishu.cn`、`open.larksuite.com`，**未包含 ERP 源**，需补充精确来源授权。 |

需遵循的项目基准：`AGENTS.md`、`docs/architecture/architecture-overview.md`、`docs/guides/agent-safety.md`、`docs/guides/agent-git-workflow.md`、`docs/api/external-api.md`、`docs/ops/runtime-config-management.md`、`plugins/monitor/docs/reviews/2026-10-11-close-safety.md`。

### 2.1 方案选择

1. **直接调用通用 runtime snapshot：不采用。** 不能提供店铺专用权限、浏览器用密钥、一次性覆盖确认；也会多发无关配置。
2. **复用 runtime 配置真相，增加 monitor 专用交付投影与薄接口：采用。** 保留现有发布/加密能力，只补店铺授权、合成快照和强制覆盖语义。
3. **另建完整配置中心、鉴权服务或长连接系统：不采用。** 五店规模不需要第二套配置管理和调度设施。

新增交付快照不是第二套可编辑配置：草稿和源 revision 仍只有 runtime config 一个 owner；交付快照只是不可变、可追踪的店铺投影，解决全局+店铺合成、密钥冻结和离线强制覆盖的确定性问题。

## 3. 配置边界与优先级

### 3.1 可下发业务配置（`schemaVersion=1`）

- 规则/保护：`globalThreshold`、`autoCloseSkuOrderLimit`、`autoCloseMaxSkuOrders`、`autoCloseSingleConversionCostThreshold`、`autoCloseCampaignWhitelist`、`autoCloseEnabled`。
- 时间窗口/调度：`closeQueryRecentDays`、`reportQueryRecentDays`、`scanIntervalMinSeconds`、`scanIntervalMaxSeconds`、`notificationIntervalMinSeconds`、`notificationIntervalMaxSeconds`。
- 通知：`notifyAutoCloseSuccessEnabled`、`notifyAutoCloseFailureRepeatEnabled`、`feishuWebhookUrl`、`feishuSigningSecret`、`feishuAppId`、`feishuAppSecret`、`feishuRecipientIdentifiers`。
- 单广告配置：`campaignThresholds`，沿用现有 `CampaignRuleOverrides`；服务端新输出统一为对象形式，旧备份的数字简写在本地迁移时归一化。

以上字段名与 T001 契约的 `_ALLOWED_SETTINGS_FIELDS` 一一对应。T001 另外把 `monitorEnabled`、
运行状态、`nextScanAt` / `nextNotificationAt`、`shopName`、`queryRecentDays`、`advertiserId`、
`token`、`cookie`、`serviceUrl`、`requestTemplate`、`campaigns`、`campaignsComplete`、
`pendingAutoCloseFailures`、`shopCredentials`、`boundAdTab`、`logs` 列入
`FORBIDDEN_WIRE_FIELDS`，服务端发布层必须拒绝。

不得下发/覆盖：`monitorEnabled`、运行状态、下次执行时间、页面绑定、Cookie、request template、广告列表、失败重试状态、Token、服务地址、同步水位。废弃字段 `queryRecentDays` 只用于旧配置迁移，不进入新 wire schema。

“强制覆盖全部配置”仅指上述业务配置，不包括紧急暂停、登录授权和运行时状态。

### 3.2 服务端合成规则

- 全局键：`monitor.defaults`，提供完整业务默认值；店铺键：`monitor.shop.<shop_pk>`，仅保存该店覆盖字段。
- 合成由 Python 后端统一负责（实现见 `merge_business_settings()`），不在每个浏览器重复实现继承算法。
- settings 按字段覆盖；字段缺失代表继承；明确的 `null` 仅允许出现在可空禁用字段（当前为 `autoCloseMaxSkuOrders`、`autoCloseSingleConversionCostThreshold`），并代表禁用该项阈值；数组整体替换，空数组表示清空。
- `campaignThresholds` 只属于店铺，不允许在全局默认里引用广告 ID；按现有单广告规则语义计算。
- 完整下发必须字段齐全、类型正确、数值有限、范围合法，且时间区间满足 `min <= max`。空字符串和 `null` 的含义固定，不使用 truthy 回退。
- “人工保护”以整份业务配置为单位，不做隐式逐字段混合。人工编辑一个字段后，其他远端业务字段也不自动混入，直到新的强制覆盖成功。

## 4. 身份、Token 与敏感数据

### 4.1 唯一身份

请求身份字段仅 `sellerId`，始终是字符串；固定平台为 TikTok。后端借助现有店铺查询逻辑定位 `(platform='tiktok', shop_id=sellerId)` → `shop_pk`，并核验捕获的 seller 语义与已登记 `shop_id` 一致。已存在同步代码使用 `seller_id` 对应 `shop_id`，但上线仍需以实际目标店铺做受控验证，不能以名称/IP/数组第一项兜底。

未知 seller、缺失登记或身份冲突返回明确错误；不得仅凭一个任意 `sellerId` 返回全局配置。插件保持该店已有本地配置运行，并提示关联问题。

### 4.2 店铺专用 Token

复用 `security.api_keys` 的随机 Token 生成、哈希存储、禁用/轮换机制；不新增第二套认证中间件。

建议增加 `purpose` 与 `shop_pk`：既有 key 默认 `general`、`shop_pk` 为空；新 monitor key 必须 `purpose=monitor_config` 且绑定一个有效 `shop_pk`。新 Token 只展示一次，列表只显示非敏感标识和状态。

关键约束：

1. 在 `tts_erp_v2/access/` 的统一认证/授权 owner 内把 purpose 和 `shop_pk` 带入 typed credential/grant。
2. monitor key 只允许调用 monitor 配置 GET，不能因为 `role=readonly` 而读取 ERP 其他只读数据、其他 secrets 或管理接口。
3. 店铺比对和当前 key 状态检查必须发生在条件请求判断之前；不能让正确 ETag 绕过 401/403。
4. 专用下发接口要求店铺 Token，不能把普通 readonly/admin key 或浏览器登录 cookie 自动当成店铺绑定授权。管理能力走独立管理接口。释放明文的专用边界不能因开发环境 `auth=off`/shadow 获得匿名 bypass；测试使用合成店铺凭证，不放宽该边界。
5. 禁用/轮换后的下发校验不得仅依赖现有最多 60 秒的正缓存：在专用边界重新核对活动状态或落实等价的实时失效机制；缓存不可制造跨店或已吊销许可。
6. 插件用 `credentials: omit`，避免 ERP 登录 cookie 优先于 token（当前 `CORSMiddleware` 是 `allow_credentials=True`）；Token 永远不放 URL，不允许跨 origin 重定向携带。
7. 收到 401/403 也只记录同步失败并保留本地运行。吊销 Token 只撤销未来配置读取，不是远程停机，也不能收回已下发的飞书密钥。

Token 只存在本地独立连接设置中，不能混入可下发业务配置或旧配置备份，不能被强制覆盖替换。

### 4.3 飞书密钥的明确例外

用户明确授权本特性把飞书 webhook/签名密钥/App Secret 下发到浏览器；这是对 `docs/guides/agent-safety.md` 和 `docs/ops/runtime-config-management.md` 中“不向客户端回读 secret”规则的**限定例外**，不是放开通用 secret API。

- 仅 monitor 专用接口、有效店铺 Token、匹配店铺、HTTPS 下允许解密下发白名单飞书字段。
- TikTok OAuth/access/refresh token、Cookie、数据库密钥和 Fernet 主密钥绝不下发。
- 管理页面继续写 secret 引用，不回显明文；敏感内容不进入普通 revision JSON、日志或审计详情。
- 日志仅记 seller、版本、错误代码、`requestId` 等非敏感字段；不得打印响应正文、`Authorization`、含 token 的 webhook URL 或任意异常对象的完整请求。
- 浏览器的 `chrome.storage.local` 不是加密保险箱，持有 Profile 的用户能读取。用户已接受浏览器保存通知凭证的边界；应用 Secret 泄露应重置 Secret，删群机器人不必然撤销应用权限。

### 4.4 HTTPS 部署前置

现行架构文档记录的公网入口是 **HTTP** `http://daqiang.nat100.top/tts/...`
（见 `docs/architecture/architecture-overview.md`），**尚未验证该部署具备可信 HTTPS**。
不能把方案中的 HTTPS 写成已经存在。

上线前必须提供受信任 HTTPS 入口并保留 `/tts` 前缀，核验代理与证书。不得携带真实 Token/飞书密钥探测 HTTP、忽略证书错误或自动降级 HTTP；TLS 未满足时只是不启用远程同步，既有本地功能照常运行。生产来源限定到该获准 origin；本地模拟测试不使用真实凭证。

## 5. 持久化与发布事务

### 5.1 保留既有真相

`config.runtime_config_items` / `config.runtime_config_revisions` / `config.runtime_config_secrets` 继续负责草稿、发布、回滚和 secret 加密。monitor 命名空间使用固定受控 schema，不采用自动推断的宽松 schema。

管理界面复用现有运行配置能力，增加 monitor 的全局/店铺编辑视图。monitor 发布必须经过专用编排；通用 mutation 对保留键拒绝绕过（提示转到 monitor 管理入口），或者内部调用同一编排，二者必须选定一种且测试所有入口。本方案采用**通用 mutation 拒绝保留键、专用编排为唯一发布出口**，通用读取仍不解密。

### 5.2 新增店铺交付投影

建议模型放入独立 `tts_erp_v2/db/models/monitor_config.py`，不在现有大模型文件堆字段；迁移编号在实施时基于实际 head 分配。

| 表/变更 | 最小职责 |
| --- | --- |
| `security.api_keys` 增加 `purpose`/`shop_pk` | 在既有 credential 上表达受限用途和店铺归属。 |
| `config.monitor_shop_state` | `shop_pk` 为主键；当前 delivery version、最新 force version（无则 null）；作为串行分配版本和发布的行锁锚点。 |
| `config.monitor_config_deliveries` | 唯一 `(shop_pk, version)`；源 global/shop revision、`schema_version`、`content_hash`、`encrypted_payload`、`is_force`、`created_at`/`created_by`、操作幂等 `request_id` 与 `request_digest`（只覆盖非敏感请求元数据）。另设 `(shop_pk, request_id)` 唯一约束。记录一份不可变的完整店铺配置。 |

一个版本号同时标识交付快照；强制覆盖指令编号直接复用对应 `is_force=true` 的 delivery version，不另外维护一套无关计数器。wire 上版本/主键使用十进制字符串，避免 JS Number 大整数失真。业务阈值延续现有 schema，不借本次重做财务计算。

首次纳管店铺时在事务内建立 state；有已发布全局默认则生成初始交付，没有则保留未配置状态、GET 返回 404。无已发布店铺覆盖时 `sourceVersions.shop=null`，只继承全局；不能因缺少空覆盖草稿而杜撰一个 revision。强制覆盖要求该店已有发布交付。

### 5.3 普通发布

1. 校验操作者、草稿 optimistic version 和配置 schema。
2. 全局发布时锁定全局配置行，再按 `shop_pk` 升序锁定已纳管店铺的 state/店铺配置行；单店发布使用同一锁顺序。避免全局与单店发布混合出不可能的版本组合。
3. 复用 runtime repository 发布源 revision，合成受影响店铺的完整 payload，并通过 `token_service` 解出获准飞书字段。同一次发布把所需 secret 一次批量读取并按名称统一锁定/快照，防止五店生成期间密钥变更造成同一次全局发布包含互相矛盾的内容。
4. 将完整快照通过 `token_service` 加密保存为新的 delivery；写 content hash、源 revision、actor；更新 `current_delivery_version`，不改变 `last_force_version`。
5. 同一事务提交。失败则全部回滚，不能对外返回 2xx 却只更新一半指针。

交付冻结在发布时；secret 值后来在通用管理中更新，不会静默改变已有 delivery。密钥更新后必须重新发布 monitor 配置，管理 UI 明确提示“已保存密钥，尚未发布到插件”。加密明文只短暂存在受控内存，不写入源 revision。

### 5.4 强制覆盖

- 按店操作，并展示目标 seller、当前已发布内容/版本和覆盖警告。未发布草稿不能被“强制覆盖”隐式发布。
- POST 携带 `expectedDeliveryVersion` 与客户端生成的 `requestId`，防止界面过期和双击重试。
- 在同一锁/事务中复制所选已发布快照为新的 delivery（即使内容完全相同），设置 `is_force`，并同时推进 current 和 last_force 指针。
- 相同 `requestId` + 相同请求返回同一结果；相同 `requestId` 不同请求为 409。幂等检查先于 expected-version 冲突判断，保证丢失响应后重试可恢复。
- 普通后续发布不删除尚可能被离线插件看到的强制指令。保留 latest force 指针及其不可变目标快照。
- 不要求离线实例逐个执行所有历史强制版本：多个未处理指令合并为最新一次，且必须应用该指令固定的快照，不悄悄换成后来的普通发布。
- 初版不增加执行回执接口；后台只能显示“指令已发布”，不能冒称“所有浏览器已应用”。插件本地显示实际应用结果。

所有时间是 aware UTC（`datetime.now(UTC)`）；历史 revision/delivery 不删除。数据库升级仅通过隔离测试验证，生产升级和重启由人类执行。

## 6. 接口契约（均为拟新增）

以下为 route-relative 路径；公网使用获准 HTTPS origin + `/tts` 前缀，禁止拼无前缀的公网 `/v2`。

### 6.1 插件读取

```http
GET /v2/monitor/config?sellerId=SELLER_A
Authorization: Bearer <SHOP_SCOPED_TOKEN>
If-None-Match: W/"monitor-example-validator"
```

第一次没有已验证缓存时不带条件头。唯一身份参数是 `sellerId`，不带 `advertiserId`、IP、进程号或店铺名称。

响应结构（`payload` 下为完整 schema，此处按 T001 契约做字段省略示意）：

```json
{
  "schemaVersion": 1,
  "sellerId": "SELLER_A",
  "shopPk": "101",
  "current": {
    "version": "18",
    "sourceVersions": {"global": "4", "shop": "7"},
    "contentHash": "sha256:<64_HEX_CHARS>",
    "payload": {
      "sellerId": "SELLER_A",
      "schemaVersion": 1,
      "settings": {"globalThreshold": "10.0", "campaignThresholds": {}}
    }
  },
  "force": null
}
```

> 示意与契约的两点差异（以 T001 契约为准）：`campaignThresholds` 位于 `payload.settings` 内部，
> 不在 `payload` 顶层；`Decimal` 序列化为字符串以保证精度稳定。`current` 与 `force.target`
> 的块结构相同，键集合固定为 `{version, sourceVersions, contentHash, payload}`。

存在最新强制指令时：`force` 为 `{ "version": "17", "target": <与 current 相同结构的完整快照> }`。`version` 必须等于 `target.version`。若 `current` 就是该强制版本，仍使用相同结构；五店规模接受这一小份重复，避免引入复杂 delta 或额外读取竞态。

保留旧 `force.target` 是为了支持离线实例；已处理该指令的实例忽略它。不得把响应中的 `force` 当作永久开关。

### 6.2 ETag、缓存与错误

- ETag 表示完整可见响应的语义版本，覆盖 seller/shop、schema、current version/hash 和 force version/hash；相同配置再强制一次也必须改变 ETag。
- 采用弱 ETag `W/"..."` 表达语义等价，底层 SHA-256 计算稳定序列化的非敏感版本材料。`contentHash` 针对冻结 payload 生成，和 ETag 是不同概念。
- 不把 `requestId`、请求时间、随机数或本地人工修改状态加入 ETag。
- 200 和 304 均返回 ETag、`Cache-Control: private, no-cache`、`Vary: Authorization`；禁止 CDN/代理共享缓存明文响应。浏览器 HTTP 缓存使用 `cache: no-store`，只在扩展自己的受控存储保存已验证结果。
- RFC 9110 条件比较支持多标签、弱标签、`*`，而不是直接做单个 header 字符串相等（现有 snapshot 端点用的是后者，不能照抄）。已有框架能力优先复用；如需小型解析器，必须用协议用例覆盖，不引入新的配置协议。
- 鉴权、店铺授权、当前配置存在性检查先于 304 判断。304 没有正文，不意味着重置本地人工标记。
- 请求使用 10 秒 AbortController 超时，禁止跨 origin 重定向。完整 schema/身份校验失败不得覆盖本地、保存新 ETag 或推进 force 水位。
- 合法 200 后即使因人工保护没有应用，也可以原子保存远端 envelope 与 ETag。304 后可基于已验证缓存继续完成尚未落盘的应用事务。
- 缺少对应已验证缓存时收到 304：清除孤立 validator，下个定时周期无条件拉取，不用空 payload 冒充配置。

错误：400/422 参数或协议不合法，401 Token 无效，403 店铺不匹配/用途不符，404 店铺或已发布配置不存在，409 管理端版本冲突，429 限流，5xx 服务错误。所有下发失败只影响同步状态，不停已有本地监控、关停和飞书通知；下一次 alarm 重试，不因失败引入无限快速循环。

### 6.3 管理接口与 UI

| 接口 | 最低权限/职责 |
| --- | --- |
| `GET /v2/monitor/admin/shops` | `readwrite`；列出纳管店铺、发布版本、最近强制编号；不返回明文密钥。 |
| `GET/PUT /v2/monitor/admin/defaults/draft` | `readwrite`；复用 runtime 草稿版本校验与 secret 引用。 |
| `POST /v2/monitor/admin/defaults/publish` | `readwrite`；发布全局默认并生成受影响店铺交付。 |
| `GET/PUT /v2/monitor/admin/shops/{shop_pk}/draft` | `readwrite`；店铺覆盖草稿。 |
| `POST /v2/monitor/admin/shops/{shop_pk}/publish` | `readwrite`；普通发布，不突破本地人工保护。 |
| `POST /v2/monitor/admin/shops/{shop_pk}/force` | `readwrite`；对已发布版本发一次强制覆盖，需版本确认及幂等 `requestId`。 |
| Token 创建/禁用/轮换的管理路径 | `admin`；复用既有 key 管理 owner，强制绑定用途/店铺；具体路由随既有管理入口实现，不另建 token 服务。 |

所有新路径进入统一 role matrix（`tts_erp_v2/middleware/auth.py::required_role()` 与 `docs/api/external-api.md`）；管理接口不能被 monitor key 调用。Cookie 写请求沿用 CSRF header 和 JSON 限制。复用现有运行配置页面权限，不新增独立认证入口。

#### 6.3.1 阶段 C 实施记录（lane `monitor-config-erp`）

已实施（`tts_erp_v2/monitor_config/{keys,admin}.py`、`tts_erp_v2/api/v2/monitor_config.py`）：

| 路径 | 说明 |
| --- | --- |
| `GET /v2/monitor/admin/shops` | 纳管店铺列表（已有交付指针 **或** 已有店铺草稿/revision），含发布版本、最近强制编号、contentHash、源版本、店铺草稿版本。不返回任何明文密钥。 |
| `GET/PUT /v2/monitor/admin/defaults/draft` | `monitor.defaults` 草稿。GET 对尚不存在的键返回**零状态**（`draftVersion: 0`），PUT 以 `expectedDraftVersion: 0` 完成首次创建，因此不需要单独的建键入口。 |
| `POST /v2/monitor/admin/defaults/publish` | 发布全局默认，并在一个事务内为**已纳管**店铺重新冻结交付。 |
| `GET/PUT /v2/monitor/admin/shops/{shop_pk}/draft` | `monitor.shop.<shop_pk>` 覆盖草稿；未知 `shop_pk` 返回 404。 |
| `POST /v2/monitor/admin/shops/{shop_pk}/publish` | 无覆盖草稿的店铺也在这里首次纳管：`sourceVersions.shop = null`、只继承全局，**不**杜撰空 revision。尚无已发布全局默认时返回 409。 |
| `POST /v2/monitor/admin/shops/{shop_pk}/force` | 需要 `expectedDeliveryVersion`（十进制字符串）与客户端生成的 `requestId`。幂等检查先于版本冲突；同 `requestId` 不同请求为 409；无已发布交付为 409；缺 `requestId` 为 422。 |

实现中固定下来的几个决定：

- **保留命名空间**：通用 `/v2/config/runtime/items/*` 的 create/draft/publish/rollback/retire/restore 六个入口对 `monitor.*` 一律返回 409 并指向 `/v2/monitor/admin`（§5.1 选的“通用 mutation 拒绝保留键”）。通用读取仍不解密。
- **固定受控 schema**：`monitor.*` 的 `json_schema` 由 `admin.defaults_settings_schema()` / `admin.override_settings_schema()` 生成并封闭对象（`additionalProperties: false`），不使用 `infer_schema`。字段类型/范围仍由契约层判定，它比受支持的 JSON Schema 子集更严格。
- **释放白名单**：只有 `feishuWebhookUrl` / `feishuSigningSecret` / `feishuAppId` / `feishuAppSecret` 允许携带 `secret://` 引用。其他字段（嵌套也算）出现引用一律 422。一次发布把所需 secret 按名称升序加行锁后**只读一次**，五店共用同一份快照，避免同一次全局发布混入两代密钥。解冻后重新跑一遍契约校验。
- **白名单飞书字段不得以明文入库**：这四个字段只接受 `secret://<name>` 引用或 `""`（§3.2 固定的“该通知渠道未配置”空串语义）。任何其他字面量在**入库前**就 422，且不会进入 `draft_payload`、revision 或 delivery。校验点有三个：草稿保存（`validate_draft_payload`）、发布入口（`publish_defaults` / `publish_shop` 在写 revision 前复验）、合成入口（`_compose`，防止旧构建或直接写库的字面量被冻结进交付）。理由：§4.3 的释放例外是“服务端已持有密文”的**下发**边界，不是允许把密钥敲进草稿字段的许可；明文一旦入库，`GET` draft 会原样回显，`/v2/config/runtime/snapshot` 也会把它重发给任意 `readwrite` 调用方。
- **首次草稿原子创建**：键不存在时用 `INSERT ... ON CONFLICT (config_key) DO NOTHING ... RETURNING` 建行。`SELECT ... FOR UPDATE` 不锁不存在的行，两个并发的 `expectedDraftVersion=0` 会同时未命中并同时 INSERT；裸 IntegrityError 会变成 500，而正确结果是败者得到文档化的 409 `DraftVersionConflict`（`RETURNING` 无行即表示被跳过）。这也意味着 `PUT` 首次建键永远不会把“存在了但版本不是 0”报成 500。
- **存量明文载荷的读取面（fail closed / 脱敏）**：写入口拒绝字面量，但旧构建或直接写库可能已留下明文草稿、已发布 revision 与由此冻结的交付。规则与写入口**共用同一条判定**（`_holds_literal_secret`）：
  - 面向管理员的读取（`/v2/monitor/admin/*/draft`）**脱敏**为哨兵 `__quarantined_plaintext_secret__`，并用 `quarantinedFields`（仅字段名）告知待修项；运维修复仍需看到并编辑其余部分。哨兵既不是合法 `secret://` 引用也不是空串，原样回写会被写入口拒绝，避免隔离自我延续。
  - 通用读取（`/v2/config/runtime/items/{key}`、`/revisions`、`/snapshot`）对 `monitor.*` 同样脱敏；非 monitor 键语义不变。
  - 下发 GET **fail closed（503）**：交付快照本身可以合法地携带明文（§4.3），但那必须来自 `config.runtime_config_secrets`。交付行无法自证来源（两种情况下明文长得一样），因此回查它冻结时的源 revision；源 revision 含明文则 503 且不返回正文。**必须同时查 `current` 与保留的 `force.target`**：二者是两条独立的交付行、有各自的 provenance，而 §5.4 刻意保留较旧的强制指令供离线浏览器应用——只查 current 会让存量 force 目标照样下发。该检查**位于条件请求之前**，且不签发 `ETag`，避免被降级成 304 让插件继续沿用。503 对插件等同其他 5xx（§7.2：只记日志、保留本地配置），不会中断监控。
- **锁顺序**：全局行 → 按 `shop_pk` 升序的店铺配置行 → 按 `shop_pk` 升序的店铺 state 行；全局发布与单店发布使用同一顺序。
- **回滚**：任一店铺合成失败则整次全局发布回滚，不留下半份 revision 或半推进的指针。

本阶段**未**实施（仍在阶段 C 剩余范围或后续阶段）：Token 创建/禁用/轮换路由（§6.3 末行，仍复用既有 key 管理 owner）、`runtime-configs` 页面视图与合成预览（§8.2）、CORS 的 monitor origin / `If-None-Match` / `ETag` 暴露（§6.3 末段，属部署前置）。

#### 6.3.2 存量明文密钥的排查与修复（运维手册）

本特性的写入口已拒绝明文，但**上一轮构建或直接写库**可能已经留下明文飞书字段。以下流程是**非破坏性且保留历史**的：它不执行任何生产 `DELETE` / `TRUNCATE` / 不可逆 `UPDATE` / 迁移，也不删除任何已发布 revision。

但请注意：**本手册并非只读**。只有第 1 步是只读排查；第 3、4 步会**真实写入**（`PUT /v2/config/runtime/secrets/{name}` 写入加密 secret、`PUT .../draft` 改写草稿、`POST .../publish` 发布新 revision）。请把它理解为“只使用受支持的写入口、且不破坏既有历史”，而不是“无需写入”。涉及密钥轮换的第 2 步则是厂商侧动作，不在本仓数据面内。

**第 1 步 · 排查（只读）**

逐项查看，关注 `quarantinedFields` 是否为数组且非空：

```
GET /v2/monitor/admin/defaults/draft
GET /v2/monitor/admin/shops/{shop_pk}/draft          # 每纳管店铺一次
GET /v2/config/runtime/items/monitor.shop.{shop_pk}/revisions
```

判定：字段值等于 `__quarantined_plaintext_secret__` 即为存量明文；`quarantinedFields` 直接给出待修字段名。同一问题也会让该店的 `GET /v2/monitor/config` 返回 **503**，这是提示而非故障——插件会按 §7.2 保留本地配置继续运行。

**第 2 步 · 先轮换（推荐）**

明文可能已出现在旧备份、历史日志或页面响应中。**先到飞书开放平台轮换**对应的 webhook / 签名密钥 / App Secret，再用新值继续。轮换是厂商侧动作，不涉及本仓生产数据。

**第 3 步 · 存入 secret 表（加密入库）**

```
PUT /v2/config/runtime/secrets/{name}     body: {"value": "<新明文>"}
```

`name` 需小写字母/数字/点/横线/下划线。写入即用 `token_service.encrypt()` 加密，响应只回 `fingerprint`，不回显明文。

**第 4 步 · 改写为引用并重新发布**

```
GET  /v2/monitor/admin/defaults/draft       → 记下 draftVersion
PUT  /v2/monitor/admin/defaults/draft       → expectedDraftVersion + payload（明文字段改为 "secret://{name}" 或 ""）
POST /v2/monitor/admin/defaults/publish     → expectedDraftVersion + comment
```

店铺覆盖同理走 `.../shops/{shop_pk}/draft` 与 `.../publish`。发布后新建的 revision 与交付快照不再含明文，`quarantinedFields` 变空，503 消失。

**残留风险（如实告知）**：已发布的历史 revision 是不可变的，**仍保留原明文**。本特性不删除它们（§5.4/§5.3 的历史不可变原则），而是让所有读取面一律脱敏。若需要彻底清除历史明文，需另开一个显式的数据清理变更并经人工授权，不属于本手册。

`docs/api/external-api.md` 的 “Monitor remote configuration” 小节原先只写了阶段 B 的下发读取，并有一句已过时的 `Status: Phase B read path only. Publish/admin endpoints (§6.3) are not yet exposed.`。经用户明确授权，该小节**已在本轮更新**：

- 标题与引言扩为 `` `/v2/monitor/config` `` 与 `` `/v2/monitor/admin/*` `` 两部分，并删去上述过时的 Status 声明。
- endpoint 清单表补齐阶段 C 的 6 个管理接口（`GET /admin/shops`、`GET|PUT /admin/defaults/draft`、`POST /admin/defaults/publish`、`GET|PUT /admin/shops/{shop_pk}/draft`、`POST /admin/shops/{shop_pk}/publish`、`POST /admin/shops/{shop_pk}/force`），角色标注为 `readwrite`，并新增“Management path (operator)”小节说明：monitor 专用 token 不得调用管理接口（403）、`expectedDraftVersion`/`requestId` 的乐观并发与幂等语义、四个飞书字段只接受 `secret://` 引用或空串（字面量 422）、读取面脱敏为 `__quarantined_plaintext_secret__`。
- 下发读取新增 **fail closed（503）** 语义，并写明检查覆盖 `current` **与** 保留的 `force.target`、位于 `If-None-Match` 之前且不签发 `ETag`。
- CORS 仍未配置的描述**保留不动**，仍属部署前置。

该文件原属 `video-publish-design` lane（`docs/{api,architecture,design,ops,schema}`），本轮由用户显式授权后修改；清单内容已逐项与 `build_app().openapi()` 的实际挂载路径做集合比对（9/9 完全一致），并与 `required_role()` 实际返回值校对（admin 均为 `readwrite`，下发为 `readonly`）。

必须核对 app 的 CORS 配置。当前 `tts_erp_v2/app.py` 的 `DEFAULT_CORS_ALLOW_HEADERS` 是
`Authorization` / `X-API-Key` / `X-Request-Id` / `Content-Type`，**不含 `If-None-Match`**，
且未设置 `expose_headers`，因此 ETag 默认对扩展不可见；`DEFAULT_CORS_ORIGINS` 只列了
ads-data-sync 的签名扩展 ID，不是 monitor。实施时需要加入 monitor 实际扩展 origin、允许
`If-None-Match`、暴露 `ETag`。不使用生产 wildcard，不修改中间件注册顺序
（`RateLimit → Auth → CORS → AccessLog`）。插件 `host_permissions` 只加入获准 ERP HTTPS origin。

## 7. 插件状态与并发模型

### 7.1 分离三个概念

- **当前生效业务配置**：执行规则与通知实际读取的数据，唯一可执行真相。
- **最新远端 envelope/ETag**：已下载并校验的数据，可以比当前生效配置更新，但不能凭缓存存在就自动覆盖人工配置。
- **同步元数据**：`localOverride`、`localRevision`、`appliedRemoteVersion`/`hash`、`lastHandledForceVersion`、`lastCheckedAt`、`lastSuccessAt`、`lastErrorCode`。

状态按 `serviceAuthority + sellerId` 分区。`serviceAuthority` 是获准 origin+部署前缀；改 Token 不应把同一服务/店铺的 force 水位归零。切换服务属于新的同步域，不能复用旧 ETag。

连接 Token 独立存储；广告 Cookie 仍按 seller+advertiser 隔离。运行状态、广告快照和失败队列不是远端配置的一部分。

五个独立 Profile 天然隔离磁盘，但仍需正确处理同一个实例先后切店：业务配置/单广告规则属于各自 seller，不把 A 的全局 settings 无条件沿用给 B。建议由 storage 的按 seller 容器持有业务配置，`getState()` 投影当前店铺，保留消费方接口；不能长期同时维护两个独立可写的全局/分店真相。

### 7.2 决策表

| 条件 | 行为 |
| --- | --- |
| 无 Token/未识别 seller | 不发请求；等待填写/绑定。已有本地功能按原有条件运行，不因 ERP 状态改变 `monitorEnabled`。 |
| 拉取失败/无效响应 | 只更新非敏感同步日志/错误；保留业务配置、人工保护、旧有效 envelope、水位。 |
| 304 且有有效缓存 | 更新检查时间；不覆盖人工配置；允许重试缓存内尚未提交的应用。 |
| 普通 200，`localOverride=false` | 应用最新完整 `current` 配置。 |
| 普通 200，`localOverride=true` | 只更新远端 envelope 和展示信息，不改任何业务字段。 |
| 有未处理的新 force | 应用固定 `target`；业务配置、解除人工保护、applied version/hash、force 水位一起提交。 |
| 同一 force 再次返回 | 不重复应用；之后新的人工修改不会被抹掉。 |
| 远端 current 水位倒退/相同编号内容矛盾 | 记录协议异常，不降级，不清水位；保留本地运行。 |

current 水位指最近已验证的服务端 `current.version`，不等于 `appliedRemoteVersion`。显式新 force 应用固定旧 target 是有意的操作，不误判为服务器回滚。

当 `force.target` 比 `current` 旧：本轮只执行 `force.target`，不在同一事务里又用 current 覆盖。下一轮校验成功后，在没有新增人工修改的前提下，再普通同步 current。即使该轮返回 304，也可使用已验证 envelope；如果该轮请求失败则维持现状。这样不会把“强制指定版本”偷换成任意后发版本。

### 7.3 首次同步与历史强制指令

新安装/升级首次成功握手时，`lastHandledForceVersion` 尚未建立（null/未握手）；已握手但不存在 force 时保存字符串 `"0"`，二者不能混淆：

- 有既存/人工配置：保留本地，将服务端当前 force 水位登记为基线，不重放安装前的历史强制指令。后台之后再次点击 force 才能覆盖。
- 无既存人工配置：应用最新 current，并登记 force 基线；不先回放历史旧版本。
- 首次拉取失败不会建立假基线；人工仍可正常配置，第一次成功时遵守上一条保护。

这是防止“第一次拉取就拿历史 force 抹掉旧配置”的技术规则。持久状态被主动清除/重装后不能承诺识别过去每次执行，只能重新握手，不冒称全局 exactly-once。

### 7.4 手工编辑、远程响应、UI 与存储

1. 人工保存、单广告规则编辑和配置导入走现有高层串行队列；实际业务值发生变化才设置 `localOverride=true`、推进 `localRevision`。无变化的 `onBlur` 不制造人工覆盖。
2. 本地运行开关、Cookie 刷新、日志追加不标记业务配置人工修改；这些字段本就不可被远端写入。
3. 网络等待在配置写锁之外；请求捕获 seller/服务/凭证 epoch，不用本地保存队列等待慢网络。
4. 收到响应后重新进入同一配置操作队列，复核当前绑定、服务/凭证 epoch、人工标记和单调水位。旧店铺或旧 Token 响应一律丢弃。
5. 普通请求期间发生手工修改：根据提交时状态发现 `localOverride=true`，绝不覆盖。force 与手工保存按本地队列提交顺序线性化：force 后提交的新人工修改受保护，不比较两个机器的时钟。
6. UI 保存消息携带编辑时的配置 generation；远端应用后重新挂载/刷新旧 uncontrolled inputs，拒绝旧表单延迟 `onBlur` 再把旧值保存回来。
7. 一次 `storage.local.set` 提交完整业务配置及应用元数据；force 水位不得先落盘。写入失败就保持未处理，后续周期可重试。保证的是成功本地提交后的幂等，不是跨浏览器分布式事务。
8. 不调用会清绑定并停止监控的 `replaceConfiguration`。新增受控业务配置替换 primitive，仍归 storage 的既有写队列所有。
9. 复用已有 settings 副作用：规则/关停窗口变化时废弃旧候选 generation，重新评估；必要时重排业务 alarms。不重置 `monitorEnabled`，不撤销已提交上游的请求，不把 ERP 同步失败当作 TikTok 完整性证明。
10. `campaignsComplete`、目标存在性、白名单、SKU 保护和 `CampaignCloseQueue` 最终关口保持不变。不得恢复 allowUncached 等兼容绕过。

### 7.5 调度

使用独立的 `chrome.alarms` 配置同步 alarm，周期 0.5 分钟（对齐插件既有 `MIN_CHROME_ALARM_DELAY_SECONDS = 30` 常量）；最低 Chrome 120。启动、安装/升级、绑定变化、Token 保存时确保 alarm 存在，再立即触发一次。

同一实例请求 single-flight；每次慢操作前确保下一次 alarm 已注册。后台被挂起或浏览器休眠后允许延迟，恢复时补一次当前检查，不能承诺严格墙钟每 30 秒或逐次补发全部错过周期。没有 `setInterval`、常驻线程、offscreen 保活或 WebSocket。

配置 alarm 独立于 `monitorEnabled`：手动暂停监控不应阻止收到新配置；反过来收到配置也不能解除手动暂停。

## 8. 页面设计

### 8.1 插件

保留所有现有人工设置；新增“ERP 配置同步”卡片：

- 当前 `sellerId`（明确标签，不能只用 `seller / advertiser` 猜顺序）。
- 首次 Token 输入：password 类型，保存后显示已配置/清除/替换；不回填明文；服务地址为获准预置 origin。
- 最新服务端 delivery version/hash 和源 global/shop versions。
- 当前生效来源：服务端版本或“人工配置 `localRevision=N`”；人工配置同时显示“基于远端版本 X / 无远端基线”，不能把远端新版本冒充实际生效版本。
- 本地人工保护标记、最近检查/成功时间、最近错误、最近已应用强制编号。
- Hash 可显示短摘要并复制完整值，但从不复制包含 secret 的完整响应作诊断日志。

不因同步错误禁用现有表单、关停、扫描或通知按钮。错误提示为辅助状态，不替代原有运行状态。

### 8.2 ERP

运行配置入口增加 monitor 店铺选择、全局默认/店铺覆盖、合成预览（敏感字段掩码）、保存草稿、普通发布、强制覆盖已发布版本。

强制覆盖确认框明确“覆盖该 seller 下所有已接入实例的人工业务配置，不解除本地暂停”。展示已发布指令编号和时间；未接回执前不显示“已应用”。admin 才能生成/轮换/禁用店铺 Token。

## 9. 升级、备份与恢复

- 旧数据有 bound seller：在同一配置写队列迁入对应 seller 容器并标记人工保护；既有业务字段不改值。
- 无法确认归属的旧配置：保留为未归属备份供用户明确导入，不偷偷复制给任意新店。默认值不等于已确认人工配置，检测基于存储事实而非 `getState()` 合并后的默认对象。
- 当前 v1 配置备份继续可读；导入视为人工行为，沿用原有导入安全语义。远端同步不上传 Cookie，不把 Token/同步水位混入旧 settings 备份；备份恢复也不能把旧 force 水位回滚后触发重放。
- 从 ERP 获取的 config revision/force 元数据不能被任意导入文件授予权威。
- 数据库回滚/灾备导致服务端水位倒退时：客户端保留本地并报错，运维显式修复发布水位或重新建立同步域。不能自动把“版本小了”理解为新的强制命令。
- 整个功能可通过移除/禁用连接 Token 停止远程同步，当前人工与已应用业务配置仍可继续运行。回退插件版本前验证新增 storage 结构兼容，不直接用旧代码解释新容器；实施时提供受控导出/恢复步骤。

## 10. 实施拆分与归属

本节是实施路线，不代表已获准执行生产操作。风险等级高：自动关停规则来源、密钥释放、跨店授权与配置并发同时变化；`ArchitectureReviewRequired = yes`。插件依 `plugins/monitor/AGENTS.md` 严格 RED→GREEN；后端同样优先做安全边界的失败用例。

| 阶段 | 内容 | 预计 owner/文件 |
| --- | --- | --- |
| A | 固定 schema、迁移、受限 Token 和跨路由拒绝 | ERP `access/`、`db/models/security.py`、新增 `db/models/monitor_config.py`、独立迁移、鉴权测试。 |
| B | 发布编排、合成/加密交付、force 幂等与专用 GET | 新增 `tts_erp_v2/monitor_config/`；复用 runtime repository；薄路由 `api/v2/monitor_config.py`，app 仅注册 router，不改 middleware 顺序。 |
| C | ERP 管理视图与受控 secret 写入 | 复用 runtime-configs 页面/样式/脚本；敏感字段只写、版本冲突、force 确认、Token 管理。 |
| D | 插件纯同步状态机、HTTP client、按 seller 存储 | `src/core/remote-config.ts`、`src/extension/remote-config-client.ts`、`storage.ts`、`messages.ts`；独立测试。 |
| E | 背景编排和 popup 接入 | `entrypoints/background.ts` 仅调度/编排；`popup/main.tsx` 保留编辑并展示真实状态；WXT 精确 host permissions、minimum_chrome_version。 |
| F | 文档、兼容回归、版本/构建和受控联调 | 外部 API、runtime config 安全例外、README/CHANGELOG、插件版本同步。 |

### 10.1 与既有 lane 的热点冲突（未决）

`docs/handoff/ACTIVE.md` 中的 `video-publish-design` lane（`active`）已经占用：

- `tts_erp_v2/access/` —— 阶段 A 直接需要；
- `tts_erp_v2/db/models/` —— 阶段 A 直接需要；
- `tts_erp_v2/app.py` —— 阶段 B 需要注册 router；
- `alembic/versions/**` —— 阶段 A 的迁移；
- `docs/{api,architecture,design,ops,schema}/` —— 阶段 F 的契约更新。

因此：

- **本 lane（`monitor-config-docs`）只拥有 `docs/monitor-config-sync.md`**。该文件位于 `docs/` 根目录，不在上述声明范围内，与 `video-publish-design` 不冲突。
- 阶段 A–E 涉及上列热点文件，**在 `video-publish-design` 释放或明确移交前不得开工**。
- 交接方式按 `docs/guides/agent-git-workflow.md` §4：给热点 owner 精确 patch / 变更请求，或在其 `ready` 后创建显式 successor lane。不因 owner 不在线擅自改动，也不擅自把这些文件写进本 lane 的归属列。
- `plugins/monitor/**` 是独立 git submodule（`git@github.com:brakchen/ads-monitor.git`），不随本仓 lane 归属管理；插件侧工作在该子模块自己的仓内提交。不要修改 `plugins/ads-data-sync` 的插件代码。

## 11. 验收矩阵

### 11.1 后端

1. 五店 Token 只能读取自己；更换 `sellerId`、重复参数、普通只读 key、cookie 或 monitor key 调管理/其他只读路径均不可越权。
2. 304 前验证授权；Token 禁用后同一 ETag 不能续用；弱标签、多标签、`*` 按 RFC 工作。
3. 普通发布生成新 delivery；强制发布即使 payload 相同，也生成新 version/ETag。
4. source 发布、交付写入和 force 指针任一步失败均整体回滚；旧配置仍可读。
5. global 与 shop 并发发布不存在撕裂组合或死锁；锁顺序稳定。
6. force 幂等请求重试、响应丢失、重复点击与 stale expected version 均有确定结果。
7. force 后普通发布保留旧强制目标；两个 force 离线合并到最新指令，不需要重放全部历史。
8. secret 只在允许的店铺下发边界释放；通用 snapshot/管理列表/普通 revision/审计/日志不泄漏。
9. secret 修改而未重新发布不会偷偷改变既有 delivery/hash；发布后才改变。
10. schema 拒绝 runtime/Token/Cookie 等越界字段；错误请求不写半份配置。

### 11.2 插件

1. 首次 200、普通更新、304、无缓存 304、超时、401/403/404/429/5xx、无效 JSON/schema、跨店正文。
2. 首次失败可人工配置；长期失败只记日志，不改 `monitorEnabled`，不停既有扫描/关闭/飞书。
3. 任一业务人工修改保护整份配置；普通新版本不能覆盖；无变化 `onBlur` 不误标人工。
4. force 应用一次；同号重试、worker 重启或 Token 轮换后不抹掉后续人工修改。
5. force 写入失败不推进水位；下一轮包括 304+有效缓存时可恢复提交。
6. 首次同步的历史 force 不覆盖既存人工配置；已有同步基线的离线实例可收到新 force。
7. deferred 测试：拉取中手改、切店、换 Token、并发触发、旧 UI 延迟保存；验证真实提交顺序，不只验证 mock 调用。
8. A→B→A 配置/ETag/水位正确隔离，未找到 B 的本地配置不得使用 A 的规则和单广告阈值。
9. 更改关停窗口/阈值时旧候选失效，完整快照/白名单/SKU/同 ID 互斥回归全部保持。
10. remote apply 不调用清绑定/停止监控的 import 路径；本地暂停不会被 force 解除。
11. 升级迁移、旧 v1 备份导入/导出与同步元数据独立；导入不能回滚 force 水位。
12. 页面展示实际生效配置，不把 downloaded version 当 applied version；Token/secret 不进入错误消息或日志导出。

### 11.3 验证入口与上线分层

- ERP：只使用 `bash scripts/test_isolated.sh ...`。聚焦测试先跑，再跑 `bash scripts/test_isolated.sh fast`；需要迁移时使用 `--refresh-template`。实施时依据 [`docs/guides/agent-testing.md`](guides/agent-testing.md) 确认测试路径和域，不直接调用 pytest。
- 插件：依现有工具链执行聚焦 RED/GREEN、全量测试、coverage、`npx tsc --noEmit`、production build；覆盖率 floor 遵守 `plugins/monitor/AGENTS.md`（95% statements/functions/lines、90% branches）。若宿主 `NODE_ENV=production`，测试显式使用 `NODE_ENV=test`。
- 合并前执行变更路径的主动诊断、diff/secret 检查，版本文件保持一致；不把空 LSP 缓存当 clean。
- 受控真实验收：可信 HTTPS/CORS/扩展 host permission、不同 Profile 五店身份、Chrome 挂起/休眠恢复和飞书触达。模拟测试不冒称真实联调。
- 不未经授权触发真实广告关闭；配置强制覆盖验收使用禁用真实关停的测试设置。生产迁移/重启由人类操作。

## 12. 自审、实施前置与完成边界

方案选择已落实用户全部业务决定（[§1.1](#11-已确认的业务规则) R1–R12）。其余是工程验证/部署前置，不新增业务审批问题：

- **HTTPS**：现有 HTTP 文档不能证明 HTTPS 可用，需要部署方提供与验证（[§4.4](#44-https-部署前置)）。
- **身份**：上线前核验目标 `sellerId` 与 ERP 登记 `shop_id`；不增加 `advertiserId` 作为配置主键。
- **共享热点**：阶段 A–E 开工前必须完成 `video-publish-design` 交接（[§10.1](#101-与既有-lane-的热点冲突未决)）。
- **迁移**：只能在 `tts_erp_test_template` 及其临时克隆上验证；生产 `alembic upgrade` 与服务重启由人类执行。
- **审查范围**：本文与已入库的 T001 契约切片一致，但应用/数据库/浏览器行为**尚未实现或测试**。正式实施前应按高风险变更进行架构与安全评审。

### 12.1 本轮交付记录

- 本文件由 lane `monitor-config-docs` 交付，分支 `docs/monitor-config-sync`，基点为
  `monitor-config-contracts` 的 `3953a6d290b9ed68dea77a3f221e2e10d9eb6cc6`（其父提交 `c491ade8` 即当时 `origin/master`）。
- 未推送、未合并；未启动服务、未运行生产迁移、未接触真实凭证、未触发 TikTok/飞书动作。
- registry 行 `monitor-config-docs` 由 `.worktrees/coord-active-md` 单独提交，`docs/handoff/ACTIVE.md` 是协调元数据，不计入 lane 归属文件。

### 12.2 协议与平台参考

- HTTP 条件请求：RFC 9110 §13.1.2 `If-None-Match`、§8.8.3 `ETag`、§15.4.5 `304`。使用现有框架和标准库，不复制上游实现。
- Chrome 调度：[chrome.alarms](https://developer.chrome.com/docs/extensions/reference/api/alarms)，Chrome 120 起正常安装最小周期 30 秒，alarm 允许额外延迟；不以 unpacked 开发模式证明生产频率。
- Chrome 存储：[chrome.storage](https://developer.chrome.com/docs/extensions/reference/api/storage)。运行实现必须验证 storage 提交/中断恢复，不把一次本地提交夸大成跨进程 exactly-once。

外部依据为官方来源；本方案未新增第三方依赖，没有待评估的额外许可证或供应链引入。