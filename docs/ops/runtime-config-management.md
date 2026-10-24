# 运行配置与凭证管理

## 目的与边界

运行配置用于把可安全热更新的 JSON 下发给本服务内的调用方，例如功能开关、阈值和上游端点选择。它不是 `.env` 替代品：数据库地址、Fernet 主密钥、服务 API key 等进程启动密钥仍由 systemd 凭证或环境配置管理。

普通配置存放在 `config.runtime_config_items` / `config.runtime_config_revisions`；通用凭证存放在 `config.runtime_config_secrets`。现有 TikTok OAuth 凭证继续只能经 `tts_erp_v2.proxy.token_service` 和 `integration.credentials` 管理。

## 发布模型

- 配置键有一个可变草稿和零到多个不可变已发布 revision。
- 保存草稿和发布都必须携带 `expectedDraftVersion`，冲突返回 `409`，客户端必须重载。
- 发布会创建递增版本号；历史版本绝不修改或删除。
- 恢复不是倒退版本号：`rollback` 把目标 revision 的内容复制后发布为更高的新版本。
- `GET /v2/config/runtime/snapshot` 只返回每个未归档配置项的已发布默认 payload 与 version，并以版本映射生成 ETag；支持 `If-None-Match`。
- 配置项和 secret 均为软归档：`POST .../retire` 停止使用但保留审计历史，`POST .../restore` 恢复。归档 secret 前服务会拒绝仍被活动配置或草稿引用的操作。
- 配置项归档后其引用不再阻止 secret 归档；此时再恢复配置项会被拒绝（`409`），直到被引用的 secret 重新启用。

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
