# 访问策略与部署路径适配深模块技术方案

> 状态：**Candidate 02 运行时实现已通过 review 并合并到 `master`**（合并提交 `5fecb28`）。
>
> 架构方案已通过 `c3f9560` 合并；实现位于独立分支
> `redesign/access-policy-implementation`。本轮不改变中间件顺序、数据库 schema 或生产配置。

## 1. 问题定义

当前 `tts_erp_v2/middleware/auth.py::AuthMiddleware.__call__()` 在一个 227 行的
ASGI adapter 中同时拥有：

1. nginx 是否保留 `/tts` 前缀的兼容；
2. `scope["path"]` / `raw_path` 归一化；
3. route-relative path 推导；
4. 端点角色矩阵；
5. cookie、Bearer、`X-API-Key` 凭据优先级；
6. PostgreSQL 查询和正负缓存；
7. `off` / `shadow` / `enforce` 模式；
8. 401、403、503、302 和 429 的选择；
9. 拒绝请求的共享限流；
10. 下游 `scope` 鉴权上下文发布。

结果是部署差异、访问 policy、credential implementation 和 HTTP 展示顺序互相泄漏。
最近的静态文件鉴权、反向代理前缀、Swagger 路径及浏览器重定向修复都集中在该热点，
说明当前 module 缺少 locality。

Candidate 02 的目标不是增加一层 pass-through module，而是形成两个协作的深 module：

- **deployment path module**：纯计算 canonical path；
- **access policy module**：统一 route policy、credential resolution、模式语义和 typed outcome。

`AuthMiddleware` 保持为唯一 ASGI adapter，只负责把 ASGI 请求翻译为 interface 输入，
并把 typed outcome 翻译成 ASGI 调用或响应。

## 2. 强制约束

以下行为是 interface 的组成部分，不得因重构改变：

- 中间件注册相对顺序保持 `RateLimit → Auth → DocsAuth → CORS → AccessLog`；
- 有效请求顺序保持 `AccessLog → CORS → DocsAuth → Auth → RateLimit`；
- 生产 `TTS_ERP_AUTH_MODE=enforce`；
- 角色顺序为 `readonly < readwrite < admin`；
- 未匹配路径默认 `admin`，fail closed；
- `root_path` 只在 `build_app()` 从 `TTS_ERP_EXTERNAL_PREFIX` 读取一次；
- nginx 保留前缀和剥离前缀两种输入都必须得到相同 route-relative path；
- cookie 认证优先于 header；cookie 无效或撤销时允许 header 回退；
- cookie 中的 role 不是授权真相，数据库查询结果才是；
- 浏览器导航和 API 调用保持不同展示：302 或 JSON；
- 正缓存 60 秒、负缓存 20 秒、进程内共享；
- 成功请求由内层 `RateLimitMiddleware` 计数，拒绝请求在 access implementation 内计数；
- 继续发布 `api_key_hash`、`api_key_role`、`api_key_scopes`、`auth_method`；
- destructive guard、CSRF guard 和业务级写入授权不并入本 module；
- PostgreSQL 是 local-substitutable dependency，不创建 repository port；
- 只有一个 ASGI adapter，不创建假想的 adapter interface。

## 3. 依赖分类

| 依赖 | 分类 | 决策 |
| --- | --- | --- |
| path/root_path 计算 | in-process | 纯函数 module，可直接表驱动测试 |
| route role policy | in-process | 私有有序规则表，不暴露通用规则引擎 |
| cookie 验签 | in-process | 继续复用 `session_auth.py` implementation |
| PostgreSQL API key 查询 | local-substitutable | 保留 SQLAlchemy 2 直接实现及测试库，不建 port |
| 正负缓存 | in-process | 收入 access policy implementation |
| denied rate limit | in-process | 复用 `rate_limit.shared_hit()`，不复制 counter |
| ASGI scope/send | adapter concern | 只存在于 `AuthMiddleware` 和 `DocsAuthMiddleware` |

## 4. 已发现的架构与行为问题

### 4.1 P1：shadow 浏览器请求会重定向，而不是只记录后放行

`AuthMiddleware` 先执行 HTML 302 分支，再执行 shadow pass-through。无凭据浏览器 GET
在 shadow 模式下仍会被重定向，违反：

- `external-api.md`：“would-deny is only logged”；
- `browser-login-design.md`：“deny 只记日志”；
- `auth.py` 顶部 module contract。

推荐修复：access decision 必须先解析 mode；shadow 只返回 `ShadowAllow`，不得产生 302、
401、403 或 denied rate-limit。

### 4.2 P1：off/shadow 无法统一绕过 handler-level role gate

`off` 在 middleware 顶层直接放行且不发布 role；shadow 在缺凭据时发布空 role。
`api/deps.py::require_role_at_least()` 只读取 `api_key_role`，因此带 handler gate 的端点仍会
403。这与 “off bypasses auth entirely” 和 “shadow only logs” 不一致。

推荐修复：typed `AccessGrant` 记录 mode/bypass；`require_role_at_least()` 委托同一 role policy，
但 destructive guard 和业务规则保持独立。

### 4.3 P1：浏览器 302 拒绝绕过 denied-request rate limit

HTML redirect 在 `shared_hit()` 之前 return。反复匿名浏览器导航不会计入拒绝请求预算，
与 module 注释“Denied requests are counted”不符。

推荐修复：enforce 下所有 401/403 先命中 denied bucket，再选择 JSON 或 302 展示；预算耗尽
统一返回现有 429。

### 4.4 P1 风险：Docs Basic Auth 没有使用 canonical route path

`DocsAuthMiddleware` 位于 `AuthMiddleware` 外层，并直接比较 `request.url.path` 与
`/docs`、`/openapi.json` 等 exact path。它发生在 Auth 的 prefix normalization 之前。
在 nginx 保留 `/tts` 前缀的输入形态下，存在 `/tts/docs` 未命中 `_PROTECTED` 的风险；
现有测试没有覆盖 prefixed docs auth。

推荐修复：`DocsAuthMiddleware` 调用同一个 deployment path module 取得 `route_path`；
先写 characterization test，确认现状后再替换。

### 4.5 P2：Authorization 优先级依赖 header 顺序

活契约规定同时提供时 `Authorization` 优先于 `X-API-Key`，但 `_extract_key()` 在一次循环中
遇到哪个合法 header 就先返回哪个。当前行为依赖 ASGI header 顺序。

推荐修复：分别收集 Bearer 与 `X-API-Key`，固定 `Authorization > X-API-Key`；新增两种
header 顺序的回归测试。

### 4.6 P2：`/v2/auth/me` 回报 cookie 内旧 role

`/me` 会重新查数据库确认 key 仍有效，但成功后返回 `info["role"]`，而非数据库查询返回的
当前 role。key role 被调整后，middleware 使用新 role，`/me` 仍展示旧 role。

推荐修复：返回数据库 credential role；cookie role 仅保留兼容校验，不作为权限真相。

### 4.7 P2：非法 auth mode 静默表现为近似 enforce

代码只特判 `off` 和 `shadow`，其他值最终走 enforce 分支，没有配置警告。

推荐修复：非法值 fail closed 为 `enforce`，同时记录一次可操作的配置错误日志。

### 4.8 P3：文档与实际 static 契约不一致

`app.py` 注释称 `/static/*` 是 readonly，但 `auth.py` 和测试均将其视为 public exempt。

推荐修复：以现有测试和实现为准，更新注释与活契约，不在本次改变 static 可见性。

## 5. Design It Twice 方案比较

本轮并行启动四个独立设计方向。extension reload 后，最小 interface 与常见调用者优先两条
完整返回；表驱动和安全事故方向的进程在完成代码调查后被中断。结合它们的证据与主审查，
形成以下四种可比较设计。

### 5.1 方案 A：单一 `authorize()` 最小 interface

```python
async def authorize(request: AccessRequest) -> AccessDecision: ...
```

优点：interface 最小，access implementation depth 最大。

缺点：deployment path 若仍是 adapter 私有逻辑，`DocsAuthMiddleware` 继续复制或绕过；登录和
`/me` 仍需额外 credential interface。

结论：保留其 typed decision 思路，不单独采用。

### 5.2 方案 B：公开通用 route policy table

```python
policy = RoutePolicy(rules=[...])
required = policy.match(method, path)
```

优点：角色矩阵可审计，扩展能力强。

缺点：把 matcher、priority、overlap 和 rule schema 暴露给 caller，形成 shallow module；
当前只有一个实现，通用规则引擎没有 leverage。

结论：规则表作为私有 implementation data，不成为 interface。

### 5.3 方案 C：ASGI `RequestFacts → AccessOutcome`

```python
facts = request_facts(scope)
outcome = await decide_access(facts)
apply_context(scope, outcome.context)
```

优点：`AuthMiddleware` caller 最简单，迁移成本低。

缺点：`request_facts()` 和 `apply_context()` 把 ASGI 细节放进 public interface，且 path adaptation
与 access decision 仍在一个 module 中。

结论：适合作为 adapter 私有 helper，不作为外部 seam。

### 5.4 方案 D：两个深 module + 一个薄 adapter（推荐）

```python
canonical = canonicalize_path(path_input)
decision = await evaluate_access(access_request_from(scope, canonical))
```

优点：

- deployment 差异有独立 locality，Auth 与 DocsAuth 共享；
- access policy 对 ASGI 无感；
- typed outcome 消除 redirect/shadow/rate-limit 的控制流顺序错误；
- 私有 route table 保留审计性而不扩大 interface；
- PostgreSQL 与 counter 保持具体 implementation，不制造 port。

缺点：需要两个 interface，而不是一个函数；迁移时必须保留 scope compatibility keys。

结论：**采用方案 D，并吸收方案 A 的 typed decision 与方案 B 的私有规则表。**

## 6. 推荐 module 与 seam

建议新增：

```text
tts_erp_v2/access/
├── __init__.py          # 唯一 public interface
├── _deployment.py      # canonical path implementation
├── _policy.py          # route/mode/role decision implementation
├── _credentials.py     # PostgreSQL + cache implementation
└── _types.py           # immutable types
```

保留：

```text
tts_erp_v2/middleware/auth.py          # 唯一 ASGI adapter
tts_erp_v2/middleware/session_auth.py  # cookie crypto implementation
tts_erp_v2/middleware/rate_limit.py    # counter implementation
```

### 6.1 deployment path interface

```python
@dataclass(frozen=True, slots=True)
class DeploymentPathInput:
    path: str
    raw_path: bytes | None
    root_path: str

@dataclass(frozen=True, slots=True)
class CanonicalPath:
    downstream_path: str
    downstream_raw_path: bytes | None
    route_path: str
    root_path: str


def canonicalize_path(value: DeploymentPathInput) -> CanonicalPath: ...
```

不接收或修改 `scope`，因此是 pure in-process module。ASGI adapter 负责把
`downstream_path` 和 `downstream_raw_path` 写回 scope。

不变量：

- `root_path=""` 时 path 不变；
- path 已带完整 root 时不重复前缀；
- 只有 root boundary 完整匹配时才 strip；
- exact root 映射为 `/`；
- `raw_path` 与 downstream path 同步；
- `route_path` 永远不含 external prefix。

### 6.2 access policy interface

```python
class Role(StrEnum):
    READONLY = "readonly"
    READWRITE = "readwrite"
    ADMIN = "admin"

class AuthMode(StrEnum):
    OFF = "off"
    SHADOW = "shadow"
    ENFORCE = "enforce"

@dataclass(frozen=True, slots=True)
class AccessRequest:
    method: str
    route_path: str
    accepts_html: bool
    client_ip: str
    session_cookie: str | None
    bearer_key: str | None
    api_key: str | None

@dataclass(frozen=True, slots=True)
class AccessGrant:
    mode: AuthMode
    role: Role | None
    key_hash: str | None
    scopes: tuple[str, ...]
    auth_method: Literal["cookie", "bearer"] | None
    bypass: bool = False

    def allows(self, required: Role) -> bool: ...

AccessDecision = Allow | ShadowAllow | Deny | Redirect | Unavailable | RateLimited

async def evaluate_access(
    request: AccessRequest,
    *,
    mode: AuthMode,
) -> AccessDecision: ...
```

`evaluate_access()` 是唯一粗粒度访问决策入口。`AccessDecision` 必须完整携带：

- grant/context；
- required role；
- stable reason；
- HTTP status；
- `WWW-Authenticate` 是否需要；
- denied bucket 结果；
- presentation 为 JSON 还是 browser login。

adapter 不再重新推导 access policy，只负责序列化 decision。

### 6.3 handler-level role gate

`api/deps.py::require_role_at_least()` 保留现有 FastAPI interface，但 implementation 改为读取
`scope["access_grant"]` 并委托 `AccessGrant.allows()`：

- enforce：不足时 403；
- shadow：记录 would-deny 后允许；
- off：允许；
- 无 grant 的异常调用 fail closed。

旧的四个 scope key 同时保留，供 rate limit、access log 和尚未迁移的 caller 使用。

## 7. 私有 route policy implementation

route rules 作为 `_policy.py` 的私有、按优先级排序的 immutable data：

1. exact exempt；
2. prefix exempt（如 `/static/`）；
3. method + exact；
4. method + structured prefix/suffix；
5. readwrite prefixes；
6. readonly exact；
7. readonly prefixes；
8. fallback admin。

module import/test 时验证：

- rule id 唯一；
- role 是已知 enum；
- exact path 以 `/` 开头；
- prefix 顺序由 specificity 决定；
- 不允许同 method、同 matcher、同 priority 的冲突 rule；
- fallback 只能有一个且必须为 admin。

不暴露通用 matcher、registry 或动态插件接口。

## 8. access decision 顺序

推荐固定为以下顺序，并使用表驱动测试锁定：

1. 非 HTTP 由 adapter 直接透传；
2. canonicalize path；
3. 解析 auth mode，非法值记录错误并按 enforce；
4. off → `Allow(bypass=True)`，不查库；
5. classify route；exempt → anonymous `Allow`，不查库；
6. 验 cookie，并用 DB/cache 复查 hash；
7. cookie 无有效 principal 时解析 header，固定 Bearer 优先；
8. DB unavailable：enforce 503；shadow allow + log；
9. 计算 missing/invalid/insufficient would-deny；
10. shadow → `ShadowAllow`，不 redirect、不 denied-limit；
11. enforce denial 先命中 denied bucket；
12. bucket exhausted → 429；
13. 401 + GET + HTML → browser redirect；
14. 其他 denial → JSON；
15. allow → 发布 grant 后进入内层 rate limiter。

## 9. 自动追问记录

以下决策根据用户的持续授权，均采用推荐答案。

| # | 问题 | 推荐答案及理由 | 决策 |
| --- | --- | --- | --- |
| Q1 | Candidate 02 是否只做 `required_role()`？ | 否。只搬函数无法解决 path、mode、credential、presentation 顺序泄漏。 | 采用 |
| Q2 | module seam 放在哪里？ | `tts_erp_v2/access/`，避免把 policy 继续绑定 ASGI middleware。 | 采用 |
| Q3 | 一个 module 还是两个？ | deployment path 与 access policy 两个协作 module，职责和 caller 不同。 | 采用 |
| Q4 | 是否新增 ASGI port？ | 否。只有一个 adapter，port 没有 leverage。 | 采用 |
| Q5 | 是否新增 credential repository port？ | 否。PostgreSQL local-substitutable，直接 SQLAlchemy implementation。 | 采用 |
| Q6 | route rules 是否公开配置？ | 否。私有 immutable table，防止 shallow generic rule engine。 | 采用 |
| Q7 | 未知路径如何处理？ | 继续默认 admin，fail closed。 | 采用 |
| Q8 | handler role gate 是否纳入？ | 纳入同一 Role/AccessGrant 语义；destructive/business guard 不纳入。 | 采用 |
| Q9 | 中间件顺序是否调整？ | 不调整，也不增加改变相对顺序的新 middleware。 | 采用 |
| Q10 | root_path 从哪里读取？ | 只由 `build_app()` 读取 env，其他代码读 scope/input。 | 采用 |
| Q11 | path module 是否修改 scope？ | 不修改；返回 canonical values，由 adapter 应用。 | 采用 |
| Q12 | DocsAuth 是否共享 canonicalizer？ | 是，消除 prefixed docs 分类风险。 | 采用 |
| Q13 | credential 优先级？ | valid cookie > Bearer > X-API-Key；无效 cookie 可回退 header。 | 采用 |
| Q14 | 两种 header 同时存在？ | Bearer 固定优先，不依赖 header 顺序。 | 采用 |
| Q15 | cookie role 是否可信？ | 否，权限与 `/me` 展示均使用 DB/cache 当前 role。 | 采用 |
| Q16 | shadow 语义？ | 所有 would-deny 只记录并允许，不 redirect、不 auth denial limit。 | 采用 |
| Q17 | off 语义？ | 绕过 middleware 与 handler access gate，但 destructive guard 不绕过。 | 采用 |
| Q18 | 非法 mode？ | 记录配置错误并按 enforce，fail closed。 | 采用 |
| Q19 | 浏览器 redirect 何时发生？ | 仅 enforce、401、GET、Accept HTML。 | 采用 |
| Q20 | 302 是否计 denied budget？ | 是；所有 enforce 401/403 先计数，再选择展示。 | 采用 |
| Q21 | store unavailable？ | enforce 503；shadow allow+log；off 不查库。 | 采用 |
| Q22 | static 资源是否改成 readonly？ | 否，保持 public exempt，并修正文档注释。 | 采用 |
| Q23 | scope compatibility keys 是否删除？ | 不删；新增 typed grant，同时保留旧 keys 分阶段迁移。 | 采用 |
| Q24 | 缓存和限流是否分布式化？ | 不做；保持进程内实现，本次只提高 locality。 | 采用 |
| Q25 | 登录与 `/me` 是否强制走完整 decision？ | 不强制；共享 credential implementation，但保留各自错误语义。 | 采用 |
| Q26 | 是否顺手新增 key expiry schema？ | 不做；现有表无 expires_at，不把文案误当 schema 契约。 | 采用 |
| Q27 | 第一轮实现是否允许行为修复？ | 只允许本文列出的契约修复，并逐项先加 characterization test。 | 采用 |
| Q28 | 是否需要 migration/生产操作？ | 不需要；无 schema 变更，无生产重启由 agent 执行。 | 采用 |

## 10. 分阶段实施计划

### Phase 0：characterization tests

在任何移动前新增：

- prefixed/intact/stripped/root-boundary path matrix；
- prefixed Docs Basic Auth；
- route rule exact/prefix/method/fallback admin；
- cookie 与两个 header 的完整优先级矩阵；
- shadow API 与 HTML pass-through；
- off/shadow handler-level gate；
- enforce 401/403/302 denied bucket；
- DB unavailable 三模式；
- invalid mode fail-closed；
- `/me` DB role 更新；
- scope compatibility keys；
- 成功请求不重复计数。

### Phase 1：提取 deployment path module

- 新增纯 types/function；
- Auth adapter 使用 canonical output 并保持 scope mutation；
- DocsAuth 使用相同 route path 分类；
- 不改 middleware registration。

### Phase 2：提取 access policy module

- 移入 Role、route table、credential/cache 和 decision ordering；
- typed decision 替代 `AuthMiddleware.__call__()` 内分支；
- 保留 wire status、body、header 和日志字段。

### Phase 3：统一 handler role gate

- 引入 `AccessGrant`；
- `require_role_at_least()` 委托 grant；
- 保留 legacy scope keys；
- 不改变 destructive guard 和 CSRF guard。

### Phase 4：收尾

- `auth.py` 只保留 ASGI translation/rendering；
- login/me 复用 concrete credential implementation；
- 更新 `external-api.md`、`architecture-overview.md`、`browser-login-design.md`；
- 删除被深 module interface 测试取代的 implementation-detail tests。

## 11. 验证门禁

实现阶段必须：

1. 先跑 auth/middleware 窄测试；
2. 使用 `flock -n /tmp/tts-erp-test.lock bash scripts/test.sh fast`；
3. 与 implementation 前 master baseline 比较，新增稳定失败必须为 0；
4. LSP 0 diagnostics；
5. `git diff --check` 通过；
6. reviewer 逐项验证本文 P1/P2；
7. 不运行生产 smoke、restart 或生产数据库操作；
8. 用户 review 批准前不得 merge。

## 12. 删除测试

如果删除推荐 module：

- path/root_path 规则会重新散回 Auth、DocsAuth 和测试 fixture；
- route role/mode/credential 顺序会重新散回 middleware 与 handler helper；
- redirect、denial limit 和 scope publishing 的先后关系会重新成为调用者知识。

因此该 module 不是 pass-through；它通过小 interface 隐藏高风险访问语义，能为 caller 提供
leverage，并将事故修复集中到一个 locality 清晰的 implementation。

## 13. Implementation 落地结果

实际实现与本方案一致：

- `tts_erp_v2/access/_deployment.py`：纯 `canonicalize_path()`；
- `tts_erp_v2/access/_policy.py`：私有 route role table 与 fail-closed fallback；
- `tts_erp_v2/access/_credentials.py`：SQLAlchemy credential lookup 与 60s/20s cache；
- `tts_erp_v2/access/_access.py`：typed `evaluate_access()` 与完整 decision ordering；
- `tts_erp_v2/middleware/auth.py`：缩减为 ASGI translation/rendering adapter；
- `tts_erp_v2/api/deps.py`：handler gate 委托 `AccessGrant`；
- `tts_erp_v2/api/v2/auth.py`：login/me 复用 credential implementation，`/me` 返回 DB 当前 role。

已落地本文批准的契约修复：shadow 真正 pass-through、off/shadow handler gate 一致、
browser denial 纳入限流、Bearer 固定优先、invalid mode fail closed、prefixed Docs Basic Auth、
`/me` 使用数据库当前 role。最终测试和 reviewer 结论记录在
[`access-policy-implementation-review.md`](../archive/access-policy-implementation-review.md)。
