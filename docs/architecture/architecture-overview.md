# tts-erp 架构概览

> 本文档包含 tts-erp 项目的详细技术栈、架构和业务模型信息。
> 通用约束和边界规则见 `AGENTS.md`。

## 1. 技术栈

Python 3.13+、FastAPI/uvicorn、SQLAlchemy 2、psycopg3、PostgreSQL、APScheduler、
MinIO 和 Fernet。生产 API 与 Sync Worker 由系统级 systemd 单元运行。
数据库结构以 [schema 索引及 DDL 快照](../schema/README.md) 为准。

## 2. 业务架构

### 2.1 v2 架构（2026-08-29 切流生产）

**核心业务**：TikTok Shop 销售 + 妙手采购 → **本地分析库 + 只读 API + 定时同步**

**下游系统**：

- TikTok Shop Open API (`open-api.tiktokglobalshop.com`)
- 妙手开放平台 (`openapi.wanshifu.com`)

**TikTok Open API 调用**由同步 job、只读透传 API 和 OAuth 流程发起。签名和客户端实现位于
`tts_erp_v2/proxy/tts_shop`，凭证由 `tts_erp_v2/proxy/token_service.py` 管理。

### 2.2 访问方式

- 生产公网入口为 `https://chen.daqiangplatform.com/tts`。生产机的 Nginx 将 `/tts/*`
  转给本机 `127.0.0.1:9878`，`proxy_pass` 保留完整前缀。
- `TTS_ERP_EXTERNAL_PREFIX` 在构建 FastAPI 应用时进入 `root_path`。鉴权和路由使用统一归一化结果。
- 开发机在回环地址上运行验证实例。部署与本地启动见 [部署手册](../ops/tts-erp.md)。

## 3. 测试 DB 隔离

测试隔离机制、模板刷新、生产护栏与故障处理统一维护在 [测试指南](../guides/agent-testing.md) §2，本节不再重复。

## 4. 数据库维护待办

大表 retention 与生产 dump 归档策略尚待用户拍板，详见
[`database-maintenance-todos.md`](../ops/database-maintenance-todos.md)。

## 5. 凭证管理

### 5.1 凭证单源：`integration.credentials` + `proxy/token_service.py`

```python
# ✓ 正确
from tts_erp_v2.proxy.token_service import load_credentials
cred = load_credentials(session, provider="tiktok", external_account_id=shop_id)
access_token = cred.access_token   # 已解密
shop_cipher = cred.shop_cipher

# ✗ 错误（都是实测踩过的坑）
# 直连 oauth_receiver 库的 oauth_tokens 表      # v1 遗物（库已 2026-09-05 DROP，备份 backups/oauth_receiver_v1_legacy_*.sql.gz）
# 自己拿 Fernet key 解密 integration.credentials # 绕过统一实现（掩码/续期/降级会失效）
```

**凭证格式**：Fernet 加密的 JSON envelope（key = `.env TTS_ERP_FERNET_KEY`）

**唯一实现**：`token_service.py`（`encrypt` / `decrypt` / `load_credentials` / `upsert_credentials` / `refresh_if_needed`）

**token 续期**：由 sync-worker 的 `token.refresh` job 每 6h 进程内完成，不需要也不要有 HTTP 续期端点。

## 6. 认证与鉴权（enforce 模式）

**设计文档**：API key 部分 `docs/design/api-key-auth-design.md`；用户账号 / 会话 /
页面权限部分 `docs/design/user-account-authz-design.md`（已实现，迁移
`alembic/versions/0052_user_accounts.py`）。两套凭证共用同一授权层
（`tts_erp_v2/access/` 路由 → 最低角色矩阵 `required_role()`）：

- **人类操作者（浏览器）**：用户名 + 密码登录（`POST /v2/auth/login`）→
  服务端会话 cookie **`tts_erp_session`**（不透明 token，`security.user_sessions`
  只存 `sha256(token)` + 过期/吊销状态；`HttpOnly; Secure; SameSite=Lax;
  Path=<root_path>`）；登出/禁用/改密走 `revoked_at` 即时失效。旧的 API-key
  浏览器登录与 HMAC cookie `tts_session` 已移除（`docs/archive/browser-login-design.md`
  仅历史参考）。密码哈希 = **argon2id**（`argon2-cffi`，MIT），不存明文。
- **程序化访问（不变）**：除豁免路径（`/healthz`、`/endpoints`、`/openapi.json`、
  `/docs`、`/redoc`、`/docs/oauth2-redirect`、`/v2/auth/{login,logout,me}`）外，
  脚本/扩展继续用 `Authorization: Bearer <key>` 或 `X-API-Key: <key>`；
  无 key 401、角色不够 403。完整角色矩阵见 `docs/api/external-api.md`；
  实现集中在 `tts_erp_v2/access/`，`middleware/auth.py` 仅为 ASGI adapter
- **账号数据模型**（`security` schema，migration 0052 新增六表，与既有
  `security.api_keys` 并列）：`users`（用户名/argon2id 哈希/状态）、`roles`
  （含 `api_tier`）、`permissions`（页面权限点 `page:<id>`）、
  `role_permissions`、`user_roles`、`user_sessions`（服务端会话）
- **页面级权限**：权限点 `page:<page_id>` 与侧边栏页面一一对应，单一清单在
  `tts_erp_v2/accounts/pages.py`（含 `page:users` 用户管理与 `page:video-publish` 视频发布）；
  `GET /v2/pages/<id>` 要求会话权限集含 `page:<id>`，缺失 → 403 页面
  （已登录但无权限 ≠ 未登录）；`/v2/users*` / `/v2/roles*` 归属 `page:users`
- **侧边栏按权限过滤**：服务端渲染时按会话 `pages` 过滤入口，无权限页面不出现在
  菜单（API key / auth off 时全量显示）；页面内 API 通常复用角色矩阵，视频发布数据/API 还要求 `page:video-publish`
- **api_tier 复用既有角色矩阵**：会话用户取其角色的 `api_tier`
  （`readonly|readwrite|admin`，取最高档）代入同一张 `required_role()` 比较；
  `/v2/users` `/v2/roles` 等未列路径默认 admin 档（fail-closed）
- 三级角色 `readonly` < `readwrite` < `admin`；handler gate 读取同一 typed `AccessGrant`；
  例如 admin/reset-rate-limit=admin
- key 入库只存 SHA-256 哈希（`security.api_keys`）；模式开关 `.env TTS_ERP_AUTH_MODE=off|shadow|enforce`
  （生产 = enforce；非法值记录错误并 fail closed）；cron/脚本用 `.env TTS_ERP_SERVICE_KEY`
- 浏览器会话的 mutation 仍带 `X-Requested-With: tts-erp`（CSRF 闸，叠加
  `SameSite=Lax` + 写 API 只收 JSON）；登录限流独立 IP 滑动窗口
  （`TTS_ERP_LOGIN_RATE_LIMIT`，默认 10 次/分）；账号管理入口 = 用户管理页
  （`/v2/pages/users`）+ CLI（`python -m tts_erp_v2.accounts.cli`），无自助注册
- deployment path 由 `tts_erp_v2.access.canonicalize_path()` 统一解释，Auth 与 DocsAuth
  共用 route-relative 结果；不得在其他 middleware 重新读取 external-prefix env

## 7. External API（外部契约）

**完整活契约**（auth / 限流 / CORS / 分页 / 每端点 schema + curl + Stability matrix）= `docs/api/external-api.md`——加/改任何外部端点前先读它。分页、时间格式、money 序列化、内部主键过滤（`shop_pk`/`spu_pk`）等契约要点与不稳定端点清单均以该文档为准。

**本机实时清单**：`GET /endpoints`

### 7.1 改 app.py / middleware 后验证

使用 [测试指南](../guides/agent-testing.md) 选择隔离的鉴权、API 契约和浏览器回归。生产只读检查由运维按 [部署手册](../ops/tts-erp.md#运行状态与日志) 执行。

### 7.2 已拆除、不要再找

- 没有 `POST /v2/sync/*` —— 同步全部由 `tts-erp-sync.service` 调度（`sync_worker/scheduler.py` 的 `JOBS`）
- 没有任何 `/v2/linkage/*` —— 未接通的映射层与 DB schema 已由 migration 0044 删除
- 没有任何 `/miaoshou/*` 路由（出站代理和回调端点未挂 v2，实测 404）
- 没有 `/v1/analytics/sync/*` —— 2026-09-02 硬切 `/v2/analytics/sync/*`（无别名）；`/batches` 已换
  `/dumps`（单 dump object）；cursor 降级 has-data 预检（协议见 `docs/archive/dump-architecture.md`）
  —— 2026-09-07 v3 区间聚合（Design A）见 `docs/archive/range-aggregate-history-sync.md`
- 没有 `/v2/analytics/sync/batches`（同 release 删除；`ad_daily_pages` / `ad_cursors` 表 migration 0005 drop）
- v1 路由（`/shops`、`/token/*`、`/orders/*`、`/finance/*`、`/returns/*`、`/cancellations/*`、`/db/*`）全部 404，
  v1→v2 迁移映射见 `external-api.md` 底部 Stability matrix；v1 代码仍在 git history（仅代码级参照——v1 DB 数据已 2026-09-05 归档删除，回滚需先恢复 dump）
