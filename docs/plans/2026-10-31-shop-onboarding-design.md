# 店铺接入管理技术方案

日期：2026-10-31  
状态：前后端及 ads/monitor 插件源码已实施；数据库迁移、部署及用户验收未执行。  
范围：ERP 前端、后端、ads-data-sync 和 monitor 插件。

## 1. 目标与已确认的设计约束

这个页面承担两个目的：管理当前店铺的元数据和 TikTok App/OAuth 配置；展示插件自动发现的新店，待操作员决定接入时完成配置和授权。

用户已确认：

1. 店铺信息是元数据，统一沿用 `commerce.shops`，不建立店铺资料表或候选店铺表。
2. 当前业务中 `seller_id` 就是 `shop_id`。插件的 `sellerId` 映射为 `commerce.shops.shop_id`，不新增 Seller 身份列、身份映射表或人工关联流程。
3. 插件发现即创建店铺记录。配置 App、OAuth 授权都更新该记录，保留内部主键及已有业务关联。
4. “达到接入条件”由操作员决定，本期不建立自动判定阈值。
5. 按用户要求不运行回归测试，由用户进行验收。
6. 仅同步插件当前绑定店铺，用户每个浏览器只开一个店铺；不设计多店扫描或自动发现未绑定标签页。
7. 店名和区域不会变更，本期禁止人工编辑；采集时首次填写，已有值不覆盖。
8. 一次授权只返回一个店铺。从 A 店发起授权只能更新 A，其他返回结果视为异常。
9. 每个店铺单独配置 Service ID、App Key、App Secret，不提供共享 App 选择和跨店更新。
10. 授权成功后自动进入 API 定时同步，不增加手动启动步骤。
11. 未授权店铺只在店铺管理出现；其他页面、选择器和业务查询不允许展示或选择。
12. 关店、换浏览器或卸载插件后记录继续保留，不自动删除、归档或增加停用流程。

目标流程：插件发现店铺 → 自动创建/补充店铺元数据 → 人工配置 Service/App → 获取授权链接 → 店铺授权 → 更新原店铺凭据 → API 接入。

## 2. 当前实现与调查结论

### 2.1 旧候选接口

`GET /v2/admin/shops/unregistered` 在 `tts_erp_v2/api/v2/admin.py` 中，从 `plugin.ad_today`、`plugin.ad_daily`、`plugin.plugin_logs` 提取 seller_id，排除 `commerce.shops` 中已有的店铺。返回值只有店铺 ID 和来源，不能可靠发现没有广告数据的新店，也不包含完整元数据。

2026-10-31 检索结果：

| 范围 | 结果 |
| --- | --- |
| plugins 下当前源码 | 没有该接口的调用场景 |
| 当前 React 店铺页 | 不调用该接口，读取 channel-accounts |
| 旧版 shops.js / pages.py | 注释记录候选 UI 在 2026-09-29 下线，端点保留 |
| 其他引用 | 接口文档、后端测试、UI 审计脚本的模拟响应；前端存在“不请求该接口”的断言 |
| 部署访问日志 | 当前工作区没有应用访问日志，Docker Linux Engine 管道不存在，未能检查线上访问记录 |

结论：当前源码没有需要迁移的插件调用方；不能由此断言历史安装版本或外部客户端没有调用。用户已允许替换删除。新链路可用后删除旧路由及其专属实现。

如发布环境可访问，读取 `logs/stdout.log` 及轮转文件，筛选 `path=/v2/admin/shops/unregistered`，记录时间、状态码、调用 Key 的日志标识及 User-Agent。日志缺失或关闭应记为“未知”，不记为“零访问”。访问日志实现见 `tts_erp_v2/middleware/access_log.py`，轮转配置见 `scripts/logrotate/tts-erp.conf`。

### 2.2 Seller Center 店铺信息接口

订单插件和 ads 插件已有 `GET /api/v3/seller/common/get` 的请求及解析实现。默认 API origin 是 `https://api16-normal-sg.tiktokshopglobalselling.com`，通过已登录的 Seller Center 页面上下文请求。

| 上游字段 | 现有插件字段 | ERP 用途 |
| --- | --- | --- |
| seller_id | sellerId | 写入 shop_id；字符串 |
| shop_name | shopName | 店铺名称 |
| shop_code | shopCode | 店铺编码 |
| shop_region | shopRegion | 经校验的市场区域 |
| region_code | regionCode | 原始区域提示；不能未经验证替代 shop_region |

历史接口目录记录该接口有约 21 个 profile 字段，包括 markets、global_seller 等。本轮未取得完整原始响应样本，第一版只采用上述已有解析契约，不承诺开店日期、主体、地址等额外字段。

订单插件已有 fetch/XHR 身份响应观察、主动探测和资料持久化方式，供 ads/monitor 参考。ads 已有主动身份探测，但绑定状态主要保存 sellerId，其他资料未形成独立 ERP 元数据上报链路。

### 2.3 当前交互和接入缺口

- 店铺页只展示已入库店铺，没有插件自动发现入口。
- 当前 App 凭据按 Service ID 共享存储，与本次确认的每店独立配置不一致，需要隔离存储和查询。
- 授权链接区域缺少明确的目标店铺上下文；OAuth state 当前只绑定 Service ID。
- credential_id 存在不能证明当前授权一定可用。
- ads 默认需要先向 ERP 查询店铺区域，新店尚未入库时会失败；手动区域/缓存可以绕过部分情况，但默认接入链路仍存在前置循环。
- monitor health 上报只维护运行状态，其名称来自通知别名，不能直接视为真实店铺名称。

## 3. 数据模型：只扩展 commerce.shops

### 3.1 沿用现有字段

| 字段 | 规则 |
| --- | --- |
| id | 内部稳定主键；发现、人工维护、授权过程中不更换 |
| platform / shop_id | 唯一键；本期 platform 固定为 tiktok，sellerId 写入 shop_id |
| account_name / region / seller_type | 店铺元数据；名称和区域首次填入后只读 |
| opened_date | 人工确认的开店日期，未知保持 NULL |
| service_id | 店铺选择的 TikTok Service |
| credential_id | 现有 OAuth 店铺凭据引用 |
| status | 保留现有业务语义，不复用为“发现/待配置/待授权”状态机 |
| source_updated_at / synced_at / updated_at | 保留现有含义；插件上报不冒充订单或 OpenAPI 同步 |

继续复用 `integration.tiktok_app_credentials`、`integration.credentials` 和现有加密服务；不向 shops 复制 App Secret/Token，不新增接入实体。App 凭据需在现有表内增加店铺归属，详见 4.2；不能仅隐藏共享 UI 而继续让保存操作覆盖另一家店铺的密钥。

### 3.2 建议新增字段

| 字段 | 类型 | 作用 |
| --- | --- | --- |
| shop_code | nullable text | 店铺编码 |
| discovered_at | nullable timestamptz | 第一次收到有效插件店铺信息的服务器时间 |
| plugin_metadata | JSONB，默认空对象 | 各插件最近接收时间、采集时间、版本和有限的资料冲突信息 |

plugin_metadata 为固定结构、有限来源的元数据，不是任意原始响应仓库。示意：

```json
{
  "plugins": {
    "ads-data-sync": {
      "version": "x.y.z",
      "lastReceivedAt": "2026-10-31T10:00:01Z",
      "observedAt": "2026-10-31T10:00:00Z",
      "values": { "shopName": "Bridge nook", "shopRegion": "VN" }
    }
  }
}
```

只保留 ads/monitor 各自最新快照，不保存无限事件数组、完整响应、认证文件、浏览器 Cookie 或页面 URL。元数据结构仅由服务端构造。不引入 fieldOwners、legacy/manual 标记或人工锁定/解锁机制。

### 3.3 覆盖规则

1. 插件新建：填写合法的名称、编码、区域；Service/credential/opened_date 留空，status 不伪造为已授权。
2. 名称、区域和编码仅补齐 NULL/空白字段；已有值不被插件或 OAuth 覆盖。并发首次填入使用数据库条件更新。
3. 名称和区域在 UI 只读；后端 PATCH 也拒绝修改，不能仅禁用前端输入框。手工补录时可首次填写，创建后遵循同一只读规则。
4. 既有非空值保持原样，不迁移字段所有权。新观察与既有值冲突时保留原值并展示资料异常，不新增人工编辑流程。
5. OAuth 更新授权凭据和接入事实；可以补齐空名称/区域，不覆盖已存在的值。
6. 插件缺失/空白字段不清空已有信息。开店日期是本期允许人工维护的基础字段：PATCH 缺省保持，显式 null 清空；插件从不填写或覆盖。
7. 开店日期不从发现时间、采集时间或只有店铺基础资料的响应推断。
8. Service ID、App 凭据、credential_id、业务 status 不在插件写入白名单中。

并发更新使用唯一键 upsert 和行锁/数据库原子合并，禁止读出整块 JSON 后无锁覆盖，避免 ads 与 monitor 互相丢失来源记录。观察时间只用于资料新鲜度和冲突诊断，不用它决定覆盖已存在的名称/区域。重复上报不改变首次发现时间。

## 4. 后端接口与服务边界

### 4.1 新增 POST /v2/plugin/shops/discover

这是元数据自动登记接口，不依赖广告 dump、订单同步或监控开关。请求为拟定的新契约：

```json
{
  "protocolVersion": 1,
  "requestId": "uuid",
  "source": "ads-data-sync",
  "pluginVersion": "x.y.z",
  "observedAt": "2026-10-31T10:00:00Z",
  "seller": {
    "sellerId": "7494763368967603447",
    "shopName": "Bridge nook",
    "shopCode": "example",
    "shopRegion": "VN",
    "regionCode": "VN"
  }
}
```

规则：source 仅允许 ads-data-sync/monitor；sellerId 必填且全程字符串，不经 JS Number；名称等可选字段限制长度；请求体上限建议 8 KiB；未知字段拒绝。校验 region 时优先使用明确的 shopRegion，regionCode 只在有已确认映射时作为补充，不猜测市场。

成功统一返回 200，并在事务提交之后确认：

```json
{
  "ok": true,
  "requestId": "uuid",
  "created": true,
  "shop": {
    "id": 314,
    "platform": "tiktok",
    "shop_id": "7494763368967603447",
    "account_name": "Bridge nook",
    "region": "VN",
    "opened_date": null
  }
}
```

按现有 `(platform, shop_id)` 唯一键 upsert；重复请求不会重复建店。created 表示本次是否插入，不承诺重试时仍为 true。重复接收允许更新服务器接收时间，它代表资料上报时间，不代表实时在线或业务数据已同步。requestId 用于诊断关联，不要求新增持久化幂等表或保证历史响应逐字重放。

| 异常 | 行为 |
| --- | --- |
| 401 / 403 | 凭据或店铺范围不允许；插件提示修复连接，不高频重试 |
| 413 / 422 | 数据不合法；记录字段错误，资料变化或用户操作后再试 |
| 429 | 遵守 Retry-After |
| 5xx / 网络超时 | 保留待上报内容，退避重试；不声称登记成功 |

权限：readwrite+；受限 Key 必须包含 seller:<shop_id>，advertiser-only 不能登记任意店铺；沿用当前无限定/全局 Key 策略。Cookie 写请求遵守 CSRF 规则；浏览器插件使用现有 API Key。同步修改 `access/_policy.py` 路由分类及接口自身校验，不因默认角色分类导致插件请求被错误拦截。

### 4.2 读取和管理接口

- 沿用 `GET /v2/commerce/channel-accounts`，增加明确的 `view=management` 参数供店铺管理读取全部店铺。默认只返回具备当前授权的店铺，不允许其他页面传 management。
- 扩展响应：店铺编码、发现时间、各插件最近上报、App 配置投影、授权状态投影。仅输出必要信息，不回传完整内部插件快照或秘密。
- 添加服务端 q、source、app_state、auth_state 筛选以及稳定分页；继续 limit/offset、X-Total-Count，不固定只显示前 500 条。
- 沿用 `POST /v2/admin/shops/register` 作为手工补录入口；与插件共用唯一键和元数据服务，重复提交不生成第二条店铺。
- 沿用 `PATCH /v2/admin/shops/{shop_pk}`，区分开店日期维护和该店 App 配置。名称/区域禁止修改；Service ID、Key、Secret 在本店配置表单提交，不提供 app-options 或共享 App 选择。
- 业务用 by-external、按内部 ID 详情查询和业务参数解析也执行授权资格校验，防止绕过选择器直接传入未授权店铺 ID。
- 插件需要在未授权时获取区域。增加受 seller scope 限制的 `GET /v2/plugin/shops/{shop_id}`，只输出本店必要元数据；ads 区域刷新改用该入口，复用原区域校验逻辑。此接口不作为其他业务页面查询入口。

新增一个 shop metadata 服务模块作为创建、补齐空值及来源更新规则的唯一实现，路由负责验证和事务边界；插件发现、手动注册、人工 PATCH 和 OAuth 调用同一套规则。

每店 App 配置的存储调整：在现有 tiktok_app_credentials 增加 id 主键、可空 shop_pk 外键和 revision，唯一键调整为 `(shop_pk, service_id)`；未归属的历史配置保留但运行时不使用；密钥仍加密保存。所有读取/保存路径显式携带 shop_pk，当前配置由 shops.service_id 指向，禁止仅凭 Service ID 或全局环境默认值借用其他店的配置。相同 Service ID 字符串不意味着共享一条可变配置。

迁移按现有店铺的 service_id 及其凭据的签发 service_id，复制相应旧配置为店铺独立记录，保留旧签发 App 配置以供已有凭据处理；缺失配置明确报错，不凭空生成。每次密钥更新增加 revision。token_service、OAuth、刷新令牌和代理调用同步改造；旧配置只能在调用方全部迁移后清理。迁移不在本轮执行。

### 4.3 授权资格与全系统可见性

由后端提供统一资格判断：店铺 credential_id 必须关联 provider=tiktok、external_account_id=shop_id 的凭据，签发 Service 与当前选择一致，App 配置可解析，且无已知不可恢复的失效。Access Token 到期但可正常刷新不等同未授权。

店铺管理可查看全部店铺并解释异常；其余选择器、首页统计、采购、SPU/ROI、报表和业务接口默认执行该资格约束。不能只过滤一个下拉框或只改 channel-accounts，而遗漏独立 SQL 和直接 ID 请求。已有其他权限校验继续执行。

当前 scheduler._enumerate_tiktok_shops 只按“凭据外部 ID 存在同 ID 的 shops 行”枚举，没有要求 shops.credential_id 指向该凭据。必须改用统一资格判断，避免插件新建记录意外激活历史遗留凭据。授权事务成功后，下一个正常调度周期自动纳入同步；不需要操作员再次开启，也不在 HTTP 回调里执行长时间数据同步。

### 4.4 状态投影

| 维度 | 投影规则 |
| --- | --- |
| 插件同步 | 展示各插件资料最近上报时间，明确这是元数据同步，不冒充广告/订单同步成功 |
| App 配置 | 未选择 Service / 缺配套凭据 / 已配置；Service ID 本身不等于完整 App 配置 |
| OAuth | 未授权 / 已保存授权 / 需重新授权 / 已知异常 / 待确认 |

没有 credential_id 显示未授权；有凭据但缺乏有效性证据显示“已保存授权/待确认”，不宣称在线验证通过。Access Token 到期但可刷新不直接等同重新授权；使用现有 token_service 可用的刷新/错误事实。当前所选 Service 与凭据签发 Service 不一致时提示需重新授权，禁止直接改写旧 Token 的签发归属。

## 5. OAuth 闭环

扩展现有 `GET /v2/oauth/tiktok/authorize`，支持 shop_pk。店铺页生成链接时必须传目标店铺：

1. 校验操作权限并读取目标记录，按 shop_pk + service_id 解析该店的 App Key/Secret。
2. 将 expected_shop_pk、expected_shop_id、service_id、app_revision 和创建者写入现有一次性 OAuth state.extra；沿用现有有效期，不另建状态存储。
3. 回调交换授权码前确认配置版本未变化；取得授权店铺清单后，要求恰好一条且 shop_id 等于目标 A。
4. 空清单、多条清单或其他店铺均返回明确异常；不更新 A，不创建或更新其他店铺。
5. 验证通过后仅更新 A 的凭据引用和授权事实，保留内部主键、现有名称/区域、开店日期。
6. 删除现有回调遍历全部授权店铺并 upsert 的行为，按单目标事务提交。
7. 提交时再次在锁定/版本条件下验证 Service 和 app_revision，避免请求上游期间配置变更。配置变化时提示重新授权，不能覆盖新配置。
8. 回调页展示目标店铺及实际结果；返回店铺页或页面恢复焦点时刷新数据。没有事件通知能力时不伪装实时完成。

授权入口要求明确目标 shop_pk，不保留可绕过单店约束的通用多店入口。发布前生成且没有目标上下文的旧 state 回调提示重新生成链接，不猜测目标。授权成功后显示“已授权，等待自动同步”，实际同步时间和结果由调度任务提供，不把授权成功描述成数据已同步。

## 6. 前端交互方案

沿用现有深绿导航、暖白背景、共享表格和表单样式。页面命名为“店铺接入管理”，无需创建第二套页面。

### 6.1 列表

建议列：店铺名称/ID/编码、区域/开店日期、发现来源、插件资料上报、App 配置、OAuth 授权、操作。外部 ID 保持完整字符串，长内容提供复制；区分未配置、未上报、读取失败，不统一显示横线。

筛选：关键词、来源、App 配置、授权状态；服务端分页。页面错误和空列表分别呈现；保留失败前已加载数据并提供刷新。

### 6.2 操作

| 操作 | 行为 |
| --- | --- |
| 手工补录 | 输入店铺 ID 和基础信息，提示已有店铺时更新/查看；不强制配置 App |
| 编辑信息 | 名称、区域只读；仅维护开店日期等明确允许的字段 |
| 配置 App | 直接配置该店的 Service ID、App Key、App Secret，无共享选择器 |
| 获取授权链接 | App 配齐后可用；弹层显示目标店铺、Service、有效期，分别提供复制和打开 |
| 查看接入信息 | 查看插件来源/时间、App 和授权状态及可操作的错误说明 |

首次配置需要 Service ID 和完整 Key/Secret；编辑时 Secret 不回显，密钥输入留空表示保留，成对输入表示更换。切换到未配置过的新 Service 必须提交完整密钥对。保存只影响该店，不提供跨店更新动作。

写请求的 pending/cooldown/结果未知恢复按店铺隔离，同一店铺防重复提交。超时后先重新读取确认，不自动重放写请求。关闭有修改的表单时保留/放弃选择明确。行授权链接不自动复制，用户点击复制后再给反馈。关店或插件离线只保留最后上报时间和记录，不新增自动归档/删除/停用按钮。

## 7. ads-data-sync 改造

### 7.1 资料采集与触发

- 复用现有 seller/common/get 解析及订单插件的身份观察模式，不把完整响应直接上传。
- 自动绑定、手动绑定、同页切店、重启恢复、连接配置变更都调用相同的资料登记调度入口。
- 保存当前绑定 sellerId、白名单字段及 observedAt；资料完整性补充和待重试状态不能被“绑定未变化”的提前返回拦住。不扫描其他标签页的店铺。
- 首次有效身份立即上报；资料变化立即上报；无变化时建议每 24 小时重新观察并上报一次，仅对已经有效绑定的店铺执行。
- 主动探测使用已登录页面上下文。参考订单插件的 sellerId hint，并验证响应与当前绑定身份一致；不能因旧异步请求把 A 店资料写到 B 店。

### 7.2 与广告同步的顺序

取得当前绑定 Seller 身份 → 持久化并提交发现请求 → 收到成功确认后立即调用插件元数据查询/刷新区域 → 日历条件满足后执行广告同步。首次登记和首次查询必须串行；不能仅将登记放入后台队列后立即发出必然可能 404 的查询。

发现不需要 advertiserId、广告消耗、订单或 opened_date，但要求插件已有当前绑定的 Seller 身份。上报成功后新店已存在，通过专用插件元数据入口取得区域；区域仍不足或多时区无法确定时只暂停依赖日历的采集，资料登记已完成。开店日期为 null 时保留现有同步范围规则，不当成发现时间。未授权限制的是 ERP 其他页面展示/选择和 API 作业，不禁止插件继续采集上报。

发现失败不应阻断已有有效区域缓存和既有广告同步；没有可靠时区则不能为推进同步而猜测。现有广告同步 v4 契约保持不变。

### 7.3 本地状态与传输

新增有界待上报状态，键包含 ERP baseUrl、Seller ID 和本地配置代次；不存 Token 副本。传输复用 syncBaseUrl/syncToken，Chrome 后台执行、credentials omit、禁止重定向携带凭据，并沿用目标 URL 校验和 origin permission。

单次请求超时建议 10 秒；网络/5xx 采用约 1/5/15/60 分钟带抖动退避，之后最多每小时重试；使用持久化 Chrome alarms，不靠 service worker 内存定时器。每个店铺只保留最新待提交资料，重试复用该次 requestId。相同目标请求 single-flight；更新的资料不会被旧 ACK 清空。切店/换 ERP 地址后旧响应不更新新绑定的状态，长期未绑定的队列设置容量和保留期上限。

设置页显示“店铺资料已同步/待重试/连接错误”，提供手动重新观察和上报，不把广告上传成功当作资料确认。

## 8. monitor 改造

- 新增 seller/common/get 身份资料观察/主动请求能力，复用 ads/订单插件已验证的页面请求方式和字段契约。
- 在绑定成功、店铺切换、插件恢复、连接配置变更时登记；资料变化和周期更新规则与 ads 一致。
- 复用当前 healthServerUrl/healthApiKey 对应的 ERP 连接及 Chrome origin 权限，不再增加第二套连接输入框。
- 对已有当前绑定店铺，发现调度独立于 monitorEnabled；未绑定时不执行发现，不额外扫描或自动绑定其他店铺。
- `notificationShopName` 继续作为通知别名；店铺元数据来自接口，不用别名覆盖 account_name。
- health 保持现有四字段状态协议和独立重试语义，不在心跳成功时伪造发现确认，不增加注册副作用。
- 发现失败不阻断扫描、评估、关闭广告；复用上述有界持久化重试原则。

当前两个插件是独立项目。第一期保持相同的小型协议和清晰模块边界，分别接线；不为本次需求强行新建跨插件公共包。订单插件本期作为参考，不要求一并发布。

## 9. 文件改动清单

| 范围 | 文件/目录 | 改动 |
| --- | --- | --- |
| 数据库 | alembic/versions/ 新迁移 | shops 元数据字段、现有 App 凭据表店铺归属/版本及主键迁移；无新表 |
| 模型 | tts_erp_v2/db/models/commerce.py、integration.py | 模型字段与 App 配置约束 |
| 元数据服务 | tts_erp_v2/commerce/shop_metadata.py（建议新文件） | upsert、仅补空值及来源合并 |
| 发现路由 | tts_erp_v2/api/v2/plugin_shops.py（建议新文件）及 app 路由挂载 | 协议、权限、限长、事务确认 |
| 权限 | tts_erp_v2/access/_policy.py | 新路由的读写分类 |
| 查询 | tts_erp_v2/api/v2/commerce.py 及各页面独立店铺 SQL | 管理视图、默认授权过滤、直接 ID 资格校验 |
| 调度 | tts_erp_v2/sync_worker/scheduler.py | 根据明确凭据关联和统一授权资格自动纳入同步 |
| App/Token | tts_erp_v2/proxy/token_service.py、tiktok_auth.py 及全部 App 解析调用方 | 按店铺隔离配置、版本校验和签发配置解析 |
| 人工维护 | tts_erp_v2/api/v2/admin.py | 共用元数据规则、App 模式、移除旧接口 |
| OAuth | tts_erp_v2/api/v2/oauth.py、proxy/tiktok_oauth.py | 目标 state、全量预校验、原记录更新 |
| 前端 | frontend/src/pages/shops/index.tsx、ShopForm.tsx、contracts.ts | 列表、筛选、表单分工、链接上下文与恢复 |
| ads | src/core/types.ts、entrypoints/background.ts、src/extension/storage.ts | 身份资料持久化、所有绑定路径触发 |
| ads | src/extension/shop-discovery.ts（建议新文件）、shop-region.ts | 上报/重试及区域查询顺序 |
| monitor | entrypoints/page-hook.content.ts、entrypoints/background.ts、状态/设置模块 | 资料采集、绑定事件与状态 |
| monitor | src/extension/shop-discovery.ts（建议新文件） | 独立登记调度和上报 |
| 文档/旧引用 | docs/api/external-api.md、旧 shops.js/pages.py 注释、scripts/probe_ui_layout_audit.js | 新契约与旧引用清理 |

实施时需一并修改 OAuth 当前直接覆盖名称/区域的逻辑，以及 monitor health 契约中关于“不假定 seller/shop ID 映射”的旧说明，统一为本次用户确认的同一 ID 定义。同步调整旧接口专属测试和契约断言，使其不再描述已删除功能；不因此启动回归测试。

## 10. 发布、历史数据与旧接口删除

1. 先增加 shops 元数据字段和现有 App 凭据表的店铺归属/版本，复制既有配置并校验每店可解析。店铺主键和业务外键不变；App 表主键切换应在全部调用方支持店铺归属后完成，不能只做表结构切换。
2. 发布全系统授权资格过滤、管理视图、调度条件、单店 App 配置和 OAuth 目标校验；随后开放发现接口。未授权店铺过滤必须先于自动建店启用，避免新店泄漏到其他页面。
3. 发布 ads、monitor 当前绑定店铺上报和新的插件元数据查询。已有插件的广告 v4 和 health 协议不变；盘点所有插件区域查询调用方，业务 by-external 收紧时同步迁移依赖未授权元数据的调用方。
4. 提供可重复执行的历史补录脚本，先只读预览：从旧候选查询使用的广告表/日志以及 monitor health 提取未入库 ID；名称仅在可信时补入，通知别名不当真名，未知区域和开店日期保持空。补录走同一 upsert 规则，不删除原始业务数据、不覆盖已注册店铺。
5. 新链路可用后删除 unregistered 路由、专属 schema/SQL、文档入口、审计 mock 和专属测试；前端“不访问旧接口”的约束可以保留为历史防护，或随旧概念一并更新。

部署日志检查是发布证据，不是已完成事实。若发现仍有旧调用，记录具体消费者并迁移；不为没有调用证据的旧路径永久保留另一套候选实现。

回退时可以先停用新插件登记，新增列与已发现店铺保留；不能回退为对所有页面展示未授权店铺。App 存储迁移完成后不能直接运行仅按 Service ID 查询的旧代码，需有匹配版本的恢复方案。不要自动删除记录或执行丢列 downgrade。发现不触发 API 作业；授权成功自动加入现有调度，前提是部署环境的 worker 正常运行，本轮不启动服务。

## 11. 用户验收场景

不运行回归测试。开发实施后由用户按以下场景验收：

- 当前已绑定新店没有广告/订单、未配置 App，ads 或 monitor 仍能把它展示到店铺管理列表；不采集未绑定店铺。
- 同店在两个插件、不同浏览器多次上报只有一条 shops 记录，来源时间分别显示。
- monitor 未启用时仍可登记；登记失败不影响原有监控动作。
- 新店登记后能查询区域；区域未知时明确提示，开店日期不被自动填成发现日期。
- 名称/区域只读，直接调用 PATCH 修改也被拒绝；插件和 OAuth 只补空值，不覆盖已有值。
- A、B 店分别保存 App 配置，更新 A 不改变 B；所有 App/Token 读取携带店铺归属。
- 获取链接时目标店铺明确；授权正确店铺更新原行，授权其他店铺明确失败，无部分写入。
- 多店或其他店铺授权结果被拒绝，只允许目标 A；授权中修改同一 Service 的 Key/Secret 也会检测到版本变化。
- 更换 Service 后显示需重新授权，不把旧 Token 重新标记成新 App 签发；授权成功后下一调度周期自动同步。
- 未授权店铺仅在管理页出现，其他下拉框、统计和直接 ID 业务请求均被过滤/拒绝；仅存在历史同 ID 凭据不能触发同步。
- 关店、换浏览器或卸载插件后店铺记录继续保留，不自动删除或归档。
- 上报/保存超时、断网、401/403/429、切店期间请求返回时，状态可解释且不产生重复店铺或跨店覆盖。
- 超过 500 家店铺仍可搜索和分页；App 已配置与 OAuth 已授权不会混为一个状态。
- 旧接口删除后，当前前端和两个新插件仍有完整接入路径。

## 12. 依据与未完成事项

已读取的主要实现：

- `tts_erp_v2/db/models/commerce.py`、`db/models/integration.py`
- `tts_erp_v2/api/v2/admin.py`、`commerce.py`、`oauth.py`、`monitor_health.py`
- `tts_erp_v2/proxy/tiktok_oauth.py`、现有 App/Token 服务调用
- `tts_erp_v2/access/_policy.py`、`middleware/access_log.py`
- `frontend/src/pages/shops/index.tsx`、`ShopForm.tsx`
- `plugins/ads-data-sync/src/core/tiktok-seller-endpoints.ts`、`entrypoints/background.ts`、`src/extension/shop-region.ts`
- `plugins/order-data-sync/src/core/tiktok-seller-endpoints.ts`、`entrypoints/page-identity-hook.content.ts`、`entrypoints/background.ts`
- `plugins/monitor/entrypoints/page-hook.content.ts`、`entrypoints/background.ts`、`src/extension/health-runtime.ts`
- `docs/reference/tiktok-seller-center-api-catalog.md`、现有接口/店铺分析文档

已实施：现有 shops 元数据扩展、插件登记与资料查询、单店 App/OAuth、管理页面、业务授权过滤、调度资格及两个插件的资料上报。前端类型检查及生产构建通过，ads 构建通过，后端语法和模块导入检查通过。

尚未完成：线上访问日志读取、完整上游 profile 样本核对、数据库迁移执行、插件发布和用户验收。monitor 构建因缺少本地依赖未完成；离线依赖安装被自动审批拒绝，未通过其他方式绕过。按用户要求未运行回归测试。源码实现不代表已部署。

验收前需在目标环境将 Alembic 升级到 0072_shop_app_credentials，然后启动配套后端版本及已打包前端。历史补录脚本 scripts/backfill_discovered_shops.py 默认只读预览，--apply 才写入。App 配置迁移复制数据库中已有配置；旧环境变量中的完整 Service/Key/Secret 在迁移时按既有店铺关联一次性导入并加密，数据库配置优先，运行时不再使用全局回退。迁移应在加载原配置的目标环境执行，不生成含敏感参数的离线 SQL。
