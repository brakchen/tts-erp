# Candidate 02：访问策略深模块——实现审阅报告

> 审阅状态：**reviewer PASS / Merge verdict OK；已合并到 `master`**（合并提交 `5fecb28`）
>
> 分支：`redesign/access-policy-implementation`
>
> 工作树：`.worktrees/access-policy-implementation`
>
> 设计方案：[`access-policy-module.md`](access-policy-module.md)

## 1. 实现结论

Candidate 02 已按批准方案完成：

- deployment path 形成纯 in-process module；
- access policy、route role、credential/cache 和 access decision 集中到一个深 module；
- `AuthMiddleware` 从约 630 行缩减到约 230 行，只保留 ASGI translation/rendering 与兼容导出；
- PostgreSQL 保持 concrete local-substitutable implementation，没有制造 repository port；
- 中间件注册顺序未改变；
- handler-level role gate 与 middleware 共享 typed `AccessGrant`；
- 设计阶段发现的七项契约偏差均已修复并补测试。

## 2. Public interface

`tts_erp_v2/access/__init__.py` 暴露：

```python
canonicalize_path(DeploymentPathInput) -> CanonicalPath

evaluate_access(
    request: AccessRequest,
    *,
    mode: AuthMode,
) -> AccessDecision
```

以及 immutable types：

- `Role`
- `AuthMode`
- `AccessRequest`
- `AccessGrant`
- `AccessDecision`
- `AccessEffect`
- `Credential`

credential lookup 函数保持 concrete implementation；route table 仍为私有 implementation data，
没有开放动态 registry。

## 3. Module 分工

### `tts_erp_v2/access/_deployment.py`

- 兼容 nginx 保留或剥离 `/tts` 的两种输入；
- 同时返回 downstream path、raw path 和 route-relative path；
- 不读取环境变量，不修改 ASGI scope。

### `tts_erp_v2/access/_policy.py`

- 集中 exact/prefix/method route rules；
- 保持 `readonly < readwrite < admin`；
- unknown path 默认 admin；
- static 和约定 public route 保持 exempt。

### `tts_erp_v2/access/_credentials.py`

- 直接使用 SQLAlchemy 2 和 `security.api_keys`；
- positive cache 60 秒，negative cache 20 秒；
- plaintext 与 cookie hash 共享同一 cache；
- unknown/disabled/unknown-role credential 返回 invalid；
- DB 异常继续由 access decision 映射。

### `tts_erp_v2/access/_access.py`

固定 decision ordering：

1. mode 与 route policy；
2. cookie 验签和 DB 复查；
3. Bearer，再 `X-API-Key`；
4. role decision；
5. shadow/off bypass；
6. enforce denied budget；
7. 429 / browser 302 / JSON denial。

### `tts_erp_v2/middleware/auth.py`

唯一 ASGI adapter：

- scope → public interface values；
- 应用 canonical downstream path；
- 发布 `access_grant` 和旧 scope compatibility keys；
- typed decision → ASGI response / downstream call。

## 4. 已修复的契约偏差

### 4.1 Shadow HTML 真正 pass-through

旧行为先返回 302，再判断 shadow。现在 shadow 返回 `ShadowAllow`，只记日志。

### 4.2 Off/Shadow handler role gate 一致

`api/deps.py::require_role_at_least()` 使用 `AccessGrant.allows()`：

- off：bypass；
- shadow：bypass并保留 would-deny 日志；
- enforce：按数据库当前 role 判断；
- 没经过 middleware 的异常调用仍 fail closed。

Destructive guard、CSRF guard 与业务规则没有放宽。

### 4.3 Browser denial 进入共享限流

所有 enforce 401/403 在选择 302 或 JSON 前调用共享 denied counter。预算耗尽返回现有 429。

### 4.4 Header precedence 固定

同时提供时固定：

```text
valid cookie > Authorization Bearer > X-API-Key
```

不再依赖 ASGI header 顺序；invalid/revoked cookie 仍允许 header fallback。

### 4.5 Prefixed Docs Basic Auth

`DocsAuthMiddleware` 使用同一个 `canonicalize_path()`，`/tts/docs` 与 `/docs` 均能命中
Basic Auth protected paths。

### 4.6 Invalid auth mode fail closed

非法 `TTS_ERP_AUTH_MODE` 记录配置错误并按 `enforce` 处理。

### 4.7 `/v2/auth/me` 使用数据库当前 role

cookie payload 中 role 不再作为展示真相；role 调整并清除 cache 后，`/me` 与 middleware
都会反映数据库当前值。

## 5. 主要文件

新增：

- `tts_erp_v2/access/__init__.py`
- `tts_erp_v2/access/_types.py`
- `tts_erp_v2/access/_deployment.py`
- `tts_erp_v2/access/_policy.py`
- `tts_erp_v2/access/_credentials.py`
- `tts_erp_v2/access/_access.py`
- `tests/access/test_deployment.py`
- `tests/access/test_policy.py`

修改：

- `tts_erp_v2/middleware/auth.py`
- `tts_erp_v2/middleware/session_auth.py`
- `tts_erp_v2/api/deps.py`
- `tts_erp_v2/api/v2/auth.py`
- `tts_erp_v2/app.py`
- `tests/api/test_middleware.py`
- `tests/api/test_auth_login.py`
- 鉴权活契约和架构文档。

## 6. Test evidence

### 实施前窄测试基线

```bash
bash scripts/test.sh fast \
  tests/api/test_middleware.py \
  tests/api/test_auth_login.py
```

结果：**40 passed**。

### 实施后窄测试

```bash
bash scripts/test.sh fast \
  tests/api/test_middleware.py \
  tests/api/test_auth_login.py \
  tests/access/test_deployment.py \
  tests/access/test_policy.py
```

结果：**62 passed**。

覆盖：

- prefix intact/stripped/root-boundary；
- Unicode path 与 percent-encoded `raw_path` 原样保持；
- prefixed Docs Basic Auth 拒绝与成功认证；
- unknown route admin fallback；
- real test-DB bearer credential；
- store unavailable enforce/shadow；
- shadow HTML pass-through 且不消耗 denied budget；
- off/shadow handler gate；
- valid cookie 优先和 invalid cookie header fallback；
- Bearer 两种 header 顺序均优先；
- browser 401 与 insufficient-role 403 denied budget；
- invalid mode fail closed；
- `/me` current DB role；
- cookie tamper/expiry/revocation；
- 原有 401/403/302/rate-limit 行为。

### Fast suite 精确差异

在相同 master revision 和共享 DB lock 下：

- master：18 个既有失败；
- implementation branch：10 个失败；
- **新增失败：0**；
- 8 个 OpenAPI 基线失败在本分支未复现，不作为本 lane 的目标或承诺；
- merge 后 `master`：18 个失败，与 merge 前 baseline 相同，新增失败 0。

### Reviewer 修复闭环

独立 reviewer 提出三项：

1. `raw_path` 不应由 decoded path 重新编码，否则 Unicode 会异常且 percent-encoding 会丢失；
2. runtime caller 必须通过 `tts_erp_v2.access` public interface，不应导入私有 implementation；
3. 补齐 cookie/header、两种 header 顺序、Docs Basic success、shadow/403 budget 测试矩阵。

三项均已修复并通过对应回归测试。复审结论：**P0–P3 无剩余问题，PASS / Merge verdict OK**。

### 静态验证

- 相关 Python LSP：0 diagnostics；
- Ruff：通过；
- `py_compile`：通过；
- `git diff --check`：通过。

## 7. Git 提交

分支 rebase 后的主要提交：

- `97364d9`：提取 deployment path module；
- `69563ec`：提取 access policy 深模块；
- `b7171af`：统一 access mode 与 handler role gate；
- `e3cc368`：类型与 lint 收尾；
- `c2c2e38`：更新现行鉴权契约；
- `bcd80dd`：完成 reviewer 修复与本审阅报告；
- `c8a447a`：记录用户合并批准。


## 8. 兼容性与明确变化

保持不变：

- middleware registration order；
- 角色顺序和 unknown→admin；
- API key / cookie / CSRF / destructive guard；
- HTTP status、JSON detail 和 `WWW-Authenticate`；
- redirect URL 与 route-relative `next`；
- cache TTL 和 per-process limiter；
- scope compatibility keys；
- public static assets。

有意变化：

- shadow 不再 302/401/403；
- off/shadow 也作用于 handler role gate；
- browser denial 可能在预算耗尽时返回 429；
- Bearer 总是优先于 `X-API-Key`；
- `/me` 返回数据库当前 role；
- prefixed docs 不再绕过 Basic Auth；
- invalid auth mode 有日志并 fail closed。

## 9. 剩余风险

- cache 和 limiter 仍是 per-process，不提供跨 worker 一致性；
- handler 仍可新增 destructive/business guard，这些 guard 不属于 access policy；
- route table 与外部契约仍需同步维护；
- 302 纳入 denied budget 是有意的安全变化，运营端高频未登录刷新可能看到 429；
- compatibility exports 暂留在 `middleware/auth.py`，后续 caller 全部迁移后可独立清理。

## 10. 用户 Review 结论

用户已确认并批准：

1. 两个深 module 和薄 ASGI adapter 的落地形状；
2. 七项有意行为修复；
3. `AccessGrant` 同时服务 middleware 与 handler gate；
4. 302 纳入 denied budget 后可能返回 429；
5. 合并到 `master`。

本报告记录的 implementation 已通过 `5fecb28` 合并到 `master`。
