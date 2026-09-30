# 运行配置与凭证管理

## 目的与边界

运行配置用于把可安全热更新的 JSON 下发给本服务内的调用方，例如功能开关、阈值和上游端点选择。它不是 `.env` 替代品：数据库地址、Fernet 主密钥、服务 API key 等进程启动密钥仍由 systemd 凭证或环境配置管理。

普通配置存放在 `config.runtime_config_items` / `config.runtime_config_revisions`；通用凭证存放在 `config.runtime_config_secrets`。现有 TikTok OAuth 凭证继续只能经 `tts_erp_v2.proxy.token_service` 和 `integration.credentials` 管理。

## 发布模型

- 配置键有一个可变草稿和零到多个不可变已发布 revision。
- 保存草稿和发布都必须携带 `expectedDraftVersion`，冲突返回 `409`，客户端必须重载。
- 发布会创建递增版本号；历史版本绝不修改或删除。
- 恢复不是倒退版本号：`rollback` 把目标 revision 的内容复制后发布为更高的新版本。
- `GET /v2/config/runtime/snapshot` 只返回已发布配置，并以版本映射生成 ETag；支持 `If-None-Match`。

## Schema 与灰度

每个配置键创建时携带一个固定的 JSON Schema 子集。支持 `type`、`properties`、`required`、`additionalProperties`、`items`、`enum`、`format: "secret-reference"` 和基础长度/数值约束。创建、保存草稿、发布前都会校验 payload；标记为 `secret-reference` 的字段只能接收 `secret://<name>`，Schema 不可修改，以保证历史版本可解释。

灰度规则是有序数组：

```json
[
  {
    "name": "canary",
    "basisPoints": 500,
    "payload": {"enabled": true}
  }
]
```

调用方以 `sha256(rolloutSalt + ":" + configKey + ":" + subject) % 10000` 计算稳定桶位。规则按顺序首个命中生效；桶位不含版本号，因此发布新版本不会把同一 subject 重新分桶。

## Secret 引用

页面/API 只接受和展示 `secret://<name>`，不会回读明文。保存 secret 时使用 `token_service.encrypt()` 加密；列表仅返回引用、截短 fingerprint 和更新时间。HTTP snapshot 同样保持引用；只有服务进程内的 `resolve_runtime_config()` 能通过 `token_service.decrypt()` 解出值，且不得将结果写入日志、响应、revision 或浏览器 DOM。

先保存 secret，再在配置草稿或灰度 payload 中引用它。发布时服务验证所有引用均存在。

## 权限与页面

- `GET /v2/config/runtime/items`、`GET /v2/config/runtime/snapshot`：`readonly`。
- 草稿、发布、回滚、revision 详情和 secret 元数据/写入：`readwrite`。
- `GET /v2/pages/runtime-configs`：`readonly`；只读会话可见发布状态，`readwrite` 才可编辑、发布和管理 secret。

页面入口为 Dashboard「运行配置」卡片及侧边栏「基础设置 → 运行配置」。

## 服务端使用

服务端代码必须显式提供稳定 rollout salt 和 subject：

```python
from tts_erp_v2.runtime_config import resolve_runtime_config

resolved = resolve_runtime_config(
    session,
    config_key="feature.checkout",
    rollout_salt=rollout_salt,
    subject=shop_id,
)
```

不得把 `resolve_runtime_config()` 结果直接作为 HTTP 响应返回。对外读取一律使用 snapshot 端点。
