# Browser 登录页设计（v2 · 2026 版）

> 状态：**待评审**。目标：让运营同学能用浏览器打开 `manual-costs` 页面并填写成本，
> 而不是收到 401 JSON。设计原则：**API key 系统仍是唯一凭证源，不引入用户/密码账号体系**；
> 登录页只是把「API key」兑换成「浏览器会话 cookie」的一层薄封装。

## 1. 背景与问题（已实测验证）

访问 `https://daqiang.nat100.top/tts/v2/pages/manual-costs?shop_id=7494763368967603447`：

1. **页面本身被 401 挡住（鸡生蛋问题）**
   NAT 隧道把 `/tts/` 前缀 strip 掉后，应用收到 `GET /v2/pages/manual-costs`。
   `tts_erp_v2/middleware/auth.py` 的 `required_role()` 把 `/v2/pages/` 归为
   `readonly`，且 `TTS_ERP_AUTH_MODE=enforce` → 无 token 直接 401
   `{"detail":"missing bearer token ..."}`。浏览器普通导航无法携带
   `Authorization` header，所以**现有页面里「粘贴 token 到 localStorage」的流程
   永远走不到第一步**。现有 `test_pages.py::test_manual_costs_page_requires_some_auth`
   就是断言这个 401 的。

2. **页面 JS 的 fetch 路径在生产外网是 404（已实测）**
   页面内 `fetch("/v2/reporting/missing-cost-products")` 是**根绝对路径**。
   外网只有 `/tts/*` 被映射到 tts-erp:9877，`https://daqiang.nat100.top/v2/...`
   实测返回 **404（另一个站点 ProfitLens 的页面）**。即使登录打通，数据也拉不到。

3. **UX 差**：让运营粘贴裸 API key 不现实（key 只创建时打印一次、CLI 管理）。
   且 key 明文进 localStorage，被 XSS 可读。

## 2. 方案总览

```
浏览器导航 /v2/pages/manual-costs（无 cookie）
        │  Accept: text/html → 302
        ▼
GET /v2/auth/login?next=<原路径>      ← 公开（免 auth），简单 HTML 表单
        │  输入 API key → POST /v2/auth/login
        │  服务端 lookup_role(key) 校验 → 签发 HttpOnly 会话 cookie
        ▼
302/JS 跳回 next（manual-costs 页）
        │  之后所有请求（页面导航 + fetch）自动带 cookie
        ▼
AuthMiddleware：优先验会话 cookie（key_hash → DB 复查仍有效）→ 通过
```

要点：

- **不新建用户体系**。登录 = 输入一个 API key；服务端用现有
  `lookup_role()`（`security.api_keys` 表）校验，签发 HMAC 签名会话 cookie。
- **cookie 里只放 key 的 SHA-256 哈希，不放明文 key**；且每次请求仍回 DB
  复查该哈希是否有效 → **revoke 即会话失效**（缓存 TTL 内）。
- **页面壳公开、数据仍保护**：只有 `/v2/auth/login|logout|me` 免 auth；
  `/v2/pages/*` GET 依然要鉴权，只是浏览器被 302 引导去登录而不是看 JSON。
- **顺手修掉 prefix 404 bug**：页面 JS 从 `location.pathname` 推导 API base。

## 3. 端点设计

| 端点 | 方法 | auth | 说明 |
| --- | --- | --- | --- |
| `/v2/auth/login` | GET | 免 | 返回登录页 HTML（简单表单） |
| `/v2/auth/login` | POST | 免 | body `{key, next?}`；校验失败 401；成功 200 + `Set-Cookie` |
| `/v2/auth/logout` | POST | 免 | 清 cookie，204 |
| `/v2/auth/me` | GET | 免（handler 自行验 cookie） | `{authenticated, role}`，页面用它判断登录态 |

`next` 校验：只允许以 `/` 开头、不以 `//` 开头、不含 `\`（防 open redirect），
默认回 `/v2/pages/manual-costs`。

## 4. 会话 cookie 规格

- 名称：`tts_session`
- 内容（HMAC 签名、非加密）：`base64url(payload).sig`，
  `payload = {kh: <sha256(key)>, role: <role>, exp: <unix ts>}`
- 签名：`HMAC-SHA256(TTS_ERP_SESSION_SECRET, payload)`（新增 env，`openssl rand -hex 32` 一次生成，0600）
- 属性：`HttpOnly; Secure; SameSite=Lax; Path=/; Max-Age=43200`
  - `Secure` 通过 env `TTS_ERP_SESSION_SECURE=0` 可关（本地 `:9877` http 调试用）
  - TTL 12h **固定**（实现简化：中间件不拦截响应重签 cookie，无滑动续期；运营每天重新登录一次可接受）
- 会话内不存明文 key；`kh` 在请求时回 DB（现有缓存 lookup）复查
  `enabled/expires_at` → key 被 disable/过期 → 会话立即失效

## 5. 中间件改动（`tts_erp_v2/middleware/auth.py`）

1. `required_role()`：
   - `/v2/auth/login`、`/v2/auth/logout`、`/v2/auth/me` → `None`（免 auth）
   - 其余不变
2. `__call__` 流程调整（认证来源优先级：**cookie 会话 > header key**）：
   - 读 `tts_session` cookie → 验签 + 验 exp → 得 `kh/role`
   - 调新增的 `lookup_role_by_hash(kh)`（把现有 `lookup_role(key)` 拆成
     `lookup_role_by_hash(sha256(key))`，共用同一个 `_cache`，key 就是 hash）——
     DB 复查仍有效才通过，并把 `scope["api_key_hash"]/["api_key_role"]`
     照常写入（rate limit 按 kh 计桶，与 API 调用共享预算）
   - 无 cookie 或无效 → 回退现有 header 逻辑
3. **浏览器 302 重定向**：deny 分支里，若请求 `Accept` 含 `text/html`
   （浏览器导航特征）→ 302 到 `/v2/auth/login?next=<path+query>`；
   否则（curl / fetch，`Accept: */*` 或 json）→ 保持现有 JSON 401。
   - 注意：fetch 默认 `Accept: */*`，不误伤 API 客户端
   - **prefix 分工（已对 daqiang.nat100.top 实测验证）**：NAT nginx 会对
     upstream 的 redirect `Location` **自动加回 `/tts` 前缀**，所以应用侧
     的 Location 路径**必须不带前缀**（否则 `/tts/tts/...` 双前缀）；而
     `next` 查询值由登录页 JS 在客户端 `location.href` 直接消费、不经
     nginx，所以 `next` **必须带完整外部前缀**（`TTS_ERP_EXTERNAL_PREFIX`）。

## 6. 页面改动

### 6.1 新增登录页（`tts_erp_v2/api/v2/auth.py` 内嵌 HTML 模板，风格照抄 manual-costs）

- 居中卡片：密码框（`type=password`）+ 登录按钮 + 错误提示
- JS：`fetch('/v2/auth/login', {method:'POST', body:{key, next}})`（相对路径）
  → 200 则 `location.href = next`；401 则内联显示「key 无效或被禁用」

### 6.2 `manual-costs` 页（`tts_erp_v2/api/v2/pages.py`）

- **prefix 修复**：顶部加
  `const API = location.pathname.replace(/\/v2\/pages\/manual-costs.*$/, "") + "/v2";`
  所有 fetch 改用 `API + "/reporting/..."`（本地 `:9877` 时 `API="/v2"`，外网
  `/tts` 时 `API="/tts/v2"`）
- **删掉「粘贴 token」UI**，换成：登录态展示（来自 `/v2/auth/me`）+「退出登录」按钮
- fetch 不再手动带 `Authorization`（cookie 自动带）；对 POST 加
  `X-Requested-With: tts-erp` 头（CSRF 双保险，见 §7）
- fetch 遇 401 → `location.href = '/v2/auth/login?next=' + encodeURIComponent(location.pathname + location.search)`
- 首次进入时先 `me` 检查，未登录直接跳登录页

## 7. 安全考量

| 威胁 | 缓解 |
| --- | --- |
| CSRF（cookie 自动携带） | `SameSite=Lax`（跨站 POST 不带 cookie）+ 变更请求要求 `X-Requested-With: tts-erp` + CORS 默认 deny（跨源 fetch 预检即被拒）+ 可选 Origin 校验 |
| 登录爆破 | **现状缺陷**：`RateLimitMiddleware` 对 `api_key_hash=None` 的匿名请求**直接放行**（已读代码确认）→ 登录端点必须自己在 handler 内按 `client IP` 调 `shared_hit("ip:"+ip)` 限流（建议 10/min/IP），或给登录加独立小桶 |
| key 泄露 | cookie 只存 hash；明文 key 不进 localStorage（比现状更好）；`.env` 0600 |
| 会话重放/伪造 | HMAC 签名 + exp；篡改即验签失败；revoke key → DB 复查拒绝（正向缓存 ≤60s） |
| open redirect | `next` 白名单校验（§3） |
| 中间人 | `Secure` cookie（外网走 https）；本地 http 用 env 关掉 |

## 8. 边界情况

- **本地 `:9877`（http）**：`Secure` cookie 不发 → 需 `TTS_ERP_SESSION_SECURE=0`
- **`TTS_ERP_AUTH_MODE=off`**：中间件直接放行，登录页照常可用（不强制）
- **shadow 模式**：cookie 校验照跑，deny 只记日志（保持现有语义）
- **会话过期中途操作**：fetch 得 JSON 401 → 页面 JS 跳登录页并保留 `next`
- **cookie 与 header 并存**：cookie 优先（同一 key 时无差别；不同 key 时以 cookie 为准，文档注明）

## 9. 测试计划（TDD，先写测试）

新增 `tests_v2/api/test_auth_login.py`：

- GET 登录页 200 + text/html，无需凭据
- POST 合法 key → 200 + `Set-Cookie`（HttpOnly、SameSite=Lax、含签名）；随后 `/v2/auth/me` → `{authenticated:true, role}`
- POST 非法 key → 401；readonly key 登录后 GET manual-costs 页 200
- 带 cookie 直接 GET `/v2/pages/manual-costs` → 200（原 401 场景翻转为通过）
- 带 readwrite cookie POST `/v2/reporting/manual-costs` → 201；readonly cookie → 403
- 篡改 cookie 一个字节 → 拒绝；过期 exp → 拒绝
- logout → cookie 清除 → 再次请求 401
- revoke key（fixture disable）→ 会话失效（≤ 缓存 TTL）
- 浏览器重定向：`Accept: text/html` 无凭据 GET 页面 → 302 `/v2/auth/login?next=...`；`Accept: application/json` → 仍 JSON 401
- `next` 校验：`next=https://evil` → 拒绝/忽略
- 登录端点 IP 限流（若实现 §7 的 login throttle）

改动现有：

- `tests_v2/api/test_pages.py::test_manual_costs_page_requires_some_auth`
  → 改为断言浏览器 Accept 下 302 到 login（或保留 JSON 401 断言 + 新增 302 用例）
- `tests_v2/api/test_middleware.py` 分类变化同步

手工验证：`bash restart.sh` → `curl -i` 走外网 `/tts/` 全流程（登录 → cookie →
页面 200 → missing-cost-products 数据 200）。

## 10. 文件清单

| 文件 | 动作 |
| --- | --- |
| `tts_erp_v2/api/v2/auth.py` | **新增**：login/logout/me 路由 + cookie mint/verify + 登录页 HTML |
| `tts_erp_v2/middleware/auth.py` | 改：免 auth 路径、cookie 优先鉴权、`lookup_role_by_hash`、302 重定向 |
| `tts_erp_v2/api/v2/pages.py` | 改：prefix 感知 API base、去 token 粘贴 UI、登录态/退出、401 跳转 |
| `tts_erp_v2/app.py` | 改：include auth router |
| `.env` | 加 `TTS_ERP_SESSION_SECRET`（`openssl rand -hex 32`）、可选 `TTS_ERP_SESSION_TTL` / `TTS_ERP_SESSION_SECURE` |
| `tests_v2/api/test_auth_login.py` | 新增 |
| `tests_v2/api/test_pages.py`、`test_middleware.py` | 更新断言 |
| 本文件 | 设计文档 |

无 DB schema 变更（stateless 会话）。

## 11. 备选方案与取舍

- **A. 纯 localStorage bearer（仅放行页面壳 + 保留粘贴 token）**：改动最小，但 key
  明文进 localStorage、无自动跳转、UX 差。**否决为主方案**；如后续想做纯 API 用户
  仍走 header，无需登录。
- **B. 用户/密码账号体系（users 表 + bcrypt + 每人一个账号）**：真·人员登录、可审计
  「谁改的成本」，但要建表、密码重置、加解密依赖，明显超「简易」范围。**不做**，
  列为远期选项（若要审计，可在 manual-costs 行上记 `key_hash` 已有能力之上扩展）。
- **C. TikTok OAuth 当登录**：域不对（TikTok 授权 ≠ 运营身份）。**不做**。
- **D. 整个 `/tts` 公开**：数据端点全裸奔。**否决**。
