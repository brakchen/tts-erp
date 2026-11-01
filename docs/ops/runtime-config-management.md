# 运行配置与凭证管理

## 目的与边界

运行配置用于把可安全热更新的 JSON 下发给本服务内的调用方，例如功能开关、阈值和上游端点选择。它不是 `.env` 替代品：数据库地址、Fernet 主密钥、服务 API key 等进程启动密钥仍由 systemd 凭证或环境配置管理。

普通配置存放在 `config.runtime_config_items` / `config.runtime_config_revisions`；通用凭证存放在 `config.runtime_config_secrets`。现有 TikTok OAuth 凭证继续只能经 `tts_erp_v2.proxy.token_service` 和 `integration.credentials` 管理。

## 发布模型

- 配置键有一个可变草稿和零到多个不可变已发布 revision。
- 保存草稿和发布都必须携带 `expectedDraftVersion`，冲突返回 `409`，客户端必须重载。
- 页面主流程为“编辑 → 检查并发布 → 确认发布”；“暂存草稿”是中途保存选项。检查不写入，最后一次确认将候选内容直接发布为不可变 revision，不先提交中间草稿。
- 发布会创建递增版本号；历史版本绝不修改或删除。
- 恢复不是倒退版本号：`rollback` 把目标 revision 的内容复制后发布为更高的新版本。
- `GET /v2/config/runtime/snapshot` 只返回每个未归档配置项的已发布默认 payload 与 version，并以版本映射生成 ETag；支持 `If-None-Match`。
- 配置项和 secret 均为软归档：`POST .../retire` 停止使用但保留审计历史，`POST .../restore` 恢复。归档 secret 前服务会拒绝仍被活动配置或草稿引用的操作。
- 配置项归档后其引用不再阻止 secret 归档；此时再恢复配置项会被拒绝（`409`），直到被引用的 secret 重新启用。

## 页面操作与候选发布

列表、配置概览、编辑、检查、历史、新建和凭证管理分别显示。概览默认显示当前生效内容，有草稿时明确提示另有未发布草稿；Schema 默认折叠。暂存不保存发布说明，暂存成功也不会影响当前发布版本。

`POST /v2/config/runtime/items/{config_key}/preview` 用于只读检查：请求包含 `expectedDraftVersion`、显式提供的 `expectedPublishedVersion`（未发布为 `null`）、对象 `payload` 和可选 `sourceRevisionVersion`。返回 `configKey`、两个当前版本、服务端判断的 `hasChanges`、只读规范化文本 `publishedText`/`candidateText` 和历史来源。未发布时 `publishedText=null`。对象键顺序和排版不构成变化，数组顺序保留，布尔与数字严格区分，非有限数字拒绝。

同一个 `/publish` 接口支持候选模式：在上述请求中添加可选 `comment`，服务端持有配置与引用凭证锁，重新核对两个版本、Schema、业务约束和历史 sidecar，一次事务创建 revision。失败不改变原草稿或发布指针。已有发布内容与候选相同时返回 `409`，不创建重复 revision。

旧客户端只发送 `expectedDraftVersion` / `comment` 时，仍发布服务器草稿，普通未知附加字段仍忽略；退役 `rollout` / `draftRollout` 请求字段明确返回 `422`。新模式不能省略 `expectedPublishedVersion`，`payload=null` 非法。旧模式若显式携带发布版本，也必须通过版本检查。

新检查/候选发布的校验错误返回 HTTP `422` 和受控 `detail.code`：`runtime_schema_invalid`、`runtime_business_invalid`、`runtime_secret_unavailable`、`runtime_source_rollout_invalid`、`runtime_number_invalid`。页面映射为可修正的静态提示，不显示原始异常或凭证。旧草稿发布保留原字符串错误契约。

页面“使用历史内容”先带入编辑，允许修复旧字段或凭证引用，再走同一检查发布出口。暂存历史工作副本前也检查来源，防止绕过历史 sidecar 护栏；原 `/rollback` API 仍保留直接重新发布语义。

页面内离开未保存编辑时可暂存、明确放弃或继续编辑；凭证明文和未创建表单不作为普通配置草稿保存。刷新/关闭使用浏览器原生离开确认，不跨刷新恢复明文。网络中断导致写入结果未知时保留输入、要求手动核对并重新检查，不自动重发。发布成功响应已确认但后续重读失败时保留成功事实，读取最新详情后才能继续编辑该项。

“暂存并离开”从历史来源预检查到保存完成期间禁止关闭弹窗、放弃或重复提交；失败原因及未知结果核对在该弹窗内展示。版本冲突时可继续编辑并核对最新内容。只读失败的“重试读取”绑定原读取/检查操作，不重放写入；更改输入或离开相关视图后清除旧重试。核对提交结果只在原配置仍处于当前工作区时刷新该配置，不能跳入此前访问的其他配置。发布成功后的临时内容展示使用服务端已检查的 `candidateText`，刷新失败不撤销成功提示或声称没有写入。

凭证新增与更新入口分开；更新锁定名称并说明对现有引用的影响。底层 PUT 仍是 upsert，页面名称检查不构成服务端只创建不覆盖保证。配置历史不会恢复凭证旧明文。

## 灰度已退役

通用灰度（rollout）曾是发布模型的一部分：有序的基点规则数组 + `subject` 分桶选择。**该能力已退役**：

- 请求体不再接受 `rollout` / `draftRollout`，响应不再返回 `draftRollout` / `publishedRollout` / `rolloutRuleIndex`，页面也不再提供灰度规则编辑器。
- `GET /v2/config/runtime/snapshot` 只返回每个未归档配置项的**已发布默认 payload 与 version**，请求按 `subject` 区分的行为已移除（遗留 `subject` 查询参数被忽略）；ETag 只由版本映射生成。
- `resolve_runtime_config()` / `select_payload()` 等通用 resolver 导出已删除，不恢复通用明文解密入口；受控业务消费者见下方 Secret 引用。
- `config.runtime_config_items.draft_rollout` 与 `config.runtime_config_revisions.rollout` **列与历史数据一律保留**，本仓库不为此新增迁移、也不执行任何 `DROP COLUMN`/`DROP TABLE`。它们只剩两个作用：满足旧表 `NOT NULL DEFAULT '[]'` 定义以保证新写入合法，以及供审计读取。新代码不读写灰度行为；新 revision 固定写 `[]`。
- 保留的读取侧护栏：`analytics.spu_profit_deterioration_alert.v1` 仍会拒绝带非空 rollout sidecar 的历史 revision（fail closed），不会把退役数据当成生效行为。

## Secret 引用

页面/API 只接受和展示 `secret://<name>`，不会回读明文。保存 secret 时使用 `token_service.encrypt()` 加密；列表仅返回引用、截短 fingerprint 和更新时间。HTTP snapshot 同样保持引用；HTTP 端点和通用 resolver 均不返回 secret 明文。受控业务消费者 `tts_erp_v2.plugin.monitor_health.notifications.webhook()` 仅为服务端健康告警读取已发布配置引用的活动 secret，并经 `token_service.decrypt()` 解密使用，不向 HTTP 或插件暴露明文。

先保存 secret，再在配置草稿或 payload 中引用它。发布时服务验证所有引用均存在且未归档；`secret://` 后必须是非空的小写名称（字母、数字、点、下划线或短横线）。

校验与归档**串行化**：发布/恢复会对被引用的 secret 行加锁，与 secret 归档所用的行锁互斥，因此任何交错顺序都不可能发布出一个指向已归档 secret 的 revision。

## 服务端使用

服务端按 `config_key` + `published_version` 直接读取已发布 revision（例如 `tts_erp_v2.analytics.spu_deterioration_alert.read.effective_config_details`）。对外读取一律使用 snapshot 端点。

Monitor 健康告警固定读取 `plugin.monitor_health.v1`，已发布 payload 为 `{"feishuWebhook":"secret://monitor-health-feishu"}`。先通过现有凭证管理保存该命名 secret，再发布配置；secret 明文应为官方 HTTPS 飞书机器人 Webhook。系统不会自动写入真实配置；未发布、已归档、secret 不可用或遗留非空 rollout 均明确失败。插件不持有这份 Webhook，健康上报持久化也不以飞书配置可用为前提。具体行为见[最小探活需求](../plugins/monitor/04-health-reporting-requirements.md)。

## 权限与页面

- 所有 `/v2/config/runtime/*` endpoint：`readwrite`。
- `GET /v2/pages/runtime-configs`：`readwrite`；未满足角色时由浏览器会话跳转至登录页。

页面入口为 Dashboard「运行配置」卡片及侧边栏「基础设置 → 运行配置」。

## Schema

创建配置键时可选择携带固定的 JSON Schema 子集，或省略它并由初始草稿自动推断。推断保留对象字段的已观测类型、同构数组的元素类型及 `secret://` 的 `secret-reference` 格式；对象默认不设 `required` 且允许新增字段，避免首次草稿冻结未来配置。支持 `type`、`properties`、`required`、`additionalProperties`、`items`、`enum`、`format: "secret-reference"` 和基础长度/数值约束。创建、保存草稿、发布前都会校验 payload；标记为 `secret-reference` 的字段只能接收 `secret://<name>`，Schema 创建后不可修改，以保证历史版本可解释。
