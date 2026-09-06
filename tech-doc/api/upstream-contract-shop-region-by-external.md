# 店铺地区查询接口 — 上游调用方契约 v2（2026-09-06）

> **用途**：本文件是供上游/插件方（Chrome 数据同步插件）按当前服务端实现对接的
> **对外契约**。覆盖历史契约变更（见 §6 Breaking Change），以线上实测响应为准。
> 服务端内部单一来源 spec 另见 `tech-doc/api/channel-accounts-by-external.md`。

- 契约版本：**v2**（2026-09-06，反映 2026-09-05 命名重构后的线上行为）
- 服务端实现：TTS-ERP `/v2/commerce/*`（read-only）
- 验证状态：本契约所有示例均为线上 `2026-09-06` 实测响应（店铺 id=314）

---

## 1. 请求

```http
GET {base}/v2/commerce/channel-accounts/by-external/{shop_id}?platform=tiktok
Authorization: Bearer <API Key>      # 或 X-API-Key: <API Key>
```

| 组件 | 位置 | 必填 | 类型 | 说明 |
| --- | --- | --- | --- | --- |
| `{base}` | — | 是 | URL | 公网网关基址 = `http://daqiang.nat100.top/tts`（**带 `/tts` 前缀**）。直连调试可用 `http://127.0.0.1:9877`（无 `/tts`） |
| `shop_id` | path | 是 | string | **上游店铺 ID**（TikTok shop_id），如 `7494763368967603447`。即旧契约中的 `external_account_id`，值不变 |
| `platform` | query | 否 | string, ≤32 | 默认 `"tiktok"`。店铺 ID 仅在平台内唯一，**调用时必须显式传 `platform=tiktok`** |
| 鉴权头 | header | 是 | — | `Authorization: Bearer <key>` 或 `X-API-Key: <key>`；所需角色 = `readonly` 及以上 |

> ⚠️ 路径参数名是 `{shop_id}`（旧文档曾写作 `external_account_id`，仅参数名变化，
> **不影响 URL**：两种写法实际请求串完全一致）。

### curl 示例

```bash
curl -sS -H "Authorization: Bearer <API Key>" \
  "http://daqiang.nat100.top/tts/v2/commerce/channel-accounts/by-external/7494763368967603447?platform=tiktok"
```

---

## 2. 200 响应（成功）

**顶层 JSON 对象**（不包裹在 `data` 内），`Content-Type: application/json`。
线上实测完整响应：

```json
{
  "id": 314,
  "platform": "tiktok",
  "shop_id": "7494763368967603447",
  "account_name": "Bridge nook",
  "region": "VN",
  "seller_type": "CROSS_BORDER",
  "status": "active",
  "synced_at": "2026-08-31T02:57:36.981124Z",
  "created_at": null,
  "updated_at": null
}
```

### 字段表

| 字段 | 类型 | 可空 | 说明 |
| --- | --- | --- | --- |
| `id` | integer | 否 | 内部主键（`shop_pk`），与店铺 ID 一一对应 |
| `platform` | string | 否 | 恒为 `"tiktok"`（当前仅 tiktok 渠道在库） |
| `shop_id` | string | 否 | **上游店铺 ID**，值 === 请求 path 中的 `shop_id`（旧字段名 `external_account_id`） |
| `account_name` | string | 是 | 店铺名（可能为 null） |
| `region` | string | 是 | **ISO 3166-1 alpha-2 大写国家码**，如 `"VN"`。见 §4 时区语义。可能为 null |
| `seller_type` | string | 是 | 卖家类型，实测 `"CROSS_BORDER"`（非封闭枚举） |
| `status` | string | 是 | 实测 `"active"`（非封闭枚举） |
| `synced_at` | string(datetime) | 是 | ISO-8601 UTC（带 `Z`），末次同步时间 |
| `created_at` / `updated_at` | string(datetime) | 是 | 当前恒为 `null`（内部审计时间，未填充） |

> 客户端解析要求：**忽略多余字段**（`id`/`account_name`/`seller_type`/`status`/`synced_at` 等均存在，
> 属正常，不得因多余字段判失败）。

---

## 3. 错误响应（非 2xx）

| 状态码 | 触发条件 | 响应体 |
| --- | --- | --- |
| **401** | 缺 key / key 无效 / 被禁用 | 鉴权中间件 JSON `{"detail": "..."}` |
| **403** | key 角色 < readonly | `{"detail": "requires readonly"}` |
| **404** | 无 `(platform, shop_id)` 匹配行 | `{"detail": "channel account not found for platform='tiktok' shop_id='<值>'"}` |

> 客户端需按 HTTP 状态码区分处理；404 表示该店铺尚未在 tts-erp 建立渠道账户行，
> 属配置问题，不是重试能解决的。

---

## 4. `region` / 时区语义（重要）

- `region` = **国家码**，不是 IANA 时区；服务端**不返回、也不臆造**时区字段。
- 客户端"国家码 → IANA 时区"的本地映射，**仅对单时区国家是确定性的**。当前白名单
  VN / TH / SG / MY / PH / CN / JP / KR / GB 均为单时区（无夏令时歧义），可直接映射：

  | region | IANA（参考） |
  | --- | --- |
  | VN | `Asia/Ho_Chi_Minh` |
  | TH | `Asia/Bangkok` |
  | SG | `Asia/Singapore` |
  | MY | `Asia/Kuala_Lumpur` |
  | PH | `Asia/Manila` |
  | CN | `Asia/Shanghai` |
  | JP | `Asia/Tokyo` |
  | KR | `Asia/Seoul` |
  | GB | `Europe/London` |

- **多时区国家（如 US / ID，不在白名单）**：不能由 region 推断账户时区，需由下游
  （TikTok 侧账户/店铺时区设置）提供**账户级 IANA 时区**，服务端当前不提供该字段。
  对这类店铺，客户端应显式报"时区不确定"并暂停，不得猜一个默认时区。
- 生产店铺 id=314 实测 `region="VN"` → 可确定时区 `Asia/Ho_Chi_Minh`（UTC+7）。

---

## 5. 客户端校验清单（改造指引）

解析 200 后逐条校验，全部通过才判定"店铺地区有效"：

1. 响应为**顶层 JSON 对象**（非数组、非 `data` 包裹）。
2. `shop_id` 存在且为 **string**，且 === 请求 URL 中的店铺 ID。
3. `platform` 严格等于 `"tiktok"`。
4. `region` 为 string；在白名单内（大写 VN/TH/SG/MY/PH/CN/GB/JP/KR）→ 映射时区；
   白名单外 → 按"时区尚不能确定"暂停（保留告警，不要静默跳过）。
5. 多余字段忽略。

> 网络超时建议 ≥ 30s（实测存在个别 ~22s 慢响应；20s 硬超时会误报网络失败）。

---

## 6. Breaking Change（2026-09-05 起生效，本次故障根因）

| | 旧契约（v1，2026-09-05 之前/当晚过渡窗口） | 新契约（v2，当前线上） |
| --- | --- | --- |
| 响应字段 | `external_account_id`（顶层，string） | **`shop_id`**（顶层，string，值不变） |
| 路径参数命名 | 文档写作 `{external_account_id}` | 文档写作 `{shop_id}` |
| 实际 URL | 不变 | 不变 |
| `platform` / `region` | 不变 | 不变 |

- 变更来源：服务端 commerce 域命名重构（ADR-0003），**2026-09-05 部署到生产**。
- **服务端未保留旧字段别名**：请求 `external_account_id` 会拿不到该键（得到 undefined/null）。
- 插件 0.1.92/0.1.98 校验的是旧字段 → 对 200 响应校验失败 → 暂停采集（故障现象，
  2026-09-06 09:29）。
- **迁移动作（插件侧）**：读取键改为 `shop_id`；为兼容旧版服务端可写
  `const shopId = body.shop_id ?? body.external_account_id;`（先 `shop_id` 后旧名兜底）。

---

## 7. 其他约定

- 超时：单次查询正常 < 50ms；建议客户端超时 30s、失败按指数退避重试（勿 1s 级高频）。
- 幂等：GET 无副作用，可安全重试。
- 该接口属 `/v2/commerce/*` 只读域；任何返回数据不应被客户端回写。
