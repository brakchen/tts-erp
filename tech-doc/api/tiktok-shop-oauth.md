# TikTok Shop seller authorization（新店授权 / OAuth onboarding）

**Single-source spec** for `/v2/oauth/tiktok/*`. 协议事实以
`tts-partner-api-docs/Authorization overview.md` + `Seller authorization guide.md`
为准（授权域/参数名/grant_type 全部照搬，不要"修正"）；本文件只写我们这侧
的端点契约与落地行为。

Lane E 落地：v1 独立服务 `oauth-receiver` 的 `/authorize` + `/callback` 职责迁入
v2 app（oauth-receiver 库已于 2026-09-05 DROP）。sync worker **不**负责授权——
它只消费结果（对 `integration.credentials` 行 fan-out 跑数据 job）。

## 流程总览

```text
操作者(admin)                    TikTok Seller Center               tts-erp
─────────────                    ───────────────────                ───────
GET /v2/oauth/tiktok/authorize ───────────────────────────────► 注册一次性 state
   ◄── {authorize_url, state}                                    (存 sha256, TTL 45min)
浏览器打开 authorize_url ──► seller 登录/同意
                              └─► redirect → /v2/oauth/tiktok/callback?code&state
                                                              ► pop_state(单次消费)
                                                              ► token/get 换 token(用户级)
                                                              ► Get Authorized Shops(每店 shop_id+cipher)
                                                              ► 每店 upsert credentials+commerce.shops
                                                              ► HTML/JSON 结果页
   ◄── 授权成功，下个 tick 数据 job 自动接管
```

## 端点契约

### `GET /v2/oauth/tiktok/onboard` — 新店授权控制台页（浏览器 UI）

- **Role**: readonly（`_READONLY_EXACT` 精确豁免行；HTML 壳页本身无数据/无副作用）。
  未登录浏览器 GET → 302 `/v2/auth/login?next=/v2/oauth/tiktok/onboard`。
- **行为**: 内联 JS 探测 `/v2/auth/me`，非 admin 显示角色门槛；admin 点「生成授权链接」
  调 `GET /v2/oauth/tiktok/authorize?format=json` → 展示新链接（复制 / 新窗口打开 / 到期时间）。
  每次生成 = 新 state（45min、单次使用），**不缓存链接**。

### `GET /v2/oauth/tiktok/authorize` — 发起授权（admin）

- **Role**: `admin`（handler `require_role_at_least(request, "admin")`；
  middleware 对未知路径默认 admin，双重保险）。浏览器会话需带
  `X-Requested-With: tts-erp`。
- **Query**: 无必选。`format=json` → JSON；否则 HTML（含可直接点的链接）。
- **响应 200 (json)**:

  ```json
  {
    "ok": true,
    "authorize_url": "https://services.tiktokshop.com/open/authorize?service_id=...&state=...",
    "state": "<raw single-use token>",
    "state_expires_at": "2026-09-05T12:34:56+00:00",
    "hint": "..."
  }
  ```

- **错误**: `500`（`TIKTOK_SERVICE_ID` 未配置 / authorize host 非 http(s)）、
  `401/403`（无 key / role < admin）。
- **副作用**: `integration.oauth_states` 插一行（只存 `sha256(state)`）。

### `GET /v2/oauth/tiktok/callback` — TikTok 重定向目标（public）

- **Role**: **public**（`middleware/auth.py` `EXEMPT_PATHS` 精确豁免；
  这是 TikTok 重定向 seller 浏览器的地方，没有 API key 可带）。仍走
  RateLimit（按 IP 分桶）。
- **Query**: `code`（一次性 auth_code，30 分钟有效）、`state`（上一步返回的
  raw token）、`error`（seller 拒绝时为 `auth_denied`）、`format=json`（可选）。
- **行为顺序**: 校验并**原子消费** state（single-use；未知/过期/重用全部
  fail-closed，且不触发上游调用）→ 消费成功才调
  `GET auth.tiktok-shops.com/api/v2/token/get?app_key&app_secret&auth_code&grant_type=authorized_code`
  拿**用户级 token**（open_id/seller_*，**无 shop 身份**）→ `user_type ∈ {0,4,5}`
  （seller / global-selling；creator=1 / partner=2,3 拒绝）→ 调
  `GET open-api.tiktokglobalshop.com/authorization/202309/shops`（HMAC 签名 +
  `x-tts-access-token`）枚举授权店铺（每店含 shop_id + shop_cipher）→ **每店**
  upsert credentials + commerce.shops → commit。
- **落库**（同一事务，**一店一对行**；多店 seller 自动逐店落）:
  - `integration.credentials`：key `(provider='tiktok', external_account_id=shop_id)`；
    envelope 存 `access_token/refresh_token/shop_cipher`（Fernet；token 为用户级共享，
    shop_cipher 为该店专属），`account_label=店名`，`expires_at`，`granted_scopes`。
  - `commerce.shops`：`(platform='tiktok', shop_id)` upsert →
    `account_name/region/seller_type/status='active'/credential_id` 关联。
  - **幂等**：同一 shop 重复授权 = 续期路径，两行原地更新，不产生重复。
  - 授权响应中店铺列表为空 → `kind=no_authorized_shop`（不落任何行）。
- **响应**: 默认 HTML 结果页（浏览器展示 shop_id/name/region/scopes）；
  `format=json`:
  - 成功 → `200 {"ok": true, "kind": "authorized", "result": {"shops": [{shop_id,
    credential_id, account_id, account_name, region, seller_type, expires_at, granted_scopes}]}}`
  - seller 拒绝 → `200 {"ok": false, "kind": "denied", "error": "auth_denied"}`
  - state 无效/过期 → `400 kind=state_invalid`；重用 → `400 kind=state_reused`
  - `user_type` 不支持 → `400 kind=user_type`；店铺列表为空 → `400 kind=no_authorized_shop`
  - 店铺条目缺 `shop_id`/`shop_cipher` → `400 kind=missing_shop_id` / `missing_shop_cipher`
    （跨境路由/签名必需，宁可不落库；错误附条目 raw keys 可当场判断上游改版）
  - 上游 token/get 拒绝（如 code 已用过）→ `502 kind=upstream`（state 已消费，
    需重新发起 authorize）
  - 无 `code` 裸访问 → `400 kind=missing_code`
- **副作用**: state 消费置 `consumed_at`；成功后 sync worker 下一 tick 自动
  把新 shop 纳入（`scheduler._enumerate_tiktok_shops` 读 credentials 表）。

## 环境变量

| 变量 | 必需 | 说明 |
| --- | --- | --- |
| `TIKTOK_SERVICE_ID` | ✅ authorize 前 | Partner Center **App & Service** 页的 `service_id`（OAuth client id，≠ app_key） |
| `TIKTOK_APP_KEY` / `TIKTOK_APP_SECRET` | ✅ | token/get 与刷新共用（已有） |
| `TIKTOK_AUTHORIZE_HOST` | 可选 | 授权域名。默认 ROW `https://services.tiktokshop.com`；US 市场设 `https://services.us.tiktokshop.com` |
| `TIKTOK_AUTH_HOST` | 可选 | token 域名，默认 `https://auth.tiktok-shops.com`（已有） |
| `TIKTOK_API_HOST` | ✅ 回调 | Get Authorized Shops 域名，默认 `https://open-api.tiktokglobalshop.com`（已有） |
| `TIKTOK_REDIRECT_URI` | 可选 | 仅展示/文档用。Partner Center 里配的实际 Redirect URL 才是生效值 |

## Partner Center 一次性配置（人类操作，agent 不代办）

1. App **App & Service** 页抄 `service_id` → 写 `.env TIKTOK_SERVICE_ID`。
2. **Redirect URL** 填公网可达的 callback —— **必须带外部前缀 `/tts`**（nginx
   只把 `/tts/*` 转给 :9877 API，无前缀的 `daqiang.nat100.top/v2/...` 会落在
   ProfitLens 前端 404 页）：
   `http://daqiang.nat100.top/tts/v2/oauth/tiktok/callback`（前缀取
   `TTS_ERP_EXTERNAL_PREFIX` 实际值，当前 `/tts`；middleware 会剥前缀匹配豁免）。
3. 确认 scope（`seller.*` 读类）已勾选；勾太多影响审核与授权率。
4. 测试用 Seller Center **test account / Development Shops**，不要在开发期用
   线上 seller 真号授权。

## 上游契约确认（shop_id / shop_cipher 从哪来）—— 已实测定稿

**实测结论（2026-09-06 live 验证）**：service_id 授权流的 `token/get` 返回的是
**用户级 token**：`data` 只有 `access_token / access_token_expire_in / refresh_token /
refresh_token_expire_in / open_id / seller_name / seller_base_region / user_type /
granted_scopes`——**没有 shop_id、没有 shop_cipher**（Authorization overview 字段表
在此是准确的）。v1 时代 token/get 能直接读到 shop 字段，是因为旧 app_key 授权流与
service_id 流不同（v1 生产凭证来自旧流）。

因此每店的 `shop_id` + `shop_cipher` 来自 **Get Authorized Shops**
（`GET {TIKTOK_API_HOST}/authorization/202309/shops`，HMAC 签名——只含 app_key+
timestamp，无 shop_cipher；头带 `x-tts-access-token`；响应 `data.shops[]` 每项含
`id/cipher/name/region/seller_type`，解析容错 `id|shop_id`、`cipher|shop_cipher`、
`name|shop_name`、`region|shop_region`，首次真实调用的 raw keys 记入日志）。

**防御仍在**：店铺条目缺 shop_id/shop_cipher → 该次授权 `missing_shop_id` /
`missing_shop_cipher` 显式失败且不落半残行；店铺列表为空 → `no_authorized_shop`。
错误均附 raw keys 供当场判断——不要弱化校验。

## 生命周期备注

- **续期**：授权将到期 → 让 seller 重新点一次 authorize link（同一 shop_id →
  幂等更新）。30 天前 TikTok 会推 "Upcoming authorization expiration" webhook
  —— v2 尚未订阅 webhook（无接收端点），续期靠人工/监控 token.refresh 失败告警。
- **取消授权**：seller 在 Seller Center 取消后，token.refresh 会持续失败 →
  `integration.sync_issues` 累积 + `proxy_call` 报 "re-authorize via /callback"。
  v2 未接 "Seller deauthorization" webhook，取消不会自动删行（当前无删除端点，
  需要时人工清或后续补 webhook 接收）。
- **测试**：`tests/proxy/test_tiktok_oauth.py`（HTTP 单测）、
  `tests/proxy/test_oauth_flow.py`（state + 编排，DB 集成）、
  `tests/api/test_oauth_api.py`（契约，TestClient + admin key，`format=json`）。
