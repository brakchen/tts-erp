# tts-erp API Key 鉴权系统 · 技术方案

> 版本：v1.1 · 2026-08-17
> 状态：**已实施，2026-08-20 起为 enforce**（生产 `.env TTS_ERP_AUTH_MODE=enforce`，
> `/healthz` 实测 `auth_mode:"enforce"`；shadow 观察期已于当日结束）
> 适用范围：`/home/schan/tts-erp/`（FastAPI on uvicorn，端口 9877）
>
> **As-built 差异（2026-08-29 v2 切流后）**：
>
> - 中间件现位于 `tts_erp_v2/middleware/auth.py`（本文 §5.4 的 `tdd/auth.py` 是 v1 位置，已退役）；
>   v2 另有 `tts_erp_v2/middleware/session_auth.py` 提供浏览器会话 cookie（见 browser-login-design.md）。
> - `api_keys` 表已迁入 **`security.api_keys`**（九 schema 之一）；本文 §5.2 的 `schema.sql`
>   已于 2026-08-27 拆分为 `schema_tts_erp.sql` / `schema_oauth.sql`。
> - 豁免清单现为 `/healthz`、`/endpoints`、`/openapi.json`、`/docs`、`/redoc`、
>   `/docs/oauth2-redirect`、`/v2/auth/{login,logout,me}`（见 v2 中间件 `EXEMPT_PATHS`）。
> - §5.3 / §6 的端点矩阵基于 v1 路由（`/db/*`、`/orders/*` 等），这些路由已随 v2 硬切换删除；
>   现行端点-角色矩阵以 `tts_erp_v2/middleware/auth.py::required_role()` 为准。

---

## 1. 背景与问题

tts-erp 当前**无任何鉴权**且监听 `0.0.0.0:9877`。局域网内任何设备都可以：

- `GET /token/<shop_id>?reveal=1` 拿到店铺**明文 access token**
- `POST /orders/<id>/cancel` 等端点**直接操作真实店铺**（取消订单、确认发货）
- `POST /sync/*` 消耗 TikTok API 限流配额（10 QPS 全店共享）
- 读取全部订单/财务/买家地址数据（PII + 商业数据）

随着服务数据变重（Excel 财务融合后含完整结算金额、买家地址），这个暴露面必须收敛。

## 2. 目标 / 非目标

**目标**

1. 所有业务端点默认拒绝，凭 API key 访问（默认拒绝原则）
2. 三级权限隔离：只读 / 读写 / 管理（token 提取单独管住）
3. key 可创建、吊销、轮换，落库可审计
4. 对现有调用方（cron 同步、smoke 脚本、人工 curl）平滑迁移，可灰度可回滚
5. 实现简单：一个中间件 + 一张表 + 一个 CLI，不引入新服务

**非目标**（明确不做）

- 不做 OAuth2/OIDC/JWT 签发体系（单体内网服务，过度设计）
- 不做 per-key 限流、IP 白名单（列入后续增强，见 §10）
- 不改 oauth-receiver:9876（它同样无鉴权，是另一个项目的课题，见 §10）
- 不做 TLS（内网 HTTP 可接受；如将来经 cpolar 暴露公网，TLS + 鉴权是前置条件，见 §10）

## 3. 威胁模型

| 威胁 | 缓解 |
| --- | --- |
| 局域网内未授权访问（蹭网设备、被入侵的同网段主机） | 全端点强制 Bearer key |
| AI agent / 脚本误调危险写端点 | 角色隔离：只读 key 物理上无法触发写操作（403） |
| 明文 token 被顺手牵羊 | `/token/*` 仅 admin 角色；admin key 只人工持有，不落任何脚本 |
| key 泄落后的影响 | 库里只存 SHA-256 哈希，泄库不泄 key；按 prefix 快速吊销 |
| 重放/中间人（内网 HTTP） | 接受风险（内网）；公网暴露前必须 TLS（§10） |
| 时序攻击枚举 key | 按哈希等值查找 + `hmac.compare_digest` 复核 |

## 4. 方案总览

**`Authorization: Bearer <api_key>` 鉴权，FastAPI 中间件统一拦截，PG 存哈希，三级角色。**

与这台机器上 camofox（9377）、qt-web-extractor（8766）的 Bearer 方案保持一致，降低认知成本。

```
请求 → AuthMiddleware
        ├─ 路径在豁免表？（/healthz、/endpoints）→ 放行
        ├─ 无/畸形 Authorization header → 401
        ├─ sha256(key) 查 api_keys（带 60s 进程内缓存）
        │    ├─ 不存在 / enabled=false / 过期 → 401
        │    └─ 命中 → 得角色
        └─ 路径要求角色 > 持有角色 → 403，否则放行
```

## 5. 详细设计

### 5.1 Key 格式

```
ttserp_<role>_<32 字符 urlsafe 随机>
例：ttserp_ro_Kx9vQ2mP7nR4sT8uW1yZ3aB5cD6eF0gH
```

- `secrets.token_urlsafe(24)` 生成（192 bit 熵）
- 可识别前缀（仿 GitHub token）：日志里看到 key 片段就知道角色和归属
- **完整 key 只在创建时打印一次**，之后库里只有哈希和前 16 字符 prefix

### 5.2 存储（schema.sql 新增，遵循 `IF NOT EXISTS` 惯例）

```sql
CREATE TABLE IF NOT EXISTS api_keys (
    id           BIGSERIAL PRIMARY KEY,
    key_hash     TEXT NOT NULL UNIQUE,        -- SHA-256 hex(完整 key)
    key_prefix   TEXT NOT NULL,               -- key 前 16 字符，用于识别/吊销
    name         TEXT NOT NULL,               -- 用途命名：cron-sync / smoke / mavis-laptop
    role         TEXT NOT NULL CHECK (role IN ('readonly','readwrite','admin')),
    enabled      BOOLEAN NOT NULL DEFAULT true,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_used_at TIMESTAMPTZ,                 -- 节流更新：距上次 >1h 才写
    expires_at   TIMESTAMPTZ                  -- NULL = 永不过期
);
CREATE INDEX IF NOT EXISTS idx_api_keys_prefix ON api_keys (key_prefix);
```

为什么落库而不是写死在 `.env`：可吊销、可审计（last_used_at）、可多 key 分角色分用途，且 `docker compose restart` 不重读 .env 的坑在这台机器上已经踩过两次。

### 5.3 角色-端点矩阵（中间件按路径前缀匹配，默认拒绝）

| 角色 | 允许的路径 |
| --- | --- |
| （豁免） | `GET /healthz`、`GET /endpoints`（监控/发现需要；endpoints 清单不含敏感数据） |
| `readonly` | 全部 `GET /db/*`；GET 代理：`/orders/<id>`、`/orders/<id>/{tracking,tracking/get,risk,buyer,recipient}`、`/finance/*`、`/logistics/orders/*`；`GET /shops*`；只读搜索 `POST /returns/search`、`POST /cancellations/search`、`POST /orders/search`、`POST /orders/list` |
| `readwrite` | readonly 全部 + 店铺写操作 `POST /orders/<id>/{confirm,cancel,update_status,shipping_info,verify_shipping}` + 全部 `POST /sync/*` |
| `admin` | readwrite 全部 + `GET /token/*` |

角色按 `readonly < readwrite < admin` 线性包含，实现就是一个整数比较。

注意 `POST /orders/search` 虽语义是读，但消耗 TikTok 限流配额——归为 readonly 是刻意放宽（查询是高频需求），如滥用再上调。

### 5.4 中间件（新文件 `tdd/auth.py`）

- `AuthMiddleware(BaseHTTPMiddleware)`，在 `tts_erp_fastapi.py` 一行 `app.add_middleware(...)` 挂载
- 匹配顺序：豁免表 → 前缀规则表（最长前缀优先）→ 默认拒绝
- 响应语义：
  - 无 key / key 无效 / 吊销 / 过期 → `401` + `WWW-Authenticate: Bearer` + `{"detail": "..."}`
  - 角色不足 → `403` + `{"detail": "requires readwrite"}`
- 查找：`SELECT role, enabled, expires_at FROM api_keys WHERE key_hash = %s`；命中后 `hmac.compare_digest` 复核哈希（防 DB 侧非常规比较行为）
- **进程内缓存**：`{key_hash: (role, enabled, expires_at)}`，TTL 60 秒——吊销最多 60 秒生效，对本服务量级足够（可接受权衡，文档化）
- `last_used_at` 节流更新：仅当 `now() - last_used_at > 1h` 时异步写一次，避免每请求一写
- 认证失败打 warning 日志（IP、路径、key_prefix 若有），便于发现枚举尝试

### 5.5 开关（灰度关键）

`.env` 新增：`TTS_ERP_AUTH_MODE=off|shadow|enforce`，默认 `off`。

- `off`：中间件直接放行（现状）
- `shadow`：照常放行，但对**会被拒的请求**打日志（`[auth-shadow] would-deny ...`）——用来观察遗漏的调用方
- `enforce`：真正执行 401/403

### 5.6 管理 CLI（`api_keys.py`，仓库根目录）

```bash
python3 api_keys.py create --name cron-sync --role readwrite   # 打印一次完整 key
python3 api_keys.py list                                       # prefix/name/role/last_used，无哈希无全文
python3 api_keys.py revoke --prefix ttserp_rw_Kx9vQ2mP
python3 api_keys.py rotate --prefix ttserp_rw_Kx9vQ2mP         # = create 同名同角色新 key + revoke 旧 key
```

直连 PG（走 TTS_ERP_DB_URL），不走 HTTP（管理面不经服务自己，避免鸡生蛋问题）。

## 6. 调用方改造点（迁移清单）

| 调用方 | 改造 |
| --- | --- |
| `sync_cron.py`（每 10 分钟 cron） | 从 env 读 `TTS_ERP_SERVICE_KEY`，所有 `/sync/*` 请求带 `Authorization: Bearer` |
| `run_sync_cron.sh` | 无需改（已 source .env），`.env` 加一行 `TTS_ERP_SERVICE_KEY=ttserp_rw_...`（0600 权限不变） |
| `test_e2e.py` | 同样从 env 读 key 带 header；新增「无 key 应 401」断言（`final_smoke.py` / `regression_check.py` 在 2026-08-30 cleanup 已删，调用方合并到 `tests/test_e2e*.py`） |
| `tdd/` 测试 | 新增 `test_auth.py`（见 §8）；既有 TestClient 测试在 conftest 统一注入 admin key header |
| 人工 curl / Windows 工作站 | 发一把 `readonly` 或 `readwrite` key，随用随带 header |
| README / AGENTS.md / setup 文档 | 更新端点说明（标注所需角色）、`.env` 字段、故障排查表 |

## 7. 部署与灰度计划

1. **P0 建表**：`cat schema.sql | docker exec -i postgres psql -U postgres -d tts_erp`（幂等，无影响）
2. **P1 上线 off 模式**：部署 `auth.py` + 中间件 + CLI，`TTS_ERP_AUTH_MODE=off`，`restart.sh`，全量 pytest + smoke —— 行为零变化
3. **P2 创建 key 并改造调用方**：建 cron 服务 key（readwrite）写入 .env；建好人工 key；改 sync_cron.py / smoke
4. **P3 shadow 观察 24h+**：`shadow` 模式跑至少一天（覆盖多个 cron 周期），`grep would-deny logs/stderr.log` 应为空
5. **P4 enforce**：改 `enforce`，重启，smoke + 一轮 cron 实测
6. **P5 收尾**：更新 README/AGENTS.md/setup 文档；CHANGELOG 记条目

每步都可独立回滚：`TTS_ERP_AUTH_MODE=off` + 重启即恢复（30 秒级）。

## 8. 测试计划（`tdd/test_auth.py`）

沿用现有 pytest + TestClient + TEST_ 哨兵模式：

- 无 header → 401；畸形 header → 401
- 伪造 key → 401（且日志有记录）
- `readonly` key 调 `/db/orders` → 200；调 `/orders/<id>/cancel` → 403；调 `/token/<id>` → 403
- `readwrite` key 调 `/sync/orders` 通过认证层（业务层 mock 或仅断言非 401/403）
- `admin` key 调 `/token/<id>` 通过认证层
- `/healthz` 无 key → 200（豁免）
- `enabled=false` 的 key → 401；`expires_at` 已过 → 401
- `shadow` 模式下无 key 请求 → 200（放行）
- 缓存行为：吊销后 TTL 内仍放行、过期后拒绝（可用 monkeypatch 缩短 TTL 测）

回归：既有 131 tests 在注入合法 key 后保持全绿。

## 9. 风险与权衡

| 风险 | 评估 | 缓解 |
| --- | --- | --- |
| 缓存导致吊销延迟 ≤60s | 低（内网、低 QPS） | 文档化；必要时加 `POST /admin/flush_cache`（admin） |
| enforce 后 cron 断流 | 中 | shadow 阶段兜底观察；cron 日志监控 `sync_log` 表 error |
| key 明文落 `.env`（cron 服务 key） | 可接受 | .env 本就 0600 且已含 DB 密码/app_secret，同级 |
| 每请求多一次哈希+（缓存未命中时）一次 SELECT | negligible | SHA-256 <1µs；缓存命中后零 DB 往返 |

## 10. 后续增强（本方案不做，列出备查）

1. **oauth-receiver:9876 同样需要鉴权**——它是 token 的真正源头，裸奔状态下 tts-erp 的 `/token/*` 管得再严也是掩耳盗铃。**建议下一步优先做**。
2. 网络层兜底：`restart.sh` 监听改 `127.0.0.1` + 确需 LAN 访问时加 UFW 来源白名单（与鉴权互补，纵深防御）
3. per-key 限流（滑动窗口，防单调用方打爆 TikTok 10 QPS 配额）
4. 如需公网访问：cpolar + TLS 前置，且必须先完成本方案 + oauth-receiver 鉴权
5. key 过期自动轮换提醒（`expires_at` + cron 日报）

## 11. 文件改动清单（实施时）

| 文件 | 动作 |
| --- | --- |
| `tdd/auth.py` | 新建：AuthMiddleware + 角色矩阵 + 缓存 |
| `tdd/tts_erp_fastapi.py` | 挂载中间件（~3 行） |
| `tdd/test_auth.py` | 新建：§8 测试 |
| `tdd/conftest.py` | TestClient 统一注入 admin key |
| `api_keys.py` | 新建：管理 CLI |
| `schema.sql` | 新增 `api_keys` 表 |
| `sync_cron.py` | 请求带 Authorization header |
| `test_e2e.py` | 带 key + 401 断言（`final_smoke.py` / `regression_check.py` 2026-08-30 cleanup 已删） |
| `.env` | `TTS_ERP_AUTH_MODE` + `TTS_ERP_SERVICE_KEY` |
| `README.md` / `AGENTS.md` / `setup/tts-erp-cron-sync.md` | 文档同步 |

预估代码量：auth.py ~120 行，CLI ~80 行，测试 ~100 行，其余改动合计 <50 行。
