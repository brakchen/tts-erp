# 用户账号与页面权限体系设计（v1 · 2026-10）

> 状态：**设计待评审**。评审通过后开始开发。
> 相关文档：`tech-doc/browser-login-design.md`（现状，将被本设计取代浏览器登录部分）、
> `tech-doc/api-key-auth-design.md`（API key 部分保持不变）、`tech-doc/access-policy-module.md`。

## 1. 背景与目标

现状：网站页面登录使用 **API key**（`POST /v2/auth/login` 以 key 换 `tts_session` 会话 cookie）。
API key 是机器凭据，与"人"没有对应关系，无法做人员级的页面访问控制，也存在共享、
转借、泄露后难以定位到人的问题。

目标（v1）：

1. **账号体系**：用户名 + 密码登录；登录、登出、会话管理。
2. **页面级权限**：按 **角色 → 权限点** 控制"这个用户能进什么页面"。
   **权限粒度只到页面**——能进页面即可操作页面内全部功能，不做页面内动作级权限。
3. **不开放自助注册**：登录页不提供任何注册入口；账号只能由管理员在**用户管理页面**
   （§9）或 CLI 创建、禁用、重置密码。
4. **管理界面**：v1 实现 Web 用户管理页（`/v2/pages/users`）：用户列表、创建账号、
   重置密码、启用/禁用、分配角色、会话管理，以及**角色权限配置**（建/改角色、
   勾选角色可进的页面）；仅 `admin` 可见可用。
5. **API 访问不变**：程序化访问（Chrome 扩展、脚本、集成）继续使用 API key +
   `Authorization: Bearer` / `X-API-Key`，现有 `security.api_keys` 与角色矩阵完全不动。

### 1.1 明确不做（v1 范围外）

- 自助注册、找回密码（邮箱/短信）——注册动作收敛在管理页面/CLI，只对管理员开放。
- 页面内操作级权限（按钮/字段级）。
- 第三方登录（OAuth/SSO/LDAP）。
- 多因素认证（MFA）。

## 2. 概念模型

```
用户 user ──< user_roles >── 角色 role ──< role_permissions >── 权限点 permission
                                                                      │
                                                        permission.code = "page:<页面id>"
```

- **权限点（permission）**：只有一种类型 `page`，编码 `page:<page_id>`，与侧边栏
  页面一一对应（`page:dashboard`、`page:manual-costs` …）。权限点是代码常量级别的
  集合，入库只是为了让角色可配置。
- **角色（role）**：命名的权限点集合 + 一个 **API 档位**（`api_tier`，取值
  `readonly|readwrite|admin`，见 §7.2）。预置 3 个内置角色，可另建自定义角色。
- **用户（user）**：用户名 + 密码哈希 + 状态，可挂多个角色；有效权限 = 所有角色的
  并集。

### 2.1 预置角色

| 角色 | 页面权限 | api_tier | 说明 |
| --- | --- | --- | --- |
| `admin` | 全部页面（含 `page:users` 用户管理） | `admin` | 账号管理员、系统管理员 |
| `operator` | 全部业务页面（不含用户管理） | `readwrite` | 日常运营，进页面即可全部操作 |
| `viewer` | 经营分析类页面（dashboard / focused-spus / spu-roi / ad-daily / intercept-stats） | `readonly` | 只看数据，不进配置类页面 |

自定义角色：管理页或 CLI 创建，从权限点清单中勾选页面 + 指定 `api_tier`。

## 3. 交互载体选型：Cookie vs 无状态 Token

> 需求方明确提出要评估"cookie 方式 vs 服务端无状态 token 方式"。结论先行：
> **继续用 Cookie 承载会话，服务端保存会话记录（有状态）**。理由如下。

| 维度 | A. HttpOnly Cookie + 服务端会话表（推荐） | B. 无状态 token（JWT）放 localStorage，fetch 带 `Authorization` |
| --- | --- | --- |
| 页面直达（地址栏/书签/`<a>` 导航） | ✅ 浏览器自动携带 | ❌ 纯 HTML 导航带不上 Authorization 头，仍需兜底方案 |
| XSS 窃取凭据 | ✅ HttpOnly，JS 读不到 | ❌ localStorage 可被 JS 读走 |
| 登出/禁用即刻生效 | ✅ 服务端吊销会话 | ❌ token 到期前一直有效，除非加黑名单（那就不是无状态了） |
| 权限变更生效时机 | ✅ 每请求重查（带短 TTL 缓存） | ❌ 角色改了，旧 token 里还是旧权限 |
| 服务端成本 | 每请求一次会话查询（可缓存，量小可忽略） | 无存储，但验签仍要做 |
| CSRF | 需防护（SameSite=Lax + JSON Content-Type，见 §10） | 天然免疫 |
| 与现有代码契合度 | ✅ 现有 `tts_session` cookie 机制改造即可 | 需要改所有页面 JS 的 fetch 封装 |

- 本系统是**服务端渲染 HTML + 同源 fetch** 的多页应用，不是跨域 SPA/API-first，
  无状态 token 的核心优势（跨域、多端、免会话存储）都用不上；而它的两个硬伤
  （导航带不上、无法即时吊销）恰好都踩在需求上（登录登出权限管理要求**登出和
  禁用立即生效**）。
- 现有 API key 浏览器登录本来就是 cookie 方案（`tts_session`），前端零改动或极少改动。
- **结论：方案 A**。会话凭证是不透明随机 token，只存哈希；服务端 `security.user_sessions`
  表保存会话记录，登出/禁用/改密即吊销。这不是"无状态"，但换来精确的会话生命周期
  控制，且该表体量极小（活跃用户个位数）。

## 4. 数据模型

新增 `security` schema 6 张表（与现有 `security.api_keys` 并列）：

```sql
-- 用户
CREATE TABLE security.users (
    id                bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    username          text NOT NULL,                 -- 登录名，小写归一化，唯一
    display_name      text NOT NULL,
    password_hash     text NOT NULL,                 -- argon2id PHC 字符串
    status            text NOT NULL DEFAULT 'active',-- active | disabled
    created_at        timestamptz NOT NULL DEFAULT now(),
    updated_at        timestamptz NOT NULL DEFAULT now(),
    last_login_at     timestamptz,
    password_changed_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT users_username_key UNIQUE (username),
    CONSTRAINT users_status_check CHECK (status IN ('active', 'disabled'))
);

-- 角色
CREATE TABLE security.roles (
    code        text PRIMARY KEY,                    -- admin | operator | viewer | 自定义
    name        text NOT NULL,
    description text,
    api_tier    text NOT NULL DEFAULT 'readwrite',   -- readonly | readwrite | admin
    is_builtin  boolean NOT NULL DEFAULT false,
    created_at  timestamptz NOT NULL DEFAULT now(),
    updated_at  timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT roles_api_tier_check CHECK (api_tier IN ('readonly', 'readwrite', 'admin'))
);

-- 权限点（页面级）
CREATE TABLE security.permissions (
    code text PRIMARY KEY,                           -- page:<page_id>
    kind text NOT NULL DEFAULT 'page',
    name text NOT NULL,
    CONSTRAINT permissions_kind_check CHECK (kind = 'page')
);

-- 角色 ↔ 权限点
CREATE TABLE security.role_permissions (
    role_code       text NOT NULL REFERENCES security.roles(code) ON DELETE CASCADE,
    permission_code text NOT NULL REFERENCES security.permissions(code) ON DELETE CASCADE,
    PRIMARY KEY (role_code, permission_code)
);

-- 用户 ↔ 角色
CREATE TABLE security.user_roles (
    user_id   bigint NOT NULL REFERENCES security.users(id) ON DELETE CASCADE,
    role_code text   NOT NULL REFERENCES security.roles(code) ON DELETE CASCADE,
    PRIMARY KEY (user_id, role_code)
);

-- 服务端会话（只存 token 哈希）
CREATE TABLE security.user_sessions (
    id           bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    token_hash   bytea NOT NULL,                     -- sha256(会话 token)，唯一
    user_id      bigint NOT NULL REFERENCES security.users(id) ON DELETE CASCADE,
    created_at   timestamptz NOT NULL DEFAULT now(),
    expires_at   timestamptz NOT NULL,
    last_seen_at timestamptz,
    revoked_at   timestamptz,                        -- 登出/禁用/改密时置位
    ip           text,
    user_agent   text,
    CONSTRAINT user_sessions_token_key UNIQUE (token_hash)
);
CREATE INDEX ix_user_sessions_user ON security.user_sessions(user_id);
CREATE INDEX ix_user_sessions_active ON security.user_sessions(expires_at)
    WHERE revoked_at IS NULL;
```

- 密码哈希：**argon2id**（`argon2-cffi`，MIT，PyPI 活跃维护，已在 Python 3.14 验证可装，
  abi3 wheel）。不存明文、不存可逆加密。
- 会话 token：`secrets.token_urlsafe(32)`，cookie 中放明文 token，库里只存
  `sha256(token)`；库泄露也无法直接冒用会话。
- `updated_at` 触发器沿用 `public.fn_touch_updated_at()`（AGENTS.md 不变量）。

## 5. 认证流程

### 5.1 端点（沿用 `/v2/auth/*`，body 变化）

| 端点 | 方法 | auth | 说明 |
| --- | --- | --- | --- |
| `/v2/auth/login` | GET | 公开 | 登录页 HTML（用户名 + 密码表单） |
| `/v2/auth/login` | POST | 公开 | body `{username, password}`；成功 200 + `Set-Cookie`；失败 401 |
| `/v2/auth/logout` | POST | 公开（幂等） | 吊销服务端会话 + 清 cookie，204 |
| `/v2/auth/me` | GET | 公开（自校验 cookie） | `{authenticated, username, displayName, roles, pages, apiTier}` |
| `/v2/auth/change-password` | POST | 登录用户 | body `{oldPassword, newPassword}`；成功后吊销其他会话 |

- 登录响应体不再返回 `role` 明文以外的敏感信息；错误信息统一
  `"用户名或密码错误"`（不区分用户不存在/密码错，防枚举）。
- `next` 跳转沿用现有 open-redirect 防护（只允许同源绝对路径）。
- 登录限流沿用现有 IP 滑动窗口（`TTS_ERP_LOGIN_RATE_LIMIT`，默认 10 次/分）。
  v1 不做账号级锁定；连续失败达到限流后同样返回 429。

### 5.2 会话校验（每请求）

1. 取 `tts_session` cookie → `sha256(token)` 查 `user_sessions`
   （`revoked_at IS NULL AND expires_at > now()`）；
2. 查用户 `status = 'active'`（禁用 → 会话无效）；
3. 载入用户角色与权限点（短 TTL 缓存，沿用现有 auth cache 模式，权限变更最多延迟一个
   缓存 TTL 生效；禁用/登出走 `revoked_at` 即时）；
4. 写入 `request.scope`：`user_id`、`username`、`pages`、`api_tier`，与现有
   `api_key_hash` / `api_key_role` 并存（见 §7）。

会话 cookie 规格：

- 名称沿用 `tts_session`（前缀版本 `v2.` 区分旧格式，旧 cookie 视为无效 → 引导重新登录）；
- 属性：`HttpOnly; Secure; SameSite=Lax; Path=<root_path>`，TTL 沿用
  `TTS_ERP_SESSION_TTL`（默认 12h，固定到期，不滑动续期——内部工具够用且行为可预期）；
- 旧的 API-key-hmac 会话 cookie 一律失效（需求方确认：**不做过渡兼容**）。

## 6. 登出与失效语义

| 事件 | 效果 |
| --- | --- |
| 登出 | 当前会话 `revoked_at = now()` + 清 cookie；其他设备会话保留 |
| 管理员禁用用户 | 该用户**全部**会话即时失效（`revoked_at`） |
| 管理员重置密码 / 用户改密 | 该用户**其他**会话失效（保留当前改密会话，改密场景）；重置密码场景全部失效 |
| 会话到期 | `expires_at` 过后无效，用户重新登录 |

## 7. 权限控制

### 7.1 页面级权限（核心需求）

- 权限点与页面一一对应。**页面路由**（`GET /v2/pages/<id>`）要求会话权限集包含
  `page:<id>`；不包含 → 403 页面（简单提示 + 返回入口链接），不是 302 登录页
  （已登录但无权限 ≠ 未登录）。
- **侧边栏菜单按权限过滤**：无权限的页面不渲染入口（服务端渲染时注入
  `/v2/auth/me` 的 `pages`，或页面模板渲染时过滤，见 §12）。
- **页面内全部操作不设权限点**（需求方确认）：能进页面即可用页面内一切功能。
  页面背后的数据 API 由 §7.2 的 API 档位兜底。

### 7.2 与现有 API 角色矩阵的关系

现有 `tts_erp_v2/access/_policy.py::required_role()`（路由 → 最低角色）**保持不变**，
同时服务两类凭证：

| 凭证 | 身份来源 | 授权依据 |
| --- | --- | --- |
| API key（Bearer / X-API-Key） | `security.api_keys` | `api_keys.role`（现状不变） |
| 用户会话 cookie | `security.users` | 角色的 `api_tier` 取最高档代入 `required_role` 比较 |

即：会话用户带着 `api_tier` 走**同一张路由角色矩阵**。这样写操作（如
`POST /v2/reporting/manual-costs` 要求 `readwrite`）仍受保护，同时满足"进页面即可
操作全部"——预置角色的 `api_tier` 与其可进页面的操作需求对齐（viewer 的页面全是
只读分析页）。

- 自定义角色若勾选了配置类页面，管理页/CLI 会校验并提示 `api_tier` 至少要 `readwrite`。
- `/v2/admin/*` 等 admin 级路由：会话用户需要 `api_tier = admin`（即 `admin` 角色）。

### 7.3 权限点清单（代码常量，一次性）

与 `tts_erp_v2/api/v2/pages.py::_sidebar_html` 的页面列表同源维护：

```
page:dashboard  page:focused-spus  page:spu-roi  page:ad-daily
page:manual-costs  page:shops  page:enum-map  page:runtime-configs
page:sync-jobs  page:intercept-configs  page:intercept-requests
page:intercept-stats  page:users(用户管理, 见 §9.1)
```

新增页面时：`pages.py` 加页面 + 权限点清单加一行 + migration/CLI 补权限点行 + 给
角色授权（内置角色 admin 自动包含全部）。

## 8. 密码策略

- 长度 ≥ **6** 位；
- 必须同时含 **大写字母、小写字母、数字**；**不强制特殊字符**；
- 不限最长长度（上限 128，防 DoS）；允许空格与任意可打印字符；
- 校验在 CLI 创建/重置时执行（同一处代码，`tts_erp_v2/accounts/passwords.py`），
  返回具体不满足的规则；
- 不做定期强制改密、不强制记住历史密码（v1 简化）。

## 9. 用户管理（管理页面 + CLI，无自助注册）

注册/建号入口**只开给管理员**，两条路：Web 管理页面（日常）与 CLI（首次建号、
脚本批量、应急）；两者共用同一套 `tts_erp_v2/accounts/service.py` 服务层与密码策略。
登录页不提供任何注册/找回入口。

### 9.1 Web 用户管理页（`/v2/pages/users`）

- 权限点 `page:users`；预置角色中仅 `admin` 拥有；页面只出现在管理员的侧边栏
  （分组“基础设置”末尾，“用户管理”）。
- 页面功能（v1，表格 + 弹窗表单，风格沿用现有页面）：
  - 用户列表：用户名、显示名、角色、状态、最后登录、活跃会话数；按状态/关键字筛选；
  - **创建账号**（即管理员代注册）：用户名、显示名、初始密码、勾选角色；
    密码策略校验失败时前端逐条提示（与 §8 同源校验）；
  - 重置密码（新密码由管理员输入并线下告知用户；不走邮件）；
  - 启用/禁用（禁用即吊销全部会话）；
  - 调整角色（勾选/取消角色）；
  - 会话管理：查看活跃会话（创建时间、IP、UA、最后活跃），一键吊销单个/全部。
  - **角色权限配置**（第二个页签“角色权限”）：角色列表（名称、`api_tier`、
    可进页面数、引用用户数）；新建/编辑角色（勾选可进页面 + 选 `api_tier`）；
    删除自定义角色（仍被用户引用则拒绝）。
- 配套 API（均需 `page:users`，默认拒绝走 admin 档）：

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/v2/users` | 用户列表（含角色、状态、活跃会话数） |
| POST | `/v2/users` | 创建账号（body: username, displayName, password, roles[]） |
| PATCH | `/v2/users/{id}` | 改显示名/状态/角色（禁用/启用也走这里） |
| POST | `/v2/users/{id}/password` | 重置密码（body: newPassword） |
| GET | `/v2/users/{id}/sessions` | 该用户的活跃会话列表 |
| DELETE | `/v2/users/{id}/sessions/{sid}` | 吊销单个会话；`?all=1` 吊销全部 |
| GET | `/v2/roles` | 角色及权限点清单（创建/编辑表单用） |
| POST | `/v2/roles` | 新建自定义角色（名称、`api_tier`、页面权限点） |
| PATCH | `/v2/roles/{code}` | 编辑角色（名称/`api_tier`/页面权限点） |
| DELETE | `/v2/roles/{code}` | 删除自定义角色（被用户引用则 409） |

- 护栏：禁止禁用/降权**自己**（避免管理员自锁）；禁止删除/禁用**最后一个 admin**；
  内置 `admin` 角色不可编辑/删除（固定全部权限 + admin 档）；内置角色不可删除；
  用户名规则 `^[a-z0-9][a-z0-9_.-]{1,31}$`（创建时小写归一化，重名校验）。
- 审计：创建/重置/禁用/角色变更/会话吊销均写 `login_logger` 结构化日志
  （操作者、目标用户、动作、IP），不记录密码。

### 9.2 CLI（`python -m tts_erp_v2.accounts.cli <command>`）

模块 CLI，便于测试与复用；不是 `scripts/` 下的一次性脚本——这是长期运维入口。
场景：首次部署建第一个 admin、脚本批量建号、管理页不可用时应急。

```
create-user   <username> --name <显示名> --role operator [--role viewer ...]
              # 密码交互式输入（getpass）或 --password-stdin，不走命令行参数（防 ps 泄露）
reset-password <username>                   # 交互式输入新密码；吊销该用户全部会话
set-roles     <username> --role operator ...# 整体替换角色集合
grant-role / revoke-role <username> --role <code>
disable / enable <username>                 # disable 吊销全部会话
list-users [--status active|disabled]
show-user <username>                        # 角色、权限点、活跃会话数、最后登录
revoke-sessions <username> [--all]
create-role <code> --name ... --api-tier readwrite --pages page:dashboard,page:spu-roi
edit-role <code> [--name ...] [--api-tier ...] [--pages ...]
list-roles / show-role <code>
sync-permissions                            # 将代码权限点清单 upsert 进 permissions 表
```

- CLI 与管理页同守 §8 密码策略与用户名规则；同守“最后一个 admin”护栏。
- 角色管理双入口：管理页“角色权限”页签为主，CLI create-role/edit-role 等价可用
  （脚本批量/应急场景）；两者同守护栏与 `api_tier` 校验。

## 10. 安全考量

- **CSRF**（cookie 方案固有风险，轻量防护）：`SameSite=Lax`（跨站 POST 不带 cookie）
  + 所有写操作 API 只接受 `Content-Type: application/json`（HTML 表单跨站提交发不出
  这个类型）；v1 即此双层，不引入 CSRF token。若未来放宽 CORS 再补 double-submit。
- **防枚举**：登录失败信息统一；用户不存在时也走一次 argon2 校验（恒定时间感）。
- **限流**：登录端点 IP 滑动窗口（现有）；其余端点沿用现有共享限流。
- **密码哈希参数**：argon2id 默认参数（m=64MiB, t=3, p=4）或库默认，PHC 串自带参数，
  可平滑升级。
- **审计**：登录成功/失败、登出、禁用、改密、重置密码写入 `login_logger` 结构化日志
  （用户名、结果、IP、会话 id 前缀）；不记录密码与 token 明文。
- **cookie**：HttpOnly + Secure（`TTS_ERP_SESSION_SECURE`，本地 http 开发可置 0）+
  SameSite=Lax + Path=root_path（沿用现状）。
- **传输**：生产经 nginx TLS 终结（现状）。
- **会话表只存哈希**；`last_seen_at` 用于观测，低频更新（≥5 分钟才写一次）。

## 11. API 兼容性（明确不变）

- `security.api_keys`、`Authorization: Bearer` / `X-API-Key`、`required_role()` 路由
  矩阵、`TTS_ERP_AUTH_MODE=off|shadow|enforce`、Chrome 扩展契约
  （`/v2/analytics/sync/*`）**全部不变**。
- 浏览器 API-key 登录入口（`POST /v2/auth/login` 的 `{key}` 形态、登录页 key 表单）
  **移除**，不保留过渡开关。旧 `tts_session`（api-key-hmac 格式）一律视为未登录。
- 混合请求优先级：若同时带 cookie 与 API key，**cookie 优先**（与现状一致），
  API key 作为回退（curl / 扩展场景）。

## 12. 改动文件清单

| 文件 | 改动 |
| --- | --- |
| `tts_erp_v2/accounts/`（新） | `models.py`（users/roles/permissions/…ORM）、`passwords.py`（argon2 + 策略）、`sessions.py`（token 生成/校验/吊销）、`service.py`（登录/登出/授权装载 + 缓存）、`cli.py`（§9 命令） |
| `tts_erp_v2/db/models/security.py` | 增 6 张表 ORM（或全部放 `accounts/models.py`，二选一，倾向后者独立） |
| `alembic/versions/0052_user_accounts.py`（新） | 建表 + 种子（3 内置角色、权限点、admin/operator/viewer 的 role_permissions） |
| `tts_erp_v2/middleware/session_auth.py` | cookie 改为不透明 token v2 格式；校验改查 `user_sessions` |
| `tts_erp_v2/access/` | `_types.py` 增 `UserContext`；`_access.py` 会话分支接入用户凭证；`_policy.py` 不动 |
| `tts_erp_v2/api/v2/auth.py` | 登录页表单（用户名+密码）、login/logout/me/change-password |
| `tts_erp_v2/api/v2/users.py`（新） | §9.1 用户管理 API（/v2/users、/v2/roles） |
| `tts_erp_v2/api/v2/pages.py` | 侧边栏按权限过滤 + 403 页面 + `users` 页面路由 |
| `tts_erp_v2/templates/pages/users.html`、`static/js/users.js`（新） | 用户管理页（用户 + 角色权限两页签） |
| `tests/api/test_user_auth.py`、`tests/accounts/`（新） | 见 §13 |
| `tech-doc/external-api.md`、`architecture-overview.md` | 契约同步 |

**不改动**：`app.py` 中间件注册顺序（AGENTS.md 硬约束）、`access/_policy.py` 路由矩阵、
`proxy/token_service`、Chrome 扩展契约。

## 13. 测试计划

按 `tech-doc/agent-testing.md`，入口 `bash scripts/test_isolated.sh fast`：

1. **accounts 单测**：密码策略（各规则边界）、argon2 哈希/校验、token 生成与哈希、
   会话吊销语义；
2. **登录 API**：成功/失败/用户不存在/禁用用户/限流 429/`next` 校验/cookie 属性；
3. **登出与失效**：登出后旧 cookie 401、禁用即失效、改密吊销他会话、过期失效；
4. **页面权限**：有权限 200 + 侧边栏含入口；无权限 403 + 侧边栏不含入口；
   权限并集（多角色）；自定义角色；
5. **API 档位**：会话用户调写接口（readwrite 路由）按 `api_tier` 放行/403；
   API key 行为回归（现有用例全绿）；
6. **用户管理页**：创建账号后新账号可登录、密码策略拒绝、禁用/会话吊销生效、
   非 admin 403 + 侧边栏无入口、自禁用/最后一个 admin 护栏；
   **角色权限配置**：改角色可进页面 → 对应用户可见页面变化、内置 admin 角色不可
   编辑、删除被引用角色 409；
7. **兼容回归**:API key 两 header 形态、`TTS_ERP_AUTH_MODE` 三态、扩展端点
   `/v2/analytics/sync/*` 现有契约用例不回归。

## 14. 上线步骤

1. 代码合并后 `alembic upgrade`（**生产迁移人工执行**，agent 只在测试形态库验证，
   AGENTS.md §3）；
2. `python -m tts_erp_v2.accounts.cli sync-permissions`（或 migration 自带种子）；
3. CLI 创建首个 `admin` 账号（密码交互输入）；
4. `bash restart.sh` + `systemctl --user restart tts-erp-sync.service`（涉及
   `middleware/`、`api/`）；
5. 验证：登录 → 各页面进出 → 登出 → 旧 cookie 失效；API key 回归一条 curl。

## 15. 未来演进（非 v1）

- 账号级登录锁定、登录审计查询页。
- 页面内动作级权限（若出现"能看不能改"的需求）：权限点扩 `action:*` 类型即可，
  模型无需重构。
