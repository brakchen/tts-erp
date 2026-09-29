# Candidate 02：访问策略与部署路径适配——架构审阅报告

> 审阅状态：**等待用户确认；未实施，未合并到 `master`**
>
> 分支：`redesign/access-policy-module`
>
> 工作树：`.worktrees/access-policy-module`
>
> 详细技术方案：[`access-policy-module.md`](access-policy-module.md)

## 1. 结论摘要

Candidate 02 值得实施，优先级为 **Strong**。

当前最大问题不是 `auth.py` 文件较长，而是一个 ASGI adapter 同时拥有部署路径、route role、
credential、mode、redirect、denied limit 和 response rendering。控制流顺序已经产生实际或高风险
契约偏差。

推荐方案：

1. 新建纯 `deployment path module`，统一生成 canonical route path；
2. 新建 `access policy module`，以 typed request/decision 统一访问语义；
3. `AuthMiddleware` 保持唯一 ASGI adapter；
4. route table 保持私有，不开放动态 registry；
5. PostgreSQL 保持 concrete local-substitutable implementation，不创建 repository port；
6. handler-level `require_role_at_least()` 委托同一 `AccessGrant` 语义；
7. 中间件注册顺序完全不变。

本轮只完成审阅和设计，没有修改任何运行时代码。

## 2. 审阅范围

已检查：

- `tts_erp_v2/middleware/auth.py`
- `tts_erp_v2/middleware/session_auth.py`
- `tts_erp_v2/middleware/rate_limit.py`
- `tts_erp_v2/middleware/access_log.py`
- `tts_erp_v2/app.py`
- `tts_erp_v2/api/deps.py`
- `tts_erp_v2/api/v2/auth.py`
- `tests/api/test_middleware.py`
- `tests/api/test_auth_login.py`
- `tech-doc/agent-safety.md`
- `tech-doc/architecture-overview.md`
- `tech-doc/browser-login-design.md`
- `tech-doc/external-api.md` 的鉴权契约

同时检查了近期开关、静态资源、前缀、Swagger 与 OAuth role 修复历史。

## 3. 主要发现

### P1：shadow HTML 请求会 302，而不是 pass-through

redirect 分支位于 shadow 分支之前。当前代码与 shadow 活契约冲突。

**建议**：typed decision 先完成 mode decision；shadow 永远返回 `ShadowAllow`。

### P1：off/shadow 与 handler role gate 语义不一致

middleware 会放行，但 `require_role_at_least()` 仍可能因 scope role 为空返回 403。

**建议**：发布 typed `AccessGrant`，handler helper 委托同一 mode/role policy。

### P1：浏览器 redirect 不计 denied-request rate limit

302 在 `shared_hit()` 之前返回，浏览器匿名尝试绕过拒绝预算。

**建议**：enforce 的所有 401/403 先计 denied bucket，再选择 302 或 JSON。

### P1 风险：prefixed Docs Basic Auth 未使用 canonical path

DocsAuth 位于 Auth 外层并使用 exact `request.url.path`；当前没有 `/tts/docs` 形态的鉴权测试。

**建议**：先加 characterization test，再让 DocsAuth 复用 deployment path module。

### P2：Bearer 优先级依赖 ASGI header 顺序

文档声明 `Authorization` 优先，但 `_extract_key()` 返回第一个遇到的合法 header。

**建议**：固定 Bearer > `X-API-Key`，覆盖两种 header 顺序。

### P2：`/v2/auth/me` 可能回报旧 role

接口确认 DB key 有效后仍返回 cookie payload 中的 role，而 middleware 使用 DB role。

**建议**：统一使用 credential store 返回的当前 role。

### P2：非法 auth mode 无配置诊断

除 `off`/`shadow` 外的值静默进入近似 enforce 流程。

**建议**：记录配置错误并 fail closed 为 enforce。

### P3：static 注释与实际契约不一致

实际实现和测试是 public exempt，`app.py` 注释仍称 readonly。

**建议**：保留 public 行为，只修正文档。

## 4. 推荐 interface

### 4.1 部署路径

```python
canonicalize_path(
    value: DeploymentPathInput,
) -> CanonicalPath
```

输入/输出均为 immutable value，不直接接触 ASGI scope。

### 4.2 访问决策

```python
async def evaluate_access(
    request: AccessRequest,
    *,
    mode: AuthMode,
) -> AccessDecision
```

`AccessDecision` 为：

- `Allow`
- `ShadowAllow`
- `Deny`
- `Redirect`
- `Unavailable`
- `RateLimited`

成功结果携带 `AccessGrant`；handler-level gate 使用同一 grant 判定。

### 4.3 adapter

`AuthMiddleware` 只做：

1. ASGI scope → input values；
2. 应用 canonical path 到 scope；
3. 调用 `evaluate_access()`；
4. 发布 grant/兼容 scope keys；
5. allow 时进入下游，其他 decision 序列化为现有 HTTP response。

## 5. 方案比较结果

| 方案 | depth | locality | 主要问题 | 结论 |
| --- | --- | --- | --- | --- |
| 单一 `authorize()` | 高 | policy 高 | deployment path 仍散落 | 吸收 typed decision |
| 公开通用规则表 | 中 | role 高 | interface 过大、规则引擎 shallow | 规则表改为私有 |
| ASGI RequestFacts helpers | 中 | adapter 高 | ASGI 泄漏到 public interface | 只作 adapter 私有 helper |
| 两个深 module + 薄 adapter | 高 | path/policy 均高 | 迁移需保留 scope compatibility | **推荐** |

## 6. 兼容性承诺

实现时保持：

- `readonly < readwrite < admin`；
- unknown route → admin；
- cookie-first 与 header fallback；
- 401/403/503/429 现有稳定 JSON；
- enforce 浏览器 302；
- `root_path` 单源与 `/tts` redirect 形状；
- positive/negative cache TTL；
- scope keys；
- successful request rate-limit；
- public static assets；
- CORS、CSRF、destructive guard；
- middleware registration order。

明确允许的契约修复只有：

1. shadow 真正 pass-through；
2. off/shadow handler gate 与模式一致；
3. browser denial 纳入共享拒绝预算；
4. Bearer 优先级不依赖 header 顺序；
5. `/me` 使用 DB 当前 role；
6. invalid mode 记录错误并 fail closed；
7. DocsAuth 使用 canonical path（以 characterization test 为前提）。

## 7. 测试与实施门禁

实施必须分阶段：

1. characterization tests；
2. deployment path module；
3. access policy module；
4. handler role gate 统一；
5. 文档与旧 test 清理。

重点新增矩阵：

- prefix intact/stripped/root boundary；
- prefixed docs basic auth；
- exact/prefix/method/fallback role；
- cookie/Bearer/X-API-Key 优先级；
- off/shadow/enforce；
- 302/401/403/429 顺序；
- DB unavailable；
- handler role gate；
- scope compatibility；
- `/me` role 更新。

代码阶段必须运行 auth 窄测试和 locked fast suite，并要求新增稳定失败为 0。

## 8. Design It Twice 执行说明

本轮并行启动四条只读设计 lane：

- 最小 interface；
- 私有/公开 policy table；
- 常见 caller 优先；
- 安全事故优先。

其中两条完整返回，另外两条在 extension reload 时被停止。已返回的两条独立指出相同的
shadow redirect、browser denied-limit 与 header precedence 问题；主审查结合代码事实完成了
表驱动和安全方案的比较。所有 agent 均未写文件。

## 9. 过期文档清理

同一分支已修正：

- `order-dump-intake-module.md`：改为已审阅并合并；
- `order-dump-intake-module-review.md`：记录批准和 merge commits；
- `dumps-tts-erp-review.md`：标记为历史审阅记录，并清理遗留 Git 冲突标记。

## 10. 剩余风险

- path/raw_path 迁移错误会导致 StaticFiles 404 或 redirect loop；
- private rule table 若排序错误可能降低角色要求；
- shadow/off handler gate 修复属于可观察行为变化；
- 302 开始返回 429 属于有意的安全行为变化；
- 当前 limiter/cache 仍是 per-process，不提供跨 worker 一致性；
- role matrix 与 `external-api.md` 仍需文档同步测试，否则可能再次漂移。

## 11. 用户 review 清单

请确认：

1. 是否采用“deployment path module + access policy module + 单一 ASGI adapter”；
2. 是否批准修复本文列出的 7 项契约偏差；
3. 是否认可 route table 只作为私有 implementation，不开放 registry；
4. 是否认可 PostgreSQL 继续作为 concrete local-substitutable dependency；
5. 是否认可 handler-level role gate 纳入同一 `AccessGrant`，但 destructive/business guard 保持独立；
6. 是否批准进入 Candidate 02 implementation 阶段。

在用户明确批准前，本分支只包含文档，**不会实现或合并 Candidate 02**。
