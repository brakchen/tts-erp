# TikTok Shop 逐店授权

现行合同与 [店铺接入管理技术方案](../plans/2026-10-31-shop-onboarding-design.md) 一致。
本服务从已经存在的店铺发起授权；每店独立配置 Service ID、App Key 和 App Secret。
`tts_erp_v2/api/v2/oauth.py` 与 `tts_erp_v2/proxy/tiktok_oauth.py` 是接口及编排实现。

## 操作流程

插件发现或人工补录店铺 → 在店铺管理配置本店 App → 针对该店生成授权链接 →
卖家同意 → 回调校验目标和 App 版本 → 更新原店铺的授权凭据 → 定时同步自动接管。

已有店铺的内部主键不变；授权不会自动创建其他店铺，也不会覆盖已有店名、区域或开店日期。
未授权店铺只在店铺管理的 `view=management` 读取面出现，业务读取仍按当前授权资格过滤。

## 发起授权

`GET /v2/oauth/tiktok/authorize?shop_pk=<内部主键>&format=json`

- 权限：`readwrite` 或以上；readonly 返回 403。
- `shop_pk` 必填且大于零；未传或非法值返回 422，目标店铺不存在返回 404。
- 店铺必须已经选择 `service_id`，并在 `integration.tiktok_app_credentials` 中配置本店 App。
  缺少 Service 或 App 返回 422；全局环境变量和其他店铺的配置不能补足它。
- `format=json` 返回 JSON；省略时返回含授权链接的 HTML。
- `redirect_to` 只是展示提示，不决定回调目标。

成功响应：

```json
{
  "ok": true,
  "authorize_url": "https://services.tiktokshop.com/open/authorize?service_id=...&state=...",
  "shop_id": "目标上游店铺ID",
  "service_id": "该店Service",
  "state": "原始一次性随机值",
  "state_expires_at": "2026-11-03T12:34:56+00:00",
  "hint": "..."
}
```

服务器只保存 state 的 SHA-256，TTL 为 45 分钟。
`integration.oauth_states.extra` 绑定 `expected_shop_pk`、`expected_shop_id`、
`service_id`、`app_revision` 和发起身份 `created_by`，不存 App Secret。
每次发起生成新 state；授权成功、上游失败或验证失败后都不能重复使用已消费的 state。

## 回调

`GET /v2/oauth/tiktok/callback?code=...&state=...[&app_key=...][&format=json]`

这是公开的 TikTok 重定向目标，无需 API key；仍经过限流。

1. 原子校验并消费 state；未知、过期、重用或缺少目标绑定的 state 失败关闭。
2. 核对原店铺 ID、当前 Service 及本店 App revision；配置变化必须重新生成链接。
3. 如 callback 提供 `app_key`，必须与 state 对应本店 App 匹配。
4. 使用本店 App 交换 code，要求卖家 `user_type` 属于 `{0, 4, 5}`。
5. 使用用户级 token 调用 Get Authorized Shops。返回必须**恰好一店**，且 ID 与目标一致；
   空列表、多店、缺少 ID 或另一店铺均失败，不创建或更新任何凭据。
6. 目标必须有 shop cipher。再次锁定并核对目标与 App 后，通过 `token_service` 加密保存凭据。
   设置原店铺的 `credential_id` 和授权事实；空白元数据可补齐，已有值保持。

成功返回 `200`：

```json
{"ok":true,"kind":"authorized","result":{"shops":[{"shop_id":"目标ID","credential_id":123,"account_id":314,"account_name":"店名","region":"VN","seller_type":"CROSS_BORDER","expires_at":"...","granted_scopes":[]}]}}
```

`result.shops` 为保留的响应容器，成功时只有目标店铺这一项。省略 `format=json` 返回 HTML 结果页。

| 情况 | HTTP / kind |
| --- | --- |
| 卖家拒绝，回调含 `error=auth_denied` | 200 / `denied`，`ok=false` |
| 缺少 code | 400 / `missing_code` |
| state 未知、过期或缺少目标绑定 | 400 / `state_invalid` |
| state 已用 | 400 / `state_reused` |
| 目标店铺、Service 或 App revision 变化 | 400 / `configuration_changed` |
| callback App Key 不匹配 | 400 / `app_key_mismatch` |
| 非卖家授权 | 400 / `user_type` |
| 店铺列表不是恰好目标一店 | 400 / `shop_mismatch` |
| 目标缺少 cipher | 400 / `missing_shop_cipher` |
| 上游 HTTP/业务拒绝 | 502 / `upstream` |
| 其他代理错误 | 502 / `proxy` |

失败不写入半成品凭据；state 已消费时操作者须重新发起授权。

## 凭据及续期

- `integration.tiktok_app_credentials` 按 `(shop_pk, service_id)` 唯一，更新密钥增加 revision。
  历史无 shop_pk 的 App 行保留用于迁移审计，运行时不能借用。
- `integration.credentials` 按 `(provider, external_account_id)` 关联目标 shop_id；加密 envelope
  保存 token 和该店 cipher，独立列 `service_id` 固定签发应用，`extra.app_revision` 绑定版本。
- 同一店重新授权原地更新凭据和原店铺，不产生重复店铺。
- 同步、刷新和代理调用经统一凭据服务解析本店配置；不得回退到全局 App 或跨店配置。
- 卖家取消授权不会自动删除店铺或历史业务事实。

## Partner Center 与环境

由人工在 Partner Center 配置应用和授权范围，并在店铺管理保存该店的 Service/App。
App Secret 只进入 Fernet 加密列，不回显。
Redirect URL 应使用部署实际公网前缀；当前配置示例为
`https://daqiang.nat100.top/tts/v2/oauth/tiktok/callback`，前缀由实际 `TTS_ERP_EXTERNAL_PREFIX` 决定。
部署、生产迁移及服务重启由人工执行，本文不提供自动迁移入口。

| 变量 | 作用 |
| --- | --- |
| `TIKTOK_AUTHORIZE_HOST` | 授权域名；默认 `https://services.tiktokshop.com` |
| `TIKTOK_AUTH_HOST` | token 域名；默认 `https://auth.tiktok-shops.com` |
| `TIKTOK_API_HOST` | Get Authorized Shops 的 API 域名 |
| `TIKTOK_REDIRECT_URI` | 展示提示；Partner Center 配置才决定重定向目标 |

`TIKTOK_SERVICE_ID` / `TIKTOK_APP_KEY` / `TIKTOK_APP_SECRET` 不再是逐店授权的配置来源。

## 页面与验证

店铺管理页提供带目标上下文的授权入口。旧 `/v2/oauth/tiktok/onboard` HTML 壳仍存在，
其旧按钮尚未携带必填 shop_pk；它不能证明现行授权链可用，后续另行迁移。

测试覆盖：`tests/proxy/test_tiktok_oauth.py`（上游 HTTP 解析）、
`tests/proxy/test_oauth_flow.py`（state 与单目标编排）、`tests/api/test_oauth_api.py`（HTTP 合同）。
数据库测试统一通过 `bash scripts/test_isolated.sh fast <测试文件>`，每次使用独立临时克隆库。
