# 运行配置交互改造：产品约定与技术方案

日期：2026-11-01（Asia/Shanghai）。

状态：用户已认可产品草案的默认主流程“编辑 → 检查 → 确认发布；暂存草稿是可选操作”。本文给出对应技术方案，尚未实施或部署；具体 API 扩展属于待评审设计。

## 1. 目标、依据与范围

目标是降低操作理解成本：用户不必先理解草稿版本和发布版本，即可知道自己在修改什么、修改是否生效、下一步做什么。保留已有配置生命周期、权限和审计能力。

### 1.1 已确认的产品方向

1. 首页聚焦配置列表；新建配置和凭证管理为独立入口。
2. 打开后先看当前生效内容与草稿状态，再选择修改或继续草稿。
3. 编辑区只围绕一项配置；Schema 默认折叠、只读。
4. 主操作是“检查并发布”；进入检查页没有写入副作用。
5. 最后一次“确认发布”完成本次内容的保存与发布。“暂存草稿”服务于中途保存。
6. 明确区分未保存修改、已保存未发布草稿、已发布内容，并保护未保存输入。
7. 历史内容先带入编辑，再使用同一套检查发布流程；凭证管理单独处理。

用户最近一次明确确认的是第 4、5 项默认主流程；其余沿用上一轮产品草案，实施前一并按本文验收矩阵复核。

### 1.2 技术基线和权威边界

本文从 `origin/master` 的 `8b082808a025c4250852c683ae2ebc54c11df47b` 建立文档分支。此前现场分析读过 `E:\tts-erp` 的未提交页面样式改动；它们不属于本任务，未复制、修改或暂存。落地时必须重新同步主干并核对行为差异。

| 依据 | 本方案使用方式 |
| --- | --- |
| [前端开发规范](../guides/frontend-development.md) | 免费 HeroUI 优先、共享 AppShell、服务端拥有业务校验 |
| [前端迁移方案](frontend-progressive-migration.md) | 复用同源 MPA 工程，不另建 SPA 或设计系统 |
| [运行配置现行契约](../ops/runtime-config-management.md) | 不可变 revision、Schema、secret 引用、归档及恢复语义 |
| [外部 API 契约](../api/external-api.md#runtime-configuration-v2configruntime) | readwrite 角色、现有 endpoint 与兼容边界；实施时更新该权威文档 |
| [架构概览](../architecture/architecture-overview.md) | 配置存储、同源路径、凭证单一实现 |
| [安全规则](../guides/agent-safety.md) | 隔离测试、凭证处理、生产操作边界 |
| [测试指南](../guides/agent-testing.md)、[Git 工作流](../guides/agent-git-workflow.md) | 实施和交付证据要求 |

本文只拥有本次交互改造设计，不替代现行运行契约，也不把计划状态写成已上线状态。

### 1.3 范围与非目标

本轮覆盖 `runtime-configs` 的列表、详情、编辑、检查发布、新建、历史、停用/启用、凭证操作入口和失败恢复。权限仍为 readwrite/admin。

不改业务阈值或计算公式，不更换 JSON 编辑器，不自动把任意 Schema 生成业务表单，不新增配置消费机制、不恢复 rollout、不改变数据库表结构、不改插件、不调整全站导航或中间件顺序。发布配置不等于强制运行中任务刷新；生效文案必须符合实际消费者读取时机。

仅改按钮文字无法形成连续流程；实现需要重组页面状态，并补上检查请求和原子发布能力。最小后端边界限定在现有 runtime config 路由、校验及 repository；继续由它们拥有发布真相。

## 2. 页面与用户可见状态

### 2.1 页面组织

复用 `/v2/pages/runtime-configs` 和当前 AppShell。首版以页面内视图切换组织工作区，保留该 MPA URL，不引入 React Router、子页面路由或全站导航状态。

```text
配置列表 ── 查看与修改 ── 配置概览 ── 修改 / 继续草稿 ── 编辑
   │                         │                            │
   ├─ 新建配置               ├─ 发布历史 ── 使用此版本 ────┤
   └─ 管理凭证               └─ 更多：停用 / 重新启用       │
                                                          ├─ 暂存草稿
                                                          └─ 检查并发布
                                                                │
                                                   检查变更 ── 确认发布
                                                       └─ 返回修改
```

页面内的“返回”恢复列表筛选或来源视图。浏览器刷新后回到列表；不承诺跨刷新恢复未保存编辑，也不使用 localStorage/sessionStorage 存储配置或凭证明文。

### 2.2 各视图的职责

| 视图 | 展示 | 主要操作及结果 |
| --- | --- | --- |
| 列表 | 名称为主、键为辅；生效版本、草稿状态、更新时间 | “查看与修改”；顶部独立“管理凭证”“新建配置”；显示已停用项为可选筛选 |
| 概览 | 当前已发布内容只读；是否另有草稿；发布版本和状态 | 无草稿显示“修改配置”，有草稿显示“继续编辑草稿”，未发布显示“完成配置并发布” |
| 编辑 | 当前发布版本、工作副本、尚未发布提示 | 主按钮“检查并发布”，次按钮“暂存草稿”；Schema 收进“高级信息” |
| 检查 | 旧版与候选内容、增加/删除标记、发布说明、生效时机提示 | “返回修改”保留输入；“确认发布”才写入 |
| 历史 | 版本、说明、发布者、时间、完整内容 | “使用此版本”只加载工作副本，不直接发布 |
| 新建 | 名称、配置键、初始 JSON；高级选项为自定义 Schema | “创建并继续编辑”创建服务器草稿后进入编辑；名称和键创建后不新增改名能力 |
| 凭证 | 引用、指纹、时间、有效/归档状态 | 独立“新增凭证”；行内“更新凭证”“归档/恢复凭证” |

不添加第二套导航/Header。每个视图有明确标题和当前配置名称；长配置键换行。手机检查页将旧/新内容纵向排列，保留标签和差异标记，不把整页横向撑开。

### 2.3 状态文案

| 事实 | 文案 |
| --- | --- |
| 有已发布版本、无本地编辑 | 当前已发布 v3 |
| 浏览器输入尚未保存 | 你的修改尚未保存，也未发布；当前仍使用 v3 |
| 暂存成功 | 草稿已保存，尚未发布；当前仍使用 v3 |
| 从未发布 | 尚未发布，业务暂时无法通过运行快照读取此配置 |
| 发布响应已确认 | 已发布为 v4；后续读取将使用此版本 |
| 发布已确认、详情重读失败 | 发布为 v4 已确认，最新详情加载失败；可重试读取 |
| 停用 | 已停用，不再出现在运行快照；历史保留 |

“后续读取”不宣传为所有消费者立即刷新；特定业务消费者对停用、缺配置、回退和缓存的行为须通过实现验证再提供专属说明。通用页面不声称停止后台任务。

## 3. 关键技术选择

### 3.1 发布方案对比

| 方案 | 优点 | 问题 | 选择 |
| --- | --- | --- | --- |
| 前端连续调用保存草稿和发布 | 无须扩展发布 endpoint | 两次提交会部分成功；检查内容与最终草稿之间存在更多竞态 | 不采用为新主流程 |
| 在现有 publish endpoint 增加携带候选内容的原子模式 | 一次确认、一次数据库提交；复用现有发布 owner | 需要扩展请求模型及并发回归 | 推荐 |
| 新增发布任务表、队列和请求回执 | 可提供持久化幂等回执 | 为本次配置页面引入额外持久化和执行系统 | 本轮不引入 |

推荐方案将“保存与发布”实现为一次事务：候选内容直接持久化为不可变 revision，更新发布指针并清空旧草稿，不先提交一份中间草稿。任何校验或事务失败均不留下本次半完成草稿。该细化比产品草案允许的“两步部分成功”更简单，且符合用户已确认的一次最终确认。

### 3.2 组件与状态所有权

继续使用 React 19、TypeScript、Vite、HeroUI 3.2.6、vanilla-jsoneditor 3.13.0、lossless-json 4.3.3。不升级这些既有依赖。

拟调整/新增位置均在 `frontend/src/pages/runtime-configs/`：

| 文件 | 职责 |
| --- | --- |
| `index.tsx` | 视图切换、所选配置、读请求与工作流组装，退出旧的长页堆叠布局 |
| `ConfigList.tsx`（新增） | 只展示列表和入口 |
| `ConfigWorkspace.tsx`（新增） | 概览/编辑布局、状态说明、高级信息及局部操作 |
| `PublishReview.tsx`（新增） | 只读检查内容、差异展示、发布说明及确认 |
| `RevisionHistory.tsx`（新增） | 按需读取历史及选择历史内容 |
| `CreateConfig.tsx`、`SecretPanel.tsx` | 改为独立视图；保留表单输入及原有能力 |
| `useConfigWorkflow.ts`（新增） | 页面局部 reducer：视图、工作副本、检查结果、离开目标 |
| `useRuntimeCommands.ts` | 继续唯一拥有 singleflight、Retry-After、提交结果不确定及手动核对流程 |
| `contracts.ts` | 检查请求/响应类型与运行时解码；不放 UI 或业务公式 |
| `JsonEditor.tsx`、`json.ts` | 复用编辑、只读展示和无损 JSON 解析序列化 |

使用判别联合表达 `list / overview / edit / reviewing / review / history / create / secrets`，避免多个布尔值拼出互相矛盾的视图。状态保留以下事实：

- 服务端 detail：`draftVersion`、`publishedVersion`、草稿与已发布内容。
- 工作副本原始文本、进入编辑时的文本、发布说明，以及可选历史来源版本。
- 已成功暂存的文本，用于判断是否存在未保存输入；这里只是离开保护，不参与发布资格判断。
- 检查结果及其对应的候选文本、草稿版本、发布版本。内容编辑、切换来源、版本冲突后立即失效。

请求中的候选内容在检查期间锁定，晚到的检查响应不能覆盖后续编辑或其他配置。最终确认只发送用户刚刚检查的同一候选内容。发布说明在确认前可修改，不改变候选内容。

## 4. API 与事务设计

以下均是拟新增/扩展的契约；现行接口仍以现行文档为准。线上的 HTTP 路径继续经现有客户端拼接 `rootPath`，不写死 `/tts` 或域名。

### 4.1 新增只读检查 endpoint

`POST /v2/config/runtime/items/{config_key}/preview`

```json
{
  "expectedDraftVersion": 7,
  "expectedPublishedVersion": 3,
  "payload": {"feishuWebhook": "secret://monitor-health-feishu"},
  "sourceRevisionVersion": null
}
```

`expectedPublishedVersion` 必须显式提供，首次发布使用 `null`；草稿版本始终必填。`sourceRevisionVersion` 仅从历史载入时提供。字段使用 camelCase，服务端使用 snake_case。

处理顺序：授权/CSRF → 读取并核对配置有效状态与版本 → JSON Schema/业务约束 → 活动 secret 引用存在性 → 如有历史来源则检查原 revision 及退役 rollout 护栏 → 判断候选与当前发布内容有无语义变化 → 返回检查结果。检查请求不创建草稿、不生成 revision、不修改时间或审计事实。

响应为一次检查观察到的版本、`hasChanges`、旧/新规范化 JSON 文本，以及历史来源：

```json
{
  "configKey": "plugin.monitor_health.v1",
  "draftVersion": 7,
  "publishedVersion": 3,
  "hasChanges": true,
  "publishedText": "{\n  \"feishuWebhook\": \"secret://previous\"\n}",
  "candidateText": "{\n  \"feishuWebhook\": \"secret://monitor-health-feishu\"\n}",
  "sourceRevisionVersion": null
}
```

以上仅为结构示例，示例旧引用不代表现有健康告警业务允许它。未发布时 `publishedText=null`，不能拿 `{}` 冒充已存在的基线。

规范化和语义相等比较由服务端拥有：对象键顺序和纯排版不构成变更，数组顺序保留，布尔值、数字、字符串与 null 类型严格区分，避免 Python 中 `True == 1` 造成误判；数字比较保持既有 payload 解析契约，不增加前端 Number 转换。复用已有校验/序列化基础，补齐此处的明确规则和回归。首次发布即使候选为 `{}` 也属于新增。

前端以服务端 `hasChanges` 决定是否可确认，展示用 diff 不参与该决策。没有变化时解释“与当前发布内容一致，无需重复发布”，仍允许返回编辑或处理独立草稿。

### 4.2 扩展现有 publish endpoint

`POST /v2/config/runtime/items/{config_key}/publish`

新页面提交：

```json
{
  "expectedDraftVersion": 7,
  "expectedPublishedVersion": 3,
  "payload": {"feishuWebhook": "secret://monitor-health-feishu"},
  "sourceRevisionVersion": null,
  "comment": "更新告警接收地址"
}
```

在同一事务中执行：

1. 用现有 `locked_item()` 获取配置行锁，拒绝已停用项。
2. 比较草稿版本和发布版本；检查后被别人保存、发布或恢复历史，都必须返回 409，且不自动用新版本重试。
3. 重做 Schema、业务约束、历史来源和实际变化校验；不相信浏览器检查结果。
4. 沿用 `lock_referenced_secrets()` 的引用锁及固定顺序，与凭证归档串行化。
5. 复用 `repository.publish()` 创建高一号 revision、更新发布指针、清空旧草稿和递增草稿版本。
6. 一次 commit 成功后返回现有 `{configKey, publishedVersion}` 成功确认；中途错误 rollback，无部分写入。

`preview` 校验只证明检查时可发布，不能锁定之后的配置或凭证状态，最终提交必须重复检查。所有发布校验共用 runtime config 的同一服务函数，不复制为前端规则，也不为 preview 另建业务计算 owner。

### 4.3 兼容模式

保留旧客户端只发送 `expectedDraftVersion` 和 `comment` 的请求：仍发布服务器已保存草稿。新增模式以是否显式提供 `payload` 区分；携带候选时必须同时提供 `expectedPublishedVersion`，缺少返回 422，`payload=null` 不能被当作未提供。

旧客户端若携带 `expectedPublishedVersion`，也应实际核对，不能静默忽略。请求模型通过字段存在性区分 `null` 和缺省。不得因本次扩展重新接受已退役 rollout 字段。

不删除原 `/draft`、`/rollback` 或 lifecycle API。新页面的历史操作使用候选发布模式，并带来源版本；原 rollback API 继续服务现有调用方。两条入口共享后端发布与历史校验实现，没有第二套 revision 分配逻辑。

### 4.4 暂存、新建及凭证

- 暂存继续用现有 `PUT .../draft`，发送 `expectedDraftVersion`，成功后采用响应中的新草稿版本；后续检查使用新版本。
- 新建继续用现有 `POST .../items`。成功后进入新项编辑并明确“尚未发布”；创建成功但详情读取失败时，不再次创建。
- 新增凭证时前端校验名称未出现在当前元数据列表；更新操作锁定原名称，提示可能影响已有引用并确认。底层现有 PUT 是 upsert，因此不能把前端列表检查宣传为服务端“只创建不覆盖”的并发保证；本轮不新增此承诺。
- 凭证依然经 token_service 加密，明文不回读、不写日志或浏览器持久化。离开凭证明文表单时只有“继续编辑/放弃输入”，不提供普通配置的“暂存明文”。明确保存成功后清空输入。

## 5. 变更展示与依赖复用

检查页接收服务端生成的规范化文本，使用行差异展示增加/删除/未变内容。桌面可并排查看旧/新全文；窄屏使用统一差异视图，增加、删除同时带符号/文字，不只靠颜色。JSON 内容作为 React 文本渲染，禁止把配置内容作为 HTML 注入。

现有 `JsonEditor` 负责编辑和全文查看，不具备本项目已封装的差异能力。拟采用维护中的 [jsdiff](https://github.com/kpdecker/jsdiff) 的 `diffLines`，不自写通用差异算法，不新增整套编辑器或 UI 库。

2026-11-01 查阅 npm 官方 registry 和上游 tag：候选精确版本 `diff@9.0.0`，BSD-3-Clause，声明无运行时依赖，含 TypeScript 类型及 ESM 导出。参考 [v9.0.0 package.json](https://github.com/kpdecker/jsdiff/blob/v9.0.0/package.json)、[v9.0.0 README](https://github.com/kpdecker/jsdiff/blob/v9.0.0/README.md)、[安全公告页](https://github.com/kpdecker/jsdiff/security/advisories)。本次仅完成来源和包元数据核对；GitHub 公告 API 被限流，不能据此声称无漏洞。实施前复核公告、锁文件审计、Node 22/Vite/TypeScript 构建兼容性及打包增量；通过后精确锁定版本并记录证据。

仅调用 `diffLines`，不使用会再次普通 JSON 序列化的 `diffJson`。配置数字继续走既有 [lossless-json](https://github.com/josdejong/lossless-json) 和服务端 JSON 文本，防止大整数改变。设置上游支持的执行时间/编辑距离限制；超过限制时明确显示“差异过大，未生成高亮”，保留完整旧/新内容供检查，不伪装为无变化，也不改变服务端 `hasChanges`。

HeroUI 的 Button、Table、Modal、Disclosure 等优先直接复用，遵循 [官方 Modal 公开 API](https://heroui.com/en/docs/react/components/modal)。不新增通用 Button 包装、不深改组件内部样式。

## 6. 返回、冲突与失败恢复

### 6.1 离开保护

页面内返回、切换配置、进入凭证管理、停用当前配置，统一经过页面局部离开保护：有未保存配置输入时显示“暂存并继续 / 放弃修改并继续 / 留在此页”。暂存失败不得执行原离开动作。发布说明是浏览器局部输入，暂存草稿接口不会保存它；离开前须明确说明放弃该说明，不能用“草稿已保存”暗示说明也已保存。

外部 MPA 导航、浏览器返回、刷新和关闭使用有编辑时才注册的 `beforeunload`，依赖浏览器原生离开确认。该确认无法提供自定义三按钮，也不能保证所有移动端进程终止都弹出；这与页面内三选项是明确的能力边界。本轮不改全站 AppShell 或拦截浏览器历史制造伪导航。

### 6.2 错误状态

| 情况 | 页面行为 |
| --- | --- |
| JSON 语法错误 | 留在编辑页，保留原文、指出错误，不发送发布请求 |
| preview 校验失败 | 留在编辑页，显示可修正的校验信息；不写入 |
| 409 版本冲突 | 检查结果失效；保留工作副本；提供“查看最新内容”与旧/新对照，不自动合并或覆盖 |
| 最终发布已无变化 | 不新增 revision，解释当前内容已一致并引导刷新核对 |
| 401 / 403 | 分别显示登录失效/无权限，不冒充空列表；不自动重发 |
| 429 / Retry-After | 沿用现有完整冷却，不自动读写重试 |
| 网络中断、5xx、成功响应格式不符 | 沿用不确定结果闸门；保留工作副本，显示“提交结果未确认，请查询核对”，不宣称发布失败或自动回滚 |
| 成功确认后刷新失败 | 保留已确认版本号，重试读取；不把已成功发布重新标记为未知 |

现有 `useRuntimeCommands.verify()` 返回当前事实，不能证明原请求是否提交。新页面使用用户文案，隐藏内部 attempt.kind；核对后如需重试，必须重新获取版本、重新检查并再次确认，不能仅解除闸门就发送旧检查结果。本轮不引入持久化幂等回执，因此不承诺精确识别某次断网请求。

错误详情采用服务端受控字段/静态错误映射，不直接展示数据库异常、上游响应或包含用户凭证明文的原始 detail。preview 的语法、Schema、引用、版本冲突应有区别；只扩展本页面需要的错误映射，不全站重写错误体系。

历史来源必须在 preview 和 publish 两端检查原 revision，保留已有遗留 rollout 拒绝语义；不能通过“先复制历史 payload”绕开旧 rollback 的护栏。选择历史内容覆盖工作副本时也需要未保存保护。

## 7. 实施分解与验证

本次交付为文档。后续代码由一个 writer 串行实施；各任务是结果单元，不强行拆成独立发布批次。

TDD Route：本次 docs-only 为 skipped；后续实施按 auto/strict 处理发布事务、并发和页面状态变更，依据是新增候选发布契约和显著回归信号。先补能揭示旧行为缺口的针对性回归，再改实现；不为静态文案编写镜像测试。

| 顺序 | 改动位置 | 最小结果及验证 |
| --- | --- | --- |
| 1 | `tts_erp_v2/api/v2/config.py`、`tts_erp_v2/runtime_config/repository.py`、`validation.py`；必要时该包内提取页面无关的发布校验服务；`tests/api/test_runtime_config.py` | preview 无写入；新模式原子发布；旧请求兼容；版本、类型、Schema、secret 和历史护栏测试 |
| 2 | 页面 `contracts.ts`、`useConfigWorkflow.ts`、`useRuntimeCommands.ts` 及对应测试 | 状态转换、候选冻结、晚到响应、成功确认/读取失败区分、不确定结果与重新检查 |
| 3 | `index.tsx` 及列表、工作区、检查、历史组件；现有 `index.test.tsx`、`JsonEditor.test.tsx` | 打通列表到确认发布；移除旧长页堆叠与必须先点保存的 UI；保留编辑器能力 |
| 4 | `CreateConfig.tsx`、`SecretPanel.tsx` 及页面局部测试 | 新建/凭证独立入口、名称锁定、更新确认、未保存保护及 lifecycle 文案 |
| 5 | `tests/browser/test_runtime_config_workflow.py`（新增）、现有隔离浏览器支持；`docs/ops/runtime-config-management.md`、`docs/api/external-api.md` | 原生浏览器验证、更新已实施契约、窄屏/普通桌面/超宽屏人工检查 |

后端关键回归：首次发布、无服务器草稿但有候选、已有草稿覆盖为本次发布、原子失败不改草稿/版本、相同版本并发只成功一次、检查后别人暂存/发布/停用、secret 与发布并发、历史 rollout 护栏、空对象、null/零/布尔/数字类型、大整数、旧 API 调用方。

前端关键回归：检查操作没有任何写请求、返回修改不丢输入、只暂存不发布、确认只提交一次且内容等于检查内容、无变化提示、草稿/生效状态区分、晚到响应、离开三选项、凭证明文离开例外、不确定提交不重放、确认成功后读取失败。

执行前按测试指南准备 worktree 环境并检查依赖。只使用隔离入口，命令从仓库根执行：

```bash
bash scripts/envsetup/install-test-deps.sh --check
bash scripts/test_isolated.sh fast tests/api/test_runtime_config.py
FRONTEND_TEST_FILTER=src/pages/runtime-configs bash scripts/test_isolated.sh frontend tests/frontend/test_components.py
bash scripts/test_isolated.sh browser tests/browser/test_runtime_config_workflow.py
bash scripts/test_baseline.sh
```

新增浏览器测试复用项目现有 owned-clone / child-server / BrowserFence 机制，必须验证真实写入进入当前临时库；未建立该绑定前不通过浏览器写配置。不要把 mock 截图作为真实事务验收。基线 suite 正常只运行一次；出现新失败只重跑最小失败选择。当前文档阶段没有执行上述应用测试。

### 7.1 用户验收矩阵

| 场景 | 可观察结果 |
| --- | --- |
| 首次进入 | 可以定位配置，读懂当前版本和是否另有草稿；无需接触 Schema |
| 修改后检查 | 能指出本次改变的内容，知道尚未保存/发布 |
| 暂存后离开再回来 | 草稿保留，当前发布版本不变；发布说明不被误认为已保存 |
| 确认发布 | 一个明确确认操作完成，得到服务端确认版本；生效时机文案准确 |
| 使用历史 | 先进入编辑和检查，选中历史内容不立即生效 |
| 校验、并发或网络失败 | 输入保留，有明确下一步，不靠重复点击解决 |
| 手机/桌面/超宽屏、键盘 | 状态、差异和按钮完整可达；焦点进入/返回合理，动态状态可读 |
| 无权限、停用配置、空列表 | 各状态有独立解释；不展示不存在的只读授权承诺 |

## 8. 收口、风险与交付边界

1. 旧页面组件里的堆叠布局、先保存才能发布的按钮条件、直接恢复历史的 UI 在新流程通过验收后退出；不保留两套可选主流程。
2. 后端旧客户端契约有实际外部兼容意义，因此保留原入口并共用发布实现；本轮不删除 legacy JS/template 分支，清理由消费者盘点后的单独任务负责。
3. 本轮不新增迁移、配置持久化缓存、发布队列、回执表或 rollout 开关。部署/生产操作仍按仓库既有授权边界处理。
4. 最大实施风险是检查内容与实际提交不一致、版本冲突处理覆盖输入、数字精度改变和历史护栏绕行。用冻结候选、双版本校验、无损文本和共享服务端验证收敛。
5. 当前只完成源码/现行契约核对和文档设计，未验证线上 UI、实际配置数据、生产消费者生效时机或依赖运行兼容性；不得把本方案写成已实现证据。

实施前同步最新 `origin/master`，若发布 API、页面 owner、业务消费者或权限契约已变化，先调整本文相关部分，不能照搬旧文件内容覆盖其他工作。代码完成后按仓库规定验证、提交和推送，再在串行集成流程中验证精确合并提交。当前文档分支的保留与推送只交付可评审方案，不代表用户已经批准运行时改造。
